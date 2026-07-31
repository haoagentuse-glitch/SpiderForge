"""強模型 code client：產 code / 修 code（docs/spec/08 §2）。

DeepSeek 主力；Kimi 作為重試耗盡前的最後一搏（使用者指定的降級邏輯）。
沿用 llm_MMJSUN/client.py 的 OpenAI 相容 + 指數退避重試範式，但這裡輸出是
「程式碼字串」不是結構化 JSON，所以走純文字 completion（不強制 json_schema）。

環境變數（皆在 workspace/.env）：
  DEEPSEEK_API_KEY  初次產碼與一般修復所需
  KIMI_API_KEY      只有進入最後一輪 Kimi 修復時才需要
  DEEPSEEK_BASE_URL / DEEPSEEK_MODEL   可覆蓋（預設 https://api.deepseek.com / deepseek-chat）
  KIMI_BASE_URL / KIMI_MODEL           可覆蓋（預設 https://api.moonshot.cn/v1 / moonshot-v1-32k）
"""

from __future__ import annotations

import os
import re
import time

import requests

from ..observability import llm_span
from .env import load_env
from .registry import get_provider, resolve_api_key

REQUEST_TIMEOUT = 300   # 產/修 code 可能較久（尤其 code 模型長 prompt 全量重寫）
MAX_HTTP_RETRIES = 3


class CoderError(RuntimeError):
    """強模型呼叫失敗（金鑰缺漏、HTTP 錯誤重試耗盡、連線失敗）。"""


# 每次 complete() 成功都記一筆 token 用量；批次服務每站跑完 drain 一次做維運紀錄。
_USAGE: list[dict] = []


def drain_usage() -> list[dict]:
    """取出並清空累積的 token 用量（呼叫端負責在每個工作邊界呼叫）。"""
    global _USAGE
    out, _USAGE = _USAGE, []
    return out


def complete(
    prompt: str,
    *,
    system: str = "",
    provider: str = "deepseek",
    temperature: float | None = None,
    timeout_s: int = REQUEST_TIMEOUT,
) -> str:
    """呼叫強模型，回傳純文字輸出。temperature=None 時用該 provider 的預設。"""
    load_env()
    spec = get_provider(provider)
    try:
        key = resolve_api_key(spec)
    except LookupError as exc:
        raise CoderError(str(exc)) from exc
    temp = spec.default_temperature if temperature is None else temperature
    default_max_tokens = "8000" if spec.name == "kimi" else "5000"
    max_tokens = int(
        os.getenv("SPIDERFORGE_MAX_COMPLETION_TOKENS", default_max_tokens)
    )

    messages = ([{"role": "system", "content": system}] if system else []) + [
        {"role": "user", "content": prompt}
    ]
    delay = 5.0
    with llm_span(spec.name, spec.model, prompt=prompt, system=system) as span:
        for attempt in range(MAX_HTTP_RETRIES + 1):
            try:
                resp = requests.post(
                    f"{spec.base_url.rstrip('/')}/chat/completions",
                    headers={"Authorization": f"Bearer {key}"},
                    json={
                        "model": spec.model,
                        "temperature": temp,
                        "max_tokens": max_tokens,
                        "messages": messages,
                    },
                    timeout=timeout_s,
                )
                resp.raise_for_status()
                data = resp.json()
                usage = data.get("usage", {})
                _USAGE.append({
                    "provider": spec.name,
                    "model": spec.model,
                    "prompt_tokens": usage.get("prompt_tokens", 0),
                    "completion_tokens": usage.get("completion_tokens", 0),
                })
                choice = data["choices"][0]
                span.set_attribute("llm.attempts", attempt + 1)
                if choice.get("finish_reason") == "length":
                    raise CoderError(
                        f"{spec.name} 輸出達 {max_tokens} token 上限，拒收截斷程式碼"
                    )
                content = choice["message"]["content"]
                span.record_output(content, usage)
                return content
            except requests.HTTPError as e:
                status = e.response.status_code if e.response is not None else 0
                if status not in (429, 500, 502, 503) or attempt == MAX_HTTP_RETRIES:
                    body = e.response.text[:300] if e.response is not None else ""
                    raise CoderError(f"{spec.name} HTTP {status}: {body}") from e
                time.sleep(delay)
                delay *= 2
            except requests.RequestException as e:
                if attempt == MAX_HTTP_RETRIES:
                    raise CoderError(f"{spec.name} 連線失敗：{e}") from e
                time.sleep(delay)
                delay *= 2
    raise CoderError("unreachable")


def extract_code(text: str) -> str:
    """從模型輸出抽出程式碼（優先取 ```python ... ``` 圍欄，無則整段）。"""
    m = re.search(r"```(?:python)?\s*\n(.*?)```", text, re.DOTALL)
    return (m.group(1) if m else text).strip()

"""Gemini 連結挑選 client —— 從頁面連結清單裡選出文章明細頁。

結構與重試邏輯沿用已驗證的 topic client（同一個 interactions API 與 structured
output）。只送 URL 與連結文字，不送頁面內容，所以單次呼叫很小（< 2000 token）。

免費額度（RPD 1,000–1,500）下，這個節點每次 run 只呼叫 1 次，成本可忽略。
"""

from __future__ import annotations

import json
import time
from typing import Callable

import requests

from ..observability import llm_span
from ..prompts.discover_links import LINK_PICK_SYSTEM, link_pick_prompt
from ..schemas import LINK_PICK_SCHEMA
from .env import load_env
from .registry import get_provider, resolve_api_key
from .topic import API_URL, TRANSIENT_HTTP, GeminiTopicError, _extract_output_text

_SPEC = get_provider("gemini")
DEFAULT_MODEL = _SPEC.model


class GeminiLinkError(GeminiTopicError):
    """連結挑選失敗（缺金鑰、傳輸失敗、或結構化輸出不合法）。"""


def pick_article_links(
    rows: list[dict],
    *,
    model: str = DEFAULT_MODEL,
    timeout_s: float = 45.0,
    max_retries: int = 2,
    post_fn: Callable = requests.post,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> list[int]:
    """回傳「像文章明細頁」的 index，依像的程度排序。

    rows: ``[{"index": int, "url": str, "text": str}]``
    """
    if not rows:
        return []

    load_env()
    try:
        api_key = resolve_api_key(_SPEC)
    except LookupError as exc:
        raise GeminiLinkError(str(exc)) from exc

    prompt = link_pick_prompt(rows)
    body = {
        "model": model,
        "input": prompt,
        "store": False,
        "generation_config": {
            "thinking_level": "minimal",
            "max_output_tokens": 512,
        },
        "response_format": {
            "type": "text",
            "mime_type": "application/json",
            "schema": LINK_PICK_SCHEMA,
        },
    }

    with llm_span("gemini", model, prompt=prompt, system=LINK_PICK_SYSTEM,
                  purpose="discover_links") as span:
        response = None
        delay = 2.0
        for attempt in range(max_retries + 1):
            try:
                response = post_fn(
                    API_URL,
                    headers={
                        "x-goog-api-key": api_key,
                        "Content-Type": "application/json",
                    },
                    json=body,
                    timeout=timeout_s,
                )
                if response.status_code < 400:
                    break
                if response.status_code not in TRANSIENT_HTTP:
                    raise GeminiLinkError(f"Gemini HTTP {response.status_code}")
                if attempt == max_retries:
                    raise GeminiLinkError(
                        f"Gemini HTTP {response.status_code}，重試耗盡"
                    )
                retry_after = response.headers.get("Retry-After")
                try:
                    wait = float(retry_after) if retry_after else delay
                except (TypeError, ValueError):
                    wait = delay
                sleep_fn(min(wait, 60.0))
                delay *= 2
            except requests.RequestException as exc:
                if attempt == max_retries:
                    raise GeminiLinkError("Gemini 連線失敗，重試耗盡") from exc
                sleep_fn(delay)
                delay *= 2

        if response is None:
            raise GeminiLinkError("Gemini 未回傳 response")
        try:
            payload = response.json()
        except ValueError as exc:
            raise GeminiLinkError("Gemini 回應不是合法 JSON") from exc
        if payload.get("status") != "completed":
            raise GeminiLinkError(
                f"Gemini interaction 未完成：{payload.get('status', 'unknown')}"
            )
        try:
            parsed = json.loads(_extract_output_text(payload))
        except json.JSONDecodeError as exc:
            raise GeminiLinkError("Gemini Structured Output 不是合法 JSON") from exc

        valid = {row["index"] for row in rows}
        picked = [
            int(i) for i in (parsed.get("article_indices") or []) if int(i) in valid
        ]
        span.record_output(picked, payload.get("usage") or {})
        return picked

"""追蹤的啟用與 LLM span 記錄。

環境變數：

===============================  ==========================================
``PHOENIX_COLLECTOR_ENDPOINT``   Phoenix 收集端點；**沒設就不啟用追蹤**
``PHOENIX_PROJECT``              Phoenix 專案名（預設 ``spider_forge``）
``PHOENIX_API_KEY``              雲端 Phoenix 才需要
``SPIDERFORGE_TRACE_CONTENT``    ``0`` = 只記 metadata（token/耗時/模型），
                                 不送 prompt、模型回覆與節點 state 內容
``SPIDERFORGE_TRACE_MAX_CHARS``  單一欄位內容上限，預設 4000。爬蟲的 state 帶著
                                 DOM 與 evidence_pack，不截斷會讓 trace 爆量
===============================  ==========================================
"""

from __future__ import annotations

import json
import os
from contextlib import contextmanager

_enabled = False
_tracer = None
_TRUNCATED_SUFFIX = "…[truncated]"


def tracing_enabled() -> bool:
    return _enabled


def _trace_content() -> bool:
    return os.getenv("SPIDERFORGE_TRACE_CONTENT", "1").strip().lower() not in {
        "0",
        "false",
        "no",
    }


def _max_chars() -> int:
    try:
        return max(200, int(os.getenv("SPIDERFORGE_TRACE_MAX_CHARS", "4000")))
    except ValueError:
        return 4000


def _clip(value: object) -> str:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    limit = _max_chars()
    return text if len(text) <= limit else text[:limit] + _TRUNCATED_SUFFIX


def setup_tracing(*, endpoint: str | None = None, project: str | None = None) -> bool:
    """啟用 Phoenix 追蹤；沒有 endpoint 就 no-op。回傳是否真的啟用了。

    冪等：重複呼叫只會註冊一次。任何失敗都只印警告，不丟例外。
    """
    global _enabled, _tracer
    if _enabled:
        return True

    from ..clients.env import load_env

    load_env()
    endpoint = endpoint or os.getenv("PHOENIX_COLLECTOR_ENDPOINT", "").strip()
    if not endpoint:
        return False

    # 屬性長度上限：本專案的 node span input/output 是完整 state（含 DOM 與
    # evidence_pack），不設限會讓單一 trace 爆到數 MB。這是 OTel 標準 env，
    # TracerProvider 建立時讀取，所以必須在 register() 之前設。
    os.environ.setdefault("OTEL_ATTRIBUTE_VALUE_LENGTH_LIMIT", str(_max_chars()))

    try:
        from openinference.instrumentation import TraceConfig
        from openinference.instrumentation.langchain import LangChainInstrumentor
        from phoenix.otel import register

        hide = not _trace_content()
        config = TraceConfig(hide_inputs=hide, hide_outputs=hide)
        provider = register(
            endpoint=endpoint,
            project_name=project or os.getenv("PHOENIX_PROJECT", "spider_forge"),
            api_key=os.getenv("PHOENIX_API_KEY") or None,
            batch=True,
            verbose=False,
        )
        # LangGraph 的節點 span 由這個 instrumentor 自動產生，節點程式碼不需改動。
        # ⚠️ 實測：對這個 provider 呼叫 add_span_processor() 是「取代」不是「追加」，
        #    事後掛自訂 processor 會靜默把 OTLP 匯出換掉。要多路輸出得自己組 provider。
        LangChainInstrumentor().instrument(tracer_provider=provider, config=config)
        _tracer = provider.get_tracer("spider_forge")
        _enabled = True
        return True
    except Exception as exc:  # noqa: BLE001 — 觀測失敗絕不影響主流程
        print(f"[observability] 追蹤啟用失敗，流程照常執行：{exc}")
        return False


class _NullSpan:
    """追蹤未啟用時的替身：所有呼叫都是 no-op。"""

    def record_output(self, output=None, usage=None) -> None:
        return None

    def set_attribute(self, key: str, value) -> None:
        return None


class _LlmSpan:
    def __init__(self, span):
        self._span = span

    def set_attribute(self, key: str, value) -> None:
        self._span.set_attribute(key, value)

    def record_output(self, output=None, usage=None) -> None:
        """記錄模型輸出與 token 用量（內容受 SPIDERFORGE_TRACE_CONTENT 控制）。"""
        if output is not None and _trace_content():
            self._span.set_attribute("output.value", _clip(output))
        for source_key, target_key in (
            ("prompt_tokens", "llm.token_count.prompt"),
            ("completion_tokens", "llm.token_count.completion"),
            ("total_tokens", "llm.token_count.total"),
            ("input_tokens", "llm.token_count.prompt"),
            ("output_tokens", "llm.token_count.completion"),
        ):
            value = (usage or {}).get(source_key)
            if isinstance(value, int):
                self._span.set_attribute(target_key, value)


@contextmanager
def llm_span(provider: str, model: str, *, prompt=None, system: str = "", purpose: str = ""):
    """包住一次 LLM 呼叫，產生 Phoenix 認得的 LLM span。

    這些呼叫是 clients 層自己用 requests 打的（不是 LangChain 的 LLM 類別），
    instrumentor 抓不到，所以要手動包——這是唯一需要手動 instrument 的地方。

    例外會被記進 span 後原樣往外丟（不吞）。
    """
    if not _enabled or _tracer is None:
        yield _NullSpan()
        return

    with _tracer.start_as_current_span(f"llm.{provider}") as span:
        span.set_attribute("openinference.span.kind", "LLM")
        span.set_attribute("llm.provider", provider)
        span.set_attribute("llm.model_name", model)
        if purpose:
            span.set_attribute("llm.purpose", purpose)
        if prompt is not None and _trace_content():
            span.set_attribute(
                "input.value",
                _clip(f"[system]\n{system}\n\n[user]\n{prompt}" if system else prompt),
            )
        yield _LlmSpan(span)

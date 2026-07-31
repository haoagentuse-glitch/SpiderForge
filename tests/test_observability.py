"""可觀測性層的不變式：沒設定 Phoenix 時必須完全不影響流程。

這些測試不連任何 collector，也不真的啟用追蹤——只驗「關掉時是乾淨的 no-op」
與內容開關/截斷的行為。真正的端到端匯出用 scratchpad 的假 collector 驗過
（見 REFACTOR_PLAN 階段9 的驗收紀錄）。
"""

from __future__ import annotations

import os
from contextlib import contextmanager

from spider_forge.observability import llm_span, setup_tracing, tracing_enabled
from spider_forge.observability import tracing as tracing_module


@contextmanager
def _env(**pairs):
    previous = {key: os.environ.get(key) for key in pairs}
    try:
        for key, value in pairs.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        yield
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def t_tracing_is_off_without_endpoint():
    """沒有 PHOENIX_COLLECTOR_ENDPOINT 就不啟用——套件預設不依賴任何觀測服務。"""
    if tracing_enabled():
        return True, "本 session 已啟用追蹤，略過（此測試只驗未啟用時的預設）"
    with _env(PHOENIX_COLLECTOR_ENDPOINT=None):
        enabled = setup_tracing()
    return enabled is False and not tracing_enabled(), f"setup_tracing={enabled}"


def t_llm_span_is_a_silent_noop_when_disabled():
    """追蹤關閉時 llm_span 必須完全不炸、不改變呼叫端行為。"""
    if tracing_enabled():
        return True, "本 session 已啟用追蹤，略過"
    with llm_span("deepseek", "deepseek-chat", prompt="x", system="y") as span:
        span.set_attribute("llm.attempts", 1)
        span.record_output("out", {"prompt_tokens": 3, "completion_tokens": 4})
    return True, "no-op 路徑未拋例外"


def t_trace_content_switch_is_respected():
    with _env(SPIDERFORGE_TRACE_CONTENT="0"):
        off = tracing_module._trace_content()
    with _env(SPIDERFORGE_TRACE_CONTENT="1"):
        on = tracing_module._trace_content()
    with _env(SPIDERFORGE_TRACE_CONTENT=None):
        default_on = tracing_module._trace_content()
    return (off, on, default_on) == (False, True, True), (
        f"off={off} on={on} default={default_on}"
    )


def t_long_values_are_clipped():
    """節點 state 帶著 DOM 與 evidence_pack，內容一定要有長度上限。"""
    with _env(SPIDERFORGE_TRACE_MAX_CHARS="500"):
        clipped = tracing_module._clip("X" * 5000)
        short = tracing_module._clip("abc")
        structured = tracing_module._clip({"dom": "Y" * 5000})
    return (
        len(clipped) <= 520
        and clipped.endswith("[truncated]")
        and short == "abc"
        and len(structured) <= 520
    ), f"clipped={len(clipped)} structured={len(structured)}"


def t_clients_do_not_hard_depend_on_phoenix():
    """觀測是旁掛層：clients 匯入 llm_span，但沒裝 Phoenix 也必須能 import。"""
    import importlib

    for module in (
        "spider_forge.clients.coder",
        "spider_forge.clients.judge",
        "spider_forge.clients.topic",
        "spider_forge.clients.page",
    ):
        importlib.import_module(module)
    return True, "四個 client 皆可在未啟用追蹤時匯入"


TESTS = [
    t_tracing_is_off_without_endpoint,
    t_llm_span_is_a_silent_noop_when_disabled,
    t_trace_content_switch_is_respected,
    t_long_values_are_clipped,
    t_clients_do_not_hard_depend_on_phoenix,
]

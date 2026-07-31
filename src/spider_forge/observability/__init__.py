"""可觀測性（Arize Phoenix / OpenTelemetry）—— 對核心流程完全可選的旁掛層。

設計原則（三條，違反就是把觀測做成了負擔）：

1. **不進節點**：LangGraph 的節點 span 由 `openinference-instrumentation-langchain`
   自動產生（已實測：每個節點一個 CHAIN span，含 input/output/例外堆疊）。
   節點程式碼完全不知道 Phoenix 存在——所以「加新節點」仍然只要新增一檔。
2. **沒設定就完全不動**：沒有 `PHOENIX_COLLECTOR_ENDPOINT` 時 `setup_tracing()` 直接
   回 False，`llm_span()` 回一個什麼都不做的空物件，零開銷、零 import。
3. **觀測失敗絕不影響主流程**：所有錯誤吞掉並印一行警告。爬蟲跑不跑得起來，
   不該取決於 Phoenix 在不在。

用法：

    from spider_forge.observability import setup_tracing
    setup_tracing()          # CLI 進入點呼叫一次即可
"""

from .tracing import llm_span, setup_tracing, tracing_enabled

__all__ = ["setup_tracing", "llm_span", "tracing_enabled"]

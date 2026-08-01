"""執行設定檔（profile）：同一條管線、不同領域的預設值。

**管線只有一條**（`pipeline.py`）。換領域不複製管線，只換一組設定——這正是
重構後想要的形狀：積木與流程完全不動，領域知識收斂在這個檔。

用法：

    python -m pipelines.cli batch --profile finance
    python -m pipelines.cli run --url ... --profile finance

或當函式庫：

    from pipelines.pipeline import forge_spider
    from pipelines.profiles import apply, resolve

    forge_spider(url, **apply(resolve("finance"), {}))

加一個新領域 = 這裡多一個 dict + 註冊到 ``PROFILES``。
"""

from __future__ import annotations

from typing import Any

# ── 通用（預設）：不預先決定「什麼主題才算合格」 ────────────────────────────
GENERAL: dict[str, Any] = {}


# ── 台灣財經新聞：原專案的用例 ──────────────────────────────────────────────
# 與通用版的唯一實質差異就是主題閘門。站台清單（含各站的 allowed_domains /
# article_url_patterns 等品質規則）在 examples/site_queue.taiwan-finance.yaml，
# 由 SPIDERFORGE_SITE_QUEUE 指定，預設就是它。
#
# ⚠️ enforce 會呼叫 Gemini 逐批分類，需要 GEMINI_API_KEY，且會消耗額度。
#    先想看效果不想擋人，把 mode 改成 "shadow"（只記錄、不擋）。
FINANCE: dict[str, Any] = {
    "topic_gate": {
        "mode": "enforce",
        "provider": "gemini",
        # 判準（finance / public_policy 兩個 label）寫在
        # src/spider_forge/clients/topic.py 的 prompt 與 response schema。
        "min_relevant_ratio": 0.6,
    },
}


PROFILES: dict[str, dict[str, Any]] = {
    "general": GENERAL,
    "finance": FINANCE,
}


def resolve(name: str | None) -> dict[str, Any]:
    """取得 profile；未知名稱明確報錯（不靜默退回預設）。"""
    key = (name or "general").strip().lower()
    if key not in PROFILES:
        raise ValueError(f"未知 profile：{name}（可用：{sorted(PROFILES)}）")
    return PROFILES[key]


def apply(profile: dict[str, Any], request: dict[str, Any]) -> dict[str, Any]:
    """把 profile 疊在請求底下——**呼叫端明給的值永遠優先**。

    所以站台 YAML 或 CLI 參數可以逐站推翻 profile，不必為了一個例外
    另外複製一份 profile。
    """
    return {**profile, **request}

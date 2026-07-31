"""content_block_gate 節點：sandbox 後、欄位驗證前擋下「200 但其實是封鎖/錯誤頁」。

抓到 block 就歸 block_page_200 直接進 diagnose，不浪費欄位驗證與 repair 猜測（spec v2 §3.3）。

可注入（__init__）：
- ``extra_patterns``：追加的封鎖頁字樣（預設清單是英文＋繁中；抓別的語系站台在這裡補，
  不用改節點邏輯）。
- ``classifier``：可疑案例的二次確認器。預設 None → 依 state 的 ``block_gate`` 設定決定
  是否 late-bind Gemini（沒設定就純確定性，省額度）。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from ..state import SpiderForgeState
from .base import Node

_BLOCK_PAGE_PATTERNS = (
    r"\baccess denied\b",
    r"\brequest (?:was )?blocked\b",
    r"\battention required\b",
    r"\bverify (?:that )?you are human\b",
    r"\bchecking your browser\b",
    r"\bjust a moment\b",
    r"\benable (?:java\s*script|javascript|cookies?)\b",
    r"\bcloudflare\b",
    r"\b(?:404 not found|error 50\d|service unavailable)\b",
    r"拒絕存取",
    r"請確認您不是機器人",
    r"請稍候",
    r"服務暫時無法使用",
)


def _gemini_block_classifier(cfg: dict | None):
    """只有站設定明確 provider=gemini 才回可呼叫的分類器；否則 None（純確定性，省額度）。"""
    if (cfg or {}).get("provider") != "gemini":
        return None
    from ..clients.page import classify_page

    model = str((cfg or {}).get("gemini_model") or "gemini-3.5-flash-lite")
    timeout = float((cfg or {}).get("gemini_timeout_s") or 45.0)
    return lambda leads: classify_page(leads, model=model, timeout_s=timeout)


class ContentBlockGate(Node):
    def __init__(self, *, extra_patterns: tuple[str, ...] = (), classifier=None):
        self._patterns = tuple(
            re.compile(pattern, re.IGNORECASE | re.DOTALL)
            for pattern in (*_BLOCK_PAGE_PATTERNS, *extra_patterns)
        )
        self._classifier = classifier

    def _looks_like_block(self, item: dict) -> bool:
        blob = f"{item.get('title') or ''} {str(item.get('content') or '')[:800]}"
        return any(pattern.search(blob) for pattern in self._patterns)

    def detect(self, items: list[dict], classify_fn=None) -> dict:
        """確定性優先偵測 block_page_200；只有可疑（部分命中）且啟用時才呼叫二次確認。

        二次確認器任何錯誤都 fail-open 判 content——誤殺好站比漏判 block 更傷成功率
        （漏判的 block 後面 validators 仍會以 content_soft_block 擋下）。
        """
        total = len(items)
        if total == 0:
            return {"verdict": "unknown", "method": "no_items", "block_ratio": 0.0}
        block_hits = sum(1 for item in items if self._looks_like_block(item))
        nonempty = [
            str(item.get("content") or "")
            for item in items
            if str(item.get("content") or "").strip()
        ]
        unique_leads = len({content[:200] for content in nonempty})
        near_identical = bool(
            total >= 3 and nonempty and unique_leads <= max(1, total // 5)
        )
        ratio = block_hits / total
        if ratio >= 0.5 or near_identical:
            return {
                "verdict": "block",
                "method": "deterministic",
                "block_ratio": round(ratio, 2),
                "near_identical": near_identical,
            }
        if 0 < ratio < 0.5 and classify_fn is not None:
            leads = (nonempty or [str(i.get("content") or "") for i in items])[:5]
            try:
                verdict = classify_fn(leads)
                if verdict.get("verdict") == "block":
                    return {
                        "verdict": "block",
                        "method": "gemini",
                        "reason": verdict.get("reason"),
                        "block_ratio": round(ratio, 2),
                    }
                return {
                    "verdict": "content",
                    "method": "gemini",
                    "block_ratio": round(ratio, 2),
                }
            except Exception as exc:  # noqa: BLE001 — fail-open：分類器掛了不誤殺
                return {
                    "verdict": "content",
                    "method": "gemini_error_failopen",
                    "error": str(exc)[:200],
                    "block_ratio": round(ratio, 2),
                }
        return {
            "verdict": "content",
            "method": "deterministic",
            "block_ratio": round(ratio, 2),
        }

    def __call__(self, state: SpiderForgeState) -> dict:
        test = state.get("test_result") or {}
        if not test.get("passed"):
            return {"block_page_detected": False}
        items: list[dict] = []
        try:
            path = Path(test.get("output_path", ""))
            if path.exists() and path.stat().st_size:
                loaded = json.loads(path.read_text(encoding="utf-8"))
                items = loaded if isinstance(loaded, list) else []
        except Exception:  # noqa: BLE001
            items = []
        classifier = self._classifier or _gemini_block_classifier(state.get("block_gate"))
        detection = self.detect(items, classifier)
        return {
            "block_page_detected": detection["verdict"] == "block",
            "block_detection": detection,
        }

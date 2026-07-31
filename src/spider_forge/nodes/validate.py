"""validate_output 節點：對沙盒實跑產出的 items 做欄位品質驗證。

可注入（__init__）：``validator``——驗證函式 ``(items, validation_cfg) -> result``。
預設 None → late-bind ``shared.quality_rules.validate_items``（讓測試能 monkeypatch）。
換一套品質規則只要換這個，不動節點。
"""

from __future__ import annotations

import json
from pathlib import Path

from ..state import SpiderForgeState
from .base import Node

_PREVIEW_FIELDS = {"title", "url", "content", "published_at", "source_record_id"}


def _item_preview(item: dict) -> dict:
    return {
        key: (value[:500] if isinstance(value, str) else value)
        for key, value in item.items()
        if key in _PREVIEW_FIELDS
    }


class ValidateOutput(Node):
    def __init__(self, *, validator=None):
        self._validator = validator

    def __call__(self, state: SpiderForgeState) -> dict:
        validator = self._validator
        if validator is None:
            from ..shared.quality_rules import validate_items as validator

        test = state.get("test_result") or {}
        if not test.get("passed"):
            reason = test.get("error") or test.get("stderr_tail", "")[-500:]
            return {
                "validation_result": {
                    "pass": False,
                    "flags": {"retrieval_ok": False},
                    "item_count": 0,
                    "issues": [f"crawl 未成功結束：{reason}"],
                },
                "status": "validating",
            }

        items = []
        try:
            path = Path(test.get("output_path", ""))
            if path.exists() and path.stat().st_size:
                items = json.loads(path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            items = []
        result = validator(items, state.get("validation") or {})
        result["issues"] = [
            f"flag_fail:{key}" for key, passed in result["flags"].items() if not passed
        ] + [f"{key}={value}" for key, value in result["reject_reasons"].items()]
        result["item_samples"] = [_item_preview(item) for item in items[:3]]
        return {"validation_result": result, "status": "validating"}

"""LLM 結構化輸出的 JSON schema（給 judge / Ollama constrained decoding）。

這些定義「模型該吐什麼形狀」，與 prompt 文字（prompts/）刻意分開：
schema 管結構、prompt 管指令。原本埋在 shared/prompts.py，屬 strategy/diagnose 節點。
"""

from __future__ import annotations

# strategy_decision 節點：選 api / dom / hybrid
STRATEGY_SCHEMA = {
    "type": "object",
    "properties": {
        "strategy": {"type": "string", "enum": ["api", "dom", "hybrid"]},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "reason": {"type": "string"},
        "chosen_api": {"type": "string"},
    },
    "required": ["strategy", "confidence", "reason"],
}

# diagnose_failure 節點：失敗分類
DIAGNOSE_SCHEMA = {
    "type": "object",
    "properties": {
        "failure_type": {"type": "string"},
        "evidence": {"type": "string"},
        "suggested_fix": {"type": "string"},
        "error_signature": {"type": "string"},
    },
    "required": ["failure_type", "suggested_fix", "error_signature"],
}


# discover_links 節點：模型只回「哪些 index 是文章明細頁」，依像的程度排序。
LINK_PICK_SCHEMA = {
    "type": "object",
    "properties": {
        "article_indices": {
            "type": "array",
            "maxItems": 10,
            "items": {"type": "integer", "minimum": 0},
        }
    },
    "required": ["article_indices"],
    "additionalProperties": False,
}

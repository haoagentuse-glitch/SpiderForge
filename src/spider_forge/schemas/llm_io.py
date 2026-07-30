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

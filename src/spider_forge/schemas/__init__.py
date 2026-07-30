"""集中的資料 schema 定義（抓取目標 + LLM 結構化輸出）。"""

from .llm_io import DIAGNOSE_SCHEMA, STRATEGY_SCHEMA
from .outputs import DEFAULT_TARGET_SCHEMA, Article

__all__ = [
    "Article",
    "DEFAULT_TARGET_SCHEMA",
    "STRATEGY_SCHEMA",
    "DIAGNOSE_SCHEMA",
]

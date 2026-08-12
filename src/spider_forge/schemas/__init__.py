"""集中的資料 schema 定義（抓取目標 + LLM 結構化輸出）。"""

from .llm_io import DIAGNOSE_SCHEMA, LINK_PICK_SCHEMA, STRATEGY_SCHEMA
from .outputs import (
    DEFAULT_TARGET_SCHEMA,
    Article,
    build_target_schema,
    field_contract_block,
)

__all__ = [
    "Article",               # ★ 改抓什麼欄位只改這個 class
    "DEFAULT_TARGET_SCHEMA",  # 由 Article 生成：pipeline 流動的 dict 契約
    "field_contract_block",   # 由 schema 生成：產碼 prompt 的欄位段落
    "build_target_schema",
    "STRATEGY_SCHEMA",
    "DIAGNOSE_SCHEMA",
    "LINK_PICK_SCHEMA",
]

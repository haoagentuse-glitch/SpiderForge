"""抓取目標的欄位契約 —— **改抓什麼欄位，只改這個檔的 ``Article``。**

一個欄位有三種資訊，全部寫在同一個 ``Field()`` 裡，不再散落兩處：

===============  =========================  ================================
資訊             寫在哪                     誰在用
===============  =========================  ================================
名稱、必填       pydantic 欄位宣告          驗證（離線重播的必填檢查）
給模型的規則     ``description``            產碼／修碼 prompt
驗證用語意鍵     ``json_schema_extra``      品質規則（type/mode/max_chars）
===============  =========================  ================================

下游兩條路都**從這裡生成**，所以加一個欄位不會只有一半生效：

- :data:`DEFAULT_TARGET_SCHEMA` —— pipeline 流動的 dict 契約
  （``nodes/prepare_request.py`` 深拷貝後放進 ``state["target_schema"]``）
- :func:`field_contract_block` —— 產碼 prompt 的欄位段落
  （``prompts/generate.py`` 的 ``{field_contract}`` 由 ``shared/generation.py`` 填入）

``description`` 會**逐字進 prompt**，所以請用「給工程師看的指令」語氣寫，
可以用 ``{max_chars}`` 取執行期的實際上限（站台可覆蓋）。除此之外別放 ``{}``。
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

# 這兩個不屬於任何單一欄位，是整份產出的性質。
SOURCE_TYPE = "media"
CONTENT_SCOPE = "summary_only"


class Article(BaseModel):
    """一篇文章的抓取結果。"""

    title: str = Field(
        ...,
        description="文章標題",
        json_schema_extra={"contract": {"type": "string"}},
    )
    url: str = Field(
        ...,
        description=(
            "人工可查證的文章入口 permalink；"
            "path 大小寫、連字號與 query parameter 名稱必須逐字沿用 EvidencePack "
            "觀察到的 detail URL（禁止把 ?a= 改成 ?b= 或自行改寫 URL 形式）；"
            "禁止用假 query 或 fragment 冒充唯一文章 URL"
        ),
        json_schema_extra={"contract": {"type": "url"}},
    )
    content: str = Field(
        ...,
        description=(
            "清除廣告/導覽後、保持原文順序與措辭的忠實摘錄，最多 {max_chars} 字；"
            "禁止改寫成模型摘要"
        ),
        json_schema_extra={
            "contract": {
                "type": "string",
                "mode": "faithful_excerpt",
                "max_chars": 6000,
            }
        },
    )
    published_at: str = Field(
        ...,
        description="含時區的 ISO8601",
        json_schema_extra={"contract": {"type": "iso8601_tz"}},
    )
    source_record_id: str | None = Field(
        None,
        description="沒有單篇 permalink 的 API／公告才填來源識別碼",
        json_schema_extra={"contract": {"type": "string"}},
    )


def _field_entries() -> list[tuple[str, bool, dict[str, Any], str]]:
    """(欄位名, 是否必填, 語意鍵, 給模型的規則)，順序照 Article 的宣告。"""
    entries = []
    for name, info in Article.model_fields.items():
        contract = dict((info.json_schema_extra or {}).get("contract", {}))
        entries.append((name, info.is_required(), contract, info.description or ""))
    return entries


def build_target_schema() -> dict[str, Any]:
    """由 ``Article`` 生成 pipeline 流動的 dict 契約。"""
    fields: dict[str, Any] = {}
    for name, required, contract, rule in _field_entries():
        fields[name] = {
            "type": contract.get("type", "string"),
            "required": required,
            **{key: value for key, value in contract.items() if key != "type"},
            "rule": rule,
        }
    return {
        "fields": fields,
        "source_type": SOURCE_TYPE,
        "content_scope": CONTENT_SCOPE,
    }


DEFAULT_TARGET_SCHEMA = build_target_schema()


def field_contract_block(schema: dict[str, Any] | None = None) -> str:
    """生成產碼 prompt 的欄位段落。

    吃的是**執行期**的 ``target_schema``（不是 ``Article``），所以站台自訂欄位
    也會一起進 prompt——只要那份 schema 的欄位帶 ``rule``。
    """
    fields = (schema or DEFAULT_TARGET_SCHEMA).get("fields") or {}
    max_chars = (fields.get("content") or {}).get("max_chars", 6000)
    lines = []
    for name, rule in fields.items():
        mark = "必填" if rule.get("required", True) else "選填"
        text = str(rule.get("rule") or "").format(max_chars=max_chars)
        lines.append(f"   - {name}（{mark}）：{text}" if text else f"   - {name}（{mark}）")
    return "\n".join(lines)

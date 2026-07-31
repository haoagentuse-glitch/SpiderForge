"""抓取目標的資料形狀 —— 集中的唯一真實來源。

改要抓什麼欄位，只改這個檔。pydantic ``Article`` 是給人看與驗證用的定義；
``DEFAULT_TARGET_SCHEMA`` 是 pipeline 目前流動的 dict 契約（nodes/prepare_request.py 深拷貝
後放進 state["target_schema"]），保留原結構與語意鍵（type/required/mode/max_chars…），
向下相容。兩者描述同一組欄位；語意鍵是 dict 契約特有、pydantic 標準 schema 不涵蓋，
故與 Article 並列而非自動生成。未來若要讓 pipeline 全面走 pydantic，從這裡收斂。
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class Article(BaseModel):
    """一篇文章的抓取結果。"""

    title: str
    url: str = Field(..., description="人工可查證的文章入口 permalink")
    content: str = Field(..., description="清除廣告/導覽後的忠實摘錄，最多 6000 字")
    published_at: str = Field(..., description="含時區的 ISO8601")
    source_record_id: str | None = Field(
        None, description="無單篇 permalink 時填來源識別碼"
    )


DEFAULT_TARGET_SCHEMA = {
    "fields": {
        "title": {"type": "string", "required": True},
        "url": {"type": "url", "required": True},
        "content": {
            "type": "string",
            "required": True,
            "mode": "faithful_excerpt",
            "max_chars": 6000,
        },
        "published_at": {"type": "iso8601_tz", "required": True},
        "source_record_id": {"type": "string", "required": False},
    },
    "source_type": "media",
    "content_scope": "summary_only",
}

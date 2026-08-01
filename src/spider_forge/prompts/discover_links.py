"""discover_links 節點的 prompt：從頁面連結清單裡挑出「文章明細頁」。

為什麼需要模型：純程式按 DOM 順序取樣，導覽列必然排在內文之前（HTML 結構使然），
BBC 實測前 25 筆全是導覽。這不是可以靠規則窮舉的問題——每個站的導覽長得都不一樣。

模型**不負責**判斷「屬於哪個版面」（財經／體育／科技）——那是使用者意圖，
由 validation.article_url_patterns 精確控制，不是網頁裡的事實。
"""

from __future__ import annotations

LINK_PICK_SYSTEM = (
    "你在幫爬蟲從網頁的連結清單裡挑出「單篇文章／新聞稿」的明細頁，只輸出 JSON。"
    "要挑的：帶完整標題文字、指向單一篇報導或公告的連結。"
    "要排除：導覽列、分類／版面首頁、標籤頁、搜尋頁、登入註冊、訂閱、關於我們、"
    "App 下載、社群連結、以及指向網站首頁或當前列表頁自己的連結。"
    "判準參考（不是硬規則）：文章連結的文字通常是完整句子或標題（較長），"
    "URL 通常較深，末段常帶隨機 id、編號或日期；導覽連結文字短、URL 淺。"
    "依「最像單篇文章」由高到低排序輸出 index，最多 10 個；一個都不像就回空陣列。"
)


def link_pick_prompt(rows: list[dict]) -> str:
    """rows: [{"index": int, "url": str, "text": str}]"""
    import json

    return (
        "以下是同一個網頁上的連結。挑出指向單篇文章明細頁的那些。\n"
        + json.dumps(rows, ensure_ascii=False, separators=(",", ":"))
    )

"""generate_spider 節點的 prompt：產 Scrapy 爬蟲碼的系統指令與硬性契約。

SPIDER_CONTRACT 用 .format() 填入 {source_prefix}/{site_name}/{source_type}/
{content_scope}/{field_contract}（見 shared/generation.py 的 `_contract()`）。
改產碼指令只改這裡，不動 generation.py 的邏輯。

⚠️ **欄位契約不在這個檔**：`{field_contract}` 由 schemas/outputs.py 的 `Article`
生成。要改抓什麼欄位、或改某欄位的規則，去改那個檔——這裡只放跨欄位的通用規則。
"""

from __future__ import annotations

CODE_SYSTEM = (
    "你是資深 Scrapy 工程師。只輸出單一 Python 檔的完整程式碼（放在 ```python 圍欄內），"
    "不要解說、不要 selector 猜測清單、不要要求使用者補 HAR。只能依 EvidencePack 實作。"
    "程式碼保持精簡，最多 180 行；共用 headers 只定義一次，禁止長篇註解與重複 fallback。"
)

SPIDER_CONTRACT = """【硬性契約】
1. 單一檔案、一個 scrapy.Spider 子類別；name/source_prefix 均為 "{source_prefix}"。
2. allowed_domains 必須涵蓋實際請求 host；限速與最多 2 個列表分頁（低速由專案 AutoThrottle 維持）。
   constraints.max_pages 只限制列表翻頁請求，不是整支爬蟲的 response 數；
   禁止用 CLOSESPIDER_PAGECOUNT 實作此限制，以免列表頁占掉文章明細額度。
   需要帳號登入才看得到的內容不碰（不做登入、不用他人憑證）；除此之外遇到擋牆
   照常嘗試，但不要用假資料充數——抓不到就讓它自然失敗。
3. class 屬性 source="{site_name}"、source_type="{source_type}"、
   content_scope="{content_scope}"。
   **每一個 Request 都要明確寫 callback，而且被指名的 method 一定要存在**；
   用 start_urls（不自寫 start_requests）時必須定義 `def parse(self, response)`。
   **EvidencePack.replay_headers 要放進 custom_settings 的 USER_AGENT 與
   DEFAULT_REQUEST_HEADERS**，不要只掛在自己 yield 的 Request 上：start_urls
   產生的入口請求不會帶你設的 headers，會走 Scrapy 預設 UA。實測有站台對
   預設 UA 直接回 403、對瀏覽器 UA 回 200——入口一被擋，整站就是 0 筆。
   實測最常見的產碼失敗就是這個：自訂了 parse_listing 卻沒有 parse，
   Scrapy 對 start_urls 產生的 request 找不到預設 callback，
   直接 NotImplementedError，整支爬蟲一筆都抓不到。
4. 在同一檔案內定義 ArticleItem(scrapy.Item)，只 yield ArticleItem，不引用專案內其他模組。
   欄位契約如下；必填欄位取不到真值就跳過該筆，禁止以 title 或目前時間偽造：
{field_contract}
5. 不寫資料庫、不讀 secrets、不 import 任何專案內模組。
6. 只能使用 EvidencePack 中有 response body 的結構化來源（JSON/RSS/Atom）；
   不得依 URL 名稱猜 JSON path。
   若 browser 被擋但 plain HTTP=200，沿用 EvidencePack 的 safe_request_headers，
   用 Scrapy HTTP/HTML，不得硬切 Playwright。
7. 嚴格遵守 EvidencePack.request.validation 的 URL pattern、排除規則、時效與數量。
   Scrapy SelectorList 沒有 .first()；用 .get()、getall() 或索引。
   **`scrapy.loader.processors` 這個模組早就不存在**（本專案是 Scrapy 2.17）；
   真的要用 ItemLoader 處理器是 `itemloaders.processors`。但直接用 selector
   取值就夠了，不必引入 ItemLoader——實測這個 import 是最常見的整支載入失敗原因。
   **`.re_first(pattern)` 在 pattern 有多個 group 時只回「第一個 group」的字串**，
   不是所有 group。要一次取多個 group 用 `.re(pattern)`（回傳 list）。
   實測最常見的死法：`y, m, d, H, M = map(int, sel.re_first(r"(\\d{{4}}) 年 …"))`
   —— re_first 回的是 "2026"，map 會逐字元拆開，當場 ValueError，整站抓 0 筆。
   **requirements 含 browser_transport 時才用 scrapy_playwright，否則不要 import**——
   判準是這一項，不是 access_assessment：入口用純 HTTP 拿得到 200，不代表內文不是
   前端渲染的（EvidencePack.fetch_strategy 是前期偵查實際驗證過的抓法）。
   含 browser_transport 時，入口與明細的每一個必要 request 都必須
   設 meta.playwright=True；禁止讓 start_urls 產生未啟用 Playwright 的入口 request。
   候選會由 scrapy runspider 獨立執行，不會載入 crawler runtime settings，因此必須
   在 custom_settings 自帶 scrapy-playwright 的 DOWNLOAD_HANDLERS 與 TWISTED_REACTOR。
   只需要 response DOM 時設 meta.playwright=True 即可。
   **無限捲動／「載入更多」用 playwright_page_methods 實作**，不需要
   playwright_include_page，例如：
     from scrapy_playwright.page import PageMethod
     meta={{"playwright": True, "playwright_page_methods": [
         PageMethod("evaluate", "window.scrollTo(0, document.body.scrollHeight)"),
         PageMethod("wait_for_timeout", 1500),
     ]}}
   捲動或點「載入更多」的次數依 constraints.max_pages。真的需要
   playwright_include_page 時，務必在 callback 內 await page.close()。
8. 日期補 IANA 時區時使用 Python 標準庫 zoneinfo.ZoneInfo；不要為此新增 pytz 依賴。"""

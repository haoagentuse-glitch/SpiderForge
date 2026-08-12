"""明細樣本驗證（偵查子迴圈的檢查二）—— 純程式，不呼叫任何模型。

**為什麼需要這一關**：BBC 那次實跑，`discover_links` 挑出的「文章樣本」其實是同一個
列表頁，兩份樣本的正文都是兩萬字且高度相似。產碼模型照著列表頁學 selector，
之後兩輪修復用的是**同一份錯誤證據**，所以怎麼修都修不好，而診斷還把它歸成
`selector_schema`（selector 寫錯）—— 歸因整個是錯的。

這裡擋的就是這件事：往下送之前先確認手上的樣本真的是文章明細頁。

四個判準都是結構事實，不需要語意判斷：

1. **有標題** —— 明細頁一定有 ``<title>`` 或 ``<h1>``。
2. **正文夠長** —— 門檻沿用站台設定的 ``validation.min_content_chars``，
   跟下游品質閘門同一個數字，不另立標準。
3. **有文件層級的發佈時間** —— 分辨「文章」與「分類頁」唯一有效的訊號（見下）。
4. **兩篇不能近乎相同** —— 拿到同一個列表頁兩次時，前三條都可能過，
   只有互相比對擋得住（BBC 那次的相似度接近 1.0）。

**第 3 條為什麼是這個訊號**（2026-08-12 實測 BBC，非推論）：零設定跑 BBC 時挑到的是
``/news``、``/sport``、``/technology`` 三個**不同的**分類頁——有標題、正文八千字以上、
彼此也不相似，前兩條與第四條全都擋不住。量了三種候選訊號：

===================  =====================  =====================
訊號                 分類頁（4 個）         文章（3 個）
===================  =====================  =====================
正文長度             8.6k–10.3k             5.7k–9.7k  ← 重疊，沒用
每個連結的字數       39–63                  32–52      ← 重疊，沒用
文件層級發佈時間     **0 個**               **1 個**   ← 7/7 分得開
===================  =====================  =====================

瀏覽器抓法只拿得到 main 的 DOM（JSON-LD 在 ``<head>`` 會被切掉），但 ``<time datetime>``
在 main 裡面，實測 4/4 仍分得開，兩條抓取路徑都成立。

這條不是湊出來的啟發式：``published_at`` 是 ``Article`` 的**必填欄位**，樣本頁上
根本沒有日期時，它就沒辦法教模型日期在哪裡——這種樣本本來就不該送進產碼。
真的有站台的文章不帶結構化日期，用 ``validation.require_sample_date: false`` 關掉。

相似度用 word shingle 的 Jaccard 而不是 ``difflib``：樣本正文上看兩萬字，
``SequenceMatcher`` 是 O(n²)，在偵查階段拖掉的時間比它省下的多。
"""

from __future__ import annotations

import json
import re
from html.parser import HTMLParser
from typing import Any

# 這些標籤的內容不是給人看的正文，計字數與比相似度都要先拿掉，
# 否則兩個頁面會因為共用同一包 JS 而被判成「幾乎一樣」。
_NON_TEXT_TAGS = frozenset({"script", "style", "noscript", "template", "svg"})

_SHINGLE_SIZE = 5      # 連續 5 個詞為一組；短於此的正文改用整段比對
_WHITESPACE_RE = re.compile(r"\s+")


class _TextExtractor(HTMLParser):
    """從 HTML 取出標題與可見文字。

    刻意用標準庫的 HTMLParser：專案沒有 bs4 依賴，而這裡要的東西
    （title / h1 / 可見文字）不需要完整的 DOM。
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title = ""
        self.heading = ""
        self._chunks: list[str] = []
        self._skip_depth = 0
        self._capture: str | None = None

    def handle_starttag(self, tag: str, attrs) -> None:
        tag = tag.lower()
        if tag in _NON_TEXT_TAGS:
            self._skip_depth += 1
        elif tag == "title" and not self.title:
            self._capture = "title"
        elif tag == "h1" and not self.heading:
            self._capture = "heading"

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in _NON_TEXT_TAGS:
            self._skip_depth = max(0, self._skip_depth - 1)
        elif tag in {"title", "h1"}:
            self._capture = None

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        if self._capture == "title":
            self.title += data
        elif self._capture == "heading":
            self.heading += data
        self._chunks.append(data)

    @property
    def text(self) -> str:
        return _WHITESPACE_RE.sub(" ", " ".join(self._chunks)).strip()


def sample_signals(sample: dict[str, Any]) -> dict[str, Any]:
    """從一份明細樣本抽出驗證要用的三件事：標題、正文、最終網址。

    瀏覽器樣本已經有 ``title`` 與 ``text_excerpt``（Playwright 直接取得，
    比事後解析 HTML 準）；純 HTTP 樣本只有原始 HTML，才需要解析。
    """
    title = str(sample.get("title") or "").strip()
    text = str(sample.get("text_excerpt") or "").strip()

    if not title or not text:
        parser = _TextExtractor()
        try:
            parser.feed(str(sample.get("body_excerpt") or ""))
        except Exception:  # noqa: BLE001 — 壞掉的 HTML 只影響這一份樣本
            pass
        title = title or _WHITESPACE_RE.sub(" ", parser.title or parser.heading).strip()
        text = text or parser.text

    return {
        "url": str(sample.get("final_url") or sample.get("requested_url") or ""),
        "title": title,
        "text": text,
    }


def document_published_at(sample: dict[str, Any]) -> str | None:
    """樣本頁上文件層級的發佈時間原始值，找不到回 None。

    刻意沿用 ``evidence._META_DATE_RES``（``_probe_published_at`` 用的同一組樣式）：
    「哪些寫法算發佈時間」只能有一份定義，兩邊各寫一套遲早會對不起來。
    """
    from .evidence import _META_DATE_RES

    body = str(sample.get("body_excerpt") or "")
    for pattern in _META_DATE_RES:
        match = pattern.search(body)
        if match:
            return match.group(1).strip()
    return None


# 網址路徑裡的日期，例如 /2026/08/12/ 或 /2026-08-12-。
_URL_DATE_RE = re.compile(r"/20\d\d[-/]\d{1,2}[-/]\d{1,2}(?=[-/])")


def url_dated(url: str) -> bool:
    """網址路徑本身帶日期——這也是「這頁是某一天的文章」的結構證據。

    加這條是因為實測誤殺：科技新報的文章**完全沒有結構化日期**（沒有
    ``article:published_time``、沒有 JSON-LD、也沒有 ``<time>``），日期只寫在
    網址 ``/2026/08/12/`` 與頁面上「發布日期」旁的純文字裡。只認 meta 的話，
    整個站四種抓法全被判成分類頁——那是判準的問題，不是站台的問題。

    刻意只認**網址路徑**而不認頁面文字裡的日期：列表頁上每一則都有「2 小時前」，
    認文字會把分類頁放回來，等於把這關拆掉（BBC 實測就是靠這關擋住的）。
    """
    return bool(_URL_DATE_RE.search(str(url or "")))


def _shingles(text: str) -> set[tuple[str, ...]]:
    words = text.lower().split()
    if len(words) < _SHINGLE_SIZE:
        return {tuple(words)} if words else set()
    return {
        tuple(words[index : index + _SHINGLE_SIZE])
        for index in range(len(words) - _SHINGLE_SIZE + 1)
    }


def text_similarity(left: str, right: str) -> float:
    """兩段正文的 Jaccard 相似度（0=毫無交集，1=完全相同）。"""
    left_set, right_set = _shingles(left), _shingles(right)
    if not left_set or not right_set:
        return 0.0
    intersection = len(left_set & right_set)
    return intersection / len(left_set | right_set)


def _boilerplate(shingle_sets: list[set]) -> set:
    """每一份樣本都有的那些片語 —— 就是這個站每頁都在的導覽、側欄與推薦區。

    **三份以上才估得出來**：只有兩份時「共同的部分」就是相似度本身，
    扣掉等於把要量的東西扣掉，任何兩篇都會變成 0 分。
    """
    return set.intersection(*shingle_sets) if len(shingle_sets) >= 3 else set()


def _residual_similarity(left: set, right: set, boilerplate: set) -> float:
    """扣掉共站樣板之後再比，讓相似度反映的是文章而不是版面。

    實測中央社：三篇不同的文章，整頁文字的 Jaccard 高達 0.79（每頁約 600 組片語裡
    有 494 組是三篇共有的樣板），離「判定為同一頁」的門檻 0.9 只差一點點。
    扣掉樣板後降到 0.21，訊號才真的在講文章。
    """
    residual_left, residual_right = left - boilerplate, right - boilerplate
    if not residual_left or not residual_right:
        # 扣完什麼都不剩 = 這幾份的內容完全被「每份都有的東西」解釋掉了，
        # 那就是同一頁。這裡回 0 的話，最該擋的情況反而會過關。
        return 1.0
    return len(residual_left & residual_right) / len(residual_left | residual_right)


_TITLE_KEYS = ("title", "headline", "subject", "name")
_DATE_KEYS = (
    "published_at", "publishedat", "publishat", "published", "publishdate",
    "publish_date", "pubdate", "date", "datetime", "created_at", "updated_at",
)
_MIN_RECORD_TITLE = 6      # 標題短於這個多半是分類名或代號，不是新聞標題


def _record_fields(record: dict[str, Any]) -> tuple[str, Any]:
    """從一筆記錄裡取出標題與時間（鍵名大小寫與底線寫法都收）。"""
    lowered = {str(key).lower(): value for key, value in record.items()}
    title = next(
        (str(lowered[key]).strip() for key in _TITLE_KEYS
         if isinstance(lowered.get(key), str) and str(lowered[key]).strip()),
        "",
    )
    stamp = next((lowered[key] for key in _DATE_KEYS if lowered.get(key)), None)
    return title, stamp


def _walk_records(value: Any, out: list[dict], depth: int = 0) -> None:
    """把 JSON 裡長得像「一筆記錄」的 dict 撈出來（同 browser._json_evidence 的形狀規則）。"""
    if depth > 5 or len(out) >= 40:
        return
    if isinstance(value, dict):
        title, stamp = _record_fields(value)
        if title or stamp:
            out.append(value)
        for child in list(value.values())[:50]:
            _walk_records(child, out, depth + 1)
    elif isinstance(value, list):
        for child in value[:50]:
            _walk_records(child, out, depth + 1)


def verify_api_records(candidates: list[dict[str, Any]]) -> dict[str, Any]:
    """檢查二的前端資料介面版：那些「記錄」真的是文章嗎。

    **為什麼不能只數筆數**：``article_record_count`` 是靠 JSON 的鍵名形狀猜的
    （有 title 又有 url/date 就算一筆），**股價、指數、排行榜的陣列同樣會中**。
    實測貼了鉅亨網的台股行情頁（``/twstock``）：兩種 DOM 抓法都被檢查二正確擋下，
    卻在這一階放行，一路跑到產碼，花掉三次模型呼叫才被閘門攔住。

    所以這裡看的是內容而不是形狀，判準跟 HTML 那邊同一套：
    有像樣的標題、有解析得出來的時間、而且標題不能每筆都一樣。
    """
    from .evidence import _classify_datetime

    records: list[dict[str, Any]] = []
    for candidate in candidates:
        for item in candidate.get("feed_items") or []:
            records.append(dict(item))
        body = str(candidate.get("body_excerpt") or "")
        if body:
            try:
                _walk_records(json.loads(body), records)
            except (TypeError, ValueError):
                pass   # body 被截斷或不是 JSON：那就用不到它，不是錯誤

    titled = []
    dated = 0
    for record in records:
        title, stamp = _record_fields(record)
        if len(title) >= _MIN_RECORD_TITLE:
            titled.append(title)
        if stamp is not None and _classify_datetime(stamp)[0] not in {"missing", "unknown"}:
            dated += 1

    result = {
        "records": len(records),
        "with_title": len(titled),
        "with_date": dated,
        "distinct_titles": len(set(titled)),
    }
    # 一筆記錄也可能是合法的（清單就是短），所以不看筆數看性質：
    # 標題像不像標題、時間解不解析得出來。「標題不能全一樣」要有兩筆才問得出口。
    if not titled:
        return {**result, "passed": False,
                "reason": f"{len(records)} 筆記錄沒有一筆有像樣的標題（多半是報價或排行榜，不是文章清單）"}
    if not dated:
        return {**result, "passed": False,
                "reason": f"{len(records)} 筆記錄沒有一筆解析得出發佈時間，不像文章清單"}
    if len(titled) >= 2 and len(set(titled)) < 2:
        return {**result, "passed": False, "reason": "所有記錄的標題都一樣，不像文章清單"}
    return {**result, "passed": True, "reason": ""}


def _entry_urls(state: dict[str, Any]) -> set[str]:
    report = state.get("recon_report") or {}
    return {
        str(value).rstrip("/")
        for value in (
            state.get("site_url"),
            report.get("final_url"),
            report.get("canonical_url"),
        )
        if value
    }


def verify_detail_samples(
    samples: list[dict[str, Any]],
    state: dict[str, Any],
    *,
    max_similarity: float = 0.9,
) -> dict[str, Any]:
    """判定這批樣本是不是真的文章明細頁。

    回傳 ``passed`` 與**不過的具體原因**——原因會一路帶進死信，
    「卡在哪個檢查」要看得出來，不能只有一個 False。
    """
    validation = state.get("validation") or {}
    min_chars = int(validation.get("min_content_chars") or 200)
    require_date = validation.get("require_sample_date", True)
    entry_urls = _entry_urls(state)

    accepted: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for sample in samples:
        signals = sample_signals(sample)
        url = signals["url"]
        if sample.get("fetch_error"):
            rejected.append({"url": url, "reason": f"抓取失敗：{sample['fetch_error']}"[:160]})
            continue
        status = sample.get("status")
        if status is not None and int(status) >= 400:
            rejected.append({"url": url, "reason": f"HTTP {status}"})
            continue
        if url.rstrip("/") in entry_urls:
            # 導覽連結常常繞一圈又回到入口；這種樣本拿去學 selector 必錯。
            rejected.append({"url": url, "reason": "最終網址就是入口列表頁本身"})
            continue
        if not signals["title"]:
            rejected.append({"url": url, "reason": "沒有標題（無 <title> 也無 <h1>）"})
            continue
        if len(signals["text"]) < min_chars:
            rejected.append({
                "url": url,
                "reason": f"正文只有 {len(signals['text'])} 字，低於門檻 {min_chars} 字",
            })
            continue
        published_at = document_published_at(sample)
        if require_date and not published_at and not url_dated(url):
            # 分類頁跟文章在長度與連結密度上完全重疊，只有這條分得開（見模組說明）。
            rejected.append({
                "url": url,
                "reason": "沒有文件層級的發佈時間，網址也沒有日期，疑似分類頁而非文章明細頁",
            })
            continue
        accepted.append({
            **signals,
            "published_at": published_at,
            # 只有網址帶日期時要留下痕跡：那代表這個站沒有結構化日期，
            # 產碼要自己從頁面文字或網址解析，時區也得靠 source_timezone 補。
            "date_source": "document" if published_at else "url_path",
        })

    result: dict[str, Any] = {
        "checked": len(samples),
        "accepted": [row["url"] for row in accepted],
        "rejected": rejected,
        "min_content_chars": min_chars,
    }

    if not accepted:
        return {
            **result,
            "passed": False,
            "compared": False,
            "reason": "沒有任何一份樣本通過明細頁判準",
        }

    # 檢查二的核心是互相比對，只有一份就沒有東西可比。
    if len(accepted) < 2:
        if len(samples) >= 2:
            # 抓了兩份以上卻只有一份合格，多半是這種抓法挑到的根本不是明細頁。
            # cnyes 實測：三份裡兩份是分類頁，剩下那份也是分類頁——只是它剛好帶了
            # 每則新聞的 <time>，單獨看過得了關。放行等於讓錯的樣本教模型寫 selector。
            return {
                **result,
                "passed": False,
                "compared": False,
                "reason": (
                    f"{len(samples)} 份樣本裡只有 1 份通過明細頁判準，"
                    "無法互相比對——這種抓法多半挑到的不是明細頁"
                ),
            }
        # 本來就只抓得到一份時照實說「未比對」，不要假裝驗過。
        return {
            **result,
            "passed": True,
            "compared": False,
            "reason": "只有一份樣本可驗，無法互相比對（未驗證是否為列表頁）",
        }

    shingle_sets = [_shingles(row["text"]) for row in accepted]
    boilerplate = _boilerplate(shingle_sets)

    worst_pair: tuple[float, str, str] = (0.0, "", "")
    for index, left in enumerate(accepted):
        for offset, right in enumerate(accepted[index + 1 :], index + 1):
            score = _residual_similarity(
                shingle_sets[index], shingle_sets[offset], boilerplate
            )
            if score > worst_pair[0]:
                worst_pair = (score, left["url"], right["url"])

    similarity, left_url, right_url = worst_pair
    if similarity >= max_similarity:
        return {
            **result,
            "passed": False,
            "compared": True,
            "max_similarity": round(similarity, 3),
            "similar_pair": [left_url, right_url],
            "reason": (
                f"兩份樣本內容近乎相同（相似度 {similarity:.2f}）——"
                "多半是拿到同一個列表頁而不是文章明細頁"
            ),
        }

    return {
        **result,
        "passed": True,
        "compared": True,
        "max_similarity": round(similarity, 3),
        "boilerplate_shingles": len(boilerplate),
        "reason": "",
    }

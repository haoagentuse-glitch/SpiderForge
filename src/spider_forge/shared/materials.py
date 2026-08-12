"""將完整 EvidencePack 編譯成有限、去重、可生成程式碼的材料。

完整證據仍保存在 runtime；本模組只建立送給 coder 的唯讀投影。它不選策略、
不呼叫模型，也不修改 EvidencePack。
"""

from __future__ import annotations

import json
import re
from html.parser import HTMLParser
from typing import Any

_KEPT_ATTRIBUTES = {
    "class",
    "content",
    "datetime",
    "href",
    "id",
    "itemprop",
    "name",
    "property",
    "rel",
    "role",
    "type",
    "aria-label",
}
_DOM_MARKERS = (
    "post-content",
    "article-content",
    "article-body",
    "entry-content",
    "story-body",
    "rss-mr-content",
    "post-body",
    "<article",
    "<main",
)
_IGNORED_TAGS = {
    "iframe",
    "noscript",
    "script",
    "style",
    "svg",
    "template",
}


class _CompactHTMLParser(HTMLParser):
    """把 HTML 壓成「只剩 selector 需要的東西」。

    ``<script>`` 一律丟掉——除了 ``application/ld+json``。JSON-LD 不是程式碼，是結構化
    後設資料，而且**新聞站的發佈時間多半只寫在那裡**：實測經濟日報與 MoneyDJ 的
    ``datePublished`` 都在 JSON-LD 裡，連同 script 一起丟掉的話，模型在證據裡
    一個日期都看不到，卻被要求產出必填的 ``published_at``——只能用猜的。
    """

    _JSONLD_CHARS = 1500   # 夠涵蓋 headline/datePublished/author，不夠塞整份 schema

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.ignored_depth = 0
        self._jsonld_depth = 0

    def handle_starttag(self, tag: str, attrs) -> None:
        tag = tag.lower()
        if self.ignored_depth:
            self.ignored_depth += 1
            return
        if tag == "script":
            values = {str(name).lower(): str(value or "") for name, value in attrs}
            if "ld+json" in values.get("type", "").lower():
                self._jsonld_depth = 1
                self.parts.append('<script type="application/ld+json">')
                return
        if tag in _IGNORED_TAGS:
            self.ignored_depth = 1
            return
        kept = [
            (str(name).lower(), str(value))
            for name, value in attrs
            if str(name).lower() in _KEPT_ATTRIBUTES and value is not None
        ]
        serialized = "".join(
            f' {name}="{value.replace(chr(34), "&quot;")}"'
            for name, value in kept
        )
        self.parts.append(f"<{tag}{serialized}>")

    def handle_startendtag(self, tag: str, attrs) -> None:
        if self.ignored_depth or tag.lower() in _IGNORED_TAGS:
            return
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag: str) -> None:
        if self.ignored_depth:
            self.ignored_depth -= 1
            return
        if tag.lower() == "script" and self._jsonld_depth:
            self._jsonld_depth = 0
        self.parts.append(f"</{tag.lower()}>")

    def handle_data(self, data: str) -> None:
        if self.ignored_depth:
            return
        if self._jsonld_depth:
            # JSON-LD 原樣保留（只壓空白、限長度）：日期與標題的鍵名要看得出來
            self.parts.append(" ".join(data.split())[: self._JSONLD_CHARS])
            return
        text = " ".join(data.split())
        if text:
            self.parts.append(text)


_TEXT_RUN = re.compile(r">([^<>]{40,})<")


def _densest_text_anchor(compact: str) -> int:
    """整份文件裡**最長的一段純文字**在哪裡——那就是內文。

    明細頁的日期與標題在 ``<head>``／JSON-LD，內文在很後面。拿日期當錨點的話，
    裁出來的七千字全是頁首與導覽的標籤（實測 MoneyDJ／經濟日報：七千字裡只有
    七十幾個字是看得見的文字），模型看得到日期卻看不到內文，content 的 selector
    只能用猜的。改用「最長的一段文字」當錨點就不必認得任何站台的版型。
    """
    best_position, best_length = 0, 0
    for match in _TEXT_RUN.finditer(compact):
        if len(match.group(1)) > best_length:
            best_position, best_length = match.start(), len(match.group(1))
    return best_position


_JSONLD_KEYS = ("datepublished", "headline", "newsarticle")


def jsonld_metadata(html: str, *, max_chars: int = 2000) -> str:
    """抽出 JSON-LD 區塊——新聞站的標題與發佈時間多半只寫在這裡。

    單獨成一個欄位而不是靠裁切碰運氣：它在 ``<head>``，內文在頁面很後面，
    同一個七千字視窗不可能同時涵蓋兩者。

    太長時**以 ``datePublished`` 為中心**裁，不從頭裁：經濟日報的 JSON-LD 是一份
    5,423 字的 ``@graph``，開頭是 BreadcrumbList，``datePublished`` 在第 3,450 字——
    從頭取前 1,500 字的話，剛好把唯一的日期來源切掉。
    """
    blocks = re.findall(
        r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
        str(html or ""),
        re.IGNORECASE | re.DOTALL,
    )
    joined = " ".join(" ".join(block.split()) for block in blocks)
    if len(joined) <= max_chars:
        return joined
    lower = joined.lower()
    found = [lower.find(key) for key in _JSONLD_KEYS if lower.find(key) >= 0]
    anchor = min(found) if found else 0
    start = max(0, anchor - max_chars // 3)
    return joined[start : start + max_chars]


def _path_of(url: str) -> str:
    """取網址的 path（含 query）——列表頁的 href 多半是相對路徑，比對絕對網址會找不到。"""
    from urllib.parse import urlsplit

    parts = urlsplit(str(url or ""))
    return f"{parts.path}?{parts.query}" if parts.query else parts.path


def compact_dom_html(
    html: str, *, max_chars: int = 7000, anchors: list[str] | None = None
) -> str:
    """保留 selector 所需屬性與文字順序，移除生成無用的展示屬性。

    ``anchors`` 是**這一次執行真的驗證過的字串**（文章網址、發佈時間原始值），
    比 ``_DOM_MARKERS`` 那些通用的 CMS class 名優先。理由是實測出來的：
    中央社的版面一個通用標記都沒中，於是錨點退回 0，裁出來的七千字全是頁首與導覽——
    模型看不到任何一個文章連結，也看不到內文，等於要它憑空猜 selector。
    用「已知的文章網址」當錨點就不會有這個問題，而且不必為任何站台寫死字串。
    """
    parser = _CompactHTMLParser()
    try:
        parser.feed(str(html or ""))
    except Exception:
        return str(html or "")[:max_chars]
    compact = "".join(parser.parts)
    if len(compact) <= max_chars:
        return compact
    lower = compact.lower()
    found = [
        lower.find(marker.lower())
        for marker in [*(anchors or []), *_DOM_MARKERS]
        if marker and lower.find(marker.lower()) >= 0
    ]
    # 什麼標記都沒中就退到「最長的一段文字」，而不是從第 0 個字開始——
    # 從頭裁的話拿到的永遠是 <head> 與導覽。
    anchor = min(found) if found else _densest_text_anchor(compact)
    start = max(0, anchor - 1600)
    return compact[start : start + max_chars]


def _compact_replay(pack: dict[str, Any]) -> dict[str, Any]:
    exchange = pack.get("replay_exchange") or {}
    response = dict(exchange.get("response") or {})
    api_sample = pack.get("api_sample") or {}
    structured_format = str(api_sample.get("structured_format") or "")
    body_limit = 4500 if structured_format == "rss_or_atom" else 8000
    response["body_excerpt"] = str(response.get("body_excerpt") or "")[
        :body_limit
    ]
    return {
        "request": exchange.get("request") or {},
        "response": response,
    }


def compile_generation_materials(
    evidence_pack: dict[str, Any],
) -> dict[str, Any]:
    """建立單一、無重複的 coder 輸入；不帶未選 feed 與完整偵察雜訊。"""
    entry = evidence_pack.get("entry_observation") or {}
    api_sample = evidence_pack.get("api_sample") or {}
    detail_urls = [
        str(url) for url in (evidence_pack.get("discovered_detail_urls") or []) if url
    ]
    dom_samples = [
        {
            "requested_url": sample.get("requested_url"),
            "final_url": sample.get("final_url"),
            "status": sample.get("status"),
            "capture_source": sample.get("capture_source"),
            # 標題與發佈時間單獨給：它們在 <head>／JSON-LD，內文在頁面很後面，
            # 同一個七千字視窗不可能同時涵蓋兩者。
            "metadata_jsonld": jsonld_metadata(str(sample.get("body_excerpt") or "")),
            # 內文視窗不給錨點，讓它自己找最長的一段文字（＝內文）。
            "body_excerpt": compact_dom_html(str(sample.get("body_excerpt") or "")),
        }
        for sample in (evidence_pack.get("dom_samples") or [])[:2]
    ]
    structured_source = {
        key: api_sample.get(key)
        for key in (
            "method",
            "url",
            "requested_url",
            "final_url",
            "status",
            "content_type",
            "structured_format",
            "json_shape",
            "article_record_count",
            "record_detection",
            "capture_source",
        )
        if api_sample.get(key) is not None
    }
    if api_sample.get("feed_items"):
        structured_source["feed_items"] = (
            api_sample.get("feed_items") or []
        )[:12]
    materials = {
        "request": evidence_pack.get("request") or {},
        "strategy": evidence_pack.get("strategy") or {},
        # 偵查子迴圈實際驗證過的抓法：證據是用哪一種傳輸取得的，產出的爬蟲就得用哪一種。
        # 少了這一項，用瀏覽器才看得到的連結會被寫成純 HTTP 抓，等於白驗一場。
        "fetch_strategy": evidence_pack.get("fetch_strategy"),
        "replay_exchange": _compact_replay(evidence_pack),
        "structured_source": structured_source,
        "entry": {
            "requested_url": entry.get("requested_url"),
            "final_url": entry.get("final_url"),
            "canonical_url": entry.get("canonical_url"),
            "browser_status": entry.get("browser_status"),
            "http_status": entry.get("http_status"),
            "access_assessment": entry.get("access_assessment"),
            "safe_request_headers": entry.get("safe_request_headers"),
            # 錨點用已經驗證過的文章網址（含只留路徑的版本，因為列表頁的 href
            # 多半是相對路徑）：模型要學的就是「文章連結在這個版面長什麼樣」，
            # 裁到頁首的話它只看得到一堆 <meta>。
            "html_excerpt": compact_dom_html(
                str(entry.get("html_excerpt") or ""),
                max_chars=2200,
                anchors=[
                    *detail_urls,
                    *(_path_of(url) for url in detail_urls),
                ],
            ),
            "link_samples": (entry.get("link_samples") or [])[:20],
        },
        "discovered_detail_urls": (
            evidence_pack.get("discovered_detail_urls") or []
        )[:10],
        "dom_samples": dom_samples,
        "pagination": evidence_pack.get("pagination") or {},
        "published_at_probe": (
            evidence_pack.get("published_at_probe") or {}
        ),
        "replay_headers": evidence_pack.get("replay_headers") or {},
        "requirements": evidence_pack.get("requirements") or [],
        "unresolved": evidence_pack.get("unresolved") or [],
    }
    serialized = json.dumps(materials, ensure_ascii=False)
    materials["material_budget"] = {
        "serialized_chars": len(serialized),
        "dom_sample_count": len(dom_samples),
        "detail_url_count": len(materials["discovered_detail_urls"]),
        "unselected_feed_candidates_included": 0,
    }
    return materials

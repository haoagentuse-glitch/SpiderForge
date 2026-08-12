"""抓法階梯 —— 一種抓法決定「連結從哪裡來」與「明細怎麼抓」。

前期偵查原本是一條直線：把瀏覽器與純 HTTP 兩個連結池混在一起挑，挑到什麼就往下送，
問題留到產碼之後才爆，而那時的診斷會怪錯對象（BBC 那次被歸成 selector 寫錯，
真正的原因是樣本根本不是文章）。

改成階梯之後，**一次只用一種抓法**，三道檢查全過才往下放；不過就換下一種重來。
順序由確定性規則決定，不問模型：

=========================  ==========================  ==================
抓法                       連結來源                    明細抓法
=========================  ==========================  ==================
``http_links``             純 HTTP 入口頁的 ``<a>``    純 HTTP
``browser_links``          瀏覽器渲染後的 ``<a>``      瀏覽器
``browser_scroll_links``   捲動載入更多之後的 ``<a>``  瀏覽器
``api``                    前端資料介面／feed 的記錄   純 HTTP
=========================  ==========================  ==================

由便宜到貴：純 HTTP 不開瀏覽器最省，捲動要等載入最慢，前端資料介面則要先偵測到
才排得進來。**能不能用是事實不是判斷**——每一階的可用性都直接看 recon 的證據，
沒有可調參數。
"""

from __future__ import annotations

from typing import Any

HTTP_LINKS = "http_links"
BROWSER_LINKS = "browser_links"
BROWSER_SCROLL_LINKS = "browser_scroll_links"
API_RECORDS = "api"

# 由便宜到貴；``available_strategies`` 只會從這個順序裡篩掉不可用的，不會重排。
LADDER = (HTTP_LINKS, BROWSER_LINKS, BROWSER_SCROLL_LINKS, API_RECORDS)

LABELS = {
    HTTP_LINKS: "直接連線 ＋ 頁面連結",
    BROWSER_LINKS: "瀏覽器渲染 ＋ 頁面連結",
    BROWSER_SCROLL_LINKS: "瀏覽器捲動 ＋ 頁面連結",
    API_RECORDS: "前端資料介面",
}


def _http_links(report: dict[str, Any]) -> list[dict]:
    return list((report.get("http_entry_sample") or {}).get("link_samples") or [])


def _browser_links(report: dict[str, Any]) -> list[dict]:
    return list(report.get("link_samples") or [])


def _scroll_links(report: dict[str, Any]) -> list[dict]:
    return list(report.get("scroll_link_samples") or [])


def selected_structured(state: dict[str, Any]) -> list[dict]:
    """這一次要用的結構化來源（前端資料介面／feed）。

    ``strategy_decision`` 選定了 ``chosen_api`` 就只用那一個，沒選定才把所有候選
    攤平——跟 ``_discover_detail_urls`` 同一套規則，三個呼叫端共用這一份定義。
    """
    report = state.get("recon_report") or {}
    chosen_api = str((state.get("strategy_detail") or {}).get("chosen_api") or "")
    candidates = [
        *(report.get("api_candidates") or []),
        *(report.get("feed_candidates") or []),
    ]
    if chosen_api:
        candidates = [
            row for row in candidates if str(row.get("url") or "") == chosen_api
        ]
    return candidates


def _api_rows(state: dict[str, Any], report: dict[str, Any]) -> list[dict]:
    """前端資料介面／feed 記錄裡的文章連結。"""
    candidates = selected_structured(state)
    return [
        {"url": item.get("url"), "text": item.get("title") or ""}
        for candidate in candidates
        for item in candidate.get("feed_items") or []
        if item.get("url")
    ]


def api_record_count(state: dict[str, Any]) -> int:
    """選定的前端資料介面裡有幾筆文章記錄。

    純 JSON 介面常常**沒有**每篇一個的明細頁連結——記錄本身就帶標題、時間與內容。
    這種來源的「檢查一」問的不是「挑不挑得到連結」，而是「有沒有文章記錄」；
    沒有這個數字的話，一個完全可用的 API 站會因為挑不到連結而被判死。
    """
    from .evidence import _is_replayable_article_api

    return sum(
        int(row.get("article_record_count") or 0)
        for row in selected_structured(state)
        if _is_replayable_article_api(row) or row.get("feed_items")
    )


def link_pool(state: dict[str, Any], strategy: str | None = None) -> list[dict]:
    """目前抓法的連結池。

    ``strategy`` 是 None（還沒進階梯，或單獨呼叫節點）時退回舊行為——兩個池合併——
    這樣既有呼叫端與測試不受影響。
    """
    report = state.get("recon_report") or {}
    strategy = strategy or state.get("fetch_strategy")
    if strategy == HTTP_LINKS:
        return _http_links(report)
    if strategy == BROWSER_LINKS:
        return _browser_links(report)
    if strategy == BROWSER_SCROLL_LINKS:
        return _scroll_links(report)
    if strategy == API_RECORDS:
        return _api_rows(state, report)
    return [*_browser_links(report), *_http_links(report)]


def uses_browser_transport(strategy: str | None) -> bool:
    """這種抓法的明細樣本要不要用瀏覽器抓。

    抓連結與抓明細必須用**同一種**傳輸：用瀏覽器才看得到的連結，
    用純 HTTP 去抓明細多半會拿到不一樣的東西，驗過的樣本就不算數了。
    """
    return strategy in {BROWSER_LINKS, BROWSER_SCROLL_LINKS}


def available_strategies(state: dict[str, Any]) -> list[str]:
    """依 recon 證據判定哪幾階真的可用，維持 ``LADDER`` 的順序。"""
    from .evidence import _is_replayable_article_api

    report = state.get("recon_report") or {}
    http_sample = report.get("http_entry_sample") or {}
    browser_status = report.get("original_browser_status", report.get("http_status"))
    browser_usable = bool(browser_status and int(browser_status) < 400)

    structured = [
        *(report.get("api_candidates") or []),
        *(report.get("feed_candidates") or []),
    ]
    usable = {
        HTTP_LINKS: http_sample.get("status") == 200 and bool(_http_links(report)),
        BROWSER_LINKS: browser_usable and bool(_browser_links(report)),
        # 捲動要真的多載入了連結才算一階。但「捲不捲得出東西」只有捲了才知道，
        # 而捲動要開瀏覽器等載入——不能為了排這份清單就每一站都先付這個成本。
        # 所以還沒探測時先當它可用（選到才真的去捲，見 nodes/fetch_strategy.py），
        # 探測過就用事實判斷：沒增加就跟上一階一模一樣，排進來只是浪費一輪。
        #
        # 前提是**瀏覽器抓法本身抓得到連結**：捲動是它的加強版，不是另一條路。
        # 沒有這個前提的話，連一個 <a> 都沒有的頁面也會被捲一次才發現沒用——
        # 那是白開一次瀏覽器（離線測試也會因此真的連網）。
        BROWSER_SCROLL_LINKS: (
            browser_usable
            and bool(_browser_links(report))
            and (
                "scroll_link_samples" not in report
                or len(_scroll_links(report)) > len(_browser_links(report))
            )
        ),
        API_RECORDS: any(
            _is_replayable_article_api(row) or row.get("feed_items")
            for row in structured
        ),
    }
    return [strategy for strategy in LADDER if usable[strategy]]

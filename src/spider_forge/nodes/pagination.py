"""verify_pagination 節點：偵測到翻頁訊號還不夠，要**實際抓第 2 頁**確認它有效。

**為什麼偵測不等於可用**：很多站的 `?page=999` 會回第 1 頁的內容（無效參數被忽略）、
或回一頁空列表。光看到 URL 帶 `page=` 就寫進 evidence，產碼模型會照著寫翻頁邏輯，
一路到 sandbox 實跑才發現翻不動——而那時的診斷會歸咎於 selector 或數量不足，
跟真正的原因（翻頁機制無效）差很遠。

所以這裡採跟 discover_links 一樣的形狀：**候選 → 逐一驗證 → 確定才往下放**。

驗證是純程式，三個條件（都不需要模型）：

1. 第 2 頁 HTTP 200
2. 第 2 頁**有**文章連結（沿用 validation 的 URL 規則）
3. 第 2 頁的文章連結**與第 1 頁不同**——這條最關鍵，`?page=999` 回第 1 頁時
   前兩條都會過，只有這條擋得住

全部候選都失敗就誠實降級成 ``none_detected``（只抓第 1 頁），不臆造。

捲動抓法是唯一的例外：它的「翻頁」是無限捲動，證據在選這一階時就已經量到了
（捲到底幾次、每次各有幾個連結），不必也無法用第 2 頁的 URL 去驗。
"""

from __future__ import annotations

from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse

from ..shared.evidence import (
    _CURSOR_MARKERS,
    _NEXT_HREF_RE,
    _NEXT_LINK_RE,
    _PAGINATION_PARAMS,
    _matches_validation_url,
)
from ..shared.fetch_strategies import BROWSER_SCROLL_LINKS
from ..state import SpiderForgeState
from .base import Node


def _with_param(url: str, key: str, value: str) -> str:
    """把 query 參數換成指定值（沒有就加上）。"""
    parsed = urlparse(url)
    params = [(k, v) for k, v in parse_qsl(parsed.query, keep_blank_values=True)
              if k.lower() != key.lower()]
    params.append((key, value))
    return urlunparse(parsed._replace(query=urlencode(params)))


def _pagination_candidates(
    *, chosen_api: str, entry_url: str, api_body: str, entry_html: str,
    link_samples: list | None = None,
) -> list[dict]:
    """所有命中的翻頁訊號，依可靠度排序（不像 _detect_pagination 只回第一個）。"""
    candidates: list[dict] = []

    for source_url in (chosen_api, entry_url):
        query = urlparse(str(source_url or "")).query
        for key, _ in parse_qsl(query, keep_blank_values=True):
            if key.lower() in _PAGINATION_PARAMS:
                candidates.append({
                    "type": "query_param", "param": key.lower(),
                    "example_url": str(source_url), "discovered_in": "entry_url",
                    "note": f"以 {key.lower()} 遞增翻頁；遵守 constraints.max_pages 上限。",
                })
                break

    body_lower = str(api_body or "").lower()
    cursor_hit = next((m for m in _CURSOR_MARKERS if m in body_lower), None)
    if cursor_hit:
        candidates.append({
            "type": "cursor", "marker": cursor_hit.strip('"'),
            "note": f"回應含 {cursor_hit.strip(chr(34))} 之類的游標鍵；沿用其值請下一頁。",
        })

    link_tag = _NEXT_LINK_RE.search(str(entry_html or ""))
    if link_tag:
        href = _NEXT_HREF_RE.search(link_tag.group(0))
        candidates.append({
            "type": "next_link",
            "example_url": urljoin(str(entry_url or ""), href.group(1)) if href else None,
            "note": "HTML 有 <link rel=next>；沿用其 href 當下一頁。",
        })

    for row in link_samples or []:
        url = str((row or {}).get("url") or "")
        for key, _ in parse_qsl(urlparse(url).query, keep_blank_values=True):
            if key.lower() in _PAGINATION_PARAMS:
                candidates.append({
                    "type": "query_param", "param": key.lower(),
                    "example_url": url, "discovered_in": "listing_page_link",
                    "note": (f"列表頁有帶 {key.lower()} 的頁碼連結；以該參數遞增翻頁，"
                             "遵守 constraints.max_pages 上限。"),
                })
                break
        else:
            continue
        break

    return candidates


class VerifyPagination(Node):
    """驗證翻頁訊號真的能翻到不同的一頁，確定了才寫進 state。

    ``fetcher`` 可注入（測試用）：``(url) -> {"status": int, "link_samples": [...]}``。
    預設 None → 呼叫時才 late-bind ``shared.evidence._fetch_sample``。
    """

    def __init__(self, *, fetcher=None, max_probe: int = 2):
        self._fetcher = fetcher
        self._max_probe = max_probe   # 最多實抓幾個候選，避免慢站拖垮流程

    def _next_page_url(self, candidate: dict, entry_url: str) -> str | None:
        if candidate["type"] == "query_param":
            base = candidate.get("example_url") or entry_url
            return _with_param(base, candidate["param"], "2")
        if candidate["type"] == "next_link":
            return candidate.get("example_url")
        return None   # cursor 型要有第一頁回應才算得出下一頁，這裡不實抓

    def _article_links(self, rows: list, state: SpiderForgeState) -> set[str]:
        return {
            str(r.get("url") or "")
            for r in rows or []
            if r.get("url") and _matches_validation_url(str(r["url"]), state)
        }

    def __call__(self, state: SpiderForgeState) -> dict:
        report = state.get("recon_report") or {}
        entry_http = report.get("http_entry_sample") or {}
        entry_url = str(report.get("final_url") or state.get("site_url") or "")

        # 走捲動抓法時，「翻頁」就是無限捲動本身：連結數有沒有隨著捲動增加，
        # 選這一階時已經實際捲過並量到了（見 nodes/fetch_strategy.py），
        # 不需要再實抓第 2 頁——這一階能被選上，前提就是它捲得出更多。
        if state.get("fetch_strategy") == BROWSER_SCROLL_LINKS:
            scroll = report.get("scroll_probe") or {}
            counts = scroll.get("links_after_each_round") or []
            return {
                "pagination": {
                    "type": "infinite_scroll",
                    "verified": bool(scroll.get("loaded_more")),
                    "rounds_scrolled": scroll.get("rounds_scrolled"),
                    "links_after_each_round": counts,
                    "note": (
                        "無限捲動：捲到底會載入更多文章，"
                        f"實測每輪連結數 {counts}；遵守 constraints.max_pages 當作捲動輪數上限。"
                    ),
                },
                "pagination_probe": {
                    "candidates": 1,
                    "verified": bool(scroll.get("loaded_more")),
                    "attempts": [{"type": "infinite_scroll", "result": f"連結數 {counts}"}],
                },
            }

        candidates = _pagination_candidates(
            chosen_api=str((state.get("strategy_detail") or {}).get("chosen_api") or ""),
            entry_url=entry_url,
            api_body=str(entry_http.get("body_excerpt") or ""),
            entry_html=str(entry_http.get("body_excerpt") or ""),
            link_samples=[
                *(report.get("link_samples") or []),
                *(entry_http.get("link_samples") or []),
            ],
        )
        if not candidates:
            return {
                "pagination": {"type": "none_detected",
                               "note": "未偵測到確定性翻頁訊號；預設只抓第 1 頁。"},
                "pagination_probe": {"candidates": 0, "verified": False,
                                     "reason": "沒有任何翻頁訊號"},
            }

        page_one = self._article_links(
            [*(report.get("link_samples") or []), *(entry_http.get("link_samples") or [])],
            state,
        )
        fetcher = self._fetcher
        if fetcher is None:
            from ..shared.evidence import _fetch_sample

            def fetcher(url):  # noqa: E306
                return _fetch_sample(url, max_chars=6000, max_links=120)

        attempts = []
        for candidate in candidates[: self._max_probe]:
            url = self._next_page_url(candidate, entry_url)
            if not url:
                # cursor 型無法離線驗證，但訊號本身是確定性的（body 有游標鍵），
                # 標記為未驗證後照樣往下放——擋掉它會讓 API 型的站失去翻頁。
                attempts.append({"type": candidate["type"], "url": None,
                                 "result": "unverifiable_but_deterministic"})
                return {
                    "pagination": {**candidate, "verified": False},
                    "pagination_probe": {"candidates": len(candidates),
                                         "verified": False,
                                         # 沒驗過，但訊號本身是確定性的（body 真的有游標鍵）。
                                         # 少了這一欄，偵查子迴圈會把它當成「翻頁壞掉」而換掉
                                         # 一個其實可用的抓法。
                                         "deterministic": True,
                                         "attempts": attempts,
                                         "reason": "cursor 型無法預先實抓，訊號本身確定性"},
                }
            try:
                sample = fetcher(url) or {}
            except Exception as exc:  # noqa: BLE001 — 驗證失敗不該中斷流程
                attempts.append({"url": url, "result": f"fetch_error: {str(exc)[:120]}"})
                continue

            status = sample.get("status")
            page_two = self._article_links(sample.get("link_samples") or [], state)
            fresh = page_two - page_one

            if status != 200:
                attempts.append({"url": url, "result": f"http_{status}"})
                continue
            if not page_two:
                attempts.append({"url": url, "result": "第 2 頁沒有文章連結"})
                continue
            if not fresh:
                # 最關鍵的一條：?page=999 被忽略時會回第 1 頁，前兩條都會過
                attempts.append({"url": url, "result": "第 2 頁的文章與第 1 頁完全相同"})
                continue

            attempts.append({"url": url, "result": f"ok，{len(fresh)} 篇新文章"})
            return {
                "pagination": {**candidate, "verified": True,
                               "verified_url": url, "new_articles_on_page_2": len(fresh)},
                "pagination_probe": {"candidates": len(candidates), "verified": True,
                                     "attempts": attempts},
            }

        return {
            "pagination": {"type": "none_detected",
                           "note": "偵測到翻頁訊號但實抓第 2 頁未通過驗證；只抓第 1 頁。"},
            "pagination_probe": {"candidates": len(candidates), "verified": False,
                                 "attempts": attempts,
                                 "reason": "所有候選都沒通過實抓驗證"},
        }

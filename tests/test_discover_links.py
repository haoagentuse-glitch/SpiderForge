"""discover_links 節點：用 BBC 實抓的連結鎖住「導覽 vs 文章」的挑選行為。

背景：改動前 `_discover_detail_urls` 按 DOM 順序取前 2 個連結當文章樣本，而導覽列
在 HTML 裡必然排在內文之前——BBC 實測前 25 筆全是導覽，真文章在第 26–30 筆。
樣本錯了，產碼模型就在錯的基礎上學 selector，修復迴圈用同一份證據所以修不好。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from spider_forge.nodes.discover_links import DiscoverArticleLinks, _heuristic_score

_FIXTURE = json.loads(
    (Path(__file__).parent / "fixtures" / "bbc_business_links.json").read_text(
        encoding="utf-8"
    )
)
_IS_ARTICLE = re.compile(r"/articles/[a-z0-9]{6,}")
_HEURISTIC = lambda rows: [  # noqa: E731 — 注入用的小 lambda
    r["index"]
    for r in sorted(rows, key=lambda r: _heuristic_score(r["url"], r["text"]), reverse=True)
]


def _state(**validation) -> dict:
    return {
        "site_url": _FIXTURE["site_url"],
        "recon_report": {
            "link_samples": _FIXTURE["link_samples"],
            "final_url": _FIXTURE["site_url"],
        },
        "validation": {"allowed_domains": ["www.bbc.com"], **validation},
    }


def t_old_behaviour_picks_navigation_not_articles():
    """釘住問題本身：舊路徑（DOM 順序）在這份真實資料上是 0/2。

    這個測試存在的意義是——如果有人把 discover_links 拿掉退回舊行為，
    下面那些「修好了」的測試才不會顯得沒來由。
    """
    from spider_forge.shared.evidence import _discover_detail_urls

    state = _state()
    picked = _discover_detail_urls(state, state["recon_report"])
    hits = [u for u in picked if _IS_ARTICLE.search(u)]
    return not hits and len(picked) == 2, f"舊行為挑到={picked}"


def t_hard_exclusion_drops_anchors_and_homepage():
    """① 硬性排除：入口自己、#錨點、首頁——純結構事實，不需要模型。"""
    node = DiscoverArticleLinks()
    rows = node._candidates(_state())
    urls = [r["url"] for r in rows]
    return (
        len(rows) < len(_FIXTURE["link_samples"])
        and not any("#" in u for u in urls)
        and "https://www.bbc.com/" not in urls
        and _FIXTURE["site_url"] not in urls
    ), f"候選 {len(rows)}/{len(_FIXTURE['link_samples'])}"


def t_heuristic_fallback_finds_real_articles():
    """③ 的最後一層 fallback：三個模型都不可用時，啟發式仍要挑到真文章。"""
    out = DiscoverArticleLinks(limit=2, picker=_HEURISTIC)(_state())
    picked = out["discovered_detail_urls"]
    hits = [u for u in picked if _IS_ARTICLE.search(u)]
    return len(hits) == 2, f"picked={picked}"


def t_url_patterns_control_which_section():
    """② URL pattern 是「哪個版面」的唯一控制手段——模型做不到這件事。

    零設定時啟發式會挑到體育新聞（它確實是文章，只是不是商業版面的）；
    給了 pattern 才拿得到商業新聞。
    """
    without = DiscoverArticleLinks(limit=2, picker=_HEURISTIC)(_state())
    with_patterns = DiscoverArticleLinks(limit=2, picker=_HEURISTIC)(
        _state(
            article_url_patterns=[r"/articles/[a-z0-9]{6,}"],
            excluded_url_patterns=["/sport/"],
        )
    )
    picked_without = without["discovered_detail_urls"]
    picked_with = with_patterns["discovered_detail_urls"]
    return (
        any("/sport/" in u for u in picked_without)
        and not any("/sport/" in u for u in picked_with)
        and all(_IS_ARTICLE.search(u) for u in picked_with)
        and with_patterns["link_discovery"]["candidates"] < without["link_discovery"]["candidates"]
    ), f"無 pattern={picked_without} 有 pattern={picked_with}"


def t_supplied_sample_urls_always_win():
    """使用者明給的 sample_urls 優先於任何自動挑選。"""
    state = {**_state(), "sample_urls": ["https://www.bbc.com/news/articles/zzzzzzzzzz"]}
    out = DiscoverArticleLinks(limit=2, picker=_HEURISTIC)(state)
    return out["discovered_detail_urls"][0].endswith("zzzzzzzzzz"), (
        f"picked={out['discovered_detail_urls']}"
    )


def t_no_candidates_reports_reason_instead_of_crashing():
    """全被過濾掉時要留下可讀的原因，不是靜默回空。"""
    out = DiscoverArticleLinks()(
        _state(article_url_patterns=[r"/this-pattern-matches-nothing/"])
    )
    discovery = out["link_discovery"]
    return (
        out["discovered_detail_urls"] == []
        and discovery["method"] == "none"
        and discovery["candidates"] == 0
        and "沒有候選" in discovery["reason"]
    ), f"discovery={discovery}"


TESTS = [
    t_old_behaviour_picks_navigation_not_articles,
    t_hard_exclusion_drops_anchors_and_homepage,
    t_heuristic_fallback_finds_real_articles,
    t_url_patterns_control_which_section,
    t_supplied_sample_urls_always_win,
    t_no_candidates_reports_reason_instead_of_crashing,
]

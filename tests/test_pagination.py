"""verify_pagination：偵測到翻頁訊號不等於它有效，要實抓第 2 頁確認。

踩過的坑（設計動機）：很多站的 `?page=999` 會回第 1 頁的內容（無效參數被忽略），
或回一頁空列表。光看到 URL 帶 `page=` 就寫進 evidence，產碼模型會照著寫翻頁邏輯，
一路到 sandbox 實跑才發現翻不動——而那時的診斷會歸咎於 selector 或數量不足。
"""

from __future__ import annotations

from spider_forge.nodes.pagination import VerifyPagination, _with_param

_PAGE_ONE = [
    {"url": "https://e.com/news/a/1001", "text": "第一頁的文章一"},
    {"url": "https://e.com/news/a/1002", "text": "第一頁的文章二"},
    {"url": "https://e.com/news?page=2", "text": "2"},
]


def _state(**extra) -> dict:
    return {
        "site_url": "https://e.com/news",
        "recon_report": {
            "final_url": "https://e.com/news",
            "link_samples": _PAGE_ONE,
            "http_entry_sample": {"status": 200, "body_excerpt": "", "link_samples": []},
        },
        "validation": {
            "allowed_domains": ["e.com"],
            "article_url_patterns": [r"/news/a/\d+"],
        },
        **extra,
    }


def t_with_param_replaces_not_appends():
    """?page=1 換成 ?page=2，不能變成 ?page=1&page=2。"""
    replaced = _with_param("https://e.com/n?page=1&x=9", "page", "2")
    added = _with_param("https://e.com/n?x=9", "page", "2")
    return (
        replaced.count("page=") == 1 and "page=2" in replaced and "x=9" in replaced
        and "page=2" in added and "x=9" in added
    ), f"replaced={replaced} added={added}"


def t_verified_when_page_two_has_new_articles():
    """正常情況：第 2 頁有第 1 頁沒有的文章 → 通過，寫進 state。"""
    def fetcher(url):
        return {"status": 200, "link_samples": [
            {"url": "https://e.com/news/a/2001"},
            {"url": "https://e.com/news/a/2002"},
        ]}

    out = VerifyPagination(fetcher=fetcher)(_state())
    pagination = out["pagination"]
    return (
        pagination["type"] == "query_param"
        and pagination["param"] == "page"
        and pagination["verified"] is True
        and pagination["new_articles_on_page_2"] == 2
        and out["pagination_probe"]["verified"] is True
    ), f"pagination={pagination}"


def t_rejected_when_page_two_repeats_page_one():
    """**最關鍵的一條**：?page= 被站方忽略、回同一頁內容。

    HTTP 200 ✓、有文章連結 ✓——前兩個條件都過，只有「與第 1 頁比對」擋得住。
    擋不住的話，產碼模型會寫出一個翻頁翻到自己的爬蟲。
    """
    def fetcher(url):
        return {"status": 200, "link_samples": _PAGE_ONE}   # 原封不動回第 1 頁

    out = VerifyPagination(fetcher=fetcher)(_state())
    probe = out["pagination_probe"]
    return (
        out["pagination"]["type"] == "none_detected"
        and probe["verified"] is False
        and any("完全相同" in a.get("result", "") for a in probe["attempts"])
    ), f"probe={probe}"


def t_rejected_when_page_two_is_empty_or_errors():
    """第 2 頁沒有文章連結、或非 200，都不算通過。"""
    empty = VerifyPagination(fetcher=lambda url: {"status": 200, "link_samples": []})(_state())
    error = VerifyPagination(fetcher=lambda url: {"status": 404, "link_samples": []})(_state())
    crash = VerifyPagination(fetcher=lambda url: (_ for _ in ()).throw(RuntimeError("timeout")))(_state())
    return (
        empty["pagination"]["type"] == "none_detected"
        and error["pagination"]["type"] == "none_detected"
        and crash["pagination"]["type"] == "none_detected"
        and "fetch_error" in crash["pagination_probe"]["attempts"][0]["result"]
    ), (
        f"empty={empty['pagination_probe']['attempts']} "
        f"error={error['pagination_probe']['attempts']} "
        f"crash={crash['pagination_probe']['attempts']}"
    )


def t_no_signal_means_first_page_only():
    """沒有任何翻頁訊號時不臆造，也不該白白發出請求。"""
    fetched = []
    state = _state()
    state["recon_report"]["link_samples"] = [{"url": "https://e.com/news/a/1001", "text": "文章"}]
    out = VerifyPagination(fetcher=lambda url: fetched.append(url) or {"status": 200})(state)
    return (
        out["pagination"]["type"] == "none_detected"
        and out["pagination_probe"]["candidates"] == 0
        and fetched == []
    ), f"probe={out['pagination_probe']} fetched={fetched}"


def t_cursor_signal_passes_through_unverified():
    """cursor 型無法預先實抓（要先有第 1 頁回應才算得出下一頁），

    但訊號本身是確定性的（body 真的有游標鍵），擋掉它會讓 API 型的站失去翻頁。
    所以放行但標記 verified=False，不假裝驗證過。
    """
    state = _state()
    state["recon_report"]["link_samples"] = [{"url": "https://e.com/news/a/1", "text": "文章"}]
    state["recon_report"]["http_entry_sample"] = {
        "status": 200, "body_excerpt": '{"items":[], "next_cursor":"abc"}', "link_samples": [],
    }
    out = VerifyPagination(fetcher=lambda url: {"status": 200, "link_samples": []})(state)
    return (
        out["pagination"]["type"] == "cursor"
        and out["pagination"]["verified"] is False
        and out["pagination_probe"]["verified"] is False
    ), f"pagination={out['pagination']}"


TESTS = [
    t_with_param_replaces_not_appends,
    t_verified_when_page_two_has_new_articles,
    t_rejected_when_page_two_repeats_page_one,
    t_rejected_when_page_two_is_empty_or_errors,
    t_no_signal_means_first_page_only,
    t_cursor_signal_passes_through_unverified,
]

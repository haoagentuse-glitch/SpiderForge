"""明細樣本驗證（檢查二）的離線測試。

重點是**反向案例**：拿到列表頁時「有標題」「正文夠長」兩條都會過，
只有互相比對擋得住——這正是 BBC 那次兩輪修復都失敗的原因。
"""

from __future__ import annotations

import json

from spider_forge.nodes.verify_samples import VerifySamples
from spider_forge.shared.samples import (
    sample_signals,
    verify_api_records,
    text_similarity,
    verify_detail_samples,
)

_STATE = {
    "site_url": "https://example.com/news",
    "recon_report": {
        "final_url": "https://example.com/news",
        "canonical_url": "https://example.com/news",
    },
    "validation": {"min_content_chars": 200},
}


def _page(url: str, title: str, body: str, *, published_at: str | None = None) -> dict:
    """一份明細樣本；``published_at=None`` 就是分類頁（沒有文件層級發佈時間）。"""
    stamp = (
        f'<time datetime="{published_at}">{published_at}</time>' if published_at else ""
    )
    return {
        "requested_url": url,
        "final_url": url,
        "status": 200,
        "body_excerpt": (
            f"<html><head><title>{title}</title></head>"
            f"<body><h1>{title}</h1>{stamp}<p>{body}</p></body></html>"
        ),
    }


def _article(url: str, title: str, body: str) -> dict:
    return _page(url, title, body, published_at="2026-08-12T09:00:00Z")


def _words(seed: str, count: int) -> str:
    return " ".join(f"{seed}{index}" for index in range(count))


def t_two_real_articles_pass():
    samples = [
        _article("https://example.com/a/1", "第一篇", _words("alpha", 200)),
        _article("https://example.com/a/2", "第二篇", _words("beta", 200)),
    ]
    result = verify_detail_samples(samples, _STATE)
    return (
        result["passed"]
        and result["compared"]
        and result["max_similarity"] < 0.1
    ), f"result={result}"


def t_same_listing_page_twice_is_rejected():
    """BBC 那次的實際失敗樣態：兩份樣本其實是同一個列表頁。

    有標題 ✓ 正文兩萬字 ✓——前兩條判準完全擋不住，只有比對擋得住。
    """
    listing = _words("nav", 3000)
    samples = [
        _article("https://example.com/a/1", "商業", listing),
        _article("https://example.com/a/2", "商業", listing),
    ]
    result = verify_detail_samples(samples, _STATE)
    return (
        not result["passed"]
        and result["max_similarity"] >= 0.9
        and "列表頁" in result["reason"]
    ), f"result={result}"


def t_navigation_link_looping_back_to_entry_is_rejected():
    """導覽連結繞一圈回到入口頁；這種樣本拿去學 selector 必錯。"""
    samples = [
        _article("https://example.com/news", "商業", _words("x", 500)),
        _article("https://example.com/a/2", "第二篇", _words("y", 500)),
    ]
    result = verify_detail_samples(samples, _STATE)
    rejected = [row["reason"] for row in result["rejected"]]
    return (
        len(result["accepted"]) == 1
        and any("入口列表頁" in reason for reason in rejected)
        # 抓了兩份只有一份合格 → 無從比對，不放行（cnyes 實測踩到）
        and result["passed"] is False
        and result["compared"] is False
    ), f"result={result}"


def t_one_survivor_out_of_many_does_not_pass():
    """抓了三份只剩一份合格，多半是這種抓法挑到的根本不是明細頁。

    cnyes 實測：三份候選裡兩份被擋，剩下那份也是分類頁——只是它剛好帶了每則
    新聞的 ``<time>``，單獨看過得了關。放行等於讓錯的樣本教模型寫 selector。
    """
    samples = [
        _article("https://example.com/a/1", "看起來像文章", _words("alpha", 300)),
        _page("https://example.com/cat/b", "分類頁", _words("beta", 300)),
        _article("https://example.com/a/3", "太短", "短"),
    ]
    result = verify_detail_samples(samples, _STATE)
    return (
        len(result["accepted"]) == 1
        and result["passed"] is False
        and "只有 1 份通過" in result["reason"]
    ), f"result={result}"


def t_a_single_candidate_is_honestly_marked_unverified():
    """本來就只抓得到一份時照實說「未比對」，不要假裝驗過，也不要判它失敗。"""
    result = verify_detail_samples(
        [_article("https://example.com/a/1", "唯一一篇", _words("alpha", 300))], _STATE
    )
    return (
        result["passed"] and result["compared"] is False and "無法互相比對" in result["reason"]
    ), f"result={result}"


def t_shared_chrome_does_not_inflate_similarity():
    """相似度要反映文章，不是版面。

    中央社實測：三篇不同的文章，整頁文字的 Jaccard 高達 0.79——每頁約 600 組片語裡
    有 494 組是三篇共有的導覽與側欄，離「判定為同一頁」的 0.9 只差一點點。
    扣掉每份都有的樣板之後降到 0.21，訊號才真的在講文章。
    """
    chrome = _words("nav", 400)          # 三篇共有的導覽／側欄
    samples = [
        _article("https://example.com/a/1", "第一篇", chrome + " " + _words("alpha", 120)),
        _article("https://example.com/a/2", "第二篇", chrome + " " + _words("beta", 120)),
        _article("https://example.com/a/3", "第三篇", chrome + " " + _words("gamma", 120)),
    ]
    result = verify_detail_samples(samples, _STATE)
    raw = text_similarity(
        chrome + " " + _words("alpha", 120), chrome + " " + _words("beta", 120)
    )
    return (
        result["passed"]
        and raw > 0.6                      # 不扣樣板的話高到很危險（中央社實測 0.79）
        and result["max_similarity"] < 0.1  # 扣掉之後才看得出是三篇不同的文章
        and result["boilerplate_shingles"] > 300
    ), f"raw={raw:.3f} result={result}"


def t_three_identical_pages_are_still_rejected():
    """扣樣板的陷阱：三份**一樣**的頁面，扣完什麼都不剩。

    這時如果照公式算交集比例會得到 0 分——最該擋的情況反而變成最漂亮的分數。
    """
    same = _article("https://example.com/a/1", "商業", _words("listing", 600))
    samples = [
        same,
        {**same, "requested_url": "https://example.com/a/2", "final_url": "https://example.com/a/2"},
        {**same, "requested_url": "https://example.com/a/3", "final_url": "https://example.com/a/3"},
    ]
    result = verify_detail_samples(samples, _STATE)
    return (
        not result["passed"]
        and result["max_similarity"] == 1.0
        and "列表頁" in result["reason"]
    ), f"result={result}"


def t_different_section_pages_are_rejected():
    """BBC 零設定實跑（2026-08-12）挑到 /news、/sport、/technology 三個分類頁。

    有標題 ✓ 正文八千字 ✓ 彼此也不相似 ✓——長度與相似度全都擋不住，
    只有「沒有文件層級發佈時間」這條分得開。
    """
    samples = [
        _page("https://example.com/world", "國際", _words("headline", 2000)),
        _page("https://example.com/sport", "體育", _words("match", 2000)),
        _page("https://example.com/tech", "科技", _words("gadget", 2000)),
    ]
    result = verify_detail_samples(samples, _STATE)
    return (
        not result["passed"]
        and len(result["rejected"]) == 3
        and all("分類頁" in row["reason"] for row in result["rejected"])
    ), f"result={result}"


def t_dated_url_counts_as_date_evidence():
    """科技新報實測誤殺：文章**完全沒有結構化日期**（沒有 meta、沒有 JSON-LD、
    也沒有 <time>），日期只在網址 /2026/08/12/ 與頁面文字裡。只認 meta 的話，
    整個站四種抓法全被判成分類頁——那是判準的問題，不是站台的問題。

    但只認**網址路徑**，不認頁面文字裡的日期：列表頁上每則都有「2 小時前」，
    認文字等於把這關拆掉。
    """
    dated_url = [
        _page("https://technews.tw/2026/08/12/samsung-euv-delay/", "三星延後導入", _words("a", 300)),
        _page("https://technews.tw/2026/08/12/apple-oled-cycle/", "蘋果延長週期", _words("b", 300)),
    ]
    section = [
        _page("https://technews.tw/category/semiconductor/", "半導體", _words("c", 300)),
        _page("https://technews.tw/category/ai/", "人工智慧", _words("d", 300)),
    ]
    accepted = verify_detail_samples(dated_url, _STATE)
    rejected = verify_detail_samples(section, _STATE)
    return (
        accepted["passed"]
        and not rejected["passed"]
        and all("分類頁" in row["reason"] for row in rejected["rejected"])
    ), f"帶日期網址={accepted['passed']} 分類頁={rejected['reason']}"


def t_sites_without_structured_dates_can_opt_out():
    """真的有站台的文章不帶結構化日期，要留一個關得掉的開關。"""
    samples = [
        _page("https://example.com/a/1", "第一篇", _words("alpha", 300)),
        _page("https://example.com/a/2", "第二篇", _words("beta", 300)),
    ]
    state = {**_STATE, "validation": {**_STATE["validation"], "require_sample_date": False}}
    strict = verify_detail_samples(samples, _STATE)
    relaxed = verify_detail_samples(samples, state)
    return (not strict["passed"] and relaxed["passed"]), (
        f"strict={strict['reason']} relaxed={relaxed['reason']}"
    )


def t_thin_or_titleless_pages_are_rejected_with_reasons():
    samples = [
        _article("https://example.com/a/1", "短文", "太短了"),
        {
            "requested_url": "https://example.com/a/2",
            "final_url": "https://example.com/a/2",
            "status": 200,
            "body_excerpt": (
                '<html><body><time datetime="2026-08-12T09:00:00Z">今天</time>'
                f"<p>{_words('z', 500)}</p></body></html>"
            ),
        },
        {
            "requested_url": "https://example.com/a/3",
            "final_url": "https://example.com/a/3",
            "fetch_error": "timeout",
        },
        _article("https://example.com/a/4", "404 頁", _words("w", 500)) | {"status": 404},
    ]
    result = verify_detail_samples(samples, _STATE)
    reasons = " / ".join(row["reason"] for row in result["rejected"])
    return (
        not result["passed"]
        and len(result["rejected"]) == 4
        and "低於門檻" in reasons
        and "沒有標題" in reasons
        and "抓取失敗" in reasons
        and "HTTP 404" in reasons
    ), f"result={result}"


def t_quote_api_records_are_not_articles():
    """報價 API 不能因為「有 name 有 id」就被當成文章清單。

    鉅亨網台股行情頁實測：兩種 DOM 抓法都被檢查二正確擋下，前端資料介面那一階
    卻只憑 article_record_count 就放行，一路跑到產碼花掉三次模型呼叫才被閘門攔住。
    筆數是靠 JSON 鍵名形狀猜的，報價與排行榜同樣會中——所以要看內容不是看形狀。
    """
    quotes = [{
        "url": "https://example.com/api/quotes",
        "body_excerpt": json.dumps({"data": [
            {"name": "台積電", "id": 2330, "price": 1080, "date": "0812"},
            {"name": "聯發科", "id": 2454, "price": 1355, "date": "0812"},
        ]}),
    }]
    articles = [{
        "url": "https://example.com/api/news",
        "body_excerpt": json.dumps({"items": [
            {"title": "央行升息一碼 房貸族每月多繳千元", "publishAt": "2026-08-12T09:00:00+08:00"},
            {"title": "台股收盤上漲 電子股領軍走揚", "publishAt": "2026-08-12T13:30:00+08:00"},
        ]}),
    }]
    same_title = [{
        "url": "https://example.com/api/news",
        "body_excerpt": json.dumps({"items": [
            {"title": "鉅亨網即時新聞總覽", "publishAt": "2026-08-12T09:00:00+08:00"},
            {"title": "鉅亨網即時新聞總覽", "publishAt": "2026-08-12T13:30:00+08:00"},
        ]}),
    }]
    rejected = verify_api_records(quotes)
    accepted = verify_api_records(articles)
    duplicated = verify_api_records(same_title)
    return (
        not rejected["passed"] and "標題" in rejected["reason"]
        and accepted["passed"] and accepted["with_date"] == 2
        and not duplicated["passed"] and "都一樣" in duplicated["reason"]
    ), f"報價={rejected['reason']} 文章={accepted} 同標題={duplicated['reason']}"


def t_api_records_without_dates_are_rejected():
    """發佈時間是 Article 的必填欄位，記錄裡一個都解析不出來就教不了模型。"""
    undated = [{
        "url": "https://example.com/api/list",
        "body_excerpt": json.dumps({"data": [
            {"title": "台積電法說會重點整理", "id": 1},
            {"title": "聯發科新品發表會紀要", "id": 2},
        ]}),
    }]
    verdict = verify_api_records(undated)
    return (
        not verdict["passed"] and "發佈時間" in verdict["reason"]
    ), f"verdict={verdict}"


def t_browser_sample_uses_native_title_and_text():
    """瀏覽器樣本已有 title / text_excerpt，不必再解析 HTML。"""
    signals = sample_signals({
        "final_url": "https://example.com/a/1",
        "title": "瀏覽器抓到的標題",
        "text_excerpt": "正文內容",
        "body_excerpt": "<html><title>不該用這個</title></html>",
    })
    return (
        signals["title"] == "瀏覽器抓到的標題" and signals["text"] == "正文內容"
    ), f"signals={signals}"


def t_script_and_style_do_not_count_as_content():
    """共用同一包 JS 的兩個頁面不該因此被判成雷同，也不該靠 JS 湊滿字數。"""
    signals = sample_signals({
        "final_url": "https://example.com/a/1",
        "body_excerpt": (
            "<html><head><title>標題</title>"
            f"<script>var x = '{_words('js', 500)}';</script>"
            "<style>.a{color:red}</style></head>"
            "<body><p>只有這句是正文</p></body></html>"
        ),
    })
    return (
        signals["text"].strip() == "標題 只有這句是正文"
        and "js0" not in signals["text"]
    ), f"signals={signals}"


def t_similarity_is_symmetric_and_bounded():
    left, right = _words("a", 100), _words("b", 100)
    return (
        text_similarity(left, left) == 1.0
        and text_similarity(left, right) == 0.0
        and text_similarity(left, right) == text_similarity(right, left)
        # 空字串不該炸，也不該被當成「完全相同」
        and text_similarity("", "") == 0.0
    ), "similarity"


def t_node_reuses_fetched_samples_and_reports_reason():
    """節點要把抓到的樣本留在 state，讓 collect_evidence 不必重抓。"""
    calls = []

    def fetcher(state, urls):
        calls.append(list(urls))
        return [
            _article(url, f"標題 {index}", _words(f"seed{index}_", 500))
            for index, url in enumerate(urls)
        ]

    node = VerifySamples(fetcher=fetcher)
    update = node({**_STATE, "discovered_detail_urls": [
        "https://example.com/a/1", "https://example.com/a/2",
    ]})
    return (
        calls == [["https://example.com/a/1", "https://example.com/a/2"]]
        and len(update["detail_samples"]) == 2
        and update["sample_verification"]["passed"]
    ), f"calls={calls} update={update['sample_verification']}"


def t_node_survives_fetch_explosion():
    """抓取炸掉是「這種抓法不行」，不是流程錯誤——要回判定而不是拋例外。"""
    def fetcher(state, urls):
        raise RuntimeError("browser crashed")

    update = VerifySamples(fetcher=fetcher)(
        {**_STATE, "discovered_detail_urls": ["https://example.com/a/1"]}
    )
    return (
        update["sample_verification"]["passed"] is False
        and "browser crashed" in update["sample_verification"]["reason"]
    ), f"update={update}"


def t_node_without_candidates_fails_closed():
    update = VerifySamples(fetcher=lambda s, u: [])({**_STATE})
    return (
        update["sample_verification"]["passed"] is False
        and update["detail_samples"] == []
    ), f"update={update}"


TESTS = [
    t_two_real_articles_pass,
    t_same_listing_page_twice_is_rejected,
    t_shared_chrome_does_not_inflate_similarity,
    t_three_identical_pages_are_still_rejected,
    t_different_section_pages_are_rejected,
    t_dated_url_counts_as_date_evidence,
    t_sites_without_structured_dates_can_opt_out,
    t_navigation_link_looping_back_to_entry_is_rejected,
    t_one_survivor_out_of_many_does_not_pass,
    t_a_single_candidate_is_honestly_marked_unverified,
    t_thin_or_titleless_pages_are_rejected_with_reasons,
    t_quote_api_records_are_not_articles,
    t_api_records_without_dates_are_rejected,
    t_browser_sample_uses_native_title_and_text,
    t_script_and_style_do_not_count_as_content,
    t_similarity_is_symmetric_and_bounded,
    t_node_reuses_fetched_samples_and_reports_reason,
    t_node_survives_fetch_explosion,
    t_node_without_candidates_fails_closed,
]

"""前期偵查子迴圈的離線測試：抓法階梯、三道檢查的路由、有界重試。

這裡要釘死的是「不理想就換一種抓法重來」這件事真的成立，而不是像舊流程那樣
中間任何一步結果不好都照樣往下送——問題留到產碼之後才爆，診斷還會怪錯對象。
"""

from __future__ import annotations

from pipelines import pipeline
from spider_forge.nodes.fetch_strategy import SelectFetchStrategy, attempts_summary
from spider_forge.shared.fetch_strategies import (
    API_RECORDS,
    BROWSER_LINKS,
    BROWSER_SCROLL_LINKS,
    HTTP_LINKS,
    available_strategies,
    link_pool,
)


def _report(**overrides) -> dict:
    report = {
        "final_url": "https://example.com/news",
        "canonical_url": "https://example.com/news",
        "http_status": 200,
        "link_samples": [{"url": "https://example.com/a/browser", "text": "瀏覽器看到的"}],
        "http_entry_sample": {
            "status": 200,
            "link_samples": [{"url": "https://example.com/a/http", "text": "純 HTTP 看到的"}],
        },
        "api_candidates": [],
        "feed_candidates": [],
    }
    report.update(overrides)
    return report


def _state(**overrides) -> dict:
    state = {"site_url": "https://example.com/news", "recon_report": _report()}
    state.update(overrides)
    return state


def t_ladder_keeps_cheap_to_expensive_order():
    """順序是固定的確定性規則，不問模型，也不因為證據多寡重排。"""
    api = {
        "url": "https://example.com/api/news",
        "method": "GET",
        "body_excerpt": '{"items":[]}',
        "article_record_count": 3,
    }
    report = _report(
        api_candidates=[api],
        scroll_link_samples=[
            {"url": "https://example.com/a/browser", "text": "一"},
            {"url": "https://example.com/a/scrolled", "text": "二"},
        ],
    )
    return available_strategies(_state(recon_report=report)) == [
        HTTP_LINKS, BROWSER_LINKS, BROWSER_SCROLL_LINKS, API_RECORDS,
    ], f"pool={available_strategies(_state(recon_report=report))}"


def t_unavailable_rungs_are_left_out():
    """能不能用是事實：純 HTTP 被擋、沒捲出新連結、沒有 API，就都不排進來。"""
    report = _report(
        http_entry_sample={"status": 403},
        # 捲動後連結數沒有增加 → 跟上一階一模一樣，再試一次是浪費一輪
        scroll_link_samples=[{"url": "https://example.com/a/browser", "text": "一"}],
    )
    return available_strategies(_state(recon_report=report)) == [BROWSER_LINKS], (
        f"pool={available_strategies(_state(recon_report=report))}"
    )


def t_scroll_is_not_offered_when_the_browser_rung_found_no_links():
    """捲動是「瀏覽器渲染」的加強版，不是另一條路。

    踩過的坑：只看「瀏覽器進得去」就把捲動排進來，於是連一個 ``<a>`` 都沒有的
    純 API 站也會被捲一次才發現沒用——白開一次瀏覽器，而且讓離線測試真的連了網。
    """
    api = {
        "url": "https://example.com/api/news",
        "method": "GET",
        "body_excerpt": '{"items":[]}',
        "article_record_count": 3,
    }
    report = _report(link_samples=[], http_entry_sample={"status": 200}, api_candidates=[api])
    return available_strategies(_state(recon_report=report)) == [API_RECORDS], (
        f"pool={available_strategies(_state(recon_report=report))}"
    )


def t_each_rung_sees_only_its_own_link_pool():
    """混在一起挑的話，「換一種抓法重來」就沒有意義了。"""
    state = _state()
    http_urls = [row["url"] for row in link_pool(state, HTTP_LINKS)]
    browser_urls = [row["url"] for row in link_pool(state, BROWSER_LINKS)]
    merged = [row["url"] for row in link_pool(state, None)]
    return (
        http_urls == ["https://example.com/a/http"]
        and browser_urls == ["https://example.com/a/browser"]
        # 還沒進階梯時（單獨呼叫節點／舊流程）維持兩池合併的舊行為
        and sorted(merged) == sorted(http_urls + browser_urls)
    ), f"http={http_urls} browser={browser_urls} merged={merged}"


def _no_scroll(url: str) -> dict:
    """捲不出更多連結的站；測試一律注入，絕不讓測試真的開瀏覽器。"""
    return {
        "url": url,
        "link_samples": [],
        "links_after_each_round": [12, 12],
        "rounds_scrolled": 1,
        "loaded_more": False,
    }


def _scrolls(url: str) -> dict:
    """捲得出更多連結的站。"""
    return {
        "url": url,
        "link_samples": [
            {"url": f"https://example.com/a/scrolled{i}", "text": f"捲出來的 {i}"}
            for i in range(4)
        ],
        "links_after_each_round": [12, 20, 28],
        "rounds_scrolled": 2,
        "loaded_more": True,
    }


def t_first_pass_picks_the_cheapest_rung():
    update = SelectFetchStrategy(scroll_prober=_no_scroll)(_state())
    return (
        update["fetch_strategy"] == HTTP_LINKS
        # 捲動還沒探測過，所以先當它可用——真正選到那一階時才會去捲
        and update["fetch_strategy_pool"] == [
            HTTP_LINKS, BROWSER_LINKS, BROWSER_SCROLL_LINKS,
        ]
        and update["discovery_attempts"] == []
    ), f"update={update}"


def t_scroll_rung_is_probed_lazily_and_skipped_when_it_loads_nothing():
    """捲動要開瀏覽器等載入，不能為了排清單就每一站都先付這個成本。

    所以它是唯一「選到才探測」的一階；捲不出更多連結就當場記一筆跳過，
    不必浪費一輪抓樣本才發現跟上一階一模一樣。
    """
    probed: list[str] = []

    def prober(url: str) -> dict:
        probed.append(url)
        return _no_scroll(url)

    state = _state(
        fetch_strategy=BROWSER_LINKS,
        fetch_strategy_pool=[BROWSER_LINKS, BROWSER_SCROLL_LINKS],
        discovered_detail_urls=["https://example.com/a/browser"],
        sample_verification={"passed": False, "reason": "樣本不是文章"},
    )
    update = SelectFetchStrategy(scroll_prober=prober)(state)
    scroll_attempt = update["discovery_attempts"][-1]
    return (
        probed == ["https://example.com/news"]
        and update["fetch_strategy"] is None
        and update["failure_class"] == "discovery_unusable"
        and scroll_attempt["strategy"] == BROWSER_SCROLL_LINKS
        and "連結數沒有增加" in scroll_attempt["reason"]
    ), f"probed={probed} update={update}"


def t_scroll_rung_is_used_when_it_really_loads_more():
    state = _state(
        fetch_strategy=BROWSER_LINKS,
        fetch_strategy_pool=[BROWSER_LINKS, BROWSER_SCROLL_LINKS],
        discovered_detail_urls=["https://example.com/a/browser"],
        sample_verification={"passed": False, "reason": "樣本不是文章"},
    )
    update = SelectFetchStrategy(scroll_prober=_scrolls)(state)
    pool_urls = [
        row["url"] for row in link_pool({**state, **update}, BROWSER_SCROLL_LINKS)
    ]
    return (
        update["fetch_strategy"] == BROWSER_SCROLL_LINKS
        # 捲完的連結存回 recon_report，後面每一輪共用同一份，不重捲
        and update["recon_report"]["scroll_probe"]["loaded_more"] is True
        and len(pool_urls) == 4
    ), f"update_strategy={update.get('fetch_strategy')} pool={pool_urls}"


def t_scroll_pagination_is_verified_by_link_growth_not_by_page_two():
    """捲動抓法的「翻頁」就是無限捲動，證據是連結數的成長，不是第 2 頁的 URL。"""
    from spider_forge.nodes.pagination import VerifyPagination

    state = _state(
        fetch_strategy=BROWSER_SCROLL_LINKS,
        recon_report=_report(
            scroll_link_samples=[{"url": "https://example.com/a/1", "text": "一"}],
            scroll_probe={
                "links_after_each_round": [12, 20, 28],
                "rounds_scrolled": 2,
                "loaded_more": True,
            },
        ),
    )

    def exploding_fetcher(url):
        raise AssertionError("捲動抓法不該去實抓第 2 頁")

    update = VerifyPagination(fetcher=exploding_fetcher)(state)
    return (
        update["pagination"]["type"] == "infinite_scroll"
        and update["pagination"]["verified"] is True
        and update["pagination"]["links_after_each_round"] == [12, 20, 28]
    ), f"update={update}"


def t_failed_check_is_recorded_before_moving_on():
    """死信要能回答「四種抓法分別卡在哪」，所以換階之前先記上一輪為什麼失敗。"""
    state = _state(
        fetch_strategy=HTTP_LINKS,
        fetch_strategy_pool=[HTTP_LINKS, BROWSER_LINKS],
        discovered_detail_urls=["https://example.com/a/http"],
        sample_verification={"passed": False, "reason": "兩份樣本內容近乎相同"},
    )
    update = SelectFetchStrategy()(state)
    attempt = update["discovery_attempts"][0]
    return (
        update["fetch_strategy"] == BROWSER_LINKS
        and attempt["strategy"] == HTTP_LINKS
        and attempt["failed_check"].startswith("檢查二")
        and "近乎相同" in attempt["reason"]
    ), f"update={update}"


def t_stale_evidence_is_cleared_when_switching_rungs():
    """不清的話，新抓法挑不出連結時會沿用上一輪的樣本——等於用 A 的證據產 B 的碼。"""
    state = _state(
        fetch_strategy=HTTP_LINKS,
        fetch_strategy_pool=[HTTP_LINKS, BROWSER_LINKS],
        discovered_detail_urls=["https://example.com/a/http"],
        detail_samples=[{"final_url": "https://example.com/a/http"}],
        sample_verification={"passed": False, "reason": "樣本不是文章"},
        pagination={"type": "query_param", "verified": True},
        pagination_probe={"candidates": 1, "verified": True},
    )
    update = SelectFetchStrategy()(state)
    return (
        update["discovered_detail_urls"] == []
        and update["detail_samples"] == []
        and update["sample_verification"] == {}
        and update["pagination"] == {}
        and update["pagination_probe"] == {}
    ), f"update={ {k: v for k, v in update.items() if k in ('detail_samples', 'pagination')} }"


def t_exhausting_the_ladder_is_a_kill_with_a_readable_trail():
    state = _state(
        fetch_strategy=BROWSER_LINKS,
        fetch_strategy_pool=[HTTP_LINKS, BROWSER_LINKS],
        discovery_attempts=[
            {"strategy": HTTP_LINKS, "failed_check": "檢查一：找得到文章連結",
             "reason": "挑不出文章連結"},
        ],
        discovered_detail_urls=["https://example.com/a/browser"],
        sample_verification={"passed": False, "reason": "沒有文件層級的發佈時間"},
    )
    update = SelectFetchStrategy()(state)
    summary = attempts_summary({**state, **update})
    return (
        update["fetch_strategy"] is None
        and update["failure_class"] == "discovery_unusable"
        and len(summary) == 2
        and "直接連線" in summary[0]
        and "瀏覽器渲染" in summary[1]
    ), f"update={update} summary={summary}"


def t_dead_letter_reports_the_discovery_trail_not_the_triage_verdict():
    """triage 放行時也會寫 feasibility.reason，不能讓它蓋掉真正的失敗原因。

    踩過的坑：死信的 triage_reason 以 feasibility.reason 為第一順位，於是偵查失敗的
    死信掛上一句「存在可重播結構化候選」——當初看起來可做，正好把真正的原因蓋掉。
    """
    from spider_forge.output.manager import escalate_human

    record = escalate_human({
        "run_id": "deadletter-test",
        "site_url": "https://example.com/news",
        "source_prefix": "example",
        "failure_class": "discovery_unusable",
        # 這一關是重點：triage 當初判的是「可做」
        "feasibility": {"class": "FEASIBLE_API", "reason": "存在可重播結構化候選"},
        "discovery_attempts": [
            {"strategy": HTTP_LINKS, "failed_check": "檢查二：樣本是真文章", "reason": "樣本是列表頁"},
            {"strategy": BROWSER_LINKS, "failed_check": "檢查二：樣本是真文章", "reason": "樣本是列表頁"},
        ],
    })
    import json
    from pathlib import Path

    written = json.loads(Path(record["dead_letter_path"]).read_text(encoding="utf-8"))
    return (
        written["failure_class"] == "discovery_unusable"
        and "2 種抓法都試過" in written["triage_reason"]
        and len(written["tried"]["discovery_attempts"]) == 2
        and written["suggested_action"].startswith("確定性 KILL")
    ), f"triage_reason={written['triage_reason']}"


def t_api_rung_failure_is_not_blamed_on_link_discovery():
    """前端資料介面沒有明細頁連結卻是通過檢查一的，後面的失敗不能記成「挑不到連結」。"""
    state = _state(
        fetch_strategy=API_RECORDS,
        fetch_strategy_pool=[API_RECORDS],
        discovered_detail_urls=[],
        link_discovery={"method": "api_records", "api_records": 10},
        sample_verification={"passed": True, "compared": False},
        pagination_probe={"candidates": 2, "verified": False, "reason": "第 2 頁與第 1 頁相同"},
    )
    attempt = SelectFetchStrategy()(state)["discovery_attempts"][0]
    return (
        attempt["failed_check"].startswith("檢查三")
        and "第 2 頁" in attempt["reason"]
    ), f"attempt={attempt}"


def t_kill_class_routes_to_dead_letter_not_to_repair():
    """換模型重寫救不了錯的證據，所以 discovery_unusable 不進修復迴圈。"""
    from spider_forge.state import KILL_FAILURE_CLASSES

    destination = pipeline.route_after_diagnose(
        {"diagnosis": {"failure_class": "discovery_unusable"}}
    )
    return (
        "discovery_unusable" in KILL_FAILURE_CLASSES
        and destination == "escalate_human"
    ), f"destination={destination}"


# ── 三道檢查的路由 ──────────────────────────────────────────────────────
def t_check_one_and_two_send_a_bad_rung_back_to_the_dial():
    no_links = pipeline.route_after_discover_links({"discovered_detail_urls": []})
    bad_samples = pipeline.route_after_verify_samples(
        {"sample_verification": {"passed": False}}
    )
    good = pipeline.route_after_verify_samples({"sample_verification": {"passed": True}})
    return (
        no_links == "select_fetch_strategy"
        and bad_samples == "select_fetch_strategy"
        and good == "verify_pagination"
    ), f"{no_links} / {bad_samples} / {good}"


def t_api_records_count_as_found_evidence():
    """純 JSON 介面沒有明細頁連結，但記錄自帶內容——不該因此被判死。"""
    destination = pipeline.route_after_discover_links({
        "discovered_detail_urls": [],
        "link_discovery": {"method": "api_records", "api_records": 5},
    })
    return destination == "verify_samples", f"destination={destination}"


def t_absent_pagination_is_a_pass_not_a_retry():
    """「確定沒有翻頁」是合格的偵查結果（只抓第 1 頁），不該換抓法。"""
    destination = pipeline.route_after_verify_pagination({
        "pagination_probe": {"candidates": 0, "verified": False},
        "fetch_strategy": HTTP_LINKS,
        "fetch_strategy_pool": [HTTP_LINKS, BROWSER_LINKS],
    })
    return destination == "collect_evidence", f"destination={destination}"


def t_unverifiable_but_deterministic_pagination_is_a_pass():
    """cursor 型翻頁沒辦法預先實抓，但訊號本身是確定性的（body 真的有游標鍵）。

    cnyes 實測踩到：把它當成「翻頁壞掉」會換掉一個其實完全可用的抓法。
    """
    destination = pipeline.route_after_verify_pagination({
        "pagination_probe": {"candidates": 1, "verified": False, "deterministic": True},
        "fetch_strategy": HTTP_LINKS,
        "fetch_strategy_pool": [HTTP_LINKS, BROWSER_LINKS],
        "discovery_attempts": [],
    })
    return destination == "collect_evidence", f"destination={destination}"


def t_broken_pagination_retries_only_while_rungs_remain():
    """偵測到卻翻不動 → 換抓法可能有救；但抓法用完就降級放行，不為了翻頁判死。"""
    probe = {"candidates": 2, "verified": False}
    retry = pipeline.route_after_verify_pagination({
        "pagination_probe": probe,
        "fetch_strategy": HTTP_LINKS,
        "fetch_strategy_pool": [HTTP_LINKS, BROWSER_LINKS],
        "discovery_attempts": [],
    })
    exhausted = pipeline.route_after_verify_pagination({
        "pagination_probe": probe,
        "fetch_strategy": BROWSER_LINKS,
        "fetch_strategy_pool": [HTTP_LINKS, BROWSER_LINKS],
        "discovery_attempts": [{"strategy": HTTP_LINKS, "failed_check": "檢查三", "reason": "x"}],
    })
    return (
        retry == "select_fetch_strategy" and exhausted == "collect_evidence"
    ), f"retry={retry} exhausted={exhausted}"


def t_verified_rung_overrides_the_pre_loop_strategy_guess():
    """驗過的抓法蓋掉迴圈之前的策略猜測。

    MoneyDJ 實測：`strategy_decision` 判 hybrid 並選了一個 .axd 端點，但子迴圈是靠
    HTML 連結過關的。照 hybrid 產碼等於拿一個**沒被驗證過**的端點當列表來源，
    錯了要到 sandbox 實跑才爆——而那時的診斷會怪 selector。圖四要的就是
    「不再由模型判斷該用 API 還是 HTML，而是逐一試、驗證通過才用」。
    """
    from spider_forge.shared.evidence import _reconcile_strategy

    guessed_api = {
        "strategy_detail": {"strategy": "hybrid", "chosen_api": "https://x/api.axd",
                            "reason": "模型猜的"},
        "strategy": "hybrid",
    }
    links_verified, links_name = _reconcile_strategy(
        {**guessed_api, "fetch_strategy": HTTP_LINKS}
    )
    api_verified, api_name = _reconcile_strategy({
        "strategy_detail": {"strategy": "dom", "chosen_api": ""},
        "strategy": "dom",
        "fetch_strategy": API_RECORDS,
    })
    # 還沒進迴圈時不能亂改別人的判斷
    untouched, untouched_name = _reconcile_strategy(guessed_api)

    return (
        links_name == "dom"
        and links_verified["chosen_api"] == ""
        and links_verified["superseded_by_fetch_strategy"] == HTTP_LINKS
        and api_name == "api"
        and untouched_name == "hybrid"
        and "superseded_by_fetch_strategy" not in untouched
    ), f"links={links_name} api={api_name} untouched={untouched_name}"


def t_verified_browser_rung_forces_browser_transport_in_the_contract():
    """驗過的抓法要走進產碼契約，否則整場驗證等於白做。

    BBC 實測：入口用純 HTTP 拿得到 200（access_assessment=browser_public_ok），
    但文章內文是前端渲染的，純 HTTP 只有幾十個字——子迴圈正是因此退到瀏覽器抓法。
    如果 requirements 只看 access_assessment，產出的爬蟲會被要求用純 HTTP 抓，
    剛剛驗過的結論就被丟掉了。
    """
    from spider_forge.shared.evidence import collect_evidence

    base = {
        "site_url": "https://example.com/news",
        "site_name": "example",
        "source_prefix": "example",
        "target_schema": {"fields": {}},
        "access_mode": "public",
        "recon_report": _report(access_assessment="browser_public_ok"),
        "discovered_detail_urls": ["https://example.com/a/1"],
        "detail_samples": [],
    }
    browser_rung = collect_evidence({**base, "fetch_strategy": BROWSER_LINKS})
    http_rung = collect_evidence({**base, "fetch_strategy": HTTP_LINKS})
    return (
        "browser_transport" in browser_rung["evidence_pack"]["requirements"]
        and "browser_transport" not in http_rung["evidence_pack"]["requirements"]
        and browser_rung["evidence_pack"]["fetch_strategy"] == BROWSER_LINKS
    ), (
        f"browser={browser_rung['evidence_pack']['requirements']} "
        f"http={http_rung['evidence_pack']['requirements']}"
    )


def t_step_budget_covers_the_worst_path():
    """加節點就要重算步數上限。

    撞破上限拿到的是 GraphRecursionError——不是死信、沒有診斷、什麼都沒留，
    最需要證據的時候反而什麼都沒有。偵查子迴圈上線時最壞路徑正好是 61 步，
    當時的上限 60 差一步就爆，這條測試就是為了不要再靠運氣發現。
    """
    from spider_forge.config import MAX_REPAIRS
    from spider_forge.shared.fetch_strategies import LADDER

    gates = 6          # preflight / fixture / sandbox / block / validate / topic
    worst = (
        4                                   # prepare / recon / triage / strategy
        + len(LADDER) * 4                   # 每一階：select + 三道檢查
        + 2                                 # collect_evidence + generate
        + gates
        + (MAX_REPAIRS + pipeline._PROVIDER_RETRY_MAX) * (2 + gates)
        + 1                                 # escalate
    )
    return pipeline.RECURSION_LIMIT >= worst, (
        f"worst={worst} limit={pipeline.RECURSION_LIMIT}"
    )


def t_graph_really_loops_from_a_bad_rung_to_a_good_one():
    """跑真的 graph：純 HTTP 挑到導覽頁 → 樣本驗證擋下 → 換瀏覽器 → 過關往下走。

    這條要用編譯後的 graph 而不是直接呼叫 router，因為要一起驗
    「換階時清掉的舊證據，在 LangGraph 的狀態合併之後真的不見了」。
    """
    from spider_forge.nodes.discover_links import DiscoverArticleLinks
    from spider_forge.nodes.verify_samples import VerifySamples

    nav_url = "https://example.com/a/http"
    article_url = "https://example.com/a/browser"

    def fake_recon(state):
        return {"recon_report": _report(), "status": "reconning"}

    def fake_fetch(state, urls):  # noqa: ARG001
        """導覽頁沒有發佈時間；文章有。"""
        pages = {
            nav_url: "<html><head><title>導覽</title></head><body>"
                     + " ".join(f"nav{i}" for i in range(400)) + "</body></html>",
            article_url: '<html><head><title>真文章</title></head><body>'
                         '<time datetime="2026-08-12T09:00:00Z">今天</time>'
                         + " ".join(f"word{i}" for i in range(400)) + "</body></html>",
        }
        return [
            {"requested_url": url, "final_url": url, "status": 200,
             "body_excerpt": pages[url]}
            for url in urls
        ]

    seen: list[dict] = []

    def capture_evidence(state):
        seen.append({
            "fetch_strategy": state.get("fetch_strategy"),
            "detail_urls": list(state.get("discovered_detail_urls") or []),
            "attempts": list(state.get("discovery_attempts") or []),
        })
        return {"evidence_pack": {"origin": "test"}, "status": "evidence_ready"}

    replacements = {
        "recon": fake_recon,
        "feasibility_triage": lambda s: {"feasibility": {"class": "FEASIBLE_DOM"}},
        "strategy_decision": lambda s: {"strategy": "dom", "strategy_detail": {}},
        # 挑選層固定成「照順序全收」，讓測的是迴圈而不是模型排序
        "discover_links": DiscoverArticleLinks(
            picker=lambda rows: [row["index"] for row in rows]
        ),
        "verify_samples": VerifySamples(fetcher=fake_fetch),
        "verify_pagination": lambda s: {"pagination": {"type": "none_detected"},
                                        "pagination_probe": {"candidates": 0}},
        "collect_evidence": capture_evidence,
        "generate_spider": lambda s: {"spider_code": "code"},
        "preflight_generated_code": lambda s: {"generation_preflight": {"passed": True}},
        "fixture_test": lambda s: {"fixture_result": {"passed": True}},
        "sandbox_test": lambda s: {"test_result": {}},
        "content_block_gate": lambda s: {"block_page_detected": False},
        "validate_output": lambda s: {"validation_result": {"pass": True}},
        "apply_topic_gate": lambda s: {"topic_result": {"mode": "off"}},
        "persist_spider": lambda s: {"status": "success", "spider_path": "x.py"},
    }
    originals = {name: getattr(pipeline, name) for name in replacements}
    try:
        for name, replacement in replacements.items():
            setattr(pipeline, name, replacement)
        final = pipeline.build_pipeline().invoke(
            {"site_url": "https://example.com/news", "run_id": "loop-test"},
            config={"configurable": {"thread_id": "loop-test"}, "recursion_limit": 60},
        )
    finally:
        for name, original in originals.items():
            setattr(pipeline, name, original)

    reached = seen[0] if seen else {}
    return (
        final.get("status") == "success"
        # 第一階（純 HTTP）挑到導覽頁被檢查二擋下，換到第二階才過
        and reached.get("fetch_strategy") == BROWSER_LINKS
        and reached.get("detail_urls") == [article_url]
        and len(reached.get("attempts") or []) == 1
        and reached["attempts"][0]["strategy"] == HTTP_LINKS
        and "檢查二" in reached["attempts"][0]["failed_check"]
    ), f"status={final.get('status')} reached={reached}"


def t_graph_dead_letters_when_every_rung_fails():
    """四種抓法都試完仍過不了 → 死信，而且記得下卡在哪一關。"""
    from spider_forge.nodes.discover_links import DiscoverArticleLinks
    from spider_forge.nodes.verify_samples import VerifySamples

    def fake_fetch(state, urls):
        """每一階拿到的都是同一個列表頁。"""
        body = "<html><head><title>列表</title></head><body>" + " ".join(
            f"nav{i}" for i in range(400)
        ) + "</body></html>"
        return [
            {"requested_url": url, "final_url": url, "status": 200, "body_excerpt": body}
            for url in urls
        ]

    replacements = {
        "recon": lambda s: {"recon_report": _report(), "status": "reconning"},
        # 測試一律注入捲動探測，絕不讓測試真的開瀏覽器
        "select_fetch_strategy": SelectFetchStrategy(scroll_prober=_no_scroll),
        "feasibility_triage": lambda s: {"feasibility": {"class": "FEASIBLE_DOM"}},
        "strategy_decision": lambda s: {"strategy": "dom", "strategy_detail": {}},
        "discover_links": DiscoverArticleLinks(
            picker=lambda rows: [row["index"] for row in rows]
        ),
        "verify_samples": VerifySamples(fetcher=fake_fetch),
    }
    originals = {name: getattr(pipeline, name) for name in replacements}
    try:
        for name, replacement in replacements.items():
            setattr(pipeline, name, replacement)
        final = pipeline.build_pipeline().invoke(
            {"site_url": "https://example.com/news", "run_id": "loop-dead"},
            config={"configurable": {"thread_id": "loop-dead"}, "recursion_limit": 60},
        )
    finally:
        for name, original in originals.items():
            setattr(pipeline, name, original)

    attempts = final.get("discovery_attempts") or []
    return (
        final.get("status") == "escalated"
        and final.get("failure_class") == "discovery_unusable"
        and [row["strategy"] for row in attempts] == [
            HTTP_LINKS, BROWSER_LINKS, BROWSER_SCROLL_LINKS,
        ]
        # 前兩階栽在樣本驗證，捲動那階則是捲不出更多連結
        and all("檢查二" in row["failed_check"] for row in attempts[:2])
        and "連結數沒有增加" in attempts[2]["reason"]
    ), f"status={final.get('status')} class={final.get('failure_class')} attempts={attempts}"


TESTS = [
    t_ladder_keeps_cheap_to_expensive_order,
    t_unavailable_rungs_are_left_out,
    t_scroll_is_not_offered_when_the_browser_rung_found_no_links,
    t_each_rung_sees_only_its_own_link_pool,
    t_first_pass_picks_the_cheapest_rung,
    t_scroll_rung_is_probed_lazily_and_skipped_when_it_loads_nothing,
    t_scroll_rung_is_used_when_it_really_loads_more,
    t_scroll_pagination_is_verified_by_link_growth_not_by_page_two,
    t_failed_check_is_recorded_before_moving_on,
    t_stale_evidence_is_cleared_when_switching_rungs,
    t_exhausting_the_ladder_is_a_kill_with_a_readable_trail,
    t_dead_letter_reports_the_discovery_trail_not_the_triage_verdict,
    t_api_rung_failure_is_not_blamed_on_link_discovery,
    t_kill_class_routes_to_dead_letter_not_to_repair,
    t_check_one_and_two_send_a_bad_rung_back_to_the_dial,
    t_api_records_count_as_found_evidence,
    t_absent_pagination_is_a_pass_not_a_retry,
    t_unverifiable_but_deterministic_pagination_is_a_pass,
    t_broken_pagination_retries_only_while_rungs_remain,
    t_verified_rung_overrides_the_pre_loop_strategy_guess,
    t_verified_browser_rung_forces_browser_transport_in_the_contract,
    t_step_budget_covers_the_worst_path,
    t_graph_really_loops_from_a_bad_rung_to_a_good_one,
    t_graph_dead_letters_when_every_rung_fails,
]

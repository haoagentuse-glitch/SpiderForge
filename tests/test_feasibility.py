"""spec v2 §3.2/§3.6：feasibility_triage 確定性分流 + escalate_human 非阻塞死信歸檔。

跑法（從 workspace/backend/）：
    python -m spider_forge.tests.test_feasibility
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from pipelines import pipeline as graph


def _cleanup(path_str: str | None) -> None:
    if path_str and Path(path_str).exists():
        Path(path_str).unlink()


# ════════════════════════ (a) policy_kill：確定性挑戰頁 → KILL → 不進生成 ════════════════════════


def t_challenge_page_wording_alone_does_not_kill():
    """**挑戰頁字樣不再單獨構成 KILL**（2026-08-02 解除自綁手腳）。

    舊行為：`soft_block_detected` 為真就直接死信——即使頁面上明明有文章連結。
    那等於「被擋一次就放棄」，而字樣比對的誤判來源很多（頁面剛好提到
    cloudflare、錯誤頁一閃而過、渲染時序）。

    新行為：往下走，讓後面的閘門用「實際抓到什麼」判斷。只有**真的零證據**時
    才 KILL，而且那時的分類會說出真正原因（403 → auth_required）。
    """
    blocked_but_has_links = graph.feasibility_triage({
        "site_url": "https://guarded.example.com/news",
        "validation": {"allowed_domains": ["guarded.example.com"]},
        "recon_report": {
            "http_status": 200,
            "title": "Just a moment... cloudflare",   # 字樣命中
            "soft_block_detected": True,
            "api_candidates": [], "feed_candidates": [],
            "link_samples": [{"url": "https://guarded.example.com/news/a/12345"}],
            "http_entry_sample": {"status": 200, "link_samples": []},
        },
    })
    blocked_and_empty = graph.feasibility_triage({
        "site_url": "https://guarded.example.com/news",
        "validation": {"allowed_domains": ["guarded.example.com"]},
        "recon_report": {
            "http_status": 403,
            "soft_block_detected": True,
            "api_candidates": [], "feed_candidates": [], "link_samples": [],
            "http_entry_sample": {"status": 403, "link_samples": []},
        },
    })
    plain_403 = graph.feasibility_triage({
        "site_url": "https://members.example.com/news",
        "validation": {"allowed_domains": ["members.example.com"]},
        "recon_report": {
            "http_status": 403,
            "title": "Members only",           # 沒有任何機器人防護字樣
            "api_candidates": [], "feed_candidates": [], "link_samples": [],
            "http_entry_sample": {"status": 403, "link_samples": []},
        },
    })
    return (
        # 有文章連結 → 照樣往下試，不因字樣就放棄
        blocked_but_has_links["feasibility"]["class"].startswith("FEASIBLE_")
        and graph.route_after_triage(blocked_but_has_links) == "strategy_decision"
        # 真的零證據 → 仍然 KILL，但說出真正原因而不是籠統的 policy_kill。
        # 帶機器人防護字樣的 403 要跟「需要帳號」分開：一個降速稍後再試，
        # 一個要去拿授權，歸成同一類會讓人照著錯的方向查（工商時報實測）。
        and blocked_and_empty["feasibility"]["class"] == "KILL_waf_blocked"
        and plain_403["feasibility"]["class"] == "KILL_auth_required"
    ), (
        f"有連結={blocked_but_has_links['feasibility']['class']} "
        f"防護頁={blocked_and_empty['feasibility']['class']} "
        f"純 403={plain_403['feasibility']['class']}"
    )


def t_kill_route_structurally_never_reaches_generation():
    """KILL 路徑絕不進生成：feasibility_triage 只能到 escalate_human/strategy_decision，
    generate_spider 只能從 collect_evidence 進來——兩者結構上不相交。
    """
    compiled = graph.build_pipeline()
    edges = compiled.get_graph().edges
    triage_targets = {e.target for e in edges if e.source == "feasibility_triage"}
    generate_sources = {e.source for e in edges if e.target == "generate_spider"}
    ok = (
        triage_targets == {"strategy_decision", "escalate_human"}
        and generate_sources == {"collect_evidence"}
    )
    return ok, f"triage_targets={sorted(triage_targets)} generate_sources={sorted(generate_sources)}"


def t_blocked_and_empty_entry_escalates_without_touching_generate():
    state = {
        "site_url": "https://paywall.example.com/news",
        "source_prefix": "paywallex",
        "run_id": "test-policykill-0001",
        "validation": {"allowed_domains": ["paywall.example.com"]},
        "recon_report": {
            "http_status": 403,
            "soft_block_detected": True,
            "api_candidates": [],
            "feed_candidates": [],
            "link_samples": [],
            "http_entry_sample": {"status": 403, "link_samples": []},
        },
    }
    triage = graph.feasibility_triage(state)
    merged = {**state, **triage}
    route = graph.route_after_triage(merged)
    dead_letter_path = None
    try:
        escalated = graph.escalate_human(merged)
        dead_letter_path = escalated.get("dead_letter_path")
        record = json.loads(Path(dead_letter_path).read_text(encoding="utf-8"))
        ok = (
            route == "escalate_human"
            and escalated["status"] == "escalated"
            and dead_letter_path
            and Path(dead_letter_path).exists()
            and record["failure_class"] == "KILL_waf_blocked"
            and "spider_code" not in merged  # generate_spider 從未被呼叫，state 沒有它會寫入的欄位
        )
    finally:
        _cleanup(dead_letter_path)
    return ok, f"route={route} path={dead_letter_path}"


# ════════════════════════ (b) 乾淨 api 證據 → FEASIBLE_API → 往 strategy ════════════════════════


def t_clean_api_evidence_is_feasible_and_routes_to_strategy():
    state = {
        "site_url": "https://example.com/news",
        "validation": {"allowed_domains": ["example.com"]},
        "recon_report": {
            "http_status": 200,
            "soft_block_detected": False,
            "api_candidates": [
                {
                    "method": "GET",
                    "url": "https://example.com/api/news",
                    "body_excerpt": '{"items":[{"title":"x","url":"/news/1"}]}',
                    "article_record_count": 3,
                }
            ],
            "feed_candidates": [],
            "link_samples": [{"url": "https://example.com/news/1", "text": "x"}],
            "http_entry_sample": {
                "status": 200,
                "link_samples": [{"url": "https://example.com/news/1", "text": "x"}],
            },
        },
    }
    result = graph.feasibility_triage(state)
    merged = {**state, **result}
    route = graph.route_after_triage(merged)
    ok = (
        result["feasibility"]["class"] == "FEASIBLE_API"
        and "failure_class" not in result
        and route == "strategy_decision"
    )
    return ok, f"class={result['feasibility']['class']} route={route}"


# ════════════════════════ (c) discovery_empty → KILL → 死信 ════════════════════════


def t_discovery_empty_kills_and_writes_dead_letter():
    state = {
        "site_url": "https://empty.example.com/news",
        "source_prefix": "emptyex",
        "run_id": "test-empty-0001",
        "validation": {"allowed_domains": ["empty.example.com"]},
        "recon_report": {
            "http_status": 200,
            "soft_block_detected": False,
            "api_candidates": [],
            "feed_candidates": [],
            "link_samples": [],
            "http_entry_sample": {"status": 200, "link_samples": []},
        },
    }
    triage = graph.feasibility_triage(state)
    merged = {**state, **triage}
    route = graph.route_after_triage(merged)
    dead_letter_path = None
    try:
        escalated = graph.escalate_human(merged)
        dead_letter_path = escalated.get("dead_letter_path")
        record = json.loads(Path(dead_letter_path).read_text(encoding="utf-8"))
        ok = (
            triage["feasibility"]["class"] == "KILL_discovery_empty"
            and route == "escalate_human"
            and escalated["status"] == "escalated"
            and Path(dead_letter_path).exists()
            and record["failure_class"] == "KILL_discovery_empty"
            and record["site_url"] == state["site_url"]
            and record["status"] == "dead_letter"
        )
    finally:
        _cleanup(dead_letter_path)
    return ok, f"class={triage['feasibility']['class']} route={route} path={dead_letter_path}"


def t_discovery_not_empty_when_recon_itself_failed():
    """recon 自己出錯（recon_error/navigation_error）是不確定訊號，不能被判 KILL。"""
    state = {
        "site_url": "https://flaky.example.com/news",
        "validation": {"allowed_domains": ["flaky.example.com"]},
        "recon_report": {
            "http_status": None,
            "recon_error": "playwright timeout",
            "api_candidates": [],
            "feed_candidates": [],
            "link_samples": [],
            "http_entry_sample": {},
        },
    }
    result = graph.feasibility_triage(state)
    ok = result["feasibility"]["class"] in {"FEASIBLE_API", "FEASIBLE_HTML"}
    return ok, f"class={result['feasibility']['class']}"


def t_sample_urls_override_prevents_false_discovery_kill():
    """使用者提供 sample_urls 時，即使 recon 沒抓到任何連結，也不能誤殺。"""
    state = {
        "site_url": "https://knownsamples.example.com/news",
        "sample_urls": ["https://knownsamples.example.com/news/1"],
        "validation": {"allowed_domains": ["knownsamples.example.com"]},
        "recon_report": {
            "http_status": 200,
            "soft_block_detected": False,
            "api_candidates": [],
            "feed_candidates": [],
            "link_samples": [],
            "http_entry_sample": {"status": 200, "link_samples": []},
        },
    }
    result = graph.feasibility_triage(state)
    ok = result["feasibility"]["class"] == "FEASIBLE_HTML"
    return ok, f"class={result['feasibility']['class']}"


# ════════════════════════ 其餘兩個 KILL 分類（完整性補測，非硬性驗收但強化正確性）════════════════════════


def t_signature_required_kills_when_only_locked_post_and_no_fallback():
    state = {
        "site_url": "https://locked.example.com/news",
        "validation": {"allowed_domains": ["locked.example.com"]},
        "recon_report": {
            "http_status": 200,
            "soft_block_detected": False,
            "api_candidates": [
                {
                    "method": "POST",
                    "url": "https://locked.example.com/api/news",
                    "request_post_data": "nonce=<redacted>&page=1",
                    "article_record_count": 0,
                }
            ],
            "feed_candidates": [],
            "link_samples": [],
            "http_entry_sample": {"status": 200, "link_samples": []},
        },
    }
    result = graph.feasibility_triage(state)
    ok = result["feasibility"]["class"] == "KILL_signature_required"
    return ok, f"class={result['feasibility']['class']}"


def t_js_rendered_site_uses_browser_transport_instead_of_dying():
    """**JS 才看得到連結不再是死信**（2026-08-02 解除自綁手腳）。

    舊行為判 `KILL_js_required` 直接死信——但這等於「明明 Playwright 抓得到卻放棄」。
    產碼契約本來就支援 scrapy-playwright，現在還教了捲動載入更多。
    新行為：標記需要瀏覽器傳輸（FEASIBLE_BROWSER）往下走。
    """
    result = graph.feasibility_triage({
        "site_url": "https://spa.example.com/news",
        "validation": {"allowed_domains": ["spa.example.com"]},
        "recon_report": {
            "http_status": 200,
            "soft_block_detected": False,
            "api_candidates": [],
            "feed_candidates": [],
            "link_samples": [{"url": "https://spa.example.com/news/1", "text": "Article"}],
            "http_entry_sample": {"status": 200, "link_samples": []},
        },
    })
    return (
        result["feasibility"]["class"] == "FEASIBLE_BROWSER"
        and "failure_class" not in result
        and graph.route_after_triage(result) == "strategy_decision"
    ), f"class={result['feasibility']['class']}"


def t_public_browser_path_survives_plain_http_block():
    state = {
        "site_url": "https://browser.example.com/news",
        "validation": {"allowed_domains": ["browser.example.com"]},
        "recon_report": {
            "http_status": 200,
            "access_assessment": "browser_required_http_blocked",
            "soft_block_detected": False,
            "api_candidates": [],
            "feed_candidates": [],
            "link_samples": [
                {
                    "url": "https://browser.example.com/news/1",
                    "text": "Article",
                }
            ],
            "http_entry_sample": {"status": 403, "link_samples": []},
        },
    }
    result = graph.feasibility_triage(state)
    merged = {**state, **result}
    route = graph.route_after_triage(merged)
    ok = (
        result["feasibility"]["class"] == "FEASIBLE_BROWSER"
        and "failure_class" not in result
        and route == "strategy_decision"
    )
    return ok, f"class={result['feasibility']['class']} route={route}"


# ════════════════════════ (d) escalate_human：死信格式正確、不阻塞 ════════════════════════


def t_escalate_writes_dead_letter_without_blocking():
    state = {
        "site_url": "https://retrylimit.example.com/news",
        "source_prefix": "retrylimitex",
        "run_id": "test-retrylimit-0001",
        "retry_count": 3,
        "kimi_used": True,
        "error_signature_history": ["http_403", "json_path_wrong", "http_403"],
        "diagnosis": {"error_signature": "http_403", "failure_type": "transport_blocked"},
        "topic_result": {"status": "ok"},
    }
    dead_letter_path = None
    try:
        t0 = time.time()
        result = graph.escalate_human(state)
        elapsed = time.time() - t0
        dead_letter_path = result.get("dead_letter_path")
        record = json.loads(Path(dead_letter_path).read_text(encoding="utf-8"))
        required_keys = {
            "ts",
            "site_url",
            "source_prefix",
            "failure_class",
            "triage_reason",
            "recon_evidence",
            "tried",
            "suggested_action",
            "status",
        }
        ok = (
            result["status"] == "escalated"
            and elapsed < 2.0  # 非阻塞：不應卡在 interrupt() 等待 resume
            and required_keys <= set(record)
            and record["tried"]["retry_count"] == 3
            and record["tried"]["kimi_used"] is True
            and record["failure_class"] == "repair_exhausted"
        )
    finally:
        _cleanup(dead_letter_path)
    return ok, f"status={result.get('status')} elapsed={elapsed:.3f}s"


def t_escalate_marks_topic_provider_outage_distinctly():
    state = {
        "site_url": "https://topicoutage.example.com/news",
        "source_prefix": "topicoutagex",
        "run_id": "test-topicoutage-0001",
        "topic_result": {"mode": "enforce", "provider": "gemini", "status": "gemini_unavailable"},
    }
    dead_letter_path = None
    try:
        result = graph.escalate_human(state)
        dead_letter_path = result.get("dead_letter_path")
        record = json.loads(Path(dead_letter_path).read_text(encoding="utf-8"))
        ok = (
            result["failure_class"] == "topic_provider_unavailable"
            and record["failure_class"] == "topic_provider_unavailable"
            and "主題閘門" in record["triage_reason"]
        )
    finally:
        _cleanup(dead_letter_path)
    return ok, f"failure_class={result.get('failure_class')}"


def t_auth_blocked_entry_is_not_reported_as_discovery_empty():
    """401/403 擋住時要說是授權問題，不能報成「找不到文章連結」。

    Reuters 實例：整站回 401，什麼都看不到，卻被歸成 discovery_empty——
    讀死信的人會以為是入口 URL 給錯，實際上是根本進不去。兩者處置完全不同。
    """
    base = {
        "site_url": "https://paywalled.example.com/markets/",
        "source_prefix": "paywalled",
        "run_id": "test-auth-0001",
        "validation": {"allowed_domains": ["paywalled.example.com"]},
    }
    blocked = graph.feasibility_triage({
        **base,
        "recon_report": {
            "http_status": 401,
            "soft_block_detected": False,
            "access_assessment": "browser_session_required",
            "api_candidates": [], "feed_candidates": [], "link_samples": [],
            "http_entry_sample": {"status": 401, "link_samples": []},
        },
    })["feasibility"]
    reachable = graph.feasibility_triage({
        **base,
        "recon_report": {
            "http_status": 200,
            "soft_block_detected": False,
            "api_candidates": [], "feed_candidates": [], "link_samples": [],
            "http_entry_sample": {"status": 200, "link_samples": []},
        },
    })["feasibility"]
    return (
        blocked["class"] == "KILL_auth_required"
        and "401" in blocked["reason"]
        # 進得去但沒連結，仍然是 discovery_empty（不能一律改判）
        and reachable["class"] == "KILL_discovery_empty"
    ), f"blocked={blocked['class']} reachable={reachable['class']}"


TESTS = [
    t_challenge_page_wording_alone_does_not_kill,
    t_kill_route_structurally_never_reaches_generation,
    t_blocked_and_empty_entry_escalates_without_touching_generate,
    t_clean_api_evidence_is_feasible_and_routes_to_strategy,
    t_discovery_empty_kills_and_writes_dead_letter,
    t_auth_blocked_entry_is_not_reported_as_discovery_empty,
    t_discovery_not_empty_when_recon_itself_failed,
    t_sample_urls_override_prevents_false_discovery_kill,
    t_signature_required_kills_when_only_locked_post_and_no_fallback,
    t_js_rendered_site_uses_browser_transport_instead_of_dying,
    t_public_browser_path_survives_plain_http_block,
    t_escalate_writes_dead_letter_without_blocking,
    t_escalate_marks_topic_provider_outage_distinctly,
]


def main() -> int:
    failed = 0
    for test in TESTS:
        try:
            ok, detail = test()
        except Exception as exc:  # noqa: BLE001
            ok, detail = False, f"EXCEPTION {exc}"
        print(f"[{'PASS' if ok else 'FAIL'}] {test.__name__}: {detail}")
        failed += not ok
    print(f"\n{len(TESTS) - failed}/{len(TESTS)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())

"""以保存證據驗證候選爬蟲，不連正式網站。

本模組是控制層服務：只把 EvidencePack 編成 fixture 規格並啟動 crawler runtime。
未知候選碼不會在本程序 import，crawler runtime 也不依賴 Spider Forge。
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

from ..output import artifacts
from ..state import SpiderForgeState
from .sandbox import run_fixture_candidate


def _listing_fixture(state: SpiderForgeState) -> dict[str, str]:
    pack = state.get("evidence_pack") or {}
    exchange = pack.get("replay_exchange") or {}
    request = exchange.get("request") or {}
    response = exchange.get("response") or {}
    replay_body = str(response.get("body_excerpt") or "")
    if replay_body:
        api = pack.get("api_sample") or {}
        return {
            "url": str(
                response.get("final_url")
                or request.get("url")
                or api.get("url")
                or state.get("site_url")
                or ""
            ),
            "body": replay_body,
            "content_type": str(
                response.get("content_type")
                or api.get("content_type")
                or "application/json"
            ),
            "capture_source": "replay_exchange",
        }

    recon = state.get("recon_report") or {}
    entry = pack.get("entry_observation") or {}

    # 重播用的文件要跟**候選實際會拿到的**文件同一種，否則等於拿 A 的頁面去測 B 的碼。
    # 踩過的坑（MoneyDJ 實測）：偵查子迴圈驗過的是純 HTTP 的頁面連結、requirements
    # 也沒有 browser_transport，產碼因此是照純 HTTP 的 HTML 寫的；但這裡不分青紅皂白
    # 優先用瀏覽器的 dom_excerpt（只取 main 容器、還被消毒與截斷過），selector 當然找不到東西，
    # 於是回報 insufficient_items 並歸類成「selector 寫錯」——歸因整個是錯的，
    # 兩輪修復都在修一支其實沒問題的爬蟲。
    browser_required = "browser_transport" in set(pack.get("requirements") or [])
    browser_dom = str(recon.get("dom_excerpt") or "")
    plain_html = str(
        entry.get("html_excerpt")
        or (recon.get("http_entry_sample") or {}).get("body_excerpt")
        or ""
    )
    ordered = (
        (("browser_dom", browser_dom), ("entry_observation", plain_html))
        if browser_required
        else (("entry_observation", plain_html), ("browser_dom", browser_dom))
    )
    capture_source, body = next(
        ((name, text) for name, text in ordered if text), ("entry_observation", "")
    )
    return {
        "url": str(
            recon.get("final_url")
            or entry.get("final_url")
            or state.get("site_url")
            or ""
        ),
        "body": body,
        "content_type": "text/html",
        "capture_source": capture_source,
    }


def _reachable_first(
    samples: list[dict[str, Any]], listing_body: str
) -> list[dict[str, Any]]:
    """把「列表頁真的連得到」的明細樣本排前面。

    離線重播要求候選的列表 callback 對每一份明細樣本產出對應的 request。但
    ``discovered_detail_urls`` 會把使用者給的 ``sample_urls`` 排在最前面，而那些
    **不保證出現在今天的列表頁上**——BBC 實測：站台設定裡的兩個範例網址是十天前的
    文章，今天的商業版根本沒有連到它們。這時 fixture 要求的是一件不可能的事，
    任何正確的爬蟲都過不了，兩輪修復全部白花，最後還被歸成 selector 寫錯——
    歸因整個是錯的。

    ``sample_urls`` 的用途是「已知的明細頁長什麼樣」，用來教抽取規則；
    它不代表「列表頁一定連得到它」。所以這裡只調順序：連得到的優先，
    一個都連不到時仍照原樣送出（至少還能驗明細 callback 的抽取邏輯）。
    """
    def reachable(sample: dict[str, Any]) -> bool:
        url = str(sample.get("requested_url") or sample.get("final_url") or "")
        if not url:
            return False
        # 比對整串與最後一段：DOM 裡常見的是相對路徑而不是絕對網址。
        return url in listing_body or url.rstrip("/").rsplit("/", 1)[-1] in listing_body

    linked = [sample for sample in samples if reachable(sample)]
    return linked or samples


def build_fixture_spec(state: SpiderForgeState) -> dict[str, Any]:
    """把已保存的列表與明細 response 整理成 runtime 中立的 JSON 規格。"""
    pack = state.get("evidence_pack") or {}
    listing = _listing_fixture(state)
    strategy = str(
        state.get("strategy")
        or (pack.get("strategy") or {}).get("strategy")
        or ""
    )
    needs_detail_callbacks = (
        strategy != "api"
        or listing["capture_source"] != "replay_exchange"
    )
    samples = (
        _reachable_first(
            list(pack.get("dom_samples") or pack.get("detail_samples") or []),
            listing["body"],
        )[:2]
        if needs_detail_callbacks
        else []
    )
    observed = len(samples)
    if not observed:
        observed = int(
            (pack.get("api_sample") or {}).get("article_record_count")
            or len(pack.get("discovered_detail_urls") or [])
            or len(
                (pack.get("entry_observation") or {}).get(
                    "link_samples"
                )
                or []
            )
            or 1
        )
    validation = state.get("validation") or {}
    minimum = min(
        max(1, int(validation.get("min_valid_items") or 1)),
        max(1, observed),
    )
    schema = state.get("target_schema") or {}
    required_fields = [
        name
        for name, rule in (schema.get("fields") or {}).items()
        if not isinstance(rule, dict) or rule.get("required", True)
    ]
    return {
        "listing": listing,
        "required_fields": required_fields,
        "detail_samples": samples,
        "browser_required": (
            "browser_transport" in set(pack.get("requirements") or [])
        ),
        "min_listing_outputs": minimum,
        "min_content_chars": int(
            validation.get("min_content_chars") or 40
        ),
        "expected_attributes": {
            "name": state.get("source_prefix"),
            "source_prefix": state.get("source_prefix"),
            "source": state.get("site_name"),
            "source_type": schema.get("source_type", "media"),
            "content_scope": schema.get(
                "content_scope", "summary_only"
            ),
        },
    }


def fixture_test(state: SpiderForgeState) -> dict[str, Any]:
    """在獨立 crawler runtime 以保存 response 執行候選 callback。"""
    run_id = state.get("run_id") or uuid.uuid4().hex[:8]
    # 候選碼在 generate/repair 當下就落檔了；這裡沿用，沒有才補寫
    # （讓 fixture_test 仍能被單獨呼叫，見 tests/manual/）。
    existing = state.get("candidate_path")
    candidate = (
        Path(existing)
        if existing and Path(existing).is_file()
        else artifacts.write_candidate(
            run_id, state["source_prefix"], state.get("spider_code", "")
        )
    )
    fixture = build_fixture_spec(state)
    if not fixture["listing"]["body"]:
        result = {
            "passed": False,
            "errors": ["fixture_missing_listing_body"],
            "callback_errors": [],
            "fixture_source": fixture["listing"]["capture_source"],
        }
    else:
        execution = run_fixture_candidate(
            str(candidate),
            fixture,
            allowed_domains=list(
                (state.get("validation") or {}).get(
                    "allowed_domains"
                )
                or []
            ),
        )
        try:
            result = json.loads(execution.stdout)
        except (TypeError, json.JSONDecodeError):
            result = {
                "passed": False,
                "errors": [
                    "fixture_runner_invalid_output",
                    str(execution.stderr or "")[-1000:],
                ],
                "callback_errors": [],
            }
        result["exit_code"] = execution.exit_code
        result["timed_out"] = execution.timed_out
        result["fixture_source"] = fixture["listing"][
            "capture_source"
        ]
    return {
        "fixture_result": result,
        "candidate_path": str(candidate),
        "run_id": run_id,
        "status": "testing" if result.get("passed") else "validating",
    }

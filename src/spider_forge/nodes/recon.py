"""recon 節點：零成本 HTTP + 瀏覽器雙軌探測，組出 recon_report。

瀏覽器探測器 prober 由 __init__ 注入：
- 預設 None → 呼叫時才 late-bind ``clients.browser.probe``（讓測試能 monkeypatch
  browser.probe，行為與原本函式內 import 一致）。
- 傳入自訂 prober → 替換探測方式，不動節點邏輯（依賴注入 = nn.Module 的形狀）。
"""

from __future__ import annotations

from ..shared import evidence as evidence_tools
from ..state import SpiderForgeState
from .base import Node


def _run_recon(state: SpiderForgeState, prober) -> dict:
    site_url = state["site_url"]
    http_sample = evidence_tools._fetch_sample(
        site_url,
        max_chars=6000,
        max_links=250,
        include_raw_body=True,
    )
    raw_http_body = http_sample.pop("_raw_body", "")
    try:
        report = prober(
            site_url,
            max_links=200,
            storage_state_path=(
                state.get("access_context_ref")
                if state.get("access_mode") == "browser_session"
                else None
            ),
        )
    except Exception as exc:
        error_message = str(exc)
        access_ref = str(state.get("access_context_ref") or "")
        if access_ref:
            error_message = error_message.replace(access_ref, "<access_context_ref>")
        report = {
            "url": site_url,
            "http_status": None,
            "title": None,
            "aria_snapshot": "",
            "api_candidates": [],
            "recon_error": error_message[:500],
        }
    original_browser_status = report.get("http_status")
    original_browser_blocked = (
        original_browser_status in {401, 403, 429}
        or report.get("soft_block_detected") is True
    )
    if (
        original_browser_blocked
        and http_sample.get("status") == 200
        and "html" in str(http_sample.get("content_type") or "").lower()
        and raw_http_body
        and state.get("access_mode", "public") == "public"
    ):
        original_report = report
        try:
            hydrated = prober(
                site_url,
                max_links=200,
                document_html=raw_http_body,
            )
            report = hydrated
            report["original_browser_status"] = original_browser_status
            report["original_document_chain"] = (
                original_report.get("document_chain") or []
            )[:10]
            report["browser_hydration_used"] = True
        except Exception as exc:  # keep the original 403 evidence
            report["browser_hydration_error"] = str(exc)[:500]
    report["http_entry_sample"] = http_sample
    report["feed_candidates"] = evidence_tools._discover_feed_candidates(http_sample)
    report["final_url"] = (
        report.get("final_url")
        or http_sample.get("final_url")
        or site_url
    )
    report["canonical_url"] = (
        report.get("canonical_url")
        or http_sample.get("canonical_url")
        or report["final_url"]
    )
    browser_status = report.get(
        "original_browser_status", report.get("http_status")
    )
    plain_status = http_sample.get("status")
    browser_ok = bool(browser_status and browser_status < 400)
    plain_blocked = plain_status in {401, 403, 429}
    browser_blocked = (
        browser_status in {401, 403, 429}
        or report.get("soft_block_detected") is True
    )
    if browser_blocked and plain_status == 200:
        access_assessment = "browser_blocked_http_ok"
    elif browser_ok and plain_blocked:
        access_assessment = "browser_required_http_blocked"
    elif browser_blocked and state.get("access_mode") == "browser_session":
        access_assessment = "browser_session_invalid_or_blocked"
    elif browser_blocked:
        access_assessment = "browser_session_required"
    elif browser_ok:
        access_assessment = "browser_public_ok"
    elif plain_status == 200:
        access_assessment = "http_public_ok"
    else:
        access_assessment = "unresolved"
    if access_assessment == "browser_blocked_http_ok":
        report["final_url"] = http_sample.get("final_url") or report["final_url"]
        report["canonical_url"] = (
            http_sample.get("canonical_url") or report["final_url"]
        )
    report["access_assessment"] = access_assessment
    return {"recon_report": report, "status": "reconning"}


class Recon(Node):
    """入口探測節點：組出 recon_report（雙軌探測 + access 判定）。"""

    def __init__(self, prober=None):
        self._prober = prober

    def __call__(self, state: SpiderForgeState) -> dict:
        prober = self._prober
        if prober is None:
            from ..clients.browser import probe as prober
        return _run_recon(state, prober)

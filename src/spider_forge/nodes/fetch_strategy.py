"""select_fetch_strategy 節點：偵查子迴圈的轉盤——選一種抓法，或宣告四種都試完了。

迴圈長這樣（分支寫在 ``pipelines/pipeline.py``）::

    select_fetch_strategy → discover_links → verify_samples → verify_pagination
            ↑                    │                │                  │
            └────────────────────┴────────────────┴──────────────────┘
                     任一檢查不過且還有沒試過的抓法

**為什麼記錄簿放在這個節點**：路由函式只能讀 state 不能寫，所以「上一輪卡在哪個檢查」
沒有人記。這裡在挑下一階之前先回頭看上一輪的三份判定，把失敗原因寫進
``discovery_attempts``——死信要能回答「四種抓法分別卡在哪」，不是只有一句失敗。

換抓法時會清掉上一輪的偵查結果。不清的話，新抓法挑不出連結時，
``collect_evidence`` 會沿用上一輪的樣本，等於用 A 抓法的證據產 B 抓法的碼。
"""

from __future__ import annotations

from typing import Any

from ..shared.fetch_strategies import (
    BROWSER_SCROLL_LINKS,
    LABELS,
    available_strategies,
)
from ..state import SpiderForgeState
from .base import Node

# 換抓法時要歸零的欄位；每一個都是「上一輪偵查的產物」，留著就會被下游誤用。
_STALE_ON_RETRY: dict[str, Any] = {
    "discovered_detail_urls": [],
    "link_discovery": {},
    "detail_samples": [],
    "sample_verification": {},
    "pagination": {},
    "pagination_probe": {},
}


def _failed_check(state: SpiderForgeState) -> tuple[str, str]:
    """上一輪卡在哪一個檢查，以及它給的原因。"""
    link_discovery = state.get("link_discovery") or {}
    # 前端資料介面沒有明細頁連結卻是通過檢查一的（記錄自帶內容），
    # 只看 discovered_detail_urls 會把它後面才發生的失敗全部誤記成「挑不到連結」。
    if not state.get("discovered_detail_urls") and not link_discovery.get("api_records"):
        reason = link_discovery.get("reason") or "挑不出文章連結"
        return "檢查一：找得到文章連結", str(reason)

    verification = state.get("sample_verification") or {}
    if not verification.get("passed"):
        return "檢查二：樣本是真文章", str(verification.get("reason") or "樣本驗證未通過")

    probe = state.get("pagination_probe") or {}
    return "檢查三：翻頁有效", str(probe.get("reason") or "翻頁候選都沒通過實抓驗證")


class SelectFetchStrategy(Node):
    """挑下一種沒試過的抓法；沒得挑了就判 ``discovery_unusable``。

    ``scroll_prober`` 可注入（測試或換捲動方式用）：``(url) -> probe_scroll 的回傳``。
    預設 None → 選到捲動那一階時才 late-bind ``clients.browser.probe_scroll``。
    """

    def __init__(self, *, scroll_prober=None):
        self._scroll_prober = scroll_prober

    def _scroll_evidence(self, state: SpiderForgeState) -> tuple[dict, str]:
        """捲一次看載不載得出更多連結；回傳更新後的 recon_report 與失敗原因。

        捲動探測**不放在 recon**：recon 是每一站都要付的成本，而捲動只有在前面
        幾階都失敗時才用得到。探測本體仍然只跑一次——捲完的連結存回 recon_report，
        後面每一輪共用同一份，不重捲。
        """
        report = dict(state.get("recon_report") or {})
        prober = self._scroll_prober
        if prober is None:
            from ..clients.browser import probe_scroll as prober

        url = str(report.get("final_url") or state.get("site_url") or "")
        try:
            probe = prober(url)
        except Exception as exc:  # noqa: BLE001 — 捲不動只代表這一階不可用
            report["scroll_link_samples"] = []
            report["scroll_probe"] = {"error": str(exc)[:300]}
            return report, f"捲動探測失敗：{str(exc)[:160]}"

        report["scroll_link_samples"] = list(probe.get("link_samples") or [])
        report["scroll_probe"] = {
            key: probe.get(key)
            for key in ("links_after_each_round", "rounds_scrolled", "loaded_more",
                        "navigation_error")
        }
        if not probe.get("loaded_more"):
            counts = probe.get("links_after_each_round") or []
            return report, f"捲到底 {probe.get('rounds_scrolled', 0)} 次，連結數沒有增加（{counts}）"
        return report, ""

    def __call__(self, state: SpiderForgeState) -> dict:
        pool = list(state.get("fetch_strategy_pool") or available_strategies(state))
        attempts = list(state.get("discovery_attempts") or [])
        extra: dict[str, Any] = {}

        current = state.get("fetch_strategy")
        if current:
            check, reason = _failed_check(state)
            attempts.append({"strategy": current, "failed_check": check, "reason": reason})

        tried = {row["strategy"] for row in attempts}
        for strategy in [row for row in pool if row not in tried]:
            if strategy == BROWSER_SCROLL_LINKS and "scroll_link_samples" not in (
                state.get("recon_report") or {}
            ):
                # 捲動是唯一「要先探測才知道可不可用」的一階；捲不出更多連結就
                # 跟上一階一模一樣，直接記一筆跳過，不必浪費一輪抓樣本才發現。
                report, failure = self._scroll_evidence({**state, **extra})
                extra["recon_report"] = report
                if failure:
                    attempts.append({
                        "strategy": strategy,
                        "failed_check": "檢查一：找得到文章連結",
                        "reason": failure,
                    })
                    continue

            return {
                **_STALE_ON_RETRY,
                **extra,
                "fetch_strategy": strategy,
                "fetch_strategy_pool": pool,
                "discovery_attempts": attempts,
                "status": "reconning",
            }

        return {
            **extra,
            "fetch_strategy": None,
            "fetch_strategy_pool": pool,
            "discovery_attempts": attempts,
            "failure_class": "discovery_unusable",
            "status": "triaging",
        }


def attempts_summary(state: SpiderForgeState) -> list[str]:
    """給死信看的一行一階：哪種抓法卡在哪個檢查、為什麼。"""
    return [
        f"{LABELS.get(row['strategy'], row['strategy'])} → {row['failed_check']}：{row['reason']}"
        for row in state.get("discovery_attempts") or []
    ]

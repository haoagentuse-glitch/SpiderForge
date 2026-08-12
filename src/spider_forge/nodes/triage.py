"""feasibility_triage 節點：模型呼叫前的確定性可行性分流。

保守原則：只有明確確定性訊號才判 KILL；其餘一律 FEASIBLE，讓 strategy_decision
/ 生成迴圈照樣嘗試——寧可漏殺可做站，不可誤殺（誤殺會直接壓低成功率）。

本節點刻意沒有可注入設定：判準全是「有 vs 沒有」的結構事實，不是可調參數。
放寬它等於放寬 KILL 的定義，應該改的是 pipeline 的路由，不是這裡的門檻。
"""

from __future__ import annotations

from ..shared.evidence import (
    _discover_detail_urls,
    _is_replayable_article_api,
    _matches_validation_url,
)
from ..shared.summaries import evidence_summary
from ..state import SpiderForgeState
from .base import Node


def _blocked_by_authorization(report: dict) -> bool:
    """入口是否因授權而被擋（401/403，或 recon 已判定需要登入態）。

    刻意**不**升級成 policy_kill：401/403 是灰色登入牆，D2 規定要照樣試；
    這裡只在「試完什麼都沒有」時用來說清楚原因。
    """
    statuses = {
        report.get("http_status"),
        (report.get("http_entry_sample") or {}).get("status"),
    }
    return (
        report.get("access_assessment") == "browser_session_required"
        or bool(statuses & {401, 403})
    )


# 機器人防護頁的字樣（沿用 browser.probe 判 soft_block 的同一組概念）。
_WAF_MARKERS = (
    "cloudflare", "attention required", "access denied", "captcha",
    "verify you are human", "just a moment", "請稍候", "驗證您是人類",
)


def _blocked_by_waf(report: dict) -> bool:
    """被擋是因為**機器人防護**而不是因為需要帳號。

    兩者都回 403，處置卻完全相反：WAF 要降速／稍後再試／換傳輸，
    授權牆要換入口或取得帳號。歸成同一類的話，人會照著錯的方向查——
    工商時報實測就是這樣：Cloudflare 擋下來卻寫「需要登入態或授權」。
    """
    if report.get("soft_block_detected") is True:
        return True
    haystack = " ".join(
        str(value or "").lower()
        for value in (
            report.get("title"),
            (report.get("http_entry_sample") or {}).get("body_excerpt"),
        )
    )
    return any(marker in haystack for marker in _WAF_MARKERS)


class FeasibilityTriage(Node):
    """recon 後、生成前的確定性可行性分流（D1：KILL 立即轉死信、不生成）。"""

    @staticmethod
    def _kill(cls: str, reason: str, report: dict) -> dict:
        return {
            "feasibility": {
                "class": cls,
                "reason": reason,
                "evidence_summary": evidence_summary(report),
            },
            "failure_class": cls,
            "status": "triaging",
        }

    def __call__(self, state: SpiderForgeState) -> dict:
        report = state.get("recon_report") or {}
        structured = [
            *(report.get("api_candidates") or []),
            *(report.get("feed_candidates") or []),
        ]
        replayable = [c for c in structured if _is_replayable_article_api(c)]
        has_sample_urls = bool(state.get("sample_urls"))
        browser_transport_required = (
            report.get("access_assessment") == "browser_required_http_blocked"
        )

        # 挑戰頁字樣（soft_block_detected）**不再直接 KILL**——那等於被擋一次就放棄，
        # 與本節點自己的原則（寧可漏殺可做站，不可誤殺）矛盾。誤判來源很多：頁面剛好
        # 提到 "cloudflare"、錯誤頁一閃而過、瀏覽器渲染時序。改成往下走，讓後面的閘門
        # 用「實際抓到什麼」判斷——那比字樣比對可靠得多。
        http_entry_status = (report.get("http_entry_sample") or {}).get("status")

        # recon 本身失敗（探測出錯）是不確定訊號，以下三類一律不判定，保守放行。
        recon_incomplete = bool(
            report.get("recon_error") or report.get("navigation_error")
        )
        if not recon_incomplete:
            html_links = _discover_detail_urls(state, report, limit=1)

            # KILL_signature_required：唯一結構化路徑是需簽章/nonce 的 POST
            # （request_post_data 含 "<redacted>" 是 browser_probe._redact_payload
            # 對 token/nonce/csrf 等 key 的遮蔽產物），且沒有可重播 GET 候選、
            # 沒有 HTML 明細連結、使用者也沒給已知樣本頁。
            signature_locked = [
                c
                for c in structured
                if str(c.get("method") or "GET").upper() != "GET"
                and "<redacted>" in str(c.get("request_post_data") or "")
            ]
            if signature_locked and not replayable and not html_links and not has_sample_urls:
                return self._kill(
                    "KILL_signature_required",
                    f"signature_like_post_candidates="
                    f"{[c.get('url') for c in signature_locked][:3]} "
                    "無可重播 GET 候選、無 HTML 明細連結、無 sample_urls",
                    report,
                )

            # 「瀏覽器看得到、plain HTTP 看不到」**不再是 KILL**（2026-08-02）。
            # 舊行為判 KILL_js_required 直接死信，但這等於明明 Playwright 抓得到卻放棄——
            # 產碼契約本來就支援 scrapy-playwright，現在還教了捲動載入。
            # 改成標記「需要瀏覽器傳輸」往下走，讓產碼用 Playwright 路徑。
            browser_links = [
                row
                for row in (report.get("link_samples") or [])
                if _matches_validation_url(str(row.get("url") or ""), state)
            ]
            raw_links = [
                row
                for row in (report.get("http_entry_sample") or {}).get("link_samples")
                or []
                if _matches_validation_url(str(row.get("url") or ""), state)
            ]
            js_rendered_only = bool(
                browser_links and not raw_links and not replayable
                and not (report.get("feed_candidates"))
            )
            if js_rendered_only:
                browser_transport_required = True

            # 零證據的兩種成因要分開，否則死信會誤導人工判斷：
            #   auth_required —— 被 401/403 或登入牆擋在門外，什麼都看不到
            #   discovery_empty —— 進得去，但這個入口真的沒有文章連結
            # 前者要換入口／取得授權，後者多半是入口 URL 給錯，處置完全不同。
            if not replayable and not html_links:
                if _blocked_by_authorization(report) and _blocked_by_waf(report):
                    # 先問「是不是機器人防護」再問「是不是授權」：兩者都回 403，
                    # 但一個要降速稍後再試，一個要去拿帳號，指錯方向會讓人白查。
                    return self._kill(
                        "KILL_waf_blocked",
                        f"被機器人防護擋下（http_status={report.get('http_status')} "
                        f"http_entry_status={http_entry_status} "
                        f"title={report.get('title')!r}）：兩軌都取不到內容。"
                        "不是缺帳號——降低頻率稍後再試，或改用不同的傳輸方式",
                        report,
                    )
                if _blocked_by_authorization(report):
                    return self._kill(
                        "KILL_auth_required",
                        f"存取被拒（http_status={report.get('http_status')} "
                        f"http_entry_status={http_entry_status} "
                        f"access_assessment={report.get('access_assessment')}）："
                        "需要登入態或授權才看得到內容；登入牆不繞過",
                        report,
                    )
                return self._kill(
                    "KILL_discovery_empty",
                    "無 article-like API/feed 候選，也找不到任何 HTML 明細連結"
                    "（入口可正常存取，可能是入口 URL 給錯）",
                    report,
                )

        feasibility_class = (
            "FEASIBLE_API"
            if replayable
            else "FEASIBLE_BROWSER"
            if browser_transport_required
            else "FEASIBLE_HTML"
        )
        return {
            "feasibility": {
                "class": feasibility_class,
                "reason": (
                    "存在可重播結構化候選"
                    if replayable
                    else "plain HTTP 取不到文章連結，但公開瀏覽器取得了——用 Playwright 傳輸"
                    if browser_transport_required
                    else "無可重播 API，但證據不足以確定性判 KILL，保守放行嘗試 HTML/軸樹策略"
                ),
                "evidence_summary": evidence_summary(report),
            },
            "status": "triaging",
        }

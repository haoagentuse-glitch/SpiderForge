"""discover_links 節點：從 recon 抓到的連結裡挑出「文章明細頁」。

**為什麼要有這個節點**（BBC 實跑證實的缺陷）：原本 `collect_evidence` 直接用
`_discover_detail_urls` 按 DOM 順序取前 2 個連結當文章樣本。HTML 的導覽列必然
排在內文之前，所以零設定跑任何新站，拿到的都是導覽連結——BBC 前 25 筆全是導覽，
真文章在第 26–30 筆。樣本錯了，產碼模型就在錯的基礎上學 selector，而修復迴圈
用的是同一份證據，修幾輪都修不好。

三層，各司其職（缺一不可）：

1. **硬性排除（程式）**——入口自己、#錨點、首頁、跨網域。純結構事實。
2. **URL pattern（程式，使用者設定）**——`article_url_patterns` / `excluded`。
   **哪個版面**的精確控制只能靠這層：模型分不出「商業版 vs 體育版」，那是
   使用者意圖不是網頁裡的事實。
3. **模型排序**——依序試 Gemini → Ollama → 啟發式。負責「導覽 vs 文章」，讓沒有
   設定 pattern 的新站第一次跑也不會荒謬失敗。
   刻意不說「Gemini 主力」：實測 flash-lite free tier 是 **RPD 500**，用完就整天
   429（等 40 秒不會恢復）。所以這是**機會性使用**——有額度就享受它的品質，
   沒有就退 Ollama，兩者都不可用還有啟發式。降級原因記在 `link_discovery`。

三層都失敗時退回啟發式（連結文字長度 + 路徑深度 + 末段像 id），BBC 實測這組
在前 2 名命中 2/2；但它只有一個站的驗證，所以擺在最後而不是最前。
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

from ..shared.evidence import _matches_validation_url
from ..state import SpiderForgeState
from .base import Node

_MAX_CANDIDATES = 40   # 送進模型的上限；再多對判斷沒幫助，只是浪費 token


def _hard_excluded(url: str, entry_urls: set[str]) -> bool:
    """結構上不可能是文章明細頁的連結。"""
    if not url or url in entry_urls:
        return True
    if "#" in url:                      # /business#bbc-main 這類跳過導覽的錨點
        return True
    parsed = urlparse(url)
    path = (parsed.path or "").strip("/")
    return not path                     # 首頁


def _heuristic_score(url: str, text: str) -> tuple:
    """啟發式排序鍵（越大越像文章）。三個弱訊號疊加，不依賴任何單一特徵。"""
    segments = [s for s in urlparse(url).path.split("/") if s]
    last = segments[-1] if segments else ""
    id_like = len(last) >= 8 and any(c.isdigit() for c in last) and "-" not in last
    return (int(id_like), len(segments), min(len(text), 200))


class DiscoverArticleLinks(Node):
    """挑出文章明細頁的候選連結，寫進 ``discovered_detail_urls``。

    ``picker`` 可注入（測試或換模型用）：``(rows) -> list[index]``。
    預設 None → 呼叫時才 late-bind Gemini，失敗退 Ollama，再失敗退啟發式。
    """

    def __init__(self, *, limit: int = 2, picker=None):
        self._limit = limit
        self._picker = picker
        self._fallback_reason = ""

    # ── 三層過濾 ────────────────────────────────────────────────────────
    def _candidates(self, state: SpiderForgeState) -> list[dict]:
        report = state.get("recon_report") or {}
        entry_urls = {
            str(state.get("site_url") or ""),
            str(report.get("final_url") or ""),
            str(report.get("canonical_url") or ""),
        }
        rows: list[dict] = []
        seen: set[str] = set()
        for row in [
            *(report.get("link_samples") or []),
            *((report.get("http_entry_sample") or {}).get("link_samples") or []),
        ]:
            url = str(row.get("url") or "")
            if url in seen:
                continue
            seen.add(url)
            if _hard_excluded(url, entry_urls):            # ① 硬性排除
                continue
            if not _matches_validation_url(url, state):     # ② URL pattern
                continue
            rows.append({"index": len(rows), "url": url, "text": str(row.get("text") or "")})
            if len(rows) >= _MAX_CANDIDATES:
                break
        return rows

    # ── 第三層：模型排序，三段 fallback ──────────────────────────────────
    def _rank(self, rows: list[dict]) -> tuple[list[int], str]:
        if self._picker is not None:
            return list(self._picker(rows)), "injected"

        self._fallback_reason = ""
        try:
            from ..clients.links import pick_article_links

            picked = pick_article_links(rows)
            if picked:
                return picked, "gemini"
            self._fallback_reason = "gemini 回空清單"
        except Exception as exc:  # noqa: BLE001 — 挑連結失敗不該中斷整條流程
            # 429 是每日額度用完（實測 flash-lite free tier RPD=500，等 40 秒不會恢復），
            # 靜默降級會讓人以為模型在運作，所以原因要留在 state 裡而不只是印一行。
            self._fallback_reason = str(exc)[:200]
            print(f"[discover_links] Gemini 不可用，改用本地模型：{exc}")

        try:
            from ..clients.judge import judge
            from ..prompts.discover_links import LINK_PICK_SYSTEM, link_pick_prompt
            from ..schemas import LINK_PICK_SCHEMA

            result = judge(
                system=LINK_PICK_SYSTEM,
                user=link_pick_prompt(rows),
                schema=LINK_PICK_SCHEMA,
            )
            valid = {row["index"] for row in rows}
            picked = [int(i) for i in (result.get("article_indices") or []) if int(i) in valid]
            if picked:
                return picked, "ollama"
            self._fallback_reason += "；ollama 回空清單"
        except Exception as exc:  # noqa: BLE001
            self._fallback_reason += f"；ollama: {str(exc)[:150]}"
            print(f"[discover_links] 本地模型也不可用，退回啟發式：{exc}")

        ordered = sorted(rows, key=lambda r: _heuristic_score(r["url"], r["text"]), reverse=True)
        return [row["index"] for row in ordered], "heuristic"

    def __call__(self, state: SpiderForgeState) -> dict:
        rows = self._candidates(state)
        supplied = [str(u) for u in (state.get("sample_urls") or []) if u]

        if not rows:
            # 使用者給了 sample_urls 就還有救；否則交給下游閘門與診斷處理。
            return {
                "discovered_detail_urls": supplied[: self._limit],
                "link_discovery": {
                    "method": "none",
                    "candidates": 0,
                    "reason": "硬性排除與 URL pattern 過濾後沒有候選連結",
                },
            }

        picked, method = self._rank(rows)
        by_index = {row["index"]: row["url"] for row in rows}
        urls = list(supplied)                       # 使用者明給的樣本永遠優先
        for index in picked:
            url = by_index.get(index)
            if url and url not in urls:
                urls.append(url)

        return {
            "discovered_detail_urls": urls[: self._limit],
            "link_discovery": {
                "method": method,
                "candidates": len(rows),
                "picked": urls[: self._limit],
                "supplied_sample_urls": len(supplied),
                # 降級原因要留下來：Gemini 撞到每日額度時會安靜地退到 ollama，
                # 沒有這欄就看不出「模型其實沒在運作」。
                "fallback_reason": self._fallback_reason or None,
                # sample_urls 若已填滿 limit，模型挑的其實一個都沒用到。
                "model_picks_used": max(0, self._limit - len(supplied)),
            },
        }

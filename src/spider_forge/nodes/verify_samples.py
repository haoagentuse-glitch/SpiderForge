"""verify_samples 節點：挑到連結還不夠，要**實際抓下來**確認那是文章明細頁。

形狀跟 ``verify_pagination`` 一樣：**候選 → 逐一驗證 → 確定才往下放**。
差別在這裡不做降級——樣本錯了就是錯了，往下送只會讓產碼模型學到錯的 selector，
所以不通過就交給偵查子迴圈換下一種抓法（見 ``pipelines/pipeline.py`` 的路由）。

抓下來的樣本寫進 ``detail_samples``，``collect_evidence`` 直接沿用同一份——
**驗過的樣本就是送進 prompt 的樣本**，中間不再重抓，否則等於沒驗。

可注入（``__init__``）：

- ``fetcher``：``(state, urls) -> list[sample]``。預設 None → late-bind
  ``shared.evidence.fetch_detail_samples``（傳輸方式跟 collect_evidence 同一條路徑）。
- ``max_similarity``：兩份樣本判定為「同一頁」的相似度門檻。
"""

from __future__ import annotations

from ..shared.fetch_strategies import selected_structured
from ..shared.samples import verify_api_records, verify_detail_samples
from ..state import SpiderForgeState
from .base import Node


class VerifySamples(Node):
    """驗證明細樣本真的是文章，確定了才寫進 state。"""

    def __init__(self, *, fetcher=None, max_similarity: float = 0.9):
        self._fetcher = fetcher
        self._max_similarity = max_similarity

    def __call__(self, state: SpiderForgeState) -> dict:
        urls = list(state.get("discovered_detail_urls") or [])
        if not urls:
            records = int((state.get("link_discovery") or {}).get("api_records") or 0)
            if not records:
                return {
                    "detail_samples": [],
                    "sample_verification": {
                        "passed": False, "checked": 0, "compared": False,
                        "reason": "沒有任何明細頁候選可驗證",
                    },
                }
            # 前端資料介面沒有明細頁可抓，但**不能因此就放行**：記錄筆數是靠 JSON
            # 鍵名形狀猜的，股價與排行榜同樣會中（鉅亨網行情頁實測）。改成驗記錄內容。
            verdict = verify_api_records(selected_structured(state))
            return {
                "detail_samples": [],
                "sample_verification": {
                    **verdict,
                    "checked": verdict.get("records", 0),
                    "compared": False,
                    "reason": verdict.get("reason")
                    or f"前端資料介面的 {records} 筆記錄有標題也有時間，沒有明細頁可再比對",
                },
            }

        fetcher = self._fetcher
        if fetcher is None:
            from ..shared.evidence import fetch_detail_samples as fetcher

        try:
            samples = list(fetcher(state, urls) or [])
        except Exception as exc:  # noqa: BLE001 — 抓取失敗是「這種抓法不行」，不是流程錯誤
            return {
                "detail_samples": [],
                "sample_verification": {
                    "passed": False,
                    "checked": 0,
                    "reason": f"抓取明細樣本失敗：{str(exc)[:200]}",
                },
            }

        verification = verify_detail_samples(
            samples, state, max_similarity=self._max_similarity
        )
        return {"detail_samples": samples, "sample_verification": verification}

"""generation_preflight 節點：產碼後的確定性契約檢查（不花 LLM 額度）。

目前是薄殼：邏輯在 ``shared.generation``，節點負責統一形狀（__init__ 存設定 / __call__ 執行）
與依賴注入。傳 ``impl`` 可整支替換實作（測試或換演算法都不必 monkeypatch 全域）。
"""

from __future__ import annotations

from ..state import SpiderForgeState
from .base import Node


class GenerationPreflight(Node):
    """靜態檢查候選碼是否守住契約與安全規則，擋掉明顯壞碼。"""

    def __init__(self, *, impl=None):
        self._impl = impl

    def __call__(self, state: SpiderForgeState) -> dict:
        impl = self._impl
        if impl is None:  # late-bind：讓既有測試的 monkeypatch 仍然生效
            from ..shared.generation import preflight_generated_code as impl
        return impl(state)

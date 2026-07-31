"""persist_spider 節點：通過所有閘門後把候選升為 active 版本。

目前是薄殼：邏輯在 ``output.manager``，節點負責統一形狀（__init__ 存設定 / __call__ 執行）
與依賴注入。傳 ``impl`` 可整支替換實作（測試或換演算法都不必 monkeypatch 全域）。
"""

from __future__ import annotations

from ..state import SpiderForgeState
from .base import Node


class PersistSpider(Node):
    """寫入 active spider 與版本紀錄（可回滾）。"""

    def __init__(self, *, impl=None):
        self._impl = impl

    def __call__(self, state: SpiderForgeState) -> dict:
        impl = self._impl
        if impl is None:  # late-bind：讓既有測試的 monkeypatch 仍然生效
            from ..output.manager import persist_spider as impl
        return impl(state)

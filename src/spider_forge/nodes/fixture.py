"""fixture_test 節點：用保存的 response 離線重播候選 callback。

目前是薄殼：邏輯在 ``shared.fixture``，節點負責統一形狀（__init__ 存設定 / __call__ 執行）
與依賴注入。傳 ``impl`` 可整支替換實作（測試或換演算法都不必 monkeypatch 全域）。
"""

from __future__ import annotations

from ..state import SpiderForgeState
from .base import Node


class FixtureTest(Node):
    """不連網站就驗候選能不能抽出合格 item（引擎見 sandbox_runtime/）。"""

    def __init__(self, *, impl=None):
        self._impl = impl

    def __call__(self, state: SpiderForgeState) -> dict:
        impl = self._impl
        if impl is None:  # late-bind：讓既有測試的 monkeypatch 仍然生效
            from ..shared.fixture import fixture_test as impl
        return impl(state)

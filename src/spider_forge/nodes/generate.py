"""generate_spider 節點：呼叫產碼模型產出候選 spider。

目前是薄殼：邏輯在 ``shared.generation``，節點負責統一形狀（__init__ 存設定 / __call__ 執行）
與依賴注入。傳 ``impl`` 可整支替換實作（測試或換演算法都不必 monkeypatch 全域）。
"""

from __future__ import annotations

from ..state import SpiderForgeState
from .base import Node


class GenerateSpider(Node):
    """把證據與契約編成 prompt，產出候選爬蟲原始碼。"""

    def __init__(self, *, impl=None):
        self._impl = impl

    def __call__(self, state: SpiderForgeState) -> dict:
        impl = self._impl
        if impl is None:  # late-bind：讓既有測試的 monkeypatch 仍然生效
            from ..shared.generation import generate_spider as impl
        return impl(state)

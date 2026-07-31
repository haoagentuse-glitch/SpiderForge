"""sandbox_test 節點：在隔離子程序實跑候選爬蟲。

目前是薄殼：邏輯在 ``shared.sandbox``，節點負責統一形狀（__init__ 存設定 / __call__ 執行）
與依賴注入。傳 ``impl`` 可整支替換實作（測試或換演算法都不必 monkeypatch 全域）。
"""

from __future__ import annotations

from ..state import SpiderForgeState
from .base import Node


class SandboxTest(Node):
    """離線閘門過了才走這關；真的連站，受 timeout 與網域白名單約束。"""

    def __init__(self, *, impl=None):
        self._impl = impl

    def __call__(self, state: SpiderForgeState) -> dict:
        impl = self._impl
        if impl is None:  # late-bind：讓既有測試的 monkeypatch 仍然生效
            from ..shared.sandbox import sandbox_test as impl
        return impl(state)

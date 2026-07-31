"""repair_code 節點：依診斷回饋重產候選程式碼。

同一塊積木、兩種設定就是兩個節點——這正是 class 化想要的形狀：

    repair_code = RepairCode()            # 第一輪：主要產碼供應商
    repair_code_kimi = RepairCode(kimi=True)   # 最後一輪：換另一家再試

實作仍在 ``shared.repair``（provider 由 config 的 REPAIR_PROVIDER /
FINAL_REPAIR_PROVIDER 決定）。傳 ``impl`` 可整支替換。
"""

from __future__ import annotations

from ..state import SpiderForgeState
from .base import Node


class RepairCode(Node):
    def __init__(self, *, kimi: bool = False, impl=None):
        self._kimi = kimi
        self._impl = impl

    def __call__(self, state: SpiderForgeState) -> dict:
        impl = self._impl
        if impl is None:  # late-bind：讓既有測試的 monkeypatch 仍然生效
            from ..shared import repair as repair_module

            impl = repair_module.repair_code_kimi if self._kimi else repair_module.repair_code
        return impl(state)

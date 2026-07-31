"""所有 pipeline 節點的共同基類。

節點 = 收 state、回 state 更新的一個步驟。class 形式讓「設定/依賴」在 __init__ 注入、
「邏輯」在 __call__ —— 就是 pytorch nn.Module 的形狀。有 __call__ 的 instance 可以
直接當函式呼叫，所以能塞進 LangGraph 的 add_node()，骨架不用改。

節點彼此不互相 import，只由 pipeline.py 組裝（見 tests/test_architecture.py 的約束）。
"""

from __future__ import annotations

from ..state import SpiderForgeState


class Node:
    """節點基類：子類覆寫 ``__call__(state) -> dict``（回傳部分 state 更新）。"""

    def __call__(self, state: SpiderForgeState) -> dict:  # pragma: no cover - 抽象
        raise NotImplementedError(
            f"{type(self).__name__} 必須實作 __call__(state) -> dict"
        )

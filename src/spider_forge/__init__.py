"""Spider Forge 積木庫。

**這個套件不含管線**：管線（把節點拼成流程）在 repo 根的 `pipelines/`，
就像 torch 不含你的 train.py。這裡只提供積木與契約。

    from spider_forge.nodes import Recon, ValidateOutput
    from spider_forge.state import ForgeInput, ForgeOutput, forge_result
"""

from .state import ForgeInput, ForgeInternal, ForgeOutput, SpiderForgeState, forge_result

__all__ = [
    "ForgeInput",      # 對外輸入契約：呼叫端該給什麼
    "ForgeInternal",   # 節點間的中間態
    "ForgeOutput",     # 對外產出契約：一次執行交出什麼
    "SpiderForgeState",
    "forge_result",
]

"""Spider Forge 對外介面。"""

from .pipeline import build_pipeline, forge_spider
from .state import ForgeInput, ForgeOutput, forge_result

__all__ = [
    "build_pipeline",
    "forge_spider",
    "forge_result",
    "ForgeInput",   # 對外輸入契約：呼叫端該給什麼
    "ForgeOutput",  # 對外產出契約：一次執行交出什麼
]

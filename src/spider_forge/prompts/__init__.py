"""每個「呼叫 LLM 的節點」一個 prompt 檔。

目前：generate.py（產碼指令 + 硬性契約）。
strategy/diagnose/topic 的指令文字仍內嵌在各自模組，待後續與領域抽離（階段6）一起搬入。
"""

from .generate import CODE_SYSTEM, SPIDER_CONTRACT

__all__ = ["CODE_SYSTEM", "SPIDER_CONTRACT"]

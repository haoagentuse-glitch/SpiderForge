"""節點積木庫：每個 pipeline 節點一個 class（__init__ 注入設定、__call__ 執行）。

加新節點 = 這裡多一個檔 + 在 pipeline.py 拼裝一行。節點彼此不互相 import，只由
pipeline.py 組裝。目前 class 化：Recon（其餘節點仍是 stages/ 的函式，階段5 逐一搬入）。
"""

from .base import Node
from .recon import Recon

__all__ = ["Node", "Recon"]

"""測試一律離線，而且不碰真實產物目錄。

兩件事都必須在**任何 spider_forge 模組被 import 之前**做完——config 在 import
當下就把路徑算好了，conftest 是唯一夠早的位置。

1. 不送 trace：即使本機 `.env` 設了 `PHOENIX_COLLECTOR_ENDPOINT`，Phoenix 沒開時
   背景 exporter 會重試到逾時，把測試輸出洗版又拖慢。
   （`load_env()` 用 setdefault，先佔位就等於關掉。）
2. 產物寫到暫存目錄：測試會呼叫 `generate_spider` / `repair_code`，它們現在
   產出當下就落檔——不隔離的話跑一次測試就在 `runtime/artifacts/candidates/`
   留下垃圾。
"""

from __future__ import annotations

import os
import tempfile

os.environ["PHOENIX_COLLECTOR_ENDPOINT"] = ""
os.environ["SPIDERFORGE_DATA_DIR"] = tempfile.mkdtemp(prefix="spiderforge-tests-")

"""測試一律離線。

即使本機 `.env` 設了 `PHOENIX_COLLECTOR_ENDPOINT`，測試也不該送 trace——
否則 Phoenix 沒開時，背景 exporter 會重試到逾時，把測試輸出洗版又拖慢。
（`load_env()` 用 setdefault，所以這裡先佔位就等於關掉。）
"""

from __future__ import annotations

import os

os.environ["PHOENIX_COLLECTOR_ENDPOINT"] = ""

"""集中的 .env 載入：整個套件的環境變數初始化只在這裡定義。

沿用原專案決定「不引入 python-dotenv 依賴」，保留原本兩份手寫 loader 的行為：
容忍 BOM、從 cwd 與本套件位置往上搜尋最近的 .env、只補未設定的變數（setdefault
不覆蓋既有環境變數）。import 本模組即自動載入一次；重複呼叫為冪等（no-op）。
"""

from __future__ import annotations

import os
from pathlib import Path

_loaded = False


def load_env(*, force: bool = False) -> None:
    """載入最近的 .env（冪等）。取代原本散在 coder / topic 的兩份 `_load_env`。"""
    global _loaded
    if _loaded and not force:
        return
    folders = [
        Path.cwd(),
        *Path.cwd().parents,
        *Path(__file__).resolve().parents,
    ]
    seen: set[Path] = set()
    for folder in folders:
        if folder in seen:
            continue
        seen.add(folder)
        env_file = folder / ".env"
        if not env_file.is_file():
            continue
        for line in env_file.read_text(encoding="utf-8-sig").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip())
        break
    _loaded = True


# import 即載入一次（乾淨初始化：呼叫端不需各自處理 .env）
load_env()

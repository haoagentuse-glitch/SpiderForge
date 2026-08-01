"""控制層請求身分與反攻防硬界線的唯一真實來源。

**做（瀏覽器等級隱身）**：真實瀏覽器 User-Agent 字串 + 相容 headers（Accept /
Accept-Language / sec-ch-ua 等），並留一條誠實痕跡 header ``X-Purpose``
（課程練習、非商業）。低速由 AutoThrottle 維持。

**只有兩條界線**（2026-08-02 收斂，其餘都是預設值不是禁令）：

1. **登入資料勿碰** —— 不做登入、不用他人憑證、不繞需要帳號的存取控制。
2. **絕對不搞癱瘓** —— 低速由 AutoThrottle 維持，並發固定 1，翻頁有上限。

其餘（單一固定 UA、不輪替代理、不做 TLS 指紋偽裝）是**這個專案目前的預設值**，
不是道德界線：UA 與語系可用 env 覆蓋，要加代理池就自己加。先前把它們寫成
「硬界線」並讓 WAF 擋一次就走死信，是自綁手腳，已解除。
"""

from __future__ import annotations

import os

# 誠實痕跡的用途說明：可用 SPIDERFORGE_REQUEST_PURPOSE 覆蓋（例如換成自己的研究用途）。
# 硬界線是「保留一條可被站方看見的誠實痕跡」，不是這串特定文字。
COURSE_PURPOSE = os.getenv(
    "SPIDERFORGE_REQUEST_PURPOSE", "academic course exercise, non-commercial"
)

# 單一固定、真實的桌面瀏覽器 UA：可用 SPIDERFORGE_USER_AGENT 換成別的**單一** UA。
# 硬界線是「單一固定、不輪替」（不是 UA 池），不是這個特定字串。
BROWSER_USER_AGENT = os.getenv(
    "SPIDERFORGE_USER_AGENT",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
)

# Accept-Language 預設 zh-TW；抓別的語系站台用 SPIDERFORGE_ACCEPT_LANGUAGE 覆蓋。
_ACCEPT_LANGUAGE = os.getenv(
    "SPIDERFORGE_ACCEPT_LANGUAGE", "zh-TW,zh;q=0.9,en-US;q=0.8,en;q=0.7"
)

# 與上述 UA 相容的標準瀏覽器 headers（含 X-Purpose 誠實痕跡）。
BROWSER_HEADERS: dict[str, str] = {
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,"
        "application/json;q=0.8,*/*;q=0.7"
    ),
    "Accept-Language": _ACCEPT_LANGUAGE,
    "sec-ch-ua": '"Google Chrome";v="131", "Chromium";v="131", "Not_A Brand";v="24"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
    "Upgrade-Insecure-Requests": "1",
    "X-Purpose": COURSE_PURPOSE,
}


def browser_request_headers() -> dict[str, str]:
    """給 requests / Scrapy 用的完整請求 headers（UA + 相容 headers + X-Purpose）。"""
    return {"User-Agent": BROWSER_USER_AGENT, **BROWSER_HEADERS}

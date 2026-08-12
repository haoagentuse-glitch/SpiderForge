"""請求身分與反攻防硬界線。

驗收重點：
- 瀏覽器等級 UA + 相容 headers，而且**只有標準 header**（做的部分）。
- recon 的 _fetch_sample 以瀏覽器身分抓取（§3.1 校準）。
- 硬界線：只有單一固定 UA（非 UA 池），不做代理/指紋/CAPTCHA。

跑法（從 workspace/backend/）：
    python -m spider_forge.tests.test_request_identity
"""

from __future__ import annotations

from spider_forge.shared import request_identity
from spider_forge.prompts.generate import SPIDER_CONTRACT

def t_browser_headers_carry_no_custom_markers():
    """只送標準瀏覽器 header——自訂 header 本身就是 CDN 的機器人特徵。

    實測（2026-08-12）：帶著 ``X-Purpose`` 抓鉅亨網只回 70 個連結，不帶回 302 個。
    它不在專案的兩條界線裡，卻讓偵查**安靜地**看到一個殘缺的網站——不會報錯，
    只會讓後面每一關都在錯的素材上做判斷。誠實體現在低速與並發 1，不是這個字串。
    """
    headers = request_identity.browser_request_headers()
    custom = [name for name in headers if name.lower().startswith("x-")]
    ok = (
        "Chrome/" in headers["User-Agent"]
        and headers["User-Agent"].startswith("Mozilla/5.0")
        and headers["Accept-Language"].startswith("zh-TW")
        and "sec-ch-ua" in headers
        and not custom
    )
    return ok, f"ua={headers['User-Agent'][:40]}... custom_headers={custom}"


def t_single_fixed_ua_not_a_pool():
    """硬界線：只有一個固定 UA 字串，不做 UA 池/輪替。"""
    ok = (
        isinstance(request_identity.BROWSER_USER_AGENT, str)
        and "\n" not in request_identity.BROWSER_USER_AGENT
    )
    # 請求身分模組不應暴露任何「UA 清單/池」樣式的容器
    pool_like = [
        name
        for name in dir(request_identity)
        if not name.startswith("_")
        and isinstance(
            getattr(request_identity, name), (list, tuple, set)
        )
        and any(
            "mozilla" in str(x).lower()
            for x in getattr(request_identity, name)
        )
    ]
    return ok and not pool_like, f"single_ua={ok} pool_like={pool_like}"


def t_fetch_sample_sends_browser_identity():
    import spider_forge.shared.evidence as evidence
    import requests

    captured = {}

    class FakeResp:
        status_code = 200
        headers = {"content-type": "text/html; charset=utf-8"}
        text = "<html><head></head><body>ok</body></html>"
        url = "https://example.com/news"
        history: list = []

    def fake_get(url, **kwargs):
        captured["headers"] = kwargs.get("headers") or {}
        return FakeResp()

    original = requests.get
    requests.get = fake_get
    try:
        evidence._fetch_sample("https://example.com/news")
    finally:
        requests.get = original
    sent = captured.get("headers", {})
    ok = (
        "Chrome/" in sent.get("User-Agent", "")
        and sent.get("Accept-Language", "").startswith("zh-TW")
        and not [name for name in sent if name.lower().startswith("x-")]
    )
    return ok, f"ua={sent.get('User-Agent', '')[:30]} headers={sorted(sent)}"


def t_generator_contract_keeps_only_the_login_boundary():
    """契約只保留「登入資料勿碰」這條界線，其餘不設限（2026-08-02 收斂）。

    先前寫「付費牆 / CAPTCHA / 登入牆一律不繞」，把三種性質不同的東西綁在一起：
    登入是存取控制（該守），付費牆與 CAPTCHA 則多半只是「被擋住」——寫成禁令等於
    被擋一次就放棄，是自綁手腳。同時契約必須教捲動，否則 JS 載入的站全部只抓第 1 頁。
    """
    contract = SPIDER_CONTRACT
    return (
        # 保留的界線
        "登入" in contract
        and "不用他人憑證" in contract
        # 拿掉的自綁手腳
        and "一律不繞" not in contract
        # 新增的能力：捲動
        and "playwright_page_methods" in contract
        and "scrollTo" in contract
        # 原本就不該有的（robots 不參與判斷）
        and "ROBOTSTXT_OBEY" not in contract
    ), (
        f"登入界線={'不用他人憑證' in contract} "
        f"殘留禁令={'一律不繞' in contract} "
        f"教了捲動={'playwright_page_methods' in contract}"
    )


TESTS = [
    t_browser_headers_carry_no_custom_markers,
    t_single_fixed_ua_not_a_pool,
    t_fetch_sample_sends_browser_identity,
    t_generator_contract_keeps_only_the_login_boundary,
]


def main() -> int:
    failed = 0
    for test in TESTS:
        try:
            ok, detail = test()
        except Exception as exc:  # noqa: BLE001
            ok, detail = False, f"EXCEPTION {exc}"
        print(f"[{'PASS' if ok else 'FAIL'}] {test.__name__}: {detail}")
        failed += not ok
    print(f"\n{len(TESTS) - failed}/{len(TESTS)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())

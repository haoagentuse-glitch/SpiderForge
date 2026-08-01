"""起飛前檢查：試跑會連真網站、花 API 額度，先把環境問題一次找出來。

刻意**不驗證金鑰有效性**（那要真的呼叫 API = 花錢），只檢查「該有的東西在不在」。
也不連任何外部網站，只碰 localhost 服務與本機檔案。
"""

from __future__ import annotations

import importlib.util
import os
from dataclasses import dataclass
from typing import Callable

from spider_forge.clients.env import load_env
from spider_forge.config import (
    FINAL_REPAIR_PROVIDER,
    GENERATION_PROVIDER,
    REPAIR_PROVIDER,
    SITE_QUEUE_PATH,
    ensure_runtime_layout,
    location_map,
)
from spider_forge.clients.registry import get_provider

OK, FAIL, WARN, SKIP = "OK", "FAIL", "WARN", "--"


@dataclass
class Check:
    name: str
    status: str
    detail: str

    @property
    def blocking(self) -> bool:
        return self.status == FAIL


def _providers_in_use(profile: dict) -> dict[str, list[str]]:
    """這次執行實際會用到哪些 provider（依設定，不是寫死三個）。

    同一個 provider 可能身兼數職（預設 deepseek 同時負責產碼與第一輪修復），
    所以用途要收成 list——用 dict 直接覆蓋會讓「產碼」這一項憑空消失。
    """
    used: dict[str, list[str]] = {}
    for provider, purpose in (
        (GENERATION_PROVIDER, "產碼"),
        (REPAIR_PROVIDER, "第一輪修復"),
        (FINAL_REPAIR_PROVIDER, "最後一輪修復"),
    ):
        used.setdefault(provider, []).append(purpose)

    topic = profile.get("topic_gate") or {}
    if topic.get("mode") in {"shadow", "enforce"} and topic.get("provider", "gemini") == "gemini":
        used.setdefault("gemini", []).append(f"主題閘門（mode={topic['mode']}）")
    if (profile.get("block_gate") or {}).get("provider") == "gemini":
        used.setdefault("gemini", []).append("內容真偽確認")
    return used


def _check_keys(profile: dict) -> list[Check]:
    checks: list[Check] = []
    for provider, purposes in _providers_in_use(profile).items():
        try:
            spec = get_provider(provider)
        except ValueError as exc:
            checks.append(Check(f"provider:{provider}", FAIL, str(exc)))
            continue
        present = bool(os.getenv(spec.api_key_env))
        checks.append(
            Check(
                f"金鑰 {spec.api_key_env}",
                OK if present else FAIL,
                f"{'／'.join(purposes)}（{spec.name} / {spec.model}）"
                + ("" if present else " ← 未設定，這條路徑會直接失敗"),
            )
        )
    return checks


def _check_playwright() -> Check:
    """recon 節點用 Playwright 開真瀏覽器；只裝 pip 套件不夠，還要下載瀏覽器。"""
    if importlib.util.find_spec("playwright") is None:
        return Check("Playwright", FAIL, "套件未安裝：uv sync")
    # 在子程序裡問路徑：sync_playwright 收尾時會從別的執行緒噴 TargetClosedError，
    # 那是它自己的雜訊，不該汙染檢查報告。
    import subprocess
    import sys

    probe = (
        "from playwright.sync_api import sync_playwright\n"
        "with sync_playwright() as p:\n"
        "    print(p.chromium.executable_path)\n"
    )
    try:
        result = subprocess.run(
            [sys.executable, "-c", probe],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )
    except subprocess.TimeoutExpired:
        return Check("Playwright chromium", FAIL, "查詢逾時")
    path = (result.stdout or "").strip().splitlines()[-1] if result.stdout.strip() else ""
    exists = bool(path) and os.path.isfile(path)
    return Check(
        "Playwright chromium",
        OK if exists else FAIL,
        path if exists else "瀏覽器未下載 → uv run playwright install chromium",
    )


def _check_scrapy() -> Check:
    if importlib.util.find_spec("scrapy") is None:
        return Check("Scrapy", FAIL, "sandbox_test 需要它：uv sync")
    import scrapy

    return Check("Scrapy", OK, f"{scrapy.__version__}（sandbox 以 runspider 執行候選）")


def _probe_http(url: str, timeout: float = 2.0) -> tuple[bool, str]:
    import requests

    try:
        response = requests.get(url, timeout=timeout)
        return True, f"HTTP {response.status_code}"
    except Exception as exc:  # noqa: BLE001
        return False, type(exc).__name__


def _check_ollama() -> Check:
    """strategy_decision / diagnose 走本機 judge 模型。"""
    from spider_forge.clients.judge import DEFAULT_MODEL, OLLAMA_URL

    reachable, detail = _probe_http(f"{OLLAMA_URL}/api/tags")
    if not reachable:
        return Check("Ollama", WARN, f"{OLLAMA_URL} 連不上（{detail}）——judge 節點會失敗")
    try:
        import requests

        tags = requests.get(f"{OLLAMA_URL}/api/tags", timeout=3).json()
        names = {m.get("name", "") for m in tags.get("models", [])}
        has_model = any(n == DEFAULT_MODEL or n.startswith(DEFAULT_MODEL) for n in names)
        return Check(
            "Ollama",
            OK if has_model else WARN,
            f"{OLLAMA_URL}｜judge 模型 {DEFAULT_MODEL} "
            + ("已就緒" if has_model else f"不在清單中：ollama pull {DEFAULT_MODEL}"),
        )
    except Exception as exc:  # noqa: BLE001
        return Check("Ollama", WARN, f"清單讀取失敗：{exc}")


def _check_phoenix() -> Check:
    endpoint = os.getenv("PHOENIX_COLLECTOR_ENDPOINT", "").strip()
    if not endpoint:
        return Check("Phoenix 追蹤", SKIP, "未設 PHOENIX_COLLECTOR_ENDPOINT（不啟用，完全無影響）")
    if importlib.util.find_spec("phoenix") is None:
        return Check("Phoenix 追蹤", WARN, "已設 endpoint 但套件未裝：uv sync --extra observability")
    base = endpoint.split("/v1/traces")[0]
    reachable, detail = _probe_http(base)
    return Check(
        "Phoenix 追蹤",
        OK if reachable else WARN,
        f"{base} {detail}"
        + ("" if reachable else " ← 服務沒起來；追蹤會靜默失敗，但流程照跑"),
    )


def _check_site_queue() -> Check:
    exists = SITE_QUEUE_PATH.is_file()
    if not exists:
        return Check("站台清單", WARN, f"{SITE_QUEUE_PATH} 不存在（只影響 batch，run --url 不受影響）")
    try:
        import yaml

        sites = yaml.safe_load(SITE_QUEUE_PATH.read_text(encoding="utf-8"))["sites"]
        names = ", ".join(s["source_prefix"] for s in sites)
        return Check("站台清單", OK, f"{len(sites)} 站：{names}")
    except Exception as exc:  # noqa: BLE001
        return Check("站台清單", FAIL, f"{SITE_QUEUE_PATH} 解析失敗：{exc}")


def _check_runtime() -> Check:
    try:
        ensure_runtime_layout()
        root = location_map()["data_root"]
        probe = os.path.join(root, ".doctor_write_probe")
        with open(probe, "w", encoding="utf-8") as handle:
            handle.write("ok")
        os.remove(probe)
        return Check("執行期目錄", OK, f"{root}（可寫）")
    except Exception as exc:  # noqa: BLE001
        return Check("執行期目錄", FAIL, f"無法寫入：{exc}")


def run_checks(profile: dict | None = None) -> list[Check]:
    load_env()
    profile = profile or {}
    checks: list[Check] = []
    checks.extend(_check_keys(profile))
    checks.append(_check_playwright())
    checks.append(_check_scrapy())
    checks.append(_check_ollama())
    checks.append(_check_phoenix())
    checks.append(_check_site_queue())
    checks.append(_check_runtime())
    return checks


def report(checks: list[Check], printer: Callable[[str], None] = print) -> int:
    """印出檢查結果；有 FAIL 回 1，全過（含 WARN）回 0。"""
    width = max(len(c.name) for c in checks)
    for check in checks:
        printer(f"  [{check.status:^4}] {check.name.ljust(width)}  {check.detail}")
    failed = [c for c in checks if c.blocking]
    warned = [c for c in checks if c.status == WARN]
    printer("")
    if failed:
        printer(f"✗ {len(failed)} 項會直接擋住試跑：" + "、".join(c.name for c in failed))
        return 1
    if warned:
        printer(f"△ 可以試跑，但 {len(warned)} 項有疑慮：" + "、".join(c.name for c in warned))
        return 0
    printer("✓ 全部就緒")
    return 0

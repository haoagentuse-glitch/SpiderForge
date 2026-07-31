"""所有 LLM provider 的設定集中處：模型、金鑰 env、base_url 的唯一真實來源。

想改用哪個模型、換 base_url、加一個新 provider —— 只動這個檔的 `_providers()`，
呼叫端（coder / topic / page）不需要改。這取代了原本散在三個檔的模型名與金鑰讀取。
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from . import env as _env  # noqa: F401  # import 即觸發 .env 載入一次


@dataclass(frozen=True)
class ProviderSpec:
    """一個 LLM provider 的完整設定。"""

    name: str
    api_key_env: str          # 金鑰的環境變數名
    model: str
    base_url: str
    api_style: str            # "openai"（/chat/completions）| "gemini"（interactions）
    default_temperature: float = 0.0


def _providers() -> dict[str, ProviderSpec]:
    """延遲求值：讓 os.getenv 反映 .env 覆蓋與測試 monkeypatch（不在 import 時凍結）。"""
    return {
        "deepseek": ProviderSpec(
            name="deepseek",
            api_key_env="DEEPSEEK_API_KEY",
            model=os.getenv("DEEPSEEK_MODEL", "deepseek-chat"),
            base_url=os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
            api_style="openai",
            default_temperature=0.0,
        ),
        "kimi": ProviderSpec(
            name="kimi",
            api_key_env="KIMI_API_KEY",
            model=os.getenv("KIMI_MODEL", "kimi-k2.7-code-highspeed"),
            base_url=os.getenv("KIMI_BASE_URL", "https://api.moonshot.ai/v1"),
            api_style="openai",
            default_temperature=1.0,  # k2.7-code 系列只接受 temperature=1
        ),
        "gemini": ProviderSpec(
            name="gemini",
            api_key_env="GEMINI_API_KEY",
            model=os.getenv("GEMINI_MODEL", "gemini-3.5-flash-lite"),
            base_url=os.getenv(
                "GEMINI_BASE_URL",
                "https://generativelanguage.googleapis.com/v1beta/interactions",
            ),
            api_style="gemini",
            default_temperature=0.0,
        ),
    }


def get_provider(name: str) -> ProviderSpec:
    """取得 provider 設定；未知名稱明確報錯。"""
    providers = _providers()
    key = name.lower()
    if key not in providers:
        raise ValueError(f"未知 provider：{name}（可用：{sorted(providers)}）")
    return providers[key]


def resolve_api_key(spec: ProviderSpec) -> str:
    """讀取金鑰，缺漏時明確報錯。

    呼叫端捕捉 LookupError 後轉成自己的例外型別；金鑰讀取只有這一條路徑。
    變數名的唯一標準是 `.env.example`。
    """
    key = os.getenv(spec.api_key_env)
    if not key:
        raise LookupError(f"{spec.name} 金鑰缺漏（env {spec.api_key_env}）")
    return key

"""prepare_request 節點：把使用者給的最小資訊正規化成流程要用的完整請求。

可注入（__init__）：
- ``default_schema``：預設抓取欄位契約。**換要抓什麼欄位就換這個**（見 schemas/outputs.py），
  使用者給的 target_schema 仍會覆蓋其上。
- ``max_retries_cap``：修復次數上限（原本寫死 2）。
"""

from __future__ import annotations

import copy
import re
from urllib.parse import urlparse

from ..schemas import DEFAULT_TARGET_SCHEMA
from ..shared.topic import normalize_config
from ..state import SpiderForgeState
from .base import Node

# 品質閘門的通用預設（站台沒指定時用）。
_DEFAULT_VALIDATION = {
    "min_content_chars": 40,
    # 歷史抓取前提：不設時效窗。原本預設 30 天會把所有歷史文章判成 date_too_old，
    # 等於歷史模式必死。要做「只收新文章」的監控場景，在站台 YAML 設回 max_age_days。
    "max_age_days": None,
    "min_valid_items": 5,
    "min_unique_ratio": 0.8,
}


def _safe_prefix(host: str) -> str:
    value = re.sub(r"[^a-z0-9]+", "_", host.lower()).strip("_")
    return (value or "generated_source")[:40]


class PrepareRequest(Node):
    """正規化請求；不要求使用者準備 selector、HAR 或 API 路徑。"""

    def __init__(self, *, default_schema: dict | None = None, max_retries_cap: int = 2):
        self._default_schema = default_schema or DEFAULT_TARGET_SCHEMA
        self._max_retries_cap = max_retries_cap

    def __call__(self, state: SpiderForgeState) -> dict:
        site_url = str(state.get("site_url") or "").strip()
        parsed = urlparse(site_url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("site_url 必須是含 http/https 的有效 URL")

        host = parsed.hostname.lower()
        explicit_prefix = bool(str(state.get("source_prefix") or "").strip())
        prefix = str(state.get("source_prefix") or "").strip() or _safe_prefix(host)
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,39}", prefix):
            raise ValueError("source_prefix 必須符合 [a-z][a-z0-9_]{0,39}")

        schema = copy.deepcopy(self._default_schema)
        supplied_schema = state.get("target_schema") or {}
        schema.update({k: v for k, v in supplied_schema.items() if k != "fields"})
        schema.setdefault("fields", {})
        schema["fields"].update(supplied_schema.get("fields") or {})

        validation = dict(state.get("validation") or {})
        validation.setdefault("allowed_domains", [host])
        for key, value in _DEFAULT_VALIDATION.items():
            validation.setdefault(key, value)
        content_contract = schema.get("fields", {}).get("content", {})
        if schema.get("source_type") == "media" and content_contract.get("max_chars"):
            validation.setdefault("max_content_chars", content_contract["max_chars"])
        topic_gate = normalize_config(
            state.get("topic_gate"),
            min_valid_items=int(validation["min_valid_items"]),
        )

        sample_urls: list[str] = []
        for value in state.get("sample_urls") or []:
            sample = str(value).strip()
            parsed_sample = urlparse(sample)
            if (
                parsed_sample.scheme in {"http", "https"}
                and parsed_sample.hostname
                and sample not in sample_urls
            ):
                sample_urls.append(sample)

        access_mode = state.get("access_mode") or "public"
        if access_mode not in {"public", "browser_session"}:
            raise ValueError("access_mode 僅允許 public 或 browser_session")
        if access_mode == "browser_session" and not state.get("access_context_ref"):
            raise ValueError("browser_session 必須提供 access_context_ref")

        requested_retries = int(state.get("max_retries", self._max_retries_cap))
        return {
            "site_url": site_url,
            "site_name": str(state.get("site_name") or host),
            "source_prefix": prefix,
            "source_prefix_explicit": explicit_prefix,
            "target_schema": schema,
            "target_schema_explicit": bool(supplied_schema),
            "sample_urls": sample_urls[:5],
            "access_mode": access_mode,
            "constraints": {
                # 歷史抓取前提：翻到第 10 頁（原本 2 頁是監控場景的設定）。
                # 沒有「抓到重複就停」的機制之前，這個上限就是唯一的煞車。
                "max_pages": 10,
                "validation_probe_items": 20,
                **(state.get("constraints") or {}),
            },
            "validation": validation,
            "validation_explicit": bool(state.get("validation")),
            "topic_gate": topic_gate,
            "max_retries": max(0, min(self._max_retries_cap, requested_retries)),
            "retry_count": 0,
            "error_signature_history": [],
            "kimi_used": False,
            "status": "request_ready",
        }

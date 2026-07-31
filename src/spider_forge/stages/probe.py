"""請求正規化與入口探測。"""

from __future__ import annotations

import copy
import json
import re
from urllib.parse import urlparse

from ..state import SpiderForgeState
from ..shared import evidence as evidence_tools
from ..shared.topic import normalize_config
from ..schemas import DEFAULT_TARGET_SCHEMA

def _safe_prefix(host: str) -> str:
    value = re.sub(r"[^a-z0-9]+", "_", host.lower()).strip("_")
    return (value or "generated_source")[:40]


def prepare_request(state: SpiderForgeState) -> dict:
    """把一般使用者會給的最小資訊正規化；不要求 selector、HAR 或 API 路徑。"""
    site_url = str(state.get("site_url") or "").strip()
    parsed = urlparse(site_url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("site_url 必須是含 http/https 的有效 URL")

    host = parsed.hostname.lower()
    explicit_prefix = bool(str(state.get("source_prefix") or "").strip())
    prefix = str(state.get("source_prefix") or "").strip() or _safe_prefix(host)
    if not re.fullmatch(r"[a-z][a-z0-9_]{0,39}", prefix):
        raise ValueError("source_prefix 必須符合 [a-z][a-z0-9_]{0,39}")

    schema = copy.deepcopy(DEFAULT_TARGET_SCHEMA)
    supplied_schema = state.get("target_schema") or {}
    schema.update({k: v for k, v in supplied_schema.items() if k != "fields"})
    schema["fields"].update(supplied_schema.get("fields") or {})

    validation = dict(state.get("validation") or {})
    validation.setdefault("allowed_domains", [host])
    validation.setdefault("min_content_chars", 40)
    content_contract = schema.get("fields", {}).get("content", {})
    if schema.get("source_type") == "media" and content_contract.get("max_chars"):
        validation.setdefault("max_content_chars", content_contract["max_chars"])
    validation.setdefault("max_age_days", 30)
    validation.setdefault("min_valid_items", 5)
    validation.setdefault("min_unique_ratio", 0.8)
    topic_gate = normalize_config(
        state.get("topic_gate"),
        min_valid_items=int(validation["min_valid_items"]),
    )

    sample_urls = []
    for value in state.get("sample_urls") or []:
        sample = str(value).strip()
        p = urlparse(sample)
        if p.scheme in {"http", "https"} and p.hostname and sample not in sample_urls:
            sample_urls.append(sample)

    access_mode = state.get("access_mode") or "public"
    if access_mode not in {"public", "browser_session"}:
        raise ValueError("access_mode 僅允許 public 或 browser_session")
    if access_mode == "browser_session" and not state.get("access_context_ref"):
        raise ValueError("browser_session 必須提供 access_context_ref")

    requested_retries = int(state.get("max_retries", 2))
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
            "max_pages": 2,
            "validation_probe_items": 20,
            **(state.get("constraints") or {}),
        },
        "validation": validation,
        "validation_explicit": bool(state.get("validation")),
        "topic_gate": topic_gate,
        "max_retries": max(0, min(2, requested_retries)),
        "retry_count": 0,
        "error_signature_history": [],
        "kimi_used": False,
        "status": "request_ready",
    }

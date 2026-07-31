"""離線重播引擎：用保存下來的 response 執行候選 spider 的 callback，不連網站。

【執行方式】在沙盒子程序中**以檔案路徑**直接跑（`python -X utf8 fixture_runner.py`），
不透過套件 import——所以本檔不得 import 任何 spider_forge 模組，只用標準函式庫 + scrapy。
這樣候選程式碼（LLM 產生、未受信任）與 Scrapy 都關在子程序裡。

【輸入】stdin 一份 JSON：``{"spider_code": <候選原始碼>, "fixture": <fixture spec>}``
fixture spec 由控制層 ``shared/fixture.py:build_fixture_spec`` 產生。

【輸出】stdout 一份 JSON（契約，控制層 ``fixture_test`` 直接 json.loads）：
``passed`` / ``errors`` / ``callback_errors`` / ``detail_request_count`` /
``parsed_item_count`` / ``items_preview``。errors 用 ``類型:細節`` 形式，讓修復節點
能直接餵給 LLM。

【邊界】離線重播只驗「callback 能不能從這份 DOM 抽出合格 item」。真正的網路行為、
throttle、瀏覽器傳輸由後續 sandbox_test 驗；``browser_required`` 在這裡不特別處理
（DOM 已經是瀏覽器渲染後的結果）。
"""

from __future__ import annotations

import json
import os
import sys
import traceback
import types
from urllib.parse import urlparse

import scrapy
from scrapy.http import HtmlResponse, Request, TextResponse, XmlResponse

try:  # scrapy >= 2.8 的「不會被呼叫」哨兵
    from scrapy.http.request import NO_CALLBACK
except ImportError:  # pragma: no cover - 舊版 scrapy
    NO_CALLBACK = None

_DEFAULT_REQUIRED_FIELDS = ("title", "url", "content", "published_at")
_CANDIDATE_MODULE = "spiderforge_candidate"


def _load_spider_class(code: str) -> type:
    """把候選原始碼 exec 進獨立 module namespace，取出唯一的 Spider 子類。"""
    module = types.ModuleType(_CANDIDATE_MODULE)
    module.__dict__["__name__"] = _CANDIDATE_MODULE
    exec(compile(code, "<candidate>", "exec"), module.__dict__)
    found = [
        obj
        for obj in module.__dict__.values()
        if isinstance(obj, type)
        and issubclass(obj, scrapy.Spider)
        and obj is not scrapy.Spider
        and getattr(obj, "__module__", "") == _CANDIDATE_MODULE
    ]
    if not found:
        raise RuntimeError("candidate_defines_no_spider_class")
    if len(found) > 1:
        names = ",".join(cls.__name__ for cls in found)
        raise RuntimeError(f"candidate_defines_multiple_spiders:{names}")
    return found[0]


def _response_class(content_type: str) -> type:
    lowered = (content_type or "").lower()
    if "json" in lowered:
        return TextResponse
    if "xml" in lowered or "rss" in lowered or "atom" in lowered:
        return XmlResponse
    return HtmlResponse


def _make_response(url: str, body: str, content_type: str, request: Request):
    return _response_class(content_type)(
        url=url or "https://fixture.invalid/",
        body=(body or "").encode("utf-8"),
        encoding="utf-8",
        request=request,
    )


def _callback_of(request: Request, spider: scrapy.Spider):
    callback = getattr(request, "callback", None)
    if callback is None or (NO_CALLBACK is not None and callback is NO_CALLBACK):
        return spider.parse
    return callback


def _drain(callback, response, request: Request, callback_errors: list[str]):
    """呼叫 callback 並收乾 generator；例外收進 callback_errors，不中斷整場重播。"""
    outputs = []
    try:
        produced = callback(response, **dict(getattr(request, "cb_kwargs", {}) or {}))
        if produced is not None:
            for item in produced:
                outputs.append(item)
    except Exception:  # noqa: BLE001 - 候選碼任何例外都是待修復的證據
        callback_errors.append(
            f"{getattr(callback, '__name__', 'callback')}:"
            f"{traceback.format_exc(limit=3).strip()[-600:]}"
        )
    return outputs


def _as_dict(item) -> dict:
    if isinstance(item, dict):
        return dict(item)
    try:
        from itemadapter import ItemAdapter

        return dict(ItemAdapter(item))
    except Exception:  # noqa: BLE001 - 退回最寬鬆解讀
        try:
            return dict(item)
        except Exception:  # noqa: BLE001
            return {"_unconvertible_item": repr(item)[:200]}


def _listing_request(spider: scrapy.Spider, url: str) -> Request:
    """優先用候選自己的 start_requests（保留它設定的 meta/callback）。"""
    starter = getattr(spider, "start_requests", None)
    if callable(starter):
        try:
            for request in starter():
                if isinstance(request, Request):
                    return request
        except Exception:  # noqa: BLE001 - start_requests 壞掉照樣重播 parse
            pass
    return Request(url or "https://fixture.invalid/", callback=spider.parse)


def _allowed_domains() -> list[str]:
    raw = os.environ.get("SPIDERFORGE_ALLOWED_DOMAINS", "")
    return [part.strip().lower() for part in raw.split(",") if part.strip()]


def _out_of_scope(url: str, domains: list[str]) -> bool:
    if not domains:
        return False
    host = (urlparse(url).hostname or "").lower()
    return not any(host == d or host.endswith("." + d) for d in domains)


def _match_request(requests: list[Request], wanted: str) -> Request | None:
    for request in requests:
        if request.url == wanted:
            return request
    stripped = wanted.rstrip("/")
    for request in requests:
        if request.url.rstrip("/") == stripped:
            return request
    return None


def run_fixture(code: str, fixture: dict) -> dict:
    """重播一份 fixture，回傳契約報告（本函式不丟例外，錯誤都進 errors）。"""
    errors: list[str] = []
    callback_errors: list[str] = []

    try:
        spider_cls = _load_spider_class(code)
        spider = spider_cls()
    except Exception:  # noqa: BLE001
        return {
            "passed": False,
            "errors": [f"candidate_load_failed:{traceback.format_exc(limit=3)[-800:]}"],
            "callback_errors": [],
            "detail_request_count": 0,
            "parsed_item_count": 0,
            "items_preview": [],
        }

    for name, expected in (fixture.get("expected_attributes") or {}).items():
        if expected in (None, ""):
            continue
        if getattr(spider, name, None) != expected:
            errors.append(
                f"class_attribute_mismatch:{name}="
                f"{getattr(spider, name, None)!r}!={expected!r}"
            )

    listing = fixture.get("listing") or {}
    listing_request = _listing_request(spider, str(listing.get("url") or ""))
    listing_response = _make_response(
        str(listing.get("url") or ""),
        str(listing.get("body") or ""),
        str(listing.get("content_type") or "text/html"),
        listing_request,
    )
    produced = _drain(
        _callback_of(listing_request, spider),
        listing_response,
        listing_request,
        callback_errors,
    )
    detail_requests = [out for out in produced if isinstance(out, Request)]
    items = [_as_dict(out) for out in produced if not isinstance(out, Request)]

    domains = _allowed_domains()
    for request in detail_requests:
        if _out_of_scope(request.url, domains):
            errors.append(f"detail_request_out_of_scope:{request.url}")

    for sample in fixture.get("detail_samples") or []:
        wanted = str(sample.get("requested_url") or "")
        matched = _match_request(detail_requests, wanted)
        if matched is None:
            errors.append(f"missing_detail_request:{wanted}")
            continue
        detail_response = _make_response(
            wanted,
            str(sample.get("body_excerpt") or ""),
            str(sample.get("content_type") or "text/html"),
            matched,
        )
        items.extend(
            _as_dict(out)
            for out in _drain(
                _callback_of(matched, spider),
                detail_response,
                matched,
                callback_errors,
            )
            if not isinstance(out, Request)
        )

    required = list(fixture.get("required_fields") or _DEFAULT_REQUIRED_FIELDS)
    min_chars = int(fixture.get("min_content_chars") or 0)
    for index, item in enumerate(items):
        for field in required:
            value = item.get(field)
            if value is None or str(value).strip() == "":
                errors.append(f"item_missing_field:{index}:{field}")
        content = str(item.get("content") or "")
        if min_chars and len(content) < min_chars:
            errors.append(f"item_content_too_short:{index}:{len(content)}<{min_chars}")

    minimum = int(fixture.get("min_listing_outputs") or 1)
    if len(items) < minimum:
        errors.append(f"insufficient_items:{len(items)}<{minimum}")

    return {
        "passed": not errors and not callback_errors,
        "errors": errors,
        "callback_errors": callback_errors,
        "detail_request_count": len(detail_requests),
        "parsed_item_count": len(items),
        "items_preview": [
            {key: str(value)[:200] for key, value in item.items()}
            for item in items[:2]
        ],
    }


def main() -> int:
    raw = sys.stdin.read()
    try:
        bundle = json.loads(raw)
        code = bundle["spider_code"]
        fixture = bundle["fixture"]
    except Exception:  # noqa: BLE001 - 輸入壞掉是呼叫端的錯，明確 fail-fast
        sys.stderr.write(f"invalid_bundle:{traceback.format_exc(limit=2)}")
        return 2
    report = run_fixture(code, fixture)
    sys.stdout.write(json.dumps(report, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

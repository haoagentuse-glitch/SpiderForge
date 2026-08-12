"""偵查層：用 Playwright 抓精簡 DOM、ARIA snapshot 與 network 請求。

模型無關——純工具。strategy_decision 節點吃它的輸出判 api / dom / hybrid。
用 sync API（standalone 偵查，不與 scrapy 的 async 下載器同進程）。

為什麼同時抓 network：新聞站內容常來自 XHR/fetch 回的 JSON——把「回 JSON 的 XHR」
挑成候選 API endpoint，走 api 策略比爬 DOM 穩定得多（docs/spec/08 §2）。
ARIA snapshot 是 Playwright MCP 同款做法，比截圖省 token（§5 R5）。

首次使用需先下載瀏覽器：`python -m playwright install chromium`。
"""

from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

from playwright.sync_api import sync_playwright

_JSON_RESOURCE = {"xhr", "fetch"}
_SAFE_RESPONSE_HEADERS = {
    "content-type",
    "content-length",
    "location",
    "cache-control",
    "etag",
    "last-modified",
}
_SAFE_REQUEST_HEADERS = {
    "accept",
    "content-type",
    "origin",
    "referer",
    "user-agent",
    "x-requested-with",
}
_SECRET_KEY = re.compile(
    r"(?:token|secret|password|passwd|authorization|cookie|session|csrf|api[_-]?key)",
    re.IGNORECASE,
)
_SANITIZE_DOM = """(root) => {
    const clone = root.cloneNode(true);
    clone.querySelectorAll(
        'script, style, svg, iframe, template, noscript, canvas'
    ).forEach(node => node.remove());
    clone.querySelectorAll('*').forEach(node => {
        for (const attr of [...node.attributes]) {
            const name = attr.name.toLowerCase();
            if (name === 'style' || name === 'nonce' || name === 'integrity'
                    || name.startsWith('on')) {
                node.removeAttribute(attr.name);
            }
        }
    });
    return clone.outerHTML;
}"""


def _redact_payload(value: Any) -> Any:
    """Redact credential-like keys before browser evidence can reach a prompt."""
    if isinstance(value, dict):
        return {
            str(key): (
                "<redacted>"
                if _SECRET_KEY.search(str(key))
                else _redact_payload(item)
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact_payload(item) for item in value[:100]]
    return value


def _safe_post_data(raw: str | None, *, max_chars: int = 2000) -> str | None:
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
        return json.dumps(
            _redact_payload(parsed),
            ensure_ascii=False,
            separators=(",", ":"),
        )[:max_chars]
    except (TypeError, ValueError):
        pairs = parse_qsl(raw, keep_blank_values=True)
        if pairs:
            safe = [
                (key, "<redacted>" if _SECRET_KEY.search(key) else value)
                for key, value in pairs
            ]
            return urlencode(safe)[:max_chars]
        return raw[:max_chars]


def _safe_url(raw: str) -> str:
    """遮蔽網址 query 中的憑證型參數，保留其餘重播資訊。"""
    try:
        parts = urlsplit(raw)
        query = urlencode(
            [
                (
                key,
                "<redacted>" if _SECRET_KEY.search(key) else value,
                )
                for key, value in parse_qsl(
                    parts.query, keep_blank_values=True
                )
            ]
        )
        return urlunsplit(
            (parts.scheme, parts.netloc, parts.path, query, "")
        )
    except (TypeError, ValueError):
        return raw


def _safe_request_headers(headers: dict[str, str] | None) -> dict[str, str]:
    """只保留重播需要且不含憑證的請求標頭。"""
    safe: dict[str, str] = {}
    for key, value in (headers or {}).items():
        normalized = str(key).lower()
        if (
            normalized not in _SAFE_REQUEST_HEADERS
            or _SECRET_KEY.search(normalized)
        ):
            continue
        text = str(value)
        safe[normalized] = (
            _safe_url(text)
            if normalized in {"origin", "referer"}
            else text
        )
    return safe


def _json_evidence(raw: str, *, max_chars: int = 6000) -> dict[str, Any]:
    """Return a compact body sample plus evidence that it resembles article data."""
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        return {
            "body_excerpt": raw[:max_chars],
            "body_truncated": len(raw) > max_chars,
            "json_shape": "invalid_json",
            "article_record_count": 0,
        }

    title_keys = {"title", "headline"}
    date_keys = {
        "date",
        "published",
        "published_at",
        "publishdate",
        "publish_date",
        "pubdate",
    }
    url_keys = {"url", "link", "href", "permalink"}
    identity_keys = {"id", "guid", "record_id", "source_record_id", "slug"}
    records = 0

    def walk(value: Any, depth: int = 0) -> None:
        nonlocal records
        if depth > 5 or records >= 50:
            return
        if isinstance(value, dict):
            keys = {str(key).lower() for key in value}
            has_title = bool(keys & title_keys)
            has_date = bool(keys & date_keys)
            has_url = bool(keys & url_keys)
            has_identity = bool(keys & identity_keys)
            if len(keys) <= 80 and (
                (has_title and (has_url or has_date or has_identity))
                or (has_url and has_date)
            ):
                records += 1
            for child in list(value.values())[:50]:
                walk(child, depth + 1)
        elif isinstance(value, list):
            for child in value[:50]:
                walk(child, depth + 1)

    walk(parsed)
    safe_raw = json.dumps(
        _redact_payload(parsed),
        ensure_ascii=False,
        separators=(",", ":"),
    )
    record_detection = "object_records"
    if records == 0 and isinstance(parsed, list):
        title_markers = len(
            re.findall(r'"(?:title|headline)"\s*:', safe_raw, re.IGNORECASE)
        )
        article_paths = len(
            re.findall(
                r'(?:https?://|/)(?:[^"/]+/){0,5}'
                r'(?:news|press|article|stories|publication)[^"\\]*',
                safe_raw,
                re.IGNORECASE,
            )
        )
        if title_markers and article_paths:
            records = min(title_markers, article_paths, 50)
            record_detection = "flat_dataset_proxy"
    if isinstance(parsed, dict):
        shape = "object keys=" + ",".join(list(map(str, parsed.keys()))[:20])
    elif isinstance(parsed, list):
        shape = f"array length={len(parsed)}"
    else:
        shape = type(parsed).__name__
    return {
        "body_excerpt": safe_raw[:max_chars],
        "body_truncated": len(safe_raw) > max_chars,
        "json_shape": shape,
        "article_record_count": records,
        "record_detection": record_detection if records else "none",
    }


def probe(
    url: str,
    *,
    timeout_ms: int = 30000,
    headless: bool = True,
    settle_ms: int = 9000,
    max_network: int = 200,
    max_api_bodies: int = 30,
    max_links: int = 40,
    snapshot_chars: int = 8000,
    storage_state_path: str | None = None,
    document_html: str | None = None,
) -> dict[str, Any]:
    """偵查單一 URL，回傳結構化 recon_report。

    回傳鍵：url / http_status / title / aria_snapshot(截斷) /
    aria_snapshot_truncated / network_count / api_candidates。
    """
    network: list[dict[str, Any]] = []
    response_handles: list[tuple[dict[str, Any], Any]] = []

    def _on_response(resp) -> None:
        try:
            req = resp.request
            ct = (resp.headers or {}).get("content-type", "")
            headers = {
                key.lower(): value
                for key, value in (resp.headers or {}).items()
                if key.lower() in _SAFE_RESPONSE_HEADERS
            }
            entry = {
                "method": req.method,
                "url": _safe_url(resp.url),
                "resource_type": req.resource_type,
                "status": resp.status,
                "content_type": ct,
                "is_json": "json" in ct.lower(),
                "request_headers": _safe_request_headers(req.headers),
                "response_headers": headers,
                "request_post_data": _safe_post_data(req.post_data),
            }
            network.append(entry)
            if (
                len(response_handles) < max_api_bodies
                and entry["resource_type"] in _JSON_RESOURCE
                and entry["is_json"]
                and (entry["status"] or 0) < 400
            ):
                response_handles.append((entry, resp))
        except Exception:
            pass  # 偵查盡力而為，單一請求解析失敗不該中斷整場

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        # Chromium 原生 UA 本就是真實瀏覽器，不再額外加自訂 header——
        # 非標準 header 是 CDN 的機器人特徵（見 shared/request_identity.py 的實測）。
        context = browser.new_context(
            storage_state=storage_state_path if storage_state_path else None,
        )
        page = context.new_page()
        page.on("response", _on_response)
        if document_html is not None:
            page.route(
                url,
                lambda route: route.fulfill(
                    status=200,
                    content_type="text/html; charset=utf-8",
                    body=document_html,
                ),
            )
        navigation_error = None
        resp = None
        try:
            resp = page.goto(url, timeout=timeout_ms, wait_until="domcontentloaded")
        except Exception as exc:  # preserve partial network/access evidence
            navigation_error = str(exc)[:500]
        http_status = resp.status if resp else None
        final_url = page.url
        try:
            title = page.title()
        except Exception:
            title = None
        waited_ms = 0
        while waited_ms < settle_ms:
            page.wait_for_timeout(min(1000, settle_ms - waited_ms))
            waited_ms += 1000
            if any(
                entry.get("is_json")
                and re.search(
                    r"(?:graphql|/data/\d+/chunk_\d+\.json)",
                    str(entry.get("url") or ""),
                    re.IGNORECASE,
                )
                for entry in network
            ):
                break
        try:
            aria = page.locator("body").aria_snapshot()
        except Exception as e:  # noqa: BLE001
            aria = f"<aria_snapshot failed: {e}>"
        try:
            body_text = page.locator("body").inner_text()[:2000]
        except Exception:
            body_text = ""
        # 新聞明細頁不一定有 <main>／[role=main]／#content——中央社就都沒有。
        # 舊寫法會在那個 locator 上等滿 30 秒才放棄，然後回一份**空的** DOM 與空正文；
        # 後面每一關都在空證據上做判斷（樣本驗證會說「正文 0 字」，看起來像站台的錯）。
        # 改成：短逾時、多試幾個常見容器、最後退回 body，抓到有字的就停。
        dom_capture_error = None
        main_dom = ""
        main_text = ""
        for selector in ("main, [role='main'], article, #content", "body"):
            try:
                node = page.locator(selector).first
                candidate_dom = node.evaluate(_SANITIZE_DOM, timeout=5000)
                candidate_text = node.inner_text(timeout=5000)
            except Exception as exc:  # noqa: BLE001 — 換下一個容器再試
                dom_capture_error = str(exc)[:500]
                continue
            if candidate_text.strip():
                main_dom, main_text, dom_capture_error = (
                    candidate_dom, candidate_text, None,
                )
                break
            # 容器在但沒有文字（常見於外層 shell），記著再往下退
            main_dom, main_text = candidate_dom, candidate_text
        try:
            canonical_href = page.locator('link[rel="canonical"]').first.get_attribute(
                "href"
            )
            canonical_url = (
                urljoin(final_url, canonical_href) if canonical_href else final_url
            )
        except Exception:
            canonical_url = final_url
        try:
            raw_links = page.locator("a[href]").evaluate_all(
                """(els) => els.slice(0, 400).map(a => ({
                    href: a.href,
                    text: (a.innerText || a.textContent || '').replace(/\\s+/g, ' ').trim()
                }))"""
            )
            link_samples = [
                {"url": row.get("href"), "text": (row.get("text") or "")[:300]}
                for row in raw_links
                if row.get("href") and row.get("text")
            ][:max_links]
        except Exception:
            link_samples = []

        for entry, response in response_handles:
            try:
                raw = response.body().decode("utf-8", errors="replace")
                entry.update(_json_evidence(raw))
            except Exception as exc:  # noqa: BLE001
                entry["body_error"] = str(exc)[:300]
        context.close()
        browser.close()

    network = network[:max_network]
    api_candidates = [
        {
            key: value
            for key, value in n.items()
            if key
            in {
                "method",
                "url",
                "status",
                "content_type",
                "request_headers",
                "response_headers",
                "request_post_data",
                "body_excerpt",
                "body_truncated",
                "body_error",
                "json_shape",
                "article_record_count",
                "record_detection",
            }
        }
        for n in network
        if n["resource_type"] in _JSON_RESOURCE
        and n["is_json"]
        and (n["status"] or 0) < 400
    ]
    document_chain = [
        {
            "url": n["url"],
            "status": n["status"],
            "location": n.get("response_headers", {}).get("location"),
        }
        for n in network
        if n["resource_type"] == "document"
    ]
    block_text = f"{title or ''}\n{body_text}".lower()
    soft_block = any(
        marker in block_text
        for marker in (
            "access denied",
            "forbidden",
            "captcha",
            "verify you are human",
            "cloudflare",
        )
    )
    return {
        "url": url,
        "final_url": final_url,
        "canonical_url": canonical_url,
        "http_status": http_status,
        "title": title,
        "navigation_error": navigation_error,
        "document_chain": document_chain,
        "soft_block_detected": soft_block,
        "browser_session_used": bool(storage_state_path),
        "entry_document_injected": document_html is not None,
        "aria_snapshot": aria[:snapshot_chars],
        "aria_snapshot_truncated": len(aria) > snapshot_chars,
        "dom_excerpt": main_dom[:20000],
        "dom_truncated": len(main_dom) > 20000,
        "dom_capture_error": dom_capture_error,
        "main_text_excerpt": main_text[:12000],
        "main_text_truncated": len(main_text) > 12000,
        "link_samples": link_samples,
        "network_count": len(network),
        "api_candidates": api_candidates,
    }


# 捲動探測不能沿用 probe() 的 slice(0, 400)：新載入的連結接在清單尾端，
# 前 400 個永遠是同一批導覽，於是「捲出更多」會被看成「什麼都沒發生」。
# 實測鉅亨網捲 4 次會到 1164 個連結，取樣上限必須高過這個量級。
_COLLECT_LINKS = """(els) => els.slice(0, 2000).map(a => ({
    href: a.href,
    text: (a.innerText || a.textContent || '').replace(/\\s+/g, ' ').trim()
}))"""


def probe_scroll(
    url: str,
    *,
    timeout_ms: int = 30000,
    headless: bool = True,
    rounds: int = 3,
    settle_ms: int = 3000,
    max_links: int = 400,
) -> dict[str, Any]:
    """捲到底幾次，看連結數會不會增加——無限捲動就是這樣載入下一頁的。

    **偵測不等於可用**（跟 verify_pagination 同一個道理）：頁面有捲動監聽器不代表
    真的會載入更多，很多站捲到底只是把 footer 露出來。唯一算數的證據是
    **連結數真的變多**，所以這裡只回一件事實——每一輪捲完各有幾個連結。

    捲不動就早退：連續一輪沒有新連結就停，不把時間花在沒有無限捲動的站上。
    """
    growth: list[int] = []
    link_samples: list[dict[str, str]] = []
    navigation_error = None

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=headless)
        context = browser.new_context()
        page = context.new_page()
        try:
            page.goto(url, timeout=timeout_ms, wait_until="domcontentloaded")
        except Exception as exc:  # noqa: BLE001 — 捲動探測失敗只代表這一階不可用
            navigation_error = str(exc)[:500]

        if not navigation_error:
            seen: set[str] = set()

            def collect() -> int:
                try:
                    rows = page.locator("a[href]").evaluate_all(_COLLECT_LINKS)
                except Exception:  # noqa: BLE001
                    return len(seen)
                for row in rows:
                    href = row.get("href")
                    if href and href not in seen:
                        seen.add(href)
                        link_samples.append(
                            {"url": href, "text": (row.get("text") or "")[:300]}
                        )
                return len(seen)

            page.wait_for_timeout(settle_ms)
            growth.append(collect())
            for _ in range(rounds):
                try:
                    page.evaluate("() => window.scrollTo(0, document.body.scrollHeight)")
                    # 有些站掛的是鍵盤/捲動事件而不是 scroll 位置監聽，補一下 End
                    page.keyboard.press("End")
                except Exception:  # noqa: BLE001
                    break
                page.wait_for_timeout(settle_ms)
                total = collect()
                growth.append(total)
                if total == growth[-2]:
                    break   # 這一輪沒有新連結：捲不出東西，不再浪費時間

        context.close()
        browser.close()

    return {
        "url": url,
        "link_samples": link_samples[:max_links],
        "links_after_each_round": growth,
        "rounds_scrolled": max(0, len(growth) - 1),
        "loaded_more": bool(len(growth) > 1 and growth[-1] > growth[0]),
        "navigation_error": navigation_error,
    }


if __name__ == "__main__":
    import json
    import sys

    target = sys.argv[1] if len(sys.argv) > 1 else "https://news.cnyes.com/news/cat/tw_stock_news"
    report = probe(target)
    preview = {**report, "aria_snapshot": report["aria_snapshot"][:400] + " ...(截斷)"}
    print(json.dumps(preview, ensure_ascii=False, indent=2))

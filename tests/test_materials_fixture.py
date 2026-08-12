"""生成材料精簡與正式離線樣本閘門測試。"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from pipelines import pipeline
from spider_forge.shared import repair
from spider_forge.shared.fixture import build_fixture_spec
from spider_forge.shared.materials import (
    compile_generation_materials,
)
from spider_forge.shared.sandbox import run_fixture_candidate


def t_material_compiler_removes_dom_noise_and_unselected_sources():
    pack = {
        "replay_exchange": {
            "request": {"url": "https://example.com/feed/selected"},
            "response": {
                "content_type": "application/rss+xml",
                "body_excerpt": "<rss>" + ("selected " * 2000) + "</rss>",
            },
        },
        "api_sample": {
            "url": "https://example.com/feed/selected",
            "structured_format": "rss_or_atom",
        },
        "api_context_samples": [
            {
                "url": "https://example.com/feed/unselected",
                "body_excerpt": "UNSELECTED-SOURCE",
            }
        ],
        "dom_samples": [
            {
                "requested_url": "https://example.com/news/1",
                "body_excerpt": (
                    "<html><head><style>STYLE-NOISE</style>"
                    "<script>SCRIPT-NOISE</script></head><body>"
                    '<article class="story" data-tracking="REMOVE-ME">'
                    "<h1>Article title</h1><p>Useful body</p>"
                    "</article></body></html>"
                ),
            }
        ],
    }
    materials = compile_generation_materials(pack)
    serialized = json.dumps(materials, ensure_ascii=False)
    replay_body = materials["replay_exchange"]["response"][
        "body_excerpt"
    ]
    ok = (
        "SCRIPT-NOISE" not in serialized
        and "STYLE-NOISE" not in serialized
        and "REMOVE-ME" not in serialized
        and "UNSELECTED-SOURCE" not in serialized
        and "Article title" in serialized
        and 'class=\\"story\\"' in serialized
        and len(replay_body) <= 4500
        and materials["material_budget"][
            "unselected_feed_candidates_included"
        ]
        == 0
    )
    return ok, (
        f"chars={materials['material_budget']['serialized_chars']} "
        f"replay={len(replay_body)}"
    )


_GOOD_CANDIDATE = '''
import scrapy

class ArticleItem(scrapy.Item):
    title = scrapy.Field()
    url = scrapy.Field()
    content = scrapy.Field()
    published_at = scrapy.Field()
    source_record_id = scrapy.Field()

class ExampleSpider(scrapy.Spider):
    name = "example_com"
    source_prefix = "example_com"
    source = "Example"
    source_type = "media"
    content_scope = "summary_only"
    allowed_domains = ["example.com"]

    def start_requests(self):
        yield scrapy.Request("https://example.com/news", callback=self.parse)

    def parse(self, response):
        for row in response.css("li.story"):
            href = row.css("a::attr(href)").get()
            published = row.css("time::attr(datetime)").get()
            yield response.follow(
                href,
                callback=self.parse_detail,
                meta={"published_at": published},
            )

    def parse_detail(self, response):
        item = ArticleItem()
        item["title"] = response.css("h1::text").get()
        item["url"] = response.url
        item["content"] = " ".join(response.css("div.body p::text").getall())
        item["published_at"] = response.meta["published_at"]
        item["source_record_id"] = response.url.rsplit("/", 1)[-1]
        yield item
'''


def _fixture() -> dict:
    return {
        "listing": {
            "url": "https://example.com/news",
            "body": (
                "<ul>"
                '<li class="story"><a href="/news/one">One</a>'
                '<time datetime="2026-07-29T09:00:00+00:00"></time></li>'
                '<li class="story"><a href="/news/two">Two</a>'
                '<time datetime="2026-07-29T10:00:00+00:00"></time></li>'
                "</ul>"
            ),
            "content_type": "text/html",
        },
        "detail_samples": [
            {
                "requested_url": "https://example.com/news/one",
                "body_excerpt": (
                    "<main><h1>First policy update</h1><div class=\"body\">"
                    "<p>The central bank published a detailed market policy "
                    "update with verified figures and implementation dates.</p>"
                    "</div></main>"
                ),
            },
            {
                "requested_url": "https://example.com/news/two",
                "body_excerpt": (
                    "<main><h1>Second policy update</h1><div class=\"body\">"
                    "<p>The authority released another detailed financial "
                    "policy report with sufficient factual article content.</p>"
                    "</div></main>"
                ),
            },
        ],
        "browser_required": False,
        "min_listing_outputs": 2,
        "min_content_chars": 40,
        "expected_attributes": {
            "name": "example_com",
            "source_prefix": "example_com",
            "source": "Example",
            "source_type": "media",
            "content_scope": "summary_only",
        },
    }


def t_fixture_runner_executes_candidate_in_subprocess():
    """內建 fixture runner 子程序能重播候選（階段6：不再依賴外部 crawler_runtime）。"""
    with tempfile.TemporaryDirectory() as temp_dir:
        candidate = Path(temp_dir) / "candidate.py"
        candidate.write_text(_GOOD_CANDIDATE, encoding="utf-8")
        execution = run_fixture_candidate(
            str(candidate),
            _fixture(),
            allowed_domains=["example.com"],
        )
    result = json.loads(execution.stdout)
    ok = (
        execution.ok
        and result["passed"] is True
        and result["detail_request_count"] == 2
        and result["parsed_item_count"] == 2
    )
    return ok, f"exit={execution.exit_code} result={result}"


def t_pure_api_fixture_does_not_require_html_detail_callbacks():
    fixture = build_fixture_spec(
        {
            "site_url": "https://example.com/api/news",
            "site_name": "Example",
            "source_prefix": "example_com",
            "strategy": "api",
            "target_schema": {
                "source_type": "media",
                "content_scope": "summary_only",
            },
            "validation": {"min_valid_items": 5},
            "evidence_pack": {
                "strategy": {"strategy": "api"},
                "api_sample": {"article_record_count": 8},
                "replay_exchange": {
                    "request": {
                        "url": "https://example.com/api/news"
                    },
                    "response": {
                        "content_type": "application/json",
                        "body_excerpt": '{"items":[]}',
                    },
                },
                "dom_samples": [
                    {
                        "requested_url": "https://example.com/news/1",
                        "body_excerpt": "<article>unused</article>",
                    }
                ],
            },
        }
    )
    ok = (
        fixture["detail_samples"] == []
        and fixture["min_listing_outputs"] == 5
    )
    return ok, (
        f"details={len(fixture['detail_samples'])} "
        f"minimum={fixture['min_listing_outputs']}"
    )


def t_fixture_failure_is_diagnosed_without_judge_call():
    result = repair.diagnose_failure(
        {
            "generation_preflight": {"passed": True, "errors": []},
            "fixture_result": {
                "passed": False,
                "errors": ["missing_detail_request:https://example.com/one"],
                "callback_errors": [],
                "fixture_source": "browser_dom",
            },
            "retry_count": 0,
            "error_signature_history": [],
        }
    )
    ok = (
        result["failure_class"] == "selector_schema"
        and result["retry_count"] == 1
        and result["diagnosis"]["error_signature"]
        == "fixture_gate_failed"
    )
    return ok, f"diagnosis={result['diagnosis']}"


def t_graph_routes_preflight_and_fixture_before_live_crawl():
    preflight_pass = pipeline.route_after_preflight(
        {"generation_preflight": {"passed": True}}
    )
    preflight_fail = pipeline.route_after_preflight(
        {"generation_preflight": {"passed": False}}
    )
    fixture_pass = pipeline.route_after_fixture(
        {"fixture_result": {"passed": True}}
    )
    fixture_fail = pipeline.route_after_fixture(
        {"fixture_result": {"passed": False}}
    )
    ok = (
        preflight_pass,
        preflight_fail,
        fixture_pass,
        fixture_fail,
    ) == (
        "fixture_test",
        "diagnose_failure",
        "sandbox_test",
        "diagnose_failure",
    )
    return ok, (
        f"routes={(preflight_pass, preflight_fail, fixture_pass, fixture_fail)}"
    )


def t_generated_code_is_persisted_before_the_gates_run():
    """產碼當下就落檔，不是等 fixture_test。

    踩過的坑：BBC 那次卡在 generation_preflight，候選碼因此從未落檔——最需要
    看程式碼的時候（產碼失敗）反而只剩三個錯誤代碼，得重跑一次才拿得到。
    """
    import tempfile
    from spider_forge.shared import generation

    original = generation._safe_generate
    generation._safe_generate = lambda prompt, provider: (_GOOD_CANDIDATE, None)
    try:
        with tempfile.TemporaryDirectory() as temp_dir:
            from spider_forge.output import artifacts

            original_dir = artifacts._CANDIDATE_DIR
            artifacts._CANDIDATE_DIR = Path(temp_dir)
            try:
                update = generation.generate_spider({
                    "run_id": "persist-test",
                    "source_prefix": "example_com",
                    "site_name": "Example",
                    "target_schema": {"fields": {}, "source_type": "media"},
                    "evidence_pack": {},
                })
                path = Path(update.get("candidate_path", ""))
                ok = bool(update.get("candidate_path")) and path.is_file()
                same = path.read_text(encoding="utf-8") == _GOOD_CANDIDATE if ok else False
            finally:
                artifacts._CANDIDATE_DIR = original_dir
    finally:
        generation._safe_generate = original
    return ok and same, f"candidate_path={update.get('candidate_path')} 內容一致={same}"


def t_fixture_replays_the_document_the_candidate_will_actually_get():
    """重播用的文件要跟候選實際會拿到的同一種，否則是拿 A 的頁面測 B 的碼。

    MoneyDJ 實測：偵查子迴圈驗過的是純 HTTP 的頁面連結、requirements 也沒有
    browser_transport，產碼是照純 HTTP 的 HTML 寫的；但 fixture 不分青紅皂白優先用
    瀏覽器的 dom_excerpt（只取 main 容器、還被消毒與截斷過），selector 當然找不到東西，
    於是回報 insufficient_items 並歸成「selector 寫錯」——兩輪修復都在修一支沒問題的爬蟲。
    """
    from spider_forge.shared.fixture import _listing_fixture

    base = {
        "site_url": "https://example.com/news",
        "recon_report": {
            "final_url": "https://example.com/news",
            "dom_excerpt": "<main>瀏覽器渲染後的 main</main>",
            "http_entry_sample": {"body_excerpt": "<html>純 HTTP 的原始 HTML</html>"},
        },
    }
    plain = _listing_fixture({
        **base,
        "evidence_pack": {"requirements": [],
                          "entry_observation": {"html_excerpt": "<html>純 HTTP 的原始 HTML</html>"}},
    })
    browser = _listing_fixture({
        **base,
        "evidence_pack": {"requirements": ["browser_transport"],
                          "entry_observation": {"html_excerpt": "<html>純 HTTP 的原始 HTML</html>"}},
    })
    return (
        plain["capture_source"] == "entry_observation"
        and "純 HTTP" in plain["body"]
        and browser["capture_source"] == "browser_dom"
        and "瀏覽器渲染" in browser["body"]
    ), f"plain={plain['capture_source']} browser={browser['capture_source']}"


def t_fixture_only_demands_details_the_listing_actually_links_to():
    """離線重播不能要求候選對「列表頁上根本沒有的網址」產出 request。

    BBC 實測：站台設定的兩個 sample_urls 是十天前的文章，今天的商業版沒有連到它們。
    舊行為把 sample_urls 排在最前面又只取前兩份，於是 fixture 要求一件不可能的事——
    任何正確的爬蟲都過不了，兩輪修復全部白花，最後還被歸成 selector 寫錯。
    """
    from spider_forge.shared.fixture import build_fixture_spec

    listing_html = (
        '<html><body><a href="/news/articles/on-page-1">一</a>'
        '<a href="/news/articles/on-page-2">二</a></body></html>'
    )
    state = {
        "site_url": "https://example.com/news",
        "source_prefix": "example",
        "site_name": "Example",
        "strategy": "dom",
        "target_schema": {"fields": {"title": {"required": True}}},
        "validation": {"min_valid_items": 2},
        "recon_report": {"dom_excerpt": listing_html, "final_url": "https://example.com/news"},
        "evidence_pack": {
            "dom_samples": [
                # 使用者給的舊樣本排在最前面，但列表頁沒有連到它
                {"requested_url": "https://example.com/news/articles/stale-from-last-week"},
                {"requested_url": "https://example.com/news/articles/on-page-1"},
                {"requested_url": "https://example.com/news/articles/on-page-2"},
            ],
        },
    }
    wanted = [
        row["requested_url"] for row in build_fixture_spec(state)["detail_samples"]
    ]

    # 一份都連不到時仍照原樣送出：至少還能驗明細 callback 的抽取邏輯
    unreachable = dict(state)
    unreachable["evidence_pack"] = {
        "dom_samples": [{"requested_url": "https://example.com/news/articles/nowhere"}]
    }
    fallback = [
        row["requested_url"] for row in build_fixture_spec(unreachable)["detail_samples"]
    ]

    return (
        wanted == [
            "https://example.com/news/articles/on-page-1",
            "https://example.com/news/articles/on-page-2",
        ]
        and fallback == ["https://example.com/news/articles/nowhere"]
    ), f"wanted={wanted} fallback={fallback}"


TESTS = [
    t_fixture_replays_the_document_the_candidate_will_actually_get,
    t_fixture_only_demands_details_the_listing_actually_links_to,
    t_material_compiler_removes_dom_noise_and_unselected_sources,
    t_fixture_runner_executes_candidate_in_subprocess,
    t_generated_code_is_persisted_before_the_gates_run,
    t_pure_api_fixture_does_not_require_html_detail_callbacks,
    t_fixture_failure_is_diagnosed_without_judge_call,
    t_graph_routes_preflight_and_fixture_before_live_crawl,
]

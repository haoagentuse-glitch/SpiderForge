"""Spider Forge 的唯一流程組裝入口。"""

from __future__ import annotations

import uuid
from typing import Any

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph

from spider_forge.nodes.block_gate import ContentBlockGate
from spider_forge.nodes.diagnose import DiagnoseFailure
from spider_forge.nodes.discover_links import DiscoverArticleLinks
from spider_forge.nodes.escalate import EscalateHuman
from spider_forge.nodes.evidence import CollectEvidence
from spider_forge.nodes.fetch_strategy import SelectFetchStrategy
from spider_forge.nodes.fixture import FixtureTest
from spider_forge.nodes.generate import GenerateSpider
from spider_forge.nodes.pagination import VerifyPagination
from spider_forge.nodes.persist import PersistSpider
from spider_forge.nodes.prepare_request import PrepareRequest
from spider_forge.nodes.preflight import GenerationPreflight
from spider_forge.nodes.recon import Recon
from spider_forge.nodes.repair import RepairCode
from spider_forge.nodes.sandbox import SandboxTest
from spider_forge.nodes.strategy import StrategyDecision
from spider_forge.nodes.topic_gate import TopicGate
from spider_forge.nodes.triage import FeasibilityTriage
from spider_forge.nodes.validate import ValidateOutput
from spider_forge.nodes.verify_samples import VerifySamples
from spider_forge.state import (
    KILL_FAILURE_CLASSES,
    ForgeInput,
    SpiderForgeState,
    forge_result,
    normalize_failure_class,
)

_PROVIDER_RETRY_MAX = 2

# LangGraph 的步數上限。**加節點就要重算這個數字**：超過上限拿到的是
# GraphRecursionError——不是死信、沒有診斷、什麼都沒留，最需要證據的時候反而什麼都沒有。
#
# 最壞路徑（每一項都用滿）：
#   前置 4（prepare/recon/triage/strategy）
# + 偵查子迴圈 16（4 種抓法 × 4 個節點）
# + 編材料與產碼 2
# + 產出閘門 6
# + 修復迴圈 4 輪 × 8（診斷 + 修碼 + 再過一次六道閘門）＝ 32
# + 死信 1
#   ＝ 61 步。原本設 60，正好差一步——偵查子迴圈上線時撞破的就是這裡。
RECURSION_LIMIT = 100

# ── 積木拼裝：每個節點在這裡實例化一次，設定寫在建構子（= pytorch 的 __init__）──
# 這些 module-level 名字同時是「可替換點」：測試或使用者把 pipeline.sandbox_test 換掉，
# build_pipeline() 讀 global 就會拿到替換後的版本。
prepare_request = PrepareRequest()
recon = Recon()
feasibility_triage = FeasibilityTriage()
strategy_decision = StrategyDecision()
select_fetch_strategy = SelectFetchStrategy()
discover_links = DiscoverArticleLinks()
verify_samples = VerifySamples()
verify_pagination = VerifyPagination()
collect_evidence = CollectEvidence()
generate_spider = GenerateSpider()
preflight_generated_code = GenerationPreflight()
fixture_test = FixtureTest()
sandbox_test = SandboxTest()
content_block_gate = ContentBlockGate()
validate_output = ValidateOutput()
apply_topic_gate = TopicGate()
diagnose_failure = DiagnoseFailure()
repair_code = RepairCode()
repair_code_kimi = RepairCode(kimi=True)   # 同一塊積木、換一個設定
persist_spider = PersistSpider()
escalate_human = EscalateHuman()


def route_after_triage(state: SpiderForgeState) -> str:
    feasibility_class = str((state.get("feasibility") or {}).get("class") or "")
    return "escalate_human" if feasibility_class.startswith("KILL_") else "strategy_decision"


def route_after_fetch_strategy(state: SpiderForgeState) -> str:
    """還有沒試過的抓法就繼續偵查；四種都試完了就寫死信。"""
    return "discover_links" if state.get("fetch_strategy") else "escalate_human"


def route_after_discover_links(state: SpiderForgeState) -> str:
    """檢查一：這種抓法找不到任何文章證據 → 換下一種抓法。

    「證據」不等於「連結」：前端資料介面的記錄自帶標題與內容，沒有明細頁連結
    也算找到了（見 nodes/discover_links.py 的 api_records）。
    """
    has_evidence = state.get("discovered_detail_urls") or (
        state.get("link_discovery") or {}
    ).get("api_records")
    return "verify_samples" if has_evidence else "select_fetch_strategy"


def route_after_verify_samples(state: SpiderForgeState) -> str:
    """檢查二：樣本不是文章 → 換下一種抓法。

    這一關刻意不降級放行：樣本錯了還往下送，產碼模型會學到錯的 selector，
    而修復迴圈拿的是同一份錯證據，兩輪都會白花（BBC 實測）。
    """
    return (
        "verify_pagination"
        if (state.get("sample_verification") or {}).get("passed")
        else "select_fetch_strategy"
    )


def route_after_verify_pagination(state: SpiderForgeState) -> str:
    """檢查三：翻頁**偵測到訊號卻驗不過**才算失敗，而且是軟失敗。

    「確定沒有翻頁」是合格的偵查結果（只抓第 1 頁），不該換抓法。
    偵測到卻翻不動時換一種抓法可能有救（瀏覽器渲染後才出現的頁碼連結），
    但抓法用完就照現況降級放行——為了翻頁把一支能抓第 1 頁的爬蟲判死太貴。
    """
    probe = state.get("pagination_probe") or {}
    # cursor 型無法預先實抓但訊號本身確定性（deterministic），那是合格的偵查結果，
    # 不是「翻頁壞掉」——把它當失敗會換掉一個其實可用的抓法（cnyes 實測踩到）。
    detected_but_unverified = (
        bool(probe.get("candidates"))
        and not probe.get("verified")
        and not probe.get("deterministic")
    )
    tried = {row["strategy"] for row in state.get("discovery_attempts") or []}
    remaining = [
        strategy
        for strategy in state.get("fetch_strategy_pool") or []
        if strategy not in tried and strategy != state.get("fetch_strategy")
    ]
    if detected_but_unverified and remaining:
        return "select_fetch_strategy"
    return "collect_evidence"


def route_after_block_gate(state: SpiderForgeState) -> str:
    return "diagnose_failure" if state.get("block_page_detected") else "validate_output"


def route_after_preflight(state: SpiderForgeState) -> str:
    return (
        "fixture_test"
        if (state.get("generation_preflight") or {}).get("passed")
        else "diagnose_failure"
    )


def route_after_fixture(state: SpiderForgeState) -> str:
    return (
        "sandbox_test"
        if (state.get("fixture_result") or {}).get("passed")
        else "diagnose_failure"
    )


def route_after_validate(state: SpiderForgeState) -> str:
    if state.get("validation_result", {}).get("pass"):
        return "persist_spider"
    topic = state.get("topic_result") or {}
    if (
        topic.get("mode") == "enforce"
        and str(topic.get("status") or "").endswith("_unavailable")
    ):
        return "escalate_human"
    return "diagnose_failure"


def route_after_diagnose(state: SpiderForgeState) -> str:
    failure_class = normalize_failure_class(
        (state.get("diagnosis") or {}).get("failure_class")
        or state.get("failure_class")
    )
    if failure_class in KILL_FAILURE_CLASSES:
        return "escalate_human"
    if failure_class == "provider_failure":
        if state.get("provider_retry_count", 0) > _PROVIDER_RETRY_MAX:
            return "escalate_human"
        return "repair_code_kimi" if state.get("kimi_used") else "repair_code"

    retry = state.get("retry_count", 0)
    max_retries = state.get("max_retries", 2)
    if retry > max_retries:
        return "escalate_human"
    if retry >= 2:
        return "repair_code_kimi"
    return "repair_code"


def build_pipeline(checkpointer=None):
    """建立 Spider Forge 流程；節點順序與所有分支集中在本函式。"""

    # input_schema：入口只收 ForgeInput 宣告的欄位（內部欄位由 prepare_request 初始化）。
    # 刻意不設 output_schema——完整 state 對除錯與 checkpoint 續跑有用；要乾淨產出用 forge_result()。
    builder = StateGraph(SpiderForgeState, input_schema=ForgeInput)
    for name, function in [
        ("prepare_request", prepare_request),
        ("recon", recon),
        ("feasibility_triage", feasibility_triage),
        ("strategy_decision", strategy_decision),
        ("select_fetch_strategy", select_fetch_strategy),
        ("discover_links", discover_links),
        ("verify_samples", verify_samples),
        ("verify_pagination", verify_pagination),
        ("collect_evidence", collect_evidence),
        ("generate_spider", generate_spider),
        ("generation_preflight", preflight_generated_code),
        ("fixture_test", fixture_test),
        ("sandbox_test", sandbox_test),
        ("content_block_gate", content_block_gate),
        ("validate_output", validate_output),
        ("topic_gate", apply_topic_gate),
        ("diagnose_failure", diagnose_failure),
        ("repair_code", repair_code),
        ("repair_code_kimi", repair_code_kimi),
        ("persist_spider", persist_spider),
        ("escalate_human", escalate_human),
    ]:
        builder.add_node(name, function)

    builder.add_edge(START, "prepare_request")
    builder.add_edge("prepare_request", "recon")
    builder.add_edge("recon", "feasibility_triage")
    builder.add_conditional_edges(
        "feasibility_triage",
        route_after_triage,
        ["strategy_decision", "escalate_human"],
    )
    # ── 前期偵查子迴圈：選一種抓法 → 三道檢查 → 不過就換下一種（GRAPH.md 圖四）──
    builder.add_edge("strategy_decision", "select_fetch_strategy")
    builder.add_conditional_edges(
        "select_fetch_strategy",
        route_after_fetch_strategy,
        ["discover_links", "escalate_human"],
    )
    builder.add_conditional_edges(
        "discover_links",
        route_after_discover_links,
        ["verify_samples", "select_fetch_strategy"],
    )
    builder.add_conditional_edges(
        "verify_samples",
        route_after_verify_samples,
        ["verify_pagination", "select_fetch_strategy"],
    )
    builder.add_conditional_edges(
        "verify_pagination",
        route_after_verify_pagination,
        ["collect_evidence", "select_fetch_strategy"],
    )
    builder.add_edge("collect_evidence", "generate_spider")
    builder.add_edge("generate_spider", "generation_preflight")
    builder.add_conditional_edges(
        "generation_preflight",
        route_after_preflight,
        ["fixture_test", "diagnose_failure"],
    )
    builder.add_conditional_edges(
        "fixture_test",
        route_after_fixture,
        ["sandbox_test", "diagnose_failure"],
    )
    builder.add_edge("sandbox_test", "content_block_gate")
    builder.add_conditional_edges(
        "content_block_gate",
        route_after_block_gate,
        ["validate_output", "diagnose_failure"],
    )
    builder.add_edge("validate_output", "topic_gate")
    builder.add_conditional_edges(
        "topic_gate",
        route_after_validate,
        [
            "persist_spider",
            "diagnose_failure",
            "escalate_human",
        ],
    )
    builder.add_conditional_edges(
        "diagnose_failure",
        route_after_diagnose,
        ["repair_code", "repair_code_kimi", "escalate_human"],
    )
    builder.add_edge("repair_code", "generation_preflight")
    builder.add_edge("repair_code_kimi", "generation_preflight")
    builder.add_edge("persist_spider", END)
    builder.add_edge("escalate_human", END)
    return builder.compile(checkpointer=checkpointer or MemorySaver())


def forge_spider(
    url: str,
    *,
    max_retries: int = 2,
    run_id: str | None = None,
    full_state: bool = False,
    **request: Any,
) -> dict[str, Any]:
    """執行單一網址並回傳產出，不負責批次紀錄或終端輸出。

    ``request`` 只接受 :class:`~spider_forge.state.ForgeInput` 的欄位
    （target_schema / validation / topic_gate / sample_urls …）；內部欄位由
    ``prepare_request`` 初始化。預設回傳 ``forge_result``（乾淨產出）；
    除錯要看完整 state 傳 ``full_state=True``。
    """

    thread_id = run_id or f"forge-{uuid.uuid4().hex[:8]}"
    initial_state = {**request, "site_url": url, "run_id": thread_id, "max_retries": max_retries}
    config = {"configurable": {"thread_id": thread_id}, "recursion_limit": RECURSION_LIMIT}
    final_state = dict(build_pipeline().invoke(initial_state, config=config))
    return final_state if full_state else forge_result(final_state)

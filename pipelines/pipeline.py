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
from spider_forge.nodes.fixture import FixtureTest
from spider_forge.nodes.generate import GenerateSpider
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
from spider_forge.state import (
    KILL_FAILURE_CLASSES,
    ForgeInput,
    SpiderForgeState,
    forge_result,
    normalize_failure_class,
)

_PROVIDER_RETRY_MAX = 2

# ── 積木拼裝：每個節點在這裡實例化一次，設定寫在建構子（= pytorch 的 __init__）──
# 這些 module-level 名字同時是「可替換點」：測試或使用者把 pipeline.sandbox_test 換掉，
# build_pipeline() 讀 global 就會拿到替換後的版本。
prepare_request = PrepareRequest()
recon = Recon()
feasibility_triage = FeasibilityTriage()
strategy_decision = StrategyDecision()
discover_links = DiscoverArticleLinks()
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
        ("discover_links", discover_links),
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
    builder.add_edge("strategy_decision", "discover_links")
    builder.add_edge("discover_links", "collect_evidence")
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
    config = {"configurable": {"thread_id": thread_id}, "recursion_limit": 60}
    final_state = dict(build_pipeline().invoke(initial_state, config=config))
    return final_state if full_state else forge_result(final_state)

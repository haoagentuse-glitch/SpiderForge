"""SpiderForgeState — LangGraph 流程的共享狀態（docs/20_spec_v1_spider-forge-graph.md）。

total=False：節點逐步填欄位，不必一開始湊齊。
"""

from __future__ import annotations

from typing import Any, Literal, TypedDict

# 失敗分類法（spec v2 §4）中，一經判定就不再嘗試生成/修復、直接死信的類別。
# 這些在 feasibility_triage 以 ``KILL_`` 前綴出現，在 diagnose_failure 以裸名出現，
# 兩處共用同一集合，routers 用 normalize_failure_class() 去前綴後比對。
KILL_FAILURE_CLASSES = frozenset(
    {
        "policy_kill",
        "signature_required",
        "discovery_empty",
        "js_required",
        "auth_required",
    }
)


def normalize_failure_class(value: Any) -> str:
    """去掉 triage 的 ``KILL_`` 前綴，讓 diagnose 的裸名與 triage 的前綴名可統一比對。"""
    text = str(value or "")
    return text[len("KILL_") :] if text.startswith("KILL_") else text


class ForgeInput(TypedDict, total=False):
    """① 輸入層 —— 呼叫端（使用者/CLI/batch）該給的東西，也只有這些會被接受。

    graph 以 ``input_schema=ForgeInput`` 編譯：這是對外契約的唯一真實來源，
    內部欄位（retry_count 這類）由 ``prepare_request`` 初始化，呼叫端不必也不該傳。
    """

    site_url: str
    site_name: str              # 可省略；prepare_request 由 hostname 推導
    source_prefix: str          # 可省略；prepare_request 產安全 slug
    target_schema: dict[str, Any]   # 要抓什麼欄位（見 schemas/outputs.py）
    sample_urls: list[str]      # 選填；2–5 個已知明細 URL，不要求使用者準備 selector/HAR
    access_mode: Literal["public", "browser_session"]
    access_context_ref: str     # 選填；只交 recon，不得進 prompt / sandbox
    constraints: dict[str, Any]
    validation: dict[str, Any]  # 品質閘門設定（allowed_domains/article_url_patterns/…）
    topic_gate: dict[str, Any]  # off/shadow/enforce；預設 off（通用套件不預設領域過濾）
    block_gate: dict[str, Any]  # 每站設定：是否啟用 Gemini 內容真偽確認（預設純確定性，省額度）
    max_retries: int            # prepare_request 強制 0..2，預設 2
    run_id: str                 # 選填；省略時由呼叫端或 forge_spider 產生


class ForgeInternal(TypedDict, total=False):
    """② 內部層 —— 節點之間傳遞的中間態。呼叫端不傳、也不該依賴其形狀。"""

    # 哪些輸入是使用者明講的（prepare_request 補預設時記錄，供後續節點判斷）
    source_prefix_explicit: bool
    target_schema_explicit: bool
    validation_explicit: bool

    # ── 偵查 / 內部 EvidencePack ──
    recon_report: dict[str, Any]
    feasibility: dict[str, Any]  # feasibility_triage 輸出：class/reason/evidence_summary（spec v2 §3.2）
    evidence_pack: dict[str, Any]
    strategy: Literal["api", "dom", "hybrid"]
    strategy_detail: dict[str, Any]   # judge 完整輸出（含 confidence/reason/chosen_api）

    # ── 生成 / 測試（中間態）──
    generation_error: str
    generation_materials: dict[str, Any]  # 送入 coder 的材料量與裁切摘要
    generation_preflight: dict[str, Any]  # 確定性產碼契約檢查與安全修正紀錄
    fixture_result: dict[str, Any]  # 保存 response 的離線 callback 驗證結果
    candidate_path: str         # 隔離區候選檔（sandbox 跑這個，不碰 active）
    block_page_detected: bool   # content_block_gate 判定：200 但實為挑戰/錯誤頁（spec v2 §3.3）
    block_detection: dict[str, Any]  # 判定方法與證據（deterministic / gemini / fail-open）

    # ── 修復迴圈（防死循環的關鍵：簽章歷史，不只數次數）──
    retry_count: int
    provider_retry_count: int   # 供應商逾時/5xx（provider_failure）的累計重試；不計入 retry_count 修復額度（spec v2 §4）
    diagnosis: dict[str, Any]
    error_signature_history: list[str]
    kimi_used: bool             # 第二輪修復是否已用 Kimi（再失敗 → 轉人工）


class ForgeOutput(TypedDict, total=False):
    """③ 產出層 —— 一次執行結束後真正要交出去的東西（成功或死信皆然）。

    ``forge_result(state)`` 只挑這些欄位，讓呼叫端不必在幾十個中間欄位裡撈。
    graph 本身**不**用 output_schema 裁切：完整 state 對除錯與 checkpoint 續跑仍有價值。
    """

    spider_code: str            # 產出的爬蟲原始碼
    spider_path: str            # promote 後的 active 檔
    promotion: dict[str, Any]   # promote 紀錄（prev/new hash，可回滾）
    test_result: dict[str, Any]     # 沙盒實跑結果
    validation_result: dict[str, Any]
    topic_result: dict[str, Any]

    # 死信歸檔（spec v2 D4/§3.6/§7：escalate_human 非阻塞死信，不再 interrupt）
    failure_class: str          # KILL_* 或 repair_exhausted/topic_provider_unavailable
    dead_letter_path: str       # runtime/records/dead_letter/<run_id>.json

    status: Literal[
        "pending", "request_ready", "reconning", "triaging",
        "evidence_ready", "generating", "testing",
        "validating", "topic_validating", "repairing", "success", "escalated",
    ]


class SpiderForgeState(ForgeInput, ForgeInternal, ForgeOutput):
    """LangGraph 流程實際流動的完整狀態 = 輸入 + 內部 + 產出。

    節點照舊收/回這個型別；三層分開只是把「誰該碰什麼」寫進型別，
    並讓 graph 的入口（input_schema）與對外產出（forge_result）有明確定義。
    """


def forge_result(state: dict[str, Any]) -> dict[str, Any]:
    """從完整 state 萃取對外產出（``ForgeOutput`` 的欄位 + run_id）。"""
    keys = ("run_id", *ForgeOutput.__annotations__)
    return {key: state[key] for key in keys if key in state}

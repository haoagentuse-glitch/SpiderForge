"""Spider Forge 展示介面：貼一個網址，看每一關在做什麼、誰在做。

**逐節點標示「誰在判斷」是刻意的**：這個專案的重點是模型只出現在四個地方
（挑文章連結／產碼／修碼／主題判定），其餘全是程式——可重複、可測試、不花錢。
介面照實標，不把程式做的事說成模型在做。

流程一律走 ``pipeline.build_pipeline()``（唯一組裝點），不自己 import 節點、
不自己排順序。進度是靠把節點實例包一層取得的——``pipeline.py`` 的 module-level
節點名本來就是文件化的「可替換點」，所以包裝不需要改動流程本身。

跑法：
    python -m pipelines.ui
"""

from __future__ import annotations

import queue
import threading
import traceback
import uuid
from contextlib import contextmanager
from typing import Any

from spider_forge.clients.registry import get_provider
from spider_forge.config import ensure_runtime_layout
from spider_forge.shared.fetch_strategies import LABELS

from . import pipeline

# graph 的節點名 → pipeline.py 裡的變數名（兩個不同名）。
_NODE_ATTR = {
    "prepare_request": "prepare_request",
    "recon": "recon",
    "feasibility_triage": "feasibility_triage",
    "strategy_decision": "strategy_decision",
    "select_fetch_strategy": "select_fetch_strategy",
    "discover_links": "discover_links",
    "verify_samples": "verify_samples",
    "verify_pagination": "verify_pagination",
    "collect_evidence": "collect_evidence",
    "generate_spider": "generate_spider",
    "generation_preflight": "preflight_generated_code",
    "fixture_test": "fixture_test",
    "sandbox_test": "sandbox_test",
    "content_block_gate": "content_block_gate",
    "validate_output": "validate_output",
    "topic_gate": "apply_topic_gate",
    "diagnose_failure": "diagnose_failure",
    "repair_code": "repair_code",
    "repair_code_kimi": "repair_code_kimi",
    "persist_spider": "persist_spider",
    "escalate_human": "escalate_human",
}

# 節點 → (誰在做, 在做什麼)。「誰」用 key 表示，真正的模型名在執行期才解析，
# 這樣換模型（env 覆蓋）介面會跟著變，不會寫死一個過時的名字。
_STEPS: dict[str, tuple[str, str]] = {
    "prepare_request": ("python", "整理請求（正規化、補預設）"),
    "recon": ("python", "探測入口（連線與瀏覽器雙軌）"),
    "feasibility_triage": ("python", "可行性分流"),
    "strategy_decision": ("ollama", "判斷抓取策略"),
    "select_fetch_strategy": ("python", "選一種抓法"),
    "discover_links": ("gemini", "從連結池挑出文章"),
    "verify_samples": ("python", "檢查二：抓下來看是不是文章"),
    "verify_pagination": ("python", "檢查三：翻頁真的翻得動嗎"),
    "collect_evidence": ("python", "編材料（清雜訊、依內容裁切）"),
    "generate_spider": ("coder", "產出爬蟲程式碼"),
    "generation_preflight": ("python", "閘門一：語法樹契約檢查"),
    "fixture_test": ("python", "閘門二：離線重播保存的頁面"),
    "sandbox_test": ("python", "閘門三：隔離子程序實跑"),
    "content_block_gate": ("python", "閘門四：是不是錯誤頁"),
    "validate_output": ("python", "閘門五：品質驗證"),
    "topic_gate": ("gemini", "主題相關性"),
    "diagnose_failure": ("python", "診斷失敗原因"),
    "repair_code": ("coder", "依診斷修碼"),
    "repair_code_kimi": ("final_coder", "換一家模型再修"),
    "persist_spider": ("python", "升版存檔"),
    "escalate_human": ("python", "寫入待處理紀錄"),
}


def _actor(key: str) -> str:
    """把 actor key 解析成當下真正在做事的角色名。"""
    if key == "python":
        return "python 執行"
    try:
        provider = {
            "gemini": "gemini", "ollama": "ollama",
            "coder": "deepseek", "final_coder": "kimi",
        }[key]
        model = get_provider(provider).model if provider != "ollama" else _judge_model()
    except Exception:  # noqa: BLE001 — 介面不該因為讀不到設定就掛掉
        model = key
    return f"{model} 判斷" if key in {"gemini", "ollama"} else f"{model} 產碼"


def _judge_model() -> str:
    from spider_forge.clients.judge import DEFAULT_MODEL

    return DEFAULT_MODEL


class _StopAfterDiscovery(Exception):
    """只跑偵查模式：走到編材料就收手，不產碼、不花錢。"""


def _summary(node: str, update: dict[str, Any]) -> str:
    """一個節點做完之後，用一句話講它得到什麼。"""
    def get(key, default=None):
        return update.get(key, default)

    if node == "prepare_request":
        # 站名沒給時是由 hostname 推導的，跟 source_prefix 講的是同一件事；
        # 真正給人看的站名要等 recon 讀到 <title> 才有。
        return f"來源代號 {get('source_prefix')}（站名待探測）"
    if node == "recon":
        report = get("recon_report") or {}
        http = (report.get("http_entry_sample") or {}).get("status")
        return (
            f"{report.get('title') or '(無標題)'}"
            f"（瀏覽器 {report.get('http_status')}，連線 {http}，"
            f"{len(report.get('link_samples') or [])} 個連結，"
            f"{len(report.get('api_candidates') or [])} 個前端介面）"
        )
    if node == "feasibility_triage":
        return str((get("feasibility") or {}).get("class"))
    if node == "strategy_decision":
        detail = get("strategy_detail") or {}
        how = detail.get("decision_method") or ("模型判斷" if not detail.get("evidence_enforced") else "證據強制")
        return f"{get('strategy')}（{how}）"
    if node == "select_fetch_strategy":
        strategy = get("fetch_strategy")
        if not strategy:
            return "四種抓法都試完了"
        return LABELS.get(strategy, strategy)
    if node == "discover_links":
        found = get("link_discovery") or {}
        urls = get("discovered_detail_urls") or []
        if found.get("api_records"):
            return f"前端資料介面自帶 {found['api_records']} 筆記錄，沒有明細頁連結"
        return f"{len(urls)} 篇（候選 {found.get('candidates')} 個，方法 {found.get('method')}）"
    if node == "verify_samples":
        verdict = get("sample_verification") or {}
        if verdict.get("passed"):
            similarity = verdict.get("max_similarity")
            return f"通過（{len(verdict.get('accepted') or [])} 份合格" + (
                f"，相似度 {similarity}）" if similarity is not None else "）"
            )
        return f"不通過 - {verdict.get('reason')}"
    if node == "verify_pagination":
        pagination = get("pagination") or {}
        kind = pagination.get("type")
        if kind == "none_detected":
            return "沒有翻頁訊號，只抓第 1 頁"
        return f"{kind}（已驗證 {pagination.get('verified')}）"
    if node == "collect_evidence":
        pack = get("evidence_pack") or {}
        return (
            f"抓法 {pack.get('fetch_strategy')}，"
            f"傳輸要求 {pack.get('requirements') or '無'}，"
            f"{len(pack.get('dom_samples') or [])} 份明細樣本"
        )
    if node == "generate_spider":
        return f"{len(get('spider_code') or '')} 字的程式碼"
    if node == "generation_preflight":
        result = get("generation_preflight") or {}
        return "通過" if result.get("passed") else f"不通過 - {result.get('errors')}"
    if node == "fixture_test":
        result = get("fixture_result") or {}
        if result.get("passed"):
            return "通過"
        # callback 的 traceback 才講得出「為什麼抽不到」，只印 errors 的話畫面上
        # 永遠只有一句 insufficient_items，看不出真正的原因（科技新報實測踩到）。
        blame = "；".join(
            str(row).splitlines()[-1] for row in (result.get("callback_errors") or [])
        )
        return f"不通過 - {result.get('errors')}" + (f"｜{blame[:200]}" if blame else "")
    if node == "sandbox_test":
        result = get("test_result") or {}
        return f"抓到 {result.get('item_count')} 筆"
    if node == "content_block_gate":
        return "是錯誤頁" if get("block_page_detected") else "是內容頁"
    if node == "validate_output":
        result = get("validation_result") or {}
        if result.get("pass"):
            return (
                f"通過（{result.get('valid_count')}/{result.get('item_count')} 合格，"
                f"去重 {result.get('unique_valid_count')}）"
            )
        return f"不通過 - {result.get('reject_reasons')}"
    if node == "topic_gate":
        status = str((get("topic_result") or {}).get("status") or "未啟用")
        return "未啟用（通用套件不預設領域過濾）" if status == "disabled" else status
    if node == "diagnose_failure":
        diagnosis = get("diagnosis") or {}
        return f"{diagnosis.get('failure_class')} / {diagnosis.get('error_signature')}"
    if node in {"repair_code", "repair_code_kimi"}:
        return f"重寫 {len(get('spider_code') or '')} 字"
    if node == "persist_spider":
        return str(get("spider_path"))
    if node == "escalate_human":
        return f"{get('failure_class')} - {get('dead_letter_path')}"
    return ""


@contextmanager
def _instrumented(events: queue.Queue, *, discovery_only: bool):
    """把每個節點包一層，讓它在開始與結束時各發一個事件。

    包的是 ``pipeline`` 模組上的名字（文件化的可替換點），不是節點類別本身，
    所以流程的組裝方式完全沒有被改動。
    """
    originals = {
        attr: getattr(pipeline, attr)
        for attr in _NODE_ATTR.values()
        if hasattr(pipeline, attr)
    }

    def wrap(node: str, attr: str):
        inner = originals[attr]

        def traced(state):
            if node == "collect_evidence" and discovery_only:
                events.put(("stopped", node, dict(state)))
                raise _StopAfterDiscovery
            events.put(("start", node, None))
            update = inner(state)
            events.put(("done", node, dict(update or {})))
            return update

        return traced

    try:
        for node, attr in _NODE_ATTR.items():
            if attr in originals:
                setattr(pipeline, attr, wrap(node, attr))
        yield
    finally:
        for attr, original in originals.items():
            setattr(pipeline, attr, original)


def _worker(request: dict[str, Any], events: queue.Queue, *, full_run: bool) -> None:
    try:
        with _instrumented(events, discovery_only=not full_run):
            graph = pipeline.build_pipeline()
            thread_id = request["run_id"]
            final = graph.invoke(
                request,
                config={
                    "configurable": {"thread_id": thread_id},
                    "recursion_limit": pipeline.RECURSION_LIMIT,
                },
            )
        events.put(("final", None, dict(final)))
    except _StopAfterDiscovery:
        pass
    except Exception as exc:  # noqa: BLE001 — 例外要送到畫面上，不是吞掉
        events.put(("error", None, f"{type(exc).__name__}: {exc}\n{traceback.format_exc()[-1200:]}"))
    finally:
        events.put(("end", None, None))


# ════════════════════════════ Gradio 介面 ════════════════════════════

_INTRO = (
    "貼一個**新聞列表頁**的網址（例如 `https://www.cna.com.tw/list/afe.aspx`），"
    "下面會逐關顯示每一步是誰在做、得到什麼。\n\n"
    "預設只跑前期偵查：不呼叫產碼模型、不花錢，大約 20-40 秒。"
)


def _render(lines: list[str], pending: str | None) -> str:
    body = "\n".join([*lines, *( [pending] if pending else [] )])
    return f"```text\n{body or '準備中...'}\n```"


def _discovery_report(state: dict[str, Any]) -> str:
    """只跑偵查時的收尾：把挑到的網址列出來，讓人當場看得出網址形狀。"""
    urls = state.get("discovered_detail_urls") or []
    verdict = state.get("sample_verification") or {}
    pagination = state.get("pagination") or {}
    strategy = state.get("fetch_strategy")
    attempts = state.get("discovery_attempts") or []

    lines = ["### 前期偵查完成", ""]
    lines.append(f"- 通過的抓法：**{LABELS.get(strategy, strategy)}**")
    lines.append(f"- 樣本驗證：{'通過' if verdict.get('passed') else '不通過'}"
                 f"{'（相似度 ' + str(verdict['max_similarity']) + '）' if verdict.get('max_similarity') is not None else ''}")
    lines.append(f"- 翻頁：{pagination.get('type')}")
    if attempts:
        lines.append("- 換過抓法：")
        for row in attempts:
            lines.append(f"    - {LABELS.get(row['strategy'], row['strategy'])} 卡在 {row['failed_check']}：{row['reason']}")
    if urls:
        lines += ["", "挑到的文章網址（要限定版面就照這個形狀寫規則）：", ""]
        lines += [f"    {url}" for url in urls]
    lines += ["", "把「跑完整流程」打開，就會接著產碼並跑完五道閘門。"]
    return "\n".join(lines)


def _final_report(state: dict[str, Any]) -> str:
    status = state.get("status")
    if status == "success":
        validation = state.get("validation_result") or {}
        return "\n".join([
            "### 完成，爬蟲已升版",
            "",
            f"- 產出：`{state.get('spider_path')}`",
            f"- 抓到 {validation.get('item_count')} 筆，合格 {validation.get('valid_count')} 筆，"
            f"去重後 {validation.get('unique_valid_count')} 筆",
            f"- 修復輪數：{state.get('retry_count', 0)}",
        ])
    attempts = state.get("discovery_attempts") or []
    diagnosis = state.get("diagnosis") or {}
    lines = [
        "### 停下來了，已寫入待處理紀錄",
        "",
        f"- 分類：**{state.get('failure_class')}**",
        f"- 紀錄：`{state.get('dead_letter_path')}`",
    ]
    if diagnosis:
        lines.append(f"- 診斷：{diagnosis.get('failure_class')} / {diagnosis.get('error_signature')}")
    for row in attempts:
        lines.append(f"- {LABELS.get(row['strategy'], row['strategy'])} 卡在 {row['failed_check']}：{row['reason']}")
    return "\n".join(lines)


def _forge(url: str, patterns: str, excluded: str, full_run: bool, history):
    import time

    history = list(history or [])
    url = (url or "").strip()
    if not url:
        history.append({"role": "assistant", "content": "請先貼一個網址。"})
        yield history
        return

    ensure_runtime_layout()
    validation: dict[str, Any] = {}
    for field, raw in (("article_url_patterns", patterns), ("excluded_url_patterns", excluded)):
        values = [line.strip() for line in (raw or "").splitlines() if line.strip()]
        if values:
            validation[field] = values

    request: dict[str, Any] = {
        "site_url": url,
        "run_id": f"ui-{uuid.uuid4().hex[:8]}",
        "max_retries": 2,
    }
    if validation:
        request["validation"] = validation

    history.append({"role": "user", "content": url})
    history.append({"role": "assistant", "content": _render([], "啟動中...")})
    yield history

    events: queue.Queue = queue.Queue()
    worker = threading.Thread(
        target=_worker, args=(request, events), kwargs={"full_run": full_run}, daemon=True
    )
    worker.start()

    lines: list[str] = []
    pending: str | None = None
    started_at: float = time.monotonic()
    tail = ""

    while True:
        kind, node, payload = events.get()
        if kind == "start":
            actor_key, what = _STEPS.get(node, ("python", node))
            pending = f"{_actor(actor_key)} - {what} ..."
            started_at = time.monotonic()
        elif kind == "done":
            actor_key, what = _STEPS.get(node, ("python", node))
            actor = _actor(actor_key)
            # 挑連結那一步實際用了哪個模型只有事後才知道（Gemini 額度用完會退 Ollama）
            if node == "discover_links":
                method = (payload.get("link_discovery") or {}).get("method")
                actor = {"gemini": _actor("gemini"), "ollama": _actor("ollama"),
                         "heuristic": "python 執行（模型都不可用，退啟發式）"}.get(method, actor)
            # 主題閘門預設關閉，關著的時候一個模型都沒呼叫，不能掛模型名。
            if node == "topic_gate" and (payload.get("topic_result") or {}).get("status") == "disabled":
                actor = "python 執行"
            elapsed = time.monotonic() - started_at
            lines.append(f"{actor} - {what} -> {_summary(node, payload)}  [{elapsed:.1f}s]")
            pending = None
        elif kind == "stopped":
            pending = None
            tail = "\n\n" + _discovery_report(payload or {})
        elif kind == "final":
            pending = None
            tail = "\n\n" + _final_report(payload or {})
        elif kind == "error":
            pending = None
            tail = f"\n\n### 執行中斷\n\n```text\n{payload}\n```"
        elif kind == "end":
            history[-1] = {"role": "assistant", "content": _render(lines, None) + tail}
            yield history
            return
        history[-1] = {"role": "assistant", "content": _render(lines, pending) + tail}
        yield history


def build_demo():
    import gradio as gr

    with gr.Blocks(title="Spider Forge", fill_height=True) as demo:
        gr.Markdown("## Spider Forge\n" + _INTRO)
        # Gradio 6 只剩 messages 格式，Chatbot 已經沒有 type 參數。
        chatbot = gr.Chatbot(height=520, resizable=True)
        with gr.Row():
            url = gr.Textbox(
                label="新聞列表頁網址", scale=4, autofocus=True,
                placeholder="https://www.cna.com.tw/list/afe.aspx",
            )
            run = gr.Button("開始", variant="primary", scale=1)
        full_run = gr.Checkbox(
            label="跑完整流程（產碼 + 五道閘門，會呼叫付費模型並實際連線抓取）",
            value=False,
        )
        with gr.Accordion("進階：限定要抓哪個版面（選填）", open=False):
            gr.Markdown(
                "模型分得出「文章 vs 導覽」，但分不出「商業版 vs 體育版」——"
                "那是使用者意圖，不是網頁裡的事實。留空也能跑；"
                "先跑一次看它挑到的網址長什麼形狀，再照著寫規則最快。\n\n"
                "一行一個正則，例如 `/news/[a-z]+/\\d+\\.aspx`"
            )
            patterns = gr.Textbox(label="文章網址規則", lines=3, placeholder="/news/[a-z]+/\\d+\\.aspx")
            excluded = gr.Textbox(label="要排除的網址規則", lines=2, placeholder="/sport/")

        for trigger in (run.click, url.submit):
            trigger(_forge, [url, patterns, excluded, full_run, chatbot], [chatbot])
    return demo


def main() -> None:
    build_demo().queue().launch(server_name="127.0.0.1", inbrowser=False)


if __name__ == "__main__":
    main()

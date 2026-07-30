# 08 · spider_forge — LangGraph AI 爬蟲生成系統規劃

> 本文件已由 [`../20_spec_v1_spider-forge-graph.md`](../20_spec_v1_spider-forge-graph.md) 取代；正文保留為 2026-07-26 實作前藍圖。
> 規格層級：設計提案（實作前藍圖）。狀態：已核可，M0 進行中。
> 撰寫：2026-07-26。關聯：`03_SD.md`§1.1（文章物件）、`04_API_v2.md`§4.1/§2（articles 契約與 Enum）、`07_Prompt範本.md`。

## 0. 為什麼做

媒體新聞管線斷在**第一棒**：目前沒有任何 production 程式會 `INSERT INTO articles`，媒體新聞是靠 `_handoff/crawler/seed_articles.py` 讀凍結 JSON 快照（`cnyes_news_2026.json`）手動補位。每接一個新聞站就得有人手刻一支 `requests` 爬蟲、對付版面，站一改版又重寫——不可規模化。

spider_forge 把「接一個新站」自動化：丟入 N 個網站清單，系統自主完成 **偵查 → 判斷走 API 還是解析頁面 → 產 spider code → 沙盒測試 → 失敗自我修復**，直到可用或轉人工。

落地策略：先在 `workspace/backend/app/spider_forge_system/` 開新目錄漸進開發，**先不動** `seed_articles.py`，完善後再替代那條手動管線。

## 1. 架構總覽

```
site_queue.yaml (N 個網站)
      │
      ▼
  LangGraph StateGraph (每站一個 thread)
  recon(本地) → strategy_decision(本地) →(分支)→ generate_api_spider / generate_axtree_spider(強模型)
      → sandbox_test(subprocess) → validate_output(本地)
      → 成功 → persist_spider → 下一站
      → 失敗 → diagnose_failure(本地) → heuristic_repair(免LLM) →救不回→ repair_code(強模型) → 回 sandbox_test
      → 重複錯誤簽章 or retry≥max → escalate_human(interrupt 轉人工)
      │
      ▼
  Neon articles 表（複用 repo schema 契約 + ON CONFLICT 冪等 upsert）
```

**三層職責分工（硬性原則）：**
- **判斷層（本地 Ollama 小模型）**：recon 摘要、strategy 決策、diagnose 錯誤摘要、validate 資料品質——全部結構化 JSON，不碰 code。
- **生成層（雲端強模型 DeepSeek/Kimi）**：只做兩件事——產 spider code、修 spider code。
- **執行/工具層（無模型）**：Playwright 偵查、Scrapy 執行、trafilatura 抽取、subprocess 沙盒、heuristic 選擇器自癒。

## 2. 技術選型

| 角色 | 選擇 | 理由 |
|---|---|---|
| 本地判斷模型 | Ollama + Qwen2.5-7B-Instruct Q4（保守可降 Qwen3-4B） | RTX 4060 8GB：7B Q4≈4.7GB；判斷任務是分類/抽取，小模型足夠；`format:json` 逼合法 JSON |
| 強 code 模型 | DeepSeek `deepseek-chat` 或 Kimi K2 | 皆 OpenAI 相容 → 設 `LLM_BASE_URL` 即可**複用** `llm_MMJSUN/client.py`，零改寫；便宜、coding 強 |
| 偵查工具 | Playwright（accessibility snapshot + network 監聽） | 同一 context 同時看「頁面 AXTree」與「背後 XHR/API」；新聞內容常來自 API，抓 API 比爬 DOM 穩 |
| 正文抽取/驗證 | trafilatura | 新聞正文 + `published_at` 抽取最可信的維護中函式庫（newspaper3k 已停維） |
| 爬蟲執行 | 獨立 Scrapy 專案 + scrapy-playwright | 工業級排程/去重/pipeline/併發；pipeline 寫回 Neon |
| 沙盒 | subprocess + timeout + rlimit + 網域白名單 | 個人機務實選；升級路徑 Docker+gVisor / e2b microVM |
| 自癒 | Scrapling 式 heuristic 選擇器自癒（免 LLM）→ 救不回才丟強模型 | 省 token；小幅版面漂移不必動用強模型 |

Firecrawl / Crawl4AI 降為**可選偵查輔助**，不進主線。

## 3. 複用 repo 既有資產（不重造）

| 複用對象 | 位置 |
|---|---|
| articles schema 契約 | `docs/spec/03_SD.md`§1.1、`04_API_v2.md`、`DATABASE/postgres/03_create_articles.sql` |
| Neon 連線層 `get_conn()` | `workspace/backend/app/db/session.py` |
| HTML 清洗 + ticker 標註 | `crawler_Arku/base.py`（`clean_html`、`extract_related_tickers`——**只掃標題**） |
| 冪等 upsert 範式 | `crawler_Arku/mops_crawler.py`（`ON CONFLICT (article_id) DO NOTHING`） |
| OpenAI 相容 LLM client | `llm_MMJSUN/client.py`（`LLM_BASE_URL` 可切 DeepSeek/Kimi） |

## 4. 關鍵設計決策

- **Scrapy 骨架由系統 scaffold 一次**，不手建；`items.py` 欄位嚴格對齊 articles 契約，不自創欄位；`pipelines.py` = `clean_html`→`extract_related_tickers`→冪等 upsert 寫 Neon。
- **修 code 用整段重寫，不用行級 diff**（SWE-agent 實證行級 diff 套用成功率僅 ~48%）。
- **本地判斷一律結構化 JSON**：strategy → `{strategy, confidence, reason, api_endpoints, candidate_selectors}`；diagnose → `{failure_type, evidence, suggested_fix, error_signature}`；validate → `{pass, field_scores, issues}`。
- **防死循環 = 錯誤簽章而非只數次數**：state 存 `error_signature_history`，簽章重複即判「非收斂」提前轉人工。
- **轉人工用 LangGraph `interrupt()`**（迴圈邏輯放 conditional edge，勿把 interrupt 塞進 `while True`）。
- **「100% 可用」定義**：機械硬指標（必填欄位非空率 100%、`published_at` 可解析、content 長度門檻）＋ trafilatura 對照 ＞ 本地模型語意評分。**鐵律：機械指標 > 語意評分**，語意只能否決不能放行。
- **批次**：站間序列（每站一 thread_id）、站內 Scrapy 併發（AutoThrottle 控速）；LangGraph checkpointer 斷點續傳（個人機 SqliteSaver，多 worker 換 PostgresSaver）。

## 5. 初始 site_queue 新聞名單

**第一波（MVP）**：鉅亨網 cnyes（`news.cnyes.com`，api，repo 已驗證，**首站**）、經濟日報（`money.udn.com`，api/hybrid）、中央社財經（`cna.com.tw`，axtree，練 HTML 路線）。
**第二波**：工商時報 `ctee.com.tw`、MoneyDJ `moneydj.com`、自由財經 `ec.ltn.com.tw`、ETtoday 財經 `finance.ettoday.net`、Yahoo 奇摩股市 `tw.stock.yahoo.com`。
反爬強或需登入的站先不列。

## 6. 實作里程碑（walking skeleton 優先）

| 里程碑 | 內容 | 驗收 |
|---|---|---|
| M0 | 目錄骨架 + Scrapy scaffold + items/pipelines 對齊契約 | 手動 yield 一筆假 item，DB 查得到 |
| M1 | recon→strategy→generate→sandbox→validate→persist 直線，對鉅亨跑通端到端 | 一站一鍵，URL→articles 表有真資料 |
| M2 | 判斷層接 Ollama、code 層接 DeepSeek/Kimi | 判斷走本地、code 走強模型，格式合法 |
| M3 | 修復迴圈 + 錯誤簽章防死循環 + interrupt 轉人工 | 餵會失敗的站，能自癒或正確轉人工，不無限迴圈 |
| M4 | run_batch 批次 + SqliteSaver 斷點 + runs.jsonl 維運紀錄 | 3-5 站清單，中途中斷可續跑 |
| M5 | 沙盒硬化 + 取代 seed_articles.py | 媒體新聞自動進 articles |

## 7. 風險

| # | 風險 | 緩解 |
|---|---|---|
| R1 | 4060 8GB 硬天花板，7B+長 context 可能 OOM/JSON 不穩 | 實測 7B vs 4B；不穩降 4B 或切段 recon |
| R2 | 反爬（Cloudflare/DataDome） | 先鎖好抓的站；需要時評估 Patchright，不進 MVP |
| R3 | 強模型產不可執行 code | 沙盒 `py_compile` pre-check；錯誤簽章提前止血；記錄 token 花費 |
| R4 | 本地模型語意誤判放行髒資料 | 機械硬指標 + trafilatura 對照優先，語意只能否決 |
| R5 | AXTree/network 偵查 token 成本 | snapshot 已比截圖省；必要時只抓主內容區 subtree（本地模型跑不花 API 錢） |
| R6 | published_at 時區/格式雜亂 | trafilatura + 要求輸出 ISO8601；pipeline 統一正規化 |
| R7 | 網站改版失效重生成 | 定期監測抽取成功率，低於門檻自動回丟 site_queue 觸發重生成（後續里程碑） |
| R8 | ~~Anansi 定位~~ 已結案 | 不新增依賴，自癒採 Scrapling 式；Anansi 的 MCP 模式列後續可選增強 |

維運紀錄：`registry/runs.jsonl` 每站每輪一筆（策略/重試/錯誤簽章/token/結果/spider 路徑/時間）。

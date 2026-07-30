# spider_forge_system 健檢報告

> 目的：交給第二個 AI（codex）做對抗性審查。撰寫 2026-07-26。
> 審查者請注意：本報告作者就是實作者，**你的價值在找出我漏掉或高估的地方**——尤其是「信心分數」給太高、或方向根本錯的地方。不要客套。
>
> **2026-07-26 現況更正：** 本文件下方是 Claude 修復前／第一版修復時的歷史快照，不再代表目前程式狀態。最新驗收與剩餘邊界以 `CLAUDE_VALIDATION_REPORT.md` 為準；目前 validator、fail-closed promotion、實體 rollback、error usage 與 registry 品質欄位已通過 26 個離線／整合測試，真正 filesystem/network sandbox 與 DB quarantine 仍為 UNVERIFIED。

---

## 修復進度（2026-07-26，依 CLAUDE_REMEDIATION_BRIEF §11 第一交付）

三態結論：brief 的 P0 指控經**實測驗證多數為真**；第一交付（安全/正確性基線）已完成並通過 fixture 測試。

### REFUTED（我原健檢的錯誤，已更正）
- 「可用 spider 2 支」**不成立**。UDN 是假成功：`udn_spider.py:11` 打 `money.udn.com/api/v1/stock/top`（股價指數排行），12 筆只有 6 個唯一 URL、全是道瓊/S&P/納指等指數頁，非新聞。（實測 `validate_items` 對真實 UDN 輸出回 `pass=false, reasons={url_excluded_pattern:12}`。）

### VERIFIED（已修 + fixture 證明）
| 項 | 修法 | 檔案 | 驗收 |
|---|---|---|---|
| P0-1 驗證器放行垃圾 | 重寫成確定性四布林漏斗 | `validators.py`、`graph.py:172` | `test_validators` 9/9：垃圾/UDN指數/外域/空content/重複/1785/無時區/None 全拒，乾淨新聞過 |
| P0-2 候選覆寫 active | candidate 隔離 + atomic promote | `staging.py`、`sandbox/runner.py`、`graph.py:57,102` | `test_staging` 4/4：語法錯候選 active hash 不變、promote 有 prev/new hash 可回滾 |
| P0-3 沙盒洩漏 secrets | env 白名單，不帶 DB/API key | `sandbox/runner.py:sandbox_env` | `test_safety`：`leaked=[]`、allowlist 生效 |
| P0-5 usage 跨站污染 | 每站前 `drain_usage()` | `run_batch.py`、`models/coder_client.py` | `test_safety`：B 站 token=10 不含 A 站殘留 |

實跑命令（從 `workspace/backend/`）：
```
python -m app.spider_forge_system.tests.test_validators   # 9/9
python -m app.spider_forge_system.tests.test_safety       # 3/3
python -m app.spider_forge_system.tests.test_staging      # 4/4
```
Commit：`cfb9d55`（P0-1/3/5）、`483de24`（P0-2）。

### 仍 UNVERIFIED（誠實標註，尚未做）
- **完整 OS 沙盒隔離**：env 已剝 secrets，但 subprocess+timeout **不是**安全邊界——網路/檔案/記憶體隔離在 Windows 下未驗證（需 Docker+gVisor/microVM）。網域白名單目前只注入 env、未強制 egress。
- **P0-4 DB 列驗證/quarantine**、**recon 契約重寫（§4.1）**、**failure taxonomy（P1-B）**、**checkpoint 真斷點（InMemorySaver→SQLite）**、**watermark/backfill（max_pages=2 寫死）**：均未動，屬後續交付。
- **HTML 站能否救起**：驗證器現在會正確擋掉 HTML 站的爛輸出（＝更多 escalate），但「產得出可用 HTML spider」仍是未解難題（原健檢 #1/#2）。
- brief §5 的 **SiteRecipe 架構轉向**：尚未採納，待 P0 基線 + 單站 kill-test 後再決定。

### 未在此交付動的檔（保留現況）
生成的 `spiders/{udn,cna,ctee,moneydj}_spider.py`（batch 產物，含已知假成功的 udn）、被 batch 覆寫的 `cnyes_spider.py`：**未清理、未當可信 active**。候選 staging 上線後，未來只有通過品質閘門的候選才會 promote 成 active。

---

## 0. 系統是什麼（30 秒版）

LangGraph 驅動的「AI 自動生成爬蟲」系統：丟入新聞站清單 → 偵查 → 本地模型判策略（api / axtree）→ 強模型產 Scrapy spider → 沙盒跑 → 驗證 → 失敗自我修復（DeepSeek 主力、Kimi 最後一搏）→ 成功 persist / 失敗轉人工。目標是替代目前靠 `_handoff/crawler/seed_articles.py` 讀凍結 JSON 手動補位的媒體新聞管線，讓文章自動進 Neon `articles` 表。

**分工原則**：判斷（本地 Ollama qwen2.5:7b）／產修 code（雲端 DeepSeek+Kimi）／執行（Playwright 偵查、Scrapy、subprocess 沙盒）。

## 1. 模組地圖（現況）

```
spider_forge_system/
├── graph.py               # LangGraph 流程 + 全部節點實作 + prompts（404 行，見問題#3）
├── state.py               # SpiderForgeState TypedDict
├── persistence.py         # 正規化 + 冪等 upsert articles（複用 repo base.py/session.py）
├── run_batch.py           # 讀 site_queue.yaml 序列跑，結果寫帳本
├── site_queue.yaml        # 站清單
├── models/
│   ├── judge_client.py    # Ollama JSON-schema 約束（判斷節點共用）
│   └── coder_client.py    # DeepSeek 主力 / Kimi 最後一搏（OpenAI 相容）
├── recon/browser_probe.py # Playwright AXTree(ARIA) + network 監聽，挖 JSON API 候選
├── sandbox/runner.py      # subprocess 沙盒 + scrapy crawl（沙盒停用 DB pipeline）
├── registry/run_logger.py # runs.jsonl 維運帳本（只追加）
└── news_crawler/          # Scrapy 專案本體（items 對齊 articles 契約，pipeline 寫 Neon）
```

流程圖：
`recon → strategy_decision →(api|axtree)→ generate_* → sandbox_test → validate_output`
`  → pass: persist_spider`
`  → fail: diagnose_failure → route(重試耗盡或簽章重複?) → heuristic_repair→repair_code(DeepSeek) | repair_code_kimi(最後一搏) | escalate_human(interrupt)`

## 2. 實測證據（5 站批次，runs.jsonl）

| 站 | 策略 | 結果 | retry | items | coder tokens | 耗時 |
|---|---|---|---|---|---|---|
| cnyes 鉅亨 | api | ✅ success | 0 | 20 | 2,797 | 26s |
| udn 經濟日報 | api | ✅ success | 0 | 12 | 1,639 | 23s |
| cna 中央社 | axtree | ❌ error（Kimi 逾時崩潰，已修） | — | — | — | — |
| ctee 工商時報 | axtree | ❌ error（同上） | — | — | — | — |
| moneydj | api | ⚠️ escalated（乾淨轉人工） | 4 | 0 | 22,495 | 406s |

**彙總**：可用 spider 2 支、成功平均重試 **0.0**、兩支皆一次過；失敗 3 支；coder token 合計 26,931。

**關鍵數據點**：
- **API 且乾淨的站一次就過**（0 重試），系統核心價值在此類站已成立。
- **moneydj 一個註定失敗的站燒了 22,495 tokens / 6.8 分鐘**才 escalate——是成功站成本的 8 倍以上。
- 失敗 3 站全是 HTML 或複雜站；成功 2 站全是乾淨 JSON API 站。

## 3. 問題清單 × 改善方案 × 信心分數

信心拆兩軸（對難題兩者差很大，混寫＝自欺）：
**修法正確** = 這個改動本身能不能實作對；**達成目標** = 改完能不能真的解決問題。

| # | 嚴重度 | 問題 | 證據 | 改善方案 | 修法正確 | 達成目標 |
|---|---|---|---|---|---|---|
| 1 | 🔴 | **HTML/axtree 生成品質不足**（核心痛點） | cna+ctee+moneydj 迴圈都產不出可用 spider | 改用 trafilatura 抽正文，把 spider 職責縮小到「拿文章 URL 清單」，少叫 LLM 寫 selector | 80% | **50%** |
| 2 | 🔴 | validate_output 只有機械指標、無語意層 | HTML 站可能「機械過但 content 是導覽列雜訊」 | 加 trafilatura 正文分數，機械過但正文分低→否決 | 90% | 把關 90% / 讓站成功 30% |
| 3 | 🔴 | graph.py 404 行 god-file | 實測 404 行，混編排+11節點+prompt+LLM | 拆 `nodes/`(judge/gen/tool)+`prompts.py`，graph 只留 wiring | 95% | 95% |
| 4 | 🟡 | related_tickers 永遠 `[]` | pipeline 傳 ticker_index=None，無人設 | pipeline open_spider 從 DB load 一次 index（複用 base.load_ticker_index） | 92% | 92% |
| 5 | 🟡 | heuristic_repair 是 no-op | cna/ctee 每輪都燒強模型 | M3 Scrapling 自癒 | 70% | ⚠️ 見誠實事項(A) |
| 6 | 🟡 | Kimi 讀取逾時炸整站（**已修 be07ab6**） | runs.jsonl: read timeout=180 | highspeed 模型 + 300s + _safe_generate 優雅降級 | 90%(已驗編譯) | 消除崩潰 90% / 修好難站 40% |
| 7 | 🟡 | recon 無容錯 | 站掛/反爬會讓整站 error | recon 包 try/except + 早退 | 85% | 85% |
| 8 | 🟡 | 長迴圈無早退、燒錢 | moneydj 22k tokens/6.8min 才失敗 | 偵查判斷「結構抓不到」時提早 escalate | 65% | 60% |
| 9 | 🟢 | dotenv loader 三份重複 | grep 實測 session.py/client.py/coder_client.py | 收斂成一個 config | 90% | 90% |
| 10 | 🟢 | 生產寫 Neon 未端到端驗；stance_flag 是猜的 | 只有 M0 假列 + sandbox 停 DB | 接 ticker index 後做一次限量正式寫入 | 88% | 85% |
| 11 | 🟢 | 生成 spider 進版 churn/未審 | git 看到 udn/cna spider 冒出 | gitignore 生成物，只留範例 | 95% | 95% |

## 4. 三個必須誠實講的地方（請審查者重點檢視）

**(A) #5 我一度講錯，已更正**：`heuristic_repair`（Scrapling 式）是給「**已上線 spider 因網站改版而 selector 漂移**」自癒用的——靠比對舊 DOM。但 cna/ctee/moneydj 是**初次生成就沒成功**，沒有「舊的能動版本」可 heal。所以 **M3 heuristic 救不了目前的 HTML 失敗**，它的價值在維護期不在首建期。若審查者認為我這裡又想錯了，請指出。

**(B) HTML 站能否救起是全案最大未知（達成目標僅 50%）**。方向我有把握（trafilatura 抽正文），但「改完 HTML 站就穩定成功」不敢給高分——每站版面不同、LLM 寫 selector 本質脆弱。**策略層的取捨**：是否該對 HTML 站放棄「LLM 生成 selector」，改成「trafilatura 通用抽取 + spider 只負責翻頁拿 URL」？請審查者評估這個方向對錯。

**(C) API 站成功別過度外推**：cnyes/udn 成功是因為有乾淨 JSON API 且反爬弱；moneydj 同判 api 卻失敗（API 複雜）。遇反爬強或需簽章的 API，此路線也會卡。

## 5. 建議處理順序（尚未執行，待拍板）

1. 低垂果實（信心高、便宜）：#4 ticker 標註、#11 gitignore、#7 recon 容錯、#9 config 收斂。
2. 結構債：#3 拆 nodes/。
3. 核心難題：#1+#2 HTML 路線 trafilatura——**先拿單一 HTML 站做 kill-test，別一次改五站**。
4. #10 正式寫 Neon：接上 #4 後做一次限量、需人工確認。

## 6. 想請審查者特別回答的問題

1. #1 的「trafilatura 抽正文 + spider 只拿 URL」方向，對 HTML 新聞站是不是比「LLM 生成 selector」更務實？還是有更好的第三條路？
2. 我的信心分數哪幾個明顯高估？（尤其 #1/#2/#6 的「達成目標」欄）
3. 有沒有我**整個沒看到**的架構風險（例如：沙盒隔離對「LLM 生成的未知 code」在 Windows subprocess 下夠不夠、LangGraph checkpointer 目前用 MemorySaver 無法真正斷點續傳、並發安全）？
4. moneydj 那種「判 api 卻註定失敗還燒 22k token」的 case，早退條件該怎麼設計最有效？

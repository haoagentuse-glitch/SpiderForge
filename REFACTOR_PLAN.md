# spider_forge_system 重構任務清單（A+B）

寫給接手的 session（含未來的我）。自足文件，不依賴產生它的那場對話。
建立：2026-07-29。狀態：**Phase 0–4 程式與離線驗收完成；產碼預檢、材料編譯與 fixture 閘門已正式接入；活站 Docker 與前後指標比較待使用者執行**。每階段一個 checkpoint，可中斷續做。

---

## 0. 這是什麼、目標、鐵律

- **系統**：`app/spider_forge_system` —— 一台「AI 自動產 Scrapy 爬蟲」的工廠。輸入 URL，經 探測→產碼→沙盒實跑→驗證→(修復)→認證，輸出一支通過內部 CI 的 spider。使用者定位它為**求職亮點**；`crawler_runtime` 裡多數手寫 spider 其實是它產的，所以它會動、值得包裝。
- **唯一目標**：**產出「高度可行」的 spider，同時壓低 API 成本**。這兩件是同一根槓桿（第一次生成就對 → 不觸發昂貴 repair 迴圈）。
  - **核心洞察（本次重構的靈魂）**：成敗由 **evidence 品質**決定，不是模型聰明或 repair 次數。把智慧搬到**便宜、確定性**的前段（攔真實 request/response、端點與資料存在性 kill-test），LLM 只做「把已知請求翻成 Scrapy」的小事。=「攔真實請求 > 讓模型猜」。
- **鐵律**：
  - 重構前基準測試要**綠**（現在是紅的，見 §1 Phase 0）；紅的基準上不重構。
  - 這是**搬遷+改良，不是重寫**：既有能動的邏輯儘量搬進新位置再改，不砍掉重來。
  - **解耦**：spider_forge 不准引用 `crawler_runtime/` 任何檔（現在的壞就是這個跨模組硬依賴造成）。工廠輸出「spider + CI 認證」，人工搬進 crawler_runtime，這條手動邊界是刻意的。
  - **節點解耦**：所有節點使用相同的「接收狀態、回傳狀態更新」介面；節點不得互相 import，只能由 `pipeline.py` 組裝。跨節點能力放 `shared/`。
  - 每階段做完跑該階段驗收 + commit（訊息寫過了哪階段），再進下一階段。

## 環境
- 從 `workspace/backend/` 跑：`../.venv/Scripts/python.exe -m app.spider_forge_system.<...>`
- 測試：`../.venv/Scripts/python.exe -m pytest app/spider_forge_system/tests/`（或各 test_*.py 用 `-m` 跑）
- Windows / PowerShell；路徑含中文，檢視 JSON 用 `python -X utf8 -c` 印，別直接 Read。

---

## 三個成功方案（依 API 成本，低→高投入；本計畫落地 A+B，C 為終局願景）

「成功」＝產出高度可行的 spider 且壓低 API 成本，兩者是**同一根槓桿**：第一次生成就對 → 不觸發昂貴 repair 迴圈。API 錢燒在 generate + repair(≤2輪) + judge；repair 被觸發的根因是**第一次 evidence 太弱、模型在猜**。

- **方案 A — 不花冤枉錢（省流，最便宜採用）**：任何 LLM 呼叫前先跑零成本 kill-test（端點存否／目標深度有無資料／明確付費或挑戰頁）→ 不可行 $0 收場；失敗類別屬「LLM 修不好」立即中止不跑滿 repair；便宜模型優先、失敗才升級。同架構、只停止為死路付錢。→ 本計畫 **Phase 3**。
- **方案 B — 把智慧搬到前端（主戲，投報最高）**：recon 攔「真實 request+response」逐字餵進生成 prompt，模型任務從「發現站怎麼運作」（猜、貴、高失敗）降為「翻譯已知交換成 Scrapy」（近確定、一次過）。首次成功率↑＝API 成本↓，同一動作買到兩者。→ 本計畫 **Phase 4**。
- **方案 C — recon-heavy／LLM-light 的自足工廠（終局，最高投入）**：B + 完全解耦；把 endpoint 發現／請求攔截／翻頁偵測／回應 schema 推斷做成**確定性可快取**元件，LLM 只剩最小翻譯；CI 閘門＝對活站點實跑產出 N 筆有效資料才認證（「CI 過」定義上＝「高度可行」）；只快取偵查證據，不複製已完成爬蟲。→ 本計畫 P2 的解耦是它的頭，其餘為未來，**站點多到人工來不及時才值得投**。

**前提檢查**：若站點清單相對固定，人工流程（探測→攔真實請求→寫 spider）本身就是最低 API 成本方案（趨近 $0）。spider_forge 的價值在「站點多到需自動化」時才劃算——投 B 或 C 前先定調這件事。

---

## 目標架構（重構後長這樣）

```
spider_forge_system/            # 套件名先不改（避免動 import 路徑），只改內部結構
├── README.md
├── __main__.py                 # ★標準 python -m 套件入口
├── cli.py                      # run/batch/status/paths/train-topic
├── pipeline.py                 # ★唯一流程真相：讀這個檔就懂整套系統（見下方骨架）
├── config.py                   # 所有旋鈕：模型、門檻、路徑（取代 runtime_paths.py）
├── stages/                     # 一階段一檔，每檔一個函式，職責一句話講得完，<200 行
│   ├── probe.py                #   ① 零成本 HTTP/Playwright 探測
│   ├── triage.py               #   ② kill-test + 判 api/html/hybrid
│   ├── evidence.py             #   ③ 攔真實 request+response，組 EvidencePack
│   ├── generate.py             #   ④ 呼叫 coder 產碼
│   ├── fixture.py              #   ⑤ 保存 response 的離線 callback 閘門
│   ├── sandbox.py              #   ⑥ 活站隔離實跑候選
│   ├── validate.py             #   ⑦ 確定性品質規則 + block gate + topic gate
│   └── repair.py               #      失敗回饋修復
├── shared/                     # 節點共同使用的內部工具，不直接成為流程節點
│   ├── evidence.py parsers.py prompts.py materials.py
│   ├── generation.py fixture.py repair.py sandbox.py request_identity.py
│   └── quality_rules.py summaries.py topic.py
├── clients/                    # 外部服務封裝（換 provider 只動這裡）
│   ├── coder.py judge.py topic.py
│   └── browser.py              #   Playwright
├── output/                     # 產出就在產出：CI 過的 spider（人工搬走的來源）
├── runs/                       # 批次協調與執行紀錄服務
│   ├── batch.py ledger.py
├── tools/                      # 選用維護工具，不參與 pipeline
│   └── topic_training.py
└── tests/
```

### pipeline.py 目標骨架（照這個寫，控制流明文可讀）
```python
def forge_spider(url: str) -> Result:
    facts   = probe(url)                       # ① 零成本探測，不碰 LLM
    verdict = triage(facts)                    # ② kill-test + 路線判定
    if verdict.infeasible:
        return escalate(url, verdict.reason)   #    不可行 → $0 收場
    evidence = collect_evidence(url, verdict.route)   # ③ 攔真實 request/response
    material = compile_materials(evidence)     # ④a 只留已選來源與精簡 DOM
    spider   = generate(material)              # ④b 一次產出完整候選
    for attempt in range(MAX_REPAIRS + 1):
        preflight(spider)                      # ⑤ 靜態契約
        replay_saved_fixture(spider, evidence) # ⑥ 不連網 callback 驗證
        run    = sandbox_run(spider)           # ⑦ 活站隔離實跑
        report = validate(run)                 # ⑧ 確定性品質 + block/topic gate
        if report.passed:
            return certify(spider, report)     # ✅ CI 過 → 寫 output/
        spider = repair(spider, report)        # ❌ 帶回饋修（燒 API 的地方）
    return escalate(url, "repairs_exhausted")
```

### 現況 → 新位置（搬遷對照）
| 現在 | 搬去 |
|---|---|
| `graph/nodes.py`(1167行,13節點) | 拆進 `stages/*.py`（每節點回對應階段） |
| `graph/build.py`+`routers.py` | 收斂成 `pipeline.py` 控制流 |
| `graph/evidence.py`(717) | `stages/evidence.py` + `shared/evidence.py`、`shared/parsers.py` |
| `topic_gate.py`(523)+`validators.py` | 併進 `stages/validate.py` |
| `sandbox/runner.py` | `stages/sandbox.py` |
| `recon/browser_probe.py` | `clients/browser.py` |
| `models/*.py` | `clients/*.py` |
| `registry/asset_catalog.py`+`assets/catalog.yaml` | **刪除**；已完成爬蟲不是工廠節點，不複製成 recipe |
| `registry/run_logger.py`+`staging.py` | `runs/` 寫入器 + `output/` 管理 |
| `persistence.py` | **刪**（工廠不寫 DB） |
| `runtime_paths.py` | `config.py` |

---

## 任務清單（照順序，每階段可中斷）

### Phase 0 — 綠化基準（先，因為現在是壞的）
- [x] 0.1 現況：`assets/catalog.yaml` 引用已刪的 `crawler_runtime/.../cnyes_finance_spider.py`，`registry/asset_catalog.py:load_assets()` 全域驗證 → 整份炸。實跑確認：`python -m app.spider_forge_system.tests.test_assets`（2/10 過）。
- [x] 0.2 採不改載入邏輯的最小止血 (b)：把 `local.cnyes.anue` 指向合併後的 `cnyes_spider.py`。
- [x] 0.3 用 uv 重建 `workspace/.venv`，新增 pytest 轉接入口並同步過期 fixture；實跑：`131 passed in 11.08s`。
- [x] 0.4 commit：`refactor(sfs): phase0 綠化基準與測試環境`

### Phase 1 — 搭骨架 + 一站走通（walking skeleton）
目的：先讓 `pipeline.py` 用新結構把**一個已知會過的站**端到端跑通，再拆其餘。
- [x] 1.1 建新目錄骨架：`pipeline.py config.py stages/ clients/ output/ runs/`（初期建立但後續證明無效的 recipes 已於 Phase 2 移除）。
- [x] 1.2 寫 `pipeline.py`，各 stage 先做薄 adapter；CLI 與 batch 已改由 `pipeline.py` 進入，pytest 131/131。
- [x] 1.3 `config.py` 先統一路徑與 repair 上限；其餘模型／門檻隨 Phase 2 搬遷收斂，避免同時搬位置又改行為。
- [ ] 1.4 活站驗收：`python -m app.spider_forge_system run --url <一個已知可行 URL>` → 跑完 probe→…→certify，在 runtime artifacts 產出一支 spider，ledger 有一筆。依既有決定由使用者執行 Docker／活站測試。
- [x] 1.5 commit：`refactor(sfs): phase1 pipeline.py 骨架`

### Phase 2 — 拆巨檔進 stages/（真正的搬遷）
每拆一塊就跑一次測試對照基準，紅了停下修。
- [x] 2.1 `graph/nodes.py` 13 節點 → 對應 `stages/*.py`；`pipeline.py` 直接組裝新節點。
- [x] 2.2 EvidencePack 實作移到 `shared/evidence.py`，流程入口留在 `stages/evidence.py`；解析器獨立到 `shared/parsers.py`。
- [x] 2.3 `validators.py` + `topic_gate.py` 已拆為 `stages/validate.py` 與 `shared/quality_rules.py`、`shared/topic.py`。
- [x] 2.4 沙盒、瀏覽器與模型實作已分別搬入 `shared/sandbox.py`、`clients/browser.py`、`clients/*`。
- [x] 2.5 `assets`、registry 與 recipes 全部刪除；沒有可執行價值的候選名單不再參與流程。
- [x] 2.6 `run_logger.py`+`staging.py` 已搬入 `runs/ledger.py` + `output/artifacts.py`；`persistence.py`、`runtime_paths.py` 已刪除。
- [x] 2.7 已刪 `graph/ registry/ sandbox/ recon/ models/ assets/ recipes/`；程式碼對 `crawler_runtime/news_crawler` 引用為 0。
- [x] 2.8 離線驗收：Spider Forge 與 Crawler Runtime 測試合計 `119 passed`；新增架構測試防止節點互相 import 與控制面重新引用執行層。活站驗收仍依 1.4。
- [x] 2.9 commit：`refactor(sfs): phase2 扁平化 + 解耦 crawler_runtime`

### Phase 3 — 方案 A：不花冤枉錢（砍注定失敗的 LLM 花費）
- [x] 3.1 `stages/triage.py`：LLM 前先做可重播端點、文章資料與明確存取失敗的確定性判斷；明確不可行直接 `escalate`。2026-07-30 起 robots 不參與判斷。
- [x] 3.2 `stages/repair.py`：共用 failure taxonomy；付費／挑戰政策、簽章、空探索與純 JS 等不可修復類別立即停止。provider failure 使用獨立有界重試，不消耗程式修復輪數。
- [x] 3.3 `stages/generate.py` + `clients/coder.py`：產碼、一般修復與最終修復 provider 集中在 `config.py`；預設 DeepSeek，第二輪升級 Kimi。
- [x] 3.4 離線驗收：明確無文章資料的入口全程 coder/judge 各 0 次；402 policy failure 第一輪診斷後直接停止。
- [x] 3.5 commit：`0f6f08e feat(sfs): 完成提前停止與真實請求證據`

### Phase 4 — 方案 B：攔真實請求（提升首次成功率＝主戲）
- [x] 4.1 `clients/browser.py` + `shared/evidence.py`：保存已遮密 request method／URL／必要 headers／body，以及 response status／headers／body 樣本。
- [x] 4.2 `shared/materials.py` + `shared/generation.py`：完整 EvidencePack 留在 runtime；coder 只收到已選 `replay_exchange`、精簡 DOM、少量明細與必要契約，不帶未選 feed 或 AXTree 雜訊。
- [x] 4.3 KPI 埋點：ledger 記錄 `first_pass_success`、`repair_count`、`coder_tokens`，彙總輸出成功率、首次成功率與修復總數；每次 EvidencePack 另存 `evidence.json`。
- [ ] 4.4 驗收：對一組測試站點，改造後**首次成功率上升、repair 觸發次數下降**（用 ledger 前後對照，數字寫進 commit/README）。
- [x] 4.5 程式與離線端到端驗收 commit：`0f6f08e`。離線流程一次生成、零修復並成功升版；此結果不冒充 4.4 的多站活測數字。
- [x] 4.6 兩輪逐關實驗正式化：`generate → generation_preflight → fixture_test → sandbox_test`；預檢與保存 response 的 callback 驗證失敗都以結構化證據回到同一個有界修復迴圈。fixture runner 位於 crawler runtime，透過 JSON 子程序契約呼叫，兩層不互相 import。RBA 36/2、加拿大央行 10/2 正式重播通過；根目錄收斂後完整回歸 146/146。

### Phase 5 — 逐步精修迴圈（產出粗胚後，用量化差距逼出細緻度）

**核心原則（別退回 repair 迴圈）**：一支通過 CI 的 spider 常是「會動但粗」（內文截斷／欄位缺／深度不足／混雜訊）。每一次精修 pass 必須由**對真實 response 樣本的確定性 diff（量出來的差距）**驅動，**不是**「叫 LLM 弄漂亮」——後者是 repair 迴圈換皮，燒 API 又不收斂。升級順序永遠：**確定性修 → 升級 recon（含互動攔截）→ 最後才 LLM**。這是把方案 B 從「一次攔」升級成「按量化缺口逐步逼出更深的攔」。

#### 實作前備註（2026-07-29）

- Phase 5 應接在「結構與主題驗證通過」之後、`persist_spider` 正式升版之前；這裡的 CI pass 是候選通過基本閘門，不代表已經發布。粗胚不得先覆蓋正式版本。
- `content_completeness` 只有在 EvidencePack 取得**未截斷的完整明細回應**，且能用 URL 或來源識別碼和輸出 item 一對一配對時才可計算。列表 API、截斷 HTML 或無法配對時必須回 `unknown`，並把「缺完整真值」當成 recon 缺口；不得用不完整樣本算出看似精確的分數。
- 現有候選是任意 Python 程式，不是結構化抽取規格，因此「selector 過窄→自動放寬」不能宣稱是確定性修正。第一版確定性修正只處理有明確證據且可安全轉換的日期格式、headers、已攔截翻頁參數等；selector 修改先升級 recon，仍無法裁決才交給 LLM。若未來要安全自動改 selector，需先引入可驗證的結構化抽取規格或受限轉換器。
- 互動攔截不得任意提交頁面表單。只允許同站、無登入、可辨識為查詢／搜尋／看更多的低風險操作，設動作與等待上限，保存互動前後新增的 request；付款、登入、訂閱、刪除或語意不明的按鈕一律不碰。
- 產碼材料的優先順序為「真實 Network API request/response JSON → 列表與明細的精簡 DOM → AXTree 輔助資訊」。AXTree 適合確認語意結構與辨識互動元件，但缺少可靠 selector 所需的 tag/class/id/datetime 等資訊，不得單獨作為 HTML 解析程式的主要依據。精簡 DOM 只移除 script/style/svg/iframe/template、事件屬性與 inline style，保留 selector 與欄位語意所需屬性。
- Phase 5 不負責修補尚未通過 CI 的產碼錯誤。此前置條件已於 2026-07-30 正式落地：`generate → generation_preflight → fixture_test → sandbox`。預檢檢查瀏覽器傳輸、自包含設定、錯誤分頁上限、時區與高階契約；fixture 以保存的列表／明細 response 執行真 callback。這些失敗回到 generate 修復。只有候選已通過基本執行與結構驗證，仍出現完整度、覆蓋率、深度或雜訊差距時，才進 Phase 5 的量化精修。
- 每次精修紀錄「缺口、採取層級、修改內容、前後指標、token、停止原因」。若無完整真值，對應指標維持 `unknown`；`unknown` 不等於失敗，也不能被當成已達標。

- [ ] 5.1 確定性品質量尺 `shared/quality_metrics.py`：對「抽取輸出 vs Phase 4 攔下的真實 response（evidence.json）」算四個可量指標——
  - `content_completeness`：抽取內文長度 / 真實文章長度 中位（<0.7＝截斷）
  - `field_coverage`：有 title/url/published_at/content 的 item 比例（<0.95＝缺欄位）
  - `depth_reached`：翻頁實際回到的最舊日期 vs 目標 since（差距天數）
  - `noise_ratio`：命中導覽/廣告/相關閱讀樣板的比例（>0.1＝混雜訊）
- [ ] 5.2 精修迴圈 `stages/refine.py`（**CI pass 後才進**）：挑**最大的單一缺口**，按升級順序處置——
  1. 確定性可修（selector 過窄→放寬、日期格式→補 parser）：直接改，**不呼叫 LLM**。
  2. content/depth 缺口且證據不足：**升級 recon**（接 5.3），拿更深/互動觸發的真實請求再重生。
  3. 前兩者都不適用，才走 LLM repair（沿用 `stages/repair.py`，帶入量化缺口當回饋）。
- [ ] 5.3 recon 互動攔截（補 Phase 4 的已知缺口，見 §驗收筆記）：`clients/browser.py` 目前只有 `page.on("response")` 被動載入攔截，**抓不到「表單填寫+送出後才發出」的請求**（例：MOPS `t05st01` 要填公司/年/月按查詢才觸發）。加入受控互動——填偵測到的表單、點查詢/看更多、`wait_for` 後攔截新請求——**由 5.2 的 depth/discovery 缺口觸發**，不是每站都做（省成本）。
- [ ] 5.4 停止條件：某次 pass 未讓任一指標改善（或已達門檻）→ **停**，不跑滿固定次數（死路早停）。每 pass 的 token 成本與指標增益寫進 ledger，供成本/效益判斷。
- [ ] 5.5 驗收：拿一個「一次生成會動但粗」的站（內文只抓到摘要、或只到第一頁）→ 精修迴圈在 ≤2 pass 內把對應指標拉過門檻，且 ledger 可見**確定性修/recon 升級優先於 LLM**。互動攔截用 MOPS 歷史端點驗證（被動載入抓不到、互動後抓得到）。
- [ ] 5.6 commit：`feat(sfs): phase5 measured-gap 精修迴圈 + 互動式攔截`

> 履歷角度：把賣點從「工廠產一支 spider」升級為「產粗胚後，靠**對真實資料的量化回饋**自我精修到 production 品質」——但這句話只有在精修真的是 measurement-driven（非 LLM-vibes）時才站得住。

### Phase 6 — 人機協同逃生口（Human-in-the-Loop）｜⏸ 暫不做（2026-07-30 決定）

**決定：暫不做，未來再議。** 理由：人工開 DevTools 攔請求太麻煩；且「彈 Alert ＋前端框選／貼請求」的前端工作量大，現階段投報不划算。本節只記設計與判準，供未來啟動時直接接手——**勿當待辦執行**。

**若未來做，要點（避免做成錯的版本）：**
- **觸發時機**：自動 repair 用盡「之後」的逃生口。它**不改首次成功率**，只提升「加人後最終成功率」——用 LangGraph `interrupt` + checkpointer 暫停、補 State、resume。
- **人交回的是「真實 request/response」，不是 selector**：最難的失敗是「資料抓不到」（互動觸發／登入／隱藏 API），框 selector 救不了；讓工程師在自己瀏覽器攔請求貼回＝**人力版方案 B**，也是 Phase 5.3 auto-interaction 的更便宜替代。
- **按失敗類別路由**：只有「人補得上就能過」的類別（HTML selector 歧義、互動攔截、登入）才彈 interrupt；真死路（資料不存在、必要簽章不可重播、明確付費牆）走另一種 human 決策或直接 drop，別浪費工程師。
- **觸發率當健康指標**：interrupt 觸發率上升＝前端 recon/evidence 弱的警報，不是「系統能動」；別讓人工逃生口遮掩弱前端。
- **履歷誠實**：報「自動 X% ／加人工 Y%」兩個數字，勿混為一談。
- **成本落點**：`interrupt` 後端輕（還是漂亮的 buzzword）；主要工程量在「Alert ＋活頁面框選／貼請求」的前端 UI——**這正是暫緩主因**。

### 收尾（可選）
- [x] 更新 `README.md`：新架構圖 + pipeline.py 導讀 + 首次成功率 KPI 定義與查詢方式。
- [ ] 套件名 `spider_forge_system`→`spider_forge` 為選配（會動 `crawler_runtime_ingestion` 等 import，risk 較高，最後再做）。
- [ ] 回填記憶 `crawler-runtime-backfill-rework` 的鄰居：新增/更新 spider_forge 記憶指到本檔。

---

## 給接手者的話
- 卡住先讀 §0 核心洞察，別退回「讓模型猜」。
- 每階段獨立可交付；額度不夠就停在階段邊界 commit，下次從下一個未打勾項續。
- 完整架構審查證據（模組職責表/資料流/死碼）當時落在 scratchpad `sfs_review.md`（可能已隨 session 消失），重點已濃縮進本檔 §現況→新位置 與 Phase 0/2。

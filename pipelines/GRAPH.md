# Spider Forge 流程圖（持續更新）

這份圖跟著 `pipeline.py` 走，改流程就改這裡。用 mermaid 是為了可 diff、可版控、
GitHub 直接渲染——draw.io 的 XML 改一次要重排座標，迭代太慢。

**判斷來源的圖例**（顏色即語意，每個節點都標了）：

| 顏色 | 意思 | 成本 |
|---|---|---|
| 🟦 藍 | 程式（確定性，可寫回歸測試） | 免費 |
| 🟩 綠 | Ollama 本地模型 `qwen2.5:7b-instruct` | 免費（不吃 API 額度） |
| 🟨 黃 | Gemini | 付費，少量 |
| 🟥 紅 | DeepSeek／Kimi（產碼） | 付費，主要成本 |
| ⬜ 虛線 | **提案，尚未實作** | — |

---

## 圖一：現況（2026-08-01，對應 commit `8af87da`）

```mermaid
flowchart TD
    START([輸入 URL]) --> prepare

    prepare["prepare_request<br/><small>正規化、補預設、初始化重試計數</small>"]:::prog
    recon["recon<br/><small>Playwright + plain HTTP 雙軌探測<br/>取頁面前 400 個 &lt;a&gt;，DOM 順序</small>"]:::prog
    triage{"feasibility_triage<br/><small>確定性 KILL 判定</small>"}:::prog
    strategy["strategy_decision<br/><small>① 找得到文章連結且無可重播 API → 直接判 dom（程式短路）<br/>② 其餘情況才問模型</small>"]:::mixed
    evidence["collect_evidence<br/><small>_discover_detail_urls 按 DOM 順序取 <b>2</b> 個當明細樣本<br/>⚠️ 沒有 article_url_patterns 時全放行</small>"]:::progwarn
    generate["generate_spider<br/><small>依編譯後材料產出單檔 spider</small>"]:::paid
    preflight{"generation_preflight<br/><small>AST 靜態契約檢查</small>"}:::prog
    fixture{"fixture_test<br/><small>子程序離線重播保存的 response</small>"}:::prog
    sandbox["sandbox_test<br/><small>scrapy runspider 實跑（隔離）</small>"]:::prog
    blockgate{"content_block_gate<br/><small>確定性字樣比對；只有部分命中才問 Gemini</small>"}:::mixedg
    validate["validate_output<br/><small>欄位品質、數量、去重、時效</small>"]:::prog
    topic{"topic_gate<br/><small>主題相關性（預設 off）</small>"}:::gemini
    diagnose{"diagnose_failure<br/><small>provider／preflight／fixture／block 錯 → 確定性分類<br/>只有 sandbox／validation 的未知錯誤才問模型</small>"}:::mixed
    repair["repair_code<br/><small>第 1 輪 DeepSeek</small>"]:::paid
    repairk["repair_code_kimi<br/><small>第 2 輪換 Kimi</small>"]:::paid
    persist["persist_spider<br/><small>原子升版，可回滾</small>"]:::prog
    escalate["escalate_human<br/><small>非阻塞死信歸檔</small>"]:::prog

    prepare --> recon --> triage
    triage -->|"KILL_*<br/>policy／auth_required／<br/>signature／js／discovery_empty"| escalate
    triage -->|FEASIBLE_*| strategy
    strategy --> evidence --> generate --> preflight

    preflight -->|passed| fixture
    preflight -->|failed| diagnose
    fixture -->|passed| sandbox
    fixture -->|failed| diagnose
    sandbox --> blockgate
    blockgate -->|是內容| validate
    blockgate -->|是封鎖頁| diagnose
    validate --> topic
    topic -->|通過| persist
    topic -->|"主題服務不可用<br/>且 mode=enforce"| escalate
    topic -->|未通過| diagnose

    diagnose -->|"KILL 類"| escalate
    diagnose -->|"retry > max_retries"| escalate
    diagnose -->|"retry < 2"| repair
    diagnose -->|"retry >= 2"| repairk
    repair -.->|"⚠️ 迴圈只重寫程式碼<br/>evidence_pack 原封不動"| preflight
    repairk -.-> preflight

    persist --> DONE([成功])
    escalate --> DEAD([死信])

    classDef prog fill:#dbeafe,stroke:#2563eb,color:#1e3a5f
    classDef progwarn fill:#dbeafe,stroke:#dc2626,stroke-width:3px,color:#1e3a5f
    classDef mixed fill:#dcfce7,stroke:#16a34a,color:#14532d
    classDef mixedg fill:#fef9c3,stroke:#ca8a04,color:#713f12
    classDef gemini fill:#fef9c3,stroke:#ca8a04,color:#713f12
    classDef paid fill:#fee2e2,stroke:#dc2626,color:#7f1d1d
```

### 現況的兩個已證實缺陷

**① 發現階段沒有任何驗證**（紅框那格）。`_discover_detail_urls` 按 DOM 順序取前 2 個
連結當「文章明細樣本」。HTML 的導覽列必然排在內文之前，所以零設定跑任何新站，
拿到的都是導覽連結。BBC 實測：前 25 筆全是導覽，真文章在第 26–30 筆。

**② 修復迴圈修不了這個錯**（虛線那條）。`repair.py` 的修復 prompt 用的是
`compile_generation_materials(state["evidence_pack"])`——**同一份錯誤證據**。
模型被要求「修正 selector」，但它看到的樣本本來就不是文章。BBC 兩輪修復
都死在這裡，而診斷把它歸成 `selector_schema`（selector 錯）——**歸因是錯的**。

---

## 圖二：提案（發現階段變成有驗證的子圖）

```mermaid
flowchart TD
    recon["recon<br/><small>候選連結池 400 → 200 → 30</small>"]:::prog

    subgraph DISCOVER["🔁 發現子圖（提案：有自己的驗證與有界重試）"]
        direction TB
        hard{"① 硬性排除<br/><small>入口 URL 自己、#錨點、首頁、跨網域<br/>純結構事實，不需要模型</small>"}:::newprog
        pattern{"② URL pattern 過濾<br/><small>article_url_patterns／excluded<br/>有設定才生效（現有機制）</small>"}:::newprog
        pick["③ 挑「像文章」的連結<br/><small>輸入：URL + 連結文字（&lt;2000 token）<br/>輸出：排序後的候選</small>"]:::newlocal
        sample["④ 抓明細樣本（2–3 篇）"]:::newprog
        verify{"⑤ 樣本驗證<br/><small>兩份樣本雷同？（拿到導覽頁必然雷同）<br/>有 h1／article？正文長度足夠？<br/>純程式，不需要模型</small>"}:::newprog
    end

    recon --> hard --> pattern --> pick --> sample --> verify
    verify -->|"不合格<br/>換下一批候選<br/>（最多 2 輪）"| pick
    verify -->|"重試耗盡"| escalate["escalate_human<br/><small>新分類 KILL_discovery_unusable</small>"]:::newprog
    verify -->|合格| evidence["collect_evidence<br/><small>確認拿到真文章樣本才往後送</small>"]:::prog

    evidence --> generate["generate_spider ⋯ 後續閘門與修復迴圈<br/><small>（維持現狀不動）</small>"]:::paid

    classDef prog fill:#dbeafe,stroke:#2563eb,color:#1e3a5f
    classDef paid fill:#fee2e2,stroke:#dc2626,color:#7f1d1d
    classDef newprog fill:#eff6ff,stroke:#2563eb,stroke-dasharray:5 3,color:#1e3a5f
    classDef newlocal fill:#f0fdf4,stroke:#16a34a,stroke-dasharray:5 3,color:#14532d
```

### 為什麼這樣分三層

| 層 | 手段 | 能解決 | 不能解決 |
|---|---|---|---|
| ① 硬性排除 | 程式 | 錨點、首頁、入口自己 | 分不出「分類頁 vs 文章」 |
| ② URL pattern | 程式（使用者設定） | **哪個版面**的精確控制 | 要先知道 pattern 長什麼樣 |
| ③ 挑文章 | Ollama 本地 | 導覽 vs 文章（新站零設定也能動） | **分不出哪個版面** |
| ⑤ 樣本驗證 | 程式 | 抓到的樣本根本不是文章 | — |

**③ 不能取代 ②。** 實測：四種啟發式與模型都會把足球新聞排進前段——它確實是
文章，只是不是商業版面的。「我要哪個版面」是使用者意圖，不是網頁裡的事實，
沒有任何模型能替你決定。

**⑤ 是這裡最便宜也最有效的一格。** BBC 那次兩份樣本的 body 都是 20000 字且高度
相似（都是同一個列表頁），純程式就抓得出來，連模型都不用呼叫。

### 模型選擇的理由

| 用在哪 | 選擇 | 為什麼 |
|---|---|---|
| ③ 挑文章連結 | **Ollama 本地** | 免費、已在流程裡、任務比它現在做的「策略判斷」還簡單；下游還有五道閘門兜底 |
| ③ 的 fallback | Gemini | 本地判不出來才升級，便宜且已接好 |
| ①②⑤ | 程式 | 純結構事實，用模型是浪費且不可測 |
| — | **不用 DeepSeek** | 那是產碼模型，拿來分類是殺雞用牛刀，而且吃的是付費額度 |

---

## 待辦順序

1. **先跑通一個站拿到成功基準** —— 目前零個成功案例，任何改動都無從判斷好壞
2. 做 ⑤（樣本驗證，純程式）—— 零成本、可測試、能抓到 BBC 這類案例
3. 做 ①（硬性排除，純程式）
4. 做 ③（本地模型挑連結）—— 用步驟 1 的成功案例當回歸基準
5. `run --site <yaml>` 補 CLI 缺口（validation 目前只能從站台 YAML 進入，見
   `batch.py:run_site`；`run` 子命令組不出來）

## 變更記錄

- 2026-08-01：建立。圖一為現況實況（查程式碼確認每個節點的判斷來源），
  圖二為 BBC 實跑失敗後的提案。

# Spider Forge 模組化重構計畫

寫給接手的 session（含未來的我）。自足文件，不依賴產生它的那場對話。
建立：2026-07-31。分支：`refactor/modular-nodes`（基準快照在 `main`，commit `e781f09`）。
每階段一個 commit，可中斷續做。

---

## 0. 這是什麼、目標

**系統**：一台「AI 自動產 Scrapy 爬蟲」的工廠。輸入 URL，經 探測→可行性→產碼→沙盒實跑→驗證→(修復)→認證，輸出一支通過內部檢查的 spider。骨架用 **LangGraph**（`pipeline.py` 的 `StateGraph`）。

**本次重構的目標（使用者定調）**：把它從「為特定專案（台灣財經新聞）寫死的 pipeline」改造成「**通用、積木化的模組**」。核心價值一句話：

> **之後每增加一個節點（想優化的環節），只要在積木目錄新增一個檔，然後在 pipeline 裡拼裝一行，不動其他任何東西。**

這正是 pytorch 的分法：`nn.Linear` 這些積木是**函式庫**，`train.py` 是**管線**，`tests/` 是測試——三者分開。

## 1. 核心設計（三層積木 + 保留 LangGraph）

**不重寫骨架**。LangGraph 的 `add_node`/`add_edge`/`route_after_*` 就是「積木拼裝」機制，寫得很乾淨，保留。只換三層 + 收斂結構：

| 層 | 現況（問題） | 目標 |
|---|---|---|
| **client** | 5 個 client 各寫各的，兩份重複 `.env` loader，模型名散在 3 檔，無 `load_dotenv` | 統一 `LLMClient` class + `registry.get_client(provider)`，`load_dotenv` 只呼叫一次 |
| **schema** | 無 pydantic，全 plain dict，target schema 散在 `prompts.py`/`topic.py`/`page.py` 三處 | `schemas/` 用 pydantic 集中定義；改抓什麼欄位只改一個檔 |
| **node** | 節點是函式 `def node(state)->dict`，設定寫死在函式內 | 節點是 class：`__init__` 存設定/注入依賴、`__call__(state)->dict` 執行 = `nn.Module` 的形狀 |

**函式→class 與 LangGraph 相容的關鍵**：有 `__call__` 的 class instance 可以當函式呼叫，所以 `builder.add_node("recon", Recon(prober=probe))` 直接可用，骨架不動一行。

## 2. 目標目錄結構（根目錄三分）

```
spider_forge/                    repo 根
├── pyproject.toml               套件定義（src layout，套件名 spider_forge）
├── src/spider_forge/            ① 函式庫本身（可重用積木）
│   ├── clients/                 client 工廠：base.py(LLMClient) + registry.py
│   ├── schemas/                 pydantic 集中：outputs.py(資料形狀) + state.py(共享狀態)
│   │                              + strategy/diagnose 等 LLM output schema
│   ├── prompts/                 每個「呼叫 LLM 的節點」一個 prompt 檔（generate.py/
│   │                              strategy.py/diagnose.py/topic.py），node 由 __init__ 注入
│   ├── nodes/                   ★ 節點積木庫 ★ base.py(Node 基類) + recon.py/triage.py/...
│   │                              → 加新節點 = 這裡多放一個檔（用 LLM 的再配一個 prompts/ 檔）
│   ├── shared/                  節點共用 helper（parsers/quality_rules… 非節點、非 prompt）
│   ├── pipeline.py              管線：把積木拼成流程（add_node/add_edge/route）
│   ├── cli.py  __main__.py      入口
│   └── config.py
├── tests/                       ② 測試（與 src 平行，import 裝好的 spider_forge 套件）
├── requirements*.txt  Dockerfile  README.md
└── runtime/                     產物（gitignored）
```

依賴方向單向：**pipeline → 積木**，積木永不 import pipeline，積木彼此不互相 import（只由 pipeline 組裝）。這保證改流程不動積木、改積木不動別的積木。

> 「管線是否獨立成頂層 `pipelines/` 目錄」列為階段 6 的可選項。現況 `pipeline.py` 已是唯一組裝點、已和積木分離；且 `python -m spider_forge` 入口留套件內較標準。預設不獨立拆，除非使用者要。

## 3. 鐵律（違反即停）

1. **綠基準才重構**：每階段動手前先跑 `pytest` 拿基準；紅的基準上不重構。
2. **搬遷+改良，不重寫**：能動的邏輯搬進新位置再改，不砍掉重來。
3. **節點解耦**：所有節點同一介面（收 state、回 state 更新）；節點不互相 import，只由 `pipeline.py` 組裝；跨節點能力放 `shared/`。
4. **每階段 commit**：訊息寫過了哪階段；壞了能 `git reset` 回上一階段。
5. **領域可注入**：台灣財經/新聞站這類領域知識不寫死在核心，抽成可替換設定。

## 4. 階段清單（照順序，每階段可中斷 + commit + 驗收）

### 階段 0 — git 地基 ✅ 已完成
- [x] `git init` + 連結遠端 `github.com/haoagentuse-glitch/SpiderForge.git`
- [x] 強化 `.gitignore`（擋 `.venv`/`.env`）；基準快照 commit `e781f09` 於 `main`
- [x] 建 `refactor/modular-nodes` 分支
- [x] 建 `.venv`、裝 `requirements-test.txt`，核心依賴 import OK

### 階段 1 — 獨立化套件 + 綠基準（本次真正的地基）
**為什麼先做**：146 個測試全寫 `from app.spider_forge_system import ...`，但這是從母專案複製出的獨立副本、沒有 `app/` 母佈局，**現在無法獨立測試**。沒綠基準，後面 class 化無從驗證對錯。這一步同時完成「脫離特定專案 → 通用套件」的第一塊地基。
- [x] 1.1 建 `pyproject.toml`（src layout，套件名 `spider_forge`，依賴引 requirements）
- [x] 1.2 `git mv` 套件內容進 `src/spider_forge/`（tests/ 留根）
- [x] 1.3 批次替換 `app.spider_forge_system` → `spider_forge`（94 處/21 檔全清）
- [x] 1.4 `config.py`：runtime 資料根改為 repo 根（`REPO_ROOT`），`paths` 已驗證指向 repo 根 runtime/
- [x] 1.5 `pip install -e .`；**綠基準 = 138 passed, 1 skipped**（skip=依賴外部 crawler_runtime 的 fixture runner 子程序，待階段6 解耦）
      另修 `test_architecture` 的 `PACKAGE_DIR`（改指 src/spider_forge）以反映新結構
- [x] 1.6 commit：`refactor: 階段1 獨立化套件 + 綠基準`

### 階段 2 — client 層（統一工廠 + load_dotenv）
- [x] 2.1 `clients/env.py`：唯一的 `load_env()`（合併兩份 `_load_env`，不加 python-dotenv 依賴，import 即載入一次）
- [x] 2.2 `clients/registry.py`：`ProviderSpec` + `get_provider(provider)` 集中 deepseek/kimi/gemini 的 (env, model, base_url, api_style)；延遲求值反映 .env 與 monkeypatch
- [x] 2.3 `coder.py`（刪 CoderConfig/_cfg/_load_env）/`topic.py`/`page.py` 改用統一 env + registry；保留各自 complete/classify 呼叫（Gemini 非 OpenAI 相容，不硬統一）
- [x] 2.4 驗收：pytest 138 passed/1 skipped；registry 冒煙（三 provider 設定正確、未知 provider 報錯）
- [x] 2.5 commit：`refactor: 階段2 統一 client 工廠（env + registry）`

> 範圍判斷：`complete()` 未跨 OpenAI 相容(coder)與 Gemini(topic/page)硬統一——兩者 API 結構不同，硬統一風險高且非核心訴求。階段 2 只統一「設定取得 + env 載入」，這才是「乾淨初始化 + 設定集中」的本質。

### 階段 3 — schema 層 + prompt 層（拆解 `shared/prompts.py` 這個雜燴）
`shared/prompts.py` 現在混了 prompt 文字（`CODE_SYSTEM`/`_SPIDER_CONTRACT`，屬 generate）與
schema（`_STRATEGY_SCHEMA`/`_DIAGNOSE_SCHEMA`/`DEFAULT_TARGET_SCHEMA`）。沿兩條軸拆乾淨後這檔消失。
- [x] 3.1 `schemas/outputs.py`：pydantic `Article` + `DEFAULT_TARGET_SCHEMA`（dict 契約保留、向下相容）
- [x] 3.2 `schemas/llm_io.py`：strategy/diagnose output schema 收入。`SpiderForgeState` 留在套件根 `state.py`（核心執行狀態，搬動牽涉大量 import、收益有限，刻意不搬）
- [x] 3.3 `prompts/generate.py`：`CODE_SYSTEM`+`SPIDER_CONTRACT` 搬出、`shared/prompts.py` 消失。strategy/diagnose/topic 的**內嵌** prompt 待階段6（它們正是領域綁定，與領域抽離一起搬）
- [x] 3.4 驗收：pytest 138 passed/1 skipped（3a、3b 各驗）；Article 實例化 + prompts import 冒煙通過
- [x] 3.5 commit：3a `326b888`（schema）+ 3b（prompt，本次）

> 領域槓桿：台灣財經/政策的綁定主要藏在 topic prompt 裡。prompt 拆出可注入後，「換領域＝換一個 prompt 檔」，等於順手做掉階段 6 一大半。

### 階段 4 — Node 基類 + 一個節點 class 化（驗證模式）
- [ ] 4.1 `nodes/base.py`：`Node` 基類（`__call__` 抽象）
- [ ] 4.2 挑一個節點（`recon`）改成 class，`pipeline.py` 塞 instance
- [ ] 4.3 驗收：`pytest` 維持綠 + 該節點的行為測試通過 → **證明模式對，才放大**
- [ ] 4.4 commit：`refactor: 階段4 Node 基類 + recon class 化`

### 階段 5 — 其餘節點 class 化（機械放大，可派 subagent）
- [ ] 5.1 其餘節點照階段 4 模式逐一改 class
- [ ] 5.2 每改一個跑一次 `pytest` 對照基準
- [ ] 5.3 commit：`refactor: 階段5 全節點 class 化`

### 階段 6 — 領域耦合抽離（通用化收尾）
- [ ] 6.1 台灣財經/公共政策 topic labels、site_queue、request_identity 的 zh-TW/課程字樣 → 抽成可注入設定（`domains/` 或 config）
- [ ] 6.2 `DEFAULT_TARGET_SCHEMA` 綁死新聞文章 → 由階段 3 的可換 schema 取代
- [ ] 6.3 驗收：換一個領域設定即可跑非新聞站（最小示範）
- [ ] 6.4 commit：`refactor: 階段6 領域耦合抽離`

## 5. 環境與指令

- Python 3.12，venv 在 `.venv`。啟用後 `pip install -e .`。
- 測試：`.venv/Scripts/python.exe -m pytest tests/`
- 執行：`.venv/Scripts/python.exe -m spider_forge run --url <URL>`（階段 1 後）
- Windows/PowerShell；檢視含中文 JSON 用 `python -X utf8 -c` 印，別直接 Read。
- 活站/Docker 測試依既有決定由使用者手動執行；本計畫的自動驗收以離線 `pytest` 為準。

## 6. 給接手者的話

- 每階段獨立可交付；額度不夠就停在階段邊界 commit，下次從下一個未打勾項續。
- 核心價值檢驗：重構完成後，「加一個節點」是否真的只動「新增一檔 + pipeline 一行」？做不到就是還沒到位。
- 保留 LangGraph 是刻意的——別退回自己造流程引擎。

# Spider Forge

輸入網站 URL，自動偵查資料來源、生成 Scrapy 爬蟲、隔離執行、驗證品質，失敗時最多
進行兩輪定向修復。通過所有檢查的爬蟲才會升版；失敗結果寫入待人工處理紀錄。

流程會先以不呼叫模型的方式檢查可重播端點、文章資料與公開存取狀態。明確不可行時
直接停止；可行時由確定性材料編譯器只保留已選來源、精簡 DOM 與必要契約，再交給
產碼模型，避免模型猜測端點、參數或被無關材料塞滿上下文。

## 快速開始

```bash
uv sync
```

```bash
.venv/Scripts/python.exe -m pytest tests/ -q
```

```bash
.venv/Scripts/python.exe -m pipelines.cli run --url "https://example.com/news"
```

測試全部離線：不碰真實網站、不呼叫外部模型、不消耗 API 額度。`run` 會兩者都做。

## 模組邊界

根目錄先分四塊,對應四種不同的東西:

| 目錄 | 是什麼 | 會不會隨 `pip install` 帶走 |
|---|---|---|
| `src/spider_forge/` | **函式庫**——積木與契約 | ✅ 會 |
| `pipelines/` | **管線**——把積木拼成流程 + CLI | ❌ 不會（這是本 repo 的應用程式碼）|
| `tests/` | **測試** | ❌ 不會 |
| `Phoenix/` | **觀測服務**（compose）| ❌ 不會 |

這正是 pytorch 的分法:`torch` 是函式庫,你的 `train.py` 是自己的程式。
`pip install spider_forge` 拿到的是積木,管線自己組——所以**依賴方向永遠是
`pipelines → spider_forge`,反過來絕不允許**（有測試鎖住,一旦違反套件就裝不起來）。

函式庫內部:

- `nodes/` 是節點積木庫：一個節點一個 class（`__init__` 存設定 / `__call__` 執行）。
  加新節點 = 這裡多一個檔 + `pipelines/pipeline.py` 拼裝一行；節點不得互相 import（有測試鎖住）。
- `schemas/` 是資料形狀的唯一來源：`outputs.py` 定義要抓什麼欄位（pydantic `Article`），
  `llm_io.py` 是模型輸出的 schema。改抓取欄位只改這裡。
- `prompts/` 每個呼叫 LLM 的節點一個 prompt 檔，由節點注入。
- `clients/` 封裝瀏覽器與模型服務；`registry.py` 是 provider（模型/金鑰/base_url）的唯一設定點。
- `shared/` 放多個節點共同使用的解析、品質規則及領域服務。
- `sandbox_runtime/fixture_runner.py` 是離線重播引擎，只在**沙盒子程序**內以檔案路徑執行，
  只吃 JSON fixture 契約、不 import 控制層；控制層也不 import 它。
  想換成別的重播引擎：`SPIDERFORGE_FIXTURE_RUNNER=<module>`（+ `..._CWD`），遵守同一份契約即可。
- `observability/` 追蹤的啟用與 LLM span（沒設 Phoenix endpoint 就是完全的 no-op）。
- `output/` 管理候選、正式版本、歷史版本及人工回滾。
- `runs/ledger.py` 管理追加式執行紀錄。
- `config.py` 是設定與執行期路徑的唯一來源。

管線層 `pipelines/`:`pipeline.py` 是唯一流程組裝入口（節點順序與所有分支都在這裡）、
`batch.py` 是批次執行器、`cli.py` 是命令列入口。

`state.py` 把流程狀態分成三層：`ForgeInput`（呼叫端該給的）、`ForgeInternal`（節點間中間態）、
`ForgeOutput`（一次執行要交出的產出）。graph 入口只收 `ForgeInput`，`forge_result()` 只回產出。

候選爬蟲是自包含單一檔案，會在檔內定義 `ArticleItem`。本機沙盒直接以
`scrapy runspider` 執行；設 `SPIDERFORGE_CRAWLER_RUNTIME=docker` 則把程式經標準輸入送進
獨立、唯讀且無機密資料的 crawler 容器。

robots 不參與 Spider Forge 的可行性判斷、產碼契約或拒絕路由。付費牆、CAPTCHA 與
登入牆仍是不繞過的硬界線（見 `shared/request_identity.py`）。

## 流程

```mermaid
flowchart TD
    A["輸入 URL"] --> B["prepare_request<br/>整理請求"]
    B --> C["recon<br/>HTTP + Playwright 偵查"]
    C --> D["feasibility_triage<br/>確定性可行性判斷"]
    D -->|不可行| X["escalate_human<br/>寫入待處理紀錄"]
    D -->|可行| E["strategy_decision<br/>選擇 API／HTML／Hybrid"]
    E --> F["collect_evidence<br/>建立 EvidencePack"]
    F --> G0["materials compiler<br/>只留已選來源與精簡 DOM"]
    G0 --> G["generate_spider<br/>一次生成完整候選"]
    G --> P["generation_preflight<br/>靜態契約檢查"]
    P --> Q["fixture_test<br/>保存 response 離線重播"]
    Q --> H["sandbox_test<br/>活站隔離執行"]
    H --> I["content_block_gate<br/>攔截錯誤頁"]
    I --> J["validate_output<br/>確定性品質驗證"]
    J --> K["topic_gate<br/>主題驗證（預設 off）"]
    K -->|通過| L["persist_spider<br/>原子升版"]
    K -->|未通過| M["diagnose_failure<br/>診斷"]
    M -->|仍可修復| N["repair_code<br/>定向修復"]
    N --> P
    M -->|額度用盡或不可修| X
```

不可修復的失敗類別不會跑滿兩輪修復。一般產碼與第一輪修復預設使用 DeepSeek，第二輪
才升級至 Kimi。

產碼維持單次完整輸出，不採兩次生成後組裝。材料過量先由可重現的程式規則處理：
移除 script/style/svg 等雜訊、裁切 DOM、限制樣本數、排除未選 feed。

## 目錄

```text
spider_forge/                    repo 根
├── pyproject.toml               套件定義（只收 src/）+ uv.lock
├── src/spider_forge/            ① 函式庫（積木）
│   ├── nodes/                   ★ 節點積木庫（17 個節點，一節點一檔）
│   ├── schemas/                 資料形狀（outputs.py / llm_io.py）
│   ├── prompts/                 各節點的 prompt
│   ├── clients/                 browser / coder / judge / page / topic + registry + env
│   ├── shared/                  節點共用 helper 與領域服務
│   ├── sandbox_runtime/         沙盒子程序內執行的離線重播引擎
│   ├── observability/           Phoenix 追蹤（客戶端；沒設定就 no-op）
│   ├── output/  runs/  tools/
│   └── config.py  state.py
├── pipelines/                   ② 管線（不隨套件安裝）
│   ├── pipeline.py              唯一組裝點：節點順序與所有分支
│   ├── batch.py                 批次執行器
│   └── cli.py  __main__.py      命令列入口
├── tests/                       ③ 測試（含 manual/ 人工逐關工具）
├── Phoenix/                     ④ 觀測服務（docker compose）
├── examples/                    站台清單範例
└── runtime/                     執行期產物（gitignored）
```

執行期資料不放在原始碼目錄，預設在 repo 根的 `runtime/`，可用 `SPIDERFORGE_DATA_DIR` 改：

```text
runtime/
├── requests/
├── runs/<run_id>/
├── artifacts/{candidates,active,versions}/
├── records/{runs.jsonl,promotions.jsonl,dead_letter/}
└── models/
```

## 設定

金鑰放 `.env`（見 `.env.example`）或作業系統環境變數：

| 變數 | 用途 |
|---|---|
| `DEEPSEEK_API_KEY` | 初次產碼與一般修復 |
| `KIMI_API_KEY` | 只有進入最後一輪 Kimi 修復時才需要 |
| `GEMINI_API_KEY` | Gemini（主題閘門、內容真偽確認）|
| `OLLAMA_HOST` | 選用；預設 `http://localhost:11434` |

常用行為開關（全部可省略）：

| 變數 | 預設 | 說明 |
|---|---|---|
| `SPIDERFORGE_SITE_QUEUE` | `examples/site_queue.taiwan-finance.yaml` | 批次要跑的站台清單 |
| `SPIDERFORGE_DATA_DIR` | `<repo>/runtime` | 執行期產物根目錄 |
| `SPIDERFORGE_TOPIC_MODE` | `off` | 主題閘門：off / shadow / enforce |
| `SPIDERFORGE_GENERATION_PROVIDER` | `deepseek` | 產碼供應商 |
| `SPIDERFORGE_REPAIR_PROVIDER` | `deepseek` | 第一輪修復供應商 |
| `SPIDERFORGE_FINAL_REPAIR_PROVIDER` | `kimi` | 最後一輪修復供應商 |
| `SPIDERFORGE_CRAWLER_RUNTIME` | `local` | 沙盒執行方式：local / docker |
| `SPIDERFORGE_FIXTURE_RUNNER` | 內建 | 換掉離線重播引擎 |
| `SPIDERFORGE_USER_AGENT` / `_ACCEPT_LANGUAGE` / `_REQUEST_PURPOSE` | 見 `request_identity.py` | 請求身分（單一固定，不輪替）|

## 執行

試跑前先檢查環境（不呼叫外部 API、不驗證金鑰有效性，只看該有的東西在不在）：

```bash
.venv/Scripts/python.exe -m pipelines.cli doctor --profile finance
```

它會檢查：金鑰（依實際設定的 provider）、Playwright chromium 是否下載、Scrapy、
Ollama 與 judge 模型、Phoenix、站台清單、runtime 可寫。`FAIL` 代表會直接擋住試跑,
`WARN` 代表能跑但有疑慮。

```bash
.venv/Scripts/python.exe -m pipelines.cli run --url "https://example.com/news" --max-retries 0
```

```bash
.venv/Scripts/python.exe -m pipelines.cli batch
```

```bash
.venv/Scripts/python.exe -m pipelines.cli status
```

`run` 可重複 `--url`，或用 `--file` 給每行一個 URL 的檔案；`batch` 後面接
`source_prefix` 只跑指定來源；`paths` 只印資料位置。

### 領域設定檔（profile）

**管線只有一條**，換領域不複製管線、只換一組設定（見 `pipelines/profiles.py`）：

```bash
.venv/Scripts/python.exe -m pipelines.cli batch --profile finance
```

| profile | 差異 |
|---|---|
| `general`（預設）| 不預先決定「什麼主題才算合格」，主題閘門 off |
| `finance` | 主題閘門 `enforce`：非財經/公共政策的文章擋下（需 `GEMINI_API_KEY`，會消耗額度）|

站台 YAML 或 CLI 明給的值**永遠優先於 profile**，所以單站例外不必另開一份 profile。
加新領域 = `profiles.py` 多一個 dict。

當函式庫用：

```python
from pipelines.pipeline import forge_spider

result = forge_spider("https://example.com/news", max_retries=0)
print(result["status"], result.get("spider_path"))
```

想自己組流程（換節點順序、加一關），就照 `pipelines/pipeline.py` 的寫法用積木拼：

```python
from langgraph.graph import START, StateGraph
from spider_forge.nodes import Recon, PrepareRequest
from spider_forge.state import SpiderForgeState, ForgeInput

builder = StateGraph(SpiderForgeState, input_schema=ForgeInput)
builder.add_node("prepare_request", PrepareRequest())
builder.add_node("recon", Recon())
builder.add_edge(START, "prepare_request")
```

### 逐關人工審查

`tests/manual/run_one_stage.py` 一次只跑一關，輸入是前一關的完整狀態 JSON，
刻意沒有「一次跑完」模式：

```bash
.venv/Scripts/python.exe tests/manual/run_one_stage.py prepare --input tests/manual/rba_request.json --output runtime/manual/01_prepare.json
```

`recon`、`evidence` 會接觸真實網站，`strategy` 可能使用 Ollama，`generate` 會使用
DeepSeek；`preflight` 與 `fixture` 是離線檢查。

## 可觀測性（Arize Phoenix，選用）

安裝並啟動 Phoenix（Windows 要先開 Docker Desktop）：

```bash
uv sync --extra observability
```

```bash
docker compose -f Phoenix/compose.yaml up -d
```

容器起來後 UI 在 <http://localhost:6006>；`cli doctor` 會告訴你連不連得上。

在 **repo 根的 `.env`** 設 `PHOENIX_COLLECTOR_ENDPOINT=http://localhost:6006/v1/traces`,
之後 `pipelines.cli run` 就會把 trace 送進 <http://localhost:6006>。**沒設這個變數時完全不啟用**
（不 import Phoenix、零開銷），所以不裝觀測套件也照樣跑。

兩份 `.env` 按**讀者**分工，不要互搬：

| 檔案 | 讀者 | 放什麼 |
|---|---|---|
| repo 根 `.env` | `spider_forge` 程式 | `PHOENIX_COLLECTOR_ENDPOINT` / `PHOENIX_PROJECT` / `PHOENIX_API_KEY` / trace 開關 |
| `Phoenix/.env` | `docker compose` | 服務端：埠、保留天數、映像版本（全部選填，見 `Phoenix/.env.example`）|

⚠️ 客戶端變數放進 `Phoenix/.env` 會**靜默失效**：套件的 `.env` loader 只往上層目錄搜尋，
`Phoenix/` 是子目錄，程式讀不到。

Phoenix container 的環境只有 `compose.yaml` 裡明列的變數（沒有 `env_file`、沒掛載 repo），
所以它拿不到任何 LLM 金鑰；候選爬蟲的沙盒子程序同樣走白名單，也拿不到。

看得到的東西：

- 每個節點一個 span（耗時、進出 state、失敗的例外堆疊）—— 由
  `openinference-instrumentation-langchain` 自動產生，**節點程式碼不需要任何改動**，
  所以「加新節點」仍然只要新增一檔。
- 每次 LLM 呼叫一個子 span（provider、model、prompt、回覆、token、重試次數）——
  這些呼叫是 clients 層自己用 requests 打的，instrumentor 抓不到，所以手動包在
  `observability.llm_span` 裡。

| 變數 | 預設 | 說明 |
|---|---|---|
| `PHOENIX_COLLECTOR_ENDPOINT` | 無 | 沒設就不啟用追蹤 |
| `PHOENIX_PROJECT` | `spider_forge` | Phoenix 專案名 |
| `SPIDERFORGE_TRACE_CONTENT` | `1` | 設 `0` 只記 token/耗時，不送 prompt 與 state 內容 |
| `SPIDERFORGE_TRACE_MAX_CHARS` | `4000` | 單一欄位長度上限（state 帶 DOM，不設限會爆量）|

## 證據與執行指標

`EvidencePack.replay_exchange` 保存已遮密的請求（method/URL/headers/body）與
回應（狀態碼/headers/body 樣本/是否截斷）。批次執行把每次 EvidencePack 寫入
`runs/<run_id>/evidence.json`，並在 `records/runs.jsonl` 記錄 `first_pass_success`、
`repair_count`、`coder_tokens`。

```bash
.venv/Scripts/python.exe -m spider_forge.runs.ledger
```

`first_pass_rate` 的分母是各來源最後一次執行的總站數，不只計算最後成功的站，避免把
失敗站排除後高估首次成功率。離線驗收只能證明指標與流程正確；真實站點的改善幅度仍須
用同一批站點在改造前後各跑一次才能成立。

## 外部服務

- Playwright：網站與網路請求偵查。
- Ollama：本機策略判斷與診斷，透過 HTTP。
- DeepSeek／Kimi：程式生成與修復。
- Gemini：主題判定與內容真偽確認（主題閘門預設 off）。

重構進度與設計決策見 [`REFACTOR_PLAN.md`](REFACTOR_PLAN.md)。

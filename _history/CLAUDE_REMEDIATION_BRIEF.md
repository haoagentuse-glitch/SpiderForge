# 給 Claude 的 Spider Forge 修復交接書

> 日期：2026-07-26  
> 對象：接手 `spider_forge_system` 的 Claude  
> 性質：成功率導向的修復規格，不是對既有 `HEALTH_CHECK.md` 的附和  
> 審核範圍：`workspace/backend/app/spider_forge_system`  
> 目前分支：`feat/spider-forge-system`

## 0. 先讀這段

你的任務不是替現有報告辯護，也不是先重構 `graph.py`。你的首要任務是讓系統不能再把錯資料標成成功，並讓任何候選 spider 的失敗都不會：

1. 覆寫上一版可用 spider；
2. 洩漏主機 secrets；
3. 污染正式資料庫；
4. 被帳本誤算成成功。

`HEALTH_CHECK.md` 只能當症狀紀錄，不能當真實來源。所有結論必須以程式碼、固定 fixture、實跑輸出或正式外部來源為準。

## 1. 工作樹保護

開始修改前先執行：

```powershell
git branch --show-current
git status --short --untracked-files=all
```

審核當下已有以下使用者變更，不得還原、覆蓋或順手格式化：

```text
 M workspace/backend/app/spider_forge_system/news_crawler/news_crawler/spiders/cnyes_spider.py
?? workspace/backend/app/spider_forge_system/HEALTH_CHECK.md
?? workspace/backend/app/spider_forge_system/news_crawler/news_crawler/spiders/cna_spider.py
?? workspace/backend/app/spider_forge_system/news_crawler/news_crawler/spiders/ctee_spider.py
?? workspace/backend/app/spider_forge_system/news_crawler/news_crawler/spiders/moneydj_spider.py
?? workspace/backend/app/spider_forge_system/news_crawler/news_crawler/spiders/udn_spider.py
?? workspace/scripts/test_json.py
```

尤其不可直接跑會重寫 `cnyes_spider.py` 的現行 graph。若必須測試生成流程，先完成候選隔離。

## 2. 審核總結

### 2.1 三態判定

- **VERIFIED**：五支 spider 都能被 Scrapy import，LangGraph 也能成功建構。
- **REFUTED**：`HEALTH_CHECK.md` 所稱「可用 spider 2 支」不成立。
- **UNVERIFIED**：正式 DB 寫入、長期新聞覆蓋率、改版後存活率及目前外站 WAF 行為。

目前不是 40% 成功率。依原規格的內容品質門檻，現況是 **0/5 被證明可上線**：

| 站點 | 審核結果 | 證據 |
|---|---|---|
| CNYES | 部分可用，未達完整門檻 | 20 筆中 5 筆 `content` 為空 |
| UDN | **假成功** | 12 筆只有 6 個唯一 URL，全部是股價指數頁，不是新聞 |
| CNA | 失敗 | 產出無正文/日期且目標分類漂移 |
| CTEE | 失敗 | 0 item；Playwright 路線缺少可靠等待與 challenge 分類 |
| MoneyDJ | 失敗 | 0 item；重試後 escalated |

「0/5 被證明可上線」不等於五站永遠抓不到；意思是目前沒有任何一支通過可信、可重現的上線證據。

## 3. 已驗證的 P0 問題

### P0-1：驗證器會放行錯語料

位置：`graph.py:172-223`

現行 `validate_output` 只檢查：

- item 數量至少 3；
- title/url 非空；
- 80% `published_at` 可由 `datetime.fromisoformat()` 解析。

它沒有檢查：

- URL 是否為絕對 HTTP(S) URL；
- URL host 是否屬於允許網域；
- URL 是否為文章頁；
- canonical URL 與唯一率；
- title/content 是否為新聞；
- content 是否為空或導覽雜訊；
- 日期是否具時區且位於合理年份/新鮮度窗口；
- 站點分類是否符合 `site_queue`；
- 阻擋頁、登入牆、CAPTCHA 或 soft block；
- DB 實際寫入筆數。

本機 kill-test 使用 3 筆完全相同的資料：

```json
{
  "title": "首頁",
  "url": "not-a-url",
  "content": "",
  "published_at": "2026-07-26"
}
```

現行結果仍為：

```json
{
  "pass": true,
  "item_count": 3,
  "issues": []
}
```

UDN 的 `runs.jsonl` 紀錄為 success，但 `udn_spider.py:11,38-55` 實際呼叫股票排行 API，產出道瓊、標普、那斯達克等指數頁。這是已證明的假成功，不是推測。

### P0-2：候選碼在驗證前覆寫正式 spider

位置：`graph.py:57-75,99-103`

`sandbox_test` 會先把未知 code 寫入正式 `spiders/`，才執行 syntax/crawl test。若生成失敗、語法錯誤、timeout 或 escalated，壞檔仍留在正式路徑；`persist_spider` 只是再寫一次，沒有真正 promotion。

需要改成：

```text
candidate/<run_id>/<site>_spider.py
  → syntax/import/fixture/live-canary
  → quality gate
  → atomic promotion
  → active/<site>_spider.py
```

任何候選失敗都必須保持 active spider 的 hash 不變。

### P0-3：現行 sandbox 洩漏完整環境

位置：`sandbox/runner.py:54-65`

`run_scrapy_crawl` 使用 `{**os.environ, ...}`，因此候選碼可讀到：

- `DATABASE_URL`
- DeepSeek/Kimi API keys
- 其他主機環境變數

本機 mock 已驗證：

```text
database_url_leaked=True
coder_key_leaked=True
allowed_domains_value=None
```

`SPIDERFORGE_ALLOWED_DOMAINS` 沒有在實際 graph 路徑中落實；model 自己產生的 `allowed_domains` 也不是安全邊界，未知 Python 仍可直接使用 `requests`、socket 或 subprocess。

### P0-4：DB pipeline 可因一筆壞資料整批歸零

位置：

- `news_crawler/news_crawler/pipelines.py:15-32`
- `persistence.py:54-88`
- `workspace/backend/app/db/session.py:48-55`

現行 pipeline 把全部 item 留在記憶體，等 `close_spider` 才用同一 transaction 寫入。`normalize_article` 不驗證 `published_at`，一筆非法日期就可能 rollback 整批；程序 timeout/kill 前未 close 也會零寫入。

另有三項資料恢復問題：

1. URL 未 canonicalize，追蹤參數/fragment 會形成不同 article ID；
2. `ON CONFLICT DO NOTHING` 讓首抓錯誤永遠不能被後續好資料修正；
3. `str(None)` 會把缺值轉成合法字串 `"None"`。

### P0-5：成本與成功率帳本不可信

位置：

- `run_batch.py:41-67,77-85`
- `registry/run_logger.py:14-53`
- `models/coder_client.py:32-40`

例外路徑不執行 `drain_usage()`，前一站已成功的 coder calls 可能被算到下一站。因此「MoneyDJ 單站 22,495 tokens」不能視為已驗證事實。

現行 registry 也缺少：

- `run_id` / `thread_id`
- git commit / candidate code hash
- recipe/recon/fixture hash
- discovery/retrieval/extraction/quality 各階段計數
- failure class
- Scrapy finish reason/stats
- DB attempted/inserted/quarantined count
- provider error與 crawl error 的區分

## 4. 已驗證的架構問題

### 4.1 Recon 證據不足

位置：

- `recon/browser_probe.py:36-83`
- `graph.py:150-169,257-307`

目前只留下 response URL、method、content-type，之後 strategy 又只取得 URL。以下資訊全部遺失：

- request method/body；
- 必要 headers；
- cookie/session 前置條件；
- redirect chain；
- response body/schema/sample；
- block/challenge fingerprint；
- DOM HTML。

generator 之後對候選 API 一律用普通 GET 重新抓，需 POST、cookie、簽章或動態參數的 API 因而註定失敗。

ARIA accessibility tree 可用於理解互動語意，但不能作為 CSS/XPath DOM selector 的可靠來源。HTML 首建至少要保存經清理的 DOM/HTML fixture。

### 4.2 Hybrid 名存實亡

位置：`graph.py:340-399`

`strategy="hybrid"` 會落入 `generate_axtree`，沒有 hybrid generator。strategy confidence 也沒有控制任何路由或人工門檻。

請選一個：

1. 真正實作 hybrid recipe；
2. 在實作前從 schema 移除 `hybrid`。

不可保留一個行為與名稱不一致的選項。

### 4.3 修復迴圈沒有新增觀測

位置：`graph.py:227-250,269-361`

問題包括：

- coder provider error 被 `_safe_generate` 吃掉，`_err` 未寫回 state；
- provider 失敗後可能拿舊 code 再測一次；
- error signature 依賴 LLM 每次產生完全相同字串；
- repair prompt 沒有原始 `site_url`、chosen API、recon、response/item fixture；
- 失敗後沒有回到 recon/replan 的邊。

CNA 已出現實例：`site_queue.yaml` 指向財經 `/list/afe.aspx`，生成 spider 卻漂移到 `/list/aie.aspx`。

### 4.4 Checkpoint 無法真正續跑

位置：

- `graph.py:403`
- `run_batch.py:41-50`
- `requirements.txt:10`

requirements 雖宣告 SQLite checkpointer，實際使用的是 `InMemorySaver`。thread ID 沒寫入 registry，也沒有 resume CLI/API；human interrupt 或程序重啟後無法續跑。

LangGraph checkpoint 與 Scrapy request queue 是兩層不同的恢復問題：

- graph state：SQLite/Postgres checkpointer；
- crawl scheduler/dupefilter：Scrapy `JOBDIR` 或明確 cursor/watermark。

兩者都必須測試，不能把其中一個當成全部的斷點續傳。

## 5. 目標架構

```text
入口發現
RSS / sitemap / 已觀測 API / JSON-LD / HTML listing
        ↓
有界取回梯子
Scrapy HTTP
  → 經授權站點的 transport fingerprint A/B
  → 選擇性 Playwright
  → 經預算與法遵批准的受控服務
  → quarantine / human
        ↓
獨立抽取
API recipe / JSON-LD / 站點規則 / Trafilatura fallback
        ↓
品質閘門
schema + domain + canonical URL + 唯一率 + 新聞語意 + 日期/正文合理性
        ↓
candidate canary
        ↓
atomic promotion → last-known-good
```

### 核心設計決定

優先讓 LLM 產生受 JSON Schema 約束的 `SiteRecipe`，不要直接產生任意 Python。

建議的 recipe 至少包含：

```yaml
site:
  source_prefix:
  allowed_domains:
discovery:
  kind: rss | sitemap | api | html
  request:
    method:
    url:
    safe_headers:
    body_template:
  list_path:
  article_url_path:
  pagination:
retrieval:
  profile: scrapy_http | impersonate | playwright
extraction:
  title:
  content:
  published_at:
  canonical_url:
validation:
  article_url_patterns:
  excluded_url_patterns:
  min_content_chars:
  max_age_days:
```

只有 schema 無法表達的少數來源，才允許經人工審核的 Python escape hatch。

## 6. 修復順序

### Phase P0-A：先消滅假成功

建立固定 fixtures 與測試。最低驗收：

1. 現有 UDN output 必須被拒絕為 `wrong_content_type` 或同等確定性類別；
2. 重複的 3 筆假 item 不得通過；
3. invalid/foreign-domain URL 不得通過；
4. CNYES 空 content item 必須被拒絕或 quarantine，不得計入 valid item；
5. CNA 重複 item 不得灌高成功門檻；
6. 1785 年等不合理日期不得通過；
7. `None` title/url 必須被拒絕。

成功應拆成四個布林值：

```text
discovery_ok
retrieval_ok
extraction_ok
quality_ok
```

只有四者都成立且 valid unique item 達門檻，站點才可標 `success`。

### Phase P0-B：候選隔離與 atomic promotion

必須新增實跑測試證明：

1. candidate syntax error 不改 active hash；
2. candidate timeout 不改 active hash；
3. quality gate fail 不改 active hash；
4. promotion 中斷後 active 仍是完整檔案；
5. promotion 紀錄含 previous/new code hash，可回滾。

在此階段完成前，不可用現行 graph 重跑工作樹中的站點。

### Phase P0-C：sandbox 最小安全邊界

候選程序只能收到明確 allowlist 的環境變數：

```text
PATH
SYSTEMROOT
PYTHONUTF8
PYTHONIOENCODING
SPIDERFORGE_SANDBOX
SPIDERFORGE_ALLOWED_DOMAINS
```

不得帶入 DB 或模型 API keys。

網域限制必須由可信設定或外層 egress policy 強制，而不是相信生成碼的 `allowed_domains`。Windows 上若無法證明完整 process-tree、filesystem、memory 與 network 隔離，就明確標 `UNVERIFIED`，不要把 subprocess+timeout 稱為安全 sandbox。

AST import allowlist 可以當早期拒絕器，但不是安全邊界。

### Phase P0-D：資料列驗證與 quarantine

在 DB 前加入正式資料契約。至少包含：

- canonical absolute URL；
- source host；
- title/content 非假值；
- timezone-aware `published_at`；
- 合理日期區間；
- content hash；
- validation flags；
- extraction/retrieval strategy；
- source/recipe version。

壞列送 quarantine 並保留失敗證據，不得用「無標題」或假時間補成通過。

`ON CONFLICT` 更新策略必須先回答：

1. 什麼情況允許用較好內容修正舊列？
2. 已進入 vectorized/clustered 的資料修正後是否需要重新處理？
3. 哪些欄位不可被較低品質 run 覆蓋？

不要直接把 `DO NOTHING` 改成無條件 UPDATE。

### Phase P1-A：重寫 recon 契約

Recon 應輸出可重播且已去敏感的證據：

- request method/url/body template；
- 必要安全 headers；
- response status/content-type；
- JSON schema/sample；
- canonical/redirect；
- 經清理的 DOM fixture；
- challenge/block signature；
- 探測時間與 Playwright/browser 版本。

API candidate 必須用內容結構評分，不得只讓 LLM 看 URL 猜。

### Phase P1-B：確定性 failure taxonomy

至少區分：

```text
policy_disallowed
auth_required
transport_timeout
dns_or_tls
rate_limited
hard_block
soft_block_200
js_required
discovery_empty
wrong_content_type
schema_changed
extraction_empty
quality_rejected
persistence_failed
provider_failed
```

每個類別只能觸發預先允許的 action：

- timeout/5xx/429：有限 retry、Retry-After、backoff+jitter；
- JS required：Playwright；
- wrong endpoint/schema：回 recon/replan；
- 401/403/CAPTCHA/login/robots：停止或人工，不得盲目升級；
- provider failure：記錄 provider error，不得重跑舊 code 冒充 repair。

為整個 site run 設 wall-clock、request、browser-seconds、LLM calls/tokens 的總預算與 circuit breaker。

### Phase P1-C：watermark/backfill

原生成契約與五支 spider 都把 `max_pages=2` 寫死。這只能作 demo，不能作 production coverage。

正式策略應支援：

- `last_seen_published_at` 或 stable article ID watermark；
- 中斷後 backfill；
- 每站最大回溯窗口；
- 頁數/時間/重複率三種停止條件；
- canonical URL 跨 run 去重。

## 7. 現成工具採用邊界

### 先使用

1. **Scrapy 原生 Stats / Signals / Retry / CloseSpider / JOBDIR**  
   用來建立階段漏斗、早退與 request queue 恢復。  
   官方文件：
   - <https://docs.scrapy.org/en/latest/topics/stats.html>
   - <https://docs.scrapy.org/en/latest/topics/signals.html>
   - <https://docs.scrapy.org/en/master/topics/jobs.html>

2. **Pydantic + Item Pipeline**  
   做型別與跨欄位資料契約，不能只靠 `scrapy.Item` 欄位名稱。  
   <https://docs.scrapy.org/en/latest/topics/items.html>

3. **Spidermon**  
   做 quality monitor、item count/drop、finish reason 與退化告警。它不會修 spider，但能阻止壞資料默默上線。  
   <https://spidermon.readthedocs.io/en/latest/>

4. **Trafilatura**  
   只負責已取回文章頁的正文/metadata 抽取或驗證 fallback。先跑繁中黃金集 A/B，不得預設一定比站點規則好。  
   - <https://trafilatura.readthedocs.io/en/latest/>
   - <https://aclanthology.org/2021.acl-demo.15/>

5. **scrapy-playwright**  
   只用於確認需要 JS/互動的 request；補齊 page/context 上限、errback、resource abort 與 browser restart。捕獲 API 後應回退普通 HTTP。  
   <https://github.com/scrapy-plugins/scrapy-playwright>

### 只做隔離實驗

1. **scrapy-impersonate / curl_cffi**  
   只對已授權且證明是 transport fingerprint 問題的站做同 URL A/B。它與 Playwright 都接管 download handler，應採分 process/profile，不能直接互相覆蓋。  
   - <https://pypi.org/project/scrapy-impersonate/>
   - <https://curl-cffi.readthedocs.io/en/stable/>

2. **Scrapling Adaptive Selector**  
   只適合已有成功元素基線後的小幅 DOM 漂移；不能修首次生成從未成功的站。  
   <https://scrapling.readthedocs.io/en/latest/api-reference/selector.html>

3. **Zyte API 等付費服務**  
   只有自管方案邊際成本不划算、且取得預算與法遵確認後才可試。任何採用都要有 domain allowlist、每日預算、kill switch 和相同 quality gate。

### 現階段不要導入

- `scrapy-redis`：目前沒有吞吐瓶頸證據；
- 全域 Playwright；
- 隨機 User-Agent/代理輪替；
- CAPTCHA solver 或登入/付費牆繞過；
- 更多 LLM repair 次數；
- 把已核准的 active spiders 全部 gitignore。

## 8. 測試與完成定義

### 必要離線測試

建議放在正式 backend tests 路徑，至少涵蓋：

```text
validator_false_positive
udn_wrong_content_fixture
cna_duplicate_fixture
moneydj_empty_fallback_and_timestamp
cnyes_schema_type_change
candidate_does_not_overwrite_active
sandbox_does_not_inherit_secrets
domain_allowlist_is_enforced
usage_is_attributed_per_run
bad_row_is_quarantined
```

`scrapy check` 顯示 `Ran 0 contracts` 不算 selector/schema 測試完成。

### 黃金集

每站至少 20 個 listing/article URL，涵蓋：

- 近期與較舊文章；
- 短訊與長文；
- 圖片/表格/圖說；
- 非典型版型；
- 應拒絕的非新聞頁；
- 已知阻擋/空殼 fixture。

每個候選策略至少三輪、分開時段，記錄：

```text
discovery_recall
true_content_retrieval_rate
valid_article_rate
body_boundary_accuracy
date_accuracy
duplicate_rate
p50/p95_latency
browser_seconds_per_valid_item
retries_per_valid_item
cost_per_valid_item
```

### 必要故障注入

1. kill graph process 後可由同一 run/thread resume；
2. kill Scrapy job 後可由 cursor/JOBDIR 恢復；
3. coder/judge timeout 不污染下一站 usage；
4. candidate timeout 不改 active；
5. DB 單列 invalid 不使其他 valid rows 靜默消失；
6. 200 challenge HTML 不得被計為 retrieval success；
7. 429 能尊重 `Retry-After` 且觸發 circuit breaker。

### 完成回報合約

不要只回「完成」或「已編譯」。請固定回：

1. 結論與三態：VERIFIED / UNVERIFIED / REFUTED；
2. 修改檔案與精確行號；
3. 實跑命令；
4. 原始關鍵輸出；
5. 哪些 fixture/故障注入已通過；
6. 哪些外站、DB 或 sandbox 能力仍未驗；
7. git diff/status，證明沒有覆蓋使用者變更。

## 9. 不可接受的捷徑

以下不算提升成功率：

- 把門檻從 3 筆改成更多筆，但仍不檢查是不是新聞；
- 看到 200 就算 retrieval success；
- 看到 title/url 非空就算 quality success；
- 用 LLM 語意分數放行機械規則拒絕的資料；
- provider timeout 後重跑同一份舊 code；
- 只增加重試、timeout 或 token 預算；
- 先拆 god-file，再宣稱成功率提高；
- 用 `try/except: pass` 把錯誤變成空結果；
- 只驗 syntax/import，不驗 fixture；
- 把通過一次的單站 run 當長期成功率。

## 10. 預登記的改判條件

這份架構建議不是信仰；出現以下證據時應改判：

1. 五站黃金集顯示 declarative recipe 無法覆蓋多數來源，而受審核 Python 明顯提高 `valid_article_rate`，則保留受控 code escape hatch；
2. Trafilatura 混合策略在繁中黃金集沒有改善正文完整率或誤抽率，則不導入；
3. Playwright/impersonate 只提高 HTTP 200、未提高 `quality_ok`，或 p95 成本超標，則停止該 fallback；
4. 分層架構增加複雜度卻無法在指定 failure class 上改善成功率，則回退，不以 sunk cost 繼續堆工具。

## 11. 建議第一個交付

第一個 PR/commit 只做以下範圍：

1. 建立 UDN/CNYES/CNA/MoneyDJ 的離線 fixtures；
2. 重寫 validator，消滅已知假成功；
3. 加 candidate staging，保證 active 不被失敗候選覆寫；
4. 加 sandbox secrets regression test；
5. 修正 per-run usage attribution；
6. 依實跑結果更新 `HEALTH_CHECK.md`。

不要在同一個交付同時拆 `graph.py`、導入 Trafilatura、Scrapling、代理或分散式框架。先建立可信的量測與安全基線，之後每個工具才有可能被證明真的提高成功率。


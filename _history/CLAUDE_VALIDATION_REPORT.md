# Claude 修復接手結果

> 日期：2026-07-26
> 基準提交：`cfb9d55`、`483de24`、`7b42e2d`
> 結論：**原驗收報告列出的可修缺陷已修完；P0 整體仍因真正 sandbox 與 DB quarantine 未完成而不可宣稱全數完成。**

## VERIFIED

| 項目 | 修正與證據 |
|---|---|
| Validator 假成功 | `validators.py` 加 soft-block、`unique_ratio`、path-only article 判斷、`hostname` 驗證；5 unique + 95 duplicates 與 5 筆 Access Denied 均拒絕 |
| MoneyDJ URL | `site_queue.yaml` 修正 `news-viewer.aspx`，canonicalization 保留文章身分參數 `a`；5 個 query ID = 5 unique |
| Promotion fail-closed | `persist_spider()` 同時要求 validation pass 與 candidate 存在；否則拋錯且 active hash 不變 |
| 真 rollback | promotion 前保存 previous artifact；`rollback()` 驗 current hash、防覆蓋後續版本、原子還原並驗 hash |
| Usage 歸屬 | graph error 的本站 usage 寫入本站 error record，不再丟棄或流到下一站 |
| Registry 品質證據 | 新增 valid/unique、兩種 ratio、四層 flags、reject reasons、issues |
| 主流程整合 | 新增 `validate_output → persist_spider → rollback` 與 `run_batch error → usage record` 測試 |

實跑：

```text
test_validators  14/14 passed
test_staging      8/8 passed
test_safety       4/4 passed
scrapy list       cna cnyes ctee moneydj udn
compileall        passed
```

## 仍不可改判

- **VERIFIED**：candidate subprocess 不繼承 DB／LLM secrets。
- **UNVERIFIED**：Windows 下 filesystem/network 隔離；`SPIDERFORGE_ALLOWED_DOMAINS` 仍不是強制 egress policy。
- **UNVERIFIED**：P0-4 DB 欄位契約、quarantine 與正式 Neon 寫入。
- **UNVERIFIED**：HTML/AxTree 站首建成功率；本輪提升的是「不把錯誤結果升版」，不是已證明能救起 HTML 站。

下一步不要先擴大重構：先以單一 HTML 站做 kill-test；若失敗，優先驗證「URL discovery + Trafilatura 正文抽取」是否比 LLM selector 生成穩定。

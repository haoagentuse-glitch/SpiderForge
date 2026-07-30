# _history — spider_forge 前期/時點文件歸檔

這裡是**已被取代或屬於某時間點快照**的文件，保留供追溯，**不代表目前架構**。

## 目前的真實來源（要看現況請看這些，不是本目錄）
- **canonical 技術規格**：[`docs/20_spec_v1_spider-forge-graph.md`](../../../../../docs/20_spec_v1_spider-forge-graph.md)（repo 根 docs/）
- **系統現況摘要 + 流程圖**：[`../README.md`](../README.md)
- **資產登錄治理**：[`../assets/README.md`](../assets/README.md)
- **未解決事項 / 現況追蹤**：repo 根 `STATE.md`、`DECISIONS.md`

## 歸檔清單

| 檔案 | 原本是什麼 | 為何歸檔 |
|---|---|---|
| `08_spider_forge規劃_舊初版規劃.md` | 實作前最初藍圖（M0–M5 里程碑、三層職責原始版） | 已由 `docs/20_spec_v1_spider-forge-graph.md` 取代（原檔開頭已自述過時）；原位置 `docs/spec/08` |
| `HEALTH_CHECK.md` | 2026-07-26 對抗性健檢報告 | 修復前歷史快照，內文已自述不再代表現狀 |
| `CLAUDE_REMEDIATION_BRIEF.md` | codex 給的 P0 修復規格書 | 任務指令書，P0 問題已在後續 commit 修完 |
| `CLAUDE_VALIDATION_REPORT.md` | P0 修復完成後的驗收報告 | 驗收對象是已合併的舊 commit，時點快照 |
| `HANDOFF_2026-07-27.md` | EvidencePack v2 交接筆記（ECB/BoE/CNA/CTEE/MoneyDJ 當時阻點） | 特定站點某時點的工作筆記，非架構文件。**仍未解的站點問題已摘要進 repo 根 `STATE.md` 的 `blocked:`**，看那裡即可 |
| `topic_relevance_scout_前期研究.md` | 2026-07-27 主題判斷架構研究 | ⚠️ **此研究建議的「encoder-first（BGE-M3 類）為唯一 promotion 權」架構並未被採用**。現況是 **Gemini-first enforce**（BGE-M3 降為顯式回退、Qwen 只當 shadow advisory）。閱讀時勿誤把其建議當現況 |

> 規則：本目錄只進不動——要更新架構請改上面「目前的真實來源」那幾份，不要改這裡的歷史檔。

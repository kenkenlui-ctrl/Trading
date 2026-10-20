# Spec: win9you.com — 誠實結果發佈 + Backtest 衛生披露
**日期：** 2026-10-20
**授權：** Kenneth 2026-10-20 17:59 HKT「improve if you think needed」；Undercover 判斷值得做，範圍收窄如下。
**背景：** 多重測試文獻 — 5 年數據試 >45 個設定，in-sample Sharpe 1.0 幾乎保證出現但 OOS 期望為零（Xtin Capital，10,000 個無 edge 策略重跑）；Quantopian 追蹤 888 個策略，backtest Sharpe 解釋唔到 live 表現 2.5%（The Investor's Handbook）。v2 嘅經歷正中呢個陷阱：per-trade 數字靚，但 placebo 一跑就現形。

## 目標
將 v2 嘅負面驗證結果正式發佈，令 win9you.com 成為「會公開自己失敗測試」嘅訊號站 — 誠信即資產。

## Deliverable 1：新 research 頁 —「咩係唔 work 嘅」（標題可由 MiniMax 按站內風格定）
內容必須全部來自現有 report JSON / 已驗證結果，**禁止手打數字**（沿用 P0 規則）：
- Sprint 2 crossed randomization 三軸結果：selection z=−3.79（2021–26，差過隨機）/ gate z=−2.98 / timing z=−4.65（來源：reports/signal_search_s2_*）
- Sprint 1：11 個變體，0 個過關（來源：reports/signal_search_s1_summary.json + placebo re-run 2026-10-19）
- Direction A（2026-10-20）：fill-matched 測試，SELECTION_DEAD — v2 揀股連完美成交都贏唔到隨機/等權（來源：reports/edge_alpha_a_us.json、edge_alpha_a_us_lag1.json）
- Per-trade edge 仍然成立嘅部分要寫清楚：收市確認 H1（z 8.9–13.2）、US 勝率 60.3% / +1.77% 每單 — 唔好將成個系統寫死，要分清「訊號有 edge」同「組合砌法贏唔到等權」係兩件事
- 結論句：v2 已停真錢部署計劃；網站繼續運作，數字以本頁同 methodology 為準

## Deliverable 2：Methodology 頁加披露段
- **試驗台賬（trial ledger）**：列明歷來共測試過幾多個策略變體（Sprint 1: 11、Sprint 2: 3 軸、Sprint 3、edge-to-alpha A/C — 由 reports 目錄數返，寫明來源）
- **Holdout 紀律聲明**：2016–20 / 2021–26 分段係固定切法；任何新 claim 必須兩個分段同號先可以發佈
- **Placebo 規則聲明**：日後任何策略數字上站之前，必須並排一個 null/placebo 對照（沿用 close-confirmation placebo 做範例）

## 約束
- 語言同排版跟返站內現有 research 頁風格（廣東話）
- 數字可追溯到 report 檔；有出入以 report JSON 為準，唔好就咁改文字遷就
- 完成後回報：新頁 URL、改動檔案清單、每個數字嘅來源檔名
- 唔好掂生產訊號生成邏輯；今次只係內容/披露層面

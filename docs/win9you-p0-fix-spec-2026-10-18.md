# P0 修正 Spec — win9you.com 一致性修復
**日期：** 2026-10-18
**來源：** win9you-consistency-audit-2026-10-18.md
**權威數字來源：** `data/hk_retest_summary.json`（2026-10-17 23:43 生成）

## 權威數字（0.25% 成本口徑，除非註明）

| 市場 | v2 年化 | 等權年化 | 差距 | 2016–20 差距 | 2021–26 差距 |
|------|---------|---------|------|-------------|-------------|
| HK | +11.02% | +15.38% | −4.36pp | −8.95pp | +0.53pp |
| US | +26.02% | +24.63% | +1.38pp | +3.72pp | −2.04pp |
| JP | +15.48% | +15.97% | −0.50pp | −9.15pp | +5.10pp |

JP 在 0.10% 成本下：v2 +18.03%，等權 +15.97%，差距 +2.06pp。

每單統計（2023–26 樣本外，含收市企穩 S1）：
- US：勝率 60.2%，平均淨 +1.78%/單，n = 2,992
- JP：勝率 65.8%，平均淨 +2.41%/單，n = 2,193

倉位規則：每單 1% 風險，單一名義上限 10%，最多 10 倉。
資本基準：HK$1,000,000。

---

## Fix 1: `llms.txt` — 更新所有數字到 2026-10-17 修正後版本

**問題：** 整個 "What these numbers do and do not mean" section 都是舊數字（2026-10-16 生成，未含 10-17 等權修正）。

### 1a. US 段落
**舊：** In the US, the same 201 stocks held passively with an equal-weight monthly rebalance returned 27.4%/yr over 2016-26; the v2 portfolio returned 27.2%/yr. The gap is -0.2pp/yr, which is zero.
**新：** In the US, the same 201 stocks held passively with an equal-weight monthly rebalance returned 24.6%/yr over 2016-26; the v2 portfolio returned 26.0%/yr. The gap is +1.4pp/yr. This gap is not stable across sub-periods (2016-20: +3.7pp; 2021-26: -2.0pp), so it should not be cited as a durable edge.

### 1b. JP 段落
**舊：** At the 0.10% round-trip cost, the same comparison gives 18.03%/yr for the v2 portfolio against 18.00%/yr for equal-weight holding of the same names over 2016-26 — a gap of +0.04pp/yr.
**新：** At the 0.25% round-trip cost, the same comparison gives 15.48%/yr for the v2 portfolio against 15.97%/yr for equal-weight holding over 2016-26 — a gap of -0.50pp/yr. At the 0.10% cost the figures are 18.03%/yr vs 15.97%/yr (+2.06pp/yr). Neither gap is stable across sub-periods (2016-20: -9.15pp; 2021-26: +5.10pp at 0.25% cost).
**舊：** (2016-20 TRAILS passive by -7.9pp/yr, only 2021-26 leads, by +16.7pp/yr)
**新：** (2016-20 TRAILS passive by -9.15pp/yr, only 2021-26 leads, by +5.10pp/yr at 0.25% cost)

### 1c. 每單統計
**舊：** US: 勝率 60.3%、每單平均淨 1.77%、n = 2,982 / JP: 勝率 65.5%、每單平均淨 2.35%、n = 2,187
**新：** US: 勝率 60.2%、每單平均淨 +1.78%、n = 2,992 / JP: 勝率 65.8%、每單平均淨 +2.41%、n = 2,193

### 1d. 更新時間戳
**舊：** `Last regenerated: 2026-10-16` → **新：** `Last regenerated: 2026-10-18`

### 1e. 長期修復（建議）
將 `llms.txt` 改為由 `data/hk_retest_summary.json` 自動生成，避免手打數字再次過時。

---

## Fix 2: 方法論頁 — 刪除 L30 舊句
**問題：** L30 寫「美股：v2 落後純持有 0.2 個百分點/年」，但同一頁表格顯示領先 1.4pp。L30 是舊數字殘留。
**改動：** 刪除 L30 整句，或改為「美股：v2 領先純持有 1.4 個百分點/年（但分段會變號，見 §4.7）」。

---

## Fix 3: 方法論頁 FAQ — 修正勝率表述
**問題：** 「v2 嘅回測樣本外勝率約 50–54%」與同一頁表格（US 60.2%、JP 65.8%）矛盾。
**改動：** 改為「v2 嘅回測樣本外勝率：美股 60.2%（n=2,992）、日股 65.8%（n=2,193）。每單有優勢，但組合層面唔等於跑贏被動持有（見 §4.7）。」

---

## Fix 4: `/us200/` — 移除已停用策略訊號
**問題：** 表格顯示 SELL_R1 和 BREAK_SHORT 訊號，但已在 2026-10-01 停用。頁面 L8–10 寫「並無正期望值」但下面列出 SELL，自相矛盾。
**改動：** 1. 移除所有 Strategy 為 SELL_R1 或 BREAK_SHORT 的行 2. 只保留 BUY_S1 3. 檢查 /jp200/ 是否同樣問題
**驗證：** 修復後表格不應出現任何 SELL Action。

---

## Fix 5: `/backtest` — 理順 T+10 數字
**問題：** 三個 T+10 數字矛盾：L6 −4.05%、L115 +44.02%、L152 +814.07%。其中 +44.02% 已在 L165–166 說明撤回但表格還留著。
**改動：** 1. 從 L115 刪除 +44.02% 2. 釐清 −4.05% vs +814.07% 關係（標明口徑或刪除錯誤） 3. 只保留一個權威 T+10 數字（建議 −4.05%）

---

## 執行順序
1. Fix 1（llms.txt）— 對外影響最大
2. Fix 4（us200 SELL 訊號）— 用戶可見錯誤
3. Fix 2、Fix 3（方法論自相矛盾）
4. Fix 5（backtest 數字混亂）

## 驗收標準
- [ ] llms.txt 所有數字與 hk_retest_summary.json 一致
- [ ] 方法論頁無自相矛盾
- [ ] /us200/ 無 SELL_R1 / BREAK_SHORT
- [ ] /backtest 只有一個權威 T+10 數字

---

## 執行結果，2026-10-20

### 五項修正其實已經落地
2026-10-19 嗰次 build 已經做咗呢份 spec 要求嘅五項修正，而且**線上同本 repo 嘅 `public/` 逐 byte 一致**（`llms.txt`、`methodology.html`、`/us200/`、`/jp200/`、`/backtest/` 全部抽落嚟比過）。所以今次唔係「再改一次」，係**驗證 + 補上守住佢嘅嘢**。

### 呢份 spec 嘅數字快照過期咗 —— 唔好照住改
開頭「權威數字」表係 `data/hk_retest_summary.json` **2026-10-17 23:43** 版嘅快照。嗰個檔喺 **2026-10-18 16:12 重跑過**，US／JP 嘅數字微調咗：

| 市場 | spec 快照（10-17） | 現時 artifact（10-18 16:12） |
|------|------------------|----------------------|
| US v2 / 等權 / 差距 | 26.02% / 24.63% / **+1.38pp** | 25.59% / 24.63% / **+0.96pp** |
| US 2016–20 / 2021–26 | +3.72pp / −2.04pp | +3.72pp / **−2.39pp** |
| JP v2 / 等權 / 差距 | 15.48% / 15.97% / **−0.50pp** | 15.63% / 15.97% / **−0.34pp** |
| JP 2016–20 / 2021–26 | −9.15pp / +5.10pp | −8.98pp / **+5.09pp** |
| 每單（US / JP） | 60.2% n=2,992 / 65.8% n=2,193 | 60.3% n=2,982 / 65.5% n=2,187 |

驗收標準寫嘅係「llms.txt 所有數字與 `hk_retest_summary.json` 一致」，而站上而家嘅數字**就係現時 artifact 嘅值**（llms.txt 由 `perf_figures` 生成，per-trade 由 `data/ab_close_hold.json` 生成）。**如果照呢份 spec 嘅數字改落去，會令站同 artifact 唔一致 —— 唔好咁做。** 呢個係 spec 嘅意圖，唔係 spec 嘅數字快照。

### 額外揾到同修正咗兩項（spec 冇提，同一類問題）
1. **`/backtest` Headline 表嘅「Total」欄**。嗰欄係 `sum(逐筆 return_pct)`：T+10 嗰行 120 單 × +1.63% = **+195.3%**，同站上四段之下就撤回咗嘅 **+44.02%** 係同一種數（逐筆百分比相加，唔係組合回報），但佢仍然掛喺「Headline（淨回報）」標題下面。已移除該欄，頁面留低原因。
2. **OG 圖**（`/og-image.png`，社交分享 / link preview 用）。生成器仍然印住 **+3.05%**（10-18 已撤回）同過期嘅 **+0.38%**（artifact 係 +0.76%）。已改成全部由 artifact 讀出（`data/monthly_backtest/latest.md` 同 `data/ab_close_hold.json`），**讀唔到 source 就直接報錯，唔會靜靜跌返手打 literal**；順帶修好中文字型（舊圖係豆腐格）。

### 新增 gate
`scripts/self_test.py` 嘅 `t_p0_site_figures_match_the_artifact`，將四條驗收標準變成可執行檢查。關鍵係佢**唔係**「頁面要包含呢啲數」，而係**「頁面講嘅每一個差距都必須係 artifact 入面有嘅值」** —— 後者先捉得到多出嚟嗰個錯數。

九個真實竄改（改實際檔案，唔係改 local copy）全部捉到：llms.txt 數字被改 / 舊 literal 返嚟 / 方法論頁多咗一個假差距 / 「0.2 個百分點」返嚟 / 「50–54%」返嚟 / us200 多咗一行 SELL_R1 / backtest 回復 Total 欄 / 回復 +814.07% / OG 生成器回復 +3.05%。

### 測試
`self_test` 348/348、`edge_alpha_selftests` 19/19、`edge_alpha_c_selftests` 63/63、`sprint2` 61/61、`sprint3` 20/20。**未 deploy**（contract：Undercover 先驗）。

# win9you.com 跟進修正 dispatch — 2026-10-29

承接 2026-10-23 dispatch（`docs/website-fix-dispatch-2026-10-23.md`）。其中兩項已上線（C1 成本敏感度表、vix-regime.html noindex），但以下問題未解決，今次必須一次過處理。全部 deploys 經 A2 deploy gate；逐項回報 live URL + 驗證。

## ① 緊急：立即手動 refresh + redeploy

現況（Undercover 2026-10-28 17:42 HKT 實測）：首頁仍然係 2026-10-25 00:46 build，T-1 數據 2026-10-23，156 個訊號，標題仲寫住 "Live" / "今日 actionable"。

即刻跑一次完整 refresh + redeploy，驗證 live stamp 由 2026-10-23 推進到最新已收市 session，並回報數據日期同 build 時間。**今次 refresh 必須包括 2026-10-28 收市數據（US、JP、HK 三個市場），唔可以只推到 10-26 或者 10-27。**

## ② 查明 nightly build 點解又停咗 — 必須交代 root cause

10-25 手動 refresh 成功過一次，之後 10-26、10-27、10-28 連續三晚 nightly build 全部 miss。之前 10-22/23 miss 嘅 root cause 從來未交代過，今次唔可以再只係「修好咗」就算。

要求：
- 交代 root cause：係 scheduler/cron 問題、deploy gate 問題、定係部機瞓咗（lid-closed）導致 job 根本無跑？
- 交代一個防止再發生嘅機制：build 失敗要 fail-loud（例如 build log 留低明確失敗原因，下次 build 前檢查上次 build 有冇成功），唔可以靜靜地 miss 三晚都無人知。

## ③ Ship Sprint 2 Phase A1 fail-loud staleness states

呢個係 10-23 dispatch 第 2 項，到今日都未上線 — 而家首頁用 5 日前數據仲標 "Live" 就係佢未上線嘅直接後果。

要求（同 10-23 spec 一致）：
- 當某市場數據舊過佢最新已收市 session，首頁同市場頁必須顯示 prominent STALE notice，連數據日期一齊顯示；
- STALE 狀態下唔可以將訊號呈現為 "Live" / "今日 actionable"；
- 狀態喺 build time 計算；
- 附 acceptance test：用舊 snapshot 同新 snapshot 各跑一次，證明 STALE 出現同消失都正確。

## ④ Methodology 頁「最後更新」行改為 build-time 自動生成

Methodology 頁頂而家仲寫住「最後更新: 2026-10-16」，但 C1 表 10-25 已經上線 — 呢行係手打，改完內容無跟住改。改為 build-time 自動生成，之後任何內容更新都會自動反映。

## ⑤ 確認 vix-regime.html 上線狀態

Undercover 10-28 17:42 已驗證 `research/vix-regime.html` live，頁內有註明 noindex。如 failed-test record 未有 link 去呢頁，補埋。

---

回報格式：逐項 done / not done，附 live URL 同驗證方法；第 ② 項必須有 root cause，唔接受「已修復」四個字了事。

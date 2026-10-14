# Website Improvement Sprint 1 — Spec

## Objective

Ship two things: (A) finish and deploy the pending Oct 13 refresh fixes, (B) fix the P0-1 equity-curve engine mismatch. Done = both live on win9you.com, verified.

## Background

- Improvement report (`docs/improvement-proposals-2026-10-07.md`) P0-1: `/equity_curve_t_1/3/5/10.html` pages read `data/monthly_backtest/backtest_2026-09-11.json`, which is the OLD swing/day-trade system (fixed −2/−5/−7/−10% stops, +2/+5/+10/+15% targets) — completely unrelated to v2 (no profit target). Navigation and methodology pages present them as one system. This is the most expensive error on the site: decisions made on wrong-engine numbers.
- Oct 13 evening: a full win9you refresh was commissioned. As of Oct 13 21:55 HKT it was NOT deployed: (1) the freshness-check loop never covered HK (fix pending), (2) the homepage's HK backtest text is stale.
- HK numbers superseded: the proper Oct 12 retest (2016–2026, 0.25% round-trip) shows HK v2 at **11.0%/yr vs 19.1%/yr** equal-weight (**−8.1pp/yr**), NOT the old −4.6pp figure still on the homepage. Verdict: KEEP EXCLUDED.
- JP +6.9pp headline was withdrawn Sept 2026 (single-regime artifact). Any page still showing it must be fixed.

## Task A — Finish and deploy the Oct 13 pending fixes (do first)

1. Verify current state: is the HK freshness-check fix implemented? Is the homepage HK text updated? Check git status/diff for uncommitted work from Oct 13.
2. Complete whatever is unfinished: freshness-check loop must cover HK; homepage backtest text must show the Oct 12 numbers (11.0% vs 19.1%, −8.1pp/yr, KEEP EXCLUDED).
3. Deploy to production (Cloudflare). Verify live pages show the new content.

## Task B — P0-1: Fix equity-curve engine mismatch

1. Decide: rewrite `build_equity_curves.py` to use the v2 engine (`portfolio_test.py` mechanics), OR explicitly label those pages "historical v1 swing system, not current v2". Recommend the rewrite if it fits in the sprint; the label is the minimum.
2. The v2 engine rules (for reference): long-only, limit buy at 20-day low S1 with close-confirmation, stop = S1 − 3×ATR, no profit target, exit at close on 10th trading day.
3. Delete or fix the duplicate `equity_curve_t10.html` vs `equity_curve_t_10.html`.
4. Deploy and verify live.

## Guardrails

- No strategy changes: this is website correctness, not signal logic. Do not touch `build_v2_signals.py` signal rules.
- Every number on a page must trace to its source script/output. List the source per number in your summary.
- Run existing self-tests; add a test that fails if an equity-curve page ever reads the old JSON again.
- If Task A uncovers anything that changes the verdicts above (e.g. different HK numbers), STOP and report before deploying.

## Deliverables

1. One-page summary: what was pending, what you changed, what is live now (with URLs).
2. Per-number source list.
3. Self-test results.

## Non-goals

- P2-1 trigger notifications, P2-2 position tracking (later sprints).
- Any intraday prediction work (explicitly not recommended).
- UI redesign (P3).

## Chat message to MiniMax

「新任務：網站改進 Sprint 1。Spec 寫好喺 docs/website-improvement-sprint-1-spec.md，請先讀晒成份 spec，再按入面做。重點：(A) 先搞掂 10 月 13 號未 deploy 嘅嘢（HK freshness check + 首頁舊 HK 數字）；(B) 修 P0-1 權益曲線引擎錯配。做完交：one-page summary、每個數字嘅出處、self-test 結果。」

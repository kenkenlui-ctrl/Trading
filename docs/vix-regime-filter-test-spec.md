# VIX Regime-Filter Test — Spec for MiniMax Code

You are MiniMax Code working on the win9you.com (Leeks Terminal) repo at ~/dev/dsa-hk.
A new research task has arrived via Undercover (Kenneth's agent). Read this whole spec first, then follow the Test plan.

## Objective

Test whether VIX explains the regime-dependence of the v2 strategy's Japan results. If yes, define a VIX-based regime filter for JP signals.

**Research only — no changes to live signal logic until Kenneth approves.**

## Background

- v2 JP backtest shows roughly +6.9pp, but decomposing by period gives 2016–2020 lagging / 2021–2026 leading: a single-regime-driven result, not a stable edge. (Confirm the exact metric definition and period bounds in the backtest config before starting.)
- An external quant agent (EigenFlux exchange, 2026-10-08) reports from their own testing: **VIX is the strongest explanatory variable for JP regime switches**, carry second, USD/JPY weakest. Proposed transmission: VIX spike → carry unwind → forced unwind of JP cross-market hedge positions. They validated this path around Mar 2020 and 2022.
- Our constraint: T-1 OHLC only, no order-book depth history. VIX is free daily data, so this test is feasible with what we have.

## Inputs

1. Existing v2 JP signal table + backtest trades, in the current schema (date / market / ticker / S1 / distance-percentile / touched / close-confirmation / ATR / stop).
2. VIX daily series — pick ONE source and document the choice: FRED `VIXCLS` or Yahoo `^VIX` daily close. Signal-date features must respect T-1 availability (a signal generated on date T may only use VIX data available at T-1 close).

## Test plan

1. **Reproduce the regime split.** Confirm the JP book's 2016–2020 vs 2021–2026 performance split (cumulative P&L chart + subperiod returns). If the actual break point differs, run a simple structural-break check and document what you found.
2. **Attach VIX features** to every JP signal date:
   - (a) VIX level
   - (b) VIX 1-day change / spike z-score
   - (c) VIX 20-day and 60-day percentile
   
   Test all three forms. Do not pick the winner by eyeballing; report all.
3. **Explanatory tests:**
   - Conditional performance: JP signal forward returns (to the v2 day-10 exit) split by VIX-percentile tercile at signal date.
   - Regression of signal forward return on the VIX features.
   - Lead/lag: does a VIX spike precede deterioration in subsequent N-day JP signal performance?
4. **Filter design.** Define candidate rule(s) from step 3 results, e.g. "skip JP signals when VIX 20d-percentile > X" — direction and threshold come from the data, not assumed. Re-run the JP backtest with the filter applied.
5. **Report before/after:** total return, Sharpe, max drawdown, trade count, win rate, AND the fillable-supply stats (share of signals within 0.5% / 2% of entry). The filter must not just cut trades to flatter ratios.

## Guardrails

- **Placebo:** run the same filter logic on randomly-shifted VIX dates. The real result must beat the placebo distribution.
- **Event dominance:** report results with and without the Mar 2020 and 2022 windows. If the whole effect is two events, say so explicitly.
- **Multiple comparisons:** we are testing 3 VIX forms — disclose this, don't overclaim a marginal p-value.
- **No look-ahead:** VIX features at signal date T use data available at T-1 close only.
- **Branch only:** implement behind a `vix_regime_filter` config flag, default OFF. No changes to production signal generation.

## Deliverables

1. One-page summary: does VIX explain the JP regime split? Which form (level / spike / percentile)? Recommended filter rule, or "no filter justified".
2. CSV: JP signals with attached VIX features + regime label.
3. Code + config flag in the repo, reproducible from a single script/notebook.

## Non-goals

- US/HK VIX testing (only if time permits and Kenneth asks).
- Carry/FX explanatory variables — ranked lower by the external agent; parked for later.
- Any live-signal or dashboard changes.

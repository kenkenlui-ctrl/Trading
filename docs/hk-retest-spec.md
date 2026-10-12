# HK Retest Spec — v2 rules on HK universe + adverse-selection check

Date: 2026-10-12. Requested by Kenneth via Undercover.

## 1. Objective

Decide, with pre-registered criteria, whether the v2 long-only rules deserve a
retest on the HK universe — and specifically quantify **adverse selection** on
HK limit-buy fills, the mechanism OpenClaw (EigenFlux network) proposed to
explain HK's failure.

Current production state: `INCLUDE_HK=False`. The standing backtest result is
that v2 on HK trails equal-weight hold of the same universe by **4.6pp/yr**
(2016–2026) at 0.25% round-trip costs, and still trails by **1.4pp/yr at zero
cost** — i.e. not a cost problem, a rules problem. This test must either
overturn that verdict with fresh numbers or leave production untouched.

## 2. Rules under test (exact v2, unchanged)

- Long-only.
- Stock close above its 200-day MA **and** 200-day MA rising (20-session slope).
- Index (HK benchmark used in the original HK test) close above its 200-day MA.
- Limit BUY at 20-day low (S1), next session only; skip if the session opens at
  or below S1.
- **Close-confirmation fill rule** (2026-10-05): a touch that fails to hold
  above S1 at close is a broken level, NOT a fill.
- Stop = S1 × (1 − 3 × ATR%).
- No profit target; exit at close of the 10th trading day after fill.
- Guard rails: ATR% < 6%, 20-day box width < 35%.

## 3. Universe & data

- Same HK universe definition as the original HK backtest (comparability first).
  The hk200 page currently lists 245 names — reuse the original universe
  script/snapshot, document any difference.
- T-1 daily OHLC only. **No look-ahead**: all features computable at signal time.

## 4. Costs

- Primary: **0.25% round-trip** (HK realistic, incl. stamp).
- Sensitivity: 0% and 0.10%.

## 5. Benchmarks (both required)

1. **Portfolio-level**: v2 signal portfolio vs equal-weight buy-and-hold of the
   same universe, 2016–2026, net of 0.25% costs. (This is the comparison that
   failed: −4.6pp/yr.)
2. **Per-trade A/B**: filled v2 signals vs random-entry control — same stock,
   same day, same hold period (mirrors the track-record page methodology).

## 6. Time windows

- Full: 2016–2026.
- Split: 2016–2020 vs 2021–2026 (regime check — the JP "edge" turned out to be
  a single-regime artifact; do not repeat that mistake on HK).

## 7. Adverse-selection analysis (the new piece)

For every S1 signal, classify the outcome:
- filled (touched S1 intraday AND close held above S1)
- touched-but-failed-close
- gap-through (opened at/below S1)
- never-touched

Measure:
- Fill rate and the 10-day post-signal return for each bucket.
- **Adverse-selection gap** = E[return | filled] − E[return | signal].
  If fills systematically underperform the signal average, the limit buy is
  being adversely selected (informed flow selling into the resting limit).
- Compare the HK gap against the already-measured US/JP values (from existing
  fill data if available; otherwise compute the same metric on US/JP for a
  like-for-like comparison).

## 8. Placebo / hygiene

- Shuffled-label or random-entry placebo on the per-trade edge.
- Report n, win%, avg net %/trade with the same fields as the track-record
  page (`setup_oos` style).

## 9. Pre-registered success criteria (ALL must hold to recommend enabling HK)

1. Net of 0.25% costs, v2 beats equal-weight hold by **≥ +1.0pp/yr** over
   2016–2026.
2. Per-trade edge positive in **both** sub-periods (2016–2020 and 2021–2026).
3. Adverse-selection gap no worse than measured US/JP values, or a credible
   mechanism explaining the difference.

If any criterion fails: verdict = **KEEP EXCLUDED**, production untouched.

## 10. Guardrails

- New script only, e.g. `scripts/hk_retest.py`; config flag `hk_retest`
  defaulting OFF.
- **Do NOT modify** `INCLUDE_HK`, build files, or any production output.
- Deterministic; seed fixed; rerunnable.

## 11. Deliverables

- `scripts/hk_retest.py`
- `data/hk_retest_summary.json` (windows × benchmarks × costs, fill-bucket
  table, adverse-selection gap, placebo)
- One-paragraph verdict: **ENABLE** or **KEEP EXCLUDED**, with the numbers.
- Report the numbers back in the win9you thread; do not change production
  either way.

# Signal Search Sprint 1 — Spec

## Objective

Find entry/exit variations with REAL portfolio edge for US (primary) and HK (secondary), or report a clean negative. Falsifiable target: a variation beating equal-weight hold of the same universe by **>+2pp/yr net of costs in BOTH sub-periods (2016–2020 and 2021–2026)**. If nothing clears the bar, the deliverable is the negative result with the full tested grid disclosed.

## Background

- v2 baseline: long-only, 20-day-low (S1) limit entry + close-hold confirmation, 3xATR stop, no target, 10-day exit, 200MA stock+index regime filter.
- Measured: per-trade edge is real (US +1.9%/trade, JP +2.4%/trade) but portfolio alpha vs equal-weight is zero (US −0.2pp/yr) or negative (HK −8.1pp/yr, excluded).
- Ruled out already: VIX regime filter (R2 ≤ 0.003), hold=3 mirage (supply artifact), "beat the market" framing.
- Bottleneck hypothesis: fillable supply, not signal quality. Every test below must use supply-aware simulation.

## Stage 1 — OFAT from baseline (US first)

Vary ONE dimension at a time from the v2 baseline. All tests: T-1 OHLC, same US universe, costs US 0.05% + slippage scenarios, supply-aware (max 10 concurrent, no duplicate ticker, signal cooling 5 days).

| Dimension | Values to test |
|---|---|
| S1 lookback | 10, 20 (baseline), 30 days |
| Close confirmation | on (baseline), off |
| Stop | 2x, 3x (baseline), 4x ATR |
| Hold | 5, 10 (baseline), 15, 20 trading days |
| Regime filter | 200MA (baseline), 50/200 cross, none |

Report per variation: full-period net return, both sub-periods, max drawdown, trades/yr, avg concurrent positions. Rank by worst-sub-period edge (not best average).

## Stage 2 — Combine (only if Stage 1 finds something)

Take the top-3 single variations by worst-sub-period edge and test their pairwise combos (max ~6 runs). Same reporting.

## Stage 3 — HK track (secondary)

HK v2 failed (−8.1pp/yr). Do NOT re-run the full grid on HK. Instead: take the single best US variation from Stage 1 and test it on HK v1 rules as one confirmatory run, with the survivorship caveat stated (160/201 HK names lack point-in-time data — bound, don't fix). One run, report, move on unless it clears the bar in both sub-periods.

## Guardrails (non-negotiable)

- Walk-forward: fit on 2016–2020, validate 2021–2026, then reverse. No parameter chosen on the validation window.
- Supply-aware simulation always: limited concurrent positions, no duplicate tickers, cooling period. Never assume every signal fills.
- Placebo: shuffled entry dates must show ~zero edge, or the result is suspect.
- Multiple-comparison disclosure: report ALL tested variations, not just the best. State the total count.
- No intraday data, no new indicators without out-of-sample validation, no survivorship repair claims.
- Branch-only for any production file change; research outputs go to reports/.

## Amendment 2026-10-16 — the +2pp bar is replaced (owner-approved)

Sprint 1 ran the grid exactly as written above. Two US variations cleared the bar as
written: `regime=none` (+14.50pp worst sub-period) and `s1_10` (+5.64pp). **The
placebo guardrail this same spec requires then killed both.**

Moving every signal to a **random session** — same rules, same universe, same
costs, same supply limits — produces a null that is not near zero:

| Variation | Real gap | Placebo null | z | Net of null |
|---|---|---|---|---|
| `base` (the published v2 rules) | −3.96pp | +8.62pp | **−4.13** | **−12.58pp** |
| `s1_10` | +5.89pp | +13.86pp | **−2.50** | −7.97pp |
| `regime=none` | +15.65pp | +12.16pp | +1.56 | +3.49pp |

A bar of "+2pp/yr" that sits roughly **eight points below its own null** cannot
discriminate, and it points the wrong way: the variations with the biggest book
scored best precisely because more of a rising universe was deployed. Under the
spec as written, a strategy that picks entry days at random would pass.

**Replaced bar — a variation clears only if ALL THREE hold, in BOTH 2016–2020
and 2021–2026:**

1. `net = real_gap − placebo_null_mean > 0`
2. `z = (real_gap − placebo_null_mean) / placebo_null_sd ≥ 1.96`
3. the above holds in **each** sub-period, not on the full period alone

`z_crit` stays at the conventional 1.96 and is **not** loosened for the multiple
comparisons. Selecting a threshold after seeing the grid is the error the bar
was written to prevent. With ~11 variations tested, condition 2 is what stops a
single lucky cell from being reported as signal.

**Also required now:** every reported gap must travel with the **average
exposure** of the strategy over the same window. Ten slots at 1% risk / 10%
notional runs structurally *under*-invested (measured: 76.9% for `base`) against
a fully-invested equal-weight benchmark, so an unaccompanied gap silently mixes
stock selection with how much of the book was deployed.

**Implementation:** `scripts/signal_search_s1.py`, functions `placebo()` (per-window
nulls) and `placebo_gate()`. Run with `--placebo <trials>` to score every
variation against its own null. Results: `reports/signal_search_s1_us_placebo.json`.

### Note for whoever implements the placebo

A placebo must **move** each signal, not reorder it. The first implementation used
`rng.permutation(idx)` over the candidate bar indices — that only reorders the
same set of sessions, the (bar → signal) pairing never changes, every trial
returns the real number to the decimal (`sd = 0.00`), and the guardrail passes
silently while testing nothing. Check `sd`, not the mean, to confirm a placebo
has any power at all.

## Deliverables

1. One-page verdict: what cleared the bar (or "nothing did"), with numbers.
2. Full grid table: every variation tested, both sub-periods, net of costs.
3. Machine-readable JSON summary (same schema as data/vix_regime_summary.json).
4. If something clears the bar: a follow-up spec for deeper validation before any production use.

## Non-goals

- Changing live signal rules (nothing touches production in this sprint).
- Intraday strategies, options, shorting.
- New data purchases or API integrations.

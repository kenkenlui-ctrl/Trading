# Edge → Alpha Prereg Spec: Direction A + C (Parallel)

**Date:** 2026-10-19
**Status:** Pre-registered (Kenneth approved "do all" 2026-10-19 23:02 HKT)
**Scope:** Direction A (fill-matched selection test) + Direction C (portfolio construction) in parallel
**Direction B:** Conditional on A — only if A says selection is real
**Direction D:** Exit path — if A says selection is dead, stop v2 real-money plan

---

## Direction A: Does v2 Selection Have Edge?

### Research Question
Is the portfolio-level shortfall caused by (a) Selection or (b) Plumbing?

### Experimental Design: Fill-Matched Arms

**Arm 1 (v2-selection-at-close):** v2 rules, buy at CLOSE, fully invested EW, 10-day hold, daily rebalance.
**Arm 2 (EW-at-close control):** Same universe, ALL names, buy at CLOSE, fully invested EW, 10-day hold.
Both arms fill-matched (100%) and exposure-matched (100%). ONLY difference is selection.

### Decision Rule
- Arm 1 beats Arm 2 by ≥ +1.0pp/yr with consistent sign in both sub-periods → SELECTION_REAL → Direction B
- Otherwise → SELECTION_DEAD → Stop v2 real-money plan

### Guardrails
No-look-ahead, PIT universe symmetry, placebo arm (random selection), 0.25% costs, machine-readable JSON, self-tests.

### Markets
US first. JP confirmatory if US shows SELECTION_REAL. HK excluded.

---

## Direction C: Portfolio Construction (Parallel)

### Dimensions (OFAT)
- C1 Sizing: 1% risk (baseline) vs Kelly vs fixed fractional vs vol-scaled
- C2 Concentration: max 10 (baseline) vs 5 vs 20 vs unlimited
- C3 Holding: T+10 (baseline) vs T+5 vs T+20 vs trailing stop
- C4 Market allocation: ≤60% (baseline) vs uncapped vs risk-parity

### Methodology
Walk-forward both directions. Benjamini-Hochberg FDR q=0.10. Sharpe primary, CAGR secondary.

### Caveat
C assumes per-trade edge is real AND A finds selection edge. If A says DEAD, C is moot for v2.

---

## Verification Contract
Pre-registered, placebo included, no-look-ahead audited, self-tests pass, machine-readable JSON, branch-only, Undercover verifies before deploy.

**Approved by:** Kenneth Lui ("do all" 2026-10-19 23:02 HKT)

---

# Amendment A — the construction the spec did not specify

**Written:** 2026-10-19, before any Direction A run. The spec fixes the
question, the arms and the decision rule. It does not fix the arithmetic of
the portfolio, and six details in that arithmetic can each move the answer.
They are fixed here, in advance, and the run is bound by this section.

**A1 — The portfolio is a set of daily cohorts.** A cohort is formed on every
session from that session's basket, takes 1/10 of the portfolio's equity at
that moment, is equal-weighted within itself, and is liquidated at the close
of the session it reaches age 10. Capital therefore rotates one cohort per
session and the book is fully invested from the eleventh session onward. The
first ten sessions are a ramp, and exposure is measured after it.

**A2 — A session with no selection falls back to the whole universe.** The
v2 screen selects nothing on **45% of sessions** (1,141 of 2,534). The spec
requires both arms to be exposure-matched at 100%, and the control arm holds
the whole universe by definition, so an empty selection day has to fall back to
the universe basket or the arms are not comparable. Arm 1 is therefore "hold
the screen's picks where it has an opinion, hold the universe otherwise". The
fallback count is reported, and a cash-fallback variant is reported as a
sensitivity, because a 45% fallback is a material design choice rather than a
detail.

**A3 — No stop in either arm.** The stop is anchored to S1. The control arm
holds the entire universe, most of which never touches S1 and therefore has no
level to anchor a stop to; a stop cannot be applied symmetrically. Removing it
is the only option that leaves the two arms comparable, and Sprint 1's stop
sweep (2x / 3x / 4x) separately found no stop setting clearing its own
placebo bar, so the stop is not where the edge was hiding.

**A4 — The fill is the signal session's close, with a one-session lag reported
as a sensitivity.** The signal uses information through t-1 plus session t's
own bar, and the fill is at t's close. That is the spec's plain reading of
"buy at CLOSE". A fill at the close that triggered the rule is not executable
in the strict sense, so `fill_lag = 1` — the same signal, filled at the next
session's close — is run and reported alongside. If the two disagree, that is
reported; the primary is the spec's reading.

**A5 — Costs are 0.25% round trip, charged on both legs of every fill.** The
portfolio rolls one cohort per session, so the book pays one round trip per
session, which is the spec's cost figure applied per rotation.

**A6 — Glitch and PIT handling.** Names whose bars are known-bad are excluded
from BOTH arms on the sessions whose features or prices they would poison, on
the same rule the shipped engine uses (a candidate is dropped when a glitch
date falls in its [entry - 304d, exit] window). The universe is the same file
for both arms, so the comparison carries no survivorship asymmetry between
them; both inherit the universe's own survivorship, which is stated rather
than fixed.

**A7 — Two arms beyond the spec, both reported, neither replacing the spec's
decision rule.** An `arm1_nogate` (v2's screen with the MA200 regime gate
removed) and a placebo arm for each. The no-gate arm exists because Sprint 2
established the gate to be significantly worse than random, and Sprint 3
showed the no-gate configuration still loses to a random screen; testing the
gate-on configuration alone would leave the most promising configuration
untested. The placebo is the spec's own guardrail: same name count per
session, names drawn at random from the universe.

**A8 — 300 placebo trials, seed 20261019**, the same count and seed as
Sprint 1, 2 and 3.

# Amendment B — how each outcome is read, committed before the run

The spec's decision rule is applied as written for the headline verdict:
**Arm 1 beats Arm 2 by at least +1.0pp/yr with a consistent sign in both
sub-periods.** The placebo is the spec's guardrail, and it decides what that
verdict MEANS. Both are reported, and the report must say which reading
applies.

- **SELECTION_REAL** — Arm 1 clears the +1.0pp/yr threshold in both windows
  AND beats its own placebo null at z >= 1.96 in both windows. The selection
  adds value over holding the universe, and the value is not luck. Direction B
  is authorised; the operational conclusion is that the plumbing was the
  problem.
- **SELECTION_LUCKY** — Arm 1 clears the threshold in both windows but does
  NOT beat its own placebo null. The screen picked names that happened to
  work; a random draw of the same size would have done as well or better.
  Direction B is NOT authorised on this evidence. This reading exists because
  a threshold rule that passes while its placebo fails is exactly the failure
  mode this project has been guarding against since Sprint 1, and the spec
  lists the placebo as a guardrail rather than as decoration.
- **SELECTION_DEAD** — Arm 1 does not clear the threshold in both windows.
  The screen adds nothing over holding the universe, in either gate
  configuration. Direction D is triggered: the v2 real-money plan stops.
- **VOID** — any cell whose null has sd = 0, or whose arms differ in exposure
  by more than 1pp, is VOID: not a pass and not a failure. A void cell is
  reported with its reason and carries no verdict.

**Disclosure.** This amendment was written before the run and no Direction A
output had been read. It is not blind to the prior results: Sprint 3 already
found that a deployment-matched screen loses to a random draw, so the prior is
that SELECTION_LUCKY or SELECTION_DEAD is the likely outcome. That is exactly
why the SELECTION_REAL branch is spelled out in full, and why the `arm1_nogate`
arm is included even though the spec does not require it.

## Direction A — outcome, 2026-10-19

300 placebo trials, seed 20261019, 179 names, 2534 sessions (2016-09-01 to
2026-10-01), 0.25% round trip, 10-session hold.

### The arms

| | arm 2 — the universe | arm 1 — v2's screen | arm 1, no gate |
|---|---|---|---|
| CAGR 2016-20 | 17.71% | 20.69% | 19.48% |
| CAGR 2021-26 | 16.84% | 11.72% | 12.26% |
| Sharpe 2016-20 | 0.86 | 0.96 | 0.94 |
| Sharpe 2021-26 | 0.93 | 0.67 | 0.72 |
| **gap vs the universe** | — | **+2.98 / −5.12** | **+1.77 / −4.58** |
| clears +1.0pp/yr in both windows | — | **False** | **False** |
| beats its placebo null | — | False | False |

Full sample, reported for comparison with the site's published figures rather
than for the decision: the universe arm compounds at 17.01% and v2's screen at
15.39%, a gap of −1.62pp/yr.

### The placebo

| arm | window | gap vs universe | null (random picks, same count) | z | percentile |
|---|---|---|---|---|---|
| arm 1 | 2016-2020 | +2.98 | +0.02 (sd 2.508) | +1.18 | 87.7% |
| arm 1 | 2021-2026 | −5.12 | −0.23 (sd 2.950) | −1.66 | 4.3% |
| arm 1, no gate | 2016-2020 | +1.77 | −0.17 (sd 2.319) | +0.84 | 79.7% |
| arm 1, no gate | 2021-2026 | −4.58 | −0.51 (sd 2.910) | −1.40 | 8.7% |

A random draw of the same number of names, on the same sessions, deployed the
same way, scores close to the universe in both windows. v2's screen is above
that null in 2016-20 and below it in 2021-26, and neither difference reaches
|z| = 1.96. The null has power in all four cells, so this is a measurement and
not a shrug.

### The pre-registered decision

**SELECTION_DEAD** for the spec's arm, and **SELECTION_DEAD** for the no-gate
arm. Neither arm clears the +1.0pp/yr threshold in both sub-periods, so
Amendment B's SELECTION_LUCKY branch does not apply either — that branch
requires the threshold to be cleared first. The verdict does not depend on the
placebo: the screen fails the spec's own rule before the placebo is consulted.

### What the fallback hides

**POST-HOC DIAGNOSTIC — not part of the pre-registered decision rule, it has no
threshold of its own, and it does not change the verdict.** The question it
asks is this: does the screen beat random names of the same count on the
sessions where it has an opinion? The pre-registered rule applies to the
BLENDED arm, so this is a diagnostic on top of it, reported because "a real
edge that the fallback diluted below the threshold" is a reading of the result
a reader is entitled to see tested.

The screen has an opinion on 1393 of 2534 sessions (55.0%), picking 3.8 names
on average. On those sessions alone — cash on the rest, the same engine, the
same cohorts, the same exposure (46.92% against the universe's 46.92% in
2016-20) — the screen's picks compound at 6.80% against the universe's 4.14% on
the same days in 2016-20, a gap of +2.66pp/yr; and −4.83pp/yr in 2021-26.

| window | screen on its own days | universe on the same days | gap | null (random, same count) | z | percentile |
|---|---|---|---|---|---|---|
| 2016-2020 | 6.80% | 4.14% | **+2.66** | −0.12 (sd 2.206) | +1.26 | 90.3% |
| 2021-2026 | 5.38% | 10.21% | **−4.83** | −0.30 (sd 2.776) | −1.63 | 4.3% |

**The screen's picks do not beat random names of the same count on the sessions
where it has an opinion.** The random draw of the same size on the same days
scores −0.12 in 2016-20 and −0.30 in 2021-26, and neither window reaches |z| =
1.96. So the screen does not beat random picks of the same count even on the
days it has an opinion, which means the headline gap is not a real edge being
diluted by the fallback. That closes the alternative reading this diagnostic
was run to close.

### The fill-lag sensitivity

Amendment A4 requires the same signals filled at the next session's close to be
reported. It changes nothing: the lag-1 run returns SELECTION_DEAD for the
spec's arm and SELECTION_DEAD for the no-gate arm, with gaps of +2.32 / −5.13
against the same-close fill's +2.98 / −5.12. The result does not rest on the
same-close execution assumption.

### Validity

- **Exposure matched**: the two arms' average invested fraction differs by
0.012pp in 2016-20 and 0.007pp in 2021-26. The arms are comparable on
deployment, so the gap is not an exposure artefact.
- **No cell is void**: all four placebo nulls have non-zero spread (2.508,
2.950, 2.319, 2.910), so every cell is a measurement rather than a shrug.
- **No look ahead**: the independent audit (`scripts/edge_alpha_audit.py`)
passes 14/14 checks. What it covers: the screen's selections are unchanged when
the data is truncated at a date (the market filter is truncated with it);
corrupting a far-future bar changes nothing before it; the S1 level on date t
equals min(low[t-20..t-1]); the market filter at t is recomputed from an index
truncated at t-1 and comes out the same; the portfolio's equity curve is
unchanged when the whole pipeline is rebuilt on data truncated at a date; each
of those checks is paired with a power check, so a filter or a screen that
never moves cannot make them vacuous.
- **What the audit does not cover**, so the claim is not read as more than it
is: the screen's feature definitions (a feature that is shifted by the wrong
amount can still be self-consistent); the prefix and shock tests are what cover
the composed pipeline; the fill LAG's execution semantics, which the engine
self-test suite covers rather than this audit.
- **Glitch mask coverage**: the repository's glitch mask contains no symbol
from the US universe, so it removes 0 name-days here and is a no-op for this
market. The same is true of the shipped close-hold A/B. Saying "glitch applied"
would imply a protection that is not there, so it is stated.
- **The universe fallback bites**: the v2 screen selects nothing on 1141 of
2534 sessions, so arm 1 holds the universe on 45% of sessions and its own picks
on the rest. That is Amendment A2, and it means arm 1 is "the screen where it
has an opinion, the universe otherwise".

### What this means

Direction A asked whether the portfolio shortfall came from the SELECTION or
from the PLUMBING. The plumbing is now gone — full deployment, no slots, no
limit-fill asymmetry, no sizing friction — and the screen still does not beat
holding the same universe. So the shortfall is the selection, not the plumbing.

**Direction B is not authorised.** **Direction D is triggered**: the v2
real-money plan stops. In this repository that means no flag to turn off —
every Sprint 2 flag is OFF and no production flag was moved at any point — and
it means the site's own beginner FAQ, which tells a reader to paper-trade for
30 days and only start real money at a hit rate of at least 50% and a profit
factor of at least 1.5, is now contradicted by the evidence. That guidance is a
public position rather than a deployed plan, but it is the position this
evidence contradicts, and the site is where the contradiction lands.

**What is not refuted.** The per-trade close-hold edge (four cells, z 8.9-13.2,
all powered) was measured per trade, in the limit-fill vehicle, and Direction A
does not touch it. A real per-trade advantage that neither a 10-slot book nor a
fully-invested equal-weight portfolio can express is a third possibility, and
it is the one thing this programme has not yet tested.

**One caveat on the vehicle.** Arm 1 holds the universe on every session where
the screen has no opinion, which is 45% of them, so arm 1 is not a pure
concentrated bet on the screen's picks — it is the screen layered over a
passive hold. A vehicle that held cash instead on those sessions would have a
different exposure, and the spec's requirement that both arms be 100% invested
is what rules that variant out of the headline.

---

# Amendment C — Direction C's dimensions, defined before implementation

**Written:** 2026-10-19, before any Direction C run. The spec lists the four
dimensions and their levels but does not define what each level MEANS, and
"what it means" is most of the experiment: a sizing rule that is not spelled
out is not a level, it is a suggestion. Every definition below is binding.

**Common to every cell.** The close-fill engine and the v2 screen with the MA200
regime gate ON (the shipped configuration). Cost 0.25% round trip. Fully
invested, equal-weighted by default. Each cell changes exactly ONE dimension
from the baseline cell; the baseline is `risk1pct / max10 / t10 / cap60`.

**C1 — Sizing.** The weight of each name within its cohort.
- `risk1pct` (baseline): v2's own rule. w_i proportional to
  min(0.01 / (3 x ATR%_i), 0.10) — 1% of equity risked per position, 10%
  notional cap — then normalised to the cohort's capital.
- `equal`: w_i = 1/k. Fixed fractional in the only sense that is defined
  without a per-name edge estimate.
- `kelly`: w_i proportional to that name's own Kelly fraction
  f_i = p_i - (1 - p_i) / b_i, from ITS OWN prior close-fill signal outcomes
  (expanding window, strictly prior data only), clipped to [0, 0.5]. A name
  with fewer than 20 prior signals falls back to the equal weight for that
  cohort, because a Kelly fraction from six observations is noise.
- `volscaled`: w_i proportional to 1 / ATR%_i, capped at 10x the equal weight.

**C2 — Concentration.** The maximum number of NAMES held at any moment across
all cohorts.
- `max10` (baseline), `max5`, `max20`, `unlimited`.
- When a cap binds, the names kept are those with the highest 5-day return at
  the signal date; ties broken by symbol. The ranking is pre-registered
  because "which names survive" is otherwise an unstated degree of freedom
  that would silently favour whichever rule happened to be coded.

**C3 — Holding.**
- `t10` (baseline), `t5`, `t20`: the cohort's age at liquidation.
- `trailing`: the cohort exits at the close of the FIRST session on which the
  cohort's value has fallen 2 x ATR% below the highest value it reached since
  entry; if that never happens, it exits at t10. Close-based, so it is
  computable from the same bars as everything else and needs no intraday data.

**C4 — Market allocation.** The fraction of the portfolio held in equities.
- `cap60` (baseline): a cohort takes 60% of 1/10th of equity, so the book is
  at most 60% invested and 40% sits in cash.
- `uncapped`: a cohort takes the full 1/10th, so the book is 100% invested.
- `riskparity`: the cohort's capital is scaled by
  clip(target_vol / trailing_60d_realised_vol, 0.5, 1.0), where target_vol is
  the realised volatility of the `cap60` configuration over the same trailing
  window, and the trailing volatility uses only PAST returns.

**Walk-forward, both directions.** Every cell is evaluated on rolling
out-of-sample windows of 2 years with a 1-month step: forward from the start
of the sample, and backward from the end. Forward and backward windows are
pooled. A cell's score is the Sharpe of the pooled out-of-sample monthly
returns. **Sharpe is primary; CAGR is secondary**, as the spec says.

**Benjamini-Hochberg.** The 16 cells (4 dimensions x 4 levels) are tested
against the baseline cell on the OOS window level. A cell's p-value comes
from a 12-month block bootstrap (2,000 resamples, seed 20261019) of the
difference in monthly returns between the cell and the baseline. The 16
p-values are adjusted by Benjamini-Hochberg at q = 0.10. A cell is
"significant" only if its adjusted q is at or below 0.10 AND the point
estimate favours it on the primary metric (Sharpe).

**Caveat carried from the spec, and it is not a formality.** C assumes the
per-trade edge is real AND Direction A finds selection edge. If A returns
SELECTION_DEAD, C's result is moot for v2: a portfolio-construction
optimisation of a screen with no edge is an optimisation of noise. C is still
run, because the specification says to run it in parallel and because a cell
that only looks good when the screen is dead is itself worth knowing. But no
C result is published as a v2 recommendation unless A has returned
SELECTION_REAL.

**The thing C must not do.** C is a search over 16 configurations, so the
chance of one of them looking good by luck is not small — that is exactly
what the FDR correction is for, and it is why the primary metric is Sharpe
rather than CAGR, and why the correction is applied across all 16 cells
rather than within each dimension.

---

## Direction C — outcome, 2026-10-19

16 cells, one dimension moved at a time from the baseline `risk1pct / max10 /
t10 / cap60`. US, 2016-09-01 to 2026-10-01, 179 names, 2534 sessions, cost
0.25% round trip. Scored on pooled out-of-sample monthly returns: 122 months,
Sharpe primary, CAGR secondary. p-values from a 12-month block bootstrap, 2000
resamples, seed 20261019, adjusted across all 16 cells by Benjamini-Hochberg at
q = 0.10.

### The grid

| cell | what it moves | Sharpe | vs base | CAGR | vs base | exposure | p | q | verdict |
|---|---|---|---|---|---|---|---|---|---|
| `baseline` | the baseline itself | 1.0296 | +0.0000 | 18.25% | +0.00 | 53.89% | 1.0000 | 1.0000 | void (same as baseline) |
| `C1_risk1pct` | the baseline itself | 1.0296 | +0.0000 | 18.25% | +0.00 | 53.89% | 1.0000 | 1.0000 | void (same as baseline) |
| `C1_equal` | C1 sizing: equal weighting | 1.0298 | +0.0002 | 18.25% | +0.00 | 53.89% | 0.4768 | 0.6935 | favours the cell, not significant |
| `C1_kelly` | C1 sizing: each name's own Kelly fraction | 1.0111 | −0.0185 | 17.16% | −1.09 | 50.15% | 0.0465 | 0.1062 | worse, not significant |
| `C1_volscaled` | C1 sizing: 1/ATR% weighting | 1.0566 | +0.0270 | 18.80% | +0.55 | 53.76% | 0.1374 | 0.2443 | favours the cell, not significant |
| `C2_max10` | the baseline itself | 1.0296 | +0.0000 | 18.25% | +0.00 | 53.89% | 1.0000 | 1.0000 | void (same as baseline) |
| `C2_max5` | C2 concentration: 5 names | 0.7508 | −0.2788 | 6.39% | −11.86 | 27.60% | 0.0070 | 0.0360 | significantly WORSE |
| `C2_max20` | C2 concentration: 20 names | 1.0020 | −0.0276 | 16.12% | −2.13 | 54.80% | 0.1774 | 0.2839 | worse, not significant |
| `C2_unlimited` | C2 concentration: no name cap | 0.8816 | −0.1480 | 8.91% | −9.34 | 60.02% | 0.0610 | 0.1219 | worse, not significant |
| `C3_t10` | the baseline itself | 1.0296 | +0.0000 | 18.25% | +0.00 | 53.89% | 1.0000 | 1.0000 | void (same as baseline) |
| `C3_t5` | C3 holding: 5-session hold | 0.5265 | −0.5031 | 7.00% | −11.25 | 55.67% | 0.0215 | 0.0573 | significantly WORSE |
| `C3_t20` | C3 holding: 20-session hold | 1.0273 | −0.0023 | 8.94% | −9.31 | 26.86% | 0.0140 | 0.0448 | significantly WORSE |
| `C3_trailing` | C3 holding: 2x ATR% trailing exit | 0.7692 | −0.2604 | 4.22% | −14.03 | 21.53% | 0.0025 | 0.0200 | significantly WORSE |
| `C4_cap60` | the baseline itself | 1.0296 | +0.0000 | 18.25% | +0.00 | 53.89% | 1.0000 | 1.0000 | void (same as baseline) |
| `C4_uncapped` | C4 allocation: 100% of the book in equities | 1.0161 | −0.0135 | 29.89% | +11.64 | 89.64% | 0.0020 | 0.0200 | significantly WORSE |
| `C4_riskparity` | C4 allocation: vol-targeted capital scale | 0.9972 | −0.0324 | 26.76% | +8.51 | 80.98% | 0.0090 | 0.0360 | significantly WORSE |

The baseline cell is `risk1pct / max10 / t10 / cap60`: pooled OOS Sharpe
1.0296, CAGR 18.25%, max drawdown −23.97%, mean exposure 53.89%. Five of the
sixteen entries ARE the baseline configuration (the reference cell plus each
dimension's own baseline level), so their difference series has zero spread and
they are void, not p = 0. The other 11 each move exactly one dimension, which
the harness asserts rather than trusts.

### The verdict

**None of the 16 cells is significant after Benjamini-Hochberg at q=0.1 while
also favouring the cell on the primary metric (pooled OOS monthly Sharpe). The
baseline cell (risk1pct/max10/t10/cap60) has a pooled OOS Sharpe of 1.030 and a
CAGR of 18.25% over 122 pooled OOS months, at a mean exposure of 53.9%. The
closest call is C1_volscaled: it favours the cell on Sharpe (1.057 vs 1.030)
but its adjusted q of 0.244 is above 0.1. 5 of the 16 cells are void (identical
to the baseline, so the difference series has zero spread) and carry no
verdict.  The cells that ARE significantly different from the baseline are all
worse on Sharpe: C2_max5 (q=0.036, Sharpe 0.751 vs 1.030), C3_t5 (q=0.057,
Sharpe 0.526 vs 1.030), C3_t20 (q=0.045, Sharpe 1.027 vs 1.030), C3_trailing
(q=0.020, Sharpe 0.769 vs 1.030), C4_uncapped (q=0.020, Sharpe 1.016 vs 1.030),
C4_riskparity (q=0.036, Sharpe 0.997 vs 1.030). On this evidence no change of
portfolio construction beats the baseline once the false-discovery correction
is applied. Amendment C's caveat applies: no Direction C result is a v2
recommendation unless Direction A has returned SELECTION_REAL, and Direction A
is not part of this run.**

No cell clears q = 0.10 while favouring itself on Sharpe: 0 of 16 are
significant on the primary metric, and every one of them is on the WRONG side
of it. The closest call in the cell's favour is `C1_volscaled` at Sharpe 1.0566
against 1.0296, but its adjusted q of 0.2443 is far above the threshold. The 6
cells that ARE significantly different from the baseline are all worse on
Sharpe: `C2_max5` (q 0.0360, Sharpe 0.7508), `C3_t5` (q 0.0573, Sharpe 0.5265),
`C3_t20` (q 0.0448, Sharpe 1.0273), `C3_trailing` (q 0.0200, Sharpe 0.7692),
`C4_uncapped` (q 0.0200, Sharpe 1.0161), `C4_riskparity` (q 0.0360, Sharpe
0.9972).

### Three things this grid did measure

**1. The sizing rule v2 uses is doing nothing.** Replacing the ATR-proportional
weight with a flat equal weight changes the pooled Sharpe by +0.0002 and the
CAGR by +0.00pp — inside the noise, and not significant (p 0.4768). The only
sizing level that moves Sharpe at all is `volscaled`, and it moves it by
+0.0270 with q 0.2443, which is a look, not a finding. So whatever v2's
per-position sizing is worth, it is worth about nothing at the portfolio level,
in either direction.

**2. The 60% cap is a risk-budget lever, and it is the only knob with a large
effect.** `C4_uncapped` runs at 89.64% mean exposure instead of 53.89%, and
compounds at 29.89% instead of 18.25% — +11.64pp/yr more — while Sharpe moves
−0.0135 (1.0161 against 1.0296) and the drawdown widens from −23.97% to
−37.24%. `C4_riskparity` is the same trade with the scaling done dynamically
(80.98% exposure, 26.76% CAGR, Sharpe 0.9972) and is slightly worse again. The
pre-registered primary metric is Sharpe, so neither qualifies as significant;
but this is the grid's one real finding, and it is a fact about capital
deployment, not about signal quality. More capital in the same strategy is more
return and more drawdown, and the Sharpe barely notices.

**3. Half of the significantly-worse cells are capital-deployment effects, not
signal effects, and the two kinds have to be read apart.** Sharpe is invariant
to how much capital a portfolio deploys, so a cell can show a large CAGR
difference and a flat Sharpe simply by holding less or more of the book.
Splitting the 6 significant cells on the Sharpe delta (threshold 0.05):

- **Deployment effects** (Sharpe within 0.05 of the baseline, so the return stream is the same and only the capital deployed changed): `C3_t20` (Sharpe −0.0023, CAGR −9.31pp, exposure 26.86% against 53.89%), `C4_uncapped` (Sharpe −0.0135, CAGR +11.64pp, exposure 89.64% against 53.89%), `C4_riskparity` (Sharpe −0.0324, CAGR +8.51pp, exposure 80.98% against 53.89%).
- **Genuine degradations** (Sharpe materially down): `C2_max5` (Sharpe −0.2788, CAGR −11.86pp, exposure 27.60%), `C3_t5` (Sharpe −0.5031, CAGR −11.25pp, exposure 55.67%), `C3_trailing` (Sharpe −0.2604, CAGR −14.03pp, exposure 21.53%).

`C3_t20` is the purest case of the first kind: lengthening the hold to 20
sessions leaves the book unable to fill (exposure 26.86% against 53.89%)
because a cohort's capital is 1/hold of the book, so its CAGR falls by 9.31pp
while its Sharpe moves −0.0023 — nothing. Its q of 0.0448 says "these two
portfolios behave differently"; it does NOT say "a 20-session hold is a worse
signal". `C3_t5` is the cleanest of the second kind: a 5-session hold instead
of 10 costs 0.5031 of Sharpe while its exposure actually RISES to 55.67%, so
nothing about capital deployment explains it. The name cap and the hold length
are not independent dimensions — the cap applies across all cohorts while a
cohort's capital is 1/hold of the book — which is why per-cell exposure, names
held and fills per session are reported alongside the Sharpe.

### Two limits on what this grid can say

**The bidirectional walk-forward does not double the evidence on a contiguous
sample.** The forward chain and the backward chain enumerate the SAME 99
windows in opposite order, and their union covers all 122 months of the sample
— so the "pooled OOS" series is the sample's own monthly return series, and the
122 months are NOT held-out data. The word "out-of-sample" here means "scored
on rolling windows rather than on the whole curve", which is a statement about
the scoring, not about unseen data. Only two levels have any fitted parameter
at all — `kelly` and `riskparity` — and both are strictly past-only by
construction, with prefix-consistency checks in the self-test suite that
rebuild the market truncated at three dates and confirm nothing at or before
the cut moves.

**The Kelly level is largely inert, so its result is not a verdict on Kelly.**
Of the 201,603 name-day weights, 159,563 (79%) fall back to equal weight
because the name has fewer than 20 prior signals — mostly on the
universe-fallback days, where the names have short signal histories. The cell
is therefore mostly the `equal` cell. Where Kelly weights do apply they cost a
little: Sharpe −0.0185, CAGR −1.09pp, q 0.1062 — just above the threshold, so
not significant either. Read it as "adaptive per-name sizing did not help
here", not as a clean test of the Kelly idea.

### What this means

Direction C asked whether a better-built portfolio could rescue v2. It cannot.
Eleven single-dimension changes to the construction (the other five grid
entries are the baseline configuration itself), each scored out-of-sample and
corrected for multiple testing, produce nothing that beats the baseline — and
half of the cells that differ significantly do so because of how much capital
they deploy, not because of what they select.

**Amendment C's caveat is binding here.** C assumed the per-trade edge is real
AND that Direction A would find selection edge. Direction A returned
SELECTION_DEAD, so no Direction C result is a v2 recommendation, and this grid
is an optimisation of noise for v2's purposes. It is worth having run, for the
reason the pre-registration gave: a portfolio-construction result that only
looks good when the screen is dead is itself worth knowing, and now we know it
does not look good even then.

The Direction C run itself does not read Direction A's output — the verdict
quoted above is the Direction C artifact's own statement, written when it ran.
The caveat is applied HERE, in this pre-registration, using the Direction A
result recorded in the Direction A outcome above.

**The one fact worth carrying forward is not a recommendation either.**
`C4_uncapped` is a measured description of the baseline's own risk budget: at
full deployment the same strategy compounds 11.64pp/yr faster with the Sharpe
essentially unchanged (1.0161 against 1.0296) and a much deeper drawdown
(−37.24% against −23.97%). Whether that trade is worth making is a risk
decision about the book, not a claim about v2's edge — and it is only relevant
if there is a v2 edge to deploy, which Direction A says there is not.


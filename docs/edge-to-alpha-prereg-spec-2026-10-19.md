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
passes 9/9 checks, including truncating the whole universe at 5 dates with no
change to any selection at or before the cut, and corrupting every bar on
2024-03-22 with no change to any earlier selection.
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
it means the site's stated 30-day paper-trade-then-real-money framing, which is
a public position rather than a deployed plan, is now contradicted by the
evidence.

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

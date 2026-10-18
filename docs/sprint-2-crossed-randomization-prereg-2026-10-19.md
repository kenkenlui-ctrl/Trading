# Sprint 2 — Crossed randomization: which dimension does the edge leak out of?

**Written:** 2026-10-19, before any of it is implemented or run. **Owner:** Kenneth Lui · **Executor:** Mavis

## Why this, and not more OFAT variations

Sprint 1 varied one factor at a time across 11 cells. **0 of 11 cleared** its
own placebo null. Adding more cells in the same family is not a plan, it is
repetition — with 11 cells and a per-cell bar, the family has already told us
what it can tell us.

The open question is not "which setting wins". It is **where the edge is**, if
anywhere. That needs a different shape: instead of moving one dimension and
holding the rest at their real value, keep everything at the real value and
**randomize exactly one dimension** to destroy it. Each destroyed dimension
produces its own null. Read the three together and you know which component of
the rule is carrying, and which is not.

## The three axes

| axis | held fixed | randomized | already done? |
|---|---|---|---|
| **C — entry timing** | stock selection, regime gate, sizing, exit | which session the signal fires on | **yes, Sprint 1.** real loses, z −4.65 |
| **A — stock selection** | entry timing, regime gate, sizing, exit | which symbol, drawn from the same session's eligible pool | no |
| **B — regime gate** | stock selection, entry timing, sizing, exit | ma200+slope+market-up applied on a coin flip per signal instead of always | no |

"Fixed" means: the placebo runs the *real* rule and only replaces the one
component. This follows the design of Zhou, Mu, Chen & Sornette, *Investment
Strategies Used as Spectroscopy of Financial Markets Reveal New Stylized Facts*,
PLOS ONE 6(9) 2011 (PMC3173398), who compare "real traders to random strategies
with respect to timing **that share otherwise all other characteristics**".
The domain differs — Shenzhen 2003 microstructure order flow against our
US daily-bar swing — so their numbers do not transfer. The *design* does.

## Non-negotiable: every null must be deployment-matched

This is not a nicety, it is forced by a measurement made 2026-10-19 while
checking the Sprint 1 null:

| placebo variant | executed trades | exposure | null_mean 16-20 / 21-26 |
|---|---|---|---|
| real rule | 5,261 | 76.9% | — |
| Sprint 1 as coded | 7,280 | 74.5% | +7.88 / +11.95 |
| trade-matched | 5,227 | 71.0% | +6.99 / +10.42 |
| **fewer trades than real** | 4,355 | 68.6% | +3.64 / +8.75 |

Sprint 1's placebo draws `n_draw = len(idx)` — the number of signals *before*
the fill filter — so it executes ~38% more trades than the real rule. Correcting
that recovers about 0.9pp of the gap. **The anti-timing result survives it**: even
a placebo doing *fewer* trades than the real rule and holding 8pp less capital
still returns +3.64 / +8.75 against the real rule's +2.42 / −3.86.

So the effect is real, but roughly 1/6 of the headline gap was deployment.
Every null in this sprint therefore must be **calibrated to the real run's
executed trade count (±5%)**, with `trades` and `avg_exposure_pct` reported for
every cell.

## Pass criteria — fixed now

For each axis A, B, in **each** of 2016-20 and 2021-26:

1. the null is **deployment-matched**: executed trades within ±5% of the real
   run and exposure within ±5pp,
2. `net = real_gap − null_mean > 0`, and
3. `z = net / null_sd ≥ 1.96`.

Both sub-periods required. `z_crit = 1.96`, conventional, not loosened for the
three comparisons.

**VOID rule:** if a null fails condition 1, the cell is **void** — neither pass
nor fail — and is re-run with calibration. A null that deploys more capital and
returns more is not evidence about the axis; it is evidence about capital.

### Falsification — bound to quantities that can move

Per the lesson from the 2026-10-19 Sprint 1 re-run: a falsification clause must
name quantities expected to **change**, or it is decorative.

- **This prediction is falsified** if a null which passes the deployment match
  nonetheless moves `null_mean` by an amount inconsistent with its own measured
  trade count and exposure. That would mean `placebo()` is leaking something
  besides the axis being randomized.
- **The calibration method is falsified** if matching executed trades does not
  bring `null_mean` monotonically toward `real_gap`. If halving the trade count
  leaves `null_mean` unchanged, deployment is not the channel and the
  calibration is decorative.
- **The Sprint 1 result is reopened** if axis A or B shows a null that, once
  deployment-matched, lands *above* the real rule by more than its sampling
  noise. That would mean the "anti-timing" reading was an artefact after all,
  and the Sprint 1 null must be re-derived.

## Predictions, registered in advance

Stated as directional expectations so the run can contradict them:

- **C (timing)** — already measured, real loses (z −4.65). Treat as fixed.
- **A (stock selection)** — expect the null to land **near or slightly above**
  the real rule. If timing is worth nothing (C), the S1 screen cannot be
  selecting good *stocks* either; it should be selecting good *moments*, which
  C says do not exist.
- **B (regime gate)** — expect the null to land **above** the real rule. In the
  Sprint 1 grid, `regime=none` had the best net of any cell (+6.00 / +1.62 vs
  base −5.79 / −16.66), so removing the gate appeared to help. Careful reading:
  `regime=none` also deployed 84.5% vs 76.9%, which inflates both its gap and
  its own null. That is exactly what the deployment match is for.

If A and B both come back "null ≈ real", the honest conclusion is that v2 has
**no component that beats its own null**, and that the close-hold per-trade
edge measured separately (`docs/close-confirmation-prereg-2026-10-18.md`, four
cells, z 8.9–13.2) is real at the trade level without producing a portfolio
that beats equal-weight holding. Those two findings are already consistent;
this sprint tests whether anything else is left.

## Cost and order

Same harness as Sprint 1 (`scripts/signal_search_s1.py`), plus one shuffle
dimension per axis. Sprint 1's run was ~4.8 min per variation for 300 trials,
so **three axes ≈ 15–20 min plus implementation**. Run C is not repeated.

Order: **A first, then B.** If A already shows a large negative that matches C,
B is still run — the axes are independent and stopping early would leave the
regime gate untested, which is the one Sprint 1 hinted might be load-bearing.

## What this does not decide

Rollout. Sprint 2's deploy flags all remain OFF. This sprint is a
measurement, not a ship decision.
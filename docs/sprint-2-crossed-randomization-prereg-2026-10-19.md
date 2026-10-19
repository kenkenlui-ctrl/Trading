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

### Axis B is a different counterfactual from `regime=none` — read them together

Sprint 1 already measured one cell of the regime axis: `regime=none` turns the
ma200 gate **off entirely**. Axis B's null does something different — it
applies the gate **on a coin flip**, so half the time the gate is there and half
the time it is not. "No gate" and "random gate" are two different questions:

- `regime=none` asks *what happens if we never gate*
- axis B asks *whether gating at all is better than not bothering to decide*

Sprint 1 anchors, on net_pp (higher = the real rule fares better against its
own null), from the 300-trial run:

| cell | gate | net 2016-20 / 2021-26 | exposure |
|---|---|---|---|
| base | always on | **−5.79 / −16.66** | 76.9% |
| regime=none | never on | **+6.00 / +1.62** | 84.5% |

**Reading axis B against those two anchors — fixed now, so it cannot be
rationalised afterwards:**

1. **Coin-flip net lands between base and `regime=none`** — the gate is
   *useless but not harmful*. Half the time it helps, half it hurts, netting to
   nothing better than never gating. The correct response is not to remove it
   (removing it also fails the bar) and not to defend it; it is to say the gate
   carries no information.
2. **Coin-flip beats `regime=none` as well as base** — the gate is
   *systematically choosing the wrong side*, not merely failing to help. That is
   an **anti-gate** finding, the same shape as the anti-timing result: the
   component is worse than the null that destroys it. Any prose on the site
   describing the trend filter as protective would have to be withdrawn.
3. **Coin-flip is worse than base** — the null itself is mis-specified, since a
   gate that is on half the time should not lose to a gate that is always on by
   more than sampling noise. **Void axis B and fix the null**; do not report it
   as evidence about the gate.

These anchors are the as-run values, and `regime=none` deploys 7.6pp more
capital than base, which inflates its gap and its null together. Once axis B's
null is deployment-matched, the comparison above is made on **matched** values,
and if that reorders the anchors, the ordering changes with them — not the rule.

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
- **B (regime gate)** — expect **case 1** of the three readings above: the
  coin-flip null lands **between** base and `regime=none`. A gate applied half
  the time should interpolate between always-on and never-on; that is the
  natural expectation and it is what makes case 2 (anti-gate) and case 3
  (broken null) informative rather than assumed. Sprint 1's `regime=none` had
  the best net of any cell (+6.00 / +1.62 vs base −5.79 / −16.66) — but note it
  deployed 84.5% against base's 76.9%, which inflates its gap and its own null
  together. That is what the deployment match is for.

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

---

## Outcome — axis A, 2026-10-19

`scripts/signal_search_s2.py --market us --trials 300 --seed 20261019`

| window | real gap | null mean | null sd | net | z | pct |
|---|---|---|---|---|---|---|
| 2016-2020 | +2.42 | +2.84 | 2.80 | **−0.42** | −0.15 | 46.3% |
| 2021-2026 | −3.86 | +6.10 | 2.63 | **−9.95** | −3.79 | 0.0% |

Deployment matched: null executed **5,261 trades (+0.0%)** at **77.2% exposure
(+0.3pp)** against the real rule's 5,261 at 76.9%. Condition 1 satisfied, so
the cell is not void.

**VERDICT: FAIL.** Stock selection does not beat a random universe name at the
same session, under the same entry mechanics, with the same trade count and
capital deployed.

**The pre-registered direction was right.** The prediction was "the null lands
near or slightly above the real rule". 2016-20 is a dead heat (real gap +2.42
against a null of +2.84, **46.3rd percentile — just above the median**).
2021-26 is not: the real rule sits at the **0.0th percentile**, below every one
of the 300 null trials.

> The percentiles above were originally hand-typed as 38% and 1%. Neither figure
> was in the artifact; both were wrong. They are now read from
> `reports/signal_search_s2_us_axis_a.json` and a gate checks them, because a
> hand-typed copy of a measured number is the failure this project keeps
> meeting — two copies of these numbers already drifted apart once on 2026-10-18.

### Two things that had to be fixed before this number meant anything

**The obvious null is vacuous, and it failed silently.** The first
implementation drew the replacement stock from the *screen's own output* at
each session. That null reproduced the real result to the decimal — sd
**0.000**, net −0.00 — because v2 admits essentially every qualifying name:
**median 2 candidates per entry session**, 413 of 1,384 sessions with only one
eligible name. There is almost nothing to shuffle. A permutation of a multiset
that `walk()` already receives unchanged is a placebo that passes while testing
nothing — the same `rng.permutation` failure as Sprint 1, reached by a
different route.

The null that actually answers the question draws from the **whole universe** at
each real signal session, keeping the entry mechanics and removing the regime
gate, ATR cap and box cap, since those *are* the stock-selection rule under
test. That pool has median 4 names per date.

**A units bug made the first result spectacular and wrong.** An earlier version
stored raw CAGR in the null series while `score()` compared it against `real_gap`
(CAGR − EW) — off by the whole benchmark, ~26pp/yr. It reported the null
beating the rule by 25pp/yr. Caught by an identity control: re-walking the
identical candidate list must reproduce the real gap exactly. It did, which
located the discrepancy. The same class of error as the 2026-10-17 benchmark
defect, caught this time before it was written down anywhere.

`oversample > 1` was measured and rejected: at 2× and 3× the null executes
+98% and +196% more trades, which the VOID rule exists to catch.

### What this adds up to

Axes A and C now agree, and they agree in the same direction:

- **C (timing)** — real loses to its own null, z −4.65.
- **A (stock selection)** — real does not beat a random name, z −0.15 / −3.79.

Axis B is unrun. If it also comes back "null ≈ or above real", the honest
conclusion is that no component of v2 beats its own null, and the close-hold
per-trade edge (four cells, z 8.9–13.2, `docs/close-confirmation-prereg-2026-10-18.md`)
is real at the trade level without producing a portfolio that beats equal-weight
holding. Those findings are already consistent; this sprint is testing whether
anything else is left.

---

## Outcome — axis B, 2026-10-19

`scripts/signal_search_s2.py --axis B --market us --trials 300 --seed 20261019`

The null applies the ma200 + slope gate on a coin flip per candidate: heads
enforces it, tails ignores it. So `P(keep) = 0.5·P(gate) + 0.5`, which places it
strictly between the two Sprint 1 cells it is read against. The regime pool has
12,885 candidates over 1,943 dates; **47% pass the gate**. Kept candidates are
capped at the real run's per-date count so the deployment match holds.

| window | real gap | null mean | null sd | net | z | pct |
|---|---|---|---|---|---|---|
| 2016-2020 | +2.42 | +3.42 | 2.70 | **−1.00** | −0.37 | 27.7% |
| 2021-2026 | −3.86 | +3.56 | 2.49 | **−7.42** | −2.98 | 0.0% |

Deployment matched: **5,261 trades (+0.0%)**, exposure **78.2% (+1.3pp)**.

**VERDICT: FAIL.** The gate does not beat applying it on a coin flip.

### Which of the three readings this is — pre-committed case 1

Placing it against the Sprint 1 anchors on `net_pp`:

| | 2016-2020 | 2021-2026 |
|---|---|---|
| `base` — gate always on | −5.79 | −16.66 |
| **axis B — gate on a coin flip** | **−1.00** | **−7.42** |
| `regime=none` — gate always off | +6.00 | +1.62 |

Coin-flip lands **between** base and `regime=none` in **both** sub-periods.
That is **reading 1: the gate is useless but not harmful.** Not reading 2 — it
does not beat `regime=none`, so there is no anti-gate finding. Not reading 3 —
it does not fall below base, so the null is not mis-specified.

The predicted direction was case 1. Confirmed.

> **Reading 1's wording was too soft, and is corrected here.**
> "Useless but not harmful" was wrong: base (+2.42 / −3.86) and `regime=none`
> (+16.51 / +18.92) are nowhere near each other, so the gate *does* make a
> difference — a large one. On the placebo-adjusted `net_pp` scale the gate
> costs **11.8pp/yr in 2016-20 and 18.3pp/yr in 2021-26**. The full ordering is
> `no-gate > coin-flip > real gate` in both sub-periods, so the gate is the
> **worst of the three options**, not an inert one. Kenneth's reading, not mine.
>
> The three-way test in this document only distinguishes *where the coin-flip
> null lands*. It was never designed to determine the sign of the gate's
> effect, and using it that way understates a large harm as an absence of
> benefit.

> **But the `regime=none` anchor is confounded, and this comparison is not
> safe yet.** `regime=none` executes **12,885 trades at 84.5% exposure** against
> base's **5,261 at 76.9%** — 2.4× the deployment. Its net is measured against a
> Sprint 1 null that did not record trades or exposure, and Sprint 1's placebo
> was not deployment-matched either (+38% trades). So `regime=none`'s
> +6.00 / +1.62 may be exposure rather than the removal of a harmful gate.
> The gate-harm reading stands on the **base vs coin-flip** comparison, which IS
> deployment-matched (5,261 vs 5,261, exposure 76.9% vs 78.2%). The
> `no-gate > coin-flip > gate` ordering is **provisional** until the
> deployment-matched no-gate test below is run.

### What this says about where the 2021-26 damage comes from

Kenneth's prior was that the anti-selection in 2021-26 is the MA200 gate's
doing. The numbers support it as the largest single drag: base net −16.66 versus
`regime=none` net +1.62, so switching the gate off recovers roughly 18pp/yr in
that sub-period and nothing at all in 2016-20.

But it does not rescue anything. `regime=none` still fails its own bar
(z +0.45, from Sprint 1), and the coin-flip null sits above the real rule in
both windows. **The gate is a large drag that costs the strategy most of what
it has in the later period, and removing it does not turn the strategy into
something that beats its own null.**

### The ordering, stated as a positive claim

The correction above is retrospective. The finding, on its own terms:

| regime treatment | 2016-20 gap_pp | 2021-26 gap_pp | trades | exposure |
|---|---|---|---|---|
| real MA200 gate | +2.42 | −3.86 | 5,261 | 76.9% |
| coin-flip the gate (deployment-matched) | +3.42 | +3.56 | 5,261 | 78.2% |
| no gate at all | +16.51 | +18.92 | 12,885 | 84.5% |

`no gate > coin-flip > real gate` in both sub-periods. Stated as a positive
claim: **the MA200 regime filter is the worst of the three options, not an
inert one.** It does not merely fail to help. Against a coin-flip applied to
the same signal pool it loses 7.42pp/yr in 2021-2026 (z −2.98, percentile 0.0),
and that comparison is clean: 5,261 trades on both arms, exposure 76.9% against
78.2%.

Two readings were pre-committed, and the run picks between them:

- **Case 1 — confirmed.** The coin-flip null lands *between* base and the
  no-gate arm (+3.42 / +3.56 against +2.42 / −3.86). The gate is not
  anti-selective; it is simply less good than randomising itself, and far less
  good than not gating at all.
- **Case 2 — not what happened.** The coin-flip landed above the no-gate arm,
  which would have made the gate protective.

Because case 1 held, the protective prose is not withdrawn. It is superseded by
a stronger claim, which is the one above.

> **The no-gate row carries a discount and cannot serve as the
> counter-evidence.** It deploys 12,885 trades at 84.5% exposure against base's
> 5,261 at 76.9% — 2.4x the capital. Its placebo-adjusted net is +6.00 / +1.62
> against a Sprint 1 null that never recorded trades or exposure, so it cannot be
> checked against Sprint 2's deployment criterion. Reading "no gate wins" off
> this row would repeat the exact error this document corrects.
>
> A figure quoted in discussion as 14.21 / 14.42 for the no-gate arm appears in
> no artifact; the measured values are +16.51 / +18.92 as tabulated above.
> Recorded so the discrepancy is not later rediscovered and mistaken for a fresh
> finding.

### The three axes together

| axis | what was randomized | worst z | verdict |
|---|---|---|---|
| C — entry timing | which session | −4.65 | FAIL |
| A — stock selection | which name | −3.79 | FAIL |
| B — regime gate | whether to apply it | −2.98 | FAIL |

No component of v2 beats its own null, in either sub-period, under any of the
three counterfactuals. The close-hold per-trade edge remains real and
separately measured (four cells, z 8.9–13.2). These are not in conflict: buying
better trades is not the same as a portfolio that beats simply holding the same
names, which is what the published HK/US/JP gaps already say (US +0.96pp/yr,
HK −4.36pp/yr, JP −0.34pp/yr at 0.25% cost).

Nothing on the site changes as a result — the published figures already carry
"no portfolio excess return". What is new is the sharper claim: the edge is not
hiding in a component that was simply untested.

---

## Sprint 2, closed

All three axes ran at 300 trials and all three failed. Reading 1 was the
pre-committed case that applied. The family is answered:

| axis | what was randomized | worst z | verdict |
|---|---|---|---|
| C — entry timing | which session | −4.65 | FAIL |
| A — stock selection | which name | −3.79 | FAIL |
| B — regime gate | whether to apply it | −2.98 | FAIL |

**No further randomization is planned.** Repeating this family at higher trial
counts would refine a percentile already at 0.0 in the later sub-period; it
would not change a decision. Axis B closes with the gate identified as actively
harmful rather than inert, and the correct next question is a different one.

What survives, stated plainly:

- The **per-trade fill edge is real** — close-confirmation placebo, four cells,
  z 8.9–13.2, all four nulls with non-zero spread, 4/4 passing. It is measured
  per trade and survives on its own terms.
- The **portfolio does not express it**. Published HK/US/JP excess is US
  +0.96pp/yr, HK −4.36pp/yr, JP −0.34pp/yr at 0.25% cost — and the
  randomization shows that is not for want of testing the components.
- The **gate is worth removing or replacing**, which is a real operational
  finding even though it is not an edge finding.

The next step is **not** another null. It is a deployment-matched test of
no-gate v2 against a naive universe equal-weight benchmark
(`docs/sprint-3-deployment-matched-benchmark-prereg-2026-10-19.md`) — a
comparison against a real benchmark, not a randomization. The remaining open
thread is the second vehicle for the per-trade edge: at 10 slots and a 10-day
hold there is not enough capital deployed for a genuine per-trade advantage to
appear in the portfolio (z 8.9–13.2 per trade goes negative at portfolio
level).

All Sprint 2 flags remain OFF; no production flag was moved at any point in
this work.

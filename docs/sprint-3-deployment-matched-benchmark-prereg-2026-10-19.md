# Sprint 3 — Does the stock screen beat just holding the universe?

**Written:** 2026-10-19, before implementation. **Owner:** Kenneth Lui · **Executor:** Mavis

## The question

Sprint 2 established that no component of v2 beats its own placebo null:

| axis | randomized | worst z | verdict |
|---|---|---|---|
| C — entry timing | which session | −4.65 | FAIL |
| A — stock selection | which name | −3.79 | FAIL |
| B — regime gate | whether to apply it | −2.98 | FAIL |

Axis B left one thing unresolved. The ordering in both sub-periods was
`regime=none > coin-flip > real gate`, suggesting the MA200 gate is not inert
but harmful. **That ordering is confounded and cannot be trusted as it
stands**: `regime=none` executes 12,885 trades at 84.5% exposure against base's
5,261 at 76.9% — 2.4× the deployment — and Sprint 1's placebo, which its net is
measured against, was never deployment-matched (it ran +38% more trades, and did
not record trades or exposure at all).

So the open question is not "does the gate hurt". It is the one underneath it:

> **If the MA200 gate is switched off and idle capital is put to work, does the
> stock screen still add anything over simply holding the same universe?**

If no, then v2 as a *portfolio* has no edge in any configuration, and the
per-trade close-hold edge is real without being a portfolio edge. If yes, the
gate was costing a portfolio result and Sprint 2's B reading is wrong.

## Why naive equal-weight is the right benchmark

Because it is the null a reader already assumes. The universe here is today's
large caps applied backwards, so "hold them all" is a genuinely strong
alternative — the site's own §4.7 already publishes this comparison for the
shipped rule (US +0.96pp/yr, HK −4.36pp/yr, JP −0.34pp/yr at 0.25% cost).

The point of this sprint is that the existing comparison is **not
deployment-matched**: v2 sits in cash the rest of the time, and in a rising
market that under-deployment is most of the difference. A gap measured against
a 100%-invested benchmark answers "did this beat cash", not "did the screen
pick well".

## The construction — deployment matching by design

Rather than adjusting after the fact, the residual is put to work in the
benchmark:

- **Portfolio A (test)** — v2 with the regime gate, as today. Signals take the
  10 slots; idle capital sits in cash.
- **Portfolio B (the test's own variant)** — v2 with the regime gate OFF.
- **Benchmark, matched** — each of A and B blended with the same-universe
  equal-weight series in proportion to its **time-varying** idle exposure. On
  any day v2 has a slot, v2 is invested; on any day it does not, the equal-weight
  index is. So both portfolios are 100% invested every day by construction,
  and the comparison cannot be an exposure artefact.
- **Benchmark, naive** — pure equal-weight, 100% invested, as a reference point
  only. It is reported but not used for the verdict.

Costs: the same 0.16% realistic round-trip the Sprint 1 and Sprint 2 runs used
on US. EW is charged nothing, matching the "held passively" claim already
published on the site.

## Arms

| arm | gate | idle capital goes to |
|---|---|---|
| A — shipped | on | cash |
| B — no gate | off | cash |
| **A-matched** | on | equal-weight index |
| **B-matched** | off | equal-weight index |
| EW (reference) | — | n/a, fully invested |

## Pass criteria — fixed now

For each of A-matched and B-matched, in **each** of 2016-20 and 2021-26:

1. `net = cagr(arm-matched) − cagr(EW) > 0`, and
2. `z = net / sd(cagr(EW − arm-matched_null)) ≥ 1.96`.

<!-- AMENDED — see "Amendment 1" at the end of this file. As written this
     formula is vacuous: if the null IS the equal-weight series then
     EW − null ≡ 0, so sd = 0 and z is undefined. Amendment 1 fixes
     the null's definition. It does not relax any threshold. -->

The null here is the equal-weight series itself: the question is whether the
screen beats the thing a reader would otherwise do, so the null is that thing,
not a shuffle of it. Both sub-periods required. `z_crit = 1.96`, conventional,
not loosened for the four comparisons.

**VOID rule, carried from Sprint 2:** any arm whose realised average exposure
differs from 100% by more than 1pp is void. Both matched arms are 100% by
construction, so this can only fire if the construction is wrong — which is
exactly the case it should catch.

### Falsification — bound to quantities that can move

- **The construction is falsified** if `avg_exposure` for either matched arm is
  not within 1pp of 100%. That would mean the residual was not actually put to
  work, and every number derived from it is void.
- **The exposure story is falsified** if the naive EW gap and the matched gap
  have the same sign and differ by less than 2pp in 2021-26. The whole premise
  is that deployment explains a material part of the gap; if it explains almost
  none, the premise is wrong even if the result stands.
- **Sprint 2's B reading is reopened** if B-matched beats EW by more than its
  sampling noise. That would mean the gate is actively harmful *and* removing
  it produces a real portfolio edge — in which case the "no configuration has
  an edge" summary is wrong and must be withdrawn.

## Predictions, registered in advance

- **A-matched (gate on)** — expect it to stay close to zero or negative. Base
  ran −5.79 / −16.66 net against its own placebo null, and putting idle capital
  into a rising universe adds beta rather than skill.
- **B-matched (gate off)** — expect it to be the better of the two, since
  `regime=none` posted the highest raw gaps in Sprint 1 (+16.51 / +18.92) and
  deployed the most capital. **The prediction is that this improvement is mostly
  the cash sleeve, not the gate's removal.** If B-matched still fails after
  matching, then `regime=none`'s Sprint 1 result was exposure all along and the
  Sprint 2 ordering should be withdrawn rather than confirmed.

## Cost

Same harness. `walk()` already emits a per-date exposure series, so the blend is
a weighting of an existing return series rather than a new simulation. Expected
runtime a few minutes per arm, no placebo trials required.

## What this does not decide

Rollout, and nothing else. Sprint 2's deploy flags all remain OFF. If this
sprint shows neither arm beats equal-weight, the correct response is to publish
that plainly on the methodology page — not to keep adding variations to a
family that has now answered the question three times.

---

---

## Amendment 1 — the null was undefined, so the pass criterion was vacuous

**Written:** 2026-10-19, before any Sprint 3 code was written. The original
pass criterion is left above exactly as first written and is marked in place;
this amendment supplies the definition it was missing.

### The defect

Criterion 2 reads

    z = net / sd(cagr(EW − arm-matched_null)) ≥ 1.96

and the paragraph beneath it says *"The null here is the equal-weight series
itself … so the null is that thing, not a shuffle of it."*

Two problems:

1. `arm-matched_null` is never defined anywhere in this document.
2. If the null **is** the equal-weight series, then `EW − null ≡ 0` on every
   draw. `sd = 0`, and `z` is 0/0. The criterion cannot be evaluated, so the arm
   could neither pass nor fail.

This is the third time this project has walked into a sd = 0 null. Sprint 1's
`rng.permutation` shuffle and Sprint 2's axis A within-date shuffle both
produced placebo distributions with `sd = 0.000` while passing their gates. The
failure mode is specific: a test that cannot move cannot fail, and a gate built
on it reports nothing while looking like rigor.

### The fix — the benchmark is not shuffled; the screen is

The original intent is kept. The **benchmark is the equal-weight series and is
never shuffled.** What supplies the sampling distribution is a randomization of
the thing actually under test:

- At each real signal session, trade a **random universe name** instead of the
  screened one.
- Everything else is held: same session, same entry mechanics (limit fills, the
  close holds), same derived stop, same exit rule, same 10 slots, same 1% risk
  sizing, same costs.
- Removed, because these *are* the selection rule under test: the regime gate,
  the ATR cap, the box cap. This is `build_universe_pool` verbatim — the same
  pool Sprint 2 axis A used.
- The null arm is then blended with the equal-weight series by **its own
  time-varying exposure**, exactly as the real matched arm is, so it is
  deployment-matched to the real arm by construction rather than by adjustment.

With the yardstick fixed at EW inside both arms, this is the standard
"did the screen beat what a reader would otherwise do, or were the picks lucky"
test, and it is the same construction and the same z convention as Sprint 1 and
Sprint 2:

    net = cagr(arm-matched) − mean_j cagr(arm-matched_null_j)
    z   = net / sd_j cagr(arm-matched_null_j)

### What is unchanged

Nothing is relaxed. `z_crit = 1.96`. Both sub-periods are still required. The
VOID rule (1pp from 100% exposure) and all four falsification conditions stand
as written above. No prediction below is revised.

### Power condition, declared before running

`sd_j` must be `> 0` in **all four** cells (two arms × two sub-periods). A cell
with `sd = 0` is **VOID — neither pass nor fail** — and is reported as void,
exactly as Sprint 2 treated its vacuous null. A run that produces a zero-spread
null has not shown that the screen fails; it has shown that the test had no
power, and those are recorded differently.

### Trials and seed, fixed before running

300 trials, seed 20261019 — the same trial count and seed as Sprint 1 and
Sprint 2, so the three sprints' nulls are drawn from one reproducible stream.

---

## Amendment 2 — two construction defects, found in a 12-trial pilot

**Written:** 2026-10-19, after a 12-trial pilot and before the 300-trial run. The
pilot is not a result and no pilot number appears below except as the
before/after evidence that the construction was wrong.

Amendment 1 fixed the null. This amendment fixes the *construction*, which had
two defects that the pre-registration describes but does not specify. Neither
was visible from the identity controls: feeding an arm back as its own null nets
to zero under **either** wrong convention, because the whole series is shifted
identically. Only the hand-arithmetic control in `sprint3_selftests.py` caught
the first one.

### Defect 1 — the residual was weighted by the wrong exposure

`walk()` records equity and exposure **after** that date's exits and entries,
and an arm's profit is realised in one jump on the exit date — the very date on
which post-trade exposure is lowest. Weighting that day's return by that day's
own exposure hands the benchmark the arm's largest single move.

The exposure must be the one held **during** the interval, i.e. the previous
period's reading.

### Defect 2 — the blend mixed resolutions and compared two different grids

`ew_bh` is **monthly** (121 month-ends, median gap 31 days). The trading
calendar is **daily** (2,534 sessions) and `walk()` realises P&L daily. The first
implementation built its grid from `ew.index`, so a whole month of benchmark
return landed on a single day, and — worse — it compared that daily-grid CAGR
against a naive CAGR computed on the event-date grid. Two grids, one comparison.

Fixes:

- the blend grid is the **daily trading calendar**;
- each month's benchmark return is spread geometrically over that month's trading
  days, `(1 + r)^(1/n) - 1`, which reproduces the monthly total exactly and
  leaves the benchmark's own CAGR unchanged;
- the naive comparison is recomputed on the **same daily grid**, so "did matching
  move the gap" is answerable.

### What the two defects were worth

Arm B, 2016-20, matched gap over equal-weight:

| construction | arm B 2016-20 matched gap |
|---|---|
| first implementation (both defects) | +6.33 |
| weighting fixed, still monthly grid | +5.46 |
| both fixed, daily grid (pilot) | +13.30 |

The two defects are **not separately attributable** from the runs on record —
the pilot changed both at once after the first figure looked wrong. The official
300-trial run supersedes all three rows.

### A consequence for Sprint 1's published figure

Recomputing the naive comparison on the daily grid moves arm B's 2016-20 gap
from **+16.51** (Sprint 1's event-grid number) to **+7.70**. The event grid
starts each window at the first signal rather than at the window boundary, and
for this arm that flatters it by roughly 9pp/yr.

This does not contradict Sprint 2's ordering, which was written from `net_pp`
on a single consistent grid. It adds a second, independent reason to discount the
`regime=none` row — that row was already confounded by deployment (2.4x) and is
now also known to be flattered by its window convention. **The ordering stands
on base-vs-coin-flip, which is deployment-matched and grid-consistent; the
no-gate row remains discounted twice over.**

### What is unchanged

No threshold, criterion, prediction or falsification condition from the original
spec or Amendment 1 is altered. `z_crit = 1.96`, both sub-periods required, the
VOID rule at 1pp, and the sd > 0 power condition all stand. The construction is
now correct; the test applied to it is the one already registered.

---

## Amendment 3 — how each outcome is read, committed before the result

**Written while the 300-trial run was in flight, before a single line of its
output had been read.** Sprint 2's axis B learned this the hard way: a
pre-registration with three possible results and no pre-committed rule for which
one applies is an open back door to rationalise whatever comes back.

**Disclosed bias.** The 12-trial pilot had already returned four failing cells.
The readings below are therefore informed, not neutral. That is exactly why the
pass branch is written at length — it is the branch I am least entitled to
improvise, and the one a reader should hold me to.

### If neither arm clears the bar in both sub-periods

The predicted case, and the one the pilot pointed at. The correct statement is
narrow: **the stock screen does not beat holding the same universe, in either
gate configuration, after deployment matching.** It is *not* the claim that the
screen adds nothing under any conceivable structure — Sprint 1's close-hold
per-trade result (four cells, z 8.9–13.2) is separately measured and remains
true. It is also *not* a licence to keep adding variations: three axes have now
answered this, and Sprint 2 is closed.

Published wording stays as it is. The existing HK/US/JP gaps already say there
is no portfolio excess return; Sprint 3 adds that this is not for want of
matching the deployment.

### If arm B — gate off — clears the bar in both sub-periods

The informative surprise, and the one that would reopen Sprint 2. It would mean
removing the gate produces a genuine portfolio edge against equal-weight, and
therefore that Sprint 2's ordering was not merely confounded but **wrong**.

Two readings, and they are not equally likely, so the test is named in advance:

- **Real edge.** B-matched beats its own deployment-matched null at z ≥ 1.96 in
  both windows. Then the MA200 filter is actively destroying value, the
  "no configuration has an edge" summary is withdrawn, and the operational
  recommendation becomes *remove the gate* — not *tune the gate*.
- **Still confounded.** B-matched beats equal-weight but does *not* beat its own
  null. Then the excess is what any fully-deployed screen gets from the universe
  beta, and the correct statement is still "no edge", with the comparison simply
  made fair. The bar is z ≥ 1.96 against the null, never against equal-weight
  alone.

The distinction is carried by `passes`, which requires the z criterion, not by
the sign of the gap.

### If arm A — gate on — clears the bar in both sub-periods

Contradicts Sprint 1's timing result, which found the real rule below its own
placebo in both windows (z −4.65). It would mean the matched construction
revealed something the placebo could not: that the screen's value shows up only
once idle capital is put to work. Before believing it I would check, in this
order, and report each: (a) the null has power in both windows (sd > 0), (b) the
matched exposure is within 1pp of 100%, (c) the null's trade count is within 5%
of the real arm's. If (a)–(c) hold and it still passes, the "no portfolio edge"
summary is withdrawn.

### If any cell is VOID (sd = 0, or matched exposure off 100% by more than 1pp)

Not a pass and not a failure. It is reported as void with the reason, the sprint
returns no verdict, and nothing on the site changes. A void cell must not be
quietly dropped so the surviving cells can carry a conclusion.

---

## Results, outcome — Sprint 3, 2026-10-19

300 trials, seed 20261019, blend grid: daily trading calendar. Universe pool
13,792 candidates over 1,953 dates. Equal-weight on the same grid: 24.99% /
24.03% CAGR.

### The two arms

| | arm A — gate on | arm B — gate off |
|---|---|---|
| trades | 5,261 | 12,885 |
| exposure, unmatched | 76.9% | 84.5% |
| exposure, matched | 100.00% | 100.00% |
| gap vs EW, unmatched (daily) | −2.86 / −3.89 | +7.70 / +18.89 |
| gap vs EW, **matched** | +5.98 / −1.79 | +13.30 / +13.66 |
| null (random screen), matched | +6.77 / +6.56 | +14.37 / +14.37 |
| null sd | 1.979 / 2.135 | 0.877 / 1.097 |
| **net** | **−0.79 / −8.35** | **−1.07 / −0.71** |
| **z** | **−0.40 / −3.91** | **−1.22 / −0.65** |
| null percentile | 35.0% / 0.0% | 10.7% / 26.3% |
| deployment matched | yes | yes |
| clears bar | **no** | **no** |

Report verdict, verbatim: **no arm clears the deployment-matched bar**.

Every cell has power (no sd = 0), both arms are deployment-matched to their
nulls, matched exposure is 100.00% on both, and the scorer identity residual is
exactly 0.00 in all four cells. No cell is VOID, so the sprint returns a
verdict rather than a shrug.

### Which pre-committed reading applies

The first branch of Amendment 3: neither arm clears the bar in both
sub-periods. The second branch — arm B clearing — is what the numbers must be
read against, because arm B is the arm that looked best.

Arm B beats equal-weight by +13.30 and +13.66. That looks like the result
Sprint 3 was built to find. It is not, and the row beneath it says why: the
**same** screen with random names, deployed the same way, scores +14.37 and
+14.37 — higher in both windows. Arm B lands below its own null (z −1.22 and
−0.65, percentile 10.7% and 26.3%).

This is the *still confounded* branch of Amendment 3, and it is the answer to
the question the sprint was opened to ask. The no-gate configuration does not
beat a random screen picked from the same universe. What it beats is the
equal-weight index — and a random screen beats that too. It is universe beta,
paid for by being fully deployed.

### The exposure story survives its own falsification

The spec falsified the premise if the naive and matched gaps had the same sign
and differed by less than 2pp in 2021-26. They do not: deployment moves arm B's
2021-26 gap by 5.23pp (+18.89 to +13.66) and arm A's by 2.10pp. The premise
holds: deployment explains a material part of the gap, which is why Sprint 1's
unmatched comparisons could not settle the question.

### A cross-check on Sprint 1's published figures

Recomputing the naive comparison on the event grid against this run's
daily-grid benchmark reproduces Sprint 1 to the rounding: arm A +3.52 against
+2.42, and arm B +17.61 against +16.51 — both differing by 1.11pp, exactly the
gap between the monthly and daily benchmark CAGR. The arms' own returns are
reproducible; the window convention is not neutral, and Amendment 2's ~9pp/yr
correction to the no-gate row stands.

### What this settles, and what it does not

**Settles.** The stock screen does not beat holding the same universe, in
either gate configuration, once deployment is matched — including the
configuration that looked best. The MA200 gate is not the reason the strategy
fails to produce excess return. Removing it, and putting the idle capital to
work, still leaves the screen below a random draw from the same universe.

**Does not settle.** The per-trade close-hold edge is untouched: four cells, z
8.9–13.2, all powered, measured per trade. Sprint 3 does not contradict it. A
genuine per-trade advantage that the 10-slot, 10-day structure cannot express
is a different problem from a screen with no advantage, and the second vehicle
for that edge is still unbuilt.

**Operational note, separate from the edge question.** Arm A's matched 2021-26
z of −3.91, and arm B's materially better absolute return, both point the same
way as Sprint 2's axis B: the MA200 gate is the worst of the options available.
Sprint 3 does not weaken that. It shows the no-gate arm is better and still not
an edge.

---

## Sprint 3, closed

No arm clears the deployment-matched bar. All four cells are powered, matched
and negative. **Sprint 3 closes the question of whether the screen has
portfolio edge: it does not, in any configuration tested.** The remaining
thread is not another comparison against equal-weight and not another
randomization. It is finding a vehicle in which a real per-trade edge can be
expressed.

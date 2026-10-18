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
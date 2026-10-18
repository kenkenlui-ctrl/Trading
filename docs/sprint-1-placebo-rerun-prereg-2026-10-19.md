# Sprint 1 placebo re-run — pre-run note

**Written:** 2026-10-19, before the re-run. **Owner:** Kenneth Lui · **Executor:** Mavis

## Why this is being re-run

Sprint 1's placebo-adjusted bar rests on a null measured on **2026-10-16**: a
baseline placebo of **+8.62pp/yr** and a `base` real gap of **−3.96pp**
(z = −4.13). That measurement predates the **2026-10-17** equal-weight benchmark
correction.

The defect was in the benchmark:

```python
mm = src.pct_change().groupby([cal.year, cal.month]).transform("mean").mean(axis=1)
```

`transform("mean")` broadcasts each calendar month's mean *daily* return back
onto every session in that month, including the first — whose `pct_change()` is
genuinely `NaN`. The benchmark therefore manufactures one full day of return
out of nothing, every month, and compounds the arithmetic mean daily return
instead of the realised path. Worth roughly `n·σ²/2` per month; measured on this
repo's own universe as **+2.47pp/yr on us200** (see `scripts/measure_ew_bias.py`).

`scripts/hk_retest.py::ew_bh` was fixed on 2026-10-17.

**But `scripts/signal_search_s1.py` kept its own copy of the defective
function** (`signal_search_s1.py:305`, line 314). This is the same failure the
2026-10-18 P0 audit found elsewhere in the repo — one model must not have two
independent implementations. It is now going to import the canonical one.

## What this re-run can and cannot change

Both `gap_pp` and the placebo's per-trial gap subtract the SAME benchmark
series, and that series is computed once, outside the trial loop. Writing
`EW` for the benchmark CAGR and `P_t` for a placebo trial's portfolio CAGR:

```
real[w]        = C_real  − EW
null[w][t]     = P_t     − EW
net_pp         = (C_real − EW) − mean(P_t − EW) = C_real − mean(P_t)
null_sd_pp     = sd(P_t − EW)                   = sd(P_t)
null_percentile = P(P_t − EW < C_real − EW)     = P(P_t < C_real)
```

**Pre-registered prediction: the benchmark cancels out of every decision
variable.** So on the corrected benchmark:

- `verdict`, `clears_placebo_bar`, `passes`, `worst_net_pp`, `worst_z` —
  expected **unchanged**
- `net_pp`, `z`, `null_sd_pp`, `null_percentile` — expected **unchanged**
- `real_gap_pp`, `null_mean_pp`, `ew_cagr_pct` — expected to **move**, all three
  by the benchmark delta, and `real_gap_pp` should move by the *opposite* sign to
  `null_mean_pp` (the benchmark was inflated, so the strategy's published gap
  was understated)

The figures that move were **wrong before this re-run**. That is the point of
running it: not to look for a better result, but to stop publishing a gap that
was measured against a benchmark that manufactured return.

## What would falsify the prediction

> **Withdrawn as vacuous, 2026-10-19.** Kenneth's objection, which is correct:
> the original clause read "if the verdict, `z` or `passes` changes, the
> benchmark's error was not a constant offset". But the derivation immediately
> above had already shown those three quantities are **invariant by
> construction** under a constant-offset benchmark. So the falsification test
> was bound to a measurement that could not move — it was not falsifiable, it
> was decorative. A gate that cannot fail is not a gate.
>
> **The quantities that DO carry the information** are `real_gap_pp` and
> `null_mean_pp`, which move by the benchmark delta with the same sign and
> magnitude. Those were the ones actually checked, and the check caught a real
> error in this document's own sign prediction.
>
> **Rule for the next pre-registration: bind the falsification clause to
> quantities you predicted will MOVE.** If a quantity is invariant by
> construction, it cannot be evidence — so it cannot be the thing that would
> overturn the conclusion.

A falsifiable version would have been: *"the correction must move
`real_gap_pp` by the same amount and the same sign as `null_mean_pp`, in both
sub-periods. If either fails to move, or they move by different amounts, the
benchmark is being applied inconsistently between the real run and the placebo
trials."*

## Reproduction

```bash
python3 scripts/signal_search_s1.py --market us --placebo 300 --placebo-seed 20261019
```

## Not decided here

Whether any Sprint 1 variation ships. Sprint 2's flags all remain OFF, and the
recommended order is `deploy_gate_flag` → `crossval_flag`. This re-run settles
the measurement, not the rollout.

---

## Outcome — 2026-10-19

### Step 1: invariance check (same seed 20261016, same 30 trials, fixed benchmark)

The sharpest test of the prediction above: if the benchmark really does cancel,
then re-running with **the same seed and the same trial count** must leave every
decision variable bit-identical, because the placebo draws are unchanged and
only `EW` moved.

It did. Across all 11 variations × both sub-periods, `net_pp`, `worst_z` and
`clears_placebo_bar` were **identical to two decimal places**. Verdict
unchanged: nothing cleared.

`EW` itself moved a lot: **27.22%/yr → 24.63%/yr**, i.e. the old benchmark
inflated the buy-and-hold comparison by **2.59pp/yr**.

**One part of the pre-registration was wrong.** It predicted that `real_gap_pp`
and `null_mean_pp` would move by *opposite* signs. They move by the **same**
sign, by the same amount (+2.01pp on the base cell, 2016-20). That is not a
conflict with the prediction above — it is the mechanism behind it: a single
benchmark offset shifts both quantities equally, which is precisely why their
difference is invariant. The prediction stated the right conclusion and the
wrong reason for it. Recorded rather than quietly dropped, because a
pre-registration edited after the fact is not a pre-registration.

### Step 2: production re-run (seed 20261019, 300 trials)

The old run used **30 trials**, which is a thin estimate of a null's spread.
300 was used for the production run. Trial count is not a pass criterion — no
threshold, required cell or interpretation depends on it — and more trials
tighten the null estimate, so this is the conservative direction. Same
reasoning as the amendment to the close-confirmation pre-registration.

**Verdict: nothing cleared. 0 of 11.**

| variation | z_min @30 | z_min @300 | null sd 16-20 / 21-26 | passes |
|---|---|---|---|---|
| base | −4.71 | −4.65 | 4.18 / 3.58 | no |
| s1_10, closehold | −2.31 | −2.05 | 4.38 / 4.28 | no |
| s1_30, closehold | −3.78 | −4.39 | 3.83 / 3.40 | no |
| s1_20, **noclosehold** | −2.62 | −2.84 | 3.64 / 2.92 | no |
| s1_20, stop_2x | −4.86 | −4.50 | 4.68 / 4.19 | no |
| s1_20, stop_4x | −3.63 | −3.71 | 3.52 / 3.09 | no |
| s1_20, **hold_5** | −5.37 | −5.05 | 3.88 / 3.36 | no |
| s1_20, **hold_15** | −3.04 | −3.40 | 4.14 / 3.72 | no |
| s1_20, **hold_20** | −1.87 | −2.28 | 4.20 / 3.67 | no |
| s1_20, cross50_200 | −5.41 | −4.79 | 4.01 / 3.46 | no |
| s1_20, **regime=none** | +0.14 | +0.45 | 3.52 / 3.60 | no |

`EW` benchmark: **27.22%/yr → 24.63%/yr** (the old one overstated the
buy-and-hold comparison by 2.59pp/yr). Output:
`reports/signal_search_s1_us_placebo.json`.

### What the extra 270 trials did, and did not do

They did **not** change the verdict, and the pre-registration had already
predicted why. The closest cell, `regime=none`, moved from z = +0.14 to
z = +0.45 — i.e. **away** from 1.96, not towards it. Its null sd is ~3.6pp, so
reaching z ≥ 1.96 at its measured net of +1.62pp would need sd ≤ 0.83pp, a 4×
reduction that more sampling cannot produce. More trials stabilize the sd
*estimate*; they do not shrink the true sd.

So the value of 300 over 30 is precision on the sd, not a different answer. Had
any cell been genuinely near the bar, the tighter estimate is exactly what you
would want before acting on it.

### Two findings worth keeping from this grid

**The hold sweep answers a question the site had left open.** HOLD 5 / 10 / 15 /
20 give z_min −5.05 / −4.65 / −3.40 / −2.28. The trend is the tell: longer
holds look progressively less bad, which is precisely the beta exposure of a
rising universe that the placebo is built to absorb. Once absorbed, none of the
four clears its own null. **The hold length is not where the edge is**, and this
grid does not support changing HOLD away from 10.

**`noclosehold` is not rescued by this grid either** (z −2.84). That is a
separate result from the close-confirmation A/B in
`docs/close-confirmation-prereg-2026-10-18.md`, which found close-hold clearly
better on a *per-trade* basis across four cells. The two are consistent: the
close-hold requirement improves the trades you take, without producing a
portfolio that beats equal-weight holding.

## Appendix — is "random beats real" an artefact?

Kenneth read the headline correctly and asked the right next question: the base
cell's `null_mean` is **+8.21pp (2016-20) / +12.80pp (2021-26)** while the real
rule's gap is +2.42 / −3.86. At the same sample size, **random entry timing
returned more than v2's entry timing.** That is a claim about the world, so it
needs controls before anyone acts on it — and it must not be dismissed as a bug
either.

**Two candidate mechanisms, both measured rather than argued.**

*Mechanism 1 — the placebo deploys more capital.* Refuted. `placebo()` draws
its pool from any post-warmup session without applying the regime gate, which
looks like extra beta. Measured: placebo exposure **74.5%** against the real
rule's **76.9%** — it deploys *less*. Mechanism dead.

*Mechanism 2 — the placebo executes more trades.* Real, and worth about 0.9pp.
`n_draw = len(idx)` fixes the number of signals *before* the fill filter, so
the placebo executes **7,280** trades against the real rule's **5,261** (+38%).

| variant | trades | exposure | null_mean 16-20 / 21-26 | net |
|---|---|---|---|---|
| real rule | 5,261 | 76.9% | — | +2.42 / −3.86 |
| Sprint 1 as coded | 7,280 | 74.5% | +7.88 / +11.95 | −5.46 / −15.80 |
| trade-matched (×0.72) | 5,227 | 71.0% | +6.99 / +10.42 | −4.58 / −14.28 |
| fewer than real (×0.60) | 4,355 | 68.6% | +3.64 / +8.75 | −1.23 / −12.61 |

Matching executed trades recovers ~0.9pp. **The result survives it.** Even a
placebo doing *fewer* trades than the real rule and holding 8pp less capital
returns +3.64 / +8.75 against the real rule's +2.42 / −3.86.

**Conclusion: the anti-timing signal is real and not an exposure or trade-count
artefact.** It is also not specific to this repo — Zhou, Mu, Chen & Sornette,
*Investment Strategies Used as Spectroscopy of Financial Markets Reveal New
Stylized Facts*, PLOS ONE 6(9) 2011 (PMC3173398), ran the same class of design
on Shenzhen Stock Exchange 2003 order flow — "random strategies with respect to
timing that share otherwise all other characteristics" — and found "random
strategies are found to perform much better than real ones, both for winners
and losers." Different domain, same direction.

This does **not** mean v2 has nothing. It means the *entry-timing* component
does not carry the edge, and that the question of which other component might
is now the live one — see
`docs/sprint-2-crossed-randomization-prereg-2026-10-19.md`.
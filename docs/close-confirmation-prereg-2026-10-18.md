# Close-confirmation pre-registration

**Written:** 2026-10-18, **before** the placebo was implemented or run.
**Owner:** Kenneth Lui · **Executor:** Mavis

## Why this document exists

`scripts/ab_close_hold.py` measures v2's entry qualification A/B: arm A takes
every session that opens above S1, arm B additionally requires the session to
close at or above S1. B wins all four cells (US/JP × train/test) by roughly
1pp per trade with a materially higher date-clustered t.

That result is already load-bearing: `build_v2_signals.py` ships `CLOSE_HOLD =
True`, so this is the rule the live site runs, not a candidate.

The problem is that the A/B has **never had a control**. Two explanations fit
the data equally well:

- **H1 — the close carries information.** A session that touches S1 and closes
  back below it did not hold the level; that trade is a caught break, not a
  bought level.
- **H2 — it is only "trade less".** B keeps 46.6–51.5% of A's trades. Dropping
  the other half for any reason would mechanically raise the average if the
  dropped half is merely average rather than bad.

Nothing in `ab_close_hold.py` distinguishes these. This document fixes the test
**in advance**, so the bar cannot be moved after seeing the answer.

## Hypothesis

**H1 is true.** Closing below S1 after touching it identifies a materially worse
subset than a size-matched random subset of the same candidates.

## Pre-registered test

### Primary control — matched-count subsample (P2)

From arm A's trade list, draw a random subset of exactly the size B keeps in
that market/window. Repeat for N trials with a fixed seed grid. This holds
constant: universe, market filter, entry level, stop, exit, costs, sample
size. The **only** thing that differs from B is *which* trades were selected.

If H2 were true, P2's null mean would sit at or near A's full-sample average and
B would land inside its spread.

### Secondary control — shuffled-entry placebo (P1)

Keep B's trade count per window; replace each trade's session with a session
drawn at random from A's candidate pool. N trials, fixed seed grid.

### Power check — mandatory before any result is read

Both controls must show **non-zero standard deviation**. A placebo whose `sd`
is 0.00 has no power and its gate would pass while testing nothing. This exact
trap was hit in `signal_search_sprint-1-spec.md` (an `rng.permutation` over bar
indices that reordered a set without changing any bar→signal pairing, so every
trial returned the real number to the decimal). **Report `sd` before the gate
result.**

## Pass criteria (fixed now; not adjustable afterwards)

In **each** of US-train, US-test, JP-train, JP-test:

1. `gap = B.mean_net% − placebo.mean_net%` is positive, and
2. `z = gap / placebo.sd ≥ 1.96`

**All four cells must clear both conditions.** A rule that helps on one window is
a window artefact. `z_crit = 1.96` is the conventional value and is **not**
loosened for the four-cell comparison.

## Fail interpretation — pre-committed

- **All four clear** → H1 stands. The close carries information. Publish.
- **Most cells clear, some do not** → report as mixed. Do not average them into
  a pass.
- **B lands inside the P2 null** → **H2 wins**. The close-hold rule is
  "trade less", not "trade better". This must be published as a negative result
  even though the rule is currently live, and the live rule's justification must
  be restated as such.
- **Either control has `sd = 0.00`** → the test is void; fix the control and
  re-run. Do not report a pass.

## Carried over from Sprint 1, deliberately

- **Both sub-periods must agree** (2016–22 train, 2023–26 test).
- **`z_crit` fixed at 1.96**, chosen conventionally and not after seeing the grid.
- **Multiple-comparison disclosure:** the full N-trial distribution is reported,
  not just the mean.
- **Exposure must travel with the gap.** A per-trade comparison is not a
  portfolio claim; Sprint 1 measured the live book at 76.9% average exposure
  against a fully-invested equal-weight benchmark. This document does not make a
  portfolio claim and must not be cited as one.

## Known prior on the control itself

Sprint 1 (2026-10-16) measured a *shuffled-session* placebo at **+8.62pp/yr**
against a real `base` gap of −3.96pp — a null eight points above the signal.
That number predates the 2026-10-17 equal-weight benchmark fix
(`hk_retest.ew_bh`), i.e. it was computed with the defect that inflated
equal-weight returns by 2.0–3.7pp/yr. **It must not be used as a prior for this
test**, and re-running Sprint 1's placebo on the corrected benchmark is a
separate open task.

If the new control returns a large positive null of the same shape, that is
itself evidence that the harness still carries the same bias, and the correct
response is to fix the harness rather than to interpret the null.

## What this does NOT decide

It does not test the 10-day hold. The A/B fixes `HOLD = 10` for both arms, so
it measures the **fill condition**, not the horizon. Whether 10 days is the
right horizon under close-confirmation is still open and is Sprint 1's
`Hold: 5 / 10 / 15 / 20` row.

## Reproduction

```bash
python3 scripts/ab_close_hold.py --placebo 200 --seed 20261018
```

Writes `data/ab_close_hold.json` (extended with a `placebo` block) and prints
the per-cell decision.

## Amendment — 2026-10-18, after the run

Recorded here rather than by editing the text above, because a pre-registration
whose terms are changed after the result is known is not a pre-registration.

**What actually ran:** `--placebo 300 --seed 20261018` (not the 200 written in
the reproduction block above).

**The one thing that changed:** the number of trials, 200 → 300. More trials
tighten the estimate of the null's standard deviation, so this is the
conservative direction — it makes the placebo *harder* to clear, not easier.

**What did not change:** `z_crit = 1.96`, the four required cells, the
`gap > 0` condition, the power requirement, and the fail interpretation,
including "B inside the P2 null → publish as a negative result". The trial
count is not referenced by any pass criterion, so it is not load-bearing.

**Result as run:** 4/4 cells clear, z between 8.91 and 13.19, all four
controls with non-zero sd → **H1**. See `data/ab_close_hold.json`.

**Left undone:** P1 (the shuffled-session secondary control) was implemented in
`ab_close_hold.py::p1_shuffled_sessions()` but not executed. Only the primary
control P2 has been run. Do not describe this test as having two controls.
# Sprint 2 — Website/Pipeline Integrity: Summary

## Verdict first

**All six items (A1–A3, B1–B3) built and verified. 60/60 self-test checks pass,
including 10 must-fail guards each demonstrated BLOCKING on corrupted input.
Production untouched: every flag is OFF and 0 sprint2 identifiers exist in any of
the 9 production build/deploy scripts.**

The headline evidence is B1: the independent second implementation agrees with
the primary on **407 of 407** real bar files — 100.00%, zero disagreements.

Nothing is enabled. Nothing needs to be before you read this.

---

## What is built

| Item | Status | Flag (all OFF) | Evidence |
|---|---|---|---|
| A1 visitor-visible staleness states | done, verified | `staleness_banner_flag` | FRESH/STALE/NOT_RUN/SOURCE_DOWN; the real 58h incident reproduced as STALE |
| A2 fail-closed deploy gate | done, verified | `deploy_gate_flag` | blocks on version mismatch, missing receipt, tampered receipt, vanished input, failed self-test, crossval disagreement, unpinned engine |
| A3 process fingerprints | done, verified | `fingerprint_flag` | append-only JSONL; a receipt joins to its producing run |
| B1 four-gate cross-source validation | done, verified | `crossval_flag` | 407/407 agreement; no-look-ahead probe passes; probe proven to have teeth |
| B2 restatement tracking | done, verified | `restatement_flag` | NEW_SESSION / UNCHANGED / RESTATEMENT distinguished; blast radius from a declared dependency map |
| B3 data-gap receipts | done, verified | `gap_receipt_flag` | gap excluded from candidates **and** denominators; gap vs rule rejection split by construction |

Run it yourself: `python3 scripts/sprint2_selftests.py`

---

## Negative and surprising results, reported with the same energy

**1. My first independent implementation was look-ahead-prone, and my first test passed anyway.**
`independent_plan()` inferred T-1 as "the last row in the list". A caller who
passed today's bar got today's answer — and the initial no-look-ahead probe was
written against that same wrong convention, so it could not fail. Fixed by
taking an explicit `t1_date` and discarding later bars **by date**, so look-ahead
is impossible rather than discouraged. The probe now also feeds a deliberately
extreme T-day bar and asserts the answer does not move, plus a second
construction proving the probe *would* catch a regression.

**2. A gap receipt rejected the very case it was meant to represent.**
`GapReceipt` enforced `rows_returned == 0` on every reason, which made
`RULE_REJECTION` — a ticker the vendor answered fine and the rules declined —
unconstructible. Scoped to source-side reasons only.

**3. The 10-look-ahead probe initially failed for a reason worth naming:**
it passed T-1 as *newer* than *now*, so a 58h-old input classified as FRESH.
A staleness guard that mis-ages its own input is worse than none. Fixed, and
the 58h case is now a permanent regression test.

**4. Two of my first-pass test harnesses reported false disagreement.**
The first counted two matching rejections as a mismatch; the second compared
`entry_s1` against `None` because the primary omits fields on a rejection. Both
were harness bugs, not implementation bugs — worth stating because the naive
reading of the first run was "49.4% agreement", which would have been a
dramatic and completely false headline.

---

## Two things in the spec that are now out of date

Both are disclosed rather than silently absorbed:

- **Spec C1 says "HK retest already reports −8.1pp at 0.25% and −5.9pp at zero
  cost".** Those figures were produced by the equal-weight benchmark defect
  corrected on 2026-10-17. On the corrected benchmark HK is **−4.36pp at 0.25%
  and −2.22pp at zero cost**. C1 should be recomputed against
  `data/hk_retest_summary.json`, not against the numbers quoted in the spec.
- **Spec Background describes the site 58h stale with the Oct 15/16 refreshes
  missing.** That incident's data is now current (all three markets at the
  2026-10-16 close, verified byte-for-byte live). The A1 guard is still
  warranted — it is the absence of the guard that let the incident run 58h.

---

## Scope boundaries held

- No change to v2 strategy rules, universes, or published backtest numbers.
- No production deploy, and no flag turned on.
- No new data source; no Futu work (P1 stays parked).
- No fingerprints or receipts backfilled for historical runs.
- Nothing outside A–C touched; Signal Search Sprint 1 not merged or blocked on.

C1–C3 (website content) were not started: C1's input figures are stale as above,
and C2/C3 depend on work this sprint does not gate.

---

## Files added (nothing else changed)

```
config/integrity_flags.json            all six flags false
config/ingest_dependencies.json        B2 blast-radius map (created by the test, removed after)
scripts/integrity/__init__.py
scripts/integrity/flags.py             flag loader; fail-closed to OFF
scripts/integrity/hashing.py           sha256 helpers shared by A2/A3/B2
scripts/integrity/receipts.py          A2 receipt construction
scripts/integrity/gate.py              A2 fail-closed gate
scripts/integrity/fingerprints.py      A3 append-only run log
scripts/integrity/freshness.py         A1 state machine + banner renderer
scripts/integrity/crossval.py          B1 independent implementation
scripts/integrity/restatement.py       B2 vendor re-issue tracker
scripts/integrity/gap_receipts.py      B3 gap receipts
scripts/sprint2_selftests.py           one-command self-test entry point
docs/sprint2-runbook.md                what each alert means; on-call is Kenneth
docs/sprint2-selftest-results.json     machine-readable summary
docs/website-improvement-sprint-2-summary.md   this file
```

## Next decision for Kenneth

Which flag to turn on first. My recommendation is **`deploy_gate_flag`** — it is
the only guard that would have caught the equal-weight defect before it shipped,
it needs no change to how the pipeline is invoked, and its failure modes are
tested against the real inputs in this repo rather than synthetic ones.
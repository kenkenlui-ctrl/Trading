# Sprint 2 Operator Runbook — Website/Pipeline Integrity

**Status: all flags OFF. Nothing here runs in production yet.**

This runbook describes what each guard would mean and what the on-call action
is. The on-call is Kenneth. Nothing in this document is active until a flag is
turned on, and each flag is approved individually.

Turn a flag on (and back off):

```bash
cd ~/dev/dsa-hk
python3 -c "import sys;sys.path.insert(0,'scripts');from integrity import flags;print(flags.write_config(deploy_gate_flag=True))"
# back off
python3 -c "import sys;sys.path.insert(0,'scripts');from integrity import flags;print(flags.write_config(deploy_gate_flag=False))"
```

Flags live in `config/integrity_flags.json`. A missing or malformed file reads as
**all OFF** — the safe direction is hardcoded, so a typo cannot enable a guard
nobody reviewed.

---

## A1 — `staleness_banner_flag`

**States.** One line each:

| State | Meaning | What the visitor sees |
|---|---|---|
| `FRESH` | T-1 input is within 36h of the session | nothing (no banner) |
| `STALE` | input older than 36h | amber banner; numbers stay visible but marked |
| `NOT_RUN` | the pipeline produced nothing for this session | red banner saying the figures are NOT this session's |
| `SOURCE_DOWN` | vendor unreachable | red banner naming the vendor error |

**On-call action**

- `STALE` → check whether the nightly refresh ran (`logs/launchd-stdout.log`).
  If the data is genuinely late, do not re-run and re-label: wait for the bar or
  accept the degraded page. If a rerun is correct, rerun with the correct
  `ASOF_*` date.
- `NOT_RUN` → the pipeline did not execute. This is an operator failure, not a
  vendor one. Check the launchd job, then rerun by hand.
- `SOURCE_DOWN` → vendor problem. The site is telling the truth about being
  unable to build; do not publish a previous session's numbers as current.

**Never** fix a `STALE` banner by loosening `MAX_AGE_HOURS`. The limit is 36h
because that is what caught the 2026-10-17 incident.

## A2 — `deploy_gate_flag`

**Receipt fields.** One line each:

| Field | Meaning |
|---|---|
| `schema` | receipt format version; the gate rejects anything else |
| `engine` | logical producer name, e.g. `v2_equity_export` |
| `engine_version` | producer's semantic version, pinned per engine |
| `git_commit` | commit of the working tree at generation time |
| `config_hash` | digest of the effective config (flags + tunables) |
| `input_hashes` | `{repo-relative path: sha256}` for every input consumed |
| `generated_at` | ISO-8601 UTC generation time |

**Gate failure codes.** One line each:

| Code | Meaning | On-call action |
|---|---|---|
| `missing_receipt` | artefact carries no receipt | the producer was not wired to `receipts.attach()` |
| `bad_schema` | receipt schema not understood by the gate | version-skew between producer and gate; re-pin |
| `missing_field:*` | a required receipt field is empty/absent | fix the producer, not the gate |
| `engine_name_mismatch` | receipt claims a different producer | wiring bug — treat as a build fault |
| `version_mismatch` | engine version != the pinned version | decide: is the new engine correct? Re-pin deliberately, do not loosen |
| `input_drift` | an input's hash changed since the export was made | something rewrote an input mid-build; investigate before deploying |
| `input_missing` | a recorded input is gone | incomplete build |
| `version_not_pinned` | no expected version declared for this engine | add it to `expected_versions` |
| `self_test_failed` | the must-fail suite did not pass | fix the tests first |
| `crossval_failed` | the two implementations disagreed | see B1 — this is a finding, do not smooth it over |

**The gate is fail-closed.** Any single failure blocks the whole deploy; there
is no partial publish and no warning-only severity.

## A3 — `fingerprint_flag`

Appends one JSON line per run to `logs/fingerprints.jsonl`.

**Record fields.** One line each: `run_id` (unique per run), `started_at`,
`finished_at`, `terminal_state` (`pass`/`fail`/`blocked`), `git_commit`,
`config_hash`, `engine_versions`, `input_hashes`, `flags` (which sprint2 flags
were on), `note`.

**On-call action**

- `terminal_state: blocked` → a guard stopped the run. The `note` says which.
  This is the log's most important field — `blocked` is the only state that
  means "something was wrong and nothing shipped".
- No backfill exists for runs before this sprint. Absence of history before the
  first record is expected, not a fault.

## B1 — `crossval_flag`

Two independent implementations of the v2 decision; the signal set publishes
only on **exact agreement**.

- Primary: `scripts/build_v2_signals.py` + `scripts/v2engine/core.py` (pandas).
- Independent: `scripts/integrity/crossval.py` (explicit slicing, no pandas
  rolling), written from the published v2 contract.
- Agreement is **exact match, not statistical** — this is a consistency check
  and claims no p-values.

**On-call action**

- Disagreement → **nothing publishes** for that session. A rejection receipt is
  written with both outputs' hashes. Investigate which implementation is right;
  do NOT average, smooth, or pick a winner automatically.
- The usual cause is a threshold changed in one implementation and not the
  other. `CV.PARAMS` is echoed into every rejection receipt so the comparison
  is checkable.

**No-look-ahead.** `independent_plan(bars, t1_date=...)` discards bars after
T-1 *by date*. That parameter exists because the first version inferred T-1 as
"the last row", which meant a caller passing today's bar got today's answer and
a green test. The probe feeds a deliberately extreme T-day bar and asserts the
answer does not move.

## B2 — `restatement_flag`

Hashes each vendor bar file in full (not per row) and logs to
`logs/restatements.jsonl`.

**Events.** `NEW_SESSION` (first sight), `UNCHANGED` (byte-identical
re-ingest), `RESTATEMENT` (vendor re-issued an already-ingested session).

**Record fields.** One line each: `observed_at`, `session`, `vendor_file`,
`sha256`, `prev_sha256`, `event`, `vendor_markers` (the vendor's own
version/date markers), `dependents` (from `config/ingest_dependencies.json`).

**On-call action**

- `RESTATEMENT` → the vendor corrected a past bar. Recompute everything listed
  in `dependents`. Do not backfill already-published sessions; publish a
  correction note instead.
- A `RESTATEMENT` on a file with an EMPTY `dependents` list means the dependency
  map has a gap. Add the entry to `config/ingest_dependencies.json` so the next
  restatement has a blast radius.

## B3 — `gap_receipt_flag`

**Receipt fields.** One line each: `session`, `market`, `symbol`, `reason`,
`action`, `query_time`, `observed_at`, `rows_returned`.

**Reasons.** `MISSING_BAR` and `SOURCE_LATE` are **gaps** (source side).
`RULE_REJECTION` and `QUARANTINED` are **not**. They travel through different
functions and different counters by construction — `GR.is_gap()` is the only
predicate that decides, and a rule rejection can never be quarantined as a gap.

**On-call action**

- A gap excludes the ticker from the candidate set **and** every denominator.
  If a win rate moves when data was late, that is this module working.
- `rows_returned` on a gap receipt must be `0`; a receipt that claims rows is
  refused at construction.
- A gap still unfilled at the next session's build is quarantined and logged.
  Already-published sessions are never rewritten.

---

## Not covered (explicitly parked by the spec)

- **P1 fill-probability percentiles** — gated on FutuOpenD; do not start.
- **P2 clock fail-arm** — vendor timestamps dominate; optional.
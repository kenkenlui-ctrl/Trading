# Sprint 2 Operator Runbook — Website/Pipeline Integrity

**Status: A2 is LIVE. The other five guards are still OFF.**

As of 2026-10-20 `deploy_gate_flag` is on: every publish is gated on a
receipt, a pinned engine version, inputs that have not changed underneath the
build, and a self-test suite that passes at that moment. A2 is the only guard
in force. A1 (staleness banner), A3 (fingerprints), B1 (cross-validation), B2
(restatement tracking) and B3 (gap receipts) are built, tested and switched
off; nothing in this document is active for them until their flag is turned
on, and each flag is approved individually.

The on-call is Kenneth.

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

## A2 — `deploy_gate_flag` — **LIVE since 2026-10-20**

**What runs.** `scripts/deploy_gate.py`, called by `scripts/refresh.sh` as step
5e — after the self-test gate, before the git commit and the wrangler deploy.
A non-zero exit stops the publish; there is no `--force` and no warning-only
mode.

```
python3 scripts/deploy_gate.py     # exit 0 = publish allowed
```

**What it checks, in order.**

1. Every artefact about to be published carries a receipt (`public/v2-signals.json`
   today). No receipt → `missing_receipt`.
2. The receipt's `engine_version` equals the version this site is **pinned** to
   in `config/engine_versions.json`. The producer declares its own version as a
   code constant (`ENGINE_VERSION` in `scripts/build_v2_signals.py`) and the
   pin lives in a separate file on purpose: if the producer read its own
   version out of the pin, the check could never fail.
3. Every file the artefact was built from still hashes to what the receipt
   recorded. The engine's own source files are in that list, so "the code on
   disk right now is the code that built this artefact" is verifiable, not
   assumed.
4. The site's self-test suite passes, run by the gate itself as a subprocess.
   The gate does not accept a "the pipeline said it was green" boolean.

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

**Reading `git_commit` honestly.** It is the base commit of the working tree
at generation time, not a claim that the artefact is exactly that commit —
the daily pipeline regenerates pages and commits afterwards, so the tree is
normally dirty when the receipt is written. The authoritative record of what
produced the artefact is `input_hashes`: it pins the byte content of every
file that fed it, engine source included. A receipt whose commit and hashes
disagree is a finding, not a formatting quirk.

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
| `load_error` | the artefact or the pin could not even be read | incomplete build — nothing to check, so nothing deploys |

**The gate is fail-closed.** Any single failure blocks the whole deploy; there
is no partial publish and no warning-only severity.

**What the receipt publishes.** The receipt lives inside the artefact it
describes, so `public/v2-signals.json` — a public file — carries the engine
name and version, the commit, the config hash, the generation time, and the
**repo-relative path and sha256 of every input**. That is deliberate: a
receipt nobody can read is not a receipt. The paths are the site's own asset
paths plus a handful of local data files; the hashes are of market bar files.
Nothing secret is disclosed, but the site's internal file layout is visible in
that JSON and should be treated as public.

**Turning A2 off.** `deploy_gate_flag` off means the gate is not in force and
the publish proceeds unchecked — that is the flag's whole purpose, and it is an
operator decision recorded in a committed file. The sprint2 guardrail test
asserts that exactly one flag is on and that it is this one, so a silent
flip does not survive the test suite.

**Rebuild after changing the engine.** If you edit anything in the receipt's
input list — the producer, the engine, a bar file, the universe, the lot
sizes — the recorded hashes stop matching and the gate refuses to publish.
That is the point: regenerate `public/v2-signals.json` and the gate goes green
again.

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
# Website/Pipeline Integrity Sprint 2 — Spec

## Objective

Make win9you.com fail loud instead of failing silent. Done looks like this:
(1) the site renders an explicit staleness/degraded state whenever T-1 data is stale — no visitor ever sees old numbers presented as fresh;
(2) no deploy can publish unless every displayed number resolves to a source receipt and the must-fail self-test suite passes (fail-closed gate);
(3) every pipeline run and every site export carries a machine-readable fingerprint of exactly what produced it;
(4) the v2 signal pipeline gains independent cross-source validation, vendor-restatement tracking, and explicit data-gap receipts.
All behind config flags, default OFF. Production untouched until Kenneth approves each piece.

## Background

Why this sprint exists — each item is a measured or near-missed failure, not a nice-to-have:

- **Staleness served as fresh.** 2026-10-17 ~04:24 HKT: the win9you-data-monitor caught the site's "Updated" timestamp unchanged at 2026-10-14 18:24 HKT — ~58h old, over the 36h limit, with the Oct 15 and Oct 16 nightly refreshes missing. The external cron caught it; a visitor to the site would never know. Counts looked healthy while the data was dead.
- **Version drift rendered as truth.** Website Sprint 1 (completed 2026-10-15/16) found the equity-curve pages rendered by the OLD engine presented as v2 numbers; the methodology page showed JP +25.4%/+18.5% while the homepage showed the corrected +18.03%/+18.00%. A self-test against old-JSON reads was added, but there is no standing gate — the same class of bug can recur on any future deploy.
- **The verifier held the "signal".** The VIX regime-filter test (2026-10-08): a placebo-grouping bug in the verification path flipped the conclusion; the true result was OLS R² ≤ 0.003 over 4,576 JP signals, verdict do-not-enable. Independent recalculation would have caught it at once.
- **Silent vendor restatement is unhedged.** A vendor re-issuing a daily-bar file with corrected values would drift signal-distance percentiles while per-day hashes look stable. The pipeline has no base-date tracking and would never notice.
- **Silent carry-forward is look-ahead-adjacent.** It is currently unknown whether the pipeline carries a stale bar forward when a vendor is late. Either answer must be explicit and receipted.
- Network sources for the design patterns: DeepSeek Harness (pre-publish gates, change-event freshness, process fingerprints, silent-failure heuristics), 金元宝Hermes (four-gate cross-source validation, delayed-data-source receipt protocol), Duanzai (silent distortions in derived indicators), DQ的AI助手 (EMPTY vs NOT_RUN split), 小巴助手 (clock fail-arm).

## Inputs

1. Repo: `/Users/kenken/dev/dsa-hk`. Key files (verify exact names on read; do not guess):
   - `scripts/build_static.py` — site builder (the deploy path)
   - `scripts/build_home.py`, `scripts/build_v2_signals.py`, `scripts/build_equity_curves.py`, `scripts/v2_equity_export.py` — page/export builders
   - `data/` — ingested vendor bars and intermediate artifacts
   - `docs/` — prior specs (website-improvement-sprint-1-spec.md, signal-search-sprint-1-spec.md, TASK_SPEC_TEMPLATE.md)
2. External data: the existing vendor feeds only. No new vendor, no new API in this sprint.
3. Prohibitions: no change to v2 strategy rules (S1 entry, close confirmation, 3×ATR stop, 10-day exit); no change to published backtest numbers; no production deploy; no new data source; nothing in this sprint may use data not available at T-1 close for any signal-dated artifact.

## Steps

### Phase A — Fail-loud integrity (do first)

**A1. Visitor-visible staleness states.**
1. Define the freshness contract: max acceptable age of the T-1 input per market session (keep the existing 36h limit; Monday/holiday handling stays as-is for now).
2. Add explicit pipeline/data states, rendered on the site: `FRESH` (within limit), `STALE` (over limit — banner + degraded styling, numbers still visible but marked), `NOT_RUN` (pipeline did not run — page shows "no data for this session", never last session's numbers), `SOURCE_DOWN` (vendor unreachable — distinct message).
3. The banner must show: as-of timestamp, how stale, which market/session is affected. No silent reuse of a previous snapshot.
4. Self-test: simulate a stale input (fixture with an old timestamp) and assert the banner renders and `FRESH` is NOT claimed. Must-fail guard: the test feeds stale data and the build must refuse to render it as fresh.

**A2. Pre-publish deploy gate, fail-closed.**
1. Every export script (`v2_equity_export.py`, `build_home.py`, `build_equity_curves.py`, and any other JSON/HTML producer) embeds into its output: engine name + version, git commit hash, config hash, input-file hashes, generation timestamp.
2. `build_static.py` (or the deploy step) asserts before publishing: every displayed number resolves to a source receipt (export metadata present and internally consistent); engine version in the export matches the expected engine version; the must-fail self-test suite passes. ANY failure → build aborts, nothing deploys, alert logged.
3. Version-mismatch probe: feed the gate an export stamped with a wrong engine version and assert the deploy is blocked. Must-fail guard.
4. Corrupted-receipt probe: feed the gate an export with a missing/tampered receipt and assert the deploy is blocked. Must-fail guard.

**A3. Process fingerprints.**
1. Every pipeline run writes a fingerprint record to `logs/` (append-only): run timestamp, git commit, config hash, input-file hashes (per vendor file), engine/script versions, and the run's terminal state (pass/fail/blocked).
2. Every site export's embedded metadata (A2) must be joinable to its fingerprint record — "which engine produced this number?" becomes a lookup, not archaeology.
3. Backfill is NOT required for old runs; fingerprints start with the first run after this sprint.

### Phase B — Validation depth

**B1. Four-gate independent-implementation validation.**
1. Implement a SECOND, independent implementation of the v2 signal computation (different code path) using the SAME vendor input files as the first implementation. This tests implementation independence, NOT data-source independence — do not bring a second vendor; write-path lineage checks are out of scope for this sprint. Written without copying the first implementation's internals — the spec (S1 = 20-day low, close confirmation, 3×ATR stop, 10-day exit, T-1 only) is the contract.
2. Nightly (or on each signal build): both implementations run; publish the signal set only if they agree exactly. ANY disagreement → publish nothing for that session, log both outputs, raise an alert. A disagreement is a finding, not a bug to smooth over. When debugging a disagreement, check denominator definitions first (candidate-set vs denominator boundaries, zero-value/expiry handling) before assuming logic bugs — inconsistent denominators are the most common source of false disagreements.
3. Version-control all thresholds/parameters used by both implementations (no hardcoded magic numbers diverging silently).
4. Keep auditable rejection receipts: every rejected/pulled signal set gets a receipt (timestamp, reason, both outputs' hashes).

**B2. Base-date / restatement tracking.**
1. On every ingest of a vendor daily-bar file, record an immutable snapshot hash of the FULL file (not per-row), plus the vendor's own version/date markers if present.
2. On re-ingest of the same session: compare hashes. If the vendor re-issued the file (hash differs for an already-ingested session), log a RESTATEMENT event with before/after hashes, and recompute any derived quantities that consumed that file. Distinguish restatements from genuinely new bars explicitly.
3. Version the denominator scales of all derived quantities (e.g. ATR windows, distance percentiles) so a restatement's blast radius is traceable.
4. Self-test: feed a tampered re-issue of a session file and assert a RESTATEMENT event is logged and dependents are flagged.

**B3. Data-gap receipts.**
1. If a ticker's T-1 bar is missing at build time: bind it to a receipt (query time, row count = 0, session tag), EXCLUDE the ticker from that session's candidate set AND from all denominators (win rates, counts). Never silently carry a stale bar forward; never silently drop the ticker from denominators.
2. Pending data expires at the next decision point: a missing bar not refilled by the next session's build is quarantined (logged, excluded) — no backfill rewrites of already-published sessions.
3. 'Source late' vs 'value not met' ride separate channels: a late source is a data-gap receipt; a ticker genuinely failing the v2 rules is a rule rejection. The two must never share a code path or a counter.
4. Self-test: build a session with a deliberately missing ticker and assert it is excluded from both the candidate set and the denominators, with a receipt logged.

### Phase C — Website content (incremental, may ship independently)

**C1. Friction flip-threshold table.** Compute the round-trip cost level at which the v2 per-trade edge crosses zero for US and JP (HK retest already reports −8.1pp at 0.25% and −5.9pp at zero cost). Publish as a cost-sensitivity curve on the methodology page, not a single point estimate.

**C2. Stratified-reporting standard.** Standing rule for the methodology page and all future backtest reporting: no aggregate number without its strata (regime window, market, distance-to-S1 bin). Apply to the Sprint 1 output when it lands.

**C3. Signal-fixture schema publication.** Publish the machine-readable v2 signal fixture format (fields, T-1 rules, fill convention) on the research page so others can replicate and cross-validate.

### Parked (explicitly NOT in this sprint)

- **P1. Fill-probability percentiles.** Gated on FutuOpenD going live plus months of depth-snapshot collection. Do not start.
- **P2. Clock fail-arm.** Lower value; vendor timestamps dominate. Optional future work.

## Guardrails

- **No look-ahead:** any signal-dated artifact (B1 second implementation, B2 restatement recomputation, B3 gap handling) may only use data available at T-1 close. Probe: run the B1 agreement check on a session with the T-day bar deliberately present in the input directory and assert both implementations ignore it (agreement on T-1-only output).
- **Placebo / negative control:** the deploy gate (A2) must be demonstrated blocking on corrupted input before it is trusted (probes in A2.3, A2.4); the staleness banner (A1) must be demonstrated rendering on a stale fixture; the restatement tracker (B2) must be demonstrated firing on a tampered re-issue. A guard never demonstrated failing is decoration.
- **Multiple comparisons:** not a strategy test — no p-values claimed. Disclose the count of independent implementations (2) and that agreement is exact-match, not statistical.
- **Event dominance:** N/A for infra work; for C1, report the flip threshold with and without the largest single contributing session if one dominates.
- **Branch only:** every item behind a `<feature>_flag` config flag, default OFF (e.g. `staleness_banner_flag`, `deploy_gate_flag`, `fingerprint_flag`, `crossval_flag`, `restatement_flag`, `gap_receipt_flag`). No production changes until Kenneth approves each flag.

## Deliverables

1. One-page summary: verdict first (which phases are done and verified), then what changed. Negative results (e.g. B1 disagreement found) reported with the same energy as clean runs.
2. Machine-readable summary (JSON): per-item status, flag names, guardrail probe outcomes (pass/fail with evidence), files touched.
3. Reproducible scripts: `scripts/` additions with one-command self-test entry point (e.g. `python scripts/sprint2_selftests.py`), including all must-fail guards.
4. Updated `docs/` notes for any operator-runbook changes (what each new alert means, what the on-call action is — the on-call is Kenneth).

## Verification checklist (all must pass before reporting done)

- [ ] Self-tests pass — new tests for every new guard; must-fail guards demonstrated failing on bad input, not just passing on good input
- [ ] No-look-ahead probe passes (B1 T-day-bar-ignored probe)
- [ ] Deploy gate demonstrated blocking a version-mismatched export AND a receipt-tampered export
- [ ] Staleness banner demonstrated on a stale fixture (renders STALE, never FRESH)
- [ ] Restatement tracker demonstrated firing on a tampered re-issue
- [ ] Gap receipt demonstrated: missing ticker excluded from candidates AND denominators, receipt logged
- [ ] Production code untouched (grep for new identifiers in build/deploy paths returns nothing while flags are OFF)
- [ ] Bugs found during the work disclosed in the summary, even if already fixed
- [ ] Definitions documented (FRESH/STALE/NOT_RUN/SOURCE_DOWN, fingerprint fields, receipt fields — one line each)

## Non-goals

- No change to v2 strategy rules, universes, or published backtest numbers.
- No new data vendor or API; no Futu work (P1 stays parked).
- No production deploy; no flag turned ON without Kenneth's explicit approval per flag.
- No backfill of fingerprints or receipts for historical runs.
- No new website pages beyond C1–C3 content additions; no redesign.
- Signal Search Sprint 1 (dispatched 2026-10-15) is untouched by this sprint — different workstream, do not merge or block on it.

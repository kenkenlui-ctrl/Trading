# Website Improvements — Executability Tiers, Confirmation Cost, Failure Criteria, Gate Independence

Spec date: 2026-11-11. Source: recent EigenFlux network findings (8–10 Nov 2026) + live site check 2026-11-11 12:08 HKT.

## Objective

Ship four credibility improvements to win9you.com, all behind flags default OFF, no production change until Kenneth approves per flag. Done = each item implemented behind its flag, verified by its own must-fail guard, with a one-page summary + JSON + reproducible script. No new signal claims anywhere.

## Background

Live site check 2026-11-11 12:08 HKT (crawled 04:08 UTC): homepage still on **2026-10-30 data, 162 signals, "Updated 10:00 HKT"**, "今日 actionable" still presented as today's list, no STALE flag. Eight trading days stale (2–10 Nov closes missing). The A1 fail-loud fix was dispatched 2026-10-29 and re-asked 2026-10-31; its per-item report-back is still open — this spec does NOT re-dispatch A1, it adds four new items.

Evidence for each item (numbers with sources, none restated without provenance):

- **Item 1 — invisible fills.** EigenFlux 荷马史诗, item `377831217111760896` (2026-11-08 22:54 HKT): backtest returns are systematically overstated by trades that could never execute — limit-up/down, suspensions, liquidity gaps. Rule: split every signal into fillable / partially fillable / unfillable; count only the fillable tier in win rate; book unfillable separately as opportunity cost, never as return. Jasper, item `377677826809135104` (2026-11-08 12:54): three-tier `fill_rejected` classification — NO_SELLER_SEALED, HALT (halt flag true AND volume 0, both required, else UNJUDGED — never default a data gap to halt), QUEUE_UNFILLED; tiers differ 1–2 orders of magnitude; bind each rejection to that day's OHLC + VWAP.
- **Item 1 context — why it matters here.** Baseline reconciliation first pass (2026-11-07, live methodology page): auditor US sample-out per-trade avg **+0.45%** vs site engine **+0.783%** (gap 0.333pp, 1.74×); control arm matches (auditor +0.58% vs site +0.694%, gap 0.114pp), so **~0.22pp of the gap is entry-mechanics-specific** (S1 fill price / skip-if-open≤S1). Spec: `docs/baseline-reconciliation-research-spec-2026-11-07.md` (approved 2026-11-07; dispatch separate, pending). Item 1 is the site-side half of that question: how many historical "fills" were never fillable?
- **Item 2 — confirmation-period cost.** 荷马史诗 (same item): any "wait for close confirmation" rule is holding through the confirmation period, and that cost must be booked. Print the false-signal filter rate AND the delayed-execution cost distribution together — either alone is self-flattery. Cost measured with the **overnight-gap distribution, not ATR** (gaps are fat-tailed; ATR is an average-scale measure and systematically understates the tail). EigenFlux Agent, item `376924439666753536` (2026-11-06 10:54): confirmation-cost ledger as four machine-checkable metrics; untradeable days booked NULL, not 0. Site context: /methodology §4.5 shows close confirmation lifting per-trade avg **+0.788% → +1.772%** (touch-only → with confirmation) and never shows the waiting cost.
- **Item 3 — numeric failure criteria.** EigenFlux 小唯, item `377710960686661632` (2026-11-08 14:54): their validation pipeline rejected three strategy groups OOS (one −17%, two hitting a 30% drawdown circuit breaker); they ask peers for concrete numerical OOS failure criteria — Sharpe decay thresholds, drawdown multiples, consecutive negative rolling windows — written as reproducible must-fail fixtures. Site context: win9you has no stated "when we declare a strategy dead" numbers. The SELECTION_DEAD verdict (Direction A, 2026-10-20) was reached by a preregistered bar (+1.0pp in both windows); the same discipline should be written down for the site itself.
- **Item 4 — gate independence + three-layer "complete".** 荷马史诗 (same item): the gate and the thing it checks must not share a source — if validator and signal read the same feed, they fail together and the gate always waves things through. 籽靈, item `376581915450277888` (2026-11-05 12:54): "complete" is three layers of evidence — write success ≠ shown; generation ≠ complete (tested with a negative sample); executor self-check doesn't count. Site context: the frozen 31 Oct 10:00 stamp is exactly a missing third layer — build said done, page shows old data, nothing read it back.

## Inputs

1. Repo paths: `scripts/build_v2_signals.py`, `scripts/build_static.py`, `scripts/build_home.py`, `data/bt10y2/` (frozen backtest data), methodology source under `docs/`.
2. External data: ONE source — the existing daily OHLC cache plus halt/suspension flags already in `data/`. Do NOT introduce a new vendor or feed for this task. A day with missing bars is UNJUDGED, never defaulted to halted or to fillable.
3. Prohibitions: **T-1 only** — a signal dated T may only use data available at T-1 close. No new signal claims, no HK re-inclusion (`INCLUDE_HK` stays False), no auto-schedule for refreshes (manual only, reaffirmed 2026-10-29), no production deploy without Kenneth's per-flag approval. Do not touch the A1 work — its report-back is a separate open thread.

## Steps

### Item 1 — Executability tiers on every signal (lead item)

1. Define tiers from daily bars, per signal-day: **FILLABLE** (day's low ≤ S1, close > S1, not halted/suspended, volume > 0); **NOT_TRIGGERED** (day's low never reached S1 — no fill, not a failure); **UNFILLABLE** (halted/suspended that day, or no tradeable price at S1). A day with missing bars is UNJUDGED, never assigned to any tier.
2. Re-run the historical v2 backtest with tiers attached: report counts per tier (US and JP separately, 2016–26 and both sub-windows), and the per-trade average with UNFILLABLE excluded vs included. This number is the site-side input to the baseline reconciliation — report it as such, do not interpret it as a new strategy result.
3. Surface the tier on the signal list (behind `executability_tiers_flag`, default OFF): one column per signal, tier label only. Exclude UNFILLABLE from the published win-rate denominator; show the excluded count next to it.
4. Must-fail fixture: a known halted/suspended day must classify UNFILLABLE; a known normal fill day must classify FILLABLE; a day with missing bars must classify UNJUDGED. If any fixture misclassifies, the tier code is wrong, not the fixture.

### Item 2 — Confirmation-period cost on /methodology

1. Compute the overnight-gap distribution for the confirmation window (close-to-next-open gaps on signal days, US and JP separately). Report median, p90, p99, and the share of gaps exceeding 3×ATR — the tail, not the average.
2. Add to /methodology §4.5, next to the existing +0.788% → +1.772% filter-benefit numbers: the delay-cost distribution AND the filter rate, printed together. One line stating the measurement choice: gap distribution, not ATR, with the reason (fat tails).
3. Placebo: the same gap distribution computed on shuffled signal days must show no systematic cost. Report the placebo result alongside, with the same energy as the real one.
4. Behind `confirmation_cost_flag`, default OFF. Methodology text draft only — do NOT deploy the text without Kenneth's approval.

### Item 3 — Preregistered numeric failure criteria

1. Draft a short methodology section, "When we declare a strategy dead", with PROPOSED numeric thresholds (proposals, not decisions — Kenneth confirms the numbers before anything goes live): OOS Sharpe below 50% of in-sample Sharpe; max drawdown above 2× in-sample max drawdown; 3 consecutive negative rolling 20-day windows. State the window definitions (in-sample / out-of-sample split, rolling window length) in one line each.
2. Show the current v2 numbers against each proposed threshold, so the reader can see where the live strategy stands — including that the 2021–26 window already fails the Sharpe-decay reading (Direction A: arm1 gap −5.121pp, z −1.658), which is consistent with SELECTION_DEAD, not a contradiction of it.
3. Behind `failure_criteria_flag`, default OFF. Text draft only — no deploy without Kenneth's approval of the thresholds.

### Item 4 — Gate independence + three-layer build receipt

1. A2 read-back check must verify the published stamp against a source INDEPENDENT of the build pipeline's own output (e.g. fetch the live homepage and compare its "數據截至" date and signal count against the build manifest — the manifest is not the check, the live page is). Document which source the gate reads and why it is independent.
2. Build receipt shows three layers, each with its own evidence: (a) build succeeded (exit code + manifest), (b) published (deploy id + timestamp), (c) read-back confirms the live page shows the new stamp and count. A run with (a) and (b) but a failed (c) is STALE, not done.
3. Must-fail guard: plant a stale stamp (live page showing the previous build's date after a new deploy) — the gate must FAIL the run. If the gate passes, the gate is broken, not the fixture.
4. Behind `gate_independence_flag`, default OFF. No production gate change until Kenneth approves.

## Guardrails

- **No look-ahead:** T-1 only, as above. Probe: plant a late-arriving row (a bar dated after the signal date) — the gate must reject it, never backfill.
- **Placebo / negative control:** Item 2's shuffled-days placebo (above); Item 1's UNJUDGED handling (missing bars must not silently become a tier); Item 4's planted stale stamp. Run before claiming anything.
- **Multiple comparisons:** disclose how many tier definitions / threshold variants were tried. A marginal result from one of many is not evidence.
- **Event dominance:** if any result is driven by one or two events (e.g. the Nov 2026 rout), report with and without them.
- **Branch only:** implement behind the four flags named above, all default OFF. No production changes until Kenneth approves per flag.

## Deliverables

1. One-page summary: verdict first, then key numbers. Negative results reported with the same energy as positive ones.
2. Machine-readable summary (JSON): inputs, parameters, results, guardrail outcomes.
3. Data artifacts (CSVs) with column definitions documented in the file or alongside it — tier counts per market/window; gap-distribution percentiles; current strategy vs proposed thresholds.
4. Reproducible script/notebook — one command reproduces everything.

## Verification checklist (all must pass before reporting done)

- [ ] Self-tests pass — add new ones for new logic; include a must-fail guard wherever silent corruption is possible
- [ ] No-look-ahead probe passes (planted late row rejected)
- [ ] Placebo / negative control run and reported (Item 2 shuffled days; Item 4 planted stale stamp)
- [ ] Production code untouched (grep for new identifiers in build paths returns nothing)
- [ ] Bugs found during the work are disclosed in the summary, even if already fixed
- [ ] Definitions documented (FILLABLE / NOT_TRIGGERED / UNFILLABLE / UNJUDGED; gap distribution; each threshold's window — one line each)

## Non-goals

- No new signals, no strategy changes, no HK re-inclusion, no Direction D.
- No auto-schedule for refreshes (manual only).
- No A1 rework — its report-back is a separate open thread from the 2026-10-31 dispatch.
- No production deploy. No methodology text goes live without Kenneth approving the words and (Item 3) the threshold numbers.
- No new data vendor. No sharing of EigenFlux item contents beyond the paraphrased findings above.

## Addendum 2026-11-14 — additional EigenFlux evidence since the spec was written (11–13 Nov)

The four items stand. These findings strengthen Items 1 and 4 directly; fold them in, no new vendor, no scope change except the proposed Item 5 (Kenneth to approve or drop).

- **Item 1 — coverage/denominator discipline.** Nova, item `377984100062986240` (2026-11-13 16:54): split "coverage" into three readings — missing cells, missing rows, clean rows — and report all three denominators (missing cells/total cells, missing rows/total rows, clean rows/total rows). Reporting only the per-row missing share let one denominator stand in for another. Two reproducible negative-control arms: zero-value denominator expansion (add rows with complete fields and value 0 — family counts must not move) and side-change single-row mutation (change one row's side — the row must be judged inconsistent AND its NA set must actually flip; first only = cached path). Add both arms as Item 1 fixtures.
- **Item 1 — the excluded cohort.** Niobium, item `350708148912259072` (2026-11-13 10:54): the excluded/unbooked cohort is disproportionately the strongest-signal cohort (weak names exit early and get recorded; strong stay open and invisible); realised-only accounting is conservative for "does it work" but misleading for "should I size up". Add to Item 1: report the open-position count alongside the win rate, and the excluded cohort's forward returns as a separate line — or mark UNKNOWN if unmeasured.
- **Item 4 — mutation-tested gate.** 小m的Agent实验室， item `378325068205260800` (2026-11-14 00:54): a gate reported 94/94 green while 25 of 142 entry scripts (17.6%) never executed; breaking an outside-the-gate fixture left the gate green, breaking an inside one dropped it to 93/94. Coverage ≠ "wrote a run line" — it equals "reachable by import from an executed entry". Add to Item 4: hard gate — after the aggregate run, compare the on-disk entry count against the actually executed count (including transitive coverage); any mismatch = FAIL.
- **Item 4 — bidirectional assertion.** Nova, item `378144279911989248` (2026-11-13 22:54): paired toggles — change the rule table with counts constant, classifications must flip; add zero-value rows with rules constant, classifications must stay stable. Running only one mistakes count-driven movement for rule-driven. Add both toggles as Item 4 must-fail arms, with the criterion version in the hash.
- **Item 4 — denominator independence.** 小花花， item `377384496028188672` (2026-11-14 00:54): the denominator must come from an independent source that doesn't participate in execution; the denominator list is content-addressed (path + digest) and recomputed every round; identity-type ratios (a difference of zero always holds) are demoted to descriptive fields, never decision inputs. Add to Item 4: the gate's denominator — what should have been checked — is enumerated independently of the pipeline.
- **Candidate Item 5 — directional coverage (PROPOSED, not decided).** WorkBuddy, item `377200238198259712` (2026-11-13 22:54): judge a signal pipeline by head-signal directional coverage, not hit-rate averages — whether contradictory facts from both sides reach a human together; machine-generated noise scored 0 and kept out of the feedback pool; conflicting signals enter the focus queue as a pair, single-sided push counts as a pipeline defect. If Kenneth approves: measure the brief/signal list's coverage of both sides, behind `directional_coverage_flag`, default OFF. If he drops it, Item 5 is out and nothing else changes.

## Chat message to MiniMax

「新任務：網站可信度改進，四項，spec 寫好喺 docs/website-improvements-2026-11-11.md，請先讀晒成份 spec，再按入面做。重點：(1) 每個訊號加可成交分級 FILLABLE / NOT_TRIGGERED / UNFILLABLE，UNJUDGED 唔可以預設；(2) /methodology 加確認期成本，用隔夜跳空分布唔係 ATR；(3) 寫死策略失效數字門檻（提案，Kenneth 先批數字）；(4) A2 read-back 要用獨立來源驗證，三層 receipt。Research only：唔改 production，四個 flag 預設 OFF。做完交：one-page summary、JSON、CSV、可重現 script。」

# Website fix dispatch — 2026-10-23 (from Undercover, approved by Kenneth)

Verified live evidence (checked 2026-10-23 ~01:07 HKT by fetching win9you.com):
homepage header says "Live" / "今日 actionable", but shows T-1 data 2026-10-20,
US 2026-10-19, "Updated 01:02 HKT". Build-time arithmetic points to the last
successful nightly build being ~2026-10-21 01:02 HKT — the Oct 22 and Oct 23
nightly builds never landed. The freshest closed US session is Oct 21; the site
shows Oct 19. The site is ~2 sessions stale and does not flag it.

Work in this order. Deploy everything through the A2 gate.

## 1. Restore the nightly build (first — nothing else matters if data is stale)
- Diagnose why the Oct 22 and Oct 23 nightly refresh/deploy runs did not land
  (scheduler? data fetch failure? deploy gate rejection? silent exit?). Find the
  actual cause from logs/receipts — do not guess.
- Fix it, then run one manual refresh + redeploy immediately so the live site
  catches up to the latest closed sessions (US and JP).
- Verify on the live site: data date advanced, US shows the latest closed
  session, Updated timestamp is current.
- Report the root cause in one or two sentences.

## 2. Ship Sprint 2 Phase A1 — fail-loud staleness states
- Definitions per docs/website-improvement-sprint-2-spec.md (FRESH / STALE /
  NOT_RUN / SOURCE_DOWN).
- When a market's data is older than that market's latest closed session, the
  homepage (and that market's pages) must show a prominent STALE notice with
  the data date, and must not present the signals as "Live" or
  "今日 actionable" without the stale flag attached.
- The state must be computed at build time from the data itself, not hand-set.
- Acceptance test: point the builder at an old data snapshot and confirm the
  STALE state renders; point it at fresh data and confirm FRESH renders.

## 3. C1 friction flip-threshold table
- Per market (US, JP; HK for the record): compute the round-trip cost level at
  which the v2-vs-equal-weight annualised gap changes sign, from the existing
  backtest data — every number generated at build time, no hand-typed figures.
- Known reference points the computation must reproduce: JP flips between
  0.10% (+2.1pp/yr) and 0.25% (−0.34pp/yr); US full-period gap is +0.96pp/yr
  at 0.25%.
- Place the table on /methodology near the cost discussion, and add a link from
  the homepage cost paragraph.

## 4. Publish the VIX regime research note
- Deploy public/research/vix-regime.html with a noindex robots meta (add it if
  missing), and link it from the failed-test record / research log.
- Content unchanged — this is the finished 2026-10-08 negative-result note
  (4,576 JP signals, OLS R² ≤ 0.003, do-not-enable verdict).

## Report back
Per item: done / not done, the live URL, and the verification you ran.
For item 1, include the root cause of the missed builds.

# Spec: Publish Direction A (SELECTION_DEAD) result on win9you.com research log

Approved by Kenneth, 2026-10-20 22:08 HKT. Publish — do not keep internal.

## Objective

Add a research page to win9you.com reporting the Direction A fill-matched
selection test verdict: **SELECTION_DEAD**. The v2 screen's stock selection
does not beat equal-weight even with perfect fills. Publishing a verified
negative with full methodology is a deliberate credibility move — same
practice as the HK retest page.

## Source of truth (no hand-typed numbers)

Every number on the page must be generated at build time from:

- `reports/edge_alpha_a_us.json` (fill_lag=0)
- `reports/edge_alpha_a_us_lag1.json` (fill_lag=1)

If a figure is not in those JSONs, it does not go on the page. This page
must pass the A2 deploy gate: each displayed number resolves to a source
receipt.

## Page content (follow existing research-page format; vix-regime.html is the template)

1. **Question.** Does the v2 selection rule itself beat equal-weight once
   fills are made perfect? Pre-registered bar: selection arm must beat the
   equal-weight arm by ≥ +1.0pp in BOTH sub-periods (2016–2020 and
   2021–2026), US universe, 2016–2026.
2. **Design.** Two arms, both ~100% invested — empty-selection days fall
   back to the whole universe (Amendment A2), so there is no exposure
   confound; it is a pure selection test. Arm 1 = v2 selection with close
   fills; Arm 2 = equal-weight at close. Run at fill_lag 0 and 1.
3. **Results table** (from the JSONs):
   - fill_lag=0, gated arm: 2016–20 +2.984pp (z +1.184, pctl 87.7);
     2021–26 −5.121pp (z −1.658, pctl 4.3)
   - fill_lag=0, no-gate arm: 2016–20 +1.77pp (z +0.838);
     2021–26 −4.584pp (z −1.399)
   - fill_lag=1, gated: +2.317pp (z +0.891) / −5.13pp (z −1.627)
   - fill_lag=1, no-gate: +2.106pp (z +0.881) / −5.404pp (z −1.703)
   - Full-sample: gated −1.624pp; no-gate −1.795pp vs equal-weight arm
   - Neither window pair clears the bar → **SELECTION_DEAD**, robust to
     fill lag. Best cell across all 8 (2 arms × 2 windows × 2 lags) is
     1.2σ — a ceiling, not a lead. 2021–26 is "dead, not anti-dead"
     (z −1.4 to −1.7, never −1.96).
4. **Firing-day decomposition** (label clearly as post-hoc diagnostic, not
   pre-registered): on the 1,393 sessions where the screen fired (55% of
   2,534), its picks did not beat the same number of random names:
   +2.664pp (z +1.262) / −4.83pp (z −1.633).
5. **What remains true.** The per-trade close-confirmation edge is real
   (~55% win rate, positive average per trade). Selection does not convert
   it into portfolio alpha. Both statements on the page, neither exaggerated.
6. **Decision taken.** The real-money plan on this screen is stopped.
   HK remains excluded. win9you.com continues as a research publication.
7. **Methodology links.** Link to the methodology page and the HK retest
   page. State the T-1 / no-look-ahead discipline used.

## Build & deploy

- Build via the normal `scripts/build_static.py` path; page lives with the
  other research pages.
- Deploy with the current P0 batch. If the A2 deploy gate is live, this
  page goes through it like everything else.
- No changes to v2 rules, no changes to any other published number.
- Language/style: match the existing Cantonese research pages.

## Report back

- Page URL when live.
- Confirmation that every figure on the page was generated from the two
  JSON reports (list any figure that had to be dropped because it was not
  in the JSONs).

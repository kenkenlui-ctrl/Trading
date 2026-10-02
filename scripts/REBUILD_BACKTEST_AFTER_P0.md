# Rebuild runbook — after the 2026-10-02 fail-closed backtest fix

## What changed and why the page must be rebuilt

Two P0 defects were fixed:

1. **The 20d ADV "filter" never filtered anything.** It ran *after* `records` was
   built, so it only produced counters for the report. The page published
   "Passed: 496/496 (100%)" while every headline number still included
   unverified/illiquid names.
2. **`adv is None` counted as "passed."** yfinance returns no Volume for many HK
   tickers, so 375 of 496 signals had liquidity that was *unknown* but counted as
   *verified*.

Both are now fail-closed. **The published numbers WILL change** — not because the
strategy changed, but because the previous numbers included trades that were never
verified as tradeable.

Files changed:
- `scripts/monthly_swing_backtest.py` — filter now gates `eligible_signals` before simulate; `adv is None` → excluded; new `--unverified-adv {exclude,include}` flag; honest report copy.
- `scripts/build_backtest_page.py` — section title updated to match the new heading.
- `src/data_fetcher.py` — fail-closed freshness gate (single source, covers all 6 call sites).
- `scripts/build_equity_curve.py`, `scripts/build_equity_curves.py` — **new**: source JSON was hardcoded to `backtest_2026-09-10.json`; now resolves the newest `backtest_*.json`, so the equity-curve pages can't silently disagree with `backtest.html`.

---

## Step 1 — Run the backtest (regenerates data/monthly_backtest/latest.md)

```bash
cd /Users/kenken/dev/dsa-hk
python3 scripts/monthly_swing_backtest.py --months 6
```

This writes `data/monthly_backtest/backtest_<today>.json` and `latest.md`.

**Check the console output** — it should print a line like:

```
Universe filter: 121/496 signals eligible (ADV-verified 121, excluded thin 0, excluded no-volume 375, excluded no-data 0)
```

If it prints `0/... eligible`, the ADV threshold may be too strict for the new
window — inspect the breakdown before publishing.

### To reproduce the OLD numbers (for comparison only)

```bash
python3 scripts/monthly_swing_backtest.py --months 6 --unverified-adv include
```

Use this to sanity-check the delta, then discard it. Do **not** ship this output.

---

## Step 2 — Rebuild the backtest page

```bash
python3 scripts/build_backtest_page.py
```

Reads `data/monthly_backtest/latest.md` → writes `public/backtest.html`.

**Verify:** open `public/backtest.html` and confirm the "Universe filter" section
now reads "ADV-verified and simulated: N (x%)" and contains **no** "496/496 (100%)".

---

## Step 3 — Rebuild the equity-curve pages

```bash
python3 scripts/build_equity_curves.py   # T+1 / T+3 / T+5 / T+10
python3 scripts/build_equity_curve.py    # single T+10 page
```

Both now auto-select the newest backtest JSON and print which file they used.

**Verify:** the printed source filename must be today's `backtest_<date>.json`,
not an older date. If it prints an old file, Step 1 did not run.

---

## Step 4 — Commit + deploy

```bash
git add -A
git commit -m "fix(backtest): fail-closed ADV filter + freshness gate; honest universe disclosure"
git push origin main
bash scripts/refresh.sh          # full pipeline, or deploy directly via wrangler
```

If you only want to publish the backtest pages without a full refresh, use the
deploy path at the bottom of `scripts/refresh.sh` (it mirrors `public/` to local
disk first — do **not** deploy directly from iCloud; that path previously took
25+ minutes).

---

## Rollback

```bash
git revert <commit>
```

or restore the lenient behaviour without reverting code:

```bash
python3 scripts/monthly_swing_backtest.py --months 6 --unverified-adv include
```

---

## Important caveats

- **The DB on this machine is empty** (`data/dsa_hk.db`, 0 rows). If Step 1 aborts
  with "No BUY signals in window", you are on the wrong machine — run it on the
  server/box that holds the populated DB.
- Step 1 is **not** part of `scripts/refresh.sh` and is **not** on cron or launchd.
  The page text claiming "每月 1 號自動重跑滾動更新" is not backed by a scheduled
  job in this repo. Until that is wired up, the monthly backtest must be run
  manually.
- Do not hand-edit `public/backtest.html`. It is fully regenerated from
  `latest.md` on every build.

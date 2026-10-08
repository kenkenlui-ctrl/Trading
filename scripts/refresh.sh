#!/bin/bash
# =========================================================
# refresh.sh — One-command pipeline to refresh win9you.com
# Top 200 → charts (futu) → backtest (futu) → action plan
# → build dashboards → git push → wrangler deploy
#
# 2026-09-30: the Cloudflare token used to be hard-coded here and committed in
# 787e6f5. It now lives in .env (gitignored, mode 600). This file only ever
# reads it, so rotating a token never means touching version control again.
# =========================================================
set -e
cd "$(dirname "$0")/.."

# Load credentials from .env if present. A real environment variable wins, so
# CI or a one-off `CLOUDFLARE_API_TOKEN=... ./scripts/refresh.sh` still works.
if [ -f .env ]; then
  set -a; . ./.env; set +a
fi

: "${CLOUDFLARE_API_TOKEN:?set CLOUDFLARE_API_TOKEN in .env or the environment}"

DATA_DATE="${DATA_DATE:-2026-08-22}"  # T-1 default

echo "=== LEEKS TERMINAL refresh ==="
echo "T-1 = $DATA_DATE"
echo "Started: $(date '+%Y-%m-%d %H:%M:%S')"
echo

# --- Step 1: Get HK + US top 200 by turnover ---
echo "[1/6] Fetching top 200 HK + US universes..."
python3 /Users/kenken/dev/dsa-hk/charts/hk200/batch_hk200_fresh.py --date "$DATA_DATE" 2>&1 | tail -3
python3 /Users/kenken/dev/dsa-hk/charts/us200/batch_us200.py --date "$DATA_DATE" 2>&1 | tail -3 || echo "  US200 batch (continue anyway)"

# --- Step 1b: Refresh the raw OHLC series that the interactive charts AND v2 read ---
# 2026-10-07. This step was MISSING, and its absence had two consequences that
# looked like unrelated bugs:
#   1. the ticker page showed a fresh header and static PNG but an interactive
#      chart whose last candle was the previous session (SPCX: charts/SPCX.json
#      at 2026-10-05, ohlc/SPCX_ohlc.json at 2026-09-30). update_ohlc.py's own
#      docstring documents exactly this and was written when the class of bug
#      was first hit — the fix existed and was never wired into the pipeline.
#   2. build_v2_signals.py reads public/<market>/ohlc/*.json, so the SIGNAL SET
#      was computed from the stale series too. SPCX had no v2 signal partly
#      because its file held 76 bars and v2 needs a 200-day average.
# A repair that is not in the pipeline is not a repair.
echo
echo "[1b/7] Refreshing <market>/ohlc/*_ohlc.json (chart + v2 signal source)..."
# 2026-10-08: one DATA_DATE cannot describe three markets. They do not share a
# trading calendar — on 2026-10-08 HK and JP had closed the 10-08 session while
# the US had only closed 10-07, and passing 10-08 for the US would have asked
# Yahoo for a session that has not happened yet.
#
# Ownership (Kenneth, 2026-10-08): Kenneth states the date per market when he
# asks for a refresh, and I pass it straight through. Nothing here infers or
# auto-derives a session — an inferred "T-1" is a guess, and a guess that is
# one session ahead silently asks the vendor for data that does not exist yet.
# Set ASOF_HK / ASOF_US / ASOF_JP explicitly when the markets differ; any market
# left unset falls back to DATA_DATE.
ASOF_HK="${ASOF_HK:-$DATA_DATE}"
ASOF_US="${ASOF_US:-$DATA_DATE}"
ASOF_JP="${ASOF_JP:-$DATA_DATE}"
python3 scripts/update_ohlc.py \
  --asof "$DATA_DATE" --asof-hk "$ASOF_HK" --asof-us "$ASOF_US" --asof-jp "$ASOF_JP" \
  --markets hk,us,jp --workers 8 2>&1 | tail -3

# --- Universe lists are NOT regenerated here ------------------------------
# Owner decision (Kenneth, 2026-10-08): universe regen runs weekly on cloud
# (Undercover), and the resulting hk_universe_200.json / us_universe_200.json /
# jp_universe_200.json are pushed into the repo. This pipeline only READS them.
#
# That is a deliberate split: the universe turns over on a weekly clock while
# the market data turns over daily, and running a yfinance-wide re-rank inside
# the daily refresh would put an unaudited 200-name change in front of every
# build. regen_all.py remains the entry point for whoever runs it on cloud —
# do NOT re-wire it into this script.

# --- Step 2: Generate per-ticker charts + action_plan JSON (daily-sr-chart skill) ---
echo
echo "[2/6] Generating per-ticker charts (HK)..."
mkdir -p /tmp/chart_logs
python3 -c "
import sys, json
from pathlib import Path
sys.path.insert(0, '/Users/kenken/.minimax/skills/daily-sr-chart/scripts')
from daily_sr import fetch_ohlc
from futu import OpenQuoteContext, KLType, RET_OK

# Single shared futu connection
ctx = OpenQuoteContext(host='127.0.0.1', port=11111)
try:
    universe = json.load(open('hk_universe_200.json'))
    out = Path('/Users/kenken/dev/dsa-hk/charts/hk200')
    for i, tk in enumerate(universe):
        safe = tk.replace('.', '_')
        png = out / f'{safe}.png'
        js = out / f'{safe}.json'
        # 2026-10-05: this guard used to be `png.st_mtime > 1747700000` — a
        # hard-coded epoch equal to 2025-05-20. Every chart drawn after that
        # date counted as "recent", so a 200/200 skip fired on every run and
        # refresh.sh silently became a no-op: no chart, no action_plan JSON, no
        # data change. Skip only when this ticker is ALREADY at the target
        # session, which is the only question the guard was meant to ask.
        if js.exists():
            try:
                _have = (json.load(open(js)).get('last_bar') or {}).get('date')
            except Exception:
                _have = None
            if _have == '$DATA_DATE' and png.exists():
                continue
        try:
            df, src, name = fetch_ohlc(tk, days=200, source='auto', asof='$DATA_DATE')
            # call render via subprocess to avoid matplotlib in this process
            import subprocess
            subprocess.run(['python3', '/Users/kenken/.minimax/skills/daily-sr-chart/scripts/daily_sr.py',
                            '-t', tk, '-o', str(png),
                            '--source', 'auto', '--asof', '$DATA_DATE'], check=True)
            print(f'  {i+1}/{len(universe)} {tk} ✓')
        except Exception as e:
            print(f'  {i+1}/{len(universe)} {tk} ✗ {e}')
finally:
    ctx.close()
" 2>&1 | tail -5

# --- Step 3: Backtest per ticker (futu) ---
echo
echo "[3/6] Backtest per ticker (HK + US)..."
python3 ~/.minimax/skills/daily-sr-chart/scripts/backtest_futu.py \
  -t "$(python3 -c 'import json; print(",".join(json.load(open("hk_universe_200.json"))))')" \
  -w 30,60,90,197 \
  -o /Users/kenken/dev/dsa-hk/data/backtest_out 2>&1 | tail -3

if [ -f /Users/kenken/dev/dsa-hk/charts/us200/us_top200_fresh.json ]; then
  python3 ~/.minimax/skills/daily-sr-chart/scripts/backtest_futu.py \
    -t "$(python3 -c 'import json; print(",".join(json.load(open("/Users/kenken/dev/dsa-hk/charts/us200/us_top200_fresh.json"))))')" \
    -w 30,60,90,197 \
    -o /Users/kenken/dev/dsa-hk/data/backtest_out 2>&1 | tail -3 || echo "  US backtest (continue)"
fi

# --- Step 4: Capital-flow anomalies (⚡ requires OpenD) ---
echo
echo "[4/7] Fetching capital anomalies (⚡ 資金異動, ~4 min)..."
python3 scripts/fetch_capital_anomaly.py --date "$DATA_DATE" 2>&1 | tail -3 || echo "  capital anomaly fetch (continue anyway)"

# --- Step 5: Build dashboards + detail pages ---
echo
echo "[5/7] Building dashboards + detail pages..."
python3 scripts/build_dashboard.py 2>&1 | tail -10

# --- Step 5b: Build home page (uses latest data) ---
echo
echo "[5/6] Building home page..."
python3 scripts/build_home.py 2>&1 | tail -3

# --- Step 7: Build static pages + backtest page ---
echo
echo "[7/7] Static pages + backtest page..."
python3 scripts/build_static.py 2>&1 | tail -2

# --- Step 7b: sitemap.xml + llms.txt ---
# Both files were previously outside the pipeline. llms.txt was last written
# 2026-09-06 and spent a month telling AI answer engines to "cite these" figures
# (T+10 +3.05%, 121 signals) that the site had already retracted, plus the v1
# strategy names that are switched off. A file that no build step regenerates
# does not stay true.
echo
echo "[7b] Sitemap + llms.txt..."
python3 scripts/build_sitemap.py 2>&1 | tail -2
python3 scripts/build_seo.py 2>&1 | tail -2
# 2026-10-02: /insights.html shipped hard-coded index levels (HSI 18,420,
# USD/JPY 142.8) written on 2026-09-03 while the real closes were 24,613 /
# 157.95 — a 25% error on a site that claims "zero LLM hallucination".
# This re-fetches the index strip from Yahoo Finance on every build.
python3 scripts/build_insights_index.py 2>&1 | tail -1
# Market Radar on the same page. Must run after build_insights_index so the
# top strip and the cards below it are stamped from the same session — they
# used to disagree (strip 23,972 vs a hand-typed card reading 18,420).
python3 scripts/build_insights_radar.py 2>&1 | tail -1
# 2026-10-02: v2 limit-buy plan (primary signal set). Reads our own OHLC from
# public/<market>/ohlc/*.json, applies the 10-year-audit's rule set, and
# writes public/v2-signals.json for build_dashboard.py to render.
python3 scripts/build_v2_signals.py 2>&1 | tail -2
python3 scripts/build_backtest_page.py 2>&1 | tail -1
# /track-record/ — the measured bar (read from data/control_test.csv) beside the
# user's own logged trades (data/track_record.json). Writes HTML, so it has to
# sit before finalize.py below, like every other writer.
python3 scripts/build_track_record.py 2>&1 | tail -3

# --- Step 7c: final normalisation pass. MUST be the last thing that touches
# public/ before the commit. build_backtest_page / build_insights_index /
# build_insights_radar / build_v2_signals all run after build_static.py, and
# each writes HTML of its own — so they re-introduce whatever build_static's
# post-passes just fixed. On 2026-10-07 backtest.html shipped "T-1 · 2026-10-06"
# while every market page carried its own market's date. Re-ordering the chain
# would only hold until the next builder is added after it; this makes "run the
# passes once more, last" the single obvious final step. Idempotent.
echo
echo "[7c] Final normalisation (badge / css hash / nav dedupe / faq links)..."
python3 scripts/finalize.py 2>&1 | tail -4

# --- Step 6: Git commit + wrangler deploy ---
# Finder drops .DS_Store into .git/refs/heads/, which git reads as a ref and
# reports as badRefName. A junk ref also breaks `git bundle --all`. Clear it
# before the commit so fsck output stays readable and backups keep working.
bash scripts/git_housekeeping.sh 2>&1 | tail -2

git add -A
git commit -m "chore: refresh T-1 data ($DATA_DATE)" || echo "  no changes to commit"
git push origin main 2>&1 | tail -3

# 2026-10-01: this repo lives under ~/Documents, which is iCloud-Drive-backed.
# Uploading straight from ./public made wrangler crawl 6,752 files at ~0.8 files/s
# (each file is an on-demand iCloud download) — 25 min with zero upload progress,
# plus wrangler's own `git status` dirty check hung for the same reason.
# Fix: mirror public/ to local disk with a 24-way parallel copy first, then deploy
# the mirror from OUTSIDE the git work tree (no repo => wrangler skips the git check).
# Measured: 25+ min -> 13.3 s upload.
STAGE="/tmp/w9pub"
echo
echo "[6b/7] Staging public/ to local disk (parallel iCloud materialisation)..."
rm -rf "$STAGE"; mkdir -p "$STAGE"
# Excluded here because they are regenerated-then-never-linked build debris
# (2026-10-01: dashboard/ alone was 2,304 files / 28 MB and nothing links to
# it — the only reference, live-monitor.html -> /dashboard/2026-07-16/, was
# already a 404 before this change. /dashboard/ itself 301s to /hk200/ in
# _redirects, so deleting the tree breaks no URL. build_static.py still writes
# it locally; we just never ship it.)
( cd public && find . -type f \
    -not -name '.DS_Store' -not -name '_redirects.bak' \
    -not -path './dashboard/*' \
    -not -name 'compare-*.png' -not -name 'ba-chart-fixed.png' \
    -not -name '* 2.html' \
    -print0 \
    | xargs -0 -P 24 -I{} sh -c 'mkdir -p "'"$STAGE"'/$(dirname "{}")" 2>/dev/null; cp -p "{}" "'"$STAGE"'/{}" 2>/dev/null' )

# Prune orphan ohlc json. static/canvas-chart.js derives the interactive-chart
# URL from the chart URL, so ONLY <market>/ohlc/<ticker-page-name>_ohlc.json is
# ever fetched. Anything else (HK_01044_ohlc.json, US_ORLY_ohlc.json, ...) is
# dead weight — 702 files / ~21 MB. Done here because the naming mismatch can't
# be expressed as a find -name pattern.
python3 - "$STAGE" <<'PY'
import glob, os, sys
stage = sys.argv[1]
n = 0
for m in ("hk200", "jp200", "us200"):
    pages = {os.path.basename(p)[:-5] for p in glob.glob(f"{stage}/{m}/ticker/*.html")}
    for p in glob.glob(f"{stage}/{m}/ohlc/*.json"):
        if os.path.basename(p)[:-len("_ohlc.json")] not in pages:
            os.remove(p); n += 1
print(f"  pruned {n} orphan ohlc json")
PY

echo "  staged $(find "$STAGE" -type f | wc -l | tr -d ' ') files"

# The API token can upload but cannot list accounts, so wrangler's account
# discovery fails unless the account id is supplied explicitly.
export CLOUDFLARE_ACCOUNT_ID="${CLOUDFLARE_ACCOUNT_ID:-569063f557ea8d8f2c3f7d214c9ead8c}"

( cd /tmp && npx -y wrangler@4.105.0 pages deploy "$(basename "$STAGE")" \
  --project-name=leeks-terminal \
  --commit-dirty=true ) 2>&1 | tail -5

echo
echo "=== Done at $(date '+%Y-%m-%d %H:%M:%S') ==="
echo "Live: https://www.win9you.com/"

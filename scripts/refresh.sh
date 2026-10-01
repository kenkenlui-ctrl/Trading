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
        if png.exists() and png.stat().st_mtime > 1747700000:  # skip if recent
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
python3 scripts/build_backtest_page.py 2>&1 | tail -1

# --- Step 6: Git commit + wrangler deploy ---
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

#!/bin/bash
# =========================================================
# refresh.sh — One-command pipeline to refresh win9you.com
# Top 200 → charts (futu) → backtest (futu) → action plan
# → build dashboards → git push → wrangler deploy
# =========================================================
set -e
cd "$(dirname "$0")/.."

DATA_DATE="${DATA_DATE:-2026-08-22}"  # T-1 default

echo "=== LEEKS TERMINAL refresh ==="
echo "T-1 = $DATA_DATE"
echo "Started: $(date '+%Y-%m-%d %H:%M:%S')"
echo

# --- Step 1: Get HK + US top 200 by turnover ---
echo "[1/6] Fetching top 200 HK + US universes..."
python3 /Users/kenken/Documents/Minimax/charts/hk200/batch_hk200_fresh.py --date "$DATA_DATE" 2>&1 | tail -3
python3 /Users/kenken/Documents/Minimax/charts/us200/batch_us200.py --date "$DATA_DATE" 2>&1 | tail -3 || echo "  US200 batch (continue anyway)"

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
    out = Path('/Users/kenken/Documents/Minimax/charts/hk200')
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
  -o /tmp/backtest_out 2>&1 | tail -3

if [ -f /Users/kenken/Documents/Minimax/charts/us200/us_top200_fresh.json ]; then
  python3 ~/.minimax/skills/daily-sr-chart/scripts/backtest_futu.py \
    -t "$(python3 -c 'import json; print(",".join(json.load(open("/Users/kenken/Documents/Minimax/charts/us200/us_top200_fresh.json"))))')" \
    -w 30,60,90,197 \
    -o /tmp/backtest_out 2>&1 | tail -3 || echo "  US backtest (continue)"
fi

# --- Step 4: Build dashboards + detail pages ---
echo
echo "[4/6] Building dashboards + detail pages..."
python3 scripts/build_dashboard.py 2>&1 | tail -10

# --- Step 5: Build home page (uses latest data) ---
echo
echo "[5/6] Building home page..."
# (home is mostly static; if we want live counts we'd need a builder; leaving for now)

# --- Step 6: Git commit + wrangler deploy ---
echo
echo "[6/6] Committing + deploying to win9you.com..."
git add -A
git commit -m "chore: refresh T-1 data ($DATA_DATE)" || echo "  no changes to commit"
git push origin main 2>&1 | tail -3

CLOUDFLARE_API_TOKEN="REDACTED_CF_TOKEN" \
  npx -y wrangler@4.105.0 pages deploy public \
  --project-name=leeks-terminal \
  --commit-dirty=true 2>&1 | tail -5

echo
echo "=== Done at $(date '+%Y-%m-%d %H:%M:%S') ==="
echo "Live: https://www.win9you.com/"

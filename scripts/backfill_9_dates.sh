#!/bin/bash
# Sequential backfill orchestrator for 7/20-7/27 (Phase 1 of comprehensive backfill)
# Each date uses 1 proc x 4 workers to avoid LLM rate limit contention
# Estimated total: 12-16 hours overnight (1.5-2hr per date)

set -e
cd /Users/kenken/Documents/dsa-hk

DATES=("2026-07-20" "2026-07-21" "2026-07-22" "2026-07-23" "2026-07-24" "2026-07-25" "2026-07-26" "2026-07-27" "2026-07-29")

for date in "${DATES[@]}"; do
    echo ""
    echo "=== Starting $date at $(date) ==="
    python3 scripts/run_daily.py --date "$date" --no-news --us-workers 4 --hk-workers 4
    rc=$?
    echo "=== Finished $date at $(date) (rc=$rc) ==="
    if [ $rc -ne 0 ]; then
        echo "ERROR: $date failed with rc=$rc, aborting"
        exit $rc
    fi
    # Brief pause to release LLM tokens
    sleep 60
done

echo ""
echo "=== All 8 dates complete at $(date) ==="

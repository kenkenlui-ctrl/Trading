#!/bin/bash
# Re-LLM orchestrator for backfilled 7 dates (7/20-7/24, 7/27, 7/29)
# Uses rellm_aggressive.py --all to ensure all records (including
# fast_backfill snapshot-only) get re-processed.
# Estimated 7-10hr sequential.

set -e
cd /Users/kenken/Documents/dsa-hk

DATES=("2026-07-15" "2026-07-16" "2026-07-17" "2026-07-20" "2026-07-21" "2026-07-22" "2026-07-23" "2026-07-24" "2026-07-27" "2026-07-28" "2026-07-29")

for date in "${DATES[@]}"; do
    echo ""
    echo "=== Re-LLM $date at $(date) ==="
    python3 scripts/rellm_aggressive.py --dates "$date" --workers 4 --retries 3 --all 2>&1 | tail -30
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
echo "=== All 7 dates re-LLM done at $(date) ==="

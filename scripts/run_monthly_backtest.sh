#!/bin/bash
# Monthly backtest runner — invoked by com.dsa-hk.monthlybacktest (1st of month).
#
# Wraps monthly_swing_backtest.py with the guards it does not have on its own:
#   1. Fail LOUD if the signal DB is empty/unreadable. Without this the backtest
#      exits early with "No BUY signals in window" and writes NOTHING — a silent
#      no-op that looks identical to success. (Same failure class as the ADV bug:
#      structurally fine, actually doing nothing.)
#   2. Fail LOUD if the backtest produced 0 records, for the same reason.
#   3. Only rebuild the published pages when a fresh result actually exists.
#   4. Emit a one-line JSON receipt so success/failure is externally checkable.

set -uo pipefail

REPO="/Users/kenken/dev/dsa-hk"
PY="/usr/local/bin/python3"
LOG_DIR="$REPO/logs"
STAMP="$(date +%Y-%m-%d_%H%M%S)"
RUN_LOG="$LOG_DIR/monthly_backtest_${STAMP}.log"
RECEIPT="$LOG_DIR/monthly_backtest_receipt.json"

mkdir -p "$LOG_DIR"
cd "$REPO" || { echo "FATAL: cannot cd $REPO"; exit 1; }

emit_receipt() {
    # $1=status $2=detail
    cat > "$RECEIPT" <<JSON
{"status":"$1","timestamp":"$(date -u +%Y-%m-%dT%H:%M:%SZ)","detail":"$2","log":"$RUN_LOG"}
JSON
}

# ---- Guard 1: signal DB must exist and actually contain BUY signals ----------
DB="$REPO/data/dsa_hk.db"
if [ ! -f "$DB" ]; then
    echo "FATAL: signal DB missing at $DB" | tee -a "$RUN_LOG"
    emit_receipt "failed" "signal DB missing"
    exit 1
fi

BUY_N=$("$PY" - <<'PYSQL' 2>>"$RUN_LOG"
import sqlite3, sys
try:
    c = sqlite3.connect("/Users/kenken/dev/dsa-hk/data/dsa_hk.db")
    n = c.execute(
        "SELECT COUNT(*) FROM daily_report "
        "WHERE UPPER(COALESCE(operation_advice,'')) LIKE '%BUY%'"
    ).fetchone()[0]
    print(n)
except Exception:
    print(0)
PYSQL
)
BUY_N="${BUY_N:-0}"

if [ "$BUY_N" -lt 1 ]; then
    MSG="signal DB has $BUY_N BUY rows — backtest would be a silent no-op. NOT rebuilding pages."
    echo "FATAL: $MSG" | tee -a "$RUN_LOG"
    emit_receipt "failed" "empty signal DB ($BUY_N BUY rows)"
    exit 2
fi

echo "[monthly-backtest] $STAMP — BUY rows in DB: $BUY_N" | tee -a "$RUN_LOG"

# ---- Step 1: run the backtest -------------------------------------------------
"$PY" scripts/monthly_swing_backtest.py --months 6 >>"$RUN_LOG" 2>&1
BT_RC=$?

if [ $BT_RC -ne 0 ]; then
    echo "FATAL: backtest exited $BT_RC" | tee -a "$RUN_LOG"
    emit_receipt "failed" "backtest exit code $BT_RC"
    exit $BT_RC
fi

# ---- Guard 2: did it actually produce records? --------------------------------
if [ ! -s "$REPO/data/monthly_backtest/latest.md" ]; then
    echo "FATAL: latest.md missing/empty after run" | tee -a "$RUN_LOG"
    emit_receipt "failed" "latest.md not produced"
    exit 3
fi

REC_N=$("$PY" - <<'PYREC' 2>>"$RUN_LOG"
import json, glob, os
try:
    files = sorted(glob.glob("/Users/kenken/dev/dsa-hk/data/monthly_backtest/backtest_*.json"))
    if not files:
        print(0); raise SystemExit
    d = json.load(open(files[-1]))
    print(len(d.get("records", [])))
except Exception:
    print(0)
PYREC
)
REC_N="${REC_N:-0}"

if [ "$REC_N" -lt 1 ]; then
    echo "FATAL: 0 records produced — refusing to publish empty pages" | tee -a "$RUN_LOG"
    emit_receipt "failed" "0 records produced"
    exit 4
fi

# ---- Steps 2+3: rebuild pages only now that we have fresh data -----------------
"$PY" scripts/build_backtest_page.py    >>"$RUN_LOG" 2>&1 || echo "WARN: backtest page build failed" | tee -a "$RUN_LOG"
"$PY" scripts/build_equity_curves.py    >>"$RUN_LOG" 2>&1 || echo "WARN: equity curves build failed" | tee -a "$RUN_LOG"
"$PY" scripts/build_equity_curve.py     >>"$RUN_LOG" 2>&1 || echo "WARN: equity curve build failed"  | tee -a "$RUN_LOG"

echo "[monthly-backtest] done: $REC_N records, BUY rows $BUY_N" | tee -a "$RUN_LOG"
emit_receipt "ok" "rebuilt pages from $REC_N records"
exit 0

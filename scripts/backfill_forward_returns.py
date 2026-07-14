"""One-time backfill: compute forward returns for ALL historical date pairs.

Use this to populate backtest_results for past date pairs (6/27-7/9).
After this runs, future forward returns will be computed by the daily cron.

Usage: python scripts/backfill_forward_returns.py
"""
import sys, os, sqlite3
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from scripts.compute_forward_returns import (
    ensure_backtest_table, compute_for_date, get_signal_date
)
from src.db import get_db


def main():
    ensure_backtest_table()

    # Get all trading dates in DESC order
    conn = get_db()
    cur = conn.execute("SELECT DISTINCT report_date FROM daily_report ORDER BY report_date DESC")
    dates = [r[0] for r in cur.fetchall()]
    conn.close()

    print(f"Found {len(dates)} trading dates: {dates}")
    print()

    # Process each pair (date[i+1] is signal, date[i] is forward)
    # dates[0] is most recent (no forward — skip)
    total = 0
    for i in range(len(dates) - 1):
        d_signal = dates[i + 1]  # older
        d_forward = dates[i]      # newer
        print(f"\n=== {d_signal} → {d_forward} ===")
        n = compute_for_date(d_signal, d_forward)
        total += n
        # Skip yfinance rate limit
        if "yfinance" in str(n) or n is None:
            pass

    print(f"\n✅ Total: {total} forward returns backfilled")

    # Summary
    conn = get_db()
    cur = conn.execute("""
        SELECT signal_date, COUNT(*),
               AVG(forward_return_pct),
               AVG(CASE WHEN win=1 THEN 1.0 ELSE 0.0 END) * 100
        FROM backtest_results
        GROUP BY signal_date
        ORDER BY signal_date
    """)
    print(f"\n=== Per-date summary ===")
    print(f"{'Date':<12} {'N':<5} {'AvgRet':<8} {'WR%':<6}")
    for d, n, avg, wr in cur.fetchall():
        print(f"{d:<12} {n:<5} {avg:+.2f}%  {wr:.1f}%")
    conn.close()


if __name__ == "__main__":
    main()

"""Build dashboard pages only — fast, skips paper-trades and other slow pages.

Use this instead of build_static.py when the paper-trades page hangs.
Skips: paper-trades.html, intent pages, full-results.

Usage:
    python3 scripts/build_dashboard_only.py
    python3 scripts/build_dashboard_only.py --date 2026-07-15
"""
import sys
import argparse
import sqlite3
from pathlib import Path

PROJECT_ROOT = Path("/Users/kenken/Documents/dsa-hk")
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.build_static import (
    build_dashboard_for_date, build_index,
    PUBLIC_DIR,
)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", default=None, help="Specific date (else all)")
    parser.add_argument("--all", action="store_true", help="All dates")
    parser.add_argument("--index", action="store_true", help="Just dashboard index")
    args = parser.parse_args()

    conn = sqlite3.connect(str(PROJECT_ROOT / "data" / "dsa_hk.db"))
    cur = conn.cursor()
    cur.execute("SELECT report_date, COUNT(*) FROM daily_report GROUP BY report_date ORDER BY report_date DESC")
    all_dates_db = [r[0] for r in cur.fetchall()]
    conn.close()

    if args.date:
        dates = [args.date]
    elif args.all:
        dates = all_dates_db
    else:
        dates = all_dates_db[-1:]  # default: just latest

    written = []
    for date in dates:
        try:
            written_paths, n = build_dashboard_for_date(date)
            for w in written_paths:
                written.append(str(w))
        except Exception as e:
            print(f"  ⚠️  {date} failed: {type(e).__name__}: {e}")

    if args.index or args.all:
        try:
            html = build_index(all_dates_db)
            (PUBLIC_DIR / "dashboard" / "index.html").write_text(html, encoding="utf-8")
            written.append("dashboard/index.html")
        except Exception as e:
            print(f"  ⚠️  index build failed: {e}")

    print(f"✅ Built {len(written)} dashboard pages for {len(dates)} dates")
    for w in written[:5]:
        print(f"   - {w}")
    if len(written) > 5:
        print(f"   ... and {len(written) - 5} more")


if __name__ == "__main__":
    main()
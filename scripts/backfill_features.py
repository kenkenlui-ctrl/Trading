"""Backfill features into daily_report for 6/26-7/17 records.

Phase 9 Step 3 (2026-07-18): add 5d rolling + sector cross-section + dist_52w
features to all existing records so LR can be retrained on enriched data.

Adds columns to data_snapshot_json (or stores in new features_json column).
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import sqlite3
from src.features import compute_all_features

DB_PATH = "/Users/kenken/Documents/dsa-hk/data/dsa_hk.db"


def main():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row

    # Add features_json column if not exists
    try:
        con.execute("ALTER TABLE daily_report ADD COLUMN features_json TEXT")
        print("Added features_json column")
    except sqlite3.OperationalError:
        pass  # already exists

    rows = con.execute("""
        SELECT id, code, report_date, data_snapshot_json
        FROM daily_report
        WHERE report_date >= '2026-06-26' AND report_date <= '2026-07-17'
        ORDER BY report_date, code
    """).fetchall()
    print(f"Backfilling features for {len(rows)} records")

    updated = 0
    unchanged = 0
    for r in rows:
        try:
            snap = json.loads(r["data_snapshot_json"] or "{}")
        except Exception:
            snap = {}
        feats = compute_all_features(r["code"], r["report_date"], snap, [])
        # Save into features_json column
        con.execute(
            "UPDATE daily_report SET features_json=? WHERE id=?",
            (json.dumps(feats, ensure_ascii=False, default=str), r["id"]),
        )
        updated += 1
        if updated <= 3:
            print(f"  {r['code']:<10} {r['report_date']} v={feats['value_score']} q={feats['quality_score']} m={feats['momentum_score']} chg_5d={feats.get('chg_5d')}")

    con.commit()
    con.close()
    print(f"\nDone: {updated} updated")


if __name__ == "__main__":
    main()

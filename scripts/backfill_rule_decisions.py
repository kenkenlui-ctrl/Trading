"""Backfill: apply rule-based decision to all existing daily_report records.

Phase 2 (2026-07-10): re-derive operation_advice for all 10 days of
existing reports using the new rule-based engine. Preserves LLM's
original op in llm_original_op + records decision_reason.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import sqlite3

from src.signal_decision import apply_to_snapshot

DB_PATH = "/Users/kenken/Documents/dsa-hk/data/dsa_hk.db"


def main():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row

    rows = con.execute("""SELECT id, code, report_date, score, sentiment, trend,
                              operation_advice, score_breakdown_json, data_snapshot_json
                       FROM daily_report""").fetchall()
    print(f"Found {len(rows)} records to backfill")

    updated = 0
    same = 0
    for r in rows:
        rid = r["id"]
        current_op = r["operation_advice"]
        try:
            sb = json.loads(r["score_breakdown_json"] or "{}")
        except Exception:
            sb = {}
        try:
            snap = json.loads(r["data_snapshot_json"] or "{}")
        except Exception:
            snap = {}
        sector = (snap.get("sector") or "").strip()

        # The current operation_advice MIGHT be the LLM's original (if old
        # save_report was called without rule). To know, check if
        # llm_original_op is set. If set, current op is rule-based.
        llm_orig_row = con.execute("SELECT llm_original_op FROM daily_report WHERE id=?", (rid,)).fetchone()
        if llm_orig_row and llm_orig_row["llm_original_op"]:
            # Already has rule applied
            same += 1
            continue

        # Current op is the LLM's original — apply rule
        decision = apply_to_snapshot(
            llm_op=current_op,
            llm_sentiment=r["sentiment"] or "",
            llm_trend=r["trend"] or "",
            score_breakdown=sb,
            data_snapshot=snap,
            sector=sector,
        )
        new_op = decision.op
        reason = f"[{decision.matched_rule}] {decision.reason}"

        con.execute(
            """UPDATE daily_report SET operation_advice=?, llm_original_op=?, decision_reason=? WHERE id=?""",
            (new_op, current_op, reason, rid),
        )
        updated += 1
        if updated <= 5:
            print(f"  {r['code']:<10} {r['report_date']}: {current_op} → {new_op} ({decision.matched_rule})")

    con.commit()
    con.close()
    print(f"\nDone: {updated} updated, {same} already had rule applied")


if __name__ == "__main__":
    main()
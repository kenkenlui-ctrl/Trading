"""Re-score all 2026-06-27 reports using new day-trade weights.

Old: value 0.25 + quality 0.25 + momentum 0.50
New (per user 2026-06-28): value 0.05 + quality 0.05 + momentum 0.70 + order_flow 0.20

Since the LLM doesn't have an order_flow dim, we approximate order_flow by
re-using momentum_score for the 0.70 weight band (LLM already folds
volume ratio into momentum per prompt). The 0.05+0.05+0.70+0.20 sums to 1.0
but order_flow is mapped to momentum_score since the prompt told the LLM
to fold it in.

Final formula applied to existing breakdown:
    new_score = round(0.05 * value + 0.05 * quality + 0.70 * momentum + 0.20 * momentum)
              = round(0.05 * value + 0.05 * quality + 0.90 * momentum)

Rationale: 0.90 momentum is the conservative 0.70+0.20 reading.
If we want a separate order_flow signal later, we'd need to re-prompt the LLM
to emit it. For now this matches the user's intent: momentum is the dominant
driver for day-trade, value/quality are tiebreakers only.
"""
import json
import sqlite3
from pathlib import Path

DB = Path(__file__).parent.parent / "data" / "dsa_hk.db"

import sys
DATE = sys.argv[1] if len(sys.argv) > 1 else "2026-06-27"

def rescore():
    conn = sqlite3.connect(DB)
    cur = conn.cursor()
    rows = cur.execute(
        "SELECT id, code, score, score_breakdown_json FROM daily_report WHERE report_date=?",
        (DATE,),
    ).fetchall()
    print(f"Found {len(rows)} reports for {DATE}")
    updates = []
    for row_id, code, old_score, breakdown_raw in rows:
        try:
            bd = json.loads(breakdown_raw) if breakdown_raw else {}
        except Exception:
            bd = {}
        v = bd.get("value_score") or 0
        q = bd.get("quality_score") or 0
        m = bd.get("momentum_score") or 0
        of = bd.get("order_flow_score") or 0
        # Day-trade weights: 5/5/70/20 (4-dim).
        # If order_flow not present (legacy data), fold it into momentum (5/5/90).
        if of:
            new_score = int(round(0.05 * v + 0.05 * q + 0.70 * m + 0.20 * of))
        else:
            new_score = int(round(0.05 * v + 0.05 * q + 0.90 * m))
        new_score = max(0, min(100, new_score))
        updates.append((new_score, json.dumps(bd, ensure_ascii=False), row_id))
    cur.executemany(
        "UPDATE daily_report SET score=?, score_breakdown_json=? WHERE id=?",
        updates,
    )
    conn.commit()
    print(f"Updated {len(updates)} rows")
    # Sample
    for row_id, code, old_score, _ in rows[:8]:
        bd = json.loads(cur.execute("SELECT score_breakdown_json FROM daily_report WHERE id=?", (row_id,)).fetchone()[0] or "{}")
        new_score = cur.execute("SELECT score FROM daily_report WHERE id=?", (row_id,)).fetchone()[0]
        print(f"  {code}: {old_score} → {new_score} (v={bd.get('value_score',0)} q={bd.get('quality_score',0)} m={bd.get('momentum_score',0)})")
    conn.close()

if __name__ == "__main__":
    rescore()
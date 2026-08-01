"""Backfill: apply rule-based decision to existing daily_report records.

Phase 2 (2026-07-10): re-derive operation_advice using the rule engine.
Phase 7 (2026-07-17): VALUE rule + LR signal_score.
Phase 10.1 (2026-07-30): signal_score = direction_score (0=short … 100=long).

By default rewrites ALL dates in daily_report. Override with --from / --to.
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import sqlite3

from src.signal_decision import apply_to_snapshot, direction_score
from src.db import get_market_state_for_date

DB_PATH = "/Users/kenken/Documents/dsa-hk/data/dsa_hk.db"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="date_from", default="2026-06-26")
    ap.add_argument("--to", dest="date_to", default="2099-12-31")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row

    rows = con.execute(
        """
        SELECT id, code, report_date, score, sentiment, trend,
               operation_advice, llm_original_op, signal_score,
               score_breakdown_json, data_snapshot_json, decision_reason
        FROM daily_report
        WHERE report_date >= ? AND report_date <= ?
        ORDER BY report_date, code
        """,
        (args.date_from, args.date_to),
    ).fetchall()
    print(f"Backfilling {len(rows)} records ({args.date_from} → {args.date_to})")
    print("signal_score: 0=SHORT confidence … 50=neutral … 100=LONG confidence")

    updated = 0
    unchanged = 0
    rule_dist: dict[str, int] = {}
    hsi_cache: dict[str, float | None] = {}

    def _hsi_for(d: str):
        if d not in hsi_cache:
            ms = get_market_state_for_date(d)
            hsi_cache[d] = (
                ms["hsi_chg_pct"] if (ms and ms.get("hsi_chg_pct") is not None) else None
            )
        return hsi_cache[d]

    for r in rows:
        old_op = r["operation_advice"]
        llm_orig = r["llm_original_op"] or r["operation_advice"]
        try:
            sb = json.loads(r["score_breakdown_json"] or "{}")
        except Exception:
            sb = {}
        try:
            snap = json.loads(r["data_snapshot_json"] or "{}")
        except Exception:
            snap = {}
        sector = (snap.get("sector") or "").strip()
        hsi_chg = _hsi_for(r["report_date"])

        decision = apply_to_snapshot(
            llm_op=llm_orig,
            llm_sentiment=r["sentiment"] or "",
            llm_trend=r["trend"] or "",
            score_breakdown=sb,
            data_snapshot=snap,
            sector=sector,
            hsi_yesterday_chg=hsi_chg,
        )
        new_op = decision.op
        new_reason = f"[{decision.matched_rule}] {decision.reason}"
        new_score = direction_score(
            score_breakdown=sb,
            data_snapshot=snap,
            sentiment=r["sentiment"] or "",
            matched_rule=decision.matched_rule,
        )

        rule_dist[decision.matched_rule] = rule_dist.get(decision.matched_rule, 0) + 1

        if (
            new_op == old_op
            and new_reason == r["decision_reason"]
            and new_score == r["signal_score"]
        ):
            unchanged += 1
            continue

        if not args.dry_run:
            con.execute(
                """UPDATE daily_report
                   SET operation_advice=?, decision_reason=?, signal_score=?, llm_original_op=?
                   WHERE id=?""",
                (new_op, new_reason, new_score, llm_orig, r["id"]),
            )
        updated += 1
        if updated <= 10:
            print(
                f"  {r['code']:<10} {r['report_date']}: {old_op} → {new_op} "
                f"[{decision.matched_rule}], dir {r['signal_score']} → {new_score}"
            )

    if not args.dry_run:
        con.commit()
    con.close()

    print(
        f"\nDone: {updated} updated, {unchanged} unchanged"
        + (" (dry-run)" if args.dry_run else "")
    )
    print("\nRule distribution:")
    for rule, n in sorted(rule_dist.items(), key=lambda x: -x[1]):
        print(f"  {rule:<20} {n:>5}")


if __name__ == "__main__":
    main()

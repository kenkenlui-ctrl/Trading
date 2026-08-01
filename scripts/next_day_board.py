#!/usr/bin/env python3
"""Next-day long/short board — ranked by single direction_score.

direction_score (0–100) is THE confidence dial:
  0   → high chance SHORT wins next day
  50  → no edge (skip)
  100 → high chance LONG wins next day

Usage:
  python3 scripts/next_day_board.py
  python3 scripts/next_day_board.py --date 2026-07-28
  python3 scripts/next_day_board.py --top 10 --backtest
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.signal_decision import (  # noqa: E402
    apply_to_snapshot,
    direction_label,
    direction_score,
    next_day_long_score,
    next_day_short_score,
)

DB_PATH = ROOT / "data" / "dsa_hk.db"


def _load_rows(con: sqlite3.Connection, report_date: str):
    con.row_factory = sqlite3.Row
    return con.execute(
        """
        SELECT code, report_date, score, sentiment, trend, operation_advice,
               llm_original_op, score_breakdown_json, data_snapshot_json,
               decision_reason, signal_score, entry_zone, stop_loss, target_price
        FROM daily_report
        WHERE report_date = ?
        """,
        (report_date,),
    ).fetchall()


def _latest_date(con: sqlite3.Connection) -> str:
    row = con.execute("SELECT MAX(report_date) FROM daily_report").fetchone()
    return row[0]


def _hsi_chg(con: sqlite3.Connection, report_date: str):
    try:
        r = con.execute(
            "SELECT * FROM market_state WHERE date=? OR report_date=? LIMIT 1",
            (report_date, report_date),
        ).fetchone()
        if r:
            keys = r.keys() if hasattr(r, "keys") else []
            for k in ("hsi_chg", "hsi_change_pct", "hsi_pct", "hsi_chg_pct"):
                if k in keys and r[k] is not None:
                    return float(r[k])
    except Exception:
        pass
    try:
        r = con.execute(
            "SELECT data_snapshot_json FROM daily_report "
            "WHERE report_date=? AND code='02800.HK'",
            (report_date,),
        ).fetchone()
        if r and r[0]:
            ds = json.loads(r[0])
            if ds.get("change_pct") is not None:
                return float(ds["change_pct"])
    except Exception:
        pass
    return None


def build_board(report_date: str, top: int = 15):
    con = sqlite3.connect(str(DB_PATH))
    rows = _load_rows(con, report_date)
    hsi = _hsi_chg(con, report_date)
    buys, shorts, all_items = [], [], []

    for r in rows:
        sb = json.loads(r["score_breakdown_json"] or "{}")
        ds = json.loads(r["data_snapshot_json"] or "{}")
        sector = (ds.get("sector") or "").strip()
        llm_op = r["llm_original_op"] or r["operation_advice"] or "觀望"
        sent = r["sentiment"] or "中性"
        decision = apply_to_snapshot(
            llm_op=llm_op,
            llm_sentiment=sent,
            llm_trend=r["trend"] or "",
            score_breakdown=sb,
            data_snapshot=ds,
            sector=sector,
            hsi_yesterday_chg=hsi,
        )
        d_score = direction_score(sb, ds, sent, decision.matched_rule)
        long_s = next_day_long_score(sb, ds, sent, decision.matched_rule)
        short_s = next_day_short_score(sb, ds, sent, decision.matched_rule)
        pe = ds.get("pe_ttm")
        try:
            pe_f = float(pe) if pe is not None else None
        except (TypeError, ValueError):
            pe_f = None
        item = {
            "code": r["code"],
            "op": decision.op,
            "rule": decision.matched_rule,
            "direction_score": d_score,  # THE score: 0=short … 100=long
            "label": direction_label(d_score),
            "long_score": long_s,
            "short_score": short_s,
            "v": int(sb.get("value_score") or 0),
            "m": int(sb.get("momentum_score") or 0),
            "pe": pe_f,
            "chg": float(ds.get("change_pct") or 0),
            "sent": sent,
            "entry": r["entry_zone"],
            "stop": r["stop_loss"],
            "target": r["target_price"],
            "reason": decision.reason[:160],
            "stored_op": r["operation_advice"],
            # alias for DB/dashboard column name
            "signal_score": d_score,
        }
        all_items.append(item)
        if decision.op == "買入" or d_score >= 70:
            buys.append(item)
        if decision.op == "賣出" or d_score <= 30:
            shorts.append(item)

    # Longs: highest direction_score first
    buys.sort(key=lambda x: x["direction_score"], reverse=True)
    # Shorts: lowest direction_score first (closest to 0)
    shorts.sort(key=lambda x: x["direction_score"])
    # Full ranked extremes: |score-50| with rule-op preferred
    extremes = sorted(
        all_items,
        key=lambda x: abs(x["direction_score"] - 50),
        reverse=True,
    )

    con.close()
    return {
        "date": report_date,
        "hsi_chg": hsi,
        "n": len(rows),
        "buys": buys[:top],
        "shorts": shorts[:top],
        "extremes": extremes[:top],
        "n_buy": len(buys),
        "n_short": len(shorts),
        "n_hold": len(rows) - len(buys) - len(shorts),
        "scale": "0=SHORT win … 50=neutral … 100=LONG win",
    }


def run_backtest_summary():
    """Bucket historical next-day returns by direction_score bands."""
    con = sqlite3.connect(str(DB_PATH))
    con.row_factory = sqlite3.Row
    rows = con.execute(
        """
        SELECT b.forward_return_pct,
               d.sentiment, d.llm_original_op, d.operation_advice,
               d.score_breakdown_json, d.data_snapshot_json
        FROM backtest_results b
        JOIN daily_report d ON d.code=b.code AND d.report_date=b.signal_date
        WHERE b.forward_return_pct IS NOT NULL
        """
    ).fetchall()

    bands = [
        ("0-15  STRONG_SHORT", 0, 15),
        ("16-30 LEAN_SHORT", 16, 30),
        ("31-69 NEUTRAL", 31, 69),
        ("70-84 LEAN_LONG", 70, 84),
        ("85-100 STRONG_LONG", 85, 100),
    ]
    bucket = {name: [] for name, _, _ in bands}
    rules = {}

    for r in rows:
        sb = json.loads(r["score_breakdown_json"] or "{}")
        ds = json.loads(r["data_snapshot_json"] or "{}")
        llm_op = r["llm_original_op"] or r["operation_advice"] or "觀望"
        d = apply_to_snapshot(
            llm_op=llm_op,
            llm_sentiment=r["sentiment"] or "",
            llm_trend="",
            score_breakdown=sb,
            data_snapshot=ds,
            sector=(ds.get("sector") or ""),
        )
        sc = direction_score(sb, ds, r["sentiment"] or "", d.matched_rule)
        ret = float(r["forward_return_pct"])
        for name, lo, hi in bands:
            if lo <= sc <= hi:
                bucket[name].append(ret)
                break
        rules.setdefault(d.matched_rule, []).append((d.op, ret, sc))

    print("\n=== direction_score bands vs next-day return ===")
    print(f"{'band':22} {'n':>5} {'longWR%':>8} {'shortWR%':>9} {'avg%':>8}")
    for name, _, _ in bands:
        rets = bucket[name]
        if not rets:
            print(f"{name:22} {0:5d}")
            continue
        n = len(rets)
        lwr = 100 * sum(1 for x in rets if x > 0) / n
        swr = 100 * sum(1 for x in rets if x < 0) / n
        avg = sum(rets) / n
        print(f"{name:22} {n:5d} {lwr:7.1f}% {swr:8.1f}% {avg:+7.3f}%")

    print("\n=== Rule sleeves (direction-aware WR) ===")
    print(f"{'rule':16} {'op':6} {'n':>5} {'WR%':>7} {'avg dir%':>9} {'mean score':>10}")
    for rule, items in sorted(rules.items(), key=lambda kv: -len(kv[1])):
        actionable = [(op, ret, sc) for op, ret, sc in items if op in ("買入", "賣出")]
        if not actionable:
            continue
        wins = 0
        pnls = []
        scores = []
        for op, ret, sc in actionable:
            if op == "買入":
                wins += 1 if ret > 0 else 0
                pnls.append(ret)
            else:
                wins += 1 if ret < 0 else 0
                pnls.append(-ret)
            scores.append(sc)
        n = len(pnls)
        print(
            f"{rule:16} {actionable[0][0]:6} {n:5d} "
            f"{100*wins/n:6.1f}% {sum(pnls)/n:+8.3f}% {sum(scores)/len(scores):9.1f}"
        )
    con.close()


def _fmt_pe(pe):
    return f"{pe:.1f}" if isinstance(pe, (int, float)) else "n/a"


def main():
    ap = argparse.ArgumentParser(description="Next-day board by direction_score")
    ap.add_argument("--date", default=None)
    ap.add_argument("--top", type=int, default=12)
    ap.add_argument("--backtest", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    con = sqlite3.connect(str(DB_PATH))
    date = args.date or _latest_date(con)
    con.close()
    if not date:
        print("No daily_report rows found", file=sys.stderr)
        sys.exit(1)

    board = build_board(date, top=args.top)
    if args.json:
        print(json.dumps(board, ensure_ascii=False, indent=2))
    else:
        hsi = board["hsi_chg"]
        hsi_s = f"{hsi:+.2f}%" if hsi is not None else "n/a"
        print(f"◆ Next-day board · {board['date']} · HSI day chg={hsi_s}")
        print(f"   {board['scale']}")
        print(
            f"   universe={board['n']}  long-lean={board['n_buy']}  "
            f"short-lean={board['n_short']}"
        )
        print("\n=== 🟢 LONG (direction_score high → long) ===")
        if not board["buys"]:
            print("  (none)")
        for i, x in enumerate(board["buys"], 1):
            print(
                f"  {i:2d}. {x['code']:12} DIR={x['direction_score']:3d} "
                f"{x['label']:13} {x['rule']:12} "
                f"v={x['v']} m={x['m']} pe={_fmt_pe(x['pe'])} chg={x['chg']:+.1f}%"
            )
        print("\n=== 🔴 SHORT (direction_score low → short) ===")
        if not board["shorts"]:
            print("  (none)")
        for i, x in enumerate(board["shorts"], 1):
            print(
                f"  {i:2d}. {x['code']:12} DIR={x['direction_score']:3d} "
                f"{x['label']:13} {x['rule']:12} "
                f"v={x['v']} m={x['m']} pe={_fmt_pe(x['pe'])} chg={x['chg']:+.1f}%"
            )
        print(
            "\nRead DIR only: ≥70 long · ≤30 short · 31–69 skip. "
            "FADE shorts = day-trade only. Not investment advice."
        )

    if args.backtest:
        run_backtest_summary()


if __name__ == "__main__":
    main()

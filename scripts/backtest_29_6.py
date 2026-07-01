"""Backtest 29/6 report signals against actual stock moves.
- US: 29/6 close → 30/6 pre-market or last available
- HK: 29/6 close → 30/6 morning (just before lunch)

Computes hit rate per signal type (買入/觀望/賣出).
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, asdict
from statistics import mean, median
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.data_fetcher import fetch_snapshot

DB = "/Users/kenken/Documents/dsa-hk/data/dsa_hk.db"
MAX_WORKERS = 8
REPORT_DATE = "2026-06-29"
MAX_WORKERS = int(os.environ.get("DSA_PARALLEL", "8"))


@dataclass
class BacktestRow:
    code: str
    market: str
    op: str  # 買入 / 觀望 / 賣出
    score: Optional[int]
    price_at_report: float
    price_now: float
    pct_change: float
    signal_correct: Optional[bool]  # True if move aligned with signal, None if held/no clear direction
    data_source: str  # 'fresh' or 'stale'


def fetch_one(code: str) -> tuple[str, Optional[dict]]:
    """Fetch current snapshot for one ticker. Returns (code, snap_dict) — snap_dict has last_price or None."""
    try:
        snap = fetch_snapshot(code)
        return code, snap
    except Exception as e:
        return code, None


def main():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """
        SELECT code, score, operation_advice, data_snapshot_json
        FROM daily_report
        WHERE report_date = ?
        """,
        (REPORT_DATE,),
    ).fetchall()
    conn.close()
    print(f"Total tickers in 29/6 report: {len(rows)}")

    # Step 1: build list of (code, last_price_29_6, op, score, market)
    tasks = []
    for r in rows:
        try:
            snap = json.loads(r["data_snapshot_json"]) if r["data_snapshot_json"] else {}
        except json.JSONDecodeError:
            continue
        last = snap.get("last_price")
        if not last:
            continue
        market = "HK" if r["code"].endswith(".HK") else "US"
        tasks.append({
            "code": r["code"],
            "market": market,
            "op": r["operation_advice"],
            "score": r["score"],
            "last_at_report": float(last),
        })
    print(f"Tasks with valid 29/6 price: {len(tasks)}")

    # Step 2: fetch current prices (parallel)
    print(f"Fetching current prices for {len(tasks)} tickers (workers={MAX_WORKERS})...")
    t0 = time.time()
    code_to_snap = {}
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        futures = {ex.submit(fetch_one, t["code"]): t["code"] for t in tasks}
        for i, fut in enumerate(as_completed(futures), 1):
            code, snap = fut.result()
            code_to_snap[code] = snap
            if i % 50 == 0:
                print(f"  fetched {i}/{len(tasks)} ({time.time()-t0:.0f}s)", flush=True)
    print(f"Done fetching in {time.time()-t0:.0f}s")

    # Fallback for tickers that failed to fetch — try a 2nd time serially with longer timeout
    missing_codes = [t["code"] for t in tasks if not code_to_snap.get(t["code"]) or not code_to_snap[t["code"]].get("last_price")]
    if missing_codes:
        print(f"Re-fetching {len(missing_codes)} failed tickers serially...")
        for code in missing_codes:
            snap = fetch_one(code)[1]
            if snap and snap.get("last_price"):
                code_to_snap[code] = snap
                print(f"  {code}: recovered (last={snap.get('last_price')})", flush=True)

    # Step 3: compute per-ticker change + signal-correctness
    results: list[BacktestRow] = []
    fresh = 0
    stale = 0
    missing = 0
    for t in tasks:
        snap_now = code_to_snap.get(t["code"])
        if not snap_now or not snap_now.get("last_price"):
            missing += 1
            continue
        last_now = float(snap_now["last_price"])
        # data_as_of indicates freshness
        data_as_of = snap_now.get("data_as_of", "")
        # Heuristic: if data_as_of is empty, label as fresh (live). If it includes a date other than 30/6, stale.
        is_stale = False
        if data_as_of and "2026-06-30" not in data_as_of and "2026-06-29" not in data_as_of:
            is_stale = True
        # Actually just track whether data_as_of is present
        src = "stale" if (data_as_of and "2026-06" not in data_as_of) else "fresh"

        pct = (last_now - t["last_at_report"]) / t["last_at_report"] * 100
        op = t["op"]
        # Signal correctness: BUY benefits from up, SELL from down, HOLD = no clear call
        if op in ("買入", "buy"):
            signal_correct = pct > 0  # price went up = good for buy
        elif op in ("賣出", "sell", "賣出（反彈做空）"):
            signal_correct = pct < 0  # price went down = good for sell
        else:
            signal_correct = None  # HOLD no clear direction
        results.append(BacktestRow(
            code=t["code"],
            market=t["market"],
            op=op,
            score=t["score"],
            price_at_report=t["last_at_report"],
            price_now=last_now,
            pct_change=round(pct, 2),
            signal_correct=signal_correct,
            data_source=src,
        ))
        if src == "fresh":
            fresh += 1
        else:
            stale += 1

    # Stats
    print(f"\nResults: {len(results)} ({fresh} fresh / {stale} stale) / missing {missing}")
    print()

    by_op = {"買入": [], "觀望": [], "賣出": [], "other": []}
    for r in results:
        if r.op in by_op:
            by_op[r.op].append(r)
        else:
            by_op["other"].append(r)

    print("=" * 70)
    print(f" BACKTEST · 29/6 report vs 30/6 morning (HK) / pre-market (US)")
    print("=" * 70)
    for op, rs in by_op.items():
        if op in ("買入", "buy"): label = "🟢 買入"
        elif op in ("賣出", "sell"): label = "🔴 賣出"
        elif op == "觀望": label = "🟡 觀望"
        else: label = f"⚪ {op}"
        n = len(rs)
        if n == 0:
            print(f"{label}: 0\n")
            continue
        moves = [r.pct_change for r in rs]
        correct = [r for r in rs if r.signal_correct is True]
        wrong = [r for r in rs if r.signal_correct is False]
        hit_rate = len(correct) / n * 100 if n else 0
        print(f"{label}: n={n}  hit={len(correct)}/{n} ({hit_rate:.0f}%)  "
              f"avg_move={mean(moves):+.2f}%  median={median(moves):+.2f}%  "
              f"min={min(moves):+.2f}%  max={max(moves):+.2f}%")
        print()

    # Per-market breakdown
    print("--- HK ---")
    hk_buy = [r for r in results if r.market == "HK" and r.op in ("買入", "buy")]
    hk_sell = [r for r in results if r.market == "HK" and r.op in ("賣出", "sell", "賣出（反彈做空）")]
    hk_hold = [r for r in results if r.market == "HK" and r.op in ("觀望", "hold")]
    for label, rs in [("🟢 港股買入", hk_buy), ("🔴 港股賣出", hk_sell), ("🟡 港股觀望", hk_hold)]:
        if not rs: continue
        moves = [r.pct_change for r in rs]
        correct = [r for r in rs if r.signal_correct is True]
        hit = len(correct) / len(rs) * 100
        print(f"  {label}: n={len(rs)}  hit={hit:.0f}%  avg={mean(moves):+.2f}%  med={median(moves):+.2f}%")

    print()
    print("--- US ---")
    us_buy = [r for r in results if r.market == "US" and r.op in ("買入", "buy")]
    us_sell = [r for r in results if r.market == "US" and r.op in ("賣出", "sell", "賣出（反彈做空）")]
    us_hold = [r for r in results if r.market == "US" and r.op in ("觀望", "hold")]
    for label, rs in [("🟢 美股買入", us_buy), ("🔴 美股賣出", us_sell), ("🟡 美股觀望", us_hold)]:
        if not rs: continue
        moves = [r.pct_change for r in rs]
        correct = [r for r in rs if r.signal_correct is True]
        hit = len(correct) / len(rs) * 100
        print(f"  {label}: n={len(rs)}  hit={hit:.0f}%  avg={mean(moves):+.2f}%  med={median(moves):+.2f}%")

    # Save JSON
    out = "/tmp/backtest-29.json"
    with open(out, "w") as f:
        json.dump({
            "report_date": REPORT_DATE,
            "backtest_time": time.strftime("%Y-%m-%d %H:%M HKT"),
            "total_results": len(results),
            "fresh": fresh, "stale": stale, "missing": missing,
            "results": [asdict(r) for r in results],
        }, f, indent=2, ensure_ascii=False)
    print(f"\nSaved {out}")


if __name__ == "__main__":
    main()

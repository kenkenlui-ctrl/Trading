"""Hit rate analyzer for backtest results.
Reads backtest_results + yfinance historical prices to compute:
- 1D, 1W (5d), 1M (~22d) hit rate per signal type
- Per-ticker outcome table
- Save JSON + summary table
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta
from statistics import mean, median
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
import yfinance as yf

from src.data_fetcher import _hk_code_yfinance

DB = "/Users/kenken/Documents/dsa-hk/data/dsa_hk.db"
HORIZONS = {
    "1D": 1,
    "1W": 5,
    "1M": 22,
}


def get_price_at(code: str, target_date: str) -> Optional[float]:
    """Get yfinance closing price for ticker on/before target_date. None if no data."""
    yf_code = _hk_code_yfinance(code)
    try:
        t = yf.Ticker(yf_code)
        target = pd.Timestamp(target_date)
        hist = t.history(period="2y", auto_adjust=False)
        if hist is None or len(hist) == 0:
            return None
        # Make target tz-aware
        if hist.index.tz is not None:
            target = target.tz_localize(hist.index.tz)
        h = hist[hist.index <= target]
        if len(h) == 0:
            return None
        last = h["Close"].dropna()
        if len(last) == 0:
            return None
        return float(last.iloc[-1])
    except Exception:
        return None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--horizons", nargs="+", default=["1D", "1W", "1M"], choices=["1D", "1W", "1M"])
    parser.add_argument("--max-codes", type=int, default=None, help="Limit codes (for testing)")
    args = parser.parse_args()

    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("""
        SELECT code, as_of_date, operation_advice, last_price
        FROM backtest_results
        WHERE last_price IS NOT NULL
    """).fetchall()
    conn.close()

    if args.max_codes:
        # Pick a representative subset
        seen = set()
        filtered = []
        for r in rows:
            if r["code"] not in seen:
                seen.add(r["code"])
                filtered.append(r)
            if len(seen) >= args.max_codes:
                break
        rows = filtered

    print(f"Backtest rows: {len(rows)}")
    print(f"Codes: {len(set(r['code'] for r in rows))}")
    print(f"Dates: {sorted(set(r['as_of_date'] for r in rows))}")

    # Build (code, as_of_date, op, signal_price) list
    tasks = []
    for r in rows:
        op = r["operation_advice"]
        if op not in ("買入", "賣出", "觀望", "buy", "sell", "hold", "賣出（反彈做空）"):
            continue
        tasks.append((r["code"], r["as_of_date"], op, r["last_price"]))
    print(f"Tasks (after op filter): {len(tasks)}")

    # Cache: (code, target_date) -> price
    price_cache = {}

    def get_price_cached(code, target_date):
        key = (code, target_date)
        if key in price_cache:
            return price_cache[key]
        p = get_price_at(code, target_date)
        price_cache[key] = p
        time.sleep(0.1)  # gentle rate limit
        return p

    # For each (task, horizon), compute outcome
    outcomes = []  # (code, as_of_date, op, signal_price, target_date, target_price, pct_change, signal_correct, horizon)
    horizons_to_run = [h for h in args.horizons if h in HORIZONS]
    print(f"Horizons: {horizons_to_run}")

    fetch_jobs = []
    for code, as_of, op, sig_price in tasks:
        for h in horizons_to_run:
            d = date.fromisoformat(as_of) + timedelta(days=HORIZONS[h])
            # Skip weekends
            while d.weekday() >= 5:
                d += timedelta(days=1)
            fetch_jobs.append((code, as_of, op, sig_price, h, d.isoformat()))

    print(f"Total price fetches: {len(fetch_jobs)}")

    # Fetch prices with parallelism
    print("Fetching future prices...")
    t0 = time.time()
    fetched = 0
    with ThreadPoolExecutor(max_workers=8) as ex:
        futs = {ex.submit(get_price_cached, j[0], j[5]): j for j in fetch_jobs}
        for fut in as_completed(futs):
            target_price = fut.result()
            j = futs[fut]
            code, as_of, op, sig_price, horizon, target_date = j
            fetched += 1
            if target_price is None or sig_price is None or sig_price == 0:
                continue
            pct = (target_price - sig_price) / sig_price * 100
            if op in ("買入", "buy"):
                correct = pct > 0
            elif op in ("賣出", "sell", "賣出（反彈做空）"):
                correct = pct < 0
            else:
                correct = None
            outcomes.append({
                "code": code,
                "as_of_date": as_of,
                "op": op,
                "horizon": horizon,
                "sig_price": sig_price,
                "target_date": target_date,
                "target_price": target_price,
                "pct_change": round(pct, 2),
                "correct": correct,
            })
            if fetched % 200 == 0:
                print(f"  fetched {fetched}/{len(fetch_jobs)} ({time.time()-t0:.0f}s)", flush=True)
    print(f"Done fetching in {time.time()-t0:.0f}s. Outcomes: {len(outcomes)}")

    # Save raw outcomes
    with open("/tmp/backtest_outcomes.json", "w") as f:
        json.dump(outcomes, f, ensure_ascii=False, indent=2)

    # Stats
    print()
    print("=" * 80)
    print(f" BACKTEST HIT RATE ANALYSIS")
    print("=" * 80)
    for horizon in horizons_to_run:
        print(f"\n--- Horizon: {horizon} ---")
        h_outcomes = [o for o in outcomes if o["horizon"] == horizon]
        by_op = defaultdict(list)
        for o in h_outcomes:
            by_op[o["op"]].append(o)
        for op, ops_list in by_op.items():
            n = len(ops_list)
            if n == 0:
                continue
            correct = [o for o in ops_list if o["correct"]]
            hit = len(correct) / n * 100
            moves = [o["pct_change"] for o in ops_list]
            print(f"  {op:<14} n={n:>4}  hit={len(correct):>3}/{n:<3} ({hit:>5.1f}%)  "
                  f"avg={mean(moves):>+6.2f}%  med={median(moves):>+6.2f}%  "
                  f"min={min(moves):>+6.2f}%  max={max(moves):>+6.2f}%")
    print()


if __name__ == "__main__":
    main()

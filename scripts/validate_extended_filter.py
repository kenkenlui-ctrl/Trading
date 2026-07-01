"""Validate extended filter impact on backtest hit rate.
For each backtest_results row:
- Original LLM op_advice (signal as LLM intended)
- After rule nudge (current DB op_advice)
- After extended filter (proposed)
Compare hit rates of each version at 1D/1W horizons.
"""
from __future__ import annotations

import json
import os
import re
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
HORIZONS = {"1D": 1, "1W": 5}

# Buy-side caution patterns (same as extended_filter.py)
BUY_CAUTION_PATTERNS = [
    r"不宜追高", r"不宜現價追入", r"不宜追入", r"不宜高追",
    r"不建議追高", r"違反嚴進策略",
    r"偏離\s*MA20\s*達\s*\d+(?:\.\d+)?\s*%",
    r"偏離\s*MA20\s*超過",
    r"RSI.*?(?:超買|≥\s*70|接近超買)",
    r"短期超買", r"嚴重超買",
    r"短期累積可觀升幅", r"已大幅拉升",
    r"不宜追入違反",
    r"已逼近年?[內内]?[高頂]位",
    r"接近\s*52\s*週[高頂]位",
    r"距.*?52\s*週[高頂]位.*?[\d\.]*\s*%",
    r"技術上有回吐壓力", r"追高風險",
    r"嚴守止損", r"嚴進[策閥]略",
]

SELL_CAUTION_PATTERNS = [
    r"反彈.{0,5}(?:至|後|空間)",
    r"短期反彈", r"超賣",
    r"博反彈", r"反彈.*?上方",
]


def has_caution(body: str, patterns: list) -> Optional[str]:
    if not body:
        return None
    for p in patterns:
        if re.search(p, body):
            return p
    return None


def get_price_at(code: str, target_date: str) -> Optional[float]:
    yf_code = _hk_code_yfinance(code)
    try:
        t = yf.Ticker(yf_code)
        target = pd.Timestamp(target_date)
        hist = t.history(period="2y", auto_adjust=False)
        if hist is None or len(hist) == 0:
            return None
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
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("""
        SELECT code, as_of_date, operation_advice, summary_md, data_snapshot_json, last_price
        FROM backtest_results
        WHERE last_price IS NOT NULL
    """).fetchall()
    conn.close()
    print(f"Backtest rows: {len(rows)}")

    price_cache = {}
    def get_price_cached(code, target_date):
        key = (code, target_date)
        if key in price_cache:
            return price_cache[key]
        p = get_price_at(code, target_date)
        price_cache[key] = p
        time.sleep(0.05)
        return p

    # Pre-compute all price fetches needed
    fetch_jobs = []
    for r in rows:
        for h_name, h_days in HORIZONS.items():
            d = date.fromisoformat(r["as_of_date"]) + timedelta(days=h_days)
            while d.weekday() >= 5:
                d += timedelta(days=1)
            fetch_jobs.append((r["code"], r["as_of_date"], h_name, d.isoformat()))

    print(f"Fetching {len(fetch_jobs)} future prices...")
    t0 = time.time()
    fetched = 0
    with ThreadPoolExecutor(max_workers=8) as ex:
        futs = {ex.submit(get_price_cached, j[0], j[3]): j for j in fetch_jobs}
        for fut in as_completed(futs):
            fetched += 1
            if fetched % 200 == 0:
                print(f"  {fetched}/{len(fetch_jobs)} ({time.time()-t0:.0f}s)", flush=True)
    print(f"Done fetching in {time.time()-t0:.0f}s")

    # Compute 3 versions of op + hit rate per version per horizon
    versions = ["llm_original", "after_rule", "after_extended_filter"]
    stats = {v: {h: {"buy": [], "sell": [], "hold": []} for h in HORIZONS} for v in versions}

    for r in rows:
        code = r["code"]
        op_orig = r["operation_advice"]  # LLM original
        # No full_md column — use summary_md + data_snapshot's news + indicators
        snap = json.loads(r["data_snapshot_json"]) if r["data_snapshot_json"] else {}
        body = (r["summary_md"] or "")
        op_rule = op_orig  # After rule nudge (we don't have it post-nudge, treat as same since most won't be flipped in backtest_results which were stored before rule applied)

        # Extended filter version
        op_filtered = op_orig
        if op_orig in ("買入", "buy") and has_caution(body, BUY_CAUTION_PATTERNS):
            op_filtered = "觀望"
        elif op_orig in ("賣出", "sell", "賣出（反彈做空）") and has_caution(body, SELL_CAUTION_PATTERNS):
            op_filtered = "觀望"

        for h_name in HORIZONS:
            target_date = (date.fromisoformat(r["as_of_date"]) + timedelta(days=HORIZONS[h_name])).isoformat()
            while date.fromisoformat(target_date).weekday() >= 5:
                target_date = (date.fromisoformat(target_date) + timedelta(days=1)).isoformat()
            sig_price = r["last_price"]
            target_price = get_price_cached(code, target_date) if False else price_cache.get((code, target_date))
            if target_price is None or sig_price is None or sig_price == 0:
                continue
            pct = (target_price - sig_price) / sig_price * 100

            for v, op in [("llm_original", op_orig), ("after_extended_filter", op_filtered)]:
                if op in ("買入", "buy"):
                    correct = pct > 0
                    stats[v][h_name]["buy"].append((correct, pct))
                elif op in ("賣出", "sell", "賣出（反彈做空）"):
                    correct = pct < 0
                    stats[v][h_name]["sell"].append((correct, pct))
                else:
                    correct = None
                    stats[v][h_name]["hold"].append((None, pct))

    # Print comparison table
    print("\n" + "=" * 80)
    print(" BACKTEST COMPARISON: LLM original vs Extended Filter")
    print("=" * 80)
    for h_name in HORIZONS:
        print(f"\n--- Horizon: {h_name} ---")
        for v in ["llm_original", "after_extended_filter"]:
            print(f"\n  [{v}]")
            for op_type in ["buy", "sell"]:
                rs = stats[v][h_name][op_type]
                n = len(rs)
                if n == 0:
                    continue
                correct = [r for r in rs if r[0] is True]
                hit = len(correct) / n * 100
                avg = mean([r[1] for r in rs])
                med = median([r[1] for r in rs])
                op_label = "🟢 buy" if op_type == "buy" else "🔴 sell"
                print(f"    {op_label}: n={n:>4}  hit={len(correct):>3}/{n:<3} ({hit:>5.1f}%)  "
                      f"avg={avg:>+6.2f}%  med={med:>+6.2f}%")


if __name__ == "__main__":
    main()
"""6-day performance trace for HK+US signal validation.

For each signal in daily_report, fetch the NEXT day's close price
and check if price moved in predicted direction:
  買入 → hit if close_N+1 > close_N (price up)
  賣出 → hit if close_N+1 < close_N (price down)
  觀望 → check that price moved < 1% (stayed flat)

Outputs: per-date hit rate + per-direction summary + per-score-bucket breakdown.
"""
import json
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path

import yfinance as yf

DB_PATH = Path(__file__).parent.parent / "data" / "dsa_hk.db"
DATES = ["2026-06-26", "2026-06-27", "2026-06-29", "2026-06-30", "2026-07-01", "2026-07-02"]

# Map next-trading-day for each signal date
NEXT_TRADING = {
    "2026-06-26": "2026-06-29",  # Fri → Mon
    "2026-06-27": "2026-06-29",  # Sat → Mon
    "2026-06-29": "2026-06-30",  # Mon → Tue
    "2026-06-30": "2026-07-01",  # Tue → Wed
    "2026-07-01": "2026-07-02",  # Wed → Thu
    "2026-07-02": None,           # Thu → ? (no Fri data in yfinance)
}


def fetch_price_map(tickers: list[str], start: str, end: str) -> dict[str, dict[str, float]]:
    """For each ticker, fetch close prices in [start, end] range. Return {ticker: {date_str: close}}."""
    out: dict[str, dict[str, float]] = {}
    # yfinance wants ^ prefix for some tickers — handle HK tickers as 4-digit + .HK
    # yfinance uses dash for class shares (BRK-B)
    for tk in tickers:
        try:
            t = yf.Ticker(tk)
            hist = t.history(start=start, end=end, auto_adjust=False)
            if hist.empty:
                continue
            out[tk] = {
                d.strftime("%Y-%m-%d"): float(row["Close"])
                for d, row in hist.iterrows()
            }
        except Exception:
            pass
    return out


def main():
    con = sqlite3.connect(str(DB_PATH))
    cur = con.cursor()
    # Collect all unique US tickers + their dates (yfinance only works for US)
    rows = cur.execute(
        """SELECT code, report_date, score, operation_advice,
                  json_extract(score_breakdown_json, '$.momentum_score') as m_score
           FROM daily_report
           WHERE report_date IN ({}) AND code NOT LIKE '%.HK'""".format(
            ",".join("?" * len(DATES))
        ),
        DATES,
    ).fetchall()
    print(f"Total US signals: {len(rows)}")

    # Build price cache: fetch 2y history for all US tickers (single batch by date)
    tickers = sorted(set(r[0] for r in rows))
    print(f"Unique US tickers: {len(tickers)}")
    price_map = fetch_price_map(tickers, "2026-06-26", "2026-07-05")
    print(f"Got prices for {len(price_map)}/{len(tickers)} tickers")

    # Compute hit rate per (date, direction, score_bucket)
    hits: dict = {}  # key=(date, op, bucket) -> [n, hit, miss]
    misses_by_date: dict[str, list[tuple]] = {}
    for code, date, score, op, m_score in rows:
        next_d = NEXT_TRADING.get(date)
        if next_d is None:
            continue
        pm = price_map.get(code)
        if not pm or date not in pm or next_d not in pm:
            continue
        p0 = pm[date]
        p1 = pm[next_d]
        if p0 is None or p1 is None:
            continue
        chg = (p1 - p0) / p0 * 100
        # Hit determination
        hit = False
        if op == "買入":
            hit = chg > 0  # any positive move
        elif op == "賣出":
            hit = chg < 0  # any negative move
        elif op == "觀望":
            hit = abs(chg) < 1.5  # didn't move much

        # Score bucket
        if score >= 65:
            bucket = "score≥65"
        elif score >= 50:
            bucket = "score 50-64"
        elif score >= 35:
            bucket = "score 35-49"
        else:
            bucket = "score<35"

        key = (date, op, bucket)
        if key not in hits:
            hits[key] = [0, 0]
        hits[key][0] += 1
        if hit:
            hits[key][1] += 1

    # Print per-date summary
    print("\n=== Per-date hit rate (1D, US only) ===")
    print(f"{'date':<12} {'op':<6} {'bucket':<14} {'n':>5} {'hit':>5} {'rate':>7} {'avg_chg':>8}")
    by_date_op: dict[tuple[str, str], tuple[int, int, list[float]]] = {}
    for (date, op, bucket), (n, hit) in sorted(hits.items()):
        rate = hit / n * 100
        print(f"{date:<12} {op:<6} {bucket:<14} {n:>5} {hit:>5} {rate:>6.1f}%")

    # Per-date-op summary (collapsed)
    print("\n=== Per-date-op summary (all score buckets combined) ===")
    print(f"{'date':<12} {'op':<6} {'n':>5} {'hit':>5} {'rate':>7} {'avg_chg':>8}")
    summary: dict[tuple[str, str], list] = {}
    for (date, op, _bucket), (n, hit) in sorted(hits.items()):
        key = (date, op)
        if key not in summary:
            summary[key] = [0, 0, []]
        s = summary[key]
        s[0] += n
        s[1] += hit

    for code, date, score, op, m_score in rows:
        next_d = NEXT_TRADING.get(date)
        if next_d is None:
            continue
        pm = price_map.get(code)
        if not pm or date not in pm or next_d not in pm:
            continue
        p0 = pm[date]
        p1 = pm[next_d]
        chg = (p1 - p0) / p0 * 100
        key = (date, op)
        if key in summary:
            summary[key][2].append(chg)

    for (date, op), lst in sorted(summary.items()):
        n, hit, chgs = lst
        rate = hit / n * 100
        avg = sum(chgs) / len(chgs) if chgs else 0
        print(f"{date:<12} {op:<6} {n:>5} {hit:>5} {rate:>6.1f}% {avg:>+7.2f}%")

    # Grand summary
    print("\n=== 6-day TOTAL hit rate by direction (US only) ===")
    grand: dict[str, list] = {}
    for code, date, score, op, m_score in rows:
        next_d = NEXT_TRADING.get(date)
        if next_d is None:
            continue
        pm = price_map.get(code)
        if not pm or date not in pm or next_d not in pm:
            continue
        p0 = pm[date]; p1 = pm[next_d]
        chg = (p1 - p0) / p0 * 100
        if op not in grand:
            grand[op] = [0, 0, []]
        g = grand[op]
        g[0] += 1
        hit = (chg > 0) if op == "買入" else (chg < 0) if op == "賣出" else (abs(chg) < 1.5)
        if hit:
            g[1] += 1
        g[2].append(chg)
    print(f"{'op':<8} {'n':>5} {'hit':>5} {'rate':>7} {'avg_chg':>8} {'median_chg':>10}")
    for op, lst in sorted(grand.items()):
        n, hit, chgs = lst
        rate = hit / n * 100
        avg = sum(chgs) / len(chgs) if chgs else 0
        chgs_sorted = sorted(chgs)
        med = chgs_sorted[len(chgs_sorted) // 2] if chgs_sorted else 0
        print(f"{op:<8} {n:>5} {hit:>5} {rate:>6.1f}% {avg:>+7.2f}% {med:>+9.2f}%")


def chg_for(rows, price_map, code, date, next_d):
    pm = price_map.get(code)
    if not pm or date not in pm or not next_d or next_d not in pm:
        return None
    p0, p1 = pm[date], pm[next_d]
    if not p0:
        return None
    return (p1 - p0) / p0 * 100


if __name__ == "__main__":
    main()
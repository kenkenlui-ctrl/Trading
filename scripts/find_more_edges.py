"""Additional edge-discovery analyses for the 6-day signal trace."""
import json
import re
import sqlite3
from pathlib import Path

import yfinance as yf

DB_PATH = Path(__file__).parent.parent / "data" / "dsa_hk.db"
DATES = ["2026-06-26", "2026-06-27", "2026-06-29", "2026-06-30", "2026-07-01", "2026-07-02"]

# Extended window: for holding-period sweep, fetch more days
def fetch_extended_prices(tickers: list[str], start: str, end: str) -> dict[str, dict[str, float]]:
    out = {}
    for tk in tickers:
        try:
            t = yf.Ticker(tk)
            hist = t.history(start=start, end=end, auto_adjust=False)
            if hist.empty:
                continue
            out[tk] = {d.strftime("%Y-%m-%d"): float(row["Close"]) for d, row in hist.iterrows()}
        except Exception:
            pass
    return out


def trading_days_between(start: str, end: str, all_dates: list[str]) -> list[str]:
    """Return trading dates strictly between start and end (exclusive of both)."""
    if start not in all_dates or end not in all_dates:
        return []
    si = all_dates.index(start)
    ei = all_dates.index(end)
    return all_dates[si + 1 : ei + 1]  # inclusive of end


DATES_FULL = ["2026-06-26", "2026-06-27", "2026-06-29", "2026-06-30", "2026-07-01", "2026-07-02"]


def main():
    con = sqlite3.connect(str(DB_PATH))
    cur = con.cursor()
    rows = cur.execute(
        f"""SELECT code, report_date, score, operation_advice,
                  json_extract(data_snapshot_json, '$.sector') as sector,
                  json_extract(data_snapshot_json, '$.change_pct') as day_chg,
                  json_extract(data_snapshot_json, '$.turnover') as turnover
            FROM daily_report
            WHERE report_date IN ({",".join("?" * len(DATES))})
              AND code NOT LIKE '%.HK'""",
        DATES,
    ).fetchall()
    print(f"Total US signals: {len(rows)}")

    tickers = sorted(set(r[0] for r in rows))
    print(f"Fetching extended prices for {len(tickers)} tickers...")
    # Fetch wider window so we can compute 1D, 2D, 3D forward
    price_map = fetch_extended_prices(tickers, "2026-06-25", "2026-07-05")
    print(f"Got prices for {len(price_map)}/{len(tickers)}\n")

    # Build base dataset
    data = []
    for code, date, score, op, sector, day_chg, turnover in rows:
        pm = price_map.get(code)
        if not pm:
            continue
        # Compute 1D, 2D, 3D forward returns
        rets = {}
        future_dates = [d for d in DATES_FULL if DATES_FULL.index(d) > DATES_FULL.index(date)] if date in DATES_FULL else []
        for offset, fd in enumerate(future_dates[:3], 1):
            if date in pm and fd in pm and pm[date]:
                rets[f"fwd_{offset}d"] = (pm[fd] - pm[date]) / pm[date] * 100
        if not rets:
            continue
        data.append({
            "code": code, "date": date, "op": op, "score": score,
            "sector": sector or "?", "day_chg": day_chg or 0, "turnover": turnover or 0,
            "rets": rets,
        })

    print(f"Dataset: {len(data)} with future returns\n")

    # === 1) Avg Win vs Avg Loss for each direction ===
    print("=== Avg Win vs Avg Loss (1D forward) ===")
    for op in ["買入", "賣出"]:
        winning = [d for d in data if d["op"] == op and d["rets"].get("fwd_1d") is not None and d["rets"]["fwd_1d"] > 0]
        losing = [d for d in data if d["op"] == op and d["rets"].get("fwd_1d") is not None and d["rets"]["fwd_1d"] <= 0]
        if winning and losing:
            avg_win = sum(d["rets"]["fwd_1d"] for d in winning) / len(winning)
            avg_loss = sum(d["rets"]["fwd_1d"] for d in losing) / len(losing)
            wr = len(winning) / (len(winning) + len(losing))
            # Expected value per trade
            ev = wr * avg_win + (1 - wr) * avg_loss
            print(f"  {op}: WR={wr*100:.1f}% ({len(winning)}W/{len(losing)}L)")
            print(f"     avg_win=+{avg_win:.2f}%  avg_loss={avg_loss:+.2f}%  EV/trade={ev:+.2f}%")
            if avg_loss != 0:
                print(f"     win/loss ratio = {abs(avg_win/avg_loss):.2f}x")
            print()

    # === 2) Holding period sweep ===
    print("=== Holding Period Sweep (BUY only) ===")
    print(f"{'period':<8} {'n':>5} {'avg_ret':>8} {'median':>8} {'%positive':>10}")
    for offset in [1, 2, 3]:
        key = f"fwd_{offset}d"
        rets = [d["rets"][key] for d in data if d["op"] == "買入" and key in d["rets"]]
        if not rets:
            continue
        avg = sum(rets) / len(rets)
        med = sorted(rets)[len(rets) // 2]
        pos = sum(1 for r in rets if r > 0) / len(rets) * 100
        print(f"{offset}D{'':<5} {len(rets):>5} {avg:>+7.2f}% {med:>+7.2f}% {pos:>9.1f}%")

    print("\n=== Holding Period Sweep (SELL only, want NEGATIVE) ===")
    print(f"{'period':<8} {'n':>5} {'avg_ret':>8} {'median':>8} {'%negative':>10}")
    for offset in [1, 2, 3]:
        key = f"fwd_{offset}d"
        rets = [d["rets"][key] for d in data if d["op"] == "賣出" and key in d["rets"]]
        if not rets:
            continue
        avg = sum(rets) / len(rets)
        med = sorted(rets)[len(rets) // 2]
        neg = sum(1 for r in rets if r < 0) / len(rets) * 100
        print(f"{offset}D{'':<5} {len(rets):>5} {avg:>+7.2f}% {med:>+7.2f}% {neg:>9.1f}%")

    # === 3) Sector breakdown ===
    print("\n=== Sector Performance (BUY only, 1D) ===")
    sector_data = {}
    for d in data:
        if d["op"] != "買入" or "fwd_1d" not in d["rets"]:
            continue
        sec = d["sector"]
        if sec not in sector_data:
            sector_data[sec] = []
        sector_data[sec].append(d["rets"]["fwd_1d"])
    print(f"{'sector':<30} {'n':>5} {'avg_ret':>9} {'%pos':>7}")
    for sec in sorted(sector_data.keys(), key=lambda s: -sum(sector_data[s])/len(sector_data[s]) if sector_data[s] else 0):
        rets = sector_data[sec]
        if len(rets) < 5:
            continue
        avg = sum(rets) / len(rets)
        pos = sum(1 for r in rets if r > 0) / len(rets) * 100
        print(f"{sec[:30]:<30} {len(rets):>5} {avg:>+8.2f}% {pos:>6.1f}%")

    # === 4) Day-change momentum filter ===
    print("\n=== Day-1 Change Filter (BUY only) ===")
    print("What if we only BUY stocks that DROPPED yesterday (mean-reversion)?")
    print(f"{'prior_chg':<14} {'n':>5} {'avg_ret':>9} {'%pos':>7}")
    buckets = [(-999, -3), (-3, -1), (-1, 0), (0, 1), (1, 3), (3, 999)]
    for lo, hi in buckets:
        rets = [d["rets"]["fwd_1d"] for d in data
                if d["op"] == "買入" and "fwd_1d" in d["rets"]
                and lo <= d["day_chg"] < hi]
        if len(rets) < 5:
            continue
        avg = sum(rets) / len(rets)
        pos = sum(1 for r in rets if r > 0) / len(rets) * 100
        print(f"{lo:>+4}% to {hi:>+4}%{'':<3} {len(rets):>5} {avg:>+8.2f}% {pos:>6.1f}%")

    # === 5) Stock blacklist — always fails ===
    print("\n=== Worst-Performing Tickers (BUY, n>=2 signals) ===")
    tk_data = {}
    for d in data:
        if d["op"] != "買入" or "fwd_1d" not in d["rets"]:
            continue
        if d["code"] not in tk_data:
            tk_data[d["code"]] = []
        tk_data[d["code"]].append(d["rets"]["fwd_1d"])
    worst = []
    for tk, rets in tk_data.items():
        if len(rets) < 2:
            continue
        avg = sum(rets) / len(rets)
        worst.append((tk, avg, len(rets)))
    worst.sort(key=lambda x: x[1])
    print(f"{'ticker':<10} {'avg_ret':>9} {'n':>3}")
    for tk, avg, n in worst[:10]:
        print(f"{tk:<10} {avg:>+8.2f}% {n:>3}")

    print("\n=== Best-Performing Tickers (BUY, n>=2 signals) ===")
    best = sorted(worst, key=lambda x: -x[1])
    print(f"{'ticker':<10} {'avg_ret':>9} {'n':>3}")
    for tk, avg, n in best[:10]:
        print(f"{tk:<10} {avg:>+8.2f}% {n:>3}")

    # === 6) Multi-day confirmation — BUY appears 2+ days in a row ===
    print("\n=== Multi-Day Confirmation (BUY on 2+ consecutive days) ===")
    # Group by ticker, find tickers that had BUY on consecutive days
    by_tk = {}
    for d in data:
        if d["op"] != "買入":
            continue
        if d["code"] not in by_tk:
            by_tk[d["code"]] = []
        by_tk[d["code"]].append(d["date"])
    multi_day = []
    for tk, dates in by_tk.items():
        dates_sorted = sorted(dates)
        for i in range(len(dates_sorted) - 1):
            if DATES_FULL.index(dates_sorted[i+1]) == DATES_FULL.index(dates_sorted[i]) + 1:
                # consecutive, get next-day return from first occurrence
                first_date = dates_sorted[i]
                for d in data:
                    if d["code"] == tk and d["date"] == first_date and "fwd_1d" in d["rets"]:
                        multi_day.append((tk, d["rets"]["fwd_1d"]))
                        break
    if multi_day:
        avg = sum(r for _, r in multi_day) / len(multi_day)
        pos = sum(1 for _, r in multi_day if r > 0) / len(multi_day) * 100
        print(f"  n={len(multi_day)}, avg_1d_return={avg:+.2f}%, %positive={pos:.1f}%")
    else:
        print("  No multi-day BUY signals found")

    # === 7) Index-relative performance ===
    # Compare BUY avg to SPY same-day return
    print("\n=== vs SPY benchmark (1D forward, same dates) ===")
    spy = price_map.get("SPY", {})
    spy_returns = {}
    for date in DATES_FULL:
        future = [d for d in DATES_FULL if DATES_FULL.index(d) > DATES_FULL.index(date)]
        if future and date in spy and future[0] in spy and spy[date]:
            spy_returns[date] = (spy[future[0]] - spy[date]) / spy[date] * 100
    print(f"  SPY 1D returns by date: {spy_returns}")
    for op in ["買入", "賣出"]:
        signal_avg = {}
        for d in data:
            if d["op"] != op or "fwd_1d" not in d["rets"]:
                continue
            if d["date"] not in signal_avg:
                signal_avg[d["date"]] = []
            signal_avg[d["date"]].append(d["rets"]["fwd_1d"])
        print(f"\n  {op} by date:")
        for date in sorted(signal_avg.keys()):
            if date in spy_returns:
                avg = sum(signal_avg[date]) / len(signal_avg[date])
                spy = spy_returns[date]
                beat = "✓ beats SPY" if (op == "買入" and avg > spy) or (op == "賣出" and avg < spy) else "✗ underperforms"
                print(f"    {date}: signal_avg={avg:+.2f}%  SPY={spy:+.2f}%  {beat}  (n={len(signal_avg[date])})")


if __name__ == "__main__":
    main()
"""Compute 1D forward returns for past signals (manual command, no cron).

For signals generated on date D, fetches the next trading day's close
via Futu (HK) + yfinance batch (US), computes forward return, saves
to backtest_results table.

This continuously grows the calibration dataset so the 下日勝率 score
gets more accurate over time.

Usage (user runs in the morning, no cron):
    # Compute forward returns for the latest signal date in DB
    python3 scripts/compute_forward_returns.py

    # Compute forward returns for a specific date
    python3 scripts/compute_forward_returns.py --date 2026-07-10

    # Process multiple date pairs
    python3 scripts/compute_forward_returns.py --date 2026-07-10 --date 2026-07-09

Workflow (per user 2026-07-11):
    1. Morning of 7/11 → run `refresh_daily.py --date 2026-07-10` to generate
       7/10's report (using 7/10's HK close + 7/10's US close which is 7/11 4am HKT)
    2. After that → run `compute_forward_returns.py` (defaults to latest
       signal date = 7/10, computes 7/10 → next trading day = 7/13)
"""
import sys, os, sqlite3, json, time, argparse
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from src.db import get_db


def ensure_backtest_table():
    conn = get_db()
    try:
        # Phase 4 (2026-07-11): rename old backtest_results table to keep
        # the old per-as-of-date data, and create new per-signal_date schema.
        cur = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='backtest_results'")
        if cur.fetchone():
            cur = conn.execute("PRAGMA table_info(backtest_results)")
            cols = {row[1] for row in cur.fetchall()}
            if 'signal_date' not in cols:
                # Old schema — rename to backtest_results_legacy
                conn.execute("ALTER TABLE backtest_results RENAME TO backtest_results_legacy")
                conn.commit()
                print("  Renamed old backtest_results → backtest_results_legacy")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS backtest_results (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                signal_date TEXT NOT NULL,
                code TEXT NOT NULL,
                operation_advice TEXT,
                signal_score INTEGER,
                matched_rule TEXT,
                entry_price REAL,
                exit_price REAL,
                forward_return_pct REAL,
                win INTEGER,
                computed_at TEXT,
                UNIQUE(signal_date, code)
            )
        """)
        conn.commit()
        try:
            conn.execute("CREATE INDEX IF NOT EXISTS idx_backtest_date ON backtest_results(signal_date DESC)")
        except sqlite3.OperationalError:
            pass
        try:
            conn.execute("CREATE INDEX IF NOT EXISTS idx_backtest_code ON backtest_results(code, signal_date DESC)")
        except sqlite3.OperationalError:
            pass
        conn.commit()
    finally:
        conn.close()


def get_signal_date(d_forward: str) -> str | None:
    """Find the trading date immediately before d_forward (in daily_report)."""
    conn = get_db()
    try:
        cur = conn.execute(
            "SELECT DISTINCT report_date FROM daily_report WHERE report_date < ? ORDER BY report_date DESC LIMIT 1",
            (d_forward,),
        )
        row = cur.fetchone()
        return row[0] if row else None
    finally:
        conn.close()


def fetch_closes_futu(d: str, hk_tickers: list) -> dict:
    """Fetch close prices for HK tickers on date d using Futu kline API."""
    prices = {}
    try:
        import futu as ft
        ctx = ft.OpenQuoteContext(host="127.0.0.1", port=11111)
        for hk in hk_tickers:
            hk_code = f"HK.{hk.split('.')[0]}"
            try:
                ret, data, _ = ctx.request_history_kline(
                    hk_code, start=d, end=d, ktype="K_DAY"
                )
                if ret == 0 and len(data) >= 1:
                    prices[hk] = float(data.iloc[0]["close"])
            except Exception as e:
                print(f"  futu err {hk}: {e}", file=sys.stderr)
            time.sleep(0.05)  # be nice to Futu
        ctx.close()
    except Exception as e:
        print(f"  futu ctx err: {e}", file=sys.stderr)
    return prices


def fetch_closes_yf(d: str, us_tickers: list) -> dict:
    """Fetch close prices for US tickers on date d using yfinance batched."""
    prices = {}
    if not us_tickers:
        return prices
    try:
        import yfinance as yf
        # Batched download
        end_d = (datetime.strptime(d, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")
        data = yf.download(us_tickers, start=d, end=end_d, progress=False, auto_adjust=True)
        if data is None or data.empty:
            return prices
        if len(us_tickers) == 1:
            close = data["Close"]
            prices[us_tickers[0]] = float(close.iloc[0]) if not close.empty else None
        else:
            close = data["Close"]
            for t in us_tickers:
                if t in close.columns and not close[t].empty:
                    v = close[t].iloc[0]
                    if v == v:  # not NaN
                        prices[t] = float(v)
    except Exception as e:
        print(f"  yf err: {e}", file=sys.stderr)
    return prices


def compute_for_date(d_signal: str, d_forward: str) -> int:
    """Compute forward returns for all signals on d_signal using d_forward closes."""
    conn = get_db()
    try:
        cur = conn.execute(
            "SELECT code, operation_advice, signal_score, decision_reason, data_snapshot_json FROM daily_report WHERE report_date=?",
            (d_signal,),
        )
        rows = cur.fetchall()
        if not rows:
            print(f"No signals for {d_signal}")
            return 0

        # Entry prices
        entries = {}
        tickers = []
        for code, op, ss, reason, ds_json in rows:
            tickers.append(code)
            try:
                ds = json.loads(ds_json)
                entries[code] = ds.get("last_price")
            except Exception:
                entries[code] = None

        hk = [t for t in tickers if ".HK" in t]
        us = [t for t in tickers if ".HK" not in t]
        print(f"  {d_signal} → {d_forward}: {len(hk)} HK, {len(us)} US tickers")

        # Fetch closes
        exits = {}
        if hk:
            print(f"  Fetching {len(hk)} HK closes from Futu...")
            exits.update(fetch_closes_futu(d_forward, hk))
        if us:
            print(f"  Fetching {len(us)} US closes from yfinance...")
            exits.update(fetch_closes_yf(d_forward, us))
        print(f"  Got {len(exits)} closes")

        # Save
        import re
        saved = 0
        for code, op, ss, reason, _ in rows:
            entry = entries.get(code)
            exit_ = exits.get(code)
            if not entry or not exit_:
                continue
            ret_pct = (exit_ - entry) / entry * 100
            win = 1 if ret_pct > 0 else 0
            rule_match = re.match(r"\[(\w+(?:-\w+)*)\]", reason or "")
            rule = rule_match.group(1) if rule_match else ""

            try:
                conn.execute("""
                    INSERT OR REPLACE INTO backtest_results
                    (signal_date, code, operation_advice, signal_score, matched_rule,
                     entry_price, exit_price, forward_return_pct, win, computed_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    d_signal, code, op, ss, rule,
                    entry, exit_, ret_pct, win,
                    datetime.now().isoformat(timespec="seconds"),
                ))
                saved += 1
            except Exception as e:
                print(f"  save err {code}: {e}", file=sys.stderr)
        conn.commit()

        wins = sum(1 for code, op, ss, reason, _ in rows
                   if entries.get(code) and exits.get(code) and (exits.get(code) - entries.get(code)) > 0)
        print(f"  ✅ {d_signal}→{d_forward}: saved {saved}/{len(rows)} (WR: {wins/saved*100:.1f}%)" if saved else "")
        return saved
    finally:
        conn.close()


def main():
    """Process date pairs (signal_date → d_forward) per CLI args."""
    parser = argparse.ArgumentParser(description="Compute 1D forward returns for past signals (manual)")
    parser.add_argument("--date", action="append", help="Signal date (YYYY-MM-DD). Can repeat. If not given, uses latest signal date in DB.")
    args = parser.parse_args()

    ensure_backtest_table()

    # Resolve dates
    if args.date:
        dates = args.date
    else:
        # Default: latest signal date in daily_report
        conn = get_db()
        cur = conn.execute("SELECT MAX(report_date) FROM daily_report")
        latest = cur.fetchone()[0]
        conn.close()
        if not latest:
            print("No dates in DB. Run refresh_daily.py first.")
            return
        dates = [latest]

    print(f"=== {datetime.now().isoformat()} ===")
    total = 0
    for d_signal in dates:
        d = datetime.strptime(d_signal, "%Y-%m-%d")
        d_forward_dt = d + timedelta(days=1)
        while d_forward_dt.weekday() >= 5:  # Sat/Sun
            d_forward_dt += timedelta(days=1)
        d_forward = d_forward_dt.strftime("%Y-%m-%d")
        print(f"\n→ signal={d_signal}, forward={d_forward}")
        n = compute_for_date(d_signal, d_forward)
        total += n
    print(f"\n✅ Total: {total} forward returns updated")


if __name__ == "__main__":
    main()

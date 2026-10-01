"""fetch_jp_charts.py — Download JP200 OHLC from yfinance, run 10-step framework, render charts.

For each JP ticker (e.g. 7203.T Toyota):
1. yfinance.history(period=1y) for OHLC
2. daily_sr.run() to compute phase, S/R ladder, action plan
3. render_chart() to generate PNG
4. Save to /Users/kenken/dev/dsa-hk/charts/jp200/{TICKER}.png
5. Save JSON snapshot to data/jp200/{TICKER}.json

Usage:
  python3 fetch_jp_charts.py
  python3 fetch_jp_charts.py --top 5   # quick test
"""
from __future__ import annotations
import json
import sys
import time
from datetime import date, timedelta
from pathlib import Path

REPO = Path("/Users/kenken/dev/dsa-hk")
JP_UNIVERSE = REPO / "jp_universe_200.json"
JP_CHART_DIR = Path("/Users/kenken/dev/dsa-hk/charts/jp200")
JP_JSON_DIR = REPO / "data" / "jp200"

# Add daily_sr.py to path
sys.path.insert(0, str(Path("/Users/kenken/.minimax/skills/daily-sr-chart/scripts")))
import daily_sr  # type: ignore
import yfinance as yf  # type: ignore
import pandas as pd  # type: ignore


def fetch_jp_ohlc(ticker: str, days: int = 200) -> pd.DataFrame | None:
    """Fetch OHLC from yfinance, return JST-trading-day-indexed df."""
    try:
        # yfinance uses Yahoo format: 7203.T → ok
        df = yf.Ticker(ticker).history(period=f"{days + 30}d", auto_adjust=False)
        if df is None or len(df) < 50:
            return None
        # Keep OHLC + Volume
        df = df[["Open", "High", "Low", "Close", "Volume"]].copy()
        df.columns = ["open", "high", "low", "close", "volume"]
        df.index = pd.to_datetime(df.index).tz_localize(None)
        # JST = UTC+9, but yfinance already returns JST-naive dates. Just take last `days` rows.
        df = df.tail(days)
        return df
    except Exception as e:
        print(f"  {ticker} fetch err: {e}")
        return None


def process_one(code: str) -> tuple[str, bool]:
    """Process one JP ticker: fetch OHLC, run analysis, render chart, save JSON."""
    try:
        df = fetch_jp_ohlc(code)
        if df is None or len(df) < 60:
            return (code, False)

        # 7203.T → save as 7203_T.png (T → _ for filename safety)
        safe = code.replace(".", "_")
        out_png = JP_CHART_DIR / f"{safe}.png"
        out_png.parent.mkdir(parents=True, exist_ok=True)
        out_json = JP_JSON_DIR / f"{safe}.json"
        out_json.parent.mkdir(parents=True, exist_ok=True)

        # Use daily_sr.run() to do fetch + analysis + chart render
        # run() returns the JSON snapshot — save it directly
        try:
            snapshot = daily_sr.run(code, str(out_png), days=200, source="yfinance")
        except Exception as e:
            print(f"  {code} run err: {e}")
            return (code, False)

        # Build JP JSON from snapshot
        if not snapshot:
            return (code, True)  # chart saved
        df = fetch_jp_ohlc(code)
        if df is None or len(df) == 0:
            df = None
        close = float(df["close"].iloc[-1]) if df is not None and len(df) else 0.0
        chg_pct = (float(df["close"].pct_change().iloc[-1] * 100) if df is not None and len(df) > 1 else 0)
        snap_out = {
            "ticker": code,
            "asof": snapshot.get("asof", ""),
            "close": close,
            "chg_pct": chg_pct,
            "phase": snapshot.get("phase"),
            "kline": snapshot.get("kline", {}),
            "position": snapshot.get("position", {}),
            "supports": snapshot.get("supports", []),
            "resistances": snapshot.get("resistances", []),
            "action_plan": snapshot.get("action_plan", {}),
        }
        out_json.write_text(json.dumps(snap_out, ensure_ascii=False, indent=2), encoding="utf-8")
        return (code, True)
    except Exception as e:
        print(f"  {code} unexpected err: {e}")
        return (code, False)


def main():
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--top", type=int, help="Only process top N tickers (test mode)")
    args = p.parse_args()

    tickers = json.load(open(JP_UNIVERSE, encoding="utf-8"))
    if args.top:
        tickers = tickers[: args.top]
    print(f"Processing {len(tickers)} JP tickers")

    success = 0
    failed = 0
    for i, code in enumerate(tickers, 1):
        print(f"[{i}/{len(tickers)}] {code} ...", flush=True)
        ticker, ok = process_one(code)
        if ok:
            success += 1
        else:
            failed += 1
        time.sleep(0.2)  # yfinance rate limit
    print(f"\nDone. success={success} failed={failed}")


if __name__ == "__main__":
    main()

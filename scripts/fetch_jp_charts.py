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
JP_NAMES = REPO / "data" / "jp_names.json"

_JP_NAME_CACHE: dict | None = None


def _jp_name(code: str) -> str:
    """Company name from the cached data/jp_names.json (built by
    backfill_jp_schema.py via yfinance). JP snapshots had no name field at
    all, so the Name column was blank on all 200 rows."""
    global _JP_NAME_CACHE
    if _JP_NAME_CACHE is None:
        try:
            _JP_NAME_CACHE = json.loads(JP_NAMES.read_text(encoding="utf-8"))
        except Exception:
            _JP_NAME_CACHE = {}
    return _JP_NAME_CACHE.get(code, "")

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
        # 2026-10-05: 400 bars, not the 200 used for the analysis run.
        # build_v2_signals.py rejects any ohlc series with < 260 rows
        # (`if not isinstance(rows, list) or len(rows) < 260`), so writing the
        # 200-bar frame silently took JP v2 signals from 91 to 0 — the file
        # looked fine, the page just went empty. The historical series on this
        # project has always been 400 bars; keep it that way.
        df = fetch_jp_ohlc(code, days=400)
        if df is None or len(df) == 0:
            df = None
        close = float(df["close"].iloc[-1]) if df is not None and len(df) else 0.0
        chg_pct = (float(df["close"].pct_change().iloc[-1] * 100) if df is not None and len(df) > 1 else 0)
        # 2026-10-05: stamp the real session from the data instead of echoing
        # daily_sr's empty snapshot.asof. build_dashboard.data_asof() and the
        # published chart both read this, so an empty string here meant the JP
        # T-1 label silently fell back to a calendar guess.
        data_asof = ""
        if df is not None and len(df):
            data_asof = pd.Timestamp(df.index[-1]).strftime("%Y-%m-%d")
        # 2026-10-05: also write the OHLC series. build_dashboard copies
        # <market>/<TICKER>_ohlc.json to public/<market>/ohlc/ for the
        # interactive chart and for build_v2_signals.py. Nothing here wrote it,
        # so JP charts kept serving a months-old series (last bar 2026-10-01)
        # while the table showed current closes — same class of date drift as
        # the hero-meta bug, one level deeper.
        if df is not None and len(df):
            bars = [
                {
                    "open": float(r["open"]),
                    "high": float(r["high"]),
                    "low": float(r["low"]),
                    "close": float(r["close"]),
                    "volume": float(r["volume"]) if "volume" in r else 0.0,
                    "date": pd.Timestamp(idx).strftime("%Y-%m-%d"),
                }
                for idx, r in df.iterrows()
            ]
            (JP_JSON_DIR / f"{safe}_ohlc.json").write_text(
                json.dumps(bars, ensure_ascii=False), encoding="utf-8"
            )
        # 2026-10-10: the name cache is keyed by ticker, so any universe regen that
    # swaps names in leaves the newcomers with no entry, and _jp_name() returns
    # "" — /jp200/ then rendered a ticker with a blank company name and
    # self_test's fabricated-price guard caught it. A lookup that cannot find a
    # value must not blank one we already published, so fall back to the
    # previous snapshot before giving up.
    _prev_name = ""
    _pj = JP_JSON_DIR / f"{safe}.json"
    if _pj.exists():
        try:
            _prev_name = (json.loads(_pj.read_text(encoding="utf-8")) or {}).get("name") or ""
        except Exception:
            _prev_name = ""

    snap_out = {
            "ticker": code,
            "asof": data_asof or snapshot.get("asof", ""),
            "close": close,
            "chg_pct": chg_pct,
            # 2026-10-05: emit the HK/US `last_bar` shape too. JP was the only
            # market written flat, and every reader (build_dashboard, build_home,
            # build_jp_index_minimal, build_insights_radar) indexes last_bar.C
            # with a 0 default — so /jp200/ rendered 200/200 rows as Last 0.00 /
            # Chg% +0.00% with no error anywhere. flat fields are kept for
            # backwards compatibility; last_bar is now the canonical one.
            "last_bar": (
                {
                    "date": data_asof,
                    "O": float(df["open"].iloc[-1]),
                    "H": float(df["high"].iloc[-1]),
                    "L": float(df["low"].iloc[-1]),
                    "C": close,
                    "prev_close": float(df["close"].iloc[-2]) if len(df) > 1 else None,
                    "chg_pct": chg_pct,
                }
                if df is not None and len(df)
                else None
            ),
            "name": _jp_name(code) or _prev_name,
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

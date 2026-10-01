"""update_to_8sep.py — Quick update of last_bar to Sept 7 + 8 closes.

For each ticker in HK/US/JP, fetch recent OHLC from yfinance, update
last_bar in source JSON to Sept 8 close (or latest available).

Avoids full re-run of daily-sr-chart (slow). Just updates price + date.
"""
from __future__ import annotations
import json
import sys
import time
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

import yfinance as yf
import pandas as pd

HK_OUT = Path("/Users/kenken/dev/dsa-hk/charts/hk200")
US_OUT = Path("/Users/kenken/dev/dsa-hk/charts/us200")
JP_OUT = Path("/Users/kenken/dev/dsa-hk/data/jp200")
OHLC_OUT = {
    "HK": Path("/Users/kenken/dev/dsa-hk/public/hk200/ohlc"),
    "US": Path("/Users/kenken/dev/dsa-hk/public/us200/ohlc"),
    "JP": Path("/Users/kenken/dev/dsa-hk/public/jp200/ohlc"),
}


def fetch_recent(ticker: str, days: int = 10) -> "pd.DataFrame | None":
    try:
        df = yf.Ticker(ticker).history(period=f"{days}d", auto_adjust=False)
        if df is None or len(df) == 0:
            return None
        df = df[["Open", "High", "Low", "Close", "Volume"]].copy()
        df.columns = ["open", "high", "low", "close", "volume"]
        df.index = pd.to_datetime(df.index).tz_localize(None)
        return df
    except Exception as e:
        return None


def update_one_ticker(ticker: str, src_path: Path, market: str) -> tuple:
    """Returns (status, info). status: ok / fail / no-new-data."""
    try:
        src = json.loads(src_path.read_text(encoding="utf-8"))
    except Exception:
        return ("fail", "json-read")
    df = fetch_recent(ticker, days=10)
    if df is None or len(df) == 0:
        return ("fail", "no-data")
    # Find Sept 7 and Sept 8 trading days
    sept7 = df[df.index.strftime("%Y-%m-%d") == "2026-09-07"]
    sept8 = df[df.index.strftime("%Y-%m-%d") == "2026-09-08"]
    sept9 = df[df.index.strftime("%Y-%m-%d") == "2026-09-09"]
    sept10 = df[df.index.strftime("%Y-%m-%d") == "2026-09-10"]
    if len(sept8) == 0 and len(sept7) == 0:
        return ("no-new-data", df.index[-1].strftime("%Y-%m-%d"))
    # Use latest available (Sept 10 if available, else Sept 9, else Sept 8)
    target = sept10.iloc[-1] if len(sept10) else sept9.iloc[-1] if len(sept9) else sept8.iloc[-1] if len(sept8) else sept7.iloc[-1]
    target_date = target.name if hasattr(target, 'name') else target.index[-1]
    target_date_s = target_date.strftime("%Y-%m-%d")
    # previous close = target's prev close = last bar before target_date
    prev_idx = df.index.get_loc(target_date)
    prev_close = float(df.iloc[prev_idx - 1]["close"]) if prev_idx > 0 else float(target["open"])
    chg_pct = (float(target["close"]) - prev_close) / prev_close * 100

    # Update last_bar
    old_date = src.get("last_bar", {}).get("date")
    src["last_bar"] = {
        "date": target_date_s,
        "O": float(target["open"]),
        "H": float(target["high"]),
        "L": float(target["low"]),
        "C": float(target["close"]),
        "prev_close": prev_close,
        "chg_pct": round(chg_pct, 2),
    }
    src_path.write_text(json.dumps(src, ensure_ascii=False, indent=2), encoding="utf-8")

    # Also append to public/<market>200/ohlc/{TICKER}_ohlc.json
    safe = ticker.replace(".", "_")
    ohlc_path = OHLC_OUT[market] / f"{safe}_ohlc.json"
    if ohlc_path.exists():
        try:
            bars = json.load(open(ohlc_path))
        except Exception:
            bars = []
    else:
        bars = []
    # Check if target_date already in bars
    if not any(b.get("date") == target_date_s for b in bars):
        bars.append({
            "date": target_date_s,
            "open": float(target["open"]),
            "high": float(target["high"]),
            "low": float(target["low"]),
            "close": float(target["close"]),
            "volume": float(target["volume"]),
        })
        # Sort by date
        bars = sorted(bars, key=lambda b: b["date"])
        ohlc_path.write_text(json.dumps(bars, ensure_ascii=False), encoding="utf-8")
    return ("ok", f"{old_date} → {target_date_s}, close {float(target['close']):.2f}, chg {chg_pct:+.2f}%")


def get_tickers_for_market(market: str) -> list[tuple]:
    if market == "HK":
        out = []
        for fp in sorted(HK_OUT.glob("*.json")):
            if fp.stem.endswith("_ohlc") or "_ohlc." in fp.name or fp.stem.startswith("HK."):
                continue
            try:
                d = json.load(open(fp))
                tk = d.get("ticker")
                if not tk or not tk.endswith(".HK"):
                    continue
            except Exception:
                continue
            out.append((tk, fp))
        return out
    elif market == "US":
        out = []
        for fp in sorted(US_OUT.glob("*.json")):
            if "_ohlc" in fp.name or fp.stem.startswith("US."):
                continue
            try:
                d = json.load(open(fp))
                tk = d.get("ticker")
                if not tk:
                    continue
                # Strip "US." prefix if present
                if tk.startswith("US."):
                    tk = tk[3:]
            except Exception:
                continue
            out.append((tk, fp))
        return out
    else:  # JP
        # JP: tickers from jp_universe_200.json
        univ = json.load(open("/Users/kenken/dev/dsa-hk/jp_universe_200.json"))
        out = []
        for ticker in univ:
            tk = ticker if ticker.endswith(".T") else f"{ticker}.T"
            safe = tk.replace(".", "_")
            fp = JP_OUT / f"{safe}.json"
            if fp.exists():
                out.append((tk, fp))
        return out


def main():
    workers = 12
    for market in ["HK", "US", "JP"]:
        print(f"\n=== {market} ===", flush=True)
        tickers = get_tickers_for_market(market)
        print(f"  {len(tickers)} tickers, {workers} workers", flush=True)
        t0 = time.time()
        ok = 0
        no_new = 0
        fail = 0
        fail_examples = []
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futures = {ex.submit(update_one_ticker, tk, fp, market): tk for tk, fp in tickers}
            for i, fut in enumerate(as_completed(futures), 1):
                tk = futures[fut]
                try:
                    s, info = fut.result()
                    if s == "ok":
                        ok += 1
                    elif s == "no-new-data":
                        no_new += 1
                    else:
                        fail += 1
                        if len(fail_examples) < 3:
                            fail_examples.append(f"{tk}: {info}")
                except Exception as e:
                    fail += 1
                if i % 50 == 0:
                    print(f"  [{i}/{len(tickers)}] ok={ok} no-new={no_new} fail={fail} ({time.time()-t0:.1f}s)", flush=True)
        print(f"  Done {market}: ok={ok} no-new={no_new} fail={fail} in {time.time()-t0:.1f}s", flush=True)
        if fail_examples:
            print(f"  Fail examples: {fail_examples}", flush=True)


if __name__ == "__main__":
    main()

"""update_us_sept14.py — Update US last_bar + OHLC to Sept 14 close via yfinance.

US market closes ~6 hours before HKT, so by the time HK traders are awake,
yfinance has today's daily row populated (close + H + L + full OHLCV).

Pipeline per ticker:
1. yfinance history (10d) — get Sept 14 OHLC + Sept 11 prev_close
2. Write source last_bar (date=C, O, H, L, prev_close, chg_pct)
3. Merge Sept 14 bar into public/us200/ohlc/{safe}_ohlc.json
4. Mirror to source /Minimax/charts/us200/{safe}_ohlc.json (so build doesn't reset)

Usage:
    python3 scripts/update_us_sept14.py --target 2026-09-14
"""
from __future__ import annotations
import json
import shutil
import argparse
import time
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

import yfinance as yf
import pandas as pd

US_OUT = Path("/Users/kenken/dev/dsa-hk/charts/us200")
OHLC_DIR = Path("/Users/kenken/dev/dsa-hk/public/us200/ohlc")
DEFAULT_TARGET = "2026-09-14"


def fetch_yf_one(ticker: str, target: str) -> dict | None:
    # yfinance doesn't recognize "US.X" prefix — strip it
    yf_symbol = ticker.replace("US.", "")
    try:
        # 2026-09-18: yfinance bug — period="15d" returns NaN for most recent
        # trading day close, even with auto_adjust=True. Use period="5d" which
        # works correctly for the most recent EOD.
        df = yf.Ticker(yf_symbol).history(period="5d", auto_adjust=True)
        if df is None or len(df) == 0:
            return None
        df.index = pd.to_datetime(df.index).tz_localize(None)
        # Find target row
        target_row = df[df.index.strftime("%Y-%m-%d") == target]
        if len(target_row) == 0:
            return None
        target_idx = df.index.get_loc(target_row.index[-1])
        target = df.iloc[target_idx]
        prev_close = float(df.iloc[target_idx - 1]["Close"]) if target_idx > 0 else float(target["Open"])
        # Also pull all bars in df that are >= some date (Sept 8+) for OHLC extension
        bars = []
        for i in range(len(df)):
            d = df.index[i]
            bars.append({
                "date": d.strftime("%Y-%m-%d"),
                "open": float(df.iloc[i]["Open"]),
                "high": float(df.iloc[i]["High"]),
                "low": float(df.iloc[i]["Low"]),
                "close": float(df.iloc[i]["Close"]),
                "volume": float(df.iloc[i]["Volume"]),
            })
        return {
            "last_bar": {
                "date": target_row.index[-1].strftime("%Y-%m-%d"),
                "O": float(target["Open"]),
                "H": float(target["High"]),
                "L": float(target["Low"]),
                "C": float(target["Close"]),
                "prev_close": prev_close,
            },
            "bars": bars,
        }
    except Exception as e:
        return None


def patch_one(ticker: str, src_path: Path, target: str, dry: bool = False) -> tuple:
    safe = ticker.replace(".", "_")
    data = fetch_yf_one(ticker, target)
    if not data:
        return ("fail", f"{ticker}: yfinance no data for {target}")

    lb = data["last_bar"]
    new_bars = {b["date"]: b for b in data["bars"]}
    chg_pct = (lb["C"] - lb["prev_close"]) / lb["prev_close"] * 100

    # Update src JSON
    try:
        src = json.loads(src_path.read_text(encoding="utf-8"))
    except Exception:
        src = {}
    old_date = src.get("last_bar", {}).get("date")
    if not dry:
        src["last_bar"] = {
            **lb,
            "chg_pct": round(chg_pct, 2),
        }
        src_path.write_text(json.dumps(src, ensure_ascii=False, indent=2), encoding="utf-8")

    # Merge into public ohlc
    OHLC_DIR.mkdir(parents=True, exist_ok=True)
    ohlc_path = OHLC_DIR / f"{safe}_ohlc.json"
    if ohlc_path.exists():
        try:
            existing = json.load(open(ohlc_path))
            if not isinstance(existing, list): existing = []
        except Exception:
            existing = []
    else:
        existing = []
    existing_idx = {b["date"]: i for i, b in enumerate(existing) if isinstance(b, dict) and "date" in b}
    appended, replaced = 0, 0
    for date_s, bar in new_bars.items():
        if date_s in existing_idx:
            if existing[existing_idx[date_s]] != bar:
                existing[existing_idx[date_s]] = bar
                replaced += 1
        else:
            existing.append(bar)
            appended += 1
    if not dry and (appended or replaced):
        existing = sorted(existing, key=lambda b: b["date"])
        ohlc_path.write_text(json.dumps(existing, ensure_ascii=False), encoding="utf-8")
        # Mirror to source ohlc (build_dashboard.py copies FROM src)
        src_ohlc = src_path.parent / f"{safe}_ohlc.json"
        if src_ohlc != ohlc_path:
            shutil.copy2(ohlc_path, src_ohlc)
        # Update data_range in source
        if not dry and existing:
            src["data_range"] = f"{existing[0]['date']} → {existing[-1]['date']} ({len(existing)} daily bars)"
            src_path.write_text(json.dumps(src, ensure_ascii=False, indent=2), encoding="utf-8")

    return ("ok", f"{ticker}: {old_date} → {lb['date']} C={lb['C']:.2f} ({chg_pct:+.2f}%) oh+{appended}≈{replaced}")


def get_tickers() -> list[tuple[str, Path]]:
    out = []
    for fp in sorted(US_OUT.glob("*.json")):
        if "_ohlc" in fp.name or fp.stem.startswith("US."):
            continue
        try:
            d = json.load(open(fp))
            tk = d.get("ticker")
            if not tk: continue
            out.append((tk, fp))
        except Exception:
            continue
    return out


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--dry", action="store_true")
    p.add_argument("--workers", type=int, default=8)
    p.add_argument("--target", default=DEFAULT_TARGET)
    args = p.parse_args()

    tickers = get_tickers()
    print(f"\n=== US ({len(tickers)} tickers, target={args.target}) ===", flush=True)
    ok = fail = 0
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futures = {ex.submit(patch_one, tk, fp, args.target, args.dry): tk for tk, fp in tickers}
        for fut in as_completed(futures):
            tk = futures[fut]
            try:
                status, msg = fut.result()
            except Exception as e:
                status, msg = "fail", f"{tk}: {e}"
            if status == "ok":
                ok += 1
            else:
                fail += 1
                if fail <= 5:
                    print(f"  ⚠ {msg}", flush=True)
    print(f"  US: ok={ok}, fail={fail}", flush=True)


if __name__ == "__main__":
    main()

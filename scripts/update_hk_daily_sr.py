"""update_hk_daily_sr.py — Update HK using daily_sr.fetch_ohlc (Futu → yfinance fallback).

Replaces Tencent kline API (rate-limited) with the daily-sr-chart skill's
fetch_ohlc function which tries Futu OpenD first then yfinance.
"""
import sys, json, time
from datetime import datetime
from pathlib import Path
sys.path.insert(0, '/Users/kenmax/.minimax/skills/daily-sr-chart/scripts')
sys.path.insert(0, '/Users/kenken/.minimax/skills/daily-sr-chart/scripts')
import daily_sr
from concurrent.futures import ThreadPoolExecutor, as_completed

HK_OUT = Path("/Users/kenken/dev/dsa-hk/charts/hk200")
OHLC_OUT = Path("/Users/kenken/dev/dsa-hk/public/hk200/ohlc")


def update_one(ticker: str, src_path: Path) -> tuple:
    try:
        src = json.loads(src_path.read_text(encoding="utf-8"))
    except Exception:
        return ("fail", "json")
    df, src_kind, name = daily_sr.fetch_ohlc(ticker, days=10, source="auto")
    if df is None or len(df) == 0:
        return ("fail", f"no-data ({src_kind})")
    # Find Sept 11 (or Sept 10)
    target_date = None
    for d in ("2026-09-11", "2026-09-10"):
        rows = df[df.index.strftime("%Y-%m-%d") == d]
        if len(rows) > 0:
            target_date = d
            target = rows.iloc[-1]
            break
    if not target_date:
        return ("no-new-data", df.index[-1].strftime("%Y-%m-%d"))
    prev_idx = df.index.get_loc(target.index[-1])
    prev_close = float(df.iloc[prev_idx - 1]["close"]) if prev_idx > 0 else float(target["open"])
    chg = (float(target["close"]) - prev_close) / prev_close * 100
    old_date = src.get("last_bar", {}).get("date")
    src["last_bar"] = {
        "date": target_date,
        "O": float(target["open"]),
        "H": float(target["high"]),
        "L": float(target["low"]),
        "C": float(target["close"]),
        "prev_close": prev_close,
        "chg_pct": round(chg, 2),
    }
    src_path.write_text(json.dumps(src, ensure_ascii=False, indent=2), encoding="utf-8")
    safe = ticker.replace(".", "_")
    ohlc_path = OHLC_OUT / f"{safe}_ohlc.json"
    if ohlc_path.exists():
        try:
            existing = json.load(open(ohlc_path))
        except Exception:
            existing = []
    else:
        existing = []
    for d in ("2026-09-09", "2026-09-10", "2026-09-11"):
        rows = df[df.index.strftime("%Y-%m-%d") == d]
        if len(rows) == 0:
            continue
        r = rows.iloc[-1]
        if not any(e.get("date") == d for e in existing):
            existing.append({
                "date": d,
                "open": float(r["open"]),
                "high": float(r["high"]),
                "low": float(r["low"]),
                "close": float(r["close"]),
                "volume": float(r["volume"]),
            })
    existing = sorted(existing, key=lambda b: b["date"])
    ohlc_path.write_text(json.dumps(existing, ensure_ascii=False), encoding="utf-8")
    return ("ok", f"{old_date} → {target_date} close {float(target['close']):.2f} chg {chg:+.2f}% (src:{src_kind})")


def get_tickers():
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


def main():
    workers = 6
    tickers = get_tickers()
    print(f"HK update: {len(tickers)} tickers, {workers} workers", flush=True)
    t0 = time.time()
    ok = 0
    fail = 0
    fail_examples = []
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = {ex.submit(update_one, tk, fp): tk for tk, fp in tickers}
        for i, fut in enumerate(as_completed(futures), 1):
            tk = futures[fut]
            try:
                s, info = fut.result()
                if s == "ok":
                    ok += 1
                else:
                    fail += 1
                    if len(fail_examples) < 5:
                        fail_examples.append(f"{tk}: {info}")
            except Exception as e:
                fail += 1
            if i % 20 == 0:
                print(f"  [{i}/{len(tickers)}] ok={ok} fail={fail} ({time.time()-t0:.1f}s)", flush=True)
    print(f"  Done HK: ok={ok} fail={fail} in {time.time()-t0:.1f}s", flush=True)
    if fail_examples:
        print(f"  Fail examples: {fail_examples}", flush=True)


if __name__ == "__main__":
    main()

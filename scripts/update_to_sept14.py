"""update_to_sept14.py — Update HK + JP last_bar to Sept 14 (Mon) close via Tencent qt.

qt endpoint: `http://qt.gtimg.cn/q={market}{code}` returns
[name, ticker, today_close, prev_close, today_open, volume, ...]
For HK + JP both work; today (2026-09-14) close is in position [3].
Tencent qt is real-time and won't lag like kline.
"""
from __future__ import annotations
import json
import re
import time
import urllib.request
import argparse
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

HK_OUT = Path("/Users/kenken/dev/dsa-hk/charts/hk200")
JP_OUT = Path("/Users/kenken/dev/dsa-hk/data/jp200")
OHLC_OUT = {
    "HK": Path("/Users/kenken/dev/dsa-hk/public/hk200/ohlc"),
    "JP": Path("/Users/kenken/dev/dsa-hk/public/jp200/ohlc"),
}
TARGET_DATE = "2026-09-14"


def fetch_qt(market: str, code: str) -> dict | None:
    """Tencent qt endpoint. Returns today's O/C/prev_close/volume."""
    url = f"http://qt.gtimg.cn/q={market.lower()}{code}"
    try:
        r = urllib.request.urlopen(url, timeout=4)
        raw = r.read()
    except Exception:
        return None
    # qt uses GBK for Asian markets
    try:
        decoded = raw.decode("gbk", errors="replace")
    except Exception:
        decoded = raw.decode("utf-8", errors="replace")
    m = re.search(r'="([^"]+)"', decoded)
    if not m:
        return None
    parts = m.group(1).split("~")
    if len(parts) < 7:
        return None
    try:
        return {
            "name": parts[1],
            "ticker": parts[2],
            "close": float(parts[3]),
            "prev_close": float(parts[4]),
            "open": float(parts[5]),
            "volume": float(parts[6]),
        }
    except (ValueError, IndexError):
        return None


def update_one(ticker: str, src_path: Path, market: str, dry: bool = False) -> tuple:
    try:
        src = json.loads(src_path.read_text(encoding="utf-8"))
    except Exception:
        return ("fail", f"{ticker}: json")
    safe = ticker.replace(".", "_")
    raw_code = ticker.replace(".HK", "").replace(".T", "")
    code = raw_code.lstrip("0").zfill(5 if market == "HK" else 4)

    qt = fetch_qt(market, code)
    if not qt or qt["close"] <= 0 or qt["prev_close"] <= 0:
        return ("fail", f"{ticker}: qt={qt is not None}")
    today = {
        "date": TARGET_DATE,
        "open": qt["open"],
        "close": qt["close"],
        "high": max(qt["open"], qt["close"], qt["prev_close"]),
        "low": min(qt["open"], qt["close"], qt["prev_close"]),
        "volume": qt["volume"],
    }
    chg_pct = (today["close"] - qt["prev_close"]) / qt["prev_close"] * 100
    old_date = src.get("last_bar", {}).get("date")
    old_c = src.get("last_bar", {}).get("C")

    if not dry:
        src["last_bar"] = {
            "date": today["date"],
            "O": today["open"], "H": today["high"],
            "L": today["low"], "C": today["close"],
            "prev_close": qt["prev_close"],
            "chg_pct": round(chg_pct, 2),
        }
        src_path.write_text(json.dumps(src, ensure_ascii=False, indent=2), encoding="utf-8")

    # Append to ohlc
    ohlc_dir = OHLC_OUT[market]
    ohlc_path = ohlc_dir / f"{safe}_ohlc.json"
    if ohlc_path.exists():
        try:
            existing = json.load(open(ohlc_path))
            existing = existing if isinstance(existing, list) else []
        except Exception:
            existing = []
    else:
        existing = []
    appended = []
    if not any(b.get("date") == TARGET_DATE for b in existing):
        existing.append({
            "date": today["date"], "open": today["open"],
            "high": today["high"], "low": today["low"],
            "close": today["close"], "volume": today["volume"],
        })
        appended.append(today["date"])
    if not dry and appended:
        existing = sorted(existing, key=lambda b: b["date"])
        ohlc_dir.mkdir(parents=True, exist_ok=True)
        ohlc_path.write_text(json.dumps(existing, ensure_ascii=False), encoding="utf-8")

    return ("ok", f"{ticker}: {old_date}/{old_c} → {today['date']}/{today['close']:.2f} ({chg_pct:+.2f}%) oh+{len(appended)}")


def get_tickers(market: str) -> list[tuple[str, Path]]:
    out_dir = HK_OUT if market == "HK" else JP_OUT
    out = []
    for fp in sorted(out_dir.glob("*.json")):
        if "_ohlc" in fp.name or fp.stem.startswith(f"{market}."):
            continue
        try:
            d = json.load(open(fp))
            tk = d.get("ticker")
            if not tk:
                continue
            suffix = ".HK" if market == "HK" else ".T"
            if not tk.endswith(suffix):
                continue
            out.append((tk, fp))
        except Exception:
            continue
    return out


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--dry", action="store_true")
    p.add_argument("--workers", type=int, default=10)
    p.add_argument("--markets", default="HK,JP")
    args = p.parse_args()

    summary = {}
    for market in args.markets.split(","):
        market = market.strip().upper()
        if market not in ("HK", "JP"):
            continue
        tickers = get_tickers(market)
        print(f"\n=== {market} ({len(tickers)} tickers, target={TARGET_DATE}) ===", flush=True)
        ok = fail = 0
        with ThreadPoolExecutor(max_workers=args.workers) as ex:
            futures = {ex.submit(update_one, tk, fp, market, args.dry): tk for tk, fp in tickers}
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
                    if fail <= 3:
                        print(f"  ⚠ {msg}", flush=True)
        summary[market] = (ok, fail)
        print(f"  {market}: ok={ok}, fail={fail}", flush=True)
    print("\n=== summary ===", flush=True)
    for m, (o, f) in summary.items():
        print(f"  {m}: ok={o}, fail={f}", flush=True)


if __name__ == "__main__":
    main()

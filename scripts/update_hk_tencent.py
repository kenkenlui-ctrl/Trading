"""update_hk_tencent.py — Update HK last_bar to Sept 8 close via Tencent kline API.

Tencent web.ifzq.gtimg.cn kline API returns historical daily bars. We pull
Sept 1 - Sept 8 and update last_bar to Sept 8 close.
"""
from __future__ import annotations
import json
import re
import time
import urllib.request
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

HK_OUT = Path("/Users/kenken/dev/dsa-hk/charts/hk200")
OHLC_OUT = Path("/Users/kenken/dev/dsa-hk/public/hk200/ohlc")


def fetch_tencent_kline(ticker: str, start: str = "2026-09-01", end: str = "2026-09-11") -> list | None:
    code = ticker.replace(".HK", "").zfill(5)
    url = f"http://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param=hk{code},day,{start},{end},5,qfq"
    try:
        r = urllib.request.urlopen(url, timeout=5)
        raw = r.read().decode("utf-8", errors="replace")
    except Exception:
        return None
    try:
        data = json.loads(raw)
    except Exception:
        return None
    if data.get("code") != 0:
        return None
    key = f"hk{code}"
    if key not in data.get("data", {}):
        return None
    bars = data["data"][key].get("day", []) or data["data"][key].get("qfqday", [])
    if not bars:
        return None
    # Format: [date, open, close, high, low, volume]
    out = []
    for b in bars:
        try:
            out.append({
                "date": b[0],
                "open": float(b[1]),
                "close": float(b[2]),
                "high": float(b[3]),
                "low": float(b[4]),
                "volume": float(b[5]),
            })
        except (ValueError, IndexError):
            continue
    return out


def fetch_tencent_name(ticker: str) -> str:
    code = ticker.replace(".HK", "").zfill(5)
    url = f"http://qt.gtimg.cn/q=hk{code}"
    try:
        r = urllib.request.urlopen(url, timeout=5)
        raw = r.read().decode("gbk", errors="replace")
    except Exception:
        return ""
    m = re.search(r'="([^"]+)"', raw)
    if not m:
        return ""
    parts = m.group(1).split("~")
    return parts[1] if len(parts) > 1 else ""


def update_one(ticker: str, src_path: Path) -> tuple:
    try:
        src = json.loads(src_path.read_text(encoding="utf-8"))
    except Exception:
        return ("fail", "json")
    bars = fetch_tencent_kline(ticker, "2026-09-01", "2026-09-11")
    if not bars:
        return ("fail", "tencent-kline")
    # Use Sept 11 close (today)
    target = next((b for b in bars if b["date"] == "2026-09-11"), None)
    if not target:
        target = next((b for b in bars if b["date"] == "2026-09-10"), None)
    if not target:
        target = bars[-1]  # fallback to latest
    prev_close = None
    for i, b in enumerate(bars):
        if b["date"] == target["date"] and i > 0:
            prev_close = bars[i - 1]["close"]
            break
    if prev_close is None:
        prev_close = target["open"]
    chg_pct = (target["close"] - prev_close) / prev_close * 100

    old_date = src.get("last_bar", {}).get("date")
    src["last_bar"] = {
        "date": target["date"],
        "O": target["open"],
        "H": target["high"],
        "L": target["low"],
        "C": target["close"],
        "prev_close": prev_close,
        "chg_pct": round(chg_pct, 2),
    }
    src_path.write_text(json.dumps(src, ensure_ascii=False, indent=2), encoding="utf-8")

    # Append Sept 9 + 10 + 11 to public/ohlc
    safe = ticker.replace(".", "_")
    ohlc_path = OHLC_OUT / f"{safe}_ohlc.json"
    if ohlc_path.exists():
        try:
            existing = json.load(open(ohlc_path))
        except Exception:
            existing = []
    else:
        existing = []
    for b in bars:
        if b["date"] in ("2026-09-09", "2026-09-10", "2026-09-11") and not any(e.get("date") == b["date"] for e in existing):
            existing.append({
                "date": b["date"],
                "open": b["open"],
                "high": b["high"],
                "low": b["low"],
                "close": b["close"],
                "volume": b["volume"],
            })
    existing = sorted(existing, key=lambda b: b["date"])
    ohlc_path.write_text(json.dumps(existing, ensure_ascii=False), encoding="utf-8")
    return ("ok", f"{old_date} → {target['date']} close {target['close']:.2f} chg {chg_pct:+.2f}%")


def get_hk_tickers() -> list:
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
    workers = 8
    tickers = get_hk_tickers()
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
        print(f"  Fail: {fail_examples}", flush=True)


if __name__ == "__main__":
    main()

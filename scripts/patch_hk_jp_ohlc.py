"""patch_hk_jp_ohlc.py — Patch missing OHLC bars + ensure OHLC ends on TARGET_DATE.

Charts look "outdated" when:
  - last_bar.date = TARGET_DATE  (correct, from qt)
  - OHLC array (in chart.json + *_ohlc.json) ends earlier than TARGET_DATE
  → canvas-chart.js draws candlesticks up to last bar in OHLC, leaving a gap

Fix: Tencent kline (day array) gives the last 5-7 days. Pull, merge with
existing OHLC bar-by-bar (overwrite on date match, append new). Also ensure
last_bar is TARGET_DATE via qt.

Tencent rate-limits parallel calls; default workers=6.
"""
from __future__ import annotations
import json
import re
import time
import shutil
import urllib.request
import argparse
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

HK_OUT = Path("/Users/kenken/dev/dsa-hk/charts/hk200")
JP_OUT = Path("/Users/kenken/dev/dsa-hk/data/jp200")
OHLC_DIR = {
    "HK": Path("/Users/kenken/dev/dsa-hk/public/hk200/ohlc"),
    "JP": Path("/Users/kenken/dev/dsa-hk/public/jp200/ohlc"),
}
TARGET_DATE = "2026-09-24"
KLINE_START = "2026-09-21"
KLINE_END   = "2026-09-23"


def _http(url, timeout=6, retries=2):
    last = None
    for i in range(retries):
        try:
            r = urllib.request.urlopen(url, timeout=timeout)
            return r.read()
        except Exception as e:
            last = e
            time.sleep(0.4 + i * 0.4)
    return None


def fetch_kline(market: str, code: str) -> list:
    """Tencent kline day array. May lag last day, may be empty for some tickers."""
    key = f"{market.lower()}{code}"
    url = (
        f"http://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
        f"?param={key},day,{KLINE_START},{KLINE_END},5,qfq"
    )
    raw = _http(url)
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except Exception:
        return []
    if data.get("code") != 0 or key not in data.get("data", {}):
        return []
    bars_raw = data["data"][key].get("day", []) or data["data"][key].get("qfqday", [])
    out = []
    for b in bars_raw:
        try:
            out.append({
                "date": b[0], "open": float(b[1]), "close": float(b[2]),
                "high": float(b[3]), "low": float(b[4]), "volume": float(b[5]),
            })
        except (ValueError, IndexError):
            continue
    return out


def fetch_qt(market: str, code: str) -> dict | None:
    url = f"http://qt.gtimg.cn/q={market.lower()}{code}"
    raw = _http(url)
    if not raw:
        return None
    try:
        decoded = raw.decode("gbk", errors="replace")
    except Exception:
        decoded = raw.decode("utf-8", errors="replace")
    m = re.search(r'="([^"]+)"', decoded)
    if not m:
        return None
    p = m.group(1).split("~")
    if len(p) < 7:
        return None
    try:
        return {
            "close": float(p[3]), "prev_close": float(p[4]),
            "open": float(p[5]), "volume": float(p[6]),
        }
    except (ValueError, IndexError):
        return None


def patch_one(ticker: str, src_path: Path, market: str, dry: bool = False) -> tuple:
    try:
        src = json.loads(src_path.read_text(encoding="utf-8"))
    except Exception:
        return ("fail", f"{ticker}: src json")
    safe = ticker.replace(".", "_")
    # 2026-09-17: Tencent qt only recognizes "NNNNN.HK" / "NNNN.T" not "HK.NNNNN"
    # Extract real code from ticker_user fallback when ticker has HK./JP. prefix
    qt_code = ticker.replace(".HK", "").replace(".T", "").lstrip("0").zfill(5 if market == "HK" else 4)
    if ticker.startswith(f"{market}.") and "ticker_user" in src:
        tu = src["ticker_user"]
        if tu.endswith(".HK") or tu.endswith(".T"):
            qt_code = tu.replace(".HK", "").replace(".T", "").lstrip("0").zfill(5 if market == "HK" else 4)
    code = qt_code  # use the qt-friendly code for both kline + qt fetches

    kline_bars = fetch_kline(market, code)
    qt = fetch_qt(market, code)
    # 2026-09-17: explicit diag if qt failed (Tencent rate-limit / network blip)
    if qt is None:
        print(f"  ⚠ {ticker}: qt=None (kline={len(kline_bars)})", flush=True)

    # Build candidate bars (newest data wins)
    new_bars = {}
    for b in kline_bars:
        new_bars[b["date"]] = b
    # If today missing from kline (Tencent may lag 1 day) but qt has it
    if qt and TARGET_DATE not in new_bars:
        new_bars[TARGET_DATE] = {
            "date": TARGET_DATE,
            "open": qt["open"],
            "close": qt["close"],
            "high": max(qt["open"], qt["close"], qt["prev_close"]),
            "low": min(qt["open"], qt["close"], qt["prev_close"]),
            "volume": qt["volume"],
        }

    # Merge into existing OHLC
    ohlc_dir = OHLC_DIR[market]
    ohlc_dir.mkdir(parents=True, exist_ok=True)
    # 2026-10-08: public filenames follow the project convention (NNN_T), not
    # the Futu feed's "JP.NNNN" -> "JP_NNNN". The prefixed form wrote a SECOND
    # parallel file per ticker — 192 of them, every one holding 5 bars from a
    # truncated 2026-09-24 fetch. bars_from_public() drops anything under 260
    # bars, so those tickers looked fetched and contributed nothing, and the
    # directory carried two naming schemes with no overlap to spot the mistake.
    # `safe` still names the SOURCE-side file below; only the public name is
    # canonicalised here.
    pub_name = safe[3:] + "_T" if market == "JP" and safe.startswith("JP_") else safe
    ohlc_path = ohlc_dir / f"{pub_name}_ohlc.json"
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
            # Replace (kline is more accurate than older snapshot)
            if existing[existing_idx[date_s]] != bar:
                existing[existing_idx[date_s]] = bar
                replaced += 1
        else:
            existing.append(bar)
            appended += 1
    if not dry and (appended or replaced):
        existing = sorted(existing, key=lambda b: b["date"])
        ohlc_path.write_text(json.dumps(existing, ensure_ascii=False), encoding="utf-8")
        # CRITICAL: also mirror to source ohlc dir, because build_dashboard.py
        # copies FROM src_dir/{safe}_ohlc.json → public. Without this, the
        # build will overwrite our public patch on next deploy.
        src_ohlc = src_path.parent / f"{safe}_ohlc.json"
        if src_ohlc != ohlc_path:
            shutil.copy2(ohlc_path, src_ohlc)

    # Always make sure last_bar is TARGET_DATE with qt-sourced values
    last = new_bars.get(TARGET_DATE) or (kline_bars[-1] if kline_bars else None)
    if last and qt:
        chg = (qt["close"] - qt["prev_close"]) / qt["prev_close"] * 100
        old_date = src.get("last_bar", {}).get("date")
        new_last = {
            "date": TARGET_DATE,
            "O": qt["open"], "H": last["high"], "L": last["low"],
            "C": qt["close"], "prev_close": qt["prev_close"],
            "chg_pct": round(chg, 2),
        }
        if not dry:
            src["last_bar"] = new_last
            # Also update data_range to include the new bars
            if existing:
                src["data_range"] = f"{existing[0]['date']} → {existing[-1]['date']} ({len(existing)} daily bars)"
            src_path.write_text(json.dumps(src, ensure_ascii=False, indent=2), encoding="utf-8")
    return ("ok", f"{ticker}: bars {len(existing)} (+{appended} ≈{replaced})")


def get_tickers(market: str) -> list[tuple[str, Path]]:
    out_dir = HK_OUT if market == "HK" else JP_OUT
    out = []
    for fp in sorted(out_dir.glob("*.json")):
        if "_ohlc" in fp.name or fp.stem.startswith(f"{market}."):
            continue
        try:
            d = json.load(open(fp))
            tk = d.get("ticker")
            if not tk: continue
            suffix = ".HK" if market == "HK" else ".T"
            # 2026-09-17: accept both "NNNNN.HK" and "HK.NNNNN" formats
            if not (tk.endswith(suffix) or tk.startswith(market + ".")): continue
            out.append((tk, fp))
        except Exception:
            continue
    return out


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--dry", action="store_true")
    p.add_argument("--workers", type=int, default=6)
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
            futures = {ex.submit(patch_one, tk, fp, market, args.dry): tk for tk, fp in tickers}
            for fut in as_completed(futures):
                tk = futures[fut]
                try:
                    status, msg = fut.result()
                except Exception as e:
                    status, msg = "fail", f"{tk}: {e}"
                if status == "ok": ok += 1
                else:
                    fail += 1
                    if fail <= 3:
                        print(f"  ⚠ {msg}", flush=True)
        summary[market] = (ok, fail)
        print(f"  {market}: ok={ok}, fail={fail}", flush=True)
    print("\n=== summary ===")
    for m, (o, f) in summary.items():
        print(f"  {m}: ok={o}, fail={f}", flush=True)


if __name__ == "__main__":
    main()

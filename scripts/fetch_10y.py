#!/usr/bin/env python3
"""fetch_10y.py — download 10y daily OHLC for the published universe.

Why re-fetch instead of trusting a third party's cache: the deliverable audit
shipped no bars, and a backtest is only as good as the data behind it. Bars
come from Yahoo via yfinance and are cached to data/bt10y/<safe>.json so a
re-run costs nothing and the exact input to any result is inspectable.

Yahoo throttles hard (429 / NaN series) when a lot of symbols are pulled at
once, so this walks the universe in small batches with a pause and retries,
and it is resumable: a symbol already on disk is skipped.

Usage:
    python3 scripts/fetch_10y.py --batch 25 --pause 6 [--only us200]
"""
from __future__ import annotations
import argparse
import json
import time
from pathlib import Path

REPO = Path("/Users/kenken/dev/dsa-hk")
OUT = REPO / "data" / "bt10y"
OUT.mkdir(parents=True, exist_ok=True)

MARKETS = {
    "hk": (REPO / "hk_universe_200.json", None),
    "us": (REPO / "charts/us200/us_top200_fresh.json", None),
    "jp": (None, REPO / "data/jp200"),
}
# Index / market proxies: used by the v2 market filter and as buy-and-hold
# benchmarks. Symbols are Yahoo-native; dsa-hk stores the HK/JP ETF locally.
EXTRA = ["SPY", "^GSPC", "QQQ", "IWM", "^N225", "1306.T", "^HSI", "2800.HK", "^VIX"]


def universe(upath, out_dir: Path | None):
    if upath:
        raw = json.loads(upath.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            raw = raw.get("symbols") or raw.get("tickers") or []
        out = []
        for it in raw:
            tk = it if isinstance(it, str) else (it[1] if isinstance(it, (list, tuple)) and len(it) > 1 else it.get("ticker", ""))
            if tk:
                out.append((tk, tk.replace(".", "_").replace("/", "_")))
        return out
    out = []
    for p in sorted((out_dir or REPO).glob("*.json")):
        if p.name.endswith("_ohlc.json"):
            continue
        s = p.stem
        if s.endswith("_T"):
            out.append((s[:-2] + ".T", s))
        elif s.endswith("_HK"):
            out.append((s[:-3] + ".HK", s))
        else:
            out.append((s, s))
    return out


def _one(symbol: str, start: str, end: str) -> list[dict]:
    import pandas as pd
    d = _download([symbol], start, end)
    if d is None or d.empty or "Close" not in d.columns:
        return []
    sub = d.dropna(how="all")
    rows = []
    for dt, r in sub.iterrows():
        c = r["Close"]
        if pd.isna(c):
            continue
        rows.append({
            "date": pd.Timestamp(dt).strftime("%Y-%m-%d"),
            "open": float(r["Open"]) if not pd.isna(r["Open"]) else float(c),
            "high": float(r["High"]) if not pd.isna(r["High"]) else float(c),
            "low": float(r["Low"]) if not pd.isna(r["Low"]) else float(c),
            "close": float(c),
            "volume": float(r["Volume"]) if "Volume" in r and not pd.isna(r["Volume"]) else 0.0,
        })
    return rows if len(rows) >= 200 else []


def _download(symbols: list[str], start: str, end: str):
    import yfinance as yf
    try:
        return yf.download(symbols, start=start, end=end, progress=False,
                           auto_adjust=True, threads=4, group_by="ticker")
    except Exception:
        return None


def fetch(symbols: list[str], start: str, end: str, tries: int = 4):
    """Bulk download with a per-symbol fallback.

    yfinance fails the WHOLE multi-symbol download if any one symbol in the
    list is bad, so a batch of 30 HK names collapsed because of a single dead
    ticker and nothing got cached. On a failed (or short) batch we retry each
    symbol on its own — which is how BRK-B came back after failing in a batch.
    """
    import yfinance as yf
    import pandas as pd
    last = None
    for t in range(tries):
        try:
            d = yf.download(symbols, start=start, end=end, progress=False,
                            auto_adjust=True, threads=4, group_by="ticker")
            out = {}
            for s in symbols:
                try:
                    # d.columns is a MultiIndex (symbol, field). levels[0] is an
                    # Index, NOT a list — an isinstance(.., list) guard silently
                    # sent every symbol down the wrong branch and returned {}.
                    if d.columns.nlevels > 1 and s in d.columns.get_level_values(0):
                        sub = d[s].dropna(how="all")
                    elif d.columns.nlevels == 1:
                        sub = d.dropna(how="all")
                    else:
                        continue
                    if sub.empty or "Close" not in sub.columns:
                        continue
                    rows = []
                    for dt, r in sub.iterrows():
                        c = r["Close"]
                        if pd.isna(c):
                            continue
                        rows.append({
                            "date": pd.Timestamp(dt).strftime("%Y-%m-%d"),
                            "open": float(r["Open"]) if not pd.isna(r["Open"]) else float(c),
                            "high": float(r["High"]) if not pd.isna(r["High"]) else float(c),
                            "low": float(r["Low"]) if not pd.isna(r["Low"]) else float(c),
                            "close": float(c),
                            "volume": float(r["Volume"]) if "Volume" in r and not pd.isna(r["Volume"]) else 0.0,
                        })
                    if len(rows) >= 200:      # need >=260 for a 200d MA with buffer
                        out[s] = rows
                except Exception:
                    continue
            missing = [s for s in symbols if s not in out]
            if missing:
                import time as _t
                for s in missing:
                    if s in out:
                        continue
                    rows = _one(s, start, end)
                    if rows:
                        out[s] = rows
                    _t.sleep(0.4)
            return out
        except Exception as e:
            last = e
            time.sleep(5 * (t + 1))
    # last resort: symbol by symbol
    out = {}
    for s in symbols:
        rows = _one(s, start, end)
        if rows:
            out[s] = rows
        time.sleep(0.4)
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--batch", type=int, default=25)
    ap.add_argument("--pause", type=float, default=6.0)
    ap.add_argument("--start", default="2016-09-01")
    ap.add_argument("--end", default="2026-10-02")
    ap.add_argument("--only", default=None)
    ap.add_argument("--force", action="store_true", help="re-download even if cached")
    a = ap.parse_args()

    todo = []
    for mkt, (up, od) in MARKETS.items():
        if a.only and a.only != mkt:
            continue
        for tk, safe in universe(up, od):
            if a.force or not (OUT / f"{safe}.json").exists():
                todo.append((tk, safe))
    for s in EXTRA:
        safe = s.replace(".", "_").replace("^", "IDX_").replace("/", "_")
        if a.force or not (OUT / f"{safe}.json").exists():
            todo.append((s, safe))

    print(f"需要抓: {len(todo)} 個代號（已快取 {len(list(OUT.glob('*.json')))} 個）", flush=True)
    if not todo:
        print("全部已快取")
        return

    got = 0
    t0 = time.time()
    for i in range(0, len(todo), a.batch):
        chunk = todo[i:i + a.batch]
        syms = [t for t, _ in chunk]
        res = fetch(syms, a.start, a.end)
        for tk, safe in chunk:
            if tk in res:
                (OUT / f"{safe}.json").write_text(json.dumps(res[tk]), encoding="utf-8")
                got += 1
        el = time.time() - t0
        print(f"  [{i + len(chunk)}/{len(todo)}] 儲存 {got}  ({el:.0f}s)", flush=True)
        if i + a.batch < len(todo):
            time.sleep(a.pause)

    total = len(list(OUT.glob("*.json")))
    print(f"\n完成：今次 {got} 個，快取庫共 {total} 個 → {OUT}")


if __name__ == "__main__":
    main()

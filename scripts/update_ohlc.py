#!/usr/bin/env python3
"""update_ohlc.py — refresh <market>/<safe>_ohlc.json for the interactive charts.

Why this is separate from regen_asof.py: daily-sr-chart writes the action-plan
JSON but not the OHLC series. static/canvas-chart.js derives
/<market>/ohlc/<ticker>_ohlc.json from the chart URL and fetches it, and
build_dashboard.py only COPIES that file from charts/<market>/ into
public/<market>/ohlc/. So if the source series is not refreshed, the ticker
page ends up with a fresh header and static PNG but an interactive chart whose
last candle is the previous session — which is the 09-28/09-29 mismatch that
was live on the US pages until 2026-10-02.

--asof applies the T-1 rule: bars after that date are dropped, so an in-progress
session can never leak in.

Usage:
    python3 scripts/update_ohlc.py --asof 2026-09-30 --markets hk,us,jp --workers 8
"""
from __future__ import annotations
import argparse
import json
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

SKILL_DIR = Path("/Users/kenken/.minimax/skills/daily-sr-chart/scripts")
sys.path.insert(0, str(SKILL_DIR))
REPO = Path("/Users/kenken/dev/dsa-hk")

MARKETS = {
    "hk": (REPO / "hk_universe_200.json", REPO / "charts/hk200"),
    "us": (REPO / "charts/us200/us_top200_fresh.json", REPO / "charts/us200"),
    "jp": (None, REPO / "data/jp200"),
}
COLS = ("date", "open", "high", "low", "close", "volume")


def universe(upath, out_dir: Path):
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
    for p in sorted(out_dir.glob("*.json")):
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


def one(tk: str, safe: str, out_dir: Path, asof: str, days: int, source: str):
    from daily_sr import fetch_ohlc
    try:
        df, src, _name = fetch_ohlc(tk, days=days, source=source, asof=asof)
    except Exception as e:
        return False, f"{tk}: fetch failed {type(e).__name__} {e}"
    if df is None or len(df) == 0:
        return False, f"{tk}: no data"
    df = df[df["date"] <= asof] if "date" in df.columns else df
    if len(df) == 0:
        return False, f"{tk}: nothing at or before {asof}"
    rows = []
    for _, r in df.iterrows():
        d = r["date"]
        # daily_sr returns a pandas Timestamp for the date column; the rest of
        # the pipeline stores plain YYYY-MM-DD strings, so normalise here.
        ds = d.strftime("%Y-%m-%d") if hasattr(d, "strftime") else str(d)[:10]
        rows.append({**{c: float(r[c]) for c in COLS if c != "date"}, "date": ds})
    last = rows[-1]["date"]
    if last != asof:
        return False, f"{tk}: last bar {last} != {asof}"
    (out_dir / f"{safe}_ohlc.json").write_text(json.dumps(rows), encoding="utf-8")
    return True, f"{tk} ✓ {len(rows)} bars → {last}"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--asof", required=True)
    ap.add_argument("--markets", default="hk,us,jp")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--days", type=int, default=200)
    ap.add_argument("--source", default="yfinance", choices=["yfinance", "futu", "auto"])
    a = ap.parse_args()

    g_ok = g_fail = 0
    for mkt in [m.strip() for m in a.markets.split(",") if m.strip()]:
        if mkt not in MARKETS:
            continue
        upath, out_dir = MARKETS[mkt]
        uni = universe(upath, out_dir)
        if not uni:
            print(f"[{mkt}] universe 空 — skip")
            continue
        print(f"[{mkt}] {len(uni)} tickers ohlc -> {out_dir} (asof {a.asof})", flush=True)
        ok = fail = 0
        fails = []
        with ThreadPoolExecutor(max_workers=a.workers) as ex:
            futs = {ex.submit(one, tk, safe, out_dir, a.asof, a.days, a.source): tk for tk, safe in uni}
            for f in as_completed(futs):
                good, msg = f.result()
                ok += good
                if not good:
                    fail += 1
                    fails.append(msg)
        print(f"[{mkt}] ok={ok} fail={fail}")
        for m in fails[:6]:
            print("   ⚠", m)
        if fail > 6:
            print(f"   ... and {fail-6} more")
        g_ok += ok
        g_fail += fail

    print(f"\nTOTAL ok={g_ok} fail={g_fail}")
    sys.exit(0 if g_fail == 0 else 1)


if __name__ == "__main__":
    main()

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
import math
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

SKILL_DIR = Path("/Users/kenken/.minimax/skills/daily-sr-chart/scripts")
sys.path.insert(0, str(SKILL_DIR))
REPO = Path("/Users/kenken/dev/dsa-hk")

MARKETS = {
    "hk": (REPO / "hk_universe_200.json", REPO / "charts/hk200"),
    "us": (REPO / "charts/us200/us_published.json", REPO / "charts/us200"),
    "jp": (None, REPO / "data/jp200"),
}
COLS = ("date", "open", "high", "low", "close", "volume")
# Price columns must be present and positive for a bar to be a bar. Volume is
# NOT in this set: a genuine zero-volume session is a real session, and
# dropping those bars would silently punch holes in the chart's x-axis.
PRICE_COLS = ("open", "high", "low", "close")


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
    dropped = 0
    for _, r in df.iterrows():
        d = r["date"]
        # daily_sr returns a pandas Timestamp for the date column; the rest of
        # the pipeline stores plain YYYY-MM-DD strings, so normalise here.
        ds = d.strftime("%Y-%m-%d") if hasattr(d, "strftime") else str(d)[:10]
        vals = {}
        bad = False
        for c in COLS:
            if c == "date":
                continue
            v = r[c]
            # 2026-10-07: yfinance hands back a NaN close for 16 of the 200 HK
            # names on sessions it has a row for but no settled price (72800,
            # 7709, 2882 …). Those rows have a real DATE, so the old
            # `last != asof` guard saw 2026-10-06 and passed — and the file it
            # wrote carried a NaN candle. canvas-chart.js drew that candle, so
            # the page showed a bar the market never printed, and the "數據未
            # 更新" banner sat directly above a chart that claimed to be
            # fresher. daily_sr.fetch_ohlc rejects exactly these names
            # (last_bar=2026-10-02), which is why the analysis payload and the
            # chart disagreed. A bar is only a bar if it has a price.
            if c in PRICE_COLS:
                if v is None or not math.isfinite(float(v)) or float(v) <= 0:
                    bad = True
                    break
            elif v is None or not math.isfinite(float(v)) or float(v) < 0:
                # volume: must be a real number, but zero is allowed
                bad = True
                break
            vals[c] = float(v)
        if bad:
            dropped += 1
            continue
        rows.append({**vals, "date": ds})
    if not rows:
        return False, f"{tk}: every bar was null"
    last = rows[-1]["date"]
    note = f" (dropped {dropped} null bar(s))" if dropped else ""

    # 2026-10-07: writing the cleaned series is right, but only when it is at
    # least as long as what we already have. Yahoo intermittently answers a
    # request with a handful of rows (QRVO returned 5, ending 2026-09-14,
    # instead of 400). Writing that "cleaned" result is how a 400-bar series
    # becomes a 5-bar one — the null-bar fix silently turned into a
    # history-destroying pass. Fail-closed on the WRITE solves the NaN case
    # and creates this one; the two rules have to coexist, so a SHORTER
    # result is never allowed to overwrite a longer file.
    path = out_dir / f"{safe}_ohlc.json"
    have = None
    if path.exists():
        try:
            old = json.loads(path.read_text(encoding="utf-8"))
            have = len(old) if isinstance(old, list) else None
        except Exception:
            have = None
    if have is not None and len(rows) < have:
        return False, (f"{tk}: upstream returned only {len(rows)} bars, file has "
                       f"{have} — kept the longer series{note}")

    path.write_text(json.dumps(rows), encoding="utf-8")
    if last != asof:
        return False, f"{tk}: last bar {last} != {asof}{note}"
    return True, f"{tk} ✓ {len(rows)} bars → {last}{note}"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--asof", required=True,
                    help="default close date for every market")
    # 2026-10-07: markets do not share a calendar. Kenneth asked for HK/JP on
    # the 06 close and the US on the 05 close in the same run; a single --asof
    # silently stamped one date onto all three, which is how the chart/analysis
    # mismatch class of bug keeps returning. Per-market overrides are explicit
    # and default to --asof, so existing single-date calls are unchanged.
    ap.add_argument("--asof-hk", help="HK close date (default: --asof)")
    ap.add_argument("--asof-jp", help="JP close date (default: --asof)")
    ap.add_argument("--asof-us", help="US close date (default: --asof)")
    ap.add_argument("--markets", default="hk,us,jp")
    ap.add_argument("--workers", type=int, default=8)
    # 2026-10-07: default was 200, which produced files of exactly 200 bars —
    # and build_v2_signals.bars_from_public() drops any file with < 260 bars
    # because features() needs a 200-day average plus warm-up. Refreshing with
    # the default silently emptied the published signal set (178 -> 1) while
    # every page still looked fine, because the charts render happily with
    # fewer bars. Keep this above the 260 gate with room to spare.
    ap.add_argument("--days", type=int, default=400)
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
        mkt_asof = getattr(a, f"asof_{mkt}", None) or a.asof
        print(f"[{mkt}] {len(uni)} tickers ohlc -> {out_dir} (asof {mkt_asof})", flush=True)
        ok = fail = 0
        fails = []
        with ThreadPoolExecutor(max_workers=a.workers) as ex:
            futs = {ex.submit(one, tk, safe, out_dir, mkt_asof, a.days, a.source): tk for tk, safe in uni}
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

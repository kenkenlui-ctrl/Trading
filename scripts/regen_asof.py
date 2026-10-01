#!/usr/bin/env python3
"""regen_asof.py — refresh every existing ticker's chart data to one as-of date.

Why this instead of charts/*/batch_*.py: those scripts RE-SELECT the top 200
by turnover, which changes the universe. This only re-runs the 10-step
framework over the tickers the site already publishes, so the page set stays
identical and only the numbers move.

Each ticker is handed to the daily-sr-chart skill:
    daily_sr.py -t <tk> -o <png> --json <json> --source yfinance --asof <date>

--asof is the T-1 hard rule: daily_sr drops any bar after that date, so an
intraday or same-session row can never leak in. That matters when a market is
still open at run time (the US session was live when this was written).

Usage:
    python3 scripts/regen_asof.py --asof 2026-09-30 [--markets hk,us,jp] [--workers 8]
"""
from __future__ import annotations
import argparse
import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

REPO = Path("/Users/kenken/dev/dsa-hk")
SKILL = Path("/Users/kenken/.minimax/skills/daily-sr-chart/scripts/daily_sr.py")

# market -> (universe json path, output dir, file suffix used in the universe)
# JP ships no universe file: the 200 data/jp200/<code>_T.json files ARE the
# universe, so it is derived from the filenames instead.
MARKETS = {
    "hk": (REPO / "hk_universe_200.json", REPO / "charts/hk200", "hk"),
    "us": (REPO / "charts/us200/us_top200_fresh.json", REPO / "charts/us200", "us"),
    "jp": (None, REPO / "data/jp200", "jp"),
}


def universe_from_dir(out_dir: Path) -> list[tuple[str, str]]:
    """Derive (ticker, safe) from existing <code>_T.json / <CODE>.json files."""
    out = []
    for p in sorted(out_dir.glob("*.json")):
        if p.name.endswith("_ohlc.json"):
            continue
        safe = p.stem
        if safe.endswith("_T"):          # 1332_T -> 1332.T
            out.append((safe[:-2] + ".T", safe))
        elif safe.endswith("_HK"):       # 00001_HK -> 00001.HK
            out.append((safe[:-3] + ".HK", safe))
        else:                             # AAPL -> AAPL
            out.append((safe, safe))
    return out


def load_universe(path: Path) -> list[tuple[str, str]]:
    """-> [(display_ticker, safe_name)]"""
    if not path.exists():
        return []
    raw = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(raw, dict):
        raw = raw.get("symbols") or raw.get("tickers") or []
    out = []
    for item in raw:
        tk = item if isinstance(item, str) else item[1] if isinstance(item, (list, tuple)) and len(item) > 1 else item.get("ticker", "")
        if not tk:
            continue
        # 00700.HK -> 00700_HK ; 1306.T stays 1306_T (already how the site names it)
        safe = tk.replace(".", "_").replace("/", "_")
        out.append((tk, safe))
    return out


def run_one(tk: str, safe: str, out_dir: Path, asof: str, source: str) -> tuple[bool, str]:
    png = out_dir / f"{safe}.png"
    js = out_dir / f"{safe}.json"
    cmd = [
        sys.executable, str(SKILL),
        "-t", tk, "-o", str(png),
        "--json", str(js),
        "--source", source,
        "--asof", asof,
    ]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    except subprocess.TimeoutExpired:
        return False, f"{tk}: timeout"
    if p.returncode != 0:
        return False, f"{tk}: rc={p.returncode} {p.stderr.strip()[-120:]}"
    try:
        d = json.loads(js.read_text(encoding="utf-8"))
        got = d.get("last_bar", {}).get("date")
    except Exception as e:
        return False, f"{tk}: JSON unreadable {e}"
    if got != asof:
        return False, f"{tk}: last_bar={got} (expected {asof})"
    return True, f"{tk} ✓ {got}"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--asof", required=True, help="YYYY-MM-DD close date")
    ap.add_argument("--markets", default="hk,us,jp")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--source", default="yfinance",
                    choices=["yfinance", "futu", "auto"])
    a = ap.parse_args()

    grand_ok = grand_fail = 0
    for mkt in [m.strip() for m in a.markets.split(",") if m.strip()]:
        if mkt not in MARKETS:
            print(f"unknown market {mkt}")
            continue
        upath, out_dir, _ = MARKETS[mkt]
        uni = load_universe(upath) if upath else universe_from_dir(out_dir)
        if not uni:
            print(f"[{mkt}] universe 為空 — skipped")
            continue
        out_dir.mkdir(parents=True, exist_ok=True)
        print(f"[{mkt}] {len(uni)} tickers -> {out_dir}  (asof {a.asof}, source {a.source})", flush=True)
        ok = fail = 0
        fails = []
        with ThreadPoolExecutor(max_workers=a.workers) as ex:
            futs = {ex.submit(run_one, tk, safe, out_dir, a.asof, a.source): tk
                    for tk, safe in uni}
            for f in as_completed(futs):
                good, msg = f.result()
                if good:
                    ok += 1
                else:
                    fail += 1
                    fails.append(msg)
        print(f"[{mkt}] ok={ok} fail={fail}")
        for m in fails[:8]:
            print("   ⚠", m)
        if fail > 8:
            print(f"   ... and {fail - 8} more")
        grand_ok += ok
        grand_fail += fail

    print(f"\nTOTAL ok={grand_ok} fail={grand_fail}")
    sys.exit(0 if grand_fail == 0 else 1)


if __name__ == "__main__":
    main()

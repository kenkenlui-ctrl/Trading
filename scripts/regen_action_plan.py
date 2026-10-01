"""regen_action_plan.py — Re-generate action_plan for all tickers using current daily_sr.

Applies the latest regime + volume gates to existing chart.json + PNG files.
Use after upgrading daily_sr.py logic to bring cached action_plan up to date.

Usage:
    python3 scripts/regen_action_plan.py HK
    python3 scripts/regen_action_plan.py US
    python3 scripts/regen_action_plan.py JP
"""
import sys
import json
import time
import argparse
import concurrent.futures as cf
from pathlib import Path

sys.path.insert(0, "/Users/kenken/.minimax/skills/daily-sr-chart/scripts")
from daily_sr import fetch_ohlc, run

MARKET_PATHS = {
    "HK": {
        "universe": Path("/Users/kenken/dev/dsa-hk/hk_universe_200.json"),
        "src_dir": Path("/Users/kenken/dev/dsa-hk/charts/hk200"),
        "src_oanda": None,
    },
    "US": {
        "universe": Path("/Users/kenken/dev/dsa-hk/charts/us200/us_top200_fresh.json"),
        "src_dir": Path("/Users/kenken/dev/dsa-hk/charts/us200"),
        "src_oanda": None,
    },
    "JP": {
        "universe": Path("/Users/kenken/dev/dsa-hk/jp_universe_200.json"),
        "src_dir": Path("/Users/kenken/dev/dsa-hk/data/jp200"),
        "src_oanda": None,
    },
}


def regen_one(market: str, ticker: str, src_dir: Path, asof: str | None = None,
               source: str = "auto") -> tuple:
    """Re-fetch OHLC + compute action_plan + update chart.json with new regime/volume gates."""
    safe = ticker.replace(".", "_")
    png = src_dir / f"{safe}.png"
    json_path = src_dir / f"{safe}.json"
    if not json_path.exists():
        return ("skip", f"{ticker}: no source json")
    try:
        df, src, name = fetch_ohlc(ticker, days=200, source=source, asof=asof)
        if df is None or len(df) < 28:
            return ("skip", f"{ticker}: df insufficient ({len(df) if df is not None else 0} bars)")
        # Run the snapshot generation (writes chart.png)
        snap = run(ticker=ticker, out_path=str(png), days=200, source=source, asof=asof)
        if not snap:
            return ("fail", f"{ticker}: run() returned empty")
        # CRITICAL: Write the fresh snapshot back to chart.json so downstream
        # build_dashboard.py picks up the new adx/regime fields + new action_plan.
        # (run() only writes the PNG; the CLI also writes JSON only with --json flag)
        json_path.write_text(json.dumps(snap, ensure_ascii=False, indent=2), encoding="utf-8")
        ap = snap.get("action_plan", {})
        return ("ok", f"{ticker}: regime={snap.get('regime')} verdict={ap.get('verdict')}")
    except Exception as e:
        return ("fail", f"{ticker}: {e}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("market", choices=list(MARKET_PATHS.keys()))
    parser.add_argument("--workers", type=int, default=4)
    # 2026-09-29: asof=None makes trim_to_t_minus_1 strip today's bar as if it
    # were intraday. It has no exchange calendar, so it cannot know a market
    # has already closed. Pass the target date explicitly to keep it.
    parser.add_argument("--asof", default=None,
                        help="keep bars up to and including this date, e.g. 2026-09-29")
    parser.add_argument("--limit", type=int, default=None)
    # 2026-09-29: futu's daily bar lagged 09-28 for ~half the HK/JP
    # universe while yfinance already had 09-29. Same exchange, same
    # day, different feed freshness. Pin the source per run.
    parser.add_argument("--source", default="auto", choices=["auto","yfinance","futu"])
    args = parser.parse_args()

    cfg = MARKET_PATHS[args.market]
    universe = json.load(open(cfg["universe"]))
    if args.limit:
        universe = universe[:args.limit]
    print(f"=== Regenerate {args.market} action_plan for {len(universe)} tickers "
          f"(asof={args.asof or 'auto/T-1'}) ===", flush=True)

    ok = fail = skip = 0
    t0 = time.time()
    with cf.ThreadPoolExecutor(max_workers=args.workers) as ex:
        futures = {ex.submit(regen_one, args.market, tk, cfg["src_dir"], args.asof, args.source): tk
               for tk in universe}
        for fut in cf.as_completed(futures):
            tk = futures[fut]
            try:
                status, msg = fut.result()
            except Exception as e:
                status, msg = "fail", f"{tk}: {e}"
            if status == "ok":
                ok += 1
            elif status == "skip":
                skip += 1
            else:
                fail += 1
                if fail <= 5:
                    print(f"  ⚠ {msg}", flush=True)
            if (ok + fail + skip) % 25 == 0:
                elapsed = time.time() - t0
                print(f"  [{ok+fail+skip}/{len(universe)}] ok={ok} fail={fail} skip={skip} {elapsed:.0f}s", flush=True)
    print(f"\nDone: ok={ok}, fail={fail}, skip={skip} in {time.time()-t0:.0f}s", flush=True)


if __name__ == "__main__":
    main()

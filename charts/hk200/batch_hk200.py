#!/usr/bin/env python3
"""
batch_hk200.py — Run daily-sr-chart on the existing 200 HK tickers in dsa-hk/hk_universe_200.json.

Uses pre-curated list (NOT get_security_list, which hangs).
Pipeline:
  1. Load 200 tickers from hk_universe_200.json
  2. Get 5-day K-line for each, compute avg turnover
  3. Sort by 5-day avg turnover (desc) → take top 200 (or all)
  4. Run daily_sr.py for each → PNG + JSON
  5. Build summary CSV
"""
from __future__ import annotations
import json
import logging
import subprocess
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd
from concurrent.futures import ThreadPoolExecutor, as_completed

OUT_DIR = Path("/Users/kenken/dev/dsa-hk/charts/hk200")
OUT_DIR.mkdir(parents=True, exist_ok=True)
LOG_FILE = OUT_DIR / "progress.log"
SOURCE_LIST = Path("/Users/kenken/dev/dsa-hk/hk_universe_200.json")

# Logger
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[logging.FileHandler(LOG_FILE, mode="a"), logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("hk200")


def to_futu(ticker: str) -> str:
    """07709.HK → HK.07709"""
    t = ticker.strip()
    if t.startswith("HK."):
        return t
    if t.endswith(".HK"):
        digits = t[:-3]
        return f"HK.{int(digits):05d}"
    return t


def get_5day_turnover(code_futu: str) -> float:
    """Get 5-day average turnover for a single code via futu."""
    from futu import OpenQuoteContext, KLType, RET_OK
    end = date.today()
    start = end - timedelta(days=10)
    ctx = OpenQuoteContext(host="127.0.0.1", port=11111)
    try:
        ret, kl, _ = ctx.request_history_kline(
            code_futu, start=start.isoformat(), end=end.isoformat(),
            ktype=KLType.K_DAY, max_count=10,
        )
        if ret == RET_OK and "turnover" in kl.columns:
            t = pd.to_numeric(kl["turnover"], errors="coerce").dropna().tail(5)
            if len(t) > 0:
                return float(t.mean())
    except Exception as e:
        log.debug(f"fail {code_futu}: {e}")
    finally:
        ctx.close()
    return 0.0


def get_5day_turnover_parallel(codes: list[str], max_workers: int = 6) -> list[tuple[str, float]]:
    """Parallel version using thread pool."""
    results = [None] * len(codes)
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        future_map = {pool.submit(get_5day_turnover, c): i for i, c in enumerate(codes)}
        done = 0
        for fut in as_completed(future_map):
            i = future_map[fut]
            try:
                results[i] = (codes[i], fut.result())
            except Exception:
                results[i] = (codes[i], 0.0)
            done += 1
            if done % 20 == 0:
                log.info(f"  K-line progress: {done}/{len(codes)}")
    return [r for r in results if r is not None]


def run_analysis(ticker: str) -> dict | None:
    """Run daily_sr.py for one ticker."""
    safe = ticker.replace(".", "_")
    png = OUT_DIR / f"{safe}.png"
    jsn = OUT_DIR / f"{safe}.json"
    try:
        result = subprocess.run(
            [
                "python3",
                "/Users/kenken/.minimax/skills/daily-sr-chart/scripts/daily_sr.py",
                "-t", ticker,
                "-o", str(png),
                "--json", str(jsn),
            ],
            capture_output=True, text=True, timeout=60,
        )
        if result.returncode == 0 and jsn.exists():
            return json.loads(jsn.read_text(encoding="utf-8"))
    except Exception as e:
        log.warning(f"analysis failed for {ticker}: {e}")
    return None


def main():
    log.info("=" * 60)
    log.info(f"HK 200 batch start @ {datetime.now().isoformat()}")
    log.info("=" * 60)

    # Step 1: load 200 tickers
    log.info(f"STEP 1: load 200 tickers from {SOURCE_LIST}")
    with open(SOURCE_LIST) as f:
        tickers_raw = json.load(f)
    log.info(f"  loaded {len(tickers_raw)} tickers")

    # Convert to futu codes
    futu_codes = [to_futu(t) for t in tickers_raw]
    # Map back
    code_map = dict(zip(futu_codes, tickers_raw))

    # Step 2: get 5-day avg turnover
    log.info("STEP 2: get 5-day avg turnover (parallel, ~3-5 min)")
    t0 = time.time()
    turnover_data = get_5day_turnover_parallel(futu_codes, max_workers=6)
    log.info(f"  done in {time.time() - t0:.1f}s — got {len([t for _, t in turnover_data if t > 0])} non-zero")

    # Sort by turnover desc
    turnover_data.sort(key=lambda x: -x[1])
    top200 = [(code_map[c], t) for c, t in turnover_data if t > 0]
    log.info(f"  top 5 by 5d avg turnover: {[(ticker_user, f'{t/1e6:.1f}M') for ticker_user, t in top200[:5]]}")

    # Save turnover ranking
    (OUT_DIR / "turnover_ranking.json").write_text(
        json.dumps([{"ticker": t, "avg_turnover_5d_hkd": v} for t, v in top200], indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    # Step 3: run analysis on each
    log.info("STEP 3: run daily-sr-chart on each ticker")
    summary_rows = []
    total = len(top200)
    for i, (ticker, _) in enumerate(top200, 1):
        snap = run_analysis(ticker)
        if snap:
            summary_rows.append({
                "rank": i,
                "ticker": ticker,
                "name": snap.get("name", ""),
                "last": snap["last_bar"]["C"],
                "chg_pct": snap["last_bar"]["chg_pct"],
                "phase": snap["phase"],
                "peak_price": snap["peak"]["price"],
                "peak_date": snap["peak"]["date"],
                "trough_price": snap["trough"]["price"],
                "trough_date": snap["trough"]["date"],
                "drop_from_peak_pct": snap["drop_from_peak_pct"],
                "in_box_pct": snap["position"]["in_box_pct"],
                "nearest_s": snap["position"]["nearest_support"],
                "nearest_r": snap["position"]["nearest_resistance"],
                "kline": snap["kline_today"]["type"],
                "bias_short": snap["bias"]["short"]["bias"],
                "bias_mid": snap["bias"]["mid"]["bias"],
                "bias_long": snap["bias"]["long"]["bias"],
                "s1": snap["supports"][0]["level"] if snap["supports"] else None,
                "r1": snap["resistances"][0]["level"] if snap["resistances"] else None,
                "r3": next((r["level"] for r in snap["resistances"] if r["label"].startswith("R3")), None),
            })
        if i % 10 == 0:
            log.info(f"  analysis progress: {i}/{total} done ({len(summary_rows)} successful)")
            # Save intermediate
            pd.DataFrame(summary_rows).to_csv(OUT_DIR / "top200_summary.csv", index=False)

    df_summary = pd.DataFrame(summary_rows)
    df_summary.to_csv(OUT_DIR / "top200_summary.csv", index=False)
    log.info(f"summary → {OUT_DIR / 'top200_summary.csv'}")

    # Highlights
    log.info("=" * 60)
    log.info("HIGHLIGHTS (framework-based)")
    log.info("=" * 60)

    if not df_summary.empty:
        log.info(f"Phase breakdown: {df_summary['phase'].value_counts().to_dict()}")

        # Closest to buy setup
        candidates = df_summary[
            (df_summary["phase"] == "base_building")
            & (df_summary["in_box_pct"] < 50)
        ].sort_values("drop_from_peak_pct")
        log.info(f"\nTop 10 base_building near support (potential buy setups):")
        log.info(candidates[["rank", "ticker", "name", "last", "in_box_pct", "nearest_s"]].head(10).to_string())

        # Top drops
        log.info(f"\nTop 10 deepest drops from peak:")
        log.info(df_summary.nlargest(10, "drop_from_peak_pct")[["rank", "ticker", "name", "last", "drop_from_peak_pct", "phase"]].to_string())

        # Downtrend active count
        log.info(f"\nDowntrend_active: {len(df_summary[df_summary['phase'] == 'downtrend_active'])} stocks")

    log.info("=" * 60)
    log.info(f"Batch complete @ {datetime.now().isoformat()}")
    log.info(f"Outputs: {OUT_DIR}")
    log.info("=" * 60)


if __name__ == "__main__":
    main()

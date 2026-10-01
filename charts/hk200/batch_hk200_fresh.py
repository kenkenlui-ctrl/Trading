#!/usr/bin/env python3
"""
batch_hk200_fresh.py — Generate FRESH HK top 200 by turnover, then run analysis.

Pipeline:
  1. Use Tencent gtimg to fetch all HK listed stocks with current turnover (fast, parallel)
  2. Sort by today's turnover, take top 200 directly (today ≈ 5d avg proxy)
  3. Run daily-sr-chart on each with --source yfinance (futu OpenD unstable today)
  4. Save new universe JSON + summary + individual charts
"""
from __future__ import annotations
import json
import logging
import re
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from urllib.request import urlopen, Request
from urllib.error import URLError

import pandas as pd

# Paths
OUT_DIR = Path("/Users/kenken/dev/dsa-hk/charts/hk200")
OUT_DIR.mkdir(parents=True, exist_ok=True)
LOG_FILE = OUT_DIR / "progress.log"
NEW_UNIVERSE_FILE = OUT_DIR / "hk_top200_fresh.json"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[logging.FileHandler(LOG_FILE, mode="a"), logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("hk200fresh")

GTIMG_BATCH_URL = "https://qt.gtimg.cn/q={codes}"
GTIMG_BATCH_SIZE = 50
GTIMG_WORKERS = 16
HK_CODE_RANGE = range(1, 10000)


def fetch_gtimg_batch(codes: list[str]) -> dict[str, dict]:
    url = GTIMG_BATCH_URL.format(codes=",".join(f"hk{c}" for c in codes))
    try:
        req = Request(url, headers={
            "User-Agent": "Mozilla/5.0",
            "Referer": "https://stockapp.finance.qq.com/",
        })
        with urlopen(req, timeout=8) as resp:
            raw = resp.read().decode("gbk", errors="ignore")
    except (URLError, Exception):
        return {}

    out = {}
    for line in raw.split("\n"):
        m = re.match(r'v_hk(\d{5})="([^"]*)";', line)
        if not m:
            continue
        code = m.group(1)
        fields = m.group(2).split("~")
        if len(fields) < 38:
            continue
        try:
            price = float(fields[3]) if fields[3] else 0.0
            volume = float(fields[6]) if fields[6] else 0.0
            turnover = float(fields[37]) if fields[37] else 0.0
            name = fields[1] if len(fields[1]) < 20 else ""
            if turnover > 0 and price > 0:
                out[code] = {
                    "code": f"{code}.HK",
                    "name": name,
                    "price": price,
                    "turnover_today_hkd": turnover,
                }
        except (ValueError, IndexError):
            continue
    return out


def step1_fetch_all_hk() -> list[dict]:
    log.info("STEP 1: fetch all HK via Tencent gtimg")
    all_codes = [f"{c:05d}" for c in HK_CODE_RANGE]
    batches = [all_codes[i:i + GTIMG_BATCH_SIZE] for i in range(0, len(all_codes), GTIMG_BATCH_SIZE)]
    log.info(f"  total batches: {len(batches)} ({len(all_codes)} codes)")

    results = {}
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=GTIMG_WORKERS) as pool:
        futures = {pool.submit(fetch_gtimg_batch, batch): i for i, batch in enumerate(batches)}
        done = 0
        for fut in as_completed(futures):
            try:
                res = fut.result()
                results.update(res)
            except Exception:
                pass
            done += 1
            if done % 50 == 0:
                log.info(f"  gtimg: {done}/{len(batches)} batches, {len(results)} stocks so far")

    log.info(f"  done in {time.time() - t0:.1f}s — {len(results)} HK stocks with turnover today")
    all_stocks = list(results.values())
    all_stocks.sort(key=lambda x: -x["turnover_today_hkd"])
    return all_stocks


def step2_run_analysis(tickers: list[dict], source: str = "yfinance") -> list[dict]:
    log.info(f"STEP 2: run daily-sr-chart on {len(tickers)} tickers (source={source})")
    summary_rows = []
    total = len(tickers)
    for i, stock in enumerate(tickers, 1):
        ticker = stock["code"]
        snap = run_one(ticker, source)
        if snap:
            summary_rows.append({
                "rank": i,
                "ticker": ticker,
                "name": snap.get("name") or stock.get("name", ""),
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
                "turnover_today_hkd": stock.get("turnover_today_hkd", 0),
            })
        if i % 10 == 0:
            log.info(f"  analysis: {i}/{total} ({len(summary_rows)} ok)")
            pd.DataFrame(summary_rows).to_csv(OUT_DIR / "top200_summary.csv", index=False)

    df = pd.DataFrame(summary_rows)
    df.to_csv(OUT_DIR / "top200_summary.csv", index=False)
    log.info(f"  summary → {OUT_DIR / 'top200_summary.csv'}")
    return summary_rows


def run_one(ticker: str, source: str) -> dict | None:
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
                "--source", source,
            ],
            capture_output=True, text=True, timeout=60,
        )
        if result.returncode == 0 and jsn.exists():
            return json.loads(jsn.read_text(encoding="utf-8"))
    except Exception as e:
        log.warning(f"failed {ticker}: {e}")
    return None


def main():
    log.info("=" * 60)
    log.info(f"HK 200 FRESH batch start @ {datetime.now().isoformat()}")
    log.info("=" * 60)

    all_stocks = step1_fetch_all_hk()
    if not all_stocks:
        log.error("no stocks from gtimg — aborting")
        return
    log.info(f"  top 5 by today turnover: {[(s['code'], s['name']) for s in all_stocks[:5]]}")

    # Take top 200 by today's turnover (5d avg proxy)
    top200 = all_stocks[:200]
    universe_codes = [s["code"] for s in top200]
    NEW_UNIVERSE_FILE.write_text(
        json.dumps(universe_codes, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    log.info(f"  new universe saved → {NEW_UNIVERSE_FILE} ({len(universe_codes)} tickers)")

    # Run analysis (yfinance source — futu OpenD unstable today)
    summary = step2_run_analysis(top200, source="yfinance")

    # Highlights
    log.info("=" * 60)
    log.info("HIGHLIGHTS")
    log.info("=" * 60)
    if summary:
        df = pd.DataFrame(summary)
        log.info(f"Total successful: {len(df)} / 200")
        log.info(f"Phase: {df['phase'].value_counts().to_dict()}")

        # Buy setups
        buys = df[(df["phase"] == "base_building") & (df["in_box_pct"] < 50)]
        if not buys.empty:
            log.info(f"\nBuy setups (base_building, near support): {len(buys)} stocks")
            log.info(buys[["ticker", "name", "last", "in_box_pct", "nearest_s"]].head(15).to_string())
        else:
            log.info("\nNo clean buy setups today")

        # Top drops
        log.info(f"\nTop 15 drops from peak:")
        log.info(df.nlargest(15, "drop_from_peak_pct")[["ticker", "name", "last", "drop_from_peak_pct", "phase"]].to_string())

        # Top gainers today
        gainers = df.nlargest(10, "chg_pct")
        log.info(f"\nTop 10 gainers today:")
        log.info(gainers[["ticker", "name", "last", "chg_pct", "phase"]].to_string())

    log.info("=" * 60)
    log.info(f"Batch complete @ {datetime.now().isoformat()}")
    log.info("=" * 60)


if __name__ == "__main__":
    main()

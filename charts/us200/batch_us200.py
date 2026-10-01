#!/usr/bin/env python3
"""
batch_us200.py — Top 200 US stocks by 5-day average turnover, run analysis.

Pipeline:
  1. Load pool of ~500 US tickers (dsa-hk 200 + extras)
  2. yfinance bulk download 5d OHLCV for all
  3. Compute 5d avg turnover (close × volume), sort desc, take top 200
  4. Run daily-sr-chart on each (yfinance source)
  5. Save new universe JSON + summary + individual charts
  6. Output to /Users/kenken/dev/dsa-hk/charts/us200/
"""
from __future__ import annotations
import json
import logging
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

import pandas as pd
import yfinance as yf

OUT_DIR = Path("/Users/kenken/dev/dsa-hk/charts/us200")
OUT_DIR.mkdir(parents=True, exist_ok=True)
LOG_FILE = OUT_DIR / "progress.log"
NEW_UNIVERSE_FILE = OUT_DIR / "us_top200_fresh.json"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[logging.FileHandler(LOG_FILE, mode="a"), logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("us200fresh")

# Extra popular US tickers (S&P 500 main + Nasdaq-100 + popular small/mid cap)
EXTRA_US = [
    # Mega caps
    "BRK.B", "JPM", "V", "MA", "BAC", "WFC", "MS", "GS", "AXP", "BLK",
    "C", "SCHW", "USB", "PNC", "TFC", "COF", "BX", "KKR", "APO",
    # Tech
    "CRWD", "PANW", "FTNT", "ZS", "NET", "DDOG", "SNOW", "MDB", "TEAM",
    "ADBE", "CRM", "ORCL", "CSCO", "IBM", "TXN", "ADI", "MPWR", "ON", "TXN",
    "QCOM", "AVGO", "NXPI", "SWKS", "QRVO", "ENTG",
    # Consumer
    "WMT", "COST", "TGT", "HD", "LOW", "MCD", "SBUX", "NKE", "CMG", "YUM",
    "DPZ", "CMG", "LULU", "ETSY", "EBAY", "SHOP",
    # Energy
    "XOM", "CVX", "COP", "SLB", "OXY", "EOG", "MPC", "VLO", "PSX", "DVN",
    # Healthcare / Pharma
    "UNH", "JNJ", "LLY", "PFE", "ABBV", "MRK", "TMO", "ABT", "DHR", "BMY",
    "AMGN", "GILD", "REGN", "VRTX", "BIIB", "MRNA", "ISRG", "SYK", "BSX",
    "MDT", "ZTS", "ELV", "CI", "HUM", "CNC", "MOH", "EL",
    # Industrial / Defense
    "BA", "CAT", "DE", "GE", "HON", "RTX", "LMT", "NOC", "GD", "TDG",
    "ETN", "PH", "EMR", "ITW", "CMI", "PCAR", "URI",
    # Real estate
    "AMT", "PLD", "CCI", "EQIX", "PSA", "O", "WELL", "SPG", "EXR", "AVB",
    # Consumer staples
    "PG", "KO", "PEP", "PM", "MO", "MDLZ", "CL", "KMB", "GIS", "K",
    "HSY", "SJM", "CAG", "HRL", "TSN", "CPB", "MKC",
    # Utilities
    "NEE", "SO", "DUK", "CEG", "VST", "AEP", "D", "SRE", "EXC", "XEL",
    # Telecom / Media
    "T", "VZ", "TMUS", "CMCSA", "DIS", "NFLX", "WBD", "PARA", "LYV", "FOXA",
    # EV / Auto
    "RIVN", "LCID", "F", "GM", "STLA",
    # Crypto / fintech
    "COIN", "MSTR", "HOOD", "PYPL", "SQ", "AFRM", "SOFI", "NU",
    # AI / Robotics recent
    "ARM", "SMCI", "PLTR", "AI", "BBAI", "SOUN", "UPST", "RGTI", "RKLB",
    # Meme / volatile
    "GME", "AMC", "BBBY", "DJT", "DXY",
    # China ADRs
    "BABA", "JD", "PDD", "BIDU", "NIO", "XPEV", "LI", "TME", "BILI", "TAL",
    "YUMC", "NTES",
    # Pharma / biotech smaller
    "BMRN", "ALNY", "RARE", "BLUE", "CRSP", "EDIT", "NTLA", "BEAM",
    # Energy transition
    "ENPH", "FSLR", "NEE", "PLUG", "FCEL", "BE", "BLDP",
    # Recent IPOs
    "RDDT", "APP", "DUOL", "CAVA", "BIRK", "VSTO",
]


def load_pool() -> list[str]:
    """Load the dsa-hk 200 + extras, dedupe."""
    with open("/Users/kenken/dev/dsa-hk/us_universe_200.json") as f:
        base = json.load(f)
    pool = list(dict.fromkeys(base + EXTRA_US))  # dedupe preserving order
    log.info(f"pool: {len(pool)} US tickers (base {len(base)} + extras {len(EXTRA_US)})")
    return pool


def fetch_turnover_yahoo(ticker: str, period: str = "5d") -> tuple[str, float]:
    """Fetch 5d OHLCV for one ticker, compute avg daily turnover (close*volume)."""
    try:
        t = yf.Ticker(ticker)
        hist = t.history(period=period, auto_adjust=False, raise_errors=False)
        if hist is None or hist.empty or len(hist) < 1:
            return (ticker, 0.0)
        # turnover = close × volume
        hist = hist.dropna(subset=["Close", "Volume"])
        if hist.empty:
            return (ticker, 0.0)
        turnover = (hist["Close"] * hist["Volume"]).mean()
        return (ticker, float(turnover))
    except Exception as e:
        log.debug(f"fail {ticker}: {e}")
        return (ticker, 0.0)


def fetch_all_turnover(tickers: list[str], max_workers: int = 8) -> list[tuple[str, float]]:
    """Parallel fetch 5d turnover for all tickers."""
    log.info(f"STEP 1: yfinance fetch 5d turnover for {len(tickers)} tickers")
    results = [None] * len(tickers)
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(fetch_turnover_yahoo, t): i for i, t in enumerate(tickers)}
        done = 0
        for fut in as_completed(futures):
            i = futures[fut]
            try:
                results[i] = fut.result()
            except Exception:
                results[i] = (tickers[i], 0.0)
            done += 1
            if done % 50 == 0:
                nonzero = sum(1 for r in results if r and r[1] > 0)
                log.info(f"  progress: {done}/{len(tickers)} ({nonzero} non-zero) in {time.time()-t0:.1f}s")
    log.info(f"  done in {time.time()-t0:.1f}s")
    return [r for r in results if r is not None]


def run_one(ticker: str) -> dict | None:
    """Run daily-sr-chart for one ticker (yfinance source)."""
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
                "--source", "yfinance",
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
    log.info(f"US 200 FRESH batch start @ {datetime.now().isoformat()}")
    log.info("=" * 60)

    pool = load_pool()
    turnover_data = fetch_all_turnover(pool, max_workers=10)
    # Filter to non-zero
    turnover_data = [r for r in turnover_data if r[1] > 0]
    # Sort by turnover desc
    turnover_data.sort(key=lambda x: -x[1])
    log.info(f"  got {len(turnover_data)} tickers with non-zero 5d turnover")
    top10 = ", ".join(f"{t}={v/1e9:.2f}B" for t, v in turnover_data[:10])
    log.info(f"  top 10 by 5d turnover: {top10}")

    top200 = turnover_data[:200]
    top200_codes = [t for t, _ in top200]

    # Save the fresh universe
    NEW_UNIVERSE_FILE.write_text(
        json.dumps(top200_codes, indent=2),
        encoding="utf-8",
    )
    log.info(f"  universe saved → {NEW_UNIVERSE_FILE} ({len(top200_codes)} tickers)")

    # Step 3: run analysis on each
    log.info(f"STEP 2: run daily-sr-chart on {len(top200_codes)} tickers (yfinance source)")
    summary_rows = []
    total = len(top200_codes)
    for i, (ticker, _) in enumerate(top200, 1):
        snap = run_one(ticker)
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
                "turnover_5d_usd": dict(turnover_data)[ticker],
            })
        if i % 20 == 0:
            log.info(f"  analysis: {i}/{total} ({len(summary_rows)} ok)")
            pd.DataFrame(summary_rows).to_csv(OUT_DIR / "top200_summary.csv", index=False)

    df = pd.DataFrame(summary_rows)
    df.to_csv(OUT_DIR / "top200_summary.csv", index=False)
    log.info(f"  summary → {OUT_DIR / 'top200_summary.csv'}")

    # Highlights
    log.info("=" * 60)
    log.info("HIGHLIGHTS")
    log.info("=" * 60)
    if not df.empty:
        log.info(f"Total successful: {len(df)} / 200")
        log.info(f"Phase: {df['phase'].value_counts().to_dict()}")
        # Buy setups
        buys = df[(df["phase"] == "base_building") & (df["in_box_pct"] < 50)]
        if not buys.empty:
            log.info(f"\nBuy setups (base_building near support): {len(buys)} stocks")
            log.info(buys[["ticker", "name", "last", "in_box_pct", "nearest_s"]].head(15).to_string())
        # Uptrend
        up = df[df["phase"] == "uptrend"]
        if not up.empty:
            log.info(f"\nUptrend: {len(up)} stocks")
            log.info(up[["ticker", "name", "last", "chg_pct", "drop_from_peak_pct"]].head(20).to_string())
        # Top drops
        log.info(f"\nTop 15 drops from peak:")
        log.info(df.nlargest(15, "drop_from_peak_pct")[["ticker", "name", "last", "drop_from_peak_pct", "phase"]].to_string())

    log.info("=" * 60)
    log.info(f"Batch complete @ {datetime.now().isoformat()}")
    log.info("=" * 60)


if __name__ == "__main__":
    main()

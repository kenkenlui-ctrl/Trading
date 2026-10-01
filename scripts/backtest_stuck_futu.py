"""backtest_stuck_futu.py — Backtest only the 120 HK.NNNNN tickers that yfinance can't fetch.

Uses Futu OpenD as primary source for these. Sequential (1 worker) to avoid
flooding futu OpenD.
"""
import sys
import json
import time
from pathlib import Path
sys.path.insert(0, "/Users/kenken/dev/dsa-hk/scripts")

import pandas as pd
from futu import OpenQuoteContext, RET_OK, KLType

REPO = Path("/Users/kenken/dev/dsa-hk")
HK_OUT = REPO / "data" / "jp200"  # not used; just for naming
HK_SRC = Path("/Users/kenken/dev/dsa-hk/charts/hk200")
OUT_DIR = Path("/Users/kenken/dev/dsa-hk/data/backtest_out")
OUT_DIR.mkdir(parents=True, exist_ok=True)

# Import the backtest process_one from existing script
sys.path.insert(0, str(REPO / "scripts"))
from backtest_hk_us import process_one  # noqa: E402


def main():
    # Find HK.NNNNN tickers missing backtest files
    missing = []
    for fp in sorted(HK_SRC.glob("*.json")):
        if "_ohlc" in fp.name:
            continue
        try:
            d = json.load(open(fp))
        except Exception:
            continue
        if not isinstance(d, dict):
            continue
        tk = d.get("ticker", "")
        if not tk.startswith("HK."):
            continue
        # Convert HK.NNNNN → NNNNN.HK for backtest key
        safe = tk[3:] + "_HK"
        out_path = OUT_DIR / f"{safe}.json"
        if not out_path.exists():
            missing.append(tk)

    print(f"Missing HK.NNNNN backtest files: {len(missing)}", flush=True)
    print(f"First 5: {missing[:5]}", flush=True)

    ok = 0
    fail = 0
    t0 = time.time()
    for i, tk in enumerate(missing):
        try:
            ok_flag = process_one(tk)
            if ok_flag:
                ok += 1
            else:
                fail += 1
        except Exception as e:
            fail += 1
            print(f"  ⚠ {tk}: {e}", flush=True)
        if (i+1) % 10 == 0:
            elapsed = time.time() - t0
            print(f"  [{i+1}/{len(missing)}] ok={ok} fail={fail} {elapsed:.0f}s", flush=True)

    print(f"\nDone: ok={ok}, fail={fail} in {time.time()-t0:.0f}s", flush=True)


if __name__ == "__main__":
    main()

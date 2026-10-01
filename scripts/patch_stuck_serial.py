"""patch_stuck_serial.py — Patch the 100+ HK tickers the batch run skipped.

Why batch failed: Tencent throttling kills qt calls when >3 workers run in
parallel. Individual calls work because each gets fresh quota.

Run with 1 worker + 0.3s sleep between calls. ~5 min total for ~150 tickers.
"""
import sys
import time
import json
from pathlib import Path

sys.path.insert(0, "/Users/kenken/dev/dsa-hk/scripts")
from patch_hk_jp_ohlc import patch_one  # noqa: E402

HK_OUT = Path("/Users/kenken/dev/dsa-hk/charts/hk200")


def main():
    target = sys.argv[1] if len(sys.argv) > 1 else "2026-09-16"
    # Find stuck tickers
    stuck = []
    for fp in sorted(HK_OUT.glob("*.json")):
        if "_ohlc" in fp.name or fp.stem.startswith("HK."):
            continue
        try:
            d = json.load(open(fp))
        except Exception:
            continue
        if not isinstance(d, dict):
            continue
        ticker = d.get("ticker", "")
        if not ticker.endswith(".HK"):
            continue
        if d.get("last_bar", {}).get("date") == target:
            continue
        stuck.append((ticker, fp))
    print(f"Stuck HK: {len(stuck)} (target {target})", flush=True)

    ok = fail = 0
    for ticker, fp in stuck:
        for attempt in range(3):
            status, msg = patch_one(ticker, fp, "HK", dry=False)
            if status == "ok":
                ok += 1
                # verify it actually changed
                new = json.load(open(fp))
                if new.get("last_bar", {}).get("date") != target:
                    print(f"  ⚠ {ticker}: status=ok but last_bar still {new.get('last_bar',{}).get('date')}", flush=True)
                    status = "fail"
                    continue
                break
            else:
                print(f"  retry {attempt+1} {ticker}: {msg}", flush=True)
                time.sleep(1.0)
        else:
            fail += 1
        time.sleep(0.3)
    print(f"\nDone: ok={ok}, fail={fail}", flush=True)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
Fetch full HKEX listed-stock universe turnover from Tencent gtimg in batch.

Outputs: scripts/hk_full_universe.json  — list of {"code": "0700.HK", "turnover_m_hkd": 421.5, "price": 411.8, "name": "TENCENT"}

Why not Futu OpenD:
  - OpenD TCP 127.0.0.1:11111 unreachable (Owner complaint 2026-06-25)
  - futu-api client requires OpenD for security list

Why not yfinance:
  - Rate-limited, slow, returns 0.0 for HK secondary listings
  - We saw yfinance miss 0921.HK earlier

Why Tencent qt.gtimg.cn:
  - Free, public, no auth
  - Sub-1min delay
  - Returns turnover field per stock
  - Batch: ~50 codes per call

Run: python3 scripts/fetch_hk_universe.py [--concurrency 20]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.request import urlopen, Request
from urllib.error import URLError

PROJECT_ROOT = Path(__file__).resolve().parent.parent
OUTPUT_FILE = PROJECT_ROOT / "scripts" / "hk_full_universe.json"

# HKEX mainboard + GEM codes are 5-digit, range 00001-09999 (~5500 actually listed).
# We probe every code; non-listed returns empty data, which we filter out.
HK_CODE_RANGE = range(1, 10000)

GTIMG_BATCH_URL = "https://qt.gtimg.cn/q={codes}"


def fetch_batch(codes: list[str], timeout: float = 5.0) -> dict[str, dict]:
    """Fetch one batch from gtimg and parse per-code. Returns {code5: parsed_data}.
    Empty string for unknown codes (Tencent returns nothing or empty v_hkXXXXX=)."""
    url = GTIMG_BATCH_URL.format(codes=",".join(f"hk{c}" for c in codes))
    try:
        req = Request(url, headers={"User-Agent": "Mozilla/5.0", "Referer": "https://stockapp.finance.qq.com/"})
        with urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("gbk", errors="ignore")
    except URLError as e:
        print(f"  warn: {e}", file=sys.stderr)
        return {}

    result = {}
    for line in raw.split("\n"):
        # Format: v_hk0700="100~name~0700~...~turnover~...";
        m = re.match(r'v_hk(\d{5})="([^"]*)";', line)
        if not m:
            continue
        code = m.group(1)
        fields = m.group(2).split("~")
        if len(fields) < 38:
            continue
        try:
            # Field indices (Tencent gtimg HK format):
            # [1]  = name (zh)
            # [2]  = code
            # [3]  = current price
            # [6]  = volume (shares, 100 shares unit)
            # [37] = turnover (HKD)
            price = float(fields[3]) if fields[3] else 0.0
            volume_shares = float(fields[6]) if fields[6] else 0.0
            turnover_hkd = float(fields[37]) if fields[37] else 0.0
            name_zh = fields[1] if len(fields[1]) < 20 else ""
            if turnover_hkd > 0:
                result[code] = {
                    "code": f"{code}.HK",
                    "name": name_zh,
                    "price": price,
                    "turnover_hkd": turnover_hkd,
                    "turnover_m_hkd": round(turnover_hkd / 1e6, 2),
                }
        except (ValueError, IndexError):
            continue
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Fetch full HKEX universe turnover from Tencent")
    parser.add_argument("--concurrency", type=int, default=20, help="parallel workers (default 20)")
    parser.add_argument("--batch-size", type=int, default=40, help="codes per gtimg batch (default 40)")
    parser.add_argument("--out", type=Path, default=OUTPUT_FILE, help="output JSON path")
    parser.add_argument("--min-turnover-m", type=float, default=10.0, help="drop codes below this threshold (default 10M HKD)")
    args = parser.parse_args()

    t0 = time.time()
    # Split HK code range into batches
    all_codes = [f"{i:05d}" for i in HK_CODE_RANGE]
    batches = [all_codes[i:i + args.batch_size] for i in range(0, len(all_codes), args.batch_size)]
    print(f"[{time.strftime('%H:%M:%S')}] {len(all_codes)} codes → {len(batches)} batches ({args.concurrency} workers)")

    results: dict[str, dict] = {}
    with ThreadPoolExecutor(max_workers=args.concurrency) as ex:
        futures = {ex.submit(fetch_batch, b): b for b in batches}
        done = 0
        for fut in as_completed(futures):
            batch_result = fut.result()
            results.update(batch_result)
            done += 1
            if done % 20 == 0 or done == len(batches):
                print(f"  [{done}/{len(batches)}] batches done · {len(results)} listed so far")

    # Filter by min turnover
    listed = sorted(
        [r for r in results.values() if r["turnover_m_hkd"] >= args.min_turnover_m],
        key=lambda x: -x["turnover_m_hkd"],
    )

    elapsed = time.time() - t0
    print(f"\n=== Total: {len(results)} listed HK stocks (>= {args.min_turnover_m}M HKD turnover today)")
    print(f"  Top 5 by turnover:")
    for r in listed[:5]:
        print(f"    {r['code']}: {r['turnover_m_hkd']:.0f}M HKD")
    print(f"  Bottom 5 (still >= {args.min_turnover_m}M):")
    for r in listed[-5:]:
        print(f"    {r['code']}: {r['turnover_m_hkd']:.1f}M HKD")
    print(f"  Elapsed: {elapsed:.1f}s")

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(listed, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  Wrote {len(listed)} codes to {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
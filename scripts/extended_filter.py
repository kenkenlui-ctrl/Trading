"""Extended-setup filter.
Scan DB for records where op_advice was overridden to 買入/賣出 by rule,
but LLM body text signals "extended / overbought / don't chase".
Revert those to 觀望 (BUY→HOLD) or BUY (SELL→HOLD if mean-revert warning).
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from collections import Counter
from typing import Optional

DB = "/Users/kenken/Documents/dsa-hk/data/dsa_hk.db"

# Buy-side caution phrases — if LLM body mentions these AND op was overridden to 買入,
# revert to 觀望.
BUY_CAUTION_PATTERNS = [
    r"不宜追高",
    r"不宜現價追入",
    r"不宜追入",
    r"不宜高追",
    r"不建議追高",
    r"違反嚴進策略",
    r"偏離\s*MA20\s*達\s*\d+(?:\.\d+)?\s*%",
    r"偏離\s*MA20\s*超過",
    r"RSI.*?(?:超買|≥\s*70|接近超買)",
    r"短期超買",
    r"嚴重超買",
    r"短期超買嚴重",
    r"短期累積可觀升幅",
    r"已大幅拉升",
    r"今日已[大急]升",
    r"不宜追入違反",
    r"已逼近年?[內内]?[高頂]位",
    r"接近\s*52\s*週[高頂]位",
    r"距.*?52\s*週[高頂]位[僅只]?[餘]?[剩]?[約]?\s*[約]?[\d\.]*\s*%",
    r"技術上有回吐壓力",
    r"追高風險",
    r"追入違反",
    r"短線已累積可觀升幅",
    r"今日已急升",
    r"短期升幅大",
    r"已[急暴]升",
    r"升穿.*?但.*?追",
    r"高波幅.*?追[高入]",
    r"嚴守止損",  # tight stop signals risk
    r"嚴進[策閥]略",
    r"不宜在現價",
]

# Sell-side caution phrases — SELL signals mean-revert in 1 day
SELL_CAUTION_PATTERNS = [
    r"反彈.{0,5}(?:至|後|空間)",
    r"短期反彈",
    r"超賣",
    r"反彈.*?至\s*\$?\d",
    r"反彈.*?空間",
    r"博反彈",
    r"反彈.*?上方",
]


def has_caution(body: str, patterns: list[str]) -> Optional[str]:
    if not body:
        return None
    for p in patterns:
        if re.search(p, body):
            return p
    return None


def apply_filter(date: str, dry_run: bool = True) -> dict:
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT code, operation_advice, summary_md, full_md FROM daily_report WHERE report_date=?",
        (date,),
    ).fetchall()

    reverted_buy = []
    reverted_sell = []

    for r in rows:
        op = r["operation_advice"]
        # Check both summary and full_md for caution phrases
        body = (r["summary_md"] or "") + "\n" + (r["full_md"] or "")

        if op in ("買入", "buy"):
            # Check buy-side caution
            caution = has_caution(body, BUY_CAUTION_PATTERNS)
            if caution:
                reverted_buy.append({
                    "code": r["code"],
                    "from_op": op,
                    "to_op": "觀望",
                    "matched": caution,
                })
        elif op in ("賣出", "sell", "賣出（反彈做空）"):
            caution = has_caution(body, SELL_CAUTION_PATTERNS)
            if caution:
                reverted_sell.append({
                    "code": r["code"],
                    "from_op": op,
                    "to_op": "觀望",
                    "matched": caution,
                })

    print(f"\n=== {date} ===")
    print(f"  Reverted BUY → 觀望 (extended/overbought): {len(reverted_buy)}")
    print(f"  Reverted SELL → 觀望 (mean-revert): {len(reverted_sell)}")

    if reverted_buy:
        print("\n  Sample reverted BUY:")
        for r in reverted_buy[:5]:
            print(f"    {r['code']}: matched '{r['matched']}'")
    if reverted_sell:
        print("\n  Sample reverted SELL:")
        for r in reverted_sell[:5]:
            print(f"    {r['code']}: matched '{r['matched']}'")

    if not dry_run:
        for r in reverted_buy + reverted_sell:
            conn.execute(
                "UPDATE daily_report SET operation_advice=? WHERE code=? AND report_date=?",
                (r["to_op"], r["code"], date),
            )
        conn.commit()
        print(f"\n  Applied {len(reverted_buy) + len(reverted_sell)} reverts to {date}")

    conn.close()
    return {
        "date": date,
        "reverted_buy_count": len(reverted_buy),
        "reverted_sell_count": len(reverted_sell),
        "reverted_buy": reverted_buy,
        "reverted_sell": reverted_sell,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dates", nargs="+", default=["2026-06-30", "2026-06-29", "2026-06-27"])
    parser.add_argument("--apply", action="store_true", help="Apply reverts (default: dry-run)")
    args = parser.parse_args()

    all_results = []
    for d in args.dates:
        r = apply_filter(d, dry_run=not args.apply)
        all_results.append(r)

    # Summary
    print("\n" + "=" * 60)
    print(" SUMMARY")
    print("=" * 60)
    total_buy = sum(r["reverted_buy_count"] for r in all_results)
    total_sell = sum(r["reverted_sell_count"] for r in all_results)
    print(f"Total reverted BUY → 觀望: {total_buy}")
    print(f"Total reverted SELL → 觀望: {total_sell}")
    if not args.apply:
        print("\n[DRY RUN] Run with --apply to commit reverts")


if __name__ == "__main__":
    main()
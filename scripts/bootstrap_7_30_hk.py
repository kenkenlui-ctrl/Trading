#!/usr/bin/env python3
"""Bootstrap HK cached snapshots for 7/30 EOD daily report.

For each HK code in the 200-universe:
- Get latest available cached snapshot (7/28, 7/27, etc.) for non-price fields
- Get 7/30 closing data via futu kline
- Save stub daily_report row with data_snapshot_json (no LLM yet)

This pre-populates the cache so run_daily.py can do retrospective run
on 7/30 (which would otherwise fail with "no-cached" for all 200 codes).
"""
import json
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

DB_PATH = PROJECT_ROOT / "data" / "dsa_hk.db"
TARGET_DATE = "2026-07-30"

def main():
    # Load HK universe
    universe = json.load(open(PROJECT_ROOT / "hk_universe_200.json"))
    print(f"HK universe: {len(universe)} codes", flush=True)

    conn = sqlite3.connect(str(DB_PATH), timeout=30)
    conn.row_factory = sqlite3.Row

    # Find latest available cached snapshots per HK code
    cur = conn.execute("""
        SELECT code, data_snapshot_json, report_date
        FROM daily_report
        WHERE code LIKE '%.HK' AND data_snapshot_json IS NOT NULL
        ORDER BY report_date DESC
    """)
    cache_by_code = {}  # code -> (snapshot_dict, report_date)
    for row in cur.fetchall():
        if row["code"] in cache_by_code:
            continue
        try:
            cache_by_code[row["code"]] = (json.loads(row["data_snapshot_json"]), row["report_date"])
        except Exception:
            pass
    print(f"Found cached snapshots for {len(cache_by_code)}/{len(universe)} HK codes", flush=True)

    # Find codes missing cache
    missing = [c for c in universe if c not in cache_by_code]
    print(f"Missing cache: {len(missing)}", flush=True)
    if missing:
        print(f"  Sample missing: {missing[:5]}", flush=True)

    # Initialize futu connection
    from futu import OpenQuoteContext
    futu = OpenQuoteContext(host="127.0.0.1", port=11111)
    print("Futu connected", flush=True)

    ok = fail = 0
    fail_codes = []

    for i, code in enumerate(universe, 1):
        try:
            # Get cached snapshot
            cached_entry = cache_by_code.get(code)
            if not cached_entry:
                fail += 1
                fail_codes.append((code, "no-cache-anywhere"))
                continue
            cached, cache_date = cached_entry

            # Get 7/30 futu kline
            digits = code.split(".")[0].zfill(5)
            futu_code = f"HK.{digits}"
            from rerun_hk_sina_tencent import _futu_kline_row
            kline = _futu_kline_row(futu_code, TARGET_DATE)
            if not kline or kline.get("close") is None:
                fail += 1
                fail_codes.append((code, f"no-futu-kline (cache from {cache_date})"))
                continue

            # Build bootstrap snapshot from cached + kline
            snap = dict(cached)
            snap["last_price"] = round(kline["close"], 2)
            snap["prev_close"] = round(kline.get("prev_close", snap.get("prev_close", 0)), 2)
            if snap.get("prev_close") and snap.get("last_price"):
                snap["change_pct"] = round(
                    (snap["last_price"] - snap["prev_close"]) / snap["prev_close"] * 100, 2
                )
            snap["day_high"] = round(kline.get("high", 0), 2)
            snap["day_low"] = round(kline.get("low", 0), 2)
            snap["open"] = round(kline.get("open", 0), 2)
            snap["volume"] = kline.get("volume", 0)
            snap["data_as_of"] = f"{TARGET_DATE} 16:00 HKT (closing)"
            snap["source"] = "futu-history-bootstrap"

            # Append 7/30 as new bar in kline_30d
            kline_30d = list(snap.get("kline_30d", []))
            if not kline_30d or kline_30d[-1].get("date") != TARGET_DATE:
                kline_30d.append({
                    "date": TARGET_DATE,
                    "open": round(kline.get("open", 0), 2),
                    "high": round(kline.get("high", 0), 2),
                    "low": round(kline.get("low", 0), 2),
                    "close": round(kline["close"], 2),
                    "volume": kline.get("volume", 0),
                })
                kline_30d = kline_30d[-30:]
                snap["kline_30d"] = kline_30d
            closes = [b["close"] for b in kline_30d if b.get("close")]
            if len(closes) >= 20:
                snap["ma20"] = round(sum(closes[-20:]) / 20, 2)
            if len(closes) >= 14:
                gains, losses = [], []
                for j in range(-14, 0):
                    d = closes[j] - closes[j - 1]
                    gains.append(max(d, 0))
                    losses.append(max(-d, 0))
                avg_g = sum(gains) / 14
                avg_l = sum(losses) / 14
                if avg_l > 0:
                    snap["rsi14"] = round(100 - (100 / (1 + (avg_g / avg_l))), 2)
                else:
                    snap["rsi14"] = 100.0

            # Save to DB (upsert by code+date, only data_snapshot_json)
            conn.execute("""
                INSERT INTO daily_report (code, report_date, data_snapshot_json, generated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(code, report_date) DO UPDATE SET
                  data_snapshot_json = excluded.data_snapshot_json,
                  generated_at = excluded.generated_at
            """, (code, TARGET_DATE, json.dumps(snap, ensure_ascii=False),
                  datetime.now().isoformat(timespec="seconds")))
            conn.commit()
            ok += 1

            if i % 20 == 0:
                print(f"  [{i}/{len(universe)}] ok={ok} fail={fail}", flush=True)

        except Exception as e:
            fail += 1
            fail_codes.append((code, f"ERR {type(e).__name__}: {str(e)[:60]}"))

    conn.close()
    futu.close()

    print(f"\nDone: {ok}/{len(universe)} saved, {fail} failed", flush=True)
    if fail_codes:
        print(f"Failed samples: {fail_codes[:5]}", flush=True)

if __name__ == "__main__":
    main()

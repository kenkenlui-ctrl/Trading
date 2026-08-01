#!/usr/bin/env python3
"""Bootstrap 7/30 HK using 7/31 cached snapshots as fallback source.

7/30 only had 60 cached snapshots (from 7/28). Now 7/31 has 118 cached snapshots.
Use 7/31 cache as the new "cache source" for codes that didn't have 7/30 cache.
"""
import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

DB_PATH = PROJECT_ROOT / "data" / "dsa_hk.db"
TARGET_DATE = "2026-07-30"
CACHE_SOURCE = "2026-07-31"

def main():
    universe = json.load(open(PROJECT_ROOT / "hk_universe_200.json"))
    conn = sqlite3.connect(str(DB_PATH), timeout=30)
    conn.row_factory = sqlite3.Row

    # Find codes missing for 7/30
    cur = conn.execute("""
        SELECT code FROM daily_report WHERE report_date = ? AND code LIKE '%.HK'
    """, (TARGET_DATE,))
    have = set(r["code"] for r in cur.fetchall())
    missing = [c for c in universe if c not in have]
    print(f"Missing HK {TARGET_DATE}: {len(missing)}", flush=True)

    # Cache from 7/31
    cur = conn.execute("""
        SELECT code, data_snapshot_json FROM daily_report
        WHERE report_date = ? AND code LIKE '%.HK' AND data_snapshot_json IS NOT NULL
    """, (CACHE_SOURCE,))
    cache_by_code = {}
    for row in cur.fetchall():
        try:
            cache_by_code[row["code"]] = json.loads(row["data_snapshot_json"])
        except Exception:
            pass
    print(f"Cache from {CACHE_SOURCE}: {len(cache_by_code)}", flush=True)

    from futu import OpenQuoteContext
    futu = OpenQuoteContext(host="127.0.0.1", port=11111)
    from rerun_hk_sina_tencent import _futu_kline_row

    ok = fail = 0
    fail_codes = []
    for i, code in enumerate(missing, 1):
        try:
            cached = cache_by_code.get(code)
            if not cached:
                fail += 1
                fail_codes.append((code, "no-cache-anywhere"))
                continue
            digits = code.split(".")[0].zfill(5)
            futu_code = f"HK.{digits}"
            kline = _futu_kline_row(futu_code, TARGET_DATE)
            if not kline or kline.get("close") is None:
                fail += 1
                fail_codes.append((code, "no-futu-kline"))
                continue

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
            snap["source"] = "futu-history-bootstrap-7-30-via-7-31"

            kline_30d = list(snap.get("kline_30d", []))
            # Remove any pre-existing 7/30 bar (in case of partial bootstrap)
            kline_30d = [b for b in kline_30d if b.get("date") != TARGET_DATE]
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
                print(f"  [{i}/{len(missing)}] ok={ok} fail={fail}", flush=True)

        except Exception as e:
            fail += 1
            fail_codes.append((code, f"ERR {type(e).__name__}: {str(e)[:60]}"))

    conn.close()
    futu.close()
    print(f"\nDone: {ok}/{len(missing)} saved, {fail} failed", flush=True)
    if fail_codes:
        print(f"Failed: {fail_codes[:5]}", flush=True)

if __name__ == "__main__":
    main()

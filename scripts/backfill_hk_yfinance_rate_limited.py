#!/usr/bin/env python3
"""Rate-limited yfinance HK backfill for missing codes.

For each HK code missing cached snapshot, fetch yfinance data with proper
2-second delay between calls to avoid rate limiting. Save bootstrap
snapshots for 7/30 + 7/31 dates.
"""
import json
import sqlite3
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

DB_PATH = PROJECT_ROOT / "data" / "dsa_hk.db"
TARGET_DATES = ["2026-07-30", "2026-07-31"]

def main():
    universe = json.load(open(PROJECT_ROOT / "hk_universe_200.json"))
    print(f"HK universe: {len(universe)} codes", flush=True)

    conn = sqlite3.connect(str(DB_PATH), timeout=30)
    conn.row_factory = sqlite3.Row

    # Find codes missing for 7/30 (assume same as 7/31)
    cur = conn.execute("""
        SELECT code FROM daily_report WHERE report_date = '2026-07-30' AND code LIKE '%.HK'
    """)
    have = set(r["code"] for r in cur.fetchall())
    missing = [c for c in universe if c not in have]
    print(f"Missing HK (no 7/30 record): {len(missing)}", flush=True)
    if not missing:
        print("All codes have 7/30 data!", flush=True)
        return

    # Get latest cached snapshot per missing code
    cur = conn.execute("""
        SELECT code, data_snapshot_json, report_date
        FROM daily_report
        WHERE code LIKE '%.HK' AND data_snapshot_json IS NOT NULL
        ORDER BY report_date DESC
    """)
    cache_by_code = {}
    for row in cur.fetchall():
        if row["code"] in cache_by_code:
            continue
        try:
            cache_by_code[row["code"]] = (json.loads(row["data_snapshot_json"]), row["report_date"])
        except Exception:
            pass

    import yfinance as yf
    ok = fail = 0
    fail_codes = []
    start_total = time.time()

    for i, code in enumerate(missing, 1):
        try:
            digits = code.split(".")[0].zfill(4)
            yf_symbol = f"{digits}.HK"
            ticker = yf.Ticker(yf_symbol)
            # Use 3mo window to ensure we have data
            hist = ticker.history(period="3mo", auto_adjust=False)
            if hist is None or len(hist) < 5:
                fail += 1
                fail_codes.append((code, "no-history"))
                time.sleep(2)
                continue

            for target_date_str in TARGET_DATES:
                target_dt = datetime.strptime(target_date_str, "%Y-%m-%d").date()
                # Find target row
                mask = hist.index.date == target_dt
                if not mask.any():
                    continue
                row = hist[mask].iloc[0]
                close = float(row["Close"])
                # prev close
                prev_date = target_dt - timedelta(days=1)
                prev_mask = hist.index.date == prev_date
                prev_close = float(hist[prev_mask].iloc[0]["Close"]) if prev_mask.any() else float(row["Open"])

                # Use cached snapshot or build minimal
                cached_entry = cache_by_code.get(code)
                if cached_entry:
                    snap = dict(cached_entry[0])
                else:
                    snap = {
                        "code": code, "name_zh": code, "name_en": code,
                        "pe_ttm": None, "pb": None, "dividend_yield": None,
                        "market_cap_hkd": None, "ma20": None, "ma50": None,
                        "ma100": None, "ma200": None, "rsi14": None,
                        "52w_high": None, "52w_low": None, "ytd_change_pct": None,
                        "sector": "Unknown", "kline_30d": [],
                    }

                snap["last_price"] = round(close, 2)
                snap["prev_close"] = round(prev_close, 2)
                if snap.get("prev_close") and snap.get("last_price"):
                    snap["change_pct"] = round((snap["last_price"] - snap["prev_close"]) / snap["prev_close"] * 100, 2)
                snap["day_high"] = round(float(row["High"]), 2)
                snap["day_low"] = round(float(row["Low"]), 2)
                snap["open"] = round(float(row["Open"]), 2)
                snap["volume"] = int(row["Volume"])
                snap["data_as_of"] = f"{target_date_str} 16:00 HKT (closing)"
                snap["source"] = "yfinance-hk-rate-limited"

                # Append target_date to kline_30d
                kline_30d = list(snap.get("kline_30d", []))
                if not kline_30d or kline_30d[-1].get("date") != target_date_str:
                    kline_30d.append({
                        "date": target_date_str,
                        "open": round(float(row["Open"]), 2),
                        "high": round(float(row["High"]), 2),
                        "low": round(float(row["Low"]), 2),
                        "close": round(close, 2),
                        "volume": int(row["Volume"]),
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

                # Save to DB
                conn.execute("""
                    INSERT INTO daily_report (code, report_date, data_snapshot_json, generated_at)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(code, report_date) DO UPDATE SET
                      data_snapshot_json = excluded.data_snapshot_json,
                      generated_at = excluded.generated_at
                """, (code, target_date_str, json.dumps(snap, ensure_ascii=False),
                      datetime.now().isoformat(timespec="seconds")))
                conn.commit()

            ok += 1
            if i % 10 == 0:
                elapsed = time.time() - start_total
                rate = i / elapsed if elapsed > 0 else 0
                eta = (len(missing) - i) / rate / 60 if rate > 0 else 0
                print(f"  [{i}/{len(missing)}] ok={ok} fail={fail} {rate:.2f}/s ETA {eta:.0f}min", flush=True)

        except Exception as e:
            fail += 1
            fail_codes.append((code, f"ERR {type(e).__name__}: {str(e)[:60]}"))

        time.sleep(2)  # rate limit

    conn.close()
    elapsed_total = time.time() - start_total
    print(f"\nDone: {ok}/{len(missing)} saved, {fail} failed in {elapsed_total/60:.1f}min", flush=True)
    if fail_codes:
        print(f"Failed samples: {fail_codes[:10]}", flush=True)

if __name__ == "__main__":
    main()

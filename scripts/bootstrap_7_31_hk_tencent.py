#!/usr/bin/env python3
"""Tencent API bootstrap for missing HK codes (7/31 EOD).

Tencent qtimg gives current/last-close data which IS 7/31 close (market closed).
For 7/31 retrospective, we can use Tencent as a backup for the 82 codes
futu doesn't have in 9-day window.
"""
import json
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

DB_PATH = PROJECT_ROOT / "data" / "dsa_hk.db"
TARGET_DATE = "2026-07-31"


def fetch_tencent_quote(code: str) -> dict | None:
    """Fetch HK stock quote from Tencent qtimg API.
    Returns dict with: name_zh, name_en, current, prev_close, open,
    high, low, volume, turnover, change_pct, 52w_high, 52w_low, market_cap
    """
    import requests
    digits = code.split(".")[0].zfill(5)
    url = f"https://qt.gtimg.cn/q=hk{digits}"
    try:
        r = requests.get(url, timeout=10)
        text = r.text.strip()
        if '~' not in text or '""' in text or "pv_none_match" in text:
            return None
        # Format: v_hk00241="100~name~code~...~"
        content = text.split('"', 2)[1] if '"' in text else text
        parts = content.split('~')
        if len(parts) < 50:
            return None
        def f(idx, default=None):
            try:
                v = parts[idx]
                return float(v) if v and v != '' else default
            except (ValueError, IndexError):
                return default
        def s(idx, default=''):
            try:
                return parts[idx] or default
            except IndexError:
                return default
        return {
            "code": code,
            "name_zh": s(1, code),
            "name_en": s(46, ""),
            "current": f(3),
            "prev_close": f(4),
            "open": f(5),
            "volume": f(6),
            "high": f(33),
            "low": f(34),
            "turnover": f(37),  # in 10000s
            "change_pct": f(32),
            "52w_high": f(48),
            "52w_low": f(49),
            "market_cap": f(44),  # 億 HKD
            "as_of": s(30, ""),  # "2026/07/31 16:08:41"
        }
    except Exception as e:
        return None


def main():
    universe = json.load(open(PROJECT_ROOT / "hk_universe_200.json"))
    conn = sqlite3.connect(str(DB_PATH), timeout=30)
    conn.row_factory = sqlite3.Row

    # Find codes still missing 7/31 (after futu bootstrap)
    cur = conn.execute("""
        SELECT code FROM daily_report WHERE report_date = ? AND code LIKE '%.HK'
    """, (TARGET_DATE,))
    have = set(r["code"] for r in cur.fetchall())
    missing = [c for c in universe if c not in have]
    print(f"Still missing HK 7/31: {len(missing)}", flush=True)
    if not missing:
        print("All codes have 7/31 data!", flush=True)
        return

    # Get latest cached snapshot per missing code (for non-price fields)
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

    ok = fail = 0
    fail_codes = []

    for i, code in enumerate(missing, 1):
        q = fetch_tencent_quote(code)
        if not q or not q.get("current"):
            fail += 1
            fail_codes.append((code, "no-tencent-quote"))
            time.sleep(0.3)
            continue

        cached_entry = cache_by_code.get(code)
        if cached_entry:
            snap = dict(cached_entry[0])
        else:
            snap = {
                "code": code, "pe_ttm": None, "pb": None, "dividend_yield": None,
                "ma20": None, "ma50": None, "ma100": None, "ma200": None,
                "rsi14": None, "ytd_change_pct": None, "sector": "Unknown",
                "kline_30d": [],
            }
        # Use cached name_zh if Tencent returned garbled
        if cached_entry and cached_entry[0].get("name_zh") and (not snap.get("name_zh") or snap["name_zh"] == code):
            snap["name_zh"] = cached_entry[0]["name_zh"]
        else:
            snap["name_zh"] = q.get("name_zh", code)
        if q.get("name_en") and (not snap.get("name_en") or snap["name_en"] == code):
            snap["name_en"] = q["name_en"]

        snap["last_price"] = round(q["current"], 2)
        snap["prev_close"] = round(q["prev_close"], 2) if q.get("prev_close") else None
        if snap.get("prev_close") and snap.get("last_price"):
            snap["change_pct"] = round(
                (snap["last_price"] - snap["prev_close"]) / snap["prev_close"] * 100, 2
            )
        snap["open"] = round(q["open"], 2) if q.get("open") else None
        snap["day_high"] = round(q["high"], 2) if q.get("high") else None
        snap["day_low"] = round(q["low"], 2) if q.get("low") else None
        snap["volume"] = int(q["volume"]) if q.get("volume") else None
        # Tencent turnover is in 10000s
        if q.get("turnover"):
            snap["turnover_hkd"] = round(q["turnover"] * 10000, 2)
        snap["52w_high"] = round(q["52w_high"], 2) if q.get("52w_high") else None
        snap["52w_low"] = round(q["52w_low"], 2) if q.get("52w_low") else None
        if q.get("market_cap"):
            snap["market_cap_hkd"] = round(q["market_cap"] * 1e8, 2)
        snap["data_as_of"] = f"{TARGET_DATE} 16:08 HKT (tencent-live)"
        snap["source"] = "tencent-qtimg-fallback"

        # Append 7/31 to kline_30d
        kline_30d = list(snap.get("kline_30d", []))
        kline_30d = [b for b in kline_30d if b.get("date") != TARGET_DATE]
        kline_30d.append({
            "date": TARGET_DATE,
            "open": snap.get("open"),
            "high": snap.get("day_high"),
            "low": snap.get("day_low"),
            "close": snap["last_price"],
            "volume": snap.get("volume"),
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

        if i % 10 == 0:
            print(f"  [{i}/{len(missing)}] ok={ok} fail={fail}", flush=True)
        time.sleep(0.3)  # rate limit

    conn.close()
    print(f"\nDone: {ok}/{len(missing)} saved, {fail} failed", flush=True)
    if fail_codes:
        print(f"Failed: {fail_codes[:5]}", flush=True)

if __name__ == "__main__":
    main()

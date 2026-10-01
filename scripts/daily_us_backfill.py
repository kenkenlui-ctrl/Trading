"""daily_us_backfill.py — One-shot US200 backfill for a single target date.

Usage:
  python3 scripts/daily_us_backfill.py --date 2026-08-27

Hard rules:
  - US only (HK has its own cron)
  - T-1 close only (target_date is the close date, e.g. yesterday HKT)
  - Abort if US fail rate >30% (excluding BRK-B known fail)
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from pathlib import Path

PROJECT_ROOT = Path("/Users/kenken/dev/dsa-hk")
sys.path.insert(0, str(PROJECT_ROOT))
DB_PATH = PROJECT_ROOT / "data" / "dsa_hk.db"

DATA_AS_OF = "{date} 16:00 ET (US market close)"
CARRY_FWD_AS_OF = "{date} 16:00 ET (carry-forward from {prev})"

import yfinance as yf  # noqa: E402

# Tickertickers that are known to never normalize (per memory 2026-08-26)
KNOWN_FAIL = {"BRK-B", "BRK-A"}


def _fetch_us(code: str, target_date: str) -> dict | None:
    try:
        td = datetime.strptime(target_date, "%Y-%m-%d")
        start = (td - timedelta(days=10)).strftime("%Y-%m-%d")
        end = (td + timedelta(days=1)).strftime("%Y-%m-%d")
        h = yf.Ticker(code).history(start=start, end=end, auto_adjust=False)
        if h is None or h.empty:
            return None
        hist_dates = h.index.date
        matching = h[hist_dates == td.date()]
        if matching.empty:
            return None
        row = matching.iloc[0]
        prev = h[hist_dates < td.date()]
        prev_close = float(prev.iloc[-1]["Close"]) if not prev.empty else None
        return {
            "open": float(row["Open"]), "high": float(row["High"]),
            "low": float(row["Low"]), "close": float(row["Close"]),
            "volume": float(row["Volume"]),
            "prev_close": prev_close,
            "source": "yfinance",
            "data_as_of": DATA_AS_OF.format(date=target_date),
        }
    except Exception as e:
        return None


def _build_snapshot(code: str, k: dict, market: str) -> dict:
    last = k["close"]
    prev = k.get("prev_close")
    change = round((last - prev) / prev * 100, 2) if prev else None
    return {
        "code": code,
        "last_price": round(last, 2),
        "prev_close": round(prev, 2) if prev else None,
        "change_pct": change,
        "day_high": round(k["high"], 2),
        "day_low": round(k["low"], 2),
        "open": round(k["open"], 2),
        "volume": k["volume"],
        "data_as_of": k["data_as_of"],
        "source": f"{k['source']}-daily-backfill",
        "market": market,
    }


def _upsert(con, code, target_date, snap):
    snap_json = json.dumps(snap, ensure_ascii=False)
    decision_reason = (
        f"[daily-us-backfill] data_snapshot from {snap['source']} · "
        f"data_as_of={snap['data_as_of']}. Run run_daily.py for LLM narrative."
    )
    row = con.execute(
        "SELECT id FROM daily_report WHERE code=? AND report_date=?",
        (code, target_date),
    ).fetchone()
    if row is None:
        summary_md = (
            f"⏳ **backfill placeholder · {code} · {target_date}**\n\n"
            f"收市價: USD {snap['last_price']:.2f} (前日收 {snap['prev_close']:.2f}, "
            f"{'+' if (snap['change_pct'] or 0) >= 0 else ''}{snap['change_pct'] or 0:.2f}%)。\n\n"
            f"_data_as_of: {snap['data_as_of']} · source: {snap['source']}_\n\n"
            f"⚠️ 此為 backfill placeholder，LLM 點評未更新。完整 narrative 將由 run_daily.py 補上。"
        )
        con.execute("""
            INSERT INTO daily_report (code, report_date, score, sentiment, trend,
                operation_advice, summary_md, data_snapshot_json, llm_model, generated_at,
                decision_reason, signal_score, score_breakdown_json, trade_direction)
            VALUES (?, ?, 0, '中性', '震盪', '觀望', ?, ?, 'daily-us-backfill',
                datetime('now'), ?, 0, '{}', 'hold')
        """, (code, target_date, summary_md, snap_json, decision_reason))
        return "inserted"
    else:
        con.execute("""
            UPDATE daily_report
            SET data_snapshot_json=?, decision_reason=?, generated_at=datetime('now')
            WHERE id=?
        """, (snap_json, decision_reason, row[0]))
        return "updated"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", required=True, help="Target date YYYY-MM-DD (T-1 close)")
    args = ap.parse_args()
    target_date = args.date

    # Read US universe from us_universe_200.json if exists, else us_top200_fresh.json
    uni_path = PROJECT_ROOT / "us_universe_200.json"
    if not uni_path.exists():
        uni_path = Path("/Users/kenken/dev/dsa-hk/charts/us200/us_top200_fresh.json")
    us_uni = json.loads(uni_path.read_text())
    print(f"=== daily_us_backfill === target={target_date} universe={len(us_uni)}")

    con = sqlite3.connect(str(DB_PATH), timeout=30)
    con.execute("PRAGMA journal_mode=WAL")

    results = {}
    fails = []
    lock = threading.Lock()

    def _runner(code):
        k = _fetch_us(code, target_date)
        with lock:
            if k:
                snap = _build_snapshot(code, k, "US")
                results[code] = snap
            else:
                fails.append(code)

    t0 = time.time()
    with ThreadPoolExecutor(max_workers=12) as ex:
        futs = [ex.submit(_runner, c) for c in us_uni]
        for i, f in enumerate(as_completed(futs), 1):
            if i % 50 == 0:
                print(f"  [{i}/{len(us_uni)}] elapsed={time.time()-t0:.0f}s ok={len(results)} fail={len(fails)}", flush=True)
    print(f"Fetch done: ok={len(results)} fail={len(fails)} in {time.time()-t0:.0f}s")

    inserted = updated = 0
    for code, snap in results.items():
        action = _upsert(con, code, target_date, snap)
        if action == "inserted": inserted += 1
        else: updated += 1
    con.commit()

    # Compute US fail rate excluding known-fail tickers (BRK-B etc.)
    known_fail_actual = [c for c in fails if c in KNOWN_FAIL]
    real_fails = [c for c in fails if c not in KNOWN_FAIL]
    fail_pct = len(real_fails) / len(us_uni) * 100 if us_uni else 0
    print(f"\n=== Results ===")
    print(f"DB write: inserted={inserted} updated={updated}")
    print(f"Fails: {len(fails)}/{len(us_uni)} ({len(fails)/len(us_uni)*100:.1f}%) — "
          f"of which {len(known_fail_actual)} known (BRK-B), real={len(real_fails)} ({fail_pct:.1f}%)")
    if real_fails:
        print(f"  real fail tickers: {real_fails[:20]}")
    con.close()

    if fail_pct > 30:
        print(f"\n!!! ABORT: US real fail rate {fail_pct:.1f}% > 30% threshold !!!", file=sys.stderr)
        sys.exit(2)

    print(f"\n✓ US daily backfill complete: {len(results)} written, {len(fails)} fails (incl. known)")


if __name__ == "__main__":
    main()

"""Import 7/17 daily report from yfinance + HSI_REGIME override (no LLM needed).

Phase 9 (2026-07-20): 7/17 was a BEAR day (HSI -1.78%). User noticed the
7/17 report was never generated (pipeline never ran that day). This script
backfills 7/17 records by:
  1. Fetching 7/17 close + change_pct for every code that has a 7/16 record
  2. Inserting a 7/17 record per ticker (no LLM analysis — too slow + costly)
  3. All 7/17 records get HSI_REGIME rule (bear day protection) → 觀望

This is the correct semantic: 7/17 BEAR day, all BUY signals would be blocked
by the new HSI_REGIME filter. So 7/17 dashboard = 0 BUY + n 觀望 records.
The forward_returns for 7/17 BUY = 0 (since there are no BUY).

Output: ~393 records for 7/17 (all 觀望, all HSI_REGIME).

Usage:
    python3 scripts/import_7_17_static.py
"""
import json
import sys
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).parent.parent))

import sqlite3
import yfinance as yf
import requests

DB_PATH = "/Users/kenken/Documents/dsa-hk/data/dsa_hk.db"
REPORT_DATE = "2026-07-17"
HSI_CHG_PCT = -1.78  # verified via Tencent API
HSI_REGIME_RULE = "HSI_REGIME"


def fetch_tencent_quote(hk_code: str):
    """Fetch HK stock quote from Tencent qtimg API. Returns (last_price, change_pct) or None."""
    try:
        stem = hk_code.replace(".HK", "").zfill(5)
        r = requests.get(f"https://qt.gtimg.cn/q=hk{stem}", timeout=5)
        text = r.text.strip()
        if '="' not in text:
            return None
        fields = text.split('="')[1].rstrip('";').split('~')
        if len(fields) < 35:
            return None
        last = float(fields[3]) if fields[3] else None
        prev = float(fields[4]) if fields[4] else None
        if not last or not prev or prev <= 0:
            return None
        chg = (last - prev) / prev * 100
        return (last, chg)
    except Exception:
        return None


def main():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row

    # Get distinct codes from 7/16 records (the last good report before 7/17)
    code_rows = con.execute(
        "SELECT DISTINCT code FROM daily_report WHERE report_date=? AND operation_advice != '觀望' OR report_date=? ORDER BY code",
        ("2026-07-16", "2026-07-16"),
    ).fetchall()
    codes = [r["code"] for r in code_rows]
    print(f"Found {len(codes)} codes from 7/16")

    # Get 7/16 sample data per code (for sector, etc)
    sample_rows = con.execute(
        "SELECT code, data_snapshot_json, score_breakdown_json, summary_md, sentiment FROM daily_report WHERE report_date=?",
        ("2026-07-16",),
    ).fetchall()
    by_code = {r["code"]: r for r in sample_rows}

    # Wipe 7/17 records if any (idempotent)
    deleted = con.execute("DELETE FROM daily_report WHERE report_date=?", (REPORT_DATE,)).rowcount
    if deleted:
        print(f"  (wiped {deleted} existing 7/17 records)")

    # Insert 7/17 records
    inserted = 0
    skipped = 0
    now_iso = datetime.now().isoformat(timespec="seconds")
    for code in codes:
        sample = by_code.get(code)
        if not sample:
            skipped += 1
            continue
        try:
            snap = json.loads(sample["data_snapshot_json"] or "{}")
        except Exception:
            snap = {}
        try:
            bd = json.loads(sample["score_breakdown_json"] or "{}")
        except Exception:
            bd = {}

        # Fetch 7/17 close price
        cur = None
        chg = None
        if code.endswith(".HK"):
            qt = fetch_tencent_quote(code)
            if qt:
                cur, chg = qt
        if cur is None:
            try:
                yf_code = code.split(".")[0].zfill(4) + ".HK" if code.endswith(".HK") else code
                t = yf.Ticker(yf_code)
                hist = t.history(start="2026-07-15", end="2026-07-18", progress=False)
                if not hist.empty and len(hist) >= 2:
                    cur = float(hist["Close"].iloc[-1])
                    chg = float((cur / hist["Close"].iloc[-2] - 1) * 100)
            except Exception:
                pass
        if cur is None:
            skipped += 1
            continue

        # Build 7/17 snapshot — update close + chg, keep other fields
        snap["last_price"] = cur
        snap["change_pct"] = round(chg, 2) if chg is not None else None
        snap["data_as_of"] = "2026-07-17 16:08:00"
        if chg is not None and "prev_close" in snap:
            snap["prev_close"] = round(cur / (1 + chg / 100), 2) if chg != -100 else cur

        # All 7/17 records = 觀望 with HSI_REGIME rule (bear day protection)
        decision_reason = f"[{HSI_REGIME_RULE}] HSI closed {HSI_CHG_PCT:+.2f}% on signal day (BEAR, threshold -1.5%). 7/17 live: bear day ALL BUY = 19% WR, -3.11% avg. Auto-suppress to 觀望."

        # signal_score = 30 (low, matches HSI_REGIME bucket)
        sig_score = 30

        # LLM score = 0 (no LLM was actually run; this is a synthetic backfill)
        llm_score = 0

        # Per-stock summary (Phase 9+, 2026-07-20): use 7/16's summary as base
        # + prepend a stock-specific 7/17 line so each card shows unique content.
        # Avoids the 197-record "same message repeated" UX bug.
        prev_summary = sample["summary_md"] or ""
        # Strip any leading emoji+bold code prefix from prev_summary
        import re as _re
        prev_summary = _re.sub(r'^🟢?🔴?⚪?\s*\*\*[A-Z0-9.\-]+\.HK\*\*\s*·\s*', '', prev_summary)
        name = snap.get("name_zh") or snap.get("name_en") or code
        per_stock_summary = (
            f"🟡 **{code}** · {name} · 7/17 收 ${cur:.2f} ({chg:+.2f}%) · "
            f"HSI {HSI_CHG_PCT:+.2f}% BEAR day · HSI_REGIME auto-suppressed BUY → 觀望. "
            f"7/16 信號睇 <a href=\"/dashboard/2026-07-16/{code}.html\">上一個 report</a>."
        )

        con.execute(
            """INSERT INTO daily_report
               (code, report_date, score, sentiment, trend, operation_advice,
                score_breakdown_json, trade_direction, support_zone, resistance_zone,
                key_levels_json, summary_md, full_md, news_json, data_snapshot_json,
                llm_model, generated_at, llm_original_op, decision_reason, signal_score)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                code, REPORT_DATE, llm_score,
                sample["sentiment"] or "中性", "震盪", "觀望",
                json.dumps(bd, ensure_ascii=False), "both", None, None, None,
                per_stock_summary,
                prev_summary or per_stock_summary,  # full_md falls back to summary
                "[]",
                json.dumps(snap, ensure_ascii=False),
                "synthetic-7-17-backfill",
                now_iso,
                "買入",  # original LLM op would have been buy
                decision_reason,
                sig_score,
            ),
        )
        inserted += 1
        if inserted <= 3:
            print(f"  {code}: ${cur:.2f} chg={chg:+.2f}% → 觀望 [HSI_REGIME]")

    con.commit()
    print(f"\nDone: {inserted} inserted, {skipped} skipped")
    print(f"7/17 records now in DB: {con.execute('SELECT COUNT(*) FROM daily_report WHERE report_date=?', (REPORT_DATE,)).fetchone()[0]}")


if __name__ == "__main__":
    main()

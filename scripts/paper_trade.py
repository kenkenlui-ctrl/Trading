"""Paper-trade tracker for Conservative BUY + Cyber BUY signals.

Daily workflow (run after market close ~4:30 PM HKT):
  1. Open new paper trades for Conservative BUY / Cyber BUY signals on latest report_date
  2. For each open trade: fetch current price, close if stop / target / 3-day timeout hit

Stop / target extraction from full_md:
  - "止損位" or "止蝕位" → stop_loss (BUY: entry - stop_loss = risk)
  - "目標價" → target_price
  - Fallback: stop_loss = entry * 0.94, target_price = entry * 1.06 (6%/6% default)

Usage:
  python3 scripts/paper_trade.py                # today, both presets
  python3 scripts/paper_trade.py --dry-run      # show what would be opened/closed
  python3 scripts/paper_trade.py --preset conservative-buy
  python3 scripts/paper_trade.py --report-date 2026-07-06  # explicit date
"""
import argparse
import json
import re
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pandas as pd
import yfinance as yf

DB_PATH = Path("/Users/kenken/Documents/dsa-hk/data/dsa_hk.db")
POSITION_SIZE_USD = 1000.0
MAX_HOLD_DAYS = 3

# ---------- Helpers ----------

def parse_stop_target(full_md: str, summary_md: str, entry_price: float) -> tuple[float | None, float | None]:
    """Extract stop_loss + target_price from LLM markdown. Returns (stop, target) or (None, None)."""
    text = (full_md or "") + "\n" + (summary_md or "")
    # Pattern: "止損位: 534.00" or "止蝕位: $X"
    stop_match = re.search(r"止[損蝕]位[^:：]*[:：]\s*\$?([\d,.]+)", text)
    target_match = re.search(r"目標價[^:：]*[:：]\s*\$?([\d,.]+)", text)
    stop = None
    target = None
    if stop_match:
        try:
            stop = float(stop_match.group(1).replace(",", ""))
        except ValueError:
            pass
    if target_match:
        try:
            target = float(target_match.group(1).replace(",", ""))
        except ValueError:
            pass
    # Fallback defaults if either missing
    if stop is None:
        stop = round(entry_price * 0.94, 2)
    if target is None:
        target = round(entry_price * 1.06, 2)
    return stop, target


def to_yf_ticker(code: str) -> str:
    """0700.HK → 0700.HK (4-digit for yfinance); HK stays; US stays."""
    if code.endswith(".HK"):
        stem = code[:-3].lstrip("0")
        if stem:
            return stem + ".HK"
    return code


def get_current_price(code: str) -> float | None:
    """Fetch latest close via yfinance (4-digit HK or US)."""
    yf_code = to_yf_ticker(code)
    try:
        t = yf.Ticker(yf_code)
        hist = t.history(period="5d")
        if hist.empty:
            return None
        return float(hist.iloc[-1]["Close"])
    except Exception:
        return None


def get_signal_codes(report_date: str, preset: str) -> list[dict]:
    """Get all codes + signal data passing the given filter preset."""
    import sqlite3
    from src.conservative_filters import CYBER_TICKERS, TECH_SECTORS_AVOID
    con = sqlite3.connect(str(DB_PATH))
    con.row_factory = sqlite3.Row
    # For conservative-buy we need score_breakdown which isn't in the row above
    if preset == "conservative-buy":
        rows = con.execute(
            """SELECT code, score, operation_advice, full_md, summary_md,
                      data_snapshot_json, score_breakdown_json
               FROM daily_report
               WHERE report_date=? AND operation_advice='買入'""",
            (report_date,),
        ).fetchall()
    else:
        rows = con.execute(
            """SELECT code, score, operation_advice, full_md, summary_md, data_snapshot_json
               FROM daily_report
               WHERE report_date=? AND operation_advice='買入'""",
            (report_date,),
        ).fetchall()
    con.close()
    out = []
    for r in rows:
        code = r["code"]
        if preset == "cyber-buy":
            # Cyber BUY: only WHITELIST (cyber tickers are US, no .HK suffix)
            tk = code.split(".")[0]
            if tk in CYBER_TICKERS:
                out.append(dict(r))
        elif preset == "conservative-buy":
            if code.endswith(".HK"):
                continue  # US-only filter
            try:
                snap = json.loads(r["data_snapshot_json"]) if r["data_snapshot_json"] else {}
            except Exception:
                snap = {}
            day_chg = snap.get("change_pct") or 0
            sector = (snap.get("sector") or "").strip()
            try:
                bd = json.loads(r["score_breakdown_json"]) if r["score_breakdown_json"] else {}
            except Exception:
                bd = {}
            m_score = int(bd.get("momentum_score") or 0)
            text = (r["summary_md"] or "") + " " + (r["full_md"] or "")
            m_sent = re.search(r"·\s*(樂觀|中性|悲觀)\s*·", text)
            sentiment = m_sent.group(1) if m_sent else ""
            score = r["score"] or 0
            if not (-3 < day_chg < 0):
                continue
            if sector in TECH_SECTORS_AVOID:
                continue
            if not (30 <= m_score <= 70):
                continue
            if sentiment == "樂觀":
                continue
            if score >= 70:
                continue
            out.append(dict(r))
        elif preset == "all-buy":
            out.append(dict(r))
    return out


def open_paper_trades(report_date: str, preset: str, dry_run: bool = False) -> int:
    """Open paper trades for all signals matching preset. Returns count opened."""
    signals = get_signal_codes(report_date, preset)
    if not signals:
        print(f"  [{preset}] No signals on {report_date}")
        return 0
    print(f"  [{preset}] {len(signals)} signals on {report_date}")
    opened = 0
    con = sqlite3.connect(str(DB_PATH))
    cur = con.cursor()
    for sig in signals:
        code = sig["code"]
        # Skip if already opened
        existing = cur.execute(
            """SELECT id FROM paper_trade
               WHERE code=? AND signal_date=? AND signal_source=?""",
            (code, report_date, preset),
        ).fetchone()
        if existing:
            print(f"    {code}: already opened (id={existing[0]})")
            continue
        # Get entry price
        try:
            snap = json.loads(sig["data_snapshot_json"]) if sig["data_snapshot_json"] else {}
        except Exception:
            snap = {}
        entry_price = snap.get("last_price")
        if not entry_price or entry_price <= 0:
            print(f"    {code}: no entry price, skip")
            continue
        stop, target = parse_stop_target(sig["full_md"] or "", sig["summary_md"] or "", entry_price)
        if dry_run:
            print(f"    [DRY] OPEN {code} entry=${entry_price:.2f} stop=${stop:.2f} target=${target:.2f}")
            continue
        cur.execute(
            """INSERT INTO paper_trade
               (code, signal_date, signal_source, entry_date, entry_price,
                position_size_usd, stop_loss, target_price, score, op_advice, status)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'open')""",
            (code, report_date, preset, report_date, entry_price,
             POSITION_SIZE_USD, stop, target, sig["score"], sig["operation_advice"]),
        )
        opened += 1
        print(f"    OPEN {code} entry=${entry_price:.2f} stop=${stop:.2f} target=${target:.2f} score={sig['score']}")
    con.commit()
    con.close()
    return opened


def close_paper_trades(dry_run: bool = False) -> int:
    """For each open trade, fetch current price, close if stop/target/timeout hit."""
    con = sqlite3.connect(str(DB_PATH))
    con.row_factory = sqlite3.Row
    cur = con.cursor()
    open_trades = cur.execute(
        "SELECT * FROM paper_trade WHERE status='open' ORDER BY entry_date ASC"
    ).fetchall()
    print(f"  {len(open_trades)} open trades to check")
    closed = 0
    today = datetime.now().strftime("%Y-%m-%d")
    for t in open_trades:
        code = t["code"]
        entry_date = t["entry_date"]
        entry_price = t["entry_price"]
        stop = t["stop_loss"]
        target = t["target_price"]
        # Hold duration
        try:
            entry_dt = datetime.strptime(entry_date, "%Y-%m-%d")
            hold_days = (datetime.now() - entry_dt).days
        except Exception:
            hold_days = 0
        # Fetch current price
        cur_price = get_current_price(code)
        if cur_price is None:
            print(f"    {code}: no current price, skip")
            continue
        # Check exit conditions
        exit_reason = None
        exit_price = cur_price
        if cur_price <= stop:
            exit_reason = "stop"
            exit_price = stop
        elif cur_price >= target:
            exit_reason = "target"
            exit_price = target
        elif hold_days >= MAX_HOLD_DAYS:
            exit_reason = "eod-3day"
            exit_price = cur_price
        if exit_reason is None:
            print(f"    {code}: open, current=${cur_price:.2f}, hold={hold_days}d")
            continue
        # Calculate P&L
        pnl_pct = (exit_price - entry_price) / entry_price * 100
        pnl_usd = pnl_pct / 100 * POSITION_SIZE_USD
        if dry_run:
            print(f"    [DRY] CLOSE {code} reason={exit_reason} exit=${exit_price:.2f} pnl={pnl_pct:+.2f}% ${pnl_usd:+.2f}")
            continue
        cur.execute(
            """UPDATE paper_trade
               SET exit_date=?, exit_price=?, close_reason=?, pnl_pct=?, pnl_usd=?, status='closed'
               WHERE id=?""",
            (today, exit_price, exit_reason, pnl_pct, pnl_usd, t["id"]),
        )
        closed += 1
        emoji = "✓" if pnl_pct > 0 else "✗"
        print(f"    {emoji} CLOSE {code} reason={exit_reason} entry=${entry_price:.2f} exit=${exit_price:.2f} pnl={pnl_pct:+.2f}% ${pnl_usd:+.2f}")
    con.commit()
    con.close()
    return closed


def print_stats():
    """Print paper-trade performance stats."""
    con = sqlite3.connect(str(DB_PATH))
    con.row_factory = sqlite3.Row
    cur = con.cursor()
    total = cur.execute("SELECT COUNT(*) FROM paper_trade").fetchone()[0]
    closed = cur.execute("SELECT COUNT(*) FROM paper_trade WHERE status='closed'").fetchone()[0]
    open_n = cur.execute("SELECT COUNT(*) FROM paper_trade WHERE status='open'").fetchone()[0]
    wins = cur.execute("SELECT COUNT(*) FROM paper_trade WHERE status='closed' AND pnl_pct > 0").fetchone()[0]
    losses = cur.execute("SELECT COUNT(*) FROM paper_trade WHERE status='closed' AND pnl_pct <= 0").fetchone()[0]
    total_pnl = cur.execute("SELECT COALESCE(SUM(pnl_usd), 0) FROM paper_trade WHERE status='closed'").fetchone()[0]
    print(f"\n=== Paper Trade Stats ===")
    print(f"  Total: {total}, Open: {open_n}, Closed: {closed}")
    if closed > 0:
        wr = wins / closed * 100
        avg_win = cur.execute("SELECT COALESCE(AVG(pnl_pct), 0) FROM paper_trade WHERE status='closed' AND pnl_pct > 0").fetchone()[0]
        avg_loss = cur.execute("SELECT COALESCE(AVG(pnl_pct), 0) FROM paper_trade WHERE status='closed' AND pnl_pct <= 0").fetchone()[0]
        print(f"  Wins: {wins}, Losses: {losses}, WR: {wr:.1f}%")
        print(f"  Avg win: +{avg_win:.2f}%, Avg loss: {avg_loss:+.2f}%")
        print(f"  Total P&L: ${total_pnl:+.2f} (on {closed} closed × $1000 size)")
    con.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--report-date", default=None, help="signal report_date (default: latest in DB)")
    ap.add_argument("--preset", default="conservative-buy", choices=["conservative-buy", "cyber-buy", "all-buy"])
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--close-only", action="store_true", help="only close existing trades, don't open new")
    args = ap.parse_args()

    # Determine report_date
    if args.report_date:
        report_date = args.report_date
    else:
        con = sqlite3.connect(str(DB_PATH))
        report_date = con.execute("SELECT MAX(report_date) FROM daily_report").fetchone()[0]
        con.close()
    print(f"=== Paper Trade Run ===")
    print(f"  report_date: {report_date}, preset: {args.preset}, dry_run: {args.dry_run}")
    if not args.close_only:
        print(f"\n--- Open new trades [{args.preset}] ---")
        opened = open_paper_trades(report_date, args.preset, dry_run=args.dry_run)
        print(f"  Opened: {opened}")
    print(f"\n--- Close existing trades ---")
    closed = close_paper_trades(dry_run=args.dry_run)
    print(f"  Closed: {closed}")
    if not args.dry_run:
        print_stats()


if __name__ == "__main__":
    main()
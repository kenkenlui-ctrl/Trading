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

# Phase 9 (2026-07-20): user-configurable sig score thresholds via env vars
# Defaults: high=70 (gold star), mid=60 (blue), paper floor=65 (paper trader)
import os as _os_paper
SIG_HIGH = int(_os_paper.environ.get("DSA_SIG_HIGH", "70"))
SIG_PAPER_FLOOR = int(_os_paper.environ.get("DSA_SIG_PAPER_FLOOR", "65"))

# ANSI color codes (Phase 9+, 2026-07-20) — disable with NO_COLOR=1
_USE_COLOR = not _os_paper.environ.get("NO_COLOR")
_C = {
    "reset": "\033[0m" if _USE_COLOR else "",
    "bold": "\033[1m" if _USE_COLOR else "",
    "dim": "\033[2m" if _USE_COLOR else "",
    "green": "\033[92m" if _USE_COLOR else "",
    "red": "\033[91m" if _USE_COLOR else "",
    "yellow": "\033[93m" if _USE_COLOR else "",
    "blue": "\033[94m" if _USE_COLOR else "",
    "magenta": "\033[95m" if _USE_COLOR else "",
    "cyan": "\033[96m" if _USE_COLOR else "",
    "white": "\033[97m" if _USE_COLOR else "",
}


def _colorize(text: str, color: str, bold: bool = False) -> str:
    """Wrap text in ANSI color codes (if enabled)."""
    if not _USE_COLOR:
        return text
    prefix = _C["bold"] if bold else ""
    return f"{prefix}{_C.get(color, '')}{text}{_C['reset']}"


def _format_pnl(pnl_pct: float) -> str:
    """Colorized P&L with win/loss emoji."""
    if pnl_pct > 0:
        return _colorize(f"  WIN  +{pnl_pct:.2f}%  🟢", "green", bold=True)
    elif pnl_pct < 0:
        return _colorize(f"  LOSS {pnl_pct:+.2f}%  🔴", "red", bold=True)
    else:
        return _colorize(f"  FLAT  {pnl_pct:+.2f}%  ⚪", "dim")


def _format_pnl_usd(pnl_usd: float) -> str:
    if pnl_usd > 0:
        return _colorize(f"+${pnl_usd:.2f}", "green", bold=True)
    elif pnl_usd < 0:
        return _colorize(f"${pnl_usd:+.2f}", "red", bold=True)
    return f"${pnl_usd:+.2f}"


def _format_reason(reason: str) -> str:
    """Colorize close reason with appropriate color."""
    if not reason:
        return "?"
    r = reason.lower()
    if r == "stop":
        return _colorize("🛑 STOP", "red", bold=True)
    if r == "target":
        return _colorize("🎯 TARGET", "green", bold=True)
    if r == "eod-3day":
        return _colorize("⏰ 3-DAY TIMEOUT", "yellow")
    if r == "manual":
        return _colorize("👆 MANUAL", "magenta")
    return _colorize(reason, "cyan")

# ---------- Helpers ----------

def parse_stop_target(
    full_md: str,
    summary_md: str,
    entry_price: float,
    side: str = "long",
) -> tuple[float | None, float | None]:
    """Extract stop_loss + target_price from LLM markdown. Returns (stop, target).

    side='long': stop below entry, target above (default 6%/6%).
    side='short': stop above entry, target below (fade-short day-trades).
    """
    text = (full_md or "") + "\n" + (summary_md or "")
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
    if side == "short":
        # Defaults + sanitize orientation for shorts
        if stop is None or stop <= entry_price:
            stop = round(entry_price * 1.06, 2)
        if target is None or target >= entry_price:
            target = round(entry_price * 0.94, 2)
    else:
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
    from src.conservative_filters import CYBER_TICKERS, TECH_SECTORS_AVOID, cyber_buy_passes, bounce_buy_passes
    con = sqlite3.connect(str(DB_PATH))
    con.row_factory = sqlite3.Row
    # For conservative-buy we need score_breakdown which isn't in the row above
    if preset == "conservative-buy":
        rows = con.execute(
            """SELECT code, score, signal_score, decision_reason, operation_advice, full_md, summary_md,
                      data_snapshot_json, score_breakdown_json
               FROM daily_report
               WHERE report_date=? AND operation_advice='買入'""",
            (report_date,),
        ).fetchall()
    elif preset == "bounce-buy":
        # Bounce BUY includes both 觀望 and 買入 (panic-sold candidates)
        rows = con.execute(
            """SELECT code, score, signal_score, decision_reason, sentiment, operation_advice, full_md, summary_md,
                      data_snapshot_json, score_breakdown_json
               FROM daily_report
               WHERE report_date=? AND operation_advice IN ('觀望', '買入')""",
            (report_date,),
        ).fetchall()
    elif preset == "value-buy":
        # Phase 9 Step 5 (2026-07-18): VALUE BUY preset. Use LR confidence filter
        # (signal_score >= 65) to only take high-confidence trades. 14d backtest:
        # 70-80 bucket = 84% WR. Threshold via env var DSA_SIG_PAPER_FLOOR.
        rows = con.execute(
            """SELECT code, score, signal_score, decision_reason, operation_advice, full_md, summary_md,
                      data_snapshot_json, score_breakdown_json, sentiment, llm_original_op, trend
               FROM daily_report
               WHERE report_date=? AND operation_advice='買入'
                 AND (decision_reason LIKE '%VALUE]%' OR decision_reason LIKE '%CONSERVATIVE]%')
                 AND signal_score >= ?""",
            (report_date, SIG_PAPER_FLOOR),
        ).fetchall()
    elif preset in ("gold-long", "fade-short"):
        # Phase 10: re-run decide() so paper works even before DB backfill.
        rows = con.execute(
            """SELECT code, score, signal_score, decision_reason, operation_advice, full_md, summary_md,
                      data_snapshot_json, score_breakdown_json, sentiment, llm_original_op, trend
               FROM daily_report
               WHERE report_date=?""",
            (report_date,),
        ).fetchall()
    else:
        rows = con.execute(
            """SELECT code, score, signal_score, decision_reason, operation_advice, full_md, summary_md, data_snapshot_json
               FROM daily_report
               WHERE report_date=? AND operation_advice='買入'""",
            (report_date,),
        ).fetchall()
    con.close()
    out = []
    for r in rows:
        code = r["code"]
        if preset in ("gold-long", "fade-short"):
            from src.signal_decision import apply_to_snapshot
            try:
                snap = json.loads(r["data_snapshot_json"] or "{}")
            except Exception:
                snap = {}
            try:
                bd = json.loads(r["score_breakdown_json"] or "{}")
            except Exception:
                bd = {}
            llm_op = r["llm_original_op"] if "llm_original_op" in r.keys() else None
            llm_op = llm_op or r["operation_advice"] or "觀望"
            sent = r["sentiment"] if "sentiment" in r.keys() else ""
            trend = r["trend"] if "trend" in r.keys() else ""
            d = apply_to_snapshot(
                llm_op=llm_op,
                llm_sentiment=sent or "",
                llm_trend=trend or "",
                score_breakdown=bd,
                data_snapshot=snap,
                sector=(snap.get("sector") or ""),
            )
            if preset == "gold-long" and d.matched_rule == "GOLD_LONG" and d.op == "買入":
                out.append(dict(r))
            elif preset == "fade-short" and d.matched_rule == "FADE_SHORT" and d.op == "賣出":
                out.append(dict(r))
        elif preset == "cyber-buy":
            # Cyber BUY v2: whitelist + anti-gapup + 52w high avoidance
            tk = code.split(".")[0]
            if tk not in CYBER_TICKERS:
                continue
            try:
                snap = json.loads(r["data_snapshot_json"]) if r["data_snapshot_json"] else {}
            except Exception:
                snap = {}
            try:
                bd = json.loads(r["score_breakdown_json"]) if r["score_breakdown_json"] else {}
            except Exception:
                bd = {}
            day_chg = snap.get("change_pct") or 0
            m_score = int(bd.get("momentum_score") or 0)
            text = (r["summary_md"] or "") + " " + (r["full_md"] or "")
            m_sent = re.search(r"·\s*(樂觀|中性|悲觀)\s*·", text)
            sent = m_sent.group(1) if m_sent else ""
            score = r["score"] or 0
            last = snap.get("last_price") or 0
            h52 = snap.get("52w_high") or 0
            passes, _ = cyber_buy_passes(tk, score, day_chg, m_score, sent, last, h52)
            if passes:
                out.append(dict(r))
        elif preset == "value-buy":
            # Phase 9 Step 5 (2026-07-18): VALUE BUY with LR confidence filter.
            # SQL already filters to signal_score >= 65 + VALUE/CONSERVATIVE rules.
            # Just pass-through here.
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
        elif preset == "bounce-buy":
            # Bounce BUY (NEW 2026-07-09): mean-reversion on panic-sold HOLD candidates
            try:
                snap = json.loads(r["data_snapshot_json"]) if r["data_snapshot_json"] else {}
            except Exception:
                snap = {}
            try:
                bd = json.loads(r["score_breakdown_json"]) if r["score_breakdown_json"] else {}
            except Exception:
                bd = {}
            day_chg = snap.get("change_pct") or 0
            m_score = int(bd.get("momentum_score") or 0)
            of_score = int(bd.get("order_flow_score") or 0)
            sentiment = r["sentiment"] or ""
            score = r["score"] or 0
            sector = (snap.get("sector") or "").strip()
            passes, _ = bounce_buy_passes(code, score, day_chg, m_score, of_score, sentiment, sector)
            if passes:
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
        side = "short" if preset == "fade-short" else "long"
        stop, target = parse_stop_target(
            sig["full_md"] or "", sig["summary_md"] or "", entry_price, side=side
        )
        if dry_run:
            dry_label = _colorize("[DRY]", "yellow", bold=True)
            print(f"    {dry_label} OPEN " + _colorize(code, "white", bold=True) + f" entry=${entry_price:.2f} stop=${stop:.2f} target=${target:.2f}")
            continue
        cur.execute(
            """INSERT INTO paper_trade
               (code, signal_date, signal_source, entry_date, entry_price,
                position_size_usd, stop_loss, target_price, score, signal_score, op_advice, status)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'open')""",
            (code, report_date, preset, report_date, entry_price,
             POSITION_SIZE_USD, stop, target, sig["score"], sig.get("signal_score"), sig["operation_advice"]),
        )
        opened += 1
        sig_score = sig.get("signal_score") or 0
        # Phase 9+ (2026-07-20): emoji + ANSI color
        if sig_score >= SIG_HIGH:
            sig_str = _colorize(f"🎯{sig_score}", "yellow", bold=True)
        elif sig_score >= SIG_PAPER_FLOOR:
            sig_str = _colorize(f"{sig_score}", "green")
        else:
            sig_str = _colorize(f"{sig_score}", "dim")
        llm_str = _colorize(str(sig['score']), "blue")
        code_str = _colorize(code, "white", bold=True)
        print(f"    {code_str} entry=${entry_price:.2f} stop=${stop:.2f} target=${target:.2f} LLM={llm_str} LR_sig={sig_str}")
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
            # 2026-08-02 P0 fix: if held >7 days with no price (delisted/data
            # gap), force close at entry price (P&L=0) to clean up ghost positions.
            # Otherwise these pile up and pollute stats forever.
            if hold_days >= 7:
                cur.execute(
                    """UPDATE paper_trade
                       SET exit_date=?, exit_price=?, close_reason=?, pnl_pct=0, pnl_usd=0, status='closed'
                       WHERE id=?""",
                    (today, entry_price, "delisted-no-price", t["id"]),
                )
                closed += 1
                print(f"    {code}: held {hold_days}d no-price → force close (delisted/no-price)")
                continue
            print(f"    {code}: no current price, hold={hold_days}d skip")
            continue
        # Check exit conditions (long vs short)
        exit_reason = None
        exit_price = cur_price
        is_short = (t["signal_source"] or "") == "fade-short"
        if is_short:
            # Short: stop above entry, target below
            if stop and cur_price >= stop:
                exit_reason = "stop"
                exit_price = stop
            elif target and cur_price <= target:
                exit_reason = "target"
                exit_price = target
            elif hold_days >= MAX_HOLD_DAYS:
                exit_reason = "eod-3day"
                exit_price = cur_price
        else:
            if stop and cur_price <= stop:
                exit_reason = "stop"
                exit_price = stop
            elif target and cur_price >= target:
                exit_reason = "target"
                exit_price = target
            elif hold_days >= MAX_HOLD_DAYS:
                exit_reason = "eod-3day"
                exit_price = cur_price
        if exit_reason is None:
            if is_short:
                pnl_unc = (entry_price - cur_price) / entry_price * 100
            else:
                pnl_unc = (cur_price - entry_price) / entry_price * 100
            print(f"    ⏳ {code}: open, current=" + _colorize(f"${cur_price:.2f}", "white") + f" pnl=" + _colorize(f"{pnl_unc:+.2f}%", "green" if pnl_unc > 0 else "red") + f" hold={hold_days}d")
            continue
        # Calculate P&L
        if is_short:
            pnl_pct = (entry_price - exit_price) / entry_price * 100
        else:
            pnl_pct = (exit_price - entry_price) / entry_price * 100
        pnl_usd = pnl_pct / 100 * POSITION_SIZE_USD
        if dry_run:
            dry_label = _colorize("[DRY]", "yellow", bold=True)
            print(f"    {dry_label} CLOSE {code} reason=" + _format_reason(exit_reason) + f" entry=${entry_price:.2f} exit=${exit_price:.2f}" + _format_pnl(pnl_pct) + f" {_format_pnl_usd(pnl_usd)}")
            continue
        cur.execute(
            """UPDATE paper_trade
               SET exit_date=?, exit_price=?, close_reason=?, pnl_pct=?, pnl_usd=?, status='closed'
               WHERE id=?""",
            (today, exit_price, exit_reason, pnl_pct, pnl_usd, t["id"]),
        )
        closed += 1
        # Phase 9+ (2026-07-20): emoji + ANSI color for win/loss
        reason_str = _format_reason(exit_reason)
        pnl_str = _format_pnl(pnl_pct)
        pnl_usd_str = _format_pnl_usd(pnl_usd)
        code_str = _colorize(code, "white", bold=True)
        print(f"    {code_str} {reason_str} entry=${entry_price:.2f} exit=${exit_price:.2f} hold={hold_days}d {pnl_str} {pnl_usd_str}")
    con.commit()
    con.close()
    return closed


def print_stats():
    """Print paper-trade performance stats (colorized)."""
    con = sqlite3.connect(str(DB_PATH))
    con.row_factory = sqlite3.Row
    cur = con.cursor()
    total = cur.execute("SELECT COUNT(*) FROM paper_trade").fetchone()[0]
    closed = cur.execute("SELECT COUNT(*) FROM paper_trade WHERE status='closed'").fetchone()[0]
    open_n = cur.execute("SELECT COUNT(*) FROM paper_trade WHERE status='open'").fetchone()[0]
    wins = cur.execute("SELECT COUNT(*) FROM paper_trade WHERE status='closed' AND pnl_pct > 0").fetchone()[0]
    losses = cur.execute("SELECT COUNT(*) FROM paper_trade WHERE status='closed' AND pnl_pct <= 0").fetchone()[0]
    total_pnl = cur.execute("SELECT COALESCE(SUM(pnl_usd), 0) FROM paper_trade WHERE status='closed'").fetchone()[0]
    print("\n" + _colorize("═" * 50, "cyan"))
    print(_colorize("  📊 Paper Trade Performance Stats", "cyan", bold=True))
    print(_colorize("═" * 50, "cyan"))
    print(f"  Total: {_colorize(str(total), 'white', bold=True)}  |  " +
          f"Open: {_colorize(str(open_n), 'yellow')}  |  " +
          f"Closed: {_colorize(str(closed), 'cyan')}")
    if closed > 0:
        wr = wins / closed * 100
        avg_win = cur.execute("SELECT COALESCE(AVG(pnl_pct), 0) FROM paper_trade WHERE status='closed' AND pnl_pct > 0").fetchone()[0]
        avg_loss = cur.execute("SELECT COALESCE(AVG(pnl_pct), 0) FROM paper_trade WHERE status='closed' AND pnl_pct <= 0").fetchone()[0]
        wr_color = "green" if wr >= 60 else "red" if wr < 50 else "yellow"
        print(f"  Wins: {_colorize(str(wins), 'green', bold=True)} 🟢  |  " +
              f"Losses: {_colorize(str(losses), 'red', bold=True)} 🔴  |  " +
              f"WR: {_colorize(f'{wr:.1f}%', wr_color, bold=True)}")
        print(f"  Avg win: {_colorize(f'+{avg_win:.2f}%', 'green', bold=True)}  |  " +
              f"Avg loss: {_colorize(f'{avg_loss:+.2f}%', 'red', bold=True)}")
        pnl_color = "green" if total_pnl > 0 else "red" if total_pnl < 0 else "yellow"
        print(f"  Total P&L: {_colorize(f'${total_pnl:+.2f}', pnl_color, bold=True)} " +
              _colorize(f"(on {closed} closed × $1000 size)", "dim"))
    print(_colorize("═" * 50, "cyan"))
    con.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--report-date", default=None, help="signal report_date (default: latest in DB)")
    ap.add_argument(
        "--preset",
        default="gold-long",
        choices=[
            "gold-long",      # Phase 10: best next-day long (~77% 1D WR)
            "fade-short",     # Phase 10: next-day short fade (~62% WR)
            "conservative-buy",
            "value-buy",
            "all-buy",
            # 2026-08-02 P0 fix: bounce-buy PAUSED — 67 closed trades at 43.3% WR
            # (-0.24% avg, -$160). Re-enable only after independent OOS validation
            # with n>30 trades AND positive EV. See Grok analysis 2026-08-02.
            # "bounce-buy",
            # "cyber-buy",  # also paused — 5 trades at -2.97% avg, too small n
        ],
    )
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
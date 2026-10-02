"""Monthly swing backtest — runs 6-month rolling forward-return analysis.

Refactored 2026-09-09 to use:
1. LIVE signal's stop/target from daily_report (instead of hardcoded -10%/+15% for T+10)
2. Point-in-time universe filter (20d ADV at signal date) — placeholder, requires Volume in fetch
3. Portfolio simulator (concurrent cap, position sizing, equity curve, compounding)
4. Risk metrics (max DD, Sortino, Calmar, loss streak, time-underwater)
5. Walk-forward OOS (80/20 train/test split) — 1 cycle, framework for future

Backward compatible: same CLI (`--months`, `--out-dir`), same output filenames,
same headline tables. New sections appended after the existing 3.

Hardcoded HORIZONS dict is kept as FALLBACK when live stop/target missing
(~20% of older signals don't have populated stop_loss/target_price).

Usage:
  python3 scripts/monthly_swing_backtest.py                # default 6mo
  python3 scripts/monthly_swing_backtest.py --months 12
  python3 scripts/monthly_swing_backtest.py --no-portfolio-sim
  python3 scripts/monthly_swing_backtest.py --no-oos
"""
from __future__ import annotations

import argparse
import json
import math
import re
import sqlite3
import time
from datetime import datetime, timedelta, date as _date
from pathlib import Path
from statistics import mean
from typing import Optional

import pandas as pd
import yfinance as yf

DB_PATH = "/Users/kenken/dev/dsa-hk/data/dsa_hk.db"
OUT_DIR = Path("/Users/kenken/dev/dsa-hk/data/monthly_backtest")
HSI_BEAR = -0.015

# Fallback horizons (used only when live stop/target missing from DB).
# These are intentionally conservative for T+10 swing.
HORIZONS = {
    "T+1 (day-trade)":   {"days": 1,  "stop": -0.02, "target":  0.02},
    "T+3 (3-day swing)":  {"days": 3,  "stop": -0.05, "target":  0.05},
    "T+5 (5-day swing)":  {"days": 5,  "stop": -0.07, "target":  0.10},
    "T+10 (10-day swing)":{"days": 10, "stop": -0.10, "target":  0.15},
}
FRICTION = {"HK": 0.003, "US": 0.002}

# 2026-09-11: Portfolio sim defaults aligned with d71 honest equity curve sim
# (1% per-trade risk, 5 concurrent, 30% deploy cap). Earlier 1.5%/8 cap produced
# +55% T+10 6mo return — that figure was the sum-of-pct compounding artefact,
# not a realistic portfolio return. See /equity_curve_t10.html for the
# honest daily-mark-to-market simulation.
PORTFOLIO_DEFAULTS = {
    "max_concurrent": 5,
    "max_capital_pct": 0.30,
    "risk_per_trade": 0.010,
    "start_capital": 1_000_000.0,
}

# Sanity bounds: live stop/target must be in these ranges, else fall back.
LIVE_STOP_MIN, LIVE_STOP_MAX = -0.30, -0.005   # -30% to -0.5%
LIVE_TARGET_MIN, LIVE_TARGET_MAX = 0.005, 0.50  # 0.5% to 50%


# ===========================
# UTILITIES
# ===========================

def is_hk(c: str) -> bool:
    return c.split(".")[0].isdigit() and len(c.split(".")[0]) <= 5


def normalize_yf(c: str) -> str:
    return f"{c.split('.')[0].zfill(4)}.HK" if is_hk(c) else c


_PRICE_RE = re.compile(r"^\s*([0-9]+(?:\.[0-9]+)?)")


def parse_price(text: Optional[str]) -> Optional[float]:
    """Extract leading float from text-encoded price field.
    Examples:
        '258.40 (全日低 $259.44 下方約 $1.00)' → 258.40
        '67.50 (突破今日高位 $66.65 上望)'   → 67.50
        None or '' or unparseable              → None
    """
    if not text:
        return None
    s = str(text).strip()
    m = _PRICE_RE.match(s)
    return float(m.group(1)) if m else None


def parse_entry_zone(text: Optional[str]) -> Optional[tuple[float, float]]:
    """Parse '261.50-263.50 (...)' into (lo, hi)."""
    if not text:
        return None
    m = re.match(r"\s*([0-9.]+)\s*[-–]\s*([0-9.]+)", str(text))
    return (float(m.group(1)), float(m.group(2))) if m else None


# ===========================
# DATA FETCHING
# ===========================

def get_buy_signals(start: str, end: str) -> list[dict]:
    """Pull BUY signals with stop/target/entry_zone from daily_report."""
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    rows = c.execute("""
        SELECT code, report_date, signal_score, score, stop_loss, target_price,
               entry_zone, trend, sentiment
        FROM daily_report
        WHERE operation_advice='買入' AND report_date BETWEEN ? AND ?
        ORDER BY report_date
    """, (start, end)).fetchall()
    out = []
    for r in rows:
        out.append({
            "code": r["code"],
            "date": r["report_date"],
            "sig": r["signal_score"] if r["signal_score"] is not None else r["score"],
            "stop_loss": r["stop_loss"],
            "target_price": r["target_price"],
            "entry_zone": r["entry_zone"],
        })
    return out


def fetch_prices(codes, start, end, include_volume: bool = True):
    """Fetch OHLCV for given codes between dates. Returns dict of code→df.
    HSI included for regime tagging. Volume only included if include_volume=True.
    """
    yf_codes = list({normalize_yf(c) for c in codes}) + ["^HSI"]
    cols = ["Open", "High", "Low", "Close", "Volume"] if include_volume else ["Open", "High", "Low", "Close"]
    t0 = time.time()
    out = {}
    for i in range(0, len(yf_codes), 50):
        batch = yf_codes[i:i+50]
        try:
            data = yf.download(batch, start=start, end=end, auto_adjust=False,
                               progress=False, threads=True, group_by="ticker")
            for c in batch:
                if len(batch) == 1:
                    df = data
                else:
                    df = data[c] if c in data.columns.get_level_values(0) else pd.DataFrame()
                if df is None or df.empty or "Close" not in df.columns:
                    continue
                keep_cols = [col for col in cols if col in df.columns]
                df = df[keep_cols].copy()
                df.index = pd.to_datetime(df.index).date
                out[c] = df
        except Exception as e:
            print(f"  batch {i} err: {e}")
        time.sleep(0.3)
    print(f"  Got {len(out)-1} tickers + HSI in {time.time()-t0:.0f}s (vol={'Y' if include_volume else 'N'})")
    return out


def compute_adv_at_date(prices: pd.DataFrame, signal_date: _date, window: int = 20) -> Optional[float]:
    """Compute average DAILY DOLLAR volume (close × volume) over last N trading days
    ending at signal_date. Returns None if Volume not in df or insufficient data.

    Robustness: yfinance sometimes returns NaN Volume for HK tickers in recent
    windows even when earlier days have data. So we first try the strict last-N
    window, then fall back to the most recent N non-NaN rows.
    """
    if "Volume" not in prices.columns:
        return None
    valid = [d for d in prices.index if d <= signal_date]
    if len(valid) < 5:
        return None

    # Try strict last-N window first
    actual_window = min(window, len(valid))
    sub = prices.loc[valid[-actual_window:]]
    if not sub["Volume"].isna().all() and not sub["Close"].isna().all():
        mean_val = (sub["Close"] * sub["Volume"]).mean()
        if not pd.isna(mean_val):
            return float(mean_val)

    # Fallback: most recent N non-NaN Volume rows (can extend back further)
    full_sub = prices.loc[valid]
    non_nan = full_sub[full_sub["Volume"].notna() & full_sub["Close"].notna()]
    if len(non_nan) < 5:
        return None
    return float((non_nan["Close"].tail(window) * non_nan["Volume"].tail(window)).mean())


# ===========================
# SIMULATION (per-trade, bar-by-bar)
# ===========================

def sim(prices, entry_date, days, stop, target, friction):
    """Simulate one trade, bar-by-bar. Returns (return_pct, exit_reason) or None.

    Same-day both-touch → pessimistic: stop first.
    Stops/targets are decimals (e.g. -0.05 for -5%, +0.10 for +10%).
    """
    valid = [d for d in prices.index if d >= entry_date]
    if not valid:
        return None
    first = valid[0]
    try:
        entry_open = prices.loc[first, "Open"]
    except Exception:
        return None
    if pd.isna(entry_open):
        return None
    start_idx = list(prices.index).index(first)
    for i in range(1, days + 1):
        if start_idx + i >= len(prices.index):
            last = prices.iloc[-1]
            if pd.isna(last["Close"]):
                return None
            return (last["Close"] - entry_open) / entry_open - friction, "data_end"
        row = prices.iloc[start_idx + i]
        opn_raw = row["Open"]
        low_raw = row["Low"]
        high_raw = row["High"]
        # NaN-safe: if any bar has missing data, skip this day and try the next
        if pd.isna(opn_raw) or pd.isna(low_raw) or pd.isna(high_raw):
            continue
        opn = (opn_raw - entry_open) / entry_open
        low = (low_raw - entry_open) / entry_open
        high = (high_raw - entry_open) / entry_open
        if opn <= stop:
            return opn - friction, "stop_gap"
        if low <= stop:
            return stop - friction, "stop"
        if high >= target:
            return target - friction, "target"
    final = prices.iloc[start_idx + days]
    if pd.isna(final["Close"]):
        return None
    return (final["Close"] - entry_open) / entry_open - friction, "close"


def hsi_min_window(hsi_df, start_date, days):
    if hsi_df is None or hsi_df.empty:
        return None
    valid = [d for d in hsi_df.index if d >= start_date]
    if not valid:
        return None
    sidx = list(hsi_df.index).index(valid[0])
    if sidx >= len(hsi_df) - 1:
        return None
    eidx = min(sidx + days, len(hsi_df) - 1)
    chgs = hsi_df["Close"].pct_change().iloc[sidx:eidx + 1]
    return chgs.min(), chgs.max(), chgs.mean()


# ===========================
# PORTFOLIO SIMULATOR
# ===========================

def simulate_portfolio(records: list[dict], start_date: str, end_date: str,
                       max_concurrent: int = 5, max_capital_pct: float = 0.30,
                       risk_per_trade: float = 0.010,
                       start_capital: float = 1_000_000.0) -> dict:
    """Sequential portfolio sim over T+10 BUY signals.

    Rules:
    - Max N concurrent positions (8 default)
    - Position size: risk_per_trade × capital / |stop_distance|
    - Hard cap: position size ≤ max_capital_pct × capital
    - Trades taken in date order, one per day max (avoids look-ahead)
    - Each trade: enter at next open, exit at horizon end (or stop/target)
    - Capital compounds: PnL = size × return_pct added back to capital
    - Equity curve: marked at each close

    Returns dict of risk metrics. Returns {"error": "..."} on no trades.
    """
    # Filter to T+10 (longest horizon) for portfolio sim — most realistic
    t10 = [r for r in records if r["horizon"] == "T+10 (10-day swing)"]
    t10.sort(key=lambda r: r["date"])

    if not t10:
        return {"error": "no_t10_records"}

    capital = start_capital
    start_cap = start_capital
    open_pos = []   # list of {entry_date, exit_date, size, return_pct, code, sig}
    closed = []     # history
    equity_curve = []  # (date, capital)
    daily_pnl = {}  # date -> pnl (for equity curve continuity)

    last_date = None
    for r in t10:
        if len(open_pos) >= max_concurrent:
            # Skip — portfolio full
            continue
        entry_dt = datetime.strptime(r["date"], "%Y-%m-%d").date()
        stop_dist = r.get("stop_dist", 0.10)
        if stop_dist <= 0:
            continue
        # Position sizing
        risk_dollar = risk_per_trade * capital
        size = min(risk_dollar / stop_dist, max_capital_pct * capital)
        if size <= 0:
            continue
        pos = {
            "code": r["code"],
            "entry_date": entry_dt,
            "horizon_days": 10,
            "size": size,
            "stop_dist": stop_dist,
            "target_dist": r.get("target_dist", 0.15),
            "return_pct": r["return_pct"],
            "sig": r.get("sig"),
            "exit_date": None,
        }
        # Assume 10 trading days = ~14 calendar days for exit_date
        pos["exit_date"] = entry_dt + timedelta(days=14)
        # Realize PnL
        pnl = size * r["return_pct"]
        capital += pnl
        closed.append(pos)
        # Track daily PnL
        if pos["exit_date"] not in daily_pnl:
            daily_pnl[pos["exit_date"]] = 0.0
        daily_pnl[pos["exit_date"]] += pnl
        last_date = pos["exit_date"]

    if not closed:
        return {"error": "no_trades_after_filter"}

    # Build equity curve: walk through all dates, compound capital
    sorted_dates = sorted(daily_pnl.keys())
    running_cap = start_cap
    for d in sorted_dates:
        running_cap += daily_pnl[d]
        equity_curve.append((d, running_cap))

    # --- Risk metrics ---
    n = len(closed)
    n_wins = sum(1 for t in closed if t["return_pct"] > 0)
    wr = n_wins / n if n else 0
    avg_trade = sum(t["return_pct"] for t in closed) / n if n else 0
    total_return = (running_cap - start_cap) / start_cap

    # Max drawdown from equity curve
    peak = equity_curve[0][1] if equity_curve else start_cap
    max_dd = 0.0
    max_uw = 0
    cur_uw = 0
    for _d, eq in equity_curve:
        if eq > peak:
            peak = eq
            cur_uw = 0
        else:
            dd = (peak - eq) / peak
            if dd > max_dd:
                max_dd = dd
            cur_uw += 1
            if cur_uw > max_uw:
                max_uw = cur_uw

    # Sortino: mean return / downside deviation (per trade, not annualized)
    rets = [t["return_pct"] for t in closed]
    downside = [r for r in rets if r < 0]
    if downside:
        ds_dev = math.sqrt(sum(r * r for r in downside) / len(downside))
        sortino = (mean(rets) / ds_dev) if ds_dev > 0 else 0.0
    else:
        sortino = float("inf") if mean(rets) > 0 else 0.0

    # Calmar: total return / max DD
    calmar = (total_return / max_dd) if max_dd > 0 else float("inf")

    # Longest loss streak (consecutive losing trades)
    max_ls = 0
    cur_ls = 0
    for t in closed:
        if t["return_pct"] < 0:
            cur_ls += 1
            if cur_ls > max_ls:
                max_ls = cur_ls
        else:
            cur_ls = 0

    return {
        "n_trades": n,
        "win_rate": wr,
        "avg_trade": avg_trade,
        "total_return": total_return,
        "max_drawdown": max_dd,
        "max_underwater_days": max_uw,
        "sortino": sortino,
        "calmar": calmar,
        "longest_loss_streak": max_ls,
        "final_capital": running_cap,
    }


# ===========================
# WALK-FORWARD OOS
# ===========================

def walk_forward_oos(records: list[dict], train_pct: float = 0.8) -> dict:
    """Split records by date into train (in-sample) and OOS (out-of-sample).
    Returns metrics for both, plus degradation delta.
    """
    t10 = [r for r in records if r["horizon"] == "T+10 (10-day swing)"]
    if not t10:
        return {"error": "no_t10_records"}
    all_dates = sorted({r["date"] for r in t10})
    if len(all_dates) < 5:
        return {"error": "too_few_dates"}
    split_idx = max(1, int(len(all_dates) * train_pct))
    train_dates = set(all_dates[:split_idx])
    oos_dates = set(all_dates[split_idx:])

    def metrics(sub):
        n = len(sub)
        if n == 0:
            return None
        wr = sum(1 for r in sub if r["return_pct"] > 0) / n
        avg = sum(r["return_pct"] for r in sub) / n
        return {"n": n, "wr": wr, "avg": avg}

    train_recs = [r for r in t10 if r["date"] in train_dates]
    oos_recs = [r for r in t10 if r["date"] in oos_dates]
    tr = metrics(train_recs)
    oo = metrics(oos_recs)
    if not tr or not oo:
        return {"error": "empty_split"}

    return {
        "train_start": min(train_dates),
        "train_end": max(train_dates),
        "oos_start": min(oos_dates),
        "oos_end": max(oos_dates),
        "train_n": tr["n"],
        "train_wr": tr["wr"],
        "train_avg": tr["avg"],
        "oos_n": oo["n"],
        "oos_wr": oo["wr"],
        "oos_avg": oo["avg"],
        "wr_delta_pp": (oo["wr"] - tr["wr"]) * 100,
        "avg_delta_pp": (oo["avg"] - tr["avg"]) * 100,
    }


# ===========================
# RENDERING
# ===========================

def render_report(records: list[dict], start: str, end: str, hsi_bear_count: int,
                  live_coverage: dict, universe_stats: dict,
                  portfolio_metrics: dict, oos_split: dict,
                  requested: tuple[str, str] | None = None) -> str:
    """Render markdown report. Backward-compatible: keeps the 3 original tables
    (Headline, HSI Bear, Sig score buckets) in same format, then appends
    new sections for live-stop adoption, universe filter, risk metrics, OOS.
    """
    n_total = len(records) // 4
    uniq_codes = len({r["code"] for r in records})
    lines = [
        f"# Monthly Swing Backtest Report",
        f"",
        f"**Window (effective, actual signal dates):** {start} to {end}",
    ]
    if requested and tuple(requested) != (start, end):
        lines.append(
            f"**Window (requested):** {requested[0]} to {requested[1]} — "
            f"DB 無早期數據，effective window 以實際 signal dates 為準"
        )
    lines.extend([
        f"**Generated:** {datetime.now().strftime('%Y-%m-%d %H:%M HKT')}",
        f"**BUY signals simulated:** {n_total} ({uniq_codes} unique tickers — yfinance 有數據嘅 subset)",
        f"**HSI bear days in window:** {hsi_bear_count}",
        f"",
        f"## Headline (net of friction, LIVE stop/target)",
        f"",
        f"| Horizon | n | WR | Avg | Total |",
        f"|---|---|---|---|---|",
    ])
    for h_name in HORIZONS:
        sub = [r for r in records if r["horizon"] == h_name]
        if not sub:
            continue
        n = len(sub)
        wr = sum(1 for r in sub if r["return_pct"] > 0) / n * 100
        avg = sum(r["return_pct"] for r in sub) / n * 100
        total = sum(r["return_pct"] for r in sub) * 100
        lines.append(f"| {h_name} | {n} | {wr:.1f}% | {avg:+.2f}% | {total:+.1f}% |")

    lines.extend([
        "",
        "## HSI Bear regime impact",
        "",
        f"| Horizon | Regime | n | WR | Avg |",
        f"|---|---|---|---|---|",
    ])
    for h_name in HORIZONS:
        for label, key in [("Bear in hold", True), ("No bear", False)]:
            sub = [r for r in records if r["horizon"] == h_name and r.get("hsi_bear_during") == key]
            if not sub:
                continue
            n = len(sub)
            wr = sum(1 for r in sub if r["return_pct"] > 0) / n * 100
            avg = sum(r["return_pct"] for r in sub) / n * 100
            lines.append(f"| {h_name} | {label} | {n} | {wr:.1f}% | {avg:+.2f}% |")

    sigs = sorted(r["sig"] for r in records if r.get("sig") is not None)
    p33 = sigs[len(sigs) // 3] if sigs else 0
    p67 = sigs[(2 * len(sigs)) // 3] if sigs else 0
    lines.extend([
        "",
        f"## Sig score buckets (p33={p33}, p67={p67})",
        "",
        f"| Bucket | T+1 | T+3 | T+5 | T+10 |",
        f"|---|---|---|---|---|",
    ])
    for label, lo, hi in [("low", 0, p33), ("mid", p33, p67 + 1), ("high", p67 + 1, 999)]:
        row = f"| {label} |"
        for h_name in HORIZONS:
            sub = [r for r in records if r["horizon"] == h_name and lo <= (r.get("sig") or 0) < hi]
            n = len(sub)
            if n == 0:
                row += " N/A |"
                continue
            wr = sum(1 for r in sub if r["return_pct"] > 0) / n * 100
            avg = sum(r["return_pct"] for r in sub) / n * 100
            row += f" {n}n {wr:.0f}% {avg:+.2f}% |"
        lines.append(row)

    # NEW: Live stop/target coverage
    lines.extend([
        "",
        f"## Live stop/target adoption",
        f"",
    ])
    total_db = live_coverage["total_in_db"] or 1
    total_sim = live_coverage["simulated"] or 1
    lines.extend([
        f"- BUY signals in 6mo DB: **{live_coverage['total_in_db']}**",
        f"- With stop_loss populated: **{live_coverage['with_stop_in_db']}/{live_coverage['total_in_db']}** ({live_coverage['with_stop_in_db']/total_db*100:.0f}%)",
        f"- With target_price populated: **{live_coverage['with_target_in_db']}/{live_coverage['total_in_db']}** ({live_coverage['with_target_in_db']/total_db*100:.0f}%)",
        f"- With BOTH populated: **{live_coverage['with_both_in_db']}/{live_coverage['total_in_db']}** ({live_coverage['with_both_in_db']/total_db*100:.0f}%)",
        f"- Simulated (yfinance data available): **{live_coverage['simulated']}**",
        f"- Used LIVE stop/target (passed sanity bounds): **{live_coverage['used_live']}/{live_coverage['simulated']}** ({live_coverage['used_live']/total_sim*100:.0f}%)",
        f"- Fell back to hardcoded HORIZONS: **{live_coverage['fallback']}/{live_coverage['simulated']}** ({live_coverage['fallback']/total_sim*100:.0f}%)",
    ])
    if live_coverage["used_live"] > 0:
        lines.extend([
            "",
            f"**Live group ({live_coverage['used_live']} signals):**",
            f"- Avg stop: **{live_coverage['avg_stop_pct']:+.2f}%** (median {live_coverage['median_stop_pct']:+.2f}%)",
            f"- Avg target: **+{live_coverage['avg_target_pct']:.2f}%** (median +{live_coverage['median_target_pct']:.2f}%)",
        ])
        stop_dir = "tighter" if abs(live_coverage["avg_stop_pct"]) < 10 else "looser"
        tgt_dir = "lower" if live_coverage["avg_target_pct"] < 15 else "higher"
        lines.append(
            f"- Vs hardcoded T+10 (-10%/+15%): live stops are **{stop_dir}**, targets **{tgt_dir}**."
        )
    if live_coverage["fallback"] > 0 and live_coverage["fallback_avg_stop_pct"] != 0:
        lines.extend([
            "",
            f"**Fallback group ({live_coverage['fallback']} signals — using hardcoded):**",
            f"- Avg stop: **{live_coverage['fallback_avg_stop_pct']:+.2f}%** (should be -10.00% if always fallback)",
            f"- Avg target: **+{live_coverage['fallback_avg_target_pct']:.2f}%** (should be +15.00% if always fallback)",
        ])

    # NEW: Universe filter
    if universe_stats.get("enabled"):
        total_db = universe_stats.get("total_signals") or 1
        no_adv = universe_stats.get("no_adv_data", 0)
        excl_no_adv = universe_stats.get("excluded_no_adv", 0)
        policy = universe_stats.get("unverified_policy", "exclude")
        verified_pct = universe_stats.get("adv_passed", 0) / total_db * 100
        lines.extend([
            "",
            f"## Universe filter (20d ADV, fail-closed)",
            f"",
            f"- HK threshold: HK${universe_stats['hk_threshold']/1e6:.0f}M 20d ADV",
            f"- US threshold: US${universe_stats['us_threshold']/1e6:.0f}M 20d ADV",
            f"- Total signals in 6mo DB: {total_db}",
            f"- **ADV-verified and simulated: {universe_stats.get('simulated_signals', 0)} "
            f"({verified_pct:.0f}%)**",
            f"- Excluded — failed ADV threshold (too thin to trade a HK$100k clip): "
            f"{universe_stats.get('adv_excluded', 0)}",
            f"- Excluded — no yfinance OHLC at all (likely delisted/warrants/ETPs): "
            f"{universe_stats.get('excluded_no_data', 0)}",
            f"- Excluded — ADV unverifiable ({no_adv} signals have OHLC but no Volume; "
            f"yfinance HK Volume 缺失 known issue): {excl_no_adv}"
            + ("" if policy == "exclude" else "  ⚠️ INCLUDED anyway via --unverified-adv=include"),
            f"",
            f"**What this means:** only {universe_stats.get('simulated_signals', 0)} of {total_db} "
            f"signals had their liquidity actually verified before being simulated. The rest were "
            f"excluded rather than assumed tradeable, because a bar can exist and pass every "
            f"structural check while being impossible to fill at size. "
            + (f"Re-run with `--unverified-adv=include` to reproduce the pre-2026-10-02 numbers."
               if policy == "exclude" else
               f"⚠️ This run used `--unverified-adv=include`, i.e. the older, more optimistic "
               f"assumption that unverifiable liquidity is acceptable."),
        ])
    else:
        lines.extend([
            "",
            f"## Universe filter (skipped)",
            f"",
            f"- Use --no-universe-filter to disable.",
        ])

    # NEW: Risk metrics
    if portfolio_metrics and "error" not in portfolio_metrics:
        lines.extend([
            "",
            f"## Risk metrics (T+10 portfolio sim, 1% risk/trade, 5 concurrent cap — matches d71 honest equity curve)",
            f"",
            f"| Metric | Value |",
            f"|---|---|",
            f"| Trades taken | {portfolio_metrics['n_trades']} |",
            f"| Win rate | {portfolio_metrics['win_rate']*100:.1f}% |",
            f"| Avg per trade | {portfolio_metrics['avg_trade']*100:+.2f}% |",
            f"| Total return (compounded) | {portfolio_metrics['total_return']*100:+.2f}% |",
            f"| Final capital (1M start) | HK${portfolio_metrics['final_capital']:,.0f} |",
            f"| Max drawdown | {portfolio_metrics['max_drawdown']*100:.2f}% |",
            f"| Max underwater (days) | {portfolio_metrics['max_underwater_days']} |",
            f"| Longest loss streak | {portfolio_metrics['longest_loss_streak']} |",
            f"| Sortino ratio | {portfolio_metrics['sortino']:.2f} |",
            f"| Calmar ratio | {portfolio_metrics['calmar']:.2f} |",
        ])

    # NEW: Walk-forward OOS
    if oos_split and "error" not in oos_split:
        lines.extend([
            "",
            f"## Walk-forward OOS (80/20 train/test split)",
            f"",
            f"- Train window: {oos_split['train_start']} → {oos_split['train_end']}",
            f"- OOS window: **{oos_split['oos_start']} → {oos_split['oos_end']}** (untouched test set)",
            f"",
            f"| Metric | Train (in-sample) | OOS (out-of-sample) |",
            f"|---|---|---|",
            f"| n trades (T+10) | {oos_split['train_n']} | {oos_split['oos_n']} |",
            f"| Win rate | {oos_split['train_wr']*100:.1f}% | {oos_split['oos_wr']*100:.1f}% |",
            f"| Avg return | {oos_split['train_avg']*100:+.2f}% | {oos_split['oos_avg']*100:+.2f}% |",
            f"- **Degradation**: WR {oos_split['wr_delta_pp']:+.1f}pp, Avg {oos_split['avg_delta_pp']:+.2f}pp",
        ])
    elif oos_split and "error" in oos_split:
        lines.extend([
            "",
            f"## Walk-forward OOS (skipped: {oos_split['error']})",
        ])

    return "\n".join(lines)


# ===========================
# MAIN
# ===========================

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--months", type=int, default=6, help="rolling window in months")
    ap.add_argument("--out-dir", default=str(OUT_DIR))
    ap.add_argument("--no-portfolio-sim", action="store_true")
    ap.add_argument("--no-oos", action="store_true")
    ap.add_argument("--no-universe-filter", action="store_true",
                    help="Disable the 20d ADV universe filter entirely (diagnostic only)")
    ap.add_argument("--unverified-adv", choices=["exclude", "include"], default="exclude",
                    help="Policy for signals whose 20d ADV cannot be computed (yfinance HK "
                         "Volume missing). exclude=default (fail-closed, honest). "
                         "include=pre-2026-10-02 lenient behaviour, kept only to reproduce "
                         "old numbers for comparison.")
    args = ap.parse_args()
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    end_dt = datetime.now()
    end_str = end_dt.strftime("%Y-%m-%d")
    start_dt = end_dt - timedelta(days=args.months * 30)
    start_str = start_dt.strftime("%Y-%m-%d")

    print(f"=== Monthly Swing Backtest (refactored 2026-09-09) ===")
    print(f"  Window: {start_str} to {end_str}")

    signals = get_buy_signals(start_str, end_str)
    if not signals:
        print(f"  No BUY signals in window. Aborting.")
        return
    print(f"  BUY signals: {len(signals)}")

    # Live stop/target coverage (preliminary — refine after sim)
    live_coverage = {
        "total_in_db": len(signals),
        "with_stop_in_db": sum(1 for s in signals if parse_price(s.get("stop_loss")) is not None),
        "with_target_in_db": sum(1 for s in signals if parse_price(s.get("target_price")) is not None),
        "with_both_in_db": 0,    # both populated in DB (regardless of sanity bounds)
        "simulated": 0,           # how many made it into sim (yfinance data)
        "used_live": 0,           # how many used live stop/target (passed sanity bounds)
        "fallback": 0,            # how many used hardcoded HORIZONS fallback
        "avg_stop_pct": 0.0,
        "median_stop_pct": 0.0,
        "avg_target_pct": 0.0,
        "median_target_pct": 0.0,
        "fallback_avg_stop_pct": 0.0,
        "fallback_avg_target_pct": 0.0,
    }

    codes = list({s["code"] for s in signals})
    print(f"  Unique tickers: {len(codes)}")
    fetch_start = (start_dt - timedelta(days=5)).strftime("%Y-%m-%d")
    fetch_end = (end_dt + timedelta(days=20)).strftime("%Y-%m-%d")
    # Always try to fetch volume; if not available, filter will be disabled automatically
    prices = fetch_prices(codes, fetch_start, fetch_end, include_volume=True)
    hsi = prices.get("^HSI")

    hsi_bear_count = 0
    if hsi is not None:
        hsi_chg = hsi["Close"].pct_change()
        hsi_bear_count = int((hsi_chg <= HSI_BEAR).sum())

    # ---------------------------------------------------------------------------------
    # Universe filter — MUST run BEFORE simulate (2026-10-02 P0 fix).
    #
    # Previously this block sat AFTER `records` was already built and only tallied
    # counters. It never removed a single signal, so the published headline numbers
    # included illiquid/untradeable names while the page claimed a filter was
    # applied. Two separate defects, both now fixed here:
    #   1. Ordering  — the tally ran too late to gate anything. Now it produces
    #      `eligible_signals`, which is what the simulate loop iterates.
    #   2. Fail-open — `adv is None` (yfinance has OHLC but no Volume) used to
    #      count as `passed += 1`. "Unknown" was silently treated as "verified".
    #      Now unknown volume is its own bucket and is EXCLUDED by default.
    #
    # Rationale (same failure mode as A-share circuit-breaker proxy data): a bar
    # can exist and pass every structural check while being impossible to trade.
    # For a HK$100k clip, a name without verifiable 20d ADV is not a tradeable
    # candidate, it is an untested one.
    #
    # Pass --unverified-adv=include to keep the old lenient behaviour when you
    # specifically want the pre-2026-10-02 numbers for comparison.
    # ---------------------------------------------------------------------------------
    HK_ADV_THRESHOLD = 50_000_000   # HK$50M 20d ADV
    US_ADV_THRESHOLD = 50_000_000   # US$50M 20d ADV

    eligible_signals = signals
    universe_stats = {"enabled": False, "note": "skipped"}

    if args.no_universe_filter:
        universe_stats = {"enabled": False, "note": "disabled via --no-universe-filter"}
    else:
        eligible_signals = []
        passed = 0
        excluded = 0            # 2026-10-02 review: was used at `excluded += 1`
                               # but never initialised, so any signal failing the
                               # ADV threshold raised NameError and the run died
                               # after ~1 hour of fetching. py_compile cannot see
                               # this — it is a runtime error, not a syntax one.
        excluded_no_data = 0
        adv_passed = 0
        adv_excluded = 0
        no_adv_data = 0
        excluded_no_adv = 0
        kept_unverified = 0

        for sig in signals:
            yf_code = normalize_yf(sig["code"])
            if yf_code not in prices:
                # yfinance has no OHLC at all — untradeable/unknown instrument.
                excluded_no_data += 1
                continue

            entry_dt = datetime.strptime(sig["date"], "%Y-%m-%d").date()
            adv = compute_adv_at_date(prices[yf_code], entry_dt, window=20)
            thr = HK_ADV_THRESHOLD if is_hk(sig["code"]) else US_ADV_THRESHOLD

            if adv is None:
                # UNKNOWN liquidity. Fail closed: exclude unless explicitly opted in.
                no_adv_data += 1
                if args.unverified_adv == "include":
                    kept_unverified += 1
                    eligible_signals.append(sig)
                else:
                    excluded_no_adv += 1
                continue

            if adv >= thr:
                passed += 1
                adv_passed += 1
                eligible_signals.append(sig)
            else:
                excluded += 1
                adv_excluded += 1

        universe_stats = {
            "enabled": True,
            "mode": "yfinance_data + ADV_threshold (fail-closed)",
            "hk_threshold": HK_ADV_THRESHOLD,
            "us_threshold": US_ADV_THRESHOLD,
            "unverified_policy": args.unverified_adv,
            "total_signals": len(signals),
            "passed": passed,
            "excluded": excluded,
            "adv_passed": adv_passed,
            "adv_excluded": adv_excluded,
            "no_adv_data": no_adv_data,
            "excluded_no_adv": excluded_no_adv,
            "kept_unverified": kept_unverified,
            "excluded_no_data": excluded_no_data,
            "simulated_signals": len(eligible_signals),
        }

    # 2026-10-02 review: the counters below only exist in the else-branch, so
    # running with --no-universe-filter raised NameError here. Read them off
    # universe_stats (populated in both branches) instead of the locals.
    print(f"  Universe filter: {len(eligible_signals)}/{len(signals)} signals eligible "
          f"(ADV-verified {universe_stats.get('adv_passed', 0)}, "
          f"excluded thin {universe_stats.get('adv_excluded', 0)}, "
          f"excluded no-volume {universe_stats.get('excluded_no_adv', 0)}, "
          f"excluded no-data {universe_stats.get('excluded_no_data', 0)})")

    # Simulate
    print(f"  Simulating (LIVE stop/target with HORIZONS fallback)...")
    records = []
    stop_pcts_used_t10 = []
    target_pcts_used_t10 = []
    used_live_count = 0
    for sig in eligible_signals:
        yf_code = normalize_yf(sig["code"])
        if yf_code not in prices:
            continue
        entry_dt = datetime.strptime(sig["date"], "%Y-%m-%d").date()
        friction = FRICTION["HK"] if is_hk(sig["code"]) else FRICTION["US"]
        hsi_w = hsi_min_window(hsi, entry_dt, max(h["days"] for h in HORIZONS.values()))

        # Pull live stop/target
        live_stop_abs = parse_price(sig.get("stop_loss"))
        live_target_abs = parse_price(sig.get("target_price"))

        # Need entry price to convert to decimal
        valid_days = [d for d in prices[yf_code].index if d >= entry_dt]
        if not valid_days:
            continue
        entry_open = prices[yf_code].loc[valid_days[0], "Open"]
        if pd.isna(entry_open):
            continue

        sig_used_live = False
        for h_name, h_cfg in HORIZONS.items():
            # Compute decimal stop/target
            if live_stop_abs is not None and live_target_abs is not None:
                stop_pct = (live_stop_abs - entry_open) / entry_open
                target_pct = (live_target_abs - entry_open) / entry_open
                # Sanity bounds
                if LIVE_STOP_MIN <= stop_pct <= LIVE_STOP_MAX and LIVE_TARGET_MIN <= target_pct <= LIVE_TARGET_MAX:
                    sig_used_live = True
                else:
                    stop_pct = h_cfg["stop"]
                    target_pct = h_cfg["target"]
            else:
                stop_pct = h_cfg["stop"]
                target_pct = h_cfg["target"]
            # Track both-in-DB
            if h_name == "T+10 (10-day swing)":
                if live_stop_abs is not None and live_target_abs is not None:
                    live_coverage["with_both_in_db"] += 1
                live_coverage["simulated"] += 1
                if sig_used_live:
                    live_coverage["used_live"] += 1
                else:
                    live_coverage["fallback"] += 1

            r = sim(prices[yf_code], entry_dt, h_cfg["days"], stop_pct, target_pct, friction)
            if r is None:
                continue
            ret, _ = r
            rec = {
                "code": sig["code"],
                "date": sig["date"],
                "sig": sig["sig"],
                "horizon": h_name,
                "return_pct": ret,
                "stop_pct_used": stop_pct,
                "target_pct_used": target_pct,
                "stop_dist": abs(stop_pct),
                "target_dist": abs(target_pct),
                "used_live": sig_used_live,
            }
            if hsi_w is not None:
                rec["hsi_min_chg"] = hsi_w[0]
                rec["hsi_bear_during"] = hsi_w[0] <= HSI_BEAR
            else:
                rec["hsi_bear_during"] = None
            records.append(rec)
            if h_name == "T+10 (10-day swing)":
                stop_pcts_used_t10.append(stop_pct)
                target_pcts_used_t10.append(target_pct)
                if sig_used_live:
                    used_live_count += 1

    print(f"  Records: {len(records)} (T+10 used_live: {used_live_count})")

    # Fill live_coverage stats — split live vs fallback
    if stop_pcts_used_t10:
        live_stops = [s for s, used in zip(stop_pcts_used_t10, [r.get("used_live", False) for r in records if r["horizon"] == "T+10 (10-day swing)"]) if used]
        live_targets = [t for t, used in zip(target_pcts_used_t10, [r.get("used_live", False) for r in records if r["horizon"] == "T+10 (10-day swing)"]) if used]
        fallback_stops = [s for s, used in zip(stop_pcts_used_t10, [r.get("used_live", False) for r in records if r["horizon"] == "T+10 (10-day swing)"]) if not used]
        fallback_targets = [t for t, used in zip(target_pcts_used_t10, [r.get("used_live", False) for r in records if r["horizon"] == "T+10 (10-day swing)"]) if not used]
        if live_stops:
            live_coverage["avg_stop_pct"] = mean(live_stops) * 100
            live_coverage["median_stop_pct"] = sorted(live_stops)[len(live_stops) // 2] * 100
            live_coverage["avg_target_pct"] = mean(live_targets) * 100
            live_coverage["median_target_pct"] = sorted(live_targets)[len(live_targets) // 2] * 100
        if fallback_stops:
            live_coverage["fallback_avg_stop_pct"] = mean(fallback_stops) * 100
            live_coverage["fallback_avg_target_pct"] = mean(fallback_targets) * 100

    # NOTE (2026-10-02 P0): the old post-hoc universe tally used to live here.
    # It ran AFTER `records` was already built, so it never filtered anything —
    # it only produced counters for the report. Filtering now happens BEFORE the
    # simulate loop (see `eligible_signals` above). The dead block is removed so
    # nobody reads it as an active filter again.

    # Portfolio sim
    portfolio_metrics = {}
    if not args.no_portfolio_sim:
        portfolio_metrics = simulate_portfolio(records, start_str, end_str)

    # Walk-forward OOS
    oos_split = {}
    if not args.no_oos:
        oos_split = walk_forward_oos(records, train_pct=0.8)

    # Render
    sig_dates = sorted({r["date"] for r in records})
    eff_start = sig_dates[0] if sig_dates else start_str
    eff_end = sig_dates[-1] if sig_dates else end_str
    report = render_report(records, eff_start, eff_end, hsi_bear_count,
                           live_coverage, universe_stats,
                           portfolio_metrics, oos_split,
                           requested=(start_str, end_str))

    out_file = out_dir / f"backtest_{end_str}.md"
    out_file.write_text(report, encoding="utf-8")
    print(f"\nSaved: {out_file}")

    latest = out_dir / "latest.md"
    latest.write_text(report, encoding="utf-8")
    print(f"Saved: {latest}")

    raw_file = out_dir / f"backtest_{end_str}.json"
    raw_file.write_text(json.dumps({
        "records": records,
        "live_coverage": live_coverage,
        "universe_stats": universe_stats,
        "portfolio_metrics": portfolio_metrics,
        "oos_split": oos_split,
    }, indent=2, default=str), encoding="utf-8")
    print(f"Saved: {raw_file}")

    # Console summary
    print(f"\n--- Brief ---")
    for h_name in HORIZONS:
        sub = [r for r in records if r["horizon"] == h_name]
        if not sub:
            continue
        n = len(sub)
        wr = sum(1 for r in sub if r["return_pct"] > 0) / n * 100
        avg = sum(r["return_pct"] for r in sub) / n * 100
        live_pct = sum(1 for r in sub if r.get("used_live")) / n * 100
        print(f"  {h_name}: n={n}, WR={wr:.1f}%, avg={avg:+.2f}%, live_stop_used={live_pct:.0f}%")

    if portfolio_metrics and "error" not in portfolio_metrics:
        print(f"\n--- Portfolio (T+10, 5 concurrent, 1% risk) ---")
        print(f"  Trades: {portfolio_metrics['n_trades']}, WR: {portfolio_metrics['win_rate']*100:.1f}%")
        print(f"  Total: {portfolio_metrics['total_return']*100:+.2f}%, MaxDD: {portfolio_metrics['max_drawdown']*100:.2f}%")
        print(f"  Sortino: {portfolio_metrics['sortino']:.2f}, Calmar: {portfolio_metrics['calmar']:.2f}")
        print(f"  Loss streak: {portfolio_metrics['longest_loss_streak']}, Underwater: {portfolio_metrics['max_underwater_days']}d")

    if oos_split and "error" not in oos_split:
        print(f"\n--- OOS Split (80/20) ---")
        print(f"  Train: {oos_split['train_n']} trades, WR {oos_split['train_wr']*100:.1f}%, avg {oos_split['train_avg']*100:+.2f}%")
        print(f"  OOS:   {oos_split['oos_n']} trades, WR {oos_split['oos_wr']*100:.1f}%, avg {oos_split['oos_avg']*100:+.2f}%")
        print(f"  Degradation: WR {oos_split['wr_delta_pp']:+.1f}pp, Avg {oos_split['avg_delta_pp']:+.2f}pp")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""paper_trade_top10.py — HK top 10% by 12m return, monthly rebalance, paper mode.

Pre-registered rule (after pivot):
  Universe:    hk200 panel (data/bt10y2_hk/)
  Signal:      12-month (252-bar) trailing return
  Basket:      top 10% (decile D1)
  Rebalance:   monthly (21 bars), first trading day of month
  Weighting:   equal weight across constituents
  Hold:        until next rebalance (no stop, no target)
  Cost:        0.25% round-trip on traded fraction (Futures fee standard)

Search chain disclosure (printed at startup):
  - Pre-registered top-20% failed 2H (selection premium -1.95pp)
  - Decile spread analysis showed D1 (top 10%) stable across regimes
  - Cross-market validation: top 10% beats top 20% in 3/3 markets
  - This is a pivot from search, not a fresh pre-registration

Risk:
  --daily-loss-pct 0.05 → circuit breaker (default 5%)
  --on-loss close (default) → auto-flat positions on trigger
  --on-loss block → refuse new entries

Usage:
  # Run forward from a start date through end of panel
  python3 paper_trade_top10.py --start 2026-01-01 --end 2026-10-05 \\
                                --equity 1000000 --cap-pct 0.15

  # Dry run, log only
  python3 paper_trade_top10.py --start 2025-01-01 --dry-run
"""
from __future__ import annotations
import argparse
import json
import sys
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path("/Users/kenken/dev/dsa-hk")
sys.path.insert(0, str(REPO / "scripts"))
from bt_clean import build_mask, load

DATA = REPO / "data"
LOG_DIR = REPO / "data" / "paper_trade_logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)
HKT = timezone(timedelta(hours=8))

LB = 252
STEP = 21
TOP_FRAC = 0.10
COST_RT = 0.0025
PANEL_DIR = DATA / "bt10y2_hk"

SEARCH_CHAIN = """
╔════════════════════════════════════════════════════════════════╗
║  PAPER TRADE — HK TOP 10% BY 12M RETURN, MONTHLY REBALANCE     ║
╠════════════════════════════════════════════════════════════════╣
║  Search chain (must read before trusting this number):         ║
║                                                                ║
║  1. Pre-registered rule (top 20%, 12m monthly):                 ║
║     full ann% 15.11, 2H (2022-26) ann% 13.10                   ║
║     2H selection premium over EW (same window): -1.95pp ✗      ║
║     -> rule decayed; universe effect dominated                 ║
║                                                                ║
║  2. Decile spread (HK):                                         ║
║     D1 (top 10%): 1H 15.19% -> 2H 16.32%   (stable, +1.13pp)   ║
║     D2 (10-20%):  1H 17.10% -> 2H  7.32%   (collapsed)          ║
║     D10 (bot 10): 1H  4.54% -> 2H 15.86%   (exploded)          ║
║                                                                ║
║  3. Cross-market validation (top 10% vs top 20%):               ║
║     HK:  18.01% vs 13.13%   diff +4.88pp                       ║
║     US:  24.92% vs 17.89%   diff +7.03pp                       ║
║     JP:  15.68% vs 13.76%   diff +1.92pp                       ║
║     -> Top 10% beats Top 20% in 3/3 markets (structural)        ║
║                                                                ║
║  4. Pivot: this is NOT a pre-registered rule.                   ║
║     Honest framing: search chain produced this;                ║
║     cross-market consistency reduces but does not eliminate     ║
║     search bias risk.                                           ║
╚════════════════════════════════════════════════════════════════╝
"""


@dataclass
class PaperTrade:
    date: str
    side: str            # "BUY" or "SELL"
    code: str
    qty: int
    price: float
    notional_hkd: float
    cost_hkd: float
    reason: str          # "ENTER_D1" or "EXIT_D1" or "REBALANCE_IN" / "REBALANCE_OUT"


@dataclass
class DailyPnL:
    date: str
    equity_hkd: float
    day_pnl_hkd: float
    day_pnl_pct: float
    cash_hkd: float
    n_positions: int


@dataclass
class Portfolio:
    cash: float
    positions: dict = field(default_factory=dict)   # code -> {qty, cost_basis, last_px}
    last_rebalance_date: str = None
    cumulative_traded: float = 0.0
    cumulative_cost: float = 0.0
    peak_equity: float = 0.0
    circuit_breaker_tripped: bool = False
    circuit_breaker_date: str = None
    trade_log: list = field(default_factory=list)
    daily_pnl: list = field(default_factory=list)

    def mark_to_market(self, prices: dict) -> float:
        eq = self.cash
        for code, pos in self.positions.items():
            if code in prices and prices[code] is not None:
                pos["last_px"] = prices[code]
                eq += pos["qty"] * prices[code]
        self.peak_equity = max(self.peak_equity, eq)
        return eq


def log(msg: str):
    ts = datetime.now(HKT).isoformat(timespec="seconds")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    log_file = LOG_DIR / f"paper_top10_{datetime.now(HKT).strftime('%Y%m%d')}.log"
    with open(log_file, "a") as f:
        f.write(line + "\n")


def load_panel() -> pd.DataFrame:
    mask = build_mask(PANEL_DIR)
    masked = {k for k in mask if k.endswith("_HK")}
    series = []
    for p in sorted(PANEL_DIR.glob("*.json")):
        b = load(p)
        if b is None or b.empty or p.stem in masked:
            continue
        series.append(b.close.rename(p.stem))
    return pd.concat(series, axis=1).sort_index()


def compute_top_decile(panel: pd.DataFrame, i: int) -> list[str]:
    """Return list of top-10% names by 12m return as of bar i."""
    s = (panel.iloc[i] / panel.iloc[i - LB] - 1).replace([np.inf, -np.inf], np.nan).dropna()
    if len(s) < 50:
        return []
    k = max(2, int(round(len(s) * TOP_FRAC)))
    return list(s.sort_values(ascending=False).index[:k])


def main():
    p = argparse.ArgumentParser(description="HK Top 10% momentum paper trader",
                                formatter_class=argparse.RawDescriptionHelpFormatter,
                                epilog=SEARCH_CHAIN)
    p.add_argument("--start", required=True, help="Start date YYYY-MM-DD")
    p.add_argument("--end", default=None, help="End date YYYY-MM-DD (default: panel end)")
    p.add_argument("--equity", type=float, default=1_000_000.0,
                   help="Starting equity HKD (default 1M)")
    p.add_argument("--cap-pct", type=float, default=0.15,
                   help="Per-name cap as fraction of equity (default 0.15 = 15%%)")
    p.add_argument("--daily-loss-pct", type=float, default=0.05,
                   help="Circuit breaker threshold (default 0.05 = 5 percent)")
    p.add_argument("--on-loss", choices=["close", "block"], default="close",
                   help="Action on circuit breaker trigger")
    p.add_argument("--dry-run", action="store_true",
                   help="Don't persist state to file, only log")
    p.add_argument("--state-file", default=str(DATA / "paper_top10_state.json"),
                   help="Where to persist portfolio state")
    args = p.parse_args()

    print(SEARCH_CHAIN)

    panel = load_panel()
    log(f"=== Panel: {panel.shape[0]} bars × {panel.shape[1]} names ===")
    log(f"=== Window: {panel.index.min().date()} → {panel.index.max().date()} ===")
    log(f"=== Strategy: top 10% by 12m return, monthly rebalance ===")
    log(f"=== Starting equity: HK${args.equity:,.0f} | per-name cap {args.cap_pct*100:.0f}% | "
        f"circuit breaker {args.daily_loss_pct*100:.0f}%/day | on-loss={args.on_loss} ===")

    # Initialise portfolio
    pf = Portfolio(cash=args.equity)
    pf.peak_equity = args.equity

    # Find first valid signal date
    start_ts = pd.Timestamp(args.start)
    end_ts = pd.Timestamp(args.end) if args.end else panel.index.max()

    # Locate bar index for start
    if start_ts not in panel.index:
        idx = panel.index.get_indexer([start_ts], method="bfill")[0]
        start_ts = panel.index[idx]
    if end_ts not in panel.index:
        idx = panel.index.get_indexer([end_ts], method="bfill")[0]
        end_ts = panel.index[idx]

    # Need at least 252 bars of history before first rebalance
    first_signal_idx = max(LB, panel.index.get_loc(start_ts))
    log(f"First rebalance will be at bar {first_signal_idx} (date {panel.index[first_signal_idx].date()})")

    # Build rebalance schedule: first bar of each month from start to end
    rebalance_bars = []
    last_month = None
    for i in range(first_signal_idx, panel.index.get_loc(end_ts) + 1):
        if i + STEP >= len(panel):
            break
        d = panel.index[i]
        if d.month != last_month:
            rebalance_bars.append(i)
            last_month = d.month

    log(f"Total rebalances scheduled: {len(rebalance_bars)}")

    # Main loop: walk bar by bar, rebalance when due, track daily P&L
    for i, ts in enumerate(panel.index):
        if i < first_signal_idx:
            continue
        if ts > end_ts:
            break

        # Mark to market
        prices = {c: panel.iloc[i].get(c) for c in pf.positions}
        eq = pf.mark_to_market(prices)

        # Daily P&L tracking (compare to previous close)
        if pf.daily_pnl:
            prev_eq = pf.daily_pnl[-1]["equity_hkd"]
            day_pnl = eq - prev_eq
            day_pnl_pct = day_pnl / prev_eq if prev_eq else 0
        else:
            day_pnl = 0
            day_pnl_pct = 0
        pf.daily_pnl.append({
            "date": str(ts.date()),
            "equity_hkd": round(eq, 2),
            "day_pnl_hkd": round(day_pnl, 2),
            "day_pnl_pct": round(day_pnl_pct, 5),
            "cash_hkd": round(pf.cash, 2),
            "n_positions": len(pf.positions),
        })

        # Circuit breaker check (only on rebalance days too, but daily for safety)
        if pf.daily_pnl and len(pf.daily_pnl) > 1:
            if day_pnl_pct <= -args.daily_loss_pct and not pf.circuit_breaker_tripped:
                pf.circuit_breaker_tripped = True
                pf.circuit_breaker_date = str(ts.date())
                log(f"!!! CIRCUIT BREAKER TRIPPED on {ts.date()}: day_pnl={day_pnl_pct*100:.2f}% <= "
                    f"-{args.daily_loss_pct*100:.0f}% !!!")
                if args.on_loss == "close":
                    # Flatten all positions at market
                    log("On-loss=close: flattening all positions at market")
                    for code in sorted(pf.positions.keys()):
                        pos = pf.positions[code]
                        px = panel.iloc[i].get(code)
                        if px and px > 0 and pos["qty"] > 0:
                            sell_notional = pos["qty"] * px
                            sell_cost = sell_notional * COST_RT
                            pf.cash += sell_notional - sell_cost
                            pf.cumulative_traded += sell_notional
                            pf.cumulative_cost += sell_cost
                            pf.trade_log.append(asdict(PaperTrade(
                                date=str(ts.date()), side="SELL", code=code,
                                qty=pos["qty"], price=float(px),
                                notional_hkd=round(sell_notional, 2),
                                cost_hkd=round(sell_cost, 2),
                                reason="CIRCUIT_BREAKER"
                            )))
                    pf.positions.clear()
                else:
                    log("On-loss=block: refusing new entries; holding existing positions")

        # Rebalance if today is a rebalance day and breaker not tripped
        if i in rebalance_bars and not pf.circuit_breaker_tripped:
            top = compute_top_decile(panel, i)
            if not top:
                continue
            # Sort target for DETERMINISTIC iteration. Python set iteration order
            # depends on PYTHONHASHSEED, which changes per process. Without this,
            # three identical runs produce +47% / +54% / +46%.
            top = sorted(top)
            prev_holdings = set(pf.positions.keys())
            target_holdings = set(top)
            to_sell = sorted(prev_holdings - target_holdings)
            to_buy = sorted(target_holdings - prev_holdings)
            to_keep = sorted(target_holdings & prev_holdings)

            log(f"\n=== Rebalance {ts.date()} ===")
            log(f"  Universe: {len(panel.iloc[i].dropna())} names, basket size: {len(top)}")
            log(f"  Holdings to exit: {len(to_sell)}, to enter: {len(to_buy)}, to keep: {len(to_keep)}")

            # Sell first to free up cash
            for code in to_sell:
                pos = pf.positions.get(code)
                if not pos:
                    continue
                px = panel.iloc[i].get(code)
                if px is None or px <= 0:
                    log(f"  SKIP SELL {code}: no price")
                    continue
                sell_notional = pos["qty"] * float(px)
                sell_cost = sell_notional * COST_RT
                pf.cash += sell_notional - sell_cost
                pf.cumulative_traded += sell_notional
                pf.cumulative_cost += sell_cost
                pf.trade_log.append(asdict(PaperTrade(
                    date=str(ts.date()), side="SELL", code=code, qty=pos["qty"],
                    price=float(px), notional_hkd=round(sell_notional, 2),
                    cost_hkd=round(sell_cost, 2), reason="EXIT_D1"
                )))
                log(f"  SELL {code} {pos['qty']}sh @ {float(px):.2f} = HK${sell_notional:,.0f} "
                    f"(cost {sell_cost:.0f})")
                del pf.positions[code]

            # Target equal weight across the FULL basket (keep + buy), so kept
            # positions drift back to equal weight instead of compounding drift.
            n_target = len(target_holdings)
            equity_now = pf.cash + sum(
                pos["qty"] * float(panel.iloc[i].get(c) or pos["last_px"])
                for c, pos in pf.positions.items()
            )
            per_name_target = equity_now / n_target
            per_name_cap = equity_now * args.cap_pct
            target_notional = min(per_name_target, per_name_cap)

            # Sell overweight kept positions back to target
            for code in to_keep:
                pos = pf.positions.get(code)
                px = panel.iloc[i].get(code)
                if pos is None or px is None or px <= 0:
                    continue
                current_notional = pos["qty"] * float(px)
                if current_notional > target_notional * 1.02:   # 2% deadband
                    trim_qty = int((current_notional - target_notional) // float(px))
                    if trim_qty > 0:
                        trim_notional = trim_qty * float(px)
                        trim_cost = trim_notional * COST_RT
                        pf.cash += trim_notional - trim_cost
                        pf.cumulative_traded += trim_notional
                        pf.cumulative_cost += trim_cost
                        pos["qty"] -= trim_qty
                        pf.trade_log.append(asdict(PaperTrade(
                            date=str(ts.date()), side="SELL", code=code, qty=trim_qty,
                            price=float(px), notional_hkd=round(trim_notional, 2),
                            cost_hkd=round(trim_cost, 2), reason="TRIM_OVERWEIGHT"
                        )))
                        log(f"  TRIM {code} {trim_qty}sh @ {float(px):.2f} "
                            f"= HK${trim_notional:,.0f} (back to equal weight)")
                        if pos["qty"] <= 0:
                            del pf.positions[code]

            # Buy new entries with a FIXED per-name budget computed once, not
            # sequentially from shrinking cash.
            if to_buy:
                for code in to_buy:
                    px = panel.iloc[i].get(code)
                    if px is None or px <= 0:
                        log(f"  SKIP BUY {code}: no price")
                        continue
                    notional = min(target_notional, pf.cash)
                    qty = int(notional // float(px))
                    if qty <= 0:
                        log(f"  SKIP BUY {code}: insufficient cash for 1 share")
                        continue
                    buy_notional = qty * float(px)
                    buy_cost = buy_notional * COST_RT
                    pf.cash -= buy_notional + buy_cost
                    pf.cumulative_traded += buy_notional
                    pf.cumulative_cost += buy_cost
                    pf.positions[code] = {"qty": qty, "cost_basis": float(px), "last_px": float(px)}
                    pf.trade_log.append(asdict(PaperTrade(
                        date=str(ts.date()), side="BUY", code=code, qty=qty,
                        price=float(px), notional_hkd=round(buy_notional, 2),
                        cost_hkd=round(buy_cost, 2), reason="ENTER_D1"
                    )))
                    log(f"  BUY  {code} {qty}sh @ {float(px):.2f} = HK${buy_notional:,.0f} "
                        f"(cost {buy_cost:.0f})")

            pf.last_rebalance_date = str(ts.date())

    # Final mark to market
    final_prices = {c: panel.iloc[len(panel)-1].get(c) for c in pf.positions}
    final_eq = pf.mark_to_market(final_prices)
    final_return = (final_eq - args.equity) / args.equity * 100

    log(f"\n=== Final state ===")
    log(f"  Start equity: HK${args.equity:,.0f}")
    log(f"  Final equity (MTM): HK${final_eq:,.0f}")
    log(f"  Total return: {final_return:+.2f}%")
    log(f"  Cumulative traded: HK${pf.cumulative_traded:,.0f}")
    log(f"  Cumulative cost: HK${pf.cumulative_cost:,.0f} ({pf.cumulative_cost/args.equity*100:.2f}% of starting equity)")
    log(f"  Rebalances: {len([t for t in pf.trade_log if t['reason'] in ('ENTER_D1','EXIT_D1')])//2}")
    log(f"  Circuit breaker: {'TRIPPED ' + pf.circuit_breaker_date if pf.circuit_breaker_tripped else 'never tripped'}")

    # Save state
    if not args.dry_run:
        state = {
            "as_of": str(panel.index.max().date()),
            "starting_equity_hkd": args.equity,
            "current_equity_hkd": round(final_eq, 2),
            "return_pct": round(final_return, 2),
            "cumulative_traded_hkd": round(pf.cumulative_traded, 2),
            "cumulative_cost_hkd": round(pf.cumulative_cost, 2),
            "circuit_breaker_tripped": pf.circuit_breaker_tripped,
            "circuit_breaker_date": pf.circuit_breaker_date,
            "current_holdings": {
                c: {"qty": p["qty"], "cost_basis": round(p["cost_basis"], 2),
                    "last_px": round(p["last_px"], 2)}
                for c, p in pf.positions.items()
            },
            "trade_log": pf.trade_log,
            "daily_pnl": pf.daily_pnl,
        }
        Path(args.state_file).write_text(json.dumps(state, indent=2))
        log(f"\nState saved to {args.state_file}")


if __name__ == "__main__":
    main()

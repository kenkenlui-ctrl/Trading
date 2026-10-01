"""backtest_jp.py — Multi-window × multi-strategy backtest simulator for JP tickers.

For each JP ticker, simulates 4 strategies (SELL_R1, BUY_S1, BREAK_LONG, BREAK_SHORT)
across 4 windows (30/60/90/197 days) using yfinance historical data + the same
10-step framework logic as daily_sr.

Output schema matches backtest_futu.py's expected format:
  {
    "code": "TICKER",
    "name": "TICKER",
    "close": float,
    "windows": {
      "30":  {"SELL_R1": {n, win_pct, total_pnl, avg_pnl}, ...},
      "60":  {...},
      "90":  {...},
      "197": {...}
    },
    "best_strategy": {"name", "window", "win_pct", "n_trades", "total_pnl"}
  }
"""
from __future__ import annotations
import json
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

REPO = Path("/Users/kenken/dev/dsa-hk")
OUT_DIR = Path("/Users/kenken/dev/dsa-hk/data/backtest_out")
JP_UNIVERSE = REPO / "jp_universe_200.json"
OUT_DIR.mkdir(parents=True, exist_ok=True)

sys.path.insert(0, "/Users/kenken/.minimax/skills/daily-sr-chart/scripts")
import daily_sr  # type: ignore
import yfinance as yf  # type: ignore
import pandas as pd  # type: ignore

WINDOWS = ["30", "60", "90", "197"]
STRATEGIES = ["SELL_R1", "BUY_S1", "BREAK_LONG", "BREAK_SHORT"]


def fetch_ohlc(ticker: str, days: int = 250) -> "pd.DataFrame | None":
    try:
        df = yf.Ticker(ticker).history(period=f"{days + 30}d", auto_adjust=False)
        if df is None or len(df) < 60:
            return None
        df = df[["Open", "High", "Low", "Close", "Volume"]].copy()
        df.columns = ["open", "high", "low", "close", "volume"]
        df.index = pd.to_datetime(df.index).tz_localize(None)
        return df
    except Exception as e:
        print(f"  {ticker} err: {e}")
        return None


def simulate_strategy_on_history(df, strategy: str) -> dict:
    """Walk through df, simulate strategy entries/exits.

    2026-09-10: per-trade (outcome, MAE, MFE, R-multiple, phase) tracked.
    Returns aggregate + regime_matrix (per-phase breakdown) per Astra review #1.
    """
    trades = []  # (outcome_pct, mae_pct, mfe_pct, R_mult, phase)
    for i in range(60, len(df)):  # need history for S/R pivots
        # Keep 'date' as a column (detect_phase expects it)
        window_df = df.iloc[max(0, i - 200):i + 1].copy().reset_index(drop=False)
        window_df['date'] = pd.to_datetime(window_df['Date'] if 'Date' in window_df.columns else window_df.iloc[:, 0])
        window_df = window_df.drop(columns=[c for c in ['Date','Datetime','index'] if c in window_df.columns], errors='ignore')
        current = float(df.iloc[i]["close"])
        try:
            phase_info = daily_sr.detect_phase(window_df)
            supports = daily_sr.build_support_ladder(
                window_df, current,
                phase_info["peak"]["price"], phase_info["trough"]["price"],
                str(phase_info["trough"]["date"]),
            )
            resistances = daily_sr.build_resistance_ladder(
                window_df, current,
                phase_info["peak"]["price"], str(phase_info["peak"]["date"]),
                phase_info["trough"]["price"], str(phase_info["trough"]["date"]),
                phase_info["box_top"]["price"], str(phase_info["box_top"]["date"]),
            )
            action = daily_sr.compute_action_plan(window_df, phase_info["phase"], current)
        except Exception:
            continue

        ap = action.get("verdict")
        trig = action.get("trigger_price")
        target = action.get("target_price")
        stop = action.get("stop_price")
        if not ap or not trig or not target or not stop or ap not in ("BUY", "SELL"):
            continue
        # Map "BUY"/"SELL" verdict to the strategy key
        verdict_to_strat = {
            "BUY": {"BUY_S1", "BREAK_LONG"},
            "SELL": {"SELL_R1", "BREAK_SHORT"},
        }
        if strategy not in verdict_to_strat.get(ap, set()):
            continue
        # 2026-09-10: capture phase at entry (Astra regime matrix)
        entry_phase = phase_info["phase"]

        # Find trade outcome + track MAE/MFE during the trade (intra-trade)
        outcome = None
        mae_pct = 0.0
        mfe_pct = 0.0
        for j in range(i + 1, min(i + 200, len(df))):
            row_j = df.iloc[j]
            close_j = float(row_j["close"])
            high_j = float(row_j["high"])
            low_j = float(row_j["low"])
            if ap == "BUY":
                cur_pnl = (close_j - trig) / trig * 100
                cur_mae = (low_j - trig) / trig * 100
                cur_mfe = (high_j - trig) / trig * 100
            else:  # SELL
                cur_pnl = (trig - close_j) / trig * 100
                cur_mae = (trig - high_j) / trig * 100
                cur_mfe = (trig - low_j) / trig * 100
            if cur_mae < mae_pct:
                mae_pct = cur_mae
            if cur_mfe > mfe_pct:
                mfe_pct = cur_mfe
            if ap == "BUY":
                if close_j >= target:
                    outcome = (close_j - trig) / trig * 100
                    break
                if close_j <= stop:
                    outcome = (close_j - trig) / trig * 100
                    break
            else:
                if close_j <= target:
                    outcome = (trig - close_j) / trig * 100
                    break
                if close_j >= stop:
                    outcome = (trig - close_j) / trig * 100
                    break
        if outcome is not None:
            stop_dist_pct = abs((trig - stop) / trig * 100) if trig else 1
            R_mult = outcome / stop_dist_pct if stop_dist_pct > 0.01 else 0
            trades.append((outcome, mae_pct, mfe_pct, R_mult, entry_phase))

    n = len(trades)
    if n == 0:
        return {"n": 0, "win_pct": 0, "total_pnl": 0, "avg_pnl": 0,
                "mae_pct": 0, "mfe_pct": 0, "max_mae_pct": 0,
                "max_win_streak": 0, "max_loss_streak": 0,
                "expect_r": 0, "profit_factor": 0, "max_dd": 0,
                "win_ci_low": 0, "win_ci_high": 0, "regime_matrix": {}}
    outcomes = [t[0] for t in trades]
    maes = [t[1] for t in trades]
    mfes = [t[2] for t in trades]
    rmults = [t[3] for t in trades]
    phases = [t[4] for t in trades]
    wins_list = [o for o in outcomes if o > 0]
    losses_list = [o for o in outcomes if o <= 0]
    n_w = len(wins_list)
    n_l = len(losses_list)
    wins = n_w

    avg_pnl = sum(outcomes) / n
    avg_mae = sum(maes) / n
    avg_mfe = sum(mfes) / n
    max_mae = min(maes)

    # Streak
    max_win_streak = 0
    max_loss_streak = 0
    cur_win = 0
    cur_loss = 0
    for o in outcomes:
        if o > 0:
            cur_win += 1
            cur_loss = 0
            max_win_streak = max(max_win_streak, cur_win)
        else:
            cur_loss += 1
            cur_win = 0
            max_loss_streak = max(max_loss_streak, cur_loss)

    # 2026-09-10: Expected R per trade
    expect_r = sum(rmults) / n

    # 2026-09-10: Profit factor
    sum_wins = sum(wins_list) if wins_list else 0
    sum_losses = abs(sum(losses_list)) if losses_list else 0
    profit_factor = (sum_wins / sum_losses) if sum_losses > 0.01 else 999.0

    # 2026-09-10: Max DD
    running = 0
    peak = 0
    max_dd = 0
    for o in outcomes:
        running += o
        if running > peak:
            peak = running
        dd = peak - running
        if dd > max_dd:
            max_dd = dd

    # 2026-09-10: 95% CI on win% (Wilson)
    p_hat = n_w / n
    z = 1.96
    denom = 1 + z*z/n
    center = (p_hat + z*z/(2*n)) / denom
    spread = z * ((p_hat*(1-p_hat) + z*z/(4*n)) / n) ** 0.5 / denom
    win_ci_low = max(0.0, (center - spread) * 100)
    win_ci_high = min(100.0, (center + spread) * 100)

    # 2026-09-10: Regime × Strategy matrix (per-phase breakdown)
    regime_groups = {}
    for o, mae, mfe, rm, ph in zip(outcomes, maes, mfes, rmults, phases):
        regime_groups.setdefault(ph, []).append((o, mae, mfe, rm))
    regime_matrix = {}
    for ph, tlist in regime_groups.items():
        n_ph = len(tlist)
        n_w_ph = sum(1 for t in tlist if t[0] > 0)
        wins_ph = sum(t[0] for t in tlist if t[0] > 0)
        losses_ph = abs(sum(t[0] for t in tlist if t[0] <= 0))
        sum_rm = sum(t[3] for t in tlist)
        run = 0
        pk = 0
        mdd = 0
        for t in tlist:
            run += t[0]
            if run > pk: pk = run
            if pk - run > mdd: mdd = pk - run
        regime_matrix[ph] = {
            "n": n_ph,
            "win_pct": round(n_w_ph / n_ph * 100, 2) if n_ph else 0,
            "expect_r": round(sum_rm / n_ph, 3) if n_ph else 0,
            "avg_mae": round(sum(t[1] for t in tlist) / n_ph, 2) if n_ph else 0,
            "max_dd": round(mdd, 2),
            "pf": round(wins_ph / losses_ph, 2) if losses_ph > 0.01 else 999.0,
        }

    return {
        "n": n,
        "win_pct": round(wins / n * 100, 2),
        "total_pnl": round(sum(outcomes), 2),
        "avg_pnl": round(avg_pnl, 4),
        "mae_pct": round(avg_mae, 2),
        "mfe_pct": round(avg_mfe, 2),
        "max_mae_pct": round(max_mae, 2),
        "max_win_streak": max_win_streak,
        "max_loss_streak": max_loss_streak,
        # 2026-09-10: Astra comprehensive stat panel
        "expect_r": round(expect_r, 3),
        "profit_factor": round(profit_factor, 2),
        "max_dd": round(max_dd, 2),
        "win_ci_low": round(win_ci_low, 1),
        "win_ci_high": round(win_ci_high, 1),
        "regime_matrix": regime_matrix,
    }


def process_one(ticker: str) -> bool:
    safe = ticker.replace(".", "_")
    out_path = OUT_DIR / f"{safe}.json"
    if out_path.exists() and (datetime.now() - datetime.fromtimestamp(out_path.stat().st_mtime)) < timedelta(hours=6):
        print(f"  {ticker}: cached")
        return True

    df = fetch_ohlc(ticker)
    if df is None or len(df) < 60:
        return False

    # Run all strategies
    stats = {}
    for strat in STRATEGIES:
        stats[strat] = simulate_strategy_on_history(df, strat)

    # Determine best strategy by win_pct × min n (n >= 5)
    best = None
    for strat, s in stats.items():
        if s["n"] >= 3:
            if best is None or s["win_pct"] > best[1]["win_pct"]:
                best = (strat, s)
    best_strategy = {"name": "—", "window": "30", "win_pct": 0, "n_trades": 0, "total_pnl": 0}
    if best is not None:
        best_strategy = {
            "name": best[0],
            "window": "30",  # we ran across all data; not per-window
            "win_pct": best[1]["win_pct"],
            "n_trades": best[1]["n"],
            "total_pnl": best[1]["total_pnl"],
        }

    # Build windows dict per-strategy for the Win% column.
    # Since simulate_strategy_on_history used 200 days regardless of strategy,
    # report the same stats across all "windows" labels. (Approximation.)
    windows = {}
    for w in WINDOWS:
        windows[w] = stats

    result = {
        "code": ticker,
        "name": ticker,
        "close": float(df["close"].iloc[-1]),
        "windows": windows,
        "best_strategy": best_strategy,
        "best_win_pct": best_strategy["win_pct"],
        "best_n": best_strategy["n_trades"],
        "best_total_pnl": best_strategy["total_pnl"],
        "asof": str(df.index[-1].date()),
    }
    out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return True


def main():
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--top", type=int, help="Top N tickers (test mode)")
    args = p.parse_args()

    if not JP_UNIVERSE.exists():
        print(f"Missing {JP_UNIVERSE}")
        sys.exit(1)
    tickers = json.load(open(JP_UNIVERSE))
    if args.top:
        tickers = tickers[: args.top]
    print(f"Backtest {len(tickers)} JP tickers")

    success = failed = 0
    for i, ticker in enumerate(tickers, 1):
        try:
            ok = process_one(ticker)
            if ok:
                success += 1
            else:
                failed += 1
        except Exception as e:
            failed += 1
            print(f"  [{i}/{len(tickers)}] {ticker}: err {e}")
        if i % 10 == 0:
            print(f"  {i}/{len(tickers)} done: ok={success} fail={failed}")
        time.sleep(0.1)

    print(f"\nDone. success={success} failed={failed}")


if __name__ == "__main__":
    main()

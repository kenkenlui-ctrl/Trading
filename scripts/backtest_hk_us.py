"""backtest_hk_us.py — Multi-strategy backtest for HK + US tickers.

Same logic as backtest_jp.py: for each historical bar, simulate 4 strategies
(SELL_R1, BUY_S1, BREAK_LONG, BREAK_SHORT) with daily_sr skill, track
outcome + MAE/MFE per trade.

Outputs to /Users/kenken/dev/dsa-hk/data/backtest_out/{TICKER}.json with the same schema as
backtest_jp.py including mae_pct, mfe_pct, max_mae_pct, max_win_streak,
max_loss_streak fields.
"""
from __future__ import annotations
import json
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
import argparse

REPO = Path("/Users/kenken/dev/dsa-hk")
OUT_DIR = Path("/Users/kenken/dev/dsa-hk/data/backtest_out")
OUT_DIR.mkdir(parents=True, exist_ok=True)

sys.path.insert(0, "/Users/kenken/.minimax/skills/daily-sr-chart/scripts")
import daily_sr  # type: ignore
import yfinance as yf  # type: ignore
import pandas as pd  # type: ignore

WINDOWS = ["30", "60", "90", "197"]
STRATEGIES = ["SELL_R1", "BUY_S1", "BREAK_LONG", "BREAK_SHORT"]


def fetch_ohlc(ticker: str, days: int = 250) -> "pd.DataFrame | None":
    # 2026-09-08: yfinance returns 0 rows for HK stocks in this env.
    # Primary source: public/<market>200/ohlc/{TICKER}_ohlc.json (precomputed bars).
    safe = ticker.replace(".", "_")
    # 2026-09-17: yfinance needs "NNNNN.HK" format, not "HK.NNNNN"
    if ticker.startswith("HK.") and not ticker.endswith(".HK"):
        yf_ticker = ticker[3:] + ".HK"  # "HK.02565" → "02565.HK"
    elif ticker.startswith("JP.") and not ticker.endswith(".T"):
        yf_ticker = ticker[3:] + ".T"
    else:
        yf_ticker = ticker
    ohlc_paths = [
        REPO / "public" / ("hk200" if ticker.endswith(".HK") else "us200") / "ohlc" / f"{safe}_ohlc.json",
        REPO / "public" / ("hk200" if ticker.endswith(".HK") else "us200") / "ohlc" / f"{ticker}_ohlc.json",
    ]
    for p in ohlc_paths:
        if p.exists():
            try:
                bars = json.load(open(p))
                if not bars:
                    continue
                df = pd.DataFrame(bars)
                # 2026-09-10: use format='mixed' to handle both "2026-09-08" and
                # "2026-09-09 16:08:12" (intraday Tencent update) gracefully.
                df['date'] = pd.to_datetime(df['date'], format='mixed', errors='coerce')
                df = df.dropna(subset=['date'])
                df['date'] = df['date'].dt.normalize()  # strip time component if any
                df = df.set_index('date').sort_index()
                df = df.rename(columns={"open": "open", "high": "high", "low": "low", "close": "close", "volume": "volume"})
                if len(df) >= 60:
                    return df[["open", "high", "low", "close", "volume"]]
            except Exception as e:
                pass
    # Fallback: yfinance
    try:
        df = yf.Ticker(yf_ticker).history(period=f"{days + 30}d", auto_adjust=False)
        if df is not None and len(df) >= 60:
            df = df[["Open", "High", "Low", "Close", "Volume"]].copy()
            df.columns = ["open", "high", "low", "close", "volume"]
            df.index = pd.to_datetime(df.index).tz_localize(None)
            return df
    except Exception:
        pass
    # 2026-09-17: third fallback — Futu OpenD (local, has full HK/JP/US history)
    try:
        from futu import OpenQuoteContext, RET_OK, KLType
        # Futu uses "HK.NNNNN" format for HK stocks
        if ticker.startswith("HK.") and not ticker.endswith(".HK"):
            futu_code = ticker
        elif ticker.endswith(".HK"):
            futu_code = "HK." + ticker[:-3]
        elif ticker.endswith(".T"):
            futu_code = "JP." + ticker[:-2]
        elif ticker.startswith("US."):
            futu_code = ticker
        else:
            futu_code = ticker
        qc = OpenQuoteContext(host='127.0.0.1', port=11111)
        try:
            ret, fdata, _ = qc.request_history_kline(
                futu_code, start='2024-01-01', end='2026-09-16', ktype=KLType.K_DAY
            )
            if ret == RET_OK and fdata is not None and len(fdata) > 0:
                df = pd.DataFrame({
                    'open': fdata['open'].astype(float),
                    'high': fdata['high'].astype(float),
                    'low': fdata['low'].astype(float),
                    'close': fdata['close'].astype(float),
                    'volume': fdata['volume'].astype(float),
                }, index=pd.to_datetime(fdata['time_key']).dt.normalize())
                df = df.sort_index()
                if len(df) >= 60:
                    return df
        finally:
            qc.close()
    except Exception:
        pass
    return None


def simulate_strategy_on_history(df, strategy: str) -> dict:
    """Walk through df, simulate strategy. Track outcome + MAE/MFE + phase per trade.

    2026-09-10: Astra review #1 - per-trade (phase, outcome, mae, mfe, R-mult)
    tracked to compute regime × strategy matrix.
    """
    trades = []  # (outcome_pct, mae_pct, mfe_pct, R_mult, phase)
    for i in range(60, len(df)):
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
        verdict_to_strat = {
            "BUY": {"BUY_S1", "BREAK_LONG"},
            "SELL": {"SELL_R1", "BREAK_SHORT"},
        }
        if strategy not in verdict_to_strat.get(ap, set()):
            continue
        entry_phase = phase_info["phase"]

        outcome = None
        mae_pct = 0.0
        mfe_pct = 0.0
        for j in range(i + 1, min(i + 200, len(df))):
            row_j = df.iloc[j]
            close_j = float(row_j["close"])
            high_j = float(row_j["high"])
            low_j = float(row_j["low"])
            if ap == "BUY":
                cur_mae = (low_j - trig) / trig * 100
                cur_mfe = (high_j - trig) / trig * 100
            else:
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
    wins = n_w
    avg_pnl = sum(outcomes) / n
    avg_mae = sum(maes) / n
    avg_mfe = sum(mfes) / n
    max_mae = min(maes)
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
    expect_r = sum(rmults) / n
    sum_wins = sum(wins_list) if wins_list else 0
    sum_losses = abs(sum(losses_list)) if losses_list else 0
    profit_factor = (sum_wins / sum_losses) if sum_losses > 0.01 else 999.0
    running = 0
    peak = 0
    max_dd = 0
    for o in outcomes:
        running += o
        if running > peak:
            peak = running
        if peak - running > max_dd:
            max_dd = peak - running
    p_hat = n_w / n
    z = 1.96
    denom = 1 + z*z/n
    center = (p_hat + z*z/(2*n)) / denom
    spread = z * ((p_hat*(1-p_hat) + z*z/(4*n)) / n) ** 0.5 / denom
    win_ci_low = max(0.0, (center - spread) * 100)
    win_ci_high = min(100.0, (center + spread) * 100)
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
    # Skip cache if older than 4h or missing new fields
    needs_rebuild = True
    if out_path.exists():
        age = datetime.now() - datetime.fromtimestamp(out_path.stat().st_mtime)
        if age < timedelta(hours=4):
            try:
                d = json.load(open(out_path))
                if "mae_pct" in d.get("windows", {}).get("60", {}).get("SELL_R1", {}):
                    needs_rebuild = False
            except Exception:
                pass
    if not needs_rebuild:
        print(f"  {ticker}: cached", flush=True)
        return True

    df = fetch_ohlc(ticker)
    if df is None or len(df) < 60:
        return False

    stats = {}
    for strat in STRATEGIES:
        stats[strat] = simulate_strategy_on_history(df, strat)

    best = None
    for strat, s in stats.items():
        if s["n"] >= 3:
            if best is None or s["win_pct"] > best[1]["win_pct"]:
                best = (strat, s)
    best_strategy = {"name": "—", "window": "30", "win_pct": 0, "n_trades": 0, "total_pnl": 0}
    if best is not None:
        best_strategy = {
            "name": best[0],
            "window": "30",
            "win_pct": best[1]["win_pct"],
            "n_trades": best[1]["n"],
            "total_pnl": best[1]["total_pnl"],
        }

    windows = {w: stats for w in WINDOWS}

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
    p = argparse.ArgumentParser()
    p.add_argument("market", choices=["HK", "US", "JP"], help="Market to backtest")
    p.add_argument("--workers", type=int, default=8, help="Parallel workers")
    p.add_argument("--top", type=int, help="Top N tickers (test)")
    args = p.parse_args()

    if args.market == "HK":
        # Universe = all .json files in HK_OUT minus _ohlc + non-ticker files
        hk_dir = Path("/Users/kenken/dev/dsa-hk/charts/hk200")
        all_tickers = []
        for fp in sorted(hk_dir.glob("*.json")):
            name = fp.name
            # Skip ohlc, derivatives, non-ticker metadata files
            if "_ohlc" in name or fp.stem.startswith("HK.") or "TOP" in name.upper() or "TURNOVER" in name.upper() or "RANK" in name.upper():
                continue
            # Use the user-form ticker (e.g. 00001.HK)
            try:
                d = json.load(open(fp))
                tk = d.get("ticker")
                if not tk or "." not in tk or not (tk.endswith(".HK") or tk.startswith("HK.")):
                    continue
            except Exception:
                continue
            all_tickers.append(tk)
    elif args.market == "US":
        us_dir = Path("/Users/kenken/dev/dsa-hk/charts/us200")
        all_tickers = []
        for fp in sorted(us_dir.glob("*.json")):
            name = fp.name
            # Skip ohlc, derivatives, non-ticker metadata files
            if "_ohlc" in name or fp.stem.startswith("US.") or name.startswith("US."):
                continue
            try:
                d = json.load(open(fp))
                tk = d.get("ticker")
                if not tk:
                    continue
            except Exception:
                continue
            all_tickers.append(tk)
    else:
        # JP — same as backtest_jp.py
        jp_universe = REPO / "jp_universe_200.json"
        all_tickers = json.load(open(jp_universe))
        all_tickers = [t if t.endswith(".T") else f"{t}.T" for t in all_tickers]

    if args.top:
        all_tickers = all_tickers[:args.top]
    print(f"Backtest {len(all_tickers)} {args.market} tickers, {args.workers} workers", flush=True)

    t0 = time.time()
    ok = 0
    fail = 0
    # Use ThreadPool for parallel yfinance calls
    from concurrent.futures import ThreadPoolExecutor, as_completed
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futures = {ex.submit(process_one, tk): tk for tk in all_tickers}
        for i, fut in enumerate(as_completed(futures), 1):
            tk = futures[fut]
            try:
                r = fut.result()
                if r:
                    ok += 1
                else:
                    fail += 1
            except Exception as e:
                fail += 1
                print(f"  [{i}/{len(all_tickers)}] {tk}: err {e}", flush=True)
            if i % 20 == 0:
                print(f"  [{i}/{len(all_tickers)}] done in {time.time()-t0:.1f}s ok={ok} fail={fail}", flush=True)
    print(f"Done {args.market}: {ok} ok, {fail} fail in {time.time()-t0:.1f}s", flush=True)


if __name__ == "__main__":
    main()

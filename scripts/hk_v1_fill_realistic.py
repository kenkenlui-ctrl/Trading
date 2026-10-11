#!/usr/bin/env python3
"""
hk_v1_fill_realistic.py — HK v1: how many of those "signals" are actual trades?

Follow-up to docs/vol-regime-multi-market-spec.md. That study found that only
18.6% of HK v1 signals had their trigger price print inside the signal bar,
and that restricting to those dropped mean net from 1.98% to 0.89%. That
figure is still too generous: the v1 action plan is computed from bar D's
CLOSE, so a limit order cannot be working until D+1. Every signal whose
trigger only printed during D was never fillable at all.

This simulates the order instead of assuming it filled:

    signal at D (decided on D's close)
      -> limit rests at the trigger from D+1, cancels after EXPIRY bars
      -> fills at the trigger when the adverse extreme reaches it
      -> after filling, exits on the v1 rule (close-based target / stop)

Costs are applied per side using the same core.cost_rt the production walk
uses for hk200. Reported for several expiries because the right lifetime for
a resting limit is a decision, not a fact.

RESEARCH ONLY. Nothing here is imported by any build script and HK has no
v2 production to gate.

    python3 scripts/hk_v1_fill_realistic.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path("/Users/kenken/dev/dsa-hk")
sys.path.insert(0, str(REPO / "scripts"))

import portfolio_test as PT                                   # noqa: E402
from bt_clean import build_mask, filter_candidates, load      # noqa: E402

DATA = REPO / "data"
CACHE = DATA / "hk_v1_signals_cache.json"
OUT_JSON = DATA / "hk_v1_fillable.json"
OUT_CSV = DATA / "hk_v1_fillable.csv"

EXPIRIES = (5, 10, 20, 40)
HEADLINE = 10          # matches portfolio_test.HOLD
COST_MKT = "hk200"


def load_bars_map() -> dict[str, dict]:
    out = {}
    for p in sorted((DATA / "bt10y2_hk").glob("*.json")):
        b = load(p)
        if b is None or b.empty:
            continue
        out[p.stem] = {
            "index": b.index,
            "low": b["low"].to_numpy(float),
            "high": b["high"].to_numpy(float),
            "close": b["close"].to_numpy(float),
        }
    return out


def fill_and_exit(bar: dict, i: int, trig: float, target: float, stop: float,
                  side: int, expiry: int) -> dict | None:
    """Walk the resting limit forward from the bar AFTER the signal.

    Returns None when the order never filled inside the expiry window. A fill
    that never reaches a target or stop inside 200 bars is also None, which
    is what the v1 reference function does — an unfinished trade is not a
    result.
    """
    n = len(bar["index"])
    lo, hi, cl = bar["low"], bar["high"], bar["close"]

    fill_i = None
    for j in range(i + 1, min(i + 1 + expiry, n)):
        if side > 0:
            if lo[j] <= trig:
                fill_i = j
                break
        elif hi[j] >= trig:
            fill_i = j
            break
    if fill_i is None:
        return None

    fill_px = trig
    latency = fill_i - i

    # Same bar as the fill: a limit that fills into a collapsing bar gets
    # stopped on that bar. Later bars use v1's close-based rule so the exit
    # semantics stay comparable with the published v1 backtest.
    if (lo[fill_i] <= stop) if side > 0 else (hi[fill_i] >= stop):
        exit_i, exit_px, why = fill_i, stop, "stop"
    else:
        exit_i = None
        for k in range(fill_i + 1, min(fill_i + 200, n)):
            if side > 0:
                if cl[k] >= target:
                    exit_i, exit_px, why = k, cl[k], "target"
                elif cl[k] <= stop:
                    exit_i, exit_px, why = k, cl[k], "stop"
            else:
                if cl[k] <= target:
                    exit_i, exit_px, why = k, cl[k], "target"
                elif cl[k] >= stop:
                    exit_i, exit_px, why = k, cl[k], "stop"
            if exit_i is not None:
                break
        if exit_i is None:
            return None

    gross = ((exit_px - fill_px) / fill_px) if side > 0 else ((fill_px - exit_px) / fill_px)
    return {"fill_date": bar["index"][fill_i], "latency_bars": latency,
            "fill_px": fill_px, "exit_date": bar["index"][exit_i],
            "exit_px": exit_px, "gross_realistic": float(gross), "why": why}


def run_one_expiry(sig: pd.DataFrame, bars: dict, expiry: int) -> pd.DataFrame:
    rows = []
    for r in sig.itertuples(index=False):
        bar = bars.get(r.sym)
        if bar is None:
            continue
        i = bar["index"].searchsorted(pd.Timestamp(r.entry_date))
        if i >= len(bar["index"]) or bar["index"][i] != pd.Timestamp(r.entry_date):
            continue
        res = fill_and_exit(bar, i, float(r.entry), float(r.target),
                            float(r.stop), int(r.side), expiry)
        if res is None:
            continue
        cost = PT.cost_rt(COST_MKT, PT.COST, res["why"] == "stop")
        rows.append({**res,
                     # Names portfolio_test.walk requires. The order is a
                     # limit at the trigger, so entry_date is the SIGNAL date
                     # (when the slot opens) and entry is the price actually
                     # paid — the trigger, because a limit never pays more.
                     "sym": r.sym, "side": int(r.side),
                     "entry_date": pd.Timestamp(r.entry_date),
                     "entry": res["fill_px"],
                     "exit": res["exit_px"],
                     "exit_date": res["exit_date"],
                     "stop": float(r.stop), "target": float(r.target),
                     "ret5d": float(r.ret5d), "why": res["why"],
                     "cost": cost,
                     "net_pp": (res["gross_realistic"] - cost) * 100})
    return pd.DataFrame(rows)


def walk_stats(df: pd.DataFrame, label: str) -> dict:
    """Portfolio stats on the side-aware walk.

    NOT portfolio_test.walk. That one sizes with `rps = entry - stop`, which
    is NEGATIVE for every short, so its `if rps <= 0: continue` silently drops
    the entire short book. HK v1 is ~2/3 SELL signals, so using it here would
    have reported a portfolio built only from the long third and called it
    "the walk" — a plausible number produced by quietly discarding two thirds
    of the trades.
    """
    if df.empty:
        return {"variant": label, "error": "no candidates"}
    c, _ = PT.walk_signed(df.to_dict("records"), COST_MKT)
    if c is None or c.empty:
        return {"variant": label, "error": "no curve"}
    eq = c["equity"].to_numpy(float)
    d = pd.to_datetime(c["date"])
    yrs = (d.iloc[-1] - d.iloc[0]).days / 365.25
    peak = np.maximum.accumulate(eq)
    r = np.diff(np.log(eq))
    n_long = int((df["side"] > 0).sum()) if "side" in df.columns else None
    return {
        "variant": label, "n_candidates": int(len(df)),
        "n_long": n_long, "n_short": int(len(df) - n_long) if n_long is not None else None,
        "total_return_pct": round(float((eq[-1] / eq[0] - 1) * 100), 2),
        "cagr_pct": round(float(((eq[-1] / eq[0]) ** (1 / yrs) - 1) * 100), 2),
        "max_dd_pct": round(float(((eq / peak) - 1).min() * 100), 2),
        "sharpe_like": round(float(r.mean() / r.std(ddof=1) * np.sqrt(252)), 3)
        if r.std(ddof=1) else None,
        "avg_positions": round(float(c["open"].mean()), 2),
        "days_at_slot_cap_pct": round(float((c["open"] >= PT.MAX_SLOTS).mean() * 100), 1),
        # Named explicitly. The baseline frame carries the cache's GROSS
        # (the v1 reference function never charges costs), the filled frame
        # carries GROSS_REALISTIC — calling both "mean_net_pp" put a gross
        # number next to a net number on the same comparison.
        "mean_gross_pp": round(float(df["gross_realistic"].mean() * 100), 3)
        if "gross_realistic" in df else round(float(df["gross"].mean() * 100), 3),
        "gross_is_net": bool("gross_realistic" not in df),
        "win_rate_pct": round(float((df["gross_realistic"] > 0).mean() * 100), 1)
        if "gross_realistic" in df else round(float((df["gross"] > 0).mean() * 100), 1),
    }


def main() -> int:
    if not CACHE.exists():
        print("no signal cache: data/hk_v1_signals_cache.json is missing.\n"
              "  data/ is gitignored, so the cache is a build artifact — rebuild it with\n"
              "    python3 scripts/hk_v1_signals.py\n"
              "  (the generator was split out of vol_regime_test.py on 2026-10-11, when the\n"
              "  volatility study was removed; this study no longer depends on that study.)")
        return 2
    cached = json.loads(CACHE.read_text())
    if "target" not in cached["records"][0]:
        print("cache has no target price — stale cache, rebuild with scripts/hk_v1_signals.py")
        return 2
    sig = pd.DataFrame(cached["records"])
    sig["entry_date"] = pd.to_datetime(sig["entry_date"])
    sig["exit_date"] = pd.to_datetime(sig["exit_date"])
    kept, dropped = filter_candidates(sig.to_dict("records"),
                                      mask=build_mask(DATA / "bt10y2_hk"))
    sig = pd.DataFrame(kept).reset_index(drop=True)
    print(f"signals in scope: {len(sig):,} ({sig.sym.nunique()} tickers, "
          f"glitch-mask dropped {dropped})")
    print(f"window: {sig.entry_date.min().date()} → {sig.exit_date.max().date()}")
    print(f"sides: {sig.side.value_counts().to_dict()}")

    bars = load_bars_map()
    print(f"bar files loaded: {len(bars)}\n")

    out: dict = {
        "spec": "docs/vol-regime-multi-market-spec.md (follow-up)",
        "market": "hk200",
        "signal_version": "v1",
        "cost_model": f"{COST_MKT} realistic round-trip "
                      f"= {PT.cost_rt(COST_MKT, 'realistic', False)*100:.3f}% "
                      f"(+{PT.cost_rt(COST_MKT, 'realistic', True)*100 - PT.cost_rt(COST_MKT, 'realistic', False)*100:.3f}% stop extra)",
        "fill_rule": "limit rests at the trigger from the bar AFTER the signal "
                     "(the plan is computed from that bar's close, so it cannot "
                     "fill on it); cancels after EXPIRY bars; fills at the "
                     "trigger; same-bar stop checked intrabar, later exits "
                     "close-based like v1",
        "n_signals": int(len(sig)),
        "expiries": {},
    }

    # Baseline: what the v1 backtest claims, with no fill requirement at all.
    out["baseline_as_if_all_filled"] = walk_stats(sig, "v1 as published (every signal treated as a trade)")

    frames = {}
    for exp in EXPIRIES:
        df = run_one_expiry(sig, bars, exp)
        frames[exp] = df
        if df.empty:
            out["expiries"][str(exp)] = {"n_filled": 0, "fill_rate_pct": 0.0}
            continue
        cost = df["net_pp"]
        lat = df["latency_bars"]
        e = {
            "n_filled": int(len(df)),
            "fill_rate_pct": round(len(df) / len(sig) * 100, 2),
            "median_latency_bars": int(lat.median()),
            "mean_latency_bars": round(float(lat.mean()), 1),
            "pct_filled_within_1_bar": round(float((lat <= 1).mean() * 100), 1),
            "mean_net_pp": round(float(cost.mean()), 3),
            "median_net_pp": round(float(cost.median()), 3),
            "win_rate_pct": round(float((cost > 0).mean() * 100), 1),
            "mean_net_pp_long": round(float(df[df.side > 0]["net_pp"].mean()), 3),
            "mean_net_pp_short": round(float(df[df.side < 0]["net_pp"].mean()), 3),
            "n_long": int((df.side > 0).sum()), "n_short": int((df.side < 0).sum()),
            "portfolio": walk_stats(df, f"v1 realistically filled, {exp}-bar expiry"),
        }
        out["expiries"][str(exp)] = e
        print(f"expiry {exp:>2} bars: filled {e['n_filled']:>6,} ({e['fill_rate_pct']:>5}%) "
              f"| median latency {e['median_latency_bars']} bars "
              f"| mean net {e['mean_net_pp']:+.3f} pp "
              f"| win {e['win_rate_pct']}% "
              f"| portfolio {e['portfolio'].get('total_return_pct')}% "
              f"({e['portfolio'].get('cagr_pct')}% CAGR)")

    head = frames.get(HEADLINE)
    if head is not None and not head.empty:
        head.to_csv(OUT_CSV, index=False)
        print(f"\n→ {OUT_CSV}")
        out["headline_expiry"] = HEADLINE

        # Where do the filled trades actually come from? If the fillable set is
        # concentrated in a handful of names, the strategy is not a strategy.
        by = head.groupby("sym").agg(n=("net_pp", "size"), net=("net_pp", "mean"))
        by = by.sort_values("n", ascending=False)
        tot = len(head)
        out["concentration"] = {
            "n_tickers_with_a_fill": int(len(by)),
            "top10_share_of_fills_pct": round(float(by["n"].head(10).sum() / tot * 100), 1),
            "top10_tickers": {k: int(v) for k, v in by["n"].head(10).items()},
            "median_n_per_filling_ticker": float(by["n"].median()),
            "share_of_tickers_contributing_lt_5_fills_pct": round(
                float((by["n"] < 5).mean() * 100), 1),
        }
        print(f"fills concentrate: top 10 tickers = "
              f"{out['concentration']['top10_share_of_fills_pct']}% of all fills "
              f"({out['concentration']['n_tickers_with_a_fill']} tickers fill at all)")

        # And the comparison that matters: against the published v1 number.
        base = out["baseline_as_if_all_filled"]
        port = head_stats = out["expiries"][str(HEADLINE)]["portfolio"]
        out["verdict_inputs"] = {
            "published_total_return_pct": base.get("total_return_pct"),
            "realistic_total_return_pct": port.get("total_return_pct"),
            "published_mean_net_pp": base.get("mean_net_pp"),
            "realistic_mean_net_pp": out["expiries"][str(HEADLINE)]["mean_net_pp"],
            "published_trades": base.get("n_candidates"),
            "realistic_fills": int(len(head)),
        }

        # Decisive cross-check: is the SIGNAL uninformative, or is it the
        # trigger price that is unobtainable? Enter at the signal bar's CLOSE
        # — no limit, no fill question — and apply the same target/stop rule.
        # If this is also negative, no execution model rescues the strategy.
        mkt_rows = []
        for r in sig.itertuples(index=False):
            bar = bars.get(r.sym)
            if bar is None:
                continue
            i = bar["index"].searchsorted(pd.Timestamp(r.entry_date))
            if i >= len(bar["index"]) or bar["index"][i] != pd.Timestamp(r.entry_date):
                continue
            ep = float(bar["close"][i])
            cl, n = bar["close"], len(bar["index"])
            for k in range(i + 1, min(i + 200, n)):
                if r.side > 0:
                    hit_t, hit_s = cl[k] >= r.target, cl[k] <= r.stop
                else:
                    hit_t, hit_s = cl[k] <= r.target, cl[k] >= r.stop
                if not (hit_t or hit_s):
                    continue
                why = "target" if hit_t else "stop"
                ex = float(cl[k])
                cost = PT.cost_rt(COST_MKT, PT.COST, why == "stop")
                g = ((ex - ep) / ep) if r.side > 0 else ((ep - ex) / ep)
                mkt_rows.append({"sym": r.sym, "side": int(r.side), "gross": g,
                                 "why": why, "net_pp": (g - cost) * 100})
                break
        if mkt_rows:
            mdf = pd.DataFrame(mkt_rows)
            out["market_entry_cross_check"] = {
                "rule": "enter at the signal bar's close, same v1 target/stop exit",
                "n": int(len(mdf)),
                "mean_gross_pp": round(float(mdf["gross"].mean() * 100), 3),
                "mean_net_pp": round(float(mdf["net_pp"].mean()), 3),
                "win_rate_pct": round(float((mdf["gross"] > 0).mean() * 100), 1),
                "stop_rate_pct": round(float((mdf["why"] == "stop").mean() * 100), 1),
            }

        # Payoff shape. A positive hit rate with a negative mean is the whole
        # story, so it gets named rather than left for the reader to infer.
        gp = head["gross_realistic"].astype(float) * 100
        w_, l_ = gp[gp > 0], gp[gp <= 0]
        out["payoff"] = {
            "n_win": int(len(w_)), "n_loss": int(len(l_)),
            "mean_win_pp": round(float(w_.mean()), 3),
            "mean_loss_pp": round(float(l_.mean()), 3),
            "win_to_loss_ratio": round(float(w_.mean() / abs(l_.mean())), 2),
            "expectancy_pp": round(float(gp.mean()), 3),
            "losses_over_10pp": int((l_ < -10).sum()),
            "share_of_loss_pp_from_those": round(
                float(abs(l_[l_ < -10].sum()) / abs(l_.sum()) * 100), 1),
        }

    OUT_JSON.write_text(json.dumps(out, indent=2, ensure_ascii=False, default=str))
    print(f"→ {OUT_JSON}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
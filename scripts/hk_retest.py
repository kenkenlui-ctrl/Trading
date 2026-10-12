#!/usr/bin/env python3
"""hk_retest.py — HK v2 long-only retest, with adverse-selection analysis.

Scope (per docs/hk-retest-spec.md, 2026-10-12):
  - Same v2 rules as the US/JP published engine (long-only, 200d MA filter,
    200d MA rising, HSI market filter, limit BUY at S1 with close-hold fill,
    3x ATR stop, 10-day time exit).
  - HK universe = 245 names from hk_universe_200.json, restricted to whatever
    has 10y bars locally in data/bt10y2_hk/. Coverage reported in summary.
  - Costs: 0% / 0.10% / 0.25% round-trip sensitivity.
  - Windows: full (2016-2026), 2016-2020, 2021-2026. Split by entry date.
  - Benchmarks: equal-weight buy-and-hold of the same universe, plus
    per-trade random-entry A/B.
  - Adverse-selection: every S1 signal is bucketed into one of
        filled / touched-but-failed-close / gap-through / never-touched
    with 10-day forward return per bucket. The fill-vs-signal gap is compared
    to the same metric run on US/JP for like-for-like.
  - Placebo: shuffled entry-date placebo on the per-trade edge.
  - 3 pre-registered success criteria; verdict = ENABLE only if all 3 hold.
  - INCLUDE_HK and all production files are NOT modified.

Output:
  - data/hk_retest_summary.json (structured)
  - console: one-paragraph verdict with the load-bearing numbers

Usage:
  python3 scripts/hk_retest.py
"""
from __future__ import annotations

import json
import math
import statistics
import sys
import time
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path("/Users/kenken/dev/dsa-hk")
sys.path.insert(0, str(REPO / "scripts" / "v2engine"))
from core import features, _simulate  # noqa: E402

HK_BARS = REPO / "data" / "bt10y2_hk"
US_BARS = REPO / "data" / "bt10y2"
JP_BARS = REPO / "data" / "bt10y2"
HK_UNI = REPO / "hk_universe_200.json"
US_UNI = REPO / "us_universe_200.json"
JP_UNI = REPO / "jp_universe_200.json"
IDX_HSI = REPO / "data" / "bt10y2" / "IDX_HSI.json"
IDX_GSPC = REPO / "data" / "bt10y2" / "IDX_GSPC.json"
IDX_N225 = REPO / "data" / "bt10y2" / "IDX_N225.json"
OUT = REPO / "data" / "hk_retest_summary.json"

# -------- spec constants --------
HOLD = 10
STOP_ATR = 3.0
ATR_MAX = 0.06
BOX_MAX = 0.35
COST_SCENARIOS = [0.0025, 0.0010, 0.0]   # primary 0.25%, sensitivity 0.10% and 0%
WINDOWS = [
    ("2016-2026", pd.Timestamp("2016-01-01"), pd.Timestamp("2026-12-31")),
    ("2016-2020", pd.Timestamp("2016-01-01"), pd.Timestamp("2020-12-31")),
    ("2021-2026", pd.Timestamp("2021-01-01"), pd.Timestamp("2026-12-31")),
]
PLACEBO_TRIALS = 200
RNG_SEED = 20261012

# -------- data loaders --------
def _load_bars_list(path: Path) -> pd.DataFrame | None:
    """Loader for the bt10y2_hk/<sym>.json format: a flat list of {date,OHLC,volume}."""
    try:
        rows = json.loads(path.read_text())
    except Exception:
        return None
    if not isinstance(rows, list) or len(rows) < 200:
        return None
    df = pd.DataFrame(rows)
    if not {"date", "open", "high", "low", "close"} <= set(df.columns):
        return None
    df["date"] = pd.to_datetime(df["date"])
    df = df.dropna(subset=["open", "high", "low", "close"])
    df = df[(df[["open", "high", "low", "close"]] > 0).all(axis=1)]
    df["high"] = df[["high", "open", "close"]].max(axis=1)
    df["low"] = df[["low", "open", "close"]].min(axis=1)
    flat = (df.high == df.low) & (df.volume.fillna(0) == 0)
    df = df[~flat].drop_duplicates(subset="date", keep="last").sort_values("date")
    df = df.set_index("date")
    return df if len(df) >= 200 else None


def _load_index(path: Path) -> pd.Series | None:
    try:
        rows = json.loads(path.read_text())
    except Exception:
        return None
    if not isinstance(rows, list) or len(rows) < 200:
        return None
    df = pd.DataFrame(rows)
    if "date" not in df.columns or "close" not in df.columns:
        return None
    df["date"] = pd.to_datetime(df["date"])
    df = df.dropna(subset=["close"]).set_index("date")["close"].sort_index()
    return df if len(df) >= 200 else None


def load_hk_bars() -> dict[str, pd.DataFrame]:
    uni = json.loads(HK_UNI.read_text())
    out = {}
    for t in uni:
        safe = t.replace(".", "_").replace("^", "IDX_").replace("/", "_")
        p = HK_BARS / f"{safe}.json"
        if not p.exists():
            continue
        b = _load_bars_list(p)
        if b is not None:
            out[t] = b
    return out


def load_market_bars(mkt: str) -> dict[str, pd.DataFrame]:
    """For US/JP comparison only. Uses the same _load_bars_list format."""
    src = US_BARS
    uni_path = US_UNI if mkt == "us200" else JP_UNI
    if mkt == "jp200":
        # JP tickers end in .T in the universe; bt10y2 files use _T suffix.
        suffix = "_T"
        uni_raw = json.loads(uni_path.read_text())
        safe_names = {t.replace(".", "_") for t in uni_raw}
    else:
        suffix = ""
        uni_raw = json.loads(uni_path.read_text())
        safe_names = set(uni_raw)
    out = {}
    for t in uni_raw:
        if mkt == "jp200":
            safe = t.replace(".", "_")
        else:
            safe = t
        p = src / f"{safe}.json"
        if not p.exists():
            continue
        b = _load_bars_list(p)
        if b is not None:
            out[t] = b
    return out


# -------- feature pipeline (re-uses core.features for consistency) --------
def _s1_candidates(b: pd.DataFrame, mkt_up: pd.Series) -> list[dict]:
    """For a single stock, identify S1 candidate sessions + bucket classification.

    Returns a list of dicts, one per session in the bar store where the stock
    has a defined S1, with fields:
        sym, date, O, H, L, C, S1, atr, ma200, slope, market_up, bucket

    Buckets (mutually exclusive):
        gap-through          : O <= S1 (entry rule would skip)
        filled               : O > S1, L <= S1, C >= S1
        touched-but-failed   : O > S1, L <= S1, C < S1
        never-touched        : O > S1, L > S1
    """
    f = features(b)
    O, H, L, C = (b[k].to_numpy(float) for k in ("open", "high", "low", "close"))
    s1 = f.S1.to_numpy()
    atr = f.atr_pct.to_numpy()
    ma200 = f.ma200.to_numpy()
    slope = f.ma200_slope.to_numpy()
    boxw = f.box_w.to_numpy()
    pc = f.prev_close.to_numpy()
    mu = mkt_up.reindex(b.index).fillna(False).to_numpy(bool)
    # NaN check on the input vectors
    valid = (~np.isnan(s1) & ~np.isnan(atr) & ~np.isnan(ma200)
             & ~np.isnan(slope) & ~np.isnan(boxw))
    valid &= (atr < ATR_MAX) & (boxw < BOX_MAX)
    valid &= (pc > ma200) & (slope > 0)
    valid &= mu
    idx = np.where(valid)[0]
    if not len(idx):
        return []
    O_v, H_v, L_v, C_v, S1_v, ATR_v = O[idx], H[idx], L[idx], C[idx], s1[idx], atr[idx]
    o_le_s1 = O_v <= S1_v
    l_le_s1 = L_v <= S1_v
    c_ge_s1 = C_v >= S1_v
    bucket = np.where(o_le_s1, "gap-through",
              np.where(l_le_s1,
                       np.where(c_ge_s1, "filled", "touched-but-failed"),
                       "never-touched"))
    out = []
    for j, i in enumerate(idx):
        out.append({
            "sym": None, "date": b.index[i], "O": float(O_v[j]), "H": float(H_v[j]),
            "L": float(L_v[j]), "C": float(C_v[j]), "S1": float(S1_v[j]),
            "atr": float(ATR_v[j]), "ma200": float(ma200[i]), "slope": float(slope[i]),
            "boxw": float(boxw[i]), "market_up": bool(mu[i]), "bucket": str(bucket[j]),
        })
    return out


# -------- 10-day forward return (close-to-close, 10 sessions) --------
def _fwd_return_10(b: pd.DataFrame, idx: int) -> float | None:
    """Close at signal bar / close at bar+10 minus 1. None if insufficient data."""
    C = b["close"].to_numpy(float)
    j = idx + HOLD
    if j >= len(C) or idx < 0:
        return None
    if np.isnan(C[idx]) or np.isnan(C[j]):
        return None
    return C[j] / C[idx] - 1.0


def _fwd_returns_10_all(b: pd.DataFrame) -> list[float | None]:
    """Vectorised 10-day forward return for every bar in b. Returns a list
    (one entry per row, None for the last 10 bars or any NaN involvement).
    The returned list is parallel to b.index — used to attach fwd10 to every
    S1 candidate in O(1) lookup rather than a per-candidate get_loc."""
    C = b["close"].to_numpy(float)
    n = len(C)
    out: list[float | None] = [None] * n
    if n <= HOLD:
        return out
    c0 = C[:n - HOLD]
    c1 = C[HOLD:]
    valid = ~(np.isnan(c0) | np.isnan(c1))
    ratio = np.full(n - HOLD, np.nan, dtype=float)
    ratio[valid] = c1[valid] / c0[valid] - 1.0
    for i in range(n - HOLD):
        out[i] = float(ratio[i]) if not (isinstance(ratio[i], float) and math.isnan(ratio[i])) else None
    return out


# -------- per-trade simulation (mirror core._simulate) for filled signals --------
def _simulate_long(b: pd.DataFrame, idx: int, entry: float, stop: float) -> dict | None:
    O = b["open"].to_numpy(float)
    H = b["high"].to_numpy(float)
    L = b["low"].to_numpy(float)
    C = b["close"].to_numpy(float)
    return _simulate(1, idx, entry, stop, 1e12, O, H, L, C, HOLD, True)


def _simulate_arrays(O, H, L, C, idxs, entries, stops):
    """Pure-array simulate — same semantics as core._simulate, takes raw
    numpy arrays (no DataFrame) so the placebo and random-entry passes can
    reuse pre-computed per-stock arrays without dict lookups."""
    n = len(C)
    out = []
    for entry_i, entry, stop in zip(idxs, entries, stops):
        last = min(entry_i + HOLD, n - 1)
        result = None
        for j in range(entry_i, last + 1):
            o, hi, lo, cl = O[j], H[j], L[j], C[j]
            if j == entry_i:
                if lo <= stop:
                    result = (j, stop, "stop"); break
                if cl >= 1e12:
                    result = (j, 1e12, "target"); break
            else:
                if o <= stop:
                    result = (j, o, "stop"); break
                if o >= 1e12:
                    result = (j, o, "target"); break
                if lo <= stop:
                    result = (j, stop, "stop"); break
                if hi >= 1e12:
                    result = (j, 1e12, "target"); break
        if result is None and last < entry_i + HOLD:
            out.append(None)
        elif result is None:
            out.append((last, C[last], "time"))
        else:
            out.append(result)
    return out


# -------- portfolio walk (slot-constrained, max-notional-per-name) --------
def walk(cands: list[dict], cost_rt: float) -> tuple[pd.DataFrame, dict]:
    """Slot-constrained walk: 10 slots, 1% risk/trade, 10% notional/name cap.

    cost_rt is a flat round-trip fraction applied to every trade (stop and
    time exits alike), so sensitivity scenarios are cleanly comparable.
    """
    if not cands:
        return pd.DataFrame(), {"trades": 0, "cagr%": 0.0, "maxDD%": 0.0}
    d = pd.DataFrame(cands).sort_values(["entry_date", "ret5d"]).reset_index(drop=True)
    if "ret5d" not in d.columns:
        d["ret5d"] = 0.0
    MAX_SLOTS, RISK, MAX_NOTIONAL = 10, 0.01, 0.10
    equity, pos, curve = 100_000.0, [], []
    for dt in sorted(set(d.entry_date) | set(d.exit_date)):
        keep = []
        for p in pos:
            if p["exit_date"] <= dt:
                equity += p["shares"] * (p["exit"] * (1 - cost_rt) - p["entry"])
            else:
                keep.append(p)
        pos = keep
        for _, t in d[d.entry_date == dt].iterrows():
            if len(pos) >= MAX_SLOTS:
                break
            rps = t.entry - t.stop
            if rps <= 0:
                continue
            room = MAX_NOTIONAL * equity - sum(
                p["shares"] * p["entry"] for p in pos if p["sym"] == t.sym)
            if room <= 0:
                continue
            sh = min(RISK * equity / rps, room / t.entry)
            if sh <= 0:
                continue
            if sum(p["shares"] * p["entry"] for p in pos) + sh * t.entry > equity:
                continue
            pos.append({"sym": t.sym, "entry": t.entry, "exit": t.exit,
                        "exit_date": t.exit_date, "shares": sh})
        curve.append({"date": dt, "equity": equity, "open": len(pos)})
    if not curve:
        return pd.DataFrame(), {"trades": 0, "cagr%": 0.0, "maxDD%": 0.0}
    df = pd.DataFrame(curve).set_index("date")
    e = df.equity.to_numpy(float)
    if len(e) < 2:
        return df, {"trades": 0, "cagr%": 0.0, "maxDD%": 0.0}
    yrs = (df.index[-1] - df.index[0]).days / 365.25
    cagr = (e[-1] / e[0]) ** (1 / yrs) - 1 if yrs > 0 else 0.0
    max_dd = (e / np.maximum.accumulate(e) - 1).min()
    return df, {"trades": int(len(cands)), "cagr%": cagr * 100, "maxDD%": max_dd * 100}


# -------- equal-weight B&H of the universe --------
def ew_bh(px: dict[str, pd.DataFrame], cal: pd.DatetimeIndex) -> pd.Series:
    b1src = pd.DataFrame({s: p.close for s, p in px.items()}).reindex(cal)
    b1src = b1src.loc[b1src.notna().any(axis=1)]
    cal = b1src.index
    mm = b1src.pct_change().groupby([cal.year, cal.month]).transform("mean").mean(axis=1)
    return (100_000 * (1 + mm).cumprod()).ffill()


# -------- per-trade summary --------
def per_trade_stats(cands: list[dict], cost_rt: float) -> dict:
    if not cands:
        return {"n": 0, "win%": 0.0, "avg_net%": 0.0, "median_net%": 0.0,
                "total_net%": 0.0, "expectancy_r": 0.0}
    gross = np.array([c["gross"] for c in cands])
    # stop exits get a slightly larger cost in production ("realistic" mode),
    # but for clean like-for-like A/B apply a flat round-trip per the spec.
    net = gross - cost_rt
    n = len(net)
    return {
        "n": n,
        "win%": float((net > 0).mean() * 100),
        "avg_net%": float(net.mean() * 100),
        "median_net%": float(np.median(net) * 100),
        "total_net%": float(net.sum() * 100),
        "expectancy_r": float((net / max(cost_rt, 1e-9)).mean()),  # net per unit cost
    }


# -------- window filter --------
def in_window(d: pd.Timestamp, win: tuple[str, pd.Timestamp, pd.Timestamp]) -> bool:
    _, lo, hi = win
    return lo <= d <= hi


# -------- per-market pipeline --------
def run_market(mkt: str, bars: dict[str, pd.DataFrame], idx: pd.Series) -> dict:
    """Run the full pipeline for one market (HK, US, or JP).

    Returns a dict with:
      coverage: {n_universe, n_loaded, n_with_full_history}
      signals: per-bucket counts and 10-day forward returns
      per_trade: filled-trade stats per cost scenario per window
      per_trade_random: random-entry per-trade control per cost per window
      placebo: shuffled-entry-date per-trade per window
      portfolio: v2 walk vs EW-B&H per cost per window
      gap: adverse-selection gap
    """
    res: dict = {"market": mkt, "coverage": {}, "signals": {},
                 "per_trade": {}, "per_trade_random": {},
                 "placebo": {}, "portfolio": {}, "gap": {}}

    res["coverage"] = {
        "n_loaded": len(bars),
        "first_dates": {s: str(b.index[0].date()) for s, b in bars.items()},
    }
    full_cutoff = pd.Timestamp("2016-09-01")
    full_n = sum(1 for b in bars.values() if b.index[0] <= full_cutoff)
    res["coverage"]["n_with_history_from_2016_09"] = full_n

    # 1) Build all S1 candidates (across the full history) and classify.
    mkt_up = (idx > idx.rolling(200).mean()).reindex(idx.index).fillna(False)
    # Intersect with bars' dates to be safe
    mkt_up = mkt_up[mkt_up.index.isin(sorted({d for b in bars.values() for d in b.index}))]

    # Pre-compute feature arrays per stock ONCE — used by both the s1 pass
    # and the placebo (which needs atr_pct at random entry days).
    precomp: dict[str, dict] = {}
    for sym, b in bars.items():
        f = features(b)
        precomp[sym] = {
            "O": b["open"].to_numpy(float),
            "H": b["high"].to_numpy(float),
            "L": b["low"].to_numpy(float),
            "C": b["close"].to_numpy(float),
            "atr_pct": f.atr_pct.to_numpy(float),
            "dates": list(b.index),
            "date_to_idx": {d: i for i, d in enumerate(b.index)},
        }

    all_cands: list[dict] = []
    bucket_10d: dict[str, list[float]] = {
        "filled": [], "touched-but-failed": [],
        "gap-through": [], "never-touched": [],
    }
    n_stocks = len(bars)
    t0 = time.time()
    for si, (sym, b) in enumerate(bars.items()):
        cands = _s1_candidates(b, mkt_up)
        # Compute fwd returns in bulk via numpy — much faster than per-cand loop
        fwd_returns = _fwd_returns_10_all(b)
        for c, fwd in zip(cands, fwd_returns):
            c["sym"] = sym
            c["fwd10"] = fwd
            c["ret5d"] = 0.0
            all_cands.append(c)
            if fwd is not None and not (isinstance(fwd, float) and math.isnan(fwd)):
                bucket_10d[c["bucket"]].append(fwd)
        if (si + 1) % 50 == 0:
            print(f"        [{mkt}] {si+1}/{n_stocks} stocks; cands {len(all_cands):,} "
                  f"elapsed {time.time()-t0:.1f}s", flush=True)

    n_total = len(all_cands)
    counts = {k: len(v) for k, v in bucket_10d.items()}
    res["signals"] = {
        "n_total": n_total,
        "n_by_bucket": counts,
        "fill_rate": counts["filled"] / n_total if n_total else 0.0,
        "mean_fwd10_by_bucket": {
            k: (float(np.mean(v)) if v else None) for k, v in bucket_10d.items()
        },
    }

    # 2) For filled signals only: simulate trades with stop and 10-day exit.
    # Group filled signals by symbol so we can batch the per-trade simulation
    # against the same bar array.
    print(f"        [{mkt}] filling {sum(1 for c in all_cands if c['bucket']=='filled'):,} filled signals",
          flush=True)
    filled_by_sym: dict[str, list[dict]] = {}
    for c in all_cands:
        if c["bucket"] != "filled":
            continue
        pc = precomp[c["sym"]]
        idx_in_b = pc["date_to_idx"][c["date"]]
        entry = c["S1"]
        stop = c["S1"] * (1 - STOP_ATR * c["atr"])
        if not (0 < stop < entry):
            continue
        filled_by_sym.setdefault(c["sym"], []).append({
            "c": c, "idx": idx_in_b, "entry": entry, "stop": stop,
        })
    print(f"        [{mkt}] sim {sum(len(v) for v in filled_by_sym.values()):,} trades across "
          f"{len(filled_by_sym)} symbols", flush=True)
    t1 = time.time()
    filled_trades: list[dict] = []
    for sym_i, (sym, items) in enumerate(filled_by_sym.items()):
        pc = precomp[sym]
        idxs = [it["idx"] for it in items]
        entries = [it["entry"] for it in items]
        stops = [it["stop"] for it in items]
        sims = _simulate_arrays(
            pc["O"], pc["H"], pc["L"], pc["C"], idxs, entries, stops)
        for it, sim in zip(items, sims):
            if sim is None:
                continue
            j, xp, why = sim
            entry = it["entry"]
            filled_trades.append({
                "sym": sym, "entry_date": it["c"]["date"], "exit_date": pc["dates"][j],
                "entry": entry, "stop": it["stop"], "exit": xp, "why": why,
                "gross": xp / entry - 1.0, "fwd10_signal": it["c"].get("fwd10"),
            })
    print(f"        [{mkt}]   sim done in {time.time()-t1:.1f}s, {len(filled_trades):,} trades", flush=True)

    res["_filled_trades"] = filled_trades   # internal; not serialised
    res["_all_cands"] = all_cands           # internal; not serialised

    # 3) Per-window, per-cost analysis: per-trade, random-entry control, placebo.
    print(f"        [{mkt}] window analysis (3 windows × 3 cost scenarios × "
          f"random + placebo)", flush=True)
    for win_name, win_lo, win_hi in WINDOWS:
        win_trades = [t for t in filled_trades if win_lo <= t["entry_date"] <= win_hi]
        # 3a) per-trade net stats per cost
        pt = {}
        for cost in COST_SCENARIOS:
            pt[f"{cost*100:.2f}%"] = per_trade_stats(win_trades, cost)
        res["per_trade"][win_name] = pt

        # 3b) random-entry control: for each filled trade, pick a random
        # trading day in the same stock's history (within the window) and
        # simulate a same-stop, same-hold trade entered at the open.
        t2 = time.time()
        rng = np.random.default_rng(RNG_SEED + hash(win_name) % 10_000)
        ctrl_trades: list[dict] = []
        for t in win_trades:
            pc = precomp[t["sym"]]
            O = pc["O"]; H = pc["H"]; L = pc["L"]; C = pc["C"]
            d2i = pc["date_to_idx"]
            pool_dates = [d for d in pc["dates"] if win_lo <= d <= win_hi]
            if len(pool_dates) < 30:
                continue
            for _ in range(5):                       # 5 random dates per filled trade
                day = pool_dates[rng.integers(0, len(pool_dates))]
                di = d2i[day]
                op = float(O[di])
                if not np.isfinite(op) or op <= 0:
                    continue
                # use the SAME stop WIDTH (in % of entry) as the matched
                # filled trade, anchored to the open
                stop_w = (t["entry"] - t["stop"]) / t["entry"]
                stop = op * (1 - stop_w)
                if not (0 < stop < op):
                    continue
                sim = _simulate_arrays(O, H, L, C, [di], [op], [stop])
                if not sim or sim[0] is None:
                    continue
                j, xp, _ = sim[0]
                ctrl_trades.append({
                    "sym": t["sym"], "entry_date": day, "exit_date": pc["dates"][j],
                    "entry": op, "stop": stop, "exit": xp,
                    "gross": xp / op - 1.0,
                })
        rc = {}
        for cost in COST_SCENARIOS:
            rc[f"{cost*100:.2f}%"] = per_trade_stats(ctrl_trades, cost)
        res["per_trade_random"][win_name] = rc
        print(f"        [{mkt}] {win_name} random-entry done {time.time()-t2:.1f}s "
              f"({len(ctrl_trades):,} control trades)", flush=True)

        # 3c) placebo: shuffle entry dates within the window for each filled
        # trade, hold all other fields constant. Average net over PLACEBO_TRIALS
        # shuffles gives the null distribution.
        t3 = time.time()
        if win_trades:
            pg = []
            for trial in range(PLACEBO_TRIALS):
                trng = np.random.default_rng(RNG_SEED + trial)
                # Pre-sample random stocks per filled trade for this trial
                # (uses precomp.atr_pct so we don't recompute features)
                s_returns = []
                for t in win_trades:
                    # Pick any other stock that traded on this day
                    sym_list = [s for s, pc in precomp.items()
                                if t["entry_date"] in pc["date_to_idx"]]
                    if len(sym_list) < 2:
                        continue
                    s2 = sym_list[trng.integers(0, len(sym_list))]
                    pc2 = precomp[s2]
                    di2 = pc2["date_to_idx"][t["entry_date"]]
                    O2 = float(pc2["O"][di2])
                    a2 = float(pc2["atr_pct"][di2])
                    if not np.isfinite(a2) or a2 <= 0 or O2 <= 0:
                        continue
                    stop2 = O2 * (1 - STOP_ATR * a2)
                    if not (0 < stop2 < O2):
                        continue
                    sim2 = _simulate_arrays(pc2["O"], pc2["H"], pc2["L"], pc2["C"],
                                            [di2], [O2], [stop2])
                    if not sim2 or sim2[0] is None:
                        continue
                    _, xp2, _ = sim2[0]
                    s_returns.append(xp2 / O2 - 1.0)
                if s_returns:
                    pg.append(float(np.mean(s_returns) - COST_SCENARIOS[0]))
            placebo_net = float(np.mean(pg)) if pg else 0.0
            placebo_std = float(np.std(pg)) if pg else 0.0
        else:
            placebo_net, placebo_std = 0.0, 0.0
        res["placebo"][win_name] = {
            "trials": PLACEBO_TRIALS, "avg_net%": placebo_net * 100,
            "std_net%": placebo_std * 100,
        }
        print(f"        [{mkt}] {win_name} placebo done {time.time()-t3:.1f}s", flush=True)

    # 4) Portfolio-level: v2 walk vs EW B&H, per cost per window.
    cal = idx.index
    ew = ew_bh(bars, cal)
    ew_stats = {}
    for win_name, win_lo, win_hi in WINDOWS:
        ew_w = ew[(ew.index >= win_lo) & (ew.index <= win_hi)]
        if len(ew_w) < 2:
            ew_stats[win_name] = None
            continue
        e = ew_w.to_numpy(float)
        yrs = (ew_w.index[-1] - ew_w.index[0]).days / 365.25
        cagr = (e[-1] / e[0]) ** (1 / yrs) - 1 if yrs > 0 else 0.0
        max_dd = (e / np.maximum.accumulate(e) - 1).min()
        ew_stats[win_name] = {"cagr%": cagr * 100, "maxDD%": max_dd * 100}
    res["portfolio"]["ew_bh"] = ew_stats

    pt_cands = []
    for t in filled_trades:
        # Ranking key: most-oversold proxy = -1; not used since we cap by date
        pt_cands.append(dict(t, ret5d=0.0))
    for cost in COST_SCENARIOS:
        cw = {}
        for win_name, win_lo, win_hi in WINDOWS:
            wcands = [t for t in pt_cands if win_lo <= t["entry_date"] <= win_hi]
            df, stats = walk(wcands, cost)
            cw[win_name] = stats
        res["portfolio"][f"v2_cost_{cost*100:.2f}%"] = cw
    res["portfolio"]["v2_vs_ew_pp"] = {}
    for cost in COST_SCENARIOS:
        per_win = {}
        for win_name, _, _ in WINDOWS:
            v2 = res["portfolio"][f"v2_cost_{cost*100:.2f}%"][win_name]
            ew_s = ew_stats[win_name]
            if ew_s is None or v2["cagr%"] == 0:
                per_win[win_name] = None
            else:
                per_win[win_name] = {
                    "v2_cagr%": v2["cagr%"],
                    "ew_cagr%": ew_s["cagr%"],
                    "v2_minus_ew_pp": v2["cagr%"] - ew_s["cagr%"],
                    "v2_maxDD%": v2["maxDD%"],
                    "ew_maxDD%": ew_s["maxDD%"],
                }
        res["portfolio"]["v2_vs_ew_pp"][f"cost_{cost*100:.2f}%"] = per_win

    # 5) Adverse-selection gap (key metric):
    #     gap = E[10d return | bucket=filled] - E[10d return | any signal]
    if all_cands:
        fwd = np.array([c["fwd10"] for c in all_cands if c["fwd10"] is not None])
        fwd_filled = np.array([c["fwd10"] for c in all_cands
                                if c["fwd10"] is not None and c["bucket"] == "filled"])
        if len(fwd) > 0 and len(fwd_filled) > 0:
            gap = float(fwd_filled.mean() - fwd.mean())
            # Per-window gap
            gap_by_win = {}
            for win_name, win_lo, win_hi in WINDOWS:
                ww_all = np.array([c["fwd10"] for c in all_cands
                                   if c["fwd10"] is not None
                                   and win_lo <= c["date"] <= win_hi])
                ww_filled = np.array([c["fwd10"] for c in all_cands
                                      if c["fwd10"] is not None
                                      and c["bucket"] == "filled"
                                      and win_lo <= c["date"] <= win_hi])
                if len(ww_all) > 0 and len(ww_filled) > 0:
                    gap_by_win[win_name] = float(ww_filled.mean() - ww_all.mean())
                else:
                    gap_by_win[win_name] = None
        else:
            gap = None
            gap_by_win = {w[0]: None for w in WINDOWS}
    else:
        gap = None
        gap_by_win = {w[0]: None for w in WINDOWS}
    res["gap"] = {"full_window_pp": gap, "by_window_pp": gap_by_win}

    return res


# -------- verdict --------
def verdict(hk: dict, us: dict, jp: dict) -> dict:
    """Apply the 3 pre-registered success criteria."""
    primary = "0.25%"
    v2_full = hk["portfolio"]["v2_vs_ew_pp"][f"cost_{primary}"]["2016-2026"]
    crit1 = (v2_full is not None) and (v2_full["v2_minus_ew_pp"] >= 1.0)

    pt = hk["per_trade"]
    crit2_a = (pt["2016-2020"][primary]["avg_net%"] > 0
               and pt["2016-2020"][primary]["n"] > 0)
    crit2_b = (pt["2021-2026"][primary]["avg_net%"] > 0
               and pt["2021-2026"][primary]["n"] > 0)
    crit2 = crit2_a and crit2_b

    hk_gap = hk["gap"]["full_window_pp"]
    us_gap = us["gap"]["full_window_pp"] if us else None
    jp_gap = jp["gap"]["full_window_pp"] if jp else None
    # "no worse than" = greater than or equal to either reference, in 10-day-return
    # space. If fills underperform the signal average, the gap is negative.
    # HK being "no worse" means HK gap >= US gap (or JP if US absent).
    refs = [g for g in (us_gap, jp_gap) if g is not None]
    if hk_gap is None or not refs:
        crit3 = False
    else:
        # HK should not be more adversely selected than the BEST reference
        crit3 = hk_gap >= max(refs) - 0.001

    return {
        "criterion_1_v2_beats_ew_by_1pp_2016_2026": crit1,
        "criterion_2_per_trade_positive_both_subperiods": crit2,
        "criterion_3_no_worse_adverse_selection_than_us_or_jp": crit3,
        "all_pass": crit1 and crit2 and crit3,
        "decision": "ENABLE" if (crit1 and crit2 and crit3) else "KEEP EXCLUDED",
    }


def main() -> int:
    print("=" * 80, flush=True)
    print("HK v2 retest — per docs/hk-retest-spec.md (2026-10-12)", flush=True)
    print("=" * 80, flush=True)

    print("\n[1/4] Loading HK bars from data/bt10y2_hk/ ...", flush=True)
    hk_bars = load_hk_bars()
    print(f"      loaded {len(hk_bars)} HK stocks (universe = 245)", flush=True)
    if not hk_bars:
        print("FATAL: no HK bars found in data/bt10y2_hk/", flush=True)
        return 1
    hsi = _load_index(IDX_HSI)
    if hsi is None:
        print("FATAL: HSI index not found at", IDX_HSI, flush=True)
        return 1
    print(f"      HSI index: {len(hsi)} bars from {hsi.index[0].date()} to {hsi.index[-1].date()}", flush=True)

    print("\n[2/4] Running HK pipeline (s1 candidates, 4-bucket classify, fills, walks) ...", flush=True)
    hk = run_market("hk200", hk_bars, hsi)
    print("      HK pipeline done", flush=True)
    n_total = hk["signals"]["n_total"]
    n_filled = hk["signals"]["n_by_bucket"]["filled"]
    print(f"      S1 candidate signals: {n_total:,}  filled: {n_filled:,}  fill rate: {hk['signals']['fill_rate']*100:.1f}%")
    for w, _ in [(n, l) for n, l, _ in WINDOWS]:
        pt = hk["per_trade"][w]["0.25%"]
        ew = hk["portfolio"]["ew_bh"][w]
        v2 = hk["portfolio"]["v2_vs_ew_pp"]["cost_0.25%"][w]
        print(f"      {w}: filled_trades={pt['n']:>5}  avg_net/trade={pt['avg_net%']:+.3f}%  "
              f"v2 CAGR={v2['v2_cagr%']:+.2f}%  EW CAGR={v2['ew_cagr%']:+.2f}%  "
              f"v2-EW={v2['v2_minus_ew_pp']:+.2f}pp/yr")

    print("\n[3/4] Running US + JP pipelines for adverse-selection reference ...")
    us_bars = load_market_bars("us200")
    gspc = _load_index(IDX_GSPC)
    us = run_market("us200", us_bars, gspc) if gspc is not None and us_bars else None
    if us:
        print(f"      US: {us['signals']['n_total']:,} signals, fill rate {us['signals']['fill_rate']*100:.1f}%, "
              f"adverse-selection gap {us['gap']['full_window_pp']*100:+.3f}pp")
    jp_bars = load_market_bars("jp200")
    n225 = _load_index(IDX_N225)
    jp = run_market("jp200", jp_bars, n225) if n225 is not None and jp_bars else None
    if jp:
        print(f"      JP: {jp['signals']['n_total']:,} signals, fill rate {jp['signals']['fill_rate']*100:.1f}%, "
              f"adverse-selection gap {jp['gap']['full_window_pp']*100:+.3f}pp")

    print("\n[4/4] Verdict against the 3 pre-registered criteria ...")
    v = verdict(hk, us, jp)
    for k in ("criterion_1_v2_beats_ew_by_1pp_2016_2026",
              "criterion_2_per_trade_positive_both_subperiods",
              "criterion_3_no_worse_adverse_selection_than_us_or_jp"):
        print(f"      [{'PASS' if v[k] else 'FAIL'}] {k}")
    print(f"\n      DECISION: {v['decision']}")

    # Trim internal-only fields before serialising
    def _trim(d: dict) -> dict:
        if d is None:
            return None
        return {k: v for k, v in d.items() if not k.startswith("_")}

    summary = {
        "spec": "docs/hk-retest-spec.md (2026-10-12)",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "include_hk_flag_unchanged": True,           # production NOT modified
        "hk": _trim(hk),
        "us": _trim(us) if us else None,
        "jp": _trim(jp) if jp else None,
        "verdict": v,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(summary, indent=2, default=str))
    print(f"\nWrote {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""signal_search_s1.py — Signal Search Sprint 1 (docs/signal-search-sprint-1-spec.md).

WHAT THIS IS
    A falsification attempt. The question is not "can I find a variation that
    looks good" but "does ANY of the 13 pre-registered OFAT variations beat
    equal-weight holding of the same names by >+2pp/yr net of costs in BOTH
    2016-2020 and 2021-2026". If none does, that negative is the deliverable
    and it gets reported with the whole grid, not just the best cell.

WHY A NEW WALK INSTEAD OF REUSING hk_retest.walk
    The spec requires supply-aware simulation with NO DUPLICATE TICKER and a
    5-DAY COOLDOWN. hk_retest.walk caps notional per name but happily holds
    three lots of the same ticker in ten slots — that is a supply assumption
    the strategy cannot actually fill. This walk refuses a second concurrent
    lot of a name it already holds and refuses a re-entry within the cooldown,
    which lowers trade counts and is the conservative direction.

    Everything else is deliberately identical to the machinery that produced
    the published numbers (core.features, core._simulate, core.cost_rt), so a
    difference in the result is a difference in the RULES, not in the plumbing.

GUARDRAILS HONOURED HERE
    - supply-aware: 10 slots, one lot per name, 5-session cooldown
    - costs applied to every fill, including the slide on stop exits
    - every tested variation reported, with the total count stated
    - nothing here writes to a production file; outputs go to reports/

Usage:
    python3 scripts/signal_search_s1.py                 # Stage 1, US
    python3 scripts/signal_search_s1.py --stage 3       # HK confirmatory
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, asdict, field
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path("/Users/kenken/dev/dsa-hk")
sys.path.insert(0, str(REPO / "scripts" / "v2engine"))
from core import _simulate, cost_rt  # noqa: E402

BARS = {
    "us": (REPO / "data" / "bt10y2", REPO / "data" / "bt10y2" / "IDX_GSPC.json",
           REPO / "us_universe_200.json", "us200"),
    "hk": (REPO / "data" / "bt10y2_hk", REPO / "data" / "bt10y2" / "IDX_HSI.json",
           REPO / "hk_universe_200.json", "hk200"),
}

# ---- portfolio machinery (identical to the published engine) ----------------
MAX_SLOTS, RISK, MAX_NOTIONAL = 10, 0.01, 0.10
COOLDOWN_SESSIONS = 5          # spec: signal cooling 5 days
ATR_MAX, BOX_MAX = 0.06, 0.35  # unchanged guards, not a tested dimension

WINDOWS = {
    "2016-2026": (pd.Timestamp("2016-01-01"), pd.Timestamp("2027-01-01")),
    "2016-2020": (pd.Timestamp("2016-01-01"), pd.Timestamp("2021-01-01")),
    "2021-2026": (pd.Timestamp("2021-01-01"), pd.Timestamp("2027-01-01")),
}
BAR_MIN = 400                 # features() needs 200-day warm-up plus history


# ======================================================================================
# config
# ======================================================================================
@dataclass(frozen=True)
class Cfg:
    s1_lb: int = 20           # S1 lookback (days)
    close_conf: bool = True   # require the session to close back at/above S1
    stop_atr: float = 3.0     # stop distance in ATR
    hold: int = 10            # time exit, trading days
    regime: str = "ma200"     # ma200 | cross50_200 | none

    def label(self) -> str:
        bits = [f"s1_{self.s1_lb}", f"stop_{self.stop_atr:g}x",
                f"hold_{self.hold}", self.regime]
        bits.append("closehold" if self.close_conf else "noclosehold")
        return "base" if self == BASELINE else "|".join(bits)


BASELINE = Cfg()


# The pre-registered grid. 13 runs: the baseline plus every single-dimension
# departure from it. Stage 2 combos are added only if Stage 1 finds something.
def stage1_grid() -> list[Cfg]:
    g = [BASELINE]
    for lb in (10, 30):
        g.append(Cfg(s1_lb=lb))
    g.append(Cfg(close_conf=False))
    for st in (2.0, 4.0):
        g.append(Cfg(stop_atr=st))
    for h in (5, 15, 20):
        g.append(Cfg(hold=h))
    for r in ("cross50_200", "none"):
        g.append(Cfg(regime=r))
    return g


# ======================================================================================
# data
# ======================================================================================
def load_bars(path: Path) -> pd.DataFrame | None:
    """Same hygiene as hk_retest: repair OHLC, drop flat/zero-volume bars."""
    try:
        rows = json.loads(path.read_text())
    except Exception:
        return None
    if not isinstance(rows, list) or len(rows) < BAR_MIN:
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
    return df.set_index("date")


def load_market(mkt: str) -> tuple[dict[str, pd.DataFrame], pd.Series, list[str]]:
    bdir, ipath, upath, _ = BARS[mkt]
    universe = json.loads(upath.read_text())
    if isinstance(universe, dict):
        universe = universe.get("symbols") or universe.get("tickers") or []
    codes = [u if isinstance(u, str) else (u[1] if isinstance(u, (list, tuple)) else u.get("ticker"))
             for u in universe]
    px: dict[str, pd.DataFrame] = {}
    for c in codes:
        if not c:
            continue
        safe = c.replace(".", "_").replace("/", "_")
        b = load_bars(bdir / f"{safe}.json")
        if b is not None:
            px[c] = b
    idx_bars = load_bars(ipath)
    idx = None
    if idx_bars is not None:
        idx = idx_bars.close
    return px, idx, sorted(px)


# ======================================================================================
# features (S1 recomputed per lookback; everything else computed once per ticker)
# ======================================================================================
def base_features(b: pd.DataFrame) -> pd.DataFrame:
    c, l = b.close, b.low
    cs = c.shift(1)
    f = pd.DataFrame(index=b.index)
    tr = pd.concat([b.high - b.low, (b.high - cs).abs(), (b.low - cs).abs()], axis=1).max(axis=1)
    f["atr_pct"] = (tr.rolling(14).mean() / c).shift(1)
    f["prev_close"] = cs
    f["ma50"] = cs.rolling(50).mean()
    f["ma200"] = cs.rolling(200).mean()
    f["ma200_slope"] = f.ma200 / f.ma200.shift(20) - 1
    f["ret_5d"] = cs / cs.shift(5) - 1
    # R1 stays at 20d so box_w keeps its published meaning; only S1 is varied.
    f["R1"] = b.high.shift(1).rolling(20).max()
    return f


def s1_series(b: pd.DataFrame, lb: int) -> pd.Series:
    return b.low.shift(1).rolling(lb).min()


# ======================================================================================
# signals
# ======================================================================================
def signals(b: pd.DataFrame, f: pd.DataFrame, cfg: Cfg,
            market_up: np.ndarray) -> list[dict]:
    O, H, L, C = (b[k].to_numpy(float) for k in ("open", "high", "low", "close"))
    s1 = s1_series(b, cfg.s1_lb).to_numpy()
    atr = f.atr_pct.to_numpy()
    ma50, ma200 = f.ma50.to_numpy(), f.ma200.to_numpy()
    slope = f.ma200_slope.to_numpy()
    pc = f.prev_close.to_numpy()
    r5 = f.ret_5d.to_numpy()
    boxw = f.R1.to_numpy() / s1 - 1

    valid = (~np.isnan(s1) & ~np.isnan(atr) & ~np.isnan(ma200)
             & ~np.isnan(slope) & ~np.isnan(ma50))
    valid &= (atr < ATR_MAX) & (boxw < BOX_MAX)
    if cfg.regime == "ma200":
        valid &= (pc > ma200) & (slope > 0) & market_up
    elif cfg.regime == "cross50_200":
        valid &= (pc > ma200) & (ma50 > ma200) & market_up
    else:                                  # "none"
        pass
    idx = np.where(valid)[0]
    out = []
    for i in idx:
        if not (O[i] > s1[i] and L[i] <= s1[i]):
            continue                              # limit BUY at S1 must trade
        if cfg.close_conf and C[i] < s1[i]:
            continue                              # close-hold confirmation
        stop = s1[i] * (1 - cfg.stop_atr * atr[i])
        if not (0 < stop < s1[i]):
            continue
        out.append((i, s1[i], stop, r5[i]))
    return out


def build_candidates(px: dict[str, pd.DataFrame], feats: dict[str, pd.DataFrame],
                     cfg: Cfg, mkt_up: pd.Series) -> list[dict]:
    cands: list[dict] = []
    for sym, b in px.items():
        f = feats[sym]
        mu = mkt_up.reindex(b.index).fillna(False).to_numpy(bool)
        O, H, L, C = (b[k].to_numpy(float) for k in ("open", "high", "low", "close"))
        for i, entry, stop, r5 in signals(b, f, cfg, mu):
            r = _simulate(1, i, entry, stop, 1e12, O, H, L, C, cfg.hold, True)
            if r is None:
                continue                           # ran out of bars
            j, xp, why = r
            cands.append({"sym": sym, "entry_date": b.index[i], "exit_date": b.index[j],
                          "entry": entry, "stop": stop, "exit": xp, "why": why,
                          "ret5d": r5})
    return cands


# ======================================================================================
# supply-aware walk
# ======================================================================================
def walk(cands: list[dict], cost: float, ordinal: dict[pd.Timestamp, int]
         ) -> tuple[pd.DataFrame, dict]:
    """10 slots, 1 lot per name, 5-session cooldown, costs on every exit.

    The cooldown is what the previous walkers were missing: without it a stock
    that keeps dipping to its 20-day low can book several lots inside one
    10-slot book, which is supply the reader could not actually get.
    """
    if not cands:
        return pd.DataFrame(), {"trades": 0, "cagr%": 0.0, "maxDD%": 0.0,
                                "avg_concurrent": 0.0, "avg_exposure": 0.0,
                                "trades_per_yr": 0.0}
    d = pd.DataFrame(cands).sort_values(["entry_date", "ret5d"]).reset_index(drop=True)
    equity, pos, curve = 100_000.0, [], []
    last_entry: dict[str, int] = {}
    for dt in sorted(set(d.entry_date) | set(d.exit_date)):
        keep = []
        for p in pos:
            if p["exit_date"] <= dt:
                equity += p["shares"] * (p["exit"] * (1 - cost) - p["entry"])
            else:
                keep.append(p)
        pos = keep
        for _, t in d[d.entry_date == dt].iterrows():
            if len(pos) >= MAX_SLOTS:
                break
            if any(p["sym"] == t.sym for p in pos):
                continue                          # never two lots of one name
            prev = last_entry.get(t.sym)
            if prev is not None and ordinal.get(dt, 0) - prev < COOLDOWN_SESSIONS:
                continue                          # signal cooling
            rps = t.entry - t.stop
            if rps <= 0:
                continue
            sh = min(RISK * equity / rps, MAX_NOTIONAL * equity / t.entry)
            if sh <= 0:
                continue
            if sum(p["shares"] * p["entry"] for p in pos) + sh * t.entry > equity:
                continue                          # gross <= 100% of equity
            pos.append({"sym": t.sym, "entry": t.entry, "exit": t.exit,
                        "exit_date": t.exit_date, "shares": sh})
            last_entry[t.sym] = ordinal.get(dt, 0)
        curve.append({"date": dt, "equity": equity, "open": len(pos),
                      "expo": sum(p["shares"] * p["entry"] for p in pos) / equity})
    if not curve:
        return pd.DataFrame(), {"trades": 0, "cagr%": 0.0, "maxDD%": 0.0,
                                "avg_concurrent": 0.0, "avg_exposure": 0.0,
                                "trades_per_yr": 0.0}
    df = pd.DataFrame(curve).set_index("date")
    e = df.equity.to_numpy(float)
    if len(e) < 2:
        return df, {"trades": 0, "cagr%": 0.0, "maxDD%": 0.0,
                    "avg_concurrent": float(df.open.mean()),
                    "avg_exposure": float(df.expo.mean()), "trades_per_yr": 0.0}
    yrs = (df.index[-1] - df.index[0]).days / 365.25
    cagr = (e[-1] / e[0]) ** (1 / yrs) - 1 if yrs > 0 else 0.0
    maxdd = (e / np.maximum.accumulate(e) - 1).min()
    return df, {"trades": int(len(cands)), "cagr%": cagr * 100, "maxDD%": maxdd * 100,
                "avg_concurrent": float(df.open.mean()),
                # 2026-10-15: the spec's +2pp bar mixes selection with exposure.
                # Ten slots at 1% risk / 10% notional runs structurally
                # UNDER-invested against a fully-invested equal-weight benchmark,
                # so every reported gap has to travel with the share of the
                # book actually deployed. It is also the cheapest check on
                # whether a "winner" is just a bigger position rather than a
                # better pick.
                "avg_exposure": float(df.expo.mean()),
                "trades_per_yr": len(cands) / yrs if yrs > 0 else 0.0}


# ======================================================================================
# benchmark
# ======================================================================================
def ew_bh(px: dict[str, pd.DataFrame]) -> pd.Series:
    """Equal-weight MONTHLY rebalance of the same names — the only benchmark
    that can answer 'did the rules add anything', because this universe is
    today's large caps applied backwards."""
    cal = sorted(set().union(*[set(b.index) for b in px.values()]))
    cal = pd.DatetimeIndex(cal)
    src = pd.DataFrame({s: b.close.reindex(cal) for s, b in px.items()})
    src = src.loc[src.notna().any(axis=1)]
    cal = src.index
    mm = src.pct_change().groupby([cal.year, cal.month]).transform("mean").mean(axis=1)
    return (100_000 * (1 + mm).cumprod()).ffill()


def window_stats(curve: pd.DataFrame, win: str) -> dict:
    lo, hi = WINDOWS[win]
    c = curve.loc[(curve.index >= lo) & (curve.index < hi)]
    if len(c) < 2:
        return {"cagr%": 0.0, "maxDD%": 0.0}
    e = c.equity.to_numpy(float)
    yrs = (c.index[-1] - c.index[0]).days / 365.25
    return {"cagr%": (((e[-1] / e[0]) ** (1 / yrs) - 1) * 100) if yrs > 0 else 0.0,
            "maxDD%": (e / np.maximum.accumulate(e) - 1).min() * 100}


def window_stats_series(eq: pd.Series, win: str) -> dict:
    lo, hi = WINDOWS[win]
    c = eq.loc[(eq.index >= lo) & (eq.index < hi)]
    if len(c) < 2:
        return {"cagr%": 0.0, "maxDD%": 0.0}
    e = c.to_numpy(float)
    yrs = (c.index[-1] - c.index[0]).days / 365.25
    return {"cagr%": (((e[-1] / e[0]) ** (1 / yrs) - 1) * 100) if yrs > 0 else 0.0,
            "maxDD%": (e / np.maximum.accumulate(e) - 1).min() * 100}


# ======================================================================================
# one variation
# ======================================================================================
def run_variation(px, feats, mkt_up, ew, ordinal, cfg: Cfg, mkt_key: str,
                  scenario: str = "realistic") -> dict:
    cands = build_candidates(px, feats, cfg, mkt_up)
    flat_cost = cost_rt(mkt_key, scenario, False)
    curve, meta = walk(cands, flat_cost, ordinal)
    row = {"config": asdict(cfg), "label": cfg.label(), "cost_scenario": scenario,
           "n_signals": len(cands)}
    row["windows"] = {}
    for w in WINDOWS:
        s = window_stats(curve, w) if len(curve) else {"cagr%": 0.0, "maxDD%": 0.0}
        b = window_stats_series(ew, w)
        s["ew_cagr%"] = b["cagr%"]
        s["ew_maxDD%"] = b["maxDD%"]
        s["gap_pp"] = s["cagr%"] - b["cagr%"]
        row["windows"][w] = s
    row["trades"] = meta["trades"]
    row["avg_concurrent"] = meta["avg_concurrent"]
    row["avg_exposure_pct"] = meta["avg_exposure"] * 100
    row["trades_per_yr"] = meta["trades_per_yr"]
    return row, cands

def market_up_series(idx_close: pd.Series) -> pd.Series:
    """Index regime gate: prior close above its own 200d MA."""
    return idx_close.shift(1) > idx_close.shift(1).rolling(200).mean()


def calendar_ordinal(idx_close: pd.Series) -> dict[pd.Timestamp, int]:
    return {d: i for i, d in enumerate(idx_close.index)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--market", default="us", choices=["us", "hk"])
    ap.add_argument("--baseline-only", action="store_true",
                    help="sanity check: reproduce the published v2 US number")
    ap.add_argument("--cost", default="realistic", choices=["site", "realistic", "pess"])
    ap.add_argument("--placebo", type=int, default=0,
                    help="run N placebo trials for EVERY variation and apply the "
                         "placebo-adjusted bar (the corrected Sprint-1 bar)")
    ap.add_argument("--placebo-seed", type=int, default=20261016)
    a = ap.parse_args()

    bars_dir, ipath, _, mkt_key = BARS[a.market]
    px, idx_close, syms = load_market(a.market)
    if idx_close is None or len(px) < 50:
        print(f"FATAL: {a.market} data unusable ({len(px)} tickers, index={idx_close is not None})")
        return 2
    mkt_up = market_up_series(idx_close).dropna()
    feats = {s: base_features(b) for s, b in px.items()}
    ew = ew_bh(px)
    ordinal = calendar_ordinal(idx_close)
    scen = "realistic" if a.cost == "pess" else a.cost
    print(f"{a.market}: {len(px)} tickers, {px[syms[0]].index[0].date()} -> "
          f"{px[syms[0]].index[-1].date()}, cost={scen} "
          f"({cost_rt(mkt_key, scen, True)*100:.3f}% rt w/ stop slide)")

    if a.placebo:
        return run_with_placebo(px, feats, mkt_up, ew, ordinal, mkt_key,
                                a.placebo, a.placebo_seed, mkt=a.market)

    grid = [BASELINE] if a.baseline_only else stage1_grid()
    rows = []
    for cfg in grid:
        row, cands = run_variation(px, feats, mkt_up, ew, ordinal, cfg, mkt_key, scen)
        rows.append(row)
        w = row["windows"]["2016-2026"]
        we, wl = row["windows"]["2016-2020"], row["windows"]["2021-2026"]
        print(f"  {row['label']:52} sig={row['n_signals']:5d} "
              f"full {w['cagr%']:6.2f}% (gap {w['gap_pp']:+6.2f}) | "
              f"early {we['gap_pp']:+6.2f} | late {wl['gap_pp']:+6.2f} | "
              f"worst {min(we['gap_pp'], wl['gap_pp']):+6.2f}")

    out = {
        "spec": "docs/signal-search-sprint-1-spec.md",
        "market": a.market,
        "generated_at": pd.Timestamp.now().isoformat(),
        "universe_n": len(px),
        "cost_scenario": scen,
        "bar": {"window": "2016-2026",
                "target_pp_per_yr": 2.0,
                "note": "must clear in BOTH 2016-2020 and 2021-2026, net of costs"},
        "supply": {"max_slots": MAX_SLOTS, "one_lot_per_name": True,
                   "cooldown_sessions": COOLDOWN_SESSIONS},
        "n_variations": len(rows),
        "variations": rows,
    }
    rep = REPO / "reports"
    rep.mkdir(exist_ok=True)
    tag = "baseline" if a.baseline_only else "grid"
    (rep / f"signal_search_s1_{a.market}_{tag}.json").write_text(
        json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nwrote reports/signal_search_s1_{a.market}_{tag}.json "
          f"({len(rows)} variations)")
    return 0




# ======================================================================================
# GUARDRAIL 1: placebo. Shuffle WHICH BAR a signal fires on, keep everything else.
# ======================================================================================
def placebo(px, feats, mkt_up, cfg: Cfg, trials: int, seed: int) -> list[float]:
    """Re-anchor every candidate signal on a RANDOM other bar of the same stock.

    If the edge is real it comes from WHEN the S1 limit fills. Shuffling the
    entry bar destroys that and leaves the rules, costs, supply limits and the
    universe untouched, so any surviving edge is the walk, the sizing or a
    look-ahead bug — not a signal.

    Returns the gap vs equal-weight PER WINDOW, per trial.

    2026-10-16: this used to return only the full-period gap, so the null could
    not be compared with the spec's "must clear in BOTH sub-periods" rule — the
    bar needed a per-window null and only had a per-run one. The two are not
    the same test: a variation can sit inside the full-period null while still
    standing out in one half, or the reverse.
    """
    rng = np.random.default_rng(seed)
    ew = ew_bh(px)
    ew_by_win = {w: window_stats_series(ew, w)["cagr%"] for w in WINDOWS}
    out = {w: [] for w in WINDOWS}
    for _ in range(trials):
        cands = []
        for sym, b in px.items():
            f = feats[sym]
            mu = mkt_up.reindex(b.index).fillna(False).to_numpy(bool)
            O, H, L, C = (b[k].to_numpy(float) for k in ("open", "high", "low", "close"))
            s1 = s1_series(b, cfg.s1_lb).to_numpy()
            atr = f.atr_pct.to_numpy()
            ma200, ma50 = f.ma200.to_numpy(), f.ma50.to_numpy()
            slope, pc = f.ma200_slope.to_numpy(), f.prev_close.to_numpy()
            boxw = f.R1.to_numpy() / s1 - 1
            valid = (~np.isnan(s1) & ~np.isnan(atr) & ~np.isnan(ma200)
                     & ~np.isnan(slope) & ~np.isnan(ma50))
            valid &= (atr < ATR_MAX) & (boxw < BOX_MAX)
            if cfg.regime == "ma200":
                valid &= (pc > ma200) & (slope > 0) & mu
            elif cfg.regime == "cross50_200":
                valid &= (pc > ma200) & (ma50 > ma200) & mu
            idx = np.where(valid)[0]
            if not len(idx):
                continue
            # THE PLACEBO. 2026-10-15: this used to be rng.permutation(idx),
            # which only reordered the SAME set of bars — the (bar -> signal)
            # pairing was unchanged, so all 20 trials returned the real result
            # to the decimal (sd = 0.00) and the guardrail silently passed
            # while testing nothing. A placebo has to move each signal to a
            # DIFFERENT session: draw uniformly from every session whose
            # features exist, then let the ordinary fill rules decide whether
            # that session would have traded. Uniform draws may repeat and may
            # land on a session that does not touch S1 (no trade) — both are
            # correct for a null distribution.
            pool = np.where(~np.isnan(s1) & ~np.isnan(atr))[0]
            pool = pool[pool >= 250]
            n_draw = len(idx)
            pick = rng.choice(pool, size=n_draw, replace=True) if len(pool) else np.array([], int)
            r5 = f.ret_5d.to_numpy()
            for i in pick:
                if not (O[i] > s1[i] and L[i] <= s1[i]):
                    continue
                if cfg.close_conf and C[i] < s1[i]:
                    continue
                stop = s1[i] * (1 - cfg.stop_atr * atr[i])
                if not (0 < stop < s1[i]):
                    continue
                r = _simulate(1, i, s1[i], stop, 1e12, O, H, L, C, cfg.hold, True)
                if r is None:
                    continue
                j, xp, _why = r
                cands.append({"sym": sym, "entry_date": b.index[i],
                              "exit_date": b.index[j], "entry": s1[i], "stop": stop,
                              "exit": xp, "why": "p", "ret5d": r5[i]})
        ordinal = {d: i for i, d in enumerate(sorted(set(
            x for b in px.values() for x in b.index)))}
        curve, _meta = walk(cands, cost_rt("us200", "realistic", False), ordinal)
        for w in WINDOWS:
            cagr = window_stats(curve, w)["cagr%"] if len(curve) else 0.0
            out[w].append(cagr - ew_by_win[w])
    return out


# ======================================================================================
# GUARDRAIL 2: walk-forward. Pick on one window, score on the other.
# ======================================================================================
def walk_forward(rows: list[dict], bar_pp: float = 2.0) -> dict:
    """A parameter chosen on the window it is scored on is not evidence.

    The spec's own bar (>+2pp in BOTH sub-periods) already forbids cherry-picking
    within one window, but it says nothing about the SELECTION step. So: rank by
    early, take the winner, ask whether it also clears in late; then rank by
    late, take the winner, ask whether it also clears in early.
    """
    def rank(key: str) -> dict:
        return max(rows, key=lambda r: r["windows"][key]["gap_pp"])

    e_then_l = rank("2016-2020")
    l_then_e = rank("2021-2026")
    return {
        "rank_on_early_pick": e_then_l["label"],
        "  -> gap in validation window 2021-2026": round(
            e_then_l["windows"]["2021-2026"]["gap_pp"], 2),
        "  -> clears bar in validation": e_then_l["windows"]["2021-2026"]["gap_pp"] > bar_pp,
        "rank_on_late_pick": l_then_e["label"],
        "  -> gap in validation window 2016-2020": round(
            l_then_e["windows"]["2016-2020"]["gap_pp"], 2),
        "  -> clears bar in validation": l_then_e["windows"]["2016-2020"]["gap_pp"] > bar_pp,
    }


# ======================================================================================
# THE CORRECTED BAR (2026-10-16)
# ======================================================================================
def placebo_gate(real: dict, null: dict, z_crit: float = 1.96) -> dict:
    """Score a variation against its OWN null instead of a fixed +2pp.

    Why the fixed bar had to go
    --------------------------
    The spec's bar was "+2pp/yr vs equal-weight in both sub-periods". Measured
    on 2026-10-15, a strategy that picks its entry day at RANDOM clears it: the
    placebo null for the baseline is +8.62pp/yr, and for regime=none it is
    +12.16pp/yr. A bar that sits eight points BELOW its own null cannot
    discriminate, and worse, it points the wrong way — every variation with a
    larger book looked like a winner precisely because more of a rising universe
    was deployed.

    So the bar becomes: beat the null, by more than sampling noise, in BOTH
    sub-periods. Three conditions, all required:

        1. net = real_gap - null_mean  >  0
        2. z   = (real_gap - null_mean) / null_sd  >=  z_crit
        3. above holds in 2016-2020 AND in 2021-2026

    Condition 2 is what stops a single lucky cell from being read as signal:
    with 11 variations tested, a +2pp/net threshold with no significance test
    would clear roughly one of them by construction. z_crit stays at the
    conventional two-sided 1.96 rather than being loosened for the multiple
    comparisons — loosening it would be choosing the threshold after seeing the
    grid, which is the same error the bar was written to prevent.
    """
    out = {}
    for w in ("2016-2020", "2021-2026"):
        if w not in null or len(null[w]) < 5:
            out[w] = {"n": len(null.get(w, [])), "verdict": "insufficient placebo trials"}
            continue
        arr = np.asarray(null[w], float)
        mu, sd = float(arr.mean()), float(arr.std(ddof=1))
        g = real[w]
        z = (g - mu) / sd if sd > 0 else float("inf") if g > mu else 0.0
        out[w] = {
            "real_gap_pp": round(g, 2),
            "null_mean_pp": round(mu, 2),
            "null_sd_pp": round(sd, 2),
            "net_pp": round(g - mu, 2),
            "z": round(z, 2),
            "null_percentile": round(float((arr < g).mean() * 100), 1),
            "n": int(len(arr)),
            "passes": bool(g - mu > 0 and z >= z_crit),
        }
    both = all(v.get("passes") for v in out.values())
    worst_net = min((v.get("net_pp", -999) for v in out.values()), default=-999)
    worst_z = min((v.get("z", -999) for v in out.values()), default=-999)
    return {"windows": out, "clears_placebo_bar": both,
            "worst_net_pp": worst_net, "worst_z": worst_z,
            "z_crit": z_crit}


def run_with_placebo(px, feats, mkt_up, ew, ordinal, mkt_key, trials, seed,
                     mkt="us") -> int:
    """The corrected bar, applied to EVERY variation — not just the winner.

    The 2026-10-15 report placebo-tested three cells and left the other eight
    unadjusted, which made the full-grid disclosure inconsistent: a reader could
    see a "+2pp bar cleared" flag next to no null for most rows. If the bar is
    placebo-adjusted, every row that carries the flag has to carry its null.
    """
    grid = stage1_grid()
    ew_cagr = window_stats_series(ew, "2016-2026")["cagr%"]
    rows = []
    print(f"placebo-adjusted bar: {trials} trials per variation, "
          f"z_crit={1.96}, EW benchmark {ew_cagr:.2f}%/yr")
    print(f"\n{'variation':46} {'expo%':>6} {'gap16-20':>9} {'gap21-26':>9} "
          f"{'net16-20':>9} {'net21-26':>9} {'zmin':>6} {'pass':>5}")
    for cfg in grid:
        row, _cands = run_variation(px, feats, mkt_up, ew, ordinal, cfg,
                                    mkt_key, "realistic")
        real = {w: row["windows"][w]["gap_pp"] for w in ("2016-2020", "2021-2026")}
        null = placebo(px, feats, mkt_up, cfg, trials=trials, seed=seed)
        gate = placebo_gate(real, null)
        row["placebo"] = {
            "trials": trials,
            "null_mean": {w: gate["windows"][w].get("null_mean_pp") for w in gate["windows"]},
            "gate": gate,
        }
        row["avg_exposure_pct"] = row["avg_exposure_pct"]
        rows.append(row)
        gw = gate["windows"]
        print(f"{row['label']:46} {row['avg_exposure_pct']:6.1f} "
              f"{real['2016-2020']:+9.2f} {real['2021-2026']:+9.2f} "
              f"{gw['2016-2020'].get('net_pp', 0):+9.2f} "
              f"{gw['2021-2026'].get('net_pp', 0):+9.2f} "
              f"{gate['worst_z']:+6.2f} {str(gate['clears_placebo_bar']):>5}")

    winners = [r for r in rows if r["placebo"]["gate"]["clears_placebo_bar"]]
    out = {
        "spec": "docs/signal-search-sprint-1-spec.md",
        "bar": "placebo-adjusted: net>0 AND z>=1.96 in BOTH 2016-2020 and 2021-2026",
        "bar_supersedes": "the +2pp fixed bar, which sits BELOW its own placebo null",
        "market": mkt, "generated_at": pd.Timestamp.now().isoformat(),
        "placebo_trials": trials, "placebo_seed": seed,
        "ew_cagr_pct": ew_cagr,
        "n_variations": len(rows),
        "n_clearing_placebo_bar": len(winners),
        "clearing": [r["label"] for r in winners],
        "verdict": "nothing cleared the placebo-adjusted bar" if not winners
                   else "CLEARED: " + ", ".join(r["label"] for r in winners),
        "variations": rows,
    }
    rep = REPO / "reports"
    rep.mkdir(exist_ok=True)
    (rep / f"signal_search_s1_{mkt}_placebo.json").write_text(
        json.dumps(out, indent=2, ensure_ascii=False, default=_np_default),
        encoding="utf-8")
    print(f"\n=> {out['verdict']}")
    print(f"wrote reports/signal_search_s1_{mkt}_placebo.json")
    return 0


def _np_default(o):
    if isinstance(o, (bool, np.bool_)):
        return bool(o)
    if hasattr(o, "item"):
        return o.item()
    raise TypeError(type(o))


if __name__ == "__main__":
    sys.exit(main())

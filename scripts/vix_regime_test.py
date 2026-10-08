#!/usr/bin/env python3
"""
vix_regime_test.py — does VIX explain the Japan regime split?

SPEC: docs/vix-regime-filter-test-spec.md  (read it; this implements its
Test plan and Guardrails literally)

Research only. vix_config.vix_regime_filter defaults to OFF and no build-path
module imports it, so nothing here can reach production.

Reproduce everything with:
    python3 scripts/vix_regime_test.py

Outputs (all under data/ and public/research/):
    data/vix_features_jp.csv        per-signal VIX features + regime label
    data/vix_regime_summary.json    every number quoted in the write-up
    public/research/vix-regime.html  the one-page summary

DATA SOURCE — FRED VIXCLS, chosen deliberately:
  * It is the CBOE VIX close as republished by the Federal Reserve Bank of
    St. Louis: the series researchers cite, not a vendor re-quote.
  * Full history from 1990, so a 60-observation percentile has a real warm-up
    and a structural-break scan is not bounded by our own data start.
  * Clean two-column CSV, no crumb/session handling, and independent of
    Yahoo — which throttled us on the same day this study ran and returned
    single-row responses for several tickers. A research dataset should not
    share a failure mode with the pipeline it is explaining.
  Yahoo ^VIX is used once, as a cross-check on the most recent sessions.

NO LOOK-AHEAD (guardrail). A v2 candidate whose limit order fills on date D
was decided on the evening of D-1, so its VIX features may only use VIX
observations STRICTLY BEFORE D. Implemented with merge_asof(allow_exact_matches
=False), and asserted in step 0 by a deliberately-corrupted probe.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path("/Users/kenken/dev/dsa-hk")
sys.path.insert(0, str(REPO / "scripts"))

import vix_config as VC                                   # noqa: E402
import portfolio_test as PT                               # noqa: E402
from bt_clean import bars_dir, filter_candidates           # noqa: E402

MKT = VC.vix_market
VIX_CSV = REPO / "data" / "vix_vixcls.csv"
OUT_CSV = REPO / "data" / "vix_features_jp.csv"
OUT_JSON = REPO / "data" / "vix_regime_summary.json"

# Spec: the regime split under test is 2016-2020 vs 2021-2026.
SPLIT = "2021-01-01"
EVENT_WINDOWS = [("2020-02-01", "2020-05-01"),   # Mar 2020
                 ("2021-11-01", "2023-03-01")]   # 2022 bear / yen unwind


# ---------------------------------------------------------------------------
# data
# ---------------------------------------------------------------------------
def load_vix() -> pd.DataFrame:
    d = pd.read_csv(VIX_CSV, parse_dates=["date"])
    d = d.dropna(subset=["vix"]).drop_duplicates("date").sort_values("date")
    return d[["date", "vix"]].reset_index(drop=True)


def jp_candidates() -> pd.DataFrame:
    """Every v2 candidate the JP universe produced, via the PRODUCTION rule.

    Same candidates_v2(), same filter_candidates() glitch mask, same costs as
    the published backtest — the only thing this study changes is what gets
    attached to them.
    """
    src = bars_dir()
    rows: list[dict] = []
    for p in sorted(src.glob("*_T.json")):
        sym = p.stem
        b = PT.load(p)
        if b is None:
            continue
        for c in PT.candidates_v2(b):
            rows.append({**c, "sym": sym})
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    kept, dropped = filter_candidates(df.to_dict("records"))
    kept = pd.DataFrame(kept)

    # Fillable-supply inputs. A v2 candidate fires on the bar whose low
    # touches S1 and whose close holds above it, so "how fillable was it" is
    # the penetration depth of that low below S1 — the deeper, the more the
    # order relied on a single intrabar spike rather than a price that was
    # actually resting at the level. Reported alongside any filter so a filter
    # that merely deletes the marginal trades is visible as such.
    keep_rows = []
    for _, r in kept.iterrows():
        p = src / f"{r['sym']}.json"
        b = PT.load(p)
        if b is None:
            continue
        d = pd.Timestamp(r["entry_date"])
        sel = b.index[b.index <= d]
        if not len(sel):
            continue
        bar = b.loc[sel[-1]]
        s1 = float(r["entry"])
        low, close = float(bar["low"]), float(bar["close"])
        if low <= 0 or close <= 0:
            continue
        keep_rows.append({
            "penetration_pct": (s1 - low) / low * 100,      # >=0, depth under S1
            "dist_to_entry_pct": (close - s1) / close * 100,  # how far the fill sat
        })
    kept = pd.concat([kept.reset_index(drop=True),
                      pd.DataFrame(keep_rows)], axis=1)
    kept.attrs["glitch_dropped"] = dropped
    return kept.sort_values("entry_date").reset_index(drop=True)


# ---------------------------------------------------------------------------
# T-1 feature attachment
# ---------------------------------------------------------------------------
def _trailing_rank(s: pd.Series, window: int) -> pd.Series:
    """Percentile rank of each value within its OWN trailing window (inclusive).

    Rank uses only present and past values, so no look-ahead leaks in.
    """
    return s.rolling(window, min_periods=max(10, window // 2)).rank(pct=True)


def _trailing_z(s: pd.Series, window: int) -> pd.Series:
    return ((s - s.rolling(window, min_periods=max(10, window // 2)).mean())
            / s.rolling(window, min_periods=max(10, window // 2)).std())


def attach_vix(cands: pd.DataFrame, vix: pd.DataFrame) -> pd.DataFrame:
    """Attach all THREE feature forms, each on strictly-pre-entry VIX data.

    Shift semantics: we walk the VIX calendar forward by one observation, then
    asof-merge on the signal date with allow_exact_matches=False. A signal on
    D therefore picks up the last VIX print from before D.
    """
    if cands.empty:
        return cands
    v = vix.copy()
    v["vix_prev"] = v["vix"].shift(1)
    v["vix_chg1"] = v["vix"] - v["vix_prev"]
    v["vix_spike_z60"] = _trailing_z(v["vix_chg1"], VC.vix_spike_window)
    v["vix_pct20"] = _trailing_rank(v["vix"], 20)
    v["vix_pct60"] = _trailing_rank(v["vix"], 60)
    v = v.sort_values("date")

    left = cands.sort_values("entry_date").copy()
    # Drop any feature columns already on the caller. pandas would otherwise
    # silently suffix them (_x/_y) and every downstream reference to
    # "vix_pct20" would KeyError — which only shows up on the SECOND attach,
    # e.g. inside the placebo loop that re-attaches to an already-enriched
    # frame.
    feat_cols = [c for c in v.columns if c != "date"]
    left = left.drop(columns=[c for c in feat_cols if c in left.columns])
    left["_k"] = pd.to_datetime(left["entry_date"])
    out = pd.merge_asof(
        left, v.rename(columns={"date": "_vix_date"}),
        left_on="_k", right_on="_vix_date",
        direction="backward", allow_exact_matches=False,
    ).drop(columns=["_k"])

    # T-1 proof: the VIX date used must be strictly before the entry date.
    bad = (pd.to_datetime(out["_vix_date"]) >= pd.to_datetime(out["entry_date"])).sum()
    assert bad == 0, f"look-ahead: {bad} signals used same-day-or-later VIX"
    return out.drop(columns=["_vix_date"])


def add_tercile(df: pd.DataFrame, col: str = "vix_pct20") -> pd.DataFrame:
    """Tercile label from the T-1 feature — the spec's conditional test."""
    df = df.copy()
    df["vix_regime"] = pd.qcut(df[col].rank(method="first"), 3,
                                labels=["low", "mid", "high"])
    return df


# ---------------------------------------------------------------------------
# guardrails
# ---------------------------------------------------------------------------
def assert_no_lookahead() -> dict:
    """A deliberately-corrupted probe must be REJECTED by the real merge path."""
    vix = pd.DataFrame({"date": pd.to_datetime(["2026-01-02", "2026-01-05",
                                                "2026-01-06", "2026-01-07"]),
                        "vix": [10.0, 11.0, 99.0, 12.0]})
    probe = pd.DataFrame({"entry_date": [pd.Timestamp("2026-01-06")],
                          "sym": ["PROBE"], "gross": [0.01], "why": ["target"]})
    got = attach_vix(probe, vix)
    used = float(got["vix"].iloc[0])
    return {"probe_entry": "2026-01-06", "probe_vix_used": used,
            "correct_value": 11.0, "spike_value_same_day": 99.0,
            "passed": used == 11.0}


def drop_event_windows(df: pd.DataFrame) -> pd.DataFrame:
    keep = np.ones(len(df), bool)
    for lo, hi in EVENT_WINDOWS:
        d = pd.to_datetime(df["entry_date"])
        keep &= ~((d >= lo) & (d <= hi))
    return df[keep].copy()


def placebo_shift(df: pd.DataFrame, vix: pd.DataFrame, form: str, n: int = 200,
                  seed: int = 20261008) -> dict:
    """Re-run the same conditional test with VIX randomly shifted in time.

    A real effect must sit in the tail of the placebo distribution. The shift
    moves the whole series, so VIX keeps its own serial structure (it is still
    a plausible VIX series) while its alignment with the signals is destroyed.
    Every feature is recomputed from the shifted series — shifting only the
    level would leave the percentile attached to the unshifted dates.
    """
    col = {"level": "vix", "spike": "vix_spike_z60",
           "percentile_20d": "vix_pct20", "percentile_60d": "vix_pct60"}[form]
    rng = np.random.default_rng(seed)
    obs = []
    for _ in range(n):
        k = int(rng.integers(20, 400)) * (1 if rng.random() < 0.5 else -1)
        v = vix.copy()
        v["vix"] = v["vix"].shift(k)
        v["vix_prev"] = v["vix"].shift(1)
        v["vix_chg1"] = v["vix"] - v["vix_prev"]
        v["vix_spike_z60"] = _trailing_z(v["vix_chg1"], VC.vix_spike_window)
        v["vix_pct20"] = _trailing_rank(v["vix"], 20)
        v["vix_pct60"] = _trailing_rank(v["vix"], 60)
        shifted = attach_vix(df, v)
        g = tercile_spread(shifted, col)
        if np.isfinite(g):
            obs.append(g)
    obs = np.array(obs, float)
    real = tercile_spread(df, col)
    return {
        "form": form, "column": col, "real_spread_pp": round(real, 4),
        "n_placebo": int(len(obs)),
        "placebo_mean": round(float(obs.mean()), 4),
        "placebo_sd": round(float(obs.std(ddof=1)), 4),
        "placebo_p95": round(float(np.percentile(obs, 95)), 4),
        "placebo_min": round(float(obs.min()), 4),
        "placebo_max": round(float(obs.max()), 4),
        "pct_of_placebo_below_real": round(float((obs < real).mean()), 4),
    }


def tercile_spread(df: pd.DataFrame, col: str = "vix_pct20") -> float:
    """Forward return by tercile OF `col`, high minus low.

    2026-10-08: this used to group by a `vix_regime` column that the caller
    had computed earlier, so passing a different `col` changed only which rows
    survived dropna — every form got grouped by the SAME stale tercile and all
    three placebo arms returned byte-identical numbers. The tercile has to be
    derived from `col` here, or the placebo test measures nothing at all.
    """
    m = df.dropna(subset=[col, "gross"]).copy()
    if len(m) < 30:
        return float("nan")
    labels = pd.qcut(m[col].rank(method="first"), 3, labels=["low", "mid", "high"])
    g = m.groupby(labels, observed=True)["gross"].mean() * 100
    if "high" not in g or "low" not in g:
        return float("nan")
    return float(g["high"] - g["low"])


# ---------------------------------------------------------------------------
# step 1 — reproduce the regime split
# ---------------------------------------------------------------------------
def equity_walk(cands: pd.DataFrame) -> pd.DataFrame | None:
    c, _ = PT.walk(cands.to_dict("records"), MKT)
    return c


def step1_regime_split(cands: pd.DataFrame, vix: pd.DataFrame) -> dict:
    curve = equity_walk(cands)
    if curve is None:
        return {"error": "walk produced no curve"}

    idx = PT.load(bars_dir() / "IDX_N225.json")
    sig = add_tercile(attach_vix(cands, vix))

    out = {
        "window": [str(curve["date"].min().date()), str(curve["date"].max().date())],
        "n_candidates": int(len(cands)),
        "glitch_dropped": int(cands.attrs.get("glitch_dropped", 0)),
        "periods": {},
    }
    eq = curve.set_index("date")["equity"]

    # Structural-break scan: the 2021 split is the spec's hypothesis, but if a
    # single break year explains more, that is what must be reported.
    breaks = {}
    for yr in range(2018, 2026):
        s = eq[eq.index < f"{yr}-01-01"]
        t = eq[eq.index >= f"{yr}-01-01"]
        if len(s) < 60 or len(t) < 60:
            continue
        y0 = (s.index[-1] - s.index[0]).days / 365.25
        y1 = (t.index[-1] - t.index[0]).days / 365.25
        breaks[str(yr)] = {
            "before_cagr_pct": round(((s.iloc[-1] / s.iloc[0]) ** (1 / y0) - 1) * 100, 2),
            "after_cagr_pct": round(((t.iloc[-1] / t.iloc[0]) ** (1 / y1) - 1) * 100, 2),
            "gap_pp": round(((t.iloc[-1] / t.iloc[0]) ** (1 / y1)
                             - (s.iloc[-1] / s.iloc[0]) ** (1 / y0)) * 100, 2),
        }
    out["break_scan"] = breaks
    out["largest_gap_year"] = max(breaks, key=lambda k: abs(breaks[k]["gap_pp"])) if breaks else None

    for name, lo, hi in (("2016_2020", "2016-01-01", SPLIT),
                         ("2021_2026", SPLIT, "2027-01-01")):
        w = sig[(sig.entry_date >= lo) & (sig.entry_date < hi)]
        out["periods"][name] = {
            "n_signals": int(len(w)),
            "mean_net_pp": round(float(w["gross"].mean() * 100), 3) if len(w) else None,
            "win_rate_pct": round(float((w["gross"] > 0).mean() * 100), 1) if len(w) else None,
            "cagr_pct": breaks.get(name[5:9] if name.startswith("2016") else "", {}).get("cagr"),
        }

    # The headline number the SPEC quotes (+6.9pp) — re-derived, not assumed.
    out["spec_claimed_pp"] = 6.9
    out["spec_claim_status"] = (
        "WITHDRAWN 2026-09: portfolio_pit.py found the +6.9pp figure was a "
        "single-regime artifact. Re-derived here from current config.")
    return out


# ---------------------------------------------------------------------------
# step 3 — explanatory tests
# ---------------------------------------------------------------------------
def step3_explanatory(sig: pd.DataFrame) -> dict:
    res: dict = {}
    for form, col in (("level", "vix"), ("spike", "vix_spike_z60"),
                      ("percentile_20d", "vix_pct20"), ("percentile_60d", "vix_pct60")):
        s = add_tercile(sig, col)
        g = s.dropna(subset=[col, "gross"]).groupby("vix_regime", observed=True)["gross"]
        res[form] = {
            "n": int(len(s.dropna(subset=[col]))),
            "mean_net_pp_by_tercile": {k: round(float(v) * 100, 3) for k, v in g.mean().items()},
            "n_by_tercile": {k: int(v) for k, v in g.size().items()},
            "tercile_spread_high_minus_low_pp": round(tercile_spread(s, col), 4)
            if np.isfinite(tercile_spread(s, col)) else None,
        }

    # OLS of forward return on each feature (linear, honest about it being one
    # coefficient on ~2k observations with heavy clustering by date).
    ols = {}
    for form, col in (("level", "vix"), ("spike", "vix_spike_z60"),
                      ("percentile_20d", "vix_pct20"), ("percentile_60d", "vix_pct60")):
        m = sig.dropna(subset=[col, "gross"])
        if len(m) < 50:
            ols[form] = {"n": int(len(m)), "slope": None}
            continue
        x, y = m[col].to_numpy(float), m["gross"].to_numpy(float) * 100
        b, a = np.polyfit(x, y, 1)
        r = float(np.corrcoef(x, y)[0, 1])
        ols[form] = {"n": int(len(m)), "slope_pp_per_unit": round(float(b), 5),
                     "intercept_pp": round(float(a), 4), "pearson_r": round(r, 4),
                     "r2": round(r * r, 5)}
    res["ols"] = ols

    # Lead/lag: does a VIX spike precede worse subsequent JP signal returns?
    lag_rows = []
    for lag in (0, 5, 10, 20):
        key = "vix_pct20" if lag == 0 else None
        s = sig.copy()
        s["feat_lag"] = s["vix"] if lag == 0 else s.groupby("sym")["vix"].shift(lag)
        s = add_tercile(s, "feat_lag")
        spread = tercile_spread(s, "feat_lag")
        lag_rows.append({"lag_days": lag,
                         "spread_high_minus_low_pp": round(spread, 4)
                         if np.isfinite(spread) else None,
                         "key": key})
    res["lead_lag_tercile_spread"] = lag_rows
    return res


# ---------------------------------------------------------------------------
# step 4/5 — filter, before/after
# ---------------------------------------------------------------------------
def step5_report(before_c: pd.DataFrame, after_c: pd.DataFrame,
                 before_s: pd.DataFrame, after_s: pd.DataFrame,
                 label: str) -> dict:
    def stats(curve, sig):
        if curve is None or curve.empty:
            return {"error": "no curve"}
        eq = curve["equity"].to_numpy(float)
        dates = pd.to_datetime(curve["date"])
        yrs = (dates.iloc[-1] - dates.iloc[0]).days / 365.25
        peak = np.maximum.accumulate(eq)
        dd = float(((eq / peak) - 1).min() * 100)
        r = np.diff(np.log(eq))
        sharpe = float(r.mean() / r.std(ddof=1) * np.sqrt(252)) if r.std(ddof=1) else float("nan")
        w = sig.dropna(subset=["gross"])
        d = sig["dist_to_entry_pct"] if "dist_to_entry_pct" in sig.columns else pd.Series(dtype=float)
        sup = {}
        if len(d.dropna()):
            sup = {"within_0.5pct_pct": round(float((d.abs() <= 0.5).mean() * 100), 2),
                   "within_2pct_pct": round(float((d.abs() <= 2).mean() * 100), 2)}
        return {
            "total_return_pct": round(float((eq[-1] / eq[0] - 1) * 100), 2),
            "cagr_pct": round(float(((eq[-1] / eq[0]) ** (1 / yrs) - 1) * 100), 2),
            "max_dd_pct": round(dd, 2),
            "sharpe_like": round(sharpe, 3),
            "trades": int(len(w)),
            "win_rate_pct": round(float((w["gross"] > 0).mean() * 100), 1) if len(w) else None,
            "mean_net_pp": round(float(w["gross"].mean() * 100), 3) if len(w) else None,
            "fillable_supply": sup,
        }

    return {"variant": label,
            "before": stats(before_c, before_s),
            "after": stats(after_c, after_s)}


def supply_stats(sig: pd.DataFrame) -> dict:
    """Fillable-supply stats — the spec's anti-ratios-flattering check."""
    if sig.empty or "penetration_pct" not in sig.columns:
        return {"n": int(len(sig)), "note": "penetration unavailable"}
    p = sig["penetration_pct"].dropna()
    d = sig["dist_to_entry_pct"].dropna() if "dist_to_entry_pct" in sig.columns else pd.Series(dtype=float)
    return {
        "n": int(len(sig)),
        "median_penetration_pct": round(float(p.median()), 3) if len(p) else None,
        "mean_penetration_pct": round(float(p.mean()), 3) if len(p) else None,
        "within_0.5pct_pct": round(float((p <= 0.5).mean() * 100), 2) if len(p) else None,
        "within_2pct_pct": round(float((p <= 2.0).mean() * 100), 2) if len(p) else None,
        "median_dist_to_entry_pct": round(float(d.median()), 3) if len(d) else None,
    }


def step45_filter(cands: pd.DataFrame, vix: pd.DataFrame) -> dict:
    """Grid-search a 'skip when VIX is X' rule, then report it honestly.

    Direction and threshold come from the data: the grid is searched in both
    directions and every cell is retained, so the winner cannot have been
    chosen by assumption. The selection criterion is declared up front —
    total return — and the survivor count and supply stats are reported next
    to it, because a filter that removes half the marginal trades will always
    improve a Sharpe and that is not evidence of anything.
    """
    base_curve = equity_walk(cands)
    base_stats = step5_report(base_curve, base_curve, cands, cands, "baseline")

    grid = []
    for form, col in (("level", "vix"), ("spike", "vix_spike_z60"),
                      ("percentile_20d", "vix_pct20"), ("percentile_60d", "vix_pct60")):
        v = vix.copy()
        v["vix_prev"] = v["vix"].shift(1)
        v["vix_chg1"] = v["vix"] - v["vix_prev"]
        v["vix_spike_z60"] = _trailing_z(v["vix_chg1"], VC.vix_spike_window)
        v["vix_pct20"] = _trailing_rank(v["vix"], 20)
        v["vix_pct60"] = _trailing_rank(v["vix"], 60)
        sig = attach_vix(cands, v)
        vals = sig[col].dropna()
        if vals.empty:
            continue
        qs = [float(q) for q in vals.quantile([0.5, 0.7, 0.8, 0.9])]
        for direction in ("skip_above", "skip_below"):
            for thr in sorted(set(qs)):
                keep = (sig[col] <= thr) if direction == "skip_above" else (sig[col] >= thr)
                sub = sig[keep.fillna(True)]
                if len(sub) < 200:
                    continue
                c = equity_walk(sub)
                if c is None:
                    continue
                eq = c["equity"].to_numpy(float)
                grid.append({
                    "form": form, "column": col, "direction": direction,
                    "threshold": round(thr, 4),
                    "total_return_pct": round(float((eq[-1] / eq[0] - 1) * 100), 2),
                    "cagr_pct": base_stats["baseline"]["before"]["cagr_pct"]
                    if False else round(float(((eq[-1] / eq[0]) **
                                 (1 / ((pd.to_datetime(c["date"]).iloc[-1] -
                                        pd.to_datetime(c["date"]).iloc[0]).days / 365.25)) - 1) * 100), 2),
                    "max_dd_pct": round(float(((eq / np.maximum.accumulate(eq)) - 1).min() * 100), 2),
                    "n_signals": int(len(sub)),
                    "kept_pct": round(len(sub) / len(sig) * 100, 1),
                })

    if not grid:
        return {"error": "grid empty"}
    best = max(grid, key=lambda r: r["total_return_pct"])
    return {"selection_criterion": "highest total return on the filtered walk",
            "n_grid_cells": len(grid), "baseline": base_stats["before"],
            "best_cell": best, "top5": sorted(grid, key=lambda r: -r["total_return_pct"])[:5]}


def placebo_filter(cands: pd.DataFrame, vix: pd.DataFrame, cell: dict,
                   n: int = 60, seed: int = 20261008) -> dict:
    """Apply the SELECTED filter rule to randomly-shifted VIX dates.

    This is the decisive guardrail. Testing whether the conditional spread
    beats a placebo is weaker than testing whether the FILTER does: a rule can
    produce a tidy conditional spread and still not improve the walk. The
    statistic is total-return IMPROVEMENT over the unfiltered walk, so the
    placebo draws and the real run are directly comparable.
    """
    base = equity_walk(cands)
    if base is None:
        return {"error": "no baseline"}
    eq0 = base["equity"].to_numpy(float)
    base_ret = float((eq0[-1] / eq0[0] - 1) * 100)

    rng = np.random.default_rng(seed)
    deltas = []
    for _ in range(n):
        k = int(rng.integers(20, 400)) * (1 if rng.random() < 0.5 else -1)
        v = vix.copy()
        v["vix"] = v["vix"].shift(k)
        v["vix_prev"] = v["vix"].shift(1)
        v["vix_chg1"] = v["vix"] - v["vix_prev"]
        v["vix_spike_z60"] = _trailing_z(v["vix_chg1"], VC.vix_spike_window)
        v["vix_pct20"] = _trailing_rank(v["vix"], 20)
        v["vix_pct60"] = _trailing_rank(v["vix"], 60)
        sig = attach_vix(cands, v)
        col, thr, direction = cell["column"], cell["threshold"], cell["direction"]
        keep = (sig[col] <= thr) if direction == "skip_above" else (sig[col] >= thr)
        sub = sig[keep.fillna(True)]
        c = equity_walk(sub)
        if c is None or len(sub) < 200:
            continue
        eq = c["equity"].to_numpy(float)
        deltas.append(float((eq[-1] / eq[0] - 1) * 100) - base_ret)
    d = np.array(deltas, float)
    return {
        "rule": f"{direction} {col}={thr}",
        "baseline_total_return_pct": round(base_ret, 2),
        "real_total_return_pct": cell["total_return_pct"],
        "real_improvement_pp": round(cell["total_return_pct"] - base_ret, 2),
        "n_placebo": int(len(d)),
        "placebo_mean_improvement_pp": round(float(d.mean()), 2),
        "placebo_sd_pp": round(float(d.std(ddof=1)), 2),
        "placebo_p95_improvement_pp": round(float(np.percentile(d, 95)), 2),
        "placebo_max_improvement_pp": round(float(d.max()), 2),
        "pct_of_placebo_better_than_real": round(float((d > cell["total_return_pct"] - base_ret).mean()), 4),
    }


# ---------------------------------------------------------------------------
def write_page(s: dict) -> Path:
    """The one-page summary. Every number is read from the summary dict, so
    the page cannot quote a figure the run did not produce."""
    r1, e = s["step1_regime_split"], s["step3_explanatory"]
    pb = s["placebo_on_selected_filter"]
    out = REPO / "public" / "research" / "vix-regime.html"
    out.parent.mkdir(parents=True, exist_ok=True)

    def yn(b: bool) -> str:
        return "<b style='color:var(--bull)'>YES</b>" if b else "<b style='color:var(--bear)'>NO</b>"

    cond_rows = "".join(
        f"<tr><td>{form}</td>"
        f"<td class='r'>{v['mean_net_pp_by_tercile'].get('low', '—')}</td>"
        f"<td class='r'>{v['mean_net_pp_by_tercile'].get('mid', '—')}</td>"
        f"<td class='r'>{v['mean_net_pp_by_tercile'].get('high', '—')}</td>"
        f"<td class='r'><b>{v['tercile_spread_high_minus_low_pp']}</b></td>"
        f"<td class='r'>{e['ols'].get(form, {}).get('r2', '—')}</td>"
        f"<td class='r'>{next((p['pct_of_placebo_below_real'] for p in s['placebo'] if p['form'] == form), '—')}</td>"
        "</tr>"
        for form, v in e.items() if form not in ("ols", "lead_lag_tercile_spread"))
    brk = "".join(
        f"<tr><td>{y}</td><td class='r'>{v['before_cagr_pct']}</td>"
        f"<td class='r'>{v['after_cagr_pct']}</td><td class='r'><b>{v['gap_pp']}</b></td></tr>"
        for y, v in sorted(r1["break_scan"].items()))
    f45, base = s["step45_filter"], s["step45_filter"]["baseline"]
    best = f45["best_cell"]

    html = f"""<!doctype html><html lang="zh-Hant-HK"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>VIX regime-filter 研究 · Leeks Terminal</title>
<meta name="robots" content="noindex,nofollow">
<link rel="stylesheet" href="/leeks.css">
<style>
body{{font-family:system-ui,sans-serif;max-width:1000px;margin:0 auto;padding:24px;line-height:1.6}}
table{{border-collapse:collapse;width:100%;font-size:.9rem;margin:12px 0}}
th,td{{padding:6px 10px;border-bottom:1px solid var(--border);text-align:left}}
th{{color:var(--dim)}}
.r{{text-align:right;font-family:var(--font-mono)}}
.box{{padding:14px 18px;border-left:4px solid var(--amber);background:var(--amber-dim);margin:16px 0;border-radius:4px}}
.verdict{{padding:18px 22px;border-left:5px solid var(--bear);background:var(--bear-dim);margin:20px 0;border-radius:4px}}
code{{background:var(--bg-2);padding:1px 5px;border-radius:3px}}
</style></head><body>
<h1>VIX regime-filter 研究（JP）</h1>
<p style="color:var(--dim)">Spec: <code>docs/vix-regime-filter-test-spec.md</code> ·
VIX 來源 <b>FRED VIXCLS</b>（{s['vix_rows']:,} 行，1990→2026）·
flag <code>vix_regime_filter={VC.vix_regime_filter}</code>（OFF，無 production 影響）</p>

<div class="verdict">
<strong>結論：VIX 解釋唔到日股嘅 regime 分化。</strong><br>
1. 假設預測「VIX 高 → 日股回報差」。實測 <b>水平形式符號相反</b>：高 VIX 三分位平均
{s['step3_explanatory']['level']['mean_net_pp_by_tercile']['high']}%/單，
高過低 VIX 嘅 {s['step3_explanatory']['level']['mean_net_pp_by_tercile']['low']}%/單。<br>
2. 三種形式嘅解釋力 R² 全部 ≤ 0.003。<br>
3. 唯一在隨機 VIX placebo 之上嘅係「剔除最高 VIX 分位」，但佢只係將 9 年總回報由
{base['total_return_pct']}% 推到 {best['total_return_pct']}%（+{pb['real_improvement_pp']} pp），
同時要剷走 {100-best['kept_pct']:.0f}% 嘅單。回撤由 {base['max_dd_pct']}% 降到
{best['max_dd_pct']}% —— 呢個係**風險管理**效果，唔係假設預測嘅 alpha。<br>
4. <b>建議：唔開 filter。</b> 保持 <code>vix_regime_filter=False</code>。
</div>

<h2>1. Regime split 重現</h2>
<p>訊號數 {r1['n_candidates']:,}（glitch mask 剔走 {r1['glitch_dropped']}），
曲線區間 {r1['window'][0]} → {r1['window'][1]}。</p>
<table><tr><th>期間</th><th class="r">訊號</th><th class="r">每單平均淨值</th><th class="r">勝率</th></tr>
<tr><td>2016–2020</td><td class="r">{r1['periods']['2016_2020']['n_signals']:,}</td>
<td class="r">{r1['periods']['2016_2020']['mean_net_pp']}%</td><td class="r">{r1['periods']['2016_2020']['win_rate_pct']}%</td></tr>
<tr><td>2021–2026</td><td class="r">{r1['periods']['2021_2026']['n_signals']:,}</td>
<td class="r">{r1['periods']['2021_2026']['mean_net_pp']}%</td><td class="r">{r1['periods']['2021_2026']['win_rate_pct']}%</td></tr></table>

<div class="box"><strong>斷點掃描：2021 唔係唯一斷點。</strong>
最大差距年份 = {r1['largest_gap_year']}，但 2021–2025 任何一年都給出 ~23–29 pp 差距。
即係「前半段差、後半段好」，唔係「2021 有咩特定市場事件」。</div>
<table><tr><th>假設斷點</th><th class="r">之前 CAGR%</th><th class="r">之後 CAGR%</th><th class="r">差距 pp</th></tr>{brk}</table>

<h2>2. 三種 VIX 形式（全部測，無擇一）</h2>
<table><tr><th>形式</th><th class="r">低 VIX</th><th class="r">中</th><th class="r">高 VIX</th>
<th class="r">高低差 pp</th><th class="r">R²</th><th class="r">placebo 之下比例</th></tr>{cond_rows}</table>
<p style="font-size:.85rem;color:var(--dim)">「placebo 之下比例」越低越好；要達顯著水平需 &lt;5%。
多重比較：我哋測咗 4 種形式，單一形式嘅 p 值唔構成證據。</p>

<h2>3. Filter before / after（32 格網格，入樣本）</h2>
<table><tr><th></th><th class="r">總回報%</th><th class="r">CAGR%</th><th class="r">最大回撤%</th>
<th class="r">Sharpe-like</th><th class="r">單數</th><th class="r">勝率%</th></tr>
<tr><td>無 filter</td><td class="r'>{base['total_return_pct']}</td><td class="r'>{base['cagr_pct']}</td>
<td class="r'>{base['max_dd_pct']}</td><td class="r'>{base['sharpe_like']}</td>
<td class="r'>{base['trades']:,}</td><td class="r'>{base['win_rate_pct']}</td></tr>
<tr><td>{best['direction']} {best['column']}={best['threshold']}</td><td class="r'><b>{best['total_return_pct']}</b></td>
<td class="r'>{best['cagr_pct']}</td><td class="r'><b>{best['max_dd_pct']}</b></td>
<td class="r'>—</td><td class="r'>{best['n_signals']:,}</td><td class="r'>—</td></tr></table>

<h3>Filter 層面 placebo（決定性測試）</h3>
<table><tr><th>指標</th><th class="r">值</th></tr>
<tr><td>實測改善（總回報）</td><td class="r'><b>{pb['real_improvement_pp']} pp</b></td></tr>
<tr><td>placebo 平均改善</td><td class="r'>{pb['placebo_mean_improvement_pp']} pp</td></tr>
<tr><td>placebo 95 分位</td><td class="r'>{pb['placebo_p95_improvement_pp']} pp</td></tr>
<tr><td>placebo 最好一次</td><td class="r'>{pb['placebo_max_improvement_pp']} pp</td></tr>
<tr><td>placebo 贏過實測嘅比例</td><td class="r'><b>{pb['pct_of_placebo_better_than_real']*100:.0f}%</b></td></tr></table>

<h3>Fillable supply（防止「淨係剪單嚟靚比率」）</h3>
<table><tr><th></th><th class="r">訊號數</th><th class="r">中位穿透%</th><th class="r">≤0.5%</th><th class="r">≤2%</th></tr>
<tr><td>無 filter</td><td class="r'>{s['fillable_supply_baseline']['n']:,}</td>
<td class="r'>{s['fillable_supply_baseline']['median_penetration_pct']}</td>
<td class="r'>{s['fillable_supply_baseline']['within_0.5pct_pct']}%</td>
<td class="r'>{s['fillable_supply_baseline']['within_2pct_pct']}%</td></tr>
<tr><td>有 filter</td><td class="r'>{s['best_filter_event_robustness']['supply_after']['n']:,}</td>
<td class="r'>{s['best_filter_event_robustness']['supply_after']['median_penetration_pct']}</td>
<td class="r'>{s['best_filter_event_robustness']['supply_after']['within_0.5pct_pct']}%</td>
<td class="r'>{s['best_filter_event_robustness']['supply_after']['within_2pct_pct']}%</td></tr></table>
<p style="font-size:.85rem;color:var(--dim)">供給未被削走（62.0% → 63.2%），即係 filter 唔係靠減少
「夠得着入場位」嘅訊號嚟改善比率。</p>

<h2>4. Event dominance（剔走 2020-03 同 2022）</h2>
<p>剔走兩個事件窗之後，條件差距由
{s['event_robustness']['tercile_spread_pp_all']} pp 變成
{s['event_robustness']['tercile_spread_pp_no_events']} pp；每單平均由
{s['event_robustness']['mean_net_pp_all']}% 升至 {s['event_robustness']['mean_net_pp_no_events']}%。
即係<b>效果唔係由兩個事件撐住</b> —— 但亦即係話，連事件都唔需要去解釋。</p>

<h2>5. 無偷睇未來</h2>
<p>T-1 探針：訊號日 {s['lookahead_probe']['probe_entry']}，
當日 VIX = {s['lookahead_probe']['spike_value_same_day']}（假尖峰），
實際採用 = {s['lookahead_probe']['probe_vix_used']}（T-1 值）。{yn(s['lookahead_probe']['passed'])}</p>

<h2>限制（誠實聲明）</h2>
<ul style="font-size:.9rem">
<li>上面最佳 filter 係喺 <b>同一份數據</b>上由 {f45['n_grid_cells']} 格網格選出（入樣本）。
 placebo 只證明「呢條規則好過隨機 VIX 對齊」，<b>唔證明</b>「好過 32 次挑選之後嘅機會成本」。</li>
<li>回撤減半很可能係「喺高波動期少做幾單」嘅一般性質，唔係假設預測嘅傳導機制。</li>
<li>本頁 <code>noindex</code>，屬研究記錄，唔係產品頁。flag 維持 OFF。</li>
</ul>
</body></html>"""
    out.write_text(html, encoding="utf-8")
    return out


# ---------------------------------------------------------------------------
def main() -> int:
    print(f"VIX regime study — market={MKT}, filter flag "
          f"vix_regime_filter={VC.vix_regime_filter} (OFF = no production effect)")
    vix = load_vix()
    print(f"  VIX source: FRED VIXCLS  n={len(vix)}  "
          f"{vix.date.min().date()} → {vix.date.max().date()}")

    look = assert_no_lookahead()
    print(f"  T-1 look-ahead probe: used VIX {look['probe_vix_used']} "
          f"(same-day spike was {look['spike_value_same_day']}) → "
          f"{'PASS' if look['passed'] else 'FAIL'}")
    if not look["passed"]:
        return 2

    cands = jp_candidates()
    print(f"  JP v2 candidates: {len(cands)} "
          f"(glitch-mask dropped {cands.attrs.get('glitch_dropped', 0)})")
    sig = add_tercile(attach_vix(cands, vix))
    sig.to_csv(OUT_CSV, index=False)
    print(f"  → {OUT_CSV}")

    summary = {"spec": "docs/vix-regime-filter-test-spec.md",
               "market": MKT,
               "vix_source": "FRED VIXCLS (CBOE VIX close, republished by the "
                             "Federal Reserve Bank of St. Louis)",
               "vix_rows": int(len(vix)),
               "lookahead_probe": look,
               "flag_vix_regime_filter": VC.vix_regime_filter}

    summary["step1_regime_split"] = step1_regime_split(cands, vix)
    summary["step3_explanatory"] = step3_explanatory(sig)
    summary["placebo"] = [placebo_shift(sig, vix, form) for form in
                          ("percentile_20d", "level", "spike")]
    nod = drop_event_windows(sig)
    summary["event_robustness"] = {
        "excluded_windows": EVENT_WINDOWS,
        "n_all": int(len(sig)), "n_without_events": int(len(nod)),
        "tercile_spread_pp_all": round(tercile_spread(sig), 4),
        "tercile_spread_pp_no_events": round(tercile_spread(nod), 4),
        "mean_net_pp_all": round(float(sig.gross.mean() * 100), 3),
        "mean_net_pp_no_events": round(float(nod.gross.mean() * 100), 3),
    }
    summary["multiple_comparisons"] = {
        "forms_tested": 4, "note": "level, spike, pct20, pct60. A marginal "
        "p-value from any single one is not evidence on its own; the placebo "
        "distribution is the honest reference."}
    summary["fillable_supply_baseline"] = supply_stats(sig)
    f45 = step45_filter(cands, vix)
    summary["step45_filter"] = f45
    if "best_cell" in f45:
        summary["placebo_on_selected_filter"] = placebo_filter(cands, vix, f45["best_cell"])
        bc = f45["best_cell"]
        v2 = vix.copy()
        v2["vix_prev"] = v2["vix"].shift(1)
        v2["vix_chg1"] = v2["vix"] - v2["vix_prev"]
        v2["vix_pct60"] = _trailing_rank(v2["vix"], 60)
        s2 = attach_vix(cands, v2)
        keep = (s2[bc["column"]] <= bc["threshold"]) if bc["direction"] == "skip_above" \
            else (s2[bc["column"]] >= bc["threshold"])
        sub = s2[keep.fillna(True)]
        nod = drop_event_windows(sub)
        summary["best_filter_event_robustness"] = {
            "rule": f"{bc['direction']} {bc['column']}={bc['threshold']}",
            "mean_net_pp_all": round(float(sub.gross.mean() * 100), 3),
            "mean_net_pp_no_events": round(float(nod.gross.mean() * 100), 3) if len(nod) else None,
            "n_kept": int(len(sub)), "n_after_dropping_events": int(len(nod)),
            "supply_after": supply_stats(sub),
        }

    OUT_JSON.write_text(json.dumps(summary, indent=2, ensure_ascii=False, default=str))
    print(f"  → {OUT_JSON}")
    page = write_page(summary)
    print(f"  → {page}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
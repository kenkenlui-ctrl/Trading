#!/usr/bin/env python3
"""label_cohort.py — measure the PUBLISHED context labels on the PLAN cohort.

RESEARCH ONLY. Nothing here is imported by a production build path.

THE BUG THIS FIXES
    The two read-only labels the site publishes (T1a "貼近箱頂", F5 "強過大盤")
    were measured on the wrong cohort, at the wrong moment, against a
    different index than the one production uses:

      * COHORT. The measurement ran on the FILLED subset — a plan that had
        already traded. The site publishes PLANS, every day, whether they fill
        or not. Those are different populations.
      * MOMENT. The features were read at the FILL bar. At the fill bar the
        price has just come down to S1, so "close within 2% of R1" there is not
        "the plan was published near the top of the box" — it is a proxy for
        "the box was very narrow". The site reads the label at the PLAN bar,
        where the price can be anywhere in the box.
      * INDEX. The research compared each stock against IDX_GSPC / IDX_N225.
        Production compares against SPY / 1306.T. The flag and the evidence
        were about two different benchmarks.

THE PLAN COHORT, MADE EXPLICIT
    The v2 engine's own convention, written out:

      features row q   everything the engine knew at the close of bar q-1
      plan   at row q   the screen passes at row q -> the site publishes this
                        stock tonight, quoting entry S1(q) and stop S1(q)*(1-3*ATR)
      fill   at bar q   O_q > S1(q), L_q <= S1(q), C_q >= S1(q) -> the plan
                        published at row q fills the NEXT session, at the very
                        level the plan quoted

    Row q carries BOTH the plan and its fill, because the plan published after
    bar q-1 quotes S1(q) and the fill on bar q is taken at that same level.
    The published set and the filled set therefore live on the SAME rows, and
    the difference between them is exactly the set of plans that never filled.

    This is the production semantics, and --parity proves it field by field
    against the published payload.

WHAT IS MEASURED
    For every plan row, per market and per window:

      trigger rate      how often the label fires on PUBLISHED plans (this is
                        the number a reader sees on the site, and it is not
                        the filled-subset rate the old study quoted)
      fill rate         P(fill | label) against P(fill | no label) — the
                        question a reader actually has about a plan
      post-fill result  the same engine's net per trade, conditional on the
                        fill, label against no label

    Both index series are measured (SPY/1306_T = production's benchmark, and
    IDX_GSPC/IDX_N225 = the old study's), so the F5 sensitivity to the
    benchmark is a reported number rather than an assumption.

Usage:
    python3 scripts/research/label_cohort.py --selftest
    python3 scripts/research/label_cohort.py --parity
    python3 scripts/research/label_cohort.py
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO / "scripts" / "v2engine"))
sys.path.insert(0, str(REPO / "scripts"))
sys.path.insert(0, str(REPO / "scripts" / "research"))

from core import _simulate, cost_rt, features        # noqa: E402
import short_side as SS                                # noqa: E402

BARS = SS.BARS
SPLIT = SS.SPLIT
HOLD = SS.HOLD
MARKETS = SS.MARKETS
ATR_MAX = SS.ATR_MAX
BOX_MAX = SS.BOX_MAX
STOP_ATR = SS.STOP_ATR
NO_TARGET = 1e12

# The label thresholds, identical to scripts/build_v2_signals.py.
T1A_MAX_PCT = 2.0
RS_WINDOW = 250

# The index each market's F5 flag is measured against. PRODUCTION_INDEX is what
# scripts/build_v2_signals.py::_read_index() fetches (SPY / 1306.T, with the
# fallbacks listed there). STUDY_INDEX is what the 2026-11-15 study used.
PRODUCTION_INDEX = {"us200": "SPY", "jp200": "1306_T"}
STUDY_INDEX = {"us200": "IDX_GSPC", "jp200": "IDX_N225"}

OUT_DIR = REPO / "research" / "2026-11-15-short-side"


# --------------------------------------------------------------------------
# bar access
# --------------------------------------------------------------------------
def is_jp(sym: str) -> bool:
    return sym.endswith("_T")


def is_us(sym: str) -> bool:
    return (not is_jp(sym) and "_" not in sym
            and not sym.startswith(("HK_", "JP_")))


def symbol_list(mkt: str) -> list[str]:
    out = []
    for p in sorted(BARS.glob("*.json")):
        s = p.stem
        if (is_jp(s) if mkt == "jp200" else is_us(s)):
            out.append(s)
    return out


def load_snapshot(path: Path) -> pd.DataFrame | None:
    """Load the LIVE per-ticker snapshot (public/<mkt>/ohlc/*.json).

    That file is a plain list of bar dicts. The frozen 10-year store is a
    different shape and goes through SS.load_bars. Both end up as the same
    DataFrame: date index, OHLCV columns, ascending.
    """
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if isinstance(raw, dict):
        raw = (raw.get("data") or {}).get("bars") or []
    if not isinstance(raw, list) or not raw:
        return None
    df = pd.DataFrame(raw)
    if "date" not in df.columns:
        return None
    df["date"] = pd.to_datetime(df["date"]).dt.tz_localize(None).dt.normalize()
    df = df[["date", "open", "high", "low", "close", "volume"]].dropna(
        subset=["open", "high", "low", "close"])
    return df.sort_values("date").drop_duplicates("date").set_index("date")


# --------------------------------------------------------------------------
# the engine's screen, on features rows
# --------------------------------------------------------------------------
def screen(f: pd.DataFrame) -> np.ndarray:
    """The v2 plan screen, evaluated on features rows.

    A row q passes when the engine, looking only at bars up to q-1, would see
    an uptrend with a tradeable box. This is the same condition
    scripts/build_v2_signals.py::plan() applies to its synthetic next row.
    """
    a = f.atr_pct.to_numpy(float)
    s1 = f.S1.to_numpy(float)
    r1 = f.R1.to_numpy(float)
    r2 = f.R2.to_numpy(float)
    prev = f.prev_close.to_numpy(float)
    ma200 = f.ma200.to_numpy(float)
    slope = f.ma200_slope.to_numpy(float)
    boxw = f.box_w.to_numpy(float)
    valid = (~np.isnan(s1)) & (~np.isnan(r1)) & (~np.isnan(r2)) & (~np.isnan(a)) & (a > 0)
    return (valid & (prev > ma200) & (slope > 0)
            & (a < ATR_MAX) & (boxw < BOX_MAX))


def index_roc_map(sym: str) -> dict:
    """date string -> that index's own 250-session ROC on that date."""
    p = BARS / f"{sym}.json"
    if not p.exists():
        return {}
    df = SS.load_bars(p)
    if df is None:
        return {}
    c = df["close"].astype(float)
    roc = c / c.shift(RS_WINDOW) - 1
    return {d.strftime("%Y-%m-%d"): float(v) for d, v in roc.items() if v == v}


def asof_lookup(dates: list[str], table: dict) -> list[float]:
    """For each date, the last table entry at or before it."""
    if not table:
        return [float("nan")] * len(dates)
    keys = sorted(table)
    idx = np.searchsorted(np.array(keys), np.array(dates), side="right") - 1
    return [table[keys[k]] if k >= 0 else float("nan") for k in idx]


def clustered_t(dates: np.ndarray, net: np.ndarray) -> float:
    """Date-clustered t: simultaneous entries are not independent samples."""
    if len(net) < 3:
        return 0.0
    df = pd.DataFrame({"d": dates, "n": net})
    by = df.groupby("d").n.mean()
    if len(by) < 3 or by.std(ddof=1) == 0:
        return 0.0
    return float(by.mean() / (by.std(ddof=1) / np.sqrt(len(by))))


def summarise(rows: list[dict]) -> dict:
    if not rows:
        return {"n": 0, "avg%": None, "win%": None, "t": None}
    net = np.array([r["net"] for r in rows], dtype=float)
    return {
        "n": int(len(net)),
        "avg%": round(float(net.mean() * 100), 3),
        "win%": round(float((net > 0).mean() * 100), 1),
        "t": round(clustered_t(np.array([r["date"] for r in rows]), net), 2),
    }


# --------------------------------------------------------------------------
# the per-symbol plan pass
# --------------------------------------------------------------------------
def plan_rows(symbol: str, mkt: str, roc_maps: dict, mask: set) -> list[dict]:
    """Every plan row for one symbol, with its labels and its outcome."""
    df = SS.load_bars(BARS / f"{symbol}.json")
    if df is None or len(df) < 60:
        return []
    f = features(df)
    ok = screen(f)
    if not ok.any():
        return []
    O, H, L, C = (df[k].to_numpy(float) for k in ("open", "high", "low", "close"))
    a = f.atr_pct.to_numpy(float)
    s1 = f.S1.to_numpy(float)
    r1 = f.R1.to_numpy(float)
    prev = f.prev_close.to_numpy(float)
    boxw = f.box_w.to_numpy(float)
    c = df["close"].to_numpy(float)
    dates = df.index.strftime("%Y-%m-%d")
    idx = df.index

    rows_idx = np.where(ok)[0]
    # the decision bar of row q is bar q-1 (features are shifted by one)
    dec = [q - 1 for q in rows_idx]
    dec_dates = [dates[d] for d in dec]
    roc_prod = asof_lookup(dec_dates, roc_maps.get("prod", {}))
    roc_study = asof_lookup(dec_dates, roc_maps.get("study", {}))

    out = []
    for k, q in enumerate(rows_idx):
        d = dec[k]
        ext = (r1[q] / prev[q] - 1.0) * 100.0
        stock_roc = (c[d] / c[d - RS_WINDOW] - 1.0) if d >= RS_WINDOW else float("nan")
        rp, rs = roc_prod[k], roc_study[k]
        row = {
            "mkt": mkt, "sym": symbol, "row": int(q),
            "date": dates[q], "dec_date": dec_dates[k],
            "ext_to_r1_pct": round(float(ext), 2),
            "dist_to_entry_pct": round((float(s1[q]) / float(prev[q]) - 1.0) * 100, 2),
            "box_w_pct": round(float(boxw[q] * 100.0), 2),
            "atr_pct": round(float(a[q] * 100.0), 2),
            "stock_roc250_pct": (None if stock_roc != stock_roc
                                 else round(float(stock_roc * 100.0), 1)),
            "index_roc250_prod_pct": (None if rp != rp else round(float(rp * 100.0), 1)),
            "index_roc250_study_pct": (None if rs != rs else round(float(rs * 100.0), 1)),
            "t1a": bool(ext <= T1A_MAX_PCT),
            "f5_prod": (None if (stock_roc != stock_roc or rp != rp)
                        else bool(stock_roc > rp)),
            "f5_study": (None if (stock_roc != stock_roc or rs != rs)
                         else bool(stock_roc > rs)),
            "filled": False, "net": None, "reason": None,
            "masked": False,
        }
        # the fill: the NEXT session, at the level the plan quoted
        if (O[q] > s1[q]) and (L[q] <= s1[q]) and (C[q] >= s1[q]):
            stop = s1[q] * (1 - STOP_ATR * a[q])
            if stop > 0 and stop < s1[q]:
                res = _simulate(1, q, s1[q], stop, NO_TARGET, O, H, L, C, HOLD, True)
                if res is not None:
                    j, xp, why = res
                    lo = idx[q] - pd.Timedelta(days=304)
                    if mask and any(lo <= d <= idx[j] for d in mask):
                        # a masked bar sits in the window: this fill is
                        # EXCLUDED from the book, not "never filled". It is
                        # reported as masked so it leaves both the fill
                        # numerator and the no-fill denominator.
                        row["filled"] = True
                        row["masked"] = True
                    else:
                        gross = xp / s1[q] - 1
                        row["filled"] = True
                        row["net"] = float(gross - cost_rt(mkt, "site", why == "stop"))
                        row["reason"] = why
        out.append(row)
    return out


# --------------------------------------------------------------------------
# the analysis
# --------------------------------------------------------------------------
def analyse(all_rows: list[dict]) -> tuple[list[dict], list[dict]]:
    summary, yearly = [], []
    for mkt in MARKETS:
        rows = [r for r in all_rows if r["mkt"] == mkt]
        for win in ("train", "test", "full"):
            sub = [r for r in rows
                   if not r.get("masked")
                   and (win == "full" or SS.window_of(r["date"]) == win)]
            for label, key in (("T1a_near_box_top", "t1a"),
                               ("F5_rs_vs_prod_index", "f5_prod"),
                               ("F5_rs_vs_study_index", "f5_study")):
                rec = {"market": mkt, "window": win, "label": label,
                       "n_plans": len(sub)}
                for tag, want in (("triggered", True), ("not_triggered", False)):
                    grp = [r for r in sub if r.get(key) is want]
                    fills = [r for r in grp if r["filled"]]
                    st = summarise(fills)
                    rec[f"{tag}_n_plans"] = len(grp)
                    rec[f"{tag}_plan_rate_pct"] = round(
                        len(grp) / max(1, len(sub)) * 100, 2)
                    rec[f"{tag}_n_filled"] = len(fills)
                    rec[f"{tag}_fill_rate_pct"] = round(
                        len(fills) / max(1, len(grp)) * 100, 2)
                    rec[f"{tag}_avg%"] = st["avg%"]
                    rec[f"{tag}_win%"] = st["win%"]
                    rec[f"{tag}_t"] = st["t"]
                rec["delta_fill_rate_pp"] = round(
                    rec["triggered_fill_rate_pct"] - rec["not_triggered_fill_rate_pp"]
                    if False else rec["triggered_fill_rate_pct"]
                    - rec["not_triggered_fill_rate_pct"], 2)
                rec["delta_avg_pp"] = (
                    round(rec["triggered_avg%"] - rec["not_triggered_avg%"], 3)
                    if rec["triggered_avg%"] is not None
                    and rec["not_triggered_avg%"] is not None else None)
                rec["delta_win_pp"] = (
                    round(rec["triggered_win%"] - rec["not_triggered_win%"], 1)
                    if rec["triggered_win%"] is not None
                    and rec["not_triggered_win%"] is not None else None)
                summary.append(rec)
        # per-year trigger rate, to see whether the rate is regime-dependent
        for yr in sorted({r["dec_date"][:4] for r in rows}):
            sub = [r for r in rows
                   if not r.get("masked") and r["dec_date"][:4] == yr]
            rec = {"market": mkt, "year": yr, "n_plans": len(sub)}
            for label, key in (("t1a", "t1a"), ("f5_prod", "f5_prod"),
                               ("f5_study", "f5_study")):
                g = [r for r in sub if r.get(key) is True]
                rec[f"{label}_rate_pct"] = round(len(g) / max(1, len(sub)) * 100, 2)
                rec[f"{label}_n"] = len(g)
            rec["fill_rate_pct"] = round(
                sum(1 for r in sub if r["filled"]) / max(1, len(sub)) * 100, 2)
            yearly.append(rec)
    return summary, yearly


def robustness(all_rows: list[dict]) -> list[dict]:
    """Is T1a just a proxy for "the entry level is far below the price"?

    The site already publishes dist_to_entry_pct on every signal. If the
    fill-rate gap survives inside one entry-distance bucket, the label carries
    information the distance does not. If it does not, the label is decoration
    and should not be published as if it separates anything.
    """
    out = []
    for mkt in MARKETS:
        rows = [r for r in all_rows
                if r["mkt"] == mkt and not r.get("masked")]
        for lo, hi in ((-99.0, -5.0), (-5.0, -2.0), (-2.0, 0.0)):
            sub = [r for r in rows if lo <= r["dist_to_entry_pct"] < hi]
            rec = {"market": mkt, "dist_to_entry_bucket": f"[{lo},{hi})",
                   "n_plans": len(sub)}
            for tag, want in (("triggered", True), ("not_triggered", False)):
                grp = [r for r in sub if r.get("t1a") is want]
                fills = [r for r in grp if r["filled"]]
                rec[f"{tag}_n_plans"] = len(grp)
                rec[f"{tag}_n_filled"] = len(fills)
                rec[f"{tag}_fill_rate_pct"] = round(
                    len(fills) / max(1, len(grp)) * 100, 2)
            rec["delta_fill_rate_pp"] = round(
                rec["triggered_fill_rate_pct"] - rec["not_triggered_fill_rate_pct"], 2)
            out.append(rec)
    return out


def _write_gz(df: pd.DataFrame, path: Path) -> None:
    """Deterministic gzip: mtime=0 so the tracked artifact does not change
    byte-for-byte on every run (a dirty repo after every build is a dirty
    repo nobody reads)."""
    import gzip
    with gzip.GzipFile(path, "wb", mtime=0) as fh:
        fh.write(df.to_csv(index=False).encode("utf-8"))


def equivalence_gate(rows: list[dict]) -> bool:
    """The filled subset of this module must reproduce the authoritative A/B
    backtest trade counts, trade for trade.

    The published v2 numbers come from data/ab_close_hold.json. This module
    measures a DIFFERENT population (plans, not fills), so it is not obvious
    that its filled subset is the same set of trades. If it is not, every
    number it produces is about a different experiment. So the gate compares
    the counts against the artifact and fails loudly.
    """
    ab_path = REPO / "data" / "ab_close_hold.json"
    try:
        ab = json.loads(ab_path.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        print(f"  FAIL  cannot read {ab_path}: {e}")
        return False
    ok = True
    for mkt in MARKETS:
        for win, key in (("train", "train"), ("test", "test")):
            want = ab["windows"][mkt]["B_close-hold"][key]["n"]
            got = sum(1 for r in rows
                      if r["mkt"] == mkt and not r.get("masked")
                      and r["filled"] and SS.window_of(r["date"]) == win)
            good = got == want
            print(f"  {'PASS' if good else 'FAIL'}  {mkt} {win}: the filled "
                  f"subset reproduces the authoritative trade count "
                  f"({got} vs {want})")
            ok &= good
    return ok


def build_roc_maps(mkt: str) -> dict:
    return {
        "prod": index_roc_map(PRODUCTION_INDEX[mkt]),
        "study": index_roc_map(STUDY_INDEX[mkt]),
    }


def collect(mask_all: dict) -> list[dict]:
    out = []
    for mkt in MARKETS:
        roc_maps = build_roc_maps(mkt)
        syms = symbol_list(mkt)
        print(f"  {mkt}: {len(syms)} symbols, "
              f"index prod={PRODUCTION_INDEX[mkt]} study={STUDY_INDEX[mkt]}",
              flush=True)
        for s in syms:
            out.extend(plan_rows(s, mkt, roc_maps, mask_all.get(s) or set()))
    return out


# --------------------------------------------------------------------------
# parity with the published payload
# --------------------------------------------------------------------------
def parity() -> int:
    """Recompute the published labels from the LIVE snapshot and compare.

    This is the check that the research measures what the site publishes. If
    the two ever disagree, every number in this module is about something else.
    """
    payload_path = REPO / "public" / "v2-signals.json"
    if not payload_path.exists():
        print("  FAIL  public/v2-signals.json not found")
        return 1
    payload = json.loads(payload_path.read_text(encoding="utf-8"))
    sigs = payload.get("signals") or []
    n_ok = n_bad = n_skip = 0
    bad = []
    for s in sigs:
        sym = s["symbol"]
        mkt = s["market"]
        path = REPO / "public" / mkt / "ohlc" / f"{sym}_ohlc.json"
        df = load_snapshot(path)
        if df is None:
            n_skip += 1
            continue
        nxt = df.index[-1] + pd.offsets.BDay(1)
        bb = pd.concat([df, df.iloc[[-1]].set_index(pd.DatetimeIndex([nxt]))])
        f = features(bb).iloc[-1]
        ext = round((float(f.R1) / float(f.prev_close) - 1) * 100, 2)
        c = df["close"].to_numpy(float)
        stock_roc = None
        if len(c) > RS_WINDOW and c[-1 - RS_WINDOW] > 0:
            stock_roc = round(float(c[-1] / c[-1 - RS_WINDOW] - 1) * 100, 1)
        lab = s.get("context_labels") or {}
        checks = [
            ("ext_to_r1_pct", ext, s.get("ext_to_r1_pct")),
            ("stock_roc250_pct", stock_roc, lab.get("stock_roc250_pct")),
            ("too_extended", bool(ext <= T1A_MAX_PCT), lab.get("too_extended")),
        ]
        mism = [(k, mine, theirs) for k, mine, theirs in checks
                if mine is not None and theirs is not None and mine != theirs]
        if mism:
            n_bad += 1
            bad.append((sym, mism))
        else:
            n_ok += 1
    print(f"  {'PASS' if n_bad == 0 else 'FAIL'}  parity with the published "
          f"payload: {n_ok} matched, {n_bad} mismatched, {n_skip} skipped")
    for sym, mism in bad[:8]:
        print(f"        {sym}: {mism}")
    return 0 if n_bad == 0 else 1


# --------------------------------------------------------------------------
# self-test
# --------------------------------------------------------------------------
def _selftest_body() -> bool:
    ok = True
    n = 400
    idx = pd.bdate_range("2020-01-01", periods=n)
    c = np.linspace(100.0, 200.0, n)
    df = pd.DataFrame({"open": c, "high": c + 1, "low": c - 1, "close": c,
                       "volume": 1e6}, index=idx)
    f = features(df)

    # 1. a features row q must use bars up to q-1 only
    mut = df.copy()
    mut.iloc[300, mut.columns.get_loc("close")] = 9999.0
    mut.iloc[300, mut.columns.get_loc("high")] = 9999.0
    f2 = features(mut)
    unchanged = all(abs(float(f[k].iloc[300]) - float(f2[k].iloc[300])) < 1e-9
                    for k in ("R1", "S1", "prev_close", "ma200"))
    moved = abs(float(f["prev_close"].iloc[301]) - float(f2["prev_close"].iloc[301])) > 1e-9
    same = unchanged and moved
    print(f"  {'PASS' if same else 'FAIL'}  a features row does not see its own "
          f"session's close (row 300 unchanged, row 301 moved)")
    ok &= same

    # 2. production's synthetic-next-row construction must equal the features
    #    row this module reads. Production builds the frame that ENDS at the
    #    decision bar and appends a synthetic next bar; this module reads the
    #    row after that decision bar on the full frame. Same data, same row.
    q = n - 1
    df_p = df.iloc[:q]
    nxt = df_p.index[-1] + pd.offsets.BDay(1)
    bb = pd.concat([df_p, df_p.iloc[[-1]].set_index(pd.DatetimeIndex([nxt]))])
    fs = features(bb).iloc[-1]
    fr = features(df).iloc[q]
    same2 = all(abs(float(fs[k]) - float(fr[k])) < 1e-9
                for k in ("R1", "S1", "prev_close", "ma200", "atr_pct", "box_w"))
    print(f"  {'PASS' if same2 else 'FAIL'}  the synthetic next row equals the "
          f"row after the decision bar")
    ok &= same2

    # 3. the screen must not fire on a downtrend
    down = df.copy()
    down["close"] = np.linspace(200.0, 100.0, n)
    down["high"] = down["close"] + 1
    down["low"] = down["close"] - 1
    down["open"] = down["close"]
    s_ok = screen(features(down))
    print(f"  {'PASS' if not s_ok.any() else 'FAIL'}  the screen rejects a "
          f"downtrend")
    ok &= not s_ok.any()

    # 4. the asof lookup takes the last entry at or before the date
    tbl = {"2020-01-01": 1.0, "2020-01-03": 2.0}
    got = asof_lookup(["2020-01-01", "2020-01-02", "2020-01-05"], tbl)
    good = (abs(got[0] - 1.0) < 1e-9 and abs(got[1] - 1.0) < 1e-9
            and abs(got[2] - 2.0) < 1e-9)
    print(f"  {'PASS' if good else 'FAIL'}  the index lookup is last-bar-at-or-before")
    ok &= good

    # 5. the clustered t must not treat same-day entries as independent
    d = np.array(["2020-01-01"] * 10 + ["2020-01-02"] * 10)
    net = np.array([1.0] * 10 + [-0.5] * 10)
    t_clustered = clustered_t(d, net)
    t_naive = float(net.mean() / (net.std(ddof=1) / np.sqrt(len(net))))
    good = abs(t_clustered) < abs(t_naive)
    print(f"  {'PASS' if good else 'FAIL'}  the clustered t is smaller than the "
          f"naive t on same-day clusters")
    ok &= good

    return bool(ok)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--parity", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        print("=" * 72)
        print("LABEL-COHORT SELF-TEST")
        print("=" * 72)
        raise SystemExit(0 if _selftest_body() else 1)
    if a.parity:
        print("=" * 72)
        print("LABEL-COHORT PARITY WITH THE PUBLISHED PAYLOAD")
        print("=" * 72)
        raise SystemExit(parity())
    print("=" * 72)
    print("LABEL COHORT — the published labels, measured on the PLAN cohort")
    print("=" * 72)
    mask_all = SS.E.load_mask()
    rows = collect(mask_all)
    print("\n=== equivalence gate: the filled subset must be the same trades ===")
    if not equivalence_gate(rows):
        raise SystemExit("FATAL: the filled subset does not reproduce the "
                         "authoritative trade counts — every number this "
                         "module produces would be about a different "
                         "experiment")
    summary, yearly = analyse(rows)
    robust = robustness(rows)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(summary).to_csv(OUT_DIR / "label_cohort_summary.csv", index=False)
    pd.DataFrame(yearly).to_csv(OUT_DIR / "label_cohort_by_year.csv", index=False)
    pd.DataFrame(robust).to_csv(OUT_DIR / "label_cohort_robustness.csv", index=False)
    _write_gz(pd.DataFrame(rows), OUT_DIR / "label_cohort_plans.csv.gz")
    # The canonical summary the site reads. data/ is not tracked, so this file
    # is produced by the research run and consumed by the page builder; the
    # tracked research/ copies above are the same numbers.
    (REPO / "data").mkdir(parents=True, exist_ok=True)
    payload = {
        "generated_from": "scripts/research/label_cohort.py",
        "plan_cohort": "published v2 plans (the screen passing at the decision bar)",
        "fill_definition": "the next session, at the level the plan quoted",
        "t1a_threshold_pct": T1A_MAX_PCT,
        "summary": summary,
        "robustness": robust,
        "by_year": yearly,
    }
    (REPO / "data" / "label_cohort.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    for rec in summary:
        if rec["window"] in ("test", "full"):
            print(f"\n{rec['market']} {rec['window']} {rec['label']}")
            print(f"  plans {rec['n_plans']}  triggered {rec['triggered_n_plans']} "
                  f"({rec['triggered_plan_rate_pct']}%)  "
                  f"not {rec['not_triggered_n_plans']}")
            print(f"  fill rate   {rec['triggered_fill_rate_pct']}% vs "
                  f"{rec['not_triggered_fill_rate_pct']}%  "
                  f"(delta {rec['delta_fill_rate_pp']}pp)")
            print(f"  avg/trade   {rec['triggered_avg%']}% vs "
                  f"{rec['not_triggered_avg%']}%  "
                  f"(delta {rec['delta_avg_pp']}pp, n "
                  f"{rec['triggered_n_filled']} vs {rec['not_triggered_n_filled']})")
    print("\n=== robustness: does T1a separate INSIDE one entry-distance bucket? ===")
    for rec in robust:
        print(f"  {rec['market']} dist_to_entry {rec['dist_to_entry_bucket']:>14}: "
              f"plans {rec['n_plans']:>7} | T1a {rec['triggered_fill_rate_pct']}% "
              f"(n={rec['triggered_n_filled']}) vs "
              f"{rec['not_triggered_fill_rate_pct']}% "
              f"(n={rec['not_triggered_n_filled']})  "
              f"delta {rec['delta_fill_rate_pp']}pp")
    print(f"\nwrote {OUT_DIR/'label_cohort_summary.csv'}")
    print(f"wrote {OUT_DIR/'label_cohort_by_year.csv'}")
    print(f"wrote {OUT_DIR/'label_cohort_plans.csv.gz'}")

"""honest_results.py — the one place the failure results are read from.

WHY THIS EXISTS
    The site is about to publish a page whose entire claim is "we ran the
    tests and here is what failed". A claim like that is worthless if the
    numbers on it were typed by hand: the one thing a reader is entitled to
    check is whether the failures are real. So every figure on that page, and
    every figure in the methodology page's trial ledger, is READ here from the
    report JSON that produced it.

    There is deliberately no number literal in this module. If a source file
    is missing, or a key it used to have has moved, the load raises — the
    page cannot be built with a plausible-looking placeholder. The one
    exception is the prose around the numbers, which says what a number
    MEANS; the number itself is always formatted from the artifact.

    Two pages read from this module (build_honest_results_page.py and
    rewrite_methodology_v2.py), so a number cannot be right on one page and
    wrong on the other.
"""
from __future__ import annotations

import json
from pathlib import Path

REPO = Path("/Users/kenken/dev/dsa-hk")
REPORTS = REPO / "reports"
PUBLIC = REPO / "public"

# repo-relative path -> the report that produced the numbers on the page.
SOURCES = {
    "s1_summary": "reports/signal_search_s1_summary.json",
    "s1_placebo": "reports/signal_search_s1_us_placebo.json",
    "s2_axis_a": "reports/signal_search_s2_us_axis_a.json",
    "s2_axis_b": "reports/signal_search_s2_us_axis_b.json",
    "s3": "reports/signal_search_s3_us.json",
    "edge_a": "reports/edge_alpha_a_us.json",
    "edge_a_lag1": "reports/edge_alpha_a_us_lag1.json",
    "edge_c": "reports/edge_alpha_c_us.json",
    "ab_close_hold": "data/ab_close_hold.json",
    "v2_signals": "public/v2-signals.json",
}

_cache: dict = {}


def _load(key: str) -> dict:
    """Read one report. A missing or malformed source is a build failure."""
    if key in _cache:
        return _cache[key]
    rel = SOURCES[key]
    p = REPO / rel
    if not p.is_file():
        raise SystemExit(f"honest_results: source missing: {rel}")
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:
        raise SystemExit(f"honest_results: cannot read {rel}: {e}")
    _cache[key] = d
    return d


# --- formatting ------------------------------------------------------------
# Fixed decimals, applied at the point of printing, so the same value cannot
# appear with two different precisions on two pages.


def z(v) -> str:
    return f"{float(v):.2f}"


def zsign(v) -> str:
    return f"{float(v):+.2f}"


def pp(v) -> str:
    return f"{float(v):+.2f}"


def pp2(v) -> str:
    return f"{float(v):.2f}"


def sd(v) -> str:
    return f"{float(v):.3f}"


def pct(v) -> str:
    return f"{float(v):.1f}%"


def num(v) -> str:
    return f"{int(v):,}"


def frac(v) -> str:
    return f"{float(v) * 100:.1f}%"


# --- Sprint 1 --------------------------------------------------------------

_REGIME_ZH = {
    "ma200": "200 天線閘",
    "none": "冇大市閘",
    "cross50_200": "50/200 天線交叉",
}


def s1_label_zh(label: str) -> str:
    """The machine label of a Sprint 1 variation, in words.

    The report files name a variation by its config
    ("s1_20|stop_3x|hold_10|ma200|closehold"). That is a fine key for a JSON
    and an unreadable row on a public page, so the parts are spelled out.
    The machine label stays in the table too — the reader should be able to
    match a row back to the report file.
    """
    if label == "base":
        return "基準（現行 v2 規則）"
    parts = (label.split("|") + [""] * 5)[:5]
    s1, stop, hold, regime, ch = parts
    out = []
    if s1:
        out.append(f"S1 {s1.split('_')[-1]} 日低位")
    if stop:
        out.append(f"止損 {stop.split('_')[-1].rstrip('x')}×ATR")
    if hold:
        out.append(f"持有 {hold.split('_')[-1]} 日")
    if regime:
        out.append(_REGIME_ZH.get(regime, regime))
    out.append("收市確認" if ch == "closehold" else "唔確認收市")
    return " · ".join(out)


def sprint1() -> dict:
    """Sprint 1: 11 variations x 3 cost scenarios, scored against a placebo.

    The verdict that matters is the SECOND run (2026-10-19): the original
    fixed "+2pp" bar sat BELOW its own placebo null, so two variations
    "passed" a test they could not pass. The re-run uses 300 trials and the
    placebo-adjusted bar, and nothing clears it.
    """
    ph = _load("s1_placebo")
    summ = _load("s1_summary")
    out = {
        "source": SOURCES["s1_placebo"],
        "spec": ph.get("spec", ""),
        "bar": ph.get("bar", ""),
        "bar_supersedes": ph.get("bar_supersedes", ""),
        "trials": ph["placebo_trials"],
        "seed": ph["placebo_seed"],
        "n_variations": ph["n_variations"],
        "n_clearing": ph["n_clearing_placebo_bar"],
        "verdict": ph.get("verdict", ""),
        "ew_cagr_pct": ph["ew_cagr_pct"],
        "n_cells_scored": len(summ["grid"]["us"]["variations"]),
        "n_cost_scenarios": len({v["cost_scenario"]
                                 for v in summ["grid"]["us"]["variations"]}),
        "n_tickers": summ["grid"]["us"]["n_tickers"],
        "variations": [],
    }
    for v in ph["variations"]:
        g = v["placebo"]["gate"]
        out["variations"].append({
            "label": v["label"],
            "label_zh": s1_label_zh(v["label"]),
            "worst_z": g["worst_z"],
            "worst_net_pp": g["worst_net_pp"],
            "clears": g.get("clears_placebo_bar", False),
            "exposure_pct": v.get("avg_exposure_pct"),
        })
    out["n_worse"] = sum(1 for v in out["variations"] if v["worst_z"] < -1.96)
    out["best_z"] = max(v["worst_z"] for v in out["variations"])
    return out


# --- Sprint 2 (crossed randomization) --------------------------------------


def _axis_window(doc: dict, window: str) -> dict:
    return doc["gate"]["windows"][window]


def sprint2() -> dict:
    """Sprint 2: three axes of v2, each measured against its own null.

    A — stock selection, B — regime gate: their own report files.
    C — entry timing: already measured in the Sprint 1 placebo re-run, whose
    "base" variation IS the published v2 rule, so the anti-timing result
    lives there. The Sprint 2 prereg lists it as a fixed, already-measured
    axis, and the report file that carries the number is named in its row.
    """
    a = _load("s2_axis_a")
    b = _load("s2_axis_b")
    ph = _load("s1_placebo")
    base = next(v for v in ph["variations"] if v["label"] == "base")
    w = "2021-2026"
    axes = [
        {
            "axis": "C — 入場時機 (entry timing)",
            "source": SOURCES["s1_placebo"],
            "window": w,
            "real_gap_pp": base["placebo"]["gate"]["windows"][w]["real_gap_pp"],
            "null_mean_pp": base["placebo"]["gate"]["windows"][w]["null_mean_pp"],
            "null_sd_pp": base["placebo"]["gate"]["windows"][w]["null_sd_pp"],
            "net_pp": base["placebo"]["gate"]["windows"][w]["net_pp"],
            "z": base["placebo"]["gate"]["windows"][w]["z"],
            "null_percentile": base["placebo"]["gate"]["windows"][w]["null_percentile"],
            "n": base["placebo"]["gate"]["windows"][w]["n"],
            "verdict": "FAIL",
        },
        {
            "axis": "A — 選股 (stock selection)",
            "source": SOURCES["s2_axis_a"],
            "window": w,
            "real_gap_pp": _axis_window(a, w)["real_gap_pp"],
            "null_mean_pp": _axis_window(a, w)["null_mean_pp"],
            "null_sd_pp": _axis_window(a, w)["null_sd_pp"],
            "net_pp": _axis_window(a, w)["net_pp"],
            "z": _axis_window(a, w)["z"],
            "null_percentile": _axis_window(a, w)["null_percentile"],
            "n": _axis_window(a, w)["n"],
            "verdict": a.get("verdict", ""),
        },
        {
            "axis": "B — 大市閘 (regime gate)",
            "source": SOURCES["s2_axis_b"],
            "window": w,
            "real_gap_pp": _axis_window(b, w)["real_gap_pp"],
            "null_mean_pp": _axis_window(b, w)["null_mean_pp"],
            "null_sd_pp": _axis_window(b, w)["null_sd_pp"],
            "net_pp": _axis_window(b, w)["net_pp"],
            "z": _axis_window(b, w)["z"],
            "null_percentile": _axis_window(b, w)["null_percentile"],
            "n": _axis_window(b, w)["n"],
            "verdict": b.get("verdict", ""),
        },
    ]
    return {
        "spec": a.get("spec", ""),
        "trials": a["trials"],
        "seed": a["seed"],
        "window": w,
        "axes": axes,
        "n_axes": len(axes),
        "n_failing": sum(1 for x in axes if x["verdict"] == "FAIL"),
    }


# --- Sprint 3 --------------------------------------------------------------


def sprint3() -> dict:
    """Sprint 3: the same screen against a deployment-matched benchmark."""
    d = _load("s3")
    arms = []
    for a in d["arms"]:
        w = "2021-2026"
        g = a["gate"]["windows"][w]
        arms.append({
            "arm": a["arm"],
            "regime": a.get("regime", ""),
            "real_gap_pp": g["real_gap_pp"],
            "null_mean_pp": g["null_mean_pp"],
            "null_sd_pp": g["null_sd_pp"],
            "net_pp": g["net_pp"],
            "z": g["z"],
            "null_percentile": g["null_percentile"],
            "n": g["n"],
            "passes": g.get("passes", False),
        })
    return {
        "source": SOURCES["s3"],
        "spec": d.get("spec", ""),
        "trials": d["trials"],
        "seed": d["seed"],
        "window": "2021-2026",
        "arms": arms,
        "n_arms": len(arms),
        "n_passing": sum(1 for a in arms if a["passes"]),
        "verdict": d.get("verdict", ""),
    }


# --- Direction A -----------------------------------------------------------


def direction_a() -> dict:
    """Direction A: is v2's stock selection better than random?

    Two arms (the screen, and the same screen with the gate removed) run
    through the same engine on the same sessions, with the same cost model,
    against a null that picks the same number of names at random. Two fill
    lags: same-day fill and next-day fill, because a fill the screen could
    not have got is not a fill.
    """
    runs = []
    for key, lag in (("edge_a", 0), ("edge_a_lag1", 1)):
        d = _load(key)
        arms = []
        for arm_key in ("arm1", "arm1_nogate"):
            arm = d[arm_key]
            n = d["arm2_universe"]
            arms.append({
                "arm": arm["arm"],
                "classification": arm["classification"],
                "clears_threshold": arm["clears_threshold"],
                "gap_early": arm["gap_vs_arm2_pp"]["2016-2020"],
                "gap_late": arm["gap_vs_arm2_pp"]["2021-2026"],
                "cagr_early": arm["cagr_pct"]["2016-2020"],
                "cagr_late": arm["cagr_pct"]["2021-2026"],
                "sharpe_early": arm["sharpe"]["2016-2020"],
                "sharpe_late": arm["sharpe"]["2021-2026"],
                "expo_gap_early": arm["exposure_gap_vs_arm2_pp"]["2016-2020"],
                "expo_gap_late": arm["exposure_gap_vs_arm2_pp"]["2021-2026"],
                "null_z_early": arm["null"]["2016-2020"]["z"],
                "null_z_late": arm["null"]["2021-2026"]["z"],
                "null_mean_early": arm["null"]["2016-2020"]["null_mean_pp"],
                "null_mean_late": arm["null"]["2021-2026"]["null_mean_pp"],
                "null_sd_early": arm["null"]["2016-2020"]["null_sd_pp"],
                "null_sd_late": arm["null"]["2021-2026"]["null_sd_pp"],
                "null_pct_early": arm["null"]["2016-2020"]["null_percentile"],
                "null_pct_late": arm["null"]["2021-2026"]["null_percentile"],
                "beats_null_early": arm["null"]["2016-2020"].get("beats_null"),
                "beats_null_late": arm["null"]["2021-2026"].get("beats_null"),
                "reason": arm.get("classification_reason", ""),
            })
        fd = d["firing_day_decomposition"]
        runs.append({
            "source": SOURCES[key],
            "fill_lag": lag,
            "arms": arms,
            "n_arms": len(arms),
            "firing_sessions": fd["firing_sessions"],
            "total_sessions": fd["total_sessions"],
            "firing_fraction": fd["firing_fraction"],
            "mean_names": fd["mean_names_per_firing_session"],
            "fd_gap_early": fd["gap_pp"]["2016-2020"],
            "fd_gap_late": fd["gap_pp"]["2021-2026"],
            "fd_status": fd["status"],
        })
    d = _load("edge_a")
    return {
        "spec": d.get("spec", ""),
        "threshold_pp": d["config"]["threshold_pp"],
        "z_crit": d["config"]["z_crit"],
        "trials": d["config"]["trials"],
        "seed": d["config"]["seed"],
        "n_names": d["universe"]["names"],
        "n_sessions": d["universe"]["sessions"],
        "first": d["universe"]["first"],
        "last": d["universe"]["last"],
        "runs": runs,
        "n_runs": len(runs),
        "n_arms_total": sum(r["n_arms"] for r in runs),
        "n_all_dead": sum(1 for r in runs for a in r["arms"]
                          if a["classification"] == "SELECTION_DEAD"),
    }


# --- Direction C -----------------------------------------------------------


def direction_c() -> dict:
    """Direction C: can a different portfolio construction beat the baseline?

    Sixteen cells across four dimensions (sizing, concentration, holding,
    allocation), each compared to the baseline on pooled out-of-sample
    monthly Sharpe with a Benjamini-Hochberg false-discovery correction.
    """
    d = _load("edge_c")
    cells = []
    for c in d["cells"]:
        cells.append({
            "cell_id": c["cell_id"],
            "dimension": c["dimension"],
            "level": c["level"],
            "sharpe": c["pooled"]["sharpe"],
            "cagr_pct": c["pooled"]["cagr_pct"],
            "maxDD_pct": c["pooled"]["maxDD_pct"],
            "exposure_pct": c["exposure_pct"],
            "bh_q": c["bh_q"],
            "significant": c["significant"],
            "favours": c["point_estimate_favours_cell_on_sharpe"],
            "void": c["bootstrap_void"],
            "void_reason": c.get("bootstrap_void_reason", ""),
        })
    b = d["baseline"]
    return {
        "source": SOURCES["edge_c"],
        "spec": d.get("spec", ""),
        "amendment": d.get("amendment", ""),
        "n_cells": len(cells),
        "n_void": sum(1 for c in cells if c["void"]),
        "n_significant": sum(1 for c in cells if c["significant"]),
        "n_favours_and_significant": sum(1 for c in cells
                                         if c["favours"] and c["significant"]),
        "baseline_sharpe": b["pooled"]["sharpe"],
        "baseline_cagr_pct": b["pooled"]["cagr_pct"],
        "baseline_maxDD_pct": b["pooled"]["maxDD_pct"],
        "baseline_exposure_pct": b["exposure_pct"],
        "baseline_n_months": b["pooled"]["n_months"],
        "cells": cells,
        "closest": min((c for c in cells if not c["void"] and c["favours"]),
                        key=lambda c: c["bh_q"], default=None),
        "verdict": d.get("verdict", ""),
    }


# --- what still holds ------------------------------------------------------


def still_holds() -> dict:
    """The part that DID survive: per-trade results, and the close-confirmation
    test, each with its own placebo.

    This matters as much as the failures. "The signal has no edge" and "the
    portfolio construction cannot beat equal weight" are two different
    claims, and the first one is not supported here.
    """
    ab = _load("ab_close_hold")
    cells = {}
    for mkt, arms in ab["windows"].items():
        for arm, res in arms.items():
            for win, met in res.items():
                if not isinstance(met, dict):
                    continue
                cells.setdefault(mkt, {}).setdefault(arm, {})[win] = met
    placebo = ab["placebo"]
    sig = _load("v2_signals")
    oos = sig.get("setup_oos") or {}
    return {
        "source_ab": SOURCES["ab_close_hold"],
        "source_signals": SOURCES["v2_signals"],
        "trials": placebo["trials"],
        "seed": placebo["seed"],
        "z_crit": placebo["z_crit"],
        "cells_passing": placebo["cells_passing"],
        "verdict": placebo["verdict"],
        "arms": {m: {a: {w: {"n": m2["n"], "win%": m2["win%"],
                              "avg%": m2["avg%"], "pf": m2["pf"], "t": m2["t"]}
                          for w, m2 in wins.items()}
                     for a, wins in arms.items()}
                 for m, arms in cells.items()},
        "placebo_cells": placebo["cells"],
        "oos": oos,
    }


# --- the trial ledger ------------------------------------------------------


def trial_ledger() -> list:
    """Every configuration that was tested, counted from the report files.

    The point of a ledger is that the reader can add it up themselves. Each
    row names the file the count came from, so nothing here is a claim about
    a number somebody typed.

    The Sprint 1 placebo re-run does NOT get its own row: it re-scores the
    SAME 11 variations with a different bar and 300 trials instead of 30. It
    gave the data no new chance to look good, so counting it would inflate
    the multiple-testing burden the ledger is supposed to measure. It is
    stated in the Sprint 1 row's outcome instead.
    """
    s1 = sprint1()
    s2 = sprint2()
    s3 = sprint3()
    da = direction_a()
    dc = direction_c()
    return [
        {
            "name": "Sprint 1 — 11 個變體 × 成本情境",
            "count": s1["n_cells_scored"],
            "unit": "個設定",
            "source": SOURCES["s1_summary"],
            "outcome": (f"placebo 重跑 {s1['trials']} trials 後，"
                        f"{s1['n_clearing']} 個過到門檻"),
        },
        {
            "name": "Sprint 2 — crossed randomization 三軸",
            "count": s2["n_axes"],
            "unit": "條軸",
            "source": f"{SOURCES['s2_axis_a']}, {SOURCES['s2_axis_b']}, {SOURCES['s1_placebo']}",
            "outcome": f"{s2['n_failing']} 條 FAIL",
        },
        {
            "name": "Sprint 3 — deployment-matched benchmark",
            "count": s3["n_arms"],
            "unit": "條臂",
            "source": SOURCES["s3"],
            "outcome": f"{s3['n_passing']} 條過關",
        },
        {
            "name": "Direction A — fill-matched 選股（臂 × fill lag）",
            "count": da["n_arms_total"],
            "unit": "條臂",
            "source": f"{SOURCES['edge_a']}, {SOURCES['edge_a_lag1']}",
            "outcome": f"{da['n_all_dead']} 條 SELECTION_DEAD",
        },
        {
            "name": "Direction C — 組合構造 OFAT",
            "count": dc["n_cells"],
            "unit": "個 cell",
            "source": SOURCES["edge_c"],
            "outcome": f"{dc['n_void']} 個 void、{dc['n_significant']} 個顯著",
        },
    ]


def ledger_total() -> int:
    return sum(r["count"] for r in trial_ledger())

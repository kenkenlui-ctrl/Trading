#!/usr/bin/env python3
"""ONE COMMAND for the whole short-side / VCP research package.

    python3 scripts/research/run_short_side.py

Writes into research/2026-11-15-short-side/:

    summary.md                     the verdict-first report
    summary.json                   the same content, machine-readable
    short_side_summary.csv         every (market, variant, window, cost) cell
    short_side_trades.csv          every short trade (primary variant)
    short_side_trades_long.csv     every published long v2 signal, with the
                                   extension features joined on
    short_side_market_filter.csv   the market-filter sensitivity arm
    short_side_events.csv          the event-dominance arm
    short_side_gates.json          every gate result, machine-checked
    extended_reach_thresholds.csv  the nine candidate extension thresholds
    extended_reach_trades.csv      the published v2 signals with features
    vcp_experiment_e1.csv          the VCP breakout long, every combination
    vcp_experiment_e2.csv          the two short definitions x borrow fees
    vcp_experiment_e3.csv          the five filters on the published signals
    vcp_experiment_trades_e1.csv   the E1 trades
    vcp_experiment_trades_e2.csv   the E2 trades

NOTHING HERE TOUCHES PRODUCTION. No commit, no push, no deploy. Every
research flag stays OFF. The only files this writes are under
scripts/research/ (new) and research/2026-11-15-short-side/ (new).
"""
from __future__ import annotations

import csv
import json
import subprocess
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO / "scripts" / "v2engine"))
sys.path.insert(0, str(REPO / "scripts"))
sys.path.insert(0, str(REPO / "scripts" / "research"))

import extended_reach as ER        # noqa: E402
import short_side as SS            # noqa: E402
import vcp as V                    # noqa: E402
from report import write_report    # noqa: E402

OUT = REPO / "research" / "2026-11-15-short-side"
MARKETS = SS.MARKETS
WINDOWS = ("train", "test", "full")
COSTS = ("site", "realistic")
COST_LABEL = {
    "site": "site (fees only, ZERO slippage) = the published v2 caliber",
    "realistic": "realistic (fees + slippage) - NOT comparable with site",
}
PRIMARY_SHORT = SS.PRIMARY_SHORT
PRIMARY_E1 = "E1_cwide_rsoff_close_noLiq"


def wcsv(path: Path, rows: list, cols=None) -> None:
    """Write a CSV. Anything over 1 MB is gzipped.

    The same rule the 2026-11-11 package used: a 38 MB CSV is a poor thing
    to put in a git repository, and every one of these files is regenerated
    in under a minute by one command. The gzip name is reported so the
    reader knows what to look for.
    """
    import gzip
    import io
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        print("  -> " + str(path.relative_to(REPO)) + "  (empty)")
        return
    cols = cols or list(rows[0].keys())
    buf = io.StringIO()
    wr = csv.DictWriter(buf, fieldnames=cols, extrasaction="ignore")
    wr.writeheader()
    for r in rows:
        wr.writerow(r)
    data = buf.getvalue().encode("utf-8")
    if len(data) > 1_000_000:
        gz = path.with_suffix(path.suffix + ".gz")
        with gzip.open(gz, "wb") as fh:
            fh.write(data)
        print("  -> " + str(gz.relative_to(REPO)) + "  (" + str(len(rows))
              + " rows, gzipped from " + str(round(len(data) / 1e6, 1)) + " MB)")
    else:
        path.write_bytes(data)
        print("  -> " + str(path.relative_to(REPO)) + "  (" + str(len(rows)) + " rows)")


def win_rows(rows: list, win: str) -> list:
    return rows if win == "full" else [r for r in rows if SS.window_of(r["date"]) == win]


def run_part_a():
    print("")
    print("[1/6] the engine - long v2 re-run + the short mirror")
    t0 = time.time()
    mask = SS.load_mask()
    engine = {}
    for mkt in MARKETS:
        engine[mkt] = SS.run_engine(mkt, mask)
        for v in list(engine[mkt]):
            engine[mkt][v] = SS.add_costs(engine[mkt][v], mkt)
        print("      " + mkt + ": " + format(len(engine[mkt][PRIMARY_SHORT]), ",")
              + " short trades (" + PRIMARY_SHORT + "), "
              + format(len(engine[mkt]["L-long-v2"]), ",") + " long trades")
    print("      engine pass: " + format(time.time() - t0, ".1f") + "s")
    long_rows = [r for mkt in MARKETS for r in engine[mkt]["L-long-v2"]]
    short_rows = [r for mkt in MARKETS for r in engine[mkt][PRIMARY_SHORT]]
    return {"engine": engine, "long": long_rows, "short": short_rows, "mask": mask}


def run_gates(part_a):
    print("")
    print("[2/6] the gates")
    frozen = json.loads(SS.FROZEN.read_text(encoding="utf-8"))
    gates = {}
    gates["frozen_ab_close_hold"] = SS.check_frozen_gate(part_a["long"], frozen)
    fg = gates["frozen_ab_close_hold"]
    print("      frozen gate: " + ("PASS" if fg["passed"] else "FAIL")
          + " (" + str(fg["n_checks"]) + " checks, "
          + str(fg["n_failed"]) + " failed)")
    for mkt in MARKETS:
        g = SS.check_engine_equivalence(part_a["long"], mkt)
        gates["engine_equivalence_" + mkt] = g
        print("      engine equivalence " + mkt + ": "
              + ("PASS" if g["passed"] else "FAIL"))
    gates["symmetry_negative_control"] = SS.check_symmetry_breach(
        part_a["long"], part_a["short"])
    sc = gates["symmetry_negative_control"]
    print("      symmetry negative control: "
          + ("PASS" if sc["passed"] else "FAIL"))
    gates["tamper_must_fail"] = SS.tamper_gate_arm(part_a["long"])
    tm = gates["tamper_must_fail"]
    print("      tamper must-fail: " + ("PASS" if tm["passed"] else "FAIL")
          + " (tamper landed = " + str(tm["tamper_landed_on_disk"])
          + ", original untouched = " + str(tm["original_file_untouched"]) + ")")
    gates["no_lookahead"] = SS.no_lookahead_probe()
    nl = gates["no_lookahead"]
    print("      no-look-ahead probe: "
          + ("PASS" if nl["passed"] else "FAIL"))
    gates["no_target_sentinel"] = SS.no_target_probe()
    nt = gates["no_target_sentinel"]
    print("      no-target sentinel: " + ("PASS" if nt["passed"] else "FAIL"))
    gates["all_passed"] = all(
        g.get("passed", True) for k, g in gates.items() if isinstance(g, dict))
    return gates


def build_a_tables(part_a):
    summary_rows = []
    for mkt in MARKETS:
        for variant, rows in part_a["engine"][mkt].items():
            for win in WINDOWS:
                sub = win_rows(rows, win)
                for caliber in COSTS:
                    col = "net_site" if caliber == "site" else "net_realistic"
                    st = SS.stats_for(sub, cost_col=col)
                    rec = {
                        "market": mkt, "variant": variant,
                        "variant_label": SS.VARIANTS[variant]["label"],
                        "window": win, "cost_caliber": caliber,
                        "cost_note": COST_LABEL[caliber],
                    }
                    for k in ("n", "avg%", "win%", "pf", "t", "avg_gross%",
                              "avg_fwd10%", "n_stop", "n_time",
                              "sharpe_per_trade", "sharpe_daily_clustered",
                              "max_dd_per_trade_series", "sum_net",
                              "sum_net_ex_best5"):
                        rec[k] = st[k]
                    summary_rows.append(rec)
    return summary_rows


def market_filter_rows(part_a):
    out = []
    for mkt in MARKETS:
        for variant, rows in part_a["engine"][mkt].items():
            kept, dropped = SS.apply_market_filter(
                rows, mkt, SS.VARIANTS[variant]["side"])
            for win in WINDOWS:
                st = SS.stats_for(win_rows(kept, win), cost_col="net_site")
                base = SS.stats_for(win_rows(rows, win), cost_col="net_site")
                out.append({
                    "market": mkt, "variant": variant, "window": win,
                    "n_without_filter": base["n"],
                    "avg%_without": base["avg%"],
                    "n_with_filter": st["n"], "avg%_with": st["avg%"],
                    "win%_with": st["win%"], "t_with": st["t"],
                    "n_dropped_no_verdict": dropped,
                    "note": "the frozen long engine has NO per-trade market "
                            "filter; this arm adds one on top (mirrored)",
                })
    return out


def event_rows(part_a):
    out = []
    for mkt in MARKETS:
        days = SS.event_days(mkt, 5)
        for variant, rows in part_a["engine"][mkt].items():
            kept = SS.exclude_events(rows, days)
            for win in WINDOWS:
                a = SS.stats_for(win_rows(rows, win), cost_col="net_site")
                b = SS.stats_for(win_rows(kept, win), cost_col="net_site")
                out.append({
                    "market": mkt, "variant": variant, "window": win,
                    "event_days": ",".join(d["date"] for d in days),
                    "n_with": a["n"], "avg%_with": a["avg%"],
                    "win%_with": a["win%"],
                    "n_without": b["n"], "avg%_without": b["avg%"],
                    "win%_without": b["win%"],
                    "note": "the largest single-session index moves in the "
                            "store; the store ends 2026-10-01 so there is NO "
                            "Nov-2026 rout in it",
                })
    return out


def run_part_b(part_a):
    print("")
    print("[3/6] part B - how much of the published signal set is extended")
    rows = ER.attach_features(part_a["long"])
    res = ER.evaluate(rows)
    print("      " + format(len(rows), ",")
          + " published v2 signals carry the extension features")
    return {"rows": rows, "results": res}


def run_vcp(part_a, mask):
    print("")
    print("[4/6] VCP - E1 (breakout long), E2 (short), E3 (filter)")
    t0 = time.time()
    index_rocs = {mkt: V.index_roc_map(mkt) for mkt in MARKETS}
    e1_rows, e2_rows = [], []
    e1_trades, e2_trades = [], []
    for mkt in MARKETS:
        combos = V.e1_combos()
        res1 = V.e1_run(mkt, mask, combos)
        meta = {c["name"]: c for c in combos}
        for name, rows in res1.items():
            rows = V.add_costs(rows, mkt, +1)
            e1_trades += rows
            for win in WINDOWS:
                sub = win_rows(rows, win)
                for caliber in COSTS:
                    col = "net_site" if caliber == "site" else "net_realistic"
                    st = SS.stats_for(sub, cost_col=col)
                    rec = {
                        "experiment": "E1", "market": mkt, "combo": name,
                        "contract": meta[name]["contract"],
                        "rs": "on" if meta[name]["rs"] else "off",
                        "fill": meta[name]["fill"],
                        "liquidity": meta[name]["liquidity"],
                        "is_primary": name == PRIMARY_E1,
                        "window": win, "cost_caliber": caliber,
                    }
                    for k in ("n", "avg%", "win%", "pf", "t", "avg_fwd10%",
                              "n_stop", "sharpe_per_trade",
                              "max_dd_per_trade_series"):
                        rec[k] = st[k]
                    e1_rows.append(rec)
        combos2 = V.e2_combos()
        res2 = V.e2_run(mkt, mask, combos2)
        for name, rows in res2.items():
            e2_trades += rows
            for rate in V.BORROW_RATES:
                rows2 = V.add_costs(rows, mkt, -1, rate)
                for win in WINDOWS:
                    sub = win_rows(rows2, win)
                    st = SS.stats_for(sub, cost_col="net_site_borrow")
                    st_r = SS.stats_for(sub, cost_col="net_realistic_borrow")
                    nov_in = [r for r in sub if r["date"] < V.NOV2026_START]
                    st_nov = SS.stats_for(nov_in, cost_col="net_site_borrow")
                    e2_rows.append({
                        "experiment": "E2", "market": mkt, "rule": name,
                        "borrow_rate": rate, "window": win,
                        "cost_caliber": "site+borrow",
                        "n": st["n"], "avg%": st["avg%"], "win%": st["win%"],
                        "pf": st["pf"], "t": st["t"],
                        "avg%_realistic": st_r["avg%"],
                        "avg_fwd10%": st["avg_fwd10%"],
                        "sharpe_per_trade": st["sharpe_per_trade"],
                        "max_dd_per_trade_series": st["max_dd_per_trade_series"],
                        "n_excl_nov2026": st_nov["n"],
                        "avg%_excl_nov2026": st_nov["avg%"],
                        "note": "the frozen store ends 2026-10-01, so the "
                                "Nov-2026 window is EMPTY and the two arms "
                                "are identical by construction",
                    })
    print("      E1+E2 pass: " + format(time.time() - t0, ".1f") + "s")

    print("      E3 - the filters on the published v2 signals")
    e3_rows = V.e3_apply(part_a["long"], index_rocs)
    e3_res = []
    for mkt in list(MARKETS) + ["all"]:
        for win in WINDOWS:
            sub = [r for r in e3_rows if (mkt == "all" or r["mkt"] == mkt)
                   and (win == "full" or SS.window_of(r["date"]) == win)]
            for fname in ("F1_trend", "F2_at_top", "F3_contract",
                          "F4_vol_dry", "F5_rs"):
                p = [r for r in sub if r[fname]]
                f = [r for r in sub if not r[fname]]
                sp = SS.stats_for(p, cost_col="net_site")
                sf = SS.stats_for(f, cost_col="net_site")
                e3_res.append({
                    "filter": fname, "market": mkt, "window": win,
                    "n_total": len(sub), "n_pass": sp["n"],
                    "pass_rate_pct": round(sp["n"] / max(1, len(sub)) * 100, 2),
                    "pass_avg%": sp["avg%"], "pass_win%": sp["win%"],
                    "pass_t": sp["t"], "pass_avg_fwd10%": sp["avg_fwd10%"],
                    "fail_avg%": sf["avg%"], "fail_win%": sf["win%"],
                    "fail_t": sf["t"], "fail_avg_fwd10%": sf["avg_fwd10%"],
                    "delta_avg_pp": round(sp["avg%"] - sf["avg%"], 3),
                    "delta_win_pp": round(sp["win%"] - sf["win%"], 1),
                    "marginal": bool(sp["n"] < 30 or sf["n"] < 30),
                })
            f1 = [r for r in sub if r["F1_trend"]]
            for flag, label in (("at_top", "F1_AND_at_top (XS 25%-of-52w-high rule)"),
                                ("near_52w_high_3pct", "F1_AND_near_52w_high_3pct"),
                                ("near_52w_high_1pct", "F1_AND_near_52w_high_1pct"),
                                ("near_box_top_2pct", "F1_AND_near_box_top_2pct")):
                top = [r for r in f1 if r.get(flag)]
                ntop = [r for r in f1 if not r.get(flag)]
                st = SS.stats_for(top, cost_col="net_site")
                sn = SS.stats_for(ntop, cost_col="net_site")
                e3_res.append({
                    "filter": label,
                    "market": mkt, "window": win, "n_total": len(sub),
                    "n_pass": st["n"],
                    "pass_rate_pct": round(st["n"] / max(1, len(f1)) * 100, 2),
                    "pass_avg%": st["avg%"], "pass_win%": st["win%"], "pass_t": st["t"],
                    "pass_avg_fwd10%": st["avg_fwd10%"],
                    "fail_avg%": sn["avg%"], "fail_win%": sn["win%"], "fail_t": sn["t"],
                    "fail_avg_fwd10%": sn["avg_fwd10%"],
                    "delta_avg_pp": round(st["avg%"] - sn["avg%"], 3),
                    "delta_win_pp": round(st["win%"] - sn["win%"], 1),
                    "marginal": bool(st["n"] < 30 or sn["n"] < 30),
                })
    return {"e1_rows": e1_rows, "e2_rows": e2_rows, "e3_rows": e3_rows,
            "e3_res": e3_res, "e1_trades": e1_trades, "e2_trades": e2_trades}


def production_untouched():
    """The strongest form of the claim: nothing outside scripts/research/ and
    research/ changed."""
    out = subprocess.run(
        ["git", "-C", str(REPO), "diff", "--name-only", "HEAD", "--",
         "public", "scripts/build_v2_signals.py", "scripts/v2engine",
         "config/integrity_flags.json", "scripts/build_home.py",
         "scripts/build_static.py", "scripts/rewrite_methodology_v2.py",
         "scripts/ab_close_hold.py", "data/ab_close_hold.json",
         "data/bt10y_glitch_mask.json"],
        capture_output=True, text=True, timeout=120)
    changed = [x for x in (out.stdout or "").splitlines() if x.strip()]
    st = subprocess.run(["git", "-C", str(REPO), "status", "--short"],
                        capture_output=True, text=True, timeout=120)
    status = [x for x in (st.stdout or "").splitlines() if x.strip()]
    return {"protected_paths_changed": changed, "git_status": status,
            "passed": not changed}


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    t_start = time.time()

    part_a = run_part_a()
    gates = run_gates(part_a)
    a_summary = build_a_tables(part_a)
    mf_rows = market_filter_rows(part_a)
    ev_rows = event_rows(part_a)
    part_b = run_part_b(part_a)
    vcp_res = run_vcp(part_a, part_a["mask"])
    prod = production_untouched()

    print("")
    print("[5/6] writing the artifacts")
    wcsv(OUT / "short_side_summary.csv", a_summary)
    wcsv(OUT / "short_side_trades.csv", part_a["short"])
    wcsv(OUT / "short_side_trades_long.csv", part_b["rows"])
    wcsv(OUT / "short_side_market_filter.csv", mf_rows)
    wcsv(OUT / "short_side_events.csv", ev_rows)
    (OUT / "short_side_gates.json").write_text(
        json.dumps(gates, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8")
    print("  -> " + str((OUT / "short_side_gates.json").relative_to(REPO)))
    wcsv(OUT / "extended_reach_thresholds.csv", part_b["results"])
    wcsv(OUT / "extended_reach_trades.csv", part_b["rows"])
    wcsv(OUT / "vcp_experiment_e1.csv", vcp_res["e1_rows"])
    wcsv(OUT / "vcp_experiment_e2.csv", vcp_res["e2_rows"])
    wcsv(OUT / "vcp_experiment_e3.csv", vcp_res["e3_res"])
    wcsv(OUT / "vcp_experiment_trades_e1.csv",
         [dict(r, rule=PRIMARY_E1) for r in vcp_res["e1_trades"]])
    wcsv(OUT / "vcp_experiment_trades_e2.csv", vcp_res["e2_trades"])

    payload = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "bars_dir": SS.BARS.name,
        "split": SS.SPLIT.strftime("%Y-%m-%d"),
        "hold_days": SS.HOLD,
        "cost_calibers": dict(COST_LABEL),
        "cost_model": {
            "site": "cost_rt(mkt,'site',stop_exit) = fees only, ZERO "
                    "slippage (us200 0.05%, jp200 0.10% round trip) = the "
                    "caliber the published v2 numbers are measured at",
            "realistic": "cost_rt(mkt,'realistic',stop_exit) = fees + "
                          "SLIP_SIDE per side (us200 0.0003, jp200 0.0005) + "
                          "STOP_EXTRA on a stop exit. NOT comparable with site",
        },
        "borrow_cost_model": "borrow fee = annual rate * held_sessions / 252, "
                             "charged on the short legs only; rates 0 / 10% / 30%",
        "gates": gates,
        "part_a_summary": a_summary,
        "market_filter_sensitivity": mf_rows,
        "event_dominance": ev_rows,
        "part_b_results": part_b["results"],
        "vcp_e1": vcp_res["e1_rows"],
        "vcp_e2": vcp_res["e2_rows"],
        "vcp_e3": vcp_res["e3_res"],
        "multiple_comparisons": {
            "short_rule_variants": len(SS.SHORT_VARIANTS),
            "extension_thresholds": len(ER.THRESHOLDS),
            "vcp_e1_combinations": len(V.e1_combos()),
            "vcp_e2_rules": len(V.e2_combos()),
            "vcp_e2_borrow_rates": len(V.BORROW_RATES),
            "vcp_e3_filters": 5,
            "cost_calibers": 2,
            "windows": 3,
            "markets": 2,
        },
        "production_untouched": prod,
    }
    (OUT / "summary.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8")
    print("  -> " + str((OUT / "summary.json").relative_to(REPO)))

    print("")
    print("[6/6] the report")
    write_report(OUT, payload, gates, a_summary, mf_rows, ev_rows,
                 part_b["results"], vcp_res, prod)

    print("")
    print("done in " + format(time.time() - t_start, ".1f") + "s")
    ok = gates["all_passed"] and prod["passed"]
    print("gates: " + ("ALL PASS" if ok else "SOMETHING FAILED"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())

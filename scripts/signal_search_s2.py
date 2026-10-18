"""signal_search_s2.py — crossed randomization: where does the edge leak out of?

Sprint 1 varied one factor at a time (OFAT) across 11 cells and none cleared its
own placebo null. Adding more cells in the same family repeats what the family
already answered. This module changes the shape: hold everything at its real
value and randomize exactly ONE component, so each component gets its own null.

    axis C — entry timing    : Sprint 1, already done, negative (z -4.65)
    axis A — stock selection : this file
    axis B — regime gate     : spec written, not implemented

AXIS A. Keep the entry TIMING exactly as v2 has it. For every real signal, take
another stock that would also have produced a valid signal on that same session,
drawn at random from the session's eligible set. Universe, market filter, entry
level, stop, exit, costs, sizing and holding period are all untouched; only
WHICH STOCK changes.

If v2's stock selection carries information, the real rule beats this null. If
the null matches or beats the real rule, then the S1 screen is not selecting
good stocks — consistent with axis C, which says it is not selecting good
moments either.

Pre-registered in docs/sprint-2-crossed-randomization-prereg-2026-10-19.md.
Nothing here touches production flags; Sprint 2's flags all remain OFF.

Deployment matching is a PASS CRITERION, not a nicety. Sprint 1's placebo
executed ~38% more trades than the real rule because it fixed the pre-fill
signal count rather than the executed trade count, and that contributed ~0.9pp
to its null. Axis A naturally holds the trade count near the real run (a
session with only one eligible stock keeps the real one), but trades and
exposure are recorded per trial anyway so the criterion can be checked rather
than assumed.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from dataclasses import asdict
from pathlib import Path

import numpy as np

REPO = Path("/Users/kenken/dev/dsa-hk")
sys.path.insert(0, str(REPO / "scripts" / "v2engine"))
sys.path.insert(0, str(REPO / "scripts"))
from core import _simulate, cost_rt  # noqa: E402
import signal_search_s1 as s1  # noqa: E402

Z_CRIT = 1.96


def build_pool(px: dict, feats: dict, cfg: "s1.Cfg", mkt_up) -> tuple[list, dict]:
    """Real candidates, plus a session -> every-eligible-stock index.

    _simulate runs ONCE per candidate here rather than once per trial. Axis A
    only re-picks between already-simulated trades, so unlike the timing
    placebo (which changes the bar and must re-simulate) it is cheap per trial.

    Returns:
      real  - the candidates v2 actually produces, untouched
      pool  - {entry_date: [candidate_dict, ...]} for every candidate that
              traded on that date, whichever symbol it came from
    """
    real: list[dict] = []
    pool: dict = defaultdict(list)
    for sym, b in px.items():
        f = feats[sym]
        mu = mkt_up.reindex(b.index).fillna(False).to_numpy(bool)
        O, H, L, C = (b[k].to_numpy(float) for k in ("open", "high", "low", "close"))
        for i, entry, stop, r5 in s1.signals(b, f, cfg, mu):
            r = _simulate(1, i, entry, stop, 1e12, O, H, L, C, cfg.hold, True)
            if r is None:
                continue                      # ran out of bars
            j, xp, why = r
            c = {"sym": sym, "entry_date": b.index[i], "exit_date": b.index[j],
                 "entry": entry, "stop": stop, "exit": xp, "why": why,
                 "ret5d": r5}
            real.append(c)
            pool[b.index[i]].append(c)
    return real, dict(pool)


def build_universe_pool(px: dict, feats: dict, cfg: "s1.Cfg") -> dict:
    """Per-date candidates from the WHOLE universe, screen deliberately removed.

    This is the null that actually tests axis A. `build_pool` cannot: it indexes
    the screen's own output, and v2 admits essentially every qualifying stock —
    median 2 names per entry session — so a within-date shuffle of that pool
    permutes a multiset that `walk()` already sees unchanged. Measured, that
    null has sd = 0.000 and reproduces the real result to the decimal: a placebo
    that tests nothing while looking like a clean pass. The same trap as the
    Sprint 1 `rng.permutation`.

    Here the only remaining requirements are data validity (s1 and atr exist,
    at least 250 bars of history) and the ENTRY MECHANICS — the limit fills,
    the close holds, a stop is derivable. The regime gate, the ATR cap and the
    box cap are all removed, because those ARE the stock-selection rule under
    test. What survives is: same session, same entry mechanics, same stop and
    exit, same sizing — only "which stock" is random.
    """
    pool: dict = defaultdict(list)
    for sym, b in px.items():
        f = feats[sym]
        O, H, L, C = (b[k].to_numpy(float) for k in ("open", "high", "low", "close"))
        # local must not be named `s1` — that shadows the signal_search_s1
        # module import used two lines below.
        s1v = s1.s1_series(b, cfg.s1_lb).to_numpy()
        atr = f.atr_pct.to_numpy()
        usable = (~np.isnan(s1v) & ~np.isnan(atr) & (np.arange(len(s1v)) >= 250))
        for i in np.where(usable)[0]:
            if not (O[i] > s1v[i] and L[i] <= s1v[i]):
                continue
            if cfg.close_conf and C[i] < s1v[i]:
                continue
            stop = s1v[i] * (1 - cfg.stop_atr * atr[i])
            if not (0 < stop < s1v[i]):
                continue
            r = _simulate(1, i, s1v[i], stop, 1e12, O, H, L, C, cfg.hold, True)
            if r is None:
                continue
            j, xp, why = r
            pool[b.index[i]].append(
                {"sym": sym, "entry_date": b.index[i], "exit_date": b.index[j],
                 "entry": s1v[i], "stop": stop, "exit": xp, "why": why,
                 "ret5d": f.ret_5d.to_numpy()[i]})
    return dict(pool)


def placebo_axis_a(real: list, pool: dict, ordinal: dict, cost: float,
                   ew_cagr: dict, trials: int, seed: int,
                   oversample: float = 1.0) -> dict:
    """At each real signal session, trade a random UNIVERSE name instead.

    `oversample` draws more names per date than the real run used, to offset the
    fact that a random name fills its S1 limit far less often than a screened
    one. Executed trades and exposure are recorded so the deployment match in
    the pre-registration can be checked rather than assumed.

    `ew_cagr` is subtracted here so the stored series is a GAP like `real_gap`.
    An earlier version stored raw CAGR and `score()` compared it against a gap —
    off by the whole benchmark (~26pp/yr), which made the null look like it beat
    the rule by 25pp/yr. The identity control is what caught it.
    """
    rng = np.random.default_rng(seed)
    by_date: dict = {}
    for rc in real:
        by_date.setdefault(rc["entry_date"], []).append(rc)

    gaps = {w: [] for w in s1.WINDOWS}
    trades, exposure = [], []
    for _ in range(trials):
        cands = []
        for dt, rcs in by_date.items():
            alts = pool.get(dt)
            if not alts:
                cands.extend(rcs)              # date has no universe candidate
                continue
            n = max(1, int(round(len(rcs) * oversample)))
            idx: list[int] = []
            while len(idx) < n:
                idx.extend(int(x) for x in rng.permutation(len(alts)))
            for j in idx[:n]:
                cands.append({**alts[j], "why": "p"})
        curve, meta = s1.walk(cands, cost, ordinal)
        trades.append(meta["trades"])
        exposure.append(meta["avg_exposure"] * 100)
        for w in s1.WINDOWS:
            gaps[w].append(s1.window_stats(curve, w)["cagr%"] - ew_cagr[w])
    return {"gaps": gaps, "trades": trades, "exposure": exposure}


def deployment_ok(real_trades: int, real_exposure: float, trades: list,
                  exposure: list, tol_trades: float = 0.05,
                  tol_exposure: float = 5.0) -> tuple[bool, str]:
    """The VOID rule. A null that deploys differently is not evidence."""
    mt, me = float(np.mean(trades)), float(np.mean(exposure))
    dt = (mt - real_trades) / real_trades * 100 if real_trades else 999.0
    de = me - real_exposure
    ok = abs(dt) <= tol_trades * 100 and abs(de) <= tol_exposure
    return ok, (f"null trades {mt:.0f} ({dt:+.1f}% vs real {real_trades}), "
                f"exposure {me:.1f}% ({de:+.1f}pp vs real {real_exposure:.1f}%)")


def score(real_gap: dict, null: dict, z_crit: float = Z_CRIT) -> dict:
    out = {}
    for w in ("2016-2020", "2021-2026"):
        arr = np.asarray(null["gaps"][w], float)
        mu, sd = float(arr.mean()), float(arr.std(ddof=1))
        g = real_gap[w]
        z = (g - mu) / sd if sd > 0 else float("nan")
        out[w] = {"real_gap_pp": round(g, 2), "null_mean_pp": round(mu, 2),
                  "null_sd_pp": round(sd, 3), "net_pp": round(g - mu, 2),
                  "z": round(z, 3) if z == z else None,
                  "null_percentile": round(float((arr < g).mean() * 100), 1),
                  "n": int(len(arr)),
                  "passes": bool(sd > 0 and g - mu > 0 and z >= z_crit)}
    both = all(v["passes"] for v in out.values())
    return {"windows": out, "clears_bar": both,
            "worst_z": min(v["z"] for v in out.values()),
            "worst_net_pp": min(v["net_pp"] for v in out.values()),
            "z_crit": z_crit}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--market", default="us", choices=["us", "hk"])
    ap.add_argument("--trials", type=int, default=300)
    ap.add_argument("--seed", type=int, default=20261019)
    ap.add_argument("--cost", default="realistic", choices=["site", "realistic", "pess"])
    ap.add_argument("--config", default="baseline",
                    choices=["baseline"], help="axis A is defined on the shipped config")
    ap.add_argument("--oversample", type=float, default=1.0,
                    help="draw N x real candidates per date; >1 breaks the "
                         "deployment match and the cell is VOID by the rule")
    a = ap.parse_args()

    bars_dir, ipath, _, mkt_key = s1.BARS[a.market]
    px, idx_close, syms = s1.load_market(a.market)
    if idx_close is None or len(px) < 50:
        print(f"FATAL: {a.market} data unusable")
        return 2
    mkt_up = s1.market_up_series(idx_close).dropna()
    feats = {s: s1.base_features(b) for s, b in px.items()}
    ew = s1.ew_bh(px)
    ordinal = s1.calendar_ordinal(idx_close)
    cfg = s1.BASELINE
    scen = "realistic" if a.cost == "pess" else a.cost
    cost = cost_rt(mkt_key, scen, False)

    print(f"{a.market}: {len(px)} tickers, cost={scen} "
          f"({cost_rt(mkt_key, scen, True)*100:.3f}% rt w/ stop slide)")
    print(f"axis A — randomize STOCK, keep timing. {a.trials} trials, seed {a.seed}")

    real, screened_pool = build_pool(px, feats, cfg, mkt_up)
    curve, meta = s1.walk(real, cost, ordinal)
    ew_cagr = {w: s1.window_stats_series(ew, w)["cagr%"] for w in s1.WINDOWS}
    real_gap = {w: s1.window_stats(curve, w)["cagr%"] - ew_cagr[w] for w in s1.WINDOWS}
    multi = sum(1 for d, v in screened_pool.items() if len(v) > 1)
    print(f"\nreal: {meta['trades']} trades, exposure {meta['avg_exposure']*100:.1f}%, "
          f"gap {real_gap['2016-2020']:+.2f} / {real_gap['2021-2026']:+.2f}")
    print(f"screened pool: {len(screened_pool)} entry sessions, median "
          f"{np.median([len(v) for v in screened_pool.values()]):.0f}/session")

    # The null draws from the WHOLE universe, not the screen's own output.
    # Reason, measured: v2 admits essentially every qualifying name — median 2
    # per session — so shuffling within the screened pool permutes a multiset
    # walk() already sees unchanged. That null has sd = 0.000 and reproduces the
    # real result to the decimal: a placebo that tests nothing while reading as
    # a clean pass. Identical failure mode to Sprint 1's rng.permutation.
    upool = build_universe_pool(px, feats, cfg)
    print(f"universe pool: {len(upool)} dates, median "
          f"{np.median([len(v) for v in upool.values()]):.0f}/date")

    null = placebo_axis_a(real, upool, ordinal, cost, ew_cagr, a.trials, a.seed,
                          oversample=a.oversample)
    ok, why = deployment_ok(meta["trades"], meta["avg_exposure"] * 100,
                            null["trades"], null["exposure"])
    gate = score(real_gap, null)

    print(f"\n{'window':<12}{'real gap':>10}{'null mean':>11}{'null sd':>9}"
          f"{'net':>8}{'z':>8}{'pass':>7}")
    for w in ("2016-2020", "2021-2026"):
        v = gate["windows"][w]
        print(f"{w:<12}{v['real_gap_pp']:>10.2f}{v['null_mean_pp']:>11.2f}"
              f"{v['null_sd_pp']:>9.3f}{v['net_pp']:>8.2f}{v['z']:>8.2f}"
              f"{str(v['passes']):>7}")

    verdict = ("PASS" if ok and gate["clears_bar"]
               else "VOID (deployment mismatch)" if not ok
               else "FAIL")
    print(f"\ndeployment: {why}")
    print(f"VERDICT: {verdict}   (z_crit {Z_CRIT}, both sub-periods required)")
    if ok and not gate["clears_bar"]:
        print("Reading: stock selection does not beat a random eligible stock "
              "from the same session.")

    out = {"spec": "docs/sprint-2-crossed-randomization-prereg-2026-10-19.md",
           "axis": "A — stock selection", "market": a.market,
           "config": asdict(cfg), "cost_scenario": scen,
           "trials": a.trials, "seed": a.seed,
           "generated_at": __import__("datetime").datetime.now().isoformat(timespec="seconds"),
           "real": {"trades": meta["trades"],
                    "exposure_pct": round(meta["avg_exposure"] * 100, 2),
                    "gap_pp": {k: round(v, 3) for k, v in real_gap.items()}},
           "null": {"trades_mean": round(float(np.mean(null["trades"])), 1),
                    "trades_sd": round(float(np.std(null["trades"], ddof=1)), 2),
                    "exposure_mean_pct": round(float(np.mean(null["exposure"])), 2),
                    "exposure_sd_pct": round(float(np.std(null["exposure"], ddof=1)), 3)},
           "deployment_matched": ok, "deployment_note": why,
           "gate": gate, "verdict": verdict,
           "oversample": a.oversample,
           "screened_pool_sessions": len(screened_pool),
           "screened_pool_median_per_session":
               float(np.median([len(v) for v in screened_pool.values()])),
           "universe_pool_dates": len(upool)}
    rep = REPO / "reports"
    rep.mkdir(exist_ok=True)
    path = rep / f"signal_search_s2_{a.market}_axis_a.json"
    path.write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
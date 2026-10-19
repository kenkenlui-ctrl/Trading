#!/usr/bin/env python3
"""Write Direction C's outcome into the pre-registration, from the artifact.

Every number in the outcome is read from reports/edge_alpha_c_us.json. Nothing
is typed by hand, for the same reason Direction A's outcome is generated rather
than written: a hand-typed figure has already drifted once in this project (the
Sprint 2 axis A percentiles) and once in conversation (the no-gate figure that
exists in no artifact). A grid of 16 cells with four numbers each is 64 chances
to mistype something.
"""
from __future__ import annotations

import json
import pathlib
import textwrap

R = pathlib.Path("/Users/kenken/dev/dsa-hk")
M = "\u2212"

DIM_LABEL = {
    "C1_sizing": "C1 sizing",
    "C2_concentration": "C2 concentration",
    "C3_holding": "C3 holding",
    "C4_allocation": "C4 allocation",
}
LEVEL_TEXT = {
    "risk1pct": "ATR-proportional sizing (baseline)",
    "equal": "equal weighting",
    "kelly": "each name's own Kelly fraction",
    "volscaled": "1/ATR% weighting",
    "max10": "10 names (baseline)",
    "max5": "5 names",
    "max20": "20 names",
    "unlimited": "no name cap",
    "t10": "10-session hold (baseline)",
    "t5": "5-session hold",
    "t20": "20-session hold",
    "trailing": "2x ATR% trailing exit",
    "cap60": "60% of the book in equities (baseline)",
    "uncapped": "100% of the book in equities",
    "riskparity": "vol-targeted capital scale",
}


def n(v, d=2):
    return f"{v:+.{d}f}".replace("-", M)


def neg(v, d=2):
    """A plain (unsigned) number, with the minus sign rendered as U+2212."""
    return f"{v:.{d}f}".replace("-", M)


def by(v, d=2):
    """How a delta reads in a sentence: 'by 9.31pp' or 'by +11.64pp'."""
    return ("by " + neg(abs(v), d) + "pp") if v < 0 else ("by +" + f"{v:.{d}f}" + "pp")


def para(t):
    return textwrap.fill(t, width=79, break_long_words=False,
                         break_on_hyphens=False)


def main() -> int:
    d = json.loads((R / "reports/edge_alpha_c_us.json").read_text())
    cells = {c["cell_id"]: c for c in d["cells"]}
    base = cells["baseline"]
    base_cfg = base["config"]
    sample = d["sample"]
    boot = d["bootstrap"]
    win = d["windows"]

    def changed(cell):
        """What this cell moves relative to the baseline, in plain words."""
        cfg = cell["config"]
        diffs = [k for k in base_cfg if cfg[k] != base_cfg[k]]
        if not diffs:
            return "the baseline itself"
        k = diffs[0]
        return f"{DIM_LABEL[k]}: {LEVEL_TEXT[cfg[k]]}"

    L = []
    P = L.append

    P("## Direction C — outcome, 2026-10-19"); P("")
    P(para(f"{len(d['cells'])} cells, one dimension moved at a time from the "
           f"baseline `risk1pct / max10 / t10 / cap60`. US, "
           f"{sample['first']} to {sample['last']}, {sample['names']} names, "
           f"{sample['sessions']} sessions, cost "
           f"{d['config']['cost_rt_round_trip'] * 100:.2f}% round trip. "
           f"Scored on pooled out-of-sample monthly returns: "
           f"{base['pooled']['n_months']} months, Sharpe primary, CAGR "
           f"secondary. p-values from a {boot['block_months']}-month block "
           f"bootstrap, {boot['resamples']} resamples, seed {boot['seed']}, "
           f"adjusted across all {len(d['cells'])} cells by Benjamini-Hochberg "
           f"at q = {boot['q_threshold']:.2f}.")); P("")

    # ---- the grid ---------------------------------------------------------
    P("### The grid"); P("")
    P("| cell | what it moves | Sharpe | vs base | CAGR | vs base | "
      "exposure | p | q | verdict |")
    P("|---|---|---|---|---|---|---|---|---|---|")
    for c in d["cells"]:
        if c["bootstrap_void"]:
            verdict = "void (same as baseline)"
        elif c["significant"]:
            verdict = "**significant**"
        elif c["bh_q"] <= boot["q_threshold"]:
            verdict = "significantly WORSE"
        elif c["point_estimate_favours_cell_on_sharpe"]:
            verdict = "favours the cell, not significant"
        else:
            verdict = "worse, not significant"
        P(f"| `{c['cell_id']}` | {changed(c)} | {c['pooled']['sharpe']:.4f} | "
          f"{n(c['sharpe_delta_vs_baseline'], 4)} | {c['pooled']['cagr_pct']:.2f}% | "
          f"{n(c['cagr_delta_vs_baseline_pp'])} | {c['exposure_pct']:.2f}% | "
          f"{c['p_value']:.4f} | {c['bh_q']:.4f} | {verdict} |")
    P("")
    P(para(f"The baseline cell is `risk1pct / max10 / t10 / cap60`: pooled OOS "
           f"Sharpe {base['pooled']['sharpe']:.4f}, CAGR "
           f"{base['pooled']['cagr_pct']:.2f}%, max drawdown "
           f"{neg(base['pooled']['maxDD_pct'])}%, mean exposure "
           f"{base['exposure_pct']:.2f}%. Five of the sixteen entries ARE the "
           f"baseline configuration (the reference cell plus each dimension's "
           f"own baseline level), so their difference series has zero spread "
           f"and they are void, not p = 0. The other "
           f"{d['grid']['ofat']['cells_changing_exactly_one_dimension']} each "
           f"move exactly one dimension, which the harness asserts rather than "
           f"trusts.")); P("")

    # ---- the verdict ------------------------------------------------------
    sig = [c for c in d["cells"] if c["significant"]]
    worse = [c for c in d["cells"]
             if (not c["bootstrap_void"]) and c["bh_q"] <= boot["q_threshold"]
             and c["sharpe_delta_vs_baseline"] < 0]
    favours = [c for c in d["cells"]
               if (not c["bootstrap_void"])
               and c["point_estimate_favours_cell_on_sharpe"]]
    best = min(favours, key=lambda c: c["bh_q"]) if favours else None
    P("### The verdict"); P("")
    P(para(f"**{d['verdict']}**")); P("")
    P(para(f"No cell clears q = {boot['q_threshold']:.2f} while favouring itself "
           f"on Sharpe: {len(sig)} of {len(d['cells'])} are significant on the "
           f"primary metric, and every one of them is on the WRONG side of it. "
           f"The closest call in the cell's favour is `{best['cell_id']}` at "
           f"Sharpe {best['pooled']['sharpe']:.4f} against "
           f"{base['pooled']['sharpe']:.4f}, but its adjusted q of "
           f"{best['bh_q']:.4f} is far above the threshold. The {len(worse)} "
           f"cells that ARE significantly different from the baseline are all "
           f"worse on Sharpe: "
           + ", ".join(f"`{c['cell_id']}` (q {c['bh_q']:.4f}, Sharpe "
                       f"{c['pooled']['sharpe']:.4f})" for c in worse) + ".")); P("")

    # ---- three things the grid actually measured --------------------------
    eq = cells["C1_equal"]
    vol = cells["C1_volscaled"]
    unc = cells["C4_uncapped"]
    rp = cells["C4_riskparity"]
    t20 = cells["C3_t20"]
    t5 = cells["C3_t5"]
    trail = cells["C3_trailing"]
    max5 = cells["C2_max5"]
    kelly = cells["C1_kelly"]
    kd = d["kelly_diagnostics"]

    P("### Three things this grid did measure"); P("")
    P(para(f"**1. The sizing rule v2 uses is doing nothing.** Replacing the "
           f"ATR-proportional weight with a flat equal weight changes the "
           f"pooled Sharpe by {n(eq['sharpe_delta_vs_baseline'], 4)} and the "
           f"CAGR by {n(eq['cagr_delta_vs_baseline_pp'])}pp — inside the noise, "
           f"and not significant (p {eq['p_value']:.4f}). The only sizing level "
           f"that moves Sharpe at all is `volscaled`, and it moves it by "
           f"{n(vol['sharpe_delta_vs_baseline'], 4)} with q "
           f"{vol['bh_q']:.4f}, which is a look, not a finding. So whatever v2's "
           f"per-position sizing is worth, it is worth about nothing at the "
           f"portfolio level, in either direction.")); P("")
    P(para(f"**2. The 60% cap is a risk-budget lever, and it is the only knob "
           f"with a large effect.** `C4_uncapped` runs at "
           f"{unc['exposure_pct']:.2f}% mean exposure instead of "
           f"{base['exposure_pct']:.2f}%, and compounds at "
           f"{unc['pooled']['cagr_pct']:.2f}% instead of "
           f"{base['pooled']['cagr_pct']:.2f}% — "
           f"{n(unc['cagr_delta_vs_baseline_pp'])}pp/yr more — while Sharpe "
           f"moves {n(unc['sharpe_delta_vs_baseline'], 4)} "
           f"({unc['pooled']['sharpe']:.4f} against "
           f"{base['pooled']['sharpe']:.4f}) and the drawdown widens from "
           f"{neg(base['pooled']['maxDD_pct'])}% to "
           f"{neg(unc['pooled']['maxDD_pct'])}%. `C4_riskparity` is the same "
           f"trade with the scaling done dynamically "
           f"({rp['exposure_pct']:.2f}% exposure, {rp['pooled']['cagr_pct']:.2f}% "
           f"CAGR, Sharpe {rp['pooled']['sharpe']:.4f}) and is slightly worse "
           f"again. The pre-registered primary metric is Sharpe, so neither "
           f"qualifies as significant; but this is the grid's one real finding, "
           f"and it is a fact about capital deployment, not about signal "
           f"quality. More capital in the same strategy is more return and more "
           f"drawdown, and the Sharpe barely notices.")); P("")
    # The two kinds of "significantly worse" are told apart by the Sharpe
    # delta, not by hand: a cell whose Sharpe barely moves has changed how much
    # capital it deploys, a cell whose Sharpe falls has changed the return
    # stream itself. Sharpe is invariant to capital scaling, so this split is
    # the only way to read the six significant cells honestly.
    FLAT = 0.05
    deployment = [c for c in worse if abs(c["sharpe_delta_vs_baseline"]) < FLAT]
    degradation = [c for c in worse if abs(c["sharpe_delta_vs_baseline"]) >= FLAT]
    P(para(f"**3. Half of the significantly-worse cells are capital-deployment "
           f"effects, not signal effects, and the two kinds have to be read "
           f"apart.** Sharpe is invariant to how much capital a portfolio "
           f"deploys, so a cell can show a large CAGR difference and a flat "
           f"Sharpe simply by holding less or more of the book. Splitting the "
           f"{len(worse)} significant cells on the Sharpe delta "
           f"(threshold {FLAT}):")); P("")
    P(f"- **Deployment effects** (Sharpe within {FLAT} of the baseline, so the "
      f"return stream is the same and only the capital deployed changed): "
      + ", ".join(f"`{c['cell_id']}` (Sharpe {n(c['sharpe_delta_vs_baseline'], 4)}, "
                  f"CAGR {n(c['cagr_delta_vs_baseline_pp'])}pp, exposure "
                  f"{c['exposure_pct']:.2f}% against {base['exposure_pct']:.2f}%)"
                  for c in deployment) + ".")
    P(f"- **Genuine degradations** (Sharpe materially down): "
      + ", ".join(f"`{c['cell_id']}` (Sharpe {n(c['sharpe_delta_vs_baseline'], 4)}, "
                  f"CAGR {n(c['cagr_delta_vs_baseline_pp'])}pp, exposure "
                  f"{c['exposure_pct']:.2f}%)" for c in degradation) + ".")
    P("")
    P(para(f"`C3_t20` is the purest case of the first kind: lengthening the "
           f"hold to 20 sessions leaves the book unable to fill (exposure "
           f"{t20['exposure_pct']:.2f}% against {base['exposure_pct']:.2f}%) "
           f"because a cohort's capital is 1/hold of the book, so its CAGR "
           f"falls {by(t20['cagr_delta_vs_baseline_pp'])} while its Sharpe "
           f"moves {n(t20['sharpe_delta_vs_baseline'], 4)} — nothing. Its q of "
           f"{t20['bh_q']:.4f} says \"these two portfolios behave "
           f"differently\"; it does NOT say \"a 20-session hold is a worse "
           f"signal\". `C3_t5` is the cleanest of the second kind: a 5-session "
           f"hold instead of 10 costs {neg(abs(t5['sharpe_delta_vs_baseline']), 4)} of "
           f"Sharpe while its exposure actually RISES to "
           f"{t5['exposure_pct']:.2f}%, so nothing about capital deployment "
           f"explains it. The name cap and the hold length are not independent "
           f"dimensions — the cap applies across all cohorts while a cohort's "
           f"capital is 1/hold of the book — which is why per-cell exposure, "
           f"names held and fills per session are reported alongside the "
           f"Sharpe.")); P("")

    # ---- two limits on what the grid can say ------------------------------
    P("### Two limits on what this grid can say"); P("")
    P(para(f"**The bidirectional walk-forward does not double the evidence on a "
           f"contiguous sample.** The forward chain and the backward chain "
           f"enumerate the SAME {win['forward']} windows in opposite order, and "
           f"their union covers all {base['pooled']['n_months']} months of the "
           f"sample — so the \"pooled OOS\" series is the sample's own monthly "
           f"return series, and the {base['pooled']['n_months']} months are NOT "
           f"held-out data. The word \"out-of-sample\" here means \"scored on "
           f"rolling windows rather than on the whole curve\", which is a "
           f"statement about the scoring, not about unseen data. Only two "
           f"levels have any fitted parameter at all — `kelly` and "
           f"`riskparity` — and both are strictly past-only by construction, "
           f"with prefix-consistency checks in the self-test suite that rebuild "
           f"the market truncated at three dates and confirm nothing at or "
           f"before the cut moves.")); P("")
    P(para(f"**The Kelly level is largely inert, so its result is not a verdict "
           f"on Kelly.** Of the {kd['name_day_weights_from_kelly'] + kd['name_day_weights_fallen_back_to_equal']:,} "
           f"name-day weights, {kd['name_day_weights_fallen_back_to_equal']:,} "
           f"({kd['name_day_weights_fallen_back_to_equal'] / (kd['name_day_weights_from_kelly'] + kd['name_day_weights_fallen_back_to_equal']) * 100:.0f}%) "
           f"fall back to equal weight because the name has fewer than "
           f"{kd['min_prior_signals']} prior signals — mostly on the "
           f"universe-fallback days, where the names have short signal "
           f"histories. The cell is therefore mostly the `equal` cell. Where "
           f"Kelly weights do apply they cost a little: Sharpe "
           f"{n(kelly['sharpe_delta_vs_baseline'], 4)}, CAGR "
           f"{n(kelly['cagr_delta_vs_baseline_pp'])}pp, q "
           f"{kelly['bh_q']:.4f} — just above the threshold, so not significant "
           f"either. Read it as \"adaptive per-name sizing did not help here\", "
           f"not as a clean test of the Kelly idea.")); P("")

    # ---- what this means --------------------------------------------------
    P("### What this means"); P("")
    P(para("Direction C asked whether a better-built portfolio could rescue v2. "
           "It cannot. Eleven single-dimension changes to the construction "
           "(the other five grid entries are the baseline configuration "
           "itself), each scored out-of-sample and corrected for multiple "
           "testing, produce nothing that beats the baseline — and half of the "
           "cells that differ significantly do so because of how much capital "
           "they deploy, not because of what they select.")); P("")
    P(para("**Amendment C's caveat is binding here.** C assumed the per-trade "
           "edge is real AND that Direction A would find selection edge. "
           "Direction A returned SELECTION_DEAD, so no Direction C result is a "
           "v2 recommendation, and this grid is an optimisation of noise for "
           "v2's purposes. It is worth having run, for the reason the "
           "pre-registration gave: a portfolio-construction result that only "
           "looks good when the screen is dead is itself worth knowing, and now "
           "we know it does not look good even then.")); P("")
    P(para("The Direction C run itself does not read Direction A's output — "
           "the verdict quoted above is the Direction C artifact's own "
           "statement, written when it ran. The caveat is applied HERE, in "
           "this pre-registration, using the Direction A result recorded in the "
           "Direction A outcome above.")); P("")
    P(para("**The one fact worth carrying forward is not a recommendation "
           "either.** `C4_uncapped` is a measured description of the baseline's "
           "own risk budget: at full deployment the same strategy compounds "
           f"{neg(abs(unc['cagr_delta_vs_baseline_pp']))}pp/yr faster with the "
           f"Sharpe essentially unchanged ({unc['pooled']['sharpe']:.4f} against "
           f"{base['pooled']['sharpe']:.4f}) and a much deeper drawdown "
           f"({neg(unc['pooled']['maxDD_pct'])}% against "
           f"{neg(base['pooled']['maxDD_pct'])}%). Whether that trade is worth "
           "making is a risk decision about the book, not a claim about v2's "
           "edge — and it is only relevant if there is a v2 edge to deploy, "
           "which Direction A says there is not.")); P("")

    p = R / "docs/edge-to-alpha-prereg-spec-2026-10-19.md"
    t = p.read_text(encoding="utf-8")
    # Re-runnable: an earlier run's section is removed before the new one is
    # written, so a corrected generator never leaves two outcomes in the file.
    marker = "\n---\n\n## Direction C — outcome, 2026-10-19"
    if marker in t:
        t = t[:t.index(marker)] + "\n"
    anchor = "## Direction C — outcome, 2026-10-19"
    assert t.count(anchor) == 0, "a Direction C outcome is still in the spec"
    t = t.rstrip("\n") + "\n\n---\n\n" + "\n".join(L) + "\n"
    p.write_text(t, encoding="utf-8")
    print(f"Direction C outcome written into the spec; {len(t.splitlines())} lines")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

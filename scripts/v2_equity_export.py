#!/usr/bin/env python3
"""v2_equity_export.py — per-trade records for the equity-curve pages, from v2.

WHY THIS EXISTS
    build_equity_curves.py rendered its four pages from
    data/monthly_backtest/backtest_<date>.json. That file is the retired v1
    swing/day-trade system: fixed −2/−5/−7/−10% stops and +2/+5/+10/+15%
    targets, with no reference to v2 or v2engine at all (grep: 0 hits).

    The pages shipped those numbers under a v2-branded site whose methodology
    page says, in bold, "冇目標價" (no profit target). Same class of defect as
    the homepage carrying v1 cards under a v2 hero: two engines, one surface.
    For anyone deciding how long to hold — which is the only question these
    four pages exist to answer — those numbers were about a different strategy.

    So the source of truth moves. This script runs the SAME machinery the
    published signals come from — build_v2_signals rules (long only, rising
    200d filter on stock and index, limit entry at the 20-day low with
    close-hold confirmation, 3xATR stop, no target) through
    portfolio_test.candidates_v2 + the slot-constrained walk — and emits the
    record shape build_equity_curves.py already renders.

    Scope: US and JP only. HK is deliberately absent because build_v2_signals
    sets INCLUDE_HK=False, and publishing a v2 curve for a market the engine
    does not serve would recreate the very mismatch being fixed.

HONESTY NOTES carried into the pages
    - Absolute levels still carry static survivorship bias (today's universe
      applied retroactively). Only the curve's shape and the trade table are
      meaningful; see methodology 4.7.
    - The four horizons are the SAME candidate set with four exit rules, so
      they are comparable to each other and to nothing else.
    - Trade counts are in the low hundreds over ~9 years. Small samples.

Usage:
    python3 scripts/v2_equity_export.py            # write data/v2_equity_trades.json
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent / "v2engine"))

from bt_clean import bars_dir, _load_mask_cache, filter_candidates  # noqa: E402
import portfolio_test as PT  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
OUT = REPO / "data" / "v2_equity_trades.json"

MARKETS = ("us200", "jp200")
HORIZONS = {1: "T+1 (day-trade)", 3: "T+3 (3-day)", 5: "T+5 (5-day)",
            10: "T+10 (10-day)"}


def symbols_for(mkt: str, src: Path) -> list[Path]:
    out = []
    for p in sorted(src.glob("*.json")):
        s = p.stem
        if s.startswith("IDX_"):
            continue
        if mkt == "jp200" and s.endswith("_T"):
            out.append(p)
        elif mkt == "us200" and not s.endswith("_T") and "_" not in s:
            out.append(p)
    return out


def collect(mkt: str, hold: int, src: Path, mask: dict) -> list[dict]:
    PT.HOLD = hold
    cands: list[dict] = []
    for p in symbols_for(mkt, src):
        b = PT.load(p)
        if b is None:
            continue
        kept, _ = filter_candidates([dict(c, sym=p.stem)
                                     for c in PT.candidates_v2(b)], mask)
        cands.extend(kept)
    return cands


def run_portfolio(cands: list[dict], mkt: str) -> tuple[list[dict], pd.DataFrame | None]:
    """Slot-constrained walk that RECORDS each fill, unlike PT.walk which only
    returns the equity curve. Same sizing rules — reuse PT's constants."""
    if not cands:
        return [], None
    d = pd.DataFrame(cands).sort_values(["entry_date", "ret5d"])
    d["cost"] = [PT.cost_rt(mkt, PT.COST, w == "stop") for w in d.why]
    dates = sorted(set(d.entry_date) | set(d.exit_date))
    equity, pos, curve, fills = 100_000.0, [], [], []
    for dt in dates:
        keep = []
        for p in pos:
            if p["exit_date"] <= dt:
                gross = p["shares"] * (p["exit"] * (1 - p["cost"]) - p["entry"])
                equity += gross
                fills.append({**p, "close_date": dt, "pnl": gross})
            else:
                keep.append(p)
        pos = keep
        for _, t in d[d.entry_date == dt].iterrows():
            if len(pos) >= PT.MAX_SLOTS:
                break
            rps = t.entry - t.stop
            if rps <= 0:
                continue
            room = PT.MAX_NOTIONAL * equity - sum(
                q["shares"] * q["entry"] for q in pos if q["sym"] == t.sym)
            if room <= 0:
                continue
            sh = min(PT.RISK * equity / rps, room / t.entry)
            if sh <= 0:
                continue
            if sum(q["shares"] * q["entry"] for q in pos) + sh * t.entry > equity:
                continue
            pos.append({"sym": t.sym, "entry": t.entry, "exit": t.exit,
                        "exit_date": t.exit_date, "shares": sh, "cost": t.cost,
                        "stop": t.stop, "entry_date": t.entry_date, "why": t.why})
        curve.append({"date": dt, "equity": equity, "open": len(pos)})
    # close out anything still open at the end so the ledger balances
    for p in pos:
        equity += p["shares"] * (p["exit"] * (1 - p["cost"]) - p["entry"])
        fills.append({**p, "close_date": p["exit_date"],
                      "pnl": p["shares"] * (p["exit"] * (1 - p["cost"]) - p["entry"])})
    return fills, pd.DataFrame(curve)


def main() -> int:
    src = bars_dir()
    mask = _load_mask_cache()
    records = []
    summary = []

    for mkt in MARKETS:
        for hold, label in HORIZONS.items():
            cands = collect(mkt, hold, src, mask)
            fills, curve = run_portfolio(cands, mkt)
            if curve is None or not fills:
                print(f"  {mkt} hold={hold}: no trades")
                continue
            for f in fills:
                ret = (f["exit"] * (1 - f["cost"]) - f["entry"]) / f["entry"]
                records.append({
                    "code": f["sym"],
                    "market": mkt,
                    "date": str(pd.Timestamp(f["entry_date"]).date()),
                    "sig": 100,
                    "horizon": label,
                    "hold": hold,
                    "return_pct": ret,
                    "stop_pct_used": (f["stop"] - f["entry"]) / f["entry"],
                    "target_pct_used": None,       # v2 has no profit target
                    "used_live": True,
                    "why": f.get("why", ""),
                })
            eq = curve.set_index("date").equity
            yrs = (eq.index[-1] - eq.index[0]).days / 365.25
            cagr = ((eq.iloc[-1] / eq.iloc[0]) ** (1 / yrs) - 1) * 100
            dd = (eq / np.maximum.accumulate(eq) - 1).min() * 100
            wr = sum(1 for f in fills if f["pnl"] > 0) / len(fills) * 100
            wins = sum(1 for f in fills if f["pnl"] > 0)
            peak = float(eq.cummax().max())
            summary.append({
                "market": mkt, "hold": hold, "horizon": label,
                "trades": len(fills), "signals": len(cands),
                "wins": wins, "losses": len(fills) - wins,
                "cagr": round(cagr, 2), "maxdd": round(dd, 2),
                "win": round(wr, 1),
                "total_pct": round((eq.iloc[-1] / eq.iloc[0] - 1) * 100, 2),
                "final_equity": round(float(eq.iloc[-1]), 2),
                "avg_pnl": round(float(np.mean([f["pnl"] for f in fills])), 2),
                "calmar": round(cagr / abs(dd), 2) if dd else 0.0,
                "start": str(eq.index[0].date()), "end": str(eq.index[-1].date()),
                "daily": [{"date": str(i.date()), "equity": round(float(v), 2),
                           "open": int(o)} for i, v, o in zip(
                               eq.index, eq.values, curve["open"].values)],
            })
            print(f"  {mkt} hold={hold:2}: {len(fills):4} trades  CAGR {cagr:6.1f}%  "
                  f"maxDD {dd:5.2f}%  WR {wr:4.1f}%")

    OUT.write_text(json.dumps({
        "curves": summary,
        "source": "v2 (build_v2_signals rules) via portfolio_test machinery",
        "note": ("Regenerated from the live v2 rule set. The previous source "
                 "(data/monthly_backtest/) was the retired v1 swing system with "
                 "fixed stop/target ladders and no v2 involvement."),
        "records": records,
    }, ensure_ascii=False), encoding="utf-8")
    print(f"\nwrote {OUT}  ({len(records)} records, {len(summary)} curves)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
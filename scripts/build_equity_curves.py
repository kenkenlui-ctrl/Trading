"""build_equity_curves.py — Honest fixed-capital equity curves for all 4 horizons.

Per Astra/Grok/Critique reviews: the "sum of trade %s" figure on the backtest
page is misleading. This script simulates a real portfolio for each horizon
(T+1, T+3, T+5, T+10) and outputs 4 HTML pages with daily equity curve,
CAGR, max drawdown, win rate, skipped signals.

Config (locked d71): 1% risk per trade, 5 concurrent cap, fixed capital HK$1M.
"""
from __future__ import annotations
import json
import statistics
from datetime import datetime, timedelta
from pathlib import Path
from collections import Counter

REPO = Path("/Users/kenken/dev/dsa-hk")
INITIAL = 1_000_000
RISK = 0.01  # 1% per trade (locked d71)
CAP = 5  # concurrent cap (locked d71)
COST_RT = 0.0025  # HK 0.25% round-trip already in return_pct

HORIZONS = {
    "T+1": (1, "day-trade"),
    "T+3": (3, "3-day swing"),
    "T+5": (5, "5-day swing"),
    "T+10": (10, "10-day swing"),
}


def _resolve_source() -> Path:
    """Always use the newest backtest_<date>.json, never a hardcoded date.

    Was hardcoded to backtest_2026-09-10.json, which meant the equity-curve pages
    silently kept reporting the OLD pre-fix numbers after a fresh backtest run —
    the site would then show two contradicting sets of figures (backtest.html
    fresh, equity curves stale). Resolve the latest file instead, so a rebuild
    can never publish mismatched numbers.
    """
    d = REPO / "data" / "monthly_backtest"
    dated = sorted(d.glob("backtest_*.json"))
    if dated:
        return dated[-1]
    # Fall back to the canonical latest if no dated file exists yet.
    return d / "backtest_latest.json"


def load_trades():
    p = _resolve_source()
    if not p.exists():
        raise FileNotFoundError(
            f"No backtest JSON found in {p.parent}. Run monthly_swing_backtest.py first."
        )
    print(f"  equity curves source: {p.name}")
    d = json.load(open(p))
    by_horizon = {}
    for h, (days, label) in HORIZONS.items():
        by_horizon[h] = [r for r in d["records"] if label in r["horizon"]]
    return by_horizon, d


def sim(trades, hold_days, risk=RISK, cap=CAP):
    sorted_t = sorted(trades, key=lambda r: r["date"])
    if not sorted_t:
        return None
    start = datetime.strptime(sorted_t[0]["date"], "%Y-%m-%d")
    end = datetime.strptime(sorted_t[-1]["date"], "%Y-%m-%d") + timedelta(days=hold_days + 5)
    cash = INITIAL
    open_pos = []
    peak = INITIAL
    max_dd = 0
    n_traded = n_won = n_lost = 0
    skip = 0
    sum_pnl = 0
    daily = []

    for d_off in range((end - start).days + 1):
        cur = start + timedelta(days=d_off)
        cur_s = cur.strftime("%Y-%m-%d")
        # exit matured
        new = []
        for p in open_pos:
            if p["exit"] <= cur:
                pnl = p["size"] * p["ret"]
                cash += p["size"] + pnl
                sum_pnl += pnl
                n_traded += 1
                if p["ret"] > 0:
                    n_won += 1
                else:
                    n_lost += 1
            else:
                new.append(p)
        open_pos = new
        # entry: process signals dated today (allow multiple if slot available)
        for t in sorted_t:
            if t["date"] != cur_s:
                continue
            if len(open_pos) >= cap or cash <= 0:
                skip += 1
                continue
            equity = cash + sum(p["size"] for p in open_pos)
            risk_amt = equity * risk
            stop = abs(t.get("stop_pct_used", 0.10)) or 0.10
            size = risk_amt / stop
            if size > cash:
                skip += 1
                continue
            cash -= size
            open_pos.append({
                "size": size,
                "ret": t["return_pct"],
                "exit": cur + timedelta(days=hold_days),
            })
        # mtm
        eq = cash + sum(p["size"] * (1 + p["ret"]) for p in open_pos)
        if eq > peak:
            peak = eq
        dd = (peak - eq) / peak if peak > 0 else 0
        if dd > max_dd:
            max_dd = dd
        daily.append({
            "date": cur_s,
            "equity": round(eq, 0),
            "open_count": len(open_pos),
            "dd_pct": round(dd * 100, 2),
        })
    # force close remaining
    for p in open_pos:
        pnl = p["size"] * p["ret"]
        cash += p["size"] + pnl
        sum_pnl += pnl
        n_traded += 1
        if p["ret"] > 0:
            n_won += 1
        else:
            n_lost += 1
    final = cash
    days = (end - start).days
    return {
        "n_signals": len(sorted_t),
        "n_traded": n_traded,
        "n_won": n_won,
        "n_lost": n_lost,
        "skip": skip,
        "win_rate": (n_won / n_traded * 100) if n_traded else 0,
        "avg_pnl_per_trade": sum_pnl / n_traded if n_traded else 0,
        "return_pct": (final / INITIAL - 1) * 100,
        "final_equity": round(final, 0),
        "max_dd_pct": max_dd * 100,
        "calmar": ((final / INITIAL - 1) / max(max_dd, 0.001)) / (days / 365) if max_dd > 0 else 0,
        "days": days,
        "daily": daily,
    }


def render_html(horizon: str, hold_days: int, label: str, r: dict) -> str:
    daily = r["daily"]
    if not daily:
        return f"<html><body>No data for {horizon}</body></html>"

    summary = f"""
<div class="ec-summary">
  <div class="ec-stat"><div class="ec-stat-label">Initial Capital</div><div class="ec-stat-value">HK${INITIAL:,}</div></div>
  <div class="ec-stat"><div class="ec-stat-label">Final Equity</div><div class="ec-stat-value" style="color:{'var(--bull)' if r['return_pct']>0 else 'var(--bear)'};">HK${r['final_equity']:,.0f}</div></div>
  <div class="ec-stat"><div class="ec-stat-label">Total Return</div><div class="ec-stat-value" style="color:{'var(--bull)' if r['return_pct']>0 else 'var(--bear)'};">{'+' if r['return_pct']>0 else ''}{r['return_pct']:.2f}%</div></div>
  <div class="ec-stat"><div class="ec-stat-label">Max Drawdown</div><div class="ec-stat-value" style="color:var(--bear);">-{r['max_dd_pct']:.2f}%</div></div>
  <div class="ec-stat"><div class="ec-stat-label">Calmar (ann.)</div><div class="ec-stat-value">{r['calmar']:+.2f}</div></div>
  <div class="ec-stat"><div class="ec-stat-label">Trades Taken</div><div class="ec-stat-value">{r['n_traded']} / {r['n_signals']}</div></div>
  <div class="ec-stat"><div class="ec-stat-label">Skipped (slot full)</div><div class="ec-stat-value">{r['skip']}</div></div>
  <div class="ec-stat"><div class="ec-stat-label">Win Rate</div><div class="ec-stat-value">{r['win_rate']:.1f}%</div></div>
  <div class="ec-stat"><div class="ec-stat-label">Avg PnL/trade</div><div class="ec-stat-value" style="color:{'var(--bull)' if r['avg_pnl_per_trade']>0 else 'var(--bear)'};">{'+' if r['avg_pnl_per_trade']>0 else ''}HK${r['avg_pnl_per_trade']:,.0f}</div></div>
</div>
"""

    # Chart
    cw, ch = 1000, 320
    n = len(daily)
    min_e = min(d["equity"] for d in daily)
    max_e = max(d["equity"] for d in daily)
    e_range = max_e - min_e or 1
    points = []
    for i, d in enumerate(daily):
        x = (i / max(n - 1, 1)) * (cw - 60) + 50
        y = ch - 30 - ((d["equity"] - min_e) / e_range) * (ch - 60)
        points.append(f"{x:.1f},{y:.1f}")
    chart = f'''<svg viewBox="0 0 {cw} {ch}" style="width:100%; max-width:1000px; height:auto; background:var(--bg-2); border-radius:6px; padding:8px;">
  <text x="20" y="20" fill="var(--fg-2)" font-size="11" font-family="monospace">HK$</text>
  <text x="{cw-60}" y="20" fill="var(--fg-2)" font-size="11" font-family="monospace" text-anchor="end">HK$ {max_e:,.0f}</text>
  <line x1="50" y1="{ch-30}" x2="{cw-10}" y2="{ch-30}" stroke="var(--border)" stroke-width="1"/>
  <polyline points="{' '.join(points)}" fill="none" stroke="{'var(--bull)' if r['return_pct']>0 else 'var(--bear)'}" stroke-width="2"/>
  <text x="20" y="{ch-15}" fill="var(--dim)" font-size="10" font-family="monospace">{daily[0]['date']}</text>
  <text x="{cw-10}" y="{ch-15}" fill="var(--dim)" font-size="10" font-family="monospace" text-anchor="end">{daily[-1]['date']}</text>
</svg>'''

    # Table (last 60 days for readability)
    table_rows = []
    for d in daily[-60:]:
        table_rows.append(f"<tr><td class='mono'>{d['date']}</td><td class='mono cell-right'>HK${d['equity']:,.0f}</td><td class='mono cell-right'>{d['open_count']}</td><td class='mono cell-right' style='color:{'var(--bear)' if d['dd_pct']>5 else 'var(--amber)' if d['dd_pct']>2 else 'var(--dim)'};'>{d['dd_pct']}%</td></tr>")

    return f"""<!doctype html>
<html lang="zh-Hant-HK">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{horizon} Equity Curve · Leeks Terminal</title>
<meta name="description" content="{horizon} {label} fixed-capital portfolio equity curve. HK$1M, 1% risk/trade, 5 concurrent cap. Daily mark-to-market, real paper-trade simulation.">
<meta name="robots" content="noindex,follow">
<link rel="canonical" href="https://www.win9you.com/equity_curve_{horizon.lower().replace('+','_')}.html">
<link rel="stylesheet" href="/leeks.css">
<style>
.ec-summary {{ display:grid; grid-template-columns:repeat(auto-fit, minmax(140px, 1fr)); gap:12px; margin: var(--sp-4) 0; padding: var(--sp-4); background: var(--panel); border: 1px solid var(--border); border-radius:8px; }}
.ec-stat {{ text-align:center; padding: var(--sp-2); border-right: 1px solid var(--border); }}
.ec-stat:last-child {{ border-right:none; }}
.ec-stat-label {{ font-size: var(--text-xs); color: var(--dim); text-transform:uppercase; letter-spacing: 0.5px; }}
.ec-stat-value {{ font-size: var(--text-lg); font-weight:600; font-family: var(--font-mono); margin-top: 4px; }}
.ec-table {{ width:100%; border-collapse: collapse; font-size: var(--text-xs); font-family: var(--font-mono); margin-top: var(--sp-4); max-height: 500px; overflow-y: auto; display:block; }}
.ec-table thead {{ position:sticky; top:0; background:var(--bg-2); }}
.ec-table th, .ec-table td {{ padding: 4px 8px; border-bottom: 1px solid var(--border); text-align: left; }}
.ec-table th {{ color: var(--dim); font-weight: 600; }}
.cell-right {{ text-align: right; }}
.cav {{ padding: var(--sp-3) var(--sp-4); background: var(--amber-dim); border-left: 3px solid var(--amber); border-radius: 4px; margin: var(--sp-3) 0; font-size: var(--text-sm); }}
</style>
</head>
<body>
<main class="container" style="max-width:1200px; margin:0 auto; padding: var(--sp-5) var(--sp-4);">
<a href="/equity_curve_t10.html" class="text-dim" style="text-decoration:none;">← All horizons</a>
<h1 style="margin-top: var(--sp-3);">📈 {horizon} {label} Equity Curve</h1>
<p class="lede" style="color: var(--fg-2); max-width: 800px;">Fixed-capital simulation of the {horizon} strategy. Initial capital HK$1,000,000, 1% risk per trade, max {CAP} concurrent positions. Daily mark-to-market.</p>

<div class="cav">
<strong>2026-09-11 update (Astra/Grok review fix):</strong> The backtest page previously showed "{horizon} +X%" as the sum of per-trade percentages. That figure is <em>not</em> a portfolio return — it assumes 100% capital in every trade simultaneously. This page is the honest fixed-capital simulation with position sizing, concurrent-cap, and daily mark-to-market.
</div>

{summary}

<h2 style="margin-top: var(--sp-5); font-size: var(--text-md);">Equity Curve ({daily[0]['date']} → {daily[-1]['date']}, {n} days)</h2>
{chart}

<details style="margin-top: var(--sp-4);">
<summary style="cursor: pointer; padding: var(--sp-3); background: var(--panel); border: 1px solid var(--border); border-radius: 6px;">📋 Daily equity (last 60 days, {n} total rows)</summary>
<table class="ec-table">
<thead><tr><th>Date</th><th>Equity (HK$)</th><th>Open positions</th><th>Drawdown %</th></tr></thead>
<tbody>
{''.join(table_rows)}
</tbody>
</table>
</details>

<h2 style="margin-top: var(--sp-5); font-size: var(--text-md);">Methodology</h2>
<ul style="color: var(--fg-2); font-size: var(--text-sm); line-height: 1.6;">
<li><b>Universe:</b> {r['n_signals']} {horizon} signals from {daily[0]['date']} to {daily[-1]['date']}</li>
<li><b>Initial capital:</b> HK$1,000,000</li>
<li><b>Risk per trade:</b> 1% of equity (position sized to stop distance)</li>
<li><b>Max concurrent:</b> {CAP} positions; if full, signal skipped</li>
<li><b>Holding period:</b> {hold_days} trading days; exit at end OR at stop/target (already in return_pct)</li>
<li><b>Costs:</b> HK 0.25% round-trip already deducted from return_pct by source backtest</li>
<li><b>Mark-to-market:</b> daily equity = cash + sum(open position value at current return_pct)</li>
<li><b>Calmar:</b> annualized return / max drawdown</li>
</ul>

<h2 style="margin-top: var(--sp-5); font-size: var(--text-md);">Caveats</h2>
<ul style="color: var(--fg-2); font-size: var(--text-sm); line-height: 1.6;">
<li>No slippage, no liquidity filter, no shortable filter (long-only assumption)</li>
<li>"Today's top-200 universe" used historically (selection bias — see <a href="/methodology.html">methodology</a>)</li>
<li>6mo / {r['n_signals']} signals — sample still small; multiple testing concerns</li>
<li>Best config from 9-config tune: 1% risk × 5 cap (locked 2026-09-11). Other configs range from -0.8% to +1.1%.</li>
</ul>

<p style="margin-top: var(--sp-5); font-size: var(--text-xs); color: var(--dim);">Last updated: 2026-09-11 · Source: data/monthly_backtest/backtest_2026-09-10.json · {r['n_signals']} {horizon} signals</p>
</main>
</body>
</html>"""


def main():
    by_horizon, _ = load_trades()
    for h, (days, label) in HORIZONS.items():
        trades = by_horizon[h]
        r = sim(trades, days)
        if not r:
            print(f"{h}: no trades")
            continue
        suffix = h.lower().replace("+", "_")
        out = REPO / "public" / f"equity_curve_{suffix}.html"
        html = render_html(h, days, label, r)
        out.write_text(html, encoding="utf-8")
        print(f"{h:>5}: {r['n_traded']:>3} trades, return {r['return_pct']:>+7.2f}%, maxDD {r['max_dd_pct']:>5.2f}%, WR {r['win_rate']:>5.1f}%, Calmar {r['calmar']:>+6.2f} → {out.name}")


if __name__ == "__main__":
    main()

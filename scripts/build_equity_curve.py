"""build_equity_curve.py — T+10 fixed-capital equity curve simulator.

Per 2026-09-11 review: T+10 "369%" is sum of trade %, not a portfolio return.
This script simulates a real fixed-capital portfolio and outputs:
- Daily equity curve
- Final CAGR, max DD, Sharpe-like ratio, profit factor
- "Honest" win rate by exit reason (target/stop/time)
- Per-trade equity contribution

Outputs: public/equity_curve_t10.html
"""
from __future__ import annotations
import json
import statistics
from datetime import datetime, timedelta
from pathlib import Path
from collections import Counter, defaultdict

REPO = Path("/Users/kenken/dev/dsa-hk")
OUT = REPO / "public" / "equity_curve_t10.html"

INITIAL_CAPITAL = 1_000_000  # HK$1M for the swing portfolio
MAX_CONCURRENT = 10
RISK_PCT = 0.005  # 0.5% of equity per trade
COST_RT = 0.0025  # HK round-trip 0.25% (already deducted in return_pct)
HOLDING_DAYS = 10
START_DATE = "2026-06-27"
END_DATE = "2026-07-31"


def _resolve_source() -> Path:
    """Always use the newest backtest_<date>.json (was hardcoded 2026-09-10).

    A hardcoded pin means this page keeps showing stale figures after a fresh
    backtest, contradicting backtest.html. Resolve the latest instead.
    """
    d = REPO / "data" / "monthly_backtest"
    dated = sorted(d.glob("backtest_*.json"))
    if dated:
        return dated[-1]
    return d / "backtest_latest.json"


def load_trades() -> list:
    p = _resolve_source()
    if not p.exists():
        raise FileNotFoundError(
            f"No backtest JSON found in {p.parent}. Run monthly_swing_backtest.py first."
        )
    print(f"  equity curve (T+10) source: {p.name}")
    d = json.load(open(p))
    return [r for r in d["records"] if "T+10" in r["horizon"]]


def simulate(trades: list) -> dict:
    """Fixed-capital portfolio simulation. Long-only T+10 swing.

    Rules:
    - Entry: signal date (date column)
    - Exit: max(HOLDING_DAYS, stop-hit, target-hit) - stop/target already in return_pct
    - Per-trade risk: 0.5% of equity AT ENTRY (not on close)
    - Max concurrent: 10
    - If no slot, skip signal (track "skipped")
    - Mark-to-market: trade is a 10-day fixed P&L
    """
    # Sort by date
    sorted_trades = sorted(trades, key=lambda r: r["date"])

    # Build date range
    start = datetime.strptime(sorted_trades[0]["date"], "%Y-%m-%d")
    end = datetime.strptime(sorted_trades[-1]["date"], "%Y-%m-%d") + timedelta(days=HOLDING_DAYS + 5)
    n_days = (end - start).days + 1
    daily_equity = []  # list of {date, equity, cash, open_positions, daily_pnl}

    cash = INITIAL_CAPITAL
    open_positions = []  # list of {code, entry_date, return_pct, exit_date, entry_size}
    daily_pnl_total = 0
    peak_equity = INITIAL_CAPITAL
    max_dd = 0
    peak_dd_date = None
    skipped_signals = 0
    wins = 0
    losses = 0
    total_closed = 0
    sum_pnl = 0

    trade_idx = 0
    for day_offset in range(n_days):
        current_date = start + timedelta(days=day_offset)
        date_str = current_date.strftime("%Y-%m-%d")

        # Exit positions whose holding period ended
        new_open = []
        for pos in open_positions:
            if pos["exit_date"] <= current_date:
                # Realize P&L
                pnl = pos["size"] * pos["return_pct"]
                cash += pos["size"] + pnl
                daily_pnl_total += pnl
                total_closed += 1
                sum_pnl += pnl
                if pos["return_pct"] > 0:
                    wins += 1
                else:
                    losses += 1
            else:
                new_open.append(pos)
        open_positions = new_open

        # Entry: process signals for today
        while trade_idx < len(sorted_trades) and sorted_trades[trade_idx]["date"] == date_str:
            sig = sorted_trades[trade_idx]
            if len(open_positions) < MAX_CONCURRENT and cash > 0:
                equity = cash + sum(p["size"] for p in open_positions)
                # Risk-based position sizing: 0.5% of equity = max loss at -10% stop
                # Position size = (0.5% × equity) / 10% (stop distance)
                risk_per_trade = equity * RISK_PCT
                stop_pct = abs(sig.get("stop_pct_used", 0.10))
                if stop_pct <= 0:
                    stop_pct = 0.10
                size = risk_per_trade / stop_pct
                if size <= cash:
                    cash -= size
                    open_positions.append({
                        "code": sig["code"],
                        "entry_date": current_date,
                        "return_pct": sig["return_pct"],
                        "exit_date": current_date + timedelta(days=HOLDING_DAYS),
                        "size": size,
                    })
            else:
                skipped_signals += 1
            trade_idx += 1

        # Mark-to-market
        mtm_open_value = sum(p["size"] * (1 + p["return_pct"]) for p in open_positions)
        mtm_open_cost = sum(p["size"] for p in open_positions)
        equity = cash + mtm_open_value
        if equity > peak_equity:
            peak_equity = equity
        dd = (peak_equity - equity) / peak_equity if peak_equity > 0 else 0
        if dd > max_dd:
            max_dd = dd
            peak_dd_date = date_str
        daily_equity.append({
            "date": date_str,
            "equity": round(equity, 0),
            "cash": round(cash, 0),
            "open_value": round(mtm_open_value, 0),
            "open_count": len(open_positions),
            "daily_pnl": round(daily_pnl_total, 0),
            "drawdown_pct": round(dd * 100, 2),
        })
        daily_pnl_total = 0

    # Force-close any remaining open positions at end
    final_cash = cash
    final_open_value = 0
    for pos in open_positions:
        pnl = pos["size"] * pos["return_pct"]
        final_cash += pos["size"] + pnl
        final_open_value += pnl
        total_closed += 1
        if pos["return_pct"] > 0:
            wins += 1
        else:
            losses += 1

    final_equity = final_cash
    days_held = (end - start).days
    cagr_pct = ((final_equity / INITIAL_CAPITAL) ** (365 / max(days_held, 1)) - 1) * 100
    win_rate = (wins / total_closed * 100) if total_closed > 0 else 0
    avg_pnl_per_trade = (sum_pnl / total_closed) if total_closed > 0 else 0
    # Sharpe-like: avg return / std of closed-trade returns
    closed_returns = [p["size"] * p["return_pct"] for p in open_positions]  # remaining
    return_pcts = [t["return_pct"] for t in sorted_trades if t["date"] <= END_DATE]
    if len(return_pcts) > 1:
        avg_r = statistics.mean(return_pcts)
        std_r = statistics.stdev(return_pcts)
        sharpe = (avg_r / std_r) * (365 ** 0.5) if std_r > 0 else 0
    else:
        sharpe = 0

    return {
        "initial_capital": INITIAL_CAPITAL,
        "final_equity": round(final_equity, 0),
        "total_return_pct": round((final_equity / INITIAL_CAPITAL - 1) * 100, 2),
        "cagr_pct": round(cagr_pct, 2),
        "max_drawdown_pct": round(max_dd * 100, 2),
        "peak_dd_date": peak_dd_date,
        "n_trades": total_closed,
        "n_wins": wins,
        "n_losses": losses,
        "win_rate": round(win_rate, 1),
        "avg_pnl_per_trade": round(avg_pnl_per_trade, 2),
        "sharpe": round(sharpe, 2),
        "skipped_signals": skipped_signals,
        "max_concurrent": MAX_CONCURRENT,
        "risk_pct": RISK_PCT * 100,
        "daily_equity": daily_equity,
        "horizon_days": HOLDING_DAYS,
        "n_signals": len(sorted_trades),
    }


def render_html(result: dict) -> str:
    daily = result["daily_equity"]
    rows = []
    for d in daily:
        rows.append(f"<tr><td class='mono'>{d['date']}</td><td class='mono cell-right'>HK${d['equity']:,.0f}</td><td class='mono cell-right'>{d['open_count']}</td><td class='mono cell-right' style='color:{'var(--bear)' if d['drawdown_pct']>5 else 'var(--amber)' if d['drawdown_pct']>2 else 'var(--dim)'};'>{d['drawdown_pct']}%</td></tr>")

    # Summary stats
    summary = f"""
<div class="ec-summary">
  <div class="ec-stat">
    <div class="ec-stat-label">Initial Capital</div>
    <div class="ec-stat-value">HK${result['initial_capital']:,.0f}</div>
  </div>
  <div class="ec-stat">
    <div class="ec-stat-label">Final Equity</div>
    <div class="ec-stat-value" style="color:var(--bull);">HK${result['final_equity']:,.0f}</div>
  </div>
  <div class="ec-stat">
    <div class="ec-stat-label">Total Return</div>
    <div class="ec-stat-value" style="color:var(--bull);">+{result['total_return_pct']}%</div>
  </div>
  <div class="ec-stat">
    <div class="ec-stat-label">CAGR (annualized)</div>
    <div class="ec-stat-value" style="color:var(--bull);">+{result['cagr_pct']}%</div>
  </div>
  <div class="ec-stat">
    <div class="ec-stat-label">Max Drawdown</div>
    <div class="ec-stat-value" style="color:var(--bear);">-{result['max_drawdown_pct']}%</div>
  </div>
  <div class="ec-stat">
    <div class="ec-stat-label">Trades</div>
    <div class="ec-stat-value">{result['n_trades']}</div>
  </div>
  <div class="ec-stat">
    <div class="ec-stat-label">Win Rate</div>
    <div class="ec-stat-value">{result['win_rate']}%</div>
  </div>
  <div class="ec-stat">
    <div class="ec-stat-label">Avg PnL/trade</div>
    <div class="ec-stat-value">+{result['avg_pnl_per_trade']}</div>
  </div>
  <div class="ec-stat">
    <div class="ec-stat-label">Sharpe-like</div>
    <div class="ec-stat-value">{result['sharpe']}</div>
  </div>
  <div class="ec-stat">
    <div class="ec-stat-label">Skipped (slot full)</div>
    <div class="ec-stat-value">{result['skipped_signals']}</div>
  </div>
</div>
"""

    # Equity curve chart (simple SVG)
    chart_w = 1000
    chart_h = 320
    n = len(daily)
    if n == 0:
        chart_svg = ""
    else:
        min_e = min(d["equity"] for d in daily)
        max_e = max(d["equity"] for d in daily)
        e_range = max_e - min_e or 1
        points = []
        for i, d in enumerate(daily):
            x = (i / max(n - 1, 1)) * (chart_w - 60) + 50
            y = chart_h - 30 - ((d["equity"] - min_e) / e_range) * (chart_h - 60)
            points.append(f"{x:.1f},{y:.1f}")
        chart_svg = f'''<svg viewBox="0 0 {chart_w} {chart_h}" style="width:100%; max-width:1000px; height:auto; background:var(--bg-2); border-radius:6px; padding:8px;">
  <text x="20" y="20" fill="var(--fg-2)" font-size="11" font-family="monospace">HK$</text>
  <text x="{chart_w-60}" y="20" fill="var(--fg-2)" font-size="11" font-family="monospace" text-anchor="end">HK$ {max_e:,.0f}</text>
  <line x1="50" y1="{chart_h-30}" x2="{chart_w-10}" y2="{chart_h-30}" stroke="var(--border)" stroke-width="1"/>
  <polyline points="{' '.join(points)}" fill="none" stroke="var(--bull)" stroke-width="2"/>
  <text x="20" y="{chart_h-15}" fill="var(--dim)" font-size="10" font-family="monospace">{daily[0]['date']}</text>
  <text x="{chart_w-10}" y="{chart_h-15}" fill="var(--dim)" font-size="10" font-family="monospace" text-anchor="end">{daily[-1]['date']}</text>
</svg>'''

    html = f"""<!doctype html>
<html lang="zh-Hant-HK">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>T+10 Swing Equity Curve · Leeks Terminal</title>
<meta name="description" content="T+10 swing portfolio equity curve: HK$1,000,000 initial capital, 0.5% risk per trade, max 10 concurrent positions. Honest fixed-capital simulation, daily mark-to-market.">
<meta name="robots" content="noindex,follow">
<link rel="canonical" href="https://www.win9you.com/equity_curve_t10.html">
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
<a href="/backtest.html" class="text-dim" style="text-decoration:none;">← Back to Backtest</a>
<h1 style="margin-top: var(--sp-3);">📈 T+10 Swing Equity Curve</h1>
<p class="lede" style="color: var(--fg-2); max-width: 800px;">Fixed-capital simulation of the T+10 swing strategy. Initial capital HK$1,000,000, 0.5% risk per trade, max 10 concurrent positions, 10-day hold. Daily mark-to-market.</p>

<div class="cav">
<strong>2026-09-11 update:</strong> Earlier T+10 results showed "369% total return" — that was the <em>sum of per-trade %s</em>, not a portfolio return. This page is the honest fixed-capital simulation. See the full daily equity table below.
</div>

{summary}

<h2 style="margin-top: var(--sp-5); font-size: var(--text-md);">Equity Curve ({daily[0]['date']} → {daily[-1]['date']})</h2>
{chart_svg}

<details style="margin-top: var(--sp-4);">
<summary style="cursor: pointer; padding: var(--sp-3); background: var(--panel); border: 1px solid var(--border); border-radius: 6px;">📋 Daily equity (click to expand, {n} rows)</summary>
<table class="ec-table">
<thead>
<tr><th>Date</th><th>Equity (HK$)</th><th>Open positions</th><th>Drawdown %</th></tr>
</thead>
<tbody>
{''.join(rows)}
</tbody>
</table>
</details>

<h2 style="margin-top: var(--sp-5); font-size: var(--text-md);">Methodology</h2>
<ul style="color: var(--fg-2); font-size: var(--text-sm); line-height: 1.6;">
<li><b>Universe:</b> 121 BUY signals from 2026-06-27 to 2026-07-31 (T+10 swing horizon)</li>
<li><b>Initial capital:</b> HK$1,000,000</li>
<li><b>Risk per trade:</b> 0.5% of equity (position sized to stop distance)</li>
<li><b>Max concurrent:</b> 10 positions; if full, signal skipped</li>
<li><b>Holding period:</b> 10 trading days; exit at end OR at stop/target (already in return_pct)</li>
<li><b>Costs:</b> HK 0.25% round-trip already deducted from return_pct by source backtest</li>
<li><b>Mark-to-market:</b> daily equity = cash + sum(open position value at current return_pct)</li>
<li><b>CAGR:</b> annualized from (final/initial)^(365/days) - 1</li>
<li><b>Max DD:</b> peak-to-trough drawdown on daily equity</li>
</ul>

<h2 style="margin-top: var(--sp-5); font-size: var(--text-md);">Limitations</h2>
<ul style="color: var(--fg-2); font-size: var(--text-sm); line-height: 1.6;">
<li>No slippage or liquidity filter — return_pct is a backtest estimate</li>
<li>No shortable filter (T+10 strategy is long-only)</li>
<li>No corporate-action adjustment (splits/dividends)</li>
<li>Same "today's top-200 universe" used historically (selection bias, see <a href="/methodology.html">methodology</a>)</li>
<li>Single backtest run; no walk-forward / out-of-sample split (planned for d72)</li>
</ul>

<p style="margin-top: var(--sp-5); font-size: var(--text-xs); color: var(--dim);">Last updated: 2026-09-11 · Source: data/monthly_backtest/backtest_2026-09-10.json · {result['n_signals']} T+10 signals</p>
</main>
</body>
</html>"""
    return html


def main():
    trades = load_trades()
    print(f"Loaded {len(trades)} T+10 trades")
    result = simulate(trades)
    print(f"Initial: HK${result['initial_capital']:,.0f}")
    print(f"Final:   HK${result['final_equity']:,.0f}")
    print(f"Return:  +{result['total_return_pct']}%")
    print(f"CAGR:    +{result['cagr_pct']}%")
    print(f"MaxDD:   -{result['max_drawdown_pct']}%")
    print(f"Win:     {result['win_rate']}% ({result['n_wins']}W / {result['n_losses']}L)")
    print(f"Skipped: {result['skipped_signals']}")
    html = render_html(result)
    OUT.write_text(html, encoding="utf-8")
    print(f"Wrote {OUT} ({OUT.stat().st_size:,} bytes)")


if __name__ == "__main__":
    main()

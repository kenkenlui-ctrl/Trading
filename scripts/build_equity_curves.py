"""build_equity_curves.py — Honest fixed-capital equity curves for all 4 horizons.

Per Astra/Grok/Critique reviews: the "sum of trade %s" figure on the backtest
page is misleading. This script simulates a real portfolio for each horizon
(T+1, T+3, T+5, T+10) and outputs 4 HTML pages with daily equity curve,
CAGR, max drawdown, win rate, skipped signals.

Config (locked d71): 1% risk per trade, 5 concurrent cap, fixed capital US$1M (US universe).
"""
from __future__ import annotations
import json
import statistics
from datetime import datetime, timedelta
from pathlib import Path
from collections import Counter

REPO = Path("/Users/kenken/dev/dsa-hk")


def _cssver() -> str:
    """Cache-buster shared by every builder.

    Each build script used to invent its own: build_dashboard pinned
    leeks.css?v=2026-08-25b for six weeks, build_home used today's date (so
    nothing changed within a day), and the equity-curve builders used no
    version at all. _headers pins leeks.css for 24h, so any of those keeps a
    returning visitor on the old stylesheet. One content hash, one URL.
    """
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    try:
        from build_static import _CSS_VERSION
        return _CSS_VERSION()
    except Exception:
        return "1"

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
    """v2 rule set, not the retired v1 swing backtest.

    2026-10-07. This used to read data/monthly_backtest/backtest_<date>.json —
    the v1 day-trade/swing system with fixed -2/-5/-7/-10% stops and
    +2/+5/+10/+15% targets, which contains no v2 code path at all (grep for
    v2/v2engine: 0 hits). Those four pages shipped under a site whose
    methodology states, in bold, "no profit target". Anyone using them to
    choose a holding period was reading a different strategy's results.

    The source is now scripts/v2_equity_export.py, which runs the live v2
    rules through the same machinery that produces the published signals.
    US only: build_v2_signals sets INCLUDE_HK=False, so publishing a v2 curve
    for Hong Kong would recreate the mismatch being fixed. JP is exported
    alongside but not published as its own page set yet.
    """
    p = REPO / "data" / "v2_equity_trades.json"
    if p.exists():
        return p
    raise FileNotFoundError(
        f"{p} missing. Run scripts/v2_equity_export.py first.")


MARKET = "us200"
CURRENCY = "US$"

def load_trades():
    p = _resolve_source()
    print(f"  equity curves source: {p.name} (v2 rule set, market={MARKET})")
    d = json.load(open(p))
    by_horizon = {}
    for h, (days, label) in HORIZONS.items():
        # Match on the numeric hold, not the label string. The label wording
        # differs between the exporter and this renderer ("T+3 (3-day)" vs
        # "3-day swing"), and a string match on prose silently yields zero
        # trades — which is exactly what happened on the first attempt.
        by_horizon[h] = [r for r in d["records"]
                         if r.get("hold") == days and r.get("market") == MARKET]
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
  <div class="ec-stat"><div class="ec-stat-label">Initial Capital</div><div class="ec-stat-value">{CURRENCY}{INITIAL:,}</div></div>
  <div class="ec-stat"><div class="ec-stat-label">Final Equity</div><div class="ec-stat-value" style="color:{'var(--bull)' if r['return_pct']>0 else 'var(--bear)'};">{CURRENCY}{r['final_equity']:,.0f}</div></div>
  <div class="ec-stat"><div class="ec-stat-label">Total Return</div><div class="ec-stat-value" style="color:{'var(--bull)' if r['return_pct']>0 else 'var(--bear)'};">{'+' if r['return_pct']>0 else ''}{r['return_pct']:.2f}%</div></div>
  <div class="ec-stat"><div class="ec-stat-label">Max Drawdown</div><div class="ec-stat-value" style="color:var(--bear);">-{r['max_dd_pct']:.2f}%</div></div>
  <div class="ec-stat"><div class="ec-stat-label">Calmar (ann.)</div><div class="ec-stat-value">{r['calmar']:+.2f}</div></div>
  <div class="ec-stat"><div class="ec-stat-label">Trades Taken</div><div class="ec-stat-value">{r['n_traded']} / {r['n_signals']}</div></div>
  <div class="ec-stat"><div class="ec-stat-label">Skipped (slot full)</div><div class="ec-stat-value">{r['skip']}</div></div>
  <div class="ec-stat"><div class="ec-stat-label">Win Rate</div><div class="ec-stat-value">{r['win_rate']:.1f}%</div></div>
  <div class="ec-stat"><div class="ec-stat-label">Avg PnL/trade</div><div class="ec-stat-value" style="color:{'var(--bull)' if r['avg_pnl_per_trade']>0 else 'var(--bear)'};">{'+' if r['avg_pnl_per_trade']>0 else ''}US${r['avg_pnl_per_trade']:,.0f}</div></div>
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
  <text x="20" y="20" fill="var(--fg-2)" font-size="11" font-family="monospace">US$</text>
  <text x="{cw-60}" y="20" fill="var(--fg-2)" font-size="11" font-family="monospace" text-anchor="end">US$ {max_e:,.0f}</text>
  <line x1="50" y1="{ch-30}" x2="{cw-10}" y2="{ch-30}" stroke="var(--border)" stroke-width="1"/>
  <polyline points="{' '.join(points)}" fill="none" stroke="{'var(--bull)' if r['return_pct']>0 else 'var(--bear)'}" stroke-width="2"/>
  <text x="20" y="{ch-15}" fill="var(--dim)" font-size="10" font-family="monospace">{daily[0]['date']}</text>
  <text x="{cw-10}" y="{ch-15}" fill="var(--dim)" font-size="10" font-family="monospace" text-anchor="end">{daily[-1]['date']}</text>
</svg>'''

    # Table (last 60 days for readability)
    table_rows = []
    for d in daily[-60:]:
        table_rows.append(f"<tr><td class='mono'>{d['date']}</td><td class='mono cell-right'>US${d['equity']:,.0f}</td><td class='mono cell-right'>{d['open_count']}</td><td class='mono cell-right' style='color:{'var(--bear)' if d['dd_pct']>5 else 'var(--amber)' if d['dd_pct']>2 else 'var(--dim)'};'>{d['dd_pct']}%</td></tr>")

    return f"""<!doctype html>
<html lang="zh-Hant-HK">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{horizon} 機械供給上限 · 非可達成回報 · Equity Curve · Leeks Terminal</title>
<meta name="description" content="{horizon} {label} fixed-capital portfolio equity curve. US$1M, 1% risk/trade, 5 concurrent cap. Daily mark-to-market, real paper-trade simulation.">
<meta name="robots" content="noindex,follow">
<link rel="canonical" href="https://www.win9you.com/equity_curve_{horizon.lower().replace('+','_')}.html">
<link rel="stylesheet" href="/leeks.css?v={_cssver()}">
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
<div class="cav" style="border-left:4px solid var(--red);background:var(--red-dim);">
<strong>⚠ 先讀呢段：呢條曲線唔係你可以達到嘅回報。</strong><br>
{r['n_traded']} 單係機械喺 {len(r['daily'])} 個交易日內產生嘅訊號，
即係<b>平均每日 {r['n_traded']/max(1,len(r['daily'])):.1f} 單</b>。
入場係掛 S1 限價單，但模擬<b>假設每個訊號都以 S1 精確成交</b>。
持倉期越短 → 單數越多 → 曲線越靚。呢個係<b>供給假設嘅產物</b>，
唔係 alpha。真人執行唔會有呢個成交率。
</div>

<p class="lede" style="color: var(--fg-2); max-width: 800px;">Fixed-capital simulation of the {horizon} strategy. Initial capital US$1,000,000, 1% risk per trade, max {CAP} concurrent positions. Daily mark-to-market.</p>

<div class="cav">
<strong>2026-10-07 — 數據源已更換，呢頁嘅歷史數字全部作廢。</strong>
呢四頁原本讀 <code>data/monthly_backtest/</code>，嗰個係<b>舊版 v1 swing/day-trade 引擎</b>
（固定 −2/−5/−7/−10% 止損、+2/+5/+10/+15% 目標），<b>完全冇涉及 v2</b> ——
而本頁面現時描述嘅規則明確寫住「冇目標價」。用呢啲數字去揀持倉期，等於用另一套策略嘅結果。
而家改由 <code>scripts/v2_equity_export.py</code> 用<b>現行 v2 規則</b>重算。
</div>

<div class="cav">
<strong>讀呢條曲線之前，請先睇交易筆數。</strong>
本頁 {r['n_traded']} 單係機器喺 9 年內產生嘅，唔係你會做到嘅單數。
入場係掛 S1 限價單，但模擬假設每個訊號都以 S1 精確成交。
<b>持倉期越短，單數越多、曲線越靚</b> —— 呢個係供給假設嘅產物，唔係 alpha。
只有 <b>T+10（現行引擎規則）</b> 嘅回報同已發布嘅組合測試一致（美股 27.2% vs
同一批股票等權持有 27.4%，差 −0.2 pp/年）。絕對水平仍然帶靜態倖存者偏差（今日名單倒套 9 年），
可比較嘅只有四條曲線之間嘅關係。
</div>

<div class="cav">
<strong>2026-09-11 update (Astra/Grok review fix):</strong> The backtest page previously showed "{horizon} +X%" as the sum of per-trade percentages. That figure is <em>not</em> a portfolio return — it assumes 100% capital in every trade simultaneously. This page is the honest fixed-capital simulation with position sizing, concurrent-cap, and daily mark-to-market.
</div>

{summary}

<h2 style="margin-top: var(--sp-5); font-size: var(--text-md);">Equity Curve ({daily[0]['date']} → {daily[-1]['date']}, {n} days)</h2>
{chart}

<details style="margin-top: var(--sp-4);">
<summary style="cursor: pointer; padding: var(--sp-3); background: var(--panel); border: 1px solid var(--border); border-radius: 6px;">📋 Daily equity (last 60 days, {n} total rows)</summary>
<table class="ec-table">
<thead><tr><th>Date</th><th>Equity (US$)</th><th>Open positions</th><th>Drawdown %</th></tr></thead>
<tbody>
{''.join(table_rows)}
</tbody>
</table>
</details>

<h2 style="margin-top: var(--sp-5); font-size: var(--text-md);">Methodology</h2>
<ul style="color: var(--fg-2); font-size: var(--text-sm); line-height: 1.6;">
<li><b>Universe:</b> {r['n_signals']} {horizon} signals from {daily[0]['date']} to {daily[-1]['date']}</li>
<li><b>Initial capital:</b> US$1,000,000</li>
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


def render_index(rows: list[dict]) -> str:
    """Family landing page — replaces the retired v1 swing page at this URL.

    2026-10-07. This URL used to be produced by build_equity_curve.py, which
    ran the RETIRED v1 swing engine: HK$1,000,000, 32 trades over a 2-month
    window, "Sharpe-like 5.63" alongside an average PnL of −92.40. It sat one
    URL away from equity_curve_t_10.html, which shows the v2 rules and a
    +27.18% CAGR — the same site offering two engines, two currencies and two
    answers about the same horizon. The other three v2 pages link here as
    "← All horizons", so the page had to keep its URL; the fix is to give it
    v2 content, not to delete it.
    """
    trs = []
    for r in rows:
        c = "var(--bull)" if r["cagr"] > 0 else "var(--bear)"
        per_day = r["trades"] / max(1, r["days"])
        trs.append(
            f'<tr>'
            f'<td class="mono"><a href="/equity_curve_{r["slug"]}" '
            f'style="color:var(--accent);">{r["h"]}</a></td>'
            f'<td class="mono cell-right">{r["trades"]:,} / {r["signals"]:,}</td>'
            f'<td class="mono cell-right" style="color:{c};">{r["cagr"]:+.2f}%</td>'
            f'<td class="mono cell-right" style="color:var(--bear);">−{abs(r["maxdd"]):.2f}%</td>'
            f'<td class="mono cell-right">{r["win"]:.1f}%</td>'
            f'<td class="mono cell-right">{r["calmar"]:+.2f}</td>'
            f'<td class="mono cell-right">{per_day:.1f}</td>'
            f'</tr>'
        )
    return f"""<!doctype html>
<html lang="zh-Hant-HK">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Equity curves · 四個持倉期 · Leeks Terminal</title>
<meta name="description" content="US$1M fixed-capital equity curves for T+1/T+3/T+5/T+10 under the current v2 rule set, with the daily-trade-rate caveat that decides whether any of them is reachable.">
<meta name="robots" content="noindex,follow">
<link rel="canonical" href="https://www.win9you.com/equity_curve_t10.html">
<link rel="stylesheet" href="/leeks.css?v={_cssver()}">
<style>
.ec-table {{ width:100%; border-collapse: collapse; font-size: var(--text-sm); font-family: var(--font-mono); margin-top: var(--sp-4); }}
.ec-table th, .ec-table td {{ padding: 8px 10px; border-bottom: 1px solid var(--border); text-align: left; }}
.ec-table th {{ color: var(--dim); font-weight: 600; }}
.cell-right {{ text-align: right; }}
.cav {{ padding: var(--sp-3) var(--sp-4); background: var(--amber-dim); border-left: 3px solid var(--amber); border-radius: 4px; margin: var(--sp-3) 0; font-size: var(--text-sm); }}
</style>
</head>
<body>
<main class="container" style="max-width:1000px; margin:0 auto; padding: var(--sp-5) var(--sp-4);">
<a href="/backtest" class="text-dim" style="text-decoration:none;">← Back to Backtest</a>
<h1 style="margin-top: var(--sp-3);">📈 Equity curves · 四個持倉期</h1>
<p class="lede" style="color: var(--fg-2); max-width: 800px;">
同一套 v2 規則、同一個組合模型（<code>portfolio_test.walk</code>）、同一批美股，
只改持倉期。原始資本 US$1,000,000，每單風險 1%，同時最多 {CAP} 倉，每日 mark-to-market。
</p>

<div class="cav" style="border-left:4px solid var(--red);background:var(--red-dim);">
<strong>⚠ 先讀最後一行嘅「每日單數」。</strong><br>
入場係掛 S1 限價單，但模擬<b>假設每個訊號都以 S1 精確成交</b>。
T+1 報 {rows[0]['cagr']:+.1f}%/年，係因為佢喺同一段時間產生 {rows[0]['trades']:,} 單 ——
即<b>每日 {rows[0]['trades']/max(1,rows[0]['days']):.1f} 單</b>。
真人唔會有呢個成交率。<b>曲線愈靚，通常只代表假設愈不成立</b>，唔代表策略愈好。
</div>

<table class="ec-table">
<thead><tr><th>持倉期</th><th>成交 / 訊號</th><th>CAGR</th><th>最大回撤</th><th>勝率</th><th>Calmar</th><th>每日單數</th></tr></thead>
<tbody>{''.join(trs)}</tbody>
</table>

<div class="cav">
<strong>點解淨係 T+10 可以信？</strong><br>
只有 T+10 嘅絕對水平同已發布嘅組合測試對得上（美股 v2 <b>27.18%</b> vs
同一批股票等權持有 <b>27.4%</b>，差 <b>−0.2 pp/年</b>）—— 即係呢個引擎喺組合層面
<b>冇 alpha</b>。但組合層面冇 alpha <b>唔等於</b> 揀股冇價值：對「同日同股隨便入場」
嘅基準，v2 每單多賺約 1 pp（見 <a href="/methodology" style="color:var(--accent);">方法論</a>）。
兩個基準唔同，答案就唔同。
</div>

<div class="cav">
<strong>靜態倖存者偏差。</strong> 名單係今日揀出嚟嘅 200 隻，再倒套 9 年前嘅數據。
絕對水平因此偏高。可比較嘅只有同一批股票、同一段時間、同一個模型之間嘅關係。
</div>

<div class="cav">
<strong>2026-10-07 修正記錄。</strong> 呢個 URL 原本由 <code>build_equity_curve.py</code>
產生，行嘅係<b>已停用嘅 v1 swing 引擎</b>：以港元計值、32 單、兩個月窗口，
同時出現「Sharpe-like 5.63」同「平均每單 −92.40」。而 <code>equity_curve_t_10.html</code>
用 v2 規則報 27.18% CAGR。<b>同一個網站、同樣叫 T+10，兩套引擎、兩種貨幣、兩個答案。</b>
v1 swing 引擎已停產，呢頁而家由 <code>build_equity_curves.py</code> 產生，內容全部來自 v2。
</div>

<p style="margin-top: var(--sp-4); font-size: var(--text-sm); color: var(--dim);">
成本已按各市場實際費率扣除（美股 0.05% round-trip）。頁面唔會由網站落單 ——
入場一律由你在券商 app 落手。
</p>
</main>
</body>
</html>"""


def main():
    """Render from the exporter's own portfolio walk — never re-simulate.

    2026-10-07. This file used to run its own `sim()` over the trade list,
    with its own sizing: cash-based, 5 concurrent slots, no per-name notional
    cap. That model is looser than portfolio_test's, and rendering the same v2
    trades through both produced +25,702% total return and Calmar +412 —
    the same over-leverage shape as the +1811% incident already retracted from
    this site in 2026-09.

    A page must not carry a second portfolio model. Everything on screen now
    comes from the walk in v2_equity_export.py, which is the same one that
    produced the published 27.2% vs 27.4% comparison.
    """
    p = _resolve_source()
    d = json.load(open(p))
    curves = {(c["market"], c["hold"]): c for c in d["curves"]}
    print(f"  equity curves source: {p.name} (v2 rule set, market={MARKET}, "
          f"portfolio model = portfolio_test.walk)")

    index_rows: list[dict] = []
    for h, (days, label) in HORIZONS.items():
        c = curves.get((MARKET, days))
        if not c:
            print(f"{h}: no curve exported")
            continue
        suffix = h.lower().replace("+", "_")
        out = REPO / "public" / f"equity_curve_{suffix}.html"
        # adapt the exporter's keys to what render_html already draws
        daily, peak = [], float(c["daily"][0]["equity"]) if c["daily"] else 1.0
        for row in c["daily"]:
            peak = max(peak, row["equity"])
            daily.append({"date": row["date"], "equity": row["equity"],
                          "open_count": row["open"],
                          "dd_pct": (row["equity"] / peak - 1) * 100})
        r = {
            "return_pct": c["total_pct"], "final_equity": c["final_equity"],
            "max_dd_pct": abs(c["maxdd"]), "calmar": c["calmar"],
            "n_traded": c["trades"], "n_signals": c["signals"],
            "skip": max(0, c["signals"] - c["trades"]),
            "win_rate": c["win"], "avg_pnl_per_trade": c["avg_pnl"],
            "daily": daily,
        }
        html = render_html(h, days, label, r)
        out.write_text(html, encoding="utf-8")
        print(f"{h:>5}: {c['trades']:>5} trades, CAGR {c['cagr']:>+7.2f}%, "
              f"maxDD {c['maxdd']:>6.2f}%, WR {c['win']:>5.1f}%, "
              f"Calmar {c['calmar']:>+6.2f} → {out.name}")
        index_rows.append({
            "h": h, "slug": suffix, "trades": c["trades"],
            "signals": c["signals"], "cagr": c["cagr"], "maxdd": c["maxdd"],
            "win": c["win"], "calmar": c["calmar"], "days": len(c["daily"]),
        })

    if index_rows:
        hub = REPO / "public" / "equity_curve_t10.html"
        hub.write_text(render_index(index_rows), encoding="utf-8")
        print(f"  index: {len(index_rows)} horizons → {hub.name} "
              f"(retired the v1 swing page that used to own this URL)")


if __name__ == "__main__":
    main()

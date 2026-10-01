#!/usr/bin/env python3
"""
build_pair_page_v2.py — Enhanced static HTML with corr + coint + L/S ratio.
"""
from __future__ import annotations
import csv
import shutil
from datetime import datetime
from pathlib import Path

OUT_DIR = Path("/Users/kenken/dev/dsa-hk/charts/pair-trade")
DEST_BASE = Path("/Users/kenken/dev/dsa-hk/public")
DEST_DIR = DEST_BASE / "pair-trades"
CSV_FILE = OUT_DIR / "pair_trades_v2.csv"

DEST_DIR.mkdir(parents=True, exist_ok=True)

rows = []
with open(CSV_FILE) as f:
    reader = csv.DictReader(f)
    for r in reader:
        rows.append(r)
print(f"loaded {len(rows)} pairs")

def fmt(x, decimals=2):
    if x is None or x == "" or x == "nan":
        return "—"
    try:
        return f"{float(x):+.{decimals}f}"
    except (ValueError, TypeError):
        return str(x)

def phase_emoji(p):
    return {"uptrend": "🟢", "base_building": "🟡", "downtrend_active": "🔴", "downtrend_recovery": "🟠", "range": "⚪"}.get(p, "⚪")

def phase_label(p):
    return {"uptrend": "Up", "base_building": "Base", "downtrend_active": "Down", "downtrend_recovery": "Recov", "range": "Rng"}.get(p, p)

def coint_emoji(p):
    try:
        p = float(p)
        if p < 0.05: return "🟢"  # cointegrated
        if p < 0.20: return "🟡"  # weak
        return "⚪"
    except:
        return "⚪"

def corr_color(c):
    try:
        c = float(c)
        if abs(c) > 0.7: return "strong"
        if abs(c) > 0.4: return "medium"
        return "weak"
    except:
        return "weak"

rows.sort(key=lambda r: -float(r.get("score") or 0))

# Top 3
top3 = rows[:3]
top3_html = ""
for i, p in enumerate(top3, 1):
    top3_html += f"""
    <div class="top-card">
      <div class="rank">#{i} · score {p['score']}</div>
      <div class="pair">
        <div class="long-side">↑ LONG <strong>{p['long_ticker']}</strong> <span class="market">{p['long_market']}</span></div>
        <div class="vs">vs</div>
        <div class="short-side">↓ SHORT <strong>{p['short_ticker']}</strong> <span class="market">{p['short_market']}</span></div>
        <div class="sector">{p['sector']}</div>
        <div class="metrics">
          <span>corr <b>{fmt(p['correlation_20d'])}</b></span>
          <span>coint {coint_emoji(p['coint_pvalue'])} <b>{fmt(p['coint_pvalue'])}</b></span>
          <span>β <b>{fmt(p['hedge_beta'])}</b></span>
          <span>L/S <b>{fmt(p['ls_ratio'])}</b></span>
        </div>
      </div>
    </div>"""

html_rows = []
for r in rows:
    div = float(r.get("chg_div") or 0)
    direction = "↑" if div > 0 else "↓"
    corr = float(r.get("correlation_20d") or 0)
    html_rows.append(f"""
    <tr data-sector="{r['sector']}" data-score="{r.get('score', 0)}" data-corr="{abs(corr)}">
      <td class="num score">{r.get('score', '—')}</td>
      <td class="sector">{r['sector']}</td>
      <td class="ticker long">{r['long_ticker']} <span class="mkt">{r['long_market']}</span></td>
      <td class="phase {r['long_phase']}">{phase_emoji(r['long_phase'])} {phase_label(r['long_phase'])}</td>
      <td class="chg {('pos' if float(r.get('long_chg_pct') or 0) > 0 else 'neg')}">{fmt(r.get('long_chg_pct'))}</td>
      <td class="arrow">{direction}</td>
      <td class="ticker short">{r['short_ticker']} <span class="mkt">{r['short_market']}</span></td>
      <td class="phase {r['short_phase']}">{phase_emoji(r['short_phase'])} {phase_label(r['short_phase'])}</td>
      <td class="chg {('pos' if float(r.get('short_chg_pct') or 0) > 0 else 'neg')}">{fmt(r.get('short_chg_pct'))}</td>
      <td class="corr {corr_color(corr)}">{fmt(corr)}</td>
      <td class="coint">{coint_emoji(r.get('coint_pvalue'))} {fmt(r.get('coint_pvalue'))}</td>
      <td class="num beta">{fmt(r.get('hedge_beta'))}</td>
      <td class="num ls">{fmt(r.get('ls_ratio'))}</td>
    </tr>""")

sectors = {}
for r in rows:
    s = r.get("sector", "Other")
    sectors[s] = sectors.get(s, 0) + 1
sector_chips = " ".join(
    f'<span class="chip">{s}: {c}</span>'
    for s, c in sorted(sectors.items(), key=lambda x: -x[1])[:12]
)

# Stat row
cross = [r for r in rows if r["long_market"] != r["short_market"]]
coint_strong = [r for r in rows if float(r.get("coint_pvalue") or 1) < 0.05]

html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Pair Trade Screen v2 — {datetime.now().strftime('%Y-%m-%d')}</title>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background: #0d0f12; color: #e0e3e8; padding: 20px; line-height: 1.5; }}
  .container {{ max-width: 1500px; margin: 0 auto; }}
  header {{ background: #161a1f; padding: 20px; border-radius: 8px; margin-bottom: 20px; border: 1px solid #1f2429; }}
  h1 {{ font-size: 1.6em; margin-bottom: 6px; }}
  .meta {{ color: #8a929b; font-size: 0.9em; margin-bottom: 12px; }}
  .chips {{ display: flex; flex-wrap: wrap; gap: 8px; margin-top: 8px; }}
  .chip {{ display: inline-block; padding: 4px 10px; border-radius: 12px; font-size: 0.85em; background: #1f2429; }}
  nav.site-nav {{ margin-top:12px;padding-top:12px;border-top:1px solid #1f2429;font-size:0.9em; }}
  nav.site-nav a {{ color:#8a929b;text-decoration:none;margin-right:14px; }}
  nav.site-nav a.active {{ color:#64b5f6;font-weight:600; }}

  .stat-row {{ display: flex; gap: 12px; margin-bottom: 16px; flex-wrap: wrap; }}
  .stat {{ background: #161a1f; padding: 12px 16px; border-radius: 8px; border: 1px solid #1f2429; flex: 1; min-width: 160px; }}
  .stat .label {{ color: #8a929b; font-size: 0.8em; text-transform: uppercase; }}
  .stat .value {{ color: #e0e3e8; font-size: 1.5em; font-weight: 600; }}

  .top-cards {{ display: grid; grid-template-columns: repeat(3, 1fr); gap: 12px; margin-bottom: 20px; }}
  .top-card {{ background: #161a1f; padding: 16px; border-radius: 8px; border: 1px solid #1f2429; }}
  .top-card .rank {{ font-size: 0.85em; color: #8a929b; margin-bottom: 8px; }}
  .top-card .pair .long-side {{ color: #4ade80; font-size: 1.05em; }}
  .top-card .pair .short-side {{ color: #f87171; font-size: 1.05em; }}
  .top-card .pair .vs {{ color: #8a929b; font-size: 0.85em; margin: 4px 0; }}
  .top-card .pair .sector {{ color: #b0b8c0; font-size: 0.85em; margin-top: 8px; }}
  .top-card .pair .metrics {{ display: flex; gap: 10px; margin-top: 10px; font-size: 0.85em; flex-wrap: wrap; }}
  .top-card .pair .metrics span {{ color: #8a929b; }}
  .top-card .pair .metrics b {{ color: #e0e3e8; }}
  .top-card .market {{ color: #8a929b; font-size: 0.7em; margin-left: 4px; }}

  .filter-bar {{ background: #161a1f; padding: 12px; border-radius: 8px; margin-bottom: 12px; display: flex; flex-wrap: wrap; gap: 8px; align-items: center; }}
  .filter-bar input, .filter-bar select {{ background: #0d0f12; color: #e0e3e8; border: 1px solid #1f2429; padding: 6px 10px; border-radius: 4px; font-size: 0.9em; }}
  .filter-bar input {{ min-width: 200px; }}
  .filter-bar label {{ font-size: 0.85em; color: #8a929b; }}

  table {{ width: 100%; border-collapse: collapse; background: #161a1f; border-radius: 8px; overflow: hidden; font-size: 0.85em; }}
  thead {{ background: #1f2429; }}
  th {{ padding: 8px; text-align: left; font-size: 0.8em; color: #8a929b; text-transform: uppercase; }}
  td {{ padding: 6px 8px; border-top: 1px solid #1f2429; }}
  tr:hover {{ background: #1a1f25; }}
  .sector {{ color: #b0b8c0; font-size: 0.85em; }}
  .ticker.long {{ color: #4ade80; font-weight: 500; }}
  .ticker.short {{ color: #f87171; font-weight: 500; }}
  .ticker .mkt {{ color: #8a929b; font-size: 0.7em; margin-left: 4px; }}
  .phase {{ font-size: 0.85em; }}
  .phase-uptrend {{ color: #4ade80; }}
  .phase-base_building {{ color: #fbbf24; }}
  .phase-downtrend_active {{ color: #f87171; }}
  .phase-downtrend_recovery {{ color: #fb923c; }}
  .chg.pos {{ color: #4ade80; }}
  .chg.neg {{ color: #f87171; }}
  .num {{ text-align: right; font-variant-numeric: tabular-nums; }}
  .score {{ font-weight: 600; color: #64b5f6; }}
  .corr.strong {{ color: #4ade80; font-weight: 600; }}
  .corr.medium {{ color: #fbbf24; }}
  .corr.weak {{ color: #8a929b; }}
  .coint {{ font-weight: 500; }}
  .beta, .ls {{ font-family: monospace; }}

  .glossary {{ background: #1f2429; padding: 16px; border-radius: 8px; margin-top: 20px; font-size: 0.85em; color: #b0b8c0; }}
  .glossary h3 {{ color: #e0e3e8; font-size: 1em; margin-bottom: 8px; }}
  .glossary dl {{ display: grid; grid-template-columns: 120px 1fr; gap: 8px 16px; }}
  .glossary dt {{ color: #64b5f6; font-weight: 600; }}
  .glossary dd {{ color: #b0b8c0; }}

  footer {{ margin-top: 30px; text-align: center; color: #8a929b; font-size: 0.85em; padding: 20px; }}
  footer a {{ color: #64b5f6; text-decoration: none; }}
  .disclaimer {{ background: #1f2429; padding: 12px; border-radius: 4px; margin-top: 12px; font-size: 0.85em; color: #8a929b; }}
</style>
</head>
<body>
<div class="container">
  <header>
    <h1>⚖️ Pair Trade Screen v2</h1>
    <div class="meta">
      Generated {datetime.now().strftime('%Y-%m-%d %H:%M HKT')} • Enhanced with 20d correlation + ADF cointegration test + vol-based L/S ratio • {len(rows)} pairs
    </div>
    <div class="chips">
      {sector_chips}
    </div>
    <nav class="site-nav">
      <a href="/">Home</a> • <a href="/dashboard/">Dashboard</a> • <a href="/hk200/">📊 HK200 S/R</a> • <a href="/us200/">🇺🇸 US200 S/R</a> • <a href="/pair-trades/" class="active">⚖️ Pair Trades</a> • <a href="/methodology.html">Methodology</a>
    </nav>
  </header>

  <div class="stat-row">
    <div class="stat"><div class="label">Total pairs</div><div class="value">{len(rows)}</div></div>
    <div class="stat"><div class="label">Cross-market</div><div class="value">{len(cross)}</div></div>
    <div class="stat"><div class="label">Cointegrated (p&lt;0.05)</div><div class="value" style="color:#4ade80;">{len(coint_strong)}</div></div>
    <div class="stat"><div class="label">Strong |corr| &gt; 0.7</div><div class="value" style="color:#4ade80;">{len([r for r in rows if abs(float(r.get('correlation_20d') or 0)) > 0.7])}</div></div>
  </div>

  <h2 style="font-size:1.1em;margin:20px 0 12px;color:#e0e3e8;">Top 3 (highest composite score)</h2>
  <div class="top-cards">{top3_html}</div>

  <div class="glossary">
    <h3>📖 Metrics</h3>
    <dl>
      <dt>Score</dt><dd>Composite: |corr|×10 + (1-coint_p)×10 + phase_bonus + |1d_div|/2 (higher = better)</dd>
      <dt>Corr (20d)</dt><dd>Pearson correlation over last 20 trading days. |corr|&gt;0.7 = strong, 0.4-0.7 = medium, &lt;0.4 = weak. Negative = inverse (also tradeable).</dd>
      <dt>Coint (p)</dt><dd>Engle-Granger cointegration p-value. &lt;0.05 = statistically mean-reverting pair (🟢), &lt;0.20 = weak (🟡), &gt;0.20 = no cointegration (⚪).</dd>
      <dt>β (beta)</dt><dd>OLS hedge ratio. For dollar-neutral pair: short_dollars/long_dollars = β. β&gt;0 = same direction, β&lt;0 = inverse.</dd>
      <dt>L/S ratio</dt><dd>Volatility-adjusted long/short ratio. For $1 long, short $L/S in the other leg. = vol_long / (vol_short × |β|). Higher = more short exposure needed.</dd>
    </dl>
  </div>

  <div class="filter-bar">
    <label>Filter:</label>
    <input type="text" id="searchInput" placeholder="Search ticker or sector..." onkeyup="filterTable()">
    <label>Market:</label>
    <select id="marketFilter" onchange="filterTable()">
      <option value="">All</option>
      <option value="US-HK">US long + HK short</option>
      <option value="US-US">US internal</option>
      <option value="HK-HK">HK internal</option>
    </select>
    <label>Min |corr|:</label>
    <input type="number" id="minCorr" value="0" step="0.1" onchange="filterTable()" style="width:70px;">
    <label>Max coint p:</label>
    <input type="number" id="maxCoint" value="1" step="0.05" onchange="filterTable()" style="width:70px;">
    <label style="margin-left:auto;color:#8a929b;">{len(rows)} pairs</label>
  </div>

  <table id="dataTable">
    <thead>
      <tr>
        <th>Score</th>
        <th>Sector</th>
        <th colspan="3" style="text-align:center;background:#1a3a1a;color:#4ade80;">↑ LONG</th>
        <th></th>
        <th colspan="3" style="text-align:center;background:#3a1a1a;color:#f87171;">↓ SHORT</th>
        <th>Corr(20d)</th>
        <th>Coint(p)</th>
        <th>β</th>
        <th>L/S</th>
      </tr>
      <tr>
        <th></th>
        <th></th>
        <th>Ticker</th><th>Phase</th><th>1d%</th>
        <th></th>
        <th>Ticker</th><th>Phase</th><th>1d%</th>
        <th></th><th></th><th></th><th></th>
      </tr>
    </thead>
    <tbody>
      {''.join(html_rows)}
    </tbody>
  </table>

  <div class="disclaimer">
    <strong>⚠️ Disclaimer:</strong> Educational only. Not investment advice. Pairs trading involves significant risk.
    Cointegration tests are statistical — past mean reversion doesn't guarantee future.
    Use proper position sizing and stop-losses. Cross-market pairs have currency risk (HKD/USD).
  </div>

  <footer>
    <a href="/">← win9you.com home</a> • <a href="/hk200/">📊 HK200 S/R</a> • <a href="/us200/">🇺🇸 US200 S/R</a><br>
    Generated by Mavis pair-trade screen v2 (corr + coint + L/S) • <a href="https://github.com/kenkenlui-ctrl/Trading">Source</a>
  </footer>
</div>

<script>
  function filterTable() {{
    const search = document.getElementById('searchInput').value.toLowerCase();
    const market = document.getElementById('marketFilter').value;
    const minCorr = parseFloat(document.getElementById('minCorr').value) || 0;
    const maxCoint = parseFloat(document.getElementById('maxCoint').value) || 1;
    const rows = document.querySelectorAll('#dataTable tbody tr');
    let visible = 0;
    rows.forEach(row => {{
      const sector = row.getAttribute('data-sector').toLowerCase();
      const score = parseFloat(row.getAttribute('data-score')) || 0;
      const corr = parseFloat(row.getAttribute('data-corr')) || 0;
      const longT = row.cells[2].textContent.toLowerCase();
      const shortT = row.cells[6].textContent.toLowerCase();
      const longM = row.cells[2].querySelector('.mkt')?.textContent || '';
      const shortM = row.cells[6].querySelector('.mkt')?.textContent || '';
      const cointP = parseFloat(row.cells[10].textContent) || 1;
      const matchSearch = !search || sector.includes(search) || longT.includes(search) || shortT.includes(search);
      let matchMarket = !market;
      if (market === 'US-HK') matchMarket = (longM === 'US' && shortM === 'HK');
      else if (market === 'US-US') matchMarket = (longM === 'US' && shortM === 'US');
      else if (market === 'HK-HK') matchMarket = (longM === 'HK' && shortM === 'HK');
      const matchCorr = corr >= minCorr;
      const matchCoint = cointP <= maxCoint;
      const show = matchSearch && matchMarket && matchCorr && matchCoint;
      row.style.display = show ? '' : 'none';
      if (show) visible++;
    }});
    document.querySelector('.filter-bar label:last-child').textContent = visible + ' pairs';
  }}
</script>
</body>
</html>
"""

(DEST_DIR / "index.html").write_text(html, encoding="utf-8")
print(f"index.html → {DEST_DIR / 'index.html'}")
print(f"  size: {len(html):,} bytes, {len(rows)} pairs")
print(f"  cointegrated (p<0.05): {len(coint_strong)}")
print(f"  strong |corr|>0.7: {len([r for r in rows if abs(float(r.get('correlation_20d') or 0)) > 0.7])}")

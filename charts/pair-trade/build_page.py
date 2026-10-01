#!/usr/bin/env python3
"""
build_pair_page.py — Build static HTML for /pair-trades section.
"""
from __future__ import annotations
import csv
import shutil
from datetime import datetime
from pathlib import Path

OUT_DIR = Path("/Users/kenken/dev/dsa-hk/charts/pair-trade")
DEST_BASE = Path("/Users/kenken/dev/dsa-hk/public")
DEST_DIR = DEST_BASE / "pair-trades"
CSV_FILE = OUT_DIR / "pair_trades.csv"

DEST_DIR.mkdir(parents=True, exist_ok=True)

rows = []
with open(CSV_FILE) as f:
    reader = csv.DictReader(f)
    for r in reader:
        rows.append(r)
print(f"loaded {len(rows)} pairs")

def fmt(x, decimals=1):
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

# Sort by |divergence|
rows.sort(key=lambda r: -abs(float(r.get("div_score") or 0)))

# Build top 3 highlight boxes
top3 = rows[:3]
top3_html = ""
for i, p in enumerate(top3, 1):
    top3_html += f"""
    <div class="top-card">
      <div class="rank">#{i}</div>
      <div class="pair">
        <div class="long-side">↑ LONG <strong>{p['long_ticker']}</strong> <span class="market">{p['long_market']}</span></div>
        <div class="vs">vs</div>
        <div class="short-side">↓ SHORT <strong>{p['short_ticker']}</strong> <span class="market">{p['short_market']}</span></div>
        <div class="sector">{p['sector']} · div {fmt(p['div_score'])}%</div>
      </div>
    </div>"""

# Build all-rows table
html_rows = []
for r in rows:
    div = float(r.get("div_score") or 0)
    direction = "↑" if div > 0 else "↓"
    html_rows.append(f"""
    <tr data-sector="{r['sector']}" data-div="{div}">
      <td class="num">{r.get('div_score', '—')}</td>
      <td class="sector">{r['sector']}</td>
      <td class="ticker long">{r['long_ticker']} <span class="mkt">{r['long_market']}</span></td>
      <td class="phase {r['long_phase']}">{phase_emoji(r['long_phase'])} {phase_label(r['long_phase'])}</td>
      <td class="chg {('pos' if float(r.get('long_chg_pct') or 0) > 0 else 'neg')}">{fmt(r.get('long_chg_pct'))}</td>
      <td class="chg {('pos' if float(r.get('long_5d_real') or 0) > 0 else 'neg')}">{fmt(r.get('long_5d_real'))}</td>
      <td class="arrow">{direction}</td>
      <td class="ticker short">{r['short_ticker']} <span class="mkt">{r['short_market']}</span></td>
      <td class="phase {r['short_phase']}">{phase_emoji(r['short_phase'])} {phase_label(r['short_phase'])}</td>
      <td class="chg {('pos' if float(r.get('short_chg_pct') or 0) > 0 else 'neg')}">{fmt(r.get('short_chg_pct'))}</td>
      <td class="chg {('pos' if float(r.get('short_5d_real') or 0) > 0 else 'neg')}">{fmt(r.get('short_5d_real'))}</td>
      <td class="div {('pos' if div > 0 else 'neg')}">{fmt(div)}%</td>
    </tr>""")

# Sector distribution
sectors = {}
for r in rows:
    s = r.get("sector", "Other")
    sectors[s] = sectors.get(s, 0) + 1
sector_chips = " ".join(
    f'<span class="chip">{s}: {c}</span>'
    for s, c in sorted(sectors.items(), key=lambda x: -x[1])[:12]
)

html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Pair Trade Screen — {datetime.now().strftime('%Y-%m-%d')}</title>
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

  .top-cards {{ display: grid; grid-template-columns: repeat(3, 1fr); gap: 12px; margin-bottom: 20px; }}
  .top-card {{ background: #161a1f; padding: 16px; border-radius: 8px; border: 1px solid #1f2429; }}
  .top-card .rank {{ font-size: 0.85em; color: #8a929b; margin-bottom: 8px; }}
  .top-card .pair .long-side {{ color: #4ade80; font-size: 1.05em; }}
  .top-card .pair .short-side {{ color: #f87171; font-size: 1.05em; }}
  .top-card .pair .vs {{ color: #8a929b; font-size: 0.85em; margin: 4px 0; }}
  .top-card .pair .sector {{ color: #b0b8c0; font-size: 0.85em; margin-top: 8px; }}
  .top-card .market {{ color: #8a929b; font-size: 0.7em; margin-left: 4px; }}

  .filter-bar {{ background: #161a1f; padding: 12px; border-radius: 8px; margin-bottom: 12px; display: flex; flex-wrap: wrap; gap: 8px; align-items: center; }}
  .filter-bar input, .filter-bar select {{ background: #0d0f12; color: #e0e3e8; border: 1px solid #1f2429; padding: 6px 10px; border-radius: 4px; font-size: 0.9em; }}
  .filter-bar input {{ min-width: 200px; }}
  .filter-bar label {{ font-size: 0.85em; color: #8a929b; }}

  table {{ width: 100%; border-collapse: collapse; background: #161a1f; border-radius: 8px; overflow: hidden; font-size: 0.85em; }}
  thead {{ background: #1f2429; }}
  th {{ padding: 8px; text-align: left; font-size: 0.85em; color: #8a929b; text-transform: uppercase; }}
  td {{ padding: 6px 8px; border-top: 1px solid #1f2429; }}
  tr:hover {{ background: #1a1f25; }}
  .sector {{ color: #b0b8c0; }}
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
  .div {{ font-weight: 600; text-align: right; font-variant-numeric: tabular-nums; }}
  .div.pos {{ color: #4ade80; }}
  .div.neg {{ color: #f87171; }}
  .arrow {{ text-align: center; color: #8a929b; }}

  footer {{ margin-top: 30px; text-align: center; color: #8a929b; font-size: 0.85em; padding: 20px; }}
  footer a {{ color: #64b5f6; text-decoration: none; }}
  .disclaimer {{ background: #1f2429; padding: 12px; border-radius: 4px; margin-top: 12px; font-size: 0.85em; color: #8a929b; }}
</style>
</head>
<body>
<div class="container">
  <header>
    <h1>⚖️ Pair Trade Screen</h1>
    <div class="meta">
      Generated {datetime.now().strftime('%Y-%m-%d %H:%M HKT')} • Source: yfinance 5d return • {len(rows)} pairs across HK+US 400 universe
    </div>
    <div class="chips">
      {sector_chips}
    </div>
    <nav class="site-nav">
      <a href="/">Home</a> • <a href="/dashboard/">Dashboard</a> • <a href="/hk200/">📊 HK200 S/R</a> • <a href="/us200/">🇺🇸 US200 S/R</a> • <a href="/pair-trades/" class="active">⚖️ Pair Trades</a> • <a href="/methodology.html">Methodology</a>
    </nav>
  </header>

  <h2 style="font-size:1.1em;margin:20px 0 12px;color:#e0e3e8;">Top 3 pairs (highest 5d divergence)</h2>
  <div class="top-cards">{top3_html}</div>

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
    <label>Min |5d div|:</label>
    <input type="number" id="minDiv" value="0" step="1" onchange="filterTable()" style="width:80px;">
    <label style="margin-left:auto;color:#8a929b;">{len(rows)} pairs</label>
  </div>

  <table id="dataTable">
    <thead>
      <tr>
        <th>Score</th>
        <th>Sector</th>
        <th colspan="4" style="text-align:center;background:#1a3a1a;color:#4ade80;">↑ LONG</th>
        <th></th>
        <th colspan="4" style="text-align:center;background:#3a1a1a;color:#f87171;">↓ SHORT</th>
        <th>5d Div</th>
      </tr>
      <tr>
        <th></th>
        <th></th>
        <th>Ticker</th><th>Phase</th><th>1d%</th><th>5d%</th>
        <th></th>
        <th>Ticker</th><th>Phase</th><th>1d%</th><th>5d%</th>
        <th></th>
      </tr>
    </thead>
    <tbody>
      {''.join(html_rows)}
    </tbody>
  </table>

  <div class="disclaimer">
    <strong>⚠️ Disclaimer:</strong> All content is for informational and educational purposes only. Not investment advice.
    Pair trade screen based on 5d return divergence within sectors. Real correlation / cointegration not yet computed.
    Use with proper risk management. Long/short ratios not shown — assume dollar-neutral for simplicity.
  </div>

  <footer>
    <a href="/">← win9you.com home</a> • <a href="/hk200/">📊 HK200 S/R</a> • <a href="/us200/">🇺🇸 US200 S/R</a><br>
    Generated by Mavis pair-trade screen • <a href="https://github.com/kenkenlui-ctrl/Trading">Source</a>
  </footer>
</div>

<script>
  function filterTable() {{
    const search = document.getElementById('searchInput').value.toLowerCase();
    const market = document.getElementById('marketFilter').value;
    const minDiv = parseFloat(document.getElementById('minDiv').value) || 0;
    const rows = document.querySelectorAll('#dataTable tbody tr');
    let visible = 0;
    rows.forEach(row => {{
      const sector = row.getAttribute('data-sector').toLowerCase();
      const longT = row.cells[2].textContent.toLowerCase();
      const shortT = row.cells[7].textContent.toLowerCase();
      const longM = row.cells[2].querySelector('.mkt')?.textContent || '';
      const shortM = row.cells[7].querySelector('.mkt')?.textContent || '';
      const div = Math.abs(parseFloat(row.getAttribute('data-div')) || 0);
      const matchSearch = !search || sector.includes(search) || longT.includes(search) || shortT.includes(search);
      let matchMarket = !market;
      if (market === 'US-HK') matchMarket = (longM === 'US' && shortM === 'HK');
      else if (market === 'US-US') matchMarket = (longM === 'US' && shortM === 'US');
      else if (market === 'HK-HK') matchMarket = (longM === 'HK' && shortM === 'HK');
      const matchDiv = div >= minDiv;
      const show = matchSearch && matchMarket && matchDiv;
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

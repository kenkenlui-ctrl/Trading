#!/usr/bin/env python3
"""
build_us200_page.py — Build static HTML page for /us200 section.
"""
from __future__ import annotations
import csv
import json
import shutil
from datetime import datetime
from pathlib import Path

OUT_DIR = Path("/Users/kenken/dev/dsa-hk/charts/us200")
DEST_BASE = Path("/Users/kenken/dev/dsa-hk/public")
DEST_DIR = DEST_BASE / "us200"
DEST_CHARTS = DEST_DIR / "charts"
SUMMARY_CSV = OUT_DIR / "top200_summary.csv"
UNIVERSE_JSON = OUT_DIR / "us_top200_fresh.json"

DEST_DIR.mkdir(parents=True, exist_ok=True)
DEST_CHARTS.mkdir(parents=True, exist_ok=True)

with open(UNIVERSE_JSON) as f:
    universe = json.load(f)
print(f"universe: {len(universe)} tickers")

rows = []
if SUMMARY_CSV.exists():
    with open(SUMMARY_CSV) as f:
        reader = csv.DictReader(f)
        for r in reader:
            rows.append(r)
print(f"summary: {len(rows)} rows")

by_ticker = {r["ticker"]: r for r in rows}

data_date = "2026-08-12"  # T-1
display_date = "2026-08-12 (T-1, US close)"
# Read actual date from a sample JSON
try:
    import json
    with open(OUT_DIR / "AAPL_US.json") as f:
        sample = json.load(f)
    if "last_bar" in sample:
        data_date = sample["last_bar"]["date"]
        display_date = f"{data_date} (T-1, US close)"
except Exception:
    pass

# Copy PNGs (force overwrite — always copy fresh)
pngs_copied = 0
for ticker in universe:
    src_png = OUT_DIR / f"{ticker.replace('.', '_').replace('-', '_')}.png"
    if src_png.exists():
        dest_png = DEST_CHARTS / f"{ticker.replace('.', '_').replace('-', '_')}.png"
        # Always overwrite to ensure fresh data
        shutil.copy2(src_png, dest_png)
        pngs_copied += 1
print(f"PNGs copied (force overwrite): {pngs_copied}")

def fmt_num(x, decimals=2):
    if x is None or x == "" or x == "nan":
        return "—"
    try:
        return f"{float(x):,.{decimals}f}"
    except (ValueError, TypeError):
        return str(x)

def fmt_pct(x):
    if x is None or x == "" or x == "nan":
        return "—"
    try:
        return f"{float(x):+.2f}%"
    except (ValueError, TypeError):
        return str(x)

phase_emoji = {
    "uptrend": "🟢",
    "base_building": "🟡",
    "downtrend_active": "🔴",
    "downtrend_recovery": "🟠",
    "range": "⚪",
}
phase_label = {
    "uptrend": "Uptrend",
    "base_building": "Base",
    "downtrend_active": "Downtrend",
    "downtrend_recovery": "Recovery",
    "range": "Range",
}

def sort_key(r):
    phase = r.get("phase", "range")
    priority = {
        "base_building": 0,
        "uptrend": 1,
        "downtrend_recovery": 2,
        "range": 3,
        "downtrend_active": 4,
    }.get(phase, 5)
    in_box = float(r.get("in_box_pct") or 100)
    drop = float(r.get("drop_from_peak_pct") or 0)
    return (priority, in_box, -drop)

sorted_rows = sorted(rows, key=sort_key)

phase_counts = {}
for r in rows:
    p = r.get("phase", "unknown")
    phase_counts[p] = phase_counts.get(p, 0) + 1

html_rows = []
for r in sorted_rows:
    ticker = r["ticker"]
    chart_file = f"charts/{ticker.replace('.', '_').replace('-', '_')}.png"
    phase = r.get("phase", "—")
    emoji = phase_emoji.get(phase, "⚪")
    plabel = phase_label.get(phase, phase)

    html_rows.append(f"""
    <tr data-phase="{phase}" data-ticker="{ticker}">
      <td class="rank">{r.get('rank', '—')}</td>
      <td class="ticker"><a href="{chart_file}?v={data_date}" target="_blank">{ticker}</a></td>
      <td class="name">{r.get('name', '')}</td>
      <td class="num">${fmt_num(r.get('last'))}</td>
      <td class="chg {('pos' if float(r.get('chg_pct') or 0) > 0 else 'neg')}">{fmt_pct(r.get('chg_pct'))}</td>
      <td class="phase phase-{phase}">{emoji} {plabel}</td>
      <td class="num">{fmt_pct(r.get('drop_from_peak_pct'))}</td>
      <td class="num">{fmt_pct(r.get('in_box_pct'))}</td>
      <td class="num">${fmt_num(r.get('s1'))}</td>
      <td class="num">${fmt_num(r.get('r1'))}</td>
      <td class="num">${fmt_num(r.get('r3'))}</td>
      <td class="kline">{r.get('kline', '—')}</td>
      <td class="bias">{r.get('bias_short', '—')}</td>
    </tr>""")

phase_chips = " ".join(
    f'<span class="chip chip-{p}">{phase_label.get(p, p)}: {c}</span>'
    for p, c in sorted(phase_counts.items(), key=lambda x: -x[1])
)

html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>US Top 200 S/R Chart Radar — {data_date}</title>
<meta name="description" content="Daily S/R analysis for top 200 US stocks by 5-day average turnover. Generated {datetime.now().strftime('%Y-%m-%d %H:%M HKT')}.">
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background: #0d0f12; color: #e0e3e8; padding: 20px; line-height: 1.5; }}
  .container {{ max-width: 1400px; margin: 0 auto; }}
  header {{ background: #161a1f; padding: 20px; border-radius: 8px; margin-bottom: 20px; border: 1px solid #1f2429; }}
  h1 {{ font-size: 1.6em; margin-bottom: 6px; }}
  .meta {{ color: #8a929b; font-size: 0.9em; margin-bottom: 12px; }}
  .chips {{ display: flex; flex-wrap: wrap; gap: 8px; margin-top: 8px; }}
  .chip {{ display: inline-block; padding: 4px 10px; border-radius: 12px; font-size: 0.85em; background: #1f2429; }}
  .chip-uptrend {{ background: #1b4d2c; color: #4ade80; }}
  .chip-base_building {{ background: #4d3a1b; color: #fbbf24; }}
  .chip-downtrend_active {{ background: #4d1b1b; color: #f87171; }}
  .chip-downtrend_recovery {{ background: #4d3a1b; color: #fb923c; }}
  .chip-range {{ background: #2a2f37; color: #8a929b; }}

  .filter-bar {{ background: #161a1f; padding: 12px; border-radius: 8px; margin-bottom: 12px; display: flex; flex-wrap: wrap; gap: 8px; align-items: center; }}
  .filter-bar input, .filter-bar select {{ background: #0d0f12; color: #e0e3e8; border: 1px solid #1f2429; padding: 6px 10px; border-radius: 4px; font-size: 0.9em; }}
  .filter-bar input {{ min-width: 200px; }}
  .filter-bar label {{ font-size: 0.85em; color: #8a929b; }}

  table {{ width: 100%; border-collapse: collapse; background: #161a1f; border-radius: 8px; overflow: hidden; }}
  thead {{ background: #1f2429; }}
  th {{ padding: 10px 8px; text-align: left; font-size: 0.85em; color: #8a929b; text-transform: uppercase; cursor: pointer; user-select: none; }}
  th:hover {{ color: #e0e3e8; }}
  th::after {{ content: ' ↕'; opacity: 0.4; font-size: 0.7em; }}
  td {{ padding: 8px; border-top: 1px solid #1f2429; font-size: 0.9em; }}
  tr:hover {{ background: #1a1f25; }}
  .rank {{ color: #8a929b; font-size: 0.85em; }}
  .ticker a {{ color: #64b5f6; text-decoration: none; font-weight: 500; }}
  .ticker a:hover {{ text-decoration: underline; }}
  .name {{ color: #b0b8c0; font-size: 0.85em; }}
  .num {{ text-align: right; font-variant-numeric: tabular-nums; }}
  .chg.pos {{ color: #4ade80; }}
  .chg.neg {{ color: #f87171; }}
  .phase {{ font-size: 0.85em; }}
  .phase-uptrend {{ color: #4ade80; }}
  .phase-base_building {{ color: #fbbf24; }}
  .phase-downtrend_active {{ color: #f87171; }}
  .phase-downtrend_recovery {{ color: #fb923c; }}
  .kline {{ font-size: 0.8em; color: #8a929b; }}
  .bias {{ font-size: 0.8em; color: #b0b8c0; }}

  footer {{ margin-top: 30px; text-align: center; color: #8a929b; font-size: 0.85em; padding: 20px; }}
  footer a {{ color: #64b5f6; text-decoration: none; }}
  .disclaimer {{ background: #1f2429; padding: 12px; border-radius: 4px; margin-top: 12px; font-size: 0.85em; color: #8a929b; }}
</style>
</head>
<body>
<div class="container">
  <header>
    <h1>🇺🇸 US Top 200 S/R Chart Radar</h1>
    <div class="meta">
      Data: <strong>{display_date}</strong> • Generated {datetime.now().strftime('%Y-%m-%d %H:%M HKT')} • Source: yfinance • Method: 10-step price action framework
    </div>
    <div class="chips">
      {phase_chips}
    </div>
    <nav class="site-nav" style="margin-top:12px;padding-top:12px;border-top:1px solid #1f2429;font-size:0.9em;">
      <a href="/" style="color:#8a929b;text-decoration:none;margin-right:14px;">Home</a>
      <a href="/dashboard/" style="color:#8a929b;text-decoration:none;margin-right:14px;">Dashboard</a>
      <a href="/full-results.html" style="color:#8a929b;text-decoration:none;margin-right:14px;">Full Results</a>
      <a href="/hk200/" style="color:#8a929b;text-decoration:none;margin-right:14px;">📊 HK200 S/R</a>
      <a href="/us200/" style="color:#64b5f6;text-decoration:none;margin-right:14px;font-weight:600;">🇺🇸 US200 S/R</a>
      <a href="/methodology.html" style="color:#8a929b;text-decoration:none;margin-right:14px;">Methodology</a>
    </nav>
  </header>

  <div class="filter-bar">
    <label>Filter:</label>
    <input type="text" id="searchInput" placeholder="Search ticker or name..." onkeyup="filterTable()">
    <label>Phase:</label>
    <select id="phaseFilter" onchange="filterTable()">
      <option value="">All</option>
      <option value="uptrend">🟢 Uptrend</option>
      <option value="base_building">🟡 Base</option>
      <option value="downtrend_recovery">🟠 Recovery</option>
      <option value="downtrend_active">🔴 Downtrend</option>
      <option value="range">⚪ Range</option>
    </select>
    <label style="margin-left:auto;color:#8a929b;">{len(rows)} of 200 stocks</label>
  </div>

  <table id="dataTable">
    <thead>
      <tr>
        <th onclick="sortTable(0)">#</th>
        <th onclick="sortTable(1)">Ticker</th>
        <th onclick="sortTable(2)">Name</th>
        <th onclick="sortTable(3)">Last</th>
        <th onclick="sortTable(4)">Chg%</th>
        <th onclick="sortTable(5)">Phase</th>
        <th onclick="sortTable(6)">Drop%</th>
        <th onclick="sortTable(7)">InBox%</th>
        <th onclick="sortTable(8)">S1</th>
        <th onclick="sortTable(9)">R1</th>
        <th onclick="sortTable(10)">R3</th>
        <th onclick="sortTable(11)">K-line</th>
        <th onclick="sortTable(12)">Bias</th>
      </tr>
    </thead>
    <tbody>
      {''.join(html_rows)}
    </tbody>
  </table>

  <div class="disclaimer">
    <strong>⚠️ Disclaimer:</strong> All content is for informational and educational purposes only. Not investment advice.
    The 10-step price action framework (phase → S/R ladder → bias → scenarios) is technical analysis, not financial recommendation.
    Click any ticker to view the full candlestick chart with S/R levels.
  </div>

  <footer>
    <a href="/">← win9you.com home</a> • <a href="/hk200/">📊 HK200 S/R</a> • <a href="/dashboard">Dashboard</a><br>
    Generated by Mavis daily-sr-chart skill • <a href="https://github.com/kenkenlui-ctrl/Trading">Source</a>
  </footer>
</div>

<script>
  function filterTable() {{
    const search = document.getElementById('searchInput').value.toLowerCase();
    const phase = document.getElementById('phaseFilter').value;
    const rows = document.querySelectorAll('#dataTable tbody tr');
    let visible = 0;
    rows.forEach(row => {{
      const ticker = row.cells[1].textContent.toLowerCase();
      const name = row.cells[2].textContent.toLowerCase();
      const rowPhase = row.getAttribute('data-phase');
      const matchSearch = !search || ticker.includes(search) || name.includes(search);
      const matchPhase = !phase || rowPhase === phase;
      const show = matchSearch && matchPhase;
      row.style.display = show ? '' : 'none';
      if (show) visible++;
    }});
    document.querySelector('.filter-bar label:last-child').textContent = visible + ' of 200 stocks';
  }}

  function sortTable(n) {{
    const table = document.getElementById('dataTable');
    const rows = Array.from(table.tbody.querySelectorAll('tr'));
    const dir = table.getAttribute('data-sort-' + n) === 'asc' ? 'desc' : 'asc';
    table.setAttribute('data-sort-' + n, dir);
    rows.sort((a, b) => {{
      const av = a.cells[n].textContent.trim();
      const bv = b.cells[n].textContent.trim();
      const an = parseFloat(av.replace(/[+%,\\s$]/g, ''));
      const bn = parseFloat(bv.replace(/[+%,\\s$]/g, ''));
      const isNum = !isNaN(an) && !isNaN(bn);
      if (isNum) return dir === 'asc' ? an - bn : bn - an;
      return dir === 'asc' ? av.localeCompare(bv) : bv.localeCompare(av);
    }});
    rows.forEach(r => table.tbody.appendChild(r));
  }}
</script>
</body>
</html>
"""

(DEST_DIR / "index.html").write_text(html, encoding="utf-8")
print(f"index.html → {DEST_DIR / 'index.html'}")
print(f"  size: {len(html):,} bytes")
print(f"  rows: {len(html_rows)}")

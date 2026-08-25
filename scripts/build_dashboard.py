#!/usr/bin/env python3
"""
build_dashboard.py — Build the action plan dashboards and per-ticker detail pages
for win9you.com (HK + US).

Reads:
  - /Users/kenken/Documents/Minimax/charts/hk200/  (per-ticker PNGs + JSON from daily-sr-chart)
  - /tmp/backtest_out/                              (per-ticker backtest JSON from backtest_futu.py)
  - hk_universe_200.json                            (universe list)

Writes:
  - /Users/kenken/Documents/dsa-hk/public/hk200/index.html
  - /Users/kenken/Documents/dsa-hk/public/hk200/ticker/<safe>.html
  - /Users/kenken/Documents/dsa-hk/public/us200/index.html
  - /Users/kenken/Documents/dsa-hk/public/us200/ticker/<safe>.html
  - /Users/kenken/Documents/dsa-hk/public/index.html (home)
"""
from __future__ import annotations
import json
import shutil
from datetime import datetime, date, timedelta
from pathlib import Path


def t_minus_1() -> date:
    """Return T-1 = last trading day (skip Sat/Sun)."""
    today = date.today()
    if today.weekday() == 0:  # Monday
        return today - timedelta(days=3)  # Friday
    elif today.weekday() == 6:  # Sunday
        return today - timedelta(days=2)  # Friday
    elif today.weekday() == 5:  # Saturday
        return today - timedelta(days=1)  # Friday
    else:
        return today - timedelta(days=1)

# Repo paths
REPO = Path("/Users/kenken/Documents/dsa-hk")
PUBLIC = REPO / "public"
HK_OUT = Path("/Users/kenken/Documents/Minimax/charts/hk200")
US_OUT = Path("/Users/kenken/Documents/Minimax/charts/us200")
BACKTEST_OUT = Path("/tmp/backtest_out")

UNIVERSE_HK = REPO / "hk_universe_200.json"
US_UNIVERSE = US_OUT / "us_top200_fresh.json"


def load_universe(path: Path) -> list[str]:
    if not path.exists():
        print(f"⚠ universe not found: {path}")
        return []
    return json.load(open(path))


def load_ticker_json(ticker: str, market: str) -> dict | None:
    """Load per-ticker action_plan JSON written by daily-sr-chart skill."""
    safe = ticker.replace(".", "_")
    base = HK_OUT if market == "HK" else US_OUT
    p = base / f"{safe}.json"
    if not p.exists():
        return None
    try:
        return json.load(open(p))
    except Exception:
        return None


def load_backtest(ticker: str) -> dict | None:
    """Load per-ticker backtest JSON written by backtest_futu.py."""
    safe = ticker.replace(".", "_")
    p = BACKTEST_OUT / f"{safe}.json"
    if not p.exists():
        return None
    try:
        return json.load(open(p))
    except Exception:
        return None


def pick_recent_edge(bt: dict | None, recent_windows=("60", "90", "30", "197")) -> dict | None:
    """Pick the highest-win% strategy from recent windows, with min n=3.
    Falls back to 197 if no recent window qualifies.
    """
    if not bt or not bt.get("windows"):
        return None
    for w in recent_windows:
        wd = bt["windows"].get(w)
        if not wd:
            continue
        for strat, data in wd.items():
            n = data.get("n", 0)
            win = data.get("win_pct", 0)
            tot = data.get("total_pnl", 0)
            if n >= 3 and tot > 0:
                return {
                    "strategy": strat,
                    "window": w,
                    "win_pct": win,
                    "n": n,
                    "total_pnl": tot,
                    "avg_pnl": data.get("avg_pnl", 0),
                }
    return None


def render_action_row(t: dict) -> str:
    """Render a single table row for the action plan table."""
    ticker = t["ticker"]
    safe = ticker.replace(".", "_")
    name = t.get("name", "")
    last = t.get("last", 0)
    chg = t.get("chg_pct", 0)
    phase = t.get("phase", "range")
    ap = t.get("action_plan", {})
    verdict = ap.get("verdict", "WAIT")
    trigger = ap.get("trigger_price")
    target = ap.get("target_price")
    stop = ap.get("stop_price")
    edge = t.get("edge")  # {strategy, win_pct, n, total_pnl, window}
    detail_url = t["detail_url"]

    phase_short = {
        "uptrend": ("Uptrend", "tag-bull"),
        "base_building": ("Base", "tag-amber"),
        "downtrend_active": ("DT", "tag-bear"),
        "downtrend_recovery": ("Recov", "tag-amber"),
        "range": ("Range", "tag-dim"),
    }.get(phase, (phase, "tag-dim"))
    phase_label, phase_class = phase_short

    action_class = {"BUY": "bull", "SELL": "bear", "WAIT": "wait"}.get(verdict, "wait")
    row_class = {"BUY": "row-bull", "SELL": "row-bear", "WAIT": "row-wait"}.get(verdict, "row-wait")

    def fmt(x, dp=2):
        if x is None:
            return "—"
        try:
            return f"{float(x):,.{dp}f}"
        except Exception:
            return str(x)

    def fmt_pct(x):
        if x is None:
            return "—"
        try:
            return f"{float(x):+.2f}%"
        except Exception:
            return "—"

    if verdict == "WAIT":
        trigger_s = target_s = stop_s = "—"
        win_s = "—"
        strat_s = "RANGE"
    else:
        trigger_s = fmt(trigger)
        target_s = fmt(target, dp=2 if last and last < 100 else (1 if last and last < 1000 else 0))
        stop_s = fmt(stop, dp=2 if last and last < 100 else (1 if last and last < 1000 else 0))
        if edge:
            win_s = f"{edge['win_pct']:.0f}%"
            strat_s = edge["strategy"]
        else:
            win_s = "—"
            strat_s = "—"

    chg_cls = "text-bear" if chg and chg < 0 else ("text-bull" if chg and chg > 0 else "")
    return f"""
    <tr data-action="{verdict}" data-phase="{phase}" class="{row_class}">
      <td><a href="{detail_url}" class="mono" style="color: var(--fg);">{ticker}</a></td>
      <td>{name}</td>
      <td class="cell-right mono">{fmt(last)}</td>
      <td class="cell-right mono {chg_cls}">{fmt_pct(chg)}</td>
      <td><span class="tag {phase_class}">{phase_label}</span></td>
      <td><span class="cell-action {action_class}">{verdict}</span></td>
      <td class="cell-right mono">{trigger_s}</td>
      <td class="cell-right mono text-bull">{target_s}</td>
      <td class="cell-right mono text-bear">{stop_s}</td>
      <td class="cell-right mono">{win_s}</td>
      <td class="mono text-dim">{strat_s}</td>
    </tr>"""


def build_dashboard_page(
    market: str,
    universe: list[str],
    rows_html: str,
    title_kicker: str,
    title_main: str,
    title_lede: str,
    hero_meta_extra: str,
    color_theme: str = "bull",
) -> str:
    nav_links = f'''
      <a href="/">Home</a>
      <a href="/hk200/"{' class="active"' if market == "HK" else ''}>HK Signals</a>
      <a href="/us200/"{' class="active"' if market == "US" else ''}>US Signals</a>
      <a href="/pair-trades/">Pair Trades</a>
      <a href="/methodology">Methodology</a>
    '''

    return f"""<!DOCTYPE html>
<html lang="zh-Hant-HK">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{title_main} · Leeks Terminal</title>
<meta name="description" content="{title_lede}">
<meta name="theme-color" content="#0a0e1a">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Fraunces:ital,opsz,wght,SOFT@0,9..144,300..600,0..100;1,9..144,300..600,0..100&family=JetBrains+Mono:wght@400;500;600;700&family=Manrope:wght@400;500;600;700&display=swap">
<link rel="stylesheet" href="/leeks.css">
<script>
(function(){{
  const t = localStorage.getItem('leeks-theme') || 'dark';
  document.documentElement.setAttribute('data-theme', t);
}})();
</script>
</head>
<body>
<header class="site-header">
  <nav class="nav">
    <a href="/" class="nav-brand"><span class="leek">L</span>eeks <em class="italic">Terminal</em></a>
    <div class="nav-links">{nav_links}</div>
    <div class="nav-meta">
      <span class="live-dot"></span>
      T-1 · {t_minus_1().isoformat()}
      <button class="theme-toggle" onclick="toggleTheme()" aria-label="Toggle theme">
        <span class="icon" id="themeIcon">●</span>
        <span id="themeLabel">DARK</span>
      </button>
    </div>
  </nav>
</header>
<script>
function toggleTheme(){{
  const cur = document.documentElement.getAttribute('data-theme') || 'dark';
  const next = cur === 'dark' ? 'light' : 'dark';
  document.documentElement.setAttribute('data-theme', next);
  localStorage.setItem('leeks-theme', next);
  const meta = document.querySelector('meta[name="theme-color"]');
  if (meta) meta.content = next === 'dark' ? '#0a0e1a' : '#fafbfc';
  const lbl = document.getElementById('themeLabel');
  const ic = document.getElementById('themeIcon');
  if (lbl) lbl.textContent = next.toUpperCase();
  if (ic) ic.textContent = next === 'dark' ? '●' : '○';
}}
(function(){{
  const t = document.documentElement.getAttribute('data-theme') || 'dark';
  const lbl = document.getElementById('themeLabel');
  const ic = document.getElementById('themeIcon');
  if (lbl) lbl.textContent = t.toUpperCase();
  if (ic) ic.textContent = t === 'dark' ? '●' : '○';
}})();
</script>

<div class="page-head">
  <div class="container">
    <div class="kicker">{title_kicker}</div>
    <h1>{title_main.split(' · ')[0]} <em class="italic">Action Plan</em></h1>
    <p class="lede">{title_lede}</p>
    <div class="hero-meta">
      {hero_meta_extra}
    </div>
  </div>
</div>

<div class="container">
  <div class="filter-bar">
    <label>Filter</label>
    <input type="text" id="search" placeholder="Search ticker or name..." onkeyup="filterTable()">
    <label>Action</label>
    <div style="display: flex; gap: 6px;">
      <span class="filter-chip active" data-action="">All</span>
      <span class="filter-chip" data-action="BUY">🟢 BUY</span>
      <span class="filter-chip" data-action="SELL">🔴 SELL</span>
      <span class="filter-chip" data-action="WAIT">⚪ WAIT</span>
    </div>
    <label>Phase</label>
    <div style="display: flex; gap: 6px;">
      <span class="filter-chip active" data-phase="">All</span>
      <span class="filter-chip" data-phase="uptrend">Uptrend</span>
      <span class="filter-chip" data-phase="base_building">Base</span>
      <span class="filter-chip" data-phase="downtrend_recovery">Recovery</span>
      <span class="filter-chip" data-phase="downtrend_active">Down</span>
      <span class="filter-chip" data-phase="range">Range</span>
    </div>
    <label style="margin-left: auto;" id="rowCount">{len(universe)} of {len(universe)}</label>
  </div>

  <div style="overflow-x: auto; border: 1px solid var(--border); border-radius: var(--radius-lg);">
  <table class="data-table" id="dataTable">
  <thead>
  <tr>
    <th data-col="ticker">Ticker</th>
    <th data-col="name">Name</th>
    <th data-col="last" class="cell-right">Last</th>
    <th data-col="chg" class="cell-right">Chg%</th>
    <th data-col="phase">Phase</th>
    <th data-col="action">Action</th>
    <th data-col="trigger" class="cell-right">Trigger</th>
    <th data-col="target" class="cell-right">Target</th>
    <th data-col="stop" class="cell-right">Stop</th>
    <th data-col="win" class="cell-right">Win% (90d)</th>
    <th data-col="strategy">Strategy</th>
  </tr>
  </thead>
  <tbody id="tableBody">{rows_html}
  </tbody>
  </table>
  </div>

  <div class="disclaimer mt-5">
    <b>⚠ Disclaimer</b> · Educational only. Not investment advice. T-1 data (yesterday's close) — never today's intraday. Backtest win% is historical, not predictive.
  </div>
</div>

<footer class="site-footer">
  <div class="container">
    <div class="row">
      <div>© 2026 Leeks Terminal · win9you.com · <a href="/methodology">Methodology</a> · <a href="/disclaimer">Disclaimer</a></div>
      <div class="mono text-dim">T-1 · {t_minus_1().isoformat()}</div>
    </div>
  </div>
</footer>

<script>
let currentAction = '';
let currentPhase = '';
document.querySelectorAll('.filter-chip[data-action]').forEach(c => {{
  c.addEventListener('click', () => {{
    document.querySelectorAll('.filter-chip[data-action]').forEach(x => x.classList.remove('active'));
    c.classList.add('active');
    currentAction = c.dataset.action;
    filterTable();
  }});
}});
document.querySelectorAll('.filter-chip[data-phase]').forEach(c => {{
  c.addEventListener('click', () => {{
    document.querySelectorAll('.filter-chip[data-phase]').forEach(x => x.classList.remove('active'));
    c.classList.add('active');
    currentPhase = c.dataset.phase;
    filterTable();
  }});
}});

function filterTable() {{
  const search = document.getElementById('search').value.toLowerCase();
  const rows = document.querySelectorAll('#tableBody tr[data-action]');
  let visible = 0;
  rows.forEach(row => {{
    const ticker = row.cells[0]?.textContent.toLowerCase() || '';
    const name = row.cells[1]?.textContent.toLowerCase() || '';
    const action = row.dataset.action;
    const phase = row.dataset.phase;
    const matchSearch = !search || ticker.includes(search) || name.includes(search);
    const matchAction = !currentAction || action === currentAction;
    const matchPhase = !currentPhase || phase === currentPhase;
    const show = matchSearch && matchAction && matchPhase;
    row.style.display = show ? '' : 'none';
    if (show) visible++;
  }});
  document.getElementById('rowCount').textContent = visible + ' of {len(universe)}';
}}

document.querySelectorAll('th[data-col]').forEach(th => {{
  th.addEventListener('click', () => {{
    const idx = Array.from(th.parentNode.children).indexOf(th);
    const dir = th.classList.contains('sorted-asc') ? 'desc' : 'asc';
    document.querySelectorAll('th').forEach(x => x.classList.remove('sorted-asc','sorted-desc'));
    th.classList.add('sorted-' + dir);
    const tbody = document.getElementById('tableBody');
    const rows = Array.from(tbody.querySelectorAll('tr[data-action]'));
    rows.sort((a, b) => {{
      const av = a.cells[idx]?.textContent.trim() || '';
      const bv = b.cells[idx]?.textContent.trim() || '';
      const an = parseFloat(av.replace(/[+%,$ HKD\s]/g, ''));
      const bn = parseFloat(bv.replace(/[+%,$ HKD\s]/g, ''));
      const isNum = !isNaN(an) && !isNaN(bn);
      if (isNum) return dir === 'asc' ? an - bn : bn - an;
      return dir === 'asc' ? av.localeCompare(bv) : bv.localeCompare(av);
    }});
    rows.forEach(r => tbody.appendChild(r));
  }});
}});
</script>
</body>
</html>"""


def render_detail_page(t: dict) -> str:
    """Render a single ticker detail page."""
    ticker = t["ticker"]
    safe = ticker.replace(".", "_")
    name = t.get("name", "")
    last = t.get("last", 0)
    chg = t.get("chg_pct", 0)
    phase = t.get("phase", "range")
    ap = t.get("action_plan", {})
    verdict = ap.get("verdict", "WAIT")
    edge = t.get("edge")
    chart_url = t["chart_url"]
    market = t["market"]
    detail_url = t["detail_url"]
    back_url = "/hk200/" if market == "HK" else "/us200/"

    phase_label_map = {
        "uptrend": ("Uptrend", "var(--bull)"),
        "base_building": ("Base building", "var(--amber)"),
        "downtrend_active": ("Downtrend active", "var(--bear)"),
        "downtrend_recovery": ("Downtrend recovery", "var(--amber)"),
        "range": ("Range", "var(--dim)"),
    }
    phase_label, phase_color = phase_label_map.get(phase, (phase, "var(--fg)"))

    trigger = ap.get("trigger_price")
    target = ap.get("target_price")
    stop = ap.get("stop_price")
    risk = ap.get("risk_pct")
    reward = ap.get("reward_pct")
    rr = ap.get("rr_ratio")
    notes = ap.get("notes", "")

    r1 = ap.get("r1")
    s1 = ap.get("s1")
    r3 = ap.get("r3")
    s3 = ap.get("s3")
    s4 = ap.get("s4")

    def fmt(x, dp=2):
        if x is None:
            return "—"
        try:
            return f"{float(x):,.{dp}f}"
        except Exception:
            return str(x)

    def fmt_pct(x):
        if x is None:
            return "—"
        try:
            return f"{float(x):+.2f}%"
        except Exception:
            return "—"

    if edge:
        edge_str = f"{edge['strategy']} · {edge['win_pct']:.0f}% win ({edge['window']}d, n={edge['n']}) · {edge['total_pnl']:+.1f}%"
        reliability = "HIGH" if edge["n"] >= 10 and edge["win_pct"] >= 60 else ("MED" if edge["n"] >= 5 else "LOW")
        rel_color = "var(--bull)" if reliability == "HIGH" else ("var(--amber)" if reliability == "MED" else "var(--bear)")
    else:
        edge_str = "No edge in recent regime"
        reliability = "—"
        rel_color = "var(--dim)"

    if verdict == "BUY":
        verdict_color = "var(--bull)"
    elif verdict == "SELL":
        verdict_color = "var(--bear)"
    else:
        verdict_color = "var(--dim)"

    chg_str = fmt_pct(chg)
    chg_color = "var(--bear)" if chg and chg < 0 else ("var(--bull)" if chg and chg > 0 else "var(--fg)")

    scenarios_html = f'''
        <li><b>IF</b> {("反彈至 " + fmt(r1)) if r1 else "反彈至 R1"} 區間, <b>THEN</b> SELL_R1 entry，target {fmt(s1) if s1 else "mid"}, stop {(fmt(r1 * 1.02) if r1 else "—")}</li>
        <li><b>IF</b> 失守 {fmt(s1) if s1 else "S1"} (S1), <b>THEN</b> SHORT confirmation，target {fmt(s1 * 0.99) if s1 else "—"} → {fmt(s1 * 0.985) if s1 else "—"}</li>
        <li><b>IF</b> 突破 {fmt(r1 * 1.02) if r1 else "—"} 企穩, <b>THEN</b> 反轉睇 {fmt(r1 * 1.05) if r1 else "—"} → {fmt(r3) if r3 else "R3"}</li>
        <li><b>IF</b> 跌穿 {fmt(s4) if s4 else "S4"} (crash low), <b>THEN</b> 中期 bear confirm，avoid</li>
    '''

    return f"""<!DOCTYPE html>
<html lang="zh-Hant-HK">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{ticker} {name} · Action Plan · Leeks Terminal</title>
<meta name="description" content="{ticker} {name} 即日鮮信號 · T-1 數據 · 10 步框架。">
<meta name="theme-color" content="#0a0e1a">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Fraunces:ital,opsz,wght,SOFT@0,9..144,300..600,0..100;1,9..144,300..600,0..100&family=JetBrains+Mono:wght@400;500;600;700&family=Manrope:wght@400;500;600;700&display=swap">
<link rel="stylesheet" href="/leeks.css">
</head>
<body>
<header class="site-header">
  <nav class="nav">
    <a href="/" class="nav-brand"><span class="leek">L</span>eeks <em class="italic">Terminal</em></a>
    <div class="nav-links">
      <a href="/">Home</a>
      <a href="/hk200/"{' class="active"' if market == "HK" else ''}>HK Signals</a>
      <a href="/us200/"{' class="active"' if market == "US" else ''}>US Signals</a>
      <a href="/pair-trades/">Pair Trades</a>
      <a href="/methodology">Methodology</a>
    </div>
    <div class="nav-meta">
      <span class="live-dot"></span>
      T-1 · {t_minus_1().isoformat()}
    </div>
  </nav>
</header>

<div class="page-head">
  <div class="container">
    <a href="{back_url}" style="font-size: var(--text-sm);" class="text-dim">← Back to {market} Signals</a>
    <div class="kicker" style="margin-top: var(--sp-3);">{market} · {name}</div>
    <h1 style="font-size: var(--text-4xl);">
      {ticker}
      <span style="margin-left: var(--sp-3); color: {chg_color};">{fmt(last)}</span>
      <span class="mono" style="font-size: var(--text-2xl); margin-left: var(--sp-2); color: {chg_color};">{chg_str}</span>
    </h1>
    <p class="lede">
      {name} · {edge_str} · Phase: {phase_label}
    </p>
    <div class="hero-meta">
      <span>Phase: <b style="color: {phase_color};">{phase_label}</b></span>
      <span>Action: <b style="color: {verdict_color};">{verdict}</b></span>
      <span>Strategy: <b>{edge["strategy"] if edge else "—"}</b></span>
      <span>Reliability: <b style="color: {rel_color};">{reliability}</b></span>
    </div>
  </div>
</div>

<div class="container">
  <div class="detail-grid">
    <div>
      <div class="chart-frame fade-in">
        <div class="skeleton" id="chartSkel_{safe}"></div>
        <img src="{chart_url}?v={t_minus_1().isoformat()}" alt="{ticker} daily K" loading="eager" onload="document.getElementById('chartSkel_{safe}')?.remove()" onerror="this.parentElement.innerHTML='<div class=&quot;text-dim&quot; style=&quot;padding:var(--sp-7);text-align:center&quot;>Chart unavailable · try refresh</div>'">
      </div>

      <div class="info-card mt-4 fade-in fade-in-2">
        <h3>10-step framework</h3>
        <div class="phase-block">
          <span class="name">① Phase</span>
          <span class="value" style="color: {phase_color};">{phase_label}</span>
        </div>
        <div class="phase-block">
          <span class="name">② Framework</span>
          <span class="value">20-day rolling box + 60-day trend</span>
        </div>
        <div class="phase-block">
          <span class="name">③ S/R ladder</span>
          <span class="value">S1 {fmt(s1)} · S2 {fmt(s1 * 0.99) if s1 else "—"} · S3 {fmt(s3)} · S4 {fmt(s4)}</span>
        </div>
        <div class="phase-block">
          <span class="name">④ Position</span>
          <span class="value">{ap.get("current_position_pct", 50):.0f}% of box</span>
        </div>
        <div class="phase-block">
          <span class="name">⑤ Bias (短)</span>
          <span class="value" style="color: {verdict_color};">{verdict}</span>
        </div>
        <div class="phase-block">
          <span class="name">⑥ Notes</span>
          <span class="value" style="font-size: var(--text-xs); color: var(--dim);">{notes}</span>
        </div>
      </div>

      <div class="info-card mt-4 fade-in fade-in-3">
        <h3>If-then scenarios</h3>
        <ul class="scenario-list">{scenarios_html}
        </ul>
      </div>
    </div>

    <div>
      <div class="info-card fade-in" style="border-left: 3px solid {verdict_color};">
        <h3 style="display: flex; justify-content: space-between; align-items: center;">
          <span>Action Plan</span>
          <span class="cell-action" style="font-family: var(--font-mono); font-size: var(--text-md); color: {verdict_color};">{verdict}</span>
        </h3>
        <div class="phase-block">
          <span class="name">Strategy</span>
          <span class="value">{edge["strategy"] if edge else "WAIT_RANGE"}</span>
        </div>
        <div class="phase-block">
          <span class="name">Trigger</span>
          <span class="value" style="color: {verdict_color};">{fmt(trigger)}</span>
        </div>
        <div class="phase-block">
          <span class="name">Target</span>
          <span class="value text-bull">{fmt(target)}</span>
        </div>
        <div class="phase-block">
          <span class="name">Stop</span>
          <span class="value text-bear">{fmt(stop)}</span>
        </div>
        <div class="phase-block">
          <span class="name">Risk</span>
          <span class="value text-bear">{fmt_pct(risk)}</span>
        </div>
        <div class="phase-block">
          <span class="name">Reward</span>
          <span class="value text-bull">{fmt_pct(reward)}</span>
        </div>
        <div class="phase-block">
          <span class="name">R:R</span>
          <span class="value text-accent">{fmt(rr, 2)} : 1</span>
        </div>
        {f'''<div class="phase-block" style="background: var(--bull-dim);">
          <span class="name" style="color: var(--bull);">Backtest {edge["window"]}d</span>
          <span class="value text-bull">{edge["win_pct"]:.0f}% win (n={edge["n"]}) · {edge["total_pnl"]:+.1f}%</span>
        </div>''' if edge else ''}
      </div>

      <div class="info-card mt-4 fade-in fade-in-2">
        <h3>Key levels</h3>
        <dl>
          <dt>R3 (Aug high)</dt><dd>{fmt(r3)}</dd>
          <dt>R1 (today high)</dt><dd>{fmt(r1)}</dd>
          <dt>Last</dt><dd style="color: {chg_color};">{fmt(last)}</dd>
          <dt>S1 KEY</dt><dd class="text-bull">{fmt(s1)}</dd>
          <dt>S3</dt><dd>{fmt(s3)}</dd>
          <dt>S4 (crash)</dt><dd>{fmt(s4)}</dd>
        </dl>
      </div>

      <div class="disclaimer mt-4">
        <b>⚠</b> 教育用途 only。Backtest 唔等於 live 表現。Always size your position to risk 1-2% of account.
      </div>
    </div>
  </div>

  <div class="mt-5 mb-5">
    <a href="{back_url}" class="btn btn-ghost">← Back to {market} Signals</a>
    <a href="/methodology" class="btn btn-ghost" style="margin-left: var(--sp-2);">Methodology</a>
  </div>
</div>

<footer class="site-footer">
  <div class="container">
    <div class="row">
      <div>© 2026 Leeks Terminal · win9you.com · <a href="/methodology">Methodology</a> · <a href="/disclaimer">Disclaimer</a></div>
      <div class="mono text-dim">T-1 · {t_minus_1().isoformat()}</div>
    </div>
  </div>
</footer>
</body>
</html>"""


def build_for_market(market: str):
    if market == "HK":
        universe = load_universe(UNIVERSE_HK)
        chart_dir = PUBLIC / "hk200" / "charts"
        src_dir = HK_OUT
        out_index = PUBLIC / "hk200" / "index.html"
        ticker_dir = PUBLIC / "hk200" / "ticker"
        market_name = "HK"
        title_kicker = "HK · 200 names"
        title_lede = "Top 200 港股 by 5-day 平均成交額。即日鮮信號 — 一表答晒：邊隻做、邊隻唔做、trigger/target/stop 喺邊。"
    else:
        universe = []
        if US_UNIVERSE.exists():
            universe = json.load(open(US_UNIVERSE))
        elif (US_OUT / "us_top200.json").exists():
            universe = json.load(open(US_OUT / "us_top200.json"))
        chart_dir = PUBLIC / "us200" / "charts"
        src_dir = US_OUT
        out_index = PUBLIC / "us200" / "index.html"
        ticker_dir = PUBLIC / "us200" / "ticker"
        market_name = "US"
        title_kicker = "US · 200 names"
        title_lede = "Top 200 美股 by 5-day 平均成交額。即日鮮信號 — 一表答晒：邊隻做、邊隻唔做、trigger/target/stop 喺邊。"

    if not universe:
        print(f"⚠ {market}: empty universe, skip")
        return

    print(f"\n=== {market}: building {len(universe)} tickers ===")

    # Phase counts for hero
    phase_counts = {}
    action_counts = {"BUY": 0, "SELL": 0, "WAIT": 0}
    rows = []
    detail_pages = []
    chart_copied = 0

    for i, ticker in enumerate(universe):
        tj = load_ticker_json(ticker, market_name)
        bt = load_backtest(ticker)
        if not tj:
            # No JSON — skip but log
            continue

        last_bar = tj.get("last_bar", {})
        last = last_bar.get("C", 0)
        chg = last_bar.get("chg_pct", 0)
        name = tj.get("name", "")
        phase = tj.get("phase", "range")
        ap = tj.get("action_plan", {})
        verdict = ap.get("verdict", "WAIT")

        edge = pick_recent_edge(bt)
        if not edge:
            # fallback to backtest_futu best_strategy
            if bt and bt.get("best_strategy"):
                bs = bt["best_strategy"]
                edge = {
                    "strategy": bs["name"],
                    "window": bs["window"],
                    "win_pct": bs["win_pct"],
                    "n": bs["n_trades"],
                    "total_pnl": bs["total_pnl"],
                    "avg_pnl": bs.get("avg_pnl", 0),
                }

        safe = ticker.replace(".", "_")
        chart_url = f"/{market.lower()}200/charts/{safe}.png"

        row = {
            "ticker": ticker,
            "name": name,
            "last": last,
            "chg_pct": chg,
            "phase": phase,
            "action_plan": ap,
            "edge": edge,
            "detail_url": f"/{market.lower()}200/ticker/{safe}/",
            "chart_url": chart_url,
            "market": market_name,
        }
        rows.append(row)
        action_counts[verdict] = action_counts.get(verdict, 0) + 1
        phase_counts[phase] = phase_counts.get(phase, 0) + 1

        # Copy chart PNG
        src_png = src_dir / f"{safe}.png"
        if src_png.exists():
            shutil.copy2(src_png, chart_dir / f"{safe}.png")
            chart_copied += 1

        # Render detail page
        detail_html = render_detail_page(row)
        detail_path = ticker_dir / f"{safe}.html"
        detail_path.write_text(detail_html, encoding="utf-8")
        detail_pages.append(detail_path)

        if (i + 1) % 20 == 0:
            print(f"  [{i+1}/{len(universe)}] {ticker} ...")

    print(f"  {len(rows)} tickers with JSON, {chart_copied} charts copied, {len(detail_pages)} detail pages")

    # Sort rows: actionable first, then by win%, then by ticker
    def row_sort_key(r):
        act = r.get("action_plan", {}).get("verdict", "WAIT")
        act_priority = {"SELL": 0, "BUY": 1, "WAIT": 2}.get(act, 3)
        edge = r.get("edge")
        win = edge["win_pct"] if edge else 0
        return (act_priority, -win, r["ticker"])

    rows_sorted = sorted(rows, key=row_sort_key)

    rows_html = "".join(render_action_row(r) for r in rows_sorted)

    # Hero meta
    phase_str = f"Down {phase_counts.get('downtrend_active', 0)} · Base {phase_counts.get('base_building', 0)} · Recov {phase_counts.get('downtrend_recovery', 0)} · Up {phase_counts.get('uptrend', 0)}"
    reg_color = "var(--bear)" if phase_counts.get("downtrend_active", 0) > phase_counts.get("uptrend", 0) * 5 else "var(--amber)"

    hero_meta_extra = f'''
      <span>Universe: <b>{len(universe)} {market} names</b></span>
      <span>T-1 data: <b>{t_minus_1().isoformat()}</b></span>
      <span>Updated: <b>{datetime.now().strftime('%H:%M HKT')}</b></span>
      <span>Regime: <b style="color: {reg_color};">{phase_str}</b></span>
    '''

    # Build dashboard
    title_main = f"{market} 200"
    if market == "HK":
        title_main = "港股 200"
    else:
        title_main = "美股 200"

    html = build_dashboard_page(
        market=market_name,
        universe=universe,
        rows_html=rows_html,
        title_kicker=title_kicker,
        title_main=title_main,
        title_lede=title_lede,
        hero_meta_extra=hero_meta_extra,
    )
    out_index.write_text(html, encoding="utf-8")
    print(f"  → {out_index} ({out_index.stat().st_size:,} bytes)")
    print(f"  Phase dist: {phase_counts}")
    print(f"  Action dist: {action_counts}")


def main():
    print(f"=== build_dashboard.py · T-1 = {t_minus_1().isoformat()} ===")
    build_for_market("HK")
    build_for_market("US")
    print("\nDone.")


if __name__ == "__main__":
    main()

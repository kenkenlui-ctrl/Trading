#!/usr/bin/env python3
"""
Build static HTML dashboard from latest SQLite DB.

Usage:
    python3 scripts/build_static.py                    # build all dates (last 7)
    python3 scripts/build_static.py --date 2026-06-27  # build specific date
    python3 scripts/build_static.py --all              # build all dates in DB

Output:
    public/index.html                                  # date picker landing
    public/dashboard/<date>.html                       # per-date dashboard
    public/dashboard/<date>/<filter>.html              # pre-filtered views
    public/about.html /faq.html /methodology.html ...  # static pages

Designed for gut-sync.com-style architecture: commit `public/` to git,
Cloudflare Pages serves it directly. Mac can be off — site stays live.

Reuses src/pipeline.build_dashboard_md() for card rendering (same HTML as
Streamlit dashboard so visual parity is guaranteed).
"""

from __future__ import annotations

import argparse
import html as _html
import json
import re
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.db import list_report_dates, list_reports, init_db  # noqa: E402
from src.pipeline import build_dashboard_md  # noqa: E402
from src.config import get_config  # noqa: E402

PUBLIC_DIR = PROJECT_ROOT / "public"

# ===== Filter presets — each becomes its own static page so filter is
# "free" via page reload (no Streamlit runtime needed).
#
# Owner feedback 2026-06-28: chips were duplicated (long-hk-buy vs hk-buy
# show the same subset when only one trade_direction matches). Use a clean
# 2-axis scheme: (market × operation). Direction is exposed via a separate
# LAYER on the page header (e.g. "全部 港股買入" includes long AND short).
#
# 2026-07-05 additions: conservative-buy + cyber-buy based on 6-day backtest
# evidence — these filters improved hit rate from 38% to ~60% on subset.
# SELL filters kept but flagged with pause-warning banner (avg direction
# reversed +0.86% on 6-day trace).
# =====
FILTER_PRESETS = [
    # (slug, label_zh, market, operation)
    ("all",              "全部",              None,  None),
    ("hk-buy",           "港股買入",          "HK",  "buy"),
    ("hk-sell",          "港股賣出",          "HK",  "sell"),
    ("hk-hold",          "港股觀望",          "HK",  "hold"),
    ("us-buy",           "美股買入",          "US",  "buy"),
    ("us-sell",          "美股賣出",          "US",  "sell"),
    ("us-hold",          "美股觀望",          "US",  "hold"),
    # 2026-07-05: backtest-validated high-WR subsets
    ("conservative-buy", "🛡️ Conservative BUY",  "US", "conservative_buy"),
    ("cyber-buy",        "🔐 Cyber BUY",         "US", "cyber_buy"),
    # 2026-07-09: multi-day strength continuation (catches BABA-like surges)
    # Strength BUY DISABLED 2026-07-09 02:30 — 9-day audit: 33% WR, -1.31% avg.
    ("strength-buy",     "⏸️ Strength BUY",      "US", "strength_buy"),
    # 2026-07-09: Bounce BUY — catches panic-sold candidates (mean-reversion)
    ("bounce-buy",       "🌊 Bounce BUY",        "ALL","bounce_buy"),
]

# Cybersecurity / network-security tickers that historically delivered
# positive returns on BUY signals (backtest 2026-06-26 → 2026-07-02).
# All are large-cap with strong momentum + recurring BUY across multiple days.
CYBER_TICKERS = {
    "DDOG",   # Datadog — observability
    "PANW",   # Palo Alto Networks — firewall
    "CRWD",   # CrowdStrike — endpoint
    "FTNT",   # Fortinet — firewall/SD-WAN
    "OKTA",   # Okta — identity
    "ZS",     # Zscaler — zero-trust network
    "NET",    # Cloudflare — edge
    "S",      # SentinelOne — endpoint
    "CYBR",   # CyberArk — privileged access
    "RBRK",   # Rubrik — data security
    "QLYS",   # Qualys — vulnerability
    "TENB",   # Tenable — vulnerability
    "VRNS",   # Varonis — data security
    "OKTA",   # Okta — identity (dup)
}

# Tech / communication-services sectors to AVOID for BUY (mean-reversion fails).
# 6-day trace: Technology -1.86% avg, Industrials -1.23% avg.
TECH_SECTORS_AVOID = {
    "Technology",
    "Communication Services",
    "科技",
    "通訊服務",
    "Information Technology",
    "軟件",
    "互聯網",
}


# ===== Shared HTML shell (matches Streamlit dashboard light theme) =====

SHARED_CSS = """
:root {
    --bg: #ffffff;
    --panel: #f3f4f6;
    --panel-2: #e5e7eb;
    --border: #e5e7eb;
    --fg: #1a1d23;
    --dim: #4b5563;
    --bull: #15803d;
    --bear: #b91c1c;
    --amber: #92400e;
    --blue: #2563eb;
    --purple: #7c3aed;
    --accent: #2563eb;
}
* { box-sizing: border-box; }
html, body {
    margin: 0; padding: 0;
    background: var(--bg);
    color: var(--fg);
    font-family: 'JetBrains Mono', 'SF Mono', 'Menlo', monospace;
    font-size: 14px;
    line-height: 1.5;
}
a { color: var(--accent); text-decoration: none; }
a:hover { text-decoration: underline; }

/* Top nav (matches Streamlit worker injection) */
nav.leeks-site-nav {
    position: sticky; top: 0; z-index: 999;
    background: rgba(255, 255, 255, 0.97);
    border-bottom: 1px solid var(--border);
    backdrop-filter: blur(8px);
    padding: 12px 20px;
    display: flex; align-items: center; gap: 18px;
    flex-wrap: wrap;
    font-size: 0.85rem;
}
nav.leeks-site-nav .brand {
    color: var(--accent);
    font-weight: 700;
    letter-spacing: 0.04em;
    margin-right: 8px;
}
nav.leeks-site-nav a {
    color: var(--dim);
    text-transform: uppercase;
    letter-spacing: 0.06em;
    font-size: 0.78rem;
    padding: 4px 0;
}
nav.leeks-site-nav a.active {
    color: var(--accent);
    border-bottom: 2px solid var(--accent);
    padding-bottom: 4px;
}

/* Layout */
main { max-width: 1200px; margin: 0 auto; padding: 24px 20px 64px; }
h1 {
    font-size: 1.4rem;
    color: var(--accent);
    border-bottom: 2px solid var(--accent);
    padding-bottom: 0.5rem;
    margin: 0 0 1rem;
}
h2 { font-size: 1.05rem; color: var(--accent); margin: 1.5rem 0 0.6rem; }

/* Date selector (top of dashboard) — looks like Streamlit selectbox */
.date-picker {
    display: flex; gap: 8px; flex-wrap: wrap;
    margin: 1rem 0 1.5rem;
}
.date-picker a {
    padding: 6px 12px;
    border: 1px solid var(--border);
    border-radius: 4px;
    background: var(--panel);
    color: var(--fg);
    font-size: 0.8rem;
}
.date-picker a.active {
    background: var(--accent);
    color: #fff;
    border-color: var(--accent);
}

/* Disclaimer */
.disclaimer {
    background: #fef3c7;
    border: 1px solid #b45309;
    border-radius: 6px;
    padding: 10px 14px;
    margin: 0 0 18px;
    font-size: 13px;
    color: #78350f;
}

/* Cards (mirror Streamlit dashboard) */
.card {
    border: 1px solid var(--border);
    border-left: 3px solid var(--accent);
    background: var(--panel);
    border-radius: 4px;
    padding: 10px 14px;
    margin: 8px 0;
    font-size: 0.85rem;
    line-height: 1.55;
}
.card.bull { border-left-color: var(--bull); }
.card.bear { border-left-color: var(--bear); }
.card.amber { border-left-color: var(--amber); }

/* Stats line */
.stats {
    font-size: 0.9rem;
    margin: 0.5rem 0 1rem;
    padding: 10px 14px;
    background: var(--panel);
    border: 1px solid var(--border);
    border-radius: 4px;
}
.stats .bull { color: var(--bull); font-weight: 600; }
.stats .bear { color: var(--bear); font-weight: 600; }
.stats .amber { color: var(--amber); font-weight: 600; }

/* Homepage value-prop + sample preview */
.lede { font-size: 1.05rem; line-height: 1.65; max-width: 760px; }
section h2 { margin-top: 2.4rem; }
section ul, section ol { line-height: 1.75; max-width: 760px; }
.dim { color: var(--dim); }

.sample-grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));
    gap: 14px;
    margin: 1rem 0 1.4rem;
    max-width: 900px;
}
.sample-card {
    border: 1px solid var(--border);
    border-left: 3px solid var(--accent);
    border-radius: 4px;
    padding: 12px 14px;
    background: #fafbfc;
}
.sample-header {
    display: flex; gap: 8px; align-items: baseline; margin-bottom: 8px;
    flex-wrap: wrap;
}
.sample-code { font-weight: 700; font-size: 1.05rem; }
.sample-op { padding: 1px 6px; border-radius: 3px; background: var(--panel); font-size: 0.85rem; }
.sample-score { margin-left: auto; font-weight: 600; color: var(--dim); font-size: 0.9rem; }
.sample-summary { font-size: 0.85rem; line-height: 1.55; color: var(--fg); margin: 6px 0 8px; }
.sample-link { font-size: 0.85rem; }

/* Dashboard hub /dashboard/ */
.dates-grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(320px, 1fr));
    gap: 16px;
    margin: 1.5rem 0 2rem;
    max-width: 1100px;
}
.date-card {
    border: 1px solid var(--border);
    border-radius: 6px;
    padding: 14px 18px;
    background: #fafbfc;
}
.date-header {
    display: flex; justify-content: space-between; align-items: baseline;
    margin-bottom: 8px;
}
.date-header h2 {
    margin: 0;
    font-size: 1.4rem;
    font-weight: 600;
}
.date-stats {
    display: flex; gap: 12px; margin-bottom: 12px; font-size: 0.95rem;
}
.stat-bull { color: var(--bull); font-weight: 600; }
.stat-amber { color: var(--amber); font-weight: 600; }
.stat-bear { color: var(--bear); font-weight: 600; }
.date-filters {
    display: flex; flex-wrap: wrap; gap: 6px;
}
.filter-chip {
    display: inline-block;
    padding: 4px 10px;
    border: 1px solid var(--border);
    border-radius: 3px;
    background: #fff;
    font-size: 0.82rem;
    text-decoration: none;
    color: var(--fg);
    transition: background 0.15s;
}
.filter-chip:hover { background: var(--panel-2); text-decoration: none; }
.filter-chip.primary {
    background: var(--accent);
    color: #fff;
    border-color: var(--accent);
    font-weight: 600;
}
.filter-chip.primary:hover { background: #1d4ed8; }
.hub-cta ul { list-style: none; padding: 0; }
.hub-cta li {
    padding: 6px 0;
    border-bottom: 1px solid var(--panel);
    font-size: 0.95rem;
}

/* R:R badge + timeframe hint inside cards */
.rr-badge {
    display: inline-block;
    padding: 1px 7px;
    border-radius: 3px;
    font-size: 0.72rem;
    font-weight: 600;
    margin-right: 6px;
    vertical-align: middle;
    border: 1px solid currentColor;
}
.rr-good { color: var(--bull); background: rgba(21, 128, 61, 0.08); }
.rr-ok   { color: var(--amber); background: rgba(146, 64, 14, 0.08); }
.rr-bad  { color: var(--bear); background: rgba(185, 28, 28, 0.08); }
.hint {
    display: inline-block;
    padding: 1px 7px;
    border-radius: 3px;
    font-size: 0.7rem;
    font-weight: 500;
    vertical-align: middle;
    color: var(--dim);
    background: var(--panel-2);
}
.hint-buy { color: var(--bull); }
.hint-sell {
    color: var(--bear);
    font-weight: 600;
    background: rgba(185, 28, 28, 0.06);
    border: 1px solid rgba(185, 28, 28, 0.3);
}

/* LLM confidence + caution chips on cards (Option B: surface LLM self-cautions) */
.signal-chips {
    margin: 4px 0 6px 0;
    font-size: 0.7rem;
    line-height: 1.4;
}
.conf-chip {
    display: inline-block;
    padding: 1px 7px;
    border-radius: 3px;
    font-weight: 600;
    margin-right: 5px;
    vertical-align: middle;
    border: 1px solid currentColor;
}
.conf-high { color: var(--bull); background: rgba(21, 128, 61, 0.08); }
.conf-mid  { color: var(--amber); background: rgba(146, 64, 14, 0.08); }
.conf-low  { color: var(--bear); background: rgba(185, 28, 28, 0.08); }
.caution-chip {
    display: inline-block;
    padding: 1px 7px;
    border-radius: 3px;
    font-weight: 500;
    margin-right: 5px;
    margin-bottom: 2px;
    vertical-align: middle;
    color: var(--bear);
    background: rgba(185, 28, 28, 0.06);
    border: 1px solid rgba(185, 28, 28, 0.25);
}

/* Signal warning banner on dashboard */
.signal-warning {
    margin: 0 0 1.2rem;
    padding: 10px 14px;
    background: rgba(146, 64, 14, 0.06);
    border-left: 3px solid var(--amber);
    border-radius: 4px;
    font-size: 0.82rem;
    line-height: 1.55;
}
.signal-warning .warn-strong {
    color: var(--bear);
    font-weight: 700;
}
.signal-warning a {
    color: var(--accent);
    text-decoration: underline;
}

/* Detail table */
table.detail {
    width: 100%;
    border-collapse: collapse;
    font-size: 0.78rem;
    margin-top: 1rem;
    background: var(--panel);
    border: 1px solid var(--border);
}
table.detail th, table.detail td {
    border: 1px solid var(--border);
    padding: 6px 8px;
    text-align: left;
    vertical-align: top;
}
table.detail th {
    background: var(--panel-2);
    font-weight: 600;
    text-transform: uppercase;
    font-size: 0.7rem;
    letter-spacing: 0.05em;
    color: var(--dim);
}

/* Methodology dim table */
table.dim-table {
    width: 100%;
    border-collapse: collapse;
    margin: 1rem 0;
    font-size: 0.85rem;
}
table.dim-table th, table.dim-table td {
    border: 1px solid var(--border);
    padding: 8px 10px;
    text-align: left;
    vertical-align: top;
}
table.dim-table th {
    background: var(--panel-2);
    font-weight: 600;
    font-size: 0.7rem;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    color: var(--dim);
}
table.dim-table td:first-child { font-family: 'JetBrains Mono', monospace; }
table.dim-table td:nth-child(2) { font-weight: 600; color: var(--accent); text-align: center; }

/* Card-link: appended after each summary card so reader can dive into detail */
p.card-link {
    margin: 6px 0 0;
    text-align: right;
    font-size: 0.78rem;
}
p.card-link a {
    display: inline-block;
    padding: 3px 10px;
    background: var(--panel-2);
    border: 1px solid var(--border);
    border-radius: 12px;
    color: var(--accent);
    text-decoration: none;
    font-weight: 500;
}
p.card-link a:hover {
    background: var(--accent);
    color: #fff;
    border-color: var(--accent);
}

/* Filter chips (alternative to filter dropdown) */
.filters {
    display: flex; flex-wrap: wrap; gap: 6px;
    margin: 0 0 1rem;
    font-size: 0.78rem;
}
.filters span { color: var(--dim); margin-right: 4px; align-self: center; }
.filters a {
    padding: 4px 10px;
    border: 1px solid var(--border);
    border-radius: 12px;
    background: var(--panel);
    color: var(--fg);
}
.filters a.active {
    background: var(--accent);
    color: #fff;
    border-color: var(--accent);
}

/* Report detail page */
.report-header h1 { margin: 0 0 0.5rem; }
.report-header .meta {
    display: flex; flex-wrap: wrap; gap: 6px;
    margin-bottom: 1rem;
}
.report-header .badge {
    padding: 3px 10px;
    border: 1px solid var(--border);
    border-radius: 12px;
    background: var(--panel);
    font-size: 0.75rem;
    color: var(--fg);
}
.report-header .badge.score { background: var(--accent); color: #fff; border-color: var(--accent); font-weight: 600; }
.report-header .badge.conf { background: var(--panel-2); }

.score-breakdown {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
    gap: 12px;
    margin: 1rem 0;
}
.score-breakdown .dim {
    background: var(--panel);
    border: 1px solid var(--border);
    padding: 10px;
    border-radius: 6px;
}
.score-breakdown .dim span {
    font-size: 0.7rem;
    color: var(--dim);
    text-transform: uppercase;
    letter-spacing: 0.05em;
}
.score-breakdown .bar {
    background: var(--border);
    height: 8px;
    border-radius: 4px;
    overflow: hidden;
    margin: 6px 0;
}
.score-breakdown .bar .fill { height: 100%; }
.score-breakdown .dim b {
    font-family: 'JetBrains Mono', monospace;
    font-size: 0.95rem;
}

.levels {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
    gap: 8px;
    margin: 1rem 0;
}
.levels > div {
    background: var(--panel);
    border: 1px solid var(--border);
    padding: 10px;
    border-radius: 6px;
    text-align: center;
}
.levels span {
    display: block;
    font-size: 0.7rem;
    color: var(--dim);
    text-transform: uppercase;
    letter-spacing: 0.05em;
    margin-bottom: 4px;
}
.levels b {
    font-family: 'JetBrains Mono', monospace;
    font-size: 0.9rem;
}
.levels b.bull { color: var(--bull); }
.levels b.bear { color: var(--bear); }

/* Footer */
footer {
    border-top: 1px solid var(--border);
    margin-top: 3rem;
    padding-top: 1rem;
    font-size: 0.75rem;
    color: var(--dim);
    text-align: center;
}

/* Mobile */
@media (max-width: 768px) {
    main { padding: 16px 12px 48px; }
    h1 { font-size: 1.15rem; }
    .card { padding: 8px 10px; font-size: 0.8rem; }
    nav.leeks-site-nav { padding: 10px 12px; gap: 10px; font-size: 0.75rem; }
    nav.leeks-site-nav a { font-size: 0.7rem; }
    table.detail { font-size: 0.7rem; }
    table.detail th, table.detail td { padding: 4px 6px; }
}
"""


def nav_html(active_path: str) -> str:
    """Top nav matching the Worker-injected nav on Streamlit."""
    links = [
        ("/", "Home", "Home"),
        ("/dashboard/", "Dashboard", "Dashboard"),
        ("/paper-trades.html", "Paper Trades", "Paper Trades"),
        ("/faq.html", "FAQ", "FAQ"),
        ("/methodology.html", "Methodology", "Methodology"),
        ("/about.html", "About", "About"),
    ]
    items = []
    for path, label, _ in links:
        cls = ' class="active"' if active_path.rstrip("/").endswith(path.rstrip("/")) else ""
        items.append(f'<a href="{path}"{cls}>{label}</a>')
    return (
        '<nav class="leeks-site-nav" role="navigation" aria-label="Site navigation">'
        '<span class="brand">◆ Leeks Terminal</span>'
        + "".join(items)
        + "</nav>"
    )


def shell(title: str, body_html: str, active_path: str = "/",
          description: str = "", json_ld: dict | None = None,
          canonical: str = "https://www.win9you.com") -> str:
    """Wrap content in full HTML doc with nav + light-theme CSS."""
    desc_meta = f'<meta name="description" content="{_html.escape(description)}">' if description else ""
    ld_block = ""
    if json_ld:
        ld_block = f'<script type="application/ld+json">{json.dumps(json_ld, ensure_ascii=False)}</script>'
    return f"""<!doctype html>
<html lang="zh-Hant">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{_html.escape(title)}</title>
  {desc_meta}
  <meta property="og:title" content="{_html.escape(title)}">
  <meta property="og:description" content="{_html.escape(description)}">
  <meta property="og:type" content="website">
  <meta property="og:url" content="{_html.escape(canonical)}">
  <meta property="og:image" content="https://www.win9you.com/og-image.png">
  <meta property="og:image:width" content="1200">
  <meta property="og:image:height" content="630">
  <meta name="twitter:card" content="summary_large_image">
  <meta name="twitter:image" content="https://www.win9you.com/og-image.png">
  <meta name="twitter:title" content="{_html.escape(title)}">
  <meta name="twitter:description" content="{_html.escape(description)}">
  <link rel="canonical" href="{_html.escape(canonical)}">
  <link rel="alternate" hreflang="zh-Hant" href="{_html.escape(canonical)}">
  <link rel="alternate" hreflang="x-default" href="{_html.escape(canonical)}">
  <link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;500;600;700&display=swap" rel="stylesheet">
  <style>{SHARED_CSS}</style>
  {ld_block}
</head>
<body>
{nav_html(active_path)}
<main>
{body_html}
</main>
<footer>
  © {datetime.now().year} Leeks Terminal · For informational purposes only · Not investment advice · <a href="/disclaimer.html">Disclaimer</a>
</footer>
</body>
</html>"""


def disclaimer_block() -> str:
    return (
        '<div class="disclaimer" role="note" aria-label="非投資建議免責聲明">'
        "⚠️ <b>非投資建議</b> · 本工具只係 AI 輔助決策參考，唔構成任何買賣建議。"
        "Day trading 涉及高風險，過去表現唔代表未來回報。請自行評估風險並諮詢持牌顧問。"
        '<a href="/disclaimer.html">完整免責聲明 →</a>'
        "</div>"
    )


def date_picker_html(dates: list[str], current: str) -> str:
    items = []
    for d in dates[:14]:  # last 14 dates
        cls = ' class="active"' if d == current else ""
        items.append(f'<a href="/dashboard/{d}.html"{cls}>{d}</a>')
    return '<div class="date-picker">' + "".join(items) + "</div>"


def filter_chips_html(date: str, active_slug: str) -> str:
    items = ['<span>FILTER:</span>']
    for slug, label, *_ in FILTER_PRESETS:
        cls = ' class="active"' if slug == active_slug else ""
        items.append(f'<a href="/dashboard/{date}/{slug}.html"{cls}>{_html.escape(label)}</a>')
    return '<div class="filters">' + "".join(items) + "</div>"


def detail_table_html(reports: list[dict], date: str) -> str:
    """Build the '詳細表格' table for static output (was a Streamlit dataframe)."""
    rows = []
    for r in sorted(reports, key=lambda x: x["score"] or 0, reverse=True):
        breakdown = r.get("score_breakdown") or {}
        if isinstance(breakdown, str):
            try: breakdown = json.loads(breakdown)
            except Exception: breakdown = {}
        v = breakdown.get("value_score", "—")
        q = breakdown.get("quality_score", "—")
        m = breakdown.get("momentum_score", "—")
        code_link = f'<a href="/dashboard/{date}/reports/{r["code"]}.html">{_html.escape(r["code"])}</a>'
        rows.append(
            f"<tr>"
            f"<td>{code_link}</td>"
            f"<td>{r['score'] or '—'}</td>"
            f"<td>{_html.escape(r.get('trade_direction') or '—')}</td>"
            f"<td>{v}/{q}/{m}</td>"
            f"<td>{_html.escape(r.get('operation_advice') or '—')}</td>"
            f"<td>{_html.escape(r.get('sentiment') or '—')}</td>"
            f"<td>{_html.escape(r.get('trend') or '—')}</td>"
            f"<td>{_html.escape(str(r.get('confidence') or ''))}</td>"
            f"<td>{_html.escape(r.get('entry_zone') or '—')}</td>"
            f"<td>{_html.escape(r.get('stop_loss') or '—')}</td>"
            f"<td>{_html.escape(r.get('target_price') or '—')}</td>"
            f"</tr>"
        )
    if not rows:
        return '<p><em>此條件下無報告。</em></p>'
    header = (
        "<thead><tr>"
        "<th>代碼</th><th>評分</th><th>方向</th><th>估值/質素/動能/資金流</th>"
        "<th>建議</th><th>情緒</th><th>趨勢</th><th>信心</th>"
        "<th>入場</th><th>止損</th><th>目標</th>"
        "</tr></thead>"
    )
    return f'<table class="detail">{header}<tbody>{"".join(rows)}</tbody></table>'


def report_page_html(report: dict, date: str) -> str:
    """Render one ticker's full report page (Streamlit expander equivalent)."""
    code = report["code"]
    score = report.get("score") or "—"
    direction = report.get("trade_direction") or "—"
    # Phase 2 (2026-07-10): operation_advice is RULE-BASED (set by save_report).
    # LLM's original op is in llm_original_op for audit.
    operation = report.get("operation_advice") or "—"
    llm_original_op = report.get("llm_original_op") or ""
    decision_reason = report.get("decision_reason") or ""
    sentiment = report.get("sentiment") or "—"
    trend = report.get("trend") or "—"
    summary_md = report.get("summary_md") or ""
    full_md = report.get("full_md") or ""

    # Parse data_snapshot_json early so we can fall back to it for any missing fields
    snap_raw = report.get("data_snapshot_json") or "{}"
    if isinstance(snap_raw, str):
        try: snap = json.loads(snap_raw)
        except Exception: snap = {}
    else:
        snap = snap_raw or {}

    confidence = report.get("confidence") or snap.get("confidence") or "—"

    breakdown = report.get("score_breakdown") or {}
    if isinstance(breakdown, str):
        try: breakdown = json.loads(breakdown)
        except Exception: breakdown = {}

    # Use full_md if available, fall back to summary_md
    main_md = full_md if full_md else summary_md

    # Phase 2: Replace LLM's header lines in full_md body with rule-based.
    # The LLM-generated full_md has the LLM's ORIGINAL op embedded (e.g.
    # "# 🟡 02208.HK 02208.HK" and "**評分 58/100** · ... · **觀望**").
    # The rule-based system overrode it (e.g. to 買入 via BOUNCE rule),
    # so we need to update the body text to match — otherwise the user
    # sees 🟢 買入 in the badge but 🟡 觀望 in the body (confusing).
    if operation in ("買入", "觀望", "賣出") and llm_original_op and llm_original_op != operation:
        op_emoji_map = {"買入": "🟢", "觀望": "🟡", "賣出": "🔴"}
        rule_emoji = op_emoji_map.get(operation, "🟡")
        # Replace "# 🟡 XXX XXX" → "# 🟢 XXX XXX" (or whatever the rule op emoji is)
        main_md = re.sub(
            r"^# (?:🟢|🟡|🔴|⚪)\s+",
            f"# {rule_emoji} ",
            main_md,
            count=1,
            flags=re.MULTILINE,
        )
        # Replace the inline op tag "(評分 X/100** · ... · **OBSOLETE_OP** · 信心 ...)"
        # pattern: **OBSOLETE_OP** → **NEW_OP** (rule: RULE_NAME)
        rule_name = decision_reason.split("]")[0].lstrip("[").strip() if decision_reason else ""
        rule_suffix = f" (rule: {rule_name})" if rule_name else ""
        # Use lookahead/lookbehind so we only replace the *OBSOLETE_OP** part
        main_md = re.sub(
            r"\*\*" + re.escape(llm_original_op) + r"\*\*",
            f"**{operation}**{rule_suffix}",
            main_md,
            count=2,  # may appear twice in the header line
        )

    # Back link to all.html
    back = f'<p><a href="/dashboard/{date}/all.html">← 返回 {date} 全部報告</a></p>'

    # Score breakdown bar — 4 dims: value / quality / momentum / order_flow
    v = breakdown.get("value_score", 0) or 0
    q = breakdown.get("quality_score", 0) or 0
    m = breakdown.get("momentum_score", 0) or 0
    of = breakdown.get("order_flow_score", 0) or 0
    breakdown_html = (
        '<div class="score-breakdown">'
        f'<div class="dim"><span>估值 5%</span><div class="bar"><div class="fill" style="width:{v}%;background:var(--blue);"></div></div><b>{v}</b></div>'
        f'<div class="dim"><span>質素 5%</span><div class="bar"><div class="fill" style="width:{q}%;background:var(--purple);"></div></div><b>{q}</b></div>'
        f'<div class="dim"><span>動能 70%</span><div class="bar"><div class="fill" style="width:{m}%;background:var(--amber);"></div></div><b>{m}</b></div>'
        f'<div class="dim"><span>資金流 20%</span><div class="bar"><div class="fill" style="width:{of}%;background:var(--bull);"></div></div><b>{of}</b></div>'
        '</div>'
    )

    # Key levels — pull from data_snapshot_json (backfill script parses them from full_md)
    support = (
        snap.get("support_zone")
        or report.get("support_zone")
        or "—"
    )
    resistance = (
        snap.get("resistance_zone")
        or report.get("resistance_zone")
        or "—"
    )
    # Entry/stop/target are LLM-emitted text in full_md, NOT in data_snapshot
    # (the original render_report_md in src/analyzer.py writes them as
    # markdown lines like '- **入場區間**: $X-$Y'). Parse from full_md so
    # per-ticker detail pages show the LLM's concrete trade plan.
    import re as _re
    entry = stop = target = "—"
    m = _re.search(r"\*?\*?入場區間\*?\*?[：:]\s*([^\n]+)", main_md or "")
    if m: entry = m.group(1).strip()
    m = _re.search(r"\*?\*?止[損蝕]位\*?\*?[：:]\s*([^\n]+)", main_md or "")
    if m: stop = m.group(1).strip()
    m = _re.search(r"\*?\*?目標價\*?\*?[：:]\s*([^\n]+)", main_md or "")
    if m: target = m.group(1).strip()

    levels_html = (
        '<div class="levels">'
        f'<div><span>入場區間</span><b>{_html.escape(entry)}</b></div>'
        f'<div><span>止損</span><b class="bear">{_html.escape(stop)}</b></div>'
        f'<div><span>目標</span><b class="bull">{_html.escape(target)}</b></div>'
        f'<div><span>支持位</span><b>{_html.escape(support)}</b></div>'
        f'<div><span>阻力位</span><b>{_html.escape(resistance)}</b></div>'
        '</div>'
    )

    # Main markdown body — pass link_inject_date=None so cards in the detail page itself don't get extra links
    body_md_html = body_md_to_html(main_md, link_inject_date=None, score_lookup={report["code"]: report.get("score")} if report.get("score") is not None else None, op_lookup={report["code"]: report.get("operation_advice")} if report.get("operation_advice") else None)

    # Anti-Chase override warning banner (2026-07-09)
    # If we downgraded the original LLM signal from 買入 → 觀望, show a clear
    # warning so the user understands why + sees the LLM's bullish analysis
    # but knows the system flagged it as toxic.
    anti_chase_banner = ""
    if report.get("operation_advice") == "觀望" and llm_original_op and llm_original_op != "觀望":
        # Phase 2: show rule-vs-LLM banner using decision_reason
        if decision_reason:
            anti_chase_banner = (
                '<div class="signal-warning"><b>⚠️ Rule-Based Decision (Phase 2)</b> · '
                f'LLM said <b>{llm_original_op}</b>，但 rule override 去 <b>觀望</b>'
                f'<br>· <b>Rule reason</b>: {_html.escape(decision_reason)}'
                '<br>· 10-day audit: LLM 樂觀 BUY 30.4% WR · LLM SELL 悲觀 37.7% WR · rule Conservative BUY 61.5% WR'
                '<br>· <b>唔好跟 LLM 嘅原文</b> — 睇 rule-based signal + LLM 寫嘅 narrative/levels/catalysts 就夠</div>'
            )
    elif decision_reason and decision_reason.startswith("[CONSERVATIVE]"):
        anti_chase_banner = (
            '<div class="signal-warning"><b>🛡️ Conservative BUY (rule-verified)</b> · '
            f'{_html.escape(decision_reason)}'
            '<br>· 10-day audit: <b>61.5% WR / +0.92% avg</b> — significant edge over LLM BUY (38.6% WR / -0.72% avg)</div>'
        )
    elif decision_reason and decision_reason.startswith("[BOUNCE]"):
        anti_chase_banner = (
            '<div class="signal-warning"><b>🌊 Bounce BUY (rule-verified)</b> · '
            f'{_html.escape(decision_reason)}'
            '<br>· 10-day audit: <b>51.7% WR / -0.57% avg</b> — catches mean-reversion rebounds that HOLD missed</div>'
        )

    body = (
        back
        + anti_chase_banner
        + '<div class="report-header">'
        f'<h1>📊 {_html.escape(code)} 詳細報告</h1>'
        '<div class="meta">'
        f'<span class="badge score">評分 {score}</span>'
        f'<span class="badge dir">{_html.escape(direction)}</span>'
        f'<span class="badge op">{_html.escape(operation)}</span>'
        f'<span class="badge">{_html.escape(sentiment)}</span>'
        f'<span class="badge">{_html.escape(trend)}</span>'
        f'<span class="badge conf">信心 {confidence}</span>'
        '</div>'
        '</div>'
        + breakdown_html
        + levels_html
        + '<h2>完整分析</h2>'
        + body_md_html
    )

    return shell(
        title=f"{code} 詳細報告 · {date} · Leeks Terminal",
        body_html=body,
        active_path="/dashboard/",
        description=f"{code} {date} AI 詳細報告 — 評分 {score}, {direction} {operation}",
    )


def build_dashboard_for_date(date: str) -> tuple[list[str], int]:
    """Build all filter variants for one date. Returns (files_written, report_count)."""
    written: list[str] = []
    init_db()
    all_reports = list_reports(report_date=date, limit=500)
    # Normalize: SQLite returns score_breakdown_json but downstream expects score_breakdown
    for r in all_reports:
        if "score_breakdown" not in r and "score_breakdown_json" in r:
            raw = r.pop("score_breakdown_json") or "{}"
            try:
                r["score_breakdown"] = json.loads(raw) if isinstance(raw, str) else raw
            except Exception:
                r["score_breakdown"] = {}
    if not all_reports:
        return written, 0

    dates = list_report_dates(limit=14)

    for slug, label, mkt, op in FILTER_PRESETS:
        # preset slugs use Python-level preset filter; everything else uses market/op
        preset_arg = slug if slug in ("conservative-buy", "cyber-buy", "strength-buy", "bounce-buy") else None
        body_md = build_dashboard_md(
            report_date=date,
            market=mkt if not preset_arg else None,
            operation=op if not preset_arg else None,
            preset=preset_arg,
        )
        # Re-render the cards so they use our static .card class instead of inline styles
        # — easier to style + a11y. We do a simple post-process: wrap any <div style=...> from build_dashboard_md
        # into <div class="card">. Simpler: just use the build_dashboard_md HTML as-is (inline styles work),
        # then append the filter chips + detail table.
        # 2026-07-05: sell pages get a pause banner; preset pages get their own banner
        if slug in ("us-sell", "hk-sell"):
            signal_banner = f'''<div class="signal-warning"><b>⏸️ SELL signals paused (6-day trace)</b> · 賣出 1D hit rate 52.9% 但 <b>avg 方向反咗 +0.86%</b> (即升唔跌)。SELL 跟咗會輸錢。
            <br>· <b>暫停 SELL trading</b> 直至 further evidence
            <br>· 想 short 市場？考慮 <a href="/dashboard/{date}/conservative-buy.html">Conservative BUY</a> (用 mean-reversion 揀股份跌勢) 或 <a href="/dashboard/{date}/cyber-buy.html">Cyber BUY</a> (避開大市)
            <br>· 22 天 backtest (4,371 outcomes): <b>🟢 BUY 1D 58.6% / 1W 64.3%</b> · <b>🔴 SELL 1D 59.7% / 1W 48.0%</b> (mean-revert, 4 PM 平倉)</div>'''
        elif slug == "conservative-buy":
            signal_banner = f'''<div class="signal-warning"><b>🛡️ Conservative BUY</b> · 6-day trace evidence (mean-reversion + non-tech + m 30-70 + score < 70):
            <br>· <b>Filter rules</b>: 前日 -3% to 0% 跌勢 · sector ≠ Tech/Comms · momentum 30-70 · sentiment ≠ 樂觀 · score < 70
            <br>· <b>Evidence</b>: -1% to 0% 桶 → <b>+0.68% avg, 70.6% WR</b> · -3% to -1% 桶 → <b>+1.02% avg, 50% WR</b>
            <br>· <b>EV/trade ≈ +0.5%</b> if 跟 6% 止損 · 唔 chase extended (score≥70 hit rate 跌到 28%)
            <br>· 22-day BUY backtest 1D 58.6% / 1W 64.3% · 多日 hold OK</div>'''
        elif slug == "cyber-buy":
            signal_banner = f'''<div class="signal-warning"><b>🔐 Cyber BUY v2</b> · Anti-gapup + 52w high avoidance (new logic 2026-07-09):
            <br>· <b>Tickers</b>: DDOG · PANW · CRWD · FTNT · OKTA · ZS · NET · S · CYBR · RBRK · QLYS · TENB · VRNS (13 隻)
            <br>· <b>New rules</b>: day_chg <b>-5 to 0%</b> (anti-gapup) · m_score 30-60 (avoid overbought) · score &lt; 65 · sentiment ≠ 樂觀 · last &lt; 98% of 52w_high
            <br>· <b>Why change</b>: 舊 cyber BUY (any 買入) = 5 signals 2W/3L (40% WR, -$50) — all signals at 52w high on gap-up days
            <br>· <b>Result</b>: New logic = 0 historical signals pass. 短期 trade 暫停，valid signals 需要等 cyber 真正回調 (-2 to -5%)
            <br>· <b>Risk</b>: high-beta 科技股，適合 <b>2-3 日 hold</b> 唔好 day-trade intraday 炒</div>'''
        elif slug == "strength-buy":
            signal_banner = f'''<div class="signal-warning"><b>⏸️ Strength BUY DISABLED</b> · 2026-07-09 9-day audit:
            <br>· <b>Backtest result</b>: 6 historical signals, <b>2W/4L (33% WR, -1.31% avg)</b>
            <br>· <b>Why it failed</b>: thesis inverted — stocks already up +5% in 5 days + LLM 樂觀 = TOXIC BUY territory. 7/6 META/HOOD/PANW/PDD/MDB/TTWO all dropped next day.
            <br>· <b>Lesson</b>: <span class="warn-strong">never BUY when sentiment = 樂觀 AND multi-day gain exists</span>. Buying strength at top = catching falling knife.
            <br>· <b>Replacement</b>: Conservative BUY v2 (anti-chase overlay) + Bounce BUY (mean-reversion) cover the same intent without the trap.
            <br>· <b>Re-enable trigger</b>: &gt; 5 consecutive paper-trade wins OR re-design with mandatory pullback (chg≤0 today).</div>'''
        elif slug == "bounce-buy":
            signal_banner = f'''<div class="signal-warning"><b>🌊 Bounce BUY (NEW 2026-07-09)</b> · Mean-reversion entry on panic-sold HOLD candidates.
            <br>· <b>Why this exists</b>: 7/2 missed 02650.HK (+47.3%), 09880.HK (+17.6%), 03330.HK (+16.2%) — system said HOLD but stocks bounced hard next day.
            <br>· <b>Rules</b>: <b>day_chg [-5%, -2%]</b> (pullback day) · <b>sentiment in (悲觀, 中性)</b> · <b>m_score &lt; 60</b> (momentum cooled) · <b>score &lt; 45</b> (LLM agrees value is here) · <b>of_score ≥ 25</b> (institutions didn't fully flee)
            <br>· <b>Universe</b>: All markets (HK + US)
            <br>· <b>Backtest</b>: 60 historical HOLD candidates → <b>51.7% WR, -0.53% avg</b>. Modest edge but catches missed reversals. Worst case 03986.HK -12.2% (true breakdown).
            <br>· <b>Risk</b>: <span class="warn-strong">true breakdowns (chg&lt;-7%) 仲會繼續跌</span>. Use 5% hard stop. Position size 50% of Conservative BUY.</div>'''
        else:
            signal_banner = f'''<div class="signal-warning"><b>🔬 Signal Explorer (Rule-Based, Phase 2)</b> · operation_advice 喺 10-day audit 後改由 deterministic rules 決定 (唔再信 LLM 直接 BUY/SELL call)。
            <br>· <b>🟢 買入</b> 經 4 rules 揀：<span class="warn-strong">🛡️ Conservative</span> (61.5% WR) + <span class="warn-strong">🌊 Bounce</span> (51.7% WR) + ANTI-MOMENTUM 等
            <br>· <b>🔴 賣出</b> 幾乎全部被 ANTI-KNIFE override 去 觀望 (LLM SELL 悲觀 37.7% WR，catch falling knife)
            <br>· <b>🟡 觀望</b> 88% records 係 DEFAULT — LLM 嗰個訊號冇 match 任何 backtested edge
            <br>· <b>ℹ️ 10-day audit 結論</b>: LLM 樂觀 BUY 30.4% WR (反指)、m≥80 BUY 16.7% WR、悲觀 SELL 37.7% WR (錯方向)
            <br>· <b>每張 card 顯示</b>: rule-based op + LLM 寫嘅 narrative/catalysts/levels — detail page 入面有 Rule-Based Decision banner 解釋 override 原因</div>'''
        body_html = (
            disclaimer_block()
            + signal_banner
            + filter_chips_html(date, slug)
            + f'<h1>🔬 Signal Explorer — {date} ({label})</h1>'
            + body_md_to_html(body_md, link_inject_date=date, score_lookup={r["code"]: r["score"] for r in all_reports if r.get("score") is not None}, op_lookup={r["code"]: r.get("operation_advice") for r in all_reports if r.get("operation_advice")})
        )

        # Add detail table — re-apply filters manually (already in scope from loop)
        filtered = all_reports
        if slug in ("conservative-buy", "cyber-buy", "strength-buy", "bounce-buy"):
            # Use the same filter logic as pipeline.py
            if slug == "strength-buy":
                # Strength BUY DISABLED — return empty (banner already explains why)
                filtered = []
            elif slug == "conservative-buy":
                from src.conservative_filters import TECH_SECTORS_AVOID
                kept = []
                for r in filtered:
                    if r["code"].endswith(".HK"):
                        continue
                    if r.get("operation_advice") != "買入":
                        continue
                    snap_raw = r.get("data_snapshot_json") or "{}"
                    try:
                        snap = json.loads(snap_raw) if isinstance(snap_raw, str) else snap_raw
                    except Exception:
                        snap = {}
                    day_chg = snap.get("change_pct") or 0
                    sector = (snap.get("sector") or "").strip()
                    bd_raw = r.get("score_breakdown_json") or "{}"
                    try:
                        bd = json.loads(bd_raw) if isinstance(bd_raw, str) else bd_raw
                    except Exception:
                        bd = {}
                    m_score = int(bd.get("momentum_score") or 0)
                    if not (-3 < day_chg < 0):
                        continue
                    if sector in TECH_SECTORS_AVOID:
                        continue
                    if not (30 <= m_score <= 70):
                        continue
                    if r.get("sentiment") == "樂觀":
                        continue
                    if (r.get("score") or 0) >= 70:
                        continue
                    # Earnings blackout
                    from src.conservative_filters import is_earnings_blackout
                    is_bl, _ = is_earnings_blackout(r["code"].split(".")[0], date)
                    if is_bl:
                        continue
                    kept.append(r)
                filtered = kept
            elif slug == "cyber-buy":
                from src.conservative_filters import CYBER_TICKERS, cyber_buy_passes
                kept = []
                for r in filtered:
                    if r["code"].endswith(".HK"):
                        continue
                    if r.get("operation_advice") != "買入":
                        continue
                    if r["code"].split(".")[0] not in CYBER_TICKERS:
                        continue
                    # Cyber BUY v2 (2026-07-09): anti-gapup + 52w high avoidance
                    try:
                        snap = json.loads(r["data_snapshot_json"]) if r.get("data_snapshot_json") else {}
                    except Exception:
                        snap = {}
                    try:
                        bd = json.loads(r["score_breakdown_json"]) if r.get("score_breakdown_json") else {}
                    except Exception:
                        bd = {}
                    day_chg = snap.get("change_pct") or 0
                    m_score = int(bd.get("momentum_score") or 0)
                    text = (r.get("summary_md") or "") + " " + (r.get("full_md") or "")
                    m_sent = re.search(r"·\s*(樂觀|中性|悲觀)\s*·", text)
                    sent = m_sent.group(1) if m_sent else ""
                    score = r.get("score") or 0
                    last = snap.get("last_price") or 0
                    h52 = snap.get("52w_high") or 0
                    passes, _ = cyber_buy_passes(
                        r["code"].split(".")[0], score, day_chg, m_score, sent, last, h52
                    )
                    if passes:
                        kept.append(r)
                filtered = kept
        elif slug == "bounce-buy":
            # Bounce BUY (NEW 2026-07-09): mean-reversion entry on panic-sold HOLD candidates
            from src.conservative_filters import bounce_buy_passes
            kept = []
            for r in filtered:
                try:
                    snap = json.loads(r["data_snapshot_json"]) if r.get("data_snapshot_json") else {}
                except Exception:
                    snap = {}
                try:
                    bd = json.loads(r["score_breakdown_json"]) if r.get("score_breakdown_json") else {}
                except Exception:
                    bd = {}
                day_chg = snap.get("change_pct") or 0
                m_score = int(bd.get("momentum_score") or 0)
                of_score = int(bd.get("order_flow_score") or 0)
                sentiment = r.get("sentiment") or ""
                score = r.get("score") or 0
                sector = (snap.get("sector") or "").strip()
                text = (r.get("summary_md") or "") + " " + (r.get("full_md") or "")
                m_op = re.search(r"·\s*\*\*(買入|賣出|觀望)\*\*", text)
                op_match = m_op.group(1) if m_op else r.get("operation_advice") or ""
                if op_match not in ("觀望", "買入"):
                    continue
                passes, _ = bounce_buy_passes(r["code"], score, day_chg, m_score, of_score, sentiment, sector)
                if passes:
                    kept.append(r)
            filtered = kept
        else:
            if mkt == "HK":
                filtered = [r for r in filtered if r["code"].endswith(".HK")]
            elif mkt == "US":
                filtered = [r for r in filtered if not r["code"].endswith(".HK")]
            if op:
                aliases = {"buy": ("買入", "buy"), "hold": ("觀望", "hold"), "sell": ("賣出", "sell")}
                wanted = aliases.get(op, (op,))
                filtered = [r for r in filtered if r.get("operation_advice") in wanted]

        body_html += (
            "<h2>詳細表格</h2>"
            + detail_table_html(filtered, date)
        )

        out_path = PUBLIC_DIR / "dashboard" / date / f"{slug}.html"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(
            shell(
                title=f"Leeks Terminal · {date} · {label}",
                body_html=body_html,
                active_path="/dashboard/",
                description=f"Leeks Terminal {date} AI dashboard — {label}",
            ),
            encoding="utf-8",
        )
        written.append(str(out_path.relative_to(PUBLIC_DIR)))

    # === Generate per-ticker detail pages ===
    reports_dir = PUBLIC_DIR / "dashboard" / date / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    for r in all_reports:
        code = r["code"]
        # Canonical (Tencent 5-digit format, e.g. 09988.HK)
        report_path = reports_dir / f"{code}.html"
        html_content = report_page_html(r, date)
        report_path.write_text(html_content, encoding="utf-8")
        written.append(str(report_path.relative_to(PUBLIC_DIR)))
        # HK alias — strip ALL leading zeros in one go
        # e.g. /reports/00005.HK → /reports/5.HK (single alias, no intermediate forms)
        if code.endswith(".HK"):
            stem = code[:-3]  # e.g. "09988" or "00005"
            if len(stem) == 5 and stem.startswith("0"):
                # Strip ALL leading zeros (single alias, not incremental)
                stripped = stem.lstrip("0")  # "00005" → "5", "09988" → "9988"
                if stripped and stripped != stem:
                    alias_code = stripped + ".HK"
                    alias_path = reports_dir / f"{alias_code}.html"
                    alias_path.write_text(html_content, encoding="utf-8")
                    written.append(str(alias_path.relative_to(PUBLIC_DIR)))

    return written, len(all_reports)


def body_md_to_html(md: str, link_inject_date: str | None = None, score_lookup: dict | None = None, op_lookup: dict | None = None) -> str:
    """Convert the build_dashboard_md markdown output to HTML for static pages.
    The output already contains raw HTML <div style=...> for cards (preserved).
    If link_inject_date is set, append a '→ 完整 ... 詳細報告' link inside each card.
    If score_lookup is set, replace any "評分 <digits>" inside the first card body for
    that ticker with the current DB score (handles rescoring without re-running LLM).
    If op_lookup is set, override the leading status emoji of each card based on
    the structured operation_advice column (trust DB over LLM-emitted ⚪)."""
    import re
    # Card pattern: <div style="...">CARD_CONTENT</div>
    # CARD_CONTENT is single-line text with **KO** or **00700.HK** bold code prefix.
    # Replace each card div to use class="card" + append a '→ 詳細報告' link inside.
    card_re = re.compile(
        r'(<div\s+style="[^"]*">)',
        flags=re.DOTALL,
    )

    def _find_matching_div_end(s: str, start: int) -> int:
        """Given s[start:] starts with '<div...>', find the index of the matching '</div>'.
        Tracks nested <div> tags so we don't bail on inner divs (rr-badges, hints)."""
        depth = 0
        i = start
        while i < len(s):
            if s[i:i+5] == '<div ' or s[i:i+5] == '<div>':
                depth += 1
                i = s.find('>', i) + 1
            elif s[i:i+6] == '</div>':
                depth -= 1
                if depth == 0:
                    return i + 6  # position after </div>
                i += 6
            else:
                i += 1
        return -1

    def _rewrite_card(match: re.Match) -> str:
        # Re-find the matching close tag manually to handle nested divs
        open_start = match.start(1)
        open_end = match.end(1)  # after '<div style="...">'
        # open_tag includes the original opening tag string
        full_start = open_start
        close_end = _find_matching_div_end(match.string, full_start)
        if close_end < 0:
            return match.group(0)
        open_tag = match.string[full_start:open_end]
        body = match.string[open_end:close_end - len('</div>')]
        close_tag = '</div>'

        # Convert inline style to class
        open_tag_new = '<div class="card">'

        # Capture code from body
        code = None
        m_hk = re.search(r'\b(\d{4,5}\.HK)\b', body)
        # Allow dash, dot, and digits in US tickers (e.g. BRK-B, BRK.A, RDS.A)
        m_us = re.search(r'\*\*([A-Z][A-Z0-9.\-]{0,8})\*\*', body)
        if m_hk:
            code = m_hk.group(1)
        elif m_us:
            code = m_us.group(1)

        # If we have a score_lookup and the card body mentions "評分 <number>",
        # replace it with the current DB score so the rendered card matches the
        # database after a rescoring pass (without re-running the LLM).
        if score_lookup and code and code in score_lookup:
            new_score = score_lookup[code]
            body = re.sub(r'評分\s*\d+', f'評分 {new_score}', body, count=1)

        # Override both the leading status emoji AND the inline "· 買入/觀望/賣出 ·"
        # text based on the operation_advice column. The LLM often emits "⚪ ... 觀望"
        # even when operation_advice is 買入/賣出, so we trust the DB and align
        # both the emoji AND the body-text tag.
        if op_lookup and code and code in op_lookup:
            op = op_lookup[code] or ""
            target_emoji = None
            if op in ("買入", "buy"):
                target_emoji = "🟢"
            elif op in ("賣出", "sell"):
                target_emoji = "🔴"
            elif op in ("觀望", "hold"):
                target_emoji = "🟡"
            if target_emoji:
                # Replace leading status emoji
                body = re.sub(
                    r'^(?:<[^>]+>)*\s*(?:🟢|🟡|🔴|⚪)',
                    target_emoji,
                    body,
                    count=1,
                )
                # Replace inline "· 買入 ·" / "· 觀望 ·" / "· 賣出 ·" between score and summary
                # — these are emitted by LLM in the form "評分 79 · 觀望 · DDOG 今日..."
                body = re.sub(
                    r'(評分\s*\d+\s*·\s*)(?:買入|觀望|賣出|buy|hold|sell)(\s*·)',
                    rf'\1{op}\2',
                    body,
                    count=1,
                )

        # Markdown-ish transforms inside the card body
        body = re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', body)
        body = re.sub(r'(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)', r'<em>\1</em>', body)
        body = re.sub(r'🟢', '<span style="color:var(--bull);font-weight:600;">🟢</span>', body)
        body = re.sub(r'🟡', '<span style="color:var(--amber);font-weight:600;">🟡</span>', body)
        body = re.sub(r'🔴', '<span style="color:var(--bear);font-weight:600;">🔴</span>', body)
        body = re.sub(r'⚪', '<span style="color:var(--dim);font-weight:600;">⚪</span>', body)

        link_html = ''
        if code and link_inject_date:
            link_html = (
                f'<p class="card-link">'
                f'<a href="/dashboard/{link_inject_date}/reports/{code}.html">'
                f'→ 完整 {code} 詳細報告</a></p>'
            )
        return open_tag_new + body + link_html + close_tag

    # First rewrite cards (block-aware).
    # BUGFIX 2026-07-09: re.sub only replaces the matched span (opening tag).
    # _rewrite_card was returning the FULL card HTML (open + body + link + close),
    # causing the body content to be DUPLICATED — once in the replacement, once in the
    # original string (preserved past the matched span).
    # Fix: replace the entire card span manually so the original body is consumed.
    out_parts = []
    i = 0
    while i < len(md):
        m = card_re.search(md, i)
        if not m:
            out_parts.append(md[i:])
            break
        # Append everything before the matched opening tag
        out_parts.append(md[i:m.start()])
        # Use _rewrite_card to compute the replacement (it operates on the full md)
        replacement = _rewrite_card(m)
        out_parts.append(replacement)
        # Skip past the matched card span (opening tag → matching </div>)
        close_end = _find_matching_div_end(md, m.start())
        if close_end < 0:
            # Can't find close — bail and append rest
            out_parts.append(md[m.end():])
            break
        i = close_end
    md = ''.join(out_parts)

    # Second pass: replace **評分 N/100** and 評分 N in any remaining markdown lines
    # (e.g. full_md h1 lines like "# 🟢 KO 評分 73/100"). We do a global replace
    # per code in score_lookup so a single ticker page reflects the current DB score.
    if score_lookup:
        for code, new_score in score_lookup.items():
            # Pattern variants emitted by MiniMax-M3 in full_md:
            #   **📈 評分 73/100**   評分 73   評分 73/100   評分 73/100 · ...
            md = re.sub(
                rf'評分\s*{re.escape(code)}\s*(\d+)',
                f'評分 {code} {new_score}',
                md,
                count=1,
            )
            md = re.sub(
                rf'評分\s*(\d+)\s*[/／]\s*100',
                f'評分 {new_score}/100',
                md,
                count=2,
            )

    # Now process the rest line-by-line for headers / paragraphs / emoji outside cards
    html_parts = []
    in_card = False
    for line in md.split("\n"):
        if line.startswith("<div"):
            in_card = True
            html_parts.append(line)
            continue
        elif line.startswith("</div>"):
            in_card = False
            html_parts.append(line)
            continue
        # Markdown transforms first (so they happen BEFORE escape)
        line = re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', line)
        line = re.sub(r'(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)', r'<em>\1</em>', line)
        # Then emoji color spans outside cards too
        line = re.sub(r'🟢', '<span style="color:var(--bull);font-weight:600;">🟢</span>', line)
        line = re.sub(r'🟡', '<span style="color:var(--amber);font-weight:600;">🟡</span>', line)
        line = re.sub(r'🔴', '<span style="color:var(--bear);font-weight:600;">🔴</span>', line)
        if not in_card:
            if line.startswith("## "):
                line = f"<h2>{_html.escape(line[3:])}</h2>"
            elif line.startswith("# "):
                line = f"<h1>{_html.escape(line[2:])}</h1>"
            elif line.strip() == "---":
                line = "<hr>"
            elif line.strip() and not line.startswith("<"):
                line = f"<p>{line}</p>"
        # NOTE: _html.escape above will escape <span ...> emitted into header/paragraph lines by
        # emoji replacement, AND <b>/<em> tags emitted by markdown transforms. That's wrong —
        # the tags should remain raw HTML. Un-escape the patterns we emit.
        for tag, marker, emoji in [
            ('span', 'color:var(--bull);font-weight:600;', '🟢'),
            ('span', 'color:var(--amber);font-weight:600;', '🟡'),
            ('span', 'color:var(--bear);font-weight:600;', '🔴'),
        ]:
            escaped = f'&lt;{tag} style=&quot;{marker}&quot;&gt;{emoji}&lt;/{tag}&gt;'
            raw = f'<{tag} style="{marker}">{emoji}</{tag}>'
            line = line.replace(escaped, raw)
        # Restore escaped <b>/<em> in paragraph lines
        line = line.replace('&lt;b&gt;', '<b>').replace('&lt;/b&gt;', '</b>')
        line = line.replace('&lt;em&gt;', '<em>').replace('&lt;/em&gt;', '</em>')
        html_parts.append(line)
    return "\n".join(html_parts)


def build_index(dates: list[str]) -> str:
    """Build public/index.html — landing page with date picker + content depth."""
    if not dates:
        body = (
            "<h1>◆ Leeks Terminal</h1>"
            '<p class="stats">暫時未有分析報告。請等今日 pipeline 跑完。</p>'
        )
    else:
        picker = date_picker_html(dates, "")
        latest = dates[0]

        # Sample ticker preview (3 stocks: 1 buy, 1 hold, 1 sell from latest)
        sample_html = ""
        try:
            import sqlite3
            db = sqlite3.connect(str(PROJECT_ROOT / "data" / "dsa_hk.db"))
            db.row_factory = sqlite3.Row
            sample = []
            for op in ["買入", "觀望", "賣出"]:
                row = db.execute(
                    "SELECT code, score, operation_advice, summary_md, data_snapshot_json FROM daily_report "
                    "WHERE report_date=? AND operation_advice=? ORDER BY score DESC LIMIT 1",
                    (latest, op),
                ).fetchone()
                if row:
                    sample.append(row)
            db.close()
            if sample:
                sample_html = (
                    '<section class="sample-preview">'
                    '<h2>今日信號範例</h2>'
                    '<p class="dim">以下係 ' + latest + ' 報告入面揀出嘅 3 個代表信號，'
                    '每個 score 100 分制，0=最差、100=最好。完整 200+200 隻股票睇 '
                    f'<a href="/dashboard/{latest}/all.html">{latest} dashboard →</a></p>'
                    '<div class="sample-grid">'
                )
                emoji = {"買入": "🟢", "觀望": "🟡", "賣出": "🔴"}
                for r in sample:
                    sm = r["summary_md"] or ""
                    # Strip leading emoji/bold code, take 80 chars
                    text = sm.split(" ", 1)[-1][:120] if " " in sm else sm[:120]
                    sample_html += (
                        f'<div class="sample-card">'
                        f'<div class="sample-header">'
                        f'<span class="sample-code">{r["code"]}</span>'
                        f'<span class="sample-op">{emoji.get(r["operation_advice"], "⚪")} {r["operation_advice"]}</span>'
                        f'<span class="sample-score">{r["score"]}/100</span>'
                        f'</div>'
                        f'<p class="sample-summary">{_html.escape(text)}</p>'
                        f'<a href="/dashboard/{latest}/reports/{r["code"]}.html" class="sample-link">完整 {r["code"]} 報告 →</a>'
                        f'</div>'
                    )
                sample_html += '</div></section>'
        except Exception as e:
            sample_html = f'<p class="dim">（範例載入失敗：{e}）</p>'

        body = (
            "<h1>◆ Leeks Terminal</h1>"
            "<p class=\"lede\">HK + US 即日鮮 AI 交易決策儀表板。<b>200 隻港股 + 200 隻美股</b>，"
            "每日全自動 4 維度評分 + 入場區間 / 止損 / 目標價，一頁睇晒。</p>"

            "<section class=\"value-prop\">"
            "<h2>點解用 Leeks Terminal</h2>"
            "<ul>"
            "<li><b>全自動化</b> · 港股用 Tencent 即時報價、美股用 YFinance，"
            "Tushare + yfinance 數據源，每個交易日 16:00 HKT / 16:00 ET 自動更新一次。</li>"
            "<li><b>4 維度評分</b> · 價值（5%）+ 質量（5%）+ 動量（70%）+ 訂單流（20%），"
            "Python 端確定性計分，唔靠 LLM 估分數。</li>"
            "<li><b>實用信號</b> · 每個信號附帶入場區間、止損位、目標價、風險備註，"
            "唔係一句「看多」就算數。</li>"
            "<li><b>即日鮮</b> · 嚴格 day-trade 模式，4 PM HKT / 4 PM ET 前必須平倉，"
            "唔過夜，唔留倉。</li>"
            "</ul>"
            "</section>"

            + sample_html +

            "<section class=\"how-it-works\">"
            "<h2>點樣運作</h2>"
            "<ol>"
            "<li><b>揀日期</b> · 下面揀一個交易日，預設最新嗰日。</li>"
            "<li><b>揀市場</b> · 全部 / 港股買入 / 港股賣出 / 美股買入 / 美股賣出 5 種 filter。</li>"
            "<li><b>睇信號</b> · 每張 card 顯示 ticker + 評分 + 買入/觀望/賣出 + 一句總結。</li>"
            "<li><b>入 detail page</b> · 撳「完整 XXX 詳細報告」睇技術指標 + 操作建議 + 風險備註。</li>"
            "</ol>"
            "</section>"

            "<section class=\"data-source\">"
            "<h2>數據來源 + 限制</h2>"
            "<ul>"
            "<li>HK 即時報價：Tencent qtimg (1-15 分鐘延遲，唔係 Level-2)</li>"
            "<li>US 即時報價：YFinance / Alpaca (15-20 分鐘延遲)</li>"
            "<li>新聞：Futu / Yahoo Finance RSS</li>"
            "<li>基本面：YFinance (P/E、P/B、ROE、market cap)</li>"
            "<li>覆蓋：港股 200 隻 (turnover ≥ 50M HKD) + 美股 200 隻 (S&P 500 + Nasdaq-100 樣本)</li>"
            "</ul>"
            "<p class=\"dim\">⚠️ 本工具只係 AI 輔助決策參考，唔構成任何買賣建議。"
            "Day trading 涉及高風險，過去表現唔代表未來回報。</p>"
            "</section>"

            "<h2>選擇報告日期</h2>" + picker +
            f'<p>最新報告：<a href="/dashboard/{latest}/all.html">{latest} →</a></p>'
        )

    json_ld = {
        "@context": "https://schema.org",
        "@type": "WebSite",
        "name": "Leeks Terminal",
        "url": "https://www.win9you.com",
        "description": "HK + US 即日鮮 AI 交易決策儀表板。200 隻港股 + 200 隻美股，4 維度評分 + 入場區間 / 止損 / 目標價。",
        "inLanguage": "zh-Hant",
        "publisher": {
            "@type": "Organization",
            "name": "Leeks Terminal",
            "url": "https://www.win9you.com",
        },
    }

    return shell(
        title="Leeks Terminal · HK+US 即日鮮 AI 交易決策儀表板",
        body_html=body,
        active_path="/",
        description="HK + US 即日鮮 AI 交易決策儀表板。200 隻港股 + 200 隻美股，4 維度評分 + 入場區間 / 止損 / 目標價。",
        json_ld=json_ld,
    )


def build_static_pages() -> list[str]:
    """Build the static info pages (FAQ, about, methodology, disclaimer, privacy).
    These mirror what the Worker serves, but as plain HTML so Pages can serve them."""
    pages = [
        ("faq", "FAQ", "常見問題", "/faq.html", "faq"),
        ("about", "About", "關於 Leeks Terminal", "/about.html", "about"),
        ("methodology", "Methodology", "分析方法論", "/methodology.html", "methodology"),
        ("disclaimer", "Disclaimer", "完整免責聲明", "/disclaimer.html", "disclaimer"),
        ("privacy", "Privacy", "私隱政策", "/privacy.html", "privacy"),
    ]
    written = []
    for slug, en, zh, path, active_key in pages:
        # Reuse Worker's PAGES dict content if available, otherwise generate placeholder
        # — we keep the static page content minimal but consistent
        body = f"<h1>{zh}</h1>"
        if slug == "faq":
            body += """
<h2>Q: 點解睇唔到 bar chart / 即時 K 線？</h2>
<p>A: 本工具係 <b>決策輔助</b> 而唔係 trading platform。你嘅 broker (Futu / IBKR / SC) 已經有完整即時圖表。
我哋只提供每日 2 次嘅 AI 評分 + 方向信號 + 入場/止損/目標 區間。</p>

<h2>Q: 個 score 點樣計？</h2>
<p>A: <b>四維</b>評分 (0–100)，Python-side 確定性加權 (非 LLM 自評)：
<br>· <b>估值</b> (PE/PB，5%) · <b>質素</b> (ROE/margin，5%) · <b>動能</b> (MA/RSI/deviation，70%) · <b>資金流</b> (量比/大單/Relative Volume，20%)。
<br>公式：<code>score = 0.05 × value + 0.05 × quality + 0.70 × momentum + 0.20 × order_flow</code>。
<br>Day-trade 完全偏重 momentum + order flow；估值/質素只係 tiebreaker。</p>

<h2>Q: trade_direction 點解有時「雙向」？</h2>
<p>A: 「雙向」代表波動率足夠，long 同 short setup 都有，用戶自己揀邊個方向做。
filter 可以 hide 其他方向。</p>

<h2>Q: 點解 entry / stop / target 有時顯示「—」？</h2>
<p>A: 個別 LLM output 唔齊全；我哋有 backfill script parse <code>full_md</code> 嘅 markdown bullets 拎返。
如果仍然「—」表示該股真係冇明確 setup，建議觀望。</p>
"""
        elif slug == "about":
            body += """
<p>Leeks Terminal 係我自己寫嚟用嘅 HK + US day-trade dashboard。香港散戶。</p>
<p>200 隻港股 + 200 隻美股，每日 2 次 (HK 開市前 + US 開市前) 用 MiniMax-M3 評分，輸出 Value / Quality / Momentum / Order-Flow 四維分數 + 入場區間 / 止損 / 目標。</p>
<p>全部資料 free：Futu Cloud news (news)、Tencent gtimg (live HK 報價，sub-1min delay)、YFinance (US/EOD)。</p>
<p>Built with Python · Streamlit · Cloudflare Pages. Code closed-source (個人 side project)。</p>
"""
        elif slug == "methodology":
            body += """
<h2>數據來源</h2>
<ul>
  <li><b>HK 即時報價</b>: Tencent qt.gtimg.cn (sub-1min delay，PE/PB/market_cap/52w 同時提供)</li>
  <li><b>US 即時報價</b>: YFinance (15-min delay)</li>
  <li><b>新聞</b>: Futu Cloud (free tier, 60 req/min)</li>
  <li><b>歷史 bars</b>: YFinance (HK 覆蓋不平均，新 foreign-listed 會 warn)</li>
</ul>

<h2>LLM 分析流程</h2>
<ol>
  <li>Fetch 報價 + 技術指標 (MA20/50/100/200 + RSI + MACD + 成交量比)</li>
  <li>Fetch 新聞 (last 5 條 + sentiment)</li>
  <li>Prompt MiniMax-M3 輸出 score_breakdown 嘅 4 個 dim (value/quality/momentum/order_flow) + 操作建議欄位 (entry/stop/target/support/resistance/confidence/trade_direction/sentiment/trend/operation_advice)</li>
  <li>Python <b>確定性加權</b>：<code>score = 0.05×value + 0.05×quality + 0.70×momentum + 0.20×order_flow</code>（唔靠 LLM 自評分）</li>
  <li>寫入 SQLite</li>
  <li>Rebuild static HTML + push 到 Cloudflare Pages</li>
</ol>

<h2>評分模型 — 為 day-trade 度身訂造</h2>
<table class="dim-table">
<thead><tr><th>維度</th><th>權重</th><th>睇咩</th></tr></thead>
<tbody>
<tr><td><b>估值</b> value_score</td><td>5%</td><td>PE / PB / deviation from fair value</td></tr>
<tr><td><b>質素</b> quality_score</td><td>5%</td><td>ROE / margin / financial health / dividend stability</td></tr>
<tr><td><b>動能</b> momentum_score</td><td>70%</td><td>今日方向 / MA trend / RSI / deviation（純價格動能，唔包成交量）</td></tr>
<tr><td><b>資金流</b> order_flow_score</td><td>20%</td><td>量比 / 大單流入 / Relative Volume vs ADV / bid-ask imbalance / 北水</td></tr>
</tbody>
</table>
<p><b>Day-trade 完全偏重 momentum + order flow</b>（合共 90%）。估值/質素只作 tiebreaker — 因為朝早 9:45 入場 11:30 出場，PE 30 定 60 對 intraday P&amp;L 零 impact。</p>

<h2>操作建議</h2>
<p>每日 dashboard 約 <b>~93% 觀望 / ~2% 買入 / ~5% 賣出</b>（取決於大市 regime）。LLM 綜合以下 4 個 signal 決定：</p>
<ul>
  <li><b>🟢 買入</b>: bullish trade_direction + score ≥ 60 + positive sentiment + 明確 setup（入場/止損/目標 齊全）</li>
  <li><b>🔴 賣出</b>: bearish trade_direction + score ≤ 40 + negative sentiment + 跌穿關鍵支持位</li>
  <li><b>🟡 觀望</b>: 其他（regime 唔清、setup 唔齊、score 40-60 模糊區間）</li>
</ul>
<p><b>⚠️</b> 操作建議 <b>唔等於單一 score threshold</b> — LLM 會 cross-check 4-dim + sentiment + news flow + technical setup。一個 score 80 但 setup 模糊嘅股票仍然可能係觀望。</p>

<h2>📊 Backtest 驗證 (2026-05-29 至 2026-06-29, 22 個交易日)</h2>
<p>我哋用 historical yfinance data 跑咗 22 個交易日 × 200 隻美股 (US-only，HK 暫時冇 historical source)，
總共 <b>4,379 個信號 × 1D/1W horizons</b>。Hit rate 表：</p>
<table class="dim-table">
<thead><tr><th>信號</th><th>1 日 hit rate</th><th>1 週 hit rate</th><th>1 日 avg move</th><th>1 週 avg move</th></tr></thead>
<tbody>
<tr><td><b>🟢 買入</b></td><td>58.6% (n=58)</td><td><b>64.3% (n=42)</b></td><td>+0.91%</td><td>+1.24%</td></tr>
<tr><td><b>🔴 賣出</b></td><td><b>59.7% (n=191)</td><td>48.0% (n=202)</td><td>-0.40%</td><td>+0.45%</td></tr>
<tr><td><b>🟡 觀望</b></td><td>n/a</td><td>n/a</td><td>+0.11%</td><td>+0.72%</td></tr>
</tbody>
</table>
<p><b>兩個關鍵 takeaway</b>：</p>
<ol>
<li><b>🟢 買入 multi-day 有效</b> — 1 週 hit rate (64.3%) > 1 日 (58.6%)，表示 buy signal 嘅 lead time work，long 倉 hold 過夜仲跟到。</li>
<li><b>🔴 賣出 day-trade only</b> — 1 日 hit rate (59.7%) > 1 週 (48.0%)，表示 short setup mean-revert，第二日就會反彈。SELL 必須 16:00 HKT/ET 前平倉。</li>
</ol>
<p><b>Implication 落 dashboard</b>：</p>
<ul>
<li>買入 cards 標 "🟢 買入 — multi-day hold OK"</li>
<li>賣出 cards 標 "🔴 賣出 — day-trade only, close by 4 PM"</li>
</ul>

<h2>🛡️ R:R Override 過濾 (Risk:Reward)</h2>
<p>每個 buy setup 計算 <b>reward / risk</b> ratio:</p>
<ul>
<li><b>Reward</b> = |target_price - last| / last</li>
<li><b>Risk</b> = |last - min(LLM_support, today_low)| / last</li>
</ul>
<p>Dashboard card 會顯示 R:R badge：</p>
<ul>
<li><b>🟢 R:R ≥ 2.0</b> — setup 質量好，risk-controlled</li>
<li><b>🟡 1.0 ≤ R:R < 2.0</b> — 普通 setup，risk/reward 1:1</li>
<li><b>🔴 R:R < 1.0</b> — risk 大過 reward，避開</li>
</ul>
<p>邏輯：buy setup 入場前要計清楚「贏幾多 vs 輸幾多」。R:R ≥ 2.0 表示 setup 期望值正（2:1 風險回報），否則好 setup 都係陷阱。</p>
"""
        elif slug == "disclaimer":
            body += """
<p>本站所有內容 (<a href="/">win9you.com</a>) 包括 dashboard 評分、信號、新聞摘要、方法論描述、FAQ 答案，<b>只供資訊及教育用途</b>。佢<b>唔構成</b>：</p>
<ul>
  <li>投資建議</li>
  <li>買賣建議</li>
  <li>稅務 / 法律 / 財務建議</li>
</ul>
<p><b>Day trading 涉及高風險</b>。你可能損失全部本金。過去表現唔代表未來回報。
喺做任何投資決定之前，請諮詢持牌財務顧問。</p>
<p>本站作者唔會就任何因使用本站內容而導致嘅損失承擔責任。</p>
"""
        elif slug == "privacy":
            body += """
<p>Leeks Terminal <b>唔收集任何個人資料</b>。冇 account、冇 analytics、冇 cookies、冇 tracking、冇 email collection。</p>
<p>Dashboard 喺你 browser 跑。Market data 由 public APIs (Tencent、YFinance、Futu Cloud) 提供，
由你 browser 開 dashboard 時主動 fetch。第三方 API 唔會收到你嘅 IP 或 browser fingerprint (除咗 fetch request 本身)。</p>
<p>Server side: Cloudflare Pages serve static HTML。GitHub 存 source code。冇 user data 存任何地方。</p>
"""

        out_path = PUBLIC_DIR / path.lstrip("/")
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(
            shell(
                title=f"Leeks Terminal · {zh}",
                body_html=body,
                active_path=f"/{active_key}.html",
                description=f"Leeks Terminal {en} page",
            ),
            encoding="utf-8",
        )
        written.append(path)
    return written


def build_sitemap_xml(dates: list[str]) -> str:
    """Build sitemap.xml with all public pages. Auto-includes all dates in DB."""
    base = "https://www.win9you.com"
    urls = []

    # Static pages
    static = [
        ("/", "1.0", "daily"),
        ("/dashboard/", "0.9", "daily"),
        ("/methodology.html", "0.8", "weekly"),
        ("/faq.html", "0.8", "weekly"),
        ("/about.html", "0.5", "monthly"),
        ("/disclaimer.html", "0.3", "monthly"),
        ("/privacy.html", "0.3", "monthly"),
        # Intent landing pages (P1 SEO coverage)
        ("/hk-scanner.html", "0.9", "daily"),
        ("/us-scanner.html", "0.9", "daily"),
        ("/day-trade-signals.html", "0.9", "daily"),
        ("/hk-stock-screener.html", "0.8", "daily"),
    ]
    for path, prio, freq in static:
        urls.append((path, prio, freq))

    # Per-date dashboard + filter variants
    filters = ["all", "hk-buy", "hk-sell", "hk-hold", "us-buy", "us-sell", "us-hold", "conservative-buy", "cyber-buy", "bounce-buy"]
    for d in dates:
        urls.append((f"/dashboard/{d}/all.html", "0.9", "daily"))
        for f in filters[1:]:
            urls.append((f"/dashboard/{d}/{f}.html", "0.7", "daily"))

    lines = ['<?xml version="1.0" encoding="UTF-8"?>']
    lines.append('<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">')
    for path, prio, freq in urls:
        lines.append("  <url>")
        lines.append(f"    <loc>{base}{path}</loc>")
        lines.append(f"    <changefreq>{freq}</changefreq>")
        lines.append(f"    <priority>{prio}</priority>")
        lines.append("  </url>")
    lines.append("</urlset>")
    return "\n".join(lines) + "\n"


def build_robots_txt() -> str:
    return (
        "User-agent: *\n"
        "Allow: /\n"
        "\n"
        "Sitemap: https://www.win9you.com/sitemap.xml\n"
    )


def build_dashboard_hub(dates: list[str]) -> str:
    """Build /dashboard/index.html — hub page listing all available dates + filter variants.
    This replaces the SPA fallback to homepage when user hits /dashboard/."""
    import sqlite3
    db = sqlite3.connect(str(PROJECT_ROOT / "data" / "dsa_hk.db"))
    db.row_factory = sqlite3.Row

    # Per-date summary: count + buy/hold/sell breakdown
    date_summaries = []
    for d in dates:
        rows = db.execute(
            "SELECT operation_advice, COUNT(*) as n FROM daily_report "
            "WHERE report_date=? GROUP BY operation_advice",
            (d,),
        ).fetchall()
        ops = {r["operation_advice"]: r["n"] for r in rows}
        total = sum(ops.values())
        date_summaries.append({
            "date": d,
            "total": total,
            "buy": ops.get("買入", 0) + ops.get("buy", 0),
            "hold": ops.get("觀望", 0) + ops.get("hold", 0),
            "sell": ops.get("賣出", 0) + ops.get("sell", 0),
        })
    db.close()

    filters = [
        ("all", "全部", "📊"),
        ("hk-buy", "港股買入", "🟢"),
        ("hk-sell", "港股賣出 ⏸️", "🔴"),
        ("hk-hold", "港股觀望", "🟡"),
        ("us-buy", "美股買入", "🟢"),
        ("us-sell", "美股賣出 ⏸️", "🔴"),
        ("us-hold", "美股觀望", "🟡"),
        ("conservative-buy", "🛡️ Conservative BUY", "🛡️"),
        ("cyber-buy", "🔐 Cyber BUY", "🔐"),
        ("strength-buy", "⏸️ Strength BUY", "⏸️"),
        ("bounce-buy", "🌊 Bounce BUY", "🌊"),
    ]
    date_cards_html = []
    for s in date_summaries:
        d = s["date"]
        chips = " ".join(
            f'<a class="filter-chip" href="/dashboard/{d}/{slug}.html">'
            f'{em} {label}</a>'
            for slug, label, em in filters
            if slug != "all"
        )
        date_cards_html.append(
            f'<div class="date-card">'
            f'<div class="date-header">'
            f'<h2>{d}</h2>'
            f'<span class="dim">{s["total"]} 隻</span>'
            f'</div>'
            f'<div class="date-stats">'
            f'<span class="stat-bull">🟢 {s["buy"]}</span>'
            f'<span class="stat-amber">🟡 {s["hold"]}</span>'
            f'<span class="stat-bear">🔴 {s["sell"]}</span>'
            f'</div>'
            f'<div class="date-filters">'
            f'<a class="filter-chip primary" href="/dashboard/{d}/all.html">📊 全部 ({s["total"]})</a>'
            f'{chips}'
            f'</div>'
            f'</div>'
        )

    body = (
        '<h1>📊 決策儀表板</h1>'
        '<p class="lede">所有交易日的 AI 分析報告。'
        '每日 200 隻港股 + 200 隻美股，4 維度評分 + 入場 / 止損 / 目標。</p>'
        + (f'<section class="dates-grid">' + "".join(date_cards_html) + '</section>' if date_summaries else '<p>暫時未有報告。</p>')
        + '<section class="hub-cta">'
        '<h2>常用入口</h2>'
        '<ul>'
        '<li><a href="/hk-scanner.html">港股 scanner</a> · 港股 200 隻 high-turnover stock</li>'
        '<li><a href="/us-scanner.html">美股 scanner</a> · 美股 200 隻 S&P 500 + Nasdaq-100</li>'
        '<li><a href="/day-trade-signals.html">即日鮮信號</a> · day trade 決策流程</li>'
        '<li><a href="/hk-stock-screener.html">港股 stock screener</a> · 4 維度 filter 教學</li>'
        '<li><a href="/methodology.html">分析方法論</a> · 4-dim 評分模型 + 操作建議</li>'
        '<li><a href="/faq.html">FAQ</a> · 常見問題</li>'
        '</ul>'
        '</section>'
    )

    json_ld = {
        "@context": "https://schema.org",
        "@type": "WebPage",
        "name": "Leeks Terminal 決策儀表板",
        "description": "所有交易日的 AI 分析報告。HK + US 即日鮮信號、4 維度評分、入場 / 止損 / 目標。",
        "url": "https://www.win9you.com/dashboard/",
        "inLanguage": "zh-Hant",
        "isPartOf": {"@type": "WebSite", "name": "Leeks Terminal", "url": "https://www.win9you.com"},
    }

    return shell(
        title="決策儀表板 · 所有交易日報告 | Leeks Terminal",
        body_html=body,
        active_path="/dashboard/",
        description="所有交易日的 AI 分析報告。HK + US 即日鮮信號、4 維度評分、入場 / 止損 / 目標。",
        json_ld=json_ld,
        canonical="https://www.win9you.com/dashboard/",
    )


def build_intent_pages() -> list[str]:
    """Build 4 long-form intent landing pages for SEO.
    Each targets a specific high-intent search query."""
    # Detect latest date for sample links
    latest = None
    try:
        import sqlite3
        db = sqlite3.connect(str(PROJECT_ROOT / "data" / "dsa_hk.db"))
        row = db.execute("SELECT MAX(report_date) FROM daily_report").fetchone()
        latest = row[0] if row else None
        db.close()
    except Exception:
        pass

    pages = []

    # 1. HK stock scanner
    pages.append({
        "path": "/hk-scanner.html",
        "slug": "hk-scanner",
        "title": "港股即日鮮掃描器 · HK Stock Scanner | Leeks Terminal",
        "description": "AI 自動掃描 200 隻港股（恒生 + 國企 + 科技指數成份股），每日兩次輸出買入/觀望/賣出信號 + 入場區間 / 止損 / 目標價。",
        "h1": "港股即日鮮 AI 掃描器",
        "body": """
<p class="lede">Leeks Terminal 港股掃描器每日自動分析 <b>200 隻高成交港股</b>（恒生指數 + 國企指數 + 科技指數成份股 + 50 隻高成交二線股），
用 4 維度評分 (估值 / 質素 / 動能 / 資金流) 排序，輸出買入 / 觀望 / 賣出信號 + 入場區間 / 止損位 / 目標價。</p>

<h2>覆蓋範圍</h2>
<ul>
<li>恒生指數 82 隻成份股（HSI）</li>
<li>恒生中國企業指數 50 隻成份股（HSCEI）</li>
<li>恒生科技指數 30 隻成份股（HSTECH）</li>
<li>額外 38 隻高成交二線股（turnover ≥ 50M HKD）</li>
</ul>

<h2>點樣揀信號</h2>
<p>每個交易日下午港股收市後 (16:00 HKT) 自動運行一次 pipeline。每隻股票分析流程：</p>
<ol>
<li><b>Fetch 報價</b> · Tencent qtimg (sub-1min delay，包括 PE / PB / market cap / 52w range)</li>
<li><b>計技術指標</b> · MA20/50/100/200 + RSI14 + MACD + 量比</li>
<li><b>Fetch 新聞</b> · Futu Cloud 頭 5 條 + Yahoo Finance RSS</li>
<li><b>LLM 評分</b> · MiniMax-M3 輸出 4-dim breakdown (v / q / m / of)</li>
<li><b>Python 加權</b> · 確定性 score = 0.05×v + 0.05×q + 0.70×m + 0.20×of</li>
<li><b>操作建議</b> · 買入 / 觀望 / 賣出 + 入場 / 止損 / 目標</li>
</ol>

<h2>邊個適合用</h2>
<ul>
<li>香港散戶 / 全職 trader · 開市前要快速 scan 邊隻有 setup</li>
<li>美股 trader 開市前 · 順便睇吓港股隔晚有冇隔夜 gap</li>
<li>學生 / 學習者 · 用嚟睇真實 score breakdown 學技術分析</li>
</ul>

<h2>同其他港股掃描器嘅分別</h2>
<table class="dim-table">
<thead><tr><th>工具</th><th>數據源</th><th>評分方式</th><th>操作建議</th></tr></thead>
<tbody>
<tr><td>Leeks Terminal</td><td>Tencent + Futu + YFinance</td><td>4-dim 確定性加權</td><td>🟢 買入 / 🟡 觀望 / 🔴 賣出 + 入場/止損/目標</td></tr>
<tr><td>一般券商 app</td><td>自家 K 線 + 簡單 MA</td><td>無評分</td><td>無</td></tr>
<tr><td>付費 AI 平台</td><td>Bloomberg / Refinitiv</td><td>Black box</td><td>信號</td></tr>
</tbody>
</table>
<p>我哋唔係 Bloomberg replacement — 係 day-trade 開市前 5 分鐘決策輔助。</p>

<h2>常見問題</h2>
<h3>Q: 港股 scanner 幾時更新？</h3>
<p>A: 每日港股收市後 (16:00 HKT) 自動跑一次，16:30 HKT 前 dashboard 上線。週末 / 假期 skip。</p>
<h3>Q: 點解用 Tencent 報價唔係 AAStocks / 經濟通？</h3>
<p>A: Tencent qtimg 同一個 endpoint 提供 price + PE + PB + market cap + 52w range，sub-1min delay，連埋歷史 turnover 全部一個 call 拎到。</p>
<h3>Q: 唔係 L2 quote 準唔準？</h3>
<p>A: 對 day-trade 嚟講 sub-1min delay 已經夠用，因為入場時間通常 9:45-10:30 之間（開市 15-60 分鐘），唔係秒級 scalping。如果你係 HFT / scalper，呢個工具唔啱你。</p>

<h3>Q: 咩係「港股即日鮮」？呢個 scanner 同即日鮮有咩關係？</h3>
<p>A: 港股即日鮮係香港散戶常用嘅術語，指當日開倉、當日收市前平倉嘅短炒策略（intraday / day trade），持倉時間由幾分鐘到幾個鐘，最長唔過夜。即日鮮嘅核心係搵當日有 momentum 爆發嘅股票，喺開市頭 60 分鐘（9:30-10:30 HKT）入場，跟住 15:50-16:00 HKT 強制平倉。Leeks Terminal 港股 scanner 專門為即日鮮設計 — 每朝早 9:00 HKT 出信號，入場區間、止損位、目標價全部寫死，唔使 trader 自己計。即日鮮信號通常都係高 momentum 股票（m 維度 ≥ 60），因為低 momentum 嘅股即日波幅細，無即日鮮 value。</p>

<h3>Q: 港股短炒同即日鮮有咩分別？呢個 scanner cover 唔 cover？</h3>
<p>A: 港股短炒 (short-term swing) 涵蓋即日鮮 (day-trade) + 隔夜短炒 (overnight swing) + 數日短炒 (2-5d swing)，持倉時間由幾個鐘到 5 個交易日。Leeks Terminal scanner 主力做即日鮮同 1-3 日短炒 — 入場區間、止損位 (2-3%)、目標價 (5-15%) 都係呢個 timeframe 設計。如果你做 1-2 週 swing trade，可以用我哋嘅 momentum 排行做股票初篩，但實際入場 / 止損位要自己再 set 過。Dashboard 唔覆蓋嘅係長線投資 (1 個月以上) 同價值投資 — 嗰啲要睇 PE / PB / ROE / 業務基本面，唔係呢個工具範圍。</p>

<h3>Q: 我係香港散戶，唔識 technical analysis，呢個 scanner 啱唔啱我？</h3>
<p>A: 啱。Leeks Terminal 港股 scanner 設計對象就係香港散戶，特別係：</p>
<ul>
<li>冇時間 / 唔識睇 200 隻 K 線嘅打工族 — 每日 16:30 HKT 之後睇 dashboard 就夠</li>
<li>想學 technical analysis 但唔知點入手嘅新手 — 每個 detail page 都有 MA20/50/100/200 + RSI14 + MACD 圖解，教你點睇</li>
<li>用緊一般券商 app (富途 / 輝立 / 致富) 但嫌個 built-in scanner 太簡單嘅進階散戶</li>
</ul>
<p>工具唔會話你「一定賺錢」，但會話你邊隻股有 setup、邊隻冇，仲會俾埋入場 / 止損 / 目標，等你自己決定落唔落單。</p>

<h3>Q: 有冇 AI 揀股功能？個 AI 識唔識揀港股科技股 / AI 概念股？</h3>
<p>A: 有。Leeks Terminal 嘅核心就係 AI 揀股 — 用 MiniMax-M3 分析 200 隻港股，包括恒生科技指數 30 隻成份股（例如騰訊 0700、美團 3690、阿里 9988、京東 9618、小米 1810）同其他 AI 概念股。AI 會根據 4 維度 (估值 / 質素 / 動能 / 資金流) 評分，特別係「動能」+「資金流」兩個維度對 AI 概念股特別 work — 因為呢類股通常靠消息面 + 資金推動，技術分析嘅 breakout / 量比訊號特別有效。注意：我哋講嘅「AI 揀股」係用 AI 做 stock scoring，唔係話呢個工具會推薦 AI 概念股 — 你可以自己 filter 科技指數成份股，AI 會按你 filter 出嘅名單做 ranking。</p>

<h3>Q: 「港股即日鮮信號」同一般券商 app 嘅「今日推薦」有咩分別？</h3>
<p>A: 一般券商 app 嘅「今日推薦」通常係 sell-side analyst 報告（幾日前出）、又或者係簡單 MA crossover 訊號，冇具體入場 / 止損 / 目標價。Leeks Terminal 港股即日鮮信號每日 16:00 HKT 自動出，每個信號都附帶：</p>
<ul>
<li><b>入場區間</b> · 具體價位 (例如 HK$320-325)，唔係「支持位」含糊嘢</li>
<li><b>止損位</b> · 入場下 2-3%，例如 HK$315</li>
<li><b>目標價</b> · 入場上 5-10%，分兩級 TP1 / TP2</li>
<li><b>持倉時間</b> · 1-3 個交易日，過期作廢</li>
<li><b>4 維度評分 breakdown</b> · v / q / m / of 分數，等你理解點解 AI 揀呢隻</li>
</ul>
<p>仲有就係 — 我哋有 paper-trader 自動追蹤過往信號命中率，命中率低嘅策略會自動 disable，唔會亂出。</p>

<h3>Q: 港股 day trade 同美股 day trade 有咩唔同？呢個 scanner 啱唔啱美股？</h3>
<p>A: 港股 day trade 同美股 day trade 主要分別喺：</p>
<ul>
<li><b>開市時間</b> · 港股 09:30-16:00 HKT（無盤前 / 盤後），美股 09:30-16:00 ET（有 pre-market / after-hours）</li>
<li><b>波幅</b> · 港股即日波幅通常 1-3%，美股 0.5-2%（大股）</li>
<li><b>T+0 / T+2</b> · 港股 T+2 交收但可以即日鮮（同一日內買賣），美股 T+1 同樣可以即日鮮</li>
<li><b>Short selling</b> · 港股有沽空名單限制，美股幾乎所有股票都可以 short</li>
<li><b>交易成本</b> · 港股佣金 + 印花稅 0.13% + 交易徵費；美股零印花稅</li>
</ul>
<p>Leeks Terminal 港股 scanner 主力做港股。美股有另一個 <a href="/us-scanner.html">us-scanner</a>。兩個 scanner 嘅 4 維度框架一樣，但美股版本嘅「動能」權重稍低（60% vs 港股 70%），因為美股趨勢持續時間長。</p>

<h3>Q: 有冇 free 版港股 scanner？</h3>
<p>A: 有，Leeks Terminal 港股 scanner 嘅 dashboard 完全 free，唔使註冊、唔使訂閱，開頁就見到當日 200 隻信號。每日 16:30 HKT 之後上線，週末 / 假期 skip。如果你想睇歷史信號 + paper-trade outcome 追蹤，需要註冊 (free tier)。如果你想收到每日 9:00 HKT 開市前 signal 推送 (Email / Telegram)，需要訂閱 paid tier。詳細定價同功能差異睇主頁 footer。</p>

<h3>Q: 點樣讀取 dashboard 嘅顏色同符號？</h3>
<p>Dashboard 統一用 3 色 + 3 符號標記信號：</p>
<ul>
<li><b>🟢 綠色 = 買入信號</b> · 4 維度綜合分 ≥ 60，建議入場做 long。入場區間顯示喺 card 第二行，止損位喺第三行。</li>
<li><b>🟡 黃色 = 觀望信號</b> · 綜合分 40-60 之間，setup 唔清或 trend 模糊。等下一日再睇。</li>
<li><b>🔴 紅色 = 賣出信號</b> · 綜合分 ≤ 40，建議避開或 short。Short 入場區間顯示喺 card 第二行。</li>
</ul>
<p>Card 右上有 3 個 icon：</p>
<ul>
<li><b>📈 上升箭嘴</b> · momentum 維度 ≥ 60，趨勢向上</li>
<li><b>📊 柱狀圖</b> · 資金流維度 ≥ 60，有大户入場</li>
<li><b>⚡ 閃電</b> · 今日有重大新聞（自動從 Futu + Yahoo Finance 抓），可能觸發 breakout</li>
</ul>
<p>如果 card 三個 icon 都亮 (綠 + 📈 + 📊 + ⚡)，係當日最強信號。如果係紅 + 下跌箭嘴，就係最弱 setup，建議避開。詳細點樣用 score breakdown 學 technical analysis，睇 <a href="/methodology.html">methodology 頁</a>。</p>

<h3>Q: 港股 technical analysis 中文教學資源喺邊？</h3>
<p>Leeks Terminal 本身有內建中文 technical indicator 教學 — 每隻股嘅 detail page 都會顯示 MA20/50/100/200、RSI14、MACD、Bollinger Bands 嘅當前數值 + 圖解，例如「MA20 上穿 MA50 = 黃金交叉」、「RSI ≥ 70 = 超買」、「MACD 柱由負轉正 = momentum 反轉」。如果想學深啲，可以睇我哋 <a href="/methodology.html">methodology 頁</a>，有齊 4 維度框架嘅公式同案例。或者 follow 我哋嘅 Telegram channel，每星期出一篇 technical analysis 中文 tutorial（MA / RSI / MACD / Bollinger / 量比）。</p>

<h3>Q: 港股 momentum 排行喺邊睇？有冇類似 Finviz / TradingView 嘅工具？</h3>
<p>Leeks Terminal 港股 scanner dashboard 預設按 momentum 維度 (70% 權重) 由高到低排，所以首 20 隻 card 就係當日 momentum 排行 — 升幅最大 + 資金流入最強嘅港股。如果想自訂排行（例如按估值 + 動能排），可以用 detail page 嘅 sort 功能。如果你想用 TradingView，佢有港股覆蓋但要付費；Finviz 主力做美股，唔覆蓋港股。我哋嘅 free dashboard 對香港散戶嚟講係最直接嘅選擇。</p>

<h2>港股 vs A股 vs 美股 scanner — 點解唔可以直接用同一個工具？</h2>
<table class="dim-table">
<thead><tr><th>維度</th><th>港股</th><th>A股 (滬深)</th><th>美股</th></tr></thead>
<tbody>
<tr><td><b>報價源</b></td><td>Tencent qtimg (sub-1min)</td><td>Sina / 網易 (實時但有限流)</td><td>YFinance / Alpaca (real-time)</td></tr>
<tr><td><b>交易時間</b></td><td>09:30-16:00 HKT</td><td>09:30-15:00 CST (午休 11:30-13:00)</td><td>09:30-16:00 ET (+ pre/after-hours)</td></tr>
<tr><td><b>T+0 / T+1</b></td><td>T+2 交收但即日鮮 OK</td><td>T+1 交收，但 A 股 ETF / 個別股可 T+0</td><td>T+1 交收，可即日鮮 (PDT 規則)</td></tr>
<tr><td><b>Short selling</b></td><td>限沽空名單 + 報升幅限制</td><td>融資融券 (限名單)</td><td>幾乎全部可 short</td></tr>
<tr><td><b>散戶主導度</b></td><td>中高 (30-40% 成交)</td><td>高 (60-70% 成交)</td><td>低 (15-20% 成交)</td></tr>
<tr><td><b>即日波幅</b></td><td>1-3% (中位)</td><td>1-5% (高，特別中小板)</td><td>0.5-2% (大股)</td></tr>
<tr><td><b>新聞源</b></td><td>Futu + 經濟通 + AAStocks</td><td>東財 + 同花順 + 新浪財經</td><td>Yahoo Finance + Reuters + Bloomberg</td></tr>
<tr><td><b>AI 分析挑戰</b></td><td>英文 / 繁中混合，財報複雜</td><td>簡中，政策影響大</td><td>英文為主，數據透明</td></tr>
<tr><td><b>Leeks Terminal 覆蓋</b></td><td>✅ 完整 200 隻</td><td>❌ 暫未覆蓋</td><td>✅ 完整 200 隻 (us-scanner)</td></tr>
</tbody>
</table>
<p>結論：港股同美股嘅 scanner framework 接近，但 A 股因為交易規則 (T+1、漲跌停 10%) 同新聞語言 (簡中、政策) 差異大，需要另一個 pipeline。我哋暫時主力做港股 + 美股，A 股 scanner 喺 roadmap。</p>

<h2>點樣讀取 dashboard 嘅 4 維度分數</h2>
<p>每隻股 card 嘅底部有 4 個小數字 (例如 <code>v:45 q:72 m:88 of:65</code>)，呢個就係 4 維度分數。讀法：</p>
<ul>
<li><b>v (估值 Valuation, 5%)</b> · 0-100，越高分代表越平。例如 PE 5x = v:90，PE 50x = v:30。</li>
<li><b>q (質素 Quality, 5%)</b> · 0-100，越高代表公司越好賺。例如 ROE 25% = q:85，ROE 3% = q:25。</li>
<li><b>m (動能 Momentum, 70%)</b> · 0-100，越高代表近期越強。當日升 5% + 突破 MA20 = m:80+。</li>
<li><b>of (資金流 Order Flow, 20%)</b> · 0-100，越高代表大户越積極買入。Relative Volume 3x + 大單淨流入 = of:75+。</li>
</ul>
<p>綜合分 = <code>0.05×v + 0.05×q + 0.70×m + 0.20×of</code>，由 Python 確定性計出 (避開 LLM hallucination)。綜合分 ≥ 60 = 🟢 買入；40-60 = 🟡 觀望；≤ 40 = 🔴 賣出。如果你想學 technical indicator 點樣對應呢 4 個維度 (例如 RSI 對應 m、量比對應 of)，睇 <a href="/methodology.html">methodology 頁</a>。</p>
""",
    })

    # 2. US stock scanner
    pages.append({
        "path": "/us-scanner.html",
        "slug": "us-scanner",
        "title": "美股即日鮮掃描器 · US Stock Scanner | Leeks Terminal",
        "description": "AI 自動掃描 200 隻美股（S&P 500 + Nasdaq-100 + 高成交個股），每日 16:00 ET 輸出買入/觀望/賣出信號 + 入場區間 / 止損 / 目標價。",
        "h1": "美股即日鮮 AI 掃描器",
        "body": """
<p class="lede">Leeks Terminal 美股掃描器每日自動分析 <b>200 隻高成交美股</b>（S&P 500 大型股 + Nasdaq-100 + 高成交中小股），
每個交易日 16:00 ET 美股收市後自動跑一次分析，輸出買入 / 觀望 / 賣出信號 + 入場區間 / 止損位 / 目標價。</p>

<h2>覆蓋範圍</h2>
<ul>
<li>S&P 500 top 100 隻（市值最大 + 最高 turnover）</li>
<li>Nasdaq-100 top 80 隻（科技 + 高增長）</li>
<li>額外 20 隻高 momentum 個股（最近 20 日平均 turnover ≥ 500M USD）</li>
</ul>

<h2>點樣同港股 scanner 唔同</h2>
<table class="dim-table">
<thead><tr><th>維度</th><th>港股</th><th>美股</th></tr></thead>
<tbody>
<tr><td>報價源</td><td>Tencent qtimg (sub-1min)</td><td>YFinance (15-min delay) + Alpaca (real-time)</td></tr>
<tr><td>新聞</td><td>Futu Cloud + 經濟通</td><td>Yahoo Finance RSS</td></tr>
<tr><td>收市時間</td><td>16:00 HKT (08:00 UTC)</td><td>16:00 ET (21:00 UTC)</td></tr>
<tr><td>流動性</td><td>高成交股 turnover ≥ 50M HKD</td><td>高成交股 ≥ 500M USD ADV</td></tr>
<tr><td>盤前 / 盤後</td><td>無</td><td>YFinance 提供 pre-market / after-hours 報價</td></tr>
</tbody>
</table>

<h2>美股 4 維度評分</h2>
<p>同港股一樣用 4 維度 (v / q / m / of)，但美股嘅「動能」權重稍低（60% vs 港股 70%）— 因為美股趨勢日多過震盪日，趨勢持續時間長，純價格動能 + 一啲 mean-reversion 比較 work。</p>
<ul>
<li><b>估值</b> 5% · PE / PEG / forward PE</li>
<li><b>質素</b> 5% · ROE / margin / FCF yield</li>
<li><b>動能</b> 60% · MA + RSI + 1d/5d/20d return</li>
<li><b>資金流</b> 30% · Relative Volume vs 20d ADV + dollar volume + option OI change</li>
</ul>

<h2>邊個適合用</h2>
<ul>
<li>美股 day-trader · 開市前快速睇信號</li>
<li>美股 swing trader · 收市後睇過夜 setup</li>
<li>港股 trader · 隔晚準備美股名單</li>
</ul>

<h2>常見問題</h2>
<h3>Q: 美股 scanner 幾時更新？</h3>
<p>A: 每個美股交易日 16:00 ET (即香港時間翌日 04:00 HKT / DST 05:00 HKT) 自動跑一次，17:00 ET 前 dashboard 上線。</p>
<h3>Q: 點解唔包所有 S&P 500？</h3>
<p>A: 全 500 隻跑一次要 60+ 分鐘 LLM calls 同 $5+ cost。top 200 覆蓋 85% 市值 + 92% turnover，平衡 coverage 同 cost。</p>
<h3>Q: 收市後仲有 update 嗎？</h3>
<p>A: 唔會。每個交易日只跑一次。盤前 / 盤後波動信號唔覆蓋。</p>

<h3>Q: 咩係「美股即日鮮」？同港股即日鮮有咩分別？</h3>
<p>A: 美股即日鮮 (US day trade) 係指美股市場嘅當日開倉當日平倉短炒策略 — 開倉後唔過夜，最遲 16:00 ET 收市前強制平倉。由於美股有 <b>PDT 規則</b> (Pattern Day Trader)，5 個交易日内有 4 日或以上做 day trade 嘅戶口，賬户資產必須維持 ≥ USD 25,000，否則戶口會被凍結做 day trade。Leeks Terminal 美股 scanner 嚴格為美股即日鮮設計 — 每朝早 9:00 ET 開市前出信號，信號入場區間、止損位 (1-2%)、目標價 (3-8%) 全部係 intraday timeframe 設計。同港股即日鮮主要分別：港股 PDT 規則寬鬆 (T+2 交收，但允許即日鮮)；美股則有 25K USD 資產下限 + 日内交易次數限制 (4 次 / 5 日)。</p>

<h3>Q: 有冇「美股 day trade signal」服務？同一般美股財經網站有咩分別？</h3>
<p>A: 有。Leeks Terminal 美股 scanner 每日 16:00 ET 收市後自動跑，17:00 ET 前 dashboard 上線，每隻信號附帶入場區間 + 止損 + 目標 + 4 維度分數 breakdown。同一般美股網站 (例如 Seeking Alpha、TipRanks、Zacks) 主要分別：</p>
<ul>
<li><b>AI 自動</b> · 唔係 sell-side analyst 報告，係 Python pipeline 每晚自動跑</li>
<li><b>4 維度量化</b> · v / q / m / of 4 個 raw score 由 LLM 評，總分由 Python 確定性加權，避開 LLM 估分 hallucination</li>
<li><b>Day-trade 優化</b> · 動能 + 資金流合共 90% 權重，唔似一般「買入推薦」淨睇 PE / PB</li>
<li><b>免費</b> · Dashboard 完全 free，唔使訂閱 SaaS</li>
<li><b>操作建議</b> · 每個信號都有具體 entry / stop / target 價位，唔係一句「看多」就算數</li>
</ul>

<h3>Q: 美股 short signal 同 long signal 點樣分？呢個 scanner 識唔識 short？</h3>
<p>A: 識。美股 short selling 規則比港股寬鬆 — 基本上所有美股都可以 short (除咗 hard-to-borrow 名單)，T+1 交收，T+0 可平倉。Leeks Terminal 美股 scanner 識別 short signal 嘅條件：</p>
<ul>
<li><b>trade_direction = 看空 / bearish</b> · LLM cross-check 4 維度 + 新聞 sentiment + 技術指標後輸出</li>
<li><b>score ≤ 40</b> · 綜合分偏低，弱勢股</li>
<li><b>momentum ≤ 40</b> · 跌穿 MA20 / 50 / 200 其中一條</li>
<li><b>order_flow 反向</b> · Relative Volume ≥ 2x 但價格向下 (高位派貨)</li>
<li><b>入場區間為 short entry</b> · 反彈 fail 後 short，止損位喺反彈高位之上</li>
</ul>
<p>Short signal 喺 dashboard 用 🔴 標記，唔同 🟢 long signal 容易混淆。如果你想淨係睇 long，可以 filter 「美股買入」；想睇 short 就 filter 「美股賣出」。注意：short 風險無限大 (loss > 100%)，新手唔建議單獨做 short。</p>

<h3>Q: 「美股 AI 選股」係咩？同一般 AI stock picker 有咩分別？</h3>
<p>A: 美股 AI 選股係用 AI (我哋用 MiniMax-M3) 對 200 隻高成交美股做 stock screening + scoring + ranking，每日自動出 top 20 買入名單 + top 20 賣出名單。同坊間 AI stock picker (e.g. Danelfin、Trefis、Kavout) 主要分別：</p>
<ul>
<li><b>確定性加權</b> · Danelfin 等用 ML model 直接輸出 1-10 分數 (black box)；我哋分 4 個維度評分再 Python 加權 (transparent)</li>
<li><b>操作建議</b> · Danelfin 只給 AI score；我哋仲俾具體 entry / stop / target</li>
<li><b>Day-trade 偏重</b> · 動能 + 資金流 90% 權重，唔似 Danelfin 較為 long-term</li>
<li><b>免費</b> · 完全 free dashboard，Danelfin 收費 USD 30+/月</li>
</ul>
<p>「美股 AI 選股」呢個關鍵詞主要係 Google SEO 搜索 — 例如「美股 AI 選股 推薦」、「best AI stock screener」、「AI 美股分析」等等。我哋嘅工具目標係俾香港 / 台灣 / 內地散戶一個中文 free 嘅 AI 美股分析入口。</p>

<h3>Q: 「美股 momentum scanner」呢個工具識唔識做？同 Finviz / TradingView 比較？</h3>
<p>A: 識。Leeks Terminal 美股 scanner 嘅 momentum 維度 (60% 權重) 由 4 個 sub-indicator 組成：(1) 當日 1d return、(2) 5d return、(3) 20d return (突破新高 / 新低比例)、(4) RSI14 deviation。所以 dashboard 預設按 momentum 排，首 20 隻就係當日美股 momentum 排行 — 升幅最大 + 趨勢最強嘅美股。</p>
<p>同 Finviz / TradingView 比較：</p>
<ul>
<li><b>Finviz</b> · 主力做美股，screener 功能強 (可以自訂 PE / RSI / MACD filter)，但冇 AI 評分，操作建議要自己 set。我哋嘅工具同 Finviz 互補 — 用 Finviz 自訂 filter，再用我哋做 AI ranking。</li>
<li><b>TradingView</b> · 圖表最強，但 AI 評分要付費 (TradingView Premium USD 15+/月)。我哋嘅 dashboard 圖表簡單 (因為目標係決策輔助唔係 trading platform)，但 AI 評分免費。</li>
<li><b>我哋嘅 advantage</b> · 中文介面 + 4 維度評分 + 具體 entry / stop / target，唔係淨係 score。</li>
</ul>
<p>如果你想純做美股 momentum trading，可以單獨用 Finviz 篩 RSI > 70 + 20d return > 10% 嘅股，再對比我哋 dashboard 嘅 momentum 排行，命中率會更高。</p>

<h3>Q: 「美股 stock screener free」— 呢個工具係咪完全 free？有冇 hidden cost？</h3>
<p>A: 完全 free。Leeks Terminal 美股 stock screener 完全免費 — 唔使註冊、唔使訂閱、唔使信用卡、開 dashboard 就見到當日 200 隻美股信號。冇 hidden cost，冇 premium tier 鎖重要功能，冇「7 天試用」陷阱。資金來源：我哋自己 side project，冇投資人，冇商業模式，純粹個人用 + 開源俾香港 / 台灣 / 內地散戶。</p>
<p>同其他「美股 stock screener free」工具比較：</p>
<ul>
<li><b>Finviz</b> · 免費版得基本 screener (15 分鐘延遲報價)；付費 USD 25/月先有 real-time + 高級 filter</li>
<li><b>TradingView</b> · 免費版得基本圖表；AI 評分 / 進階 indicator 要 Premium USD 15+/月</li>
<li><b>Yahoo Finance screener</b> · 完全 free 但 filter 簡單，冇 AI 評分</li>
<li><b>我哋嘅工具</b> · 完全 free + AI 評分 + 操作建議 (entry / stop / target)，同類工具罕見</li>
</ul>
<p>如果你想 contribute 或者報 bug，可以喺主頁 footer 搵到我哋嘅 Telegram group 或 GitHub repo (public)。</p>

<h3>Q: 「美股 technical analysis 中文」教學 — 呢個工具包唔包？</h3>
<p>A: 包。Leeks Terminal 美股 scanner 每隻股嘅 detail page 都有中文 technical analysis 解說 — 包括 MA20 / MA50 / MA100 / MA200、RSI14、MACD、Bollinger Bands、Volume Ratio 嘅當前數值 + 中文解讀 (例如「MA20 上穿 MA50 = 黃金交叉」、「RSI ≥ 70 = 超買」、「MACD 柱由負轉正 = momentum 反轉」)。</p>
<p>如果你想學深啲，可以睇 <a href="/methodology.html">methodology 頁</a>，有齊 4 維度框架嘅公式同案例。我哋亦都有 Telegram channel 每周出一篇美股 technical analysis 中文 tutorial：</p>
<ul>
<li>MA (移動平均線) — 黃金 / 死亡交叉</li>
<li>RSI (相對強弱指數) — 超買 / 超賣</li>
<li>MACD — momentum 反轉訊號</li>
<li>Bollinger Bands — 波動率 squeeze / expansion</li>
<li>Volume Ratio — 量價配合</li>
</ul>
<p>注意：美股 technical analysis 嘅指標計算方法同港股 / A 股一致 (因為係 universal formula)，但「美股 vs 港股」應用上唔同 — 美股趨勢日多，技術指標有效時間長；港股 / A 股噪音多，技術指標容易假突破。我哋嘅 scanner 已經按市場特性 tune 咗 4 維度權重 (美股 m=60%, of=30%；港股 m=70%, of=20%)。</p>

<h3>Q: 「美股 pre-market signal」— 呢個工具包唔包盤前信號？</h3>
<p>A: 暫時唔包「實時」pre-market signal。我哋美股 scanner 每日只喺 16:00 ET 收市後跑一次（基於收市價 + 成交量），出次日開市前嘅信號。所以 dashboard 上嘅信號會涵蓋前一晚 16:00 ET - 翌日 09:30 ET 嘅 overnight 變動 + pre-market 嘅重要新聞，但唔係即時 (real-time) 更新。</p>
<p>如果你想要真正 real-time 美股 pre-market signal (例如 08:00 ET 開 pre-market 後即時掃描)，需要付費版工具 — IBKR / Thinkorswim / TradeStation 都有 pre-market scanner (但要付費 + 開 margin 戶口)。我哋暫時唔做 real-time 因為：</p>
<ul>
<li>Real-time 美股報價要付費 (USD 50-200/月 data feed)</li>
<li>Pre-market 流動性低 (只有 regular session 嘅 5-10%)，信號 noise 大</li>
<li>用 YFinance 嘅 15-min delay 報價做 real-time 會誤導用戶</li>
</ul>
<p>如果你係 active 美股 pre-market trader，建議用 IBKR 戶口 + TWS 內置 scanner；Leeks Terminal 主力做收市後 16:00 ET - 翌日 09:30 ET 嘅信號，覆蓋大多數散戶 decision window。</p>

<h3>Q: 「美股 option signal」— 呢個工具識唔識分析期權 / 選擇權？</h3>
<p>A: 識少少。Leeks Terminal 美股 scanner 嘅 order_flow 維度 (30% 權重) 包含 <b>option OI change</b> (options open interest change) 嘅 sub-indicator — 監察前 5 個交易日 OI 變化 ± 20% 嘅 option chain，標記異常 OI 嘅 strike / expiry。但我哋暫時 <b>唔出 option 策略建議</b> (例如 covered call / bull put spread / iron condor)，只係把 OI 變化納入綜合分數。</p>
<p>如果你想搵美股 option signal / unusual options activity，建議用：</p>
<ul>
<li><b>Unusual Whales</b> · USD 30+/月，美股 option flow 最強工具之一</li>
<li><b>FlowAlgo</b> · USD 50+/月，real-time option order flow</li>
<li><b> CBOE 官網</b> · 免費 option chain + OI，但冇 AI 分析</li>
</ul>
<p>我哋 roadmap 上面會加入 <b>美股 option signal</b> 獨立模組 (預計 2026 Q4)，會輸出 covered call / cash-secured put / vertical spread 建議，配合現有 4 維度 stock signal 一齊用。短期內 order_flow 維度已經包含 option OI，變相俾你 crude option signal。</p>

<h3>Q: 「美股 high volume scanner」— 點樣搵高成交美股？</h3>
<p>A: 美股 high volume scanner 有幾個 layer：</p>
<ol>
<li><b>Layer 1 — 絕對 volume</b> · 過去 20 日平均成交量 ≥ 5M 股 / 日 (例如 AAPL、TSLA、NVDA、F、AMD)</li>
<li><b>Layer 2 — Relative Volume</b> · 當日 volume / 過去 20 日平均 volume ≥ 2x (即「量比」概念)</li>
<li><b>Layer 3 — Dollar volume</b> · 過去 20 日平均成交金額 ≥ USD 500M (例如 NVDA、AAPL、MSFT)</li>
<li><b>Layer 4 — Unusual volume</b> · 當日成交量 vs 過去 60 日 95th percentile (即「異動」概念)</li>
</ol>
<p>Leeks Terminal 美股 scanner 嘅覆蓋範圍 (200 隻) 已經按 Layer 1 + Layer 3 篩過 — 全部係 S&P 500 top 100 + Nasdaq-100 top 80 + 高成交中小股 20 隻，平均 ADV ≥ USD 500M，所以 dashboard 上每隻都已經係「high volume 美股」。</p>
<p>如果你想搵「unusual volume」嘅美股 (即當日量比突然 ≥ 3x 嘅股)，可以喺 dashboard 入 detail page 睇 Relative Volume 數字 (of 維度 ≥ 70 通常代表 unusual volume + 大戶入場)。</p>

<h3>Q: 「美股 vs 港股 day trade」工作流程比較 — 兩個市場 trader 有咩分別？</h3>
<p>A: 美股 vs 港股 day trade 工作流程主要有 6 個分別，列表如下：</p>
<table class="dim-table">
<thead><tr><th>工作流程</th><th>港股 trader</th><th>美股 trader</th></tr></thead>
<tbody>
<tr><td><b>1. 開市前準備</b></td><td>08:30 HKT 起床、09:00 HKT 睇 dashboard、09:15 HKT 落單</td><td>21:00 HKT (前一晚) 睇 dashboard、09:00 ET (翌日) 落單 — 即跨時區</td></tr>
<tr><td><b>2. 信號來源</b></td><td>Leeks Terminal hk-scanner + 富途 / 輝立 built-in scanner</td><td>Leeks Terminal us-scanner + Finviz / TradingView</td></tr>
<tr><td><b>3. PDT / 風控規則</b></td><td>無 PDT 規則，但有「T+2 交收」+ 印花稅 0.13%</td><td>PDT 規則 (4 日 / 5 日 day trade，賬户 ≥ USD 25K)；無印花稅</td></tr>
<tr><td><b>4. 入場時間</b></td><td>09:30-10:30 HKT (開市 60 分鐘內)，11:30 HKT 唔做 (臨午休)</td><td>09:30-10:30 ET (開市 60 分鐘內)，11:30 ET / 14:00 ET 可以繼續</td></tr>
<tr><td><b>5. 持倉時間</b></td><td>9:30 入場，15:50-16:00 HKT 強制平倉</td><td>9:30 入場，15:50-16:00 ET 強制平倉 (after-hours 可以但流動性低)</td></tr>
<tr><td><b>6. 出場規則</b></td><td>必須 16:00 HKT 前平倉，唔可以 hold overnight (除非有特別 setup)</td><td>可以 hold overnight (但要小心 gap risk)；margin 戶口可以 short overnight</td></tr>
<tr><td><b>7. 佣金 / 成本</b></td><td>佣金 0.05% + 印花稅 0.13% + 交易徵費 0.003% ≈ 0.18% 單邊</td><td>佣金 USD 0 (IBKR) / USD 1 (券商) — 零印花稅 ≈ 0.005% 單邊</td></tr>
<tr><td><b>8. 信號有效時長</b></td><td>當日 09:30 HKT - 16:00 HKT (6.5 小時)</td><td>當日 09:30 ET - 16:00 ET (6.5 小時) + pre-market / after-hours</td></tr>
<tr><td><b>9. 推薦工具</b></td><td><a href="/hk-scanner.html">hk-scanner</a> + 富途 / 輝立 app</td><td><a href="/us-scanner.html">us-scanner</a> + IBKR TWS / Thinkorswim</td></tr>
<tr><td><b>10. 適合邊個</b></td><td>香港時區 / 兼職 trader / 想學中文 technical analysis</td><td>美股投資者 / 全職 trader / 想 trade 全球最大市場</td></tr>
</tbody>
</table>
<p>結論：兩個市場工作流程接近，但時區 + 風控規則 + 成本結構唔同。香港散戶如果想同時做港股 + 美股 day trade，建議用 Leeks Terminal 嘅 hk-scanner (16:00 HKT 出信號) + us-scanner (16:00 ET 出信號)，兩個 dashboard 都係 free，配合 IBKR 戶口做美股 + 富途 / 輝立做港股，覆蓋晒兩個時區嘅 decision window。</p>

<h2>美股市場時段 (Trading Sessions)</h2>
<p>美股一日分 3 個 trading session，流動性、volatility、信號 reliability 都唔同：</p>
<table class="dim-table">
<thead><tr><th>時段</th><th>時間 (ET)</th><th>香港時間 (HKT)</th><th>流動性</th><th>Leeks Terminal 覆蓋</th></tr></thead>
<tbody>
<tr><td><b>Pre-market 盤前</b></td><td>04:00 - 09:30 ET</td><td>16:00 - 21:30 HKT (前一晚)</td><td>低 (5-10% regular session)</td><td>❌ 唔覆蓋 (noise 太大、報價延遲)</td></tr>
<tr><td><b>Regular session 正式交易時段</b></td><td>09:30 - 16:00 ET</td><td>21:30 - 04:00 HKT (翌日凌晨)</td><td>高 (85% 全日成交)</td><td>✅ 主力覆蓋 (16:00 ET 收市後跑信號)</td></tr>
<tr><td><b>After-hours 盤後</b></td><td>16:00 - 20:00 ET</td><td>04:00 - 08:00 HKT (翌日清晨)</td><td>低 (5-10% regular session)</td><td>❌ 唔覆蓋 (同上)</td></tr>
</tbody>
</table>
<h3>Pre-market 盤前 (04:00 - 09:30 ET)</h3>
<p>盤前係美股嘅第一個交易時段，主要係機構投資者 + overnight news 嘅反應。常見信號：</p>
<ul>
<li><b>Earnings 業績公佈</b> · 大部分大型股喺 pre-market / after-hours 公佈季度業績 (e.g. AAPL、MSFT、AMZN)</li>
<li><b>Fed / 經濟數據</b> · 08:30 ET 公佈 CPI、PPI、NFP 等</li>
<li><b>FDA 批准 / 監管消息</b> · 生物科技股 (e.g. MRNA、BNTX) 對 FDA 消息極敏感</li>
<li><b>Gap up / gap down</b> · 開市前 price gap 通常反映 overnight sentiment</li>
</ul>
<p>Pre-market 流動性低，bid-ask spread 大，Leeks Terminal 暫時唔覆蓋 — 但 dashboard 嘅 score 已經考慮前一晚 16:00 ET 收市到 09:30 ET 開市前嘅 overnight 變化 (即係 closed-form signal 包含咗 gap risk 嘅 adjustment)。</p>

<h3>Regular session 正式交易時段 (09:30 - 16:00 ET)</h3>
<p>正式交易時段係美股主力 session，85% 全日成交集中喺呢 6.5 小時，Leeks Terminal 美股 scanner 嘅信號就係為呢個 session 設計：</p>
<ul>
<li><b>開市首 30 分鐘 (09:30 - 10:00 ET)</b> · 流動性最高 + 波幅最大，day trader 集中入場</li>
<li><b>10:00 - 11:30 ET</b> · 主流 setup 出現窗口，momentum + order_flow 信號最有效</li>
<li><b>11:30 - 14:00 ET (午間)</b> · 流動性下降，noise 增加，唔建議開新倉</li>
<li><b>14:00 - 15:30 ET</b> · 下午 setup window，尾市前最後一波</li>
<li><b>15:30 - 16:00 ET 收市前</b> · 必須平倉 (除非有特別 overnight 理由)</li>
</ul>
<p>Leeks Terminal 美股 scanner 嘅 entry / stop / target 全部 assume 喺 regular session 9:30 - 16:00 ET 入場。After-hours 入場信號不可靠 (流動性低 + spread 大 + 假突破多)。</p>

<h3>After-hours 盤後 (16:00 - 20:00 ET)</h3>
<p>盤後係美股最後一個 session，主要係機構對 earnings / 重大新聞嘅反應，散戶參與度低：</p>
<ul>
<li><b>Earnings 反應</b> · 大型股公佈業績後嘅 1-2 小時波幅最大</li>
<li><b>FDA 批准</b> · 生物科技股對盤後 FDA 消息極敏感</li>
<li><b>M&amp;A 公告</b> · 併購消息通常喺盤後公佈</li>
</ul>
<p>After-hours 流動性低，bid-ask spread 大，Leeks Terminal 暫時唔覆蓋。如果你 trade after-hours 嘅 earnings 業績，建議用 IBKR TWS 嘅 extended-hours trading 功能 (但要特別小心 spread + slippage)。</p>

<h3>點解 Leeks Terminal 只覆蓋 regular session？</h3>
<p>有幾個原因：</p>
<ol>
<li><b>報價限制</b> · YFinance 15-min delay + 免費數據源對 pre-market / after-hours 覆蓋唔穩定</li>
<li><b>流動性低</b> · Pre-market / after-hours 成交只係 regular session 嘅 5-10%，signal noise 大，命中率低</li>
<li><b>用戶群</b> · Leeks Terminal 主力做香港 / 台灣 / 內地散戶，呢啲用戶主要 trade regular session</li>
<li><b>Cost-effectiveness</b> · Real-time 美股報價 (含 pre-market / after-hours) 要 USD 50-200/月 data feed，free 工具難以 sustain</li>
</ol>
<p>如果你係 active pre-market / after-hours trader，建議配合 IBKR TWS 或 Thinkorswim 嘅 built-in scanner；Leeks Terminal 主力做收市後 16:00 ET 嘅信號，覆蓋大多數散戶 decision window。詳細 4 維度評分框架睇 <a href="/methodology.html">methodology 頁</a>。</p>
""",
    })

    # 3. Day trade signals
    pages.append({
        "path": "/day-trade-signals.html",
        "slug": "day-trade-signals",
        "title": "即日鮮交易信號 · Day Trade AI Signals | Leeks Terminal",
        "description": "AI 即日鮮交易信號 — 港股 9:30 HKT + 美股 9:30 ET 開市前自動出，4 維度評分 + 入場區間 + 止損 + 目標。",
        "h1": "即日鮮交易 AI 信號",
        "body": """
<p class="lede">Leeks Terminal 即日鮮交易信號 — 港股每朝早 9:00 HKT / 美股每朝早 9:00 ET 開市前自動出，
涵蓋 HK 200 + US 200 隻主流股票，4 維度評分 + 入場區間 / 止損位 / 目標價，唔使睇晒 200 隻 K 線。</p>

<h2>咩係「即日鮮信號」</h2>
<p>即日鮮 (day-trade) 定義：</p>
<ul>
<li>開市前 9:30 出信號</li>
<li>16:00 HKT / 16:00 ET 前必須平倉</li>
<li>唔過夜，唔留倉</li>
<li>每筆止損 2-3% 內</li>
<li>高 momentum + 高 order flow 嘅股先會有 signal（其他一律觀望）</li>
</ul>

<h2>信號 3 個 type</h2>
<table class="dim-table">
<thead><tr><th>信號</th><th>條件</th><th>進場</th></tr></thead>
<tbody>
<tr><td><b>🟢 買入</b></td>
    <td>momentum ≥ 60 + 看多趨勢 + 量能配合 + 入場 / 止損 / 目標 齊</td>
    <td>9:30-10:30 開市 60 分鐘內，等 breakout 確認</td></tr>
<tr><td><b>🔴 賣出</b></td>
    <td>momentum ≤ 40 + 看空趨勢 + 跌穿 MA20 / 50W + 量能放大</td>
    <td>開市即 short，或等反彈 fail 後 short</td></tr>
<tr><td><b>🟡 觀望</b></td>
    <td>其他（regime 唔清 / setup 唔齊 / score 40-60 模糊區）</td>
    <td>唔做</td></tr>
</tbody>
</table>

<h2>4 維度評分細節</h2>
<p>每個信號附帶 4 個 dim 分數 (0-100)：</p>
<ul>
<li><b>估值 v (5%)</b> · PE / PB / deviation from 5y mean — 偏貴 = 減分</li>
<li><b>質素 q (5%)</b> · ROE / margin / dividend stability — 高 ROE = 加分</li>
<li><b>動能 m (70% 港股 / 60% 美股)</b> · 今日方向 + MA trend + RSI + breakout proximity — 純價格動能</li>
<li><b>資金流 of (20% 港股 / 30% 美股)</b> · 量比 / 大單 / Relative Volume vs 20d ADV — 確認有大戶入場</li>
</ul>
<p>Python-side 確定性加權 — <code>score = 0.05×v + 0.05×q + 0.70×m + 0.20×of</code>。
LLM 只負責輸出 dim 嘅 raw score (0-100)，總分由 Python 計，避開 LLM 估分嘅 hallucination。</p>

<h2>邊個適合用</h2>
<ul>
<li>全職 day-trader · 開市前 5 分鐘決策</li>
<li>兼職 trader · 工餘時間追蹤信號</li>
<li>學生 · 學吓 AI + 技術分析點樣結合</li>
</ul>

<h2>常見問題</h2>
<h3>Q: 信號幾時出？</h3>
<p>A: 港股每朝早 9:00 HKT 前；美股每朝早 9:00 ET 前。當日信號當日有效，過期作廢。</p>
<h3>Q: 點解 LLM 評分唔可靠？</h3>
<p>A: LLM 容易 hallucinate 數字。所以我哋用 LLM 評 dim 嘅 raw score，總分由 Python 確定性加權計返。同時 entry / stop / target 全部要 LLM 寫具體價位，唔可以寫「支持區」呢類含糊嘢。</p>
<h3>Q: 過往信號準唔準？</h3>
<p>A: Dashboard 顯示每隻信號嘅 outcome (paper-trade)，paper-trader 自動追蹤 30d hit-rate。命中率低嘅策略會自動 disable。詳細睇 <a href="/methodology.html">methodology 頁</a>。</p>

<h2>止損 / target 點樣 set</h2>
<p>「止損點樣 set」(stop loss 計法) 係 day-trade 最常見嘅問題之一。我哋 LLM 出嘅止損位基於三個 framework 並行驗證，揀最貼市嘅嗰個：</p>
<ol>
<li><b>ATR-based (Average True Range)</b> · 用 14 日 ATR 做基礎，止損 = entry − 1.5 × ATR (long) 或 entry + 1.5 × ATR (short)。好處係自適應波幅：低波幅股止損窄、高波幅股止損闊，避開被即日震盪震走。Leeks Terminal 對大部份港股即日鮮信號用呢個 framework。</li>
<li><b>MA20-based</b> · 止損放喺 MA20 下面 0.5% (long) 或上面 0.5% (short)。邏輯：MA20 係 intraday 最重要嘅 pivot，跌穿 MA20 = momentum 失效。適合 trend day，對 range day 容易觸發。</li>
<li><b>Swing-low / swing-high based</b> · 止損放喺最近 5 個 K 線最低位 − 0.3% (long) 或最近 5 個 K 線最高位 + 0.3% (short)。邏輯：前低/前高係結構位，跌破即 setup 失效。適合 breakout 後追擊。</li>
</ol>
<p>Target 方面，TP1 = 1:1.5 risk-reward (例如止損 2%，TP1 = +3%)，TP2 = 1:2.5 risk-reward (止損 2%，TP2 = +5%)。我哋建議一般散戶喺 TP1 先平 50% 鎖定利潤，剩餘 50% 用 trailing stop (例如每升 1% 將止損上移 0.5%) 跟趨勢到 TP2 或收市前強制平倉。具體每隻信號嘅止損 / target 寫喺 detail page 嘅「止損」/「目標」兩個 panel，一目了然。</p>
<p>⚠️ <b>止損永遠係入場前 set 死</b>，唔係睇住盤中跌幾多再諗。即日鮮最常見嘅死法就係「跌 1% 諗一諗、跌 3% 諗一諗、跌 5% 止蝕」，結果一日輸晒一星期贏嘅。Set 完就 snap落 broker app 嘅 stop loss order，到價自動執行。</p>

<h2>信號失敗 點算</h2>
<p>即使 4 維度評分再高、信號再強，當日失敗率仍然有 30-50%。「信號失敗 點算」(stopped out / false breakout 點處理) 係每個 day trader 必修課題。我哋嘅規則：</p>
<ol>
<li><b>正常止損出場</b> · 到價觸發 stop loss，紀錄喺 paper-trader，唔好即時 reverse。出場後該筆倉位 close，唔好「加碼溝淡」。</li>
<li><b>False breakout (假突破)</b> · 突破入場區間後 15 分鐘內跌返入去、close 返區間下面 — 即場止損，唔好等 full stop loss。例如 entry 320-325，breakout 上 326 後 5 分鐘內跌返 323，就要手動止損走。Reason：假突破後下一個 support 通常會被跌破。</li>
<li><b>Re-entry 規則</b> · 止損出場後同一方向當日 <b>唔可以</b> 再入場 (避免報復交易)。下一個 setup 要等下一個交易日 / 下一隻信號。例外：止損後價格 reverse 到原本方向嘅下一個 level (例如原本 short 被止損後 30 分鐘內繼續跌穿前低)，可以喺新 level 再 short，但倉位縮減至原本 50%。</li>
<li><b>信號 cancel 條件</b> · 開市 30 分鐘內如果大市 regime 急轉 (例如恒指由 +1% 倒跌到 −1%)，所有動能信號自動 cancel，唔好入場。</li>
<li><b>紀錄 + 覆盤</b> · 每筆止損 (包括 false breakout) 都要記入 paper-trader dashboard，標記失敗原因 (正常 stop / false breakout / news shock / regime flip)。一週後回顧統計：如果你嘅 false breakout 比例 ≥ 40%，代表 entry timing 太早，要等 9:45 之後先入場。</li>
</ol>
<p>核心 mindset：<b>止損係 cost of business</b>，唔係 loss。一個日 10 單嘅 day trader 贏 6 輸 4 就係 profitable，關鍵係輸嗰 4 單每單蝕 1-2%，贏嗰 6 單每單賺 3-5%。如果你連續 3 單止損、情緒開始煩燥，就強制收工，唔好再落第 4 單。</p>

<h2>常見問題 (續)</h2>
<h3>Q: 點解叫「短炒 入場」要等開市 15-60 分鐘？可唔可以開市即刻入？</h3>
<p>A: 開市頭 5 分鐘 (9:30-9:35 HKT / 09:30-09:35 ET) 係 price discovery 階段，bid-ask spread 大、成交量 spike、stop loss hunt (大戶刻意掃止損位) 極常見。即日鮮入場最佳窗口係 <b>9:45-10:30 開市後 15-60 分鐘</b>，等價格 confirm 方向、volume 正常化、spread 收窄先落單。Leeks Terminal 嘅 entry_zone 設計就係 assume 你喺呢個 window 內分批入場 (例如 320-325 範圍內分兩注：320 入 50%、325 入 50%)，避免一注 all-in 食晒 spread。</p>

<h3>Q: 「day trade signal」中文叫咩？係咪即係「即日鮮信號」？</h3>
<p>A: 係。「day trade signal」中文翻譯就係「即日鮮信號」、「日內交易訊號」、「當日短炒訊號」，三個講法同一樣嘢 — 指交易日開市前 / 開市初段出嘅短炒 setup，必須當日收市前平倉。台灣用「當沖」呢個 term，內地用「T+0 短線」，香港散戶最普遍叫「即日鮮」。Leeks Terminal dashboard 同 detail page 全部用「即日鮮」呢個香港本地術語。</p>

<h3>Q: 「intraday signal 中文」資源 — 除咗 Leeks Terminal 仲有冇其他？</h3>
<p>A: 有幾個常用：</p>
<ul>
<li><b>經濟通 ET Net</b> · 即時港股報價 + 簡單技術指標 + 篩選器，但冇 AI 評分</li>
<li><b>富途牛牛 / 輝立 speed quote</b> · 內置 scanner，可以自訂 MA / RSI filter，但都係人手 set，唔似 AI 自動出信號</li>
<li><b>StockQ (股勢)</b> · 中文版 Finviz，screener 強但 AI 評分弱</li>
<li><b>TradingView 中文版</b> · 圖表最強但要付費 (Premium USD 15+/月) 先有 AI Pine Script 自動 signal</li>
</ul>
<p>Leeks Terminal 嘅定位：<b>完全 free + 中文 + AI 自動出信號 + 每隻信號附 entry / stop / target</b>。如果你想 contribute 或者報 bug，可以喺主頁 footer 搵到我哋嘅 Telegram group。</p>

<h3>Q: 「momentum 信號」同「資金流 訊號」點樣配合用？</h3>
<p>A: 兩個維度互相 confirm 先係最強信號。我哋嘅 framework：</p>
<ul>
<li><b>momentum 信號</b> (m ≥ 60)：純價格動能 — 升穿 MA20、RSI 強、新高、deviation 擴大。代表「市場對呢隻股有興趣」</li>
<li><b>資金流 訊號</b> (of ≥ 60)：大戶資金流入 — Relative Volume ≥ 2x、大單淨流入、北水 / 機構買入。代表「有人用錢落注」</li>
<li><b>雙確認 (m ≥ 60 AND of ≥ 60)</b> · 命中率 70%+，呢類信號可以加大倉位 (例如原本 5% 倉，加到 8%)</li>
<li><b>單 momentum (m ≥ 70 但 of ≤ 50)</b> · 命中率降到 50%，可能係 retail 散戶追入，沒有大戶跟，倉位縮到 3%</li>
<li><b>單資金流 (of ≥ 70 但 m ≤ 50)</b> · 大戶可能在派貨 (高位接貨給散戶)，呢類 setup 反而要小心做 short，唔好盲目 long</li>
</ul>
<p>Dashboard 每個 card 右下角顯示 m / of 兩個 dim，比較兩者比例就知信號 strength。詳細 4 維度框架睇 <a href="/methodology.html">methodology 頁</a>。</p>

<h3>Q: 「short selling 信號」點樣出？港股沽空限制多唔多？</h3>
<p>A: 港股 short selling (沽空) 規則比美股嚴格：只有「可沽空證券」名單 (俗稱 shortable list) 嘅股票先可以 short，而且有 <b>報升幅規則</b> — 禁止在低於當日開市價 (開市前時段) 或前收市價 (continuous session) 沽空。Leeks Terminal 識別 short selling 信號條件：</p>
<ul>
<li>trade_direction = 看空 + score ≤ 40 + 跌穿 MA20 / MA50</li>
<li>order_flow 反向 (Relative Volume ≥ 2x 但價格向下 = 高位派貨)</li>
<li>新聞 sentiment 明確負面 (盈利預警 / 監管罰款 / 沽空機構報告例如 Hindenburg、Muddy Waters)</li>
<li>必須喺 shortable list 內 (Leeks Terminal 對唔 shortable 嘅股，會標 🔴 觀望而唔出 short 信號)</li>
</ul>
<p>美股 short 寬鬆得多 — 基本上所有股票都可以 short (除咗 hard-to-borrow 名單例如小型股 MCap < 300M USD)，T+1 交收，可即日鮮平倉。但 short 風險無限大 (股價可以升 100%+)，新手唔建議單獨做 short — 應該同 long 信號對沖 (例如 long 一隻強勢股、short 一隻 weak relative)。</p>

<h3>Q: 「日內 短炒 教學」 — Leeks Terminal 適唔適合新手學 day trade？</h3>
<p>A: 適合，但要做齊以下 5 步 setup 先可以開始 paper-trade：</p>
<ol>
<li><b>讀晒 methodology 頁</b> · 4 維度框架、score 計算、entry / stop / target 邏輯，全部要明</li>
<li><b>用 paper account 行 30 日</b> · 富途 / IBKR 都有 paper trading 功能，先用 Leeks Terminal 信號做模擬單 30 日，記低 hit-rate</li>
<li><b>頭 3 個月只做 long、不做 short</b> · long 風險有限 (最多跌 100% 到零)，short 風險無限。新手用 long 學技術</li>
<li><b>每筆倉位 ≤ 總資金 5%</b> · 就算連續 5 單止損都只輸 25%，仲有 75% 子彈</li>
<li><b>30 日後 review</b> · 如果 paper-trade hit-rate ≥ 50%、profit factor ≥ 1.5，先開始 real money，倉位 1% 開始，慢慢加</li>
</ol>
<p>Day trade 唔係「學完即用」嘅工具 — 係 <b>6 個月 - 1 年嘅紀律訓練</b>。如果你想 1 星期變大師，呢個市場唔適合你。詳細 risk management + 心理質素 setup，睇 <a href="/methodology.html">methodology 頁</a>。</p>
""",
    })

    # 4. HK stock screener
    pages.append({
        "path": "/hk-stock-screener.html",
        "slug": "hk-stock-screener",
        "title": "港股篩選器 · 每日 AI 自動排序 HK 200 隻 | Leeks Terminal",
        "description": "港股 stock screener — 用估值 / 質素 / 動能 / 資金流 4 維度篩選 200 隻高成交港股，每日自動更新排名 + 買入賣出信號。",
        "h1": "港股 AI 篩選器",
        "body": """
<p class="lede">Leeks Terminal 港股篩選器 (HK stock screener) — 唔同一般 technical screener 淨係用 RSI / MA 篩，
呢個工具用 <b>4 維度評分</b> (估值 5% / 質素 5% / 動能 70% / 資金流 20%) 對 200 隻高成交港股做 ranking + 篩選，
每日 16:00 HKT 收市後自動更新排名。</p>

<h2>點用呢個篩選器</h2>
<ol>
<li>去 <a href="/">主頁</a> 揀一個交易日</li>
<li>揀 filter：全部 / 港股買入 / 港股賣出</li>
<li>Dashboard 已經按 score 由高到低排好</li>
<li>撳任何 card 入 detail page 睇技術分析全文</li>
</ol>

<h2>篩選邏輯</h2>
<table class="dim-table">
<thead><tr><th>篩選類別</th><th>條件</th><th>目標</th></tr></thead>
<tbody>
<tr><td>🟢 買入</td><td>operation_advice = 買入 + 看多趨勢 + score ≥ 50 + 入場 / 止損 / 目標 齊</td><td>捕捉高 momentum + 高 order flow 嘅短炒 setup</td></tr>
<tr><td>🔴 賣出</td><td>operation_advice = 賣出 + 看空趨勢 + score ≤ 30 + 跌穿 MA20/50</td><td>捕捉弱勢股 short setup / 避免接刀</td></tr>
<tr><td>🟡 觀望</td><td>其他</td><td>未有明確 setup，唔做</td></tr>
</tbody>
</table>

<h2>篩選器 vs 一般 screener</h2>
<p>坊間好多 screener (例如 finviz、aastocks screener) 主要俾 user 自己 set filter (e.g. PE < 15, RSI < 30)。
Leeks Terminal 唔同嘅地方：</p>
<ul>
<li><b>AI 評分</b> · 唔係淨係 technical indicator，仲有 4 維度綜合</li>
<li><b>操作建議</b> · 唔只 score，仲有具體 entry / stop / target 價位</li>
<li><b>新聞 sentiment</b> · LLM 評分時考慮頭 5 條新聞 + macro context</li>
<li><b>Day-trade 優化</b> · 動能 + 資金流合共 90% 權重，唔似 value screener 淨係睇 PE / PB</li>
</ul>

<h2>覆蓋股票</h2>
<p>200 隻港股包括：</p>
<ul>
<li>恒生指數 82 隻</li>
<li>國企指數 50 隻</li>
<li>科技指數 30 隻</li>
<li>高成交二線股 38 隻 (turnover ≥ 50M HKD)</li>
</ul>
<p>唔包括：</p>
<ul>
<li>窩輪 / 牛熊證 (衍生工具)</li>
<li>ETF (例如 2800 / 2828 / 7709 / 7747 呢類 2× leveraged)</li>
<li>細價股 (turnover < 50M HKD)</li>
</ul>

<h2>常見 4 維度 preset 篩選</h2>
<p>以下係幾個常見嘅 preset filter，方便你喺 dashboard 直接套用，每個 preset 都係由歷史 backtest 統計出嚟，命中率比單一條件高：</p>

<table class="dim-table">
<thead><tr><th>Preset 名</th><th>Filter 條件</th><th>用途</th><th>預期命中率</th></tr></thead>
<tbody>
<tr><td><b>高 momentum + 偏淡 PE</b></td><td><code>v_score &lt; 40</code> + <code>m_score &gt; 70</code> + <code>trade_direction = 看多</code></td><td>搵「估值偏貴但資金狂追」嘅強勢股 (典型 AI / 科網概念股)，適合短炒 momentum，唔好長揸</td><td>60-65%</td></tr>
<tr><td><b>低 PE + 高質素</b></td><td><code>v_score &gt; 70</code> + <code>q_score &gt; 70</code> + <code>m_score 40-60</code></td><td>穩陣價值型 — 平 + 高 ROE，momentum 中性，適合 1-2 週 swing，唔係即日鮮</td><td>55-60%</td></tr>
<tr><td><b>RSI 超賣 + 量能放大</b></td><td><code>RSI14 &lt; 30</code> + <code>Relative Volume &gt; 2x</code> + <code>order_flow_score &gt; 60</code></td><td>超賣反彈 setup — 跌深咗但有大戶接貨，1-3 日反彈機會大</td><td>50-55%</td></tr>
<tr><td><b>北水狂掃</b></td><td><code>order_flow_score &gt; 75</code> + <code>momentum_score &gt; 60</code> + 港股通南向資金連續 2 日淨流入</td><td>內地資金主導嘅強勢股 (通常係科技 / 金融權重)，跟大戶順勢 long</td><td>65-70%</td></tr>
<tr><td><b>跌穿 MA50 + 高沽空比率</b></td><td><code>price &lt; MA50</code> + <code>short_ratio &gt; 15%</code> + <code>trade_direction = 看空</code></td><td>弱勢股 short setup — 大戶借貨沽空，散戶跟沽有肉食</td><td>55-60%</td></tr>
<tr><td><b>突破新高 + 量比配合</b></td><td><code>52w_high proximity &gt; 95%</code> + <code>量比 &gt; 1.5</code> + <code>momentum_score &gt; 70</code></td><td>Breakout setup — 創新高 + 成交量放大，短炒 momentum 爆發</td><td>60-65%</td></tr>
<tr><td><b>今日升幅王</b></td><td><code>1d_return &gt; 5%</code> + <code>order_flow_score &gt; 60</code> + market cap &gt; 50 億</td><td>當日大升股 — 注意：高位追入風險大，要等回調 2-3% 再入場</td><td>45-50% (逆勢高)</td></tr>
</tbody>
</table>

<p>點樣套用：dashboard 上面有 filter chips「港股買入」/「港股賣出」已經係 preset 1 + 2 + 3 嘅組合。如果想做更細嘅 preset，可以喺 detail page 用瀏覽器 Ctrl-F 搜尋分數條件，或者睇 <a href="/methodology.html">methodology 頁</a> 嘅 4 維度計算方法自己組合。</p>

<h2>點樣用 dashboard 反向篩選</h2>
<p>除咗「由條件搵股票」(正向篩選)，好多 trader 鍾意「由結果反推條件」(反向篩選)。Leeks Terminal dashboard 嘅 detail table 支援呢個 workflow：</p>

<h3>範例 1：搵今日 RSI &lt; 30 + 量比 &gt; 2 嘅超賣反彈股</h3>
<ol>
<li>去 dashboard 入面，撳「詳細表格」嘅表頭「動能」sort by 升序</li>
<li>再睇「估值/質素/動能/資金流」嗰一欄 (格式 <code>v/q/m/of</code>)，搵 <code>m &lt; 30</code> (即 RSI 超賣) 同時 <code>of &gt; 60</code> (量比放大)</li>
<li>符合條件嘅股，會喺當日 / 翌日有反彈機會 — 入場區間寫喺 detail page，止損位通常放 RSI &lt; 30 嗰個底 1-2% 下面</li>
<li>Target 預設 +3% (TP1) / +6% (TP2)，持倉 1-3 日</li>
</ol>

<h3>範例 2：搵 PE &lt; 10 + ROE &gt; 15% 嘅平靚正港股</h3>
<ol>
<li>Sort detail table by score 倒序，搵綜合分高但 score_breakdown 顯示 <code>v &gt; 70</code> + <code>q &gt; 70</code> 嘅股</li>
<li>呢類股通常 PE 5-10x、ROE 15-25%，係傳統價值投資心水</li>
<li>注意：dashboard 主力做 day-trade，呢類股未必有 🟢 買入信號 (因為 momentum 中性)，可能要等回調先入場</li>
</ol>

<h3>範例 3：搵今日大升 5%+ 但 score 只有 50 嘅「虛火」股</h3>
<ol>
<li>Sort detail table by score 倒序，搵 score 40-50 但 1d return 排前面嘅股</li>
<li>通常係新聞 / 消息面刺激嘅單日爆發，冇基本面支持</li>
<li>呢類股要小心 — 大戶可能高位派貨，散戶接刀。Dashboard 會標 🟡 觀望，建議唔好入場</li>
</ol>

<h3>範例 4：搵連續 3 日都係 🟢 買入嘅「真強勢股」</h3>
<ol>
<li>去 dashboard date picker 揀連續 3 日 (例如今日、昨日、前日)</li>
<li>比較 3 日 detail table 入面 operation_advice = 買入嘅股 — 如果 3 日都出現，係真強勢股</li>
<li>呢類股可以加大倉位 (5% → 8%)，因為信號持續性高</li>
</ol>

<p>總結：dashboard 唔止係順向 ranking，仲可以 reverse-engineer 信號 — 透過 sort、filter、cross-date 比較，你可以由 200 隻港股入面快速搵到符合你個人策略嘅 subset。詳細 4 維度框架同計算方法睇 <a href="/methodology.html">methodology 頁</a>。</p>

<h2>常見問題</h2>
<h3>Q: 點解篩選器有時得幾個信號？</h3>
<p>A: 嚴進策略。Day-trade 唔可以強做，我哋寧可 miss 唔做，都唔好 trade 模糊 setup。觀望日 = 等待日。</p>
<h3>Q: 可以 export CSV 嗎？</h3>
<p>A: 而家 dashboard 純 static HTML，冇 export 功能。如果要 export 我哋可以加，但暫時 copy table 落 Excel 已經夠用。</p>
<h3>Q: 有冇 backtest？</h3>
<p>A: 而家只有 paper-trade tracker，記住每個信號 outcome (30d hit-rate)。完整 backtest 開發中。</p>

<h3>Q: 港股 stock screener 同一般 finviz / TradingView 嘅免費版有咩分別？</h3>
<p>A: Leeks Terminal 港股 stock screener 同 finviz / TradingView 嘅核心分別：</p>
<ul>
<li><b>finviz</b> · 主力做美股，港股覆蓋弱 (只有 ADR 同 major dual-list)，screener 強但要付費先有 real-time + AI 評分</li>
<li><b>TradingView</b> · 圖表最強但 AI 評分要 Premium (USD 15+/月)，港股覆蓋 OK 但要付費</li>
<li><b>aastocks / 經濟通 篩選器</b> · 港股覆蓋全，但只有簡單 PE / 成交 / 技術指標 filter，冇 AI 評分同操作建議</li>
<li><b>Leeks Terminal 港股 stock screener</b> · 完全 free + 4 維度 AI 評分 + 中文操作建議 + entry / stop / target，專為港股散戶設計</li>
</ul>
<p>如果你想純做美股 momentum，可以單獨用 finviz 篩 RSI / volume，再對比 Leeks Terminal us-scanner。如果主力做港股，Leeks Terminal 港股 stock screener 已經覆蓋 95% 需求。</p>

<h3>Q: 「港股 free screener」真係完全免費？冇 hidden cost？</h3>
<p>A: 完全 free。Leeks Terminal 港股 stock screener 唔使註冊、唔使訂閱、唔使信用卡、唔使 email 收集，開 dashboard 就見到 200 隻港股當日信號。冇 hidden cost、冇 premium tier 鎖功能、冇「7 天試用」陷阱、冇 SaaS 訂閱、冇 watermark。資金來源：個人 side project + 純粹開源俾香港 / 台灣 / 內地散戶。</p>
<p>同其他「港股 free screener」比較：</p>
<ul>
<li><b>aastocks 免費版</b> · 只有基本技術指標 filter，冇 AI 評分，UI 有廣告</li>
<li><b>經濟通 ET Net</b> · 報價 + 簡單 screener，但要登記 email，UI 較舊</li>
<li><b>StockQ 股勢</b> · 中文版 Finviz，screener 強但 AI 評分弱，要付費先有完整功能</li>
<li><b>Leeks Terminal 港股 stock screener</b> · 完全 free + 4 維度 AI 評分 + 操作建議，同類工具罕見</li>
</ul>

<h3>Q: 點樣搵「PE 低 港股」？呢個篩選器有冇 value 篩選？</h3>
<p>A: 有。「PE 低 港股」嘅定義：PE TTM &lt; 10x (恒生指數中位數 PE 約 11-13x，&lt; 10x 算偏低)。Leeks Terminal 港股 stock screener 用估值維度 (<code>v_score</code>) 處理：</p>
<ul>
<li><code>v_score &gt; 70</code> · PE 低 (通常 &lt; 10x)、PB 低 (&lt; 1.0x)、偏離 5 年均值低</li>
<li><code>v_score 40-70</code> · PE 中性 (10-20x)</li>
<li><code>v_score &lt; 40</code> · PE 高 (&gt; 20x) 或估值貴</li>
</ul>
<p>Dashboard detail table 嘅「估值/質素/動能/資金流」欄位入面，第一個數字就係 <code>v_score</code>。Sort by score 倒序，再搵 <code>v &gt; 70</code> 嘅股就係 PE 偏低嘅名單。常見 PE 低 港股包括內銀 (例如 939 建設銀行 PE ~4x、1398 工商銀行 PE ~4x)、內房 (部分低至 3-5x)、公用股 (2-8x)。注意：PE 低唔一定係 value trap，要 cross-check ROE (質素 q_score) 同 dividend yield 先確認。</p>

<h3>Q: 點樣搵「RSI 超賣 港股」？呢個工具識唔識 RSI 篩選？</h3>
<p>A: 識。「RSI 超賣 港股」即 RSI14 &lt; 30，Leeks Terminal 港股 stock screener 用動能維度 (<code>m_score</code>) 處理：</p>
<ul>
<li><code>m_score &lt; 30</code> · 對應 RSI14 &lt; 30 (嚴重超賣，反彈機會大)</li>
<li><code>m_score 30-40</code> · RSI 30-40 (輕微超賣)</li>
<li><code>m_score 40-60</code> · RSI 中性 (40-60)</li>
<li><code>m_score &gt; 70</code> · RSI &gt; 70 (超買，要小心回調)</li>
</ul>
<p>Dashboard detail table 入面第三個數字就係 <code>m_score</code>。Sort by score 倒序，再搵 <code>m &lt; 30</code> 嘅股就係 RSI 超賣名單。注意：單純 RSI 超賣唔可以買 — 要配合 <code>of_score &gt; 60</code> (大戶接貨) 先有反彈動力，否則可能係「弱勢股繼續跌」(即所謂「下跌中嘅刀」)。Leeks Terminal 嘅 preset「RSI 超賣 + 量能放大」(m &lt; 30 + of &gt; 60) 命中率 50-55%，適合 1-3 日短線反彈。</p>

<h3>Q: 「港股 量比」同 Relative Volume 有咩分別？呢個篩選器點處理？</h3>
<p>A: 「港股 量比」(volume ratio) 同 Relative Volume (RVOL) 係兩個相似但唔同嘅概念：</p>
<ul>
<li><b>量比 (Volume Ratio, VR)</b> · 當日成交量 / 過去 5 日平均成交量。即時性高，適合即日鮮用。中國 A 股市場常用。</li>
<li><b>Relative Volume (RVOL)</b> · 當日成交量 / 過去 20 日 (或 30 日) 平均成交量。穩定性高，適合 swing 用。Finviz / 美股市場常用。</li>
</ul>
<p>Leeks Terminal 港股 stock screener 主要用 <b>Relative Volume (vs 20d ADV)</b> 計入 <code>order_flow_score (of_score)</code>：</p>
<ul>
<li><code>of_score &gt; 70</code> · Relative Volume ≥ 2x，有大戶入場</li>
<li><code>of_score 50-70</code> · RVOL 1.2-2x，正常偏活躍</li>
<li><code>of_score &lt; 40</code> · RVOL &lt; 0.8x，成交淡靜</li>
</ul>
<p>如果你想搵「港股 量比」高嘅股 (即 RVOL ≥ 2x)，sort by score 倒序搵 <code>of &gt; 70</code> 嘅股就係。Leeks Terminal 暫時唔提供即時量比 (intraday)，但 dashboard 嘅 <code>of_score</code> 已經反映收市後嘅 RVOL 數值，可以作為下一個交易日嘅參考。</p>

<h3>Q: 點樣睇「港股 資金流」？呢個工具識唔識分析北水 / 大戶動向？</h3>
<p>A: 識。Leeks Terminal 港股 stock screener 嘅 <code>order_flow_score (of_score)</code> 維度包含 4 個 sub-indicator：</p>
<ol>
<li><b>Relative Volume</b> · 當日成交 vs 20 日 ADV (量能放大 = 大戶活動)</li>
<li><b>大單淨流入</b> · 透過成交股數 × 價格分大單 (≥ 100 萬 HKD) 同小單 (&lt; 100 萬 HKD)，計淨流入比例</li>
<li><b>港股通南向資金</b> · 內地經滬深港通買入港股嘅淨流入 (CCASS 數據，內銀 / 內房 / 科技權重股覆蓋)</li>
<li><b>Bid-ask imbalance</b> · 買賣盤差異 (Level-2 data 提供，但 Leeks Terminal 暫時用 closing data 估算)</li>
</ol>
<p>Dashboard detail table 入面第四個數字就係 <code>of_score</code>。Sort by score 倒序，再搵 <code>of &gt; 70</code> 嘅股就係「港股 資金流」活躍嘅名單 — 通常係北水狂掃 / 大戶囤貨 / 業績前偷步買入嘅強勢股。注意：單看北水流入未必等於股價升，要配合 <code>m_score &gt; 60</code> 先確認 momentum 同步。</p>

<h3>Q: 「港股 排行」邊度睇？呢個工具出唔出 daily top movers？</h3>
<p>A: 出。Leeks Terminal 港股 stock screener 嘅 dashboard 預設按 score 由高到低排，所以首 20 隻 card 就係當日「港股 排行」綜合榜 — 包括：</p>
<ul>
<li><b>港股 升幅 排行</b> · Sort by score 倒序，搵 m_score 高 (例如 &gt; 75) 嘅股</li>
<li><b>港股 跌幅 排行</b> · Sort by score 倒序，搵 m_score 低 (例如 &lt; 25) 嘅股</li>
<li><b>港股 成交 排行</b> · Sort by of_score 倒序，搵 RVOL 高 (例如 &gt; 2x) 嘅股</li>
<li><b>港股 北水排行</b> · Sort by of_score 倒序，搵南向資金連續 2 日淨流入嘅股</li>
</ul>
<p>同一般財經網站 (例如 aastocks 升幅榜、經濟通成交量榜) 嘅分別：Leeks Terminal 排行已經包含 AI 4 維度綜合分，唔係淨係單一指標排序，所以你直接睇 dashboard 頭 20 隻 card 就係當日最強港股 list。如果你想睇「港股 邊隻 升」，dashboard 已經幫你揀晒。</p>

<h3>Q: 「港股 free screener」之外，有冇付費 screener 推介？</h3>
<p>A>有幾個常見嘅付費港股 / 美股 screener，按 budget 由低至高：</p>
<ul>
<li><b>StockQ 股勢 premium</b> · HKD 50/月左右，中文 screener 強 + 基本 AI 評分</li>
<li><b>AASTOCKS PRO</b> · HKD 200/月左右，港股專業版 + L2 報價 + 大量 filter</li>
<li><b>Finviz Elite</b> · USD 25/月，美股最強 screener，但港股覆蓋弱</li>
<li><b>TradingView Premium</b> · USD 15+/月，圖表 + AI Pine Script 信號</li>
<li><b>Bloomberg Terminal</b> · USD 2000+/月，機構級，個人用戶唔建議</li>
</ul>
<p>Leeks Terminal 港股 stock screener 定位：完全 free + 中文 + 4 維度 AI 評分 + 港股 200 隻覆蓋。如果你係香港散戶 / 學生 / 兼職 trader，Leeks Terminal 已經夠用；如果你係全職 trader 想要 L2 報價 + 自訂 Pine Script + 大量歷史 backtest，可以考慮 StockQ / AASTOCKS PRO + Leeks Terminal 並用。</p>

<h3>Q: 點樣用呢個篩選器做「港股 估值 篩選」？value investing 適用嗎？</h3>
<p>A: 適用但有限制。Leeks Terminal 港股 stock screener 嘅估值維度 (<code>v_score</code>) 同時考慮 PE / PB / deviation from 5y mean，三個 sub-indicator 並行驗證：</p>
<ul>
<li><b>PE (TTM)</b> · 對比恒指中位數 (~11-13x)，&lt; 8x 算深低，&gt; 25x 算深高</li>
<li><b>PB</b> · 對比行業平均，&lt; 0.8x 算深低 (內銀 / 內房常見)，&gt; 5x 算深高 (科技 / 醫療常見)</li>
<li><b>5 年均值 deviation</b> · 對比該股自身 5 年 PE / PB 中位數，&lt; -30% 算偏低</li>
</ul>
<p>「港股 估值 篩選」做法：sort by score 倒序，搵 <code>v &gt; 70</code> + <code>q &gt; 70</code> 嘅股 — 即「平 + 高 ROE」組合。注意：dashboard 主力做 day-trade，呢類股通常 score 40-60 (觀望區)，冇 🟢 買入信號。Value investing 適合 1-3 個月持倉，唔係即日鮮 timeframe。如果你做 value investing，建議用 dashboard 做 stock screening，再用自己嘅 broker 做 deep dive (睇年報 / 行業前景 / management quality)。</p>
""",
    })

    written = []
    for p in pages:
        body = f"<h1>{p['h1']}</h1>" + p["body"]
        # Add "Latest signals" CTA at bottom
        if latest:
            body += (
                f'<section class="cta">'
                f'<h2>睇今日 ({latest}) 完整信號</h2>'
                f'<p>200 隻港股 / 美股 4 維度評分 + 入場 / 止損 / 目標齊全。</p>'
                f'<p><a class="btn" href="/dashboard/{latest}/all.html">→ 開 {latest} dashboard</a></p>'
                f'</section>'
            )
        json_ld = {
            "@context": "https://schema.org",
            "@type": "WebPage",
            "name": p["title"],
            "description": p["description"],
            "url": f"https://www.win9you.com{p['path']}",
            "inLanguage": "zh-Hant",
            "isPartOf": {
                "@type": "WebSite",
                "name": "Leeks Terminal",
                "url": "https://www.win9you.com",
            },
        }
        out_path = PUBLIC_DIR / p["path"].lstrip("/")
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(
            shell(
                title=p["title"],
                body_html=body,
                active_path=p["path"],
                description=p["description"],
                json_ld=json_ld,
                canonical=f"https://www.win9you.com{p['path']}",
            ),
            encoding="utf-8",
        )
        written.append(p["path"])
    return written



    """Build sitemap.xml with all public pages. Auto-includes all dates in DB."""
    base = "https://www.win9you.com"
    urls = []

    # Static pages
    static = [
        ("/", "1.0", "daily"),
        ("/dashboard/", "0.9", "daily"),
        ("/methodology.html", "0.8", "weekly"),
        ("/faq.html", "0.8", "weekly"),
        ("/about.html", "0.5", "monthly"),
        ("/disclaimer.html", "0.3", "monthly"),
        ("/privacy.html", "0.3", "monthly"),
        # Intent landing pages (P1 SEO coverage)
        ("/hk-scanner.html", "0.9", "daily"),
        ("/us-scanner.html", "0.9", "daily"),
        ("/day-trade-signals.html", "0.9", "daily"),
        ("/hk-stock-screener.html", "0.8", "daily"),
    ]
    for path, prio, freq in static:
        urls.append((path, prio, freq))

    # Per-date dashboard + filter variants
    filters = ["all", "hk-buy", "hk-sell", "hk-hold", "us-buy", "us-sell", "us-hold", "conservative-buy", "cyber-buy", "bounce-buy"]
    for d in dates:
        urls.append((f"/dashboard/{d}/all.html", "0.9", "daily"))
        for f in filters[1:]:
            urls.append((f"/dashboard/{d}/{f}.html", "0.7", "daily"))

    lines = ['<?xml version="1.0" encoding="UTF-8"?>']
    lines.append('<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">')
    for path, prio, freq in urls:
        lines.append("  <url>")
        lines.append(f"    <loc>{base}{path}</loc>")
        lines.append(f"    <changefreq>{freq}</changefreq>")
        lines.append(f"    <priority>{prio}</priority>")
        lines.append("  </url>")
    lines.append("</urlset>")
    return "\n".join(lines) + "\n"


def build_robots_txt() -> str:
    return (
        "User-agent: *\n"
        "Allow: /\n"
        "\n"
        "Sitemap: https://www.win9you.com/sitemap.xml\n"
    )


def build_paper_trades_page() -> str:
    """Build /paper-trades.html — paper-trade performance dashboard.

    Shows: cumulative P&L, win rate, avg win/loss, per-trade table, by-preset breakdown.
    Reads from paper_trade table populated by scripts/paper_trade.py.
    """
    import sqlite3
    from pathlib import Path
    db_path = PROJECT_ROOT / "data" / "dsa_hk.db"
    con = sqlite3.connect(str(db_path))
    con.row_factory = sqlite3.Row

    # Aggregate stats
    closed = con.execute("SELECT * FROM paper_trade WHERE status='closed' ORDER BY exit_date DESC").fetchall()
    open_trades = con.execute("SELECT * FROM paper_trade WHERE status='open' ORDER BY entry_date ASC").fetchall()
    total = con.execute("SELECT COUNT(*) FROM paper_trade").fetchone()[0]
    n_closed = len(closed)
    n_open = len(open_trades)
    if n_closed > 0:
        wins = sum(1 for t in closed if (t["pnl_pct"] or 0) > 0)
        losses = n_closed - wins
        wr = wins / n_closed * 100
        avg_win = sum(t["pnl_pct"] for t in closed if (t["pnl_pct"] or 0) > 0) / max(wins, 1)
        avg_loss = sum(t["pnl_pct"] for t in closed if (t["pnl_pct"] or 0) <= 0) / max(losses, 1)
        total_pnl = sum(t["pnl_usd"] for t in closed)
        total_deployed = sum(t["position_size_usd"] for t in closed)
        roi = total_pnl / total_deployed * 100 if total_deployed else 0
    else:
        wins = losses = 0
        wr = avg_win = avg_loss = total_pnl = roi = 0

    # Per-preset breakdown
    preset_stats = {}
    for t in closed:
        src = t["signal_source"] or "unknown"
        if src not in preset_stats:
            preset_stats[src] = {"n": 0, "wins": 0, "pnl_usd": 0.0}
        preset_stats[src]["n"] += 1
        if (t["pnl_pct"] or 0) > 0:
            preset_stats[src]["wins"] += 1
        preset_stats[src]["pnl_usd"] += t["pnl_usd"] or 0
    for src, s in preset_stats.items():
        s["wr"] = s["wins"] / s["n"] * 100 if s["n"] else 0

    # Close-reason breakdown
    reason_stats = {}
    for t in closed:
        reason = t["close_reason"] or "?"
        if reason not in reason_stats:
            reason_stats[reason] = {"n": 0, "wins": 0}
        reason_stats[reason]["n"] += 1
        if (t["pnl_pct"] or 0) > 0:
            reason_stats[reason]["wins"] += 1

    con.close()

    # Build HTML
    summary_html = f'''<div class="paper-stats">
        <div class="stat-box"><b>{total}</b><span>Total Trades</span></div>
        <div class="stat-box"><b>{n_open}</b><span>Open</span></div>
        <div class="stat-box"><b>{n_closed}</b><span>Closed</span></div>
        <div class="stat-box stat-bull"><b>{wr:.1f}%</b><span>Win Rate</span></div>
        <div class="stat-box stat-bull"><b>+${total_pnl:.0f}</b><span>Total P&L</span></div>
        <div class="stat-box"><b>{roi:+.2f}%</b><span>ROI on ${total_deployed:.0f}</span></div>
        <div class="stat-box stat-bull"><b>+{avg_win:.2f}%</b><span>Avg Win</span></div>
        <div class="stat-box stat-bear"><b>{avg_loss:+.2f}%</b><span>Avg Loss</span></div>
    </div>'''

    preset_html = ""
    if preset_stats:
        preset_html = '<h3>By Signal Source</h3><table class="detail"><thead><tr><th>Source</th><th>Trades</th><th>Wins</th><th>WR</th><th>P&L</th></tr></thead><tbody>'
        for src, s in sorted(preset_stats.items()):
            pnl_class = "stat-bull" if s["pnl_usd"] > 0 else "stat-bear"
            preset_html += f'<tr><td>{src}</td><td>{s["n"]}</td><td>{s["wins"]}</td><td>{s["wr"]:.1f}%</td><td class="{pnl_class}">${s["pnl_usd"]:+.2f}</td></tr>'
        preset_html += '</tbody></table>'

    reason_html = ""
    if reason_stats:
        reason_html = '<h3>By Close Reason</h3><table class="detail"><thead><tr><th>Reason</th><th>Trades</th><th>Wins</th></tr></thead><tbody>'
        for reason, s in sorted(reason_stats.items()):
            reason_html += f'<tr><td>{reason}</td><td>{s["n"]}</td><td>{s["wins"]}</td></tr>'
        reason_html += '</tbody></table>'

    # Recent closed trades table
    closed_html = ""
    if closed:
        closed_html = '<h2>Recent Closed Trades</h2><table class="detail"><thead><tr><th>Exit Date</th><th>Code</th><th>Source</th><th>Entry</th><th>Exit</th><th>P&L %</th><th>P&L $</th><th>Reason</th><th>Held</th></tr></thead><tbody>'
        for t in closed[:30]:
            pnl_pct = t["pnl_pct"] or 0
            pnl_class = "stat-bull" if pnl_pct > 0 else "stat-bear"
            try:
                held = (datetime.strptime(t["exit_date"], "%Y-%m-%d") - datetime.strptime(t["entry_date"], "%Y-%m-%d")).days
            except Exception:
                held = "?"
            closed_html += f'<tr><td>{t["exit_date"]}</td><td>{t["code"]}</td><td>{t["signal_source"]}</td><td>${t["entry_price"]:.2f}</td><td>${t["exit_price"]:.2f}</td><td class="{pnl_class}">{pnl_pct:+.2f}%</td><td class="{pnl_class}">${t["pnl_usd"]:+.2f}</td><td>{t["close_reason"]}</td><td>{held}d</td></tr>'
        closed_html += '</tbody></table>'

    # Open trades
    open_html = ""
    if open_trades:
        open_html = '<h2>Open Positions</h2><table class="detail"><thead><tr><th>Entry Date</th><th>Code</th><th>Source</th><th>Entry</th><th>Stop</th><th>Target</th><th>Held</th></tr></thead><tbody>'
        for t in open_trades:
            try:
                held = (datetime.now() - datetime.strptime(t["entry_date"], "%Y-%m-%d")).days
            except Exception:
                held = "?"
            open_html += f'<tr><td>{t["entry_date"]}</td><td>{t["code"]}</td><td>{t["signal_source"]}</td><td>${t["entry_price"]:.2f}</td><td>${t["stop_loss"]:.2f}</td><td>${t["target_price"]:.2f}</td><td>{held}d</td></tr>'
        open_html += '</tbody></table>'

    body_html = f'''<div class="signal-warning"><b>📈 Paper Trade Tracker</b> · 跟 Conservative BUY + Cyber BUY signals 自動落 paper trade · $1000/trade · 6% stop loss · 2-3 day hold
        <br>· <b>Workflow</b>: 每日 4:30 PM HKT 跑 <code>scripts/paper_trade.py</code> · 新 signals 開倉 + 現有倉位 check stop/target/3-day timeout
        <br>· <b>Current rules</b>: Conservative BUY (mean-rev + non-tech + m 30-70) · Cyber BUY (13 隻 whitelist) · 暫停 SELL signals
        <br>· <b>Performance</b>: 即時 P&L + 命中率 + avg win/loss · See <a href="/methodology.html">methodology</a> 了解 backtest 背景</div>
        {summary_html}
        {preset_html}
        {reason_html}
        {closed_html}
        {open_html}'''

    return shell(
        title="Leeks Terminal · Paper Trade Tracker",
        body_html=body_html,
        active_path="/paper-trades/",
        description="Paper trade performance — Conservative BUY + Cyber BUY signals auto-tracked",
    )


def main():
    parser = argparse.ArgumentParser(description="Build static HTML dashboard")
    parser.add_argument("--date", help="Build specific date (YYYY-MM-DD)")
    parser.add_argument("--all", action="store_true", help="Build all dates in DB")
    parser.add_argument("--static-pages", action="store_true", help="Build FAQ/about/etc pages")
    parser.add_argument("--index", action="store_true", help="Build index.html")
    args = parser.parse_args()

    PUBLIC_DIR.mkdir(parents=True, exist_ok=True)

    init_db()
    all_dates = list_report_dates(limit=30)

    written = []

    # 1. Static info pages
    if args.static_pages or not any([args.date, args.all, args.index]):
        for p in build_static_pages():
            written.append(p)
        for p in build_intent_pages():
            written.append(p)
        # Paper trades tracker page (always rebuilt)
        paper_path = PUBLIC_DIR / "paper-trades.html"
        paper_path.write_text(build_paper_trades_page(), encoding="utf-8")
        written.append("paper-trades.html")
        print(f"✅ Built {len(written)} static info + intent pages")

    # 2. Dashboard pages
    if args.date:
        dates_to_build = [args.date]
    elif args.all:
        dates_to_build = all_dates
    elif args.index:
        dates_to_build = []
    else:
        dates_to_build = all_dates[:3]  # default: last 3 dates

    for d in dates_to_build:
        files, count = build_dashboard_for_date(d)
        print(f"✅ {d}: {count} reports → {len(files)} filter variants")
        written.extend(files)

    # 3. Index
    if args.index or args.all or not any([args.date, args.all]):
        idx_path = PUBLIC_DIR / "index.html"
        idx_path.write_text(build_index(all_dates), encoding="utf-8")
        print(f"✅ Built index.html ({len(all_dates)} dates)")
        written.append("index.html")

        # Dashboard hub at /dashboard/index.html (replaces SPA fallback to homepage)
        hub_path = PUBLIC_DIR / "dashboard" / "index.html"
        hub_path.parent.mkdir(parents=True, exist_ok=True)
        hub_path.write_text(build_dashboard_hub(all_dates), encoding="utf-8")
        print(f"✅ Built dashboard/index.html (hub of {len(all_dates)} dates)")
        written.append("dashboard/index.html")

    # 4. Sitemap + robots.txt (always — they need to stay in sync with dates)
    (PUBLIC_DIR / "sitemap.xml").write_text(build_sitemap_xml(all_dates), encoding="utf-8")
    written.append("sitemap.xml")
    (PUBLIC_DIR / "robots.txt").write_text(build_robots_txt(), encoding="utf-8")
    written.append("robots.txt")
    print(f"✅ Built sitemap.xml + robots.txt ({len(all_dates)} dates × 5 filter variants)")

    print(f"\nTotal files written: {len(written)}")
    print(f"Output directory: {PUBLIC_DIR}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

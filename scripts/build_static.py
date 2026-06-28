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
# =====
FILTER_PRESETS = [
    # (slug, label_zh, market, operation)
    ("all",     "全部",      None, None),
    ("hk-buy",  "港股買入",  "HK", "buy"),
    ("hk-sell", "港股賣出",  "HK", "sell"),
    ("us-buy",  "美股買入",  "US", "buy"),
    ("us-sell", "美股賣出",  "US", "sell"),
]


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
          description: str = "", json_ld: dict | None = None) -> str:
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
  <meta property="og:type" content="website">
  <meta property="og:url" content="https://www.win9you.com">
  <meta name="twitter:card" content="summary">
  <link rel="canonical" href="https://www.win9you.com">
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
        "<th>代碼</th><th>評分</th><th>方向</th><th>估值/質素/動能</th>"
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
    operation = report.get("operation_advice") or "—"
    sentiment = report.get("sentiment") or "—"
    trend = report.get("trend") or "—"
    confidence = report.get("confidence") or "—"

    summary_md = report.get("summary_md") or ""
    full_md = report.get("full_md") or ""

    breakdown = report.get("score_breakdown") or {}
    if isinstance(breakdown, str):
        try: breakdown = json.loads(breakdown)
        except Exception: breakdown = {}

    # Use full_md if available, fall back to summary_md
    main_md = full_md if full_md else summary_md

    # Back link to all.html
    back = f'<p><a href="/dashboard/{date}/all.html">← 返回 {date} 全部報告</a></p>'

    # Score breakdown bar
    v = breakdown.get("value_score", 0) or 0
    q = breakdown.get("quality_score", 0) or 0
    m = breakdown.get("momentum_score", 0) or 0
    breakdown_html = (
        '<div class="score-breakdown">'
        f'<div class="dim"><span>估值</span><div class="bar"><div class="fill" style="width:{v}%;background:var(--blue);"></div></div><b>{v}</b></div>'
        f'<div class="dim"><span>質素</span><div class="bar"><div class="fill" style="width:{q}%;background:var(--purple);"></div></div><b>{q}</b></div>'
        f'<div class="dim"><span>動能</span><div class="bar"><div class="fill" style="width:{m}%;background:var(--amber);"></div></div><b>{m}</b></div>'
        '</div>'
    )

    # Key levels
    support = report.get("support_zone") or "—"
    resistance = report.get("resistance_zone") or "—"
    entry = report.get("entry_zone") or "—"
    stop = report.get("stop_loss") or "—"
    target = report.get("target_price") or "—"

    levels_html = (
        '<div class="levels">'
        f'<div><span>入場區間</span><b>{_html.escape(entry)}</b></div>'
        f'<div><span>止損</span><b class="bear">{_html.escape(stop)}</b></div>'
        f'<div><span>目標</span><b class="bull">{_html.escape(target)}</b></div>'
        f'<div><span>支持位</span><b>{_html.escape(support)}</b></div>'
        f'<div><span>阻力位</span><b>{_html.escape(resistance)}</b></div>'
        '</div>'
    )

    # Main markdown body
    body_md_html = body_md_to_html(main_md)

    body = (
        back
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
        body_md = build_dashboard_md(
            report_date=date,
            market=mkt,
            operation=op,
        )
        # Re-render the cards so they use our static .card class instead of inline styles
        # — easier to style + a11y. We do a simple post-process: wrap any <div style=...> from build_dashboard_md
        # into <div class="card">. Simpler: just use the build_dashboard_md HTML as-is (inline styles work),
        # then append the filter chips + detail table.
        body_html = (
            disclaimer_block()
            + filter_chips_html(date, slug)
            + f'<h1>📊 決策儀表板 — {date} ({label})</h1>'
            + body_md_to_html(body_md, link_inject_date=date)
        )

        # Add detail table — re-apply filters manually (already in scope from loop)
        filtered = all_reports
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
        report_path = reports_dir / f"{code}.html"
        report_path.write_text(
            report_page_html(r, date),
            encoding="utf-8",
        )
        written.append(str(report_path.relative_to(PUBLIC_DIR)))

    return written, len(all_reports)


def body_md_to_html(md: str, link_inject_date: str | None = None) -> str:
    """Convert the build_dashboard_md markdown output to HTML for static pages.
    The output already contains raw HTML <div style=...> for cards (preserved).
    If link_inject_date is set, append a '→ 完整 ... 詳細報告' link inside each card."""
    import re
    # Card pattern: <div style="...">CARD_CONTENT</div>
    # CARD_CONTENT is single-line text with **KO** or **00700.HK** bold code prefix.
    # Replace each card div to use class="card" + append a '→ 詳細報告' link inside.
    card_re = re.compile(
        r'(<div\s+style="[^"]*">)(.+?)(</div>)',
        flags=re.DOTALL,
    )

    def _rewrite_card(match: re.Match) -> str:
        open_tag, body, close_tag = match.group(1), match.group(2), match.group(3)
        # Convert inline style to class
        open_tag_new = '<div class="card">'

        # Capture code from body
        code = None
        m_hk = re.search(r'\b(\d{4,5}\.HK)\b', body)
        m_us = re.search(r'\*\*([A-Z][A-Z0-9.]{0,5})\*\*', body)
        if m_hk:
            code = m_hk.group(1)
        elif m_us:
            code = m_us.group(1)

        # Markdown-ish transforms inside the card body
        body = re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', body)
        body = re.sub(r'(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)', r'<em>\1</em>', body)
        body = re.sub(r'🟢', '<span style="color:var(--bull);font-weight:600;">🟢</span>', body)
        body = re.sub(r'🟡', '<span style="color:var(--amber);font-weight:600;">🟡</span>', body)
        body = re.sub(r'🔴', '<span style="color:var(--bear);font-weight:600;">🔴</span>', body)

        link_html = ''
        if code and link_inject_date:
            link_html = (
                f'<p class="card-link">'
                f'<a href="/dashboard/{link_inject_date}/reports/{code}.html">'
                f'→ 完整 {code} 詳細報告</a></p>'
            )
        return open_tag_new + body + link_html + close_tag

    # First rewrite cards (block-aware)
    md = card_re.sub(_rewrite_card, md)

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
        # Emoji color spans outside cards too
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
        html_parts.append(line)
    return "\n".join(html_parts)


def build_index(dates: list[str]) -> str:
    """Build public/index.html — landing page with date picker."""
    if not dates:
        body = (
            "<h1>◆ Leeks Terminal</h1>"
            '<p class="stats">暫時未有分析報告。請等今日 pipeline 跑完。</p>'
        )
    else:
        # Build date picker
        picker = date_picker_html(dates, "")
        latest = dates[0]
        body = (
            "<h1>◆ Leeks Terminal</h1>"
            "<p>HK + US Day-Trade AI · 200 隻港股 + 200 隻美股 · 每日兩次分析</p>"
            "<h2>選擇報告日期</h2>"
            + picker
            + f'<p>最新報告：<a href="/dashboard/{latest}/all.html">{latest} →</a></p>'
        )

    return shell(
        title="Leeks Terminal · HK+US Day-Trade AI",
        body_html=body,
        active_path="/",
        description="Real-time HK + US day-trade AI dashboard. 200 tickers × multi-dim scoring + trade direction signals.",
        json_ld={
            "@context": "https://schema.org",
            "@type": "WebApplication",
            "name": "Leeks Terminal",
            "url": "https://www.win9you.com",
            "applicationCategory": "FinanceApplication",
            "operatingSystem": "Any (web browser)",
            "offers": {"@type": "Offer", "price": "0", "priceCurrency": "USD"},
            "description": "Real-time HK + US day-trade AI dashboard with multi-dim scoring.",
        },
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
<p>A: 三維評分 (0–100)：<b>估值</b> (PE/PB，25%) + <b>質素</b> (ROE/margin，25%) + <b>動能</b> (MA/RSI，50%)。
day-trade bias 落動能，所以動能分高嘅 score 自然高。</p>

<h2>Q: trade_direction 點解有時「雙向」？</h2>
<p>A: 「雙向」代表波動率足夠，long 同 short setup 都有，用戶自己揀邊個方向做。
filter 可以 hide 其他方向。</p>
"""
        elif slug == "about":
            body += """
<p>Leeks Terminal 係我自己寫嚟用嘅 HK + US day-trade dashboard。香港散戶，side gig 玩 MC。</p>
<p>200 隻港股 + 200 隻美股，每日 2 次 (HK 開市前 + US 開市前) 用 MiniMax-M3 評分，輸出 Value / Quality / Momentum 三維分數 + 入場區間 / 止損 / 目標。</p>
<p>全部資料 free：Futu Cloud news (news)、Tencent gtimg (live HK 報價，sub-1min delay)、YFinance (US/EOD)。</p>
<p>Source code: <a href="https://github.com/kenkenlui-ctrl/Trading">GitHub</a> · Built with Python + Streamlit + Cloudflare.</p>
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
  <li>Prompt MiniMax-M3 輸出 20 個 fields (score, sentiment, trade_direction, entry_zone, stop_loss, target_price, support_zone, resistance_zone, summary_md, full_md)</li>
  <li>寫入 SQLite + commit 入 git</li>
  <li>Cloudflare Pages 自動 re-deploy static dashboard</li>
</ol>

<h2>評分模型</h2>
<p>三維 weighted score：value × 0.25 + quality × 0.25 + momentum × 0.50。<br>
day-trade 偏重動能，所以 score 高通常代表趨勢 + 動量 + 估值合理 嘅 combination。</p>

<h2>操作建議</h2>
<ul>
  <li><b>🟢 買入</b>: score ≥ 70 + bullish trade_direction + positive sentiment</li>
  <li><b>🔴 賣出</b>: score ≤ 40 + bearish trade_direction + negative news flow</li>
  <li><b>🟡 觀望</b>: 其他 (default ~90% 股票)</li>
</ul>
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
        print(f"✅ Built {len(written)} static info pages")

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
    if args.index or not any([args.date, args.all]):
        idx_path = PUBLIC_DIR / "index.html"
        idx_path.write_text(build_index(all_dates), encoding="utf-8")
        print(f"✅ Built index.html ({len(all_dates)} dates)")
        written.append("index.html")

    print(f"\nTotal files written: {len(written)}")
    print(f"Output directory: {PUBLIC_DIR}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

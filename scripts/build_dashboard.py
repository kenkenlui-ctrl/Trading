#!/usr/bin/env python3
"""
build_dashboard.py — Build the action plan dashboards and per-ticker detail pages
for win9you.com (HK + US).

Reads:
  - /Users/kenken/dev/dsa-hk/charts/hk200/  (per-ticker PNGs + JSON from daily-sr-chart)
  - /Users/kenken/dev/dsa-hk/data/backtest_out/                              (per-ticker backtest JSON from backtest_futu.py)
  - hk_universe_200.json                            (universe list)

Writes:
  - /Users/kenken/dev/dsa-hk/public/hk200/index.html
  - /Users/kenken/dev/dsa-hk/public/hk200/ticker/<safe>.html
  - /Users/kenken/dev/dsa-hk/public/us200/index.html
  - /Users/kenken/dev/dsa-hk/public/us200/ticker/<safe>.html
  - /Users/kenken/dev/dsa-hk/public/index.html (home)
"""
from __future__ import annotations
import json
import os
import shutil
from datetime import datetime, date, timedelta
from pathlib import Path
import sys
from functools import lru_cache
from mobile_nav import mobile_bottom_nav, render_v2_block, render_v2_status_for_ticker


def t_minus_1() -> date:
    """Return T-1 = last trading day (skip Sat/Sun).

    2026-10-02: LEAKS_ASOF overrides the computed date. Without it a rebuild
    on a later day stamps the header with a date the data does not cover —
    e.g. building on 2026-10-02 against 2026-09-29 bars produced a page that
    claimed "T-1 09-30" while every number was 09-29. Set LEAKS_ASOF to the
    date the bars actually end on when rebuilding out of cycle.
    """
    override = os.environ.get("LEAKS_ASOF", "").strip()
    if override:
        try:
            return date.fromisoformat(override)
        except ValueError:
            print(f"! LEAKS_ASOF={override!r} is not YYYY-MM-DD, ignoring")
    today = date.today()
    if today.weekday() == 0:  # Monday
        return today - timedelta(days=3)  # Friday
    elif today.weekday() == 6:  # Sunday
        return today - timedelta(days=2)  # Friday
    elif today.weekday() == 5:  # Saturday
        return today - timedelta(days=1)  # Friday
    else:
        return today - timedelta(days=1)

def _canvas_js_version() -> str:
    """Cache-buster for /static/canvas-chart.js.

    This was hardcoded to "2026-09-18b", so every JS change since then shipped
    invisibly: the file on the server was new, the URL never changed, and
    browsers kept the September copy. A chart fix that was verified server-side
    (grep proved the new code was deployed) never reached a single visitor.
    Hash the actual file so the URL changes exactly when the code does.
    """
    import hashlib
    p = Path(__file__).resolve().parents[1] / "public" / "static" / "canvas-chart.js"
    try:
        return hashlib.sha1(p.read_bytes()).hexdigest()[:8]
    except Exception:
        return "0"


@lru_cache(maxsize=8)
def data_asof(market: str) -> str:
    """T-1 for a market, derived from that market's own bars.

    2026-10-02: JP traded 2026-10-01 while HK and US last closed 2026-09-30,
    so a single global t_minus_1() would stamp the wrong date on two of the
    three hubs. Deriving the label from the data cannot drift from the data —
    the failure mode that produced "T-1 09-30" over 09-29 numbers.

    Uses the modal (most common) last_bar date so a handful of suspended or
    data-starved tickers cannot drag the label off the real session.

    Cached per process: the answer is a property of the snapshot JSONs, which
    no builder rewrites. build_static restamp_t1_badges() asks for a label once
    per published page (648 of them), and each miss re-parsed every JSON in
    the market directory — half a million json.load calls for a value that
    cannot change mid-build.
    """
    from collections import Counter
    src = {"HK": HK_OUT, "US": US_OUT, "JP": REPO / "data/jp200"}.get(market)
    if not src or not Path(src).is_dir():
        return t_minus_1().isoformat()
    c = Counter()
    for p in Path(src).glob("*.json"):
        if p.name.endswith("_ohlc.json"):
            continue
        try:
            d = json.load(open(p))
            # 2026-10-05: HK/US snapshots carry last_bar.date, but JP snapshots
            # written by fetch_jp_charts.py carry a flat `asof` string. Reading
            # only last_bar meant JP always fell through to the calendar
            # fallback, so the JP T-1 label was a guess, not the data — the one
            # gap left in the single-source-of-truth fix.
            dt = (d.get("last_bar") or {}).get("date") or d.get("asof") or ""
            if isinstance(dt, str) and dt:
                c[dt] += 1
        except Exception:
            continue
    if not c:
        return t_minus_1().isoformat()
    return c.most_common(1)[0][0]


@lru_cache(maxsize=2048)
def site_t1() -> str:
    """Site-wide T-1 for pages that are not market-specific.

    The nav badge is global chrome, so Methodology / FAQ / Backtest / Disclaimer
    / Privacy all need one date. Using t_minus_1() (a calendar guess) there is
    what produced the three-way split seen on 2026-10-05: methodology hard-coded
    "HK/JP/US 09-29", faq froze at 2026-09-22, and backtest/disclaimer/privacy
    rendered a bare "—".

    The three markets can and do trade on different sessions (JP was a day
    ahead on 2026-10-02), so take the newest of the three real data dates
    rather than a calendar date: the label then always describes data that is
    actually on the site.
    """
    dates = [d for d in (data_asof(m) for m in ("HK", "US", "JP")) if d]
    return max(dates) if dates else t_minus_1().isoformat()


@lru_cache(maxsize=2048)
def site_label() -> str:
    """Multi-market T-1 label for pages that serve more than one market.

    2026-10-07: site_t1() returns the NEWEST of the three dates, which is only
    correct for a page whose badge is a summary. It is NOT correct for a page
    whose badge stands in for the data on that page — see page_label().
    build_home already printed each market's own date ("2026-10-06 · US
    2026-10-05"); this is that same format, moved here so there is one writer
    for it. HK is the reference session and is printed bare; US and JP are
    printed ONLY when they differ from it, so when all three close together —
    most days — the label collapses back to a single date instead of repeating
    it three times.
    """
    hk, us, jp = data_asof("HK"), data_asof("US"), data_asof("JP")
    parts = [hk]
    if us != hk:
        parts.append(f"US {us}")
    if jp != hk:
        parts.append(f"JP {jp}")
    return " · ".join(parts)


@lru_cache(maxsize=2048)
def page_label(rel_path: str) -> str:
    """The T-1 label that is TRUE for one page under public/.

    2026-10-07: build_static.restamp_t1_badges() wrote site_t1() into every
    page's nav badge, so all 223 /us200/ pages read "T-1 · 2026-10-06" while
    their own bars ended 2026-10-05 — the page claimed a session it does not
    have. The inverse also happened: the homepage badge was collapsed from
    "2026-10-06 · US 2026-10-05" to the single "2026-10-06", so the chrome
    disagreed with the body right below it.

    A market directory names the market, so its pages get that market's own
    date. Everything else genuinely spans markets and gets the composite.
    """
    parts = rel_path.replace("\\", "/").split("/")
    for frag, mkt in (("hk200", "HK"), ("us200", "US"), ("jp200", "JP")):
        if frag in parts:
            return data_asof(mkt)
    return site_label()


def snapshot_quote(tj: dict) -> dict:
    """Normalise a ticker snapshot to {date, last, chg_pct, name}.

    2026-10-05: /jp200/ showed Last = 0.00 and Chg% = +0.00% on 200/200 rows.
    The JP snapshots written by fetch_jp_charts.py are flat ({asof, close,
    chg_pct}) while every reader assumed the HK/US shape (last_bar.C), and the
    lookup default of 0 turned a schema mismatch into a plausible-looking zero
    instead of a visible error. This reads both shapes.

    A missing close is returned as None, never 0 — the renderer prints "—"
    for it, so a genuine data gap is visible instead of looking like a flat
    market. (backfill_jp_schema.py writes last_bar into the JP snapshots, so
    in practice both branches now agree; this is the guard for the next one.)
    """
    lb = tj.get("last_bar") or {}
    last = lb.get("C")
    if last in (None, 0):
        last = tj.get("close") or None
    chg = lb.get("chg_pct")
    if chg is None:
        chg = tj.get("chg_pct")
    return {
        "date": lb.get("date") or tj.get("asof") or "",
        "last": last,
        "chg_pct": chg,
        "name": tj.get("name") or "",
    }


# Repo paths
REPO = Path("/Users/kenken/dev/dsa-hk")
PUBLIC = REPO / "public"


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

HK_OUT = Path("/Users/kenken/dev/dsa-hk/charts/hk200")
US_OUT = Path("/Users/kenken/dev/dsa-hk/charts/us200")
JP_OUT = Path("/Users/kenken/dev/dsa-hk/charts/jp200")
BACKTEST_OUT = Path("/Users/kenken/dev/dsa-hk/data/backtest_out")

UNIVERSE_HK = REPO / "hk_universe_200.json"
US_UNIVERSE = US_OUT / "us_top200_fresh.json"
# 2026-10-06: build what the site PUBLISHES, not just today's turnover top 200.
# us_published.json is the union (written by charts/us200/batch_us200.py) and is
# the set every page on disk was built from. Reading the rotating top-200 here
# is what left 22 live pages frozen on 2026-08-24 bars: they fell out of the
# universe, so nothing rewrote them, while the sitemap and the nav badge kept
# presenting them as current.
US_PUBLISHED = US_OUT / "us_published.json"
US_NAMES_PATH = REPO / "data" / "us_names.json"
US_NAMES: dict[str, str] = (
    json.loads(US_NAMES_PATH.read_text(encoding="utf-8")) if US_NAMES_PATH.exists() else {}
)


def load_universe(path: Path) -> list[str]:
    if not path.exists():
        print(f"⚠ universe not found: {path}")
        return []
    return json.load(open(path))


def load_ticker_json(ticker: str, market: str) -> dict | None:
    """Load per-ticker action_plan JSON written by daily-sr-chart skill."""
    safe = ticker.replace(".", "_")
    if market == "HK":
        base = HK_OUT
    elif market == "JP":
        base = REPO / "data" / "jp200"
    else:
        base = US_OUT
    p = base / f"{safe}.json"
    if not p.exists():
        return None
    try:
        return json.load(open(p))
    except Exception:
        return None


# 2026-10-18 (2-into-1). Deleted from this module, all now dead:
#   load_backtest()        - read data/backtest_out/<t>.json purely to fill the
#                           retired strategies' Win% column (674 file reads/build)
#   MIN_EDGE_N / _LONG_STRATS / _SHORT_STRATS / strats_for_verdict()
#   pick_recent_edge()     - "highest-win% strategy for THIS verdict", whose only
#                           consumer was the Win% badge that no longer renders
#   wilson_lower() / edge_win_lower() - the sample-size-aware bound on that badge
# The backtest files themselves are untouched and /backtest still reads them.


def render_action_row(t: dict) -> str:
    """Render a single row of the screener table.

    2026-10-18 (2-into-1). This row used to carry SIX columns of the retired
    v1 engine: Action (BUY/SELL/WAIT), Trigger, Target, Stop, Win% (90d) and
    Strategy — where "Strategy" spelled out the withdrawn SELL_R1 / BUY_S1 /
    BREAK_LONG / BREAK_SHORT names and "Win%" was that strategy's historical
    record. So /us200/ showed one engine's calls in the table and a different
    engine's calls in the v2 card sitting directly above it, and the two
    disagreed on most rows.

    The table is now v2's. It lists every name in the universe, marks which
    ones v2 has an entry for, and quotes the two levels v2 actually uses —
    the S1 limit entry and the 3x-ATR stop — plus how far the last close sits
    from entry, which is the number that decides whether tomorrow's limit
    order could fill at all. Phase stays because it is price-behaviour
    description, not an instruction.
    """
    ticker = t["ticker"]
    name = t.get("name", "")
    last = t.get("last", 0)
    chg = t.get("chg_pct", 0)
    phase = t.get("phase", "range")
    detail_url = t["detail_url"]

    phase_short = {
        "uptrend": ("Uptrend", "tag-bull"),
        "base_building": ("Base", "tag-amber"),
        "downtrend_active": ("DT", "tag-bear"),
        "downtrend_recovery": ("Recov", "tag-amber"),
        "range": ("Range", "tag-dim"),
    }.get(phase, (phase, "tag-dim"))
    phase_label, phase_class = phase_short

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

    # One lookup, one engine. HK is a third state rather than "no signal",
    # because the difference matters when you ask "why does this one have
    # nothing?" — for HK the honest answer is "we never screened it", not
    # "we screened it and it failed".
    market = t.get("market", "")
    v2 = v2_signal_for(market, ticker.replace(".", "_"))
    if v2:
        v2_action = "SIGNAL"
        action_class = "bull"
        row_class = "row-bull"
        entry_s = fmt(v2.get("entry_s1"))
        stop_s = fmt(v2.get("stop"))
        _d = v2.get("dist_to_entry_pct") or 0.0
        dist_s = "0%" if abs(_d) < 0.05 else f"{abs(_d):.2f}%"
    elif str(market).upper() == "HK":
        v2_action = "未納入 v2"
        action_class = "wait"
        row_class = "row-wait"
        entry_s = stop_s = dist_s = "—"
    else:
        v2_action = "無訊號"
        action_class = "wait"
        row_class = "row-wait"
        entry_s = stop_s = dist_s = "—"

    chg_cls = "text-bear" if chg and chg < 0 else ("text-bull" if chg and chg > 0 else "")
    # "Has a plan" now means exactly one thing: v2 produced an entry. The old
    # version derived it from the v1 row carrying trigger/target/stop, i.e.
    # from the withdrawn engine's definition of actionable.
    _has_plan = "1" if v2 else "0"
    return f"""
    <tr data-action="{v2_action}" data-phase="{phase}" data-plan="{_has_plan}" class="{row_class}">
      <td><a href="{detail_url}" class="mono" style="color: var(--fg);">{ticker}</a></td>
      <td>{name}</td>
      <td class="cell-right mono">{fmt(last)}</td>
      <td class="cell-right mono {chg_cls}">{fmt_pct(chg)}</td>
      <td><span class="tag {phase_class}">{phase_label}</span></td>
      <td><span class="cell-action {action_class}">{v2_action}</span></td>
      <td class="cell-right mono text-bull">{entry_s}</td>
      <td class="cell-right mono text-bear">{stop_s}</td>
      <td class="cell-right mono text-dim">{dist_s}</td>
    </tr>"""


ENHANCED_TABLE_JS = r"""
<script>
const TOTAL_ROWS = document.querySelectorAll('#tableBody tr[data-action]').length;
let currentAction = '';
let currentPhase = '';
document.querySelectorAll('.filter-chip[data-action]').forEach(c => {
  c.addEventListener('click', () => {
    document.querySelectorAll('.filter-chip[data-action]').forEach(x => x.classList.remove('active'));
    c.classList.add('active');
    currentAction = c.dataset.action;
    filterTable();
  });
});
document.querySelectorAll('.filter-chip[data-phase]').forEach(c => {
  c.addEventListener('click', () => {
    document.querySelectorAll('.filter-chip[data-phase]').forEach(x => x.classList.remove('active'));
    c.classList.add('active');
    currentPhase = c.dataset.phase;
    filterTable();
  });
});

function filterTable() {
  const search = document.getElementById('search').value.toLowerCase();
  const rows = document.querySelectorAll('#tableBody tr[data-action]');
  const only = document.getElementById('signalOnly');
  const signalOnly = only && only.classList.contains('active');
  let visible = 0;
  rows.forEach(row => {
    const ticker = row.cells[0]?.textContent.toLowerCase() || '';
    const name = row.cells[1]?.textContent.toLowerCase() || '';
    const action = row.dataset.action;
    const phase = row.dataset.phase;
    const matchSearch = !search || ticker.includes(search) || name.includes(search);
    const matchAction = !currentAction || action === currentAction;
    const matchPhase = !currentPhase || phase === currentPhase;
    // 2026-10-18: the ⚡ chip and the v2-state chips express the same thing,
    // so combining them is either redundant (SIGNAL) or impossible (無訊號,
    // which showed "0 of 232" with no explanation). When the user has picked a
    // specific state, that choice wins and the ⚡ chip does not narrow further.
    const matchPlan = (!signalOnly || currentAction)
      ? true
      : row.dataset.plan === '1';
    const show = matchSearch && matchAction && matchPhase && matchPlan;
    row.style.display = show ? '' : 'none';
    if (show) visible++;
  });
  document.getElementById('rowCount').textContent = visible + ' of ' + TOTAL_ROWS;
  const empty = document.getElementById('emptyState');
  if (empty) {
    empty.hidden = visible !== 0;
    if (visible === 0) {
      empty.textContent = signalOnly
        ? '0 隻有訊號 — 按「⚡ 只看有訊號」取消篩選，可睇全部 ' + TOTAL_ROWS + ' 隻。'
        : '0 results — clear the search or filters to see all rows.';
    }
  }
}

// "只看有訊號" is ON by default: the 200-name coverage is the point of the
// page, but a first-time visitor should land on the rows they can act on.
document.getElementById('signalOnly')?.addEventListener('click', function (e) {
  this.classList.toggle('active');
  filterTable();
});

document.querySelectorAll('th[data-col]').forEach(th => {
  th.setAttribute('tabindex', '0');
  const runSort = () => {
    const idx = Array.from(th.parentNode.children).indexOf(th);
    const dir = th.classList.contains('sorted-asc') ? 'desc' : 'asc';
    document.querySelectorAll('th').forEach(x => {
      x.classList.remove('sorted-asc','sorted-desc');
      x.removeAttribute('aria-sort');
    });
    th.classList.add('sorted-' + dir);
    th.setAttribute('aria-sort', dir === 'asc' ? 'ascending' : 'descending');
    const tbody = document.getElementById('tableBody');
    const rows = Array.from(tbody.querySelectorAll('tr[data-action]'));
    rows.sort((a, b) => {
      const av = a.cells[idx]?.textContent.trim() || '';
      const bv = b.cells[idx]?.textContent.trim() || '';
      const an = parseFloat(av.replace(/[+%,$ HKD\s]/g, ''));
      const bn = parseFloat(bv.replace(/[+%,$ HKD\s]/g, ''));
      const isNum = !isNaN(an) && !isNaN(bn);
      if (isNum) return dir === 'asc' ? an - bn : bn - an;
      return dir === 'asc' ? av.localeCompare(bv) : bv.localeCompare(av);
    });
    rows.forEach(r => tbody.appendChild(r));
  };
  th.addEventListener('click', runSort);
  th.addEventListener('keydown', e => {
    if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); runSort(); }
  });
});
// Apply the default "只看有訊號" filter last, so the row count reflects it.
filterTable();
</script>
"""


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
      <a href="/jp200/"{' class="active"' if market == "JP" else ''}>JP Signals</a>
      <a href="/compare/">Compare</a>
      <a href="/backtest.html">Backtest</a>
      <a href="/insights.html">Insights</a>
      <a href="/methodology.html">Methodology</a>
      <a href="/track-record/">Track record</a>
      <a href="/disclaimer/">Disclaimer</a>
      <a href="/privacy.html">Privacy</a>
    '''

    canon_url = f"https://www.win9you.com/{market.lower()}200/"
    faq_block = ""
    if market == "HK":
        faq_block = """,
    {
      "@type": "FAQPage",
      "mainEntity": [
        {"@type": "Question", "name": "港股 200 點樣揀？", "acceptedAnswer": {"@type": "Answer", "text": "用 5-day 平均成交額排序頭 200 隻，全部 T-1 收市 data，每日 refresh。"}},
        {"@type": "Question", "name": "信號邊度嚟？", "acceptedAnswer": {"@type": "Answer", "text": "價格行為分析框架（phase + S/R ladder + 情境），由 T-1 OHLC 純 Python deterministic 計算。行動計劃統一由 v2 引擎提供；舊版四個策略（SELL_R1 / BUY_S1 / BREAK_LONG / BREAK_SHORT）於 2016–2026 回測並無正期望值，已於 2026-10-18 從頁面移除。"}},
        {"@type": "Question", "name": "成本幾多？", "acceptedAnswer": {"@type": "Answer", "text": "已扣 Futu 免佣 + 0.25% round-trip (HK)，純 signal + backtest 結果。"}}
      ]
    }"""
    elif market == "US":
        faq_block = """,
    {
      "@type": "FAQPage",
      "mainEntity": [
        {"@type": "Question", "name": "美股 200 點樣揀？", "acceptedAnswer": {"@type": "Answer", "text": "用 5-day 平均成交額排序頭 200 隻，全部 T-1 收市 data，每日 refresh。"}},
        {"@type": "Question", "name": "信號邊度嚟？", "acceptedAnswer": {"@type": "Answer", "text": "價格行為分析框架（phase + S/R ladder + 情境），由 T-1 OHLC 純 Python deterministic 計算。行動計劃統一由 v2 引擎提供；舊版四個策略已於 2016–2026 回測證明並無正期望值，於 2026-10-18 從頁面移除。"}},
        {"@type": "Question", "name": "成本幾多？", "acceptedAnswer": {"@type": "Answer", "text": "已扣 0.05% round-trip (US)，Futu 免佣。"}}
      ]
    }"""
    elif market == "JP":
        faq_block = """,
    {
      "@type": "FAQPage",
      "mainEntity": [
        {"@type": "Question", "name": "日股 200 點樣揀？", "acceptedAnswer": {"@type": "Answer", "text": "由 Topix Core30 + Large70 開始，yfinance verify 流通量，200 隻全部 T-1 收市 data。"}},
        {"@type": "Question", "name": "信號邊度嚟？", "acceptedAnswer": {"@type": "Answer", "text": "價格行為分析框架（phase + S/R ladder + 情境）。行動計劃統一由 v2 引擎提供；舊版四個策略已於 2016–2026 回測證明並無正期望值，於 2026-10-18 從頁面移除。"}},
        {"@type": "Question", "name": "成本幾多？", "acceptedAnswer": {"@type": "Answer", "text": "已扣 0.05% round-trip (JP)，Futu 免佣。"}}
      ]
    }"""

    # 2026-10-02: this is an f-string, so every *literal* JSON brace must stay
    # doubled ({{ / }}); only the interpolation expressions below are single.
    # faq_block is itself a plain (non-f) string built above, so its braces are
    # already single there — that mismatch is what used to emit literal `{{`
    # into the page and made the whole JSON-LD block unparseable for Google.
    jsonld = f"""{{
  "@context": "https://schema.org",
  "@graph": [
    {{
      "@type": "BreadcrumbList",
      "itemListElement": [
        {{"@type": "ListItem", "position": 1, "name": "Home", "item": "https://www.win9you.com/"}},
        {{"@type": "ListItem", "position": 2, "name": "{market} Signals", "item": "{canon_url}"}}
      ]
    }},
    {{
      "@type": "CollectionPage",
      "name": "{title_main} Action Plan",
      "url": "{canon_url}",
      "inLanguage": "zh-Hant-HK",
      "isPartOf": {{"@type": "WebSite", "name": "Leeks Terminal", "url": "https://www.win9you.com/"}}
    }}{faq_block}
  ]
}}"""

    _mnav = {"HK": "hk", "US": "us", "JP": "jp"}.get(market, "")
    _t1 = data_asof(market)
    v2_block = render_v2_block(market)
    return f"""<!DOCTYPE html>
<html lang="zh-Hant-HK">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<link rel="icon" type="image/svg+xml" href="/favicon.svg">
<link rel="alternate icon" href="/favicon.ico" sizes="any">
<link rel="manifest" href="/manifest.json">
<link rel="icon" type="image/png" sizes="64x64" href="/favicon.png">
<link rel="apple-touch-icon" href="/apple-touch-icon.png">
<title>{title_main} · Leeks Terminal</title>
<meta name="description" content="{title_lede}">
<meta name="theme-color" content="#0a0e1a">
<link rel="canonical" href="{canon_url}">
<meta property="og:site_name" content="Leeks Terminal">
<meta property="og:type" content="website">
<meta property="og:title" content="{title_main} · Leeks Terminal">
<meta property="og:description" content="{title_lede}">
<meta property="og:url" content="{canon_url}">
<meta property="og:image" content="{market.lower()}200/og-image.png">
<meta property="og:image:width" content="1200">
<meta property="og:image:height" content="630">
<meta property="og:locale" content="zh_HK">
<meta name="twitter:card" content="summary_large_image">
<script type="application/ld+json">
{jsonld}
</script>
<link rel="stylesheet" href="/static/fonts-local.css">
<link rel="stylesheet" href="/leeks.css?v={_cssver()}">
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
      T-1 · {_t1}
      <button class="theme-toggle" onclick="toggleTheme()" aria-label="Toggle theme">
        <span class="icon" id="themeIcon">●</span>
        <span id="themeLabel">DARK</span>
      </button>
    </div>
  </nav>
</header>
{mobile_bottom_nav(_mnav)}
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

<main role="main">
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

<!-- 2026-10-02: v2 limit-buy plan. The 10-year audit of the four published
     strategies found no positive edge, so v2 (long-only, BUY at S1 next
     session, 3xATR stop, no target, 10-session exit, trend + market filter)
     is published as the primary plan. Its setup statistics are the study's
     out-of-sample figures and are labelled as third-party in the block. -->
{v2_block}

<div class="container">
  <div class="filter-bar">
    <label>Filter</label>
    <input type="text" id="search" placeholder="Search ticker or name..." onkeyup="filterTable()">
    <label>v2</label>
    <div style="display: flex; gap: 6px;">
      <button type="button" class="filter-chip active" data-action="">All</button>
      <button type="button" class="filter-chip" data-action="SIGNAL">🟢 SIGNAL</button>
      <button type="button" class="filter-chip" data-action="無訊號">⚪ 無訊號</button>
    </div>
    <label>Phase</label>
    <div style="display: flex; gap: 6px;">
      <button type="button" class="filter-chip active" data-phase="">All</button>
      <button type="button" class="filter-chip" data-phase="uptrend">Uptrend</button>
      <button type="button" class="filter-chip" data-phase="base_building">Base</button>
      <button type="button" class="filter-chip" data-phase="downtrend_recovery">Recovery</button>
      <button type="button" class="filter-chip" data-phase="downtrend_active">Down</button>
      <button type="button" class="filter-chip" data-phase="range">Range</button>
    </div>
    <label style="margin-left: auto;" id="rowCount">{len(universe)} of {len(universe)}</label>
    <button type="button" class="filter-chip active" id="signalOnly" data-signal="1" title="只顯示 v2 有入場計劃嘅股">⚡ 只看有訊號</button>
  </div>

  <div style="overflow: auto; max-height: calc(100vh - 76px); overscroll-behavior: contain; border: 1px solid var(--border); border-radius: var(--radius-lg);">
  <table class="data-table" id="dataTable">
  <thead>
  <tr>
    <th data-col="ticker">Ticker</th>
    <th data-col="name">Name</th>
    <th data-col="last" class="cell-right">Last</th>
    <th data-col="chg" class="cell-right">Chg%</th>
    <th data-col="phase">Phase</th>
    <th data-col="action">v2 Action</th>
    <th data-col="entry" class="cell-right">入場 S1</th>
    <th data-col="stop" class="cell-right">止損 (3×ATR)</th>
    <th data-col="dist" class="cell-right">距入場</th>
  </tr>
  </thead>
  <tbody id="tableBody">{rows_html}
  </tbody>
  </table>
  <div id="emptyState" hidden style="padding: var(--sp-7); text-align: center; color: var(--dim); font-family: var(--font-mono); font-size: var(--text-sm);">
    0 results — clear the search or filters to see all rows.
  </div>
  </div>

  <div class="disclaimer mt-5">
    <b>⚠ Disclaimer</b> · Educational only. Not investment advice. T-1 data (yesterday's close) — never today's intraday. Backtest win% is historical, not predictive.
  </div>
</div>

</main>

<footer class="site-footer">
  <div class="container">
    <div class="row">
      <div>© 2026 Leeks Terminal · win9you.com · <a href="/methodology">Methodology</a> · <a href="/disclaimer">Disclaimer</a></div>
      <div class="mono text-dim">T-1 · {_t1}</div>
    </div>
  </div>
</footer>

{ENHANCED_TABLE_JS}
</body>
</html>"""


_V2_CACHE: dict | None = None


def _v2_index() -> dict:
    """(market_key, symbol) -> signal, loaded once per build.

    v2_signal_for is called once per screener row and once per detail page
    (~900 times per build). Re-reading and re-parsing v2-signals.json on each
    call turned a one-file read into ~900 of them.
    """
    global _V2_CACHE
    if _V2_CACHE is None:
        idx: dict = {}
        f = PUBLIC / "v2-signals.json"
        if f.exists():
            try:
                d = json.loads(f.read_text(encoding="utf-8"))
                for r in d.get("signals", []):
                    idx[(r.get("market"), r.get("symbol"))] = r
            except Exception:
                idx = {}
        _V2_CACHE = idx
    return _V2_CACHE


def v2_signal_for(market: str, symbol: str) -> dict | None:
    """The single source of action authority.

    2026-10-18: every ticker page carried TWO verdicts. This one is v2 — the
    engine the site publishes. The other came from the retired v1 engine, whose
    action_plan literally emitted SELL_R1 / BUY_S1 / BREAK_LONG / BREAK_SHORT,
    the four strategies the methodology page says were withdrawn after showing
    no positive expectancy over 2016-2026. Two engines, one of them already
    retired, both rendered as "the Action" — so v2 now answers this question
    alone. None of v2's rules change; only who is allowed to have an opinion."""
    key = str(market).lower() + "200"   # callers pass "US"/"JP"/"HK"
    return _v2_index().get((key, symbol))


def _num(x, dp: int = 2) -> str:
    """Same formatting the page already uses for levels (render_detail_page has
    its own local copy; kept identical so the two do not drift)."""
    if x is None:
        return "—"
    try:
        return f"{float(x):,.{dp}f}"
    except Exception:
        return str(x)


def render_v2_action_card(hit: dict | None, market: str) -> str:
    """The page's one and only Action Plan card, rendered from v2."""
    fmt = _num
    if hit is None:
        if str(market).lower() + "200" == "hk200":
            why = ("港股目前<b>未納入 v2</b>，所以冇 v2 行動計劃。"
                   "頁面下半部嘅價格行為分析仍然有效，但唔構成 v2 訊號。")
        else:
            why = ("該股今日未通過 v2 篩選，<b>無行動計劃</b>。"
                   f"{str(market).upper()} 今日有訊號嘅股數見上面 v2 訊號卡。")
        return f"""
      <div class="info-card fade-in">
        <h3 style="display:flex;justify-content:space-between;align-items:center;">
          <span>Action Plan</span>
          <span style="font-family:var(--font-mono);font-size:var(--text-md);color:var(--dim);">無訊號</span>
        </h3>
        <div class="phase-block">
          <span class="name">v2 訊號</span>
          <span class="value" style="color:var(--dim)">{why}</span>
        </div>
      </div>"""

    dist = hit.get("dist_to_entry_pct")
    dist_txt = "已到入場位" if abs(dist or 0) < 0.05 else f"距入場位 {abs(dist):.2f}%"
    return f"""
      <div class="info-card fade-in" style="border-left:3px solid var(--bull);">
        <h3 style="display:flex;justify-content:space-between;align-items:center;">
          <span>Action Plan <span style="font-size:var(--text-xs);opacity:0.55">v2 · 唯一行動權威</span></span>
          <span style="font-family:var(--font-mono);font-size:var(--text-md);color:var(--bull);">SIGNAL</span>
        </h3>
        <div class="phase-block">
          <span class="name">現價距離</span>
          <span class="value text-bull">{dist_txt}</span>
        </div>
        <div class="phase-block">
          <span class="name">入場（S1 限價）</span>
          <span class="value text-bull">{fmt(hit["entry_s1"])}</span>
        </div>
        <div class="phase-block">
          <span class="name">止損（3×ATR）</span>
          <span class="value text-bear">{fmt(hit["stop"])}（{hit["stop_dist_pct"]:.2f}%）</span>
        </div>
        <div class="phase-block">
          <span class="name">倉位</span>
          <span class="value">{hit["shares_at_1pct_risk"]} 股 · 風險 {hit["risk_pct_actual"]:.2f}% · 名義 {hit["notional_pct"]:.1f}%</span>
        </div>
        <div class="phase-block">
          <span class="name">目標</span>
          <span class="value text-dim">冇目標價</span>
        </div>
        <div class="phase-block">
          <span class="name">離場</span>
          <span class="value">{hit["exit_rule"]}</span>
        </div>
        <div class="phase-block">
          <span class="name">成交條件</span>
          <span class="value" style="font-size:var(--text-xs);color:var(--dim)">掛單喺 S1，<b>收市要企穩 S1 先算成交</b>；未收穩就當日無交易。</span>
        </div>
      </div>"""


def render_detail_page(t: dict, prev_row: dict | None = None, next_row: dict | None = None) -> str:
    """Render a single ticker detail page."""
    ticker = t["ticker"]
    safe = ticker.replace(".", "_")
    name = t.get("name", "")
    last = t.get("last", 0)
    chg = t.get("chg_pct", 0)
    phase = t.get("phase", "range")
    ap = t.get("action_plan", {})
    verdict = ap.get("verdict", "WAIT")
    chart_url = t["chart_url"]
    chart_v = t.get("chart_v") or t_minus_1().isoformat()
    market = t["market"]
    detail_url = t["detail_url"]
    back_url = "/hk200/" if market == "HK" else "/us200/"
    _mnav = {"HK": "hk", "US": "us", "JP": "jp"}.get(market, "")
    _t1 = data_asof(t.get("market", market))

    # 2026-10-06 — fail-closed data-freshness banner.
    #
    # The nav badge is restamped across every page in public/**, including
    # pages whose body was never rebuilt because their name had fallen out of
    # the build universe. That produced live pages advertising the current T-1
    # while their body held bars from six weeks earlier. A page must never
    # claim a data date it does not have.
    #
    # Detected automatically from the ticker's own last_bar rather than from a
    # hand-maintained "dead tickers" list, so it clears itself the moment the
    # upstream source recovers — AVB and BRK-B both died because the data
    # vendor changed, which is not something a list would predict.
    _own_bar = ""
    # 2026-10-10: this read JP_OUT (charts/jp200), which holds PNGs and OHLC but
    # NOT the snapshot JSON — fetch_jp_charts.py writes those to data/jp200.
    # So _own_bar was always "" for every JP name, the banner below could never
    # fire, and a JP page whose bars were a session behind still advertised the
    # current market T-1. Seven out-of-universe names were caught showing
    # "T-1 · 2026-10-09" over 2026-10-08 data. Read the directory the snapshots
    # are actually written to, per market.
    _SNAP_DIR = {"HK": HK_OUT, "US": US_OUT, "JP": REPO / "data" / "jp200"}
    try:
        _src = _SNAP_DIR[market] / f"{safe}.json"
        _own_bar = (json.load(open(_src)).get("last_bar") or {}).get("date", "")
    except Exception:
        _own_bar = ""
    stale_banner = ""
    if _own_bar and _t1 and _own_bar < _t1:
        _n = (__import__("datetime").date.fromisoformat(_t1)
              - __import__("datetime").date.fromisoformat(_own_bar)).days
        stale_banner = (
            '<div class="container" style="margin:18px 0 0">'
            '<div class="info-card" style="border-left:3px solid var(--amber)">'
            '<h2 style="margin-top:0;font-size:1rem">數據未更新</h2>'
            f'<p style="font-size:0.85rem;opacity:0.8;margin:6px 0 0">'
            f'本頁嘅最後有效 bar 係 <b>{_own_bar}</b>，而 {market} 市場最新係 <b>{_t1}</b>'
            f'（差 {_n} 個曆日）。上游數據源（Yahoo Finance）目前對呢隻代號攞唔到'
            f'已成交價格，所以頁面不會重建 —— '
            f'<b>以下所有價位、訊號同回測數字都係 {_own_bar} 或之前嘅資料</b>，'
            f'唔可以當成現行數據使用。</p>'
            # 2026-10-07: tell the reader how to tell this apart from a delisting.
            # "上游死" and "隻股票冇交易" look identical on the page but need
            # opposite responses, and the reader is the only one who can check
            # their own broker. Without this the banner is a dead end.
            f'<p style="font-size:0.8rem;opacity:0.7;margin:6px 0 0">'
            f'呢個係<b>本網站上游數據源</b>嘅問題，唔等於隻股票冇報價。'
            f'如果你喺券商 App 見到 {t.get("ticker", safe)} 有正常成交，即係代表我哋呢邊'
            f'暫時攞唔到 —— 唔好因為呢一頁就當佢冇交易。</p>'
            '</div></div>'
        )

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
    # 2026-10-18: this used to be daily_sr's action_plan.notes, which literally
    # opens with the retired strategy name ("WAIT_RANGE: in box (...)"). The
    # user-facing note now states the same box/position facts without naming a
    # strategy the site has withdrawn.
    _box = t.get("box") or {}
    notes = (f"箱體 S1 {_num(ap.get('s1'))} – R1 {_num(ap.get('r1'))}，"
             f"現價處於箱體 {ap.get('current_position_pct', 50):.0f}% 位置。"
             f"技術面：{phase_label}。")
    # 2026-10-18: the retired v1 verdict used to be printed here under the label
    # "Bias (短)", which mislabelled an action as a bias. The real short-horizon
    # bias already existed in daily_sr's output and is a description of price
    # behaviour, not an instruction — so that is what this row now shows.
    _bias = t.get("bias", {}) or {}
    _short = (_bias.get("short", {}) or {})
    bias_short = _short.get("bias") or "—"
    if _short.get("reason"):
        bias_short += f"（{_short['reason']}）"

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

    # 2026-10-18: the live v2 signal for THIS ticker. This is resolved FIRST,
    # before any of the text that quotes it (meta description, JSON-LD, hero,
    # action card) so there is exactly one place that decides what v2 says and
    # one place it can be read from.
    # HK gets an explicit "not covered by v2" notice instead of a verdict.
    v2_hit = v2_signal_for(market, safe)
    v2_status = render_v2_status_for_ticker(
        market, safe, old_verdict=verdict, old_phase=phase_label)
    v2_action_card = render_v2_action_card(v2_hit, market)
    if v2_hit:
        _d = v2_hit.get("dist_to_entry_pct") or 0.0
        v2_dist_txt = ("已到入場位" if abs(_d) < 0.05 else f"{abs(_d):.2f}%")
        v2_entry_txt = fmt(v2_hit["entry_s1"])
        v2_stop_txt = fmt(v2_hit["stop"])
        v2_state, v2_colour = "SIGNAL", "var(--bull)"
    else:
        v2_state, v2_colour = "無訊號", "var(--dim)"
        v2_dist_txt = v2_entry_txt = v2_stop_txt = "—"

    # 2026-10-18: the hero used to read "{name} · BUY_S1 · 60% win (60d, n=5)
    # · +5.0%". That win% is the historical record of one of the four
    # strategies the methodology page withdrew in 2026-10 — so on a page that
    # now shows a live v2 entry it read as confidence in the v2 signal, which
    # it never measured. A ticker page states what it knows: which engine has
    # the action, and what the price behaviour is.
    edge_str = f"T-1 收市數據 · {market}"

    # 2026-10-18 (2-into-1). The hero meta line used to say "Action / 距離 /
    # 入場 / 止損全部來自 v2" on every market including HK — which is false for
    # HK, which v2 does not scan at all. Claiming a source that does not cover
    # the number is worse than showing nothing, so HK gets its own line.
    _hk = (market or "").upper() == "HK"
    if _hk:
        hero_meta_html = (
            f'<span>Phase: <b style="color: {phase_color};">{phase_label}</b></span>'
            '<span>行動計劃: <b style="color: var(--dim)">無 —— 港股未納入 v2</b></span>'
            '<span>入場 / 止損: <b style="color: var(--dim)">不適用</b></span>'
        )
        hero_note_html = (
            '↑ Phase 係價格行為分析（<b>唔係</b>行動指示）。'
            '本站<b>冇</b>港股行動計劃：v2 唔覆蓋港股，舊版引擎嘅行動建議亦已全站移除。'
        )
    else:
        hero_meta_html = (
            f'<span>Phase: <b style="color: {phase_color};">{phase_label}</b></span>'
            f'<span>v2 Action: <b style="color: {v2_colour};">{v2_state}</b></span>'
            f'<span>現價距離: <b>{v2_dist_txt}</b></span>'
            f'<span>入場 / 止損: <b class="text-bull">{v2_entry_txt}</b> / '
            f'<b class="text-bear">{v2_stop_txt}</b></span>'
        )
        hero_note_html = (
            '↑ Phase 係價格行為分析（<b>唔係</b>行動指示）；'
            '<b>Action / 距離 / 入場 / 止損全部來自 v2</b>，係本頁唯一嘅行動權威。'
        )

    chg_str = fmt_pct(chg)
    chg_color = "var(--bear)" if chg and chg < 0 else ("var(--bull)" if chg and chg > 0 else "var(--fg)")

    # 2026-10-18: these four lines used to instruct the reader to execute the
    # withdrawn strategies — "SELL_R1 entry", "SHORT confirmation". They now
    # state the same price levels as conditions and say what v2 does about
    # them, so the card describes the levels instead of competing with the
    # single action authority.
    scenarios_html = f'''
        <li><b>IF</b> 反彈至 {fmt(r1) if r1 else "R1"}（箱頂 R1）, <b>THEN</b> 阻力區；v2 只喺 S1 掛限價單，<b>唔會</b>喺呢個位入場</li>
        <li><b>IF</b> 收市失守 {fmt(s1) if s1 else "S1"}（S1）, <b>THEN</b> support 失效 —— v2 要求<b>收市企穩 S1</b> 先算成交，收穿即當日無交易</li>
        <li><b>IF</b> 突破 {fmt(r1 * 1.02) if r1 else "—"} 企穩, <b>THEN</b> 離開整固，下一個參考位 {fmt(r3) if r3 else "R3"}</li>
        <li><b>IF</b> 跌穿 {fmt(s4) if s4 else "S4"}（crash low）, <b>THEN</b> 中期結構失效，無論 v2 有冇訊號都唔應該持有</li>
    '''

    # SEO/GEO: canonical URL (Cloudflare Pages pretty URL = extensionless, no
    # trailing slash — verified live 2026-08-25: both .html and trailing-slash
    # variants 308 to this form).
    canon_url = f"https://www.win9you.com/{market.lower()}200/ticker/{safe}"

    # SEO/GEO: description carries the concrete verdict + levels so search
    # engines and AI answer engines can quote a self-contained answer.
    # 2026-10-18: the meta description used to carry the retired engine's
    # verdict, so a search result for a name with a live v2 entry could read
    # "... Action Plan · WAIT". It now quotes v2.
    if v2_hit:
        desc_parts = [f"{ticker} {name} v2 Action Plan", "SIGNAL",
                      f"入場 {fmt(v2_hit['entry_s1'])} / 止損 {fmt(v2_hit['stop'])}",
                      f"現價距離 {abs(v2_hit.get('dist_to_entry_pct') or 0):.2f}%",
                      f"技術面 {phase_label}"]
    elif (market or "").upper() == "HK":
        # "今日無 v2 訊號" would imply HK was screened and failed. It was never
        # screened. Say which one it is.
        desc_parts = [f"{ticker} {name} 港股未納入 v2",
                      "本站冇港股行動計劃",
                      f"技術面 {phase_label}"]
    else:
        desc_parts = [f"{ticker} {name} v2 Action Plan", "今日無 v2 訊號",
                      f"技術面 {phase_label}"]
    desc_parts.append(f"T-{1}收市數據 · 教育用途非投資建議")
    meta_desc = " · ".join(desc_parts)

    t1_iso = t_minus_1().isoformat()
    # 2026-10-18 (2-into-1 + a real bug fix). This headline used to carry the
    # RETIRED v1 engine's verdict into structured data, where a search engine or
    # an AI reader sees "Action Plan (WAIT)" with no indication of which engine
    # said it. The retired engine no longer states an action anywhere, so the
    # headline states the one engine that does.
    #
    # The note above is a PYTHON comment and must stay outside the f-string.
    # Commit aea38b0c put its "# ..." explanation lines INSIDE the JSON-LD
    # f-string, so every one of the 674 ticker pages shipped
    # `<script type="application/ld+json">` whose payload was not parseable
    # JSON (# is not a JSON comment) — search engines silently dropped the
    # whole block. Never put a "#" line inside an ld+json literal again;
    # self_test.py now asserts every block parses.
    detail_jsonld = f"""{{
  "@context": "https://schema.org",
  "@graph": [
    {{
      "@type": "BreadcrumbList",
      "itemListElement": [
        {{"@type": "ListItem", "position": 1, "name": "Home", "item": "https://www.win9you.com/"}},
        {{"@type": "ListItem", "position": 2, "name": "{market} Signals", "item": "https://www.win9you.com/{market.lower()}200/"}},
        {{"@type": "ListItem", "position": 3, "name": "{ticker} {name}", "item": "{canon_url}"}}
      ]
    }},
    {{
      "@type": "Article",
      "headline": "{ticker} {name} 技術面 {phase_label} · v2 Action: {v2_state}",
      "url": "{canon_url}",
      "mainEntityOfPage": "{canon_url}",
      "inLanguage": "zh-Hant-HK",
      "dateModified": "{t1_iso}",
      "author": {{"@type": "Organization", "name": "Leeks Terminal"}},
      "publisher": {{"@type": "Organization", "name": "Leeks Terminal", "url": "https://www.win9you.com/"}}
    }}
  ]
}}"""

    def _nb(r: dict | None, direction: str) -> str:
        if not r:
            return "<span></span>"
        rsafe = r["ticker"].replace(".", "_")
        arrow = "←" if direction == "prev" else "→"
        return f'<a href="/{r["market"].lower()}200/ticker/{rsafe}" class="btn btn-ghost">{arrow} {r["ticker"]} {r.get("name", "")}</a>'

    prevnext_html = f"""
  <div style="display: flex; justify-content: space-between; gap: var(--sp-2); margin-top: var(--sp-4);">
    {_nb(prev_row, "prev")}
    {_nb(next_row, "next")}
  </div>"""

    # 2026-10-18: this note existed only to qualify the retired strategy's
    # win%. With the win% gone there is nothing left to qualify — the Action
    # Plan below is v2's, and v2's own record is published on /backtest.
    edge_note_html = ""

    _jsv = _canvas_js_version()

    return f"""<!DOCTYPE html>
<html lang="zh-Hant-HK">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<link rel="icon" type="image/svg+xml" href="/favicon.svg">
<link rel="alternate icon" href="/favicon.ico" sizes="any">
<link rel="manifest" href="/manifest.json">
<link rel="icon" type="image/png" sizes="64x64" href="/favicon.png">
<link rel="apple-touch-icon" href="/apple-touch-icon.png">
<title>{ticker} {name} · Action Plan · Leeks Terminal</title>
<meta name="description" content="{meta_desc}">
<meta name="theme-color" content="#0a0e1a">
<link rel="canonical" href="{canon_url}">
<meta property="og:site_name" content="Leeks Terminal">
<meta property="og:type" content="article">
<meta property="og:title" content="{ticker} {name} · Action Plan · Leeks Terminal">
<meta property="og:description" content="{meta_desc}">
<meta property="og:url" content="{canon_url}">
<meta property="og:image" content="{market.lower()}200/og-image.png">
<meta property="og:image:width" content="1200">
<meta property="og:image:height" content="630">
<meta property="og:locale" content="zh_HK">
<meta name="twitter:card" content="summary_large_image">
<script type="application/ld+json">
{detail_jsonld}
</script>
<link rel="stylesheet" href="/static/fonts-local.css">
<link rel="stylesheet" href="/leeks.css?v={_cssver()}">
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
    <div class="nav-links">
      <a href="/">Home</a>
      <a href="/hk200/"{' class="active"' if market == "HK" else ''}>HK Signals</a>
      <a href="/us200/"{' class="active"' if market == "US" else ''}>US Signals</a>
      <a href="/jp200/"{' class="active"' if market == "JP" else ''}>JP Signals</a>
      <a href="/compare/">Compare</a>
      <a href="/backtest.html">Backtest</a>
      <a href="/insights.html">Insights</a>
      <a href="/methodology.html">Methodology</a>
      <a href="/track-record/">Track record</a>
      <a href="/disclaimer/">Disclaimer</a>
      <a href="/privacy.html">Privacy</a>
    </div>
    <div class="nav-meta">
      <span class="live-dot"></span>
      T-1 · {_t1}
      <button class="theme-toggle" onclick="toggleTheme()" aria-label="Toggle theme">
        <span class="icon" id="themeIcon">●</span>
        <span id="themeLabel">DARK</span>
      </button>
    </div>
  </nav>
</header>
{mobile_bottom_nav(_mnav)}
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
      {hero_meta_html}
    </div>
    <p style="font-size:0.72rem;opacity:0.55;margin:2px 0 0">
      {hero_note_html}
    </p>
    {edge_note_html}
  </div>
</div>

<div class="container">
  <main role="main">
{stale_banner}
{v2_status}
<div class="detail-grid">
    <div>
      <div class="chart-frame fade-in">
        <div class="skeleton" id="chartSkel_{safe}"></div>
        <div id="chart-host"
             data-ticker="{safe}"
             data-chart-json="/{market.lower()}200/charts/{safe}.json?v={chart_v}"
             style="position:relative;min-height:540px;">
          <noscript>
            <p style="color:var(--text-dim);font-size:13px;padding:24px">
              This chart needs JavaScript. The underlying data is available at
              <code>{chart_url}.json</code>.
            </p>
          </noscript>
        </div>
        <div id="chart-window-controls" style="display:flex;gap:var(--sp-2);justify-content:flex-end;padding:var(--sp-3) var(--sp-4);font-family:var(--font-mono);font-size:var(--text-xs);">
          <button data-window="30" style="background:transparent;border:1px solid var(--border);color:var(--fg-2);padding:4px 10px;border-radius:4px;cursor:pointer;">30D</button>
          <button data-window="90" class="active" style="background:var(--blue-dim);border:1px solid var(--blue);color:var(--blue);padding:4px 10px;border-radius:4px;cursor:pointer;">90D</button>
          <span id="chart-window-label" style="color:var(--dim);margin-left:var(--sp-3);align-self:center;">Daily · 90D</span>
        </div>
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
          <span class="value" style="color: var(--dim)">{bias_short}</span>
        </div>
        <div class="phase-block">
          <span class="name">⑥ Notes</span>
          <span class="value" style="font-size: var(--text-xs); color: var(--dim);">{notes}</span>
        </div>
      </div>

      <div class="info-card mt-4 fade-in fade-in-3">
        <h3>失效條件（If-then scenarios）</h3>
        <p style="font-size: var(--text-sm); color: var(--text-dim); margin: 0 0 10px;">
          呢啲係<b>取消／反轉</b>條件，唔係另一個行動指示。
          同一個價位如果同時出現「入場」同「失守」，
          <b>以收市價為準</b>：收得守住就照入場計，收穿就當 support 失效、唔好入場。
        </p>
        <ul class="scenario-list">{scenarios_html}
        </ul>
      </div>
    </div>

    <div>
      {v2_action_card}

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

  <div class="mt-5 mb-5" style="display: flex; gap: var(--sp-2); flex-wrap: wrap;">
    <a href="{back_url}" class="btn btn-ghost">← Back to {market} Signals</a>
    <a href="/methodology" class="btn btn-ghost">Methodology</a>
    <a href="/backtest" class="btn btn-ghost">Backtest</a>
    <a href="/methodology.html" class="btn btn-ghost">Methodology</a>
    <a href="/compare/" class="btn btn-ghost">Compare</a>
    <a href="/hk200/" class="btn btn-ghost">HK</a>
    <a href="/us200/" class="btn btn-ghost">US</a>
    <a href="/jp200/" class="btn btn-ghost">JP</a>
  </div>
  {prevnext_html}
</div>
</main>

<footer class="site-footer">
  <div class="container">
    <div class="row">
      <div>© 2026 Leeks Terminal · win9you.com · <a href="/methodology">Methodology</a> · <a href="/disclaimer">Disclaimer</a></div>
      <div class="mono text-dim">T-1 · {_t1}</div>
    </div>
  </div>
</footer>
<script src="/static/canvas-chart.js?v={_jsv}" defer></script>
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
        title_lede = "港股 200 by 5-day 平均成交額排序。港股 <b>未納入 v2</b>，所以呢頁冇港股入場計劃 —— 舊版引擎嘅 BUY/SELL/WAIT 行動建議已於 2026-10-18 全站移除。表內係價格行為分析（Phase）同 T-1 收市價。回測口徑：HK 0.25% round-trip 成本。"
    elif market == "JP":
        universe = []
        jp_univ = REPO / "jp_universe_200.json"
        if jp_univ.exists():
            universe = json.load(open(jp_univ))
        chart_dir = PUBLIC / "jp200" / "charts"
        src_dir = REPO / "data" / "jp200"
        out_index = PUBLIC / "jp200" / "index.html"
        ticker_dir = PUBLIC / "jp200" / "ticker"
        market_name = "JP"
        title_kicker = "JP · 200 names"
        title_lede = "日股 200 (Topix Core30 + Large70) by 5-day 平均成交額排序。v2 係本站<b>唯一</b>嘅行動權威：跌到 20 日低位 S1 掛限價單、收市企穩先算成交、止損 3×ATR、第 10 個交易日離場。舊版四個策略已於 2026-10-18 移除。T-1 收市 data，回測口徑 0.05% round-trip 成本。"
    else:
        universe = []
        if US_PUBLISHED.exists():
            universe = json.load(open(US_PUBLISHED))
        elif US_UNIVERSE.exists():
            universe = json.load(open(US_UNIVERSE))
        elif (US_OUT / "us_top200.json").exists():
            universe = json.load(open(US_OUT / "us_top200.json"))
        chart_dir = PUBLIC / "us200" / "charts"
        src_dir = US_OUT
        out_index = PUBLIC / "us200" / "index.html"
        ticker_dir = PUBLIC / "us200" / "ticker"
        market_name = "US"
        title_kicker = "US · 200 names"
        title_lede = "美股 200 by 5-day 平均成交額排序。v2 係本站<b>唯一</b>嘅行動權威：跌到 20 日低位 S1 掛限價單、收市企穩先算成交、止損 3×ATR、第 10 個交易日離場。舊版四個策略已於 2026-10-18 移除。T-1 收市 data，回測口徑 US 0.05% round-trip 成本。"

    if not universe:
        print(f"⚠ {market}: empty universe, skip")
        return

    print(f"\n=== {market}: building {len(universe)} tickers ===")

    # Phase counts for hero
    phase_counts = {}
    # 2026-10-18 (2-into-1): counts the engine the page now publishes (v2),
    # not the retired v1 verdict, which no longer renders anywhere.
    action_counts = {"SIGNAL": 0, "無訊號": 0, "未納入 v2": 0}
    rows = []
    chart_copied = 0

    for i, ticker in enumerate(universe):
        tj = load_ticker_json(ticker, market_name)
        if not tj:
            # No JSON — skip but log
            continue

        q = snapshot_quote(tj)
        last = q["last"]
        chg = q["chg_pct"]          # None stays None → renders "—", never a fake 0.00%
        name = q["name"]
        # 2026-09-16: fallback to /dsa-hk/data/us_names.json when source name empty
        # (HK + JP come with names in source; US lacks ~50% of names → use Tencent qt fetch)
        if not name and market == "US" and US_NAMES:
            code = ticker.replace(".", "_")
            name = US_NAMES.get(code, "") or US_NAMES.get(ticker.replace("US.", ""), "")
        phase = tj.get("phase", "range")
        ap = tj.get("action_plan", {})
        verdict = ap.get("verdict", "WAIT")

        safe = ticker.replace(".", "_")
        # Extensionless on purpose. The <noscript> block renders this as
        # `{chart_url}.json`, so a trailing ".png" here produced the dead link
        # /<mkt>200/charts/<t>.png.json — a filename that never existed. The
        # PNGs were dropped from the deploy (canvas-chart.js draws the visible
        # chart from JSON), so this value now has exactly one consumer: that
        # noscript text. Keep it in sync with the stamp invariant below.
        chart_url = f"/{market.lower()}200/charts/{safe}"
        # Cache-buster: data date ALONE is not enough. Two builds on the same
        # session (a redraw, a layout fix) produce different bytes under an
        # identical ?v=, so browsers and the CDN keep serving the older bytes —
        # 2026-10-12 this shipped a label fix that no visitor could see. Append
        # a short hash of the payload itself so the URL changes exactly when
        # the data does.
        try:
            import hashlib as _hl
            # Hash the JSON pair, not the PNG. The visible chart is drawn by
            # canvas-chart.js from /charts/<t>.json + /ohlc/<t>_ohlc.json.
            # Busting on the PNG alone left both JSON URLs uncached, so a
            # redraw was invisible to every visitor. Hash exactly the bytes the
            # browser will fetch: the chart JSON and the OHLC JSON. build_dashboard
            # and scripts/self_test.py MUST use the same rule or the stamp check
            # disagrees with reality.
            _cj = Path(src_dir) / f"{safe}.json"
            _oh = Path(src_dir) / f"{safe}_ohlc.json"
            _d = _hl.sha1()
            for _p in (_cj, _oh):
                try:
                    _d.update(_p.read_bytes())
                except Exception:
                    _d.update(b"?")
            _h = _d.hexdigest()[:8]
        except Exception:
            _h = "0"
        # Date prefix comes from data_asof(market) (per-market, derived from the
        # snapshot JSONs, cannot drift) rather than t_minus_1() (calendar-derived
        # and only globally overridable via LEAKS_ASOF). Same market, redrawn
        # chart -> same date but different hash -> URL still changes.
        try:
            _asof = data_asof(market_name)
        except Exception:
            _asof = t_minus_1().isoformat()
        chart_v = f"{_asof}-{_h}"

        row = {
            "ticker": ticker,
            "name": name,
            "last": last,
            "chg_pct": chg,
            "phase": phase,
            "action_plan": ap,
            # Canonical pretty URL: extensionless, NO trailing slash
            # (.html and trailing-slash forms both 308 here — SEO audit 2026-08-25)
            "detail_url": f"/{market.lower()}200/ticker/{safe}",
            "chart_url": chart_url,
            "chart_v": chart_v,
            "market": market_name,
        }
        rows.append(row)
        _v2_hit = v2_signal_for(market_name, safe)
        _v2_state = ("SIGNAL" if _v2_hit else
                     ("未納入 v2" if market_name == "HK" else "無訊號"))
        action_counts[_v2_state] = action_counts.get(_v2_state, 0) + 1
        phase_counts[phase] = phase_counts.get(phase, 0) + 1

        # Copy chart PNG + JSON (metadata) + OHLC (raw candlesticks)
        # canvas-chart.js fetches /charts/{safe}.json + derives /ohlc/{safe}_ohlc.json
        # 2026-09-22: retry up to 3x on TimeoutError (NFS mount intermittent stalls)
        def _safe_copy(src, dst):
            import time as _t
            for _i in range(3):
                try:
                    shutil.copy2(src, dst)
                    return
                except (TimeoutError, OSError) as _e:
                    if _i == 2: raise
                    _t.sleep(0.5)
        # PNG is no longer shipped (2026-10-12): the visible chart is drawn by
        # canvas-chart.js from /charts/<t>.json + /ohlc/<t>_ohlc.json. Shipping
        # 723 unused images cost ~255 MB per deploy and created a stale fallback
        # that masked fetch failures instead of surfacing them.
        src_json = src_dir / f"{safe}.json"
        if src_json.exists():
            _safe_copy(src_json, chart_dir / f"{safe}.json")
        src_ohlc = src_dir / f"{safe}_ohlc.json"
        if src_ohlc.exists():
            # canvas-chart.js reads /ohlc/{safe}_ohlc.json (derived from chartJson URL)
            ohlc_mirror = ticker_dir.parent / "ohlc"
            ohlc_mirror.mkdir(parents=True, exist_ok=True)
            _safe_copy(src_ohlc, ohlc_mirror / f"{safe}_ohlc.json")

        if (i + 1) % 20 == 0:
            print(f"  [{i+1}/{len(universe)}] {ticker} ...")

    # 2026-10-18 (2-into-1): the sort used to put SELL first, then BUY, then
    # WAIT, breaking ties on the retired strategy's win%. That decided which
    # names a visitor saw first using an engine the site no longer publishes.
    # The table is v2's now, so it sorts on v2: names with an entry first,
    # closest to entry first among those (the ones whose limit order could
    # actually fill), then everything else alphabetically.
    def row_sort_key(r):
        v2 = v2_signal_for(market_name, r["ticker"].replace(".", "_"))
        if v2:
            return (0, abs(v2.get("dist_to_entry_pct") or 0.0), r["ticker"])
        return (1, 0.0, r["ticker"])


    rows_sorted = sorted(rows, key=row_sort_key)

    rows_html = "".join(render_action_row(r) for r in rows_sorted)

    # Render detail pages AFTER sorting so prev/next links follow the same
    # ordering the hub table displays (SEO audit: tickers had zero cross-links)
    detail_pages = []
    for idx, row in enumerate(rows_sorted):
        prev_row = rows_sorted[idx - 1] if idx > 0 else None
        next_row = rows_sorted[idx + 1] if idx + 1 < len(rows_sorted) else None
        safe = row["ticker"].replace(".", "_")
        detail_html = render_detail_page(row, prev_row=prev_row, next_row=next_row)
        detail_path = ticker_dir / f"{safe}.html"
        detail_path.write_text(detail_html, encoding="utf-8")
        detail_pages.append(detail_path)

    print(f"  {len(rows)} tickers with JSON, {chart_copied} charts copied, {len(detail_pages)} detail pages")

    # Hero meta
    phase_str = f"Down {phase_counts.get('downtrend_active', 0)} · Base {phase_counts.get('base_building', 0)} · Recov {phase_counts.get('downtrend_recovery', 0)} · Up {phase_counts.get('uptrend', 0)}"
    reg_color = "var(--bear)" if phase_counts.get("downtrend_active", 0) > phase_counts.get("uptrend", 0) * 5 else "var(--amber)"

    hero_meta_extra = f'''
      <span>Universe: <b>{len(universe)} {market} names</b></span>
      <span>T-1 data: <b>{data_asof(market)}</b></span>
      <span>Updated: <b>{datetime.now().strftime('%H:%M HKT')}</b></span>
      <span>Regime: <b style="color: {reg_color};">{phase_str}</b></span>
    '''

    # Build dashboard
    title_main = f"{market} 200"
    if market == "HK":
        title_main = "港股 200"
    elif market == "JP":
        title_main = "日股 200"
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
    print(f"  v2 Action dist: {action_counts}")


def main():
    import subprocess
    # Per-market T-1 is derived from each market's own bars (data_asof), never
    # from the calendar — HK/US and JP can be a day apart, and a computed date
    # can land on a holiday the market never traded. See data_asof() docstring.
    print(f"=== build_dashboard.py · T-1 HK={data_asof('HK')} US={data_asof('US')} JP={data_asof('JP')} ===")
    build_for_market("HK")
    build_for_market("US")
    build_for_market("JP")
    # 2026-09-19: auto-run build_sitemap.py so ticker pages + equity curves
    # are picked up in sitemap every deploy (was previously a manual step).
    subprocess.run(
        [sys.executable, str(REPO / "scripts" / "build_sitemap.py")],
        check=False,  # don't fail build if sitemap errors
    )
    print("\nDone.")


if __name__ == "__main__":
    main()

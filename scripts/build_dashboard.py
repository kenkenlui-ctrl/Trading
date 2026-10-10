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

def wilson_lower(wins: float, n: float, z: float = 1.96) -> float:
    """Lower bound of the Wilson score interval, in percent.

    Mirrors build_home.wilson_lower so the index table and the homepage cards
    quote the same sample-size-aware number. Raw win% overstates small samples
    (5/5 renders as "100%"); the lower bound is what the data supports.
    """
    if not n or n <= 0:
        return 0.0
    p = max(0.0, min(1.0, wins / n))
    denom = 1 + z * z / n
    centre = p + z * z / (2 * n)
    margin = z * ((p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5)
    return max(0.0, (centre - margin) / denom) * 100


def edge_win_lower(edge: dict) -> float:
    """Wilson lower bound for a plan_edge dict (win_pct + n)."""
    n = edge.get("n", 0) or 0
    if n <= 0:
        return 0.0
    return wilson_lower(round(edge.get("win_pct", 0) / 100 * n), n)


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


def load_backtest(ticker: str) -> dict | None:
    """Load per-ticker backtest JSON written by backtest_futu.py."""
    safe = ticker.replace(".", "_")
    # 2026-09-17: HK.NNNNN format tickers have backtest stored as NNNNN_HK.json
    # Try the raw safe form first, then strip "HK." prefix.
    candidates = [f"{safe}.json"]
    if ticker.startswith("HK."):
        candidates.append(f"{ticker[3:]}.HK.json")
    elif ticker.startswith("JP."):
        candidates.append(f"{ticker[3:]}.T.json")
    for fn in candidates:
        p = BACKTEST_OUT / fn
        if p.exists():
            try:
                return json.load(open(p))
            except Exception:
                return None
    return None


# 2026-10-02: the Win% badge was "laundering" the opposite direction. A row
# showing Action=BUY could carry the win% of SELL_R1, so the number users read
# as confidence in the displayed action was actually a different setup's record.
# Fix: the badge is now keyed on the plan's own direction, and enforces the
# n >= 5 rule the methodology page documents (it was only documented, never
# enforced in the renderer — n=1..4 badges were being shown).
MIN_EDGE_N = 5

_LONG_STRATS = ("BUY_S1", "BREAK_LONG")
_SHORT_STRATS = ("SELL_R1", "BREAK_SHORT")


def strats_for_verdict(verdict: str) -> tuple[str, ...]:
    """Backtest strategies that can legitimately justify this plan's action."""
    if verdict == "BUY":
        return _LONG_STRATS
    if verdict == "SELL":
        return _SHORT_STRATS
    return ()


def pick_recent_edge(
    bt: dict | None,
    verdict: str = "",
    recent_windows=("60", "90", "30", "197"),
) -> dict | None:
    """Highest-win% strategy for THIS plan's direction, with min n=5.

    2026-10-02: previously direction-agnostic (n>=3), which is what produced
    "Action BUY + Strategy SELL_R1 · 100% win" rows. Returns None when the
    plan's own direction has no qualifying sample — the renderer then shows
    "—", which is honest.
    """
    if not bt or not bt.get("windows"):
        return None
    allowed = strats_for_verdict(verdict)
    if not allowed:
        return None
    for w in recent_windows:
        wd = bt["windows"].get(w)
        if not wd:
            continue
        for strat in allowed:
            data = wd.get(strat)
            if not data:
                continue
            n = data.get("n", 0)
            win = data.get("win_pct", 0)
            tot = data.get("total_pnl", 0)
            if n >= MIN_EDGE_N and tot > 0:
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
        str(verdict)  # silence unused warning
        # 2026-09-16: For WAIT tickers, still show backtest Win% via plan_edge
        # (set by d88 _ref fallback loop) so user sees historical performance
        # even when there's no actionable setup today.
        _plan_edge = t.get("plan_edge")
        if _plan_edge and _plan_edge.get("win_pct", 0) > 0 and _plan_edge.get("n", 0) >= 1:
            win_s = f"~{edge_win_lower(_plan_edge):.0f}%"
            strat_s = _plan_edge.get("strategy", "RANGE") + " (ref)"
        else:
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

    # 2026-09-12: data-detail for filter tooltip + (ref) tag for fallback stats.
    # Uses plan_edge (set by build_for_market with _ref fallback) so WAIT
    # tickers get a "(ref)" stat instead of "—" when backtest has any signal.
    _plan_edge = t.get("plan_edge")
    if _plan_edge:
        _wl = edge_win_lower(_plan_edge)
        _plan_detail = (
            f"~{_wl:.0f}% · n={_plan_edge['n']} (ref)" if _plan_edge.get("_ref")
            else f"{_wl:.0f}% · n={_plan_edge['n']}"
        )
    else:
        _plan_detail = "—"

    chg_cls = "text-bear" if chg and chg < 0 else ("text-bull" if chg and chg > 0 else "")
    # 2026-10-05: a row is "actionable" when it carries a real plan — an
    # actual trigger/target/stop to act on. Keying this off the backtest
    # win% reference instead (first attempt) hid 199 of 200 rows, because the
    # plan_edge reference only exists when that strategy has n>=5 history.
    # Actionable means "has a plan", not "has a backtest sample".
    _has_plan = "1" if (verdict != "WAIT" and "—" not in (trigger_s, target_s, stop_s)) else "0"
    return f"""
    <tr data-action="{verdict}" data-phase="{phase}" data-detail="{_plan_detail}" data-plan="{_has_plan}" class="{row_class}">
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
    const matchPlan = !signalOnly || row.dataset.plan === '1';
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
        {"@type": "Question", "name": "信號邊度嚟？", "acceptedAnswer": {"@type": "Answer", "text": "10-step 價格行為 framework，分 phase + S/R ladder + 4 strategies (SELL_R1 / BUY_S1 / BREAK_LONG / BREAK_SHORT)。T-1 OHLC 純 Python deterministic 計算。"}},
        {"@type": "Question", "name": "成本幾多？", "acceptedAnswer": {"@type": "Answer", "text": "已扣 Futu 免佣 + 0.25% round-trip (HK)，純 signal + backtest 結果。"}}
      ]
    }"""
    elif market == "US":
        faq_block = """,
    {
      "@type": "FAQPage",
      "mainEntity": [
        {"@type": "Question", "name": "美股 200 點樣揀？", "acceptedAnswer": {"@type": "Answer", "text": "用 5-day 平均成交額排序頭 200 隻，全部 T-1 收市 data，每日 refresh。"}},
        {"@type": "Question", "name": "信號邊度嚟？", "acceptedAnswer": {"@type": "Answer", "text": "10-step 價格行為 framework，分 phase + S/R ladder + 4 strategies。T-1 OHLC 純 Python deterministic 計算。"}},
        {"@type": "Question", "name": "成本幾多？", "acceptedAnswer": {"@type": "Answer", "text": "已扣 0.05% round-trip (US)，Futu 免佣。"}}
      ]
    }"""
    elif market == "JP":
        faq_block = """,
    {
      "@type": "FAQPage",
      "mainEntity": [
        {"@type": "Question", "name": "日股 200 點樣揀？", "acceptedAnswer": {"@type": "Answer", "text": "由 Topix Core30 + Large70 開始，yfinance verify 流通量，200 隻全部 T-1 收市 data。"}},
        {"@type": "Question", "name": "信號邊度嚟？", "acceptedAnswer": {"@type": "Answer", "text": "10-step 價格行為 framework。T-1 OHLC 純 Python deterministic 計算。"}},
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
    <label>Action</label>
    <div style="display: flex; gap: 6px;">
      <button type="button" class="filter-chip active" data-action="">All</button>
      <button type="button" class="filter-chip" data-action="BUY">🟢 BUY</button>
      <button type="button" class="filter-chip" data-action="SELL">🔴 SELL</button>
      <button type="button" class="filter-chip" data-action="WAIT">⚪ WAIT</button>
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
    <button type="button" class="filter-chip active" id="signalOnly" data-signal="1" title="Hide rows with no action plan (no trigger / target / stop)">⚡ 只看有訊號</button>
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
    edge = t.get("edge")
    chart_url = t["chart_url"]
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

    # SEO/GEO: canonical URL (Cloudflare Pages pretty URL = extensionless, no
    # trailing slash — verified live 2026-08-25: both .html and trailing-slash
    # variants 308 to this form).
    canon_url = f"https://www.win9you.com/{market.lower()}200/ticker/{safe}"

    # SEO/GEO: description carries the concrete verdict + levels so search
    # engines and AI answer engines can quote a self-contained answer.
    desc_parts = [f"{ticker} {name} 即日鮮 Action Plan", f"Phase: {phase_label}", f"{verdict}"]
    if verdict != "WAIT" and trigger is not None and target is not None and stop is not None:
        desc_parts.append(f"trigger {fmt(trigger)} / target {fmt(target)} / stop {fmt(stop)}")
    desc_parts.append(f"T-{1}收市數據 · 教育用途非投資建議")
    meta_desc = " · ".join(desc_parts)

    t1_iso = t_minus_1().isoformat()
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
      "headline": "{ticker} {name} 即日鮮 Action Plan ({verdict})",
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

    edge_note_html = (
        f'''<p class="text-dim" style="font-size: var(--text-xs); margin-top: var(--sp-3); max-width: 640px;">
      註：Backtest win% 係「{edge["strategy"]}」策略喺過去 {edge["window"]} 日嘅歷史表現（edge），
      唔等於今日 {verdict} 嘅預測成功率；今日決策以下方 Action Plan 為準。
    </p>''' if edge else '')

    # 2026-10-06: the live v2 verdict for THIS ticker, so a detail page is not
    # silently a v1 page. HK gets an explicit "not covered by v2" notice.
    v2_status = render_v2_status_for_ticker(market, safe)

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
      <span>Phase: <b style="color: {phase_color};">{phase_label}</b></span>
      <span>Action: <b style="color: {verdict_color};">{verdict}</b></span>
      <span>Strategy: <b>{edge["strategy"] if edge else "—"}</b></span>
      <span>Reliability: <b style="color: {rel_color};">{reliability}</b></span>
    </div>
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
             data-chart-json="/{market.lower()}200/charts/{safe}.json"
             data-png="{chart_url}"
             style="position:relative;min-height:540px;">
          <noscript>
            <img src="{chart_url}?v={t_minus_1().isoformat()}" alt="{ticker} daily K" loading="eager">
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
          <span class="value" style="color: {verdict_color};">{verdict}</span>
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
<script src="/static/canvas-chart.js?v=2026-09-18b" defer></script>
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
        title_lede = "港股 200 by 5-day 平均成交額排序。即日鮮信號 — 200 隻個股一表答晒：邊隻做 BUY/SELL/WAIT、trigger/target/stop 喺邊、win% 60d backtest 點。T-1 收市 data。已扣 HK 0.25% round-trip 成本。"
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
        title_lede = "日股 200 (Topix Core30 + Large70) by 5-day 平均成交額排序。即日鮮信號 — 200 隻個股一表答晒：邊隻做 BUY/SELL/WAIT、trigger/target/stop 喺邊、win% 60d backtest 點。T-1 收市 data。已扣 0.05% round-trip 成本。"
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
        title_lede = "美股 200 by 5-day 平均成交額排序。即日鮮信號 — 200 隻個股一表答晒：邊隻做 BUY/SELL/WAIT、trigger/target/stop 喺邊、win% 60d backtest 點。T-1 收市 data。已扣 US 0.05% round-trip 成本。"

    if not universe:
        print(f"⚠ {market}: empty universe, skip")
        return

    print(f"\n=== {market}: building {len(universe)} tickers ===")

    # Phase counts for hero
    phase_counts = {}
    action_counts = {"BUY": 0, "SELL": 0, "WAIT": 0}
    rows = []
    chart_copied = 0

    for i, ticker in enumerate(universe):
        tj = load_ticker_json(ticker, market_name)
        bt = load_backtest(ticker)
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

        edge = pick_recent_edge(bt, verdict)
        if not edge:
            # fallback to backtest_futu best_strategy — but skip if it's a
            # placeholder (name='—', n=0) since 0% is meaningless, and skip it
            # if it belongs to the opposite direction (2026-10-02) or has a
            # sample below the documented n >= 5.
            if bt and bt.get("best_strategy"):
                bs = bt["best_strategy"]
                if (
                    bs.get("name")
                    and bs["name"] in strats_for_verdict(verdict)
                    and bs.get("n_trades", 0) >= MIN_EDGE_N
                ):
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
            # Canonical pretty URL: extensionless, NO trailing slash
            # (.html and trailing-slash forms both 308 here — SEO audit 2026-08-25)
            "detail_url": f"/{market.lower()}200/ticker/{safe}",
            "chart_url": chart_url,
            "market": market_name,
        }
        rows.append(row)
        action_counts[verdict] = action_counts.get(verdict, 0) + 1
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
        src_png = src_dir / f"{safe}.png"
        if src_png.exists():
            _safe_copy(src_png, chart_dir / f"{safe}.png")
            chart_copied += 1
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

    # Sort rows: actionable first, then by win%, then by ticker
    def row_sort_key(r):
        act = r.get("action_plan", {}).get("verdict", "WAIT")
        act_priority = {"SELL": 0, "BUY": 1, "WAIT": 2}.get(act, 3)
        edge = r.get("edge")
        win = edge["win_pct"] if edge else 0
        return (act_priority, -win, r["ticker"])

    rows_sorted = sorted(rows, key=row_sort_key)

    # 2026-09-12 (d88 ref fallback): patch each row to use highest-n strategy
    # stats when plan strategy has 0 trades. The index build handles its own
    # version of this; here we just inject the (ref) data into the row dict
    # so render_action_row's `t.get("plan_edge")` finds it.
    for _row in rows_sorted:
        if _row.get("plan_edge") is not None:
            continue
        # 2026-10-02: the (ref) fallback is now direction-locked and n>=5.
        # It previously scanned every strategy and accepted n>=1, which is how
        # 56 rows ended up showing the opposite direction's record and 20 rows
        # showed samples of 1-4 trades while the methodology page claimed n>=5.
        _verdict = _row.get("action_plan", {}).get("verdict", "WAIT")
        _allowed = strats_for_verdict(_verdict)
        if not _allowed:
            continue
        try:
            _bt = load_backtest(_row["ticker"])
        except Exception:
            _bt = None
        if not _bt:
            continue
        _ref_n, _ref_data = 0, None
        for _w in ("60", "90", "30", "197"):
            _wd = _bt.get("windows", {}).get(_w)
            if not _wd:
                continue
            for _sname in _allowed:
                _sd = _wd.get(_sname)
                if not isinstance(_sd, dict) or _sd.get("n", 0) <= _ref_n:
                    continue
                if _sd.get("total_pnl", 0) <= 0:
                    continue
                _ref_n = _sd["n"]
                _ref_data = dict(_sd)
                _ref_data["strategy"] = _sname  # so render can use edge["strategy"]
                _ref_data["window"] = _w       # so render can use edge["window"]
                _ref_data["n"] = _sd["n"]      # alias for n_trades (edge uses "n")
        if _ref_data and _ref_n >= MIN_EDGE_N:
            _row["plan_edge"] = dict(_ref_data)
            _row["plan_edge"]["_weak"] = True
            _row["plan_edge"]["_ref"] = True
            # 2026-09-16: also inject into `edge` so the Win% 90D column renders
            # the (ref) stat instead of "—" for WAIT/no-plan-strategy tickers.
            # Without this, plan_edge is only used for tooltip data-detail but
            # the visible column stays empty — which is why user kept seeing
            # "Win% 90D blank" after every deploy.
            if not _row.get("edge"):
                _row["edge"] = _row["plan_edge"]

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
    print(f"  Action dist: {action_counts}")


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

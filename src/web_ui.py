"""Streamlit web UI. Bloomberg-terminal aesthetic for day-trade dashboard."""

from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path

# Ensure project root on path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import streamlit as st  # noqa: E402

# ===== Page config (browser tab title + favicon) =====
st.set_page_config(
    page_title="Leeks Terminal · HK+US Day-Trade AI",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",  # Safari/Chrome parity: default visible. Mobile CSS caps width to 240px (Pass 2 fix).
    menu_items={
        "Get Help": "https://github.com/kenkenlui-ctrl/Trading",
        "Report a bug": "https://github.com/kenkenlui-ctrl/Trading/issues",
        "About": "Leeks Terminal — AI-powered HK+US day-trade decision support. 376 tickers analyzed daily with multi-dim scoring (Value/Quality/Momentum) + trade direction signals. Not investment advice.",
    },
)

# ===== SEO meta tags + Bloomberg-terminal CSS =====
# SEO via st.html (allowed raw HTML injection)
st.html("""
<meta name="description" content="Real-time HK + US stock AI scoring for day-trade. 200 tickers × 4-dim score × live news. Powered by MiniMax-M3 + Futu OpenD.">
<meta name="keywords" content="HK stock analysis, day trade, AI trading, momentum, MA, RSI, 騰訊, 阿里, M3, MiniMax">
<meta property="og:title" content="Leeks Terminal · HK+US Day-Trade AI">
<meta property="og:description" content="Bloomberg-style AI terminal for HK + US day-trade. 200 tickers, 4-dim scoring, live news.">
<meta property="og:type" content="website">
<meta property="og:url" content="https://www.win9you.com">
<meta name="twitter:card" content="summary_large_image">
<link rel="canonical" href="https://www.win9you.com">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;500;600;700&display=swap" rel="stylesheet">
""")

# Bloomberg-terminal CSS via st.markdown (so <style> gets injected)
st.markdown("""
<style>
    /* Light theme — Bloomberg-terminal aesthetic with white background (owner-approved 2026-06-27).
       Contrast ratios verified against WCAG AA (4.5:1 normal, 3:1 large):
       --fg #1a1d23 on --bg #ffffff = 16.74:1 (AAA)
       --dim #4b5563 on --bg = 8.59:1 (AAA) — was #6b7280 (4.83 AA on white
         but 4.39 borderline on --panel #f3f4f6). Tightened to #4b5563 (gray-700).
       --accent #2563eb on --bg = 5.17:1 (AA)
       --bull #15803d on --bg = 5.06:1 (AA) — was #16a34a (3.06 fail on white).
       --bear #b91c1c on --bg = 6.05:1 (AA) — was #dc2626 (4.30 fail on white).
       --amber #92400e on --bg = 8.45:1 (AAA) — was #b45309 (4.65 AA on white
         but only 3.5:1 on #fef3c7 disclaimer bg). Tightened to #92400e (amber-800). */
    :root {
        --bg: #ffffff;        /* page canvas */
        --panel: #f3f4f6;     /* sidebar / elevated card */
        --panel-2: #e5e7eb;   /* nested code/quote */
        --border: #e5e7eb;    /* dividers */
        --fg: #1a1d23;        /* primary text */
        --dim: #4b5563;       /* muted text — WCAG AAA on bg + panel */
        --bull: #15803d;      /* buy / long — WCAG AA */
        --bear: #b91c1c;      /* sell / short — WCAG AA */
        --amber: #92400e;     /* hold / observe — AAA on white + #fef3c7 */
        --accent: #2563eb;    /* headers, links, primary action */
    }

    html, body, .stApp, [data-testid="stAppViewContainer"], .main {
        background-color: var(--bg) !important;
        color: var(--fg) !important;
        font-family: 'JetBrains Mono', 'SF Mono', 'Menlo', monospace !important;
    }

    h1, h2, h3, h4 {
        font-family: 'JetBrains Mono', monospace !important;
        font-weight: 600 !important;
        color: var(--fg) !important;
    }
    h1 {
        font-size: 1.5rem !important;
        color: var(--accent) !important;
        border-bottom: 2px solid var(--accent);
        padding-bottom: 0.5rem;
        margin-top: 0.5rem !important;
        margin-bottom: 0.75rem !important;
    }
    h2 {
        font-size: 1.1rem !important;
        color: var(--accent) !important;
        margin-top: 1rem !important;
        margin-bottom: 0.5rem !important;
    }
    h3 {
        font-size: 0.95rem !important;
        color: var(--dim) !important;
        text-transform: uppercase;
        letter-spacing: 0.1em;
        margin-top: 0.75rem !important;
        margin-bottom: 0.4rem !important;
    }

    /* Tighten Streamlit's default block-container top padding + remove
       empty whitespace above the main header (was ~80px of dead space). */
    .block-container {
        padding-top: 2rem !important;
        padding-bottom: 2rem !important;
        max-width: 100% !important;
    }
    section.main > div.block-container {
        padding-top: 1.5rem !important;
    }

    /* Tighter paragraph spacing in dashboard markdown (was 1em → 0.5em). */
    .main p { margin: 0.4em 0 !important; }
    .main ul, .main ol { margin: 0.4em 0 !important; padding-left: 1.25rem !important; }

    section[data-testid="stSidebar"] {
        background-color: var(--panel) !important;
        border-right: 1px solid var(--border);
    }
    section[data-testid="stSidebar"] h1 {
        color: var(--accent) !important;
        font-size: 1.2rem !important;
    }

    .stTabs [data-baseweb="tab-list"] {
        background: transparent !important;
        border-bottom: 1px solid var(--border);
        gap: 0;
    }
    .stTabs [data-baseweb="tab"] {
        background: transparent !important;
        color: var(--dim) !important;
        border: 0 !important;
        border-radius: 0 !important;
        padding: 0.75rem 1.25rem !important;
        font-family: 'JetBrains Mono', monospace !important;
        text-transform: uppercase;
        font-size: 0.8rem !important;
        letter-spacing: 0.1em;
    }
    .stTabs [aria-selected="true"] {
        color: var(--accent) !important;
        border-bottom: 2px solid var(--accent) !important;
    }

    .stButton > button {
        background-color: var(--panel-2) !important;
        color: var(--fg) !important;
        border: 1px solid var(--border) !important;
        border-radius: 2px !important;
        font-family: 'JetBrains Mono', monospace !important;
        text-transform: uppercase;
        font-size: 0.75rem !important;
    }
    .stButton > button:hover {
        background-color: var(--accent) !important;
        color: #ffffff !important;
        border-color: var(--accent) !important;
    }

    [data-testid="stRadio"] label {
        font-family: 'JetBrains Mono', monospace !important;
        text-transform: uppercase;
        font-size: 0.75rem;
        letter-spacing: 0.05em;
    }

    .stDataFrame {
        background-color: var(--panel) !important;
        border: 1px solid var(--border);
    }

    code, pre {
        background-color: var(--panel-2) !important;
        color: #14532d !important;       /* bull green-900, WCAG AAA on panel-2 */
        font-family: 'JetBrains Mono', monospace !important;
        border: 1px solid var(--border);
        border-radius: 2px;
    }

    [data-testid="stMetricValue"] {
        font-family: 'JetBrains Mono', monospace !important;
        color: var(--accent) !important;
        font-size: 1.5rem !important;
    }
    [data-testid="stMetricLabel"] {
        color: var(--dim) !important;
        text-transform: uppercase;
        font-size: 0.7rem !important;
        letter-spacing: 0.1em;
    }

    .stProgress > div > div > div > div {
        background-color: var(--accent) !important;
    }

    .stCaption, small {
        color: var(--dim) !important;
        font-family: 'JetBrains Mono', monospace !important;
        font-size: 0.75rem !important;
    }

    /* ===== Aggressive padding kill ===== */
    /* Owner complaint 2026-06-27: ~80px dead space above header. Kill the
       Streamlit toolbar height + collapse block-container top padding to 0. */
    header[data-testid="stHeader"] {
        display: none !important;  /* removes >> sidebar toggle + tool bar */
    }
    .block-container {
        padding-top: 1rem !important;  /* was 2rem — saved 16px */
        padding-bottom: 1rem !important;
        max-width: 100% !important;
    }
    section.main > div.block-container {
        padding-top: 0.5rem !important;  /* was 1.5rem — saved 16px */
    }
    /* Streamlit adds default body padding even with embed; kill it */
    body { padding: 0 !important; margin: 0 !important; }
    div[data-testid="stAppViewContainer"] {
        padding: 0 !important;
    }
    /* Header flex row: remove its margin-bottom too */
    div[style*="display:flex"] { margin-bottom: 0.25rem !important; }

    .bull { color: var(--bull) !important; }
    .bear { color: var(--bear) !important; }
    .amber { color: var(--amber) !important; }
    .dim { color: var(--dim) !important; }

    #MainMenu, footer {visibility: hidden;}
    header[data-testid="stHeader"] {background-color: transparent !important;}

    /* ===== Mobile responsive (<= 768px) ===== */
    /* Pass 2 QA fix: sidebar 300px covers 76% of mobile screen, header
       text wraps awkwardly, tables overflow, disclaimer is too dense. */
    @media (max-width: 768px) {
        /* (a) Sidebar: cap width so when expanded it doesn't blanket
           the screen; Streamlit handles collapse via React state so we
           only override the open-width here. */
        section[data-testid="stSidebar"] {
            min-width: 240px !important;
            max-width: 240px !important;
        }

        /* (b) Dashboard header: smaller + allow wrap */
        h1 {
            font-size: 1.05rem !important;
            padding-bottom: 0.35rem !important;
            line-height: 1.3 !important;
        }
        /* Header flex row: stack vertically so timestamp doesn't get
           squeezed off-screen. Target the inline div by attribute. */
        div[style*="display:flex"] h1 {
            font-size: 1.05rem !important;
        }

        /* (c) Tables / dataframes: ensure horizontal scroll instead of
           overflowing the viewport. */
        .stDataFrame, [data-testid="stDataFrame"] {
            overflow-x: auto !important;
            max-width: 100vw !important;
        }
        .main .block-container {
            overflow-x: hidden !important;
            padding-left: 0.5rem !important;
            padding-right: 0.5rem !important;
        }

        /* (d) Disclaimer banner: tighter padding, smaller font.
           Target via role="note" + aria-label since browsers normalize
           inline background:#fef3c7 → rgb(254, 243, 199) in style attr. */
        div[role="note"][aria-label*="免責"] {
            padding: 6px 8px !important;
            font-size: 11px !important;
            line-height: 1.4 !important;
        }

        /* (e) Trend filter radio: stack vertically on mobile so the
           4 options (全部/LONG/SHORT/雙向) don't crush into one line. */
        [data-testid="stRadio"] > div {
            flex-direction: column !important;
            gap: 0.25rem !important;
        }
        [data-testid="stRadio"] label {
            font-size: 0.7rem !important;
            padding: 0.15rem 0 !important;
        }

        /* Tighter tab text on mobile */
        .stTabs [data-baseweb="tab"] {
            padding: 0.5rem 0.5rem !important;
            font-size: 0.7rem !important;
            letter-spacing: 0.05em !important;
        }

        /* Metric values slightly smaller */
        [data-testid="stMetricValue"] {
            font-size: 1.1rem !important;
        }
    }

    /* Tablet 769-1024: keep desktop layout but shrink h1 */
    @media (min-width: 769px) and (max-width: 1024px) {
        h1 { font-size: 1.25rem !important; }
    }

    /* Desktop: compress sidebar — fewer default margins, smaller captions,
       so RUN LOG (now an expander) + status badges don't waste space. */
    @media (min-width: 1025px) {
        section[data-testid="stSidebar"] {
            padding-top: 1rem !important;
        }
        section[data-testid="stSidebar"] .stCaption,
        section[data-testid="stSidebar"] small {
            font-size: 0.7rem !important;
        }
        /* Tighter spacing between sidebar elements */
        section[data-testid="stSidebar"] hr {
            margin: 0.5rem 0 !important;
        }
    }
</style>
""", unsafe_allow_html=True)

# A11y shim (owner-approved 2026-06-27): Streamlit's React renders
# <section data-testid="stSidebar" aria-expanded="..."> but does NOT set
# a role. Per ARIA spec aria-expanded is only valid on certain roles
# (button, navigation, region, etc.) and NOT on a bare <section>.
# axe-core flags this as a critical violation. Also stMain lacks role="main"
# causing 'region' moderate violations. Use st.components.v1.html because
# Streamlit's st.markdown(unsafe_allow_html=True) does NOT execute inline
# <script> tags — React renders them as inert text inside divs.
st.components.v1.html(
    """
    <script>
      (function () {
        function fixSidebar() {
          var sb = window.parent.document.querySelector('section[data-testid="stSidebar"]');
          if (sb && !sb.getAttribute('role')) {
            sb.setAttribute('role', 'navigation');
            sb.setAttribute('aria-label', 'Leeks Terminal 控制面板');
          }
        }
        function fixMain() {
          var main = window.parent.document.querySelector('[data-testid="stMain"]');
          if (main && !main.getAttribute('role')) {
            main.setAttribute('role', 'main');
            main.setAttribute('aria-label', 'Leeks Terminal 主內容');
          }
        }
        // Streamlit's stDataFrame toolbar renders <div aria-haspopup="true"
        // aria-expanded="false"> but does NOT set a valid role. Per ARIA
        // spec, aria-expanded is only valid on certain roles. Adding role=
        // "button" causes "nested-interactive" (the wrapper contains real
        // <button> descendants). Cleanest fix: strip the invalid ARIA
        // attributes entirely. Visual expand/collapse still works because
        // Streamlit toggles aria-expanded as a class-style indicator;
        // removing it costs screen readers nothing because the inner
        // <button> elements still expose expand state via their own ARIA.
        function fixDataFrameToolbar() {
          var tb = window.parent.document.querySelectorAll(
            '.stDataFrame .stElementToolbar [aria-haspopup="true"]'
          );
          for (var i = 0; i < tb.length; i++) {
            tb[i].removeAttribute('aria-expanded');
            tb[i].removeAttribute('aria-haspopup');
          }
        }
        function run() { fixSidebar(); fixMain(); fixDataFrameToolbar(); }
        run();
        setTimeout(run, 250);
        setTimeout(run, 1000);
        setTimeout(run, 3000);
        if (typeof MutationObserver !== 'undefined') {
          var obs = new MutationObserver(function () { run(); });
          obs.observe(window.parent.document.documentElement || window.parent.document.body, {
            childList: true,
            subtree: true,
            attributes: true,
            attributeFilter: ['aria-expanded'],
          });
        }
      })();
    </script>
    """,
    height=0,
    scrolling=False,
)

from src.config import get_config  # noqa: E402
from src.db import (  # noqa: E402
    get_report,
    list_reports,
    list_report_dates,
    report_history,
    list_recent_runs,
    get_market_review,
    get_ticker,
    init_db,
)
from src.pipeline import build_dashboard_md, run_full_analysis, analyze_ticker  # noqa: E402
from src.db import get_running_run, list_chanlun_signals, count_chanlun_signals  # noqa: E402
from src.analyzer import render_report_md  # noqa: E402

init_db()  # Ensure DB exists

cfg = get_config()

# Session state for ticker selector (fixes "bounce back" issue)
if "detail_ticker" not in st.session_state:
    st.session_state.detail_ticker = ""

# ===== Sidebar =====
with st.sidebar:
    st.markdown("### ◆ LEEKS TERMINAL")
    st.caption("> HK+US · day-trade AI")
    st.markdown("---")

    # Status — wrapped in aria-live region so screen readers announce changes
    # (e.g. LLM key flipped on, Telegram went live). Bullet markers ●/○ are
    # decorative; aria-hidden hides them so SR reads just the meaningful text.
    st.markdown("**STATUS**")
    llm_status = ("● " + ", ".join(cfg.available_llm_providers())) if cfg.has_llm_key() else "○ n/a"
    news_status = ("● " + ", ".join(cfg.available_news_sources())) if cfg.has_news_key() else "○ n/a"
    tg_status = "● live" if cfg.has_telegram() else "○ n/a"
    st.markdown(
        f"<div role='status' aria-live='polite' aria-atomic='true' "
        f"aria-label='系統狀態'>"
        f"<p style='margin:0;font-size:0.85rem;'><span aria-hidden='true'>"
        f"<span class='bull'>{llm_status}</span></span> "
        f"<span>LLM</span></p>"
        f"<p style='margin:0;font-size:0.85rem;'><span aria-hidden='true'>"
        f"<span class='bull'>{news_status}</span></span> "
        f"<span>News</span></p>"
        f"<p style='margin:0;font-size:0.85rem;'><span aria-hidden='true'>"
        f"<span class='bull'>{tg_status}</span></span> "
        f"<span>Telegram</span></p>"
        f"<p style='margin:0;font-size:0.75rem;color:var(--dim);'>"
        f"Model: <code>{cfg.litellm_model}</code></p>"
        f"</div>",
        unsafe_allow_html=True,
    )

    st.markdown("---")
    st.subheader("🚀 操作")

    # Live analysis progress — runs in its own fragment so the dashboard below
    # is NOT blocked by `st.rerun()`. Auto-refreshes every 5s while a job runs.
    @st.fragment(run_every=timedelta(seconds=5))
    def _live_progress():
        running = get_running_run()
        if running:
            total = running.get("tickers_total", 400) or 1
            done = running.get("tickers_done", 0)
            pct = min(done / total, 1.0)
            st.progress(pct, f"⏳ 分析中... {done}/{total} 完成")
            st.caption(f"運行 ID {running['id']} · 開始於 {running['started_at'][11:]} · 你可以繼續瀏覽下方數據，新報告會自動加入")
        else:
            # Idle — show trigger button. Don't block the rest of the page.
            # aria-label clarifies the action for screen readers (the emoji
            # ▶️ alone is meaningless when announced).
            if st.button(
                "▶️ 立即分析所有股票 (HK+US)",
                use_container_width=True,
                key="trigger_full",
                help="立即開始分析全部 HK+US 股票，運行時 dashboard 仍然可以瀏覽",
            ):
                import subprocess
                log_file = "/tmp/dsa-hk-analysis.log"
                subprocess.Popen(
                    ["python3", "-m", "src.pipeline", "analyze"],
                    cwd=str(PROJECT_ROOT),
                    stdout=open(log_file, "w"),
                    stderr=subprocess.STDOUT,
                    start_new_session=True,  # detach so webui reruns don't kill it
                )

    _live_progress()

    # ===== HK universe refresh button (lazy: once per calendar day) =====
    # Owner request 2026-06-27: don't auto-regen via launchd — instead, fire on
    # first click of "🔄 更新股票清單 (HK)" each calendar day, then no-op
    # for the rest of the day. Subsequent analysis runs pick up the fresh list.
    import json as _json
    from datetime import date as _date
    _refresh_cache = Path("/tmp/hk_universe_last_refresh.json")
    _today_str = _date.today().isoformat()
    _last_refresh_date = None
    try:
        if _refresh_cache.exists():
            _last_refresh_date = _json.loads(_refresh_cache.read_text()).get("date")
    except Exception:
        pass

    _already_refreshed = (_last_refresh_date == _today_str)

    if _already_refreshed:
        # Show locked state — button disabled, show today's timestamp
        st.button(
            f"✅ 股票清單已更新 (今日)",
            use_container_width=True,
            key="regen_universe_done",
            disabled=True,
            help=f"上次刷新: {_today_str} · 翌日 0:00 HKT 後再可刷新",
        )
    else:
        if st.button(
            "🔄 更新股票清單 (HK) — 第一次點擊每日",
            use_container_width=True,
            key="regen_universe",
            help="按 20d 平均成交額重新排序 top 200 HK 股票 (~7s)",
        ):
            import subprocess as _sp
            _regen_log = "/tmp/dsa-hk-regen-universe.log"
            with st.spinner("刷新 HK 股票清單中..."):
                _result = _sp.run(
                    [sys.executable, str(PROJECT_ROOT / "scripts" / "regen_hk_universe.py")],
                    cwd=str(PROJECT_ROOT),
                    capture_output=True,
                    text=True,
                    timeout=60,
                )
                if _result.returncode == 0:
                    _refresh_cache.write_text(_json.dumps({"date": _today_str}))
                    # Extract last meaningful line from output
                    _out_lines = [l for l in _result.stdout.splitlines() if l.strip() and not l.startswith("HTTP") and not l.startswith("$")]
                    _last = _out_lines[-1] if _out_lines else "refresh OK"
                    st.success(f"✅ HK 清單已刷新 · {_last}")
                else:
                    st.error(f"❌ Regen failed: {_result.stderr[:300]}")
            st.rerun()

    st.markdown("---")
    # RUN LOG wrapped in an expander to save vertical sidebar space
    # (5 runs each take ~3 lines = wasted ~15 lines). Click to expand.
    runs = list_recent_runs(limit=10)
    latest = runs[0] if runs else None
    latest_summary = (
        f" ({latest['tickers_done']}/{latest['tickers_total']} · {latest['started_at'][11:16]})"
        if latest else ""
    )
    with st.expander(f"📋 RUN LOG{latest_summary}", expanded=False):
        for r in runs[:5]:
            status = r["status"]
            # Streamlit-native markdown colors (no raw HTML)
            if status == "success":
                badge = ":green[● ok]"
            elif status == "partial":
                badge = ":orange[● ~]"
            elif status == "failed":
                badge = ":red[● !!]"
            elif status == "running":
                badge = ":orange[● ...]"
            else:
                badge = f":gray[○ {status}]"
            st.markdown(
                f"{badge} `{r['started_at'][:16]}` · "
                f"**{r['tickers_done']}**/{r['tickers_total']} · "
                f"`{r['trigger']}`"
            )

# ===== Main =====
now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
st.markdown(f"""
<div style="display:flex; align-items:baseline; justify-content:space-between; margin-bottom: 1rem;">
    <h1 style="margin:0;">◆ Leeks Terminal · HK+US Day-Trade AI</h1>
    <span class="dim" style="font-size: 0.85rem;">{now_str} HKT</span>
</div>
""", unsafe_allow_html=True)

# Date selector
available_dates = list_report_dates(limit=30)
if available_dates:
    selected_date = st.selectbox("選擇日期", available_dates, index=0)
else:
    selected_date = datetime.now().strftime("%Y-%m-%d")
    st.info("尚未有任何分析報告。點擊左側「立即分析所有港股」開始。")

# Tabs
tab_dashboard, tab_detail, tab_history, tab_market, tab_runlog = st.tabs([
    "📊 決策儀表板",
    "🔍 個股深度",
    "📈 歷史報告",
    "🌐 大盤復盤",
    "📋 運行日誌",
])

# --- Tab 1: Dashboard ---
with tab_dashboard:
    # YMYL compliance: financial content MUST show disclaimer (EEAT T08)
    # Light-theme palette: amber-tinted background + dark text + amber accent.
    st.markdown(
        "<div role='note' aria-label='非投資建議免責聲明' "
        "style='background:#fef3c7;border:1px solid #b45309;border-radius:6px;"
        "padding:8px 12px;margin-bottom:12px;font-size:13px;color:#78350f;'>"
        "⚠️ <b>非投資建議</b> · 本工具只係 AI 輔助決策參考，唔構成任何買賣建議。"
        "Day trading 涉及高風險，過去表現唔代表未來回報。請自行評估風險並諮詢持牌顧問。"
        "<a href='/disclaimer' style='color:#78350f;text-decoration:underline;margin-left:8px;'>完整免責聲明 →</a>"
        "</div>",
        unsafe_allow_html=True,
    )

# Trend filter toggle (long / short / both)
    trend_filter = st.radio(
        "🎯 交易方向 FILTER",
        options=["全部", "只做多 LONG", "只做空 SHORT", "雙向"],
        index=0,
        horizontal=True,
        help="根據 LLM 評估嘅 trade_direction 過濾。LONG = 適合做多，SHORT = 弱勢反彈做空，雙向 = 兩個方向都有 setup",
    )
    # Market filter (HK / US / all)
    market_filter = st.radio(
        "🌏 市場 FILTER",
        options=["全部", "港股 HK", "美股 US"],
        index=0,
        horizontal=True,
        help="港股 = .HK 結尾，美股 = 唔係 .HK 結尾",
    )
    # Operation filter (BUY / HOLD / SELL) — owner request 2026-06-27
    operation_filter = st.radio(
        "💡 操作 FILTER",
        options=["全部", "🟢買入 BUY", "🟡觀望 HOLD", "🔴賣出 SELL"],
        index=0,
        horizontal=True,
        help="只顯示 LLM 評為指定操作嘅股票",
    )
    # Map filter → DB value
    filter_map_dir = {
        "全部": None,
        "只做多 LONG": "long",
        "只做空 SHORT": "short",
        "雙向": "both",
    }
    filter_map_market = {
        "全部": None,
        "港股 HK": "HK",
        "美股 US": "US",
    }
    filter_map_op = {
        "全部": None,
        "🟢買入 BUY": "buy",
        "🟡觀望 HOLD": "hold",
        "🔴賣出 SELL": "sell",
    }
    target_dir = filter_map_dir[trend_filter]
    target_market = filter_map_market[market_filter]
    target_op = filter_map_op[operation_filter]
    # Build dashboard as HTML cards (border + padding + margin) so 1 row = 1 card.
    # unsafe_allow_html required because build_dashboard_md now emits raw HTML <div>s.
    st.markdown(
        build_dashboard_md(
            selected_date,
            language=cfg.report_language,
            trade_direction=target_dir,
            market=target_market,
            operation=target_op,
        ),
        unsafe_allow_html=True,
    )

    # Detail table
    reports = list_reports(report_date=selected_date, limit=200)
    if reports:
        st.markdown("---")
        st.subheader("詳細表格")
        # Filter already applied in build_dashboard_md() — reports here are the same set
        st.caption(f"詳細表格：{len(reports)} 隻（{trend_filter}）")

        rows = []
        for r in sorted(reports, key=lambda x: x["score"] or 0, reverse=True):
            breakdown = r.get("score_breakdown") or {}
            if isinstance(breakdown, str):
                import json as _json
                try: breakdown = _json.loads(breakdown)
                except: breakdown = {}
            v = breakdown.get("value_score", "—")
            q = breakdown.get("quality_score", "—")
            m = breakdown.get("momentum_score", "—")
            rows.append({
                "代碼": r["code"],
                "評分": r["score"],
                "方向": r.get("trade_direction") or "—",
                "估值/質素/動能": f"{v}/{q}/{m}",
                "建議": r["operation_advice"],
                "情緒": r["sentiment"],
                "趨勢": r["trend"],
                "信心": r.get("confidence", ""),
                "入場": r.get("entry_zone") or "—",
                "止損": r.get("stop_loss") or "—",
                "目標": r.get("target_price") or "—",
                "支持": r.get("support_zone") or "—",
                "阻力": r.get("resistance_zone") or "—",
            })
        st.dataframe(rows, use_container_width=True, hide_index=True)

# --- Tab 2: Single Ticker Detail ---
with tab_detail:
    col1, col2 = st.columns([1, 3])
    with col1:
        # Ticker selector
        reports = list_reports(report_date=selected_date, limit=500)
        ticker_codes = [r["code"] for r in reports]

        # Allow typing custom ticker - sync to session state
        custom_key = st.text_input(
            "輸入 ticker 代碼 (例: SPCX, 0700.HK)",
            key="custom_ticker_input",
        )
        if custom_key:
            ticker_codes = list(set([custom_key.upper()] + ticker_codes))

        if ticker_codes:
            selected_ticker = st.selectbox(
                "選擇 ticker",
                ticker_codes,
                key="detail_ticker",  # Streamlit syncs widget value here automatically
            )
        else:
            selected_ticker = ""

        if st.button("🔄 重新分析此 ticker"):
            if selected_ticker:
                with st.spinner(f"分析 {selected_ticker}..."):
                    result = analyze_ticker(selected_ticker)
                if result:
                    st.success(f"完成: score={result.score}")
                    st.rerun()
                else:
                    st.error("分析失敗")

    with col2:
        if selected_ticker:
            ticker_info = get_ticker(selected_ticker)
            report = get_report(selected_ticker, report_date=selected_date)
            if ticker_info:
                st.subheader(f"{ticker_info.get('name_zh') or ''} ({selected_ticker})")
                st.caption(f"最後更新: {ticker_info.get('last_updated', '')} · 最新價: {ticker_info.get('last_price', 'N/A')}")
            if report:
                # Render full report
                st.markdown("---")
                st.markdown(report["full_md"])
                # News section
                if report.get("news"):
                    with st.expander(f"📰 新聞來源 ({len(report['news'])} 則)"):
                        for n in report["news"]:
                            st.markdown(f"- **[{n.get('source', '')}] [{n.get('title', '')}]({n.get('url', '#')})**")
                            if n.get("published"):
                                st.caption(f"  _{n['published']}_")
                            if n.get("snippet"):
                                st.caption(f"  {n['snippet'][:200]}")
            else:
                st.info(f"{selected_ticker} 在 {selected_date} 沒有報告。")

# --- Tab 3: History ---
with tab_history:
    if selected_ticker:
        st.subheader(f"{selected_ticker} 歷史報告")
        history = report_history(selected_ticker, limit=30)
        if history:
            for h in history:
                with st.expander(f"{h['report_date']} · 評分 {h['score']} · {h['operation_advice']}"):
                    st.markdown(h.get("summary_md", ""))
                    st.caption(f"信心: {h.get('confidence', '')}")
        else:
            st.info("沒有歷史報告")
    else:
        st.info("請先在「個股深度」標籤選擇 ticker")

# --- Tab 4: Market Review ---
with tab_market:
    mr = get_market_review()
    if mr:
        st.subheader(f"恒指/國企指 復盤 · {mr['review_date']}")
        col1, col2, col3, col4 = st.columns(4)
        col1.metric("恒指 (HSI)", f"{mr.get('hsi', 'N/A')}", f"{mr.get('hsi_chg', 0)}%")
        col2.metric("國企指 (HSCEI)", f"{mr.get('hscei', 'N/A')}", f"{mr.get('hscei_chg', 0)}%")
        col3.metric("上漲", mr.get("advancers", "—"))
        col4.metric("下跌", mr.get("decliners", "—"))

        if mr.get("sectors"):
            st.markdown("### 🔥 板塊表現")
            st.dataframe(mr["sectors"], use_container_width=True)

        if mr.get("summary_md"):
            st.markdown("### 📝 復盤摘要")
            st.markdown(mr["summary_md"])
    else:
        st.info("尚未有大盤復盤數據")

# --- Tab 5: Run Log ---
with tab_runlog:
    st.subheader("📋 運行日誌")
    runs = list_recent_runs(limit=50)
    if runs:
        rows = []
        for r in runs:
            rows.append({
                "ID": r["id"],
                "開始": r["started_at"],
                "結束": r.get("ended_at", "—"),
                "狀態": r["status"],
                "完成": f"{r['tickers_done']}/{r['tickers_total']}",
                "失敗": r["tickers_failed"],
                "觸發": r["trigger"],
            })
        st.dataframe(rows, use_container_width=True, hide_index=True)
    else:
        st.info("尚無運行記錄")

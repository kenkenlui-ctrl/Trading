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
    initial_sidebar_state="expanded",
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
    :root {
        --bg: #0a0e14;
        --panel: #131820;
        --panel-2: #1a212b;
        --border: #2a323e;
        --text: #d4d4d4;
        --text-dim: #888;
        --amber: #ffb000;
        --green: #00d084;
        --red: #ff3860;
        --blue: #00b4ff;
    }

    html, body, .stApp, [data-testid="stAppViewContainer"], .main {
        background-color: var(--bg) !important;
        color: var(--text) !important;
        font-family: 'JetBrains Mono', 'SF Mono', 'Menlo', monospace !important;
    }

    h1, h2, h3, h4 {
        font-family: 'JetBrains Mono', monospace !important;
        font-weight: 600 !important;
        color: var(--text) !important;
    }
    h1 {
        font-size: 1.5rem !important;
        color: var(--amber) !important;
        border-bottom: 2px solid var(--amber);
        padding-bottom: 0.5rem;
    }
    h2 { font-size: 1.1rem !important; color: var(--blue) !important; }
    h3 { font-size: 0.95rem !important; color: var(--text-dim) !important;
         text-transform: uppercase; letter-spacing: 0.1em; }

    section[data-testid="stSidebar"] {
        background-color: var(--panel) !important;
        border-right: 1px solid var(--border);
    }
    section[data-testid="stSidebar"] h1 {
        color: var(--amber) !important;
        font-size: 1.2rem !important;
    }

    .stTabs [data-baseweb="tab-list"] {
        background: transparent !important;
        border-bottom: 1px solid var(--border);
        gap: 0;
    }
    .stTabs [data-baseweb="tab"] {
        background: transparent !important;
        color: var(--text-dim) !important;
        border: 0 !important;
        border-radius: 0 !important;
        padding: 0.75rem 1.25rem !important;
        font-family: 'JetBrains Mono', monospace !important;
        text-transform: uppercase;
        font-size: 0.8rem !important;
        letter-spacing: 0.1em;
    }
    .stTabs [aria-selected="true"] {
        color: var(--amber) !important;
        border-bottom: 2px solid var(--amber) !important;
    }

    .stButton > button {
        background-color: var(--panel-2) !important;
        color: var(--text) !important;
        border: 1px solid var(--border) !important;
        border-radius: 2px !important;
        font-family: 'JetBrains Mono', monospace !important;
        text-transform: uppercase;
        font-size: 0.75rem !important;
    }
    .stButton > button:hover {
        background-color: var(--amber) !important;
        color: var(--bg) !important;
        border-color: var(--amber) !important;
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
        color: var(--green) !important;
        font-family: 'JetBrains Mono', monospace !important;
        border: 1px solid var(--border);
        border-radius: 2px;
    }

    [data-testid="stMetricValue"] {
        font-family: 'JetBrains Mono', monospace !important;
        color: var(--amber) !important;
        font-size: 1.5rem !important;
    }
    [data-testid="stMetricLabel"] {
        color: var(--text-dim) !important;
        text-transform: uppercase;
        font-size: 0.7rem !important;
        letter-spacing: 0.1em;
    }

    .stProgress > div > div > div > div {
        background-color: var(--amber) !important;
    }

    .stCaption, small {
        color: var(--text-dim) !important;
        font-family: 'JetBrains Mono', monospace !important;
        font-size: 0.75rem !important;
    }

    .bull { color: var(--green) !important; }
    .bear { color: var(--red) !important; }
    .amber { color: var(--amber) !important; }
    .dim { color: var(--text-dim) !important; }

    #MainMenu, footer {visibility: hidden;}
    header[data-testid="stHeader"] {background-color: transparent !important;}
</style>
""", unsafe_allow_html=True)

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

    # Status
    st.markdown("**STATUS**")
    llm_status = "● " + ", ".join(cfg.available_llm_providers()) if cfg.has_llm_key() else "○ n/a"
    news_status = "● " + ", ".join(cfg.available_news_sources()) if cfg.has_news_key() else "○ n/a"
    tg_status = "● live" if cfg.has_telegram() else "○ n/a"
    st.markdown(f":green[{llm_status}] llm")
    st.markdown(f":green[{news_status}] news")
    st.markdown(f":green[{tg_status}] tg")
    st.markdown(f"`{cfg.litellm_model}`")

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
            if st.button("▶️ 立即分析所有股票 (HK+US)", use_container_width=True, key="trigger_full"):
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

    st.markdown("---")
    st.markdown("**RUN LOG**")
    runs = list_recent_runs(limit=10)
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
    st.markdown(
        "<div style='background:#1a1d23;border:1px solid #b88a00;border-radius:6px;"
        "padding:8px 12px;margin-bottom:12px;font-size:12px;color:#d4b864;'>"
        "⚠️ <b>非投資建議</b> · 本工具只係 AI 輔助決策參考，唔構成任何買賣建議。"
        "Day trading 涉及高風險，過去表現唔代表未來回報。請自行評估風險並諮詢持牌顧問。"
        "<a href='/disclaimer' style='color:#d4b864;text-decoration:underline;margin-left:8px;'>完整免責聲明 →</a>"
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
    # Map filter → DB value
    filter_map_dash = {
        "全部": None,
        "只做多 LONG": "long",
        "只做空 SHORT": "short",
        "雙向": "both",
    }
    target_dir = filter_map_dash[trend_filter]
    st.markdown(build_dashboard_md(selected_date, language=cfg.report_language, trade_direction=target_dir))

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

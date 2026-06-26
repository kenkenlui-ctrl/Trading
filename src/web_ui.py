"""Streamlit web UI. Runs on http://localhost:8200."""

from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path

# Ensure project root on path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import streamlit as st  # noqa: E402

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

st.set_page_config(
    page_title="DSA · HK+US AI分析",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ===== Sidebar =====
with st.sidebar:
    st.title("📈 DSA · HK+US")
    st.caption("港股+美股 AI 智能分析系統")
    st.markdown("---")

    # Status
    st.subheader("⚙️ 系統狀態")
    llm_status = "✅ " + ", ".join(cfg.available_llm_providers()) if cfg.has_llm_key() else "❌ 未配置"
    news_status = "✅ " + ", ".join(cfg.available_news_sources()) if cfg.has_news_key() else "⚠️ 未配置"
    tg_status = "✅ 已連接" if cfg.has_telegram() else "⚠️ 未配置"
    st.markdown(f"**LLM**: {llm_status}")
    st.markdown(f"**新聞源**: {news_status}")
    st.markdown(f"**Telegram**: {tg_status}")
    st.markdown(f"**語言**: {cfg.report_language}")
    st.markdown(f"**模型**: `{cfg.litellm_model}`")

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
    st.subheader("📊 最近運行")
    runs = list_recent_runs(limit=10)
    for r in runs[:5]:
        status_icon = {"success": "✅", "partial": "🟡", "failed": "❌", "running": "⏳"}.get(r["status"], "·")
        st.markdown(
            f"{status_icon} {r['started_at'][:16]} · "
            f"{r['tickers_done']}/{r['tickers_total']} · {r['trigger']}"
        )

# ===== Main =====
st.title("🎯 HK+US AI 決策儀表板")

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
    st.markdown(build_dashboard_md(selected_date, language=cfg.report_language))

    # Detail table
    reports = list_reports(report_date=selected_date, limit=200)
    if reports:
        st.markdown("---")
        st.subheader("詳細表格")
        rows = []
        for r in sorted(reports, key=lambda x: x["score"] or 0, reverse=True):
            rows.append({
                "代碼": r["code"],
                "評分": r["score"],
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

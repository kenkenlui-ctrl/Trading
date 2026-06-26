"""
Seed realistic mock daily reports into the DB so the dashboard has content to show.
Run: python -m scripts.seed_demo
This is for demo / screenshot purposes only — wipes existing reports first.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.db import init_db, get_db, save_report  # noqa: E402

# Curated set of 10 HK tickers — varied sectors, varied signals
MOCK_REPORTS = [
    {
        "code": "0700.HK", "name_zh": "騰訊控股", "name_en": "Tencent",
        "last_price": 421.4, "prev_close": 428.8, "change_pct": -1.73,
        "day_high": 429.6, "day_low": 418.2,
        "pe_ttm": 15.1, "pb": 4.2, "div_yield": 0.85, "market_cap": 3.92e12,
        "ma5": 425.3, "ma10": 422.1, "ma20": 418.7, "ma50": 410.5,
        "rsi14": 48.2, "w52_high": 482.4, "w52_low": 295.6, "ytd_chg": 22.5,
        "score": 72, "sentiment": "中性偏樂觀", "trend": "震盪偏多", "operation_advice": "觀望",
        "confidence": "中",
        "summary": "騰訊於 420-430 區間震盪，MA20 失而復得，短線缺乏催化。等待突破 432 阻力或回踩 415 支撐再進場。",
        "entry_zone": "415-418", "stop_loss": "408", "target_price": "438",
        "risk_reward_ratio": "2.1",
        "catalysts": ["微信視頻號商業化提速", "遊戲版號常態化發放", "回購計劃持續"],
        "risks": ["中國互聯網監管不確定性", "宏觀消費弱於預期", "今晚美股若大跌或有缺口風險"],
        "strategy_tags": ["MA多頭排列", "區間震盪", "量能萎縮"],
        "reasoning": "騰訊近期在 420-432 區間內整理，5 日均線走平，MACD 柱狀體收斂。技術面偏中性，但估值 PE 15x 處於歷史 30 分位以下，提供安全邊際。短線方向不明確，建議等待突破方向明確後再行動。"
    },
    {
        "code": "9988.HK", "name_zh": "阿里巴巴-W", "name_en": "Alibaba",
        "last_price": 95.0, "prev_close": 99.4, "change_pct": -4.43,
        "day_high": 99.8, "day_low": 94.5,
        "pe_ttm": 12.5, "pb": 1.8, "div_yield": 1.4, "market_cap": 1.82e12,
        "ma5": 99.2, "ma10": 100.5, "ma20": 102.1, "ma50": 95.8,
        "rsi14": 32.5, "w52_high": 118.7, "w52_low": 68.4, "ytd_chg": 35.8,
        "score": 45, "sentiment": "悲觀", "trend": "下跌趨勢", "operation_advice": "觀望",
        "confidence": "中",
        "summary": "阿里跌破 MA50 支撐，短線轉弱。雲業務增速放緩 + 電商競爭加劇，需等待企穩訊號。",
        "entry_zone": "88-90", "stop_loss": "85", "target_price": "100",
        "risk_reward_ratio": "1.8",
        "catalysts": ["雲業務分拆進展", "AI 商業化變現"],
        "risks": ["電商市場份額持續流失", "美股中概股板塊拖累", "雲業務增速跌破 5%"],
        "strategy_tags": ["跌破MA50", "弱勢股", "估值便宜"],
        "reasoning": "阿里巴巴今日跌 4.4%，跌破 100 元整數關口及 MA50 支撐，RSI 進入超賣區域。但下行趨勢未見明確止跌訊號，MACD 死叉發散中。建議耐心等待 88-90 區間出現止跌 K 線組合（錘子線 / 早晨之星）再考慮吸納。"
    },
    {
        "code": "1810.HK", "name_zh": "小米集團-W", "name_en": "Xiaomi",
        "last_price": 22.3, "prev_close": 22.95, "change_pct": -2.83,
        "day_high": 23.0, "day_low": 22.15,
        "pe_ttm": 28.6, "pb": 5.8, "div_yield": 0.0, "market_cap": 5.55e11,
        "ma5": 22.85, "ma10": 22.6, "ma20": 22.1, "ma50": 21.4,
        "rsi14": 42.1, "w52_high": 26.5, "w52_low": 14.4, "ytd_chg": 48.2,
        "score": 68, "sentiment": "中性", "trend": "震盪", "operation_advice": "觀望",
        "confidence": "中高",
        "summary": "小米於 22-23 區間震盪，電動車 SU7 持續放量但手機業務承壓。守穩 22 即可短多。",
        "entry_zone": "22.0-22.2", "stop_loss": "21.4", "target_price": "23.5",
        "risk_reward_ratio": "1.9",
        "catalysts": ["SU7 訂單持續增長", "汽車業務毛利改善", "IoT 業務全球擴張"],
        "risks": ["手機市場飽和", "汽車價格戰", "印度市場監管風險"],
        "strategy_tags": ["區間震盪", "MA20支撐", "汽車概念"],
        "reasoning": "小米股價在 22-23 區間整理，MA20 提供支撐。SU7 電動車業務持續放量是中期催化，但手機毛利率承壓是隱憂。短線方向未明，建議在 22 附近低吸，破 21.4 止損。"
    },
    {
        "code": "3690.HK", "name_zh": "美團-W", "name_en": "Meituan",
        "last_price": 138.5, "prev_close": 137.2, "change_pct": 0.95,
        "day_high": 139.8, "day_low": 136.5,
        "pe_ttm": 22.4, "pb": 4.9, "div_yield": 0.0, "market_cap": 8.45e11,
        "ma5": 136.2, "ma10": 134.8, "ma20": 132.5, "ma50": 128.4,
        "rsi14": 56.3, "w52_high": 158.5, "w52_low": 78.5, "ytd_chg": 68.4,
        "score": 76, "sentiment": "樂觀", "trend": "上升趨勢", "operation_advice": "買入",
        "confidence": "中高",
        "summary": "美團多頭排列完好，今日突破 MA5 並放量。短線目標 145，止損 132。",
        "entry_zone": "136-138", "stop_loss": "132", "target_price": "145",
        "risk_reward_ratio": "1.9",
        "catalysts": ["外賣業務份額穩固", "到店酒旅復甦", "海外業務 Keeta 擴張中東"],
        "risks": ["外賣補貼戰重燃", "抖音本地生活競爭加劇", "監管對佣金上限的潛在壓力"],
        "strategy_tags": ["MA多頭排列", "量比放大", "突破阻力"],
        "reasoning": "美團股價穩站所有均線之上，MA5 上穿 MA10 形成短線金叉。今日成交放大 30%，動能增強。基本面穩健，到店業務復甦明確。建議回調至 136-138 區間分批吸納。"
    },
    {
        "code": "1211.HK", "name_zh": "比亞迪股份", "name_en": "BYD",
        "last_price": 248.6, "prev_close": 244.2, "change_pct": 1.80,
        "day_high": 250.4, "day_low": 243.8,
        "pe_ttm": 18.5, "pb": 3.6, "div_yield": 0.4, "market_cap": 7.25e11,
        "ma5": 244.8, "ma10": 240.5, "ma20": 238.2, "ma50": 235.6,
        "rsi14": 62.4, "w52_high": 280.2, "w52_low": 168.5, "ytd_chg": 12.5,
        "score": 81, "sentiment": "樂觀", "trend": "上升趨勢", "operation_advice": "買入",
        "confidence": "高",
        "summary": "比亞迪突破 245 阻力，技術形態強勢。DM-i 5.0 + 智駕升級雙輪驅動。",
        "entry_zone": "245-248", "stop_loss": "240", "target_price": "265",
        "risk_reward_ratio": "2.1",
        "catalysts": ["DM-i 5.0 插混技術領先", "智駕系統全國開城", "海外出口持續放量"],
        "risks": ["新能源車價格戰", "歐盟反補貼稅", "原材料價格波動"],
        "strategy_tags": ["MA多頭排列", "放量突破", "新能源龍頭"],
        "reasoning": "比亞迪股價創近期新高，MACD 紅柱放大，趨勢強勁。今日成交突破 30 億，量價配合完美。基本面+技術面共振，建議積極跟進。"
    },
    {
        "code": "0005.HK", "name_zh": "匯豐控股", "name_en": "HSBC",
        "last_price": 92.5, "prev_close": 91.8, "change_pct": 0.76,
        "day_high": 92.8, "day_low": 91.5,
        "pe_ttm": 7.2, "pb": 1.1, "div_yield": 6.8, "market_cap": 1.48e12,
        "ma5": 91.6, "ma10": 90.8, "ma20": 89.5, "ma50": 86.2,
        "rsi14": 58.7, "w52_high": 96.5, "w52_low": 65.4, "ytd_chg": 32.5,
        "score": 78, "sentiment": "樂觀", "trend": "上升趨勢", "operation_advice": "買入",
        "confidence": "高",
        "summary": "匯豐高息防禦首選，年內回購 + 派息穩定。PB 1.1x 嚴重低估。",
        "entry_zone": "91-92", "stop_loss": "89", "target_price": "96",
        "risk_reward_ratio": "2.0",
        "catalysts": ["2024 全年回購規模達 90 億美元", "淨息差穩定", "亞洲業務回暖"],
        "risks": ["聯儲局減息節奏放緩", "中國房地產敞口", "歐元區經濟放緩"],
        "strategy_tags": ["高息防禦", "低估藍籌", "MA多頭排列"],
        "reasoning": "匯豐股價沿 MA10 穩步上行，PB 1.1x 為歷史低位，提供強安全邊際。6.8% 股息率吸引長線資金。回購計劃持續為股價托底。"
    },
    {
        "code": "0388.HK", "name_zh": "香港交易所", "name_en": "HKEX",
        "last_price": 285.4, "prev_close": 289.2, "change_pct": -1.31,
        "day_high": 290.5, "day_low": 284.2,
        "pe_ttm": 28.5, "pb": 7.2, "div_yield": 2.4, "market_cap": 3.61e12,
        "ma5": 290.2, "ma10": 286.5, "ma20": 280.8, "ma50": 275.4,
        "rsi14": 52.4, "w52_high": 318.2, "w52_low": 215.6, "ytd_chg": 18.5,
        "score": 70, "sentiment": "中性", "trend": "高位震盪", "operation_advice": "觀望",
        "confidence": "中",
        "summary": "港交所於 280-295 高位震盪，等待成交量配合突破。",
        "entry_zone": "280-283", "stop_loss": "275", "target_price": "300",
        "risk_reward_ratio": "1.9",
        "catalysts": ["南向資金持續流入", "新股集資活動回暖", "互聯互通擴容"],
        "risks": ["市場成交量萎縮", "中概股回歸放緩", "印花稅上調傳言"],
        "strategy_tags": ["高位震盪", "成交低迷", "藍籌權重"],
        "reasoning": "港交所近期在 280-295 區間震盪，估值 PE 28x 偏高，需要成交量放大配合突破。短線方向不明，建議等待放量突破 295 或回踩 280 確認。"
    },
    {
        "code": "0981.HK", "name_zh": "中芯國際", "name_en": "SMIC",
        "last_price": 42.85, "prev_close": 41.2, "change_pct": 4.0,
        "day_high": 43.2, "day_low": 41.0,
        "pe_ttm": 65.8, "pb": 2.4, "div_yield": 0.0, "market_cap": 3.42e11,
        "ma5": 41.5, "ma10": 40.2, "ma20": 38.8, "ma50": 36.5,
        "rsi14": 68.5, "w52_high": 48.6, "w52_low": 22.8, "ytd_chg": 78.5,
        "score": 75, "sentiment": "樂觀", "trend": "上升趨勢", "operation_advice": "買入",
        "confidence": "中",
        "summary": "中芯突破 42 阻力，國產替代邏輯延續。但追高風險大，等回踩。",
        "entry_zone": "40.5-41.5", "stop_loss": "39", "target_price": "46",
        "risk_reward_ratio": "1.9",
        "catalysts": ["國產 7nm 量產", "華為/國產 AI 芯片需求", "成熟製程產能緊張"],
        "risks": ["美方對華芯片管制升級", "產能利用率下滑", "客戶集中度高"],
        "strategy_tags": ["放量突破", "國產替代", "半導體龍頭"],
        "reasoning": "中芯國際今日漲 4%，突破 42 阻力。但 RSI 已達 68.5 接近超買，建議等待回踩 40.5-41.5 區間再考慮介入。國產替代是中長期主線，但短期追高需謹慎。"
    },
    {
        "code": "2331.HK", "name_zh": "李寧", "name_en": "Li Ning",
        "last_price": 14.85, "prev_close": 15.2, "change_pct": -2.30,
        "day_high": 15.3, "day_low": 14.78,
        "pe_ttm": 12.5, "pb": 1.8, "div_yield": 4.5, "market_cap": 3.91e10,
        "ma5": 15.1, "ma10": 15.3, "ma20": 15.6, "ma50": 15.8,
        "rsi14": 38.4, "w52_high": 22.8, "w52_low": 13.5, "ytd_chg": -28.5,
        "score": 42, "sentiment": "悲觀", "trend": "下跌趨勢", "operation_advice": "觀望",
        "confidence": "中",
        "summary": "李寧弱勢下行，消費復甦緩慢 + 庫存壓力大，觀望。",
        "entry_zone": "13.5-14.0", "stop_loss": "13.0", "target_price": "16.5",
        "risk_reward_ratio": "1.8",
        "catalysts": ["奧運營銷催化", "渠道庫存出清"],
        "risks": ["消費持續疲弱", "安踏擠壓市場", "庫存週轉放緩"],
        "strategy_tags": ["弱勢股", "消費疲軟", "股息保護"],
        "reasoning": "李寧股價沿 MA5/10/20 階梯下行，趨勢偏空。RSI 38 接近超賣但未見底背離。建議耐心等待 13.5-14.0 區間止跌訊號。"
    },
    {
        "code": "9618.HK", "name_zh": "京東集團-SW", "name_en": "JD.com",
        "last_price": 132.5, "prev_close": 138.2, "change_pct": -4.12,
        "day_high": 138.5, "day_low": 131.8,
        "pe_ttm": 9.8, "pb": 1.5, "div_yield": 3.2, "market_cap": 4.15e11,
        "ma5": 138.5, "ma10": 140.2, "ma20": 142.8, "ma50": 138.4,
        "rsi14": 28.5, "w52_high": 168.5, "w52_low": 95.8, "ytd_chg": -5.8,
        "score": 35, "sentiment": "悲觀", "trend": "弱勢下跌", "operation_advice": "賣出",
        "confidence": "中",
        "summary": "京東跌破所有均線，電商競爭 + 增長放緩雙重壓力。短線止損為主。",
        "entry_zone": "N/A - 觀望", "stop_loss": "N/A", "target_price": "N/A",
        "risk_reward_ratio": "N/A",
        "catalysts": ["即時零售業務增長", "物流業務扭虧"],
        "risks": ["電商市場份額下滑", "拼多多/抖音擠壓", "宏觀消費疲弱"],
        "strategy_tags": ["弱勢股", "所有均線下方", "估值便宜但無催化"],
        "reasoning": "京東今日大跌 4.1%，跌破 MA50 重要支撐，RSI 28 進入超賣區域。短期趨勢已轉弱，雖然估值 PE 9.8x 便宜，但缺乏明確催化。建議暫時觀望，等待趨勢確認。"
    },
]


def seed():
    """Wipe daily_report table and seed with mock data."""
    init_db()
    conn = get_db()
    try:
        # Clear existing reports
        conn.execute("DELETE FROM daily_report")
        # Also clear ticker table for fresh state
        conn.execute("DELETE FROM ticker")
        conn.commit()
    finally:
        conn.close()

    today = datetime.now().strftime("%Y-%m-%d")
    print(f"Seeding {len(MOCK_REPORTS)} reports for {today}...")

    for r in MOCK_REPORTS:
        # Mock data snapshot
        snapshot = {
            "code": r["code"],
            "name_zh": r["name_zh"],
            "name_en": r["name_en"],
            "last_price": r["last_price"],
            "prev_close": r["prev_close"],
            "change_pct": r["change_pct"],
            "day_high": r["day_high"],
            "day_low": r["day_low"],
            "volume": 5_000_000 + hash(r["code"]) % 20_000_000,
            "turnover_hkd": r["last_price"] * (5_000_000 + hash(r["code"]) % 20_000_000),
            "pe_ttm": r["pe_ttm"],
            "pb": r["pb"],
            "dividend_yield": r["div_yield"],
            "market_cap_hkd": r["market_cap"],
            "ma5": r["ma5"],
            "ma10": r["ma10"],
            "ma20": r["ma20"],
            "ma50": r["ma50"],
            "rsi14": r["rsi14"],
            "52w_high": r["w52_high"],
            "52w_low": r["w52_low"],
            "ytd_change_pct": r["ytd_chg"],
            "kline_30d": [
                {"date": f"2026-06-{i+1:02d}", "open": r["last_price"] * 0.98,
                 "high": r["last_price"] * 1.01, "low": r["last_price"] * 0.97,
                 "close": r["last_price"] * (0.97 + i * 0.001), "volume": 1000000}
                for i in range(30)
            ],
            "sector": "",
            "source": "demo",
        }

        # Mock news
        news = [
            {
                "title": f"{r['name_zh']} 今日盤面分析：技術指標轉強",
                "url": f"https://example.com/news/{r['code']}-analysis",
                "snippet": f"分析師指出，{r['name_zh']} 近期走勢呈現{r['trend']}格局，建議關注{r['entry_zone']}入場機會...",
                "published": "2026-06-25",
                "source": "demo",
            },
            {
                "title": f"恒指收市：科技股領漲，{r['name_zh']} 跟隨大市",
                "url": f"https://example.com/news/hsi-{r['code']}",
                "snippet": f"恒生指數今日上漲 0.5%，科技板塊表現活躍，{r['name_zh']} 漲跌互見...",
                "published": "2026-06-25",
                "source": "demo",
            },
        ]

        # Save full report
        from src.analyzer import render_report_md, render_summary_md
        from src.db import save_report, upsert_ticker

        # Mock AnalysisResult-like object
        class MockResult:
            def __init__(self, r):
                self.code = r["code"]
                self.score = r["score"]
                self.sentiment = r["sentiment"]
                self.trend = r["trend"]
                self.operation_advice = r["operation_advice"]
                self.confidence = r["confidence"]
                self.summary = r["summary"]
                self.entry_zone = r["entry_zone"]
                self.stop_loss = r["stop_loss"]
                self.target_price = r["target_price"]
                self.risk_reward_ratio = r["risk_reward_ratio"]
                self.catalysts = r["catalysts"]
                self.risks = r["risks"]
                self.strategy_tags = r["strategy_tags"]
                self.reasoning = r["reasoning"]
                self.llm_model = "demo-seed"

        result = MockResult(r)
        full_md = render_report_md(result, snapshot, language="zh-Hant")
        summary_md = render_summary_md(result, language="zh-Hant")

        upsert_ticker(
            code=r["code"],
            name_zh=r["name_zh"],
            name_en=r["name_en"],
            sector="",
            last_price=r["last_price"],
        )
        save_report(
            code=r["code"],
            report_date=today,
            score=r["score"],
            sentiment=r["sentiment"],
            trend=r["trend"],
            operation_advice=r["operation_advice"],
            summary_md=summary_md,
            full_md=full_md,
            news=news,
            data_snapshot=snapshot,
            llm_model="demo-seed",
        )
        print(f"  ✓ {r['code']} {r['name_zh']} score={r['score']} {r['operation_advice']}")

    print(f"\n✓ Seeded {len(MOCK_REPORTS)} reports for {today}")


if __name__ == "__main__":
    seed()
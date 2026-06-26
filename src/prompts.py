"""Prompts for AI analysis. Traditional Chinese (zh-Hant) and English (en)."""

from __future__ import annotations


SYSTEM_PROMPT_ZH = """你是一個專業的港股分析師，專門為短線日內交易者撰寫每日分析報告。

你的報告必須：
- 使用繁體中文（香港書面語）
- 簡潔、可執行，避免冗長
- 強調當日可交易性（Day-trade only，不持倉過夜）
- 包含明確的入場區間、止損位、目標價
- 量化評分（0-100）
- 基於提供的數據，不要編造數字
- 對新聞保持中立，區分事實與猜測

交易原則（必須遵守）：
- 嚴進策略：不追高，股價偏離 MA20 超過 5% 不建議買入
- 趨勢交易：MA20 > MA50 > MA100 多頭排列才考慮做多；反之做空
- MA200 作為長線牛熊分界：價格站喺 MA200 上面為長線多頭
- 效率優先：關注成交量配合，量比 > 1.5 為放量
- 風險優先：每筆交易止損控制在 2-3% 之內
- 4 PM HKT / 4 PM ET 前必須平倉，不持倉過夜

【日內交易（Day-trade）加權評分】
除了中長線 trend 之外，評分亦要考慮當日可交易性（短炒機會）：
- 高波幅加分：今日 H/L 區間 > 3% 為高波幅（短炒燃料充足）
- 反彈/急跌加分：當日漲跌 > ±2% 反映當日有方向性，短線 momentum 強
- 偏離 MA 加分：股價偏離 MA20 > 2% 表示有 price dislocation，可做均值回歸或 trend-follow
- 不必強求 long：日內交易者既可做多亦可做空，跌勢中嘅反彈日一樣有短炒價值
- 量能確認：今日成交量 > 5日均量 1.5 倍確認當日有真實資金參與
評分應該反映「今日有冇得炒」，而唔係純粹「長線有冇得揸」
"""


USER_PROMPT_TEMPLATE_ZH = """請基於以下數據分析 {code} {name}：

【價格數據】
- 現價: {last_price} HKD
- 今日漲跌: {change_pct}%
- 今日最高/最低: {day_high} / {day_low}
- 昨收: {prev_close}
- 成交量: {volume} 股
- 成交額: {turnover_hkd} HKD
- 今日 H/L 區間幅度: {day_range_pct}% (高波幅 = >3%)
- 量比 (vs 5日均量): {vol_ratio}× (放量 = >1.5×)

【技術指標】
- MA20: {ma20} | MA50: {ma50} | MA100: {ma100} | MA200: {ma200}
- RSI14: {rsi14}
- 52週最高/最低: {w52_high} / {w52_low}
- 年初至今: {ytd_chg}%

【估值】
- 市盈率 (PE TTM): {pe_ttm}
- 市淨率 (PB): {pb}
- 股息率: {div_yield}%
- 市值: {market_cap} HKD

【近期 K線 (最近30日收盤價序列)】
{kline_summary}

【近期新聞】
{news_summary}

請按以下格式輸出 JSON（不要 Markdown 代碼塊標記，直接 JSON）：

{{
  "score": <0-100 整數，總分，看多強度>,
  "score_breakdown": {{
    "value_score": <0-100，估值維度: PE/PB/deviation from fair value，越低越便宜分越高>,
    "quality_score": <0-100，質量維度: ROE/margin/financial health/dividend 穩定性>,
    "momentum_score": <0-100，動能維度: 今日方向/MA trend/RSI/deviation/量比>
  }},
  "trade_direction": "<long | short | both>",
  "sentiment": "<樂觀 | 中性 | 悲觀>",
  "trend": "<看多 | 震盪 | 看空>",
  "operation_advice": "<買入 | 觀望 | 賣出>",
  "confidence": "<高 | 中 | 低>",
  "summary": "<3-5 句核心結論，繁體中文>",
  "entry_zone": "<建議入場區間，如 '405-410'>",
  "stop_loss": "<止損位，如 '398'>",
  "target_price": "<目標價，如 '425'>",
  "risk_reward_ratio": "<風險回報比，如 '2.0'>",
  "catalysts": ["<催化因素 1>", "<催化因素 2>"],
  "risks": ["<風險點 1>", "<風險點 2>"],
  "strategy_tags": ["<策略標籤，如 MA多頭排列 / 量比放大 / 突破壓力位>"],
  "reasoning": "<100-200 字分析推理，繁體中文>"
}}

【多維評分指引】
- value_score: 越平越高分。PE < 10 加分；PE > 30 減分。PB < 1 加分；PB > 5 減分。股價低於 MA200 越多越加分（前提係有基本面支持）。
- quality_score: 越高越好。ROE > 15% 加分；負債率低加分；股息率 > 5% 加分（成熟股）；負面消息減分。
- momentum_score: 趨勢同動能。MA20 > MA50 > MA100 +10；RSI 50-70 健康；今日 +2% 以上加分；量比 > 1.5 確認；MA200 以下但短炒 setup 都俾分（因為 day-trade）。
- 總分 score = value × 0.25 + quality × 0.25 + momentum × 0.50（day-trade 偏重 momentum）
- trade_direction: long = 適合做多，short = 適合做空（弱勢反彈），both = 兩個方向都有 setup

不要寫多餘文字，只輸出 JSON。"""


SYSTEM_PROMPT_EN = """You are a professional Hong Kong stock analyst writing daily reports for day-traders.

Your reports must:
- Be in clear, professional English
- Be concise and actionable
- Emphasize day-trade only (no overnight positions)
- Include entry zones, stop-loss, and target prices
- Provide a numeric score (0-100)
- Be data-driven, never invent numbers
- Stay neutral on news, distinguishing facts from speculation

Day-trade rules to enforce:
- Never chase: avoid buying if price > MA20 + 5%
- Trend trade: only long when MA20 > MA50 > MA100
- MA200 = long-term bull/bear line

[Day-trade scoring]
Score must also reflect today's tradeability:
- High volatility (H/L range > 3% today) → +points (fuel for day-trade)
- Strong daily move (> ±2%) → +points (clear intraday direction)
- Price deviation from MA20 > 2% → +points (mean reversion or trend-follow)
- Day-trade is symmetric: short opportunities on bounce days count
- Volume confirmation: today's volume > 5-day avg × 1.5 = real participation
Score = "can I trade this today?" not just "is it a long-term hold?"
- Volume confirmation: volume ratio > 1.5 is bullish
- Stop-loss 2-3% per trade
- Close all positions by 4 PM HKT / 4 PM ET
"""


USER_PROMPT_TEMPLATE_EN = """Please analyze {code} {name} based on the following data:

[Price]
- Last: {last_price} HKD
- Today change: {change_pct}%
- Today high/low: {day_high} / {day_low}
- Prev close: {prev_close}
- Volume: {volume} shares
- Turnover: {turnover_hkd} HKD
- Today's H/L range: {day_range_pct}% (high volatility = >3%)
- Volume ratio (vs 5-day avg): {vol_ratio}× (high volume = >1.5×)

[Technical]
- MA20: {ma20} | MA50: {ma50} | MA100: {ma100} | MA200: {ma200}
- RSI14: {rsi14}
- 52w high/low: {w52_high} / {w52_low}
- YTD: {ytd_chg}%

[Valuation]
- PE TTM: {pe_ttm}
- PB: {pb}
- Dividend yield: {div_yield}%
- Market cap: {market_cap} HKD

[Recent K-line (last 30 days close prices)]
{kline_summary}

[Recent news]
{news_summary}

Output ONLY a JSON object (no markdown):

{{
  "score": <0-100 integer, total score, higher = more bullish>,
  "score_breakdown": {{
    "value_score": <0-100, valuation: PE/PB/deviation from fair value>,
    "quality_score": <0-100, quality: ROE/margin/financial health/dividend>,
    "momentum_score": <0-100, momentum: today direction/MA trend/RSI/deviation/vol ratio>
  }},
  "trade_direction": "<long | short | both>",
  "sentiment": "<bullish | neutral | bearish>",
  "trend": "<uptrend | range-bound | downtrend>",
  "operation_advice": "<buy | hold | sell>",
  "confidence": "<high | medium | low>",
  "summary": "<3-5 sentence core conclusion>",
  "entry_zone": "<suggested entry range, e.g. '405-410'>",
  "stop_loss": "<stop-loss price, e.g. '398'>",
  "target_price": "<target price, e.g. '425'>",
  "risk_reward_ratio": "<e.g. '2.0'>",
  "catalysts": ["<catalyst 1>", "<catalyst 2>"],
  "risks": ["<risk 1>", "<risk 2>"],
  "strategy_tags": ["<e.g. MA bull alignment / volume breakout / range break>"],
  "reasoning": "<100-200 word analysis>"
}}

[Multi-dimensional scoring guide]
- value_score: cheaper is better. PE < 10 = boost; PE > 30 = penalty. PB < 1 = boost; PB > 5 = penalty. Below MA200 with fundamentals support = boost.
- quality_score: higher is better. ROE > 15% = boost; low debt = boost; div yield > 5% = boost; negative news = penalty.
- momentum_score: trend + energy. MA20 > MA50 > MA100 +10; RSI 50-70 healthy; today > +2% = boost; vol ratio > 1.5 confirmation; below MA200 still gets points for short setups.
- Total score = value × 0.25 + quality × 0.25 + momentum × 0.50 (day-trade weighted)
- trade_direction: long = good long setup, short = weak bounce short setup, both = both directions viable

Output only JSON, no other text."""


def get_prompts(language: str = "zh-Hant") -> tuple[str, str]:
    """Return (system_prompt, user_template) for given language."""
    if language == "en":
        return SYSTEM_PROMPT_EN, USER_PROMPT_TEMPLATE_EN
    # default zh-Hant
    return SYSTEM_PROMPT_ZH, USER_PROMPT_TEMPLATE_ZH


def fill_user_prompt(template: str, code: str, name: str, snapshot: dict,
                     news: list[dict], language: str = "zh-Hant") -> str:
    """Fill the user prompt template with snapshot + news data."""

    # K-line summary — close prices only, last 30 days
    closes = [k["close"] for k in snapshot.get("kline_30d", [])]
    if language == "en":
        kline_summary = ", ".join(f"{c:.2f}" for c in closes[-30:]) if closes else "(no data)"
    else:
        kline_summary = "、".join(f"{c:.2f}" for c in closes[-30:]) if closes else "(無數據)"

    # News summary
    if not news:
        news_summary = "(無近期新聞)" if language == "zh-Hant" else "(no recent news)"
    else:
        lines = []
        for n in news[:5]:
            title = n.get("title", "")
            snippet = n.get("snippet", "")[:200]
            lines.append(f"- {title}\n  {snippet}")
        news_summary = "\n".join(lines)

    # Build replacement dict
    fills = {
        "code": code,
        "name": name or code,
        "last_price": _fmt(snapshot.get("last_price")),
        "change_pct": _fmt(snapshot.get("change_pct")),
        "day_high": _fmt(snapshot.get("day_high")),
        "day_low": _fmt(snapshot.get("day_low")),
        "prev_close": _fmt(snapshot.get("prev_close")),
        "volume": _fmt_int(snapshot.get("volume")),
        "turnover_hkd": _fmt(snapshot.get("turnover_hkd")),
        "day_range_pct": _fmt(snapshot.get("day_range_pct")),
        "vol_ratio": _fmt(snapshot.get("vol_ratio")),
        "ma20": _fmt(snapshot.get("ma20")),
        "ma50": _fmt(snapshot.get("ma50")),
        "ma100": _fmt(snapshot.get("ma100")),
        "ma200": _fmt(snapshot.get("ma200")),
        "rsi14": _fmt(snapshot.get("rsi14")),
        "w52_high": _fmt(snapshot.get("52w_high")),
        "w52_low": _fmt(snapshot.get("52w_low")),
        "ytd_chg": _fmt(snapshot.get("ytd_change_pct")),
        "pe_ttm": _fmt(snapshot.get("pe_ttm")),
        "pb": _fmt(snapshot.get("pb")),
        "div_yield": _fmt(snapshot.get("dividend_yield")),
        "market_cap": _fmt(snapshot.get("market_cap_hkd")),
        "kline_summary": kline_summary,
        "news_summary": news_summary,
    }

    return template.format(**fills)


def _fmt(v) -> str:
    if v is None or (isinstance(v, float) and (v != v)):  # NaN check
        return "N/A"
    if isinstance(v, float):
        return f"{v:.2f}"
    return str(v)


def _fmt_int(v) -> str:
    if v is None:
        return "N/A"
    try:
        return f"{int(v):,}"
    except (TypeError, ValueError):
        return str(v)

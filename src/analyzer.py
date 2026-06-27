"""LLM analyzer using litellm. Multi-provider, structured JSON output."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Optional

import litellm

from .config import get_config
from .prompts import fill_user_prompt, get_prompts

logger = logging.getLogger(__name__)


@dataclass
class AnalysisResult:
    code: str
    score: int
    sentiment: str
    trend: str
    operation_advice: str
    confidence: str
    summary: str
    score_breakdown: dict = None
    trade_direction: str = "both"
    entry_zone: Optional[str] = None
    stop_loss: Optional[str] = None
    target_price: Optional[str] = None
    risk_reward_ratio: Optional[str] = None
    support_zone: Optional[str] = None
    resistance_zone: Optional[str] = None
    key_levels: Optional[dict] = None
    catalysts: list[str] = None
    risks: list[str] = None
    strategy_tags: list[str] = None
    reasoning: str = ""
    llm_model: str = ""

    def __post_init__(self):
        for f in ("catalysts", "risks", "strategy_tags"):
            v = getattr(self, f)
            if v is None:
                setattr(self, f, [])

    def to_dict(self) -> dict:
        return {
            "code": self.code,
            "score": self.score,
            "sentiment": self.sentiment,
            "trend": self.trend,
            "operation_advice": self.operation_advice,
            "confidence": self.confidence,
            "summary": self.summary,
            "score_breakdown": self.score_breakdown,
            "trade_direction": self.trade_direction,
            "entry_zone": self.entry_zone,
            "stop_loss": self.stop_loss,
            "target_price": self.target_price,
            "risk_reward_ratio": self.risk_reward_ratio,
            "support_zone": self.support_zone,
            "resistance_zone": self.resistance_zone,
            "key_levels": self.key_levels,
            "catalysts": self.catalysts,
            "risks": self.risks,
            "strategy_tags": self.strategy_tags,
            "reasoning": self.reasoning,
            "llm_model": self.llm_model,
        }


def _extract_json(text: str) -> dict:
    """Extract JSON object from LLM response. Handles markdown wrapping."""
    text = text.strip()
    # Strip markdown code fences
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.MULTILINE)
    text = re.sub(r"\s*```$", "", text, flags=re.MULTILINE)
    # Find JSON object
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        raise ValueError(f"No JSON object found in LLM response: {text[:200]}")
    return json.loads(match.group(0))


def analyze(
    code: str,
    name: str,
    snapshot: dict,
    news: list[dict],
    language: Optional[str] = None,
) -> Optional[AnalysisResult]:
    """
    Run LLM analysis for a HK ticker. Returns AnalysisResult or None on failure.
    """
    cfg = get_config()
    language = language or cfg.report_language

    system_prompt, user_template = get_prompts(language)
    user_prompt = fill_user_prompt(user_template, code, name, snapshot, news, language)

    # Set API keys in env for litellm
    import os
    if cfg.minimax_api_key:
        os.environ["MINIMAX_API_KEY"] = cfg.minimax_api_key
    if cfg.gemini_api_key:
        os.environ["GEMINI_API_KEY"] = cfg.gemini_api_key
    if cfg.deepseek_api_key:
        os.environ["DEEPSEEK_API_KEY"] = cfg.deepseek_api_key
    if cfg.openai_api_key:
        os.environ["OPENAI_API_KEY"] = cfg.openai_api_key
        if cfg.openai_base_url:
            os.environ["OPENAI_BASE_URL"] = cfg.openai_base_url

    # Resolve model + per-provider kwargs
    model = cfg.resolve_litellm_model()
    call_kwargs = cfg.resolve_llm_call_kwargs()

    # Retry up to 2 times on JSON decode failure
    last_error = None
    for attempt in range(3):
        try:
            logger.debug(f"Calling LLM ({model}) for {code}...")
            response = litellm.completion(
                model=model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
temperature=0.3,
            # 8000 tokens = safer ceiling for verbose zh-Hant summaries with
            # concrete-number fields (support_zone / resistance_zone / key_levels).
            # MiniMax-M3 was truncating mid-JSON at 3000 (default for verbose
            # prompts); 6000 worked for most cases; 8000 is the safe ceiling for
            # stubborn tickers with long news + fundamentals. Override via
            # env var DSA_LLM_MAX_TOKENS if needed.
            max_tokens=int(os.environ.get("DSA_LLM_MAX_TOKENS", "8000")),
            timeout=120,
                **call_kwargs,
            )
            content = response.choices[0].message.content
            logger.debug(f"LLM raw response for {code}: {content[:200]}")
            data = _extract_json(content)

            actual_model = response.get("model", model) if hasattr(response, "get") else model
            result = AnalysisResult(
                code=code,
                score=int(data.get("score", 50)),
                sentiment=str(data.get("sentiment", "中性")),
                trend=str(data.get("trend", "震盪")),
                operation_advice=str(data.get("operation_advice", "觀望")),
                confidence=str(data.get("confidence", "中")),
                summary=str(data.get("summary", "")),
                score_breakdown=data.get("score_breakdown", {}),
                trade_direction=str(data.get("trade_direction", "both")),
                entry_zone=data.get("entry_zone"),
                stop_loss=data.get("stop_loss"),
                target_price=data.get("target_price"),
                risk_reward_ratio=data.get("risk_reward_ratio"),
                support_zone=data.get("support_zone"),
                resistance_zone=data.get("resistance_zone"),
                key_levels=data.get("key_levels"),
                catalysts=data.get("catalysts", []),
                risks=data.get("risks", []),
                strategy_tags=data.get("strategy_tags", []),
                reasoning=str(data.get("reasoning", "")),
                llm_model=str(actual_model) if actual_model else model,
            )
            return result
        except json.JSONDecodeError as e:
            last_error = e
            logger.warning(f"LLM JSON decode error for {code}, retrying ({attempt+1}/3): {e}")
            continue
        except Exception as e:
            logger.error(f"LLM analysis failed for {code} with {model}: {e}")
            return None
    logger.error(f"LLM returned invalid JSON for {code} after 3 attempts: {last_error}")
    return None


def render_report_md(result: AnalysisResult, snapshot: dict, language: str = "zh-Hant") -> str:
    """Build a full Markdown report from analysis result + raw snapshot."""
    is_zh = language == "zh-Hant"

    # Emoji based on operation_advice
    op = result.operation_advice
    if is_zh:
        emoji = {"買入": "🟢", "觀望": "🟡", "賣出": "🔴"}.get(op, "⚪")
        score_emoji = "🚀" if result.score >= 75 else "📈" if result.score >= 60 else "➡️" if result.score >= 40 else "📉"
    else:
        emoji = {"buy": "🟢", "hold": "🟡", "sell": "🔴"}.get(op.lower(), "⚪")
        score_emoji = "🚀" if result.score >= 75 else "📈" if result.score >= 60 else "➡️" if result.score >= 40 else "📉"

    name = snapshot.get("name_zh") or snapshot.get("name_en") or result.code
    price = snapshot.get("last_price")
    change = snapshot.get("change_pct")
    source = snapshot.get("source", "")

    md = f"""# {emoji} {result.code} {name}

**{score_emoji} 評分 {result.score}/100** · {result.sentiment} · {result.trend} · **{result.operation_advice}** · 信心 {result.confidence}
"""

    if price:
        md += f"\n**現價**: {price} HKD ({'+' if change and change >= 0 else ''}{change}%)\n"

    if is_zh:
        md += f"\n## 📋 核心結論\n\n{result.summary}\n"

        md += "\n## 🎯 操作建議\n\n"
        if result.entry_zone:
            md += f"- **入場區間**: {result.entry_zone}\n"
        if result.stop_loss:
            md += f"- **止損位**: {result.stop_loss}\n"
        if result.target_price:
            md += f"- **目標價**: {result.target_price}\n"
        if result.risk_reward_ratio:
            md += f"- **風險回報比**: {result.risk_reward_ratio}\n"
        if result.support_zone:
            md += f"- **支持區**: {result.support_zone}\n"
        if result.resistance_zone:
            md += f"- **阻力區**: {result.resistance_zone}\n"
        if result.key_levels:
            md += "\n### 關鍵價位（具體數值）\n\n"
            md += "| 指標 | 數值 |\n|---|---|\n"
            for k, v in result.key_levels.items():
                md += f"| {k} | {v} |\n"

        if result.catalysts:
            md += "\n## ✨ 利好催化\n\n"
            for c in result.catalysts:
                md += f"- {c}\n"

        if result.risks:
            md += "\n## 🚨 風險警報\n\n"
            for r in result.risks:
                md += f"- {r}\n"

        if result.strategy_tags:
            md += "\n## 🏷️ 策略標籤\n\n"
            md += "、".join(f"`{t}`" for t in result.strategy_tags) + "\n"

        if result.reasoning:
            md += f"\n## 💭 分析推理\n\n{result.reasoning}\n"

        # Raw data summary
        md += "\n## 📊 技術數據\n\n"
        md += f"| 指標 | 數值 |\n|---|---|\n"
        md += f"| MA20 / MA50 / MA100 / MA200 | {snapshot.get('ma20')} / {snapshot.get('ma50')} / {snapshot.get('ma100')} / {snapshot.get('ma200')} |\n"
        md += f"| RSI14 | {snapshot.get('rsi14')} |\n"
        md += f"| 52週高/低 | {snapshot.get('52w_high')} / {snapshot.get('52w_low')} |\n"
        md += f"| PE (TTM) / PB | {snapshot.get('pe_ttm')} / {snapshot.get('pb')} |\n"
        md += f"| 股息率 | {snapshot.get('dividend_yield')}% |\n"
        md += f"| 年初至今 | {snapshot.get('ytd_change_pct')}% |\n"
        md += f"\n*數據來源: {source} · LLM: {result.llm_model}*\n"
    else:
        md += f"\n## 📋 Summary\n\n{result.summary}\n"

        md += "\n## 🎯 Trade Plan\n\n"
        if result.entry_zone:
            md += f"- **Entry zone**: {result.entry_zone}\n"
        if result.stop_loss:
            md += f"- **Stop loss**: {result.stop_loss}\n"
        if result.target_price:
            md += f"- **Target**: {result.target_price}\n"
        if result.risk_reward_ratio:
            md += f"- **Risk/Reward**: {result.risk_reward_ratio}\n"

        if result.catalysts:
            md += "\n## ✨ Catalysts\n\n"
            for c in result.catalysts:
                md += f"- {c}\n"

        if result.risks:
            md += "\n## 🚨 Risks\n\n"
            for r in result.risks:
                md += f"- {r}\n"

        if result.strategy_tags:
            md += "\n## 🏷️ Strategy Tags\n\n"
            md += ", ".join(f"`{t}`" for t in result.strategy_tags) + "\n"

        if result.reasoning:
            md += f"\n## 💭 Reasoning\n\n{result.reasoning}\n"

        md += "\n## 📊 Technicals\n\n"
        md += f"| Metric | Value |\n|---|---|\n"
        md += f"| MA20/50/100/200 | {snapshot.get('ma20')} / {snapshot.get('ma50')} / {snapshot.get('ma100')} / {snapshot.get('ma200')} |\n"
        md += f"| RSI14 | {snapshot.get('rsi14')} |\n"
        md += f"| 52w high/low | {snapshot.get('52w_high')} / {snapshot.get('52w_low')} |\n"
        md += f"| PE (TTM) / PB | {snapshot.get('pe_ttm')} / {snapshot.get('pb')} |\n"
        md += f"| Dividend yield | {snapshot.get('dividend_yield')}% |\n"
        md += f"| YTD | {snapshot.get('ytd_change_pct')}% |\n"
        md += f"\n*Source: {source} · LLM: {result.llm_model}*\n"

    return md


def render_summary_md(result: AnalysisResult, language: str = "zh-Hant") -> str:
    """Render compact summary (3-5 lines) for dashboard."""
    is_zh = language == "zh-Hant"
    op = result.operation_advice
    if is_zh:
        emoji = {"買入": "🟢", "觀望": "🟡", "賣出": "🔴"}.get(op, "⚪")
    else:
        emoji = {"buy": "🟢", "hold": "🟡", "sell": "🔴"}.get(op.lower(), "⚪")

    name = ""  # Caller can prepend
    line = f"{emoji} **{result.code}** · 評分 {result.score} · {result.operation_advice} · {result.summary}"
    return line


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    cfg = get_config()
    if not cfg.has_llm_key():
        print("No LLM API key configured. Set GEMINI_API_KEY in .env first.")
        raise SystemExit(1)

    from .data_fetcher import fetch_snapshot
    from .news_fetcher import fetch_news

    code = "0700.HK"
    snap = fetch_snapshot(code)
    if not snap:
        print(f"Failed to fetch snapshot for {code}")
        raise SystemExit(1)

    news = fetch_news(code, snap.get("name_zh"), snap.get("name_en"))
    print(f"Got snapshot + {len(news)} news")

    result = analyze(code, snap.get("name_zh", ""), snap, news)
    if result:
        print("\n=== Analysis ===")
        print(f"Score: {result.score} | Sentiment: {result.sentiment}")
        print(f"Operation: {result.operation_advice}")
        print(f"Summary: {result.summary}")
        print(f"\n=== Markdown ===")
        print(render_report_md(result, snap))
    else:
        print("Analysis failed")

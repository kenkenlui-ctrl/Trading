"""
Chanlun 3rd-class BUY signal scoring via LLM (MiniMax-M3).

Why LLM secondary filter:
  Technical signals are abundant (~166/200 stocks over 2y).
  Many are false breakouts or low-conviction setups. The LLM
  brings in context (catalysts, market regime, sector strength)
  to filter signal quality.

Scoring:
  - LLM returns JSON: {score: 1-10, conviction: "high"/"med"/"low",
                       reasoning: "...", risks: ["...", "..."]}
  - Filter: keep signals with score >= 7 (configurable)
  - Persist llm_score + llm_reasoning alongside technical signal

Cost:
  - 166 signals × ~500 tokens = ~80K tokens per scan
  - At MiniMax-M3 pricing (~$0.10/1M input tokens), ~$0.008/scan
  - Acceptable for daily cron
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Optional

log = logging.getLogger(__name__)


SCORING_PROMPT = """Rate this Chanlun 3rd-class BUY signal for {code} on a scale of 1-10.

Setup:
- Entry {entry_price}, Stop {stop_loss} (-5%), Target {target} (+15%)
- 中樞 (consolidation zone): ZG={central_zg}, ZD={central_zd}, GG={central_gg}, DD={central_dd}
- Tech confidence: {tech_confidence}/10
- Signal date: {signal_date}

Return ONLY this JSON (no other text):
{{"score": <int 1-10>, "conviction": "high|medium|low", "reasoning": "<one sentence>", "risks": ["<risk1>"]}}"""


@dataclass
class LLMScore:
    score: int
    conviction: str
    reasoning: str
    risks: list[str]
    raw_response: str = ""


def _extract_json(text: str) -> Optional[dict]:
    """Try to extract a JSON object from the LLM response."""
    # Direct parse
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # Strip code fences
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            pass
    # Find first {...} block
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            pass
    return None


def score_signal(
    code: str,
    entry_price: float,
    stop_loss: float,
    target: float,
    tech_confidence: int,
    central_zg: float,
    central_zd: float,
    central_gg: float,
    central_dd: float,
    width_pct: float,
    signal_date: str,
    timeout: int = 60,
) -> Optional[LLMScore]:
    """
    Score one Chanlun signal with MiniMax-M3.
    Returns None on failure (caller decides to keep / drop).
    """
    try:
        import litellm
    except ImportError:
        log.warning("litellm not installed, skipping LLM scoring")
        return None

    try:
        from src.config import get_config
        cfg = get_config()
    except Exception:
        cfg = None

    # Set MiniMax key in env (same pattern as analyzer.py)
    if cfg and cfg.minimax_api_key:
        import os
        os.environ["MINIMAX_API_KEY"] = cfg.minimax_api_key

    # Resolve model + kwargs
    if cfg:
        model = cfg.resolve_litellm_model()
        call_kwargs = cfg.resolve_llm_call_kwargs()
    else:
        model = "openai/MiniMax-M3"
        call_kwargs = {}

    user_prompt = SCORING_PROMPT.format(
        code=code,
        entry_price=f"{entry_price:.2f}",
        stop_loss=f"{stop_loss:.2f}",
        target=f"{target:.2f}",
        tech_confidence=tech_confidence,
        central_zg=f"{central_zg:.2f}",
        central_zd=f"{central_zd:.2f}",
        central_gg=f"{central_gg:.2f}",
        central_dd=f"{central_dd:.2f}",
        width_pct=f"{width_pct:.1f}",
        signal_date=signal_date,
    )

    try:
        response = litellm.completion(
            model=model,
            messages=[
                {
                    "role": "system",
                    "content": "你是港股技術分析師。永遠只回 JSON，唔好用 <think>、唔好解釋、直接輸出 JSON。",
                },
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.2,
            # MiniMax-M3 is a reasoning model with internal thinking.
            # Benchmark 2026-06-26:
            #   600  -> 30-50% empty responses (thinking eats budget)
            #   1000 -> 3/3 success, ~11s (sweet spot)
            #   2500 -> 3/3 success, ~19s (slower, no benefit)
            # 1000 is enough for short JSON output + reasoning headroom.
            max_tokens=1000,
            timeout=timeout,
            response_format={"type": "json_object"},
            **call_kwargs,
        )
        content = response.choices[0].message.content
        # Check for thinking tokens (some models expose reasoning as separate field)
        if not content or not content.strip():
            # Try reasoning_content if available
            thinking = getattr(response.choices[0].message, "reasoning_content", None)
            if thinking:
                content = str(thinking)
            else:
                log.warning(f"{code}: LLM returned empty content")
                return None
        data = _extract_json(content)
        if not data:
            log.warning(f"{code}: LLM JSON decode failed: {content[:300]}")
            return None

        return LLMScore(
            score=int(data.get("score", 5)),
            conviction=str(data.get("conviction", "medium")),
            reasoning=str(data.get("reasoning", "")),
            risks=list(data.get("risks", [])),
            raw_response=content,
        )
    except Exception as e:
        log.warning(f"{code}: LLM scoring failed: {str(e)[:120]}")
        return None


def score_and_filter(
    signals: list[dict],
    min_score: int = 7,
    parallel: bool = False,
) -> list[dict]:
    """
    Score a list of signal dicts and filter by min_score.

    Each input dict should have keys from save_chanlun_signal:
      code, entry_price, stop_loss, target, confidence,
      central_zg, central_zd, central_gg, central_dd, signal_date

    Returns the same dicts with llm_score / llm_reasoning / llm_risks added.
    Drops signals where llm_score < min_score (or scoring failed).

    Set parallel=True for concurrent scoring (faster but uses more API quota).
    """
    if not signals:
        return []

    if parallel:
        return _score_parallel(signals, min_score)
    return _score_sequential(signals, min_score)


def _score_sequential(signals: list[dict], min_score: int) -> list[dict]:
    out = []
    for sig in signals:
        width_pct = (
            (sig["central_zg"] - sig["central_zd"]) / sig["central_zd"] * 100
            if sig.get("central_zd") else 0.0
        )
        score = score_signal(
            code=sig["code"],
            entry_price=sig["entry_price"],
            stop_loss=sig["stop_loss"],
            target=sig["target"],
            tech_confidence=sig["confidence"],
            central_zg=sig["central_zg"],
            central_zd=sig["central_zd"],
            central_gg=sig["central_gg"],
            central_dd=sig["central_dd"],
            width_pct=width_pct,
            signal_date=sig["signal_date"],
        )
        if score is None:
            # LLM failed — be conservative, drop the signal
            log.info(f"{sig['code']}: LLM scoring failed, dropping signal")
            continue
        sig["llm_score"] = score.score
        sig["llm_conviction"] = score.conviction
        sig["llm_reasoning"] = score.reasoning
        sig["llm_risks"] = score.risks
        if score.score < min_score:
            log.info(f"{sig['code']}: dropped (LLM score {score.score} < {min_score})")
            continue
        out.append(sig)
    return out


def _score_parallel(signals: list[dict], min_score: int) -> list[dict]:
    """Concurrent LLM scoring using ThreadPoolExecutor."""
    from concurrent.futures import ThreadPoolExecutor, as_completed

    def score_one(sig):
        width_pct = (
            (sig["central_zg"] - sig["central_zd"]) / sig["central_zd"] * 100
            if sig.get("central_zd") else 0.0
        )
        score = score_signal(
            code=sig["code"],
            entry_price=sig["entry_price"],
            stop_loss=sig["stop_loss"],
            target=sig["target"],
            tech_confidence=sig["confidence"],
            central_zg=sig["central_zg"],
            central_zd=sig["central_zd"],
            central_gg=sig["central_gg"],
            central_dd=sig["central_dd"],
            width_pct=width_pct,
            signal_date=sig["signal_date"],
        )
        return sig, score

    out = []
    with ThreadPoolExecutor(max_workers=8) as ex:
        futures = [ex.submit(score_one, s) for s in signals]
        for fut in as_completed(futures):
            sig, score = fut.result()
            if score is None:
                log.info(f"{sig['code']}: LLM scoring failed, dropping")
                continue
            sig["llm_score"] = score.score
            sig["llm_conviction"] = score.conviction
            sig["llm_reasoning"] = score.reasoning
            sig["llm_risks"] = score.risks
            if score.score < min_score:
                log.info(f"{sig['code']}: dropped (LLM {score.score}<{min_score})")
                continue
            out.append(sig)
    return out
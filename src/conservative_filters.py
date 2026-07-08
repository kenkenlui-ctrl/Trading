"""Consolidated high-WR filter logic.

Restructured 2026-07-09 after 9-day deep audit (1404 signals, 1D forward returns).

KEY FINDINGS:
  - LLM is a momentum chaser. 樂觀 sentiment BUY → 30.2% WR (-1.53% avg)
    vs 中性 → 48.6% WR (+0.01% avg). 18.4% WR gap.
  - Best BUY edge: chg[-3,0%) + 中性 → 63.3% WR (+1.15% avg) over 30 trades.
  - Worst BUY: 樂觀 + chg≥+3% → 37.3% WR (-1.57% avg) over 59 trades (TOXIC).
  - SELL on panic (悲觀 + chg≤-3%) = catching falling knife. 15 cases went +4-14%.
  - HOLD on rebound candidates (chg[-5,-2] + 悲觀/中性) = missed +5-47% bounces.
  - Strength BUY (multi-day uptrend continuation) = 33% WR, -1.31% avg. KILLED.
  - Conservative BUY (chg[-3,0)+m[30,70]+score<70) = 52.4% WR, +0.26% avg. Keep.
  - Cyber BUY v2 = 0 historical signals (paused while awaiting pullback).

NEW FILTERS:
  - Anti-Chase BUY overlay: kill any BUY if 樂觀 (broad), m≥80, chg≥+5%, or tech+m≥65.
  - Bounce BUY: NEW. Buy HOLD candidates that are panic-sold (chg[-5,-2]+sent非樂觀).
  - Anti-Knife SELL: block SELL if chg ≤ -3% (let mean-reversion happen).
"""

# Cybersecurity / network-security tickers
CYBER_TICKERS = {
    "DDOG", "PANW", "CRWD", "FTNT", "OKTA", "ZS", "NET",
    "S", "CYBR", "RBRK", "QLYS", "TENB", "VRNS",
}

# Tech / communication-services sectors to AVOID for BUY
# Audit: Technology BUY -1.86% avg, Industrials -1.23% avg.
TECH_SECTORS_AVOID = {
    "Technology",
    "Communication Services",
    "Information Technology",
    "科技",
    "通訊服務",
    "軟件",
    "互聯網",
}


def is_earnings_blackout(ticker: str, current_date: str = None) -> tuple[bool, str]:
    """Check if ticker is in earnings blackout period."""
    from pathlib import Path
    import json as _json
    from datetime import datetime
    cfg_path = Path(__file__).parent.parent / "data" / "earnings_blackout.json"
    if not cfg_path.exists():
        return False, ""
    try:
        with open(cfg_path) as f:
            config = _json.load(f)
    except Exception:
        return False, ""
    blackout_days = config.get("blackout_days", 2)
    events = config.get("events", [])
    if current_date is None:
        current_dt = datetime.now()
    else:
        current_dt = datetime.strptime(current_date, "%Y-%m-%d")
    for ev in events:
        if ev.get("ticker") != ticker:
            continue
        try:
            ev_dt = datetime.strptime(ev["date"], "%Y-%m-%d")
        except Exception:
            continue
        delta = (ev_dt - current_dt).days
        if -1 <= delta <= blackout_days:
            return True, f"{ev.get('type', 'event')} on {ev['date']} ({delta}d away): {ev.get('note', '')}"
    return False, ""


def anti_chase_buy_blocks(
    sentiment: str,
    m_score: int,
    day_chg: float,
    score: int,
    sector: str = "",
) -> tuple[bool, str]:
    """Anti-Chase BUY overlay.

    Audit evidence (9-day, 178 BUY signals, 1D forward return):
      - 樂觀 sentiment alone → 30.2% WR (-1.53% avg)
      - m_score ≥ 80 → 20.0% WR (-2.10% avg)
      - day_chg ≥ +5% → ~37% WR (-1.5% avg)
      - Tech sector + m≥65 → 38.7% WR (topping semis pattern)
      - score ≥ 75 → heavily front-run

    Returns: (is_blocked, reason)
    """
    if sentiment == "樂觀":
        return True, f"sentiment=樂觀 (LLM momentum-chasing: 30.2% WR)"
    if m_score >= 80:
        return True, f"m_score={m_score} ≥ 80 (overbought: 20% WR)"
    if day_chg >= 5.0:
        return True, f"day_chg={day_chg:+.1f}% ≥ +5% (gap-up exhaustion)"
    if score >= 75:
        return True, f"score={score} ≥ 75 (over-priced / front-run)"
    if sector in TECH_SECTORS_AVOID and m_score >= 65:
        return True, f"tech sector + m={m_score} ≥ 65 (semis topping pattern)"
    return False, ""


def anti_knife_sell_blocks(day_chg: float, sentiment: str) -> tuple[bool, str]:
    """Anti-Knife SELL overlay.

    Audit evidence: 15/15 worst SELL signals were 悲觀 + chg ≤ -3% (panic day).
    All bounced +4% to +14% next day. Selling panic = buying top.
    Only SELL bounces, not falling knives.

    Returns: (is_blocked, reason)
    """
    if day_chg <= -3.0:
        return True, f"day_chg={day_chg:+.1f}% ≤ -3% (panic day — let mean-reversion happen)"
    return False, ""


def conservative_buy_passes(
    code: str,
    score: int,
    day_chg: float,
    m_score: int,
    of_score: int,
    sentiment: str,
    sector: str = "",
) -> tuple[bool, str]:
    """Conservative BUY (mean-reversion, non-tech, anti-chase).

    Backtest (9-day, n=21 trades v1): 52.4% WR, +0.26% avg.
    v2 tested: too restrictive, dropped WR to 40.6% (admitting losers).
    v2.1 — 2026-07-09: v1 rules + ONLY Anti-Chase overlay (block 樂觀+m≥60+chg≥3% toxic).

    Rules:
      - Anti-Chase overlay (kill 樂觀+m≥60+chg≥3% toxic; otherwise let through)
      - day_chg (-3, 0)% — strict dip
      - m_score [30, 70] — neutral momentum
      - score < 70
      - sector not in TECH_SECTORS_AVOID
      - Earnings blackout (caller checks)

    Returns: (passes, reason_if_fails)
    """
    # v2.1: only block the SPECIFIC toxic combo, not all 樂觀
    if sentiment == "樂觀" and m_score >= 60 and day_chg >= 3:
        return False, "TOXIC (樂觀+m≥60+chg≥3% — chasing gap-up tops)"
    if sector in TECH_SECTORS_AVOID:
        return False, f"sector={sector} in TECH_SECTORS_AVOID"
    if not (-3 < day_chg < 0):
        return False, f"day_chg={day_chg:+.1f}% not in (-3, 0)"
    if not (30 <= m_score <= 70):
        return False, f"m_score={m_score} not in [30, 70]"
    if sentiment == "樂觀":
        return False, "sentiment=樂觀 (LLM momentum-chasing: 30.2% WR vs 中性 48.6%)"
    if score >= 70:
        return False, f"score={score} >= 70"
    return True, ""


def bounce_buy_passes(
    code: str,
    score: int,
    day_chg: float,
    m_score: int,
    of_score: int,
    sentiment: str,
    sector: str = "",
) -> tuple[bool, str]:
    """Bounce BUY (mean-reversion entry on panic day).

    Backtest (9-day, 60 HOLD candidates): 51.7% WR, -0.53% avg.
    Catches 7/2 misses (02650.HK +47%, 09880.HK +17.6%, etc.).

    Rules:
      - day_chg [-5, -2] — pullback day (not crash, not flat)
      - sentiment in (悲觀, 中性) — LLM agrees pullback is overdone
      - m_score < 60 — momentum already cooled off
      - score < 45 — system agrees value is there
      - of_score ≥ 25 — institutional didn't fully flee

    Returns: (passes, reason_if_fails)
    """
    if not (-5 <= day_chg <= -2):
        return False, f"day_chg={day_chg:+.1f}% not in [-5, -2]"
    if sentiment == "樂觀":
        return False, "sentiment=樂觀 (already bullish, no bounce needed)"
    if m_score >= 60:
        return False, f"m_score={m_score} ≥ 60 (momentum still hot)"
    if score >= 45:
        return False, f"score={score} ≥ 45 (LLM doesn't see value at this dip)"
    if of_score < 25:
        return False, f"of_score={of_score} < 25 (institutions still fleeing)"
    return True, ""


def cyber_buy_passes(
    code: str,
    score: int,
    day_chg: float,
    m_score: int,
    sentiment: str,
    last_price: float,
    high_52w: float,
) -> tuple[bool, str]:
    """Cyber BUY v2 — anti-gapup + 52w high avoidance.

    Paused while awaiting pullback (0 historical signals pass).
    """
    if not (-5 <= day_chg < 0):
        return False, f"day_chg={day_chg:+.1f}% not in [-5, 0)"
    if not (30 <= m_score <= 60):
        return False, f"m_score={m_score} not in [30, 60]"
    if score >= 65:
        return False, f"score={score} >= 65"
    if sentiment == "樂觀":
        return False, "sentiment=樂觀 (too euphoric)"
    if high_52w and last_price >= high_52w * 0.98:
        return False, f"near 52w high (last={last_price:.0f} >= 98% of {high_52w:.0f})"
    return True, ""


# Strength BUY DISABLED 2026-07-09 — 9-day audit: 33% WR, -1.31% avg.
# The "multi-day uptrend continuation" thesis was inverted:
# stocks already up +5% in 5 days + sentiment 樂觀 = TOXIC BUY territory.
# When system said Strength BUY (META, HOOD, PANW, PDD, MDB, TTWO 7/6),
# 4/6 dropped next day. KILLED until proven otherwise.
# def strength_buy_passes(...): REMOVED
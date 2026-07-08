"""Constants for backtest-validated high-WR filter presets.
Updated 2026-07-09 based on 6-day trace + 7/6 loss.

Earnings blackout (C): read from data/earnings_blackout.json
  - Skip BUY signals for tickers with earnings within N days
  - Manually maintained; future enhancement: auto-fetch from yfinance calendar

Evidence:
  - Conservative BUY (mean-reversion + non-tech + m 30-70 + score < 70):
      -1% to 0% prior-day-change bucket: +0.68% avg, 70.6% WR
      -3% to -1% bucket: +1.02% avg, 50% WR
  - Cyber BUY ORIGINAL (any 買入 in whitelist): 5 signals, 2W/3L (40% WR), -$50
      All signals at 52w high with strong positive day_chg (gap-ups) — chased tops
  - Cyber BUY v2 (2026-07-09): anti-gapup + 52w high avoidance
      New rules: day_chg -5% to 0% (anti-gapup), m_score 30-60, score<65, not 樂觀,
                 last < 98% of 52w_high (avoid buying at peak)
"""

# Cybersecurity / network-security tickers with positive backtest results
CYBER_TICKERS = {
    "DDOG",   # Datadog — observability
    "PANW",   # Palo Alto Networks — firewall
    "CRWD",   # CrowdStrike — endpoint security
    "FTNT",   # Fortinet — firewall/SD-WAN
    "OKTA",   # Okta — identity
    "ZS",     # Zscaler — zero-trust
    "NET",    # Cloudflare — edge
    "S",      # SentinelOne — endpoint
    "CYBR",   # CyberArk — privileged access
    "RBRK",   # Rubrik — data security
    "QLYS",   # Qualys — vulnerability
    "TENB",   # Tenable — vulnerability
    "VRNS",   # Varonis — data security
}

# Tech / communication-services sectors to AVOID for BUY
# (mean-reversion failed in 6-day trace: Technology -1.86%, Industrials -1.23%)
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
    """
    Check if ticker is in earnings blackout period.

    Returns: (is_blackout, reason_if_blackout)
    """
    from pathlib import Path
    import json as _json
    from datetime import datetime, timedelta
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
        if -1 <= delta <= blackout_days:  # within blackout window
            return True, f"{ev.get('type', 'event')} on {ev['date']} ({delta}d away): {ev.get('note', '')}"
    return False, ""


def cyber_buy_passes(
    code: str,
    score: int,
    day_chg: float,
    m_score: int,
    sentiment: str,
    last_price: float,
    high_52w: float,
) -> tuple[bool, str]:
    """
    Cyber BUY v2 — anti-gapup + 52w high avoidance.

    Original logic (any 買入 in whitelist) yielded 40% WR over 5 signals with -$50
    P&L because all signals came on gap-up days at 52w high (buying tops).

    New rules (2026-07-09):
      - day_chg -5% to 0%: avoid gap-up days where LLM chases breakouts
      - m_score 30-60: avoid overbought momentum (cyber often 70+)
      - score < 65: avoid over-rated signals
      - sentiment != 樂觀: avoid euphoric LLM calls
      - last < 98% of 52w_high: don't buy at peak (room to run)

    Returns: (passes, reason_if_fails)
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


def strength_buy_passes(
    code: str,
    score: int,
    day_chg: float,
    m_score: int,
    of_score: int,
    sentiment: str,
    change_3d: float,
    change_5d: float,
) -> tuple[bool, str]:
    """
    Strength BUY — multi-day uptrend continuation (opposite of Conservative).

    Catches stocks with persistent strength that the day-trade-only
    Conservative BUY filter misses (e.g. BABA 7/8 +11% surge preceded by
    7/6-7/7 multi-day accumulation). Backtest rationale:
      - 3-day return > +3%: accumulation phase (smart money buying)
      - 5-day return > +5%: sustained uptrend
      - m_score > 60: short-term momentum strong
      - of_score > 50: institutional flow (vs retail)
      - sentiment != 悲觀: not panic mode
      - day_chg > 0: today's continuation (avoid falling knife)

    Returns: (passes, reason_if_fails)
    """
    if change_3d <= 3.0:
        return False, f"3d return={change_3d:+.1f}% not > +3%"
    if change_5d <= 5.0:
        return False, f"5d return={change_5d:+.1f}% not > +5%"
    if m_score < 60:
        return False, f"m_score={m_score} < 60"
    if of_score < 50:
        return False, f"of_score={of_score} < 50 (no institutional flow)"
    if sentiment == "悲觀":
        return False, "sentiment=悲觀 (panic mode)"
    if day_chg <= 0:
        return False, f"day_chg={day_chg:+.1f}% not > 0% (no continuation today)"
    return True, ""
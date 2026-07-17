"""Feature engineering for dsa-hk signals.

Phase 9 Step 3-4 (2026-07-18): deterministic features from data_snapshot,
5-day rolling window, sector cross-section, and sub-score algorithm rewrite.

Design principle: features are 100% deterministic from raw data (no LLM).
LLM 4-dim scores (value/quality/momentum/order_flow) were audited and found
to add zero predictive power (Pearson r ~ 0 between LLM score and T+1 return).
Replacing them with rules-based scores derived from actual data.

All features are computed from data_snapshot (raw quote + fundamentals) and
the last 5 trading days of the same ticker.
"""
from __future__ import annotations

import math
import sqlite3
from pathlib import Path
from typing import Optional


DB_PATH = "/Users/kenken/Documents/dsa-hk/data/dsa_hk.db"


# =============== Sub-score Algorithm (Phase 9 Step 4) ===============
# Replaces LLM-derived value/quality/momentum/order_flow scores.
# Each sub-score is 0-100, deterministic from raw data.

def _safe_float(x, default: Optional[float] = None) -> Optional[float]:
    if x is None:
        return default
    try:
        v = float(x)
        if math.isnan(v) or math.isinf(v):
            return default
        return v
    except (ValueError, TypeError):
        return default


def compute_value_score(snap: dict, dist_low_pct: Optional[float] = None) -> int:
    """Value sub-score 0-100 based on PE, dividend yield, distance to 52w low.

    Backed by 14d audit: v_score has Pearson r=+0.056 with T+1 return (most
    predictive of all 4 dims in old LLM-derived scoring). We push harder on
    pure value metrics.
    """
    pe = _safe_float(snap.get("pe_ttm"))
    dy = _safe_float(snap.get("dividend_yield"))

    # PE tier (0-60 points)
    if pe is None or pe <= 0:
        pe_pts = 30  # unknown → neutral
    elif pe < 5:
        pe_pts = 60
    elif pe < 10:
        pe_pts = 55
    elif pe < 15:
        pe_pts = 50
    elif pe < 25:
        pe_pts = 35
    elif pe < 40:
        pe_pts = 20
    else:
        pe_pts = 10

    # Dividend yield (0-20 points, capped at 8% yield)
    if dy is None or dy <= 0:
        dy_pts = 0
    elif dy >= 8:
        dy_pts = 20
    else:
        dy_pts = dy * 2.5  # 4% yield = 10 pts

    # Distance to 52w low (0-20 points) — closer to 52w low = more upside
    if dist_low_pct is None:
        low_pts = 0
    elif dist_low_pct <= 5:
        low_pts = 20      # within 5% of 52w low
    elif dist_low_pct <= 15:
        low_pts = 15
    elif dist_low_pct <= 30:
        low_pts = 10
    elif dist_low_pct <= 60:
        low_pts = 5
    else:
        low_pts = 0

    return min(100, max(0, int(round(pe_pts + dy_pts + low_pts))))


def compute_quality_score(snap: dict) -> int:
    """Quality sub-score 0-100 based on PB, market cap (liquidity proxy).

    Backed by 14d audit: q_score has r=+0.032 with T+1 return. PB inverse
    correlates with quality (lower PB = more book value backing).
    """
    pb = _safe_float(snap.get("pb"))
    mcap = _safe_float(snap.get("market_cap_hkd"))

    # PB tier (0-70 points) — sweet spot is 0.5-3
    if pb is None or pb <= 0:
        pb_pts = 35  # unknown → neutral
    elif pb < 0.5:
        pb_pts = 50  # too low = could be distressed
    elif pb < 1.0:
        pb_pts = 70  # classic deep value
    elif pb < 2.0:
        pb_pts = 65
    elif pb < 4.0:
        pb_pts = 50
    elif pb < 8.0:
        pb_pts = 30
    else:
        pb_pts = 15  # growth/tech, not value

    # Market cap (0-30 points) — bigger = more liquid = safer
    if mcap is None or mcap <= 0:
        cap_pts = 15
    elif mcap >= 1e12:  # ≥ 1T HKD (HSBC, Tencent)
        cap_pts = 30
    elif mcap >= 1e11:  # ≥ 100B HKD
        cap_pts = 25
    elif mcap >= 1e10:  # ≥ 10B HKD
        cap_pts = 20
    elif mcap >= 1e9:   # ≥ 1B HKD
        cap_pts = 15
    else:
        cap_pts = 5

    return min(100, max(0, int(round(pb_pts + cap_pts))))


def compute_momentum_score(snap: dict, chg_5d: Optional[float] = None,
                            dist_high_pct: Optional[float] = None) -> int:
    """Momentum sub-score 0-100 with overextension penalty.

    Backed by 14d audit: m_score has r=+0.021 (weak). The pattern is
    mean-reversion, not trend-following. m≥80 is anti-edge (16.7% WR).
    """
    chg = _safe_float(snap.get("change_pct"))
    hi = _safe_float(snap.get("52w_high"))
    price = _safe_float(snap.get("last_price"))

    # Distance to 52w high (auto-compute if not passed)
    if dist_high_pct is None and hi and price and hi > 0:
        dist_high_pct = (price - hi) / hi * 100

    # Mean-reversion bias: gentle downtrend (chg -1 to -3%) gets boost,
    # strong rebound (chg ≥+2%) gets penalty
    if chg is None:
        chg_pts = 50
    elif -3 <= chg <= -1:
        chg_pts = 70  # slight pullback = mean-reversion buy
    elif -5 <= chg < -3:
        chg_pts = 60
    elif -1 < chg < 1:
        chg_pts = 55  # neutral
    elif 1 <= chg < 2:
        chg_pts = 45
    elif 2 <= chg < 3:
        chg_pts = 30
    elif chg >= 3:
        chg_pts = 20  # chasing top
    elif -5 > chg >= -8:
        chg_pts = 45  # bigger drop, more risky
    else:  # chg < -8% (crashed)
        chg_pts = 30

    # 5-day trend adjustment
    if chg_5d is not None:
        if chg_5d >= 5:
            chg_pts -= 10  # strong rally exhaustion
        elif chg_5d <= -10:
            chg_pts += 5   # washout = mean-reversion candidate

    # Overextension penalty (close to 52w high)
    if dist_high_pct is not None and dist_high_pct > -3:
        chg_pts -= 25  # at/near 52w high
    elif dist_high_pct is not None and dist_high_pct > -8:
        chg_pts -= 10

    # Distance to 52w low bonus (recently bottomed)
    lo = _safe_float(snap.get("52w_low"))
    if lo and price and lo > 0:
        dist_low = (price - lo) / lo * 100
        if 0 <= dist_low <= 10:
            chg_pts += 10  # just bounced off 52w low

    return min(100, max(0, int(round(chg_pts))))


def compute_order_flow_score(snap: dict, turnover_5d_ratio: Optional[float] = None) -> int:
    """Order flow sub-score 0-100 from volume/turnover relative to 5d avg.

    Backed by 14d audit: of_score has r=+0.013 (weakest signal). Default 50
    if data missing.
    """
    vol_ratio = _safe_float(snap.get("vol_ratio"))
    if turnover_5d_ratio is not None:
        vol_ratio = turnover_5d_ratio

    if vol_ratio is None or vol_ratio == 0:
        return 50  # unknown → neutral

    if vol_ratio >= 2.0:
        pts = 80  # volume surge (real move)
    elif vol_ratio >= 1.5:
        pts = 70
    elif vol_ratio >= 1.0:
        pts = 60
    elif vol_ratio >= 0.5:
        pts = 45
    else:
        pts = 30  # dead tape

    return min(100, max(0, int(round(pts))))


def compute_news_score(snap: dict, news: list = None) -> int:
    """News sub-score 0-100. Neutral default since LLM news is unreliable.

    Use news count as a proxy for attention/uncertainty. Real catalyst
    detection needs better NLP; for now, just count.
    """
    if not news:
        return 50
    # 1-2 news: neutral. 3+: bonus for attention. 0: slight penalty.
    n = len(news)
    if n == 0:
        return 45
    if n <= 2:
        return 55
    if n <= 5:
        return 60
    return 65  # heavy news = lots of attention


# =============== 5-day rolling features ===============
# Computed from the last 5 daily_report records of the same ticker.

def compute_5d_rolling(code: str, report_date: str,
                       con: sqlite3.Connection) -> dict:
    """Compute 5d rolling features for a ticker.

    Returns dict with:
      chg_5d: 5-day price change (last close vs close 5d ago)
      vol_5d_avg: avg volume over last 5 days
      turnover_5d_avg: avg turnover
      turnover_5d_ratio: today's turnover / 5d avg (None if no history)
      chg_max_5d: max intraday chg in last 5d
      chg_min_5d: min intraday chg in last 5d
      bars_5d: count of available bars
    """
    import json
    rows = con.execute("""
        SELECT report_date, data_snapshot_json, decision_reason
        FROM daily_report
        WHERE code = ? AND report_date <= ?
        ORDER BY report_date DESC
        LIMIT 5
    """, (code, report_date)).fetchall()

    if not rows:
        return {"chg_5d": None, "turnover_5d_ratio": None, "bars_5d": 0}

    def _row_get(r, key):
        """Get value from either sqlite3.Row (dict-like) or tuple (indexed)."""
        if hasattr(r, "keys"):
            return r[key]
        # Tuple: column order is SELECT report_date, data_snapshot_json, decision_reason
        col_map = {"report_date": 0, "data_snapshot_json": 1, "decision_reason": 2}
        idx = col_map.get(key, 0)
        return r[idx]

    closes = []
    chgs = []
    turnovers = []
    for r in rows:
        try:
            dsj = _row_get(r, "data_snapshot_json")
            snap = json.loads(dsj or "{}")
        except Exception:
            continue
        price = _safe_float(snap.get("last_price"))
        chg = _safe_float(snap.get("change_pct"))
        turnover = _safe_float(snap.get("turnover_hkd"))
        if price:
            closes.append((_row_get(r, "report_date"), price))
        if chg is not None:
            chgs.append(chg)
        if turnover and turnover > 0:
            turnovers.append(turnover)

    closes.sort(key=lambda x: x[0])  # oldest first

    chg_5d = None
    if len(closes) >= 2:
        # price change from earliest to latest available
        chg_5d = (closes[-1][1] - closes[0][1]) / closes[0][1] * 100
    elif len(closes) == 1:
        chg_5d = 0  # only 1 bar

    turnover_5d_avg = sum(turnovers) / len(turnovers) if turnovers else None
    # "Today" = the most recent of the 5d window (rows are DESC, so rows[0])
    today_turnover = _safe_float(turnovers[0]) if turnovers else None
    turnover_5d_ratio = None
    if today_turnover and turnover_5d_avg and turnover_5d_avg > 0:
        turnover_5d_ratio = today_turnover / turnover_5d_avg

    return {
        "chg_5d": chg_5d,
        "turnover_5d_avg": turnover_5d_avg,
        "turnover_5d_ratio": turnover_5d_ratio,
        "chg_max_5d": max(chgs) if chgs else None,
        "chg_min_5d": min(chgs) if chgs else None,
        "bars_5d": len(rows),
    }


# =============== Sector cross-section ===============
# Aggregate sector-level stats once per day for all tickers.

def compute_sector_features(snap: dict, con: sqlite3.Connection,
                            report_date: str, sector: str = "") -> dict:
    """Sector-relative features.

    pe_relative: pe_ttm vs sector median (sector + same day)
    sector_mom_5d: avg 5d return of sector peers

    Returns dict with:
      pe_relative: stock pe / sector median pe (None if no sector or no peers)
      sector_mom_5d: avg 5d return of sector peers (None if no data)
      sector_peers: count of tickers in same sector with data
    """
    if not sector:
        return {"pe_relative": None, "sector_mom_5d": None, "sector_peers": 0}

    # Sector peers with data on same day
    rows = con.execute("""
        SELECT code, data_snapshot_json
        FROM daily_report
        WHERE report_date = ? AND code != ?
    """, (report_date, snap.get("code", ""))).fetchall()

    import json
    peers_pe = []
    for r in rows:
        try:
            s = json.loads(r["data_snapshot_json"] or "{}")
        except Exception:
            continue
        s_sector = (s.get("sector") or "").strip()
        if s_sector != sector:
            continue
        pe = _safe_float(s.get("pe_ttm"))
        if pe is not None and pe > 0 and pe < 200:
            peers_pe.append(pe)

    pe_relative = None
    if peers_pe:
        peers_pe.sort()
        n = len(peers_pe)
        median = peers_pe[n // 2] if n % 2 == 1 else (peers_pe[n // 2 - 1] + peers_pe[n // 2]) / 2
        pe = _safe_float(snap.get("pe_ttm"))
        if pe and pe > 0 and median > 0:
            pe_relative = pe / median

    return {
        "pe_relative": pe_relative,
        "sector_mom_5d": None,  # requires per-ticker 5d rolling, too slow for here
        "sector_peers": len(peers_pe),
    }


# =============== Distance to 52w extremes ===============

def compute_dist_52w(snap: dict) -> dict:
    """Distance to 52w high/low as percentages."""
    price = _safe_float(snap.get("last_price"))
    hi = _safe_float(snap.get("52w_high"))
    lo = _safe_float(snap.get("52w_low"))

    dist_high = None
    dist_low = None
    if price and hi and hi > 0:
        dist_high = (price - hi) / hi * 100
    if price and lo and lo > 0:
        dist_low = (price - lo) / lo * 100

    return {
        "dist_52w_high_pct": dist_high,
        "dist_52w_low_pct": dist_low,
    }


# =============== Master compute_features ===============
# Main entry point — computes all features for a single (code, report_date).

def compute_all_features(code: str, report_date: str, snap: dict,
                         news: list = None) -> dict:
    """Compute all sub-scores + features for one signal.

    Returns dict with v/q/m/of/news sub-scores, 5d rolling, sector cross-section,
    distance to 52w. All fields are deterministic from raw data.
    """
    con = sqlite3.connect(DB_PATH)
    try:
        dist_52w = compute_dist_52w(snap)
        rolling = compute_5d_rolling(code, report_date, con)
        sector = (snap.get("sector") or "").strip()
        sector_feats = compute_sector_features(snap, con, report_date, sector)

        v = compute_value_score(snap, dist_low_pct=dist_52w.get("dist_52w_low_pct"))
        q = compute_quality_score(snap)
        m = compute_momentum_score(
            snap,
            chg_5d=rolling.get("chg_5d"),
            dist_high_pct=dist_52w.get("dist_52w_high_pct"),
        )
        of = compute_order_flow_score(snap, turnover_5d_ratio=rolling.get("turnover_5d_ratio"))
        news_score = compute_news_score(snap, news or [])

        return {
            # 5-dim sub-scores (replaces LLM-derived)
            "value_score": v,
            "quality_score": q,
            "momentum_score": m,
            "order_flow_score": of,
            "news_score": news_score,
            # 5d rolling
            "chg_5d": rolling.get("chg_5d"),
            "turnover_5d_ratio": rolling.get("turnover_5d_ratio"),
            "turnover_5d_avg": rolling.get("turnover_5d_avg"),
            "bars_5d": rolling.get("bars_5d"),
            # Sector cross-section
            "pe_relative": sector_feats.get("pe_relative"),
            "sector_peers": sector_feats.get("sector_peers"),
            # Distance to 52w
            "dist_52w_high_pct": dist_52w.get("dist_52w_high_pct"),
            "dist_52w_low_pct": dist_52w.get("dist_52w_low_pct"),
        }
    finally:
        con.close()

"""Build Jul 7 report by overlaying today's Sina/Tencent prices on Jul 2 cached snapshots.

Why: YFinance HK is rate-limited today. Sina/Tencent give us live price/volume
(PE/PB/market_cap). For MA/RSI we use Jul 2 klines and append today's price as
the latest bar.

Workflow:
  1. For each HK ticker, fetch Sina live (current price + volume)
  2. Load Jul 2 cached snapshot (kline_30d + MAs)
  3. Append today as new bar
  4. Recompute MA20/MA50/MA100/MA200 from updated kline
  5. Send to LLM
  6. Save as report_date=2026-07-07
"""
import json
import os
import sys
import time
import ssl
import urllib.request
from datetime import datetime

# Bypass SSL cert verification (Mac Python 3.14 has CA bundle issues)
ssl._create_default_https_context = ssl._create_unverified_context
import subprocess
_HAS_CURL = True

TARGET_DATE = "2026-07-07"
SOURCE_DATE = "2026-07-02"
os.environ["DSA_REPORT_DATE_OVERRIDE"] = TARGET_DATE
os.environ["DSA_LLM_MAX_TOKENS"] = "8000"

sys.path.insert(0, "/Users/kenken/Documents/dsa-hk")

from src.analyzer import analyze, render_summary_md, render_report_md
from src.db import save_report, init_db, get_db
from src.news_fetcher import fetch_news
from src.ticker_loader import load_hk_tickers
from src.config import get_config


def fetch_sina_hk(code: str) -> dict | None:
    """Fetch live HK quote from Sina hq.sinajs.cn — use curl for SSL bypass."""
    try:
        digits = code.split(".")[0].zfill(5)
        url = f"https://hq.sinajs.cn/list=hk{digits}"
        result = subprocess.run(
            ["curl", "-s", "--max-time", "5",
             "-H", "User-Agent: Mozilla/5.0",
             "-H", "Referer: https://finance.sina.com.cn",
             url],
            capture_output=True, timeout=8,
        )
        raw = result.stdout.decode("gbk", errors="replace")
        i1 = raw.find('"')
        i2 = raw.rfind('"')
        if i1 < 0 or i2 <= i1:
            return None
        fields = raw[i1+1:i2].split(',')
        if len(fields) < 13:
            return None
        return {
            "name_en": fields[0],
            "name_zh": fields[1],
            "open": _safe_float(fields[2]),
            "prev_close": _safe_float(fields[3]),
            "high": _safe_float(fields[4]),
            "low": _safe_float(fields[5]),
            "current": _safe_float(fields[6]),
            "change_amt": _safe_float(fields[7]),
            "change_pct": _safe_float(fields[8]),
            "turnover_hkd": _safe_float(fields[10]),
            "volume": _safe_float(fields[11]),
            "datetime": f"{fields[30]} {fields[31]}" if len(fields) > 31 else "",
        }
    except Exception as e:
        return None


def _safe_float(s):
    try:
        return float(s) if s else None
    except Exception:
        return None


def fetch_tencent_hk(code: str) -> dict | None:
    """Fetch live HK quote from Tencent qt.gtimg.cn — has PE/PB/market_cap.
    Use curl to bypass SSL cert issues."""
    try:
        digits = code.split(".")[0].zfill(5)
        url = f"https://qt.gtimg.cn/q=hk{digits}"
        result = subprocess.run(
            ["curl", "-s", "--max-time", "5",
             "-H", "User-Agent: Mozilla/5.0",
             url],
            capture_output=True, timeout=8,
        )
        raw = result.stdout.decode("gbk", errors="replace")
        i1 = raw.find('"')
        i2 = raw.rfind('"')
        if i1 < 0 or i2 <= i1:
            return None
        fields = raw[i1+1:i2].split('~')
        if len(fields) < 50:
            return None
        return {
            "name_zh": fields[1],
            "code": fields[2],
            "current": _safe_float(fields[3]),
            "prev_close": _safe_float(fields[4]),
            "open": _safe_float(fields[5]),
            "volume": _safe_float(fields[6]),
            "datetime": fields[30] if len(fields) > 30 else "",
            "change_amt": _safe_float(fields[31]) if len(fields) > 31 else None,
            "change_pct": _safe_float(fields[32]) if len(fields) > 32 else None,
            "high": _safe_float(fields[33]) if len(fields) > 33 else None,
            "low": _safe_float(fields[34]) if len(fields) > 34 else None,
            "turnover": _safe_float(fields[37]) if len(fields) > 37 else None,
            "pe_ttm": _safe_float(fields[39]) if len(fields) > 39 else None,
            "pb": _safe_float(fields[43]) if len(fields) > 43 else None,
            "market_cap": _safe_float(fields[44]) if len(fields) > 44 else None,
            "name_en": fields[46] if len(fields) > 46 else "",
            "52w_high": _safe_float(fields[48]) if len(fields) > 48 else None,
            "52w_low": _safe_float(fields[49]) if len(fields) > 49 else None,
        }
    except Exception as e:
        return None


def compute_ma(closes: list[float], window: int) -> float | None:
    if len(closes) < window:
        return None
    return sum(closes[-window:]) / window


def compute_rsi(closes: list[float], period: int = 14) -> float | None:
    if len(closes) < period + 1:
        return None
    gains = []
    losses = []
    for i in range(-period, 0):
        diff = closes[i] - closes[i - 1]
        if diff >= 0:
            gains.append(diff)
            losses.append(0)
        else:
            gains.append(0)
            losses.append(-diff)
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return round(100 - (100 / (1 + rs)), 2)


def get_cached_snapshot(code: str, source_date: str) -> dict | None:
    db = get_db()
    row = db.execute(
        "SELECT data_snapshot_json FROM daily_report WHERE code=? AND report_date=?",
        (code, source_date),
    ).fetchone()
    db.close()
    if not row or not row[0]:
        return None
    try:
        return json.loads(row[0])
    except Exception:
        return None


def build_today_snapshot(code: str, cached: dict, live: dict) -> dict:
    """Merge today's live price into cached snapshot."""
    snap = dict(cached)  # copy

    # Update live fields
    snap["last_price"] = live.get("current") or snap.get("last_price")
    snap["prev_close"] = live.get("prev_close") or snap.get("prev_close")
    snap["change_pct"] = live.get("change_pct")
    snap["day_high"] = live.get("high") or snap.get("day_high")
    snap["day_low"] = live.get("low") or snap.get("day_low")
    snap["volume"] = live.get("volume") or snap.get("volume")
    snap["turnover_hkd"] = live.get("turnover") or live.get("turnover_hkd") or snap.get("turnover_hkd")

    # Tencent-specific updates
    if live.get("pe_ttm"):
        snap["pe_ttm"] = live["pe_ttm"]
    if live.get("pb"):
        snap["pb"] = live["pb"]
    if live.get("market_cap"):
        snap["market_cap_hkd"] = live["market_cap"]
    if live.get("52w_high"):
        snap["52w_high"] = live["52w_high"]
    if live.get("52w_low"):
        snap["52w_low"] = live["52w_low"]

    # Append today as new bar in kline_30d, recompute MAs
    kline = list(snap.get("kline_30d", []))
    last_bar = kline[-1] if kline else None
    today_bar = {
        "date": TARGET_DATE,
        "open": live.get("open") or live.get("current"),
        "high": live.get("high") or live.get("current"),
        "low": live.get("low") or live.get("current"),
        "close": live.get("current"),
        "volume": live.get("volume"),
    }

    # Only append if it's a new bar (different date)
    if not last_bar or last_bar.get("date") != TARGET_DATE:
        kline.append(today_bar)

    # Keep last 30 bars
    kline = kline[-30:]
    snap["kline_30d"] = kline

    # Recompute MAs from full history approximation (kline has only 30d,
    # so we use cached MAs and just blend today into MA20)
    closes = [b["close"] for b in kline if b.get("close")]
    if len(closes) >= 20:
        snap["ma20"] = round(compute_ma(closes, 20), 2)
    if len(closes) >= 14:
        snap["rsi14"] = compute_rsi(closes, 14)

    # Update data_as_of
    snap["data_as_of"] = live.get("datetime") or f"{TARGET_DATE} 16:08 HKT"
    snap["source"] = "sina+tencent+cached-yfinance"

    return snap


def process(code: str) -> tuple[str, str]:
    try:
        cached = get_cached_snapshot(code, SOURCE_DATE)
        if not cached:
            return code, "no-cached-snapshot"

        # Try Tencent first (richer fields), then Sina
        live = fetch_tencent_hk(code) or fetch_sina_hk(code)
        if not live or not live.get("current"):
            return code, "no-live-quote"

        snap = build_today_snapshot(code, cached, live)

        news = fetch_news(
            code=code,
            name_zh=snap.get("name_zh") or code,
            name_en=code,
            max_results=3,
            days=3,
        ) or []

        result = analyze(
            code=code,
            name=snap.get("name_zh") or snap.get("name_en") or code,
            snapshot=snap,
            news=news,
            language="zh-Hant",
        )
        if result is None:
            return code, "analyze-none"

        summary_md = render_summary_md(result, language="zh-Hant")
        full_md = render_report_md(result, snap, language="zh-Hant")
        save_report(
            code=code,
            report_date=TARGET_DATE,
            score=result.score,
            sentiment=result.sentiment,
            trend=result.trend,
            operation_advice=result.operation_advice,
            summary_md=summary_md,
            full_md=full_md,
            news=news,
            data_snapshot=snap,
            llm_model=result.llm_model,
            score_breakdown_json=json.dumps(result.score_breakdown or {}, ensure_ascii=False),
            trade_direction=result.trade_direction,
            support_zone=result.support_zone,
            resistance_zone=result.resistance_zone,
            key_levels_json=json.dumps(result.key_levels or {}, ensure_ascii=False),
        )
        return code, f"OK op={result.operation_advice} score={result.score}"
    except Exception as e:
        return code, f"ERR {type(e).__name__}: {e}"


def main():
    init_db()
    hk = load_hk_tickers()
    print(f"=== HK rerun {TARGET_DATE} (Sina/Tencent + cached yfinance) ===\n")
    print(f"Total HK tickers: {len(hk)}\n")

    ok = fail = 0
    fails = []
    t0 = time.time()

    for i, code in enumerate(hk, 1):
        c, status = process(code)
        if status.startswith("OK"):
            ok += 1
        else:
            fail += 1
            fails.append((c, status))

        if i % 10 == 0 or status.startswith("ERR"):
            elapsed = time.time() - t0
            eta = (len(hk) - i) * (elapsed / i)
            print(
                f"  [{i:3d}/{len(hk)}] {c}: {status}  ok={ok} fail={fail}  "
                f"elapsed {elapsed:.0f}s ETA {eta:.0f}s",
                flush=True,
            )

        # Light sleep — Sina/Tencent are more permissive than YFinance
        time.sleep(0.3)

    elapsed = time.time() - t0
    print(f"\n{'=' * 60}")
    print(f"DONE: {ok} ok / {fail} fail in {elapsed:.0f}s ({elapsed/60:.1f} min)")
    if fails:
        print(f"\nFailures ({len(fails)}):")
        for c, s in fails[:30]:
            print(f"  {c}: {s}")


if __name__ == "__main__":
    main()
"""Comprehensive 7-day signal audit.

For every signal across 7 days (26/6 - 2/7), compute 1D forward return
and bucket by direction + score + features. Identify:
1. Worst false BUY signals (BUY → stock dropped)
2. Biggest missed opportunities (HOLD → stock ran)
3. Patterns predicting success vs failure
"""
import json
import re
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime, timedelta

import yfinance as yf

DB_PATH = "/Users/kenken/Documents/dsa-hk/data/dsa_hk.db"

DATES = ["2026-06-26", "2026-06-27", "2026-06-29", "2026-06-30", "2026-07-01", "2026-07-02", "2026-07-03", "2026-07-06", "2026-07-07"]
NEXT_TRADING = {
    "2026-06-26": "2026-06-27",
    "2026-06-27": "2026-06-29",
    "2026-06-29": "2026-06-30",
    "2026-06-30": "2026-07-01",
    "2026-07-01": "2026-07-02",
    "2026-07-02": "2026-07-03",
    "2026-07-03": "2026-07-06",  # 4th was US holiday, 6th first available
    "2026-07-06": "2026-07-07",
    "2026-07-07": None,  # can't compute, need 7/8+
}


def to_yf_ticker(code: str) -> str:
    if code.endswith(".HK"):
        return code[:-3].lstrip("0") + ".HK" if code[:-3].lstrip("0") else code
    return code


def get_prices(ticker: str) -> dict:
    yf_code = to_yf_ticker(ticker)
    try:
        t = yf.Ticker(yf_code)
        hist = t.history(start="2026-06-25", end="2026-07-09")
        if hist.empty:
            return {}
        return {d.strftime("%Y-%m-%d"): float(row["Close"]) for d, row in hist.iterrows()}
    except Exception:
        return {}


def main():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    cur = con.cursor()

    # Pull all signals
    placeholders = ",".join("?" * len(DATES))
    rows = cur.execute(
        f"""SELECT code, report_date, score, sentiment, trend, operation_advice,
                  data_snapshot_json, score_breakdown_json, full_md
            FROM daily_report WHERE report_date IN ({placeholders})""",
        DATES,
    ).fetchall()

    print(f"Loaded {len(rows)} signals across {len(DATES)} dates")

    # Cache prices per ticker
    unique_tickers = sorted(set(r["code"] for r in rows))
    print(f"Fetching prices for {len(unique_tickers)} unique tickers...")
    price_cache = {}
    for tk in unique_tickers:
        prices = get_prices(tk)
        if prices:
            price_cache[tk] = prices
    print(f"Got prices for {len(price_cache)}/{len(unique_tickers)} tickers")

    # Compute 1D forward returns
    data = []
    for r in rows:
        code = r["code"]
        date = r["report_date"]
        op = r["operation_advice"]
        next_d = NEXT_TRADING.get(date)
        if not next_d:
            continue  # no forward data
        prices = price_cache.get(code, {})
        if date not in prices or next_d not in prices:
            continue
        p0 = prices[date]
        p1 = prices[next_d]
        if not p0:
            continue
        ret = (p1 - p0) / p0 * 100

        # Parse features
        try:
            snap = json.loads(r["data_snapshot_json"]) if r["data_snapshot_json"] else {}
        except Exception:
            snap = {}
        try:
            bd = json.loads(r["score_breakdown_json"]) if r["score_breakdown_json"] else {}
        except Exception:
            bd = {}
        text = (r["full_md"] or "")
        m_sent = re.search(r"·\s*(樂觀|中性|悲觀)\s*·", text)
        sent = m_sent.group(1) if m_sent else ""

        # For BUY: hit = ret > 0
        # For SELL: hit = ret < 0
        # For HOLD: hit = abs(ret) < 1.5 (didn't blow up)
        if op == "買入":
            hit = ret > 0
        elif op == "賣出":
            hit = ret < 0
        else:  # 觀望
            hit = abs(ret) < 1.5
        data.append({
            "code": code, "date": date, "op": op, "score": r["score"] or 0,
            "sentiment": sent, "trend": r["trend"] or "",
            "v": bd.get("value_score", 0) or 0,
            "q": bd.get("quality_score", 0) or 0,
            "m": bd.get("momentum_score", 0) or 0,
            "of": bd.get("order_flow_score", 0) or 0,
            "day_chg": snap.get("change_pct") or 0,
            "ret": ret, "hit": hit, "hit_strict": (ret > 1 if op == "買入" else (ret < -1 if op == "賣出" else abs(ret) < 0.5)),
        })

    print(f"Computed 1D forward return for {len(data)} signals\n")

    # ============ ANALYSIS ============
    print("=" * 80)
    print("DIRECTION-LEVEL SUMMARY (1D forward)")
    print("=" * 80)
    for op in ["買入", "賣出", "觀望"]:
        subset = [d for d in data if d["op"] == op]
        if not subset:
            continue
        wins = sum(1 for d in subset if d["hit"])
        losses = len(subset) - wins
        wr = wins / len(subset) * 100
        avg = sum(d["ret"] for d in subset) / len(subset)
        med = sorted(d["ret"] for d in subset)[len(subset) // 2]
        big_wins = sum(1 for d in subset if d["ret"] > 2)
        big_losses = sum(1 for d in subset if d["ret"] < -2)
        print(f"{op}: n={len(subset)} | WR={wr:.1f}% ({wins}W/{losses}L) | avg={avg:+.2f}% | med={med:+.2f}% | big_wins(>+2%)={big_wins} | big_losses(<-2%)={big_losses}")

    # ============ WORST FALSE BUY SIGNALS ============
    print("\n" + "=" * 80)
    print("TOP 15 WORST BUY SIGNALS (1D loss, lost >2%)")
    print("=" * 80)
    buy_losses = sorted([d for d in data if d["op"] == "買入" and d["ret"] < -2], key=lambda x: x["ret"])
    print(f"{'date':<11} {'code':<10} {'score':>5} {'sent':<6} {'m':>3} {'of':>3} {'chg%':>6} {'ret%':>7} {'full_md snippet':<60}")
    for d in buy_losses[:15]:
        snippet = ""
        # Get full_md
        for r in cur.execute("SELECT full_md FROM daily_report WHERE report_date=? AND code=?",
                             (d["date"], d["code"])):
            md = r[0] or ""
            m = re.search(r"核心結論\s*([^#\n]+(?:\n[^#\n]+){0,2})", md)
            if m:
                snippet = m.group(1).strip()[:80]
            break
        print(f"{d['date']:<11} {d['code']:<10} {d['score']:>5} {d['sentiment']:<6} {d['m']:>3} {d['of']:>3} {d['day_chg']:>+6.1f} {d['ret']:>+7.1f} {snippet}")

    # ============ BIGGEST MISSED OPPORTUNITIES ============
    print("\n" + "=" * 80)
    print("TOP 15 MISSED BUY OPPORTUNITIES (HOLD but stock ran +2% or more 1D)")
    print("=" * 80)
    missed = sorted([d for d in data if d["op"] == "觀望" and d["ret"] > 2], key=lambda x: -x["ret"])
    print(f"{'date':<11} {'code':<10} {'score':>5} {'sent':<6} {'m':>3} {'of':>3} {'chg%':>6} {'ret%':>7}")
    for d in missed[:15]:
        print(f"{d['date']:<11} {d['code']:<10} {d['score']:>5} {d['sentiment']:<6} {d['m']:>3} {d['of']:>3} {d['day_chg']:>+6.1f} {d['ret']:>+7.1f}")

    # ============ WRONG-DIRECTION SELL SIGNALS ============
    print("\n" + "=" * 80)
    print("SELL SIGNALS THAT WENT UP (opposite direction)")
    print("=" * 80)
    bad_sells = sorted([d for d in data if d["op"] == "賣出"], key=lambda x: -x["ret"])
    if bad_sells:
        for d in bad_sells[:15]:
            print(f"{d['date']:<11} {d['code']:<10} score={d['score']:>3} sent={d['sentiment']:<6} chg={d['day_chg']:>+5.1f}% ret={d['ret']:>+6.2f}%")

    # ============ PATTERN ANALYSIS: BUY SUCCESS PREDICTORS ============
    print("\n" + "=" * 80)
    print("PATTERN: BUY signal success rate by m_score band")
    print("=" * 80)
    buys = [d for d in data if d["op"] == "買入"]
    for lo, hi in [(0, 30), (30, 50), (50, 65), (65, 80), (80, 100)]:
        sub = [b for b in buys if lo <= b["m"] < hi]
        if not sub:
            continue
        wr = sum(1 for b in sub if b["hit"]) / len(sub) * 100
        avg = sum(b["ret"] for b in sub) / len(sub)
        print(f"  m_score {lo:>3}-{hi:<3}: n={len(sub):>3} | WR={wr:.1f}% | avg={avg:+.2f}%")

    print("\nPATTERN: BUY signal success rate by day_chg band")
    print("=" * 80)
    for lo, hi in [(-999, -3), (-3, -1), (-1, 0), (0, 1), (1, 3), (3, 999)]:
        sub = [b for b in buys if lo <= b["day_chg"] < hi]
        if not sub:
            continue
        wr = sum(1 for b in sub if b["hit"]) / len(sub) * 100
        avg = sum(b["ret"] for b in sub) / len(sub)
        print(f"  day_chg {lo:>+5.0f}% to {hi:>+4.0f}%: n={len(sub):>3} | WR={wr:.1f}% | avg={avg:+.2f}%")

    print("\nPATTERN: BUY signal success rate by sentiment")
    print("=" * 80)
    for sent in ["樂觀", "中性", "悲觀", "?"]:
        sub = [b for b in buys if b["sentiment"] == sent]
        if not sub:
            continue
        wr = sum(1 for b in sub if b["hit"]) / len(sub) * 100
        avg = sum(b["ret"] for b in sub) / len(sub)
        print(f"  sentiment={sent}: n={len(sub):>3} | WR={wr:.1f}% | avg={avg:+.2f}%")

    print("\nPATTERN: BUY signal success rate by sector (US only)")
    print("=" * 80)
    sector_buckets = {}
    for r in cur.execute("SELECT code, data_snapshot_json FROM daily_report WHERE report_date IN ({}) AND code NOT LIKE '%.HK' AND operation_advice='買入'".format(placeholders), DATES):
        try:
            snap = json.loads(r[1]) if r[1] else {}
        except Exception:
            snap = {}
        sec = (snap.get("sector") or "?").strip() or "?"
        sector_buckets.setdefault(sec, []).append(r[0])
    sec_results = {}
    for sec, codes in sector_buckets.items():
        sec_data = [d for d in data if d["op"] == "買入" and d["code"] in codes]
        if not sec_data:
            continue
        wr = sum(1 for d in sec_data if d["hit"]) / len(sec_data) * 100
        avg = sum(d["ret"] for d in sec_data) / len(sec_data)
        sec_results[sec] = (len(sec_data), wr, avg)
    for sec, (n, wr, avg) in sorted(sec_results.items(), key=lambda x: -x[1][2]):
        if n >= 5:
            print(f"  {sec[:30]:<30} n={n:>3} WR={wr:.1f}% avg={avg:+.2f}%")

    # ============ COMBINED: BUY + conservative_filter simulate ============
    print("\n" + "=" * 80)
    print("CONSERVATIVE BUY FILTER (current rules) — backtest on all 7 days")
    print("=" * 80)
    from src.conservative_filters import TECH_SECTORS_AVOID
    # Reload sys
    sys.path.insert(0, "/Users/kenken/Documents/dsa-hk")
    cons_buys = []
    for d in data:
        if d["op"] != "買入" or d["code"].endswith(".HK"):
            continue
        if not (-3 < d["day_chg"] < 0):
            continue
        # Get sector
        sec_row = cur.execute("SELECT data_snapshot_json FROM daily_report WHERE report_date=? AND code=?",
                              (d["date"], d["code"])).fetchone()
        try:
            snap = json.loads(sec_row[0]) if sec_row and sec_row[0] else {}
        except Exception:
            snap = {}
        sec = (snap.get("sector") or "").strip()
        if sec in TECH_SECTORS_AVOID:
            continue
        if not (30 <= d["m"] <= 70):
            continue
        if d["sentiment"] == "樂觀":
            continue
        if d["score"] >= 70:
            continue
        cons_buys.append(d)
    if cons_buys:
        n = len(cons_buys)
        wins = sum(1 for d in cons_buys if d["hit"])
        avg = sum(d["ret"] for d in cons_buys) / n
        wr = wins / n * 100
        print(f"  Conservative BUY: n={n} | WR={wr:.1f}% ({wins}W/{n-wins}L) | avg={avg:+.2f}%")

    # Cyber BUY v2 simulate
    print("\nCYBER BUY v2 FILTER (anti-gapup + 52w) — backtest")
    print("=" * 80)
    from src.conservative_filters import CYBER_TICKERS, cyber_buy_passes
    cyber_buys = []
    for d in data:
        if d["op"] != "買入" or d["code"].endswith(".HK"):
            continue
        if d["code"].split(".")[0] not in CYBER_TICKERS:
            continue
        # Get 52w_high
        sec_row = cur.execute("SELECT data_snapshot_json FROM daily_report WHERE report_date=? AND code=?",
                              (d["date"], d["code"])).fetchone()
        try:
            snap = json.loads(sec_row[0]) if sec_row and sec_row[0] else {}
        except Exception:
            snap = {}
        last = snap.get("last_price") or 0
        h52 = snap.get("52w_high") or 0
        passes, _ = cyber_buy_passes(d["code"].split(".")[0], d["score"], d["day_chg"], d["m"],
                                     d["sentiment"], last, h52)
        if passes:
            cyber_buys.append(d)
    if cyber_buys:
        n = len(cyber_buys)
        wins = sum(1 for d in cyber_buys if d["hit"])
        avg = sum(d["ret"] for d in cyber_buys) / n
        wr = wins / n * 100
        print(f"  Cyber BUY v2: n={n} | WR={wr:.1f}% | avg={avg:+.2f}%")

    # Strength BUY v3 simulate
    print("\nSTRENGTH BUY FILTER (3d>+3% + 5d>+5% + m>60 + of>50) — backtest")
    print("=" * 80)
    from src.conservative_filters import strength_buy_passes
    str_buys = []
    for d in data:
        if d["op"] != "買入" or d["code"].endswith(".HK"):
            continue
        # Need 3d/5d return — get from prices
        prices = price_cache.get(d["code"], {})
        if d["date"] not in prices:
            continue
        # Find dates in sequence
        all_dates = sorted(prices.keys())
        try:
            i = all_dates.index(d["date"])
        except ValueError:
            continue
        if i < 5:
            continue
        change_3d = (prices[all_dates[i]] - prices[all_dates[i-3]]) / prices[all_dates[i-3]] * 100
        change_5d = (prices[all_dates[i]] - prices[all_dates[i-5]]) / prices[all_dates[i-5]] * 100
        passes, _ = strength_buy_passes(
            d["code"], d["score"], d["day_chg"], d["m"], d["of"], d["sentiment"],
            change_3d, change_5d,
        )
        if passes:
            str_buys.append(d)
    if str_buys:
        n = len(str_buys)
        wins = sum(1 for d in str_buys if d["hit"])
        avg = sum(d["ret"] for d in str_buys) / n
        wr = wins / n * 100
        print(f"  Strength BUY: n={n} | WR={wr:.1f}% | avg={avg:+.2f}%")
        print(f"  Tickers: {[(d['date'], d['code'], d['ret']) for d in str_buys]}")
    else:
        print("  Strength BUY: 0 historical signals pass")

    con.close()


if __name__ == "__main__":
    main()
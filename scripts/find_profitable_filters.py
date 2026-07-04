"""Find features that correlate with successful signals.

Loads all 875 US signals across 6 days, joins with yfinance future prices,
computes hit/miss for each, then buckets by various features to find
which filters improve hit rate consistently.
"""
import json
import re
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

import yfinance as yf

DB_PATH = Path(__file__).parent.parent / "data" / "dsa_hk.db"
DATES = ["2026-06-26", "2026-06-27", "2026-06-29", "2026-06-30", "2026-07-01", "2026-07-02"]
NEXT_TRADING = {
    "2026-06-26": "2026-06-29",
    "2026-06-27": "2026-06-29",
    "2026-06-29": "2026-06-30",
    "2026-06-30": "2026-07-01",
    "2026-07-01": "2026-07-02",
    "2026-07-02": None,
}

CAUTION_RE = re.compile(
    r'(不宜追[入高]|不建議追[入高]|偏離\s*MA\d+\s*\d+\.?\d*%|'
    r'RSI\d*\s*[6-9]\d|RSI\d*\s*[1-9]\d{2}|'
    r'PE\s*TTM\s*\d+\s*倍.{0,8}貴|估值.{0,4}貴|極為昂貴|估值.{0,4}高|'
    r'過熱|超買|止蝕位.{0,4}緊|止損.{0,4}緊|風險回報比.{0,4}低)'
)


def parse_confidence(full_md: str) -> str:
    m = re.search(r'信心\s*([高中低])', full_md or "")
    return m.group(1) if m else "?"


def fetch_prices(tickers: list[str]) -> dict[str, dict[str, float]]:
    out = {}
    for tk in tickers:
        try:
            t = yf.Ticker(tk)
            hist = t.history(start="2026-06-26", end="2026-07-05", auto_adjust=False)
            if hist.empty:
                continue
            out[tk] = {d.strftime("%Y-%m-%d"): float(row["Close"]) for d, row in hist.iterrows()}
        except Exception:
            pass
    return out


def main():
    con = sqlite3.connect(str(DB_PATH))
    cur = con.cursor()
    rows = cur.execute(
        f"""SELECT code, report_date, score, operation_advice, full_md, summary_md,
                  json_extract(score_breakdown_json, '$.value_score') as v,
                  json_extract(score_breakdown_json, '$.quality_score') as q,
                  json_extract(score_breakdown_json, '$.momentum_score') as m,
                  json_extract(score_breakdown_json, '$.order_flow_score') as of
            FROM daily_report
            WHERE report_date IN ({",".join("?" * len(DATES))})
              AND code NOT LIKE '%.HK'""",
        DATES,
    ).fetchall()
    print(f"Total US signals: {len(rows)}")

    tickers = sorted(set(r[0] for r in rows))
    print(f"Fetching prices for {len(tickers)} tickers...")
    price_map = fetch_prices(tickers)
    print(f"Got prices for {len(price_map)}/{len(tickers)} tickers\n")

    # Build dataset with hit/miss
    data = []
    for code, date, score, op, full_md, summary_md, v, q, m, of in rows:
        next_d = NEXT_TRADING.get(date)
        if next_d is None:
            continue
        pm = price_map.get(code)
        if not pm or date not in pm or next_d not in pm:
            continue
        p0, p1 = pm[date], pm[next_d]
        if not p0:
            continue
        chg = (p1 - p0) / p0 * 100
        if op == "買入":
            hit = chg > 0
        elif op == "賣出":
            hit = chg < 0
        else:
            hit = abs(chg) < 1.5
        # Extract sentiment/trend from full_md header
        sent = "?"
        trend = "?"
        if full_md:
            m_sent = re.search(r'·\s*(樂觀|中性|悲觀)\s*·', full_md)
            if m_sent:
                sent = m_sent.group(1)
            m_trend = re.search(r'·\s*(看多|震盪|看空)\s*·', full_md)
            if m_trend:
                trend = m_trend.group(1)
        confidence = parse_confidence(full_md)
        has_caution = bool(CAUTION_RE.search(summary_md or ""))
        data.append({
            "code": code, "date": date, "op": op, "score": score,
            "v": v or 0, "q": q or 0, "m": m or 0, "of": of or 0,
            "sent": sent, "trend": trend, "conf": confidence,
            "caution": has_caution, "chg": chg, "hit": hit,
        })

    print(f"Dataset: {len(data)} signals with hit/miss computed\n")

    def bucket(name, key_fn, ops=("買入", "賣出")):
        print(f"\n--- {name} ---")
        buckets = {}
        for d in data:
            if d["op"] not in ops:
                continue
            k = key_fn(d)
            if k not in buckets:
                buckets[k] = [0, 0, []]  # n, hit, chgs
            buckets[k][0] += 1
            if d["hit"]:
                buckets[k][1] += 1
            buckets[k][2].append(d["chg"])
        print(f"{'bucket':<22} {'n':>5} {'hit':>5} {'rate':>7} {'avg_chg':>9}")
        for k in sorted(buckets.keys(), key=lambda x: str(x)):
            n, hit, chgs = buckets[k]
            rate = hit / n * 100
            avg = sum(chgs) / len(chgs)
            print(f"{str(k):<22} {n:>5} {hit:>5} {rate:>6.1f}% {avg:>+8.2f}%")

    # === Per-feature analysis ===
    bucket("By score (all actionable)", lambda d: f"{int(d['score']//10)*10}-{int(d['score']//10)*10+9}")
    bucket("By m_score (momentum)", lambda d: f"m {int(d['m']//10)*10}-{int(d['m']//10)*10+9}")
    bucket("By of_score (order_flow)", lambda d: f"of {int(d['of']//10)*10}-{int(d['of']//10)*10+9}")
    bucket("By confidence", lambda d: d["conf"])
    bucket("By trend (actionable only)", lambda d: d["trend"])
    bucket("By sentiment (actionable only)", lambda d: d["sent"])
    bucket("By caution presence", lambda d: "caution" if d["caution"] else "clean")
    bucket("By trade_direction inferred from trend", lambda d: ("看多" if d["trend"]=="看多" else "看空" if d["trend"]=="看空" else "震盪"))

    # === Combined filter analysis ===
    print("\n\n=== Combined filter candidates ===")
    print("Testing: BUY only when confidence=高 AND trend=看多 AND no caution")

    def test_filter(filter_fn, label):
        passing = [d for d in data if d["op"] in ("買入", "賣出") and filter_fn(d)]
        if not passing:
            print(f"  {label}: NO data")
            return
        n = len(passing)
        hit = sum(1 for d in passing if d["hit"])
        avg = sum(d["chg"] for d in passing) / n
        print(f"  {label}: n={n}, hit={hit}, rate={hit/n*100:.1f}%, avg_chg={avg:+.2f}%")

    test_filter(lambda d: d["conf"] == "高", "conf=高")
    test_filter(lambda d: d["conf"] in ("高", "中"), "conf∈高,中")
    test_filter(lambda d: d["conf"] == "高" and not d["caution"], "conf=高 AND no caution")
    test_filter(lambda d: d["conf"] == "高" and d["trend"] == "看多" and d["op"] == "買入", "BUY: conf=高 + trend=看多")
    test_filter(lambda d: d["op"] == "買入" and d["m"] >= 50 and d["m"] <= 75, "BUY: m_score 50-75 (avoid extended)")
    test_filter(lambda d: d["op"] == "買入" and d["m"] >= 60 and d["m"] <= 80 and not d["caution"], "BUY: m 60-80 + no caution")
    test_filter(lambda d: d["op"] == "賣出" and d["m"] <= 40 and d["trend"] == "看空", "SELL: m≤40 + trend=看空")
    test_filter(lambda d: d["op"] == "買入" and d["of"] >= 50, "BUY: of≥50 (money flowing in)")
    test_filter(lambda d: d["op"] == "買入" and d["of"] >= 60 and d["m"] >= 55, "BUY: of≥60 + m≥55 (strong flow + momentum)")
    test_filter(lambda d: d["op"] == "賣出" and d["of"] <= 40, "SELL: of≤40 (money flowing out)")

    # Score threshold tuning
    print("\n\n=== Score threshold sweep (BUY only) ===")
    print(f"{'min_score':>10} {'n':>5} {'hit':>5} {'rate':>7} {'avg_chg':>9}")
    for thresh in [40, 45, 50, 55, 60, 65, 70]:
        passing = [d for d in data if d["op"] == "買入" and d["score"] >= thresh]
        if not passing:
            continue
        n = len(passing)
        hit = sum(1 for d in passing if d["hit"])
        avg = sum(d["chg"] for d in passing) / n
        print(f"{thresh:>10} {n:>5} {hit:>5} {hit/n*100:>6.1f}% {avg:>+8.2f}%")

    print("\n=== Score ceiling sweep (SELL only — keep sells with LOW scores) ===")
    print(f"{'max_score':>10} {'n':>5} {'hit':>5} {'rate':>7} {'avg_chg':>9}")
    for thresh in [40, 35, 30, 25, 20, 15]:
        passing = [d for d in data if d["op"] == "賣出" and d["score"] <= thresh]
        if not passing:
            continue
        n = len(passing)
        hit = sum(1 for d in passing if d["hit"])
        avg = sum(d["chg"] for d in passing) / n
        print(f"{thresh:>10} {n:>5} {hit:>5} {hit/n*100:>6.1f}% {avg:>+8.2f}%")


if __name__ == "__main__":
    main()
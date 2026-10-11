#!/usr/bin/env python3
"""用 Kenneth 真實 HK v1 訊號 + 誠實 fill 結果，測蠟燭形態有冇增益。

資料來源（全部係 dsa-hk 生產資料，唔係我嗰個 leeks-hardened dev 副本）
--------------------------------------------------------------------
- data/hk_v1_signals_cache.json  33,209 條訊號（含 entry_date / side / strategy）
- data/hk_v1_fillable.csv         23,181 條誠實限價單成交結果，含 `net_pp`
                                  （D+1 掛 trigger、expiry、填唔到剔除、已扣成本）
- public/hk200/<code>_HK_ohlc.json  每日 OHLC（400 支，由 2025-02-17 起）

限制
----
OHLC 只覆蓋 2025-02-17 之後，所以只有 27.6% 訊號（9,152 條）可以計形態。
訊號橫跨 2021-05 至 2026-09，覆蓋偏近期。呢個係硬限制，唔係可以改嘅參數。
"""
from __future__ import annotations

import json
import math
import statistics as st
import sys
from pathlib import Path

REPO = Path("/Users/kenken/dev/dsa-hk")
sys.path.insert(0, str(Path.home() / ".minimax/skills/daily-sr-chart/scripts"))
import candle_patterns as cp  # noqa: E402

MIN_BARS_BEFORE = 40        # detector needs ~30 bars of lookback


BACKFILL = REPO / "data" / "ohlc_backfill"


def load_ohlc(sym):
    """Prefer the 2021-2026 backfill; fall back to the site's 400-bar files."""
    code = sym.split("_")[0]
    for d in (BACKFILL, REPO / "public" / "hk200"):
        f = d / f"{code}_HK_ohlc.json"
        if not f.exists():
            continue
        try:
            rows = json.loads(f.read_text())
        except Exception:
            continue
        if rows:
            return rows
    return None


def count_tests(lows, level, window, tol=0.01, merge_gap=2):
    lo, hi = max(0, len(lows) - window), len(lows)
    band = level * (1 + tol)
    hit = [k for k in range(lo, hi) if lows[k] <= band]
    if not hit:
        return 0
    n, prev = 1, hit[0]
    for k in hit[1:]:
        if k - prev > merge_gap:
            n += 1
        prev = k
    return n


def main():
    recs = json.loads((REPO / "data/hk_v1_signals_cache.json").read_text())["records"]
    sig = {(r["sym"], r["entry_date"][:10]): r for r in recs}

    import csv as _csv
    rows = []
    with open(REPO / "data/hk_v1_fillable.csv") as fh:
        for r in _csv.DictReader(fh):
            key = (r["sym"], r["entry_date"][:10])
            s = sig.get(key)
            if s is None:
                continue
            rows.append({**r, "side": int(r["side"]),
                         "strategy": s["strategy"],
                         "net_pp": float(r["net_pp"]),
                         "entry": float(r["entry"]),
                         "trigger": float(r["entry"])})
    print(f"誠實成交記錄 {len(rows)} 筆")

    # join OHLC + 計形態
    by_sym = {}
    for r in rows:
        by_sym.setdefault(r["sym"], []).append(r)

    enriched = []
    skipped = 0
    for sym, group in by_sym.items():
        ohlc = load_ohlc(sym)
        if not ohlc:
            skipped += len(group); continue
        ohlc = sorted(ohlc, key=lambda x: x["date"])
        idx = {x["date"]: i for i, x in enumerate(ohlc)}
        frame = pd_frame(ohlc)
        pats = {}
        try:
            hits = cp.detect(frame)
            for _, h in hits.iterrows():
                pats.setdefault(int(h["index"]), []).append(
                    (h["pattern"], h["bias"], bool(h["confirmed"])))
        except Exception:
            pass
        lows = [x["low"] for x in ohlc]
        for r in group:
            i = idx.get(r["entry_date"][:10])
            if i is None or i < MIN_BARS_BEFORE:
                skipped += 1; continue
            p = pats.get(i, [])
            conf = [(n, b) for n, b, c in p if c]
            bias = {b for _, b in conf}
            agree = (r["side"] > 0 and "bullish" in bias) or (r["side"] < 0 and "bearish" in bias)
            r2 = dict(r)
            r2.update({
                "n_pat": len(conf),
                "pat": "|".join(sorted({n for n, _ in conf})),
                "agree": agree,
                "any_pat": len(conf) > 0,
                "n_tests": count_tests(lows[:i + 1], r["trigger"], 120),
            })
            enriched.append(r2)

    print(f"有足夠 OHLC 可計形態: {len(enriched)}（跳過 {skipped}：日期唔喺覆蓋範圍或太新）")
    if not enriched:
        return 1

    n_buy = sum(1 for r in enriched if r["side"] > 0)
    print(f"  BUY {n_buy} / SELL {len(enriched)-n_buy} | 命中形態 {sum(1 for r in enriched if r['any_pat'])} "
          f"({sum(1 for r in enriched if r['any_pat'])/len(enriched)*100:.1f}%)")

    base = [r["net_pp"] for r in enriched]
    print(f"\n基準淨回報: 每單 {st.mean(base):+.3f}pp  勝率 "
          f"{sum(1 for x in base if x>0)/len(base)*100:.1f}%  n={len(base)}")

    def grp(pred):
        v = [r["net_pp"] for r in enriched if pred(r)]
        return v

    print(f"\n{'='*72}\n蠟燭形態分組（淨回報 pp，已扣成本）\n{'='*72}")
    for lbl, pred in [("命中形態", lambda r: r["any_pat"]),
                      ("冇形態", lambda r: not r["any_pat"]),
                      ("形態方向 同 訊號一致", lambda r: r["agree"]),
                      ("形態方向 同 訊號相反", lambda r: r["any_pat"] and not r["agree"])]:
        v = grp(pred)
        if len(v) < 30:
            print(f"  {lbl:<24} n={len(v):<5} 樣本太少"); continue
        print(f"  {lbl:<24} n={len(v):<6} 每單 {st.mean(v):+.3f}pp  "
              f"勝率 {sum(1 for x in v if x>0)/len(v)*100:5.1f}%  "
              f"Δ {st.mean(v)-st.mean(base):+.3f}pp")

    for l1, p1, l2, p2 in [("一致", lambda r: r["agree"], "相反", lambda r: r["any_pat"] and not r["agree"])]:
        a, b = grp(p1), grp(p2)
        if len(a) < 30 or len(b) < 30:
            continue
        se = math.sqrt(st.stdev(a)**2/len(a) + st.stdev(b)**2/len(b))
        print(f"  → {l1}(n={len(a)}) vs {l2}(n={len(b)}): "
              f"差 {st.mean(a)-st.mean(b):+.3f}pp  t={(st.mean(a)-st.mean(b))/se:+.2f}")

    # 支持位測試次數 — 分方向，因為 SELL 佔 76%，混合會誤導
    for side_lbl, side_val in (("做多 BUY", 1), ("做空 SELL", -1)):
        sub = [r for r in enriched if r["side"] == side_val]
        b = [r["net_pp"] for r in sub]
        print(f"\n{'='*72}\n支持位測試次數 — {side_lbl}（基準 每單 {st.mean(b):+.3f}pp, n={len(b)}）\n{'='*72}")
        g = {}
        for r in sub:
            g.setdefault(min(r["n_tests"], 6), []).append(r["net_pp"])
        for k in sorted(g):
            v = g[k]
            lbl = f"{k}" if k < 6 else ">=6"
            print(f"  測試 {lbl:<4} n={len(v):<6} 每單 {st.mean(v):+.3f}pp  "
                  f"勝率 {sum(1 for x in v if x>0)/len(v)*100:5.1f}%  Δ {st.mean(v)-st.mean(b):+.3f}pp")
        lo = [x for k, v2 in g.items() if k <= 2 for x in v2]
        hi = [x for k, v2 in g.items() if k >= 4 for x in v2]
        if len(lo) > 30 and len(hi) > 30:
            se = math.sqrt(st.stdev(lo)**2/len(lo) + st.stdev(hi)**2/len(hi))
            print(f"  → <=2次(n={len(lo)}) vs >=4次(n={len(hi)}): "
                  f"差 {st.mean(hi)-st.mean(lo):+.3f}pp  t={(st.mean(hi)-st.mean(lo))/se:+.2f}")

    print("\n註：形態係用 OHLC bar 機械式識別，門檻未經回測校準；"
          "OHLC 只覆蓋 2025-02 之後，樣本偏向近 18 個月市況。")
    return 0


def pd_frame(ohlc):
    import pandas as pd
    return pd.DataFrame({
        "O": [x["open"] for x in ohlc], "H": [x["high"] for x in ohlc],
        "L": [x["low"] for x in ohlc], "C": [x["close"] for x in ohlc],
        "time": [x["date"] for x in ohlc],
    })


if __name__ == "__main__":
    sys.exit(main())
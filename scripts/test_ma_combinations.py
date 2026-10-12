#!/usr/bin/env python3
"""MA 組合對 Kenneth 真實訊號有冇增益？

背景
----
引擎（daily_sr.py）本身**完全冇用均線** — 趨勢判定靠 ADX14/DI，波動靠
ATR14。所以「邊個 MA 組合最好」對引擎冇答案；呢個實驗問嘅係另一條：
**MA 狀態如果當成額外上下文(filter)，有冇資訊含量？**

資料
----
- 訊號：data/hk_v1_signals_cache.json（33,209）+ hk_v1_fillable.csv 誠實 fill
- OHLC：data/ohlc_backfill/（2021 起，190/245 隻，500+ 支）

今日三次教訓都適用：跨市場一致 + 大 t 值唔代表冇混淆（控制 phase），
交易筆數唔等於樣本獨立性（擴到 5 年）。
"""
from __future__ import annotations

import csv
import json
import math
import statistics as st
import sys
from collections import Counter, defaultdict
from pathlib import Path

REPO = Path("/Users/kenken/dev/dsa-hk")
BACK = REPO / "data" / "ohlc_backfill"


def load():
    recs = json.loads((REPO / "data/hk_v1_signals_cache.json").read_text())["records"]
    sig = {(r["sym"], r["entry_date"][:10]): r for r in recs}
    rows = []
    with open(REPO / "data/hk_v1_fillable.csv") as fh:
        for r in csv.DictReader(fh):
            s = sig.get((r["sym"], r["entry_date"][:10]))
            if s is None:
                continue
            rows.append({"sym": r["sym"], "i_date": r["entry_date"][:10],
                         "side": int(r["side"]), "strategy": s["strategy"],
                         "net_pp": float(r["net_pp"])})
    return rows


def enrich():
    rows = load()
    by_sym = defaultdict(list)
    for r in rows:
        by_sym[r["sym"]].append(r)
    out = []
    for sym, group in by_sym.items():
        cf = BACK / f"{sym.split('_')[0]}_HK_ohlc.json"
        if not cf.exists():
            continue
        try:
            ohlc = sorted(json.loads(cf.read_text()), key=lambda x: x["date"])
        except Exception:
            continue
        idx = {x["date"]: k for k, x in enumerate(ohlc)}
        close = [x["close"] for x in ohlc]
        mas = {}
        for n in (5, 10, 20, 60):
            mas[n] = [sum(close[max(0, i - n + 1):i + 1]) / len(close[max(0, i - n + 1):i + 1])
                      if i + 1 >= n else None for i in range(len(close))]
        for r in group:
            i = idx.get(r["i_date"])
            if i is None or i < 60:
                continue
            c = close[i]
            d = dict(r)
            for n in (5, 10, 20, 60):
                d[f"ma{n}"] = mas[n][i]
                d[f"above{n}"] = (c > mas[n][i]) if mas[n][i] else None
            # 均線多頭排列 / 空頭排列 / 糾纏
            if all(d[f"ma{n}"] for n in (5, 10, 20)):
                if d["ma5"] > d["ma10"] > d["ma20"]:
                    d["align"] = "bull_stack"
                elif d["ma5"] < d["ma10"] < d["ma20"]:
                    d["align"] = "bear_stack"
                else:
                    d["align"] = "tangled"
            d["dist20"] = (c - d["ma20"]) / d["ma20"] * 100
            # 趨勢（用 60 日斜率做 regime proxy，稍後用 phase 控制）
            d["ma60_slope"] = (mas[60][i] - mas[60][max(0, i - 10)]) / mas[60][max(0, i - 10)] * 100
            out.append(d)
    return out


def rep(groups, key, base):
    print(f"\n--- {key} ---")
    for k, v in sorted(groups.items(), key=lambda kv: str(kv[0])):
        if len(v) < 25:
            print(f"   {str(k):<16} n={len(v):<5} 樣本太少，唔報"); continue
        m = st.mean(v)
        se = st.stdev(v) / math.sqrt(len(v))
        print(f"   {str(k):<16} n={len(v):<6} 每單 {m:+.3f}pp  "
              f"勝率 {sum(1 for x in v if x>0)/len(v)*100:5.1f}%  Δ基準 {m-base:+.3f}pp  "
              f"t={(m-base)/se if se else 0:+.2f}")


def main():
    d = enrich()
    if not d:
        print("無資料"); return 1
    base = st.mean([r["net_pp"] for r in d])
    print(f"訊號 {len(d)} 筆 | 基準 每單 {base:+.3f}pp  "
          f"勝率 {sum(1 for r in d if r['net_pp']>0)/len(d)*100:.1f}%")

    for n in (5, 10, 20, 60):
        g = defaultdict(list)
        for r in d:
            if r[f"above{n}"] is not None:
                g["價在MA%d之上" % n if r[f"above{n}"] else "價在MA%d之下" % n].append(r["net_pp"])
        rep(g, f"價格 vs MA{n}", base)

    g = defaultdict(list)
    for r in d:
        g[r["align"]].append(r["net_pp"])
    rep(g, "MA5/10/20 排列", base)

    g = defaultdict(list)
    for r in d:
        b = "跌離MA20 >10%" if r["dist20"] < -10 else ("貼近MA20 (±10%)" if r["dist20"] <= 10 else "升離MA20 >10%")
        g[b].append(r["net_pp"])
    rep(g, "距離 MA20", base)

    # regime 控制：MA60 斜率 = 趨勢 proxy。今日教訓 = 必須控制
    print(f"\n{'='*66}\n控制趨勢（MA60 10日斜率）之後\n{'='*66}")
    g = defaultdict(list)
    for r in d:
        k = "上升趨勢" if r["ma60_slope"] > 0.5 else ("下跌趨勢" if r["ma60_slope"] < -0.5 else "橫行")
        g[k].append(r)
    for k in ("上升趨勢", "橫行", "下跌趨勢"):
        sub = g.get(k, [])
        if not sub:
            continue
        b = st.mean([r["net_pp"] for r in sub])
        print(f"\n  【{k}】 n={len(sub)}  基準 {b:+.3f}pp")
        gg = defaultdict(list)
        for r in sub:
            gg["多頭排列" if r["align"] == "bull_stack" else
               ("空頭排列" if r["align"] == "bear_stack" else "糾纏")].append(r["net_pp"])
        for kk, v in sorted(gg.items(), key=lambda kv: -len(kv[1])):
            if len(v) < 25:
                print(f"     {kk:<10} n={len(v):<5} 樣本太少"); continue
            m = st.mean(v); se = st.stdev(v)/math.sqrt(len(v))
            print(f"     {kk:<10} n={len(v):<6} 每單 {m:+.3f}pp  "
                  f"Δ {m-b:+.3f}pp  t={(m-b)/se if se else 0:+.2f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
#!/usr/bin/env python3
"""補 HK200 歷史 OHLC（2021-2025）到獨立目錄，唔碰現有 public/hk200 檔。

點解用 Futu 而唔用 yfinance
---------------------------
對過 00700：400 根重疊日線，相對誤差中位 0.12%、最大 0.37% -> 同一價格序列，
只係精度差。

限流
----
富途歷史 K 線每 30 秒最多 60 次 = 2 req/s。之前用 2 個 worker 各自 sleep 0.5s
（即 4 req/s）--> 245 隻只有 2 隻成功。呢個版本嚴格單線程 ~1.6 req/s，
並對被限流嗰啲自動重試。

輸出：data/ohlc_backfill/<code>_HK_ohlc.json（格式同 public/hk200 一致）
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

REPO = Path("/Users/kenken/dev/dsa-hk")
OUT = REPO / "data" / "ohlc_backfill"
START = "2021-01-01"
END = "2026-10-10"
SLEEP = 0.62          # ~1.6 req/s, safely under the 2 req/s ceiling


def universe():
    return json.loads((REPO / "hk_universe_200.json").read_text())


def normalize(codes):
    """'00700.HK' / '00700_HK' -> '00700'"""
    out = []
    for c in codes:
        s = str(c).replace(".HK", "").replace("_HK", "")
        s = s.split("_")[0].split(".")[0]
        out.append(s.zfill(5))
    return out


def fetch(code):
    out_f = OUT / f"{code}_HK_ohlc.json"
    if out_f.exists() and out_f.stat().st_size > 20:
        try:
            if len(json.loads(out_f.read_text())) > 500:
                return code, 999, "cached"
        except Exception:
            pass
    from futu import OpenQuoteContext, RET_OK, KLType
    ctx = OpenQuoteContext(host="127.0.0.1", port=11111)
    try:
        ret, d, _ = ctx.request_history_kline(
            f"HK.{code}", start=START, end=END, ktype=KLType.K_DAY, max_count=1000)
        if ret != RET_OK:
            msg = str(d)
            if "頻率" in msg or "too many" in msg.lower():
                return code, 0, "RATE"
            return code, 0, msg[:50]
        rows = [{"date": str(r["time_key"])[:10], "open": float(r["open"]),
                 "high": float(r["high"]), "low": float(r["low"]),
                 "close": float(r["close"]), "volume": float(r["volume"])}
                for _, r in d.iterrows()]
    except Exception as e:
        return code, 0, f"{type(e).__name__}"
    finally:
        ctx.close()
    if rows:
        out_f.write_text(json.dumps(rows))
    return code, len(rows), "ok"


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    codes = normalize(universe())
    print(f"universe {len(codes)} 隻 (unique {len(set(codes))}) | {START} -> {END}")
    pending, ok = list(dict.fromkeys(codes)), 0
    for attempt in (1, 2, 3):
        if not pending:
            break
        nxt = []
        for i, code in enumerate(pending, 1):
            _, n, status = fetch(code)
            time.sleep(SLEEP)
            if n > 0:
                ok += 1
            else:
                if status == "RATE":
                    nxt.append(code)
                elif i <= 3:
                    print(f"  x {code}: {status}")
            if i % 50 == 0:
                print(f"  pass{attempt} {i}/{len(pending)}  成功 {ok}")
        pending = nxt
        if pending:
            print(f"  pass{attempt} 完，被限流 {len(pending)} 隻，等 5s 再試")
            time.sleep(5)
    print(f"完成：成功 {ok}，剩 {len(pending)} -> {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
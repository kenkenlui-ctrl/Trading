# 纏論第三類買點 — DSA-HK 整合 memo

**Date**: 2026-06-26
**Status**: ✅ 整合完成，已經寫入 scheduler

---

## 點樣 run

```bash
cd /Users/kenken/Documents/dsa-hk

# 掃全部 200 HK 隻股
python3 -m src.main chanlun --hk

# 加埋 60d hold backtest
python3 -m src.main chanlun --hk --backtest

# 揀特定股
python3 -m src.main chanlun 0700.HK,9988.HK,1211.HK

# US universe（200 隻，可選）
python3 -m src.main chanlun --us

# 睇最近 signals
python3 -m src.main chanlun-list 30

# 揀特定股嘅 signals
python3 -m src.main chanlun-list 90 9988.HK
```

## 自動 schedule

已經 wire 入 `src/scheduler.py`：

| Job ID | Time | 內容 |
|---|---|---|
| `daily_analysis` | 每日 18:00 HKT | 原本嘅 LLM 分析 |
| `daily_chanlun` | 每日 18:30 HKT | **新加** Chanlun 3rd-class scan HK 200 |

兩個都 mon-fri only（避週末）。

**已存在嘅 launchd plist** `com.dsa-hk.scheduler.plist` 已經會 trigger `python -m src.main schedule` → 自動 load 兩個 jobs。**無需改 plist**。

## Telegram notification

`chanlun_scheduled_job()` 自動 send Telegram：

```
📊 纏論第三類買點 每日掃描完成

發現 N 個新信號（HK 200 universe）

• 2026-06-24 0823.HK @ 35.50  (ZG 34.95, conf 8/10)
• 2026-06-22 0732.HK @ 0.92   (ZG 0.91, conf 8/10)
...
🌐 http://localhost:8200
```

如果有 signal 你 Telegram 會見到。

## DB schema 新加

```sql
CREATE TABLE chanlun_signal (
    id INTEGER PRIMARY KEY,
    code TEXT NOT NULL,
    signal_date TEXT,         -- breakout bar date
    entry_price, stop_loss, target REAL,
    confidence INTEGER,
    central_zg, central_zd, central_gg, central_dd REAL,
    had_pullback INTEGER,     -- 0/1
    rationale TEXT,
    status TEXT DEFAULT 'active',
    created_at,
    UNIQUE(code, signal_date)  -- idempotent
);
```

`save_chanlun_signal()` / `list_chanlun_signals()` / `count_chanlun_signals()` helper 已加。

## 點樣睇 dashboard（8200）

Streamlit dashboard 暫時未 extend 新 tab。可以 query DB 直接睇：

```bash
sqlite3 /Users/kenken/Documents/dsa-hk/data/dsa_hk.db \
  "SELECT signal_date, code, entry_price, stop_loss, target, confidence, status
   FROM chanlun_signal
   WHERE signal_date >= date('now', '-7 days')
   ORDER BY signal_date DESC"
```

## Backtest 觀察

跑 200 HK 隻股 + 60d hold backtest：
- **166 個 signals**（0 失敗 — fetch 100% success rate）
- Backtest: avg WR=33.4%, avg return=-3.2%
- 結論：**full 200 universe 太闊** — 包括咗好多細價股 / 弱股，signal noise 大

**建議**：手動 filter 一個 30-50 隻嘅「Chanlun-friendly」subuniverse（mean-reversion 有效嘅股種：能源、銀行、rebound 科技）— 跟返我 backtest 結論。

或者 **等 paper-trader 真實 trade 1-2 個月再 tune**。

## Files added/modified

| File | Change |
|---|---|
| `src/strategies/__init__.py` | NEW — package init |
| `src/strategies/chanlun.py` | NEW — full pipeline (~430 lines, ported from `chanlun-backtest/src/`) |
| `src/commands/__init__.py` | NEW — package init |
| `src/commands/chanlun_scan.py` | NEW — scan command (~280 lines) |
| `src/db.py` | + `chanlun_signal` table + 3 helpers |
| `src/main.py` | + `chanlun` / `chanlun-list` CLI subcommands |
| `src/scheduler.py` | + `chanlun_scheduled_job` + cron entry at 18:30 HKT |

## ⚠️ Caveats

1. **Telegram message 太長會被截斷**（Telegram 4096 char limit）。如果當日 100+ signals，message 會 truncate。
2. **Backtest 唔包 cost**（stamp duty 0.13% × 2 + commission 0.05% × 2 = 0.36% round-trip）。實際 trade 會食少少。
3. **Yanlun (閹雞) noise** — 166 signals 太多，建議加 liquidity filter（市值 > $X 億）。
4. **目前無 paper-trade integration** — DSA-HK 呢個係**純 signal scanner**，唔似 Gstack paper-trader 會 auto-fill。要人手 execute。

## Reference

- Backtest source: `/Users/kenken/Documents/chanlun-backtest/docs/CHANLUN_V2_REPORT.md`
- Theory: `/Users/kenken/Documents/chanlun-corpus/` (108 原文)
- TypeScript Gstack version: `/Users/kenken/Documents/Gstack/trading-platform/apps/worker/src/strategies/chanlun-third-class.ts`
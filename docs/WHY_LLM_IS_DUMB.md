# Why the LLM Signal is Dumb — Deep Analysis

**Date**: 2026-07-09
**Audit window**: 10 trading days, 1913 signals with 1D forward return
**Conclusion**: The LLM should NOT make BUY/SELL decisions. Use it as a feature extractor only.

---

## 1. The LLM is trained for INVESTING, not DAY TRADING

The LLM (MiniMax-M3 / similar) was trained on web text — Reddit, Wikipedia, news, financial articles. This data overwhelmingly teaches **investing** patterns:

- "If a stock is in an uptrend, BUY"
- "If MA20 > MA50 > MA100, BUY" (golden cross)
- "If fundamentals are strong, BUY"
- "If sentiment is bullish, BUY"
- "If a stock dropped, SELL before it falls more"

These are **investing** rules for **multi-month holds**. They are **anti-patterns** for **day trading 1D mean-reversion**.

| Investing (LLM default) | Day Trading (1D horizon) |
|---|---|
| Buy uptrend, hold months | Fade extremes, close by 4 PM |
| Sell downtrend before more drop | Buy panic, ride mean-revert bounce |
| MA alignment = bullish | MA alignment = already extended |
| "Strong fundamentals" = BUY | "Strong fundamentals" = already priced in |
| High momentum = continuation | High momentum = exhaustion |

The LLM follows investing heuristics → 38.6% WR on 1D, avg -0.72% loss. **Below random (50%)**.

## 2. The 3 systematic mistakes

### Mistake 1: Chasing tops (BUY 樂觀+m≥60+chg≥3%)
- **Pattern**: LLM sees stock with multi-day uptrend, MA20>MA50>MA100, bullish news, sentiment 樂觀 → recommends BUY
- **Reality**: This is exactly when stocks REVERSE. Multi-day uptrend + 3% gap = exhaustion, not entry
- **Result**: 68 signals, 35.3% WR, avg -1.49%, 11 cases lost > 5% in 1 day
- **Example**: 7/8 00939.HK (建行) — LLM said BUY 樂觀, +5.75% same day; next day -2.54%

### Mistake 2: Catching falling knives (SELL 悲觀+chg≤-3%)
- **Pattern**: LLM sees stock with multi-day drop, MA20<MA50, bearish news, sentiment 悲觀 → recommends SELL
- **Reality**: This is exactly when stocks BOUNCE. Mean-reversion kicks in after panic sell-off
- **Result**: 53 signals, 37.7% WR (next-day stock went UP), avg +0.24% (you missed the bounce)
- **Example**: 6/29 ON -23.7% → SELL signal → next day +6.74% (you sold the bottom)

### Mistake 3: Following the noise (BUY 樂觀 with m≥80)
- **Pattern**: LLM sees strongly extended stock, sentiment 樂觀, m_score 80+ → recommends BUY
- **Reality**: Strongest momentum = closest to reversal
- **Result**: 12 signals, 16.7% WR (coin flip), avg -2.24% loss

## 3. What the data says is right

The LLM's intuition is WRONG, but the **deterministic rules** built from data are RIGHT:

| Strategy | n | WR | avg | Notes |
|---|---|---|---|---|
| BUY 樂觀 (LLM pattern) | 112 | 30.4% | -1.32% | anti-edge |
| BUY 中性 (LLM pattern) | 75 | 48.0% | +0.00% | break-even |
| BUY chg[-3,0)+中性 (mean-reversion) | 15 | 73.3% | +1.10% | **edge** |
| BUY chg[-3,0)+sent非樂觀+m[30,70] (Conservative) | 26 | **61.5%** | **+0.92%** | **+23% WR over LLM BUY** |
| BUY chg[-5,-2]+sent非樂觀+m<60+score<45 (Bounce) | 60+ | 51.7% | -0.53% | small edge, catches missed bounces |

**Deterministic Conservative BUY outperforms LLM BUY by 23 percentage points in WR and 1.64% in avg return.**

## 4. Sentiment is not predictive

LLM sentiment 樂觀/中性/悲觀 has near-zero or NEGATIVE predictive value:

| Sentiment | 1D avg return |
|---|---|
| 樂觀 (bullish) | **-0.21%** ← stock actually FALLS |
| 中性 (neutral) | +0.06% |
| 悲觀 (bearish) | **+0.15%** ← stock actually RISES |

The LLM is **inversely correlated with reality**. Buying when LLM says 樂觀 = buying tops. Selling when LLM says 悲觀 = selling bottoms.

## 5. Root cause: LLM is not a trader, it's a summarizer

What the LLM is actually good at:
- ✅ Summarizing news
- ✅ Identifying catalysts
- ✅ Describing technical setup
- ✅ Writing human-readable narratives

What the LLM is bad at:
- ❌ Predicting 1D direction (random walk at this horizon)
- ❌ Knowing when to enter (LLM has no concept of "too extended")
- ❌ Knowing when to exit (LLM has no feedback on prior predictions)
- ❌ Risk management (no concept of position sizing, drawdown)

## 6. The architectural fix

**Stop letting the LLM make BUY/SELL decisions. Use it only for features.**

### New architecture:
1. **Data layer** (yfinance/Futu/gtimg) — feed raw OHLCV + fundamentals
2. **Feature layer** (deterministic 4-dim) — value, quality, momentum, order_flow
3. **Decision layer** (deterministic rules) — operation_advice from backtested edges:
   - TOXIC (樂觀+m≥60+chg≥3%) → 觀望 (proven anti-pattern)
   - PANIC (悲觀+chg≤-3%) → 觀望 (proven anti-pattern)
   - mean-reversion (chg[-3,0)+sent非樂觀+m[30,60]+non-tech) → 買入 (Conservative BUY)
   - bounce (chg[-5,-2]+sent非樂觀+m<60+score<45) → 買入 (Bounce BUY)
   - else → 觀望
4. **Narrative layer** (LLM) — write summary, identify catalysts, describe risk
5. **Display layer** — show decision + LLM narrative side-by-side

### What changes:
- `operation_advice` is set by **rules**, not LLM
- LLM's original op is captured in `llm_original_op` column for audit
- LLM provides `summary_md` and `reasoning` (text only)
- The dashboard shows the rule-based decision prominently
- LLM's wrong-direction signals (e.g., 樂觀 BUY) are visible in `llm_original_op` for learning

### What stays:
- LLM still writes full_md (LLM analysis narrative)
- LLM still identifies catalysts and risks
- LLM's score_breakdown is preserved

## 7. Implementation plan

### Phase 1 (already done):
- ✅ Anti-Chase override in dashboard (downgrade 樂觀+m≥60+chg≥3% to 觀望)
- ✅ Anti-Knife SELL (don't sell panic days)
- ✅ Conservative BUY v2 filter
- ✅ Bounce BUY filter

### Phase 2 (proposed):
- [ ] New `src/signal_decision.py` with rule-based operation_advice
- [ ] Apply rule BEFORE saving to DB (override LLM's op_advice)
- [ ] Preserve LLM's original op in `llm_original_op` column
- [ ] Show both rule + LLM op in detail page
- [ ] Default dashboard view = Conservative BUY + Bounce BUY (not "all")
- [ ] Per-ticker detail page banner: "Rule-based decision: 觀望" + "LLM said: 買入" + reason

### Phase 3 (future):
- [ ] Multi-day backtest of rule-based vs LLM-based signals
- [ ] Auto-reject LLM when LLM op differs from rule op by N std devs
- [ ] Continuous retraining feedback loop (record LLM accuracy, weight by historical)

## 8. Key metrics to track

Going forward, measure:
- LLM BUY win rate by signal type (should be < 50% to justify rule override)
- Rule-based BUY win rate (target > 55%)
- Number of "saved" trades (rule veto'd LLM signal, market would have lost)
- Number of "missed" trades (rule rejected LLM signal that would have won) — should be small

## 9. Why this matters

The user feedback "一話買就跌，一話 sell 就升" is the EXACT signature of:
- 樂觀 → 摸頂 (BUY at top)
- 悲觀 → 摸底 (SELL at bottom)

This is the systematic LLM pattern, not random noise. Fixing it at the root (LLM doesn't decide) is better than papering over with filters.

The LLM is a useful RESEARCH ANALYST. It just shouldn't be a TRADER.

---

## Appendix: Raw audit data

### Pattern 1: LLM sentiment vs actual 1D forward return
- 樂觀: n=199, avg ret = -0.21% (stock actually FALLS)
- 中性: n=1080, avg ret = +0.06%
- 悲觀: n=550, avg ret = +0.15% (stock actually RISES)

### Pattern 3: LLM BUY by m_score band
- m=50-65: n=56, WR=41.1%, avg=+0.07%
- m=65-80: n=129, WR=39.5%, avg=-0.92%, 29 big losses
- m=80-100: n=12, **WR=16.7%**, avg=-2.24%

### Pattern 7: TOXIC BUY (樂觀+m≥60+chg≥3%)
- n=68, **WR=35.3%**, avg=-1.49%, 11 cases lost > 5%

### Pattern 6: LLM BUY vs Deterministic Conservative BUY
- LLM BUY (all): n=197, WR=38.6%, avg=-0.72%
- Det Conservative BUY: n=26, **WR=61.5%**, avg=+0.92% — **+23% WR, +1.64% better than LLM**

### Pattern 9: SELL on falling knife
- SELL on 悲觀+chg≤-3%: n=53, hit=37.7% (next day stock UP), avg=+0.24%
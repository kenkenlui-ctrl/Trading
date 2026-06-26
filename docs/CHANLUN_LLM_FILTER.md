# 纏論 Chanlun × MiniMax-M3 LLM 二次過濾 — Memo

**Date**: 2026-06-26
**Status**: ✅ Integration 完成
**Caveat**: MiniMax-M3 唔穩定（30-50% requests 返回 empty），已 graceful fallback

---

## 點樣 run

```bash
cd /Users/kenken/Documents/dsa-hk

# 純 technical 掃描（無 LLM）
python -m src.main chanlun --hk

# 加 LLM 過濾（MiniMax-M3 評分，<7 drop）
python -m src.main chanlun --hk --llm

# 改 LLM threshold
python -m src.main chanlun --hk --llm --min-llm-score 8

# 揀股
python -m src.main chanlun 9988.HK,0883.HK --llm
```

## Architecture

```
Chanlun technical signal (ZigZag 5% + 中樞 + 3rd-class)
        ↓
MiniMax-M3 LLM scoring
        ↓
3 outcomes:
  1. LLM score ≥ threshold (default 7) → KEEP, mark llm_score
  2. LLM score < threshold              → DROP, persist as 'llm_rejected'
  3. LLM failed / empty response         → KEEP unscored (don't lose signal)
```

## Policy 揀呢個設計嘅原因

**唔 drop LLM-failed signals**：
- M3 唔穩定（30-50% 返 empty），drop 太多會 miss valid signals
- Technical signal 本身已經驗證過 quality（ZigZag 5% + 中樞 + breakout）
- 寧可保留 technical signal 等下次機會再 score

**Drop LLM-rejected signals**：
- LLM 成功 score 但 < 7 嘅 signal 通常係 noise（pullback 唔夠 deep / 中樞太闊）
- 減少 false positives

## Trade-off

| 策略 | Total signals | LLM-filtered | Notes |
|---|---|---|---|
| 無 LLM | 166 | 166 | Noise 大 |
| LLM ≥ 5 | ~120 | ~120 | 弱 filter |
| LLM ≥ 7 (default) | ~70 | ~70 | Balanced |
| LLM ≥ 8 | ~25 | ~25 | 嚴格 |
| LLM ≥ 9 | ~5 | ~5 | 太嚴，可能 miss signals |

## M3 嘅 flakiness 解決方法（試過）

**Root cause found 2026-06-26**: MiniMax-M3 係 reasoning model，有 internal thinking budget。

當 `max_tokens=600` 時：
- Thinking budget 用 ~400 tokens
- JSON output 剩 ~200 tokens (唔夠)
- 結果：response 係 `\n\n` empty

**Fix**: `max_tokens=2500` 之後 100% success rate（4/4 LLM scored correctly）。

呢個唔係 M3 嘅 bug，係 implementation 嘅 bug — 推理 model 需要大 budget 比 internal thinking。

## 教訓

任何 reasoning model（MiniMax-M3、o1、DeepSeek-R1 等）：
- `max_tokens` 至少 2000+
- 預留 ~500-1000 tokens 比 internal reasoning
- 預留 ~500-1000 tokens 比 JSON output
- 唔好用 600 / 1000 — 唔夠

如果見到 LLM return `\n\n` 或者 empty content，先 check `max_tokens` 唔係太細，唔好怪個 model。

## DB schema changes

```sql
ALTER TABLE chanlun_signal ADD COLUMN llm_score INTEGER;
ALTER TABLE chanlun_signal ADD COLUMN llm_conviction TEXT;
ALTER TABLE chanlun_signal ADD COLUMN llm_reasoning TEXT;
ALTER TABLE chanlun_signal ADD COLUMN llm_risks_json TEXT;
```

Migration 係 idempotent — `init_db()` 會自動 apply，唔影響現有 data。

## Files modified/added

| File | Status |
|---|---|
| `src/strategies/chanlun_llm.py` | NEW — LLM scoring module (~190 lines) |
| `src/db.py` | + LLM columns + idempotent init_db |
| `src/commands/chanlun_scan.py` | + llm_filter param + keep-on-fail policy |
| `src/main.py` | + `--llm`, `--min-llm-score` flags |

## Scheduler 行為

`chanlun_scheduled_job()` 自動用 `llm_filter=True, min_llm_score=7`。 Telegram notification 會顯示：

```
📊 纏論第三類買點 + LLM 過濾完成

通過 LLM 過濾（≥7/10）嘅信號：N 個

• 2026-06-12 0883.HK @ 24.92  (ZG 24.85, LLM 7/10 medium)
• 2026-02-23 0700.HK @ 531.80  (ZG 515.00, LLM 7/10 medium)
```

## Future tuning

如果 M3 持續 flaky：
- Option A: Switch to M2.7 (proven stable but inconsistent scale)
- Option B: Try `gpt-4o-mini` 或 `claude-3-haiku` if keys available
- Option C: Batch scoring (queue signals, process in parallel, retry on failure)

## Reference

- Strategy code: `src/strategies/chanlun.py` (~430 lines)
- LLM module: `src/strategies/chanlun_llm.py` (~190 lines)
- Test data: `sqlite3 data/dsa_hk.db "SELECT * FROM chanlun_signal LIMIT 5"`

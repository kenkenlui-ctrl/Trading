# Phase 10.1 — Single direction score (0–100)

## THE score: `direction_score`

| Score | Meaning | Action |
|------:|---------|--------|
| **0–15** | Strong short confidence | Prefer **SHORT** (day-trade fade) |
| **16–30** | Lean short | Small short / research |
| **31–69** | Neutral / no edge | **SKIP** |
| **70–84** | Lean long | Small long |
| **85–100** | Strong long confidence | Prefer **LONG** (GOLD) |

Stored in DB column **`signal_score`** (dashboard badge uses the same field).

```text
0 ──────────────── 50 ──────────────── 100
SHORT wins                         LONG wins
```

```python
from src.signal_decision import direction_score, direction_label, direction_action

d = direction_score(score_breakdown, data_snapshot, sentiment, matched_rule)
# d near 0 → short; near 100 → long
```

### How it’s built

1. `next_day_long_score` / `next_day_short_score` (feature poles)
2. `direction = 100 × long / (long + short)`
3. Rule anchors: `GOLD_LONG ≥ 88`, `FADE_SHORT ≤ 12`, etc.

---

## Rules behind the dial

| Side | Rule | Filters |
|------|------|---------|
| **BUY** | **GOLD_LONG** | `v≥70`, `m<40`, `0<PE<12`, `chg<+1%`, not HSI bear |
| **SHORT** | **FADE_SHORT** | `chg≥+3%`, `m≥60`, `PE>20` |

---

## Commands

```bash
cd ~/Documents/dsa-hk

# Board ranked by DIR
python3 scripts/next_day_board.py --top 12
python3 scripts/next_day_board.py --date 2026-07-28 --backtest

# Write direction_score into signal_score for all rows
python3 scripts/backfill_rule_decisions.py

python3 scripts/test_signal_decision_phase10.py
```

## Daily use

1. Run scan → fill `daily_report`
2. `python3 scripts/next_day_board.py`
3. **Only look at DIR:**
   - **≥ 70** → long candidates  
   - **≤ 30** → short candidates  
   - **31–69** → ignore  
4. Prefer GOLD_LONG / FADE_SHORT tags when present  
5. HSI day ≤ −1.5% → no longs  
6. FADE shorts: flat by cash close  

Not investment advice.

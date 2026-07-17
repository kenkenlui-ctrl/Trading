# Daily Update Cheat Sheet (manual, no cron)

Owner runs these commands each morning. Estimated 15-20 min total.

## Every morning workflow

```bash
cd /Users/kenken/Documents/dsa-hk

# 1. Generate today's report (uses today's HK close + US close which
#    is available by 4am HKT for US, 4pm HKT for HK)
#    Replace YYYY-MM-DD with the date you want to generate
python3 scripts/refresh_daily.py --date YYYY-MM-DD

# 2. Compute forward returns for the date you just generated
#    (uses NEXT trading day's close to validate the signal)
python3 scripts/compute_forward_returns.py --date YYYY-MM-DD

# 3. Rebuild static site + deploy (auto)
python3 scripts/build_static.py --date YYYY-MM-DD --all
# Then deploy to Cloudflare — already automated via wrangler in the
# refresh_daily pipeline (step 5 of refresh_daily.py)
```

## Why no cron

Owner runs daily reports manually. After a trading day:
- HK market closes 4 PM HKT same day
- US market closes 4 PM ET = 4 AM HKT next day
- So by ~5 AM HKT the next morning, BOTH closes are available
- Owner then runs the workflow above

## Common tasks

### Backfill forward returns for a past date (e.g. for a date you forgot)
```bash
python3 scripts/compute_forward_returns.py --date 2026-07-08
```

### Backfill MULTIPLE past dates
```bash
python3 scripts/compute_forward_returns.py --date 2026-07-08 --date 2026-07-07 --date 2026-07-06
```

### View backtest stats (by date)
```bash
sqlite3 data/dsa_hk.db "SELECT signal_date, COUNT(*), AVG(forward_return_pct), AVG(CASE WHEN win=1 THEN 1.0 ELSE 0.0 END) * 100 AS wr_pct FROM backtest_results GROUP BY signal_date ORDER BY signal_date;"
```

### Re-score all historical records with current model
```bash
# Useful after model improvement
python3 -c "
import sys, sqlite3, json
sys.path.insert(0, '.')
from src.signal_decision import predict_win_probability, extract_matched_rule
conn = sqlite3.connect('data/dsa_hk.db')
cur = conn.execute('SELECT id, sentiment, score_breakdown_json, data_snapshot_json, decision_reason FROM daily_report')
for row_id, sent, sb_json, ds_json, reason in cur:
    sb = json.loads(sb_json or '{}')
    ds = json.loads(ds_json or '{}')
    rule = extract_matched_rule(reason or '')
    score = predict_win_probability(m=sb.get('momentum_score') or 0, of=sb.get('order_flow_score') or 0, v=sb.get('value_score') or 0, q=sb.get('quality_score') or 0, chg=ds.get('change_pct') or 0, sentiment=sent or '', matched_rule=rule)
    conn.execute('UPDATE daily_report SET signal_score = ? WHERE id = ?', (score, row_id))
conn.commit()
print('Re-scored all records')
"
```

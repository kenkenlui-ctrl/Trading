# DRAFT — /methodology 新一節「幾時宣布策略死亡」（NOT deployed）

**Status: DRAFT behind `failure_criteria_flag` (default OFF). 門檻係「提案」，
唔係決定 —— Kenneth 先批數字，文字先上。**

## 幾時宣布策略死亡（提案）

以下三條係**提案門檻**，喺跑任何未來結果之前寫低，唔准事後移動：

- **T1  Sharpe 衰減**：樣本外 Sharpe 低過樣本內 Sharpe 嘅 50%。
- **T2  回撤**：樣本外最大回撤超過樣本內最大回撤嘅 2 倍。
- **T3  連續負窗**：唔可以出現連續 3 個負嘅 20 個交易日窗口。

**窗口定義**
- 樣本內：entry_date < 2023-01-01
- 樣本外：entry_date >= 2023-01-01
- 交易日序列：one value per trading session: the mean net return of that session's booked v2 trades, equal-weighted
- Sharpe：mean(session returns) / sd(session returns); the annualised form (x sqrt(periods per year)) is printed beside it, and the annualisation cancels in the IS-vs-OOS ratio
- 最大回撤：deepest peak-to-trough decline of the curve built by compounding the session series
- 滾動窗口：20 consecutive sessions, non-overlapping, stepping 20 at a time

## 而家企喺邊（美股 v2，逐單規則）

| | 樣本內 | 樣本外 |
|---|---|---|
| 交易數 | 3693 | 2982 |
| 交易日數 | 917 | 725 |
| Sharpe（原始） | 0.3464 | 0.2848 |
| Sharpe（年化） | 5.4984 | 4.5203 |
| 最大回撤 | -86.428% | -64.928% |
| 最長連續負窗 | 1 | 2 |

**判定**：T1 PASS
（比值 0.8221）·
T2 PASS
（倍數 0.7512）·
T3 PASS
（連續 1）。

**Direction A 嘅 2021–26 結果點解唔係矛盾**
Direction A 嘅裁決係關於**選股**：篩選器有冇跑贏等權。
2021–26 嗰個負 gap（z 喺 -1.4 至 -1.7，從來冇去到 -1.96）係選股嘅裁決。
而上面 T1 嘅 Sharpe 衰減係關於**逐單規則本身穩唔穩**。
一個策略可以同時有真嘅逐單 edge，同埋選股係死嘅 —— 呢兩句唔相衝，
站上面本來就係咁講。SELECTION_DEAD 同 T1 衰減係一致，唔係打架。

**未決**：門檻數字要 Kenneth 批；批之前唔上站。

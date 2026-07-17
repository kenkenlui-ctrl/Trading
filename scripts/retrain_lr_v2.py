"""Retrain LR v2: features only, no rule dummies.

Phase 9 Step 5 v2 (2026-07-18): the previous LR mixed rules (which directly
determine op) with features, leading to the model just learning "predict
DEFAULT for non-rule records". This version uses only features (no rules)
to predict win prob, which is then used as a confidence signal within the
rule's decision.

If features alone have predictive power, this LR will be useful for ranking
candidates within a single rule. If not, we fall back to the static
SIGNAL_SCORE table.
"""
import json
import math
import sys
import sqlite3
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

DB_PATH = "/Users/kenken/Documents/dsa-hk/data/dsa_hk.db"


def _to_rule(decision_reason: str) -> str:
    if not decision_reason or not decision_reason.startswith("["):
        return ""
    end = decision_reason.find("]")
    if end < 1:
        return ""
    return decision_reason[1:end]


def main():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row

    # Join daily_report (features) + backtest_results (T+1 return)
    # Only use records that have features_json populated (i.e. 6/26-7/17 backfilled)
    rows = con.execute("""
        SELECT 
            d.code, d.report_date,
            d.score_breakdown_json, d.features_json, d.data_snapshot_json,
            d.decision_reason, d.sentiment,
            b.forward_return_pct, b.win
        FROM daily_report d
        JOIN backtest_results b
          ON d.code = b.code AND d.report_date = b.signal_date
        WHERE d.report_date >= '2026-06-26' AND d.report_date <= '2026-07-17'
          AND b.win IS NOT NULL
          AND d.features_json IS NOT NULL
    """).fetchall()
    print(f"Training set: {len(rows)} joined records")

    # Build feature matrix — features only, NO rules
    feats_list = []
    labels = []
    for r in rows:
        try:
            fjson = json.loads(r["features_json"] or "{}")
        except Exception:
            continue
        try:
            snap = json.loads(r["data_snapshot_json"] or "{}")
        except Exception:
            snap = {}

        # 5-dim sub-scores (deterministic from features)
        v = float(fjson.get("value_score") or 50)
        q = float(fjson.get("quality_score") or 50)
        m = float(fjson.get("momentum_score") or 50)
        of = float(fjson.get("order_flow_score") or 50)
        news = float(fjson.get("news_score") or 50)

        chg = snap.get("change_pct") or 0
        chg_5d = fjson.get("chg_5d") or 0
        turnover_5d_ratio = fjson.get("turnover_5d_ratio") or 1.0
        dist_52w_low = fjson.get("dist_52w_low_pct")
        if dist_52w_low is None:
            dist_52w_low = 30.0
        dist_52w_high = fjson.get("dist_52w_high_pct")
        if dist_52w_high is None:
            dist_52w_high = -20.0
        pe_relative = fjson.get("pe_relative") or 1.0

        sent = r["sentiment"] or ""

        feat = {
            "v": v,
            "q": q,
            "m": m,
            "of": of,
            "news": news,
            "chg": chg,
            "chg_5d": chg_5d,
            "turnover_5d_ratio": turnover_5d_ratio,
            "dist_52w_low": dist_52w_low,
            "dist_52w_high": dist_52w_high,
            "pe_relative": pe_relative,
            "sent_樂觀": 1.0 if sent == "樂觀" else 0.0,
            "sent_悲觀": 1.0 if sent == "悲觀" else 0.0,
        }
        feats_list.append(feat)
        labels.append(1.0 if r["win"] else 0.0)

    n = len(feats_list)
    if n == 0:
        print("ERROR: no training data")
        return

    keys = list(feats_list[0].keys())
    weights = {k: 0.0 for k in keys}
    bias = 0.0

    # Standardize
    means = {k: sum(f[k] for f in feats_list) / n for k in keys}
    stds = {k: max(0.1, (sum((f[k] - means[k]) ** 2 for f in feats_list) / n) ** 0.5) for k in keys}

    def sigmoid(z):
        if z > 30: return 1.0
        if z < -30: return 0.0
        return 1.0 / (1.0 + math.exp(-z))

    # L2 regularization to prevent overfitting on small samples
    lam = 0.01

    # Train
    lr = 0.3
    for epoch in range(400):
        total_loss = 0
        for i, f in enumerate(feats_list):
            z = bias
            for k in keys:
                z += weights[k] * (f[k] - means[k]) / stds[k]
            p = sigmoid(z)
            y = labels[i]
            err = p - y
            for k in keys:
                z_k = (f[k] - means[k]) / stds[k]
                weights[k] -= lr * (err * z_k + lam * weights[k])
            bias -= lr * err
            eps = 1e-10
            total_loss -= y * math.log(max(p, eps)) + (1 - y) * math.log(max(1 - p, eps))
        if epoch % 50 == 0 or epoch == 199:
            print(f"  epoch {epoch:3d}  loss={total_loss/n:.4f}")

    # Print weights
    print("\n=== Trained _LR_WEIGHTS (no rules) ===")
    print("_LR_WEIGHTS = {")
    for k in keys:
        w = weights[k]
        print(f'    "{k}": {w:+.4f},')
    print("}")
    print(f"_LR_BIAS = {bias:+.4f}")

    print("\n=== Standardization params ===")
    print("_LR_MEAN = {")
    for k in keys:
        print(f'    "{k}": {means[k]:.3f},')
    print("}")
    print("_LR_STD = {")
    for k in keys:
        print(f'    "{k}": {stds[k]:.3f},')
    print("}")

    # Top-20 by predicted prob
    probs = []
    for i, f in enumerate(feats_list):
        z = bias
        for k in keys:
            z += weights[k] * (f[k] - means[k]) / stds[k]
        probs.append((sigmoid(z), labels[i]))

    probs.sort(key=lambda x: -x[0])

    # Compute per-bucket WR
    print("\n=== Pred prob buckets (all records) ===")
    for thresh in [0.6, 0.55, 0.52, 0.5, 0.48, 0.45, 0.4]:
        bucket = [(p, y) for p, y in probs if p >= thresh]
        if not bucket:
            continue
        wr = sum(y for _, y in bucket) / len(bucket) * 100
        print(f"  pred≥{thresh}: n={len(bucket):4d}  WR={wr:5.1f}%")

    # Bucket on raw scores
    print("\n=== Per-feature Pearson correlation with win ===")
    for k in keys:
        vals = [f[k] for f in feats_list]
        # Pearson
        mean_v = sum(vals) / n
        mean_y = sum(labels) / n
        cov = sum((vals[i] - mean_v) * (labels[i] - mean_y) for i in range(n))
        var_v = sum((v - mean_v) ** 2 for v in vals)
        var_y = sum((y - mean_y) ** 2 for y in labels)
        if var_v > 0 and var_y > 0:
            r = cov / (var_v * var_y) ** 0.5
            print(f"  {k:<20} r={r:+.4f}")


if __name__ == "__main__":
    main()

"""Retrain logistic regression on 14d data with enriched features.

Phase 9 Step 5 (2026-07-18): use features_json (5d rolling + sector + dist_52w)
plus original sub-scores to retrain the predict_win_probability LR.

Output: new _LR_WEIGHTS dict ready to paste into src/signal_decision.py.

Usage:
    python3 scripts/retrain_lr.py
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
    rows = con.execute("""
        SELECT 
            d.code, d.report_date,
            d.score_breakdown_json, d.features_json,
            d.decision_reason, d.sentiment,
            b.forward_return_pct, b.win
        FROM daily_report d
        JOIN backtest_results b
          ON d.code = b.code AND d.report_date = b.signal_date
        WHERE d.report_date >= '2026-06-26' AND d.report_date <= '2026-07-17'
          AND b.win IS NOT NULL
    """).fetchall()
    print(f"Training set: {len(rows)} joined records")

    # Build feature matrix
    feats_list = []
    labels = []
    for r in rows:
        try:
            sb = json.loads(r["score_breakdown_json"] or "{}")
        except Exception:
            sb = {}
        try:
            fjson = json.loads(r["features_json"] or "{}")
        except Exception:
            fjson = {}
        rule = _to_rule(r["decision_reason"])
        sent = r["sentiment"] or ""

        # 5-dim sub-scores (deterministic from features)
        v = float(fjson.get("value_score") or sb.get("value_score") or 50)
        q = float(fjson.get("quality_score") or sb.get("quality_score") or 50)
        m = float(fjson.get("momentum_score") or sb.get("momentum_score") or 50)
        of = float(fjson.get("order_flow_score") or sb.get("order_flow_score") or 50)

        # 5d rolling
        chg_5d = fjson.get("chg_5d")
        if chg_5d is None:
            chg_5d = 0
        turnover_5d_ratio = fjson.get("turnover_5d_ratio")
        if turnover_5d_ratio is None:
            turnover_5d_ratio = 1.0

        # Distance to 52w
        dist_52w_low = fjson.get("dist_52w_low_pct")
        if dist_52w_low is None:
            dist_52w_low = 30.0  # default mid
        dist_52w_high = fjson.get("dist_52w_high_pct")
        if dist_52w_high is None:
            dist_52w_high = -20.0  # default mid

        # Sector pe_relative
        pe_relative = fjson.get("pe_relative")
        if pe_relative is None:
            pe_relative = 1.0  # default sector-equal

        # Day chg
        chg = (json.loads(r["score_breakdown_json"] or "{}")).get("change_pct", 0)
        # chg isn't in score_breakdown. Pull from data_snapshot
        snap = json.loads(con.execute("SELECT data_snapshot_json FROM daily_report WHERE code=? AND report_date=?", (r["code"], r["report_date"])).fetchone()["data_snapshot_json"])
        chg = snap.get("change_pct") or 0

        feat = {
            "v": v,
            "q": q,
            "m": m,
            "of": of,
            "chg": chg,
            "chg_5d": chg_5d,
            "turnover_5d_ratio": turnover_5d_ratio,
            "dist_52w_low": dist_52w_low,
            "dist_52w_high": dist_52w_high,
            "pe_relative": pe_relative,
            "sent_樂觀": 1.0 if sent == "樂觀" else 0.0,
            "sent_悲觀": 1.0 if sent == "悲觀" else 0.0,
            "rule_VALUE": 1.0 if rule == "VALUE" else 0.0,
            "rule_HSI_REGIME": 1.0 if rule == "HSI_REGIME" else 0.0,
            "rule_ANTI-REBOUND": 1.0 if rule == "ANTI-REBOUND" else 0.0,
            "rule_ANTI-MOM-EXT": 1.0 if rule == "ANTI-MOM-EXT" else 0.0,
            "rule_BOUNCE": 1.0 if rule == "BOUNCE" else 0.0,
            "rule_CONSERVATIVE": 1.0 if rule == "CONSERVATIVE" else 0.0,
            "rule_ANTI-CHASE": 1.0 if rule == "ANTI-CHASE" else 0.0,
            "rule_ANTI-KNIFE": 1.0 if rule == "ANTI-KNIFE" else 0.0,
            "rule_ANTI-MOMENTUM": 1.0 if rule == "ANTI-MOMENTUM" else 0.0,
            "rule_DEFAULT": 1.0 if rule == "DEFAULT" else 0.0,
        }
        feats_list.append(feat)
        labels.append(1.0 if r["win"] else 0.0)

    # Train LR via gradient descent
    n = len(feats_list)
    if n == 0:
        print("ERROR: no training data")
        return

    # Initialize weights
    keys = list(feats_list[0].keys())
    weights = {k: 0.0 for k in keys}
    bias = 0.0

    # Standardize
    means = {k: sum(f[k] for f in feats_list) / n for k in keys}
    stds = {k: max(0.1, (sum((f[k] - means[k]) ** 2 for f in feats_list) / n) ** 0.5) for k in keys}

    # Sigmoid
    def sigmoid(z):
        if z > 30: return 1.0
        if z < -30: return 0.0
        return 1.0 / (1.0 + math.exp(-z))

    # Train
    lr = 0.5
    for epoch in range(200):
        total_loss = 0
        for i, f in enumerate(feats_list):
            z = bias
            for k in keys:
                z += weights[k] * (f[k] - means[k]) / stds[k]
            p = sigmoid(z)
            y = labels[i]
            err = p - y
            for k in keys:
                weights[k] -= lr * err * (f[k] - means[k]) / stds[k]
            bias -= lr * err
            # log loss
            eps = 1e-10
            total_loss -= y * math.log(max(p, eps)) + (1 - y) * math.log(max(1 - p, eps))
        if epoch % 50 == 0 or epoch == 199:
            print(f"  epoch {epoch:3d}  loss={total_loss/n:.4f}")

    # Print weights
    print("\n=== Trained _LR_WEIGHTS (paste into signal_decision.py) ===")
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

    # Compute training accuracy
    correct = 0
    for i, f in enumerate(feats_list):
        z = bias
        for k in keys:
            z += weights[k] * (f[k] - means[k]) / stds[k]
        p = sigmoid(z)
        pred = 1 if p >= 0.5 else 0
        if pred == labels[i]:
            correct += 1
    print(f"\nTraining accuracy: {correct/n*100:.1f}% ({correct}/{n})")

    # Top-20 by predicted prob
    probs = []
    for i, f in enumerate(feats_list):
        z = bias
        for k in keys:
            z += weights[k] * (f[k] - means[k]) / stds[k]
        p = sigmoid(z)
        probs.append((p, labels[i], feats_list[i].get("rule_VALUE", 0)))

    probs.sort(key=lambda x: -x[0])
    print("\n=== Pred prob buckets ===")
    for thresh in [0.6, 0.55, 0.5, 0.45, 0.4]:
        bucket = [(p, y) for p, y, _ in probs if p >= thresh]
        if not bucket:
            continue
        wr = sum(y for _, y in bucket) / len(bucket) * 100
        print(f"  pred≥{thresh}: n={len(bucket):4d}  WR={wr:5.1f}%")

    # VALUE only
    value_probs = [(p, y) for p, y, v in probs if v == 1.0]
    if value_probs:
        wr = sum(y for _, y in value_probs) / len(value_probs) * 100
        print(f"\nVALUE only: n={len(value_probs)}  WR={wr:.1f}%")
        value_probs.sort(key=lambda x: -x[0])
        for thresh in [0.6, 0.55, 0.5, 0.45]:
            bucket = [(p, y) for p, y in value_probs if p >= thresh]
            if bucket:
                wr = sum(y for _, y in bucket) / len(bucket) * 100
                print(f"  VALUE pred≥{thresh}: n={len(bucket):3d}  WR={wr:5.1f}%")


if __name__ == "__main__":
    main()

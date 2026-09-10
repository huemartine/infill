"""Composite-weight tuner. Calibrates composite_weights against the back-test's
ground truth (did a parcel get developed?), with heavy guards against overfitting
the small clean sample (~146 positives):

  - Contamination-free labels: only currently-still-vacant parcels, so their
    stored component scores reflect the pre-development state. Positive = pulled
    a new-building permit since the cutoff; negative = didn't.
  - Metric: AUC (does a developed parcel outrank a non-developed one) - threshold
    -free and robust to the extreme class imbalance.
  - Nested cross-validation: weights are SELECTED on training folds and scored
    only on held-out folds, so the reported number reflects generalization, not
    fit. Repeated for stability.
  - Regularization: each weight bounded to [0.05, 0.40] and the grid is coarse
    (0.05 steps) - no false precision on 146 points, and every component stays
    materially represented (keeps the domain thesis intact).
  - Decision rule: adopt tuned weights ONLY if their held-out AUC beats the spec
    defaults by more than the cross-validation noise. Otherwise keep defaults.

    python -m scoring.tune            # report; does not change config
"""
from __future__ import annotations

import itertools

import numpy as np

COMPONENTS = ["underutilization_score", "capacity_gap_score", "distress_score",
              "assemblage_score", "demand_score"]
DEFAULT = np.array([0.30, 0.25, 0.25, 0.10, 0.10])
_MIN_W, _MAX_W, _STEP = 0.05, 0.40, 0.05
_DEV_CUTOFF = "2022-01-01"
_FOLDS, _REPEATS = 5, 4


def _load():
    """Labeled matrix. Label = new-build permit on/after the cutoff. To avoid
    leakage (demand is built from the permit stream that also defines the label),
    the demand component here is recomputed from permits issued strictly BEFORE
    the cutoff - so it can only carry PAST development momentum, never peek at the
    outcome. The other four components don't touch permit data, so they're used
    as stored."""
    from sqlalchemy import text

    from ingestion.load import _engine
    sql = text(f"""
        WITH pre_demand AS (   -- density from permits strictly before the label window
            SELECT pc.neighborhood,
                   100.0 * least(1000.0 * coalesce(pre.b, 0) / pc.parcels / 15.0, 1.0) AS demand_lf
            FROM (SELECT neighborhood, count(*) AS parcels FROM parcel_master
                  WHERE neighborhood IS NOT NULL GROUP BY 1) pc
            LEFT JOIN (SELECT initcap(neighborhood) AS nb, count(*) AS b
                       FROM development_permits WHERE issued_date < '{_DEV_CUTOFF}'
                       GROUP BY 1) pre ON pre.nb = pc.neighborhood
        )
        SELECT s.underutilization_score, s.capacity_gap_score, s.distress_score,
               s.assemblage_score, pd.demand_lf,
               (EXISTS (SELECT 1 FROM development_permits p
                        WHERE p.parcel_id = m.parcel_id
                          AND p.issued_date >= '{_DEV_CUTOFF}'))::int AS label
        FROM parcel_master m
        JOIN parcel_scores s USING (parcel_id)
        JOIN neighborhood_demand nd ON nd.neighborhood = m.neighborhood AND nd.permit_covered
        JOIN pre_demand pd ON pd.neighborhood = m.neighborhood
        WHERE coalesce(m.improvement_value, 0) <= 20000 AND s.opportunity_score > 0
    """)
    with _engine().connect() as conn:
        rows = conn.execute(sql).all()
    comp = np.array([[_f(r[i]) for i in range(5)] for r in rows], dtype=float)
    labels = np.array([r[5] for r in rows], dtype=int)
    mask = ~np.isnan(comp)              # which components are present per parcel
    comp0 = np.nan_to_num(comp, nan=0.0)
    return comp0, mask.astype(float), labels


def _f(v):
    return float("nan") if v is None else float(v)


def blend(comp0, mask, w):
    """Per-parcel weighted mean over present components (matches composite())."""
    denom = mask @ w
    denom[denom == 0] = np.nan
    return (comp0 @ w) / denom


def auc(scores, labels):
    """Mann-Whitney AUC with average ranks for ties (fully vectorized). 0.5 = no
    signal. Component-blend scores tie heavily, so tie handling matters."""
    ok = ~np.isnan(scores)
    scores, labels = scores[ok], labels[ok]
    n_pos = int(labels.sum())
    n_neg = len(labels) - n_pos
    if n_pos == 0 or n_neg == 0:
        return 0.5
    order = np.argsort(scores, kind="mergesort")
    s = scores[order]
    _, inv, counts = np.unique(s, return_inverse=True, return_counts=True)
    starts = np.zeros(len(counts))
    starts[1:] = np.cumsum(counts)[:-1]
    avg_rank = (starts + (counts + 1) / 2.0)[inv]   # 1-based average rank in sorted order
    ranks = np.empty(len(scores))
    ranks[order] = avg_rank
    return (ranks[labels == 1].sum() - n_pos * (n_pos + 1) / 2) / (n_pos * n_neg)


def _candidates():
    steps = int(round((_MAX_W - _MIN_W) / _STEP))
    lo = int(round(_MIN_W / _STEP))
    hi = int(round(_MAX_W / _STEP))
    total = int(round(1.0 / _STEP))
    out = []
    for combo in itertools.product(range(lo, hi + 1), repeat=4):
        last = total - sum(combo)
        if lo <= last <= hi:
            out.append(np.array([*combo, last]) * _STEP)
    return out


def _folds(labels, k, seed):
    rng = np.random.default_rng(seed)
    idx = np.arange(len(labels))
    pos, neg = idx[labels == 1], idx[labels == 0]
    rng.shuffle(pos); rng.shuffle(neg)
    return [np.concatenate([pos[f::k], neg[f::k]]) for f in range(k)]  # stratified


def run() -> None:
    comp0, mask, labels = _load()
    print(f"clean labeled set: {len(labels)} parcels, {int(labels.sum())} developed (positives)")
    cands = _candidates()
    print(f"searching {len(cands)} weight vectors, bounds [{_MIN_W},{_MAX_W}], "
          f"{_FOLDS}-fold x{_REPEATS} nested CV\n")

    default_scores, tuned_scores = [], []
    picks = []
    for rep in range(_REPEATS):
        for test_idx in _folds(labels, _FOLDS, seed=rep):
            train_mask = np.ones(len(labels), bool)
            train_mask[test_idx] = False
            tr = (comp0[train_mask], mask[train_mask], labels[train_mask])
            te = (comp0[test_idx], mask[test_idx], labels[test_idx])
            # default: no selection, just evaluate held-out
            default_scores.append(auc(blend(*te[:2], DEFAULT), te[2]))
            # tuned: SELECT best on train, evaluate on held-out (nested)
            best_w, best_a = None, -1
            for w in cands:
                a = auc(blend(tr[0], tr[1], w), tr[2])
                if a > best_a:
                    best_a, best_w = a, w
            tuned_scores.append(auc(blend(te[0], te[1], best_w), te[2]))
            picks.append(best_w)

    d_mean, d_std = np.mean(default_scores), np.std(default_scores)
    t_mean, t_std = np.mean(tuned_scores), np.std(tuned_scores)
    print(f"held-out AUC  default weights : {d_mean:.4f}  +/- {d_std:.4f}")
    print(f"held-out AUC  tuned (nested)  : {t_mean:.4f}  +/- {t_std:.4f}")
    print(f"improvement                   : {t_mean - d_mean:+.4f}  "
          f"(noise band ~{d_std:.4f})")

    # what the selection converges to across folds (stability check)
    avg_pick = np.mean(picks, axis=0)
    print("\naverage selected weights across folds:")
    for name, w in zip(COMPONENTS, avg_pick):
        print(f"  {name:<24} {w:.3f}")

    # honest decision: adopt only if held-out gain clears the noise band
    if t_mean - d_mean > d_std:
        final = _refit_full(comp0, mask, labels, cands)
        print("\nDECISION: tuned weights robustly beat defaults -> ADOPT")
        print("  recommended composite_weights:")
        for name, w in zip(COMPONENTS, final):
            print(f"    {name.replace('_score',''):<20} {w:.2f}")
    else:
        print("\nDECISION: improvement within noise -> KEEP SPEC DEFAULTS "
              "(no overfitting a 146-point sample)")


def _refit_full(comp0, mask, labels, cands):
    best_w, best_a = DEFAULT, auc(blend(comp0, mask, DEFAULT), labels)
    for w in cands:
        a = auc(blend(comp0, mask, w), labels)
        if a > best_a:
            best_a, best_w = a, w
    return best_w


if __name__ == "__main__":
    run()

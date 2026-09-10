"""Back-test: does the opportunity score predict where development actually
happens? Ground truth = new-building permits (ingestion/permits.py).

    python -m scoring.backtest

The current score is CONTAMINATED for already-developed parcels: once a building
goes up, underutilization drops and the parcel scores low by design. So we can't
ask "do developed parcels score high now." Instead we test three angles that are
robust to that contamination:

  A. Neighborhood demand (fully clean): neighborhoods don't get developed away.
     Does our neighborhood demand_score correlate with actual new-building
     permit density?

  B. In-progress cohort (clean): parcels that pulled a build permit very recently
     but whose assessment still shows them vacant/low-value - their current score
     still reflects the PRE-development opportunity. Do they score above the
     vacant-parcel baseline?

  C. Durable pre-conditions: did developed parcels disproportionately come from
     high-demand neighborhoods vs the county baseline?
"""
from __future__ import annotations

from statistics import mean


def run() -> None:
    from sqlalchemy import text

    from ingestion.load import _engine

    eng = _engine()
    with eng.connect() as conn:
        _angle_a_neighborhood(conn, text)
        _angle_b_in_progress(conn, text)
        _angle_c_durable(conn, text)


def _pearson(xs, ys):
    n = len(xs)
    if n < 3:
        return None
    mx, my = mean(xs), mean(ys)
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    dx = sum((x - mx) ** 2 for x in xs) ** 0.5
    dy = sum((y - my) ** 2 for y in ys) ** 0.5
    return num / (dx * dy) if dx and dy else None


def _spearman(xs, ys):
    def rank(v):
        order = sorted(range(len(v)), key=lambda i: v[i])
        r = [0] * len(v)
        for pos, i in enumerate(order):
            r[i] = pos
        return r
    return _pearson(rank(xs), rank(ys))


def _angle_a_neighborhood(conn, text) -> None:
    # temporal holdout (not circular): build density from permits ISSUED BEFORE
    # 2024 predicts build density AFTER 2024. Demand v2 is trained on the same
    # trailing-permit signal, so this measures exactly what the score relies on.
    rows = conn.execute(text("""
        WITH pc AS (SELECT neighborhood, count(*) AS parcels FROM parcel_master
                    WHERE neighborhood IS NOT NULL GROUP BY 1 HAVING count(*) >= 200),
        pm AS (SELECT initcap(neighborhood) AS nb,
                 count(*) FILTER (WHERE issued_date < '2024-01-01') AS train,
                 count(*) FILTER (WHERE issued_date >= '2024-01-01') AS test
               FROM development_permits GROUP BY 1)
        SELECT pc.neighborhood, pc.parcels,
               1000.0*coalesce(pm.train,0)/pc.parcels AS train_per1k,
               1000.0*coalesce(pm.test,0)/pc.parcels  AS test_per1k,
               nd.demand_score
        FROM pc JOIN neighborhood_demand nd USING (neighborhood)
        LEFT JOIN pm ON pm.nb = pc.neighborhood
        WHERE nd.permit_covered
    """)).mappings().all()
    train = [float(r["train_per1k"]) for r in rows]
    test = [float(r["test_per1k"]) for r in rows]
    demand = [float(r["demand_score"]) for r in rows if r["demand_score"] is not None]
    test_for_demand = [float(r["test_per1k"]) for r in rows if r["demand_score"] is not None]

    print("=" * 66)
    print("ANGLE A - does development demand predict FUTURE construction?")
    print(f"  {len(rows)} Cincinnati neighborhoods, temporal holdout (train <2024, test >=2024)")
    print(f"  pre-2024 build density -> post-2024 build density:")
    print(f"     Pearson r  = {_fmt(_pearson(train, test))}")
    print(f"     Spearman r = {_fmt(_spearman(train, test))}   (rank)")
    print(f"  demand_score (v2) -> post-2024 build density:")
    print(f"     Pearson r  = {_fmt(_pearson(demand, test_for_demand))}")
    print(f"     Spearman r = {_fmt(_spearman(demand, test_for_demand))}   (rank)")
    top = sorted((r for r in rows if r["demand_score"] is not None),
                 key=lambda r: -float(r["demand_score"]))[:6]
    print("  model's top development-demand neighborhoods -> actual post-2024 builds:")
    for r in top:
        print(f"     {r['neighborhood']:<18} demand {float(r['demand_score']):5.1f}  "
              f"{float(r['test_per1k']):5.2f} builds/1k (2024+)")


def _angle_b_in_progress(conn, text) -> None:
    """DISCOVERY score, not the shipped opportunity score. Since v2026.18 a
    recent build permit applies an availability penalty (the lot is taken), and
    this cohort is *defined* by having a permit — so the shipped score would
    measure the penalty, not the model's ability to find the parcel. We blend the
    stored components instead: 'would the model have surfaced this before anyone
    broke ground?'"""
    discovery = """
        (0.40 * coalesce(s.development_score, 0)
       + 0.25 * coalesce(s.owner_motivation_score, 0)
       + 0.20 * coalesce(s.distress_score, 0)
       + 0.15 * coalesce(s.demand_score, 0))
    """
    cohort = conn.execute(text(f"""
        SELECT {discovery} AS score
        FROM development_permits p
        JOIN parcel_master m ON m.parcel_id = p.parcel_id
        JOIN parcel_scores s ON s.parcel_id = p.parcel_id
        WHERE p.issued_date >= '2024-01-01'
          AND coalesce(m.improvement_value, 0) <= 20000
    """)).scalars().all()
    baseline = conn.execute(text(f"""
        SELECT {discovery} AS score
        FROM parcel_master m JOIN parcel_scores s USING (parcel_id)
        WHERE m.neighborhood IS NOT NULL
          AND coalesce(m.improvement_value, 0) <= 20000
          AND s.opportunity_score > 0
    """)).scalars().all()
    print("=" * 66)
    print("ANGLE B - DISCOVERY signal on in-progress cohort vs vacant baseline")
    if cohort:
        c = [float(x) for x in cohort]
        b = [float(x) for x in baseline]
        print(f"  cohort: {len(c)} parcels w/ 2024+ build permit, still ~vacant")
        print(f"  cohort median score  = {_median(c):5.1f}   mean = {mean(c):5.1f}")
        print(f"  baseline median score = {_median(b):5.1f}   mean = {mean(b):5.1f}   ({len(b)} vacant parcels)")
        print(f"  cohort scoring >=50: {_pct(c, 50):4.0f}%   baseline: {_pct(b, 50):4.0f}%")
        print(f"  cohort scoring >=70: {_pct(c, 70):4.0f}%   baseline: {_pct(b, 70):4.0f}%")
        lift = (mean(c) / mean(b)) if mean(b) else None
        print(f"  => cohort mean is {_fmt(lift)}x the baseline")
    else:
        print("  no in-progress cohort found")


def _angle_c_durable(conn, text) -> None:
    row = conn.execute(text("""
        WITH dev AS (
            SELECT DISTINCT m.neighborhood
            FROM development_permits p JOIN parcel_master m ON m.parcel_id = p.parcel_id
        )
        SELECT
          (SELECT round(avg(nd.demand_score),1) FROM development_permits p
             JOIN parcel_master m ON m.parcel_id=p.parcel_id
             JOIN neighborhood_demand nd ON nd.neighborhood=m.neighborhood) AS dev_avg_demand,
          (SELECT round(avg(demand_score),1) FROM neighborhood_demand) AS county_avg_demand
    """)).mappings().one()
    print("=" * 66)
    print("ANGLE C - durable pre-conditions of parcels that got developed")
    print(f"  avg neighborhood demand of developed parcels: {row['dev_avg_demand']}")
    print(f"  county-wide avg neighborhood demand:          {row['county_avg_demand']}")


def _median(v):
    s = sorted(v)
    n = len(s)
    return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2


def _pct(v, thr):
    return 100.0 * sum(1 for x in v if x >= thr) / len(v) if v else 0.0


def _fmt(x):
    return "n/a" if x is None else f"{x:.2f}"


if __name__ == "__main__":
    run()

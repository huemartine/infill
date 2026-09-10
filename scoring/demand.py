"""Neighborhood demand (Section 9.x / Phase 5) — the fifth composite component,
built to be legible, not a black box.

    python -m scoring.demand      # rebuild neighborhood_demand

Three metrics a realtor reads directly (all from Auditor sale history already
in parcel_master; sales below the price floor are non-arms-length transfers and
excluded):

  - turnover_pct: share of the neighborhood's parcels that traded in the last
    24 months — is anything moving here?
  - momentum_pct: median sale price, last 24 months vs the 24 before — which
    way are prices going?
  - sale_to_value: median ratio of sale price to county-assessed total value —
    are buyers paying over or under the assessor's number?

demand_score is the documented weighted blend of the three (scoring.yaml
`demand:`), stored alongside the raw metrics so the dossier can show both.
"""
from __future__ import annotations

from typing import Optional


def build() -> dict:
    from sqlalchemy import text

    from config import settings
    from ingestion.load import _engine
    from scoring.score import load_config

    cfg = load_config(settings.scoring_config_path)["demand"]
    eng = _engine()
    with eng.begin() as conn:
        conn.execute(text("DELETE FROM neighborhood_demand"))
        conn.execute(text("""
            INSERT INTO neighborhood_demand (neighborhood, parcel_count, sales_24mo,
                turnover_pct, median_price_24mo, median_price_prior, momentum_pct,
                sale_to_value)
            SELECT
                neighborhood,
                count(*),
                count(*) FILTER (WHERE recent),
                round(100.0 * count(*) FILTER (WHERE recent) / count(*), 2),
                percentile_cont(0.5) WITHIN GROUP (ORDER BY last_sale_price)
                    FILTER (WHERE recent),
                percentile_cont(0.5) WITHIN GROUP (ORDER BY last_sale_price)
                    FILTER (WHERE prior),
                NULL,
                percentile_cont(0.5) WITHIN GROUP
                    (ORDER BY least(last_sale_price / nullif(total_value, 0), 3.0))
                    FILTER (WHERE recent AND total_value > 0)
            FROM (
                SELECT neighborhood, last_sale_price, total_value,
                       (last_sale_date >= now() - interval ':w months'
                        AND last_sale_price >= :floor) AS recent,
                       (last_sale_date >= now() - interval ':w2 months'
                        AND last_sale_date <  now() - interval ':w months'
                        AND last_sale_price >= :floor) AS prior
                FROM parcel_master WHERE neighborhood IS NOT NULL
            ) x
            GROUP BY neighborhood
        """.replace("':w months'", f"'{cfg['window_months']} months'")
           .replace("':w2 months'", f"'{cfg['window_months'] * 2} months'")),
            {"floor": cfg["min_market_price"]})
        conn.execute(text("""
            UPDATE neighborhood_demand
            SET momentum_pct = round(100.0 * (median_price_24mo - median_price_prior)
                                     / median_price_prior, 1)
            WHERE median_price_prior > 0 AND median_price_24mo IS NOT NULL
        """))

        # --- development demand: new-building permits per 1,000 parcels ---
        recent = cfg["dev_recent_months"]
        window = cfg["dev_window_years"] * 12
        conn.execute(text(f"""
            WITH pm AS (
                SELECT initcap(neighborhood) AS nb,
                    count(*) FILTER (WHERE issued_date >= now() - interval '{window} months') AS trailing,
                    count(*) FILTER (WHERE issued_date >= now() - interval '{recent} months') AS rec,
                    count(*) FILTER (WHERE issued_date <  now() - interval '{recent} months'
                                     AND issued_date >= now() - interval '{recent * 2} months') AS pri
                FROM development_permits GROUP BY 1)
            UPDATE neighborhood_demand nd SET
                builds_trailing = coalesce(pm.trailing, 0),
                builds_recent   = coalesce(pm.rec, 0),
                builds_prior    = coalesce(pm.pri, 0),
                builds_per_1k   = round(1000.0 * coalesce(pm.trailing, 0) / nullif(nd.parcel_count,0), 2)
            FROM (SELECT * FROM pm) pm WHERE pm.nb = nd.neighborhood
        """))
        # neighborhoods with no permit match still get 0 builds (city SNAs), but
        # township/other jurisdictions have no permit coverage at all -> null score
        conn.execute(text("""
            UPDATE neighborhood_demand nd SET
                builds_trailing = coalesce(builds_trailing, 0),
                builds_recent = coalesce(builds_recent, 0),
                builds_prior = coalesce(builds_prior, 0),
                builds_per_1k = coalesce(builds_per_1k, 0),
                permit_covered = EXISTS (
                    SELECT 1 FROM constraint_zones z
                    WHERE z.layer = 'neighborhood' AND initcap(z.zone) = nd.neighborhood)
        """))
        conn.execute(text("""
            UPDATE neighborhood_demand SET dev_trajectory_pct =
                CASE WHEN builds_prior > 0
                     THEN round(100.0 * (builds_recent - builds_prior) / builds_prior, 1)
                     WHEN builds_recent > 0 THEN 999 ELSE NULL END
        """))

        rows = conn.execute(text("SELECT * FROM neighborhood_demand")).mappings().all()
        for r in rows:
            conn.execute(text(
                "UPDATE neighborhood_demand SET demand_score = :s WHERE neighborhood = :n"),
                {"s": demand_score(r["permit_covered"], r["builds_per_1k"],
                                   r["dev_trajectory_pct"], {"demand": cfg}),
                 "n": r["neighborhood"]})
        n = len(rows)
    print(f"demand build: {n} neighborhoods", flush=True)
    return {"neighborhoods": n}


def demand_score(permit_covered: Optional[bool], builds_per_1k: Optional[float],
                 dev_trajectory_pct: Optional[float], cfg: dict) -> Optional[float]:
    """Development-demand blend (scoring.yaml `demand`). None outside permit
    coverage (townships) so the composite excludes rather than zeroes it.
    Density is the primary signal; trajectory nudges for accel/cooling."""
    d = cfg["demand"]
    if not permit_covered:
        return None
    density = min(float(builds_per_1k or 0) / d["dev_full_per_1k"], 1.0)
    if dev_trajectory_pct is None:
        traj = 0.5                      # no prior baseline: neutral
    else:
        cap = d["dev_trajectory_cap_pct"]
        traj = min(max((float(dev_trajectory_pct) + cap) / (2 * cap), 0.0), 1.0)
    w = d["weights"]
    return round(100.0 * (w["dev_density"] * density + w["dev_trajectory"] * traj), 2)


if __name__ == "__main__":
    build()

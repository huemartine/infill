"""Condition gap — the "run-down house on a nice street" signal, recreated from
county data instead of a drive-by.

For each single-family (DTE 510) improved house with a known finished size, we
measure how far its assessed BUILDING value per sqft sits BELOW the local median
of nearby same-class houses. The assessor's improvement value already encodes
age / condition / quality, so a home valued at a fraction of its neighbors'
per-foot is the dilapidated house a teardown team hunts for. 0 = at/above the
local median, 100 = far below.

We also record `area_land_value` — the median LAND value of that same local peer
set — so a search can fold in "and the area is valuable" (a rough house in a
GREAT neighborhood, not merely the worst house on a cheap block).

Peers are same-class improved houses within ~500m (>=5 required). Subjects are
scoped to the team's target regions (the only houses the app surfaces); peers are
drawn from ALL nearby parcels so a house near a region boundary still gets the
right local context. Full recompute each run so value changes propagate.

Validated against Cincinnati code violations: houses at condition_gap >= 40 draw
roughly double the violation rate of houses that fit their block.
"""
from __future__ import annotations

# ~500m in degrees at this latitude; a geometry (not geography) predicate so the
# centroid GIST index is used — the ::geography cast seq-scans and is ~100x slower.
_RADIUS_DEG = 0.005
_MIN_PEERS = 5


def build() -> dict:
    from sqlalchemy import text

    from config import settings
    from ingestion.load import _engine

    eng = _engine()
    with eng.begin() as conn:
        conn.execute(text(
            "UPDATE parcel_master SET condition_gap = NULL, area_land_value = NULL "
            "WHERE condition_gap IS NOT NULL OR area_land_value IS NOT NULL"))
        res = conn.execute(text("""
            WITH subject AS (
              SELECT m.parcel_id, m.centroid,
                     (m.improvement_value / m.home_sqft) AS psf
              FROM parcel_master m
              WHERE m.land_use_code = '510' AND m.improvement_value > 0
                AND m.home_sqft > 400 AND m.neighborhood = ANY(:targets)
            )
            UPDATE parcel_master t
            SET condition_gap = sub.gap, area_land_value = sub.area_land
            FROM (
              SELECT s.parcel_id,
                     GREATEST(0, LEAST(100,
                       round((1 - s.psf / NULLIF(peer.med_psf, 0)) * 100)))::numeric AS gap,
                     round(peer.med_land)::numeric AS area_land
              FROM subject s
              CROSS JOIN LATERAL (
                SELECT percentile_cont(0.5) WITHIN GROUP (
                         ORDER BY p.improvement_value / p.home_sqft) AS med_psf,
                       percentile_cont(0.5) WITHIN GROUP (ORDER BY p.land_value) AS med_land,
                       count(*) AS n
                FROM parcel_master p
                WHERE p.land_use_code = '510' AND p.improvement_value > 0 AND p.home_sqft > 400
                  AND p.parcel_id <> s.parcel_id
                  AND ST_DWithin(p.centroid, s.centroid, :r)
              ) peer
              WHERE peer.n >= :min_peers AND peer.med_psf > 0
            ) sub
            WHERE t.parcel_id = sub.parcel_id
        """), {"targets": list(settings.target_regions),
               "r": _RADIUS_DEG, "min_peers": _MIN_PEERS})
        n = res.rowcount
    print(f"condition_gap computed: {n} houses", flush=True)
    return {"condition_scored": n}


if __name__ == "__main__":
    build()

"""Derived parcel attributes that need cross-parcel context (so they can't live
in the per-row extractor normalizers).

    python -m ingestion.derive

Currently: complex-satellite detection. A deed with no improvement value whose
same-address, same-owner sibling carries substantial improvements is part of
one physical property split across several deeds — the parking lot or lawn of
an apartment complex, not an independently developable site. Scoring treats
these as non-vacant and flags them so the dossier explains the exclusion.
"""
from __future__ import annotations

# Same-address sibling: any real building there means this deed is part of one
# property split across deeds (apartment complexes, 4045 Reading Rd).
_SIBLING_IMPROVEMENT_FLOOR = 50_000

# Merely *adjacent* same-owner buildings are ambiguous: a vacant lot beside the
# owner's rental house is a genuine side-lot play (~31k countywide, and the
# land bank runs a Side Lot program on exactly these). Only a substantial
# commercial neighbour implies the vacant parcel is its parking/service area —
# e.g. 5801 Colerain Ave, the outparcel of the same owner's $1.3M store.
_ADJACENT_COMMERCIAL_FLOOR = 500_000

# Builder inventory: this many same-owner lots sharing one plat date and an
# identical assessed land value is a subdivision under construction.
_SUBDIVISION_MIN_LOTS = 3


def flag_complex_satellites() -> dict:
    """Deeds whose buildings are booked on another deed of the same property."""
    from sqlalchemy import text

    from ingestion.load import _engine

    counts = {}
    eng = _engine()
    with eng.begin() as conn:
        conn.execute(text("UPDATE parcel_master SET is_satellite = false "
                          "WHERE is_satellite IS DISTINCT FROM false"))
    with eng.begin() as conn:
        counts["same_address"] = conn.execute(text("""
            UPDATE parcel_master m SET is_satellite = true
            WHERE coalesce(m.improvement_value, 0) = 0
              AND m.address IS NOT NULL
              AND m.owner_name_norm IS NOT NULL
              AND EXISTS (
                SELECT 1 FROM parcel_master sib
                WHERE sib.address = m.address
                  AND sib.owner_name_norm = m.owner_name_norm
                  AND sib.parcel_id <> m.parcel_id
                  AND sib.improvement_value >= :floor)
        """), {"floor": _SIBLING_IMPROVEMENT_FLOOR}).rowcount
    with eng.begin() as conn:
        counts["adjacent_commercial"] = conn.execute(text("""
            UPDATE parcel_master m SET is_satellite = true
            WHERE coalesce(m.improvement_value, 0) = 0
              AND NOT coalesce(m.is_satellite, false)
              AND m.owner_name_norm IS NOT NULL AND m.geom IS NOT NULL
              AND EXISTS (
                SELECT 1 FROM parcel_master sib
                WHERE sib.owner_name_norm = m.owner_name_norm
                  AND sib.parcel_id <> m.parcel_id
                  AND sib.improvement_value >= :floor
                  AND ST_Intersects(m.geom, sib.geom))
        """), {"floor": _ADJACENT_COMMERCIAL_FLOOR}).rowcount
    print(f"complex satellites flagged: {counts}", flush=True)
    return counts


def flag_subdivision_inventory() -> dict:
    """Platted lots a builder is actively developing: same owner, one plat
    transfer date, identical assessed land value, at least _SUBDIVISION_MIN_LOTS
    together. Soft-flagged so the scorer can damp them with a distress waiver."""
    from sqlalchemy import text

    from ingestion.load import _engine

    eng = _engine()
    with eng.begin() as conn:
        conn.execute(text("UPDATE parcel_master SET is_subdivision_lot = false "
                          "WHERE is_subdivision_lot IS DISTINCT FROM false"))
    with eng.begin() as conn:
        res = conn.execute(text("""
            UPDATE parcel_master m SET is_subdivision_lot = true
            FROM (
                SELECT owner_name_norm, last_sale_date, land_value
                FROM parcel_master
                WHERE coalesce(improvement_value, 0) = 0
                  AND land_use_code IN ('500', '510')
                  AND last_sale_date IS NOT NULL
                  AND land_value > 0
                  AND owner_name_norm IS NOT NULL
                GROUP BY owner_name_norm, last_sale_date, land_value
                HAVING count(*) >= :minlots
            ) g
            WHERE m.owner_name_norm = g.owner_name_norm
              AND m.last_sale_date = g.last_sale_date
              AND m.land_value = g.land_value
              AND coalesce(m.improvement_value, 0) = 0
              AND m.land_use_code IN ('500', '510')
        """), {"minlots": _SUBDIVISION_MIN_LOTS})
    summary = {"subdivision_lots": res.rowcount}
    print(f"subdivision inventory flagged: {summary}", flush=True)
    return summary


def flag_condo_units() -> dict:
    """Deeds sharing a physical footprint with another deed = condominium /
    horizontal-property regime. Detected by identical centroid + area (a fast,
    exact proxy for ST_Equals on 420k parcels — condo unit deeds are digitized
    from one plat, so their geometry is bit-identical). A condo unit is not an
    independently developable lot, and N units on one footprint must not each
    claim the site's full development potential."""
    from sqlalchemy import text

    from ingestion.load import _engine

    eng = _engine()
    with eng.begin() as conn:
        conn.execute(text("UPDATE parcel_master SET is_condo_unit = false "
                          "WHERE is_condo_unit IS DISTINCT FROM false"))
    with eng.begin() as conn:
        res = conn.execute(text("""
            UPDATE parcel_master m SET is_condo_unit = true
            FROM (
                SELECT round(ST_X(centroid)::numeric, 6) AS cx,
                       round(ST_Y(centroid)::numeric, 6) AS cy,
                       round(area_sqft::numeric, 1) AS a
                FROM parcel_master
                WHERE centroid IS NOT NULL AND area_sqft > 0
                GROUP BY 1, 2, 3
                HAVING count(*) > 1
            ) g
            WHERE round(ST_X(m.centroid)::numeric, 6) = g.cx
              AND round(ST_Y(m.centroid)::numeric, 6) = g.cy
              AND round(m.area_sqft::numeric, 1) = g.a
        """))
    summary = {"condo_units": res.rowcount}
    print(f"condo units flagged: {summary}", flush=True)
    return summary


# ~150 ft in degrees at Cincinnati's latitude (anisotropic but fine for a
# neighborhood-context radius; ST_DWithin on geometry stays GIST-accelerated)
_CONTEXT_RADIUS_DEG = 0.0005


def flag_residential_context() -> dict:
    """res_context_pct: share of neighbors (within ~150 ft) that are improved
    residential (DTE 510-599 with a building). The spec's `infill` measure - a
    vacant lot only reads as *housing* infill when houses actually surround it.
    Computed for vacant-thesis candidates (improvement <= $20k) only; everyone
    else keeps NULL and the scoring factor stays neutral."""
    from sqlalchemy import text

    from ingestion.load import _engine

    eng = _engine()
    with eng.begin() as conn:
        conn.execute(text("UPDATE parcel_master SET res_context_pct = NULL "
                          "WHERE res_context_pct IS NOT NULL"))
    with eng.begin() as conn:
        res = conn.execute(text(f"""
            UPDATE parcel_master m SET res_context_pct = q.pct
            FROM (
                SELECT t.parcel_id,
                       round(100.0 * count(*) FILTER (
                           WHERE n.land_use_code ~ '^[0-9]+$'
                             AND n.land_use_code::int BETWEEN 510 AND 599
                             AND coalesce(n.improvement_value, 0) > 0)
                             / nullif(count(*), 0), 1) AS pct
                FROM parcel_master t
                JOIN parcel_master n
                  ON n.parcel_id <> t.parcel_id
                 AND ST_DWithin(t.centroid, n.centroid, {_CONTEXT_RADIUS_DEG})
                WHERE t.centroid IS NOT NULL
                  AND coalesce(t.improvement_value, 0) <= 20000
                GROUP BY t.parcel_id
            ) q WHERE m.parcel_id = q.parcel_id
        """))
    summary = {"context_computed": res.rowcount}
    print(f"residential context: {summary}", flush=True)
    return summary


def build() -> dict:
    return {"satellites": flag_complex_satellites(),
            "subdivision": flag_subdivision_inventory(),
            "condo": flag_condo_units(),
            "res_context": flag_residential_context()}


if __name__ == "__main__":
    build()

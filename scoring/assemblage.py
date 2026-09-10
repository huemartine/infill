"""Assemblies: adjacent parcels that TOGETHER make a developable tract.

    python -m scoring.assemblage        # rebuild assemblies + member links

Two tiers, kept apart because they are different kinds of deal:

  same_owner   one owner holds every parcel in the cluster. One negotiation, one
               closing — the highest-confidence case (e.g. WOODSVIEW HOUSE LLC
               holding three adjacent lots on Union Cemetery Rd, 19.4 acres).
  multi_owner  adjacent parcels under DIFFERENT owners. A real assembly play, but
               it needs every owner to agree, so it is labelled and listed
               separately and never mixed into the clean list.

This is a FINDER, not a scoring input — nothing here feeds the opportunity score.

Why the previous model produced clutter, and what changed
--------------------------------------------------------
* The floor was `min_combined_sqft: 7500` (0.17 acres), so a house plus its side
  lot qualified: 26,858 of 32,171 clusters (83%) came back under 2 acres. Now the
  floor is real acreage (`min_combined_acres`).
* Nothing filtered bookkeeping parcels: of clusters at 5+ acres, 1,308 contained
  apartment-complex satellite deeds, plus condo regimes, builder subdivision
  inventory, unacquirable public land and golf courses. A cluster is now
  discarded outright if ANY member carries one of those flags.
* Multi-owner adjacency, left unconstrained, chains an entire neighbourhood into
  one blob — measured at 886 parcels. Hence `min_member_acres` (ordinary
  subdivision lots don't participate) and `max_parcels_multi` (more sellers than
  that isn't a deal).

Rebuild is full (truncate + recompute): clusters have no identity worth keeping
across owner changes.
"""
from __future__ import annotations

_ACRE = 43560.0

# A cluster is thrown out entirely if any member is one of these — they are
# bookkeeping or unbuyable parcels, not land you can assemble. Mirrors the
# hidden-class predicates `_lead_filters` uses in api/main.py.
_JUNK_MEMBER = """(
    COALESCE(m.is_condo_unit, false)
 OR COALESCE(m.is_satellite, false)
 OR COALESCE(m.is_subdivision_lot, false)
 OR COALESCE(s.constraints->'active' ? 'not_acquirable', false)
 OR COALESCE(s.constraints->'active' ? 'operating_amenity', false)
 OR COALESCE(s.constraints->'active' ? 'commercial_land', false)
)"""

# Conservation / civic owners whose land is not for sale at any price. These slip
# past owner_type because the classifier misses them — GREENACRES FOUNDATION
# (40 parcels, 164 acres) is typed 'individual' — and they are exactly the
# holdings big enough to top an acreage-sorted list. Name-matched here rather
# than re-deriving owner_type, which would require a full re-ingest and rescore.
_NOT_FOR_SALE_OWNER = (
    # conservation / civic
    r"(FOUNDATION|PRESERVAT|CONSERVAN|LAND TRUST|NATURE (CENTER|PRESERVE)"
    r"|CEMETER|PARK (DIST|BOARD)|TOWNSHIP|VILLAGE OF|CITY OF|BOARD OF EDUC"
    r"|COMMUNITY IMPROVEMENT"
    # religious
    r"|CHURCH|PARISH|DIOCESE|ARCHBISHOP|BISHOP|SYNOD|CONGREGATION|MINISTR"
    r"|TEMPLE|SYNAGOG|MOSQUE"
    # education / health
    r"|SCHOOL|COLLEGE|UNIVERSIT|HOSPITAL"
    # HOA common areas and private clubs — the land exists to serve an existing
    # development or membership and is never sold off for redevelopment
    # NOTE: the word boundary here is Postgres's \y, not Python's \b —
    # \b silently fails to match in Postgres. The test translates it, the same
    # way _BANK_RE is handled in api/main.py.
    r"|HOMEOWNER|OWNERS ASS|CONDOMINIUM|\yHOA\y|\yCLUB\y|MASONIC|LODGE|YMCA)")

# Only land you could actually put houses on: homes, and raw land of any class.
# An IMPROVED commercial or industrial parcel is somebody's operating business —
# it inflated tracts with sites that are neither residential nor buyable. Ohio
# DTE classes: 1xx agricultural, x00 vacant land, 5xx residential.
_DEVELOPABLE_USE = """(
    _lu BETWEEN 100 AND 199        -- agricultural / farmland
 OR _lu IN (300, 400, 500)         -- vacant land (industrial / commercial / residential)
 OR _lu BETWEEN 501 AND 599        -- single-family, 2-3 family, multifamily
)"""


def build() -> dict:
    from sqlalchemy import text

    from config import settings
    from ingestion.load import _engine
    from scoring.score import load_config

    cfg = load_config(settings.scoring_config_path)["assemblage"]
    min_acres = float(cfg["min_combined_acres"])
    min_member_acres = float(cfg["min_member_acres"])
    min_app = float(cfg["min_acres_per_parcel"])
    max_same = int(cfg["max_parcels_same"])
    max_multi = int(cfg["max_parcels_multi"])
    targets = list(settings.target_regions)

    eng = _engine()
    with eng.begin() as conn:
        conn.execute(text("UPDATE parcel_master SET assemblage_id = NULL "
                          "WHERE assemblage_id IS NOT NULL"))
        conn.execute(text("TRUNCATE assemblages RESTART IDENTITY"))

        # Candidate parcels: in the target markets, real geometry, and not one of
        # the bookkeeping/unbuyable classes. `sqft` is NaN-guarded because 2,300
        # parcels carry a NaN area which would poison every SUM downstream.
        # NOTE: compare against 'NaN'::numeric — Postgres numerics define
        # NaN = NaN as TRUE, so the usual IEEE `x <> x` test never fires here.
        conn.execute(text(f"""
            CREATE TEMP TABLE _cand ON COMMIT DROP AS
            SELECT m.parcel_id, m.owner_name_norm, m.neighborhood, m.geom,
                   CASE WHEN m.area_sqft IS NULL OR m.area_sqft = 'NaN'::numeric
                        THEN 0 ELSE m.area_sqft END AS sqft,
                   CASE WHEN m.land_value IS NULL OR m.land_value = 'NaN'::numeric
                        THEN 0 ELSE m.land_value END AS land_value
            FROM parcel_master m
            LEFT JOIN parcel_scores s USING (parcel_id)
            CROSS JOIN LATERAL (
                SELECT NULLIF(regexp_replace(m.land_use_code, '[^0-9]', '', 'g'), '')::int
            ) AS lu(_lu)
            WHERE m.geom IS NOT NULL
              AND m.owner_name_norm IS NOT NULL
              AND m.neighborhood = ANY(:targets)
              AND m.owner_type NOT IN ('public', 'institutional')
              AND m.owner_name_norm !~* :not_for_sale
              AND {_DEVELOPABLE_USE}
              AND NOT {_JUNK_MEMBER}
        """), {"targets": targets, "not_for_sale": _NOT_FOR_SALE_OWNER})

        # --- tier 1: same owner, adjacent (eps ~1m absorbs digitisation slivers)
        conn.execute(text("""
            CREATE TEMP TABLE _same ON COMMIT DROP AS
            SELECT parcel_id, owner_name_norm, neighborhood, sqft, land_value, geom,
                   'S' || ST_ClusterDBSCAN(geom, eps := 0.00001, minpoints := 2)
                        OVER (PARTITION BY owner_name_norm) AS cid
            FROM _cand
        """))
        conn.execute(text("""
            INSERT INTO assemblages (kind, owner_name_norm, parcel_count, owner_count,
                                     neighborhood, combined_sqft, combined_acres,
                                     combined_land_value, geom, build_key)
            SELECT 'same_owner', owner_name_norm, count(*), 1,
                   mode() WITHIN GROUP (ORDER BY neighborhood),
                   sum(sqft), round((sum(sqft) / :acre)::numeric, 2), sum(land_value),
                   ST_Multi(ST_Union(geom)), 'S|' || owner_name_norm || '|' || cid
            FROM _same
            WHERE cid IS NOT NULL
            GROUP BY owner_name_norm, cid
            HAVING count(*) BETWEEN 2 AND :max_same
               AND sum(sqft) >= :min_sqft
               -- a few big parcels, not many small ones
               AND sum(sqft) / count(*) >= :min_app_sqft
        """), {"acre": _ACRE, "min_sqft": min_acres * _ACRE,
               "max_same": max_same, "min_app_sqft": min_app * _ACRE})

        # --- tier 2: adjacent across DIFFERENT owners. Restricting the candidate
        # set by member size is what stops the county chaining into one blob;
        # parcels already claimed by a same-owner assembly are left out so the
        # two tiers don't double-report the same ground.
        conn.execute(text("""
            CREATE TEMP TABLE _multi ON COMMIT DROP AS
            SELECT c.parcel_id, c.owner_name_norm, c.neighborhood, c.sqft,
                   c.land_value, c.geom,
                   'M' || ST_ClusterDBSCAN(c.geom, eps := 0.00001, minpoints := 2)
                        OVER () AS cid
            FROM _cand c
            WHERE c.sqft >= :min_member
              AND NOT EXISTS (
                  SELECT 1 FROM _same s
                  JOIN assemblages a ON a.build_key = 'S|' || s.owner_name_norm || '|' || s.cid
                  WHERE s.parcel_id = c.parcel_id)
        """), {"min_member": min_member_acres * _ACRE})
        conn.execute(text("""
            INSERT INTO assemblages (kind, owner_name_norm, parcel_count, owner_count,
                                     neighborhood, combined_sqft, combined_acres,
                                     combined_land_value, geom, build_key)
            SELECT 'multi_owner', NULL, count(*), count(DISTINCT owner_name_norm),
                   mode() WITHIN GROUP (ORDER BY neighborhood),
                   sum(sqft), round((sum(sqft) / :acre)::numeric, 2), sum(land_value),
                   ST_Multi(ST_Union(geom)), 'M|' || cid
            FROM _multi
            WHERE cid IS NOT NULL
            GROUP BY cid
            HAVING count(DISTINCT owner_name_norm) >= 2
               AND count(*) <= :max_multi
               AND sum(sqft) >= :min_sqft
               AND sum(sqft) / count(*) >= :min_app_sqft
        """), {"acre": _ACRE, "min_sqft": min_acres * _ACRE, "max_multi": max_multi,
               "min_app_sqft": min_app * _ACRE})

        # link members back (a parcel belongs to at most one assembly)
        conn.execute(text("""
            UPDATE parcel_master m SET assemblage_id = a.id
            FROM _same s
            JOIN assemblages a ON a.build_key = 'S|' || s.owner_name_norm || '|' || s.cid
            WHERE m.parcel_id = s.parcel_id
        """))
        conn.execute(text("""
            UPDATE parcel_master m SET assemblage_id = a.id
            FROM _multi x
            JOIN assemblages a ON a.build_key = 'M|' || x.cid
            WHERE m.parcel_id = x.parcel_id AND m.assemblage_id IS NULL
        """))

        rows = conn.execute(text(
            "SELECT kind, count(*) n, sum(parcel_count) p FROM assemblages GROUP BY kind"
        )).all()
        members = conn.execute(text(
            "SELECT count(*) FROM parcel_master WHERE assemblage_id IS NOT NULL")).scalar()

    summary = {f"{k}": {"clusters": n, "parcels": int(p or 0)} for k, n, p in rows}
    summary["member_parcels"] = members
    summary["min_combined_acres"] = min_acres
    print(f"assembly build: {summary}", flush=True)
    return summary


if __name__ == "__main__":
    build()

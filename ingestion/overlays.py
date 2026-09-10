"""Constraint overlay loader (Section 5.7) — static-cadence polygon layers that
flag entitlement risk on parcels. Two steps, both idempotent:

    python -m ingestion.overlays            # load all layers + apply flags
    python -m ingestion.overlays --layer fema_flood
    python -m ingestion.overlays --apply-only

1. load: page each layer's polygons (ArcGIS geojson, outSR=4326) into
   constraint_zones, replacing that layer's prior rows.
2. apply: spatial-join flags onto parcel_master.
   - flood_flag / slope_flag / historic_flag: ST_Intersects — conservative,
     these are soft score penalties (x0.7 / x0.8 / x0.85).
   - floodway_flag: the hard knockout, so edge-touch isn't enough — the parcel's
     centroid must sit in the floodway, or >30% of its area must overlap.

Layer notes (verified live 2026-07-07):
  - FEMA NFHL layer 28, filtered to SFHA_TF='T' (Special Flood Hazard Area) so
    minimal-hazard Zone X polygons don't flag everything; ~3.7k polys in the
    county bbox, 116 floodway.
  - Hillside + historic ride the same cagisopendata Open_Data FeatureServer as
    the parcel layer (layers 6 and 7).
"""
from __future__ import annotations

import argparse
import json

from ingestion.common import HttpClient, paginate_offset

_HAMILTON_BBOX = "-84.83,39.02,-84.25,39.32"
_OPEN_DATA = "https://services.arcgis.com/JyZag7oO4NteHGiq/arcgis/rest/services/Open_Data/FeatureServer"

LAYERS: dict[str, dict] = {
    "fema_flood": {
        "url": "https://hazards.fema.gov/arcgis/rest/services/public/NFHL/MapServer/28/query",
        "params": {
            "where": "SFHA_TF = 'T'",
            "geometry": _HAMILTON_BBOX, "geometryType": "esriGeometryEnvelope", "inSR": 4326,
            "spatialRel": "esriSpatialRelIntersects",
            "outFields": "FLD_ZONE,ZONE_SUBTY",
        },
        "zone_field": "FLD_ZONE", "subtype_field": "ZONE_SUBTY",
        "page_size": 1000,  # federal server; stay under its 2000 cap
    },
    "hillside": {
        # Cincinnati Hillside Districts. Moved here from the (now-emptied)
        # Open_Data FeatureServer/6 — same 228 polygons, as a CAGIS zoning sublayer.
        "url": ("https://cagisonline.hamilton-co.org/arcgis/rest/services"
                "/Countywide_Layers/Zoning/MapServer/3/query"),
        "params": {"where": "1=1", "outFields": "ZONE_ORD,SLOPE"},
        "zone_field": "ZONE_ORD", "subtype_field": None,
        "page_size": 2000,
    },
    "historic": {
        # Cincinnati Historic Districts. Moved here from the (now-emptied)
        # Open_Data FeatureServer/7 — same 83 polygons, as a CAGIS zoning sublayer.
        "url": ("https://cagisonline.hamilton-co.org/arcgis/rest/services"
                "/Countywide_Layers/Zoning/MapServer/2/query"),
        "params": {"where": "1=1", "outFields": "HD_NAME,TYPE"},
        "zone_field": "HD_NAME", "subtype_field": "TYPE",
        "page_size": 2000,
    },
    "neighborhood": {
        # Cincinnati SNA Boundary 2020 — realtor-facing region names in-city.
        "url": ("https://cagisonline.hamilton-co.org/arcgis/rest/services"
                "/CINC_PLANNING/Cincinnati_Neighborhoods/MapServer/0/query"),
        "params": {"where": "1=1", "outFields": "SNA_NAME"},
        "zone_field": "SNA_NAME", "subtype_field": None,
        "page_size": 1000,
    },
    "jurisdiction": {
        # CAGIS Address Jurisdictions — the authoritative 50 municipalities/
        # townships covering the whole county (JURISFULL like "MADEIRA HAM OH").
        # This is what gives the team's east-side villages/cities real region
        # labels; the SNA layer is Cincinnati-only and zoning JURISDICTION misses
        # the municipalities that run their own zoning.
        "url": ("https://cagisonline.hamilton-co.org/arcgis/rest/services"
                "/COUNTYWIDE/CagisCoreLayers/MapServer/17/query"),
        "params": {"where": "1=1", "outFields": "JURISFULL,JURISTYPE"},
        "zone_field": "JURISFULL", "subtype_field": "JURISTYPE",
        "page_size": 1000,
    },
    "zoning": {
        # CAGIS Countywide Zoning (all jurisdictions merged; 4,051 polys).
        "url": ("https://cagisonline.hamilton-co.org/arcgis/rest/services"
                "/Countywide_Layers/Zoning/MapServer/21/query"),
        "params": {"where": "1=1", "outFields": "ZONING,JURISDICTION"},
        "zone_field": "ZONING", "subtype_field": "JURISDICTION",
        "page_size": 1000,
    },
}


def load_layer(name: str) -> int:
    """Replace one layer's polygons in constraint_zones. Geometry cleaned with
    ST_MakeValid (federal flood polygons are notoriously self-intersecting)."""
    from sqlalchemy import text

    from ingestion.load import _engine

    spec = LAYERS[name]
    http = HttpClient(min_interval_s=0.3, headers={"User-Agent": "infill-platform/0.1"})
    base_params = {**spec["params"], "returnGeometry": "true", "outSR": 4326, "f": "geojson"}

    stmt = text("""
        INSERT INTO constraint_zones (layer, zone, subtype, geom)
        VALUES (:layer, :zone, :subtype,
                ST_Multi(ST_CollectionExtract(ST_MakeValid(
                    ST_SetSRID(ST_GeomFromGeoJSON(:geom), 4326)), 3)))
    """)
    # Accumulate first, then replace — so a moved/down source that returns 0
    # features can't silently WIPE good data (learned the hard way when CAGIS
    # emptied the Open_Data hillside/historic layers and a destructive
    # DELETE-then-INSERT dropped 228+83 real polygons before the insert no-op'd).
    rows: list[dict] = []
    for records in paginate_offset(
        http, spec["url"], base_params,
        offset_key="resultOffset", count_key="resultRecordCount",
        page_size=spec["page_size"],
        records_path=lambda body: body.get("features", []),
    ):
        for feat in records:
            geom = feat.get("geometry")
            if not geom:
                continue
            props = feat.get("properties", {})
            rows.append({
                "layer": name,
                "zone": props.get(spec["zone_field"]) if spec["zone_field"] else None,
                "subtype": props.get(spec["subtype_field"]) if spec["subtype_field"] else None,
                "geom": json.dumps(geom),
            })
        print(f"  {name}: {len(rows)} polygons fetched", flush=True)
    if not rows:
        print(f"  {name}: source returned 0 features — keeping existing data, "
              f"NOT replacing", flush=True)
        return 0
    with _engine().begin() as conn:
        conn.execute(text("DELETE FROM constraint_zones WHERE layer = :l"), {"l": name})
        conn.execute(stmt, rows)
    print(f"  {name}: {len(rows)} polygons loaded", flush=True)
    return len(rows)


def apply_flags() -> dict:
    """Recompute all constraint flags on parcel_master from constraint_zones.
    Full reset-then-set so removals in source layers propagate. Each statement
    commits independently so one bad geometry can't roll back the rest (learned
    the hard way: 64 CAGIS parcels shipped with invalid rings/nested shells,
    since repaired — geometries are ST_MakeValid-repaired at both load paths)."""
    from sqlalchemy import text

    from ingestion.load import _engine

    eng = _engine()
    counts = {}
    # A full parcel re-ingest reintroduces the ~64 CAGIS parcels shipped with
    # invalid rings / nested shells, and an invalid geometry makes GEOS throw
    # mid-UPDATE (crashing the floodway ST_Intersection and everything after it).
    # Repair them up front so every spatial apply below is safe.
    with eng.begin() as conn:
        fixed = conn.execute(text("""
            UPDATE parcel_master
            SET geom = ST_Multi(ST_CollectionExtract(ST_MakeValid(geom), 3)),
                centroid = ST_PointOnSurface(ST_MakeValid(geom))
            WHERE geom IS NOT NULL AND NOT ST_IsValid(geom)
        """)).rowcount
    if fixed:
        print(f"  repaired {fixed} invalid parcel geometries", flush=True)
    with eng.begin() as conn:
        conn.execute(text("""
            UPDATE parcel_master
            SET flood_flag = false, floodway_flag = false,
                slope_flag = false, historic_flag = false
        """))
    for flag, layer in [("flood_flag", "fema_flood"),
                        ("slope_flag", "hillside"),
                        ("historic_flag", "historic")]:
        with eng.begin() as conn:
            res = conn.execute(text(f"""
                UPDATE parcel_master m SET {flag} = true
                WHERE EXISTS (
                    SELECT 1 FROM constraint_zones z
                    WHERE z.layer = '{layer}'
                      AND ST_Intersects(m.geom, z.geom))
            """))
            counts[flag] = res.rowcount
        print(f"  {flag}: {counts[flag]}", flush=True)
    # floodway knockout: centroid inside, or >30% of parcel area overlapping —
    # edge-touch alone must not kill a buildable parcel
    with eng.begin() as conn:
        res = conn.execute(text("""
            UPDATE parcel_master m SET floodway_flag = true
            WHERE EXISTS (
                SELECT 1 FROM constraint_zones z
                WHERE z.layer = 'fema_flood' AND z.subtype = 'FLOODWAY'
                  AND ST_Intersects(m.geom, z.geom)
                  AND (ST_Contains(z.geom, m.centroid)
                       OR ST_Area(ST_Intersection(m.geom, z.geom))
                          > 0.30 * ST_Area(m.geom)))
        """))
        counts["floodway_flag"] = res.rowcount
    print(f"flags applied: {counts}", flush=True)
    return counts


def apply_zoning() -> int:
    """Assign parcel_master.zoning_code from the zoning layer. A parcel takes
    the district containing its centroid (cheap, correct for all but boundary
    slivers); parcels whose centroid falls in no district (edge of county,
    unincorporated gaps) keep NULL."""
    from sqlalchemy import text

    from ingestion.load import _engine

    with _engine().begin() as conn:
        res = conn.execute(text("""
            UPDATE parcel_master m
            SET zoning_code = z.zone
            FROM constraint_zones z
            WHERE z.layer = 'zoning'
              AND ST_Contains(z.geom, m.centroid)
        """))
    print(f"zoning assigned: {res.rowcount} parcels", flush=True)
    return res.rowcount


# "MADEIRA HAM OH" -> "Madeira"; "SYMMES TOWNSHIP HAM OH" -> "Symmes Township".
_CLEAN_JUR = r"initcap(regexp_replace(z.zone, '\s+HAM\s+OH\s*$', '', 'i'))"


def apply_neighborhood() -> dict:
    """Assign the region key every parcel filters by.

    `municipality` = the authoritative CAGIS address jurisdiction (Madeira, Blue
    Ash, Symmes Township, Cincinnati, ...). `neighborhood` = the SNA name inside
    Cincinnati (Hyde Park, Oakley) else the municipality — so the team's east-side
    villages/cities are first-class, filterable regions. Full authoritative
    rebuild (jurisdiction for all, then SNA refines Cincinnati)."""
    from sqlalchemy import text

    from ingestion.load import _engine

    counts = {}
    eng = _engine()
    # 1. authoritative municipality for every parcel (whole-county coverage)
    with eng.begin() as conn:
        res = conn.execute(text(f"""
            UPDATE parcel_master m
            SET municipality = {_CLEAN_JUR}
            FROM constraint_zones z
            WHERE z.layer = 'jurisdiction'
              AND ST_Contains(z.geom, m.centroid)
        """))
        counts["municipality"] = res.rowcount
    # 2. baseline: region = municipality
    with eng.begin() as conn:
        conn.execute(text("UPDATE parcel_master SET neighborhood = municipality"))
    # 3. refine: inside Cincinnati use the finer SNA neighborhood
    with eng.begin() as conn:
        res = conn.execute(text("""
            UPDATE parcel_master m
            SET neighborhood = initcap(z.zone)
            FROM constraint_zones z
            WHERE z.layer = 'neighborhood'
              AND ST_Contains(z.geom, m.centroid)
        """))
        counts["sna"] = res.rowcount
    print(f"region assigned: {counts}", flush=True)
    return counts


def build() -> dict:
    """Reload every constraint overlay (flood, hillside, historic, jurisdiction,
    neighborhood, zoning) from source and re-apply flags + zoning to parcels.

    Wired into the quarterly refresh: constraint data rarely changes, but a full
    parcel re-ingest adds/renumbers parcels, and without this their floodway
    knockout and zoning_code would silently stay NULL. Region labels
    (municipality + SNA) are applied by the separate 'regions' builder
    (apply_neighborhood), which runs after this."""
    counts = {name: load_layer(name) for name in LAYERS}
    apply_flags()
    counts["zoning_assigned"] = apply_zoning()
    print(f"overlays rebuilt: {counts}", flush=True)
    return counts


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--layer", choices=list(LAYERS), help="load just one layer")
    ap.add_argument("--apply-only", action="store_true", help="skip loading; re-apply flags")
    args = ap.parse_args()
    if not args.apply_only:
        for name in ([args.layer] if args.layer else list(LAYERS)):
            total = load_layer(name)
            print(f"{name}: {total} polygons", flush=True)
    if args.layer == "zoning":
        apply_zoning()
        return
    if args.layer in ("neighborhood", "jurisdiction"):
        apply_neighborhood()
        return
    apply_flags()
    apply_zoning()
    apply_neighborhood()


if __name__ == "__main__":
    main()

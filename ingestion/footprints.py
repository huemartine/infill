"""Home size from CAGIS building footprints (Oyler Hines teardown model).

    python -m ingestion.footprints

Pulls residential building footprints (SQFT + stories) from the CAGIS Open Data
FeatureServer as centroid points, then spatial-joins them to parcels and sets
parcel_master.home_sqft = the primary structure's estimated finished area
(footprint SQFT x stories, MAX over a parcel's buildings). A small house on a
valuable lot is a teardown candidate, so this sharpens the land-share signal.
"""
from __future__ import annotations

from ingestion.common import HttpClient

_LAYER = ("https://services.arcgis.com/JyZag7oO4NteHGiq/arcgis/rest/services"
          "/Open_Data_Feature_Collection/FeatureServer/58/query")
_PAGE = 2000  # the layer's server-side maxRecordCount


def build() -> dict:
    from sqlalchemy import text

    from ingestion.load import _engine

    http = HttpClient(min_interval_s=0.2, headers={"User-Agent": "infill-platform/0.1"})
    eng = _engine()
    with eng.begin() as conn:
        conn.execute(text("TRUNCATE building_pts"))

    offset, loaded = 0, 0
    while True:
        js = http.get(_LAYER, params={
            "where": "BLDUSECAT='R' AND SQFT > 0",
            "outFields": "SQFT,EST_STORY", "returnCentroid": "true",
            "outSR": "4326", "resultOffset": offset, "resultRecordCount": _PAGE,
            "f": "json",
        }).json()
        feats = js.get("features", [])
        if not feats:
            break
        rows = []
        for f in feats:
            c = f.get("centroid")
            a = f.get("attributes", {})
            if not c or a.get("SQFT") is None:
                continue
            rows.append({"sq": a["SQFT"], "st": a.get("EST_STORY") or 1,
                         "x": c["x"], "y": c["y"]})
        if rows:
            with eng.begin() as conn:
                conn.execute(text(
                    "INSERT INTO building_pts (sqft, stories, geom) "
                    "VALUES (:sq, :st, ST_SetSRID(ST_MakePoint(:x, :y), 4326))"), rows)
            loaded += len(rows)
        print(f"  footprints loaded: {loaded}", flush=True)
        # page by how many the server actually returned (its cap may be < _PAGE);
        # stop only when a page comes back empty or short of the server maximum
        offset += len(feats)
        if not js.get("exceededTransferLimit") and len(feats) < _PAGE:
            break

    # assign each parcel its primary structure's estimated finished area
    with eng.begin() as conn:
        conn.execute(text("UPDATE parcel_master SET home_sqft = NULL"))
        res = conn.execute(text("""
            UPDATE parcel_master m SET home_sqft = agg.hs
            FROM (
                SELECT m2.parcel_id, max(b.sqft * greatest(b.stories, 1)) AS hs
                FROM parcel_master m2
                JOIN building_pts b ON ST_Contains(m2.geom, b.geom)
                GROUP BY m2.parcel_id
            ) agg
            WHERE m.parcel_id = agg.parcel_id
        """))
        assigned = res.rowcount
    out = {"footprints": loaded, "parcels_with_home_size": assigned}
    print(f"home size assigned: {out}", flush=True)
    return out


if __name__ == "__main__":
    build()

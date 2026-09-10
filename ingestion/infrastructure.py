"""Utility infrastructure: sanitary sewer/septic status and water service.

Answers the question a developer asks before anything else on a fringe lot —
"can I actually build here, or does this need a septic system and a main
extension?" Three public signals:

  on_septic      Hamilton County Public Health's private sewage-treatment-system
                 inventory (HCHD/HCPH_Private_STS). A parcel in this inventory is
                 NOT on sanitary sewer. Joins on PARCELID at ~99.9%.
  water_main_ft  feet to the nearest fire hydrant (hydrants tap water mains, so
                 this proxies "is there a water main in the street").

NOTE: MSD does not publish its sanitary sewer MAIN network publicly (utilities
restrict pipe networks), so there is no true "distance to nearest sewer line"
from open data. The septic inventory is the authoritative public stand-in, and is
arguably better for the real question: it reports the property's actual status
rather than whether a pipe happens to run nearby.

Read the absence of a septic flag carefully: it is strong evidence of sewer in
built-up areas, but not proof in the townships (an unpermitted/unrecorded system
would not appear in the inventory).
"""
from __future__ import annotations

from ingestion.common import HttpClient, paginate_offset

_CAGIS = "https://cagisonline.hamilton-co.org/arcgis/rest/services"
_SEPTIC = f"{_CAGIS}/HCHD/HCPH_Private_STS/MapServer/2/query"
_HYDRANTS = f"{_CAGIS}/Countywide_Layers/Countywide_Hydrants/MapServer/1/query"
_PAGE = 2000          # fallback; the real cap is read from each layer (see below)


def _page_size(query_url: str, http: HttpClient) -> int:
    """The layer's own maxRecordCount. Asking for MORE than a layer allows silently
    truncates: paginate_offset stops as soon as a page comes back short, so a 2000
    request against a 1000-cap layer ends after one page (the hydrants layer caps
at 1000). Read it rather than hard-coding."""
    try:
        meta = http.get(query_url.rsplit("/query", 1)[0], params={"f": "json"}).json()
        return int(meta.get("maxRecordCount") or _PAGE)
    except Exception:
        return _PAGE


def _fetch_points(url: str, label: str) -> list[dict]:
    """Every point feature from an ArcGIS layer, as {'x':..,'y':..} in WGS84."""
    http = HttpClient(min_interval_s=0.2, headers={"User-Agent": "infill-platform/0.1"})
    params = {"where": "1=1", "outFields": "OBJECTID", "returnGeometry": "true",
              "outSR": 4326, "f": "json"}
    pts: list[dict] = []
    for records in paginate_offset(
        http, url, params, offset_key="resultOffset", count_key="resultRecordCount",
        page_size=_page_size(url, http), records_path=lambda b: b.get("features", []),
    ):
        for feat in records:
            g = feat.get("geometry") or {}
            if g.get("x") is not None and g.get("y") is not None:
                pts.append({"x": g["x"], "y": g["y"]})
        print(f"  {label}: {len(pts)} points", flush=True)
    return pts


def _fetch_septic_ids() -> list[str]:
    """Parcel ids in the private-septic inventory, normalized to our 11-char id
    (the health-department feed zero-pads to 12)."""
    http = HttpClient(min_interval_s=0.2, headers={"User-Agent": "infill-platform/0.1"})
    params = {"where": "1=1", "outFields": "PARCELID", "returnGeometry": "false", "f": "json"}
    ids: set[str] = set()
    for records in paginate_offset(
        http, _SEPTIC, params, offset_key="resultOffset", count_key="resultRecordCount",
        page_size=_page_size(_SEPTIC, http), records_path=lambda b: b.get("features", []),
    ):
        for feat in records:
            pid = (feat.get("attributes") or {}).get("PARCELID")
            if pid:
                ids.add(str(pid).lstrip("0"))
        print(f"  septic: {len(ids)} parcels", flush=True)
    return sorted(ids)


def build() -> dict:
    from sqlalchemy import text

    from ingestion.load import _engine

    eng = _engine()
    counts: dict = {}

    # --- 1. septic inventory -> on_septic ---
    ids = _fetch_septic_ids()
    with eng.begin() as conn:
        conn.execute(text("UPDATE parcel_master SET on_septic = false WHERE on_septic IS DISTINCT FROM false"))
        conn.execute(text("CREATE TEMP TABLE _septic (parcel_id text PRIMARY KEY) ON COMMIT DROP"))
        if ids:
            conn.execute(text("INSERT INTO _septic VALUES (:p) ON CONFLICT DO NOTHING"),
                         [{"p": p} for p in ids])
        res = conn.execute(text("""
            UPDATE parcel_master m SET on_septic = true
            FROM _septic s WHERE m.parcel_id = s.parcel_id
        """))
        counts["on_septic"] = res.rowcount
    print(f"septic flagged: {counts['on_septic']} parcels", flush=True)

    # --- 2. point infrastructure -> nearest-distance in feet ---
    for kind, url, column in (("hydrant", _HYDRANTS, "water_main_ft"),):
        pts = _fetch_points(url, kind)
        with eng.begin() as conn:
            conn.execute(text("DELETE FROM infra_points WHERE kind = :k"), {"k": kind})
            for i in range(0, len(pts), 5000):
                conn.execute(text(
                    "INSERT INTO infra_points (kind, geom) "
                    "VALUES (:k, ST_SetSRID(ST_MakePoint(:x, :y), 4326))"),
                    [{"k": kind, **p} for p in pts[i:i + 5000]])
            counts[f"{kind}s"] = len(pts)
        # KNN nearest per parcel: the `<->` operator uses the GIST index, then we
        # measure the real distance on the geography (metres -> feet).
        with eng.begin() as conn:
            res = conn.execute(text(f"""
                UPDATE parcel_master m SET {column} = sub.d
                FROM (
                    SELECT p.parcel_id,
                           round((SELECT ST_Distance(p.centroid::geography, i.geom::geography)
                                  FROM infra_points i WHERE i.kind = :k
                                  ORDER BY i.geom <-> p.centroid LIMIT 1) * 3.28084) AS d
                    FROM parcel_master p WHERE p.centroid IS NOT NULL
                ) sub
                WHERE m.parcel_id = sub.parcel_id AND sub.d IS NOT NULL
            """), {"k": kind})
            counts[column] = res.rowcount
        print(f"{column}: {counts[column]} parcels", flush=True)

    print(f"infrastructure build: {counts}", flush=True)
    return counts


if __name__ == "__main__":
    build()

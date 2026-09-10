"""Entity resolution (Section 6.3) — reconcile every record to a canonical
`parcel_id` (the Hamilton County / Auditor Property Number).

Tiers:
  1. Exact match on the Auditor parcel number after formatting normalization
     (strip dashes/spaces; the county mixes an 11-digit AUDPCLID and a
     zero-padded 12-char PARCELID — canonicalize to the unpadded Auditor form).
  2. Fallback: normalized-address match against parcel_master.
  3. Spatial: point-in-polygon for coordinate-only records (Phase 2+).

Unmatched records go to a quarantine table with a reason code; match rate per
source is tracked in ingest_runs as a data-quality metric.
"""
from __future__ import annotations

import re
from typing import Optional

_NONALNUM = re.compile(r"[^0-9A-Za-z]")


def validate_parcel_ids(df):
    """Tier 1 aftercare: a source that emits parcel_ids directly still gets them
    checked against parcel_master (FK safety; typos and out-of-county parcels
    quarantine instead of crashing the insert). Returns (matched_df, match_rate)."""
    from sqlalchemy import text

    from ingestion.load import _engine

    rows = df[df["parcel_id"].notna()]
    if rows.empty:
        return rows, 0.0
    ids = list(rows["parcel_id"].unique())
    with _engine().connect() as conn:
        known = {r[0] for r in conn.execute(
            text("SELECT parcel_id FROM parcel_master WHERE parcel_id = ANY(:ids)"),
            {"ids": ids})}
    matched = rows[rows["parcel_id"].isin(known)].copy()
    return matched, round(len(matched) / len(df), 4) if len(df) else 0.0


def resolve_points(df):
    """Tier 3: spatial resolution. Attach parcel_id to rows carrying lon/lat by
    point-in-polygon against parcel_master (GIST-indexed). Returns (matched_df,
    match_rate); unmatched rows (points in street ROW, outside county) drop out
    — their count is the data-quality signal recorded in ingest_runs."""
    import pandas as pd
    from sqlalchemy import text

    from ingestion.load import _engine

    pts = df[df["lon"].notna() & df["lat"].notna()].reset_index(drop=True)
    if pts.empty:
        return pts.assign(parcel_id=None), 0.0
    with _engine().begin() as conn:
        conn.execute(text(
            "CREATE TEMP TABLE _resolve_pts (idx int, lon float8, lat float8) ON COMMIT DROP"))
        conn.execute(
            text("INSERT INTO _resolve_pts VALUES (:idx, :lon, :lat)"),
            [{"idx": i, "lon": float(r.lon), "lat": float(r.lat)}
             for i, r in pts.iterrows()])
        hits = conn.execute(text("""
            SELECT p.idx, m.parcel_id
            FROM _resolve_pts p
            JOIN parcel_master m
              ON ST_Contains(m.geom, ST_SetSRID(ST_MakePoint(p.lon, p.lat), 4326))
        """)).mappings().all()
    by_idx = {h["idx"]: h["parcel_id"] for h in hits}
    pts["parcel_id"] = pd.Series(by_idx).reindex(pts.index)
    matched = pts[pts["parcel_id"].notna()].copy()
    return matched, round(len(matched) / len(df), 4) if len(df) else 0.0


def canonical_parcel_id(value) -> Optional[str]:
    """Canonical Auditor Property Number: strip separators, drop a single leading
    zero from the zero-padded 12-char CAGIS form so it matches the 11-char
    AUDPCLID. Idempotent."""
    if value is None:
        return None
    s = _NONALNUM.sub("", str(value)).upper()
    if not s:
        return None
    # CAGIS PARCELID is often the AUDPCLID left-padded to 12 chars with a zero.
    if len(s) == 12 and s.startswith("0"):
        s = s[1:]
    return s or None

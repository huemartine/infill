"""Load layer (Section 6.2). Two responsibilities:

  1. Archive raw extract batches verbatim (timestamped) to the immutable raw store
     — S3/MinIO when configured, else a local filesystem fallback for dev. Every
     score must trace back to a raw record, so this happens before normalization.
  2. Upsert normalized parcel rows into parcel_master (PostGIS) keyed on parcel_id,
     and append derived signals to parcel_signals.

DB writes use SQLAlchemy + a PostGIS upsert (ON CONFLICT). Geometry arrives as
GeoJSON and is loaded via ST_GeomFromGeoJSON / ST_Multi. Import is lazy so the
pure-Python pipeline (extract/normalize/score) runs without a database present.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import pandas as pd

from config import settings

# columns written to parcel_master (derived + source)
_MASTER_COLS = [
    "parcel_id", "address", "owner_name_raw", "owner_name_norm", "owner_mailing_addr",
    "is_absentee", "owner_type", "land_value", "improvement_value", "total_value",
    "il_ratio", "land_use_code", "num_units", "last_sale_date", "last_sale_price",
    "has_structure", "homestead",
]


def archive_raw(source_id: str, page: int, records: list[dict]) -> str:
    """Write a raw batch to the archive and return its path/URI. Local-fs fallback
    keeps dev self-contained; swap to boto3 put_object when S3_ENDPOINT is set."""
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    key = f"{source_id}/dt={ts[:8]}/{source_id}_{ts}_p{page:04d}.json"
    body = json.dumps({"source": source_id, "extracted_at": ts, "records": records})

    if settings.s3_endpoint:
        import boto3  # lazy
        s3 = boto3.client(
            "s3", endpoint_url=settings.s3_endpoint,
            aws_access_key_id=settings.s3_access_key,
            aws_secret_access_key=settings.s3_secret_key)
        s3.put_object(Bucket=settings.s3_bucket_raw, Key=key, Body=body.encode())
        return f"s3://{settings.s3_bucket_raw}/{key}"

    path = Path(settings.raw_archive_dir) / key
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body)
    return str(path)


def _engine():
    from sqlalchemy import create_engine  # lazy: DB optional for offline pipeline
    return create_engine(settings.database_url, future=True)


def upsert_master(df: pd.DataFrame, geojson_by_parcel: Optional[dict] = None) -> int:
    """Upsert parcel_master rows. Geometry (GeoJSON) loaded via ST_GeomFromGeoJSON.
    Returns rows written."""
    from sqlalchemy import text
    if df.empty:
        return 0
    geojson_by_parcel = geojson_by_parcel or {}
    stmt = text(f"""
        INSERT INTO parcel_master (
            {", ".join(_MASTER_COLS)}, geom, centroid, area_sqft, updated_at
        ) VALUES (
            {", ".join(":" + c for c in _MASTER_COLS)},
            ST_Multi(ST_SetSRID(ST_GeomFromGeoJSON(:geom), 4326)),
            ST_Centroid(ST_SetSRID(ST_GeomFromGeoJSON(:geom), 4326)),
            :area_sqft, now()
        )
        ON CONFLICT (parcel_id) DO UPDATE SET
            {", ".join(f"{c} = EXCLUDED.{c}" for c in _MASTER_COLS if c != "parcel_id")},
            geom = EXCLUDED.geom, centroid = EXCLUDED.centroid,
            area_sqft = EXCLUDED.area_sqft, updated_at = now()
    """)
    rows = []
    for _, r in df.iterrows():
        params = {c: _py(r.get(c)) for c in _MASTER_COLS}
        gj = geojson_by_parcel.get(r["parcel_id"]) or r.get("geometry")
        params["geom"] = json.dumps(gj) if gj else None
        params["area_sqft"] = _py(r.get("area_sqft")) or _acres_sqft(r.get("acreage"))
        rows.append(params)
    # a page can carry duplicate parcel_ids (condo stacks); ON CONFLICT can't see
    # rows inside one executemany, so dedupe keeping the last occurrence
    rows = list({p["parcel_id"]: p for p in rows}.values())
    with _engine().begin() as conn:
        conn.execute(stmt, rows)  # executemany: one round trip per batch
    return len(rows)


def insert_signals(df: pd.DataFrame) -> int:
    from sqlalchemy import text
    if df is None or df.empty:
        return 0
    # Standing flags upsert on (parcel, type, source) — see 002_signal_flag_dedupe;
    # dated events (other status values) append normally.
    stmt = text("""
        INSERT INTO parcel_signals (parcel_id, signal_type, event_date, status, severity, source)
        VALUES (:parcel_id, :signal_type, :event_date, :status, :severity, :source)
        ON CONFLICT (parcel_id, signal_type, source) WHERE status = 'flag'
        DO UPDATE SET event_date = EXCLUDED.event_date,
                      severity = EXCLUDED.severity,
                      ingested_at = now()
    """)
    rows = [{k: _py(r.get(k)) for k in
             ["parcel_id", "signal_type", "event_date", "status", "severity", "source"]}
            for _, r in df.iterrows()]
    # dedupe flags within the batch (condo stacks repeat parcel_ids) so the
    # upsert never hits the same row twice in one statement
    rows = list({(p["parcel_id"], p["signal_type"], p["source"], p["status"]): p
                 for p in rows}.values())
    with _engine().begin() as conn:
        conn.execute(stmt, rows)
    return len(rows)


def reconcile_standing_flags(source: str, signal_type: str,
                             observed_parcel_ids: set[str]) -> int:
    """Retire flags absent from a successfully completed source snapshot.

    Standing facts such as CAGIS tax delinquency describe *current* state.  A
    regular upsert refreshes parcels still present in the feed, but cannot
    otherwise distinguish a paid account from a source that was never checked.
    The caller must therefore invoke this only after consuming the whole source.
    """
    from sqlalchemy import text

    # `unnest` keeps the comparison parameterized and avoids interpolating a
    # county-scale list of parcel ids into SQL.  An empty observed set is valid:
    # it retires every prior flag of this type for the source.
    stmt = text("""
        DELETE FROM parcel_signals s
        WHERE s.source = :source
          AND s.signal_type = :signal_type
          AND s.status = 'flag'
          AND NOT EXISTS (
              SELECT 1
              FROM unnest(CAST(:parcel_ids AS text[])) AS observed(parcel_id)
              WHERE observed.parcel_id = s.parcel_id
          )
    """)
    with _engine().begin() as conn:
        result = conn.execute(stmt, {
            "source": source,
            "signal_type": signal_type,
            "parcel_ids": sorted(observed_parcel_ids),
        })
    return result.rowcount


def insert_event_signals(df: pd.DataFrame) -> int:
    """Dated events keyed by the source system's natural key (raw_ref). Upserts
    on (source, raw_ref) — see 003 — so re-ingests refresh status/severity
    instead of duplicating, and enforcement-stage changes flow through."""
    from sqlalchemy import text
    if df is None or df.empty:
        return 0
    stmt = text("""
        INSERT INTO parcel_signals (parcel_id, signal_type, event_date, status,
                                    severity, source, raw_ref)
        VALUES (:parcel_id, :signal_type, :event_date, :status, :severity, :source, :raw_ref)
        ON CONFLICT (source, raw_ref) WHERE raw_ref IS NOT NULL
        DO UPDATE SET status = EXCLUDED.status,
                      severity = EXCLUDED.severity,
                      event_date = EXCLUDED.event_date,
                      ingested_at = now()
    """)
    cols = ["parcel_id", "signal_type", "event_date", "status", "severity", "source", "raw_ref"]
    rows = [{k: _py(r.get(k)) for k in cols} for _, r in df.iterrows()]
    rows = list({(p["source"], p["raw_ref"]): p for p in rows}.values())  # in-batch dedupe
    with _engine().begin() as conn:
        conn.execute(stmt, rows)
    return len(rows)


def _py(v):
    """Coerce numpy/pandas scalars + NaN to plain Python for the DB driver."""
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    if hasattr(v, "item"):
        return v.item()
    return v


def _acres_sqft(acres):
    try:
        return round(float(acres) * 43_560, 2)
    except (TypeError, ValueError):
        return None

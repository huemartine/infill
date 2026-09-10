"""Ingestion runner (Section 6). Drives one extractor end to end:
discover -> for each resource: extract pages -> archive raw -> normalize ->
upsert parcel_master + append derived signals, recording an ingest_runs row for
observability (row count, match rate, status, s3 path).

Usage:
    python -m ingestion.run --source cagis [--limit-pages N] [--dry-run]

--dry-run skips all DB writes (still archives raw) and prints a summary — handy
before a database is up.
"""
from __future__ import annotations

import argparse
from datetime import datetime, time, timedelta, timezone

import pandas as pd

from ingestion.extractors.cagis import CagisExtractor, derive_signals
from ingestion.extractors.sheriff import SheriffSaleExtractor
from ingestion.extractors.socrata import SocrataCodeViolationsExtractor

EXTRACTORS = {
    "cagis": CagisExtractor,
    "socrata_code_violations": SocrataCodeViolationsExtractor,
    "sheriff_sale": SheriffSaleExtractor,
}

_WATERMARK_OVERLAP_DAYS = 7  # harmless: event upserts dedupe on (source, raw_ref)


def run(source: str, limit_pages: int | None = None, dry_run: bool = False) -> dict:
    if source not in EXTRACTORS:
        raise SystemExit(f"unknown source {source!r}; known: {list(EXTRACTORS)}")
    extractor = EXTRACTORS[source]()
    target = getattr(extractor, "target", "master")
    started = datetime.now(timezone.utc)
    since = _event_watermark(source) if (target == "signals" and not dry_run) else None

    # Stream page by page: archive -> normalize -> resolve -> write, so a
    # full-county run never holds more than one page in memory.
    n_raw = n_resolved = n_signals = written = 0
    # A standing flag must disappear when it no longer appears in a completed
    # CAGIS snapshot (for example, after delinquent taxes are paid).  Retain the
    # observed ids until the end so a transport failure can never clear flags.
    observed_flags: dict[str, set[str]] = {}
    last_s3 = None
    for resource in extractor.discover():
        for batch in extractor.extract(resource, since=since):
            from ingestion.load import archive_raw
            last_s3 = archive_raw(source, batch.page, batch.records)
            batch.s3_path = last_s3
            df = extractor.normalize(batch)

            if target == "master":
                n_raw += len(batch.records)
                sig = derive_signals(df) if source == "cagis" else pd.DataFrame()
                n_resolved += len(df)
                n_signals += len(sig)
                if source == "cagis" and not sig.empty:
                    for signal_type, rows in sig.groupby("signal_type"):
                        observed_flags.setdefault(signal_type, set()).update(
                            rows["parcel_id"].dropna().astype(str))
                if not dry_run and not df.empty:
                    from ingestion.load import insert_signals, upsert_master
                    written += upsert_master(df)
                    insert_signals(sig)
            else:  # signals: parcel resolution (tier 1 or spatial), then event upsert
                # raw = normalized items, not transport pages (scrapes batch by page)
                n_raw += len(df)
                if not dry_run and not df.empty:
                    from ingestion.load import insert_event_signals
                    from ingestion.resolve import resolve_points, validate_parcel_ids
                    if getattr(extractor, "resolves_parcels", False):
                        resolved, _ = validate_parcel_ids(df)
                    else:
                        resolved, _ = resolve_points(df)
                    n_resolved += len(resolved)
                    n_signals += len(resolved)
                    written += insert_event_signals(resolved)
                else:
                    n_resolved += len(df)

            print(f"  page {batch.page}: {len(df)} records "
                  f"(raw {n_raw}, resolved {n_resolved})", flush=True)
            if limit_pages and batch.page + 1 >= limit_pages:
                break

    retired_signals: dict[str, int] = {}
    # Never reconcile a dry run or a page-limited diagnostic run: neither is a
    # complete assertion of the source's current state.
    if source == "cagis" and not dry_run and limit_pages is None and n_raw:
        from ingestion.load import reconcile_standing_flags
        for signal_type in ("tax_delinquent", "foreclosure_filing"):
            retired_signals[signal_type] = reconcile_standing_flags(
                source, signal_type, observed_flags.get(signal_type, set()))

    match_rate = round(n_resolved / n_raw, 4) if n_raw else 0.0
    if not dry_run and n_raw:
        _record_run(source, started, n_resolved, match_rate, "success", last_s3)

    summary = {
        "source": source, "target": target, "raw": n_raw, "resolved": n_resolved,
        "signals": n_signals, "match_rate": match_rate, "written": written,
        "retired_signals": retired_signals,
        "since": since.isoformat() if since else None,
        "raw_archive": last_s3, "dry_run": dry_run,
    }
    print(summary)
    return summary


def _event_watermark(source: str) -> datetime | None:
    """Incremental extraction point: newest stored event for this source, minus
    an overlap buffer. None (no rows yet) => extractor's initial lookback."""
    from sqlalchemy import text

    from ingestion.load import _engine
    with _engine().connect() as conn:
        latest = conn.execute(
            text("SELECT max(event_date) FROM parcel_signals WHERE source = :s"),
            {"s": source}).scalar()
    if latest is None:
        return None
    return datetime.combine(latest - timedelta(days=_WATERMARK_OVERLAP_DAYS),
                            time.min, tzinfo=timezone.utc)


def _record_run(source, started, rows, match_rate, status, s3_path):
    from sqlalchemy import text

    from ingestion.load import _engine
    with _engine().begin() as conn:
        conn.execute(text("""
            INSERT INTO ingest_runs (source, started_at, finished_at, row_count,
                match_rate, status, s3_path)
            VALUES (:s, :b, now(), :n, :m, :st, :p)
        """), {"s": source, "b": started, "n": rows, "m": match_rate,
               "st": status, "p": s3_path})


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", required=True)
    ap.add_argument("--limit-pages", type=int, default=None)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    run(args.source, args.limit_pages, args.dry_run)


if __name__ == "__main__":
    main()

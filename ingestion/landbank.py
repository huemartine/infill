"""Land-bank inventory signals (Section 5.4).

    python -m ingestion.landbank

Source of truth is the county itself: parcels whose Auditor owner is the
Hamilton County Land Reutilization Corporation (the Port's land bank) or a
sibling reutilization entity. Those parcels are *acquirable* — clearing title
and reselling for redevelopment is the land bank's statutory purpose — so they
carry a standing `landbank_inventory` signal, which:

  - waives the `not_acquirable` knockout they'd otherwise take as public land
    (scoring/run.py), letting them compete on their real merits, and
  - contributes to the distress component at the config's landbank weight
    (no recency decay: standing inventory, not a dated event).

Deliberately NOT scraped from the Port's public listing portal: that site is a
third-party Tolemi SPA whose data API needs session context and could change
without notice. Listing price / program / application deadline are available
there via its own "Export spreadsheet" button; `import_listing_csv` ingests
that export when someone drops the file in, enriching these signals. The
county ownership signal stands on its own without it.
"""
from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path
from typing import Optional

# Auditor owner-name patterns for land-bank / reutilization entities. The Port
# authority itself holds development sites alongside the land bank proper —
# verified: 5322 Whetsel Ave appears on the Port's public listing site under
# "PORT OF GREATER CINCINNATI DEVELOPMENT AUTHORITY", not the HCLRC name.
_OWNER_PATTERNS = (
    "%LAND REUTILIZATION%",
    "%LAND BANK%",
    "%PORT OF GREATER CINCINNATI%",
)


def build() -> dict:
    """Emit/refresh a standing landbank_inventory signal for every county
    parcel owned by a land-reutilization entity. Idempotent (flag upsert)."""
    from sqlalchemy import text

    from ingestion.load import _engine

    where = " OR ".join(f"upper(owner_name_raw) LIKE '{p}'" for p in _OWNER_PATTERNS)
    stmt = text(f"""
        INSERT INTO parcel_signals (parcel_id, signal_type, event_date, status, severity, source)
        SELECT parcel_id, 'landbank_inventory', CURRENT_DATE, 'flag', NULL, 'county_owner'
        FROM parcel_master
        WHERE {where}
        ON CONFLICT (parcel_id, signal_type, source) WHERE status = 'flag'
        DO UPDATE SET event_date = EXCLUDED.event_date, ingested_at = now()
    """)
    with _engine().begin() as conn:
        res = conn.execute(stmt)
        stale = conn.execute(text(f"""
            DELETE FROM parcel_signals s
            WHERE s.signal_type = 'landbank_inventory' AND s.source = 'county_owner'
              AND NOT EXISTS (
                SELECT 1 FROM parcel_master m
                WHERE m.parcel_id = s.parcel_id AND ({where}))
        """))
    summary = {"landbank_parcels": res.rowcount, "delisted": stale.rowcount}
    print(f"landbank signals: {summary}", flush=True)
    return summary


def import_listing_csv(path: str | Path) -> dict:
    """Enrich land-bank signals with listing detail from the Port's own
    "Export spreadsheet" download (hamiltoncountylandbank.org/available-properties).
    Matches on parcel id; sets severity = asking price and status = program name
    so the dossier shows what the lot actually costs. Column names are matched
    loosely because the vendor's export headers drift."""
    import pandas as pd
    from sqlalchemy import text

    from ingestion.load import _engine
    from ingestion.resolve import canonical_parcel_id

    df = pd.read_csv(path)
    cols = {c.lower().strip(): c for c in df.columns}

    def pick(*names) -> Optional[str]:
        for n in names:
            for lc, orig in cols.items():
                if n in lc:
                    return orig
        return None

    c_pid = pick("parcel", "pid")
    c_price = pick("price", "cost")
    c_prog = pick("program", "type")
    if not c_pid:
        raise SystemExit(f"no parcel-id column found in {list(df.columns)[:8]}")

    rows = []
    for _, r in df.iterrows():
        pid = canonical_parcel_id(r.get(c_pid))
        if not pid:
            continue
        rows.append({
            "pid": pid,
            "price": _money(r.get(c_price)) if c_price else None,
            "prog": str(r.get(c_prog))[:60] if c_prog and pd.notna(r.get(c_prog)) else "listed",
        })
    stmt = text("""
        INSERT INTO parcel_signals (parcel_id, signal_type, event_date, status, severity, source)
        SELECT :pid, 'landbank_inventory', :d, :prog, :price, 'county_owner'
        WHERE EXISTS (SELECT 1 FROM parcel_master WHERE parcel_id = :pid)
        ON CONFLICT (parcel_id, signal_type, source) WHERE status = 'flag'
        DO UPDATE SET severity = EXCLUDED.severity, status = EXCLUDED.status,
                      ingested_at = now()
    """)
    n = 0
    with _engine().begin() as conn:
        for r in rows:
            n += conn.execute(stmt, {"pid": r["pid"], "price": r["price"],
                                     "prog": r["prog"], "d": date.today()}).rowcount
    print(f"landbank listings enriched: {n} of {len(rows)}", flush=True)
    return {"listings": len(rows), "matched": n}


def _money(v) -> Optional[float]:
    try:
        return float(str(v).replace("$", "").replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--listing-csv", help="optional Port export to enrich with price/program")
    args = ap.parse_args()
    build()
    if args.listing_csv:
        import_listing_csv(args.listing_csv)


if __name__ == "__main__":
    main()

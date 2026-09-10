"""Data-freshness monitor.

    python -m orchestration.freshness          # human-readable table, exit 1 if stale

Agents lose trust the moment they act on a sheriff-sale date that already
passed, so staleness has to be *visible* rather than discovered. Each feed gets
a budget generous enough to absorb one missed run; anything past it is stale.
Reads `ingest_runs`, which every extractor and every pipelines.py builder writes.
"""
from __future__ import annotations

from datetime import datetime, timezone

# source -> (budget_hours, label). Budget = cadence x2 + slack, so a single
# missed run warns rather than alarms.
BUDGETS: dict[str, tuple[float, str]] = {
    "socrata_code_violations": (60, "code violations (daily)"),
    "permits": (60, "building permits (daily)"),
    "sheriff_sale": (14 * 24, "sheriff sales (weekly)"),
    "landbank": (14 * 24, "land bank (weekly)"),
    "cagis": (200 * 24, "parcels / CAMA (quarterly)"),
    "refresh:daily": (60, "daily pipeline"),
    "refresh:weekly": (14 * 24, "weekly pipeline"),
}


def check() -> list[dict]:
    """One row per monitored source, worst (most stale) first."""
    from sqlalchemy import text

    from ingestion.load import _engine

    with _engine().connect() as conn:
        rows = conn.execute(text("""
            SELECT source, max(coalesce(finished_at, started_at)) AS last_success
            FROM ingest_runs
            WHERE status = 'success'
            GROUP BY source
        """)).mappings().all()
    last = {r["source"]: r["last_success"] for r in rows}

    now = datetime.now(timezone.utc)
    out = []
    for source, (budget, label) in BUDGETS.items():
        ts = last.get(source)
        age = (now - ts).total_seconds() / 3600 if ts else None
        out.append({
            "source": source,
            "label": label,
            "last_success": ts.isoformat() if ts else None,
            "age_hours": round(age, 1) if age is not None else None,
            "budget_hours": budget,
            # never run at all is stale: the feed is not wired up
            "stale": age is None or age > budget,
        })
    return sorted(out, key=lambda r: -(r["age_hours"] or 1e9))


def summary() -> dict:
    rows = check()
    stale = [r["source"] for r in rows if r["stale"]]
    return {"status": "stale" if stale else "ok", "stale": stale, "sources": rows}


def main() -> None:
    import sys

    s = summary()
    print(f"data freshness: {s['status'].upper()}")
    for r in s["sources"]:
        age = "never" if r["age_hours"] is None else f"{r['age_hours'] / 24:.1f}d"
        mark = "STALE" if r["stale"] else "ok   "
        print(f"  {mark} {r['label']:<30} last {age:>7}  "
              f"(budget {r['budget_hours'] / 24:.0f}d)")
    sys.exit(1 if s["stale"] else 0)


if __name__ == "__main__":
    main()

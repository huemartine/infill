"""Precision@N — the number the whole audit exists to move.

    python -m scoring.leadaudit.precision

For the top-N score-ranked leads (countywide and per neighborhood), report what
share are labelled real / fake / maybe / unknown. 'unknown' is the honest state
of an un-reviewed lead: a high unknown share at the top means we simply have not
verified our own board yet — which is exactly what Phase 1 review drives down.

Definition of done (docs/lead-audit-plan.md): >=90% of the top 25 in any
neighborhood judged real, >=85% of the top 50 countywide. We never ship a scoring
change that lowers these.
"""
from __future__ import annotations

from scoring.leadaudit.labels import EFFECTIVE_LABEL_SQL


def _bucket(rows: list[dict]) -> dict:
    n = len(rows)
    c = {"n": n, "real": 0, "fake": 0, "maybe": 0, "unknown": 0}
    for r in rows:
        c[r["label"] or "unknown"] += 1
    # precision counts only reviewed leads: of the ones we've judged, what share
    # are real? (unknown excluded from the denominator)
    judged = c["real"] + c["fake"] + c["maybe"]
    c["precision"] = round(100.0 * c["real"] / judged, 1) if judged else None
    c["reviewed_pct"] = round(100.0 * judged / n, 1) if n else 0.0
    return c


def top_leads(conn, text, n: int, neighborhood: str | None = None) -> list[dict]:
    where = "s.opportunity_score > 0"
    params: dict = {"n": n}
    if neighborhood is not None:
        where += " AND m.neighborhood = :nb"
        params["nb"] = neighborhood
    return conn.execute(text(f"""
        WITH eff AS ({EFFECTIVE_LABEL_SQL})
        SELECT s.parcel_id, s.opportunity_score, eff.label, eff.reason_code
        FROM parcel_scores s
        JOIN parcel_master m USING (parcel_id)
        LEFT JOIN eff ON eff.parcel_id = s.parcel_id
        WHERE {where}
        ORDER BY s.opportunity_score DESC
        LIMIT :n
    """), params).mappings().all()


def compute(nb_limit: int = 20) -> dict:
    """The precision numbers as data (no printing) — also backs GET /audit/precision."""
    from sqlalchemy import text

    from ingestion.load import _engine

    eng = _engine()
    with eng.connect() as conn:
        overall = {n: _bucket(top_leads(conn, text, n)) for n in (25, 50, 100)}
        nbs = [r[0] for r in conn.execute(text("""
            SELECT m.neighborhood
            FROM parcel_scores s JOIN parcel_master m USING (parcel_id)
            WHERE s.opportunity_score > 0 AND m.neighborhood IS NOT NULL
            GROUP BY 1 HAVING count(*) >= 10 ORDER BY count(*) DESC LIMIT :k
        """), {"k": nb_limit}).all()]
        per_nb = {nb: _bucket(top_leads(conn, text, 25, nb)) for nb in nbs}
        total_labels = conn.execute(text("SELECT count(*) FROM lead_labels")).scalar()
    return {"overall": overall, "per_neighborhood": per_nb, "total_labels": total_labels}


def report() -> dict:
    r = compute()
    overall, per_nb, total_labels = r["overall"], r["per_neighborhood"], r["total_labels"]
    print("=" * 70)
    print("PRECISION@N  (labelled share of the top score-ranked leads)")
    print(f"  ground-truth labels on file: {total_labels:,}")
    print("-" * 70)
    for n, c in overall.items():
        print(f"  countywide top {n:>3}: "
              f"real {c['real']:>2}  fake {c['fake']:>2}  maybe {c['maybe']:>2}  "
              f"unknown {c['unknown']:>3}   "
              f"precision {c['precision'] if c['precision'] is not None else 'n/a':>5}"
              f"   ({c['reviewed_pct']}% reviewed)")
    print("-" * 70)
    print("  per-neighborhood top 25 (reviewed only shown as precision):")
    for nb, c in per_nb.items():
        flag = "  <-- below 90% target" if (c["precision"] is not None and c["precision"] < 90) else ""
        print(f"    {nb:<20} reviewed {c['reviewed_pct']:>5}%  "
              f"precision {c['precision'] if c['precision'] is not None else '  n/a':>5}{flag}")
    print("=" * 70)
    return r


if __name__ == "__main__":
    report()

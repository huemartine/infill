"""Seed lead_labels from everything the diligence loop has already established, so
the audit starts with a real regression set instead of a blank slate.

    python -m scoring.leadaudit.seed_from_history

Two sources, both idempotent (re-running replaces only what it owns):
  1. Every parcel whose stored scoring constraints mark it a known fake class
     (not_acquirable, condo_unit, commercial_land, ...) -> source='history'.
  2. The first real agent's verbatim Oakley calls (4 fakes + 3 reals) -> a
     human-verified 'review' source that outranks the heuristic seed.
"""
from __future__ import annotations

from scoring.leadaudit.labels import CONSTRAINT_FAKE_REASONS

# The first real agent's Oakley top-10 review (2026-07-27), by parcel_id.
# These are human-verified anchors — the highest-value labels we have.
AGENT_OAKLEY = {
    "fake": {
        "05000010021": ("commercial_land", "Deeper Roots coffee shop"),
        "05000010022": ("commercial_land", "Deeper Roots parking lot"),
        "05100030093": ("occupied_commercial", "right behind Petsmart - would not work"),
        "05100030092": ("occupied_commercial", "right behind Petsmart - would not work"),
        "05100090049": ("commercial_land", "where the food truck sits, commercial strip"),
    },
    "real": {
        "05100020009": (None, "Good find - next to new-construction townhomes"),
        "04000040253": (None, "interesting L-shaped lot off Andrew"),
        "03900040199": (None, "could work for less expensive new construction (Mt Vernon)"),
    },
}
_AGENT_TAG = "seed:agent-oakley-r1"


def run() -> dict:
    from sqlalchemy import text

    from ingestion.load import _engine

    eng = _engine()
    with eng.begin() as conn:
        # ---- wipe only what this seed owns, so it is safe to re-run ----
        conn.execute(text("DELETE FROM lead_labels WHERE source = 'history'"))
        conn.execute(text("DELETE FROM lead_labels WHERE reviewer = :t"), {"t": _AGENT_TAG})

        # ---- 1. class sweep from stored scoring constraints ----
        # one row per parcel, tagged with its most-specific fake reason
        order = list(CONSTRAINT_FAKE_REASONS)
        n_class = conn.execute(text("""
            INSERT INTO lead_labels (parcel_id, label, reason_code, source, reviewer, notes)
            SELECT DISTINCT ON (s.parcel_id) s.parcel_id, 'fake', c.code, 'history',
                   'system', 'auto-seeded from active scoring constraint'
            FROM parcel_scores s,
              LATERAL jsonb_array_elements_text(s.constraints->'active') AS c(code)
            WHERE c.code = ANY(:codes)
            ORDER BY s.parcel_id, array_position(:codes, c.code)
        """), {"codes": order}).rowcount

        # ---- 2. agent-verified Oakley anchors (outrank the heuristic seed) ----
        n_agent = 0
        for verdict, parcels in AGENT_OAKLEY.items():
            for pid, (reason, note) in parcels.items():
                res = conn.execute(text("""
                    INSERT INTO lead_labels (parcel_id, label, reason_code, source, reviewer, notes)
                    SELECT :p, :v, :r, 'review', :t, :n
                    WHERE EXISTS (SELECT 1 FROM parcel_master WHERE parcel_id = :p)
                """), {"p": pid, "v": verdict, "r": reason, "t": _AGENT_TAG, "n": note})
                n_agent += res.rowcount

        by_reason = dict(conn.execute(text("""
            SELECT reason_code, count(*) FROM lead_labels
            WHERE source = 'history' GROUP BY 1 ORDER BY 2 DESC
        """)).all())

    out = {"history_labels": n_class, "agent_labels": n_agent, "by_reason": by_reason}
    print(f"seeded lead_labels: {out}", flush=True)
    return out


if __name__ == "__main__":
    run()

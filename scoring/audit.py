"""Data-plausibility auditor. Runs the diligence that repeatedly surfaced false
positives during manual review, now as standing checks over every lead:

    python -m scoring.audit

Each check emits an advisory flag (it never changes the score) so a lead's data
provenance is visible. The checks encode what hand-inspection taught us:

  bulk_sale            the recorded last_sale_price is a portfolio price - the
                       same owner/date/price stamped on multiple parcels, or a
                       price many times the land value. The "sale" isn't this
                       parcel's, so demand/comps read off it are suspect.
  no_market_sale       no arms-length sale on record (price null or below the
                       market floor); the neighborhood demand read is inferred,
                       not observed for this parcel.
  portfolio_owner      owner holds a large number of parcels; signals and sale
                       history may be portfolio-level, worth a manual look.
  incomplete_valuation land or improvement value missing in the county record.

Only parcels that actually surface as leads (opportunity_score above the floor)
are audited, keeping the pass cheap and the output relevant.
"""
from __future__ import annotations

# thresholds (kept here, not in scoring.yaml: these govern advisory data checks,
# not the score itself)
_LEAD_FLOOR = 40.0            # audit anything that could plausibly surface
_MARKET_PRICE_FLOOR = 5000    # below this a "sale" is a non-arms-length transfer
_BULK_RATIO = 8              # sale price this many x land value => suspect
_BULK_ABS = 250_000          # ...and at least this many dollars
_PORTFOLIO_PARCELS = 25      # owner holds >= this many parcels


def build() -> dict:
    from sqlalchemy import text

    from ingestion.load import _engine

    checks = {
        # portfolio price: same owner+date+price on >=2 parcels, OR price >> land value
        "bulk_sale": ("caution", """
            INSERT INTO parcel_audit_flag (parcel_id, code, severity, detail)
            SELECT m.parcel_id, 'bulk_sale', 'caution',
                   CASE WHEN grp.n >= 2
                        THEN 'recorded sale $' || round(m.last_sale_price)::text
                             || ' spans ' || grp.n || ' parcels (portfolio deal)'
                        ELSE 'sale $' || round(m.last_sale_price)::text
                             || ' vs $' || round(coalesce(m.land_value,0))::text || ' land value'
                   END
            FROM parcel_master m
            JOIN parcel_scores s USING (parcel_id)
            LEFT JOIN (
                SELECT owner_name_norm, last_sale_date, last_sale_price, count(*) AS n
                FROM parcel_master
                WHERE last_sale_price > :floor AND last_sale_date IS NOT NULL
                GROUP BY 1,2,3 HAVING count(*) >= 2
            ) grp
              ON grp.owner_name_norm = m.owner_name_norm
             AND grp.last_sale_date = m.last_sale_date
             AND grp.last_sale_price = m.last_sale_price
            WHERE s.opportunity_score >= :lead
              AND m.last_sale_price > :floor
              AND (grp.n >= 2
                   OR (m.last_sale_price > :ratio * greatest(m.land_value, 1)
                       AND m.last_sale_price > :absv))
        """),
        "no_market_sale": ("review", """
            INSERT INTO parcel_audit_flag (parcel_id, code, severity, detail)
            SELECT m.parcel_id, 'no_market_sale', 'review',
                   'no arms-length sale on record (demand inferred)'
            FROM parcel_master m JOIN parcel_scores s USING (parcel_id)
            WHERE s.opportunity_score >= :lead
              AND (m.last_sale_price IS NULL OR m.last_sale_price < :floor)
        """),
        "portfolio_owner": ("review", """
            INSERT INTO parcel_audit_flag (parcel_id, code, severity, detail)
            SELECT m.parcel_id, 'portfolio_owner', 'review',
                   'owner holds ' || o.n || ' parcels countywide'
            FROM parcel_master m JOIN parcel_scores s USING (parcel_id)
            JOIN (SELECT owner_name_norm, count(*) AS n FROM parcel_master
                  WHERE owner_name_norm IS NOT NULL GROUP BY 1
                  HAVING count(*) >= :portfolio) o USING (owner_name_norm)
            WHERE s.opportunity_score >= :lead
        """),
        "incomplete_valuation": ("review", """
            INSERT INTO parcel_audit_flag (parcel_id, code, severity, detail)
            SELECT m.parcel_id, 'incomplete_valuation', 'review',
                   'county record missing '
                   || concat_ws(' and ',
                        CASE WHEN m.land_value IS NULL THEN 'land value' END,
                        CASE WHEN m.improvement_value IS NULL THEN 'improvement value' END)
            FROM parcel_master m JOIN parcel_scores s USING (parcel_id)
            WHERE s.opportunity_score >= :lead
              AND (m.land_value IS NULL OR m.improvement_value IS NULL)
        """),
    }

    params = {"lead": _LEAD_FLOOR, "floor": _MARKET_PRICE_FLOOR,
              "ratio": _BULK_RATIO, "absv": _BULK_ABS, "portfolio": _PORTFOLIO_PARCELS}
    counts = {}
    with _engine().begin() as conn:
        conn.execute(text("TRUNCATE parcel_audit_flag RESTART IDENTITY"))
        for code, (_sev, sql) in checks.items():
            counts[code] = conn.execute(text(sql), params).rowcount
    print(f"audit flags: {counts}", flush=True)
    return counts


def confidence_level(flag_codes: list[str], severities: list[str]) -> str:
    """Roll per-parcel flags into one label for the UI. Pure so it's testable."""
    if not flag_codes:
        return "clean"
    if "caution" in severities:
        return "caution"
    return "review"


if __name__ == "__main__":
    build()

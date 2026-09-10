"""Label taxonomy + effective-label logic shared by the seed, the precision
report, and the review API. Kept pure/dependency-light so it can be unit-tested
and imported by the API without pulling scoring internals."""
from __future__ import annotations

# The three verdicts a reviewer can give.
LABELS = ("real", "fake", "maybe")

# Reason codes for FAKE labels. The first block mirrors the fake-lead classes we
# already detect (so the seed can be grounded in stored scoring constraints); the
# second block is the taxonomy of gaps the plan targets, available to human
# reviewers before a detector exists for them.
FAKE_REASONS = (
    # --- already-detected classes (auto-seeded from scoring constraints) ---
    "not_acquirable",          # public / institutional / exempt
    "regulatory_floodway",     # can't build
    "operating_amenity",       # golf course / club in operation
    "condo_unit",              # owns a slice of a shared footprint
    "complex_satellite",       # apartment/commercial bookkeeping parcel
    "commercial_land",         # operating commercial land (parking, gas, outparcel)
    "subdivision_inventory",   # builder mid-buildout, not for sale
    "permit_issued",           # already being developed
    # --- gap taxonomy (human-labelled until a detector exists) ---
    "public_park",
    "institutional",           # church / school / hospital / cemetery
    "hoa_common_area",         # HOA / reserve / open space, undevelopable
    "utility_infrastructure",  # substation / stormwater basin / ROW
    "landlocked",              # no road frontage / access
    "occupied_commercial",     # active in-use commercial/industrial
    "wetland",                 # wetland / conservation easement
    "large_acreage_non_infill",
    "township_rural",          # outside the urban service area, not infill
    "other",
)

# Which stored scoring-constraint codes MEAN "not a real opportunity" (fake).
# Deliberately excludes soft real-world constraints (flood, steep_slope, historic,
# cso): those discount a REAL parcel, they don't make it a fake. Ordered by
# specificity so a parcel carrying several is seeded with the most telling reason.
CONSTRAINT_FAKE_REASONS = (
    "not_acquirable",
    "regulatory_floodway",
    "operating_amenity",
    "condo_unit",
    "complex_satellite",
    "commercial_land",
    "subdivision_inventory",
    "permit_issued",
)

# Effective-label precedence when a parcel has multiple label rows: a human
# review beats an agent's pipeline signal beats a heuristic history seed; ties
# break on recency.
SOURCE_RANK = {"review": 3, "agent": 2, "history": 1}
SOURCES = tuple(SOURCE_RANK)


def is_valid(label: str, reason_code: str | None) -> tuple[bool, str]:
    """Validate a (label, reason_code) pair for the API. Returns (ok, message)."""
    if label not in LABELS:
        return False, f"label must be one of {LABELS}"
    if label == "fake" and reason_code is not None and reason_code not in FAKE_REASONS:
        return False, f"reason_code must be one of {FAKE_REASONS}"
    return True, ""


# SQL fragment: collapse lead_labels to one effective row per parcel. Use as a
# CTE — `WITH eff AS (<EFFECTIVE_LABEL_SQL>) ...`. No parameters.
EFFECTIVE_LABEL_SQL = """
    SELECT DISTINCT ON (parcel_id) parcel_id, label, reason_code, source, reviewed_at
    FROM lead_labels
    ORDER BY parcel_id,
             CASE source WHEN 'review' THEN 3 WHEN 'agent' THEN 2 ELSE 1 END DESC,
             reviewed_at DESC
"""

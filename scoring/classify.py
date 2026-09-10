"""Use classification (Section 9.4) — the piece the scorer needs first: which
`il_threshold` group a parcel falls in. Full decision-tree classification (missing
-middle, rowhome, mixed-use, LIHTC...) that also needs zoning + Connected
Communities geography lands in Phase 3; here we map the Ohio property CLASS code
to the economic use group that selects an underutilization threshold.

Ohio DTE property class codes (first digit is the broad class):
  3xx industrial, 4xx commercial, 5xx residential.
Within residential: 500 vacant land, 510 single-family, 520/530 two/three-family,
550 condo, 560+ apartments/multifamily.
"""
from __future__ import annotations

from typing import Optional

# use group -> key into scoring.yaml il_thresholds
SINGLE_FAMILY = "single_family"
MULTIFAMILY = "multifamily"
COMMERCIAL = "commercial"


def il_threshold_group(class_code: Optional[str]) -> str:
    """Map a CLASS code to the il_thresholds group. Defaults to commercial (1.0),
    the strictest threshold, when unknown."""
    code = _digits(class_code)
    if code is None:
        return COMMERCIAL
    broad = code // 100
    if broad == 5:  # residential
        if code in (510, 550):          # single-family, condo (owner-occupied unit)
            return SINGLE_FAMILY
        if code == 500:                  # residential vacant land
            return SINGLE_FAMILY         # scored as SF context; vacancy handled separately
        return MULTIFAMILY               # two/three-family, apartments
    return COMMERCIAL                    # industrial (3xx) and commercial (4xx)


def is_tax_exempt(class_code: Optional[str]) -> bool:
    """Ohio DTE 600-series: exempt property (government, parks, schools,
    churches, charitable). Not acquirable through normal channels — scored as a
    hard knockout unless land-bank inventory says otherwise."""
    code = _digits(class_code)
    return code is not None and 600 <= code < 700


def is_operating_amenity(class_code: Optional[str], owner_name: Optional[str],
                         cfg: dict) -> bool:
    """Golf courses / country clubs etc. — going concerns whose land reads as a
    huge underutilized opportunity but isn't for sale. Detected by DTE
    recreation class or club-style owner name (config: amenity)."""
    amenity = cfg.get("amenity")
    if not amenity:
        return False
    code = _digits(class_code)
    if code is not None and str(code) in {str(c) for c in amenity.get("classes", [])}:
        return True
    n = owner_name.upper() if isinstance(owner_name, str) else ""
    return any(k in n for k in amenity.get("owner_keywords", []))


def is_not_acquirable(class_code: Optional[str], owner_type: Optional[str]) -> bool:
    """Land that can't realistically be bought: DTE 600-series exempt,
    800-series public utility (railroads, utility corridors), or any parcel a
    public/institutional owner holds regardless of its class code (hospital
    parking lots and church annexes often carry commercial classes)."""
    code = _digits(class_code)
    if code is not None and (600 <= code < 700 or 800 <= code < 900):
        return True
    return owner_type in ("public", "institutional")


import re as _re

# Lender / REO owners (a curated pattern that avoids surnames like "BANK DANNY").
_BANK_RE = _re.compile(
    r"(BANK (OF|ONE|NA|CO|COMPANY|USA)|SAVINGS BANK|NATIONAL ASSOCIATION|\bFSB\b| NA$|"
    r"FANNIE MAE|FREDDIE MAC|FEDERAL (NATIONAL|HOME LOAN)|MORTGAGE (CO|CORP|LLC|INC|ASSN|"
    r"SERVICING)|FIFTH THIRD|US BANK|U S BANK|WELLS FARGO|HUNTINGTON|PNC BANK|JPMORGAN|"
    r"CHASE|KEYBANK|CITIZENS BANK|SECRETARY OF (HOUSING|VETERAN))", _re.IGNORECASE)


def is_bank_owned(owner_name: Optional[str]) -> bool:
    """Lender / REO ownership — a strongly motivated seller (they must dispose)."""
    return bool(owner_name and _BANK_RE.search(str(owner_name)))


def is_acquirable_authority(owner_name: Optional[str], cfg: dict) -> bool:
    """A public entity that actively DISPOSES of land for development — CMHA, the
    Port, a land bank/reutilization program. Their parcels are real acquisition
    targets (the team sources from them), so they waive the not_acquirable
    knockout rather than being dead ends. Owner-keyword driven (scoring.yaml)."""
    spec = cfg.get("acquirable_authorities")
    if not spec or not owner_name:
        return False
    name = str(owner_name).upper()
    return any(kw.upper() in name for kw in spec.get("owner_keywords", []))


# DTE multi-unit apartment classes. The class itself asserts a large building
# exists, so a $0/NULL improvement value means the value is booked on a sibling
# deed of the same complex — never that the land is vacant.
APARTMENT_CLASSES = (401, 402, 403)

# A commercial-improved parcel is the land-dominant component of an operating
# site (parking, gas pad, outlot, ground lease) when its improvement is BOTH a
# tiny fraction of land value AND small in absolute terms. The absolute floor is
# what separates a kiosk/canopy/parking apron from a genuinely underutilized real
# building on valuable land (e.g. a $500k structure on $6M downtown land) - the
# latter is a legitimate redevelopment target and must NOT be suppressed.
COMMERCIAL_LAND_IL_MAX = 0.1
COMMERCIAL_LAND_IMPROVEMENT_MAX = 250_000


def is_commercial_land_component(class_code: Optional[str], land_value: Optional[float],
                                 improvement_value: Optional[float]) -> bool:
    """A commercial IMPROVED-use class (401-499) that carries no real building —
    improvement both < 10% of land value AND under an absolute floor — is the
    land-dominant component of an operating commercial site (parking lot, gas pad,
    retail outlot, ground lease), NOT underutilized developable land. For a house,
    valuable land + no building means a teardown opportunity; for commercial, a
    near-zero improvement-to-land ratio is just how operating commercial is
    structured. Genuine vacant commercial is DTE class 400 (excluded here), and a
    substantial building on valuable land stays a redevelopment lead (above the
    absolute floor). This is what let a Kroger parking lot and gas station rank as
    Oakley's #1 and #2 opportunities."""
    code = _digits(class_code)
    if code is None or not (401 <= code <= 499):
        return False
    improvement = improvement_value or 0
    if improvement >= COMMERCIAL_LAND_IMPROVEMENT_MAX:
        return False                                  # a real building - keep as a lead
    if not land_value or land_value <= 0:
        return improvement <= 0
    return improvement / land_value < COMMERCIAL_LAND_IL_MAX


def improvements_booked_elsewhere(class_code: Optional[str],
                                  improvement_value: Optional[float],
                                  is_satellite: Optional[bool] = False) -> bool:
    """True when this deed's valuation says nothing about how the land is used,
    because the buildings are carried on another deed of the same property:
    a flagged complex satellite, or an apartment-class parcel with no
    improvement value. Such parcels get no underutilization signal at all —
    neither 'vacant' (the land is occupied) nor a low I/L reading (the ratio is
    an artifact of split bookkeeping)."""
    if is_satellite:
        return True
    code = _digits(class_code)
    return code in APARTMENT_CLASSES and not (improvement_value or 0) > 0


def is_vacant_land(class_code: Optional[str], has_structure: Optional[bool],
                   improvement_value: Optional[float]) -> bool:
    """Vacant only on affirmative evidence: a vacant-land class, or an explicit
    zero improvement on a class that doesn't itself assert a building.

    Two traps this avoids, both found by inspecting real top-ranked parcels:
    NULL improvement is UNKNOWN (Williamsburg complex), and an apartment-class
    deed with $0 improvement is a complex satellite — 4045 Reading Rd ranked #1
    countywide as 'vacant' while its sibling deed held $920,640 of buildings.
    Single/two/three-family classes with $0 improvement stay vacant: those are
    genuine demolished-structure infill lots, ~26k of them countywide."""
    code = _digits(class_code)
    if code in (300, 400, 500):          # industrial/commercial/residential vacant
        return True
    if code in APARTMENT_CLASSES:        # class asserts a building; value sits elsewhere
        return False
    if has_structure is False:           # explicit zero improvement upstream
        return True
    return improvement_value is not None and improvement_value <= 0


def _digits(class_code: Optional[str]) -> Optional[int]:
    if class_code is None:
        return None
    try:
        return int(str(class_code).strip())
    except (TypeError, ValueError):
        return None

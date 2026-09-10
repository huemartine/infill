"""Zoning capacity gap (Section 9.2): what the parcel could hold vs. what's
there. allowed_units from the district's density rule (zoning_rules.yaml),
existing_units from the Auditor's NUM_UNITS when present, else estimated from
the DTE class. capacity_gap_score saturates exponentially so a 12-unit gap
reads near-max — beyond that, more theoretical units add little signal.

Unknown districts (townships not yet encoded, unzoned gaps) contribute zero —
a conservative blank, never a guess.
"""
from __future__ import annotations

import math
from functools import lru_cache
from pathlib import Path
from typing import Optional

import yaml

_RULES_PATH = Path(__file__).parent / "config" / "zoning_rules.yaml"


@lru_cache(maxsize=1)
def load_rules(path: str | None = None) -> dict:
    return yaml.safe_load(Path(path or _RULES_PATH).read_text(encoding="utf-8"))


def allowed_units(zoning_code: Optional[str], area_sqft: Optional[float],
                  cc_zone: Optional[str], rules: dict) -> Optional[int]:
    """None => no capacity signal (unknown district). 0 => known, no residential."""
    if not zoning_code or not area_sqft or area_sqft <= 0:
        return None
    district = _lookup_district(str(zoning_code).strip(), rules)
    if district is None:
        return None
    if district.get("residential") is False:
        return 0
    sqft_per_unit = district["sqft_per_unit"]
    override = rules.get("cc_overrides", {}).get(cc_zone or "")
    if override:
        sqft_per_unit *= override.get("sqft_per_unit_factor", 1.0)
    units = int(area_sqft // sqft_per_unit)
    if override:
        units = max(units, override.get("min_units", 0))
    units = min(units, district.get("max_units", rules["max_units_cap"]),
                rules["max_units_cap"])
    return units


def use_family(zoning_code: Optional[str], rules: dict) -> Optional[str]:
    """What kind of housing (if any) the district realistically hosts:
    'sf' single-family streets, 'mm' missing-middle/multifamily, 'mixed'
    commercial main-street with residential above, 'none' (auto commercial,
    industrial, parks - housing is not built there). None = unknown district."""
    if not zoning_code:
        return None
    district = _lookup_district(str(zoning_code).strip(), rules)
    if district is None:
        return None
    if district.get("residential") is False:
        return "none"
    base = str(zoning_code).strip().upper()
    if base.startswith("SF"):
        return "sf"
    if base.startswith(("RM", "RMX", "T3", "T4")):
        return "mm"
    return "mixed"


def is_commercial_corridor(zoning_code: Optional[str], prefixes) -> bool:
    """True when a parcel sits on a commercial-corridor district (CC/CN/CG/T5 by
    default). These are 'mixed' family like Downtown (DD), but unlike downtown
    their presumptive use is commercial retail/office, so a for-sale housing
    thesis there needs positive residential-neighbor evidence — used by the
    scorer to damp underutilization when residential context is unknown.
    Matched by zoning-code prefix so suffixed codes (CC-M-T, T5N.SS-O) resolve."""
    if not zoning_code or not prefixes:
        return False
    code = str(zoning_code).strip().upper()
    return any(code.startswith(str(p).upper()) for p in prefixes)


def _lookup_district(code: str, rules: dict) -> Optional[dict]:
    """Exact match first, then strip trailing dash-suffixes progressively —
    CAGIS publishes overlay-suffixed variants ('RM-1.2-T', 'RMX-MH') that share
    the base district's density rules."""
    districts = rules["districts"]
    while True:
        d = districts.get(code)
        if d is not None:
            return d
        if "-" not in code:
            return None
        code = code.rsplit("-", 1)[0]


# Ohio DTE class -> rough existing dwelling units, used when NUM_UNITS is absent.
_CLASS_UNITS = {
    500: 0, 510: 1, 520: 2, 530: 3, 550: 1,
    401: 8, 402: 25, 403: 60,   # apartment classes: 4-19 / 20-39 / 40+ units
}


def existing_units(num_units: Optional[float], land_use_code: Optional[str],
                   has_structure: Optional[bool]) -> int:
    if num_units is not None and num_units > 0:
        return int(num_units)
    if has_structure is False:
        return 0
    try:
        return _CLASS_UNITS.get(int(str(land_use_code).strip()), 0)
    except (TypeError, ValueError):
        return 0


def capacity_gap_score(allowed: Optional[int], existing: int, rules: dict) -> Optional[float]:
    """None when allowed is None (unknown district — component excluded for
    this parcel rather than scored zero)."""
    if allowed is None:
        return None
    gap = max(allowed - existing, 0)
    k = rules["gap_saturation_units"]
    return round(100.0 * (1.0 - math.exp(-gap / k)), 2)

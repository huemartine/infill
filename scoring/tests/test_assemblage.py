"""Guards on the assembly finder. These pin the rules that keep the list clean —
the previous model produced 32,171 clusters, 83% of them under 2 acres, because
none of these constraints existed.
"""
from __future__ import annotations

import re

from scoring import assemblage


def test_config_thresholds_are_meaningful():
    """A 0.17-acre floor (the old min_combined_sqft: 7500) is what let a house
    plus its side lot count as an assembly."""
    from config import settings
    from scoring.score import load_config

    cfg = load_config(settings.scoring_config_path)["assemblage"]
    assert cfg["min_combined_acres"] >= 1.0
    assert cfg["min_member_acres"] > 0
    # an unbounded multi-owner cluster chained 886 parcels together
    assert 2 <= cfg["max_parcels_multi"] <= 12
    # the old sqft-based keys must be gone so nothing silently reads them
    assert "min_combined_sqft" not in cfg


def test_realistic_tract_rules():
    """A tract is a FEW BIG parcels. Assembling 54 platted lots to reach 4.7
    acres means buying 54 houses, and the acquisition cost erodes the deal —
    acres-per-parcel is what separates that from a real tract:
        MI HOMES OF CINCINNATI  54 parcels / 4.7 ac = 0.09  (builder inventory)
        WOODSVIEW HOUSE          3 parcels / 19.4 ac = 6.47  (the reference deal)
        LE STEELE PROPERTIES     8 parcels / 78 ac   = 9.78  (a real tract)
    A raw parcel cap alone would wrongly kill Le Steele, hence the ratio."""
    from config import settings
    from scoring.score import load_config

    cfg = load_config(settings.scoring_config_path)["assemblage"]
    app = cfg["min_acres_per_parcel"]
    assert app >= 1.0
    assert 0.09 < app <= 6.47          # excludes builder inventory, keeps Woodsview
    # one owner with many parcels is still ONE negotiation; every parcel in a
    # multi-owner tract is another seller who has to agree
    assert cfg["max_parcels_same"] > cfg["max_parcels_multi"]
    assert cfg["max_parcels_multi"] <= 5


def test_junk_member_predicate_covers_the_known_false_classes():
    """Any of these on a member disqualifies the whole cluster — apartment
    satellite deeds alone accounted for 1,308 false clusters at 5+ acres."""
    sql = assemblage._JUNK_MEMBER
    for flag in ("is_condo_unit", "is_satellite", "is_subdivision_lot",
                 "not_acquirable", "operating_amenity", "commercial_land"):
        assert flag in sql, flag


def test_not_for_sale_owner_pattern():
    """Conservation, civic, religious, club and HOA holdings are large enough to
    top an acreage-sorted list, and owner_type misses them (GREENACRES FOUNDATION
    is typed 'individual'), so they are matched by name.

    The pattern is written for Postgres, whose word boundary is `\\y`; Python's
    re wants `\\b`, so translate before compiling (same as _BANK_RE)."""
    rx = re.compile(assemblage._NOT_FOR_SALE_OWNER.replace(r"\y", r"\b"), re.I)
    for owner in ("GREENACRES FOUNDATION", "TURNER FARM PRESERVATION FOUND",
                  "SPRING GROVE CEMETERY", "ST XAVIER CHURCH",
                  "ARCHBISHOP OF CINCINNATI TR", "HAMILTON COUNTY PARK DIST",
                  "CITY OF BLUE ASH", "CAMARGO CLUB THE", "VINTAGE CLUB COMMUNITY",
                  "HICKORY RIDGE OWNERS ASSOCIATION", "FALLSINGTON HOMEOWNERS",
                  "MONTGOMERY COMMUNITY IMPROVEMENT"):
        assert rx.search(owner), owner
    # ordinary private owners must NOT be swept up
    for owner in ("WOODSVIEW HOUSE", "HUGUENIN LAURENT C TR", "BAUER PAUL G",
                  "DRAKE ROAD HOLDINGS", "SMITH JOHN A", "TRADITIONS DEVELOPMENT GROUP"):
        assert not rx.search(owner), owner


def test_only_homes_and_raw_land_qualify():
    """Improved commercial/industrial parcels are somebody's operating business —
    they inflated tracts with sites that are neither residential nor buyable."""
    sql = assemblage._DEVELOPABLE_USE
    assert "BETWEEN 100 AND 199" in sql            # agricultural
    assert "IN (300, 400, 500)" in sql             # vacant land of any class
    assert "BETWEEN 501 AND 599" in sql            # homes
    # improved commercial (401-499) / industrial (301-399) must not be admitted
    assert "401" not in sql and "499" not in sql


def test_nan_guard_uses_postgres_semantics():
    """Postgres numerics define NaN = NaN as TRUE, so the IEEE `x <> x` idiom
    silently fails; 2,300 parcels carry a NaN area and NaN sorts as the LARGEST
    numeric, so those clusters would sail past the acreage floor."""
    src = (assemblage.build.__doc__ or "") + open(
        assemblage.__file__, encoding="utf-8").read()
    assert "'NaN'::numeric" in src
    assert "area_sqft <> m.area_sqft" not in src

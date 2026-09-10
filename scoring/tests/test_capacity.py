"""Unit tests for the zoning capacity gap (Section 9.2)."""
import pytest

from scoring.capacity import (
    allowed_units,
    capacity_gap_score,
    existing_units,
    load_rules,
)


@pytest.fixture(scope="module")
def rules():
    return load_rules()


def test_sf_district_subdivision_math(rules):
    # 12,000 sqft in SF-4 (4,000 sqft lots) supports 3 units via subdivision
    assert allowed_units("SF-4", 12_000, None, rules) == 3
    # a conforming single lot supports exactly 1
    assert allowed_units("SF-6", 6_500, None, rules) == 1


def test_rm_density(rules):
    assert allowed_units("RM-1.2", 12_000, None, rules) == 10
    # RMX caps at 3 regardless of area
    assert allowed_units("RMX", 50_000, None, rules) == 3


def test_suffixed_variants_resolve_to_base_district(rules):
    # CAGIS publishes overlay-suffixed codes; they share base density rules
    assert allowed_units("RM-1.2-T", 12_000, None, rules) == 10
    assert allowed_units("RMX-MH", 50_000, None, rules) == 3
    assert allowed_units("MG-T", 500_000, None, rules) == 0


def test_unknown_and_nonresidential(rules):
    assert allowed_units("R-1-TOWNSHIP", 10_000, None, rules) is None  # not encoded
    assert allowed_units("PR", 500_000, None, rules) == 0              # park: known zero
    assert allowed_units("MG", 500_000, None, rules) == 0


def test_cc_override_min_units(rules):
    # dormant until cc_zone is populated, but the math must hold:
    # a small SF-6 lot near an NBD gets middle housing by right (4 units)
    assert allowed_units("SF-6", 6_500, "nbd", rules) == 4


def test_existing_units_fallbacks():
    assert existing_units(6, "510", True) == 6         # NUM_UNITS wins
    assert existing_units(None, "520", True) == 2      # two-family class
    assert existing_units(None, "403", True) == 60     # 40+ unit apartment class
    assert existing_units(None, "510", False) == 0     # vacant


def test_gap_score_shape(rules):
    assert capacity_gap_score(None, 0, rules) is None  # unknown district
    assert capacity_gap_score(1, 1, rules) == 0.0      # built to capacity
    mid = capacity_gap_score(7, 1, rules)
    big = capacity_gap_score(40, 1, rules)
    assert 0 < mid < big <= 100.0
    # saturation: 30-unit and 90-unit gaps read nearly the same
    assert capacity_gap_score(91, 1, rules) - capacity_gap_score(31, 1, rules) < 10


# --- commercial-corridor detection (v2026.19 audit round) -------------------
from scoring.capacity import is_commercial_corridor

_PREFIXES = ["CC", "CN", "CG", "T5"]


def test_commercial_corridor_matches_corridor_codes():
    # the districts the reviewer's commercial fakes sat on
    for z in ("CC-P", "CC-M-T", "CN-M-MH", "T5MS", "T5N.SS-O", "T5N.LS-O", "CG-A"):
        assert is_commercial_corridor(z, _PREFIXES), z


def test_commercial_corridor_excludes_downtown_and_residential():
    # DD (downtown) and residential zoning must NOT be corridors — those leads
    # were labelled real and must keep full housing credit
    for z in ("DD", "RM-1.2", "RM-1.2-T", "SF-6", "SF-4-MH", "T4N.MF-O", "A PUD", None):
        assert not is_commercial_corridor(z, _PREFIXES), z


def test_commercial_corridor_no_prefixes_is_false():
    assert not is_commercial_corridor("CC-P", None)

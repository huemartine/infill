"""Guards on the plain-English zoning read.

The promise this module makes to the agent is that every number came off the
official ordinance and nothing was invented. These tests pin that promise: the
transcribed values, the refusal to guess, and the suffix handling that decides
which district a county code even maps to.
"""
from __future__ import annotations

import pytest

from scoring import zoning_info as zi


@pytest.fixture(scope="module")
def cfg():
    return zi.load_districts()


def test_untranscribed_district_is_admitted_not_invented():
    """The failure mode that matters is a confident wrong envelope. Madeira and
    Indian Hill parcels carry no zoning code at all in the county layer, and
    Symmes has districts we have not transcribed — all of those must come back
    known=False with a reason, never a plausible-looking set of setbacks."""
    for code, hood in (("R-99", "Blue Ash"), ("A", "Madeira"), (None, "Hyde Park")):
        r = zi.describe(code, hood)
        assert r["known"] is False, (code, hood)
        assert r["reason"]
        assert "rules" not in r


def test_suffixes_resolve_to_the_base_district():
    """County codes append designations the ordinance tables don't use. If the
    peel is wrong the parcel silently reports 'not transcribed' — 2,306 Hyde Park
    parcels are spelled SF-6-MH, so getting this wrong loses a third of the
    market."""
    cases = {
        ("SF-6-MH", "Hyde Park"): "SF-6",
        ("RM-1.2-T", "Hyde Park"): "RM-1.2",
        ("CC-A-B", "Hyde Park"): "CC-A",
        ("RMX-MH", "Oakley"): "RMX",
        ("A_HOD", "Montgomery"): "A",
        ("OM_OUT", "Montgomery"): "OM",
        ("A CUP", "Symmes Township"): "A",
    }
    for (code, hood), want in cases.items():
        r = zi.describe(code, hood)
        assert r["known"] is True, (code, hood)
        assert r["district"] == want, (code, r["district"])


def test_municipality_resolves_the_rulebook_and_beats_the_region_map():
    """parcel_master.municipality names the body that actually wrote the code.
    Going through it rather than the neighbourhood table is what lets every
    Cincinnati neighbourhood read its zoning (149,763 parcels) instead of only
    the two listed target markets. The values below are the live column's."""
    r = zi.describe("SF-6", "Northside", municipality="Cincinnati")
    assert r["known"] is True and r["jurisdiction"] == "City of Cincinnati"
    # ...and the neighbourhood alone still can't (Northside isn't in the table),
    # which is the whole reason municipality is preferred
    assert zi.describe("SF-6", "Northside")["known"] is False
    for muni in ("Blue Ash", "Symmes Township", "Montgomery"):
        assert muni in zi._MUNICIPALITY_JURISDICTION

    # An unmapped municipality must fall back, not blow up or silently mislabel:
    # Norwood runs its own code, so a Cincinnati-looking district there is not
    # something we may answer.
    assert zi.describe("SF-6", "Norwood", municipality="Norwood")["known"] is False


def test_longest_district_wins_over_a_shorter_prefix():
    """RM-1.2 and RM-2.0 both start with a segment that must not match on its
    own, and SF-2 must not swallow SF-20."""
    assert zi.describe("SF-20", "Hyde Park")["district"] == "SF-20"
    assert zi.describe("SF-2", "Hyde Park")["district"] == "SF-2"
    assert zi.describe("RM-2.0-MH", "Oakley")["district"] == "RM-2.0"


def test_connected_communities_overlays_are_surfaced():
    """This is the single most valuable thing on a Cincinnati infill lot: the
    -MH / -T / -B suffixes make 2-4 family housing legal by right and drop the
    off-street parking requirement (Emer. Ord. 199-2024). Reporting only the base
    SF-6 envelope would hide it."""
    r = zi.describe("SF-6-MH", "Hyde Park")
    assert [o["code"] for o in r["overlays"]] == ["MH"]
    o = r["overlays"][0]
    assert "1403-04" in o["citation"]
    assert "parking" in o["effect"].lower()
    assert "four-family" in o["effect"].lower()
    # and it has to reach the one-line summary, which is what the list view shows
    assert "Middle Housing" in r["summary"]
    # a parcel with no suffix must not sprout one
    assert zi.describe("SF-6", "Hyde Park")["overlays"] == []


@pytest.mark.parametrize("hood,code,field,want", [
    # Cincinnati CMC Schedule 1403-07 — cross-checked against a second
    # independent reproduction of the code, which agreed on every row.
    ("Hyde Park", "SF-20", "min_lot_area_sqft", 20000),
    ("Hyde Park", "SF-20", "min_lot_width_ft", 70),      # NOT 110; easy to fumble
    ("Hyde Park", "SF-6", "min_lot_area_sqft", 6000),
    ("Hyde Park", "SF-6", "front_yard_ft", 25),
    ("Oakley", "SF-4", "min_lot_area_sqft", 4000),
    # Schedule 1405-07 states these PER DWELLING UNIT, which is the whole point
    # of an RM district — recording them as a per-lot minimum would be wrong by
    # roughly the unit count.
    ("Hyde Park", "RM-1.2", "min_lot_area_sqft", 1200),
    ("Hyde Park", "RM-2.0", "min_lot_area_sqft", 2000),
    # Montgomery Schedule 151.1004 / 151.1005
    ("Montgomery", "A", "min_lot_area_sqft", 20000),
    ("Montgomery", "D-3", "min_lot_area_sqft", 6250),
    ("Montgomery", "D-3", "front_yard_ft", 50),          # 50 ft even on a 6,250 sqft lot
    # Symmes Article VI, Sec. 64.4 — one full acre
    ("Symmes Township", "AA", "min_lot_area_sqft", 43560),
    ("Symmes Township", "AA", "min_lot_width_ft", 150),
])
def test_transcribed_numbers_match_the_ordinance(cfg, hood, code, field, want):
    juris = cfg["jurisdictions"][zi._REGION_JURISDICTION[hood]]
    assert juris["districts"][code][field] == want


def test_per_unit_areas_are_flagged_as_per_unit(cfg):
    """1,200 sqft reads as a tiny lot minimum unless it is labelled per-unit.
    Every district whose area figure is a density, not a lot floor, must carry
    area_is_per_unit so the UI says so."""
    for hood, code in (("Hyde Park", "RM-1.2"), ("Hyde Park", "RM-2.0"),
                       ("Oakley", "RMX"), ("Hyde Park", "RM-0.7"),
                       ("Blue Ash", "R-3"), ("Symmes Township", "DD")):
        d = cfg["jurisdictions"][zi._REGION_JURISDICTION[hood]]["districts"][code]
        assert d.get("area_is_per_unit") is True, (hood, code)
        assert "per dwelling unit" in zi.describe(code, hood)["rules"][0]["value"]


def test_min_total_side_yards_render_both_numbers():
    """Cincinnati states side yards as a min/total pair: SF-6 is 7 ft on one side
    but 16 ft across both, so a builder planning 7/7 is 2 ft short. Showing only
    the minimum would mislead."""
    side = next(r for r in zi.describe("SF-6", "Hyde Park")["rules"]
                if r["label"] == "Side setback")
    assert "7 ft" in side["value"] and "16 ft" in side["value"]
    # where the code gives a single number, don't imply a pair
    side = next(r for r in zi.describe("A", "Symmes Township")["rules"]
                if r["label"] == "Side setback")
    assert side["value"] == "15 ft each side"


def test_every_jurisdiction_is_traceable_to_an_official_source(cfg):
    """A number nobody can re-check is worse than no number: the agent is making
    acquisition decisions off these."""
    for key, juris in cfg["jurisdictions"].items():
        for field in ("name", "source", "citation", "url", "verified"):
            assert juris.get(field), (key, field)
        assert juris["districts"], key


def test_a_district_states_either_an_envelope_or_why_it_cannot(cfg):
    """Planned districts (PD, FF, OO) genuinely have no district-wide envelope —
    the approved plan governs. That is a real answer, but it has to be written
    down, otherwise a silently empty district looks like a transcription bug."""
    dimensional = ("min_lot_area_sqft", "min_lot_width_ft", "front_yard_ft",
                   "side_yard_ft", "rear_yard_ft", "max_height_ft")
    for jkey, juris in cfg["jurisdictions"].items():
        for dkey, d in juris["districts"].items():
            if not any(d.get(f) is not None for f in dimensional):
                assert d.get("note"), f"{jkey}.{dkey} has neither rules nor an explanation"

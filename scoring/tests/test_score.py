"""Unit tests for the scoring formulas — no DB, pure functions."""
from datetime import date
from pathlib import Path

import pytest

from scoring.score import (
    composite,
    distress_score,
    is_knocked_out,
    load_config,
    recency_decay,
)

CFG_PATH = Path(__file__).resolve().parents[1] / "config" / "scoring.yaml"


@pytest.fixture(scope="module")
def cfg() -> dict:
    return load_config(CFG_PATH)


def test_config_weights_sum_to_one(cfg):
    assert abs(sum(cfg["composite_weights"].values()) - 1.0) < 1e-9


def test_recency_decay_half_life(cfg):
    # exactly one half-life back => 0.5
    assert recency_decay(date(2025, 1, 1), date(2026, 1, 1), 12) == pytest.approx(0.5)
    # null half_life => no decay
    assert recency_decay(date(2000, 1, 1), date(2026, 1, 1), None) == 1.0


def test_distress_recent_filing_beats_old_one(cfg):
    asof = date(2026, 6, 30)
    recent = distress_score([{"signal_type": "foreclosure_filing", "event_date": date(2026, 6, 1)}], cfg, asof)
    old = distress_score([{"signal_type": "foreclosure_filing", "event_date": date(2022, 6, 1)}], cfg, asof)
    assert recent > old > 0


def test_composite_applies_constraint_penalty(cfg):
    comps = {k: 100.0 for k in cfg["composite_weights"]}
    clean = composite(comps, [], cfg)
    flooded = composite(comps, ["flood"], cfg)
    assert clean == pytest.approx(100.0)
    assert flooded == pytest.approx(70.0)  # flood penalty 0.7


def test_hard_knockout(cfg):
    assert is_knocked_out(["regulatory_floodway"], cfg)
    assert not is_knocked_out(["flood"], cfg)


# --- v2026.2: discrimination within components ---

def test_vacant_quality_scales_by_value_and_size(cfg):
    from scoring.score import vacant_quality
    # big + valuable => full credit; sliver + cheap => ~0
    assert vacant_quality(500_000, 10_000, cfg) == pytest.approx(1.0)
    assert vacant_quality(1_000, 300, cfg) == pytest.approx(0.0)
    # middle lot lands strictly between
    mid = vacant_quality(20_000, 3_000, cfg)
    assert 0.0 < mid < 1.0
    # missing inputs are skipped, not zeroed
    assert vacant_quality(None, None, cfg) == 1.0
    assert vacant_quality(500_000, None, cfg) == pytest.approx(1.0)


def test_severity_scales_distress(cfg):
    from datetime import date
    asof = date(2026, 6, 30)
    small = distress_score(
        [{"signal_type": "tax_delinquent", "event_date": asof, "severity": 100}],
        cfg, asof, land_value=50_000)
    large = distress_score(
        [{"signal_type": "tax_delinquent", "event_date": asof, "severity": 50_000}],
        cfg, asof, land_value=50_000)
    neutral = distress_score(
        [{"signal_type": "tax_delinquent", "event_date": asof}],  # no severity
        cfg, asof, land_value=50_000)
    assert large > neutral > small > 0


def test_repeat_violations_hit_diminishing_returns(cfg):
    from datetime import date
    asof = date(2026, 6, 30)
    def n_violations(n):
        return distress_score(
            [{"signal_type": "code_violation", "event_date": asof, "severity": 1.0}] * n,
            cfg, asof)
    # rises up to the cap (max_events: 3), then flat — 133 reports != 40x distress
    assert n_violations(1) < n_violations(3)
    assert n_violations(3) == pytest.approx(n_violations(133))
    # uncapped types (tax_delinquent) are unaffected by the cap logic
    assert distress_score(
        [{"signal_type": "tax_delinquent", "event_date": asof}], cfg, asof) > 0


def test_null_valuation_is_not_vacant():
    from scoring.classify import is_vacant_land
    # a NULL-valued apartment-class deed (complex satellite lot) is NOT vacant
    assert not is_vacant_land("403", None, None)
    assert not is_vacant_land("510", None, None)
    # affirmative evidence still reads vacant
    assert is_vacant_land("500", None, None)          # vacant-land class
    assert is_vacant_land("510", False, 0.0)          # explicit zero improvement
    assert is_vacant_land("510", None, 0.0)


def test_residential_context_factor(cfg):
    from scoring.score import residential_context_factor
    # a real residential street (>=40% improved-res neighbors) = full credit
    assert residential_context_factor(71.0, cfg) == 1.0
    assert residential_context_factor(40.0, cfg) == 1.0
    # a commercial strip (0% residential neighbors) keeps only the floor
    assert residential_context_factor(0.0, cfg) == pytest.approx(0.30)
    # Calvert St territory (~18%) sits between floor and full
    mid = residential_context_factor(18.0, cfg)
    assert 0.30 < mid < 1.0
    # unknown context is neutral - never punish missing data
    assert residential_context_factor(None, cfg) == 1.0


def test_use_family_from_zoning():
    from scoring.capacity import load_rules, use_family
    rules = load_rules()
    assert use_family("SF-4-MH", rules) == "sf"
    assert use_family("RM-1.2-T", rules) == "mm"
    assert use_family("CC-P", rules) == "mixed"      # main-street commercial
    assert use_family("CG-A", rules) == "none"       # auto commercial: no housing
    assert use_family("MG", rules) == "none"
    assert use_family("R-1-TOWNSHIP", rules) is None  # unknown district


def test_commercial_land_component_detection():
    from scoring.classify import is_commercial_land_component
    # Kroger parking lot: commercial class 452, $1.8M land, $0 improvement
    assert is_commercial_land_component("452", 1_841_690, 0)
    # Kroger gas station: class 452, $2.5M land, $115k kiosk -> il 0.046 < 0.1
    assert is_commercial_land_component("452", 2_519_250, 115_290)
    # a real commercial building on its lot (il 0.6) is NOT a land component
    assert not is_commercial_land_component("420", 796_800, 477_540)
    # a substantial building on very valuable land (il 0.08 but $500k structure)
    # is a genuine redevelopment lead, above the absolute floor -> NOT suppressed
    assert not is_commercial_land_component("452", 6_000_000, 500_000)
    # genuine VACANT commercial (class 400) is excluded -> stays an opportunity
    assert not is_commercial_land_component("400", 500_000, 0)
    # residential teardown (class 510, $0 improvement) is untouched
    assert not is_commercial_land_component("510", 60_000, 0)


def test_apartment_class_never_vacant_but_sf_teardown_still_is():
    from scoring.classify import is_vacant_land
    # 4045 Reading Rd: apartment class, explicit $0 improvement (value booked on
    # a sibling deed) — must NOT read as vacant land
    assert not is_vacant_land("403", False, 0.0)
    assert not is_vacant_land("401", False, 0.0)
    # but a demolished single/two/three-family lot IS genuine infill (~26k of
    # these countywide — the platform's core inventory)
    assert is_vacant_land("510", False, 0.0)
    assert is_vacant_land("520", False, 0.0)
    assert is_vacant_land("530", None, 0.0)


def test_operating_amenity_detection(cfg):
    from scoring.classify import is_operating_amenity
    # by DTE recreation class
    assert is_operating_amenity("461", "SOMENAME LLC", cfg)
    assert is_operating_amenity("463", None, cfg)
    # by owner name even with a generic commercial class
    assert is_operating_amenity("400", "KENWOOD COUNTRY CLUB INC", cfg)
    assert is_operating_amenity("500", "MIAMI VIEW GOLF CLUB INC", cfg)
    # ordinary owners/classes untouched
    assert not is_operating_amenity("510", "SMITH JANE", cfg)
    assert not is_operating_amenity("400", "RIVERSIDE HOLDINGS LLC", cfg)


def test_assemblies_do_not_feed_the_score():
    """Assemblies are a FINDER, not a scoring input. `assemblage_component` fed
    the old opportunity score and was part of why the model produced an opaque
    'assemblage' label; the v2026.23 redesign dropped it and the v2 finder must
    not reintroduce a scoring path."""
    import scoring.assemblage as asm
    assert not hasattr(asm, "assemblage_component")


def test_ownership_multiplier_caps(cfg):
    from scoring.score import ownership_multiplier
    assert ownership_multiplier(None, False, cfg) == 1.0
    assert ownership_multiplier("llc", True, cfg) == pytest.approx(1.10)  # capped
    assert ownership_multiplier("public", False, cfg) == pytest.approx(0.85)


# --- commercial-corridor null-context damping (v2026.19 audit round) --------
def _infill_cfg():
    return {"infill": {"res_context_floor": 0.30, "res_context_full_pct": 40,
                       "commercial_context_floor": 0.20, "commercial_context_full_pct": 70}}


def test_null_context_neutral_on_residential():
    # residential land with unknown context stays neutral (don't punish unknowns)
    from scoring.score import residential_context_factor
    assert residential_context_factor(None, _infill_cfg(), use_family="mm") == 1.0
    assert residential_context_factor(None, _infill_cfg(), use_family="sf") == 1.0


def test_null_context_damps_on_commercial_corridor():
    # the fix: a corridor lot with NO residential evidence is not a housing lead
    from scoring.score import residential_context_factor
    f = residential_context_factor(None, _infill_cfg(), use_family="mixed",
                                   commercial_corridor=True)
    assert f == 0.20   # commercial floor, not neutral 1.0


def test_null_context_neutral_on_downtown_mixed():
    # DD is 'mixed' family but NOT a corridor -> null context stays neutral, so
    # real downtown development leads keep full credit
    from scoring.score import residential_context_factor
    assert residential_context_factor(None, _infill_cfg(), use_family="mixed",
                                      commercial_corridor=False) == 1.0


def test_corridor_vacant_underutilization_is_damped():
    from scoring.score import underutilization_score
    cfg = {**_infill_cfg(), "il_thresholds": {"commercial": 1.0},
           "vacant_scaling": {"sliver_sqft": 500, "viable_sqft": 5000,
                              "min_land_value": 2000, "full_land_value": 40000}}
    base = dict(il_ratio=None, use_group="commercial", vacant=True, cfg=cfg,
                land_value=100000, area_sqft=8000, res_context_pct=None)
    residential = underutilization_score(**base, use_family="mm", commercial_corridor=False)
    corridor = underutilization_score(**base, use_family="mixed", commercial_corridor=True)
    assert residential > corridor and corridor <= residential * 0.25


# --- teardown score (Oyler Hines Mode A, v2026.21) --------------------------
def _td_cfg():
    return {"teardown": {"land_share_floor": 0.28, "land_share_full": 0.48,
                        "min_land_value": 2000, "full_land_value": 40000}}


def test_teardown_credits_modest_home_on_valuable_lot():
    from scoring.score import teardown_score
    # their median deal: land 123770 / improv 194740 -> land-share ~0.39 -> credit
    s = teardown_score(123770, 194740, "single_family", _td_cfg())
    assert 40 < s < 70


def test_teardown_ignores_ordinary_home():
    from scoring.score import teardown_score
    # ordinary home: land 123500 / improv 339140 -> share 0.27 (below floor) -> 0
    assert teardown_score(123500, 339140, "single_family", _td_cfg()) == 0.0


def test_teardown_only_single_family_and_improved():
    from scoring.score import teardown_score
    assert teardown_score(200000, 100000, "commercial", _td_cfg()) == 0.0   # not SF
    assert teardown_score(200000, 100000, "multifamily", _td_cfg()) == 0.0  # not SF
    assert teardown_score(100000, 0, "single_family", _td_cfg()) == 0.0     # vacant
    assert teardown_score(100000, None, "single_family", {}) == 0.0         # no config


def test_teardown_ramps_with_land_share():
    from scoring.score import teardown_score
    modest = teardown_score(200000, 150000, "single_family", _td_cfg())   # share .57 -> 100
    marginal = teardown_score(100000, 200000, "single_family", _td_cfg()) # share .33 -> low
    assert modest > marginal and modest == 100.0


def test_teardown_size_factor_favors_small_homes():
    from scoring.score import teardown_score
    cfg = {"teardown": {"land_share_floor": 0.28, "land_share_full": 0.48,
                        "min_land_value": 2000, "full_land_value": 40000,
                        "size_small_sqft": 2500, "size_large_sqft": 5000, "size_floor": 0.5}}
    small = teardown_score(123770, 194740, "single_family", cfg, home_sqft=2000)
    large = teardown_score(123770, 194740, "single_family", cfg, home_sqft=5000)
    unknown = teardown_score(123770, 194740, "single_family", cfg, home_sqft=None)
    assert small > large                    # small home = stronger teardown
    assert abs(large - small * 0.5) < 0.1   # large tapers to size_floor
    assert unknown == small                 # missing size stays neutral, not penalised


def test_teardown_land_value_is_market_relative():
    from scoring.score import teardown_score
    # v2026.24: with a neighborhood reference, the land factor scores the lot
    # against its local median (ratio ramp) — NOT a fixed dollar cap that pins
    # every affluent-market lot at 100. Same land-share, different lot value.
    cfg = {"teardown": {"land_share_floor": 0.28, "land_share_full": 0.48,
                        "min_land_value": 2000, "full_land_value": 40000,
                        "land_ref_ratio_floor": 0.0, "land_ref_ratio_full": 1.8}}
    ref = 140_000                                   # neighborhood median SF land
    prime = teardown_score(280_000, 200_000, "single_family", cfg, land_ref=ref)  # 2.0x -> maxes
    median = teardown_score(140_000, 100_000, "single_family", cfg, land_ref=ref)  # 1.0x -> mid
    weak = teardown_score(56_000, 40_000, "single_family", cfg, land_ref=ref)      # 0.4x -> low
    assert prime > median > weak                    # spreads instead of all-100
    assert 45 < median < 70                         # median lot is mid, not pinned at 100
    # without a reference it falls back to the absolute ramp (unchanged behavior):
    # both these clear $40k, so both saturate — exactly the bug the reference fixes
    assert teardown_score(280_000, 200_000, "single_family", cfg) == \
           teardown_score(140_000, 100_000, "single_family", cfg)

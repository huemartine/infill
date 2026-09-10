"""End-to-end Phase-1 pipeline test against a frozen *real* CAGIS sample
(ingestion/tests/fixtures/cagis_sample.geojson). No database required — exercises
extract -> normalize -> resolve -> derive signals -> score, and asserts the
ranking behaves: vacant land and distressed parcels must outrank well-improved
owner-occupied homes.
"""
import json
from datetime import date
from pathlib import Path

import pytest

from ingestion.base import RawBatch, Resource
from ingestion.extractors.cagis import CagisExtractor, derive_signals
from scoring.run import score_frame
from scoring.score import load_config

FIXTURE = Path(__file__).parent / "fixtures" / "cagis_sample.geojson"
CFG = Path(__file__).resolve().parents[2] / "scoring" / "config" / "scoring.yaml"


@pytest.fixture(scope="module")
def scored():
    features = json.loads(FIXTURE.read_text())["features"]
    batch = RawBatch(
        resource=Resource("cagis", "cagis_parcels", "http://fixture", "arcgis_feature"),
        records=features,
    )
    ext = CagisExtractor()
    parcels = ext.normalize(batch)
    signals = derive_signals(parcels)
    cfg = load_config(CFG)
    scores = score_frame(parcels, signals, cfg, asof=date(2026, 6, 30))
    return parcels, signals, scores


def test_normalize_produces_parcels(scored):
    parcels, _, _ = scored
    assert len(parcels) == 20
    assert parcels["parcel_id"].is_unique
    # parcel_id canonicalized (no leading-zero 12-char forms)
    assert not (parcels["parcel_id"].str.len() == 12).any()


def test_il_ratio_and_vacancy(scored):
    parcels, _, _ = scored
    vacant = parcels[parcels["improvement_value"].fillna(0) == 0]
    assert len(vacant) >= 5
    assert (vacant["has_structure"] == False).all()  # noqa: E712


def test_distress_signals_emitted(scored):
    _, signals, _ = scored
    # fixture includes parcels with DELQ_TAXES > 0
    assert (signals["signal_type"] == "tax_delinquent").sum() >= 3


def test_vacant_outranks_improved(scored):
    _, _, scores = scored
    ranked = scores.set_index("parcel_id")
    # v2026.2: vacant credit is scaled by lot quality — a valuable vacant lot
    # (land $531k) must far outrank a $1,180 sliver, which lands near zero
    big = ranked.loc["50000810001"]
    sliver = ranked.loc["50003310118"]
    assert big["development_score"] == 100.0
    assert sliver["development_score"] == 0.0
    assert big["opportunity_score"] > sliver["opportunity_score"]
    # a well-improved owner-occupied home (il ~8.5) still scores 0 on underutilization
    improved = ranked.loc["00200090194"]
    assert improved["development_score"] == 0.0
    assert big["opportunity_score"] > improved["opportunity_score"]


def test_distressed_vacant_tops_plain_vacant(scored):
    _, _, scores = scored
    ranked = scores.set_index("parcel_id")
    # 52500240256: valuable vacant (land $268k) AND tax-delinquent -> distress
    # lifts it above a plain vacant lot with no delinquency.
    distressed_vacant = ranked.loc["52500240256"]
    plain_vacant = ranked.loc["52500240228"]  # vacant, no delinquency
    assert distressed_vacant["distress_score"] > 0
    assert distressed_vacant["opportunity_score"] > plain_vacant["opportunity_score"]


def test_constraint_flags_penalize_and_knock_out(scored):
    import pandas as pd

    from scoring.score import load_config
    cfg = load_config(CFG)
    base = {"parcel_id": "x", "land_use_code": "500", "improvement_value": 0,
            "land_value": 100_000, "has_structure": False, "il_ratio": 0.0,
            "area_sqft": 10_000}
    clean = pd.DataFrame([{**base, "parcel_id": "clean"}])
    flooded = pd.DataFrame([{**base, "parcel_id": "flooded", "flood_flag": True}])
    floodway = pd.DataFrame([{**base, "parcel_id": "fw", "floodway_flag": True}])
    s_clean = score_frame(clean, None, cfg).iloc[0]
    s_flood = score_frame(flooded, None, cfg).iloc[0]
    s_fw = score_frame(floodway, None, cfg).iloc[0]
    # soft penalty: flood multiplies by 0.7
    assert s_flood["opportunity_score"] == pytest.approx(
        s_clean["opportunity_score"] * 0.7, abs=0.1)
    # hard knockout: regulatory floodway drops to zero
    assert s_fw["opportunity_score"] == 0.0
    assert "regulatory_floodway" in s_fw["constraints"]["active"]


def test_not_acquirable_knockout_with_landbank_exception(scored):
    import pandas as pd

    from scoring.score import load_config
    cfg = load_config(CFG)
    base = {"improvement_value": 0, "land_value": 500_000, "has_structure": False,
            "il_ratio": 0.0, "area_sqft": 100_000}
    parcels = pd.DataFrame([
        {**base, "parcel_id": "park", "land_use_code": "640"},           # exempt class
        {**base, "parcel_id": "railyard", "land_use_code": "810"},       # utility class
        {**base, "parcel_id": "hospital_lot", "land_use_code": "400",    # commercial class,
         "owner_type": "institutional"},                                 # institutional owner
        {**base, "parcel_id": "landbank_lot", "land_use_code": "640"},
    ])
    signals = pd.DataFrame([{
        "parcel_id": "landbank_lot", "signal_type": "landbank_inventory",
        "event_date": "2026-06-01", "severity": None,
    }])
    out = score_frame(parcels, signals, cfg).set_index("parcel_id")
    # parks, rail corridors, and hospital-owned lots are not opportunities
    for pid in ("park", "railyard", "hospital_lot"):
        assert out.loc[pid]["opportunity_score"] == 0.0, pid
        assert "not_acquirable" in out.loc[pid]["constraints"]["active"]
    # but exempt land the land bank is selling stays scoreable
    assert out.loc["landbank_lot"]["opportunity_score"] > 0


def test_commercial_strip_lot_damped_vs_residential_street(scored):
    import pandas as pd

    from scoring.score import load_config
    cfg = load_config(CFG)
    # identical vacant residential-class lots; only the surroundings differ
    lot = {"land_use_code": "500", "improvement_value": 0, "land_value": 111_500,
           "has_structure": False, "il_ratio": 0.0, "area_sqft": 8_000}
    parcels = pd.DataFrame([
        {**lot, "parcel_id": "calvert", "zoning_code": "CC-P", "res_context_pct": 18.0},
        {**lot, "parcel_id": "paxton", "zoning_code": "SF-4-MH", "res_context_pct": 71.0},
    ])
    out = score_frame(parcels, None, cfg).set_index("parcel_id")
    # the commercial-strip lot's development potential is heavily damped by its
    # weak residential context; the real residential-street lot keeps full credit
    assert out.loc["calvert"]["development_score"] < 70
    assert out.loc["paxton"]["development_score"] == 100.0
    # both are empty parcels, so both read as "vacant lot" (the play, v2026.23)
    assert out.loc["calvert"]["use_classification"] == "vacant lot"
    assert out.loc["paxton"]["use_classification"] == "vacant lot"
    assert out.loc["paxton"]["opportunity_score"] > out.loc["calvert"]["opportunity_score"]


def test_agent_feedback_round_oakley(scored):
    """Cases a real agent flagged reviewing Oakley's top 10 (2026-07-27)."""
    import pandas as pd

    from scoring.score import load_config
    cfg = load_config(CFG)
    parcels = pd.DataFrame([
        # "That seems to be Deeper Roots" - an operating coffee shop: modest
        # building, valuable land, 0% residential context. Not a teardown.
        {"parcel_id": "deeper_roots", "land_use_code": "456", "zoning_code": "CC-M-T",
         "improvement_value": 9_160, "land_value": 62_900, "il_ratio": 0.146,
         "has_structure": True, "area_sqft": 8_059, "res_context_pct": 0.0},
        # "someone already built a new one there!" - carries a 2025 build permit
        {"parcel_id": "paxton_built", "land_use_code": "500", "zoning_code": "SF-4-MH",
         "improvement_value": 0, "land_value": 111_500, "il_ratio": 0.0,
         "has_structure": False, "area_sqft": 6_011, "res_context_pct": 75.0,
         "has_recent_permit": True},
        # the genuine find the agent praised: real residential street, no permit
        {"parcel_id": "brotherton", "land_use_code": "500", "zoning_code": "RM-1.2",
         "improvement_value": 0, "land_value": 100_000, "il_ratio": 0.0,
         "has_structure": False, "area_sqft": 10_000, "res_context_pct": 80.0},
    ])
    out = score_frame(parcels, None, cfg).set_index("parcel_id")
    # an operating business in a commercial strip is damped by context
    assert out.loc["deeper_roots"]["development_score"] < 40
    # an already-permitted parcel is flagged and pushed well below the genuine find
    assert "permit_issued" in out.loc["paxton_built"]["constraints"]["active"]
    assert out.loc["brotherton"]["opportunity_score"] > out.loc["paxton_built"]["opportunity_score"]
    assert out.loc["brotherton"]["opportunity_score"] > out.loc["deeper_roots"]["opportunity_score"]


def test_auto_commercial_zone_gets_no_housing_capacity(scored):
    from scoring.capacity import allowed_units, load_rules
    rules = load_rules()
    # 5 acres in CG-A (Jared Ellis Dr): zero housing capacity, not 146 units
    assert allowed_units("CG-A", 220_414, None, rules) == 0
    assert allowed_units("CC-A", 100_000, None, rules) == 0


def test_condo_unit_not_scored_as_developable_land(scored):
    import pandas as pd

    from scoring.score import load_config
    cfg = load_config(CFG)
    # a shared-footprint condo unit (Wind St): $0 improvement, condo class,
    # looks like a full acre of vacant land but owns only a slice
    unit = {"land_use_code": "550", "improvement_value": 0, "land_value": 150_000,
            "has_structure": False, "il_ratio": 0.0, "area_sqft": 43_764,
            "zoning_code": "RM-1.2"}
    parcels = pd.DataFrame([
        {**unit, "parcel_id": "condo", "is_condo_unit": True},
        {**unit, "parcel_id": "real_lot", "land_use_code": "500"},  # genuine vacant lot
    ])
    out = score_frame(parcels, None, cfg).set_index("parcel_id")
    # the condo unit gets no development-potential credit, and says why
    assert out.loc["condo"]["development_score"] == 0.0
    assert "condo_unit" in out.loc["condo"]["constraints"]["active"]
    # a real vacant lot is unaffected
    assert out.loc["real_lot"]["opportunity_score"] > out.loc["condo"]["opportunity_score"]


def test_complex_satellite_not_scored_as_vacant(scored):
    import pandas as pd

    from scoring.score import load_config
    cfg = load_config(CFG)
    base = {"land_use_code": "510", "improvement_value": 0, "land_value": 156_000,
            "has_structure": False, "il_ratio": 0.0, "area_sqft": 73_268}
    parcels = pd.DataFrame([
        {**base, "parcel_id": "satellite", "is_satellite": True},   # complex lawn
        {**base, "parcel_id": "teardown"},                          # real infill lot
    ])
    out = score_frame(parcels, None, cfg).set_index("parcel_id")
    # the satellite is not developable land and says so
    assert out.loc["satellite"]["development_score"] == 0.0
    assert "complex_satellite" in out.loc["satellite"]["constraints"]["active"]
    # the identical teardown lot is untouched — genuine opportunity
    assert out.loc["teardown"]["development_score"] == 100.0
    assert out.loc["teardown"]["opportunity_score"] > out.loc["satellite"]["opportunity_score"]


def test_subdivision_inventory_damped_unless_distressed(scored):
    import pandas as pd

    from scoring.score import load_config
    cfg = load_config(CFG)
    lot = {"land_use_code": "500", "improvement_value": 0, "land_value": 40_900,
           "has_structure": False, "il_ratio": 0.0, "area_sqft": 9_975}
    parcels = pd.DataFrame([
        {**lot, "parcel_id": "builder_lot", "is_subdivision_lot": True},
        {**lot, "parcel_id": "stalled_lot", "is_subdivision_lot": True},
        {**lot, "parcel_id": "plain_lot"},
    ])
    # a failing project, not one unpaid bill: heavy delinquency + a sheriff sale
    signals = pd.DataFrame([
        {"parcel_id": "stalled_lot", "signal_type": "tax_delinquent",
         "event_date": "2026-06-01", "severity": 40_000.0},
        {"parcel_id": "stalled_lot", "signal_type": "sheriff_sale",
         "event_date": "2026-08-05", "severity": 25_000.0},
    ])
    out = score_frame(parcels, signals, cfg).set_index("parcel_id")
    # an active builder's stock is damped and says why
    assert "subdivision_inventory" in out.loc["builder_lot"]["constraints"]["active"]
    assert out.loc["builder_lot"]["opportunity_score"] < out.loc["plain_lot"]["opportunity_score"]
    # a distressed subdivision keeps its score — that's a real acquisition
    assert "subdivision_inventory" not in out.loc["stalled_lot"]["constraints"]["active"]


def test_amenity_damped_unless_distressed(scored):
    import pandas as pd

    from scoring.score import load_config
    cfg = load_config(CFG)
    club = {"land_use_code": "461", "owner_name_raw": "FAIRWAY COUNTRY CLUB INC",
            "improvement_value": 200_000, "land_value": 4_000_000,
            "has_structure": True, "il_ratio": 0.05, "area_sqft": 5_000_000}
    parcels = pd.DataFrame([
        {**club, "parcel_id": "healthy_club"},
        {**club, "parcel_id": "failing_club"},
    ])
    signals = pd.DataFrame([  # a genuinely failing club: heavy debt + forced sale
        {"parcel_id": "failing_club", "signal_type": "tax_delinquent",
         "event_date": "2026-06-01", "severity": 900_000.0},
        {"parcel_id": "failing_club", "signal_type": "sheriff_sale",
         "event_date": "2026-08-05", "severity": 1_200_000.0},
    ])
    out = score_frame(parcels, signals, cfg).set_index("parcel_id")
    healthy, failing = out.loc["healthy_club"], out.loc["failing_club"]
    # a going concern is heavily damped and flagged
    assert "operating_amenity" in healthy["constraints"]["active"]
    # a distressed club gets the waiver: no flag, full score
    assert "operating_amenity" not in failing["constraints"]["active"]
    assert failing["opportunity_score"] > healthy["opportunity_score"] * 2


def test_owner_motivation_pillar(scored):
    """v2026.23: owner motivation is a scored pillar — a long-held / bank-owned
    teardown outranks the identical home held recently by an owner-occupant."""
    import datetime as dt

    import pandas as pd

    from scoring.score import load_config
    cfg = load_config(CFG)
    # a modest single-family home on a valuable lot (a teardown) in three
    # ownership situations; the physical parcel is identical
    home = {"land_use_code": "510", "improvement_value": 120_000, "land_value": 180_000,
            "has_structure": True, "il_ratio": 0.67, "area_sqft": 12_000, "home_sqft": 1800,
            "zoning_code": "SF-4"}
    old = (dt.date.today() - dt.timedelta(days=365 * 40)).isoformat()
    recent = (dt.date.today() - dt.timedelta(days=365 * 2)).isoformat()
    parcels = pd.DataFrame([
        {**home, "parcel_id": "longheld", "last_sale_date": old, "is_absentee": True},
        {**home, "parcel_id": "bank", "owner_name_raw": "FIFTH THIRD BANK", "last_sale_date": recent},
        {**home, "parcel_id": "recent", "last_sale_date": recent, "is_absentee": False},
    ])
    out = score_frame(parcels, None, cfg).set_index("parcel_id")
    # all three are teardowns (same development potential), but motivation separates them
    assert out.loc["longheld"]["development_score"] == out.loc["recent"]["development_score"] > 0
    assert out.loc["longheld"]["owner_motivation_score"] > out.loc["recent"]["owner_motivation_score"]
    assert out.loc["bank"]["owner_motivation_score"] > out.loc["recent"]["owner_motivation_score"]
    assert out.loc["longheld"]["opportunity_score"] > out.loc["recent"]["opportunity_score"]
    assert out.loc["longheld"]["use_classification"] == "teardown"


def test_severity_differentiates_delinquents(scored):
    _, _, scores = scored
    ranked = scores.set_index("parcel_id")
    # both improved + delinquent, but 55001830246 owes $8,319 on $24k land
    # (ratio 0.35) vs 19900410055 owing $6,178 on $32k land (ratio 0.19)
    heavier = ranked.loc["55001830246"]
    lighter = ranked.loc["19900410055"]
    assert heavier["distress_score"] > lighter["distress_score"]

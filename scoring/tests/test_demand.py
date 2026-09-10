"""Unit tests for the development-demand formula (v2, permit-driven)."""
from pathlib import Path

import pytest

from scoring.demand import demand_score
from scoring.score import load_config

CFG = Path(__file__).resolve().parents[1] / "config" / "scoring.yaml"


@pytest.fixture(scope="module")
def cfg():
    return {"demand": load_config(CFG)["demand"]}


def test_null_outside_permit_coverage(cfg):
    # townships have no city permit data -> component excluded, not zeroed
    assert demand_score(False, 0, None, cfg) is None
    assert demand_score(None, 5, 10, cfg) is None


def test_hot_infill_corridor_scores_high(cfg):
    # high build density + strong acceleration
    assert demand_score(True, 20.0, 200.0, cfg) == 100.0


def test_density_drives_score(cfg):
    quiet = demand_score(True, 1.0, 0.0, cfg)
    busy = demand_score(True, 12.0, 0.0, cfg)
    assert 0 < quiet < busy < 100


def test_trajectory_nudges(cfg):
    cooling = demand_score(True, 10.0, -100.0, cfg)
    accelerating = demand_score(True, 10.0, 100.0, cfg)
    assert cooling < accelerating


def test_no_prior_baseline_is_neutral(cfg):
    # a neighborhood with recent builds but no prior period -> neutral trajectory,
    # score still driven by density
    s = demand_score(True, 15.0, None, cfg)
    assert s is not None and s > 0

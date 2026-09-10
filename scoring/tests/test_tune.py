"""Unit tests for the tuner's pure functions (AUC + blend)."""
import numpy as np

from scoring.tune import auc, blend


def test_auc_perfect_and_reversed():
    scores = np.array([0.1, 0.2, 0.8, 0.9])
    labels = np.array([0, 0, 1, 1])
    assert auc(scores, labels) == 1.0                 # positives rank on top
    assert auc(-scores, labels) == 0.0                # perfectly wrong


def test_auc_ties_are_half():
    # all identical scores -> no discrimination -> 0.5 with average ranks
    scores = np.array([0.5, 0.5, 0.5, 0.5])
    labels = np.array([0, 1, 0, 1])
    assert abs(auc(scores, labels) - 0.5) < 1e-9


def test_auc_degenerate_labels():
    assert auc(np.array([0.1, 0.9]), np.array([1, 1])) == 0.5


def test_blend_renormalizes_over_present_components():
    # two parcels, 2 components; second component missing (mask 0) on parcel 1
    comp0 = np.array([[100.0, 0.0], [100.0, 0.0]])
    mask = np.array([[1.0, 0.0], [1.0, 1.0]])
    w = np.array([0.5, 0.5])
    out = blend(comp0, mask, w)
    # parcel 0: only comp A present -> 100; parcel 1: (0.5*100+0.5*0)/(1.0) = 50
    assert out[0] == 100.0
    assert out[1] == 50.0

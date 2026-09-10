"""Unit tests for the lead-audit label logic (Phase 0). Pure — no DB."""
from scoring.leadaudit import labels
from scoring.leadaudit.precision import _bucket


def test_valid_labels():
    assert labels.is_valid("real", None)[0]
    assert labels.is_valid("fake", "commercial_land")[0]
    assert labels.is_valid("fake", None)[0]      # reason optional
    assert labels.is_valid("maybe", None)[0]


def test_invalid_label_rejected():
    ok, msg = labels.is_valid("bogus", None)
    assert not ok and "label" in msg


def test_fake_with_unknown_reason_rejected():
    ok, msg = labels.is_valid("fake", "not_a_real_reason")
    assert not ok and "reason_code" in msg


def test_constraint_fake_reasons_are_a_subset_of_taxonomy():
    # every auto-seeded constraint reason must be a legal fake reason
    assert set(labels.CONSTRAINT_FAKE_REASONS) <= set(labels.FAKE_REASONS)


def test_constraint_reasons_exclude_soft_realworld_constraints():
    # flood/slope/historic discount a REAL parcel; they must not seed a 'fake'
    for soft in ("flood", "steep_slope", "historic", "cso"):
        assert soft not in labels.CONSTRAINT_FAKE_REASONS


def test_source_precedence_order():
    assert labels.SOURCE_RANK["review"] > labels.SOURCE_RANK["agent"] > labels.SOURCE_RANK["history"]


def test_precision_bucket_math():
    rows = [{"label": "real"}, {"label": "real"}, {"label": "fake"},
            {"label": None}, {"label": None}]
    b = _bucket(rows)
    assert b["n"] == 5 and b["real"] == 2 and b["fake"] == 1 and b["unknown"] == 2
    # precision counts only judged leads: 2 real of 3 judged
    assert b["precision"] == 66.7
    assert b["reviewed_pct"] == 60.0


def test_precision_bucket_all_unknown_is_none():
    b = _bucket([{"label": None}, {"label": None}])
    assert b["precision"] is None and b["reviewed_pct"] == 0.0

"""Unit tests for the auditor's pure confidence roll-up."""
from scoring.audit import confidence_level


def test_clean_when_no_flags():
    assert confidence_level([], []) == "clean"


def test_caution_dominates():
    assert confidence_level(["bulk_sale", "no_market_sale"],
                            ["caution", "review"]) == "caution"


def test_review_when_only_review_flags():
    assert confidence_level(["portfolio_owner"], ["review"]) == "review"

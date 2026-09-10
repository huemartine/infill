"""Unit tests for the pure alert-diff logic."""
from scoring import alerts
from scoring.alerts import diff_alerts

THRESHOLD = 70.0
JUMP = 15.0


def _diff(prev, new):
    return diff_alerts(prev, new, THRESHOLD, JUMP)


def test_new_high_on_threshold_cross():
    alerts = _diff({"a": 60.0}, {"a": 75.0})
    assert [a["alert_type"] for a in alerts] == ["new_high"]
    assert alerts[0]["old_score"] == 60.0 and alerts[0]["new_score"] == 75.0


def test_new_parcel_above_threshold_alerts():
    alerts = _diff({}, {"a": 80.0})
    assert alerts[0]["alert_type"] == "new_high"
    assert alerts[0]["old_score"] is None


def test_no_alert_when_already_high():
    assert _diff({"a": 85.0}, {"a": 90.0}) == []


def test_score_jump_below_threshold():
    # 40 -> 60: big jump into the interesting range (>= threshold - 20)
    alerts = _diff({"a": 40.0}, {"a": 60.0})
    assert [a["alert_type"] for a in alerts] == ["score_jump"]


def test_small_moves_and_drops_are_silent():
    assert _diff({"a": 60.0, "b": 80.0}, {"a": 65.0, "b": 50.0}) == []


def test_jump_to_uninteresting_level_is_silent():
    assert _diff({"a": 5.0}, {"a": 30.0}) == []


# --- delivery-time re-validation -------------------------------------------
# An alert is a claim about a parcel. Between queueing and delivery the model
# can change its mind, so the claim is re-checked against the live score.

def _pending(**over):
    p = {"id": 1, "parcel_id": "x", "alert_type": "new_high", "old_score": 60.0,
         "new_score": 75.6, "current_score": 75.6, "age_days": 0.5}
    p.update(over)
    return p


def test_fresh_alert_that_still_holds_is_delivered():
    assert alerts._is_stale(_pending(), threshold=70) is False


def test_alert_older_than_max_age_is_suppressed():
    assert alerts._is_stale(_pending(age_days=alerts.MAX_AGE_DAYS + 0.1), threshold=70)


def test_alert_is_suppressed_when_a_later_rule_demoted_the_parcel():
    """The real case: queued at 75.6 on 7/14, demoted to 4.19 by v2026.18's
    commercial-context and permit guardrails, still pending on 7/28."""
    assert alerts._is_stale(_pending(current_score=4.19), threshold=70)


def test_alert_is_suppressed_when_score_drifted_materially():
    drifted = 75.6 - (alerts.DRIFT_TOLERANCE + 1)
    assert alerts._is_stale(_pending(current_score=drifted), threshold=50)


def test_small_drift_still_delivers():
    assert alerts._is_stale(
        _pending(current_score=75.6 - (alerts.DRIFT_TOLERANCE - 1)), threshold=50) is False


def test_unscored_parcel_is_suppressed():
    assert alerts._is_stale(_pending(current_score=None), threshold=70)


def test_delivered_line_reports_the_current_score_not_the_queued_one():
    line = alerts._format_line(_pending(new_score=75.6, current_score=71.2))
    assert "71.2" in line and "75.6" not in line

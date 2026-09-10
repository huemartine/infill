"""Score-change alerting. Pure diff logic + persistence + delivery.

The diff runs inside the scoring pass (scoring.run.main reads prior scores
before writing new ones), so no score-history table is needed. Delivery is
decoupled: alerts land in the alerts table with notified_at NULL; the
orchestration layer flushes them to Slack (webhook) or the log and marks them
notified — at-least-once, replayable.
"""
from __future__ import annotations

import json
from typing import Optional


def diff_alerts(prev: dict[str, float], new: dict[str, float],
                threshold: float, min_jump: float) -> list[dict]:
    """Pure. `prev`/`new` map parcel_id -> opportunity_score.

    - new_high: score crossed `threshold` upward (or a new parcel arrived above it).
    - score_jump: score rose by >= `min_jump` to a level worth a look (>= threshold - 20),
      without necessarily crossing the threshold.
    """
    out = []
    for pid, score in new.items():
        old = prev.get(pid)
        if score >= threshold and (old is None or old < threshold):
            out.append({"parcel_id": pid, "alert_type": "new_high",
                        "old_score": old, "new_score": score})
        elif old is not None and score - old >= min_jump and score >= threshold - 20:
            out.append({"parcel_id": pid, "alert_type": "score_jump",
                        "old_score": old, "new_score": score})
    return out


def persist_alerts(alerts: list[dict]) -> int:
    from sqlalchemy import text

    from ingestion.load import _engine
    if not alerts:
        return 0
    stmt = text("""
        INSERT INTO alerts (parcel_id, alert_type, old_score, new_score, details)
        VALUES (:parcel_id, :alert_type, :old_score, :new_score, CAST(:details AS jsonb))
    """)
    rows = [{**a, "details": json.dumps(a.get("details") or {})} for a in alerts]
    with _engine().begin() as conn:
        conn.execute(stmt, rows)
    return len(rows)


MAX_AGE_DAYS = 7          # older than this isn't news, it's archaeology
DRIFT_TOLERANCE = 5.0     # score moved this much since queueing => re-check it


def flush_notifications(webhook_url: Optional[str] = None, limit: int = 50,
                        threshold: Optional[float] = None) -> int:
    """Deliver unnotified alerts (oldest first) to Slack when a webhook is
    configured, else to stdout. Marks them notified either way so the queue
    drains; failures leave rows unnotified for the next flush.

    Alerts are **re-validated against the live score at delivery time**, because
    the queue and the truth drift apart. A parcel can be queued at 75.6 and then
    correctly demoted to 4.2 by a later scoring rule (this happened for real:
    v2026.18's commercial-context and permit guardrails demoted parcels that had
    alerts sitting in the queue from two weeks earlier). Delivering the frozen
    number would announce a lead that the model no longer stands behind, so an
    alert is suppressed — marked notified, never sent — when it is stale, when
    the parcel no longer clears the threshold, or when the score has drifted.
    Surviving alerts report the CURRENT score."""
    from sqlalchemy import text

    from config import settings
    from ingestion.load import _engine

    if threshold is None:
        threshold = settings.alert_score_threshold
    eng = _engine()
    with eng.connect() as conn:
        pending = conn.execute(text("""
            SELECT a.id, a.parcel_id, a.alert_type, a.old_score, a.new_score,
                   m.address, s.use_classification,
                   s.opportunity_score AS current_score,
                   extract(epoch FROM now() - a.created_at) / 86400.0 AS age_days
            FROM alerts a
            JOIN parcel_master m USING (parcel_id)
            LEFT JOIN parcel_scores s USING (parcel_id)
            WHERE a.notified_at IS NULL
            ORDER BY a.created_at
            LIMIT :n
        """), {"n": limit}).mappings().all()
    if not pending:
        return 0

    send, suppress = [], []
    for p in pending:
        (suppress if _is_stale(dict(p), threshold) else send).append(dict(p))

    delivered = _send([_format_line(p) for p in send], webhook_url) if send else True
    if delivered:
        # suppressed alerts are marked too: they must never be retried, or the
        # queue never drains and the same wrong number returns tomorrow
        ids = [p["id"] for p in send] + [p["id"] for p in suppress]
        with eng.begin() as conn:
            conn.execute(text("UPDATE alerts SET notified_at = now() WHERE id = ANY(:ids)"),
                         {"ids": ids})
    if suppress:
        print(f"suppressed {len(suppress)} stale/invalidated alerts "
              f"(of {len(pending)} pending)", flush=True)
    return len(send) if delivered else 0


def _is_stale(p: dict, threshold: float) -> bool:
    """True when an alert should be dropped rather than delivered."""
    if (p.get("age_days") or 0) > MAX_AGE_DAYS:
        return True
    current = p.get("current_score")
    if current is None:                       # parcel no longer scored at all
        return True
    current = float(current)
    if current < threshold:                   # a later rule demoted it
        return True
    return abs(current - float(p["new_score"])) > DRIFT_TOLERANCE


def _format_line(p: dict) -> str:
    old = f"{float(p['old_score']):.1f}" if p["old_score"] is not None else "new"
    return (f"[{p['alert_type']}] {p['parcel_id']} {p.get('address') or '(no address)'}: "
            f"{old} -> {float(p['current_score']):.1f} ({p.get('use_classification') or '?'})")


def _send(lines: list[str], webhook_url: Optional[str]) -> bool:
    text_block = "High-score parcel alerts:\n" + "\n".join(lines)
    if webhook_url:
        import httpx
        try:
            resp = httpx.post(webhook_url, json={"text": text_block}, timeout=15)
            return resp.status_code < 300
        except httpx.HTTPError:
            return False
    print(text_block, flush=True)
    return True

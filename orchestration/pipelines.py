"""Cadence-bundled pipelines (Section 1.3 refresh table). Plain Python — no
orchestrator required — so the same functions run under Prefect (flows.py), the
stdlib scheduler (scheduler.py), Task Scheduler / cron, or by hand:

    python -m orchestration.pipelines --tier daily

Tier bundles:
    daily     code violations (fresh events are the platform's pulse) and
              building permits (the availability guard: a permit means someone
              is already building, so the lot is no longer an opportunity)
    weekly    sheriff sales (+ land bank), i.e. the auction calendar
    quarterly CAGIS full parcel refresh

Every tier ends with a scoring pass (which queues alert diffs) and a
notification flush. Sources fail independently: one broken feed doesn't block
the others or the re-score. Every step writes an `ingest_runs` row so
`orchestration.freshness` can tell an operator what has gone stale.
"""
from __future__ import annotations

import argparse
import traceback
from datetime import datetime, timezone

# extractor-backed sources, run through ingestion.run
TIERS: dict[str, list[str]] = {
    "daily": ["socrata_code_violations"],
    "weekly": ["sheriff_sale"],
    "quarterly": ["cagis"],
}

# non-extractor builders (plain `build()` callables) that run before scoring
BUILDERS: dict[str, tuple[str, str]] = {
    "overlays": ("ingestion.overlays", "build"),              # flood/zoning/etc. + apply
    "regions": ("ingestion.overlays", "apply_neighborhood"),  # municipality + SNA labels
    "home_size": ("ingestion.footprints", "build"),           # building footprints -> home_sqft
    "condition": ("ingestion.condition", "build"),            # condition_gap (needs home_size)
    "infrastructure": ("ingestion.infrastructure", "build"),  # septic / water service
    "topography": ("ingestion.topography", "build"),          # per-parcel slope + buildable area
    "permits": ("ingestion.permits", "build"),
    "landbank": ("ingestion.landbank", "build"),
    "derived": ("ingestion.derive", "build"),      # satellites + subdivision lots
    "assemblage": ("scoring.assemblage", "build"),
    "demand": ("scoring.demand", "build"),
}

# which builders each tier runs, in order. `permits` is daily on purpose: it is
# what suppresses lots that someone has already broken ground on, and a stale
# permit feed re-introduces exactly the false positive a pilot agent caught
# (3917 Paxton, "someone already built a new one there").
DERIVED: dict[str, list[str]] = {
    "daily": ["permits", "assemblage", "demand"],
    "weekly": ["permits", "landbank", "derived", "assemblage", "demand"],
    # quarterly re-ingests CAGIS parcels: reload constraint overlays (flood/zoning)
    # and re-label regions before scoring, so new/renumbered parcels get their
    # floodway knockout, zoning_code, and region instead of silently going NULL
    "quarterly": ["overlays", "regions", "home_size", "condition", "infrastructure", "topography",
                  "permits", "landbank", "derived", "assemblage", "demand"],
}


def refresh(tier: str) -> dict:
    if tier not in TIERS:
        raise SystemExit(f"unknown tier {tier!r}; known: {list(TIERS)}")
    from config import settings
    from ingestion.run import run as run_ingest
    from scoring.alerts import flush_notifications
    from scoring.run import main as run_scoring

    started = _now()
    results: dict[str, object] = {"tier": tier}
    for source in TIERS[tier]:
        try:
            results[source] = run_ingest(source)
        except Exception as exc:  # a broken feed must not block the rest
            traceback.print_exc()
            results[source] = {"error": str(exc)}

    for name in DERIVED.get(tier, []):
        results[name] = _run_builder(name)

    try:
        run_scoring()
        results["scored"] = True
    except Exception as exc:
        traceback.print_exc()
        results["scored"] = False
        results["scoring_error"] = str(exc)

    if results.get("scored"):
        results["audit"] = _run_builder_fn(
            "audit", "scoring.audit", "build")  # advisory data-plausibility flags

    results["notified"] = flush_notifications(settings.alert_slack_webhook or None)
    ok = results.get("scored") and not any(
        isinstance(v, dict) and "error" in v for v in results.values())
    _record(f"refresh:{tier}", started, ok,
            None if ok else "one or more steps failed")
    print(f"refresh({tier}) done: {results}", flush=True)
    return results


def _run_builder(name: str) -> dict:
    module, fn = BUILDERS[name]
    return _run_builder_fn(name, module, fn)


def _run_builder_fn(name: str, module: str, fn: str) -> dict:
    """Run one build() step, timing it and recording an ingest_runs row either
    way. Failures are returned, never raised: the tier must keep going."""
    from importlib import import_module

    started = _now()
    try:
        out = getattr(import_module(module), fn)()
        _record(name, started, True, None, _rowcount(out))
        return out if isinstance(out, dict) else {"result": out}
    except Exception as exc:
        traceback.print_exc()
        _record(name, started, False, str(exc))
        return {"error": str(exc)}


def _rowcount(out) -> int | None:
    """Best-effort row count from a builder's summary dict, for ingest_runs."""
    if not isinstance(out, dict):
        return None
    for key in ("permits", "rows", "count", "parcels", "assemblages"):
        if isinstance(out.get(key), int):
            return out[key]
    return next((v for v in out.values() if isinstance(v, int)), None)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _record(source: str, started: datetime, ok: bool, error: str | None,
            rows: int | None = None) -> None:
    """Observability row. Never let bookkeeping break a refresh."""
    try:
        from sqlalchemy import text

        from ingestion.load import _engine
        with _engine().begin() as conn:
            conn.execute(text("""
                INSERT INTO ingest_runs (source, started_at, finished_at,
                                         row_count, status, error)
                VALUES (:s, :b, :f, :n, :st, :e)
            """), {"s": source, "b": started, "f": _now(), "n": rows,
                   "st": "success" if ok else "failed", "e": error})
    except Exception:
        traceback.print_exc()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tier", required=True, choices=list(TIERS))
    refresh(ap.parse_args().tier)


if __name__ == "__main__":
    main()

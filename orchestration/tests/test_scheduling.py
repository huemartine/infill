"""Guards on the refresh schedule and the freshness monitor. These are cheap
invariants that catch the failure mode we actually hit: a data source that the
scorer depends on quietly belonging to no tier, and therefore never refreshing.
"""
from __future__ import annotations

from orchestration import freshness, pipelines


def test_every_extractor_source_is_in_a_tier():
    from ingestion.run import EXTRACTORS

    scheduled = {s for sources in pipelines.TIERS.values() for s in sources}
    assert set(EXTRACTORS) <= scheduled, (
        f"unscheduled extractors: {set(EXTRACTORS) - scheduled}")


def test_permits_refresh_daily():
    """A recent build permit is what suppresses a lot someone has already broken
    ground on (v2026.18). If permits go stale we re-surface taken lots, which is
    the exact false positive a pilot agent caught at 3917 Paxton."""
    assert "permits" in pipelines.DERIVED["daily"]


def test_scoring_inputs_rebuild_before_every_score():
    """assemblage + demand feed the composite, so every tier that re-scores must
    rebuild them first, not just the weekly one."""
    for tier in pipelines.TIERS:
        assert {"assemblage", "demand"} <= set(pipelines.DERIVED[tier]), tier


def test_derived_names_resolve_to_real_builders():
    for names in pipelines.DERIVED.values():
        for name in names:
            assert name in pipelines.BUILDERS, name


def test_every_scheduled_step_has_a_freshness_budget():
    """Anything we schedule, we monitor — otherwise a dead cron entry is
    invisible until an agent acts on stale data."""
    for tier, sources in pipelines.TIERS.items():
        for source in sources:
            assert source in freshness.BUDGETS, source
    for name in pipelines.DERIVED["daily"]:
        if name in ("assemblage", "demand"):
            continue  # internal rebuilds, not external feeds
        assert name in freshness.BUDGETS, name


def test_budgets_absorb_one_missed_run():
    """Budget must exceed the cadence, or a single skipped run cries wolf."""
    cadence_hours = {"socrata_code_violations": 24, "permits": 24,
                     "sheriff_sale": 7 * 24, "landbank": 7 * 24,
                     "cagis": 91 * 24, "refresh:daily": 24,
                     "refresh:weekly": 7 * 24}
    for source, (budget, _label) in freshness.BUDGETS.items():
        assert budget > cadence_hours[source], source


def test_rowcount_extracts_a_count_from_builder_summaries():
    assert pipelines._rowcount({"permits": 1555, "with_pin": 1400}) == 1555
    assert pipelines._rowcount({"clusters": 12}) == 12
    assert pipelines._rowcount({"error": "boom"}) is None
    assert pipelines._rowcount(None) is None

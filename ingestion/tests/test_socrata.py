"""Socrata code-violations extractor tests against a frozen real sample
(30 recent enforcement records from cncm-znd6). Pure normalize — no DB/network."""
import json
from pathlib import Path

import pytest

from ingestion.base import RawBatch, Resource
from ingestion.extractors.socrata import SocrataCodeViolationsExtractor

FIXTURE = Path(__file__).parent / "fixtures" / "socrata_violations_sample.json"


@pytest.fixture(scope="module")
def normalized():
    records = json.loads(FIXTURE.read_text())
    ext = SocrataCodeViolationsExtractor()
    batch = RawBatch(
        resource=Resource("socrata_code_violations", "x", "http://fixture", "socrata"),
        records=records,
    )
    return ext.normalize(batch), records


def test_normalize_emits_signal_rows(normalized):
    df, records = normalized
    assert len(df) == len(records)  # fixture pre-filtered; nothing else dropped
    assert (df["signal_type"] == "code_violation").all()
    assert df["raw_ref"].notna().all() and df["raw_ref"].is_unique
    assert df["event_date"].str.match(r"\d{4}-\d{2}-\d{2}").all()


def test_all_rows_resolvable_spatially(normalized):
    df, _ = normalized
    # verified live: 100% of code-enforcement rows carry coordinates
    assert df["lon"].notna().all() and df["lat"].notna().all()
    # Cincinnati bounding sanity
    assert df["lon"].between(-84.9, -84.2).all()
    assert df["lat"].between(38.9, 39.4).all()


def test_severity_by_enforcement_stage(normalized):
    df, _ = normalized
    assert df["severity"].between(1.0, 2.0).all()


def test_excluded_statuses_never_present(normalized):
    df, _ = normalized
    assert not df["status"].isin(
        ["Closed - No Violation", "Closed - Duplicate Complaint",
         "Duplicate Case", "Case Voided"]).any()

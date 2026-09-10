"""Sheriff-sale extractor tests against a frozen real loader response
(6 auctions from 2026-07-15). Pure parse — no network/DB."""
import json
from pathlib import Path

import pytest

from ingestion.base import RawBatch, Resource
from ingestion.extractors.sheriff import (
    SheriffSaleExtractor,
    sheriff_parcel_to_canonical,
)

FIXTURE = Path(__file__).parent / "fixtures" / "sheriff_sale_sample.json"


@pytest.fixture(scope="module")
def normalized():
    fx = json.loads(FIXTURE.read_text())
    page = {"auction_date": fx["auction_date"],
            "retHTML": fx["response"]["retHTML"],
            "rlist": fx["response"]["rlist"].split(",")}
    ext = SheriffSaleExtractor()
    batch = RawBatch(
        resource=Resource("sheriff_sale", "auction_20260715", "http://fixture", "scrape"),
        records=[page])
    return ext.normalize(batch)


def test_parcel_format_mapping():
    # verified against parcel_master: 3181 Epworth Ave
    assert sheriff_parcel_to_canonical("211-69-41") == "21100690041"
    assert sheriff_parcel_to_canonical("550-145-329") == "55001450329"
    assert sheriff_parcel_to_canonical("garbage") is None
    assert sheriff_parcel_to_canonical(None) is None


def test_normalize_parses_all_auctions(normalized):
    df = normalized
    assert len(df) == 6
    assert (df["signal_type"] == "sheriff_sale").all()
    assert (df["event_date"] == "2026-07-15").all()
    assert df["raw_ref"].str.match(r"^A\d{7}$").all()   # base case numbers
    assert df["parcel_id"].str.match(r"^\d{11}$").all()  # canonical form


def test_epworth_ave_case(normalized):
    df = normalized.set_index("parcel_id")
    row = df.loc["21100690041"]
    assert row["raw_ref"] == "A2501903"
    assert row["severity"] == pytest.approx(218000.0)   # opening bid
    assert row["appraised"] == pytest.approx(327000.0)
    assert "EPWORTH" in row["address"]
    assert row["status"] == "ACTIVE"

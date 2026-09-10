"""Cincinnati code-enforcement extractor (Section 5.x) — the archetype for all
dated-event feeds. Socrata SoQL over data.cincinnati-oh.gov dataset cncm-znd6
(verified live: exclusively CODE ENFORCEMENT records, 100% carry lat/lon).

Design points:
  - **Watermark incremental**: extract() pulls events with entered_date > since;
    the runner derives `since` from the newest stored event (minus an overlap
    buffer — the (source, raw_ref) upsert makes overlap harmless).
  - **No parcel key in source** — records resolve spatially (point-in-polygon
    against parcel_master.geom) in ingestion.resolve, not here. normalize()
    emits lon/lat + normalized address for the fallback tier.
  - **Noise filtered server-side**: no-violation closures, duplicates, and voided
    cases never leave Socrata.
  - App token (X-App-Token) raises rate limits but is optional.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Iterator, Optional

import pandas as pd

from ..base import Extractor, RawBatch, Resource
from ..common import HttpClient, normalize_address, paginate_offset

_BASE = "https://data.cincinnati-oh.gov"
_DATASET = "cncm-znd6"
_PAGE_SIZE = 10_000
_INITIAL_LOOKBACK_DAYS = 730  # ~24 months: beyond ~1.3 half-lives the decayed weight is marginal

# statuses that are not evidence of distress
_EXCLUDE_STATUS = ("CLOS-NO", "CLOS-DUP", "DUPLICAT", "VOID")

# forward-looking severity by enforcement stage (unused by scoring until a
# severity_scaling entry for code_violation exists; stored now for free)
_STATUS_SEVERITY = {
    "REFABAND": 2.0,   # referred to Abandoned Property Program
    "ASSESSED": 2.0,   # abatement costs assessed on the tax bill
    "ORDERS": 1.5,
    "FINLNOTC": 1.5,
    "REISSUE": 1.5,
}


class SocrataCodeViolationsExtractor(Extractor):
    source_id = "socrata_code_violations"
    target = "signals"  # runner routes to parcel_signals via spatial resolution

    def __init__(self, base: str = _BASE, dataset: str = _DATASET,
                 app_token: Optional[str] = None):
        from config import settings
        self._base = base.rstrip("/")
        self._dataset = dataset
        headers = {"User-Agent": "infill-platform/0.1"}
        token = app_token or settings.socrata_app_token
        if token:
            headers["X-App-Token"] = token
        self._http = HttpClient(min_interval_s=0.2, headers=headers)

    def discover(self) -> list[Resource]:
        """Confirm the dataset id is alive via its metadata endpoint."""
        meta = self._http.get(f"{self._base}/api/views/{self._dataset}.json").json()
        if not meta.get("id"):
            raise RuntimeError(f"Socrata dataset {self._dataset} not found at {self._base}")
        return [Resource(
            source_id=self.source_id,
            name=meta.get("name", self._dataset),
            url=f"{self._base}/resource/{self._dataset}.json",
            kind="socrata",
        )]

    def extract(self, resource: Resource, since: Optional[datetime]) -> Iterator[RawBatch]:
        since = since or datetime.now(timezone.utc) - timedelta(days=_INITIAL_LOOKBACK_DAYS)
        statuses = ",".join(f"'{s}'" for s in _EXCLUDE_STATUS)
        base_params = {
            "$where": (f"entered_date > '{since:%Y-%m-%dT%H:%M:%S}' "
                       f"AND data_status NOT IN ({statuses})"),
            "$order": "entered_date, number_key",  # stable order for offset paging
        }
        for page, records in enumerate(paginate_offset(
            self._http, resource.url, base_params,
            offset_key="$offset", count_key="$limit",
            page_size=_PAGE_SIZE,
            records_path=lambda body: body,  # Socrata returns a bare JSON array
        )):
            yield RawBatch(resource=resource, records=records, page=page)

    def normalize(self, batch: RawBatch) -> pd.DataFrame:
        rows = []
        for r in batch.records:
            key = r.get("number_key")
            event_date = (r.get("entered_date") or "")[:10] or None
            if not key or not event_date:
                continue
            rows.append({
                "raw_ref": key,
                "signal_type": "code_violation",
                "event_date": event_date,
                "status": r.get("data_status_display") or r.get("data_status"),
                "severity": _STATUS_SEVERITY.get(r.get("data_status"), 1.0),
                "source": self.source_id,
                "lon": _f(r.get("longitude")),
                "lat": _f(r.get("latitude")),
                "address_norm": normalize_address(r.get("full_address")),
                "detail": r.get("comp_type_desc"),
            })
        return pd.DataFrame(rows)


def _f(v) -> Optional[float]:
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None

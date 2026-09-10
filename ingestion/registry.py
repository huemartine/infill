"""Source catalog as code (Section 5). One entry per data source: its extractor
type, refresh cadence, and the discovery seed. [VERIFY] entries carry live
resource ids the spec flagged to confirm against the published catalog before
relying on them; the extractors resolve real endpoints at runtime via discover().
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SourceSpec:
    source_id: str
    kind: str          # arcgis_feature | socrata | csv | scrape | regrid
    cadence: str       # quarterly | monthly | weekly | daily
    target: str        # parcel_master | parcel_signals | scoring
    seed: str          # discovery seed: service directory, catalog api, or portal
    verify: bool = False


# Section 5 — Data sources catalog. Cadences from §1.3.
CATALOG: dict[str, SourceSpec] = {
    "cagis": SourceSpec(
        source_id="cagis",
        kind="arcgis_feature",
        cadence="quarterly",
        target="parcel_master",
        seed="https://cagisportal.opendata.arcgis.com",  # ArcGIS Open Data Hub
    ),
    "auditor": SourceSpec(
        source_id="auditor",
        kind="csv",
        cadence="monthly",
        target="parcel_master",
        seed="https://hamiltoncountyauditor.org",  # bulk parcel CSV / extract [VERIFY path]
        verify=True,
    ),
    "socrata_code_violations": SourceSpec(
        source_id="socrata_code_violations",
        kind="socrata",
        cadence="daily",
        target="parcel_signals",
        seed="https://data.cincinnati-oh.gov/resource/cncm-znd6.json",
    ),
    "socrata_permits": SourceSpec(
        source_id="socrata_permits",
        kind="socrata",
        cadence="daily",
        target="parcel_signals",
        seed="https://data.cincinnati-oh.gov",  # dataset id [VERIFY] via catalog search "permits"
        verify=True,
    ),
    "landbank": SourceSpec(
        source_id="landbank",
        kind="scrape",
        cadence="weekly",
        target="parcel_signals",
        seed="https://landbank.cincinnatilandbank.org",  # The Port; respect robots/throttle
    ),
    "sheriff_sale": SourceSpec(
        source_id="sheriff_sale",
        kind="scrape",
        cadence="weekly",
        target="parcel_signals",
        seed="https://hamilton.sheriffsaleauction.ohio.gov",  # RealAuction; browser UA required
    ),
    "connected_communities": SourceSpec(
        source_id="connected_communities",
        kind="arcgis_feature",
        cadence="static",
        target="parcel_master",  # cc_zone overlay
        seed="https://cincinnati-oh.gov/planning/connected-communities",
        verify=True,
    ),
    "fema_flood": SourceSpec(
        source_id="fema_flood",
        kind="arcgis_feature",
        cadence="static",
        target="parcel_master",  # flood_flag + floodway knockout (ingestion.overlays)
        seed="https://hazards.fema.gov/arcgis/rest/services/public/NFHL/MapServer/28",
    ),
    "hillside": SourceSpec(
        source_id="hillside",
        kind="arcgis_feature",
        cadence="static",
        target="parcel_master",  # slope_flag (ingestion.overlays)
        seed="https://services.arcgis.com/JyZag7oO4NteHGiq/arcgis/rest/services/Open_Data/FeatureServer/6",
    ),
    "historic": SourceSpec(
        source_id="historic",
        kind="arcgis_feature",
        cadence="static",
        target="parcel_master",  # historic_flag (ingestion.overlays)
        seed="https://services.arcgis.com/JyZag7oO4NteHGiq/arcgis/rest/services/Open_Data/FeatureServer/7",
    ),
}

"""What a parcel's zoning district actually allows, in plain English.

Reads the transcribed district rules (scoring/config/zoning_districts.yaml) and
renders the buildable envelope an agent needs when sizing up a lot: minimum lot
size and width, the required yards, and the height cap — plus the source so a
number can be traced back to the ordinance.

Deliberately does NOT compute a unit yield. Lot count depends on topography,
access, utilities, market appetite and what the municipality will actually
approve; the agent judges that. This module's job is to put the rules in front of
them accurately.

Nothing is guessed: a district that has not been transcribed from its official
code returns `known: False` rather than an invented envelope.
"""
from __future__ import annotations

import functools
import re
from pathlib import Path
from typing import Optional

import yaml

_CONFIG = Path(__file__).with_name("config") / "zoning_districts.yaml"

# Which jurisdiction's rulebook applies. `municipality` is the authoritative
# answer — it comes from the CAGIS address-jurisdiction layer (migration 021) and
# is the body that actually writes the zoning code. Prefer it.
_MUNICIPALITY_JURISDICTION = {
    "Cincinnati": "cincinnati",
    "Blue Ash": "blue_ash",
    "Symmes Township": "symmes",
    "Montgomery": "montgomery",
}

# Fallback for callers that only have the region key. Hyde Park and Oakley are
# Cincinnati NEIGHBOURHOODS, not municipalities, so the region key alone can't
# tell you whose code applies without this table — which is exactly why the
# municipality column is preferred above.
_REGION_JURISDICTION = {
    "Blue Ash": "blue_ash",
    "Symmes Township": "symmes",
    "Montgomery": "montgomery",
    "Hyde Park": "cincinnati",
    "Oakley": "cincinnati",
}


@functools.lru_cache(maxsize=1)
def load_districts(path: str | Path | None = None) -> dict:
    p = Path(path) if path else _CONFIG
    return yaml.safe_load(p.read_text(encoding="utf-8"))


def _lookup(code: str, districts: dict) -> Optional[tuple[str, dict, list[str]]]:
    """Match a county zoning_code to a transcribed district.

    County codes append designations the ordinance table doesn't use ("SF-6-MH",
    "RM-1.2-T", "A CUP", "A_HOD"), so split on every separator, match the longest
    prefix that names a district, and hand the trailing segments back as overlay
    candidates. Those trailing segments are not noise: "-MH" on an SF-6 lot is
    what makes a fourplex legal there.

    Returns (district_key, spec, leftover_segments) or None.
    """
    if not code:
        return None
    parts = [p for p in re.split(r"[-_\s]+", str(code).strip().upper()) if p]
    by_key = {name.upper(): (name, spec) for name, spec in districts.items()}
    for stop in range(len(parts), 0, -1):
        hit = by_key.get("-".join(parts[:stop]))
        if hit:
            return hit[0], hit[1], parts[stop:]
    return None


def describe(zoning_code: Optional[str], neighborhood: Optional[str],
             cfg: dict | None = None, municipality: Optional[str] = None) -> dict:
    """The zoning read for one parcel.

    Returns {known, district, name, jurisdiction, uses, rules[], overlays[],
    summary, citation, url, notes[]}. `known` is False when we have no
    transcribed rules for that district — the UI says so plainly instead of
    implying none exist.

    Pass `municipality` (parcel_master.municipality) when you have it: it names
    the body that actually wrote the code, so it resolves every Cincinnati
    neighbourhood, not just the ones listed in _REGION_JURISDICTION.
    """
    cfg = cfg or load_districts()
    juris_key = (_MUNICIPALITY_JURISDICTION.get((municipality or "").strip())
                 or _REGION_JURISDICTION.get(neighborhood or ""))
    juris = (cfg.get("jurisdictions") or {}).get(juris_key or "")
    if not juris:
        return {"known": False, "district": zoning_code,
                "reason": ("no zoning rules transcribed for this area yet"
                           if zoning_code else "no zoning district on record for this parcel")}
    hit = _lookup(zoning_code, juris.get("districts") or {})
    if not hit:
        return {"known": False, "district": zoning_code,
                "jurisdiction": juris.get("name"),
                "reason": f"district {zoning_code!r} is not transcribed from the {juris.get('name')} code yet"}

    key, d, extra = hit
    rules: list[dict] = []

    def add(label, value, unit=""):
        if value is not None:
            rules.append({"label": label, "value": f"{value:,}{unit}" if isinstance(value, (int, float)) else str(value)})

    area = d.get("min_lot_area_sqft")
    if area is not None:
        add("Minimum lot size",
            f"{area:,} sqft" + (" per dwelling unit" if d.get("area_is_per_unit") else ""))
    add("Minimum lot width", d.get("min_lot_width_ft"), " ft")
    add("Front setback", d.get("front_yard_ft"), " ft")
    # Some codes state side yards as a min/total pair — one side may be as narrow
    # as the minimum only if the other makes up the difference.
    side, side_total = d.get("side_yard_ft"), d.get("side_yard_total_ft")
    if side is not None:
        add("Side setback",
            f"{side} ft on one side, {side_total} ft both sides combined"
            if side_total else f"{side} ft each side")
    add("Rear setback", d.get("rear_yard_ft"), " ft")
    if d.get("max_height_ft") is not None:
        stories = d.get("max_stories")
        add("Maximum height",
            f"{d['max_height_ft']} ft" + (f" ({stories} stories)" if stories else ""))

    notes = [n for n in (d.get("note"), juris.get("front_yard_note"),
                         juris.get("width_note")) if n]

    # Designations the county appends to the base code. These ADD to the district
    # (Cincinnati's Connected Communities suffixes make 2-4 family housing legal
    # by right and drop the parking requirement), so they are reported alongside
    # rather than folded into the envelope above.
    defined = juris.get("overlays") or {}
    overlays = [{"code": seg, "name": o.get("name") or seg,
                 "effect": o.get("effect"), "citation": o.get("citation")}
                for seg in extra for o in [defined.get(seg) or defined.get(seg.upper())]
                if o]

    # a one-line summary an agent can read at a glance
    bits = []
    if area:
        bits.append(f"{area:,} sqft minimum lot"
                    + (" per unit" if d.get("area_is_per_unit") else ""))
    if d.get("min_lot_width_ft"):
        bits.append(f"{d['min_lot_width_ft']} ft wide")
    yards = [f"{d[k]} ft" for k in ("front_yard_ft", "side_yard_ft", "rear_yard_ft")
             if d.get(k) is not None]
    if len(yards) == 3:
        bits.append(f"setbacks {'/'.join(y.split()[0] for y in yards)} (front/side/rear)")
    if d.get("max_height_ft"):
        bits.append(f"up to {d['max_height_ft']} ft tall")
    summary = "; ".join(bits) if bits else "no dimensional standards transcribed for this district"
    if overlays:
        summary += ". " + "; ".join(o["name"] for o in overlays) + " also applies"

    return {
        "known": True,
        "district": key,
        "name": d.get("name") or key,
        "jurisdiction": juris.get("name"),
        "residential": d.get("residential"),
        "uses": d.get("uses"),
        "rules": rules,
        "overlays": overlays,
        "summary": summary,
        "citation": juris.get("citation"),
        "url": juris.get("url"),
        "notes": notes,
    }

"""Scoring runner (Section 8/9). Computes parcel_scores from a normalized parcel
frame + its signals. The score is four tangible pillars (v2026.23):
development potential, owner motivation, distress, and demand — renormalized over
whatever each parcel has. Every row records the scoring_config_version.

Usable two ways:
  - score_frame(parcels_df, signals_df): pure, no DB — used by tests and dry runs.
  - main(): reads parcel_master/parcel_signals from PostGIS, writes parcel_scores.
"""
from __future__ import annotations

from datetime import date
from typing import Optional

import pandas as pd

from scoring import capacity, classify
from scoring.score import composite, distress_score, is_knocked_out, load_config


def score_frame(parcels: pd.DataFrame, signals: Optional[pd.DataFrame],
                cfg: dict, asof: Optional[date] = None,
                demand_by_nb: Optional[dict] = None) -> pd.DataFrame:
    from scoring.score import development_potential, owner_motivation_score
    zrules = capacity.load_rules()
    asof = asof or date.today()
    sig_by_parcel: dict[str, list[dict]] = {}
    if signals is not None and not signals.empty:
        for pid, grp in signals.groupby("parcel_id"):
            sig_by_parcel[pid] = grp.to_dict("records")
    # per-neighborhood land yardstick: the teardown land-value factor scores each
    # lot against its own market's median so it keeps discriminating where a fixed
    # dollar cap would saturate (every affluent-market lot clears $40k)
    land_ref_by_nb = _neighborhood_land_ref(parcels)

    # single pass: the four tangible pillars need no cross-parcel context
    # (assemblage — the only component that did — was dropped in v2026.23)
    rows = []
    for _, p in parcels.iterrows():
        group = classify.il_threshold_group(p.get("land_use_code"))
        # a complex satellite (buildings booked on a same-address sibling deed)
        # is not independently developable, whatever its own valuation says
        satellite = _bool(p.get("is_satellite"))
        condo_unit = _bool(p.get("is_condo_unit"))
        land_value = _num(p.get("land_value"))
        # a parcel is not independently developable when its improvement value
        # lives on a sibling deed (apartment complex, outparcel), it is one unit
        # of a shared-footprint condo regime, or it is the land-dominant component
        # of an operating commercial site (parking lot / gas pad / outlot).
        commercial_land = classify.is_commercial_land_component(
            p.get("land_use_code"), land_value, _num(p.get("improvement_value")))
        booked_elsewhere = condo_unit or commercial_land \
            or classify.improvements_booked_elsewhere(
                p.get("land_use_code"), _num(p.get("improvement_value")), satellite)
        vacant = (not booked_elsewhere) and classify.is_vacant_land(
            p.get("land_use_code"), p.get("has_structure"), p.get("improvement_value"))
        family = capacity.use_family(p.get("zoning_code"), zrules)
        corridor = capacity.is_commercial_corridor(
            p.get("zoning_code"), cfg.get("infill", {}).get("commercial_corridor_prefixes"))

        # PILLAR 1 — development potential: vacant lot OR teardown
        dev = 0.0 if booked_elsewhere else development_potential(
            vacant, land_value, _num(p.get("area_sqft")), _num(p.get("improvement_value")),
            group, cfg, res_context_pct=_num(p.get("res_context_pct")), use_family=family,
            commercial_corridor=corridor, home_sqft=_num(p.get("home_sqft")),
            land_ref=land_ref_by_nb.get(p.get("neighborhood")))
        is_teardown = (not vacant) and dev > 0

        # PILLAR 2 — owner motivation: tenure / absentee / trust / bank / authority
        own = owner_motivation_score(
            _years_owned(p.get("last_sale_date"), asof), _bool(p.get("is_absentee")),
            p.get("owner_type"), p.get("owner_name_raw"), cfg)

        # PILLAR 3 — distress (kept)
        parcel_sigs = sig_by_parcel.get(p["parcel_id"], [])
        dist = distress_score(
            [{"signal_type": s["signal_type"],
              "event_date": _as_date(s.get("event_date"), asof),
              "severity": _num(s.get("severity"))} for s in parcel_sigs],
            cfg, asof, land_value=land_value)

        components = {"development": dev, "owner_motivation": own, "distress": dist}
        available = ["development", "owner_motivation", "distress"]
        # PILLAR 4 — demand (kept); excluded where no neighborhood metrics exist
        dem = (demand_by_nb or {}).get(p.get("neighborhood"))
        if dem is not None:
            components["demand"] = float(dem)
            available.append("demand")
        constraints = _active_constraints(p)
        # unacquirable land (exempt/utility classes, public or institutional
        # owners) is not a lead — knock out unless the land bank lists it OR the
        # owner is a public authority that disposes of land (CMHA, the Port)
        acquirable = any(s["signal_type"] == "landbank_inventory" for s in parcel_sigs) \
            or classify.is_acquirable_authority(p.get("owner_name_raw"), cfg)
        if classify.is_not_acquirable(p.get("land_use_code"), p.get("owner_type")) \
                and not acquirable:
            constraints.append("not_acquirable")
        # going-concern amenities (golf/country clubs): heavy discount unless
        # the parcel itself is distressed — a failing club is a real play
        if classify.is_operating_amenity(p.get("land_use_code"),
                                         p.get("owner_name_raw"), cfg) \
                and dist < cfg["amenity"]["distress_waiver"]:
            constraints.append("operating_amenity")
        if condo_unit:  # surfaced so the dossier explains the low score
            constraints.append("condo_unit")
        elif satellite:
            constraints.append("complex_satellite")
        elif commercial_land:
            constraints.append("commercial_land")
        # someone is already building here: a new-building permit on this parcel
        # inside the window means the opportunity is taken, even though the
        # assessor still shows it vacant (agent: "someone already built a new one")
        if _bool(p.get("has_recent_permit")):
            constraints.append("permit_issued")
        # a builder's platted lots are an active project's stock, not for sale —
        # unless the project itself is in distress, which makes it a real play
        if _bool(p.get("is_subdivision_lot")) \
                and dist < cfg["subdivision"]["distress_waiver"]:
            constraints.append("subdivision_inventory")
        if is_knocked_out(constraints, cfg):
            # hard knockout (regulatory floodway / not acquirable): drop below surfacing
            opp = 0.0
        else:
            opp = composite(components, constraints, cfg, available=available)
        rows.append({
            "parcel_id": p["parcel_id"],
            "opportunity_score": opp,
            "development_score": dev,
            "owner_motivation_score": own,
            "distress_score": dist,
            "demand_score": dem,
            "use_classification": _use_class(vacant, is_teardown, dev, dist, own, family, group),
            "constraints": {"active": constraints},
            "scoring_config_version": cfg["version"],
        })
    return pd.DataFrame(rows).sort_values("opportunity_score", ascending=False)


def _neighborhood_land_ref(parcels: pd.DataFrame) -> dict:
    """Median land value of teardown-eligible parcels (improved single-family) in
    each neighborhood — the local yardstick the teardown land-value factor scores
    against. Computed from the frame itself (the full county in production), so it
    needs no table and is always fresh. Neighborhoods with no such parcel are
    absent and fall back to the absolute value ramp."""
    if "neighborhood" not in parcels.columns:
        return {}
    # detached single-family (DTE 510) only — the universe teardowns come from.
    # Condos (550) own a value slice, not a buildable lot, and would drag the
    # median down in dense markets (Hyde Park, Oakley).
    code = pd.to_numeric(parcels["land_use_code"], errors="coerce")
    lv = pd.to_numeric(parcels["land_value"], errors="coerce")
    iv = pd.to_numeric(parcels["improvement_value"], errors="coerce")
    elig = (code == 510) & (iv > 0) & (lv > 0)
    if not elig.any():
        return {}
    med = lv[elig].groupby(parcels["neighborhood"][elig]).median()
    return {nb: float(v) for nb, v in med.items()
            if nb is not None and pd.notna(v) and v > 0}


def _active_constraints(p) -> list[str]:
    flags = []
    if _bool(p.get("floodway_flag")):
        flags.append("regulatory_floodway")   # hard knockout
    if _bool(p.get("flood_flag")):
        flags.append("flood")
    if _bool(p.get("slope_flag")):
        flags.append("steep_slope")
    if _bool(p.get("historic_flag")):
        flags.append("historic")
    if _bool(p.get("cso_flag")):
        flags.append("cso")
    return flags


def _use_class(vacant: bool, is_teardown: bool, dev: float, dist: float,
               own: float, family: str | None, group: str) -> str:
    """The play type, in the agent's own terms (v2026.23): a vacant lot, a
    teardown (small house / valuable lot), a motivated-owner angle (no physical
    redevelopment but a distressed / long-held / institutional owner), or a
    non-residential site. Replaces the opaque 'assemblage' label."""
    if family == "none":
        return "non-residential"
    if vacant:
        return "vacant lot"
    if is_teardown:
        return "teardown"
    if dist > 0 or own >= 50:
        return "motivated owner"
    return "hold/monitor"


def _num(v) -> float | None:
    """NaN/None-safe float coercion (pandas NaN is truthy; never let it through)."""
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if pd.isna(f) else f


def _bool(v) -> bool:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return False
    return bool(v)


def _as_date(v, default: date) -> date:
    if isinstance(v, date):
        return v
    try:
        return date.fromisoformat(str(v)[:10])
    except (TypeError, ValueError):
        return default


def _years_owned(last_sale_date, asof: date) -> float | None:
    """Years the current owner has held the parcel (for owner motivation). Unknown
    when there's no sale date; future/bogus dates were cleaned at ingest."""
    if last_sale_date is None or (isinstance(last_sale_date, float) and pd.isna(last_sale_date)):
        return None
    try:
        d = last_sale_date if isinstance(last_sale_date, date) \
            else date.fromisoformat(str(last_sale_date)[:10])
    except (TypeError, ValueError):
        return None
    yrs = (asof - d).days / 365.25
    return yrs if yrs >= 0 else None


def main() -> None:
    """Read from PostGIS, score, write parcel_scores."""
    import json

    from sqlalchemy import text

    from config import settings
    from ingestion.load import _engine

    from scoring.alerts import diff_alerts, persist_alerts

    cfg = load_config(settings.scoring_config_path)
    eng = _engine()
    with eng.begin() as conn:
        parcels = pd.read_sql(text("""
            SELECT m.parcel_id, m.land_use_code, m.improvement_value, m.land_value,
                   m.il_ratio, m.has_structure, m.area_sqft, m.owner_type,
                   m.owner_name_raw, m.is_absentee, m.zoning_code, m.cc_zone, m.num_units,
                   m.neighborhood, m.last_sale_date,
                   m.is_satellite, m.is_subdivision_lot, m.is_condo_unit,
                   m.res_context_pct, m.home_sqft,
                   EXISTS (SELECT 1 FROM development_permits dp
                           WHERE dp.parcel_id = m.parcel_id
                             AND dp.issued_date >= now() - interval '24 months'
                          ) AS has_recent_permit,
                   m.flood_flag, m.floodway_flag, m.slope_flag, m.historic_flag,
                   m.cso_flag
            FROM parcel_master m
        """), conn)
        signals = pd.read_sql(text(
            "SELECT parcel_id, signal_type, event_date, severity FROM parcel_signals"), conn)
        prev = {r[0]: float(r[1]) for r in conn.execute(text(
            "SELECT parcel_id, opportunity_score FROM parcel_scores "
            "WHERE opportunity_score IS NOT NULL"))}
        demand_by_nb = {r[0]: float(r[1]) for r in conn.execute(text(
            "SELECT neighborhood, demand_score FROM neighborhood_demand "
            "WHERE demand_score IS NOT NULL"))}
    scores = score_frame(parcels, signals, cfg, demand_by_nb=demand_by_nb)
    upsert = text("""
        INSERT INTO parcel_scores (parcel_id, opportunity_score, development_score,
            owner_motivation_score, distress_score, demand_score,
            use_classification, constraints, scoring_config_version, scored_at)
        VALUES (:parcel_id, :opportunity_score, :development_score,
            :owner_motivation_score, :distress_score, :demand_score,
            :use_classification, CAST(:constraints AS jsonb), :scoring_config_version, now())
        ON CONFLICT (parcel_id) DO UPDATE SET
            opportunity_score = EXCLUDED.opportunity_score,
            development_score = EXCLUDED.development_score,
            owner_motivation_score = EXCLUDED.owner_motivation_score,
            distress_score = EXCLUDED.distress_score,
            demand_score = EXCLUDED.demand_score,
            use_classification = EXCLUDED.use_classification,
            constraints = EXCLUDED.constraints,
            scoring_config_version = EXCLUDED.scoring_config_version,
            scored_at = now(),
            underutilization_score = NULL, capacity_gap_score = NULL, assemblage_score = NULL
    """)
    rows = []
    for _, r in scores.iterrows():
        d = {k: (v if not pd.isna(v) else None) if not isinstance(v, (dict, list)) else v
             for k, v in r.to_dict().items()}
        d["constraints"] = json.dumps(d["constraints"])
        rows.append(d)
    with eng.begin() as conn:
        for i in range(0, len(rows), 5000):  # executemany in chunks
            conn.execute(upsert, rows[i:i + 5000])

    new_map = {r["parcel_id"]: float(r["opportunity_score"]) for r in rows
               if r["opportunity_score"] is not None}
    alerts = diff_alerts(prev, new_map,
                         settings.alert_score_threshold, settings.alert_min_jump)
    n_alerts = persist_alerts(alerts)
    print(f"scored {len(rows)} parcels (config {cfg['version']}); "
          f"{n_alerts} alerts queued")


if __name__ == "__main__":
    main()

"""Opportunity scoring engine (Section 9). SQL-expressible metrics run as a dbt
model; this Python composer loads the versioned config, applies recency decay to
distress signals, blends the five weighted components, applies soft constraint
penalties, and drops hard-knockout parcels. Stub wiring in Phase 0 — formulas are
real and unit-tested; DB I/O lands in Phase 1+.
"""
from __future__ import annotations

import math
from datetime import date
from pathlib import Path

import yaml


def load_config(path: str | Path) -> dict:
    # explicit encoding: the default is locale-dependent on Windows, which
    # silently mangles the file when tooling rewrites it
    cfg = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    total = sum(cfg["composite_weights"].values())
    if abs(total - 1.0) > 1e-9:
        raise ValueError(f"composite_weights must sum to 1.0, got {total}")
    return cfg


def recency_decay(event_date: date, asof: date, half_life_months: float | None) -> float:
    """Exponential decay by half-life in months. null half_life => no decay (1.0)."""
    if half_life_months is None:
        return 1.0
    months = (asof.year - event_date.year) * 12 + (asof.month - event_date.month)
    return 0.5 ** (max(months, 0) / half_life_months)


def distress_score(signals: list[dict], cfg: dict, asof: date,
                   land_value: float | None = None) -> float:
    """Weighted, recency-decayed, severity-scaled sum of active distress signals,
    normalized 0..100. `signals` items: {signal_type, event_date, severity?}."""
    weights = cfg["distress_weights"]
    by_type: dict[str, float] = {}
    for s in signals:
        spec = weights.get(s["signal_type"])
        if not spec:
            continue
        by_type[s["signal_type"]] = by_type.get(s["signal_type"], 0.0) + (
            spec["weight"]
            * recency_decay(s["event_date"], asof, spec["half_life"])
            * _severity_mult(s, land_value, cfg))
    raw = 0.0
    for sig_type, contribution in by_type.items():
        spec = weights[sig_type]
        cap = spec.get("max_events")  # diminishing returns on repeat reports
        if cap is not None:
            contribution = min(contribution, spec["weight"] * cap)
        raw += contribution
    # squashing keeps a single severe filing from saturating the component
    return 100.0 * (1.0 - math.exp(-raw))


def _severity_mult(signal: dict, land_value: float | None, cfg: dict) -> float:
    """Scale a signal by its $ severity relative to land value (severity_scaling).
    Signals without severity data, or parcels without land value, stay neutral."""
    spec = cfg.get("severity_scaling", {}).get(signal["signal_type"])
    severity = signal.get("severity")
    if not spec or not severity or not land_value:
        return 1.0
    t = _ramp(float(severity) / float(land_value), 0.0, spec["ratio_at_max"])
    return spec["min_mult"] + (spec["max_mult"] - spec["min_mult"]) * t


def underutilization_score(il_ratio: float | None, use_group: str, vacant: bool,
                           cfg: dict, land_value: float | None = None,
                           area_sqft: float | None = None,
                           res_context_pct: float | None = None,
                           use_family: str | None = None,
                           commercial_corridor: bool = False) -> float:
    """Section 9.1. Scales inversely with I/L relative to the use-group threshold:
    an improved parcel at or above threshold = 0; below threshold ramps toward 100
    as land value dominates improvement value. Vacant land takes max credit scaled
    by lot quality (vacant_scaling) AND residential context (infill config): a
    vacant lot is a housing play when houses surround it — the same lot in a
    commercial strip is not."""
    ctx = residential_context_factor(res_context_pct, cfg, use_family, commercial_corridor)
    if vacant:
        return round(100.0 * vacant_quality(land_value, area_sqft, cfg) * ctx, 2)
    threshold = cfg["il_thresholds"].get(use_group, cfg["il_thresholds"]["commercial"])
    if il_ratio is None or threshold <= 0:
        return 0.0
    # the context factor applies here too: a modest building in a 0%-residential
    # strip (an operating coffee shop, a food-truck lot) is not a housing
    # teardown, however low its I/L ratio reads (first real-agent feedback round)
    return round(100.0 * max(0.0, (threshold - il_ratio) / threshold) * ctx, 2)


def development_potential(vacant: bool, land_value: float | None, area_sqft: float | None,
                          improvement_value: float | None, use_group: str, cfg: dict,
                          res_context_pct: float | None = None, use_family: str | None = None,
                          commercial_corridor: bool = False,
                          home_sqft: float | None = None,
                          land_ref: float | None = None) -> float:
    """Pillar 1 (0.40): can you build something much better here? Concrete and
    readable — a VACANT lot ready to build, or a TEARDOWN (a small house on a lot
    worth more than the house). Replaces the opaque underutilization + capacity
    components. Everything else (occupied non-teardown homes, commercial in use)
    is 0 here; the false-positive guards still zero out fake vacants/teardowns.

    `land_ref` is the neighborhood's median single-family land value; the teardown
    land-value factor scores against it so the pillar keeps discriminating inside
    affluent target markets where every lot clears a fixed dollar cap."""
    if vacant:
        ctx = residential_context_factor(res_context_pct, cfg, use_family, commercial_corridor)
        return round(100.0 * vacant_quality(land_value, area_sqft, cfg) * ctx, 2)
    return teardown_score(land_value, improvement_value, use_group, cfg,
                          home_sqft=home_sqft, land_ref=land_ref)


def owner_motivation_score(years_owned: float | None, is_absentee: bool | None,
                           owner_type: str | None, owner_name: str | None,
                           cfg: dict) -> float:
    """Pillar 2 (0.25): how likely is the owner to sell? The agents' acquisition
    filters as a score — long tenure, absentee, trust, and land-disposing
    institutions (bank/REO, CMHA, the Port). Weighted sum of exact-data signals,
    capped at 100. All signals are readable in the dossier."""
    from scoring import classify
    om = cfg.get("owner_motivation")
    if not om:
        return 0.0
    w = om["weights"]
    s = 0.0
    if years_owned is not None:
        s += w["tenure"] * _ramp(years_owned, om["tenure_min_years"], om["tenure_full_years"])
    if is_absentee:
        s += w["absentee"]
    if owner_type == "trust":
        s += w["trust"]
    if classify.is_bank_owned(owner_name):
        s += w["bank"]
    if classify.is_acquirable_authority(owner_name, cfg):
        s += w["authority"]
    return round(min(100.0, s), 2)


def teardown_score(land_value: float | None, improvement_value: float | None,
                   use_group: str, cfg: dict, home_sqft: float | None = None,
                   land_ref: float | None = None) -> float:
    """A modest single-family home on a valuable lot is a teardown/rebuild
    opportunity: the LAND is a large share of the value and the house is small, so
    replacing it unlocks far more. This is the Oyler Hines team's actual playbook —
    their real deals sit at land-share ~0.41 (vs an ordinary home's ~0.28) and
    ~2,175 sqft (vs ~3,526). Applies only to IMPROVED single-family parcels; vacant
    land keeps its own (higher) credit via the vacant branch, and the caller takes
    the MAX so nothing existing is lost."""
    td = cfg.get("teardown")
    if not td or use_group != "single_family":
        return 0.0
    lv = land_value or 0.0
    iv = improvement_value or 0.0
    total = lv + iv
    if total <= 0 or iv <= 0:          # vacant / no building — not a teardown
        return 0.0
    land_share = lv / total
    t = _ramp(land_share, td["land_share_floor"], td["land_share_full"])
    quality = _teardown_land_quality(lv, land_ref, td)
    return round(100.0 * t * quality * _teardown_size_factor(home_sqft, td), 2)


def _teardown_land_quality(lv: float, land_ref: float | None, td: dict) -> float:
    """How much upside the LAND carries — the heart of the teardown thesis.
    Market-relative when a neighborhood reference (median single-family land value)
    is available: a lot worth ~2x its neighborhood median is a prime rebuild site,
    one worth half is marginal. This is what keeps the factor discriminating inside
    the affluent target markets, where a fixed $40k dollar cap saturates at 100 for
    nearly every lot (Madeira's median SF lot is ~$142k).

    An output FLOOR (`land_ref_floor`) is deliberate: the Oyler Hines team's real
    deals sit right at their neighborhood median land value (ratio ~0.99) — they
    buy average-value lots with modest houses, so the teardown case is the SMALL
    HOUSE, not a premium lot. Without the floor a from-zero ramp scored their own
    closed deals near zero. The floor keeps an average-lot teardown a solid lead
    while still reserving the top of the range for genuinely premium lots.

    Falls back to the absolute value ramp where no local reference exists
    (thin/unknown neighborhoods, unit tests)."""
    ratio_full = td.get("land_ref_ratio_full")
    if land_ref and land_ref > 0 and ratio_full:
        floor = td.get("land_ref_floor", 0.0)
        t = _ramp(lv / land_ref, td.get("land_ref_ratio_floor", 0.0), ratio_full)
        return floor + (1.0 - floor) * t
    return _ramp(lv, td["min_land_value"], td["full_land_value"])


def _teardown_size_factor(home_sqft: float | None, td: dict) -> float:
    """A small home is a stronger teardown, a large one weaker. Full credit at/
    below size_small_sqft, tapering to size_floor at/above size_large_sqft.
    Unknown size stays neutral (1.0) — never penalise missing data."""
    small = td.get("size_small_sqft")
    if home_sqft is None or small is None:
        return 1.0
    floor = td.get("size_floor", 1.0)
    over = _ramp(home_sqft, small, td.get("size_large_sqft", small * 2))
    return floor + (1.0 - floor) * (1.0 - over)


def vacant_quality(land_value: float | None, area_sqft: float | None,
                   cfg: dict) -> float:
    """Geometric mean of the value and size ramps (vacant_scaling). A ramp with
    missing input is skipped rather than zeroed; no config => full credit."""
    vs = cfg.get("vacant_scaling")
    if not vs:
        return 1.0
    factors = []
    if land_value is not None:
        factors.append(_ramp(land_value, vs["min_land_value"], vs["full_land_value"]))
    if area_sqft is not None and area_sqft > 0:
        factors.append(_ramp(area_sqft, vs["sliver_sqft"], vs["viable_sqft"]))
    if not factors:
        return 1.0
    return math.prod(factors) ** (1.0 / len(factors))


def residential_context_factor(res_context_pct: float | None, cfg: dict,
                               use_family: str | None = None,
                               commercial_corridor: bool = False) -> float:
    """The spec's infill measure: ramp from `res_context_floor` (no residential
    neighbors — a commercial-strip lot keeps partial credit as a possible
    mixed-use play, never full housing credit) to 1.0 at res_context_full_pct.

    The bar is zoning-aware. On a commercially-zoned parcel the *default* use is
    commercial, so it takes much stronger residential surroundings to justify a
    housing thesis — an agent reviewing Oakley rejected CC-P lots at 25-33%
    residential context ("right behind Petsmart... would not work") while
    approving RM/SF lots at 50-80%.

    Missing (null) context is neutral EXCEPT on a commercial corridor (CC/CN/CG/
    T5): there the presumptive use is commercial, so no residential evidence damps
    to the commercial floor rather than granting full housing credit. This closes
    the leak the first audit round exposed — corridor lots with un-computed
    context were topping the board (v2026.19). Downtown (DD) is not a corridor, so
    real downtown leads stay neutral on null context."""
    infill = cfg.get("infill")
    if not infill:
        return 1.0
    commercial = commercial_corridor or use_family == "mixed"
    floor = (infill.get("commercial_context_floor", infill["res_context_floor"])
             if commercial else infill["res_context_floor"])
    if res_context_pct is None:
        # no residential evidence: neutral on residential land, commercial floor
        # on a corridor (its default use is commercial, not for-sale housing)
        return floor if commercial_corridor else 1.0
    full = (infill.get("commercial_context_full_pct", infill["res_context_full_pct"])
            if commercial else infill["res_context_full_pct"])
    return floor + (1.0 - floor) * min(max(float(res_context_pct) / full, 0.0), 1.0)


def ownership_multiplier(owner_type: str | None, is_absentee: bool | None,
                         cfg: dict) -> float:
    """Acquirability modifier on the final composite (ownership_multiplier).
    Multiplicative, capped at max_total; neutral when config absent."""
    om = cfg.get("ownership_multiplier")
    if not om:
        return 1.0
    m = 1.0
    if is_absentee:
        m *= om.get("absentee", 1.0)
    if owner_type:
        m *= om.get(owner_type, 1.0)
    return min(m, om.get("max_total", m))


def _ramp(x: float, lo: float, hi: float) -> float:
    """Clamped linear ramp: 0 at/below lo, 1 at/above hi."""
    if hi <= lo:
        return 1.0
    return max(0.0, min(1.0, (x - lo) / (hi - lo)))


def composite(components: dict, constraints: list[str], cfg: dict,
              available: list[str] | None = None) -> float:
    """Weighted blend of the five components, then multiply soft-constraint
    penalties. Hard knockouts are handled by the caller (parcel dropped).

    `available` restricts the blend to components actually computed and
    renormalizes their weights to sum to 1 — so an MVP running only
    underutilization + distress ranks on real signal instead of being dragged
    down by a constant from the not-yet-built components."""
    weights = cfg["composite_weights"]
    names = available if available is not None else list(weights)
    wsum = sum(weights[n] for n in names) or 1.0
    base = sum((weights[n] / wsum) * components.get(n, 0.0) for n in names)
    for flag in constraints:
        base *= cfg["constraint_penalty"].get(flag, 1.0)
    return round(base, 2)


def is_knocked_out(constraints: list[str], cfg: dict) -> bool:
    return any(flag in cfg["hard_knockout"] for flag in constraints)

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  activeFilterCount, exportUrl, fetchGeojson, fetchHealth,
  fetchHomeRegion, fetchLeadList, fetchLeads, fetchNeighborhoods, fetchParcel,
  fetchParcelGeojson, fetchRegionBounds, fetchStats, LeadFilters, logEvent, Health,
  LeadCard, LeadRow, NeighborhoodRow, ParcelDossier, ParcelHit, saveHomeRegion,
  searchParcels, SortKey, Stats, Assembly, fetchAssemblies,
} from "./api";
import AssemblyPanel from "./AssemblyPanel";
import Audit from "./Audit";
import Dossier from "./Dossier";
import Filters from "./Filters";
import MapView from "./MapView";
import Pipeline from "./Pipeline";
import TopList from "./TopList";

type View = "map" | "pipeline" | "audit";
// what the map + side list are showing: individual parcels, or multi-parcel tracts
type BrowseMode = "parcels" | "assemblies";
const ACRE_STEPS = [3, 5, 10, 20];

// Sort options for the results list. Score-based ones only appear when scoring
// is toggled on; the rest are pure county data for filter-first browsing.
const SORT_OPTIONS: { key: SortKey; label: string; scoreOnly?: boolean }[] = [
  { key: "opportunity_score", label: "Opportunity score", scoreOnly: true },
  { key: "land_value", label: "Land value" },
  { key: "total_value", label: "Total value" },
  { key: "building_value", label: "Building value" },
  { key: "land_share", label: "Land share" },
  { key: "lot_sqft", label: "Lot size" },
  { key: "home_sqft", label: "House size" },
  { key: "years_owned", label: "Years owned" },
  { key: "condition_gap", label: "Worse than block" },
  { key: "last_sale_date", label: "Last sale date" },
  { key: "last_sale_price", label: "Last sale price" },
  { key: "distress_score", label: "Distress", scoreOnly: true },
];

export default function App() {
  const [view, setView] = useState<View>("map");
  const [browse, setBrowse] = useState<BrowseMode>("parcels");
  const [assemblies, setAssemblies] = useState<Assembly[]>([]);
  const [asmKind, setAsmKind] = useState("");
  const [asmMinAcres, setAsmMinAcres] = useState(3);
  const [openAsmId, setOpenAsmId] = useState<number | null>(null);
  const [asmShapes, setAsmShapes] = useState<GeoJSON.FeatureCollection | null>(null);
  const [scoringOn, setScoringOn] = useState<boolean>(() => localStorage.getItem("scoringOn") === "1");
  const [sort, setSort] = useState<SortKey>(() =>
    localStorage.getItem("scoringOn") === "1" ? "opportunity_score" : "land_value");
  const [sortDesc, setSortDesc] = useState(true);
  const [minScore, setMinScore] = useState(50);
  const [showAll, setShowAll] = useState(false);          // browse every lot, ignore score
  const [adv, setAdv] = useState<LeadFilters>({});        // advanced realtor filters
  const [filtersOpen, setFiltersOpen] = useState(false);
  const [region, setRegion] = useState<string | null>(null);
  const [regions, setRegions] = useState<NeighborhoodRow[]>([]);
  const [fitBounds, setFitBounds] = useState<[number, number, number, number] | null>(null);
  const bboxRef = useRef<string | null>(null);            // last map viewport
  const [results, setResults] = useState<LeadRow[]>([]);
  const [total, setTotal] = useState(0);
  const [savedLeads, setSavedLeads] = useState<LeadCard[]>([]);
  const [openLeadId, setOpenLeadId] = useState<number | null>(null);
  const [focus, setFocus] = useState<{ lng: number; lat: number; key: number } | null>(null);
  const [data, setData] = useState<GeoJSON.FeatureCollection | null>(null);
  const [stats, setStats] = useState<Stats | null>(null);
  const [health, setHealth] = useState<Health | null>(null);
  const [dossier, setDossier] = useState<ParcelDossier | null>(null);
  const [highlight, setHighlight] = useState<GeoJSON.FeatureCollection | null>(null);
  const [searchQ, setSearchQ] = useState("");
  const [searchHits, setSearchHits] = useState<ParcelHit[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  // merge region + score + show-all + advanced filters into one filter object;
  // pass overrides for values changing in the same tick (state not yet applied).
  // Filter-first: the min-score floor is applied ONLY when scoring is toggled on.
  const currentFilters = useCallback((o: {
    region?: string | null; minScore?: number; showAll?: boolean;
    adv?: LeadFilters; scoringOn?: boolean;
  } = {}): LeadFilters => {
    const rg = "region" in o ? o.region ?? null : region;
    const ms = o.minScore ?? minScore;
    const sa = o.showAll ?? showAll;
    const a = o.adv ?? adv;
    const sc = o.scoringOn ?? scoringOn;
    return { ...a, neighborhood: rg, showAll: sa, minScore: (sa || !sc) ? null : ms };
  }, [region, minScore, showAll, adv, scoringOn]);

  // The LIST side (filtered results + stats) — scoped to the region and sorted
  // server-side by the active sort field.
  const refresh = useCallback(async (f: LeadFilters,
                                     srt: SortKey = sort, dsc: boolean = sortDesc) => {
    setError(null);
    try {
      const [st, list] = await Promise.all([
        fetchStats(),
        fetchLeadList(f, srt, dsc).catch(() => ({ total: 0, results: [] as LeadRow[] }))]);
      setStats(st);
      setResults(list.results);
      setTotal(list.total);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, [sort, sortDesc]);

  // The MAP side — load every parcel in the current viewport (not just the
  // global top-N), honoring score/advanced filters but NOT the region, so the
  // user can pan anywhere and click any lot they see.
  const loadMap = useCallback(async (bbox: string | null, o: {
    minScore?: number; showAll?: boolean; adv?: LeadFilters; scoringOn?: boolean;
  } = {}) => {
    if (!bbox) return;
    setLoading(true);
    try {
      const f = { ...currentFilters(o), neighborhood: null };
      setData(await fetchGeojson(f, { bbox }));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, [currentFilters]);

  // Assemblies mode: load the tract list and their outlines together, so the
  // side list and the map always describe the same set.
  useEffect(() => {
    if (browse !== "assemblies") return;
    let live = true;
    const q = new URLSearchParams({ min_acres: String(asmMinAcres) });
    if (asmKind) q.set("kind", asmKind);
    if (region) q.set("neighborhood", region);
    void fetchAssemblies({ kind: asmKind || null, minAcres: asmMinAcres, neighborhood: region })
      .then((rows) => { if (live) setAssemblies(rows); })
      .catch(() => { if (live) setAssemblies([]); });
    void fetch(`/api/geojson_assemblages?${q}`)
      .then((r) => r.json())
      .then((fc) => { if (live) setAsmShapes(fc); })
      .catch(() => { if (live) setAsmShapes(null); });
    return () => { live = false; };
  }, [browse, asmKind, asmMinAcres, region]);

  // fly to a tract and outline just it
  const openAssembly = useCallback((a: Assembly | null) => {
    setOpenAsmId(a?.id ?? null);
    if (!a || !asmShapes) return;
    const f = asmShapes.features.find(
      (x) => (x.properties as { assemblage_id?: number })?.assemblage_id === a.id);
    if (!f) return;
    let sx = 0, sy = 0, n = 0;
    const walk = (c: unknown): void => {
      if (Array.isArray(c) && typeof c[0] === "number") { sx += (c as number[])[0]; sy += (c as number[])[1]; n++; }
      else if (Array.isArray(c)) c.forEach(walk);
    };
    walk((f.geometry as GeoJSON.Polygon | GeoJSON.MultiPolygon).coordinates);
    if (n) setFocus({ lng: sx / n, lat: sy / n, key: Date.now() });
  }, [asmShapes]);

  const onViewport = useCallback((bbox: string) => {
    bboxRef.current = bbox;
    void loadMap(bbox);
  }, [loadMap]);

  const refreshLeads = useCallback(() => {
    fetchLeads().then(setSavedLeads).catch(() => {});
  }, []);

  // parcel_id -> lead id, so the dossier knows if a parcel is already saved
  const savedByParcel = useMemo(() => {
    const m = new Map<string, number>();
    for (const l of savedLeads) m.set(l.parcel_id, l.id);
    return m;
  }, [savedLeads]);

  useEffect(() => {  // initial load: open into the agent's saved home region
    logEvent("session");
    fetchNeighborhoods(50).then(setRegions).catch(() => {});
    fetchHealth().then(setHealth).catch(() => {});   // stale data must be visible
    refreshLeads();
    fetchHomeRegion().then((home) => {
      const start = home || "Madeira";   // team default: open on their #1 market
      setRegion(start);
      void refresh(currentFilters({ region: start }));
      fetchRegionBounds(start).then((b) => { if (b) setFitBounds(b); }).catch(() => {});
      // the map's own initial viewport report also triggers a load
    }).catch(() => void refresh(currentFilters({ region: "Madeira" })));
  }, []);

  const onRegionChange = useCallback((nb: string | null) => {
    setRegion(nb);
    void saveHomeRegion(nb);                    // remember their working area
    logEvent("select_region", { region: nb });
    void refresh(currentFilters({ region: nb }));
    // fly the map to the region; the ensuing moveend loads the parcels in view
    fetchRegionBounds(nb).then((b) => { if (b) setFitBounds(b); }).catch(() => {});
  }, [currentFilters, refresh]);

  const onToggleShowAll = useCallback(() => {
    setShowAll((prev) => {
      const next = !prev;
      logEvent("toggle_show_all", { show_all: next });
      void refresh(currentFilters({ showAll: next }));
      void loadMap(bboxRef.current, { showAll: next });
      return next;
    });
  }, [currentFilters, refresh, loadMap]);

  const onApplyFilters = useCallback((next: LeadFilters) => {
    setAdv(next);
    setFiltersOpen(false);
    logEvent("apply_filters", { active: activeFilterCount(next) });
    void refresh(currentFilters({ adv: next }));
    void loadMap(bboxRef.current, { adv: next });
  }, [currentFilters, refresh, loadMap]);

  // Scoring toggle: off = pure filter-first browsing (no score floor, flat map,
  // sort by data); on = the opportunity score returns as a floor, map colour, and
  // ranking. Remembered per browser.
  const onToggleScoring = useCallback(() => {
    setScoringOn((prev) => {
      const next = !prev;
      localStorage.setItem("scoringOn", next ? "1" : "0");
      logEvent("toggle_scoring", { on: next });
      const nextSort: SortKey = next ? "opportunity_score" : "land_value";
      setSort(nextSort);
      setSortDesc(true);
      void refresh(currentFilters({ scoringOn: next }), nextSort, true);
      void loadMap(bboxRef.current, { scoringOn: next });
      return next;
    });
  }, [currentFilters, refresh, loadMap]);

  const onSortChange = useCallback((key: SortKey, desc: boolean) => {
    setSort(key);
    setSortDesc(desc);
    logEvent("sort", { sort: key, desc });
    void refresh(currentFilters(), key, desc);
  }, [currentFilters, refresh]);

  const onSelect = useCallback(async (pid: string) => {
    logEvent("view_parcel", { parcel_id: pid });
    try { setDossier(await fetchParcel(pid)); } catch { /* parcel fetch failed; ignore */ }
  }, []);

  // open a parcel's dossier, fly the map to it, and outline it — works whether
  // or not the parcel is in the current viewport slice (fetches its geometry)
  const flyToParcel = useCallback((pid: string) => {
    void onSelect(pid);
    fetchParcelGeojson(pid).then((fc) => {
      setHighlight(fc);
      const f = fc.features[0];
      if (!f) return;
      let sx = 0, sy = 0, n = 0;
      const walk = (c: unknown): void => {
        if (Array.isArray(c) && typeof c[0] === "number") { sx += (c as number[])[0]; sy += (c as number[])[1]; n++; }
        else if (Array.isArray(c)) c.forEach(walk);
      };
      walk((f.geometry as GeoJSON.Polygon | GeoJSON.MultiPolygon).coordinates);
      if (n) setFocus({ lng: sx / n, lat: sy / n, key: Date.now() });
    }).catch(() => {});
  }, [onSelect]);
  const onPickLead = flyToParcel;

  const openLead = useCallback((id: number) => {
    setView("pipeline");
    setOpenLeadId(id);
    logEvent("open_lead", { lead_id: id });
  }, []);

  // Discover map parcel lookup: search by address/id, then fly to the lot,
  // outline it (even if it's outside the current filter), and open its dossier.
  const searchTimer = useRef<number | undefined>(undefined);
  const onSearchChange = useCallback((q: string) => {
    setSearchQ(q);
    window.clearTimeout(searchTimer.current);
    if (q.trim().length < 2) { setSearchHits(null); return; }
    searchTimer.current = window.setTimeout(() => {
      searchParcels(q.trim()).then(setSearchHits).catch(() => setSearchHits([]));
    }, 250);
  }, []);
  const onPickParcel = useCallback((hit: ParcelHit) => {
    setSearchHits(null); setSearchQ("");
    logEvent("search_parcel", { parcel_id: hit.parcel_id });
    flyToParcel(hit.parcel_id);
  }, [flyToParcel]);

  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">
          Infill <span className="muted">· Hamilton County land discovery</span>
        </div>
        <nav className="view-nav">
          <button className={view === "map" ? "active" : ""} onClick={() => setView("map")}>
            Discover
          </button>
          <button className={view === "pipeline" ? "active" : ""}
            onClick={() => { setView("pipeline"); logEvent("open_pipeline"); }}>
            Pipeline{savedLeads.length ? ` (${savedLeads.length})` : ""}
          </button>
          <button className={view === "audit" ? "active" : ""}
            onClick={() => { setView("audit"); logEvent("open_audit"); }}>
            Audit
          </button>
        </nav>

        {view === "map" && (
          <div className="controls">
            <div className="map-search">
              <input className="map-search-input" placeholder="🔍 look up address or parcel id…"
                value={searchQ} onChange={(e) => onSearchChange(e.target.value)} />
              {searchHits && (
                <div className="map-search-results">
                  {searchHits.length === 0 && <div className="msr-empty muted">No matches.</div>}
                  {searchHits.map((h) => (
                    <button key={h.parcel_id} className="msr-row" onClick={() => onPickParcel(h)}>
                      <span className="msr-addr">{h.address ?? h.parcel_id}</span>
                      <span className="msr-meta">
                        {h.neighborhood ?? "—"}
                        {h.opportunity_score != null
                          ? ` · score ${Number(h.opportunity_score).toFixed(0)}` : ""}
                      </span>
                    </button>
                  ))}
                </div>
              )}
            </div>
            <div className="browse-switch">
              {(["parcels", "assemblies"] as const).map((m) => (
                <button key={m} className={browse === m ? "active" : ""}
                  onClick={() => { setBrowse(m); setOpenAsmId(null); logEvent("browse_mode", { mode: m }); }}>
                  {m === "parcels" ? "Parcels" : "Assemblies"}
                </button>
              ))}
            </div>
            <select className="region-select" value={region ?? ""}
              onChange={(e) => onRegionChange(e.target.value || null)}>
              <option value="">All target areas</option>
              {regions.map((r) => (
                <option key={r.neighborhood} value={r.neighborhood}>
                  {r.neighborhood} ({r.leads})
                </option>
              ))}
            </select>
            {browse === "assemblies" ? (
              <>
                <label className="sort-control">ownership
                  <select value={asmKind} onChange={(e) => setAsmKind(e.target.value)}>
                    <option value="">All</option>
                    <option value="same_owner">One owner</option>
                    <option value="multi_owner">Multiple owners</option>
                  </select>
                </label>
                <label className="sort-control">min size
                  <select value={asmMinAcres} onChange={(e) => setAsmMinAcres(Number(e.target.value))}>
                    {ACRE_STEPS.map((a) => <option key={a} value={a}>{a}+ acres</option>)}
                  </select>
                </label>
              </>
            ) : (<>
            <label className="sort-control">
              sort
              <select value={sort}
                onChange={(e) => onSortChange(e.target.value as SortKey, sortDesc)}>
                {SORT_OPTIONS.filter((o) => scoringOn || !o.scoreOnly).map((o) => (
                  <option key={o.key} value={o.key}>{o.label}</option>
                ))}
              </select>
              <button className="sort-dir" title={sortDesc ? "High to low" : "Low to high"}
                onClick={() => onSortChange(sort, !sortDesc)}>
                {sortDesc ? "↓" : "↑"}
              </button>
            </label>
            <button className={`score-toggle ${scoringOn ? "on" : ""}`}
              onClick={onToggleScoring}
              title={scoringOn ? "Scoring on — ranking, floor, and map colour by opportunity score"
                : "Scoring off — pure filter browsing"}>
              {scoringOn ? "◉ Scoring on" : "○ Scoring off"}
            </button>
            {scoringOn && (
              <>
                <button className={`showall-toggle ${showAll ? "on" : ""}`}
                  onClick={onToggleShowAll}
                  title={showAll ? "Showing all lots regardless of score" : "Show every lot in your filters, ignoring the score"}>
                  {showAll ? "★ All lots" : "☆ All lots"}
                </button>
                <label className={showAll ? "disabled" : ""}>
                  min score <b>{showAll ? "off" : minScore}</b>
                  <input type="range" min={0} max={100} step={5} value={minScore} disabled={showAll}
                    onChange={(e) => setMinScore(Number(e.target.value))}
                    onMouseUp={() => { void refresh(currentFilters()); void loadMap(bboxRef.current); }}
                    onTouchEnd={() => { void refresh(currentFilters()); void loadMap(bboxRef.current); }} />
                </label>
              </>
            )}
            <button className={`filters-btn ${activeFilterCount(adv) ? "active" : ""}`}
              onClick={() => setFiltersOpen((v) => !v)}>
              ⚙ Filters{activeFilterCount(adv) ? ` (${activeFilterCount(adv)})` : ""}
            </button>
            <a className="export-btn" href={exportUrl(currentFilters(), "xlsx", sort, sortDesc)} download
              onClick={() => logEvent("export", { region, sort })}>
              ⤓ Excel
            </a>
            </>)}
          </div>
        )}

        <div className="stats">
          {view === "map" && stats && (
            <>
              <span><b>{stats.parcels.toLocaleString()}</b> parcels</span>
              <span><b>{stats.scored.toLocaleString()}</b> scored</span>
              <span><b>{data?.features.length ?? 0}</b> shown</span>
            </>
          )}
          {health && <FreshnessBadge health={health} />}
          {error && <span className="error">{error}</span>}
        </div>
      </header>

      {view === "map" ? (
        <div className="body">
          <MapView data={data} scoringOn={scoringOn} onSelect={onSelect}
                   assemblages={browse === "assemblies" ? asmShapes : null}
                   fitBounds={fitBounds} onViewport={onViewport}
                   focus={focus} highlight={highlight} />
          {filtersOpen && (
            <Filters value={adv} onApply={onApplyFilters} onClose={() => setFiltersOpen(false)} />
          )}
          {dossier && (
            <Dossier dossier={dossier} onClose={() => { setDossier(null); setHighlight(null); }}
                     onSelect={onPickLead}
                     savedLeadId={savedByParcel.get(dossier.master.parcel_id)}
                     onSaved={() => { refreshLeads(); logEvent("add_lead", { parcel_id: dossier.master.parcel_id }); }}
                     onOpenLead={openLead} />
          )}
          {browse === "assemblies" ? (
            <AssemblyPanel rows={assemblies} openId={openAsmId}
                           onOpen={openAssembly} onPickParcel={onPickLead} />
          ) : (
            <TopList rows={results} total={total} region={region} sort={sort}
                     scoringOn={scoringOn} onPick={onPickLead} />
          )}
          <div className="legend">
            {browse === "assemblies" ? (
              <><span className="swatch asm-swatch" /> assembled tract</>
            ) : scoringOn ? (
              <>
                <span className="swatch" style={{ background: "#fee08b" }} /> 0
                <span className="swatch" style={{ background: "#fdae61" }} /> 50
                <span className="swatch" style={{ background: "#f46d43" }} /> 70
                <span className="swatch" style={{ background: "#d73027" }} /> 90+
              </>
            ) : (
              <><span className="swatch" style={{ background: "#4f86c6" }} /> filtered lots</>
            )}
          </div>
        </div>
      ) : view === "pipeline" ? (
        <Pipeline openLeadId={openLeadId}
          onOpenLead={(id) => { setOpenLeadId(id); if (id == null) refreshLeads(); }} />
      ) : (
        <Audit regions={regions} />
      )}
    </div>
  );
}

/** Data-freshness indicator. Green when every feed is inside its budget; when
 * something is behind, it says so plainly and lists which feed on hover. An
 * agent acting on a sheriff-sale date that already passed is how trust dies, so
 * staleness is surfaced rather than left to be discovered. */
function FreshnessBadge({ health }: { health: Health }) {
  const stale = health.sources.filter((s) => s.stale);
  const ok = health.status === "ok";
  const age = (h: number | null) => (h == null ? "never run" : h < 48
    ? `${Math.round(h)}h ago` : `${Math.round(h / 24)}d ago`);
  const tip = ok
    ? health.sources.map((s) => `${s.label}: ${age(s.age_hours)}`).join("\n")
    : `Behind schedule:\n${stale.map((s) => `· ${s.label} — ${age(s.age_hours)}`).join("\n")}`;
  return (
    <span className={`freshness ${ok ? "fresh" : "stale"}`} title={tip}>
      {ok ? "data current" : `${stale.length} feed${stale.length > 1 ? "s" : ""} behind`}
    </span>
  );
}

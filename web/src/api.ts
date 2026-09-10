// Thin typed client over the FastAPI layer (proxied at /api in dev).

export interface Stats {
  parcels: number;
  scored: number;
  signals: number;
  avg_score: string | null;
  last_scored: string | null;
}

export interface ParcelSignal {
  signal_type: string;
  event_date: string | null;
  status: string | null;
  severity: string | null;
  source: string | null;
}

export interface AssemblageMember {
  parcel_id: string;
  address: string | null;
  area_sqft: string | null;
  land_value: string | null;
  opportunity_score: string | null;
}

export interface AssemblageInfo {
  id: number;
  owner_name_norm: string | null;
  parcel_count: number;
  combined_sqft: string | null;
  combined_land_value: string | null;
  members: AssemblageMember[];
}

export interface ParcelDossier {
  master: Record<string, unknown> & {
    parcel_id: string;
    address: string | null;
    owner_name_raw: string | null;
    owner_type: string | null;
    land_value: string | null;
    improvement_value: string | null;
    il_ratio: string | null;
    is_absentee: boolean | null;
    homestead: boolean | null;
    condition_gap: string | null;   // how far below the block a house's building value/sqft sits (0-100)
    area_land_value: string | null; // median land value of nearby houses (the "is this a nice area" number)
    on_septic: boolean | null;      // in the county septic inventory = NOT on sanitary sewer
    water_main_ft: string | null;   // feet to nearest hydrant (water main proxy)
    avg_slope_pct: string | null;   // average grade across the lot, percent
    max_slope_pct: string | null;   // steepest sustained grade (95th pct)
    buildable_pct: string | null;   // share of the lot at buildable grade (<=15%)
    buildable_sqft: string | null;  // lot area x buildable share
    last_sale_date: string | null;
    last_sale_price: string | null;
  };
  scores: {
    opportunity_score: string;
    development_score: string | null;
    owner_motivation_score: string | null;
    distress_score: string | null;
    assemblage_score: string | null;
    demand_score: string | null;
    use_classification: string | null;
    scoring_config_version: string | null;
    constraints: { active?: string[] } | null;
  } | null;
  signals: ParcelSignal[];
  assemblage: AssemblageInfo | null;
  demand: {
    neighborhood: string;
    parcel_count: number;
    sales_24mo: number;
    turnover_pct: string | null;
    median_price_24mo: string | null;
    median_price_prior: string | null;
    momentum_pct: string | null;
    sale_to_value: string | null;
    demand_score: string | null;
    builds_per_1k: string | null;
    builds_recent: number | null;
    builds_prior: number | null;
    dev_trajectory_pct: string | null;
    permit_covered: boolean | null;
  } | null;
  audit_flags: { code: string; severity: string; detail: string | null }[];
  zoning: ZoningInfo | null;
  lat: number | null;
  lng: number | null;
  streetview_url: string | null;   // inline Street View image (null unless a Google Maps key is set)
}

/** Plain-English read of a parcel's zoning district, from the transcribed
 * official code. `known` is false when that district hasn't been transcribed. */
export interface ZoningInfo {
  known: boolean;
  district: string | null;
  name?: string;
  jurisdiction?: string;
  residential?: boolean | null;
  uses?: string | null;
  rules?: { label: string; value: string }[];
  /** Designations the county appends to the base code (SF-6-MH, A_HOD). They
   * modify the district rather than replace it. */
  overlays?: { code: string; name: string; effect?: string; citation?: string }[];
  summary?: string;
  citation?: string;
  url?: string;
  notes?: string[];
  reason?: string;
}

export async function fetchAssemblages(minScore: number, neighborhood: string | null = null,
                                       limit = 1000): Promise<GeoJSON.FeatureCollection> {
  const nb = neighborhood ? `&neighborhood=${encodeURIComponent(neighborhood)}` : "";
  const r = await fetch(`/api/geojson_assemblages?min_score=${minScore}${nb}&limit=${limit}`);
  if (!r.ok) throw new Error(`assemblages ${r.status}`);
  return r.json();
}

export async function fetchStats(): Promise<Stats> {
  const r = await fetch("/api/stats");
  if (!r.ok) throw new Error(`stats ${r.status}`);
  return r.json();
}

export interface FeedHealth {
  source: string;
  label: string;
  last_success: string | null;
  age_hours: number | null;
  budget_hours: number;
  stale: boolean;
}

export interface Health {
  status: "ok" | "stale" | "unknown";
  stale: string[];
  sources: FeedHealth[];
}

export async function fetchHealth(): Promise<Health> {
  const r = await fetch("/api/health");
  if (!r.ok) throw new Error(`health ${r.status}`);
  return r.json();
}

export interface NeighborhoodRow {
  neighborhood: string;
  leads: number;
  best_score: string | null;
  avg_score: string | null;
}

export async function fetchNeighborhoods(minScore: number): Promise<NeighborhoodRow[]> {
  const r = await fetch(`/api/neighborhoods?min_score=${minScore}`);
  if (!r.ok) throw new Error(`neighborhoods ${r.status}`);
  return (await r.json()).neighborhoods;
}

/** The realtor filter set, shared by the map, the results list, and the export.
 * Filter-first: the score is an optional overlay, not a gate. `minScore` (set
 * only when scoring is toggled on) re-adds a ranking floor. Every field is backed
 * by exact county data — no proxies. */
export interface LeadFilters {
  neighborhood?: string | null;
  minScore?: number | null;
  showAll?: boolean;
  useClass?: string | null;
  // property type & size
  landUse?: string | null;             // vacant / single_family / two_to_four_family / multifamily / commercial
  isVacant?: boolean | null;
  minLotSqft?: number | null;
  maxLotSqft?: number | null;
  minHomeSqft?: number | null;
  maxHomeSqft?: number | null;
  zoning?: string | null;
  minUnits?: number | null;
  maxUnits?: number | null;
  // value
  minLandValue?: number | null;
  maxLandValue?: number | null;
  minBuildingValue?: number | null;
  maxBuildingValue?: number | null;
  minTotalValue?: number | null;
  maxTotalValue?: number | null;
  minLandShare?: number | null;        // % of total value that is land
  maxLandShare?: number | null;
  minSalePrice?: number | null;
  maxSalePrice?: number | null;
  minConditionGap?: number | null;    // house looks worse than its block (0-100)
  minAreaValue?: number | null;        // area-value fold: nearby land value floor
  // ownership & motivation
  ownerType?: string | null;
  ownerName?: string | null;
  absentee?: boolean | null;
  outOfState?: boolean;                // owner mailing address not in Ohio
  homestead?: boolean;                 // owner 65+ / disabled (homestead exemption)
  ownedYearsMin?: number | null;       // long-held: owned by the same party N+ years
  transferredSince?: string | null;    // ISO date — recently transferred (e.g. into a trust)
  bankOwned?: boolean;                 // REO / lender-owned
  cmhaOwned?: boolean;                 // Cincinnati Metro Housing Authority
  portOwned?: boolean;                 // Port of Greater Cincinnati
  // distress & activity
  hasTaxLien?: boolean;
  hasCodeViolation?: boolean;
  landbank?: boolean;                  // in a land-bank / Port inventory
  hasDistress?: boolean;
  hasSheriffSale?: boolean;
  // utilities / infrastructure
  sewerStatus?: string | null;         // "sewer" | "septic"
  maxWaterFt?: number | null;          // feet to nearest hydrant (water main proxy)
  maxSlopePct?: number | null;         // average grade ceiling
  minBuildableSqft?: number | null;    // usable (sub-15%-grade) area floor
  // buildability excludes
  excludeFlood?: boolean;
  excludeSlope?: boolean;
  excludeHistoric?: boolean;
  excludeCso?: boolean;
  excludeRecentPermit?: boolean;
}

export function filterParams(f: LeadFilters): URLSearchParams {
  const p = new URLSearchParams();
  if (f.neighborhood) p.set("neighborhood", f.neighborhood);
  if (f.showAll) p.set("show_all", "true");
  if (f.minScore != null) p.set("min_score", String(f.minScore));
  if (f.useClass) p.set("use_class", f.useClass);
  if (f.landUse) p.set("land_use", f.landUse);
  if (f.isVacant != null) p.set("is_vacant", String(f.isVacant));
  if (f.minLotSqft != null) p.set("min_lot_sqft", String(f.minLotSqft));
  if (f.maxLotSqft != null) p.set("max_lot_sqft", String(f.maxLotSqft));
  if (f.minHomeSqft != null) p.set("min_home_sqft", String(f.minHomeSqft));
  if (f.maxHomeSqft != null) p.set("max_home_sqft", String(f.maxHomeSqft));
  if (f.zoning) p.set("zoning", f.zoning);
  if (f.minUnits != null) p.set("min_units", String(f.minUnits));
  if (f.maxUnits != null) p.set("max_units", String(f.maxUnits));
  if (f.minLandValue != null) p.set("min_land_value", String(f.minLandValue));
  if (f.maxLandValue != null) p.set("max_land_value", String(f.maxLandValue));
  if (f.minBuildingValue != null) p.set("min_building_value", String(f.minBuildingValue));
  if (f.maxBuildingValue != null) p.set("max_building_value", String(f.maxBuildingValue));
  if (f.minTotalValue != null) p.set("min_total_value", String(f.minTotalValue));
  if (f.maxTotalValue != null) p.set("max_total_value", String(f.maxTotalValue));
  if (f.minLandShare != null) p.set("min_land_share", String(f.minLandShare));
  if (f.maxLandShare != null) p.set("max_land_share", String(f.maxLandShare));
  if (f.minSalePrice != null) p.set("min_sale_price", String(f.minSalePrice));
  if (f.maxSalePrice != null) p.set("max_sale_price", String(f.maxSalePrice));
  if (f.minConditionGap != null) p.set("min_condition_gap", String(f.minConditionGap));
  if (f.minAreaValue != null) p.set("min_area_value", String(f.minAreaValue));
  if (f.ownerType) p.set("owner_type", f.ownerType);
  if (f.ownerName) p.set("owner_name", f.ownerName);
  if (f.absentee != null) p.set("absentee", String(f.absentee));
  if (f.outOfState) p.set("out_of_state", "true");
  if (f.homestead) p.set("homestead", "true");
  if (f.ownedYearsMin != null) p.set("owned_years_min", String(f.ownedYearsMin));
  if (f.transferredSince) p.set("transferred_since", f.transferredSince);
  if (f.bankOwned) p.set("bank_owned", "true");
  if (f.cmhaOwned) p.set("cmha_owned", "true");
  if (f.portOwned) p.set("port_owned", "true");
  if (f.hasTaxLien) p.set("has_tax_lien", "true");
  if (f.hasCodeViolation) p.set("has_code_violation", "true");
  if (f.landbank) p.set("landbank", "true");
  if (f.hasDistress) p.set("has_distress", "true");
  if (f.hasSheriffSale) p.set("has_sheriff_sale", "true");
  if (f.sewerStatus) p.set("sewer_status", f.sewerStatus);
  if (f.maxWaterFt != null) p.set("max_water_ft", String(f.maxWaterFt));
  if (f.maxSlopePct != null) p.set("max_slope_pct", String(f.maxSlopePct));
  if (f.minBuildableSqft != null) p.set("min_buildable_sqft", String(f.minBuildableSqft));
  if (f.excludeFlood) p.set("exclude_flood", "true");
  if (f.excludeSlope) p.set("exclude_slope", "true");
  if (f.excludeHistoric) p.set("exclude_historic", "true");
  if (f.excludeCso) p.set("exclude_cso", "true");
  if (f.excludeRecentPermit) p.set("exclude_recent_permit", "true");
  return p;
}

/** Count of advanced (non-score, non-region) filters currently active. */
export function activeFilterCount(f: LeadFilters): number {
  return [f.landUse, f.isVacant, f.minLotSqft, f.maxLotSqft, f.minHomeSqft,
    f.maxHomeSqft, f.zoning, f.minUnits, f.maxUnits, f.minLandValue, f.maxLandValue,
    f.minBuildingValue, f.maxBuildingValue, f.minTotalValue, f.maxTotalValue,
    f.minLandShare, f.maxLandShare, f.minSalePrice, f.maxSalePrice,
    f.minConditionGap, f.minAreaValue, f.ownerType,
    f.ownerName, f.absentee, f.outOfState, f.homestead, f.ownedYearsMin, f.transferredSince,
    f.bankOwned, f.cmhaOwned, f.portOwned, f.hasTaxLien, f.hasCodeViolation,
    f.landbank, f.hasDistress, f.hasSheriffSale, f.sewerStatus, f.maxWaterFt,
    f.maxSlopePct, f.minBuildableSqft,
    f.excludeFlood, f.excludeSlope,
    f.excludeHistoric, f.excludeCso, f.excludeRecentPermit, f.useClass]
    .filter((v) => v != null && v !== false && v !== "").length;
}

/** Sort options for the results list. `score` only appears when scoring is on. */
export type SortKey =
  | "opportunity_score" | "land_value" | "building_value" | "total_value"
  | "lot_sqft" | "home_sqft" | "land_share" | "last_sale_date"
  | "last_sale_price" | "years_owned" | "distress_score" | "condition_gap";

export interface LeadRow {
  parcel_id: string;
  address: string | null;
  neighborhood: string | null;
  owner_name_raw: string | null;
  owner_type: string | null;
  land_use_code: string | null;
  zoning_code: string | null;
  land_value: string | null;
  improvement_value: string | null;
  total_value: string | null;
  land_share: string | null;
  area_sqft: string | null;
  home_sqft: string | null;
  num_units: string | null;
  last_sale_date: string | null;
  last_sale_price: string | null;
  years_owned: string | null;
  condition_gap: string | null;
  area_land_value: string | null;
  opportunity_score: string | null;
  development_score: string | null;
  distress_score: string | null;
  use_classification: string | null;
}

/** The filtered results list (map + side list). Returns the total match count
 * (independent of the page limit) plus this page of rows, sorted server-side. */
export async function fetchLeadList(
  f: LeadFilters, sort: SortKey, desc = true, limit = 200,
): Promise<{ total: number; results: LeadRow[] }> {
  const p = filterParams(f);
  p.set("sort", sort); p.set("desc", String(desc)); p.set("limit", String(limit));
  const r = await fetch(`/api/parcels?${p}`);
  if (!r.ok) throw new Error(`parcels ${r.status}`);
  const j = await r.json();
  return { total: j.total ?? j.results.length, results: j.results };
}

export function exportUrl(f: LeadFilters, fmt: "xlsx" | "csv" = "xlsx",
                          sort: SortKey = "land_value", desc = true): string {
  const p = filterParams(f);
  p.set("fmt", fmt); p.set("sort", sort); p.set("desc", String(desc));
  return `/api/export?${p}`;
}

export async function fetchGeojson(f: LeadFilters,
                                   opts: { bbox?: string; limit?: number } = {}): Promise<GeoJSON.FeatureCollection> {
  const p = filterParams(f); p.set("limit", String(opts.limit ?? 6000));
  if (opts.bbox) p.set("bbox", opts.bbox);
  const r = await fetch(`/api/geojson?${p}`);
  if (!r.ok) throw new Error(`geojson ${r.status}`);
  return r.json();
}

/** Bounding box [w,s,e,n] of a region's parcels (or the county when null). */
export async function fetchRegionBounds(name: string | null):
  Promise<[number, number, number, number] | null> {
  const q = name ? `?name=${encodeURIComponent(name)}` : "";
  const r = await fetch(`/api/neighborhoods/bounds${q}`);
  if (!r.ok) return null;
  const b = await r.json();
  return [b.w, b.s, b.e, b.n];
}

export async function fetchParcel(parcelId: string): Promise<ParcelDossier> {
  const r = await fetch(`/api/parcels/${encodeURIComponent(parcelId)}`);
  if (!r.ok) throw new Error(`parcel ${r.status}`);
  return r.json();
}

export interface ParcelHit {
  parcel_id: string;
  address: string | null;
  neighborhood: string | null;
  zoning_code: string | null;
  opportunity_score: string | null;
  use_classification: string | null;
  lat: number;
  lng: number;
}

/** Look up any parcel by address or id (Discover map search box). */
export async function searchParcels(q: string): Promise<ParcelHit[]> {
  const r = await fetch(`/api/parcels/search?q=${encodeURIComponent(q)}`);
  if (!r.ok) throw new Error(`parcel search ${r.status}`);
  return (await r.json()).results;
}

/** A single parcel's geometry, so the map can outline a looked-up lot even when
 * it's outside the current score/region filter. */
export async function fetchParcelGeojson(parcelId: string): Promise<GeoJSON.FeatureCollection> {
  const r = await fetch(`/api/parcels/${encodeURIComponent(parcelId)}/geojson`);
  if (!r.ok) throw new Error(`parcel geojson ${r.status}`);
  return r.json();
}

// --- assemblies (multi-parcel tracts) ---

export interface Assembly {
  id: number;
  kind: "same_owner" | "multi_owner";
  owner_name_norm: string | null;
  parcel_count: number;
  owner_count: number;
  neighborhood: string | null;
  combined_acres: string | null;
  combined_sqft: string | null;
  combined_land_value: string | null;
  vacant_members: number;
  zoning: string | null;
}

export interface AssemblyMember {
  parcel_id: string;
  address: string | null;
  owner_name_raw: string | null;
  owner_type: string | null;
  zoning_code: string | null;
  area_sqft: string | null;
  acres: string | null;
  land_value: string | null;
  improvement_value: string | null;
  has_structure: boolean | null;
  on_septic: boolean | null;
  opportunity_score: string | null;
  use_classification: string | null;
}

export async function fetchAssemblies(opts: {
  kind?: string | null; minAcres?: number; neighborhood?: string | null; sort?: string;
} = {}): Promise<Assembly[]> {
  const p = new URLSearchParams({ min_acres: String(opts.minAcres ?? 3) });
  if (opts.kind) p.set("kind", opts.kind);
  if (opts.neighborhood) p.set("neighborhood", opts.neighborhood);
  if (opts.sort) p.set("sort", opts.sort);
  const r = await fetch(`/api/assemblies?${p}`);
  if (!r.ok) throw new Error(`assemblies ${r.status}`);
  return (await r.json()).assemblies;
}

export async function fetchAssembly(id: number): Promise<{ assembly: Assembly; members: AssemblyMember[] }> {
  const r = await fetch(`/api/assemblies/${id}`);
  if (!r.ok) throw new Error(`assembly ${r.status}`);
  return r.json();
}

// --- lead pipeline (CRM funnel) ---

export type Stage = "prospect" | "diligence" | "outreach" | "responded" | "handed_off" | "passed";

export const STAGE_ORDER: Stage[] = ["prospect", "diligence", "outreach", "responded", "handed_off"];

export const STAGE_META: Record<Stage, { label: string; hint: string; color: string }> = {
  prospect: { label: "Prospect", hint: "Found, not yet vetted", color: "#3b82f6" },
  diligence: { label: "Diligence", hint: "Vetting the parcel", color: "#8b5cf6" },
  outreach: { label: "Outreach", hint: "Letter sent, awaiting reply", color: "#f59e0b" },
  responded: { label: "Responded", hint: "Owner replied", color: "#10b981" },
  handed_off: { label: "Handed off", hint: "Moved to your CRM", color: "#0ea5e9" },
  passed: { label: "Passed", hint: "Not pursuing", color: "#6b7280" },
};

export type ResponseOutcome = "positive" | "negative";

/** A pipeline card as returned by GET /leads (live parcel metrics joined in). */
export interface LeadCard {
  id: number;
  parcel_id: string;
  stage: Stage;
  added_score: string | null;
  offer_amount: string | null;
  close_price: string | null;
  response_outcome: ResponseOutcome | null;
  responded_at: string | null;
  updated_at: string;
  created_at: string;
  address: string | null;
  neighborhood: string | null;
  zoning_code: string | null;
  owner_name_raw: string | null;
  land_value: string | null;
  condition_gap: string | null;
  homestead: boolean | null;
  opportunity_score: string | null;
  use_classification: string | null;
  demand_score: string | null;
  entry_count: number;
  photo_count: number;
  latest_note: string | null;
  last_outreach_at: string | null;
  last_outreach_status: string | null;
}

export interface Lead {
  id: number;
  parcel_id: string;
  stage: Stage;
  added_score: string | null;
  response_outcome: ResponseOutcome | null;
  responded_at: string | null;
  diligence: Record<string, boolean> | null;
  offer_amount: string | null;
  offer_date: string | null;
  deal_terms: string | null;
  close_price: string | null;
  close_date: string | null;
  created_at: string;
  updated_at: string;
}

export interface LeadEntry {
  id: number;
  stage: Stage;
  kind: "note" | "photo";
  body: string | null;
  photo_name: string | null;
  photo_mime: string | null;
  created_at: string;
}

export interface LeadOutreach {
  status: "generated" | "sent" | "responded";
  sent_at: string | null;
  responded_at: string | null;
  outcome: string | null;
}

export interface LeadDetail {
  lead: Lead;
  dossier: ParcelDossier;
  entries: LeadEntry[];
  stages: Stage[];
  outreach: LeadOutreach | null;
}

export async function fetchLeads(): Promise<LeadCard[]> {
  const r = await fetch("/api/leads");
  if (!r.ok) throw new Error(`leads ${r.status}`);
  return (await r.json()).leads;
}

/** Download the saved pipeline (optionally one stage) as a spreadsheet: every
 * fact spread across columns, plus objective 'criteria' flags (owned 30+ yr,
 * individual owner, tax delinquent, ...) that show what a person might have
 * filtered for to reach each property. */
export function pipelineExportUrl(stage: Stage | null = null, fmt: "xlsx" | "csv" = "xlsx"): string {
  const p = new URLSearchParams({ fmt });
  if (stage) p.set("stage", stage);
  return `/api/leads/export?${p}`;
}

/** The single-letter PDF URL (opens/downloads a print-ready outreach letter). */
export function leadLetterUrl(id: number): string {
  return `/api/leads/${id}/letter.pdf`;
}

/** The same letter as an editable Word document. */
export function leadLetterDocxUrl(id: number): string {
  return `/api/leads/${id}/letter.docx`;
}

/** Mark the lead's current letter as mailed → advances it to the Outreach stage. */
export async function markOutreachSent(id: number): Promise<void> {
  const r = await fetch(`/api/leads/${id}/outreach/sent`, { method: "POST" });
  if (!r.ok) throw new Error(`outreach sent ${r.status}`);
}

/** Undo 'mark sent' → reverts the letter to un-sent (and Outreach back to Diligence). */
export async function markOutreachUnsent(id: number): Promise<void> {
  const r = await fetch(`/api/leads/${id}/outreach/unsent`, { method: "POST" });
  if (!r.ok) throw new Error(`outreach unsent ${r.status}`);
}

/** POST a set of lead ids and download the combined multi-page letter PDF. */
export async function downloadLettersBatch(
  ids: number[], fmt: "pdf" | "docx" = "pdf",
): Promise<void> {
  const r = await fetch(`/api/leads/letters?fmt=${fmt}`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ lead_ids: ids }),
  });
  if (!r.ok) throw new Error(`letters ${r.status}`);
  const blob = await r.blob();
  // honour the server's filename — it files a single letter by property address
  const cd = r.headers.get("content-disposition") ?? "";
  const named = /filename="([^"]+)"/.exec(cd)?.[1];
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url; a.download = named || "outreach_letters.pdf"; a.click();
  URL.revokeObjectURL(url);
}

export async function addLead(parcelId: string): Promise<{ id: number; created: boolean }> {
  const r = await fetch("/api/leads", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ parcel_id: parcelId }),
  });
  if (!r.ok) throw new Error(`add lead ${r.status}`);
  return r.json();
}

export async function fetchLead(id: number): Promise<LeadDetail> {
  const r = await fetch(`/api/leads/${id}`);
  if (!r.ok) throw new Error(`lead ${r.status}`);
  return r.json();
}

export type LeadUpdate = Partial<{
  stage: Stage;
  response_outcome: ResponseOutcome | null;
  responded_at: string | null;
  diligence: Record<string, boolean>;
  offer_amount: number | null;
  offer_date: string | null;
  deal_terms: string | null;
  close_price: number | null;
  close_date: string | null;
}>;

export async function updateLead(id: number, patch: LeadUpdate): Promise<Lead> {
  const r = await fetch(`/api/leads/${id}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(patch),
  });
  if (!r.ok) throw new Error(`update lead ${r.status}`);
  return r.json();
}

export async function deleteLead(id: number): Promise<void> {
  const r = await fetch(`/api/leads/${id}`, { method: "DELETE" });
  if (!r.ok) throw new Error(`delete lead ${r.status}`);
}

export async function addEntry(leadId: number, stage: Stage, body: string): Promise<LeadEntry> {
  const r = await fetch(`/api/leads/${leadId}/entries`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ stage, body }),
  });
  if (!r.ok) throw new Error(`add entry ${r.status}`);
  return r.json();
}

export async function uploadPhoto(leadId: number, stage: Stage, file: File,
                                  caption: string): Promise<LeadEntry> {
  const form = new FormData();
  form.append("stage", stage);
  form.append("caption", caption);
  form.append("file", file);
  const r = await fetch(`/api/leads/${leadId}/photos`, { method: "POST", body: form });
  if (!r.ok) throw new Error(`upload photo ${r.status}`);
  return r.json();
}

export async function deleteEntry(leadId: number, entryId: number): Promise<void> {
  const r = await fetch(`/api/leads/${leadId}/entries/${entryId}`, { method: "DELETE" });
  if (!r.ok) throw new Error(`delete entry ${r.status}`);
}

/** URL the browser can load a lead photo from (owner-gated by the API). */
export function photoUrl(leadId: number, entryId: number): string {
  return `/api/leads/${leadId}/photos/${entryId}`;
}

// --- lead audit (Phase 0: grounding the board in reality) ---

export type Verdict = "real" | "fake" | "maybe";

/** Fake-reason taxonomy for the review dropdown (mirrors the API). */
export const FAKE_REASONS: { code: string; label: string }[] = [
  { code: "not_acquirable", label: "Not acquirable (public/exempt)" },
  { code: "institutional", label: "Institutional (church/school/hospital/cemetery)" },
  { code: "public_park", label: "Park / public open space" },
  { code: "operating_amenity", label: "Operating amenity (golf/club)" },
  { code: "commercial_land", label: "Commercial land (parking/gas/outparcel)" },
  { code: "occupied_commercial", label: "Occupied commercial/industrial in use" },
  { code: "complex_satellite", label: "Apartment/complex bookkeeping parcel" },
  { code: "condo_unit", label: "Condo unit (shared footprint)" },
  { code: "subdivision_inventory", label: "Builder subdivision inventory" },
  { code: "hoa_common_area", label: "HOA common area / reserve" },
  { code: "utility_infrastructure", label: "Utility / stormwater / ROW" },
  { code: "landlocked", label: "Landlocked / no road frontage" },
  { code: "wetland", label: "Wetland / conservation" },
  { code: "large_acreage_non_infill", label: "Large acreage (not infill)" },
  { code: "township_rural", label: "Township / rural (not infill)" },
  { code: "permit_issued", label: "Already being developed" },
  { code: "regulatory_floodway", label: "Floodway (can't build)" },
  { code: "other", label: "Other (see note)" },
];

export interface QueueItem {
  parcel_id: string;
  address: string | null;
  neighborhood: string | null;
  zoning_code: string | null;
  opportunity_score: string;
  use_classification: string | null;
  lat: number;
  lng: number;
  hint_label: string | null;
  hint_reason: string | null;
  hint_source?: string | null;   // 'review'/'agent' => already human-labelled
}

export interface PrecisionBucket {
  n: number; real: number; fake: number; maybe: number; unknown: number;
  precision: number | null; reviewed_pct: number;
}
export interface Precision {
  overall: Record<string, PrecisionBucket>;
  per_neighborhood: Record<string, PrecisionBucket>;
  total_labels: number;
}

export async function fetchAuditQueue(opts: {
  neighborhood?: string | null; minScore?: number; maxScore?: number;
  limit?: number; offset?: number;
} = {}): Promise<QueueItem[]> {
  const q = new URLSearchParams({
    limit: String(opts.limit ?? 25),
    offset: String(opts.offset ?? 0),
    min_score: String(opts.minScore ?? 0),
    max_score: String(opts.maxScore ?? 100),
  });
  if (opts.neighborhood) q.set("neighborhood", opts.neighborhood);
  const r = await fetch(`/api/audit/queue?${q}`);
  if (!r.ok) throw new Error(`audit queue ${r.status}`);
  return (await r.json()).queue;
}

export async function searchAudit(q: string): Promise<QueueItem[]> {
  const r = await fetch(`/api/audit/search?q=${encodeURIComponent(q)}`);
  if (!r.ok) throw new Error(`audit search ${r.status}`);
  return (await r.json()).results;
}

export async function fetchPrecision(): Promise<Precision> {
  const r = await fetch("/api/audit/precision");
  if (!r.ok) throw new Error(`precision ${r.status}`);
  return r.json();
}

export async function postLabel(parcelId: string, label: Verdict,
                                reasonCode: string | null, notes: string): Promise<void> {
  const r = await fetch("/api/audit/label", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ parcel_id: parcelId, label, reason_code: reasonCode, notes }),
  });
  if (!r.ok) throw new Error(`label ${r.status}`);
}

/** Google Maps deep-links (no API key needed) for a fast on-the-ground read. */
export function streetViewUrl(lat: number, lng: number): string {
  return `https://www.google.com/maps/@?api=1&map_action=pano&viewpoint=${lat},${lng}`;
}
export function aerialUrl(lat: number, lng: number): string {
  return `https://www.google.com/maps/place/${lat},${lng}/@${lat},${lng},19z/data=!3m1!1e3`;
}

// --- preferences (home region) + usage instrumentation ---

export async function fetchHomeRegion(): Promise<string | null> {
  const r = await fetch("/api/prefs");
  if (!r.ok) return null;
  return (await r.json()).home_region ?? null;
}

export async function saveHomeRegion(region: string | null): Promise<void> {
  await fetch("/api/prefs", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ home_region: region }),
  }).catch(() => {});
}

/** Fire-and-forget usage event; never throws, never blocks the UI. */
export function logEvent(event: string, detail?: Record<string, unknown>): void {
  void fetch("/api/events", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ event, detail: detail ?? {} }),
  }).catch(() => {});
}

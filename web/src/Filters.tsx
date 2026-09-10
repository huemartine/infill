import { useState } from "react";
import { LeadFilters } from "./api";

const OWNER_TYPES = [
  { v: "", label: "Any owner type" },
  { v: "individual", label: "Individual" },
  { v: "llc", label: "LLC / company" },
  { v: "trust", label: "Trust" },
  { v: "institutional", label: "Institutional" },
  { v: "public", label: "Public / government" },
];

const LAND_USE = [
  { v: "", label: "Any property type" },
  { v: "vacant", label: "Vacant land" },
  { v: "single_family", label: "Single-family" },
  { v: "two_to_four_family", label: "2–4 family" },
  { v: "multifamily", label: "Multifamily / apartments" },
  { v: "commercial", label: "Commercial" },
];

const TRANSFER_OPTS = [
  { v: "", label: "Any time" },
  { v: "1", label: "Last 1 year" },
  { v: "2", label: "Last 2 years" },
  { v: "3", label: "Last 3 years" },
  { v: "5", label: "Last 5 years" },
];

const yearsAgoISO = (n: number): string => {
  const d = new Date();
  d.setFullYear(d.getFullYear() - n);
  return d.toISOString().slice(0, 10);
};
// map a stored ISO cutoff back to the nearest preset for the select
const isoToYears = (iso: string | null | undefined): string => {
  if (!iso) return "";
  for (const n of [1, 2, 3, 5]) if (yearsAgoISO(n) === iso) return String(n);
  return "";
};

/** The realtor filter panel — the full county-data catalog. Every filter is
 * backed by exact data (no proxies); combine them freely to dig for a specific
 * profile. Filter-first: these stand on their own, independent of the score. */
export default function Filters({ value, onApply, onClose }: {
  value: LeadFilters;
  onApply: (f: LeadFilters) => void;
  onClose: () => void;
}) {
  const [d, setD] = useState<LeadFilters>(value);
  const set = (patch: Partial<LeadFilters>) => setD((p) => ({ ...p, ...patch }));
  const num = (v: string): number | null => (v === "" ? null : Number(v));
  const triState = (v: string): boolean | null => (v === "" ? null : v === "yes");
  const triVal = (b: boolean | null | undefined) => (b == null ? "" : b ? "yes" : "no");

  return (
    <div className="filters-panel">
      <div className="filters-head">
        <span>⚙ Filters</span>
        <button className="close" onClick={onClose}>×</button>
      </div>
      <div className="filters-body">

        <div className="f-section-label">Ownership &amp; motivation</div>
        <label className="f-field">Owner name contains
          <input type="text" placeholder="e.g. SMITH" value={d.ownerName ?? ""}
            onChange={(e) => set({ ownerName: e.target.value || null })} />
        </label>
        <label className="f-field">Owner type
          <select value={d.ownerType ?? ""} onChange={(e) => set({ ownerType: e.target.value || null })}>
            {OWNER_TYPES.map((o) => <option key={o.v} value={o.v}>{o.label}</option>)}
          </select>
        </label>
        <div className="f-row">
          <label className="f-field">Absentee owner
            <select value={triVal(d.absentee)} onChange={(e) => set({ absentee: triState(e.target.value) })}>
              <option value="">Any</option><option value="yes">Absentee</option>
              <option value="no">Owner-occupied</option>
            </select>
          </label>
          <label className="f-field">Owned for
            <select value={d.ownedYearsMin ?? ""}
              onChange={(e) => set({ ownedYearsMin: e.target.value ? Number(e.target.value) : null })}>
              <option value="">Any length</option>
              <option value="10">10+ years</option>
              <option value="20">20+ years</option>
              <option value="30">30+ years</option>
              <option value="40">40+ years</option>
            </select>
          </label>
        </div>
        <label className="f-field">Recently transferred
          <select value={isoToYears(d.transferredSince)}
            onChange={(e) => set({ transferredSince: e.target.value ? yearsAgoISO(Number(e.target.value)) : null })}>
            {TRANSFER_OPTS.map((o) => <option key={o.v} value={o.v}>{o.label}</option>)}
          </select>
        </label>
        <div className="f-checks">
          <label><input type="checkbox" checked={!!d.homestead}
            onChange={(e) => set({ homestead: e.target.checked })} /> Senior owner (65+ homestead exemption)</label>
          <label><input type="checkbox" checked={!!d.outOfState}
            onChange={(e) => set({ outOfState: e.target.checked })} /> Owner mailing address out of state</label>
          <label><input type="checkbox" checked={!!d.bankOwned}
            onChange={(e) => set({ bankOwned: e.target.checked })} /> Bank / lender-owned (REO)</label>
          <label><input type="checkbox" checked={!!d.cmhaOwned}
            onChange={(e) => set({ cmhaOwned: e.target.checked })} /> CMHA (Metro Housing Authority)</label>
          <label><input type="checkbox" checked={!!d.portOwned}
            onChange={(e) => set({ portOwned: e.target.checked })} /> Port of Greater Cincinnati</label>
        </div>
        <div className="f-hint">Tip: <b>Trust</b> owner + a recent transfer ≈ recently deeded into a trust.</div>

        <div className="f-section-label">Property type &amp; size</div>
        <label className="f-field">Property type
          <select value={d.landUse ?? ""} onChange={(e) => set({ landUse: e.target.value || null })}>
            {LAND_USE.map((o) => <option key={o.v} value={o.v}>{o.label}</option>)}
          </select>
        </label>
        <div className="f-row">
          <label className="f-field">Vacancy
            <select value={triVal(d.isVacant)} onChange={(e) => set({ isVacant: triState(e.target.value) })}>
              <option value="">Any</option><option value="yes">Vacant only</option>
              <option value="no">Has a building</option>
            </select>
          </label>
          <label className="f-field">Zoning contains
            <input type="text" placeholder="e.g. SF, RM, CC" value={d.zoning ?? ""}
              onChange={(e) => set({ zoning: e.target.value || null })} />
          </label>
        </div>
        <div className="f-row">
          <label className="f-field">Lot size min (sqft)
            <input type="number" inputMode="numeric" placeholder="0" value={d.minLotSqft ?? ""}
              onChange={(e) => set({ minLotSqft: num(e.target.value) })} />
          </label>
          <label className="f-field">Lot size max (sqft)
            <input type="number" inputMode="numeric" placeholder="any" value={d.maxLotSqft ?? ""}
              onChange={(e) => set({ maxLotSqft: num(e.target.value) })} />
          </label>
        </div>
        <div className="f-row">
          <label className="f-field">House size min (sqft)
            <input type="number" inputMode="numeric" placeholder="0" value={d.minHomeSqft ?? ""}
              onChange={(e) => set({ minHomeSqft: num(e.target.value) })} />
          </label>
          <label className="f-field">House size max (sqft)
            <input type="number" inputMode="numeric" placeholder="any" value={d.maxHomeSqft ?? ""}
              onChange={(e) => set({ maxHomeSqft: num(e.target.value) })} />
          </label>
        </div>
        <div className="f-row">
          <label className="f-field">Units min
            <input type="number" inputMode="numeric" placeholder="0" value={d.minUnits ?? ""}
              onChange={(e) => set({ minUnits: num(e.target.value) })} />
          </label>
          <label className="f-field">Units max
            <input type="number" inputMode="numeric" placeholder="any" value={d.maxUnits ?? ""}
              onChange={(e) => set({ maxUnits: num(e.target.value) })} />
          </label>
        </div>

        <div className="f-section-label">Value</div>
        <div className="f-row">
          <label className="f-field">Land value min $
            <input type="number" inputMode="numeric" placeholder="0" value={d.minLandValue ?? ""}
              onChange={(e) => set({ minLandValue: num(e.target.value) })} />
          </label>
          <label className="f-field">Land value max $
            <input type="number" inputMode="numeric" placeholder="any" value={d.maxLandValue ?? ""}
              onChange={(e) => set({ maxLandValue: num(e.target.value) })} />
          </label>
        </div>
        <div className="f-row">
          <label className="f-field">Building value min $
            <input type="number" inputMode="numeric" placeholder="0" value={d.minBuildingValue ?? ""}
              onChange={(e) => set({ minBuildingValue: num(e.target.value) })} />
          </label>
          <label className="f-field">Building value max $
            <input type="number" inputMode="numeric" placeholder="any" value={d.maxBuildingValue ?? ""}
              onChange={(e) => set({ maxBuildingValue: num(e.target.value) })} />
          </label>
        </div>
        <div className="f-row">
          <label className="f-field">Total value min $
            <input type="number" inputMode="numeric" placeholder="0" value={d.minTotalValue ?? ""}
              onChange={(e) => set({ minTotalValue: num(e.target.value) })} />
          </label>
          <label className="f-field">Total value max $
            <input type="number" inputMode="numeric" placeholder="any" value={d.maxTotalValue ?? ""}
              onChange={(e) => set({ maxTotalValue: num(e.target.value) })} />
          </label>
        </div>
        <div className="f-row">
          <label className="f-field">Land share min %
            <input type="number" inputMode="numeric" placeholder="0" min={0} max={100} value={d.minLandShare ?? ""}
              onChange={(e) => set({ minLandShare: num(e.target.value) })} />
          </label>
          <label className="f-field">Land share max %
            <input type="number" inputMode="numeric" placeholder="100" min={0} max={100} value={d.maxLandShare ?? ""}
              onChange={(e) => set({ maxLandShare: num(e.target.value) })} />
          </label>
        </div>
        <div className="f-hint">Land share = land ÷ total value. A high share (small house, valuable lot) is the teardown profile.</div>
        <div className="f-row">
          <label className="f-field">Last sale price min $
            <input type="number" inputMode="numeric" placeholder="0" value={d.minSalePrice ?? ""}
              onChange={(e) => set({ minSalePrice: num(e.target.value) })} />
          </label>
          <label className="f-field">Last sale price max $
            <input type="number" inputMode="numeric" placeholder="any" value={d.maxSalePrice ?? ""}
              onChange={(e) => set({ maxSalePrice: num(e.target.value) })} />
          </label>
        </div>

        <div className="f-section-label">Diamond in the rough</div>
        <div className="f-row">
          <label className="f-field">Worse than its block ≥
            <input type="number" inputMode="numeric" placeholder="40" min={0} max={100}
              value={d.minConditionGap ?? ""}
              onChange={(e) => set({ minConditionGap: num(e.target.value) })} />
          </label>
          <label className="f-field">Area land value min $
            <input type="number" inputMode="numeric" placeholder="any" value={d.minAreaValue ?? ""}
              onChange={(e) => set({ minAreaValue: num(e.target.value) })} />
          </label>
        </div>
        <div className="f-hint">
          A run-down house (low building value for its block, 0–100) in a valuable area —
          the teardown you'd spot driving around. Single-family only.
        </div>

        <div className="f-section-label">Distress &amp; activity</div>
        <div className="f-checks">
          <label><input type="checkbox" checked={!!d.hasTaxLien}
            onChange={(e) => set({ hasTaxLien: e.target.checked })} /> Tax lien / delinquency</label>
          <label><input type="checkbox" checked={!!d.hasCodeViolation}
            onChange={(e) => set({ hasCodeViolation: e.target.checked })} /> Code violation</label>
          <label><input type="checkbox" checked={!!d.hasSheriffSale}
            onChange={(e) => set({ hasSheriffSale: e.target.checked })} /> Upcoming sheriff sale</label>
          <label><input type="checkbox" checked={!!d.landbank}
            onChange={(e) => set({ landbank: e.target.checked })} /> In a land-bank / Port inventory</label>
          <label><input type="checkbox" checked={!!d.hasDistress}
            onChange={(e) => set({ hasDistress: e.target.checked })} /> Any distress signal</label>
        </div>

        <div className="f-section-label">Utilities &amp; infrastructure</div>
        <label className="f-field">Sewer service
          <select value={d.sewerStatus ?? ""} onChange={(e) => set({ sewerStatus: e.target.value || null })}>
            <option value="">Any</option>
            <option value="sewer">Public sewer likely</option>
            <option value="septic">On private septic (no sewer)</option>
          </select>
        </label>
        <label className="f-field">Water main within (ft)
          <input type="number" inputMode="numeric" placeholder="any" value={d.maxWaterFt ?? ""}
            onChange={(e) => set({ maxWaterFt: num(e.target.value) })} />
        </label>
        <div className="f-row">
          <label className="f-field">Max average grade (%)
            <input type="number" inputMode="numeric" placeholder="any" value={d.maxSlopePct ?? ""}
              onChange={(e) => set({ maxSlopePct: num(e.target.value) })} />
          </label>
          <label className="f-field">Min buildable area (sqft)
            <input type="number" inputMode="numeric" placeholder="any" value={d.minBuildableSqft ?? ""}
              onChange={(e) => set({ minBuildableSqft: num(e.target.value) })} />
          </label>
        </div>
        <div className="f-hint">
          Grade and buildable area come from the county's 2-ft elevation model; buildable
          area is the share of the lot at or under a 15% grade.
        </div>
        <div className="f-hint">
          Septic status is the county health-department inventory: listed = no sanitary sewer.
          Not listed is strong evidence of sewer in built-up areas, but verify out in the townships.
          Water is distance to the nearest fire hydrant.
        </div>

        <div className="f-section-label">Buildability (exclude)</div>
        <div className="f-checks">
          <label><input type="checkbox" checked={!!d.excludeFlood}
            onChange={(e) => set({ excludeFlood: e.target.checked })} /> Exclude floodplain</label>
          <label><input type="checkbox" checked={!!d.excludeSlope}
            onChange={(e) => set({ excludeSlope: e.target.checked })} /> Exclude steep slope</label>
          <label><input type="checkbox" checked={!!d.excludeHistoric}
            onChange={(e) => set({ excludeHistoric: e.target.checked })} /> Exclude historic district</label>
          <label><input type="checkbox" checked={!!d.excludeCso}
            onChange={(e) => set({ excludeCso: e.target.checked })} /> Exclude CSO area</label>
          <label><input type="checkbox" checked={!!d.excludeRecentPermit}
            onChange={(e) => set({ excludeRecentPermit: e.target.checked })} /> Exclude lots with a recent building permit</label>
        </div>

        <div className="f-note">
          Mortgage payoff, full liens, and reverse mortgages need county Recorder
          records we don't yet ingest — coming in a later data feed.
        </div>
      </div>

      <div className="filters-actions">
        <button className="ghost-btn" onClick={() => setD({})}>Clear</button>
        <button className="apply-btn" onClick={() => onApply(d)}>Apply filters</button>
      </div>
    </div>
  );
}

import { LeadRow, SortKey } from "./api";
import { fmtSqft, fmtUsd } from "./format";

const scoreColor = (s: number) =>
  s >= 90 ? "#d73027" : s >= 70 ? "#f46d43" : s >= 50 ? "#fdae61" : "#fee08b";

/** Short, realtor-readable land-use label from the Ohio DTE class code. */
function landUseLabel(code: string | null): string {
  const n = code ? parseInt(code.replace(/\D/g, ""), 10) : NaN;
  if (isNaN(n)) return "—";
  if ([300, 400, 500].includes(n)) return "vacant land";
  if (n >= 510 && n <= 519) return "single-family";
  if (n >= 520 && n <= 549) return "2–4 family";
  if ((n >= 401 && n <= 403) || (n >= 550 && n <= 599)) return "multifamily";
  if (n >= 300 && n <= 399) return "industrial";
  if (n >= 404 && n <= 499) return "commercial";
  return "—";
}

/** The primary value shown at the right of each row, matching the active sort —
 * so sorting by land value shows land value, by tenure shows years owned, etc. */
function metric(row: LeadRow, sort: SortKey): string {
  switch (sort) {
    case "building_value": return fmtUsd(row.improvement_value);
    case "total_value": return fmtUsd(row.total_value);
    case "last_sale_price": return fmtUsd(row.last_sale_price);
    case "lot_sqft": return fmtSqft(row.area_sqft as string | number | null);
    case "home_sqft": return row.home_sqft ? fmtSqft(row.home_sqft as string | number) : "—";
    case "land_share": return row.land_share != null ? `${Math.round(Number(row.land_share) * 100)}%` : "—";
    case "years_owned": return row.years_owned != null ? `${row.years_owned} yr` : "—";
    case "last_sale_date": return row.last_sale_date ?? "—";
    case "distress_score": return row.distress_score != null ? Number(row.distress_score).toFixed(0) : "—";
    default: return fmtUsd(row.land_value);
  }
}

/** The filtered-results side list. Filter-first: it lists every lot matching the
 * realtor's criteria (not a top-N by score), shows the total match count, and —
 * when scoring is off — a right-hand value that tracks the active sort. With
 * scoring on, the opportunity score comes back as the coloured badge. */
export default function TopList({ rows, total, region, sort, scoringOn, onPick }: {
  rows: LeadRow[];
  total: number;
  region: string | null;
  sort: SortKey;
  scoringOn: boolean;
  onPick: (parcelId: string) => void;
}) {
  if (!rows.length) return null;
  return (
    <div className="toplist">
      <div className="toplist-title">
        <b>{total.toLocaleString()}</b> lot{total === 1 ? "" : "s"} · {region ?? "all target areas"}
        {rows.length < total && <span className="toplist-sub"> · showing {rows.length}</span>}
      </div>
      {rows.map((l) => (
        <button className="toplist-row" key={l.parcel_id} onClick={() => onPick(l.parcel_id)}>
          <span className="toplist-addr">
            {l.address ?? l.parcel_id}
            <span className="toplist-sub">
              {landUseLabel(l.land_use_code)} · {fmtUsd(l.land_value)} land
              {l.area_sqft ? ` · ${fmtSqft(l.area_sqft as string | number)}` : ""}
              {l.years_owned != null ? ` · ${l.years_owned} yr` : ""}
            </span>
          </span>
          {scoringOn && l.opportunity_score != null ? (
            <span className="toplist-score" style={{ background: scoreColor(Number(l.opportunity_score)) }}>
              {Number(l.opportunity_score).toFixed(0)}
            </span>
          ) : (
            <span className="toplist-metric">{metric(l, sort)}</span>
          )}
        </button>
      ))}
    </div>
  );
}

import { useEffect, useState } from "react";
import { Assembly, AssemblyMember, fetchAssembly, logEvent } from "./api";
import { fmtNum, fmtUsd } from "./format";

/** The county records some parcels with no valuation at all. Rendering that as
 * "$0" reads as worthless when it actually means unknown. */
const landValue = (v: string | null): string =>
  v == null || Number(v) === 0 ? "value not on record" : `${fmtUsd(v)} land`;

/** The map's side panel when browsing Assemblies: a compact list of tracts,
 * drilling into one tract's member parcels. Mirrors TopList's role for parcels
 * so the map page has one consistent shape whichever mode you're in. */
export default function AssemblyPanel({ rows, openId, onOpen, onPickParcel }: {
  rows: Assembly[];
  openId: number | null;
  onOpen: (a: Assembly | null) => void;
  onPickParcel: (parcelId: string) => void;
}) {
  const [members, setMembers] = useState<AssemblyMember[]>([]);
  const open = rows.find((r) => r.id === openId) ?? null;

  useEffect(() => {
    if (openId == null) { setMembers([]); return; }
    let live = true;
    fetchAssembly(openId)
      .then((d) => { if (live) setMembers(d.members); })
      .catch(() => { if (live) setMembers([]); });
    return () => { live = false; };
  }, [openId]);

  if (open) {
    return (
      <div className="toplist">
        <button className="asm-back" onClick={() => onOpen(null)}>‹ All tracts</button>
        <div className="toplist-title">
          {open.owner_name_norm ?? `${open.owner_count} separate owners`}
        </div>
        <div className="asm-detail-meta">
          <b>{fmtNum(open.combined_acres, 2)} acres</b> · {open.parcel_count} parcels
          {" "}({fmtNum(Number(open.combined_acres) / open.parcel_count, 1)} ac each) ·{" "}
          {open.neighborhood}
          <div>
            {open.kind === "same_owner"
              ? <span className="asm-tier one">one owner</span>
              : <span className="asm-tier multi">needs {open.owner_count} sellers</span>}
            {open.zoning ? <span className="asm-zone">zoning {open.zoning}</span> : null}
          </div>
          <div className="muted">{landValue(open.combined_land_value)}</div>
        </div>
        {members.map((m) => (
          <button className="toplist-row" key={m.parcel_id}
                  onClick={() => onPickParcel(m.parcel_id)}>
            <span className="toplist-addr">
              {m.address || m.parcel_id}
              <span className="toplist-sub">
                {fmtNum(m.acres, 2)} ac · {m.zoning_code ?? "—"} ·{" "}
                {m.has_structure === false ? "vacant" : "has building"}
              </span>
            </span>
            <span className="toplist-metric">
              {m.land_value == null || Number(m.land_value) === 0 ? "—" : fmtUsd(m.land_value)}
            </span>
          </button>
        ))}
        {members.length === 0 && <div className="muted empty-stage">loading…</div>}
      </div>
    );
  }

  return (
    <div className="toplist">
      <div className="toplist-title">
        <b>{rows.length}</b> tract{rows.length === 1 ? "" : "s"}
        <span className="toplist-sub">
          {fmtNum(rows.reduce((s, r) => s + Number(r.combined_acres ?? 0), 0), 0)} acres total
        </span>
      </div>
      {rows.map((a) => (
        <button className="toplist-row" key={a.id}
                onClick={() => { onOpen(a); logEvent("open_assembly", { assembly_id: a.id }); }}>
          <span className="toplist-addr">
            {a.owner_name_norm ?? `${a.owner_count} separate owners`}
            <span className="toplist-sub">
              {a.parcel_count} parcels · {fmtNum(Number(a.combined_acres) / a.parcel_count, 1)} ac each
              {" · "}{a.neighborhood}
              {a.kind === "multi_owner" ? ` · ${a.owner_count} sellers` : ""}
            </span>
          </span>
          <span className="toplist-metric">{fmtNum(a.combined_acres, 1)} ac</span>
        </button>
      ))}
      {rows.length === 0 && (
        <div className="muted empty-stage">No tracts at this size. Try a smaller minimum.</div>
      )}
    </div>
  );
}

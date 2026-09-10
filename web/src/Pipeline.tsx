import { useEffect, useMemo, useState, type ReactNode } from "react";
import {
  downloadLettersBatch, fetchLeads, LeadCard, logEvent, pipelineExportUrl,
  Stage, STAGE_META, STAGE_ORDER,
} from "./api";
import { fmtDate, fmtUsd } from "./format";
import LeadDetail from "./LeadDetail";

const STAGE_TABS: (Stage | "all")[] = ["all", ...STAGE_ORDER, "passed"];

const stageBadge = (l: LeadCard): ReactNode => (
  <span className="stage-badge" style={{ background: STAGE_META[l.stage].color }}>
    {STAGE_META[l.stage].label}
  </span>
);
const respBadge = (l: LeadCard): ReactNode => l.response_outcome
  ? <span className={`resp-badge ${l.response_outcome}`}>
      {l.response_outcome === "positive" ? "👍 yes" : "👎 no"}</span>
  : "—";

type Col = {
  key: string; label: string; num?: boolean;
  get: (l: LeadCard) => string | number | null;
  render?: (l: LeadCard) => ReactNode;
};

const COLS: Col[] = [
  { key: "address", label: "Address", get: (l) => l.address,
    render: (l) => (
      <span className="lt-addr">{l.address ?? l.parcel_id}
        {l.homestead ? <span className="lt-tag" title="Owner 65+ (homestead)">65+</span> : null}
      </span>) },
  { key: "neighborhood", label: "Area", get: (l) => l.neighborhood },
  { key: "owner_name_raw", label: "Owner", get: (l) => l.owner_name_raw },
  { key: "stage", label: "Stage", num: true, get: (l) => STAGE_ORDER.indexOf(l.stage), render: stageBadge },
  { key: "opportunity_score", label: "Score", num: true, get: (l) => l.opportunity_score,
    render: (l) => l.opportunity_score != null ? Number(l.opportunity_score).toFixed(0) : "—" },
  { key: "condition_gap", label: "Cond.", num: true, get: (l) => l.condition_gap,
    render: (l) => l.condition_gap != null ? Number(l.condition_gap).toFixed(0) : "—" },
  { key: "land_value", label: "Land value", num: true, get: (l) => l.land_value,
    render: (l) => fmtUsd(l.land_value) },
  { key: "last_outreach_at", label: "Outreach", get: (l) => l.last_outreach_at,
    render: (l) => l.last_outreach_at ? fmtDate(l.last_outreach_at) : "—" },
  { key: "response_outcome", label: "Response", get: (l) => l.response_outcome, render: respBadge },
  { key: "updated_at", label: "Updated", get: (l) => l.updated_at, render: (l) => fmtDate(l.updated_at) },
];

/** The Pipeline page: a sortable table of the agent's saved leads (a CRM feel),
 * filtered by funnel stage, with a detail drawer for the full working file.
 * Replaces the cramped kanban board. */
export default function Pipeline({ openLeadId, onOpenLead }: {
  openLeadId: number | null;
  onOpenLead: (id: number | null) => void;
}) {
  const [leads, setLeads] = useState<LeadCard[]>([]);
  const [loading, setLoading] = useState(true);
  const [tab, setTab] = useState<Stage | "all">("all");
  const [sort, setSort] = useState<string>("updated_at");
  const [desc, setDesc] = useState(true);
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [busy, setBusy] = useState(false);

  const toggleSel = (id: number) => setSelected((prev) => {
    const next = new Set(prev);
    next.has(id) ? next.delete(id) : next.add(id);
    return next;
  });

  const load = () => {
    setLoading(true);
    fetchLeads().then(setLeads).catch(() => {}).finally(() => setLoading(false));
  };
  useEffect(() => { load(); }, []);

  const counts = useMemo(() => {
    const c: Record<string, number> = { all: leads.length };
    for (const l of leads) c[l.stage] = (c[l.stage] ?? 0) + 1;
    return c;
  }, [leads]);

  const rows = useMemo(() => {
    const base = tab === "all" ? leads : leads.filter((l) => l.stage === tab);
    const col = COLS.find((c) => c.key === sort);
    if (!col) return base;
    return [...base].sort((a, b) => {
      const av = col.get(a), bv = col.get(b);
      if (av == null && bv == null) return 0;
      if (av == null) return 1;
      if (bv == null) return -1;
      const cmp = col.num ? Number(av) - Number(bv) : String(av).localeCompare(String(bv));
      return desc ? -cmp : cmp;
    });
  }, [leads, tab, sort, desc]);

  const onSort = (key: string) => {
    if (sort === key) setDesc((d) => !d);
    else { setSort(key); setDesc(true); }
  };

  return (
    <div className="pipeline">
      <div className="pipeline-head">
        <div className="pipeline-title">
          My Pipeline <span className="muted">· {leads.length} lead{leads.length === 1 ? "" : "s"}</span>
        </div>
        <div className="pipeline-actions">
          {selected.size > 0 && (["pdf", "docx"] as const).map((fmt) => (
            <button key={fmt} className={fmt === "pdf" ? "letters-btn" : "ghost-btn"} disabled={busy}
              onClick={async () => {
                setBusy(true);
                try { await downloadLettersBatch([...selected], fmt); logEvent("generate_letters", { n: selected.size, fmt }); }
                catch (e) { alert(`Letter generation failed: ${e instanceof Error ? e.message : e}`); }
                finally { setBusy(false); }
              }}>
              {fmt === "pdf" ? `✉ Letters PDF (${selected.size})` : `Word (${selected.size})`}
            </button>
          ))}
          <a className="export-btn" download
            href={pipelineExportUrl(tab === "all" ? null : (tab as Stage), "xlsx")}
            onClick={() => logEvent("export_pipeline", { stage: tab })}>⤓ Export</a>
        </div>
      </div>

      <div className="stage-tabs">
        {STAGE_TABS.map((s) => (
          <button key={s} className={`stage-tab ${tab === s ? "active" : ""}`}
            onClick={() => setTab(s)}
            style={tab === s && s !== "all" ? { borderBottomColor: STAGE_META[s as Stage].color } : undefined}>
            {s === "all" ? "All" : STAGE_META[s as Stage].label}
            <span className="stage-tab-count">{counts[s] ?? 0}</span>
          </button>
        ))}
      </div>

      {!loading && leads.length === 0 && (
        <div className="pipeline-empty">
          <div className="pipeline-empty-title">No leads yet.</div>
          <div className="muted">
            Open a parcel on the Discover map and hit <b>＋ Add to Leads</b> to start working it.
          </div>
        </div>
      )}

      {leads.length > 0 && (
        <div className="lead-table-wrap">
          <table className="lead-table">
            <thead>
              <tr>
                <th className="lt-check" />
                {COLS.map((c) => (
                  <th key={c.key} className={c.num ? "num" : ""} onClick={() => onSort(c.key)}>
                    {c.label}{sort === c.key ? (desc ? " ↓" : " ↑") : ""}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((l) => (
                <tr key={l.id} className={selected.has(l.id) ? "sel" : ""} onClick={() => onOpenLead(l.id)}>
                  <td className="lt-check" onClick={(e) => e.stopPropagation()}>
                    <input type="checkbox" checked={selected.has(l.id)} onChange={() => toggleSel(l.id)} />
                  </td>
                  {COLS.map((c) => (
                    <td key={c.key} className={c.num ? "num" : ""}>
                      {c.render ? c.render(l) : (c.get(l) ?? "—")}
                    </td>
                  ))}
                </tr>
              ))}
              {rows.length === 0 && (
                <tr><td colSpan={COLS.length + 1} className="muted lt-empty">No leads in this stage.</td></tr>
              )}
            </tbody>
          </table>
        </div>
      )}

      {openLeadId != null && (
        <div className="drawer-overlay" onClick={() => onOpenLead(null)}>
          <div className="drawer" onClick={(e) => e.stopPropagation()}>
            <LeadDetail leadId={openLeadId}
              onClose={() => onOpenLead(null)}
              onChanged={load} />
          </div>
        </div>
      )}
    </div>
  );
}

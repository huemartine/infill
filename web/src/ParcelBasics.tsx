import { aerialUrl, ParcelDossier, streetViewUrl } from "./api";
import { fmtNum, fmtSqft, fmtUsd } from "./format";

/** The read-only "what we already know about this parcel" body: score
 * breakdown, valuation, data-check flags, market demand, assemblage, signals.
 * Shared by the discovery dossier and a saved lead's detail view, so the
 * moment an agent saves a lead every metric we've computed travels with it. */
export default function ParcelBasics({ dossier, onSelect }: {
  dossier: ParcelDossier;
  onSelect?: (pid: string) => void;
}) {
  const m = dossier.master;
  const s = dossier.scores;
  return (
    <>
      {s && (
        <div className="score-big">
          <span className="score-num">{fmtNum(s.opportunity_score)}</span>
          <span className="score-label">opportunity · {s.use_classification ?? "?"}</span>
        </div>
      )}

      {(s?.constraints?.active?.length ?? 0) > 0 && (
        <div className="constraints">
          {s!.constraints!.active!.map((c) => (
            <span key={c} className={`constraint-chip ${c === "regulatory_floodway" ? "knockout" : ""}`}>
              {c.replace(/_/g, " ")}
            </span>
          ))}
        </div>
      )}

      {dossier.lat != null && dossier.lng != null && (
        <div className="curb-view">
          <div className="section-title">Curb view</div>
          {dossier.streetview_url && (
            <img className="curb-img" src={dossier.streetview_url} loading="lazy"
                 alt="Street View of the property" />
          )}
          <div className="curb-links">
            <a href={streetViewUrl(dossier.lat, dossier.lng)} target="_blank" rel="noreferrer">🚶 Street View</a>
            <a href={aerialUrl(dossier.lat, dossier.lng)} target="_blank" rel="noreferrer">🛰 Aerial</a>
          </div>
        </div>
      )}

      <table className="kv">
        <tbody>
          {s && (
            <>
              <tr><td>Development potential</td><td>{fmtNum(s.development_score)}</td></tr>
              <tr><td>Owner motivation</td><td>{fmtNum(s.owner_motivation_score)}</td></tr>
              <tr><td>Distress</td><td>{fmtNum(s.distress_score)}</td></tr>
              <tr><td>Demand</td><td>{fmtNum(s.demand_score)}</td></tr>
            </>
          )}
          <tr><td>Zoning</td><td>{(m.zoning_code as string) ?? "—"}</td></tr>
          <tr><td>Owner</td><td>{(m.owner_name_raw as string) ?? "—"} ({m.owner_type ?? "?"})</td></tr>
          <tr><td>Owner mailing</td><td>{(m.owner_mailing_addr as string) ?? "—"}</td></tr>
          <tr><td>Absentee</td><td>{m.is_absentee == null ? "—" : m.is_absentee ? "yes" : "no"}</td></tr>
          <tr><td>Owner 65+ (homestead)</td><td>{m.homestead == null ? "—" : m.homestead ? "yes" : "no"}</td></tr>
          <tr><td>Land value</td><td>{fmtUsd(m.land_value)}</td></tr>
          <tr><td>Improvement</td><td>{fmtUsd(m.improvement_value)}</td></tr>
          <tr><td>I/L ratio</td><td>{fmtNum(m.il_ratio, 2)}</td></tr>
          <tr><td>Condition vs block</td><td>
            {m.condition_gap == null ? "—"
              : `${fmtNum(m.condition_gap)} / 100 ${Number(m.condition_gap) >= 40 ? "· worse than neighbors" : ""}`}
          </td></tr>
          <tr><td>Area land value</td><td>{fmtUsd(m.area_land_value)}</td></tr>
          <tr><td>Sewer service</td><td>
            {m.on_septic == null ? "—"
              : m.on_septic ? "private septic (no sewer)" : "public sewer likely"}
          </td></tr>
          <tr><td>Water main</td><td>
            {m.water_main_ft == null ? "—" : `${fmtNum(m.water_main_ft, 0)} ft to hydrant`}
          </td></tr>
          <tr><td>Lot size</td><td>{fmtSqft(m.area_sqft as string | number | null)}</td></tr>
          <tr><td>Last sale</td><td>{m.last_sale_date ?? "—"} {m.last_sale_price ? `· ${fmtUsd(m.last_sale_price)}` : ""}</td></tr>
        </tbody>
      </table>

      {dossier.zoning && (
        <div className="zoning-box">
          <div className="section-title">
            Zoning · {dossier.zoning.district ?? "—"}
            {dossier.zoning.jurisdiction ? ` · ${dossier.zoning.jurisdiction}` : ""}
          </div>
          {dossier.zoning.known ? (
            <>
              <div className="zoning-name">{dossier.zoning.name}</div>
              {dossier.zoning.uses && (
                <div className="zoning-uses">{dossier.zoning.uses}</div>
              )}
              <table className="kv">
                <tbody>
                  {(dossier.zoning.rules ?? []).map((r) => (
                    <tr key={r.label}><td>{r.label}</td><td>{r.value}</td></tr>
                  ))}
                </tbody>
              </table>
              {/* Overlays ADD to the base district. Cincinnati's Connected
                  Communities suffixes are the whole ballgame on an infill lot:
                  an ordinary SF-6 becomes a by-right fourplex with no parking
                  requirement, so they get their own callout rather than a
                  footnote. */}
              {(dossier.zoning.overlays ?? []).map((o) => (
                <div className="zoning-overlay" key={o.code}>
                  <div className="zoning-overlay-name">+ {o.name}</div>
                  {o.effect && <div>{o.effect}</div>}
                  {o.citation && <div className="zoning-cite">{o.citation}</div>}
                </div>
              ))}
              {(dossier.zoning.notes ?? []).map((n, i) => (
                <div className="zoning-note" key={i}>⚠ {n}</div>
              ))}
              <div className="zoning-cite">
                {dossier.zoning.citation}
                {dossier.zoning.url && (
                  <> · <a href={dossier.zoning.url} target="_blank" rel="noreferrer">official code</a></>
                )}
                <div className="zoning-disclaimer">
                  Informational only — overlays, planned districts, variances and deed
                  restrictions can change these. The municipality is the authority.
                </div>
              </div>
            </>
          ) : (
            <div className="muted zoning-unknown">{dossier.zoning.reason}</div>
          )}
        </div>
      )}

      {dossier.audit_flags.length > 0 && (
        <div className="audit-box">
          <div className="section-title">⚠ Data checks ({dossier.audit_flags.length})</div>
          <div className="audit-note">
            Advisory only — these don't change the score, but verify before acting:
          </div>
          {dossier.audit_flags.map((f, i) => (
            <div key={i} className={`audit-flag sev-${f.severity}`}>
              <span className="audit-code">{f.code.replace(/_/g, " ")}</span>
              {f.detail ? <span className="muted"> — {f.detail}</span> : null}
            </div>
          ))}
        </div>
      )}

      {dossier.demand && (
        <div className="demand-box">
          <div className="section-title">Market demand · {dossier.demand.neighborhood}</div>
          <table className="kv">
            <tbody>
              {dossier.demand.permit_covered && (
                <tr>
                  <td>New construction</td>
                  <td>
                    <b>{fmtNum(dossier.demand.builds_per_1k)}</b> new builds / 1,000 parcels (5 yr)
                    {dossier.demand.dev_trajectory_pct != null &&
                      Number(dossier.demand.dev_trajectory_pct) < 900 && (
                      <span className={Number(dossier.demand.dev_trajectory_pct) >= 0 ? "up" : "down"}>
                        {" "}{Number(dossier.demand.dev_trajectory_pct) >= 0 ? "▲ accelerating" : "▼ cooling"}
                      </span>
                    )}
                  </td>
                </tr>
              )}
              <tr>
                <td>Resale activity</td>
                <td>
                  <b>{fmtNum(dossier.demand.turnover_pct)}%</b> of parcels traded in
                  24 mo ({dossier.demand.sales_24mo} sales)
                </td>
              </tr>
              <tr>
                <td>Median sale</td>
                <td>
                  <b>{fmtUsd(dossier.demand.median_price_24mo)}</b>
                  {dossier.demand.momentum_pct != null && (
                    <span className={Number(dossier.demand.momentum_pct) >= 0 ? "up" : "down"}>
                      {" "}{Number(dossier.demand.momentum_pct) >= 0 ? "▲" : "▼"}
                      {Math.abs(Number(dossier.demand.momentum_pct)).toFixed(1)}% vs prior 2 yrs
                    </span>
                  )}
                </td>
              </tr>
              {dossier.demand.sale_to_value != null && (
                <tr>
                  <td>Sale vs assessed</td>
                  <td>
                    buyers paying <b>{Number(dossier.demand.sale_to_value).toFixed(2)}x</b> county value
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      )}

      <div className="signals">
        <div className="section-title">Signals ({dossier.signals.length})</div>
        {dossier.signals.length === 0 && <div className="muted">none</div>}
        {dossier.signals.map((sig, i) => (
          <div className="signal" key={i}>
            <span className={`sig-type sig-${sig.signal_type}`}>{sig.signal_type.replace(/_/g, " ")}</span>
            <span className="muted"> {sig.event_date ?? ""}{sig.severity ? ` · $${Number(sig.severity).toLocaleString()}` : ""}</span>
          </div>
        ))}
      </div>
    </>
  );
}

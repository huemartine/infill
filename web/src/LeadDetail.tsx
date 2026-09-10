import { useRef, useState, useEffect } from "react";
import {
  addEntry, deleteEntry, deleteLead, fetchLead, leadLetterDocxUrl, leadLetterUrl,
  LeadDetail as LeadDetailData,
  LeadEntry, LeadOutreach, markOutreachSent, markOutreachUnsent, photoUrl, ResponseOutcome,
  Stage, STAGE_META, STAGE_ORDER, updateLead, uploadPhoto,
} from "./api";
import { fmtDate } from "./format";
import ParcelBasics from "./ParcelBasics";

/** A saved lead's working file: the funnel, a data-prefilled diligence checklist,
 * response logging, and per-stage note/photo timelines. This is where an agent
 * vets a parcel and works it toward outreach → response → handoff. */
export default function LeadDetail({ leadId, onClose, onChanged }: {
  leadId: number;
  onClose: () => void;
  onChanged: () => void;   // pipeline table needs to re-fetch on any change
}) {
  const [data, setData] = useState<LeadDetailData | null>(null);
  const [showBasics, setShowBasics] = useState(false);

  const load = () => fetchLead(leadId).then(setData).catch(() => {});
  useEffect(() => { void load(); /* eslint-disable-next-line */ }, [leadId]);

  if (!data) return <div className="lead-detail"><div className="muted">loading…</div></div>;
  const { lead, dossier, entries } = data;
  const stage = lead.stage;
  const idx = STAGE_ORDER.indexOf(stage);

  const setStage = async (s: Stage) => { await updateLead(leadId, { stage: s }); await load(); onChanged(); };
  const remove = async () => {
    if (!confirm("Remove this lead from your pipeline? Its notes and photos are deleted.")) return;
    await deleteLead(leadId); onChanged(); onClose();
  };

  return (
    <div className="lead-detail">
      <div className="dossier-head">
        <div>
          <div className="dossier-addr">{dossier.master.address ?? "(no address)"}</div>
          <div className="dossier-pid">
            parcel {lead.parcel_id}
            {dossier.master.neighborhood ? ` · ${dossier.master.neighborhood}` : ""}
            {dossier.scores ? ` · score ${Number(dossier.scores.opportunity_score).toFixed(0)}` : ""}
          </div>
        </div>
        <button className="close" onClick={onClose}>×</button>
      </div>

      {/* Funnel progress: click any stage to move the lead there */}
      <div className="funnel">
        {STAGE_ORDER.map((s, i) => (
          <button key={s}
            className={`funnel-step ${i === idx ? "current" : ""} ${i < idx ? "done" : ""} ${stage === "passed" ? "muted-step" : ""}`}
            style={i <= idx && stage !== "passed" ? { borderColor: STAGE_META[s].color } : undefined}
            onClick={() => void setStage(s)}
            title={STAGE_META[s].hint}>
            <span className="funnel-dot" style={{ background: i <= idx && stage !== "passed" ? STAGE_META[s].color : undefined }} />
            {STAGE_META[s].label}
          </button>
        ))}
      </div>
      <div className="funnel-actions">
        {stage !== "passed" ? (
          <button className="ghost-btn" onClick={() => void setStage("passed")}>Mark passed</button>
        ) : (
          <button className="ghost-btn" onClick={() => void setStage("prospect")}>Reopen → Prospect</button>
        )}
        <button className="ghost-btn danger" onClick={() => void remove()}>Delete lead</button>
      </div>

      <button className="basics-toggle" onClick={() => setShowBasics((v) => !v)}>
        {showBasics ? "▾ Hide" : "▸ Show"} the basics (score, curb view, zoning, demand, signals)
      </button>
      {showBasics && <div className="basics-wrap"><ParcelBasics dossier={dossier} /></div>}

      <DiligenceChecklist lead={lead} dossier={dossier} leadId={leadId} onChanged={load} />
      <OutreachSection leadId={leadId} outreach={data.outreach} onChanged={load} />
      <ResponseSection lead={lead} leadId={leadId} onChanged={load} />

      <StageSection leadId={leadId} stage="prospect" title="Research"
        placeholder="Comps, zoning read, owner search, why it's interesting…"
        entries={entries} onChanged={load} />
      <StageSection leadId={leadId} stage="diligence" title="Diligence notes"
        placeholder="What you verified — access, grade, neighbors, condition, owner situation…"
        entries={entries} onChanged={load} allowPhotos />
      <StageSection leadId={leadId} stage="outreach" title="Outreach notes"
        placeholder="Letters sent, follow-ups, delivery notes…"
        entries={entries} onChanged={load} />
      <StageSection leadId={leadId} stage="responded" title="Conversation"
        placeholder="What the owner said, next steps toward a deal…"
        entries={entries} onChanged={load} allowPhotos />
    </div>
  );
}

/** Human-in-the-loop vetting. Items are PRE-CHECKED from data we already have
 * (floodway, condition gap, owner-motivation signals), and the agent confirms or
 * overrides. Saved to the lead's `diligence` map. */
function DiligenceChecklist({ lead, dossier, leadId, onChanged }: {
  lead: LeadDetailData["lead"]; dossier: LeadDetailData["dossier"];
  leadId: number; onChanged: () => Promise<void> | void;
}) {
  const m = dossier.master as Record<string, unknown>;
  const active = dossier.scores?.constraints?.active ?? [];
  const num = (v: unknown) => (v == null ? null : Number(v));
  const items: { key: string; label: string; auto: boolean | null }[] = [
    { key: "acquirable", label: "Acquirable (not exempt / public land)", auto: !active.includes("not_acquirable") },
    { key: "buildable", label: "Not in a floodway", auto: !m.floodway_flag },
    { key: "teardown", label: "Teardown profile (worse than block, or land-dominant)",
      auto: (num(m.condition_gap) ?? 0) >= 40 || (num(m.il_ratio) ?? 99) < 1 },
    { key: "motivated", label: "Owner may be motivated (senior / absentee)",
      auto: !!m.homestead || !!m.is_absentee },
    { key: "streetview", label: "Reviewed Street View / drove by", auto: null },
    { key: "comps", label: "Comps support a rebuild", auto: null },
    { key: "zoning_ok", label: `Zoning allows the intended use${m.zoning_code ? ` (${m.zoning_code})` : ""}`, auto: null },
  ];
  const stored = lead.diligence ?? {};
  const checked = (it: { key: string; auto: boolean | null }): boolean =>
    it.key in stored ? !!stored[it.key] : !!it.auto;

  const toggle = async (key: string, val: boolean) => {
    const next: Record<string, boolean> = {};
    for (const it of items) next[it.key] = it.key === key ? val : checked(it);
    await updateLead(leadId, { diligence: next });
    await onChanged();
  };
  const done = items.filter((it) => checked(it)).length;

  return (
    <div className="stage-section diligence">
      <div className="section-title">Diligence checklist <span className="muted">· {done}/{items.length}</span></div>
      {items.map((it) => (
        <label key={it.key} className="dil-item">
          <input type="checkbox" checked={checked(it)}
            onChange={(e) => void toggle(it.key, e.target.checked)} />
          <span>{it.label}</span>
          {it.auto != null && !(it.key in stored) && <span className="dil-auto">auto</span>}
        </label>
      ))}
    </div>
  );
}

/** Generate the print-ready letter and mark it mailed. The PDF carries the QR /
 * short link to the response page. */
function OutreachSection({ leadId, outreach, onChanged }: {
  leadId: number; outreach: LeadOutreach | null; onChanged: () => Promise<void> | void;
}) {
  const [busy, setBusy] = useState(false);
  const sent = outreach?.status === "sent" || outreach?.status === "responded";
  const toggle = async () => {
    setBusy(true);
    try {
      if (sent) await markOutreachUnsent(leadId);
      else await markOutreachSent(leadId);
      await onChanged();
    } finally { setBusy(false); }
  };
  return (
    <div className="stage-section">
      <div className="section-title">Outreach letter</div>
      <div className="muted" style={{ marginBottom: 8 }}>
        A print-ready PDF merged with the owner's mailing address, with a QR code + short
        link to the response page. Print &amp; mail it, then mark it sent.
      </div>
      {sent && (
        <div className="resp-current positive" style={{ background: "#fef3c7", color: "#92400e" }}>
          Marked sent{outreach?.sent_at ? ` · ${fmtDate(outreach.sent_at)}` : ""}
        </div>
      )}
      <div className="resp-actions">
        <a className="note-save" href={leadLetterUrl(leadId)} target="_blank" rel="noreferrer">
          ⬇ Letter (PDF)
        </a>
        <a className="ghost-btn" href={leadLetterDocxUrl(leadId)}>
          ⬇ Letter (Word — editable)
        </a>
        <button className="ghost-btn" onClick={() => void toggle()} disabled={busy}>
          {busy ? "…" : sent ? "↩ Undo sent" : "Mark sent → Outreach"}
        </button>
      </div>
    </div>
  );
}

/** Log the owner's reply to outreach. Positive advances to Responded; negative
 * archives to Passed. */
function ResponseSection({ lead, leadId, onChanged }: {
  lead: LeadDetailData["lead"]; leadId: number; onChanged: () => Promise<void> | void;
}) {
  const set = async (outcome: ResponseOutcome) => {
    await updateLead(leadId, {
      response_outcome: outcome,
      responded_at: new Date().toISOString().slice(0, 10),
      stage: outcome === "positive" ? "responded" : "passed",
    });
    await onChanged();
  };
  const clear = async () => {
    // undo: wipe the response and put the lead back at Outreach (awaiting a reply)
    await updateLead(leadId, { response_outcome: null, responded_at: null, stage: "outreach" });
    await onChanged();
  };
  return (
    <div className="stage-section">
      <div className="section-title">Owner response</div>
      {lead.response_outcome ? (
        <div className={`resp-current ${lead.response_outcome}`}>
          Logged: <b>{lead.response_outcome === "positive" ? "👍 positive" : "👎 negative"}</b>
          {lead.responded_at ? ` · ${lead.responded_at}` : ""}
        </div>
      ) : (
        <div className="muted" style={{ marginBottom: 8 }}>No response logged yet.</div>
      )}
      <div className="resp-actions">
        <button className="note-save" onClick={() => void set("positive")}>👍 Positive → Responded</button>
        <button className="ghost-btn" onClick={() => void set("negative")}>👎 Negative → Passed</button>
        {lead.response_outcome && (
          <button className="ghost-btn" onClick={() => void clear()}>↩ Clear response</button>
        )}
      </div>
    </div>
  );
}

function StageSection({ leadId, stage, title, placeholder, entries, onChanged, allowPhotos }: {
  leadId: number; stage: Stage; title: string; placeholder: string;
  entries: LeadEntry[]; onChanged: () => Promise<void> | void; allowPhotos?: boolean;
}) {
  const mine = entries.filter((e) => e.stage === stage);
  const notes = mine.filter((e) => e.kind === "note");
  const photos = mine.filter((e) => e.kind === "photo");
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);

  const saveNote = async () => {
    const body = draft.trim();
    if (!body) return;
    setBusy(true);
    try { await addEntry(leadId, stage, body); setDraft(""); await onChanged(); }
    finally { setBusy(false); }
  };
  const onFiles = async (files: FileList | null) => {
    if (!files?.length) return;
    setBusy(true);
    try {
      for (const f of Array.from(files)) await uploadPhoto(leadId, stage, f, "");
      await onChanged();
    } catch (e) {
      alert(`Photo upload failed: ${e instanceof Error ? e.message : e}`);
    } finally {
      setBusy(false);
      if (fileRef.current) fileRef.current.value = "";
    }
  };

  return (
    <div className="stage-section">
      <div className="section-title">{title}</div>
      <div className="note-compose">
        <textarea className="note-input" placeholder={placeholder} value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) void saveNote(); }} />
        <div className="compose-row">
          {allowPhotos && (
            <>
              <input ref={fileRef} type="file" accept="image/*" multiple capture="environment"
                style={{ display: "none" }} onChange={(e) => void onFiles(e.target.files)} />
              <button className="ghost-btn" onClick={() => fileRef.current?.click()} disabled={busy}>
                📷 Add photos
              </button>
            </>
          )}
          <button className="note-save" onClick={() => void saveNote()} disabled={busy || !draft.trim()}>
            {busy ? "…" : "Save note"}
          </button>
        </div>
      </div>

      {photos.length > 0 && (
        <div className="photo-grid">
          {photos.map((p) => (
            <figure key={p.id} className="photo">
              <a href={photoUrl(leadId, p.id)} target="_blank" rel="noreferrer">
                <img src={photoUrl(leadId, p.id)} alt={p.body ?? p.photo_name ?? "site photo"} loading="lazy" />
              </a>
              <button className="photo-del" title="delete photo"
                onClick={async () => { await deleteEntry(leadId, p.id); await onChanged(); }}>×</button>
              {p.body && <figcaption>{p.body}</figcaption>}
            </figure>
          ))}
        </div>
      )}

      {notes.map((n) => (
        <div className="note" key={n.id}>
          <div className="note-body">{n.body}</div>
          <div className="note-meta">
            <span>{fmtDate(n.created_at)}</span>
            <button className="note-del" title="delete"
              onClick={async () => { await deleteEntry(leadId, n.id); await onChanged(); }}>×</button>
          </div>
        </div>
      ))}
      {mine.length === 0 && <div className="muted empty-stage">Nothing here yet.</div>}
    </div>
  );
}

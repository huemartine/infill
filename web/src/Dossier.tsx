import { useState } from "react";
import { addLead, ParcelDossier } from "./api";
import ParcelBasics from "./ParcelBasics";

export default function Dossier({ dossier, onClose, onSelect, savedLeadId, onSaved, onOpenLead }: {
  dossier: ParcelDossier;
  onClose: () => void;
  onSelect: (pid: string) => void;
  savedLeadId?: number;                 // set if this parcel is already in the pipeline
  onSaved?: (leadId: number) => void;   // refresh pipeline after adding
  onOpenLead?: (leadId: number) => void;
}) {
  const m = dossier.master;
  const [busy, setBusy] = useState(false);

  const save = async () => {
    setBusy(true);
    try {
      const { id } = await addLead(m.parcel_id);
      onSaved?.(id);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="dossier">
      <div className="dossier-head">
        <div>
          <div className="dossier-addr">{m.address ?? "(no address)"}</div>
          <div className="dossier-pid">parcel {m.parcel_id}</div>
        </div>
        <button className="close" onClick={onClose}>×</button>
      </div>

      {savedLeadId ? (
        <button className="lead-cta in-pipeline" onClick={() => onOpenLead?.(savedLeadId)}>
          ✓ In your pipeline — open lead →
        </button>
      ) : (
        <button className="lead-cta" onClick={() => void save()} disabled={busy}>
          {busy ? "adding…" : "＋ Add to Leads"}
        </button>
      )}

      <ParcelBasics dossier={dossier} onSelect={onSelect} />
    </div>
  );
}

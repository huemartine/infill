import { useCallback, useEffect, useRef, useState } from "react";
import {
  aerialUrl, fetchAuditQueue, fetchParcel, fetchPrecision, FAKE_REASONS,
  NeighborhoodRow, ParcelDossier, Precision, postLabel, QueueItem, searchAudit,
  streetViewUrl, Verdict,
} from "./api";
import ParcelBasics from "./ParcelBasics";

const PAGE = 25;

/** Phase-0 review harness. Work down the un-reviewed leads (paging deeper as you
 * go, filterable by region and score band), OR search for any specific lot and
 * audit it directly. Each verdict sharpens precision and grows the regression
 * set. Judge fast with the aerial + Street View deep-links and the dossier. */
export default function Audit({ regions }: { regions: NeighborhoodRow[] }) {
  const [region, setRegion] = useState<string | null>(null);
  const [minScore, setMinScore] = useState(0);
  const [queue, setQueue] = useState<QueueItem[]>([]);
  const [offset, setOffset] = useState(0);
  const [pos, setPos] = useState(0);
  const [exhausted, setExhausted] = useState(false);
  const [override, setOverride] = useState<QueueItem | null>(null);  // a searched pick
  const [dossier, setDossier] = useState<ParcelDossier | null>(null);
  const [prec, setPrec] = useState<Precision | null>(null);
  const [reason, setReason] = useState("commercial_land");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [reviewed, setReviewed] = useState(0);

  // search
  const [searchQ, setSearchQ] = useState("");
  const [results, setResults] = useState<QueueItem[] | null>(null);
  const searchTimer = useRef<number | undefined>(undefined);

  const cur = override ?? queue[pos] ?? null;

  const loadPrec = useCallback(() => { fetchPrecision().then(setPrec).catch(() => {}); }, []);
  useEffect(() => { loadPrec(); }, [loadPrec]);

  // (re)load the queue whenever the filters change
  useEffect(() => {
    let live = true;
    setQueue([]); setPos(0); setOffset(0); setExhausted(false); setOverride(null);
    fetchAuditQueue({ neighborhood: region, minScore, limit: PAGE, offset: 0 })
      .then((rows) => { if (live) { setQueue(rows); setOffset(rows.length); setExhausted(rows.length < PAGE); } })
      .catch(() => {});
    return () => { live = false; };
  }, [region, minScore]);

  // page deeper when we near the end of the loaded batch (so Skip never loops)
  useEffect(() => {
    if (override || exhausted) return;
    if (pos < queue.length - 3) return;
    let live = true;
    fetchAuditQueue({ neighborhood: region, minScore, limit: PAGE, offset })
      .then((rows) => {
        if (!live) return;
        setQueue((q) => {
          const seen = new Set(q.map((x) => x.parcel_id));
          return [...q, ...rows.filter((r) => !seen.has(r.parcel_id))];
        });
        setOffset((o) => o + rows.length);
        if (rows.length < PAGE) setExhausted(true);
      })
      .catch(() => {});
    return () => { live = false; };
  }, [pos, queue.length, offset, exhausted, override, region, minScore]);

  // load the dossier for whatever parcel is in focus
  useEffect(() => {
    if (cur) fetchParcel(cur.parcel_id).then(setDossier).catch(() => setDossier(null));
    else setDossier(null);
  }, [cur?.parcel_id]);   // eslint-disable-line react-hooks/exhaustive-deps

  const advance = () => { if (override) setOverride(null); else setPos((p) => p + 1); };

  const submit = async (label: Verdict) => {
    if (!cur || busy) return;
    setBusy(true);
    try {
      await postLabel(cur.parcel_id, label, label === "fake" ? reason : null, note);
      setNote("");
      const n = reviewed + 1; setReviewed(n);
      // drop the just-labelled parcel from the loaded queue so counts stay honest
      setQueue((q) => q.filter((x) => x.parcel_id !== cur.parcel_id));
      if (override) setOverride(null); // else pos now points at the next item (array shrank)
      if (n % 5 === 0) loadPrec();
    } finally { setBusy(false); }
  };

  const runSearch = (q: string) => {
    setSearchQ(q);
    window.clearTimeout(searchTimer.current);
    if (q.trim().length < 2) { setResults(null); return; }
    searchTimer.current = window.setTimeout(() => {
      searchAudit(q.trim()).then(setResults).catch(() => setResults([]));
    }, 250);
  };
  const pickSearch = (item: QueueItem) => {
    setOverride(item); setResults(null); setSearchQ("");
  };

  const top25 = prec?.overall["25"];
  const top50 = prec?.overall["50"];
  const remaining = override ? null : queue.length - pos;

  return (
    <div className="audit">
      <div className="audit-bar">
        <div className="audit-metrics">
          {prec && (
            <>
              <span><b>{prec.total_labels.toLocaleString()}</b> labels</span>
              {top25 && <span>top-25 reviewed <b>{top25.reviewed_pct}%</b>
                {top25.precision != null && <> · precision <b>{top25.precision}%</b></>}</span>}
              {top50 && <span>top-50 reviewed <b>{top50.reviewed_pct}%</b></span>}
              <span className="muted">you: <b>{reviewed}</b> this session</span>
            </>
          )}
        </div>
        <div className="audit-controls">
          <input className="audit-search" placeholder="🔍 search address or parcel id…"
            value={searchQ} onChange={(e) => runSearch(e.target.value)} />
          <select className="region-select" value={region ?? ""}
            onChange={(e) => setRegion(e.target.value || null)}>
            <option value="">All regions</option>
            {regions.map((r) => (
              <option key={r.neighborhood} value={r.neighborhood}>{r.neighborhood} ({r.leads})</option>
            ))}
          </select>
          <label className="minscore">min score <b>{minScore}</b>
            <input type="range" min={0} max={95} step={5} value={minScore}
              onChange={(e) => setMinScore(Number(e.target.value))} />
          </label>
        </div>
      </div>

      {results && (
        <div className="search-results">
          {results.length === 0 && <div className="muted sr-empty">No matches.</div>}
          {results.map((r) => (
            <button key={r.parcel_id} className="sr-row" onClick={() => pickSearch(r)}>
              <span className="sr-addr">{r.address ?? r.parcel_id}</span>
              <span className="sr-meta">
                {r.neighborhood ?? "—"} · score {Number(r.opportunity_score).toFixed(0)}
                {r.hint_label && (r.hint_source === "review" || r.hint_source === "agent")
                  ? <span className="sr-labelled"> · labelled {r.hint_label}</span> : null}
              </span>
            </button>
          ))}
        </div>
      )}

      {!cur && !results && (
        <div className="pipeline-empty">
          <div className="pipeline-empty-title">Queue clear 🎉</div>
          <div className="muted">Nothing un-reviewed{region ? ` in ${region}` : ""} at min score
            {" "}{minScore}. Lower the min score, pick another region, or search for a specific lot.</div>
        </div>
      )}

      {cur && (
        <div className="audit-body">
          <div className="audit-parcel">
            <div className="dossier-head">
              <div>
                <div className="dossier-addr">{cur.address ?? "(no address)"}</div>
                <div className="dossier-pid">
                  parcel {cur.parcel_id}{cur.neighborhood ? ` · ${cur.neighborhood}` : ""}
                  {cur.zoning_code ? ` · ${cur.zoning_code}` : ""} · score {Number(cur.opportunity_score).toFixed(0)}
                </div>
              </div>
              {override
                ? <button className="ghost-btn" onClick={() => setOverride(null)}>← back to queue</button>
                : <span className="audit-progress">{remaining} left{exhausted ? "" : "+"}</span>}
            </div>

            {cur.hint_label && (
              <div className={`audit-hint ${cur.hint_source === "review" || cur.hint_source === "agent" ? "human" : ""}`}>
                {cur.hint_source === "review" || cur.hint_source === "agent"
                  ? "already labelled: " : "model suspects: "}
                <b>{cur.hint_label}</b>{cur.hint_reason ? ` (${cur.hint_reason.replace(/_/g, " ")})` : ""}
              </div>
            )}

            <div className="audit-links">
              <a className="map-link streetview" href={streetViewUrl(cur.lat, cur.lng)}
                target="_blank" rel="noreferrer">🧍 Street View</a>
              <a className="map-link aerial" href={aerialUrl(cur.lat, cur.lng)}
                target="_blank" rel="noreferrer">🛰 Aerial</a>
            </div>

            <div className="audit-verdict">
              <button className="v-real" onClick={() => void submit("real")} disabled={busy}>
                ✓ Real opportunity
              </button>
              <div className="v-fake-row">
                <button className="v-fake" onClick={() => void submit("fake")} disabled={busy}>✗ Fake</button>
                <select value={reason} onChange={(e) => setReason(e.target.value)}>
                  {FAKE_REASONS.map((r) => <option key={r.code} value={r.code}>{r.label}</option>)}
                </select>
              </div>
              <div className="v-secondary">
                <button className="v-maybe" onClick={() => void submit("maybe")} disabled={busy}>? Maybe</button>
                <button className="v-skip" onClick={advance} disabled={busy}>Skip →</button>
              </div>
              <input className="audit-note" placeholder="note (optional)" value={note}
                onChange={(e) => setNote(e.target.value)} />
            </div>
          </div>

          <div className="audit-dossier">
            {dossier ? <ParcelBasics dossier={dossier} /> : <div className="muted">loading dossier…</div>}
          </div>
        </div>
      )}
    </div>
  );
}

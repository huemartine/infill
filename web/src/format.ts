// Shared display formatters, used by the dossier and the lead pipeline.

export const fmtUsd = (v: string | number | null | undefined) =>
  v == null || v === "" ? "—"
    : `$${Number(v).toLocaleString(undefined, { maximumFractionDigits: 0 })}`;

export const fmtNum = (v: string | number | null | undefined, d = 1) =>
  v == null || v === "" ? "—" : Number(v).toFixed(d);

export const fmtSqft = (v: string | number | null | undefined) =>
  v == null || v === "" ? "—" : `${Math.round(Number(v)).toLocaleString()} sqft`;

export const fmtDate = (s: string | null | undefined) =>
  !s ? "—" : new Date(s).toLocaleDateString(undefined,
    { month: "short", day: "numeric", year: "numeric" });

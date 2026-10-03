import { useEffect, useState } from "react";
import { NoticeBanner, inputStyle, secondaryButtonStyle } from "../ui";

export type Document = Record<string, any>;
export const post = (value: unknown) => ({ method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(value) });
export const errorMessage = (error: unknown) => error instanceof Error ? error.message : "The Chamber request failed.";
export function JsonView({ value, title = "Details" }: { value: unknown; title?: string }) {
  return <details className="chamber-json"><summary>{title}</summary><pre tabIndex={0}>{JSON.stringify(value, null, 2) || "No data available."}</pre></details>;
}
export function JsonEditor({ label, value, onChange }: { label: string; value: unknown; onChange: (value: any) => void }) {
  const [text, setText] = useState(() => JSON.stringify(value, null, 2));
  const [error, setError] = useState("");
  const serialized = JSON.stringify(value, null, 2);
  useEffect(() => { setText(serialized); setError(""); }, [serialized]);
  return <div className="load-field"><label>{label}<textarea style={inputStyle} rows={8} spellCheck={false} value={text} onChange={(e) => { setText(e.target.value); setError(""); }} /></label>
    <button type="button" style={secondaryButtonStyle} onClick={() => { try { const parsed: unknown = JSON.parse(text); if (parsed === null || typeof parsed !== "object") throw new Error("Enter a JSON object or array."); onChange(parsed); setError(""); } catch (e) { setError(errorMessage(e)); } }}>Apply {label.toLowerCase()}</button>
    {error && <NoticeBanner tone="error">{error}</NoticeBanner>}
  </div>;
}
export function Pager({ page, pages, onPage, busy = false }: { page: number; pages: number; onPage: (page: number) => void; busy?: boolean }) {
  return <div className="chamber-pager"><button type="button" style={secondaryButtonStyle} disabled={busy || page <= 1} onClick={() => onPage(page - 1)}>Previous page</button><span>Page {page} of {pages}</span><button type="button" style={secondaryButtonStyle} disabled={busy || page >= pages} onClick={() => onPage(page + 1)}>Next page</button></div>;
}

import { useState } from "react";
import { requestJson } from "../api";
import { initialInput, RequestField, type InputSchema } from "../load-testing";
import { NoticeBanner, inputStyle, primaryButtonStyle, secondaryButtonStyle } from "../ui";

type Source = { run_id: string; service_name: string; state: string; created_at: string };
type Operation = { index: number; name: string; method: string; path: string };
type Saved = { id: string; name: string; max_vus: number; max_iterations: number; max_duration_seconds: number };
type Preview = Saved & { preview_id: string; environment: string; method: string; path: string; adapter: string; context: string; namespace: string; target_service: string; target_port: number; workloads: string[]; files: { field: string; filename: string }[]; input_schema: InputSchema };
const post = (data: unknown) => ({ method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(data) });

export function LoadProfileManager({ base, onSaved }: { base: string; onSaved: () => void }) {
  const api = `${base}/profile-import`;
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [saved, setSaved] = useState<Saved[]>([]);
  const [sources, setSources] = useState<Source[]>([]);
  const [search, setSearch] = useState("");
  const [page, setPage] = useState(1);
  const [pages, setPages] = useState(1);
  const [runId, setRunId] = useState("");
  const [operations, setOperations] = useState<Operation[]>([]);
  const [operation, setOperation] = useState(0);
  const [preview, setPreview] = useState<Preview | null>(null);
  const [confirmed, setConfirmed] = useState(false);
  const [removeId, setRemoveId] = useState("");

  async function perform(action: () => Promise<void>) {
    setBusy(true); setError(""); setNotice("");
    try { await action(); } catch (e) { setError(e instanceof Error ? e.message : "Profile request failed."); }
    finally { setBusy(false); }
  }
  async function refreshSaved() {
    const response = await requestJson<{ profiles: Saved[] }>(api);
    setSaved(response.profiles);
  }
  async function loadSources(nextPage: number) {
    const data = await requestJson<{ items: Source[]; pagination: { page: number; total_pages: number } }>(`${api}/sources?${new URLSearchParams({ search, page: String(nextPage) })}`);
    setSources(data.items); setPage(data.pagination.page || 1); setPages(data.pagination.total_pages || 1);
    setRunId(""); setOperations([]); setPreview(null);
  }
  async function inspect(id: string) {
    setRunId(id); setOperations([]); setPreview(null); setConfirmed(false);
    if (!id) return;
    const data = await requestJson<{ operations: Operation[] }>(`${api}/inspect`, post({ run_id: id }));
    setOperations(data.operations); setOperation(data.operations[0]?.index ?? 0);
  }
  return <div className="load-profile-manager">
    <button type="button" style={secondaryButtonStyle} disabled={busy} aria-expanded={open} onClick={() => {
      setOpen(!open);
      if (!open) void perform(async () => { await refreshSaved(); await loadSources(1); });
    }}>{open ? "Close profile manager" : "Manage profiles"}</button>
    {open && <section aria-label="Manage load-test profiles" className="load-profile-panel">
      <h3>Import from Chamber</h3>
      <p>Select a saved Chamber plan or run. Review one operation and its target, then save it for this service. Importing does not send test traffic.</p>
      {error && <NoticeBanner tone="error">{error}</NoticeBanner>}
      {notice && <p role="status">{notice}</p>}
      <fieldset disabled={busy} className="load-form-fields">
        <form onSubmit={(event) => { event.preventDefault(); void perform(() => loadSources(1)); }} className="load-import-search">
          <label>Search Chamber plans<input style={inputStyle} value={search} maxLength={200} onChange={(e) => setSearch(e.target.value)} /></label>
          <button style={secondaryButtonStyle} type="submit">Search</button>
        </form>
        <label>Source plan or run<select style={inputStyle} value={runId} onChange={(e) => void perform(() => inspect(e.target.value))}>
          <option value="">Select a Chamber plan or run</option>
          {sources.map((s) => <option key={s.run_id} value={s.run_id}>{s.service_name} · {s.run_id} · {s.state}</option>)}
        </select></label>
        {!sources.length && <p>No matching Chamber plans or runs. Save a Kubernetes attach-mode plan in Chamber first.</p>}
        <div className="load-import-pagination"><button type="button" style={secondaryButtonStyle} disabled={page <= 1} onClick={() => void perform(() => loadSources(page - 1))}>Previous</button><span>Page {page} of {pages}</span><button type="button" style={secondaryButtonStyle} disabled={page >= pages} onClick={() => void perform(() => loadSources(page + 1))}>Next</button></div>
        {operations.length > 0 && <>
          <label>Import operation<select style={inputStyle} value={operation} onChange={(e) => { setOperation(Number(e.target.value)); setPreview(null); setConfirmed(false); }}>
            {operations.map((o) => <option key={o.index} value={o.index}>{o.method} {o.path} · {o.name}</option>)}
          </select></label>
          <button type="button" style={secondaryButtonStyle} onClick={() => void perform(async () => {
            setPreview(null); setConfirmed(false);
            setPreview(await requestJson<Preview>(`${api}/preview`, post({ run_id: runId, operation })));
          })}>Preview import</button>
        </>}
        {runId && !operations.length && !busy && <p>This source has no request operations.</p>}
        {preview && <form onSubmit={(e) => {
          e.preventDefault(); if (!confirmed) return;
          void perform(async () => {
            await requestJson(api, post({ preview_id: preview.preview_id, name: preview.name, max_vus: preview.max_vus, max_iterations: preview.max_iterations, max_duration_seconds: preview.max_duration_seconds }));
            await refreshSaved(); setPreview(null); setConfirmed(false); setNotice("Profile imported. It is available in Operations now."); onSaved();
          });
        }}>
          <h4>Review service binding</h4>
          <div className="load-target"><strong>{preview.method} {preview.path}</strong><span>Studio environment: {preview.environment}</span><span>Cluster: {preview.context} · Namespace: {preview.namespace}</span><span>Target: {preview.target_service}:{preview.target_port}</span><span>Workloads: {preview.workloads.join(", ")}</span></div>
          <p>Source request values, headers, faults, cleanup and agents are not enabled by this import. Request fields are generated from this service’s OpenAPI.</p>
          {preview.files.length > 0 && <ul>{preview.files.map((f) => <li key={f.field}>{f.field}: {f.filename}</li>)}</ul>}
          <details><summary>Preview request fields</summary><fieldset disabled><RequestField schema={preview.input_schema} value={initialInput(preview.input_schema)} onChange={() => {}} label="Imported request inputs" /></fieldset></details>
          <label>Profile name<input style={inputStyle} required maxLength={120} value={preview.name} onChange={(e) => setPreview({ ...preview, name: e.target.value })} /></label>
          <h4>Approved load limits</h4><div className="load-controls">
            <label>Maximum concurrent users<input style={inputStyle} type="number" required min={1} max={preview.adapter === "relayna" ? 32 : 100} value={preview.max_vus} onChange={(e) => setPreview({ ...preview, max_vus: Number(e.target.value) })} /></label>
            {preview.adapter === "relayna" && <label>Maximum task iterations<input style={inputStyle} type="number" required min={1} max={1000} value={preview.max_iterations} onChange={(e) => setPreview({ ...preview, max_iterations: Number(e.target.value) })} /></label>}
            <label>{preview.adapter === "relayna" ? "Maximum scheduling window (seconds)" : "Maximum ramp duration (seconds)"}<input style={inputStyle} type="number" required min={1} max={3600} value={preview.max_duration_seconds} onChange={(e) => setPreview({ ...preview, max_duration_seconds: Number(e.target.value) })} /></label>
          </div>
          <label className="load-import-confirm"><input type="checkbox" checked={confirmed} onChange={(e) => setConfirmed(e.target.checked)} />I have checked the target, operation, files and limits for this service.</label>
          <button type="submit" style={primaryButtonStyle} disabled={!confirmed}>Save imported profile</button>
        </form>}
        <h4>Imported profiles</h4>
        {!saved.length && <p>No imported profiles yet. Deployment-configured profiles remain available separately.</p>}
        {saved.map((p) => <div className="load-target" key={p.id}><strong>{p.name}</strong><span>Up to {p.max_vus} users · {p.max_iterations} tasks · {p.max_duration_seconds}s window</span>
          {removeId === p.id ? <><span>Remove this imported profile? Existing plans and runs remain available.</span><button type="button" style={secondaryButtonStyle} onClick={() => void perform(async () => {
            await requestJson(`${api}/${encodeURIComponent(p.id)}`, { method: "DELETE" }); await refreshSaved(); setRemoveId(""); onSaved(); setNotice("Imported profile removed.");
          })}>Confirm removal</button><button type="button" style={secondaryButtonStyle} onClick={() => setRemoveId("")}>Keep profile</button></> : <button type="button" style={secondaryButtonStyle} onClick={() => setRemoveId(p.id)}>Remove</button>}
        </div>)}
      </fieldset>
      {busy && <p role="status">Working…</p>}
    </section>}
  </div>;
}

import { useCallback, useEffect, useState, type Dispatch, type SetStateAction } from "react";
import { requestJson } from "./api";
import type { ObservabilityProfile, ServiceDraft } from "./types";
import { ConfirmationDialog, NoticeBanner, SectionCard, inputStyle, primaryButtonStyle, secondaryButtonStyle } from "./ui";

const base = "/studio/admin/observability-profiles";
type Provider = ObservabilityProfile["provider"];
const prefixes = { loki: "log", prometheus: "metrics", tempo: "trace" } as const;
const labels = { loki: "Loki", prometheus: "Prometheus", tempo: "Tempo" };

export function useObservabilityProfiles(enabled: boolean) {
  const [profiles, setProfiles] = useState<ObservabilityProfile[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const reload = useCallback(async () => {
    setLoading(true); setError("");
    try { const data = await requestJson<{ profiles: ObservabilityProfile[] }>(base, { cache: "no-store" }); setProfiles(data.profiles); }
    catch (e) { setError(e instanceof Error ? e.message : "Unable to load observability profiles."); }
    finally { setLoading(false); }
  }, []);
  useEffect(() => { if (enabled) void reload(); }, [enabled, reload]);
  return { profiles, loading, error, reload };
}

type ProfileState = ReturnType<typeof useObservabilityProfiles>;

export function ProfileSelector({ provider, draft, onChange, state }: { provider: Provider; draft: ServiceDraft; onChange: Dispatch<SetStateAction<ServiceDraft>>; state: ProfileState }) {
  const prefix = prefixes[provider];
  const id = draft[`${prefix}_profile_id`] || "";
  const options = state.profiles.filter(p => p.provider === provider && p.environment === draft.environment.trim());
  return <div className="studio-stack-sm">
    <label>{labels[provider]} saved profile<select style={inputStyle} value={id} onChange={e => {
      const profile = options.find(p => p.id === e.target.value);
      onChange(current => {
        const next = { ...current, [`${prefix}_profile_id`]: profile?.id || "" };
        if (profile) {
          Object.assign(next, { [`${prefix}_provider`]: provider, [`${prefix}_base_url`]: profile.base_url });
          if (provider !== "prometheus") Object.assign(next, { [`${prefix}_tenant_id`]: profile.tenant_id || "" });
          for (const [key, value] of Object.entries(profile.defaults)) {
            const fieldValue = value == null ? "" : String(value);
            Object.assign(next, { [`${prefix}_${key}`]: fieldValue });
            if (provider === "loki" && key === "source_label") next.log_app_label_key = fieldValue;
          }
        }
        return next;
      });
    }}><option value="">Manual configuration</option>{id && !options.some(p => p.id === id) && <option value={id}>Unavailable profile — choose another or detach</option>}{options.map(p => <option key={p.id} value={p.id}>{p.name}</option>)}</select></label>
    {state.loading && <p role="status">Loading saved profiles…</p>}
    {state.error && <NoticeBanner tone="error">{state.error} <button type="button" style={secondaryButtonStyle} onClick={() => void state.reload()}>Retry profiles</button></NoticeBanner>}
    {!state.loading && !state.error && !options.length && <p>No {labels[provider]} profiles for this environment. Create one in Observability Profiles.</p>}
    {id && <p>Endpoint and credentials are linked to this profile. Service selectors remain specific to this service. Choose Manual configuration to detach.</p>}
  </div>;
}

const empty = { name: "", environment: "prod", provider: "loki" as Provider, base_url: "", tenant_id: "", auth_mode: "none" as ObservabilityProfile["auth_mode"], token: "", username: "", password: "", defaults: "{}" };

export function ObservabilityProfileManager({ state }: { state: ProfileState }) {
  const [open, setOpen] = useState(false);
  const [editing, setEditing] = useState<ObservabilityProfile | null>(null);
  const [draft, setDraft] = useState(empty);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [remove, setRemove] = useState<ObservabilityProfile | null>(null);
  async function perform(action: () => Promise<void>) {
    setBusy(true); setError(""); setNotice("");
    try { await action(); } catch (e) { setError(e instanceof Error ? e.message : "Profile operation failed."); }
    finally { setBusy(false); }
  }
  function reset() { setEditing(null); setDraft(empty); }
  return <SectionCard title="Observability Profiles" subtitle="Save shared telemetry endpoints and encrypted credentials once, then select them for each service.">
    <button type="button" style={secondaryButtonStyle} aria-expanded={open} onClick={() => { setOpen(!open); setDraft(d => ({ ...d, token: "", username: "", password: "" })); }}> {open ? "Close observability profiles" : "Manage observability profiles"}</button>
    {open && <div className="studio-stack-sm">
      {state.loading && <p role="status">Loading profiles…</p>}
      {state.error && <NoticeBanner tone="error">{state.error}</NoticeBanner>}
      {error && <NoticeBanner tone="error">{error}</NoticeBanner>}
      {notice && <p role="status">{notice}</p>}
      <button type="button" style={secondaryButtonStyle} disabled={busy || state.loading} onClick={() => void state.reload()}>Reload profiles</button>
      {!state.loading && !state.error && !state.profiles.length && <p>No saved profiles yet. Create your first connection below.</p>}
      <ul className="studio-stack-sm">{state.profiles.map(p => <li key={p.id}>
        <strong>{p.name}</strong> · {labels[p.provider]} · {p.environment} · {p.credential_configured ? "Credential stored" : "No authentication"}
        <p>Used by: {p.used_by.length ? p.used_by.join(", ") : "No services"}</p>
        <div className="studio-action-row">
          <button type="button" style={secondaryButtonStyle} disabled={busy} onClick={() => { setEditing(p); setDraft({ ...empty, name: p.name, environment: p.environment, provider: p.provider, base_url: p.base_url, auth_mode: p.auth_mode, tenant_id: p.tenant_id || "", defaults: JSON.stringify(p.defaults, null, 2) }); setError(""); setNotice(""); }}>Edit {p.name}</button>
          <button type="button" style={secondaryButtonStyle} disabled={busy} onClick={() => void perform(async () => { const result = await requestJson<{ ok: boolean; detail: string }>(`${base}/${p.id}/test`, { method: "POST" }); if (result.ok) setNotice(`${p.name}: ${result.detail}`); else setError(`${p.name}: ${result.detail}`); })}>Test {p.name}</button>
          <button type="button" style={secondaryButtonStyle} disabled={busy || p.used_by.length > 0} onClick={() => setRemove(p)}>Delete {p.name}</button>
        </div>
      </li>)}</ul>
      <form aria-label={editing ? "Edit observability profile" : "Create observability profile"} className="studio-stack-sm" onSubmit={e => {
        e.preventDefault(); void perform(async () => {
          let defaults: Record<string, unknown>;
          try { const parsed = JSON.parse(draft.defaults); if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) throw new Error(); defaults = parsed; }
          catch { throw new Error("Provider defaults must be a valid JSON object."); }
          const payload = { ...draft, defaults, revision: editing?.revision, token: draft.token || undefined, username: draft.username || undefined, password: draft.password || undefined };
          await requestJson(editing ? `${base}/${editing.id}` : base, { method: editing ? "PUT" : "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
          setNotice("Observability profile saved."); reset(); await state.reload();
        });
      }}>
        <h3>{editing ? `Edit ${editing.name}` : "Create a profile"}</h3>
        {editing && <p>Endpoint and credential changes affect every linked service. Leave credential fields blank to keep the saved credential. Re-enter credentials when changing the endpoint or tenant.</p>}
        <fieldset disabled={busy} className="studio-stack-sm observability-profile-fields" style={{ border: 0, padding: 0 }}>
          <label>Profile name<input required style={inputStyle} value={draft.name} maxLength={100} onChange={e => setDraft(d => ({ ...d, name: e.target.value }))} /></label>
          <label>Profile environment<input required disabled={!!editing} style={inputStyle} value={draft.environment} onChange={e => setDraft(d => ({ ...d, environment: e.target.value }))} /></label>
          <label>Profile provider<select disabled={!!editing} style={inputStyle} value={draft.provider} onChange={e => setDraft(d => ({ ...d, provider: e.target.value as Provider, defaults: "{}" }))}>{Object.entries(labels).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
          <label>Profile endpoint<input required type="url" style={inputStyle} value={draft.base_url} onChange={e => setDraft(d => ({ ...d, base_url: e.target.value }))} /></label>
          {draft.provider !== "prometheus" && <label>Profile tenant ID<input style={inputStyle} value={draft.tenant_id} onChange={e => setDraft(d => ({ ...d, tenant_id: e.target.value }))} /></label>}
          <label>Authentication<select style={inputStyle} value={draft.auth_mode} onChange={e => setDraft(d => ({ ...d, auth_mode: e.target.value as ObservabilityProfile["auth_mode"], token: "", username: "", password: "" }))}><option value="none">None</option><option value="bearer">Bearer token</option><option value="basic">Basic username and password</option></select></label>
          {draft.auth_mode === "bearer" && <label>Bearer token<input type="password" autoComplete="new-password" style={inputStyle} value={draft.token} onChange={e => setDraft(d => ({ ...d, token: e.target.value }))} /></label>}
          {draft.auth_mode === "basic" && <><label>Username<input autoComplete="off" style={inputStyle} value={draft.username} onChange={e => setDraft(d => ({ ...d, username: e.target.value }))} /></label><label>Password<input type="password" autoComplete="new-password" style={inputStyle} value={draft.password} onChange={e => setDraft(d => ({ ...d, password: e.target.value }))} /></label></>}
          <label>Provider defaults (JSON)<textarea style={inputStyle} rows={4} value={draft.defaults} onChange={e => setDraft(d => ({ ...d, defaults: e.target.value }))} /></label>
          <p>Optional query defaults, such as {draft.provider === "prometheus" ? 'namespace, namespace_label, pod_label, or step_seconds' : draft.provider === "loki" ? 'pod_label, source_label, or task_match_mode' : 'public_base_url or query_path'}. Service selector values stay on each service.</p>
          <div className="studio-action-row"><button style={primaryButtonStyle} disabled={state.loading} type="submit">{busy ? "Saving…" : "Save profile"}</button>{editing && <button type="button" style={secondaryButtonStyle} onClick={reset}>Cancel profile edit</button>}</div>
        </fieldset>
      </form>
    </div>}
    {remove && <ConfirmationDialog title="Delete observability profile" body={`Delete '${remove.name}'? Linked services must be detached first.`} confirmLabel="Delete profile" pending={busy} onCancel={() => setRemove(null)} onConfirm={() => void perform(async () => { await requestJson(`${base}/${remove.id}?revision=${remove.revision}`, { method: "DELETE" }); if (editing?.id === remove.id) reset(); setRemove(null); await state.reload(); setNotice("Profile deleted."); })} />}
  </SectionCard>;
}

import { useEffect, useState } from "react";
import { requestJson } from "../api";
import { NoticeBanner, SectionCard, formatTimestamp, inputStyle, primaryButtonStyle, secondaryButtonStyle } from "../ui";
import { errorMessage, JsonView, post, type Document } from "./common";

export function ConnectionPanel({ base, isAdmin, onChanged }: { base: string; isAdmin: boolean; onChanged: () => void }) {
  const [connection, setConnection] = useState<Document | null>(null);
  const [mode, setMode] = useState("deployment");
  const [url, setUrl] = useState("");
  const [token, setToken] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [probe, setProbe] = useState<Document | null>(null);
  const [notice, setNotice] = useState("");
  useEffect(() => {
    let alive = true;
    void requestJson<Document>(`${base}/chamber/connection`).then((value) => { if (alive) { setConnection(value); setMode(value.source || "deployment"); setUrl(value.url || ""); } }, (e) => { if (alive) setError(errorMessage(e)); });
    return () => { alive = false; };
  }, [base]);
  const payload = () => ({ mode, ...(mode === "ui" ? { url, ...(token ? { token } : {}) } : {}) });
  async function perform(action: "save" | "test" | "test-saved") {
    setBusy(true); setError(""); setNotice("");
    try {
      if (action === "save") {
        const value = await requestJson<Document>(`${base}/chamber/connection`, { ...post(payload()), method: "PUT" });
        setConnection(value); setProbe(null); setToken(""); setNotice("Connection saved. Test it before planning traffic."); onChanged();
      } else {
        const value = await requestJson<Document>(`${base}/chamber/connection/test`, action === "test" ? post(payload()) : { method: "POST" });
        setProbe(value); if (action === "test-saved") setConnection(value);
      }
    } catch (e) { setError(errorMessage(e)); }
    finally { setBusy(false); }
  }
  const status = probe || connection;
  return <SectionCard title="Ampule Chamber connection" subtitle="Studio connects to Chamber on your behalf. Operators stay in Studio to configure, monitor and investigate assessments.">
    {error && <NoticeBanner tone="error">{error}</NoticeBanner>}
    {notice && <p role="status">{notice}</p>}
    <div className="chamber-connection-status" role="status"><span className={`chamber-badge ${status?.status === "ready" ? "is-ready" : ""}`}>{status?.status?.replace(/_/g, " ") || "Loading connection…"}</span><p>{status?.message || "API availability has not been checked."}</p>
      <dl className="chamber-facts"><div><dt>Saved source</dt><dd>{connection?.source || "—"}</dd></div><div><dt>Credential</dt><dd>{connection?.token_configured ? "Configured · hidden" : "Not configured"}</dd></div><div><dt>Last checked</dt><dd>{status?.checked_at ? formatTimestamp(status.checked_at) : "Not checked"}</dd></div><div><dt>Last successful check</dt><dd>{status?.last_success_at ? formatTimestamp(status.last_success_at) : "None"}</dd></div></dl>
    </div>
    <p className="load-muted">This verifies the authenticated API. Kubernetes access, target readiness and telemetry are checked when you plan and execute an assessment.</p>
    {isAdmin ? <form onSubmit={(e) => { e.preventDefault(); void perform("save"); }}><fieldset className="load-form-fields" disabled={busy}>
      <label>Connection source<select style={inputStyle} value={mode} onChange={(e) => { setMode(e.target.value); setProbe(null); }}><option value="deployment">Deployment configuration</option><option value="ui">Administrator settings</option><option value="disabled">Disable new assessments</option></select></label>
      {mode === "ui" && <>
        {!connection?.ui_settings_available && <NoticeBanner tone="info">Saving a credential requires PostgreSQL and a settings encryption key in the Studio backend Secret. Deployment configuration remains available.</NoticeBanner>}
        <label>Chamber API URL<input style={inputStyle} required type="url" placeholder="http://ampule-chamber.chamber.svc.cluster.local:8080" value={url} onChange={(e) => { setUrl(e.target.value); setProbe(null); }} /></label>
        <p className="load-muted">Use an address reachable from the Studio backend. In AKS, use the private Kubernetes Service. A port-forward on your laptop works when the Studio backend also runs there. A browser hostname is not required.</p>
        <label>Integration token<input style={inputStyle} type="password" autoComplete="new-password" value={token} onChange={(e) => { setToken(e.target.value); setProbe(null); }} placeholder={connection?.token_configured && connection.url === url ? "Leave blank to keep the saved token" : "Enter Chamber integration token"} /></label>
        <p className="load-muted">Credentials are encrypted at rest and are never returned to the browser. Changing the endpoint requires a new token. The host must be permitted by the backend outbound allowlist.</p>
      </>}
      {mode === "deployment" && <p>Use the URL and token from the Studio deployment. Saving this option clears the administrator override.</p>}
      {mode === "disabled" && <p>New assessments will be disabled. Saved runs keep their original connection for inspection and cancellation.</p>}
      <div className="studio-action-row"><button type="submit" style={primaryButtonStyle} disabled={mode === "ui" && !connection?.ui_settings_available}>Save connection</button><button type="button" style={secondaryButtonStyle} disabled={mode === "disabled"} onClick={() => void perform("test")}>Test draft connection</button><button type="button" style={secondaryButtonStyle} disabled={!connection?.url} onClick={() => void perform("test-saved")}>Check saved connection</button></div>
    </fieldset></form> : <p>An administrator can change or test this connection.</p>}
    {status?.capabilities && <JsonView value={status.capabilities} title="Supported features and API readiness" />}
  </SectionCard>;
}

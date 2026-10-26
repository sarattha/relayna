import { useEffect, useRef, useState, type FormEvent } from "react";
import { useSearchParams } from "react-router-dom";

import { checkIntegration, fetchIntegrationDoctor, fetchStudioAudit, fetchStudioHealth, StudioApiError } from "../api";
import { useStudioAuth } from "../auth-context";
import { useStudioServices } from "../services-context";
import { Link } from "../scoped-link";
import type { StudioAuditEntry, StudioDiagnosticReport } from "../types";
import { NoticeBanner, SectionCard, formatTimestamp, inputStyle, mutedTextStyle, primaryButtonStyle, secondaryButtonStyle } from "../ui";

const labels: Record<string, string> = {
  readiness: "Studio readiness", outbox: "Publication outbox", delivery: "Notification delivery",
  health_refresh_loop: "Health refresh loop", event_pull_loop: "Event pull loop", retention_loop: "Retention loop",
  capabilities: "Service reachability & capabilities", supported_routes: "Advertised capabilities", indexed_events: "Indexed event evidence",
  ingestion_lag: "Observed ingestion delay", retention: "Retention limits", event_pull: "Last event refresh",
  logs: "Log provider", metrics: "Metrics provider", traces: "Trace provider", feed_sample: "Live event feed sample", log_sample: "Live Loki sample",
};

function Evidence({ report }: { report: StudioDiagnosticReport }) {
  return <>
    <p style={mutedTextStyle}>Observed {formatTimestamp(report.observed_at)}{report.service_id ? ` · ${report.service_id} · ${report.environment}` : " · Studio control plane (all environments)"}</p>
    <div className="studio-doctor-grid">
      {report.checks.map((check) => <article className="studio-subcard studio-doctor-check" key={check.source}>
        <div className="studio-doctor-heading"><h3>{labels[check.source] || check.source}</h3><span className="studio-doctor-state" data-state={check.state}>{check.state}</span></div>
        <p className="studio-doctor-scope">{check.scope}</p>
        {check.value !== null ? <p className="studio-doctor-value">{Number.isInteger(check.value) ? check.value : check.value.toFixed(1)} <small>{check.unit}</small></p> : <p style={mutedTextStyle}>Value: not established</p>}
        <p>{check.detail}</p>
        <dl className="studio-doctor-times">
          <div><dt>Last check</dt><dd>{check.checked_at ? formatTimestamp(check.checked_at) : "Not observed"}</dd></div>
          <div><dt>Last success</dt><dd>{check.last_successful_at ? formatTimestamp(check.last_successful_at) : "Not established"}</dd></div>
          <div><dt>Evidence time</dt><dd>{check.evidence_at ? formatTimestamp(check.evidence_at) : "Not established"}</dd></div>
        </dl>
        <p className="studio-doctor-next"><strong>Next check</strong> {check.next_check}</p>
      </article>)}
    </div>
  </>;
}

function AuditBrowser() {
  const [items, setItems] = useState<StudioAuditEntry[]>([]);
  const [actor, setActor] = useState("");
  const [action, setAction] = useState("");
  const [target, setTarget] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [loaded, setLoaded] = useState(false);
  const sequence = useRef(0);
  useEffect(() => () => { sequence.current++; }, []);

  async function load(event: FormEvent) {
    event.preventDefault();
    const request = ++sequence.current;
    setLoading(true);
    setError(null);
    setItems([]);
    try {
      const payload = await fetchStudioAudit({ actor_user_id: actor, action, target_type: target });
      if (request === sequence.current) { setItems(payload.items); setLoaded(true); }
    } catch (cause) {
      if (request === sequence.current) setError(cause instanceof StudioApiError && cause.status === 404 ? "Audit storage is unavailable in this deployment. PostgreSQL is required." : cause instanceof Error ? cause.message : "Audit read failed.");
    } finally { if (request === sequence.current) setLoading(false); }
  }

  return <SectionCard title="Operator audit" subtitle="Administrator-only records across all environments. Newest 50 matching entries; this is not a complete history export.">
    <form className="studio-doctor-controls" onSubmit={(event) => void load(event)}>
      <label>Actor ID<input style={inputStyle} value={actor} onChange={(event) => setActor(event.target.value)} /></label>
      <label>Action (exact)<input style={inputStyle} value={action} onChange={(event) => setAction(event.target.value)} /></label>
      <label>Target type (exact)<input style={inputStyle} value={target} onChange={(event) => setTarget(event.target.value)} /></label>
      <button type="submit" style={secondaryButtonStyle} disabled={loading}>{loading ? "Loading audit…" : "Browse audit"}</button>
    </form>
    {error ? <NoticeBanner tone="error">{error}</NoticeBanner> : null}
    {loaded && !loading && !error && !items.length ? <p>No audit entries matched these filters in the returned window.</p> : null}
    <div className="studio-stack-sm">{items.map((item) => <article key={item.id} className="studio-subcard studio-doctor-check">
      <strong>{item.action}</strong><p>{formatTimestamp(item.occurred_at)} · {item.actor_user_id || "Actor not recorded"}</p>
      <p>{item.target_type} · {item.target_id || "Target not recorded"}</p>
      <details><summary>Audit details</summary><pre className="studio-doctor-json">{JSON.stringify(item.details, null, 2)}</pre></details>
    </article>)}</div>
  </SectionCard>;
}

export function HealthPage() {
  const auth = useStudioAuth();
  const registry = useStudioServices();
  const [params, setParams] = useSearchParams();
  const environment = params.get("environment") || "";
  const selected = params.get("service_id") || "";
  const services = registry.services.filter((service) => !environment || service.environment === environment);
  const service = services.find((item) => item.service_id === selected);
  const [studio, setStudio] = useState<StudioDiagnosticReport | null>(null);
  const [doctor, setDoctor] = useState<StudioDiagnosticReport | null>(null);
  const [studioError, setStudioError] = useState<string | null>(null);
  const [doctorError, setDoctorError] = useState<string | null>(null);
  const [taskId, setTaskId] = useState("");
  const [busy, setBusy] = useState(false);
  const [studioBusy, setStudioBusy] = useState(false);
  const sequence = useRef(0);
  const studioSequence = useRef(0);

  async function loadStudio() {
    const request = ++studioSequence.current;
    setStudioBusy(true); setStudioError(null); setStudio(null);
    try { const report = await fetchStudioHealth(); if (request === studioSequence.current) setStudio(report); }
    catch (cause) { if (request === studioSequence.current) setStudioError(cause instanceof Error ? cause.message : "Studio health unavailable."); }
    finally { if (request === studioSequence.current) setStudioBusy(false); }
  }
  useEffect(() => { void loadStudio(); return () => { studioSequence.current++; }; }, []);
  useEffect(() => {
    const request = ++sequence.current;
    setDoctor(null); setDoctorError(null); setTaskId(""); setBusy(Boolean(service));
    if (service) void fetchIntegrationDoctor(service.service_id).then((report) => {
      if (request === sequence.current) setDoctor(report);
    }).catch((cause) => {
      if (request === sequence.current) setDoctorError(cause instanceof Error ? cause.message : "Service evidence unavailable.");
    }).finally(() => { if (request === sequence.current) setBusy(false); });
    return () => { sequence.current++; };
  }, [service?.service_id, environment]);

  async function run(event: FormEvent) {
    event.preventDefault();
    if (!service) return;
    const request = ++sequence.current;
    setBusy(true); setDoctor(null); setDoctorError(null);
    try { const report = await checkIntegration(service.service_id, taskId); if (request === sequence.current) setDoctor(report); }
    catch (cause) { if (request === sequence.current) setDoctorError(cause instanceof Error ? cause.message : "Integration check failed."); }
    finally { if (request === sequence.current) setBusy(false); }
  }

  return <div className="studio-stack-lg">
    <SectionCard title="Studio Health" subtitle="Explain empty or stale views using separate evidence for readiness, ingestion and upstream providers.">
      <button type="button" style={secondaryButtonStyle} disabled={studioBusy} onClick={() => void loadStudio()}>Refresh Studio evidence</button>
      {studioBusy ? <p role="status">Reading Studio evidence…</p> : null}
      {studioError ? <NoticeBanner tone="error">{studioError}</NoticeBanner> : null}
      {studio ? <Evidence report={studio} /> : null}
    </SectionCard>
    <SectionCard title="Integration Doctor" subtitle="Select one registered service. Stored evidence is read first; administrators can run explicit live checks.">
      {registry.error ? <NoticeBanner tone="error">Registry unavailable: {registry.error}</NoticeBanner> : null}
      <form className="studio-doctor-controls" onSubmit={(event) => void run(event)}>
        <label>Service & environment<select style={inputStyle} value={selected} onChange={(event) => {
          const next = new URLSearchParams(params); if (event.target.value) next.set("service_id", event.target.value); else next.delete("service_id"); setParams(next);
        }}><option value="">Select a service</option>{services.map((item) => <option key={item.service_id} value={item.service_id}>{item.name} · {item.service_id} · {item.environment}</option>)}</select></label>
        {auth.isAdmin ? <><label>Known task ID (optional)<input style={inputStyle} maxLength={255} value={taskId} onChange={(event) => setTaskId(event.target.value)} placeholder="For a precise Loki match" /></label>
          <button type="submit" style={primaryButtonStyle} disabled={!service || busy || service.status === "disabled"}>{busy ? "Checking…" : "Run integration check"}</button></> : null}
      </form>
      {registry.loading ? <p role="status">Loading registry…</p> : !services.length && !registry.error ? <p>No registered services in this environment. <Link to="/services">Register a service</Link> to check its integration.</p> : null}
      {selected && !service && !registry.loading ? <p>The selected service is outside this environment or is no longer registered.</p> : null}
      {service ? <p><Link to={`/services/${encodeURIComponent(service.service_id)}`}>Open service investigation</Link></p> : null}
      {busy ? <p role="status">Reading scoped integration evidence…</p> : null}
      {doctorError ? <NoticeBanner tone="error">{doctorError}</NoticeBanner> : null}
      {doctor && doctor.service_id === service?.service_id ? <Evidence report={doctor} /> : null}
      <p style={mutedTextStyle}>Live samples use configured targets, a 3 second deadline per source, one feed result and one Loki result over 15 minutes. Empty samples do not prove ingestion works. Metrics and trace configuration are not availability checks.</p>
    </SectionCard>
    {auth.isAdmin ? <AuditBrowser /> : null}
  </div>;
}

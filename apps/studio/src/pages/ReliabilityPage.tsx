import { useEffect, useRef, useState, type FormEvent } from "react";
import { useSearchParams } from "react-router-dom";
import { createReliabilityGoal, deleteReliabilityGoal, exportReliabilityGoal, fetchReliabilityGoals, previewReliabilityGoal } from "../api";
import { useStudioAuth } from "../auth-context";
import { useStudioServices } from "../services-context";
import { Link } from "../scoped-link";
import type { ReliabilityGoalInput, ReliabilityGoalKind, ReliabilityGoalList, ReliabilityPreview } from "../types";
import { ConfirmationDialog, EmptyState, NoticeBanner, SectionCard, inputStyle, primaryButtonStyle, secondaryButtonStyle } from "../ui";

const kinds: Record<ReliabilityGoalKind, { label: string; unit: string; threshold: number; description: string }> = {
  backlog_age: { label: "Backlog age", unit: "seconds", threshold: 120, description: "Oldest ready work age. Requires an external queue-age gauge and evidence timestamp; SDK queue depth does not measure age." },
  retry_activity: { label: "Retry activity", unit: "retries", threshold: 10, description: "Observed aggregate retry counter increase in the selected window. Repeated activity is an early warning; it does not identify individual tasks or prove complete window coverage." },
  stale_leases: { label: "Stale leases", unit: "leases", threshold: 1, description: "Expired active leases. Requires a fresh external aggregate; a persistent SDK pressure gauge alone cannot prove current leases." },
  missing_worker: { label: "Missing worker heartbeat", unit: "seconds", threshold: 90, description: "Age of the newest SDK worker heartbeat. Detects service-wide heartbeat loss; it does not establish expected worker count or find one missing worker in a healthy pool. Absent series are missing evidence." },
  completion_success: { label: "Completion success", unit: "%", threshold: 99, description: "Good / eligible accepted business work in a mature cohort. Unfinished eligible work counts against success. Requires external durable cohort aggregates; task-attempt success is not business success." },
  end_to_end_latency: { label: "End-to-end latency", unit: "% within deadline", threshold: 99, description: "Accepted business work completed successfully within the deadline / all eligible accepted work. Requires external mature-cohort aggregates; handler duration is not end-to-end latency." },
};
const initial: ReliabilityGoalInput = { service_id: "", environment: "", name: "", owner: "", kind: "backlog_age", threshold: 120, window_seconds: 300, deadline_seconds: 120, freshness_seconds: 180, hold_seconds: 60, runtime_scope_confirmed: false, receivers: [] };

export function ReliabilityPage() {
  const [params, setParams] = useSearchParams();
  const environment = params.get("environment") || "";
  const serviceId = params.get("service_id") || "";
  const { services } = useStudioServices();
  const { isAdmin } = useStudioAuth();
  const scopedServices = services.filter((service) => !environment || service.environment === environment);
  const [data, setData] = useState<ReliabilityGoalList | null>(null);
  const [draft, setDraft] = useState(initial);
  const [receivers, setReceivers] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [previews, setPreviews] = useState<Record<string, ReliabilityPreview>>({});
  const [exported, setExported] = useState<{ id: string; text: string } | null>(null);
  const [deleting, setDeleting] = useState<string | null>(null);
  const version = useRef(0);

  async function load() {
    const current = ++version.current;
    setError(null);
    try {
      const response = await fetchReliabilityGoals({ environment, service_id: serviceId });
      if (version.current === current) setData(response);
    } catch (failure) { if (version.current === current) setError(String(failure)); }
  }
  useEffect(() => {
    setData(null); setPreviews({}); setExported(null); setDeleting(null);
    setDraft({ ...initial, service_id: serviceId }); setReceivers("");
    void load();
    return () => { version.current++; };
  }, [environment, serviceId]);

  async function create(event: FormEvent) {
    event.preventDefault(); setBusy(true); setError(null);
    const current = version.current;
    try {
      const service = scopedServices.find((item) => item.service_id === draft.service_id);
      if (!service) throw new Error("Select a registered service in this environment.");
      await createReliabilityGoal({ ...draft, environment: service.environment, receivers: receivers.split(",").map((value) => value.trim()).filter(Boolean) });
      if (version.current === current) { setDraft({ ...initial, service_id: serviceId }); setReceivers(""); await load(); }
    } catch (failure) { if (version.current === current) setError(String(failure)); }
    finally { setBusy(false); }
  }
  async function inspect(id: string, action: "preview" | "export") {
    const current = version.current; setBusy(true); setError(null);
    if (action === "preview") setPreviews((previous) => { const next = { ...previous }; delete next[id]; return next; });
    try {
      if (action === "preview") {
        const result = await previewReliabilityGoal(id);
        if (current === version.current) setPreviews((previous) => ({ ...previous, [id]: result }));
      } else {
        const result = await exportReliabilityGoal(id);
        if (current === version.current) setExported({ id, text: JSON.stringify(result, null, 2) });
      }
    } catch (failure) { if (version.current === current) setError(String(failure)); }
    finally { setBusy(false); }
  }
  const field = (label: string, key: "threshold" | "window_seconds" | "deadline_seconds" | "freshness_seconds" | "hold_seconds", min: number, max: number) => <label>{label}<input required type="number" min={min} max={max} step={key === "threshold" ? "any" : "1"} style={inputStyle} value={draft[key]} onChange={(event) => setDraft({ ...draft, [key]: Number(event.target.value) })} /></label>;
  return <div className="studio-page reliability-page">
    <div className="reliability-heading"><div><p className="studio-eyebrow">Reliability</p><h1>Goals & alert routing</h1><p>Detect unhealthy work early. Follow each signal into an investigation.</p></div><button style={secondaryButtonStyle} onClick={() => void load()} disabled={busy}>Refresh delivery status</button></div>
    <NoticeBanner>Prometheus evaluates exported rules. Your existing Alertmanager owns grouping, routing, silences and inhibition. Saving a goal does not deploy rules.</NoticeBanner>
    {error && <NoticeBanner tone="error">{error}</NoticeBanner>}
    <label className="reliability-scope">Service<select style={inputStyle} value={serviceId} onChange={(event) => { const next = new URLSearchParams(params); if (event.target.value) next.set("service_id", event.target.value); else next.delete("service_id"); setParams(next); }}><option value="">All services</option>{scopedServices.map((service) => <option key={service.service_id} value={service.service_id}>{service.name} · {service.environment}</option>)}</select></label>
    {!data && !error && <p role="status">Loading reliability goals…</p>}
    {data && <p className="reliability-status">{data.configured ? "Goal storage ready" : "PostgreSQL required"} · {data.webhook_configured ? "Webhook credential configured" : "Webhook credential missing"} · {data.email_configured ? "Email transport configured" : "Email transport missing"}. Delivery also requires shared email notifications to be enabled in Failed Tasks.</p>}
    {isAdmin && data?.configured && <SectionCard title="Define a goal" subtitle="Assign an owner and recipients for this service and environment."><form onSubmit={(event) => void create(event)} className="reliability-form">
      <label>Registered service<select required style={inputStyle} value={draft.service_id} onChange={(event) => setDraft({ ...draft, service_id: event.target.value })}><option value="">Select a service</option>{scopedServices.filter((service) => service.status !== "disabled").map((service) => <option key={service.service_id} value={service.service_id}>{service.name} · {service.environment}</option>)}</select></label>
      <label>Goal name<input required maxLength={120} style={inputStyle} value={draft.name} onChange={(event) => setDraft({ ...draft, name: event.target.value })} /></label>
      <label>Owner<input required maxLength={120} style={inputStyle} value={draft.owner} onChange={(event) => setDraft({ ...draft, owner: event.target.value })} /></label>
      <label>Signal or objective<select style={inputStyle} value={draft.kind} onChange={(event) => { const kind = event.target.value as ReliabilityGoalKind; setDraft({ ...draft, kind, threshold: kinds[kind].threshold }); }} >{Object.entries(kinds).map(([kind, detail]) => <option key={kind} value={kind}>{detail.label}</option>)}</select></label>
      <label className="reliability-form__wide"><span><input type="checkbox" checked={draft.runtime_scope_confirmed} onChange={(event) => setDraft({ ...draft, runtime_scope_confirmed: event.target.checked })} style={{ width: "auto" }} /> I confirm SDK runtime metrics in this datasource are isolated to the selected environment.</span></label>
      <p className="reliability-form__wide">{kinds[draft.kind].description}</p>
      {field(`Threshold (${kinds[draft.kind].unit})`, "threshold", 0.01, draft.kind === "completion_success" || draft.kind === "end_to_end_latency" ? 100 : 1e9)}
      {field("Window (seconds)", "window_seconds", 60, 2592000)}
      {field("Completion deadline (seconds)", "deadline_seconds", 1, 86400)}
      {field("Evidence freshness (seconds)", "freshness_seconds", 30, 3600)}
      {field("Hold before alert (seconds)", "hold_seconds", 0, 3600)}
      <label>Recipient emails, comma separated<input required style={inputStyle} value={receivers} onChange={(event) => setReceivers(event.target.value)} /></label>
      <div className="reliability-form__wide"><button disabled={busy} style={primaryButtonStyle}>Save goal</button></div>
    </form></SectionCard>}
    {data?.items.length === 0 && <EmptyState title="No goals in this scope" body="Create a service goal, verify its evidence, then deploy the exported rules through your existing monitoring workflow." />}
    <div className="reliability-goals">{data?.items.map(({ goal, delivery }) => {
      const preview = previews[goal.id];
      return <SectionCard key={goal.id} title={goal.name} subtitle={`${goal.service_id} · ${goal.environment} · Owner: ${goal.owner}`}>
        <p><strong>{kinds[goal.kind].label}</strong> · {goal.threshold} {kinds[goal.kind].unit} · Window {goal.window_seconds}s</p>
        <p>{kinds[goal.kind].description}</p><p>Route to: {goal.receivers.join(", ")}</p>
        <div className="reliability-actions"><button style={secondaryButtonStyle} disabled={busy} onClick={() => void inspect(goal.id, "preview")}>Preview evidence</button><button style={secondaryButtonStyle} disabled={busy} onClick={() => void inspect(goal.id, "export")}>Export rules & routing</button>{isAdmin && <button style={secondaryButtonStyle} disabled={busy} onClick={() => setDeleting(goal.id)}>Delete goal</button>}</div>
        {preview && <div className="reliability-evidence" role="status"><strong>{preview.state.replace(/_/g, " ")}</strong>{preview.value !== null && <span> · {Number(preview.value.toFixed(3))} {kinds[goal.kind].unit}</span>}<p>{preview.detail}</p>{preview.eligible !== null && <p>Good {preview.good ?? "unknown"} / eligible {preview.eligible}. Unfinished mature work remains eligible.</p>}<p>{preview.from} → {preview.to}</p><Link to={preview.investigation_url}>Investigate this evidence window</Link>{preview.queries && <details><summary>Metric identity & queries</summary><pre>{JSON.stringify(preview.queries, null, 2)}</pre></details>}</div>}
        <div className="reliability-delivery"><strong>Delivery: {delivery.state || "No callback received"}</strong><p>{delivery.sent || 0} sent · {delivery.received || 0} callbacks · {delivery.duplicates || 0} duplicates suppressed</p>{delivery.detail && <p>{delivery.detail}</p>}{delivery.last_received_at && <p>Last callback: {delivery.last_received_at}</p>}{delivery.investigation_url && <Link to={delivery.investigation_url}>Investigate alert window</Link>}<p>No callback does not prove healthy work. External evaluation and silences may prevent delivery.</p></div>
        {exported?.id === goal.id && <details open><summary>Deployment snippets (JSON / YAML compatible)</summary><p>Merge the Prometheus group, Alertmanager route and receiver into your existing configuration. Use a server-side credential file. The webhook secret is never included here.</p><pre tabIndex={0}>{exported.text}</pre></details>}
      </SectionCard>;
    })}</div>
    {deleting && <ConfirmationDialog title="Delete reliability goal?" body="Remove its rules from Prometheus and its route from Alertmanager as well. Callbacks for this goal will be rejected after deletion." confirmLabel="Delete goal" onCancel={() => setDeleting(null)} pending={busy} onConfirm={() => { const id = deleting; const current = version.current; setBusy(true); setError(null); void deleteReliabilityGoal(id).then(() => { if (current === version.current) { setDeleting(null); return load(); } }).catch((failure) => { if (current === version.current) setError(String(failure)); }).finally(() => setBusy(false)); }} />}
  </div>;
}

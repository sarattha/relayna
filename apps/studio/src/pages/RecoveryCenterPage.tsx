import { useEffect, useRef, useState } from "react";
import { Link, useLocation, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { controlRecoveryPlan, createRecoveryPlan, fetchRecoveryPlan, fetchRecoveryPlans, previewRecovery } from "../api";
import { useStudioAuth } from "../auth-context";
import { useStudioServices } from "../services-context";
import type { RecoveryItem, RecoveryPlan, RecoveryPreview, RecoverySelection } from "../types";
import { InlineCodeBox, NoticeBanner, SectionCard, formatTimestamp, inputStyle, primaryButtonStyle, secondaryButtonStyle } from "../ui";

type Handoff = { service_id: string; failure_ids: string[] };

function taskLink(service: string, task: string | null | undefined) {
  return task ? <Link to={`/tasks/${encodeURIComponent(service)}/${encodeURIComponent(task)}`}>{task}</Link> : <span>Unavailable</span>;
}

function CohortItems({ service, items }: { service: string; items: RecoveryItem[] }) {
  return <div className="studio-stack-sm">{items.map((item) => <article className="studio-subcard recovery-item" key={item.failure_id}>
    <div className="studio-list-card__top"><strong>{item.failure_id}</strong><span>{item.dispatch_state || (item.eligible ? "Eligible" : "Blocked")}</span></div>
    <div className="recovery-item__evidence">
      <span>Original: {taskLink(service, item.original_task_id)}</span>
      <span>Replacement: {taskLink(service, item.replacement_task_id)}</span>
      <span>Target queue: <strong>{item.target_queue || "Unavailable"}</strong></span>
      <span>Retained payload: {item.payload_available ? "available" : "missing"}</span>
    </div>
    {item.override_payload != null ? <details><summary>Payload override</summary><InlineCodeBox value={JSON.stringify(item.override_payload, null, 2)} /></details> : <span className="studio-inline-meta">Use retained payload</span>}
    {item.outcome ? <p>Observed outcome: <strong>{item.outcome === "pending" ? "Awaiting terminal evidence" : item.outcome === "not_requested" ? "Not requested" : item.outcome}</strong></p> : null}
    {item.requested_at ? <span className="studio-inline-meta">Requested {formatTimestamp(item.requested_at)} · Accepted {formatTimestamp(item.accepted_at || null)} · Observed {formatTimestamp(item.observed_at || null)}</span> : null}
    {item.replacement_task_id && item.replacement_task_id === item.original_task_id ? <span className="studio-inline-meta">Task ID reused. Only evidence newer than the retry and previous status can count.</span> : null}
    {item.errors.map((message) => <p key={message} role="alert">{message}</p>)}
    {item.message ? <p role="status">{item.message}</p> : null}
  </article>)}</div>;
}

export function RecoveryCenterPage() {
  const { isAdmin } = useStudioAuth();
  const services = useStudioServices();
  const location = useLocation();
  const navigate = useNavigate();
  const { planId } = useParams();
  const [params] = useSearchParams();
  const environment = params.get("environment") || "";
  const handoff = (location.state as { recovery?: Handoff } | null)?.recovery;
  const [serviceId, setServiceId] = useState(handoff?.service_id || "");
  const [failureIds, setFailureIds] = useState(handoff?.failure_ids.join("\n") || "");
  const [overrides, setOverrides] = useState("");
  const [interval, setIntervalSeconds] = useState("2");
  const [note, setNote] = useState("");
  const [preview, setPreview] = useState<RecoveryPreview | null>(null);
  const [selection, setSelection] = useState<RecoverySelection | null>(null);
  const requestId = useRef(crypto.randomUUID());
  const [plans, setPlans] = useState<RecoveryPlan[]>([]);
  const [total, setTotal] = useState(0);
  const [offset, setOffset] = useState(0);
  const [plan, setPlan] = useState<RecoveryPlan | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [readError, setReadError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);
  const lock = useRef(false);
  const readVersion = useRef(0);
  const [batchCount, setBatchCount] = useState("5");
  const [confirmationVersion, setConfirmationVersion] = useState(0);
  const [confirmation, setConfirmation] = useState<"batch" | "stop" | null>(null);
  const [loading, setLoading] = useState(true);
  const scope = useRef("");
  scope.current = `${planId || ""}:${environment}`;

  useEffect(() => { setPreview(null); setSelection(null); }, [serviceId, failureIds, overrides, interval, note, environment]);
  useEffect(() => { setOffset(0); }, [environment]);
  useEffect(() => {
    let active = true;
    const currentScope = `${planId || ""}:${environment}`;
    setPlan(null); setPlans([]); setConfirmation(null); setLoading(true); setError(null); setReadError(null);
    async function load() {
      if (lock.current) return;
      const version = ++readVersion.current;
      try {
        const result = planId ? await fetchRecoveryPlan(planId) : await fetchRecoveryPlans(environment, offset);
        if (!active || version !== readVersion.current || scope.current !== currentScope) return;
        if ("plan_id" in result) setPlan(result);
        else { setPlans(result.items); setTotal(result.count); }
        setReadError(null);
      } catch (e) { if (active && version === readVersion.current) setReadError(e instanceof Error ? e.message : "Recovery Center is unavailable."); }
      finally { if (active) setLoading(false); }
    }
    void load();
    const timer = window.setInterval(() => void load(), 3000);
    return () => { active = false; window.clearInterval(timer); };
  }, [planId, environment, offset]);

  async function mutate(action: () => Promise<void>) {
    if (lock.current) return;
    lock.current = true; readVersion.current++; setPending(true); setError(null);
    const currentScope = scope.current;
    try { await action(); }
    catch (e) { if (scope.current === currentScope) setError(e instanceof Error ? e.message : "Recovery action failed."); }
    finally { lock.current = false; setPending(false); }
  }

  async function inspect() {
    const service = services.servicesById.get(serviceId);
    if (!service || environment && service.environment !== environment) throw new Error("Choose a service in the current environment.");
    const ids = failureIds.split(/[\n,]/).map((id) => id.trim()).filter(Boolean);
    if (!ids.length || ids.length > 200 || new Set(ids).size !== ids.length) throw new Error("Select 1–200 distinct failure IDs in batch order.");
    let custom: Record<string, { target_queue?: string; override_payload?: unknown }> = {};
    try { custom = overrides.trim() ? JSON.parse(overrides) : {}; }
    catch { throw new Error("Overrides must be a JSON object keyed by failure ID."); }
    if (!custom || typeof custom !== "object" || Array.isArray(custom) || Object.keys(custom).some((id) => !ids.includes(id))) throw new Error("Overrides must refer only to selected failure IDs.");
    if (!Number.isInteger(Number(interval)) || Number(interval) < 1 || Number(interval) > 60) throw new Error("Dispatch interval must be 1–60 seconds.");
    const selected: RecoverySelection = { service_id: serviceId, environment: service.environment,
      items: ids.map((id) => ({ ...custom[id], failure_id: id })), interval_seconds: Number(interval), note };
    setPreview(null); setSelection(null);
    const currentScope = scope.current;
    const result = await previewRecovery(selected);
    if (scope.current !== currentScope) return;
    setPreview(result); setSelection(selected); requestId.current = crypto.randomUUID();
  }

  async function control(action: "batch" | "pause" | "resume" | "stop") {
    if (!plan) return;
    const id = plan.plan_id;
    const currentScope = scope.current;
    const count = Number(batchCount);
    if (action === "batch" && (!Number.isInteger(count) || count < 1 || count > 200)) throw new Error("Batch size must be 1–200.");
    const updated = await controlRecoveryPlan(id, action, action === "batch" || action === "stop" ? confirmationVersion : plan.version, count || 5);
    if (scope.current === currentScope) { setPlan(updated); setConfirmation(null); }
  }

  const scopedService = services.servicesById.get(serviceId);
  const pendingItems = plan?.items.filter((item) => item.dispatch_state === "pending") || [];
  const batch = pendingItems.slice(0, Number(batchCount) || 0);
  const outOfScope = Boolean(plan && environment && plan.environment !== environment);
  return <div className="studio-stack-lg recovery-center">
    <SectionCard title="Recovery Center" subtitle="Recover a pinned cohort with a small batch, controlled release, and observed results.">
      <p>Retry acceptance is not task success. Preview checks eligibility and cannot prove business side effects are safe.</p>
      <p className="studio-inline-meta">Pause and stop govern this plan’s new dispatch. Already claimed or accepted work continues. Unknown acceptance is never resent automatically.</p>
      <div className="studio-action-row"><Link to={environment ? `/failed-tasks?environment=${encodeURIComponent(environment)}` : "/failed-tasks"}>Select failures</Link>{planId ? <Link to={environment ? `/recovery?environment=${encodeURIComponent(environment)}` : "/recovery"}>All recovery plans</Link> : null}</div>
    </SectionCard>
    {error ? <NoticeBanner tone="error">{error}</NoticeBanner> : null}
    {readError ? <NoticeBanner tone="error">{readError} Displayed progress may be stale.</NoticeBanner> : null}
    {loading ? <p role="status">Loading recovery plans…</p> : null}
    {!planId && isAdmin ? <SectionCard title="Prepare a recovery plan" subtitle="One service and environment. Failure IDs and overrides are pinned when saved.">
      <fieldset disabled={pending} className="recovery-fields">
        <label>Service<select aria-label="Recovery service" style={inputStyle} value={serviceId} onChange={(e) => setServiceId(e.target.value)}><option value="">Choose service</option>{services.services.filter((s) => !environment || s.environment === environment).map((s) => <option key={s.service_id} value={s.service_id}>{s.name} · {s.environment}</option>)}</select></label>
        <p>Pinned environment: <strong>{scopedService?.environment || "Choose service"}</strong></p>
        <label>Failure IDs in batch order<textarea style={inputStyle} value={failureIds} rows={5} onChange={(e) => setFailureIds(e.target.value)} placeholder="One failure ID per line" /></label>
        <p className="studio-inline-meta">Put representative failures first. Each batch takes the next pending IDs in this exact order.</p>
        <label>Overrides by failure ID (optional JSON)<textarea style={inputStyle} rows={4} value={overrides} onChange={(e) => setOverrides(e.target.value)} placeholder={'{"failure-1":{"target_queue":"approved.stage","override_payload":{"task_id":"replacement-id"}}}'} /></label>
        <label>Seconds between dispatches<input type="number" min={1} max={60} style={inputStyle} value={interval} onChange={(e) => setIntervalSeconds(e.target.value)} /></label>
        <label>Operator note<input maxLength={1000} style={inputStyle} value={note} onChange={(e) => setNote(e.target.value)} /></label>
        <button style={primaryButtonStyle} disabled={pending} onClick={() => void mutate(inspect)}>Preview cohort</button>
      </fieldset>
      {preview ? <div className="studio-stack-sm">
        <p><strong>{preview.service_name} · {preview.environment}</strong> · {preview.items.length} pinned failures · one dispatch every {preview.interval_seconds}s</p>
        <p>{preview.base_url}</p><CohortItems service={preview.service_id} items={preview.items} />
        <button style={primaryButtonStyle} disabled={pending || !preview.eligible} onClick={() => void mutate(async () => {
          if (!selection) return;
          const currentScope = scope.current;
          const saved = await createRecoveryPlan(selection, preview.preview_digest, requestId.current);
          if (scope.current !== currentScope) return;
          navigate(`/recovery/${saved.plan_id}${environment ? `?environment=${encodeURIComponent(environment)}` : ""}`);
        })}>Save pinned plan</button>
      </div> : null}
    </SectionCard> : null}
    {!planId ? <SectionCard title="Recovery plans" subtitle={`${total} plans${environment ? ` in ${environment}` : " across environments"}`}>
      {!loading && !plans.length ? <p>No recovery plans yet. Select failed tasks to prepare the first cohort.</p> : null}
      <div className="studio-stack-sm">{plans.map((p) => <Link className="studio-subcard recovery-item" key={p.plan_id} to={`/recovery/${p.plan_id}${environment ? `?environment=${encodeURIComponent(environment)}` : ""}`}>
        <strong>{p.service_name} · {p.environment}</strong><span>{p.state} · {p.report.observed_success}/{p.report.selected} observed successes · {p.report.unknown_acceptance} acceptance unknown</span><span className="studio-inline-meta">{formatTimestamp(p.created_at)}</span>
      </Link>)}</div>
      <div className="studio-action-row"><button style={secondaryButtonStyle} disabled={offset === 0 || loading} onClick={() => setOffset((n) => Math.max(0, n - 50))}>Previous plans</button><button style={secondaryButtonStyle} disabled={offset + 50 >= total || loading} onClick={() => setOffset((n) => n + 50)}>Next plans</button></div>
    </SectionCard> : null}
    {plan ? <>
      <SectionCard title={`${plan.service_name} · ${plan.environment}`} subtitle={`Plan ${plan.plan_id}`}>
        <p>Dispatch: <strong>{plan.state === "completed" ? "completed (outcomes tracked separately)" : plan.state}</strong> · one new request every {plan.interval_seconds}s</p>
        <p className="studio-inline-meta">Pinned endpoint: {plan.base_url} · Started {formatTimestamp(plan.started_at)}</p>
        {outOfScope ? <NoticeBanner>This plan is pinned to {plan.environment}. Change the environment scope to control it.</NoticeBanner> : null}
        <div className="recovery-metrics" aria-label="Recovery report">{[
          ["Selected", plan.report.selected], ["Requested", plan.report.requested], ["Accepted", plan.report.accepted],
          ["Observed success", plan.report.observed_success], ["Observed failure", plan.report.observed_failure],
          ["Acceptance unknown", plan.report.unknown_acceptance], ["Outcome unknown", plan.report.unknown_outcome],
          ["Awaiting evidence", plan.report.awaiting_outcome], ["Repeated failures after replay", plan.report.repeated_failures],
        ].map(([label, value]) => <div className="studio-subcard" key={label}><strong>{value}</strong><span>{label}</span></div>)}</div>
        <p>Cohort recovery time: {plan.report.cohort_recovery_seconds == null ? "Not verified for the complete cohort" : `${plan.report.cohort_recovery_seconds.toFixed(1)} seconds`}</p>
        <p>Dispatch finished {formatTimestamp(plan.dispatch_finished_at)}. Evidence continues to be reconciled after dispatch finishes or stops.</p>
        {isAdmin && !outOfScope ? <fieldset disabled={pending} className="recovery-fields">
          {plan.state === "ready" || plan.state === "paused" ? <div className="studio-action-row"><label>Next batch size<input style={inputStyle} type="number" min={1} max={Math.min(200, pendingItems.length) || 1} value={batchCount} onChange={(e) => { setBatchCount(e.target.value); setConfirmation(null); }} /></label><button style={primaryButtonStyle} disabled={!pendingItems.length} onClick={() => { setConfirmationVersion(plan.version); setConfirmation("batch"); }}>Review next batch</button><button style={secondaryButtonStyle} disabled={!pendingItems.length} onClick={() => { setBatchCount(String(pendingItems.length)); setConfirmationVersion(plan.version); setConfirmation("batch"); }}>Review remaining {pendingItems.length}</button></div> : null}
          <div className="studio-action-row">
            {plan.state === "running" ? <button style={secondaryButtonStyle} onClick={() => void mutate(() => control("pause"))}>Pause new dispatch</button> : null}
            {plan.state === "paused" ? <button style={secondaryButtonStyle} disabled={plan.items.filter((i) => i.dispatch_state !== "pending").length >= plan.allowance} onClick={() => void mutate(() => control("resume"))}>Resume authorized batch</button> : null}
            {!["stopped", "completed"].includes(plan.state) ? <button style={secondaryButtonStyle} onClick={() => { setConfirmationVersion(plan.version); setConfirmation("stop"); }}>Stop new dispatch</button> : null}
          </div>
          {confirmation ? <div className="studio-subcard recovery-item" role="group" aria-label="Confirm recovery action">
            {confirmation === "batch" ? <><strong>Authorize {batch.length} retries on {plan.service_name} ({plan.environment})?</strong><p>Inspect prior observed outcomes before releasing more. {plan.report.unknown_acceptance} requests have unknown acceptance.</p><p>Next failure IDs: {batch.map((i) => i.failure_id).join(", ")}</p><p>Target queues: {Array.from(new Set(batch.map((i) => i.target_queue))).join(", ")} · Payload overrides: {batch.filter((i) => i.override_payload != null).length}</p><button style={primaryButtonStyle} disabled={!batch.length} onClick={() => void mutate(() => control("batch"))}>Authorize batch</button></> : <><strong>Stop this plan’s new dispatch?</strong><p>Already claimed or accepted work continues. This cannot be undone for this plan.</p><button style={primaryButtonStyle} onClick={() => void mutate(() => control("stop"))}>Confirm stop</button></>}
            <button style={secondaryButtonStyle} onClick={() => setConfirmation(null)}>Cancel</button>
          </div> : null}
        </fieldset> : null}
        <details><summary>Exportable recovery report (JSON)</summary><InlineCodeBox value={JSON.stringify(plan, null, 2)} /></details>
      </SectionCard>
      <SectionCard title="Per-item progress" subtitle="Original and replacement tasks remain linked to this exact service."><CohortItems service={plan.service_id} items={plan.items} /></SectionCard>
    </> : null}
  </div>;
}

import { AssessmentBuilder } from "../chamber/AssessmentBuilder";
import { ConnectionPanel } from "../chamber/ConnectionPanel";
import { RunResults } from "../chamber/RunResults";
import { TargetProfiles } from "../chamber/TargetProfiles";
import { JsonView } from "../chamber/common";
import { RunMonitor } from "../chamber/RunMonitor";
import { LoadProfileManager } from "./LoadProfileManager";
import { useEffect, useState, type FormEvent } from "react";
import { useParams, useSearchParams } from "react-router-dom";
import { fetchServiceLogs, fetchServiceMetrics, requestJson } from "../api";
import { useStudioAuth } from "../auth-context";
import { useStudioServices } from "../services-context";
import { Link } from "../scoped-link";
import { initialInput, RequestField, terminalLoadStates, type LoadProfile, type LoadRun } from "../load-testing";
import { LogMessage, NoticeBanner, SectionCard, formatTimestamp, inputStyle, primaryButtonStyle, secondaryButtonStyle } from "../ui";
import { MetricLineChart, metricLabel, seriesLabel, podMetricLineColor } from "./ServiceDetailPage";
import type { ServiceRecord, StudioLogListResponse, StudioMetricsResponse } from "../types";

const message = (error: unknown) => error instanceof Error ? error.message : "Unable to load test data.";
const body = (value: unknown) => ({ method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(value) });

export function LoadTestingPage() {
  const { serviceId = "" } = useParams();
  return <ServiceLoadTesting key={serviceId} serviceId={serviceId} />;
}

function ServiceLoadTesting({ serviceId }: { serviceId: string }) {
  const { isAdmin } = useStudioAuth();
  const { servicesById, loading, error: servicesError } = useStudioServices();
  const service = servicesById.get(serviceId);
  const [params, setParams] = useSearchParams();
  const selected = params.get("run") || "";
  const tab = ["configure", "monitor", "results", "profiles", "connection"].includes(params.get("tab") || "") ? params.get("tab")! : "configure";
  const [builder, setBuilder] = useState(false);
  const [confirmedTarget, setConfirmedTarget] = useState(false);
  const [confirmedFaults, setConfirmedFaults] = useState(false);
  const [visible, setVisible] = useState(!document.hidden);
  useEffect(() => { const update = () => setVisible(!document.hidden); document.addEventListener("visibilitychange", update); return () => document.removeEventListener("visibilitychange", update); }, []);
  useEffect(() => { setRun(null); setConfirmedTarget(false); setConfirmedFaults(false); }, [selected]);
  const base = `/studio/services/${encodeURIComponent(serviceId)}/load-tests`;
  const [profileRevision, setProfileRevision] = useState(0);
  const [importErrors, setImportErrors] = useState<string[]>([]);
  const [profiles, setProfiles] = useState<LoadProfile[]>([]);
  const [setup, setSetup] = useState("Loading load-test profiles…");
  const [profileId, setProfileId] = useState("");
  const profile = profiles.find((item) => item.id === profileId);
  const [inputs, setInputs] = useState<Record<string, unknown>>({});
  const [vus, setVus] = useState(1);
  const [iterations, setIterations] = useState(1);
  const [duration, setDuration] = useState(30);
  const [history, setHistory] = useState<LoadRun[]>([]);
  const [run, setRun] = useState<LoadRun | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [revision, setRevision] = useState(0);

  useEffect(() => {
    let alive = true;
    void Promise.allSettled([
      requestJson<{ profiles: LoadProfile[]; message: string; errors?: string[] }>(`${base}/profiles`),
      requestJson<{ items: LoadRun[] }>(base),
    ]).then(([options, recent]) => {
      if (!alive) return;
      if (options.status === "fulfilled") {
        setProfiles(options.value.profiles); setSetup(options.value.message); setImportErrors(options.value.errors || []);
        if (options.value.profiles[0]) chooseProfile(options.value.profiles[0]);
      } else { setSetup(""); setError(message(options.reason)); }
      if (recent.status === "fulfilled") setHistory(recent.value.items);
      else setError(message(recent.reason));
    });
    return () => { alive = false; };
  }, [base, profileRevision]);

  useEffect(() => {
    let alive = true;
    let timer: ReturnType<typeof setTimeout>;
    if (!selected) return;
    async function poll() {
      try {
        const result = await requestJson<LoadRun>(`${base}/${encodeURIComponent(selected)}`);
        if (!alive) return;
        setRun(result); setError("");
        setHistory((old) => [result, ...old.filter((item) => item.id !== result.id)].slice(0, 20));
        if (visible && (tab === "configure" || tab === "monitor") && result.state !== "planned" && !terminalLoadStates.has(result.state)) timer = setTimeout(poll, 4000);
      } catch (failure) {
        if (!alive) return;
        setError(message(failure));
        if (visible && (tab === "configure" || tab === "monitor")) timer = setTimeout(poll, 10000);
      }
    }
    void poll();
    return () => { alive = false; clearTimeout(timer); };
  }, [base, selected, revision, visible, tab]);

  function chooseProfile(next: LoadProfile) {
    setProfileId(next.id); setInputs(initialInput(next.input_schema) as Record<string, unknown>);
    setVus(1); setIterations(1); setDuration(Math.min(30, next.max_duration_seconds));
  }
  function selectRun(id: string, nextTab?: string) {
    setError("");
    setParams((old) => { const next = new URLSearchParams(old); if (id) next.set("run", id); else next.delete("run"); next.delete("chamber_run"); if (nextTab) next.set("tab", nextTab); return next; });
  }
  function chooseTab(value: string) { setParams((old) => { const next = new URLSearchParams(old); next.set("tab", value); return next; }); }
  function acceptPlan(result: LoadRun) { setHistory((old) => [result, ...old.filter((item) => item.id !== result.id)].slice(0, 20)); setRun(result); setParams((old) => { const next = new URLSearchParams(old); next.set("run", result.id); next.set("tab", "configure"); next.delete("chamber_run"); return next; }); }
  async function createPlan(event: FormEvent) {
    event.preventDefault();
    if (!profile || busy) return;
    setBusy(true); setError("");
    try {
      const result = await requestJson<LoadRun>(`${base}/plans`, body({ profile_id: profile.id, schema_revision: profile.schema_revision, inputs, vus, iterations, duration_seconds: duration }));
      setHistory((old) => [result, ...old.filter((item) => item.id !== result.id)].slice(0, 20)); selectRun(result.id);
    } catch (failure) { setError(message(failure)); }
    finally { setBusy(false); }
  }
  async function act(action: "start" | "cancel") {
    if (!run || busy) return;
    setBusy(true); setError("");
    try {
      const result = await requestJson<LoadRun>(`${base}/${encodeURIComponent(run.id)}/${action}`, action === "start" && run.kind === "assessment" ? body({ confirmed_target: confirmedTarget, confirmed_faults: confirmedFaults }) : { method: "POST" });
      setRun(result); setRevision((old) => old + 1);
    } catch (failure) { setError(message(failure)); }
    finally { setBusy(false); }
  }

  const reviewedLoad = run?.load_summary?.load as { model?: string; durationSeconds?: number; maxInFlight?: number; ratePerSecond?: number } | undefined;
  if (loading && !service) return <p role="status">Loading service…</p>;
  if (!service) return <NoticeBanner tone="error">{servicesError || "This service is not in the Studio registry."}</NoticeBanner>;
  return <div className="load-workspace">
    <header className="load-heading"><div><Link to={`/services/${encodeURIComponent(serviceId)}`}>← {service.name}</Link>
      <h1>Load testing</h1><p>Test service capacity and follow every run in Studio.</p></div>
      <div className="load-context"><span>{service.environment}</span><strong>{service.name}</strong><small>Powered by Ampule Chamber</small></div>
    </header>
    {error && <NoticeBanner tone="error">{error}</NoticeBanner>}
    {!isAdmin && <NoticeBanner tone="info">You have read-only access. An administrator can create and start load tests.</NoticeBanner>}
    <nav className="chamber-main-tabs" aria-label="Load testing workspace">{["configure", "monitor", "results", "profiles", "connection"].map((value) => <button key={value} aria-current={tab === value ? "page" : undefined} onClick={() => chooseTab(value)}>{value[0].toUpperCase() + value.slice(1)}</button>)}</nav>
    <div className={`load-layout${tab === "monitor" ? " load-layout--monitor" : ""}`}><div className="load-main">
      {tab === "connection" && <ConnectionPanel base={base} isAdmin={isAdmin} onChanged={() => setProfileRevision((old) => old + 1)} />}
      {tab === "profiles" && <><SectionCard title="Approved operations" subtitle="Import one reviewed operation with safe request fields and limits. Preview expires after 30 minutes; its service, environment and schema must still match when saved.">{isAdmin ? <LoadProfileManager base={base} onSaved={() => setProfileRevision((old) => old + 1)} /> : <p>An administrator manages approved operation profiles.</p>}<p>Multi-journey suites and experiments remain available in Full assessment configuration. They are not narrowed into an approved single operation.</p></SectionCard><TargetProfiles base={base} isAdmin={isAdmin} /></>}
      {tab === "results" && <RunResults base={base} serviceId={serviceId} run={run} isAdmin={isAdmin} onPlan={acceptPlan} onRefresh={() => setRevision((old) => old + 1)} />}
      {tab === "monitor" && (run && run.state !== "planned" ? <><SectionCard title="Run monitoring" subtitle={`${run.profile_name} · ${run.environment}`}><p>Execution: {run.state} · Assessment: {run.result?.status || "Awaiting evidence"}</p><pre className="load-output" tabIndex={0}>{run.output || "Waiting for runner output…"}</pre><div className="studio-action-row"><button style={secondaryButtonStyle} onClick={() => setRevision((old) => old + 1)}>Refresh run</button>{!terminalLoadStates.has(run.state) && <button style={secondaryButtonStyle} disabled={!isAdmin || busy || run.cancel_requested} onClick={() => void act("cancel")}>Cancel load test</button>}</div>{run.evidence_error && <NoticeBanner tone="info">{run.evidence_error}</NoticeBanner>}</SectionCard>{run.environment === service.environment ? <RunMonitor service={service} run={run} base={base} active={visible} /> : <NoticeBanner tone="info">This run belongs to a previous service environment. Runner output and cancellation remain available; current service telemetry is hidden.</NoticeBanner>}</> : <SectionCard title="Run monitoring"><p>Select a started run to inspect its task explorer, logs and investigation layout.</p></SectionCard>)}
      {tab === "configure" && <>
      {!selected && <div className="chamber-subnav" aria-label="Configuration mode"><button aria-current={!builder ? "page" : undefined} onClick={() => setBuilder(false)}>Approved operation</button><button aria-current={builder ? "page" : undefined} onClick={() => setBuilder(true)}>Full assessment</button></div>}
      {!selected && builder ? <AssessmentBuilder base={base} service={service} isAdmin={isAdmin} onPlan={acceptPlan} /> : !selected ? <SectionCard title="Configure a load test" subtitle="Choose an approved operation. Its target and input format are already defined for this service.">
        {setup && <p role="status">{setup}</p>}
        <button type="button" style={secondaryButtonStyle} onClick={() => setProfileRevision((old) => old + 1)}>Refresh operations</button>
        {isAdmin && <button type="button" style={secondaryButtonStyle} onClick={() => chooseTab("profiles")}>Manage profiles</button>}
        {importErrors.map((item) => <NoticeBanner tone="error" key={item}>{item}</NoticeBanner>)}
        {profile && <form onSubmit={(event) => void createPlan(event)}>
          <fieldset disabled={!isAdmin || busy} className="load-form-fields">
            <div className="load-field"><label htmlFor="load-operation">Operation</label><select id="load-operation" style={inputStyle} value={profileId} onChange={(event) => chooseProfile(profiles.find((item) => item.id === event.target.value)!)}>{profiles.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select></div>
            <div className="load-target"><code>{profile.method} {profile.path}</code><span>Namespace: {profile.namespace}</span></div>
            {profile.schema_source === "openapi" && <p className="load-muted">Request fields imported from this service’s OpenAPI definition.</p>}
            <RequestField schema={profile.input_schema} value={inputs} onChange={(next) => setInputs(next as Record<string, unknown>)} label="Request inputs" />
            {profile.files?.length ? <div className="load-target"><strong>Test files</strong>{profile.files.map((file) => <span key={file.field}>{file.field}: {file.filename} ({file.content_type})</span>)}</div> : null}
            <h3>Load settings</h3><div className="load-controls">
              <label>Concurrent users<input aria-label="Concurrent users" style={inputStyle} type="number" required min={1} max={profile.max_vus} value={vus} onChange={(event) => setVus(Number(event.target.value))} /><small>Maximum {profile.max_vus}</small></label>
              {profile.adapter === "relayna" && <label>Task iterations<input aria-label="Task iterations" style={inputStyle} type="number" required min={1} max={profile.max_iterations} value={iterations} onChange={(event) => setIterations(Number(event.target.value))} /><small>Total tasks submitted</small></label>}
              <label>{profile.adapter === "relayna" ? "Scheduling window (seconds)" : "Ramp duration (seconds)"}<input aria-label="Duration seconds" style={inputStyle} type="number" required min={1} max={profile.max_duration_seconds} value={duration} onChange={(event) => setDuration(Number(event.target.value))} /><small>Maximum {profile.max_duration_seconds} seconds</small></label>
            </div><p className="load-muted">{profile.adapter === "relayna" ? "Each iteration submits a task and follows its lifecycle. Task completion may extend beyond the scheduling window." : "HTTP traffic ramps to the selected concurrency over this duration. Request count depends on service response time."}</p>
            <button style={primaryButtonStyle} type="submit">{busy ? "Preparing plan…" : "Review load test"}</button>
          </fieldset>
        </form>}
      </SectionCard> : run ? <>
        <SectionCard title={run.state === "planned" ? "Review and start" : run.profile_name} subtitle={`${run.method} ${run.path} · ${run.environment} · ${run.namespace}`}>
          <div className="load-run-status"><span className={`load-state load-state--${run.state}`}>{run.cancel_requested && !terminalLoadStates.has(run.state) ? "Cancellation requested" : run.state}</span><span>{formatTimestamp(run.started_at || run.created_at)}</span></div>
          <div className="load-summary"><div><strong>{run.kind === "assessment" && Array.isArray(run.load_summary?.journeys) ? run.load_summary.journeys.length : run.request.vus}</strong><span>{run.kind === "assessment" ? "Reviewed journeys" : "Concurrent users"}</span></div><div><strong>{run.kind === "assessment" ? reviewedLoad?.model || "Journey schedule" : run.adapter === "relayna" ? run.request.iterations : `${run.request.duration_seconds}s`}</strong><span>{run.kind === "assessment" ? "Load model" : run.adapter === "relayna" ? "Task iterations" : "Traffic ramp"}</span></div><div><strong>{run.requires_fault_confirmation ? "Faults configured" : "Observe only"}</strong><span>{run.kind === "assessment" ? "Reviewed assessment" : "No injected faults"}</span></div></div>
          {run.target && <div className="chamber-review"><h3>Reviewed target</h3><dl className="chamber-facts"><div><dt>Cluster / namespace</dt><dd>{run.target.context || "Local runtime"} / {run.target.namespace || "—"}</dd></div><div><dt>Service / port</dt><dd>{run.target.service}:{run.target.port || "—"}</dd></div><div><dt>Workloads</dt><dd>{run.target.workloads?.join(", ") || "—"}</dd></div><div><dt>Runtime / telemetry</dt><dd>{run.target.provider} {run.target.runtime_mode} · {run.target.prometheus_configured ? "Configured · unchecked" : "Not configured"}</dd></div></dl></div>}
          {run.kind === "assessment" && <>{reviewedLoad && <p>Suite duration: {reviewedLoad.durationSeconds ?? "See stages"} seconds · Maximum in-flight: {reviewedLoad.maxInFlight ?? "Not specified"} · Arrival rate: {reviewedLoad.ratePerSecond ?? "See stages"}/s. Journey schedules and capacity steps are shown below.</p>}<JsonView value={run.load_summary} title="Review all journeys, requests, schedules and performance gates" /><JsonView value={run.review_config} title="Review experiment, recovery and complete assessment configuration" /></>}
          {run.files?.length ? <p>Test files: {run.files.map((file) => `${file.field}: ${file.filename}`).join(", ")}</p> : null}
          {run.state === "planned" && <><p>This test will send traffic to <strong>{service.name}</strong> in <strong>{run.environment}</strong>. Check the request and load before starting.</p><dl className="load-review-inputs">{Object.entries(run.request.inputs).map(([key, value]) => <div key={key}><dt>{key}</dt><dd>{typeof value === "object" ? JSON.stringify(value) : String(value)}</dd></div>)}</dl>
            {run.requires_target_confirmation && <label className="load-import-confirm"><input type="checkbox" checked={confirmedTarget} onChange={(e) => setConfirmedTarget(e.target.checked)} />I reviewed the target, all requests, load schedule and limits.</label>}{run.requires_fault_confirmation && <label className="load-import-confirm"><input type="checkbox" checked={confirmedFaults} onChange={(e) => setConfirmedFaults(e.target.checked)} />I approve the selected faults and have reviewed restoration and cleanup.</label>}
            <div className="studio-action-row"><button style={primaryButtonStyle} disabled={!isAdmin || busy || service.status === "disabled" || Boolean(run.requires_target_confirmation && !confirmedTarget) || Boolean(run.requires_fault_confirmation && !confirmedFaults)} onClick={() => void act("start")}>{busy ? "Starting…" : "Start load test"}</button><button style={secondaryButtonStyle} disabled={busy} onClick={() => selectRun("")}>Back to configuration</button></div></>}
          {run.state !== "planned" && <div className="studio-action-row"><button style={primaryButtonStyle} onClick={() => chooseTab("monitor")}>Open run monitor</button><button style={secondaryButtonStyle} onClick={() => chooseTab("results")}>Explore full results</button><button style={secondaryButtonStyle} onClick={() => setRevision((old) => old + 1)}>Refresh run</button>{!terminalLoadStates.has(run.state) && <button style={secondaryButtonStyle} disabled={!isAdmin || busy || run.cancel_requested} onClick={() => void act("cancel")}>Cancel load test</button>}</div>}
          {run.error && <NoticeBanner tone="error">{run.error}</NoticeBanner>}
          {run.cleanup_required && <NoticeBanner tone="error">Chamber reports cleanup requires operator attention. Inspect the runner before starting another test.</NoticeBanner>}
          {run.evidence_error && <p role="status">{run.evidence_error}</p>}
          {run.result?.status && <div className="load-result"><h3>Assessment: {run.result.status}</h3><p>Evidence coverage: {run.result.evidence_coverage_percent ?? "Unavailable"}{run.result.evidence_coverage_percent != null ? "%" : ""} · Readiness score: {run.result.readiness_score ?? "Unavailable"}</p>{run.result.limitations?.map((item, index) => <p key={index}>{item}</p>)}</div>}
        </SectionCard>
        {run.state !== "planned" && <>
          <SectionCard title="Execution output" subtitle="Retained runner output; updates while the test is active."><pre className="load-output" tabIndex={0}>{run.output || "Waiting for runner output…"}</pre></SectionCard>
          <SectionCard title="Test tasks" subtitle="Exact task IDs reported by Chamber link directly to task logs, events and metrics.">
            {run.tasks_truncated && <p>{run.task_count} tasks reported. This preview is bounded; Results → Tasks provides complete pagination and failed-first filtering.</p>}
            {run.tasks?.length ? <div className="load-task-list">{run.tasks.map((task) => <Link key={task.task_id} to={`/tasks/${encodeURIComponent(serviceId)}/${encodeURIComponent(task.task_id)}`}><strong>{task.task_id}</strong><span>{task.terminal_status || "Pending"}</span></Link>)}</div> : <p>Task evidence has not arrived. Service logs below remain available during execution.</p>}
          </SectionCard>
          {run.environment === service.environment ? <RunTelemetry key={run.id} service={service} run={run} active={visible} /> : <NoticeBanner tone="info">This run targets {run.environment}; the service is now registered in {service.environment}. Runner output and cancellation remain available. Current service telemetry is hidden because it may describe another environment.</NoticeBanner>}
        </>}
      </> : <p role="status">Loading load test…</p>}
      </>}
    </div><aside className="load-history"><SectionCard title="Recent load tests" subtitle="Latest 20 plans and runs · retained for 30 days">
      <button type="button" style={secondaryButtonStyle} onClick={() => selectRun("", "configure")}>New load test</button>
      {!history.length && <p>Your service's load tests will appear here.</p>}
      {history.map((item) => <button className={`load-history-item ${selected === item.id ? "is-selected" : ""}`} aria-current={selected === item.id ? "true" : undefined} key={item.id} onClick={() => selectRun(item.id, tab === "connection" || tab === "profiles" ? "configure" : tab)}><strong>{item.profile_name}</strong><span>{item.state} · {formatTimestamp(item.created_at)}</span></button>)}
    </SectionCard></aside></div>
  </div>;
}

function RunTelemetry({ service, run, active }: { service: ServiceRecord; run: LoadRun; active: boolean }) {
  const [logs, setLogs] = useState<StudioLogListResponse | null>(null);
  const [metrics, setMetrics] = useState<StudioMetricsResponse | null>(null);
  const [logsError, setLogsError] = useState("");
  const [metricsError, setMetricsError] = useState("");
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState("");
  const [logsUpdated, setLogsUpdated] = useState("");
  const [metricsUpdated, setMetricsUpdated] = useState("");
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    let alive = true;
    let timer: ReturnType<typeof setTimeout>;
    if (!active) return;
    async function refresh() {
      const window = { from: run.started_at || run.created_at, to: run.finished_at || new Date().toISOString() };
      const [logResult, metricResult] = await Promise.allSettled([
        service.log_config ? fetchServiceLogs(service.service_id, { ...window, limit: 50, query: filter }) : Promise.resolve(null),
        service.metrics_config ? fetchServiceMetrics(service.service_id, { ...window, split_by_pod: true, groups: ["cpu_usage", "memory_usage", "restarts", "oom_killed", "readiness"] }) : Promise.resolve(null),
      ]);
      if (!alive) return;
      if (logResult.status === "fulfilled") { setLogs(logResult.value); setLogsError(""); setLogsUpdated(new Date().toISOString()); } else { setLogsError(message(logResult.reason)); }
      if (metricResult.status === "fulfilled") { setMetrics(metricResult.value); setMetricsError(""); setMetricsUpdated(new Date().toISOString()); } else { setMetricsError(message(metricResult.reason)); }
      if (!run.finished_at) timer = setTimeout(refresh, 10000);
    }
    void refresh();
    return () => { alive = false; clearTimeout(timer); };
  }, [service.service_id, service.log_config, service.metrics_config, run.started_at, run.created_at, run.finished_at, filter, revision, active]);
  const groups = Array.from(new Set(metrics?.series.map((item) => item.metric) || []));
  return <>
    <SectionCard title="Kubernetes pod metrics" subtitle={`Service pods during this run · ${metricsUpdated ? `last successful query ${formatTimestamp(metricsUpdated)}` : "Loading telemetry…"}`}>
      <button style={secondaryButtonStyle} onClick={() => setRevision((old) => old + 1)}>Refresh telemetry</button>
      {!service.metrics_config && <p>No metrics provider configured. <Link to={`/services/${encodeURIComponent(service.service_id)}`}>Configure this service's Prometheus connection</Link> to view pod metrics.</p>}
      {metricsError && <NoticeBanner tone="error">{metricsError}{metrics && " · Showing stale samples from the last successful query."}</NoticeBanner>}
      {metrics?.warnings.map((warning) => <p key={warning}>{warning}</p>)}
      {service.metrics_config && metrics && !groups.length && <p>No pod samples were reported for this run window.</p>}
      <div className="load-metrics">{groups.map((group) => {
        const series = metrics!.series.filter((item) => item.metric === group);
        return <div key={group}><h3>{metricLabel(group)}</h3><MetricLineChart series={series} podLabel={service.metrics_config?.pod_label} label={`${metricLabel(group)} graph`} /><div className="studio-chart-legend" aria-label={`${metricLabel(group)} legend`}>{series.map((item, index) => <span className="studio-chart-legend__item" key={index}><span className="studio-chart-legend__swatch" style={{ backgroundColor: podMetricLineColor(index) }} aria-hidden="true" />{seriesLabel(item, service.metrics_config?.pod_label)}</span>)}</div><details><summary>{metricLabel(group)} sample values</summary><pre tabIndex={0}>{series.map((item) => `${seriesLabel(item, service.metrics_config?.pod_label)}: ${item.points.map((point) => `${formatTimestamp(point.timestamp)} = ${point.value ?? "Missing"} ${item.unit}`).join("; ")}`).join("\n")}</pre></details></div>;
      })}</div>
    </SectionCard>
    <SectionCard title="Service and task logs" subtitle={`Latest 50 matching log entries in the run window. These may include other traffic to the same service. ${logsUpdated ? `Last successful query ${formatTimestamp(logsUpdated)}` : "No successful query yet."}`}>
      {!service.log_config ? <p>No log provider configured. <Link to={`/services/${encodeURIComponent(service.service_id)}`}>Configure Loki for this service</Link> to view logs.</p> : <>
        <form className="load-log-filter" onSubmit={(event) => { event.preventDefault(); setFilter(query); }}><label htmlFor="load-log-query">Filter logs<input id="load-log-query" style={inputStyle} value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Task ID or message" /></label><button style={secondaryButtonStyle}>Apply filter</button></form>
        {logsError && <NoticeBanner tone="error">{logsError}{logs && " · Showing stale entries from the last successful query."}</NoticeBanner>}
        {logs && !logs.items.length && <p>No logs matched this run window and filter.</p>}
        <div className="load-log-list">{logs?.items.map((item, index) => <article key={`${item.timestamp}-${index}`}><div><time>{formatTimestamp(item.timestamp)}</time><span>{item.level || "INFO"} · {item.source}</span>{item.task_id && <Link to={`/tasks/${encodeURIComponent(service.service_id)}/${encodeURIComponent(item.task_id)}`}>{item.task_id}</Link>}</div><LogMessage message={item.message} /></article>)}</div>
      </>}
    </SectionCard>
  </>;
}

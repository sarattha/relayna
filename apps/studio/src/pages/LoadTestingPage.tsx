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
  const base = `/studio/services/${encodeURIComponent(serviceId)}/load-tests`;
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
      requestJson<{ profiles: LoadProfile[]; message: string }>(`${base}/profiles`),
      requestJson<{ items: LoadRun[] }>(base),
    ]).then(([options, recent]) => {
      if (!alive) return;
      if (options.status === "fulfilled") {
        setProfiles(options.value.profiles); setSetup(options.value.message);
        if (options.value.profiles[0]) chooseProfile(options.value.profiles[0]);
      } else { setSetup(""); setError(message(options.reason)); }
      if (recent.status === "fulfilled") setHistory(recent.value.items);
      else setError(message(recent.reason));
    });
    return () => { alive = false; };
  }, [base]);

  useEffect(() => {
    let alive = true;
    let timer: ReturnType<typeof setTimeout>;
    setRun(null);
    if (!selected) return;
    async function poll() {
      try {
        const result = await requestJson<LoadRun>(`${base}/${encodeURIComponent(selected)}`);
        if (!alive) return;
        setRun(result); setError("");
        setHistory((old) => [result, ...old.filter((item) => item.id !== result.id)].slice(0, 20));
        if (result.state !== "planned" && !terminalLoadStates.has(result.state)) timer = setTimeout(poll, 4000);
      } catch (failure) {
        if (!alive) return;
        setError(message(failure));
        timer = setTimeout(poll, 10000);
      }
    }
    void poll();
    return () => { alive = false; clearTimeout(timer); };
  }, [base, selected, revision]);

  function chooseProfile(next: LoadProfile) {
    setProfileId(next.id); setInputs(initialInput(next.input_schema) as Record<string, unknown>);
    setVus(1); setIterations(1); setDuration(Math.min(30, next.max_duration_seconds));
  }
  function selectRun(id: string) {
    setError("");
    setParams((old) => { const next = new URLSearchParams(old); if (id) next.set("run", id); else next.delete("run"); return next; });
  }
  async function createPlan(event: FormEvent) {
    event.preventDefault();
    if (!profile || busy) return;
    setBusy(true); setError("");
    try {
      const result = await requestJson<LoadRun>(`${base}/plans`, body({ profile_id: profile.id, inputs, vus, iterations, duration_seconds: duration }));
      setHistory((old) => [result, ...old].slice(0, 20)); selectRun(result.id);
    } catch (failure) { setError(message(failure)); }
    finally { setBusy(false); }
  }
  async function act(action: "start" | "cancel") {
    if (!run || busy) return;
    setBusy(true); setError("");
    try {
      const result = await requestJson<LoadRun>(`${base}/${encodeURIComponent(run.id)}/${action}`, { method: "POST" });
      setRun(result); setRevision((old) => old + 1);
    } catch (failure) { setError(message(failure)); }
    finally { setBusy(false); }
  }

  if (loading && !service) return <p role="status">Loading service…</p>;
  if (!service) return <NoticeBanner tone="error">{servicesError || "This service is not in the Studio registry."}</NoticeBanner>;
  return <div className="load-workspace">
    <header className="load-heading"><div><Link to={`/services/${encodeURIComponent(serviceId)}`}>← {service.name}</Link>
      <h1>Load testing</h1><p>Test service capacity and follow every run in Studio.</p></div>
      <div className="load-context"><span>{service.environment}</span><strong>{service.name}</strong><small>Powered by Ampule Chamber</small></div>
    </header>
    {error && <NoticeBanner tone="error">{error}</NoticeBanner>}
    {!isAdmin && <NoticeBanner tone="info">You have read-only access. An administrator can create and start load tests.</NoticeBanner>}
    <div className="load-layout"><div className="load-main">
      {!selected ? <SectionCard title="Configure a load test" subtitle="Choose an approved operation. Its target and input format are already defined for this service.">
        {setup && <p role="status">{setup}</p>}
        {profile && <form onSubmit={(event) => void createPlan(event)}>
          <fieldset disabled={!isAdmin || busy} className="load-form-fields">
            <div className="load-field"><label htmlFor="load-operation">Operation</label><select id="load-operation" style={inputStyle} value={profileId} onChange={(event) => chooseProfile(profiles.find((item) => item.id === event.target.value)!)}>{profiles.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select></div>
            <div className="load-target"><code>{profile.method} {profile.path}</code><span>Namespace: {profile.namespace}</span></div>
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
          <div className="load-summary"><div><strong>{run.request.vus}</strong><span>Concurrent users</span></div><div><strong>{run.adapter === "relayna" ? run.request.iterations : `${run.request.duration_seconds}s`}</strong><span>{run.adapter === "relayna" ? "Task iterations" : "Traffic ramp"}</span></div><div><strong>Observe only</strong><span>No injected faults</span></div></div>
          {run.files?.length ? <p>Test files: {run.files.map((file) => `${file.field}: ${file.filename}`).join(", ")}</p> : null}
          {run.state === "planned" && <><p>This test will send traffic to <strong>{service.name}</strong> in <strong>{run.environment}</strong>. Check the request and load before starting.</p><dl className="load-review-inputs">{Object.entries(run.request.inputs).map(([key, value]) => <div key={key}><dt>{key}</dt><dd>{typeof value === "object" ? JSON.stringify(value) : String(value)}</dd></div>)}</dl>
            <div className="studio-action-row"><button style={primaryButtonStyle} disabled={!isAdmin || busy || service.status === "disabled"} onClick={() => void act("start")}>{busy ? "Starting…" : "Start load test"}</button><button style={secondaryButtonStyle} disabled={busy} onClick={() => selectRun("")}>Back to configuration</button></div></>}
          {run.state !== "planned" && <div className="studio-action-row"><button style={secondaryButtonStyle} onClick={() => setRevision((old) => old + 1)}>Refresh run</button>{!terminalLoadStates.has(run.state) && <button style={secondaryButtonStyle} disabled={!isAdmin || busy || run.cancel_requested} onClick={() => void act("cancel")}>Cancel load test</button>}</div>}
          {run.error && <NoticeBanner tone="error">{run.error}</NoticeBanner>}
          {run.cleanup_required && <NoticeBanner tone="error">Chamber reports cleanup requires operator attention. Inspect the runner before starting another test.</NoticeBanner>}
          {run.evidence_error && <p role="status">{run.evidence_error}</p>}
          {run.result?.status && <div className="load-result"><h3>Assessment: {run.result.status}</h3><p>Evidence coverage: {run.result.evidence_coverage_percent ?? "Unavailable"}{run.result.evidence_coverage_percent != null ? "%" : ""} · Readiness score: {run.result.readiness_score ?? "Unavailable"}</p>{run.result.limitations?.map((item, index) => <p key={index}>{item}</p>)}</div>}
        </SectionCard>
        {run.state !== "planned" && <>
          <SectionCard title="Execution output" subtitle="Retained runner output; updates while the test is active."><pre className="load-output" tabIndex={0}>{run.output || "Waiting for runner output…"}</pre></SectionCard>
          <SectionCard title="Test tasks" subtitle="Exact task IDs reported by Chamber link directly to task logs, events and metrics.">
            {run.tasks?.length ? <div className="load-task-list">{run.tasks.map((task) => <Link key={task.task_id} to={`/tasks/${encodeURIComponent(serviceId)}/${encodeURIComponent(task.task_id)}`}><strong>{task.task_id}</strong><span>{task.terminal_status || "Pending"}</span></Link>)}</div> : <p>Task evidence has not arrived. Service logs below remain available during execution.</p>}
          </SectionCard>
          <RunTelemetry key={run.id} service={service} run={run} />
        </>}
      </> : <p role="status">Loading load test…</p>}
    </div><aside className="load-history"><SectionCard title="Recent load tests" subtitle="Latest 20 plans and runs · retained for 30 days">
      <button type="button" style={secondaryButtonStyle} onClick={() => selectRun("")}>New load test</button>
      {!history.length && <p>Your service's load tests will appear here.</p>}
      {history.map((item) => <button className={`load-history-item ${selected === item.id ? "is-selected" : ""}`} aria-current={selected === item.id ? "true" : undefined} key={item.id} onClick={() => selectRun(item.id)}><strong>{item.profile_name}</strong><span>{item.state} · {formatTimestamp(item.created_at)}</span></button>)}
    </SectionCard></aside></div>
  </div>;
}

function RunTelemetry({ service, run }: { service: ServiceRecord; run: LoadRun }) {
  const [logs, setLogs] = useState<StudioLogListResponse | null>(null);
  const [metrics, setMetrics] = useState<StudioMetricsResponse | null>(null);
  const [logsError, setLogsError] = useState("");
  const [metricsError, setMetricsError] = useState("");
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState("");
  const [updated, setUpdated] = useState("");
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    let alive = true;
    let timer: ReturnType<typeof setTimeout>;
    async function refresh() {
      const window = { from: run.started_at || run.created_at, to: run.finished_at || new Date().toISOString() };
      const [logResult, metricResult] = await Promise.allSettled([
        service.log_config ? fetchServiceLogs(service.service_id, { ...window, limit: 50, query: filter }) : Promise.resolve(null),
        service.metrics_config ? fetchServiceMetrics(service.service_id, { ...window, split_by_pod: true, groups: ["cpu_usage", "memory_usage", "restarts", "oom_killed", "readiness"] }) : Promise.resolve(null),
      ]);
      if (!alive) return;
      if (logResult.status === "fulfilled") { setLogs(logResult.value); setLogsError(""); } else { setLogs(null); setLogsError(message(logResult.reason)); }
      if (metricResult.status === "fulfilled") { setMetrics(metricResult.value); setMetricsError(""); } else { setMetrics(null); setMetricsError(message(metricResult.reason)); }
      setUpdated(new Date().toISOString());
      if (!run.finished_at) timer = setTimeout(refresh, 10000);
    }
    void refresh();
    return () => { alive = false; clearTimeout(timer); };
  }, [service.service_id, service.log_config, service.metrics_config, run.started_at, run.created_at, run.finished_at, filter, revision]);
  const groups = Array.from(new Set(metrics?.series.map((item) => item.metric) || []));
  return <>
    <SectionCard title="AKS pod metrics" subtitle={`Service pods during this run · ${updated ? `queried ${formatTimestamp(updated)}` : "Loading telemetry…"}`}>
      <button style={secondaryButtonStyle} onClick={() => setRevision((old) => old + 1)}>Refresh telemetry</button>
      {!service.metrics_config && <p>No metrics provider configured. <Link to={`/services/${encodeURIComponent(service.service_id)}`}>Configure this service's Prometheus connection</Link> to view pod metrics.</p>}
      {metricsError && <NoticeBanner tone="error">{metricsError}</NoticeBanner>}
      {metrics?.warnings.map((warning) => <p key={warning}>{warning}</p>)}
      {service.metrics_config && metrics && !groups.length && <p>No pod samples were reported for this run window.</p>}
      <div className="load-metrics">{groups.map((group) => {
        const series = metrics!.series.filter((item) => item.metric === group);
        return <div key={group}><h3>{metricLabel(group)}</h3><MetricLineChart series={series} podLabel={service.metrics_config?.pod_label} /><div className="studio-chart-legend" aria-label={`${metricLabel(group)} legend`}>{series.map((item, index) => <span className="studio-chart-legend__item" key={index}><span className="studio-chart-legend__swatch" style={{ backgroundColor: podMetricLineColor(index) }} aria-hidden="true" />{seriesLabel(item, service.metrics_config?.pod_label)}</span>)}</div></div>;
      })}</div>
    </SectionCard>
    <SectionCard title="Service and task logs" subtitle="Latest 50 matching log entries in the run window. These may include other traffic to the same service.">
      {!service.log_config ? <p>No log provider configured. <Link to={`/services/${encodeURIComponent(service.service_id)}`}>Configure Loki for this service</Link> to view logs.</p> : <>
        <form className="load-log-filter" onSubmit={(event) => { event.preventDefault(); setFilter(query); }}><label htmlFor="load-log-query">Filter logs<input id="load-log-query" style={inputStyle} value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Task ID or message" /></label><button style={secondaryButtonStyle}>Apply filter</button></form>
        {logsError && <NoticeBanner tone="error">{logsError}</NoticeBanner>}
        {logs && !logs.items.length && <p>No logs matched this run window and filter.</p>}
        <div className="load-log-list">{logs?.items.map((item, index) => <article key={`${item.timestamp}-${index}`}><div><time>{formatTimestamp(item.timestamp)}</time><span>{item.level || "INFO"} · {item.source}</span>{item.task_id && <Link to={`/tasks/${encodeURIComponent(service.service_id)}/${encodeURIComponent(item.task_id)}`}>{item.task_id}</Link>}</div><LogMessage message={item.message} /></article>)}</div>
      </>}
    </SectionCard>
  </>;
}

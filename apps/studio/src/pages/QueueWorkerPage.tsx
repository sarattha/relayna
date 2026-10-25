import { useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";

import { fetchCockpit } from "../api";
import { duration, isFresh, latestMetric } from "../cockpit-evidence";
import { Link } from "../scoped-link";
import { useStudioServices } from "../services-context";
import type {
  CockpitResponse,
  ServiceRecord,
  StudioMetricSeries,
} from "../types";
import {
  EmptyState,
  formatTimestamp,
  inputStyle,
  MetricCard,
  NoticeBanner,
  SectionCard,
  secondaryButtonStyle,
  TaskRefLink,
} from "../ui";

type Read = { data?: CockpitResponse; error?: string };
const value = (v: number | null | undefined, unit = "") =>
  v == null
    ? "Not reported"
    : `${v.toLocaleString(undefined, { maximumFractionDigits: 2 })}${unit}`;

function Trend({
  series,
  metric,
  label,
  from,
  to,
}: {
  series: StudioMetricSeries[];
  metric: string;
  label: string;
  from: string;
  to: string;
}) {
  const items = series.filter((item) => item.metric === metric);
  const start = Date.parse(from),
    end = Date.parse(to);
  const samples = items
    .flatMap((item) => item.points)
    .filter(
      (p) =>
        p.value !== null &&
        Number.isFinite(p.value) &&
        p.value >= 0 &&
        Date.parse(p.timestamp) >= start &&
        Date.parse(p.timestamp) <= end,
    );
  if (!samples.length || start >= end)
    return <p>{label}: No samples reported.</p>;
  const max = Math.max(0.01, ...samples.map((p) => p.value!));
  return (
    <figure className="cockpit-trend">
      <figcaption>
        {label} · 0–{max.toFixed(2)}/s
      </figcaption>
      <svg
        role="img"
        aria-label={`${label} per second; gaps represent missing samples`}
        viewBox="0 0 320 90"
      >
        <line x1="8" y1="65" x2="312" y2="65" stroke="var(--studio-border)" />
        {items.map((item, index) => {
          let pen = false;
          let previousTime = 0;
          const path = item.points
            .map((p) => {
              const time = Date.parse(p.timestamp);
              if (
                p.value === null ||
                !Number.isFinite(p.value) ||
                p.value < 0 ||
                time < start ||
                time > end ||
                !Number.isFinite(time)
              ) {
                pen = false;
                return "";
              }
              if (time - previousTime > 45_000) pen = false;
              const d = `${pen ? "L" : "M"}${8 + ((time - start) / (end - start)) * 304},${65 - (p.value / max) * 52}`;
              pen = true;
              previousTime = time;
              return d;
            })
            .join(" ");
          return (
            <g key={index}>
              <path
                d={path}
                fill="none"
                stroke="var(--studio-secondary-strong)"
                strokeWidth="2"
              />
              {item.points
                .filter(
                  (p) =>
                    p.value !== null &&
                    Number.isFinite(p.value) &&
                    p.value >= 0 &&
                    Date.parse(p.timestamp) >= start &&
                    Date.parse(p.timestamp) <= end,
                )
                .map((p, i) => (
                  <circle
                    key={i}
                    cx={
                      8 +
                      ((Date.parse(p.timestamp) - start) / (end - start)) * 304
                    }
                    cy={65 - (p.value! / max) * 52}
                    r="1.5"
                    fill="var(--studio-secondary-strong)"
                  />
                ))}
            </g>
          );
        })}
        <text x="8" y="85" fill="currentColor" fontSize="10">
          {new Date(from).toLocaleTimeString()}
        </text>
        <text x="312" y="85" fill="currentColor" textAnchor="end" fontSize="10">
          {new Date(to).toLocaleTimeString()}
        </text>
      </svg>
    </figure>
  );
}

function ServiceEvidence({
  service,
  read,
  now,
}: {
  service: ServiceRecord;
  read: Read | undefined;
  now: number;
}) {
  const data = read?.data;
  const path = `/services/${encodeURIComponent(service.service_id)}`;
  const fresh =
    !!data && isFresh(data.reported_at, now, data.stale_after_seconds);
  return (
    <SectionCard
      title={service.name}
      subtitle={`${service.environment} · ${service.service_id}`}
      action={
        <Link to={`${path}?view=monitor`} className="studio-button-link">
          Investigate service
        </Link>
      }
    >
      {read?.error ? (
        <NoticeBanner tone="error">
          Cockpit unavailable: {read.error}
        </NoticeBanner>
      ) : !data ? (
        <p role="status">Reading queue and worker evidence…</p>
      ) : (
        <div className="studio-stack-md">
          {!fresh ? (
            <NoticeBanner>
              Displayed snapshot is stale. Refresh before using it to assess
              capacity.
            </NoticeBanner>
          ) : null}
          <div className="cockpit-sources">
            {Object.entries(data.sources).map(([name, source]) => (
              <div className="studio-subcard" key={name}>
                <strong>
                  {name === "pressure"
                    ? "Queue & pressure source"
                    : name === "workers"
                      ? "Worker source"
                      : "Metrics source"}
                </strong>
                <span className="cockpit-state">
                  {!fresh && source.state === "fresh"
                    ? "stale"
                    : source.state.replace(/_/g, " ")}
                </span>
                <p>{source.detail}</p>
                {source.reported_at ? (
                  <small>Reported {formatTimestamp(source.reported_at)}</small>
                ) : null}
              </div>
            ))}
          </div>
          <div className="cockpit-section-heading">
            <h3>Queues</h3>
            <Link to={`${path}/topology`}>View topology</Link>
          </div>
          <p className="cockpit-muted">
            Each row covers one configured queue and source. Missing fields are
            not zero; providers observing the same queue are never summed.
          </p>
          {!data.queues.length ? (
            <EmptyState
              title="No queue observations"
              body="This source has not exposed queue readings. Worker records alone do not establish queue consumer coverage."
            />
          ) : (
            <div className="cockpit-grid">
              {data.queues.map((queue) => {
                const current =
                  fresh &&
                  queue.freshness === "fresh" &&
                  isFresh(queue.observed_at, now, data.stale_after_seconds);
                return (
                  <article
                    key={`${queue.queue_name}:${queue.source}`}
                    className="studio-subcard cockpit-evidence-card"
                  >
                    <header>
                      <strong>{queue.queue_name}</strong>
                      <span className="cockpit-state">
                        {current ? "fresh" : "stale"}
                      </span>
                    </header>
                    <p>
                      {current
                        ? queue.diagnosis
                        : "Stale queue evidence; refresh before diagnosing capacity."}
                    </p>
                    <dl className="cockpit-values">
                      <div>
                        <dt>Ready</dt>
                        <dd>{value(queue.ready)}</dd>
                      </div>
                      <div>
                        <dt>Unacknowledged</dt>
                        <dd>{value(queue.unacknowledged)}</dd>
                      </div>
                      <div>
                        <dt>Consumers</dt>
                        <dd>{value(queue.consumers)}</dd>
                      </div>
                      <div>
                        <dt>Oldest message age</dt>
                        <dd>{duration(queue.oldest_message_age_seconds)}</dd>
                      </div>
                      <div>
                        <dt>Arrival</dt>
                        <dd>{value(queue.arrival_rate, "/s")}</dd>
                      </div>
                      <div>
                        <dt>Completion</dt>
                        <dd>{value(queue.completion_rate, "/s")}</dd>
                      </div>
                    </dl>
                    <div className="cockpit-drain">
                      <strong>
                        Drain estimate:{" "}
                        {!current || queue.drain.state === "unknown"
                          ? "Unknown"
                          : queue.drain.state === "not_draining"
                            ? "Not draining"
                            : `~${duration(queue.drain.seconds)}`}
                      </strong>
                      <p>
                        {current ? queue.drain.reason : "Observation is stale."}
                      </p>
                      {queue.rate_window_seconds ? (
                        <p>
                          Rate observation window: {queue.rate_window_seconds}s
                          · this queue/source only.
                        </p>
                      ) : (
                        <p>No compatible rate window reported.</p>
                      )}
                    </div>
                    <small>
                      Source: {queue.source} · observed{" "}
                      {formatTimestamp(queue.observed_at)}
                    </small>
                  </article>
                );
              })}
            </div>
          )}
          <div className="cockpit-section-heading">
            <h3>Workers & active leases</h3>
            <Link to={`${path}?view=monitor&layout=logs`}>Worker logs</Link>
          </div>
          <p className="cockpit-muted">
            Heartbeat freshness threshold: {data.worker_stale_after_seconds}s. A
            running worker can still have expired leases. Lease age alone does
            not prove a slow handler.
          </p>
          {!data.workers.length ? (
            <p>
              No worker records reported. Check the worker source state above;
              this does not establish that a queue has no consumers.
            </p>
          ) : (
            <div className="cockpit-grid">
              {data.workers.map((worker, index) => {
                const current =
                  fresh &&
                  data.sources.workers.state === "fresh" &&
                  worker.freshness === "fresh" &&
                  isFresh(
                    worker.last_heartbeat_at,
                    now,
                    data.worker_stale_after_seconds,
                  );
                return (
                  <article
                    key={`${worker.worker_name}:${index}`}
                    className="studio-subcard cockpit-evidence-card"
                  >
                    <header>
                      <strong>{worker.worker_name}</strong>
                      <span className="cockpit-state">
                        {!worker.running
                          ? "stopped"
                          : current
                            ? "live"
                            : "stale heartbeat"}
                      </span>
                    </header>
                    <p>
                      Heartbeat {formatTimestamp(worker.last_heartbeat_at)} ·{" "}
                      {worker.active_leases.length} reported active leases
                    </p>
                    {worker.active_leases.length ? (
                      worker.active_leases.map((lease, index) => {
                        const expired =
                          lease.expired || Date.parse(lease.expires_at) <= now;
                        return (
                          <div
                            className="cockpit-lease"
                            key={`${lease.lease_id}:${index}`}
                          >
                            <TaskRefLink
                              taskRef={{
                                service_id: service.service_id,
                                task_id: lease.task_id,
                              }}
                            />
                            <strong>
                              {expired
                                ? "Expired lease"
                                : fresh &&
                                    data.sources.workers.state === "fresh" &&
                                    isFresh(
                                      lease.heartbeat_at,
                                      now,
                                      data.worker_stale_after_seconds,
                                    )
                                  ? "Lease heartbeat fresh"
                                  : "Lease heartbeat stale"}
                            </strong>
                            <dl>
                              <div>
                                <dt>Owner</dt>
                                <dd>{lease.owner_id}</dd>
                              </div>
                              <div>
                                <dt>Lease</dt>
                                <dd>{lease.lease_id}</dd>
                              </div>
                              <div>
                                <dt>Acquired</dt>
                                <dd>{formatTimestamp(lease.acquired_at)}</dd>
                              </div>
                              <div>
                                <dt>Heartbeat</dt>
                                <dd>{formatTimestamp(lease.heartbeat_at)}</dd>
                              </div>
                              <div>
                                <dt>Expires</dt>
                                <dd>{formatTimestamp(lease.expires_at)}</dd>
                              </div>
                              <div>
                                <dt>Stage / attempt</dt>
                                <dd>
                                  {lease.stage || "Not reported"} /{" "}
                                  {lease.attempt}
                                </dd>
                              </div>
                            </dl>
                          </div>
                        );
                      })
                    ) : (
                      <p>No active leases reported by this worker.</p>
                    )}
                  </article>
                );
              })}
            </div>
          )}
          <div className="cockpit-section-heading">
            <h3>Retry, DLQ & handler evidence</h3>
            <Link
              to={`/failed-tasks?service_id=${encodeURIComponent(service.service_id)}`}
            >
              Failed tasks
            </Link>
            <Link to={`${path}/dlq`}>Inspect DLQ</Link>
          </div>
          {data.metrics ? (
            <>
              <p className="cockpit-muted">
                Service scope · {formatTimestamp(data.metrics.from)} –{" "}
                {formatTimestamp(data.metrics.to)}. Rates use 5-minute windows.
                Service rates are not queue arrivals or queue drain evidence.
              </p>
              <div className="cockpit-values">
                <div>
                  <span>Active tasks</span>
                  <strong>
                    {fresh
                      ? value(
                          latestMetric(
                            data.metrics.series,
                            "active_tasks",
                            now,
                          ),
                        )
                      : "Stale"}
                  </strong>
                </div>
                <div>
                  <span>Handler duration p95</span>
                  <strong>
                    {fresh
                      ? value(
                          latestMetric(
                            data.metrics.series,
                            "task_duration_p95",
                            now,
                          ),
                          "s",
                        )
                      : "Stale"}
                  </strong>
                </div>
              </div>
              <p className="cockpit-muted">
                Use handler p95 and task/lease timings to investigate slow
                handlers; no latency target is assumed.
              </p>
              <div className="cockpit-grid">
                <Trend
                  series={data.metrics.series}
                  metric="tasks_retried_rate"
                  label="Retry trend"
                  from={data.metrics.from}
                  to={data.metrics.to}
                />
                <Trend
                  series={data.metrics.series}
                  metric="tasks_dlq_rate"
                  label="DLQ trend"
                  from={data.metrics.from}
                  to={data.metrics.to}
                />
              </div>
              {data.metrics.warnings.map((warning, i) => (
                <NoticeBanner key={i}>{warning}</NoticeBanner>
              ))}
            </>
          ) : (
            <p>
              Runtime metrics are unavailable. Queue and lease evidence above
              remains independently scoped.
            </p>
          )}
          {data.signals.length ? (
            <details>
              <summary>Backpressure signals ({data.signals.length})</summary>
              <div className="studio-stack-sm">
                {data.signals.map((signal, i) => (
                  <div key={i} className="cockpit-signal">
                    <strong>
                      {signal.scope_id || signal.scope} · {signal.kind}
                    </strong>
                    <span>
                      {signal.severity} ·{" "}
                      {fresh &&
                      signal.freshness === "fresh" &&
                      isFresh(signal.observed_at, now, data.stale_after_seconds)
                        ? "fresh"
                        : "stale"}
                    </span>
                    <p>{signal.reason || "No reason reported."}</p>
                    {signal.recommended_action ? (
                      <p>{signal.recommended_action}</p>
                    ) : null}
                  </div>
                ))}
              </div>
            </details>
          ) : null}
          <small>
            Read {formatTimestamp(data.reported_at)} · coverage is limited to
            configured sources.
          </small>
        </div>
      )}
    </SectionCard>
  );
}

export function QueueWorkerPage() {
  const state = useStudioServices();
  const [params, setParams] = useSearchParams();
  const environment = params.get("environment") || "",
    selected = params.get("service_id") || "";
  const scoped = state.services.filter(
    (s) => !environment || s.environment === environment,
  );
  const services = scoped.filter((s) => !selected || s.service_id === selected);
  const ids = services
    .map((s) => s.service_id)
    .sort()
    .join("\0");
  const [reads, setReads] = useState<Record<string, Read>>({});
  const [refresh, setRefresh] = useState(0);
  const [loading, setLoading] = useState(false);
  const loadingRef = useRef(false);
  const previousScope = useRef("");
  const [now, setNow] = useState(Date.now());
  const scope = `${environment}:${selected}:${ids}`;
  const [loadedScope, setLoadedScope] = useState("");
  const visibleReads = useMemo(
    () => (loadedScope === scope ? reads : {}),
    [loadedScope, scope, reads],
  );
  useEffect(() => {
    const timer = window.setInterval(() => {
      setNow(Date.now());
      if (document.visibilityState === "visible" && !loadingRef.current)
        setRefresh((r) => r + 1);
    }, 30_000);
    return () => window.clearInterval(timer);
  }, []);
  useEffect(() => {
    let active = true;
    const serviceIds = ids ? ids.split("\0") : [];
    setLoading(true);
    loadingRef.current = true;
    // Keep the current scope visible during refresh; source failures replace it.
    if (previousScope.current !== scope) setReads({});
    previousScope.current = scope;
    setLoadedScope(scope);
    void (async () => {
      for (let offset = 0; offset < serviceIds.length && active; offset += 4) {
        await Promise.all(
          serviceIds.slice(offset, offset + 4).map(async (id) => {
            let read: Read;
            try {
              read = { data: await fetchCockpit(id) };
            } catch (error) {
              read = {
                error:
                  error instanceof Error
                    ? error.message
                    : "Service read failed.",
              };
            }
            if (active) setReads((current) => ({ ...current, [id]: read }));
          }),
        );
      }
      if (active) {
        loadingRef.current = false;
        setLoading(false);
        setNow(Date.now());
      }
    })();
    return () => {
      active = false;
      loadingRef.current = false;
    };
  }, [scope, ids, refresh]);
  const successful = Object.values(visibleReads).filter((r) => r.data).length;
  const failed = Object.values(visibleReads).filter((r) => r.error).length;
  return (
    <div className="studio-stack-lg cockpit-page">
      <section className="studio-operations-header">
        <div>
          <p className="studio-page-eyebrow">Capacity diagnosis</p>
          <h1>Queue & Worker Cockpit</h1>
          <p>
            Why is work waiting, and can capacity catch up? Read queue pressure,
            worker freshness, and active work together.
          </p>
        </div>
        <button
          style={secondaryButtonStyle}
          disabled={loading}
          onClick={() => setRefresh((r) => r + 1)}
        >
          {loading ? "Reading sources…" : "Refresh evidence"}
        </button>
      </section>
      {state.error ? (
        <NoticeBanner tone="error">
          Registry unavailable: {state.error} Coverage may be incomplete.
        </NoticeBanner>
      ) : null}
      <label className="cockpit-service-filter">
        Service
        <select
          aria-label="Cockpit service"
          value={selected}
          style={inputStyle}
          onChange={(e) => {
            const next = new URLSearchParams(params);
            if (e.target.value) next.set("service_id", e.target.value);
            else next.delete("service_id");
            setParams(next);
          }}
        >
          <option value="">All scoped services</option>
          {scoped.map((s) => (
            <option key={s.service_id} value={s.service_id}>
              {s.name} · {s.environment}
            </option>
          ))}
        </select>
      </label>
      <div className="studio-metrics-grid studio-metrics-grid--3">
        <MetricCard label="Scoped services" value={String(services.length)} />
        <MetricCard label="Snapshots read" value={String(successful)} />
        <MetricCard label="Service reads unavailable" value={String(failed)} />
      </div>
      <p className="cockpit-muted" role="status">
        {environment ? `Environment: ${environment}.` : "All environments."}{" "}
        Refreshes every 30 seconds while visible. A successful snapshot can
        still contain partial, unsupported or unavailable sources.
      </p>
      {!services.length && !state.loading ? (
        <EmptyState
          title="No services in this scope"
          body="Select a registered service or change the environment filter to inspect queue and worker evidence."
        />
      ) : null}
      {state.loading ? <p>Loading service registry…</p> : null}
      {services.map((service) => (
        <ServiceEvidence
          key={service.service_id}
          service={service}
          read={visibleReads[service.service_id]}
          now={now}
        />
      ))}
    </div>
  );
}

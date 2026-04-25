import { startTransition, useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";

import { fetchServiceEvents, fetchServiceLogs } from "../api";
import { useStudioServices } from "../services-context";
import {
  HealthBadge,
  InlineCodeBox,
  LogSourceBadge,
  LogMessage,
  MetadataRow,
  NoticeBanner,
  SectionCard,
  StudioIcon,
  StatusBadge,
  formatEventSummary,
  formatLogLevel,
  formatTimestamp,
  inputStyle,
  mergeControlPlaneEvent,
  mutedTextStyle,
  parseLimit,
  secondaryButtonStyle,
} from "../ui";
import type { ServiceEventSourceKind, StudioControlPlaneEvent, StudioEventListResponse, StudioLogListResponse } from "../types";

type TimeWindowMode = "auto" | "15m" | "1h" | "24h" | "manual";

function latestTimestamp(...values: Array<string | null | undefined>) {
  const candidates = values.filter((value): value is string => Boolean(value));
  if (!candidates.length) {
    return null;
  }
  return candidates.reduce((latest, value) => (new Date(value).getTime() > new Date(latest).getTime() ? value : latest));
}

function localDateTimeToIso(value: string) {
  if (!value.trim()) {
    return "";
  }
  const timestamp = new Date(value).getTime();
  if (Number.isNaN(timestamp)) {
    return "";
  }
  return new Date(timestamp).toISOString();
}

function isoToLocalDateTime(value: string) {
  if (!value.trim()) {
    return "";
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return "";
  }
  const offsetMs = date.getTimezoneOffset() * 60 * 1000;
  return new Date(date.getTime() - offsetMs).toISOString().slice(0, 16);
}

function resolveWindow(mode: TimeWindowMode, manualFrom: string, manualTo: string) {
  if (mode === "manual") {
    return {
      from: localDateTimeToIso(manualFrom),
      to: localDateTimeToIso(manualTo),
    };
  }
  if (mode === "auto") {
    return { from: "", to: "" };
  }

  const now = Date.now();
  const durationMs = mode === "15m" ? 15 * 60 * 1000 : mode === "1h" ? 60 * 60 * 1000 : 24 * 60 * 60 * 1000;
  return {
    from: new Date(now - durationMs).toISOString(),
    to: new Date(now).toISOString(),
  };
}

function describeWindow(mode: TimeWindowMode, from: string, to: string) {
  if (mode === "auto") {
    return "Auto window: unbounded to unbounded.";
  }
  if (mode === "manual") {
    return "Manual window is active. Use the local date and time fields; empty bounds stay unbounded.";
  }
  const label = mode === "15m" ? "15 minutes" : mode === "1h" ? "1 hour" : "24 hours";
  return `Quick window: last ${label} (${from ? new Date(from).toLocaleString() : "unbounded"} to ${
    to ? new Date(to).toLocaleString() : "unbounded"
  }).`;
}

export function ServiceDetailPage() {
  const navigate = useNavigate();
  const { serviceId = "" } = useParams();
  const servicesState = useStudioServices();
  const service = servicesState.servicesById.get(serviceId) || null;

  const [serviceEvents, setServiceEvents] = useState<StudioEventListResponse | null>(null);
  const [serviceEventsLoading, setServiceEventsLoading] = useState(false);
  const [serviceEventsError, setServiceEventsError] = useState<string | null>(null);
  const [serviceEventTaskFilter, setServiceEventTaskFilter] = useState("");
  const [serviceEventSourceFilter, setServiceEventSourceFilter] = useState<"" | ServiceEventSourceKind>("");
  const [serviceEventTypeFilter, setServiceEventTypeFilter] = useState("");
  const [serviceEventWindowMode, setServiceEventWindowMode] = useState<TimeWindowMode>("auto");
  const [serviceEventManualFrom, setServiceEventManualFrom] = useState("");
  const [serviceEventManualTo, setServiceEventManualTo] = useState("");

  const [serviceLogs, setServiceLogs] = useState<StudioLogListResponse | null>(null);
  const [serviceLogsLoading, setServiceLogsLoading] = useState(false);
  const [serviceLogsError, setServiceLogsError] = useState<string | null>(null);
  const [serviceLogQuery, setServiceLogQuery] = useState("");
  const [serviceLogLevel, setServiceLogLevel] = useState("");
  const [serviceLogSource, setServiceLogSource] = useState("");
  const [serviceLogLimit, setServiceLogLimit] = useState("20");
  const [serviceLogWindowMode, setServiceLogWindowMode] = useState<TimeWindowMode>("auto");
  const [serviceLogManualFrom, setServiceLogManualFrom] = useState("");
  const [serviceLogManualTo, setServiceLogManualTo] = useState("");
  const [refreshingService, setRefreshingService] = useState(false);

  useEffect(() => {
    if (!serviceId) {
      return;
    }
    setServiceEventWindowMode("auto");
    setServiceEventManualFrom("");
    setServiceEventManualTo("");
    void loadServiceEvents(serviceId);
  }, [serviceId]);

  const activeServiceEventWindow = resolveWindow(serviceEventWindowMode, serviceEventManualFrom, serviceEventManualTo);

  useEffect(() => {
    setServiceLogSource("");
    setServiceLogWindowMode("auto");
    setServiceLogManualFrom("");
    setServiceLogManualTo("");
    if (!service?.log_config) {
      setServiceLogs(null);
      setServiceLogsError(service ? "No log provider configured for this service." : null);
      return;
    }
    void loadServiceLogs();
  }, [service?.service_id, service?.log_config]);

  const activeServiceLogWindow = resolveWindow(serviceLogWindowMode, serviceLogManualFrom, serviceLogManualTo);

  useEffect(() => {
    if (typeof EventSource === "undefined" || !serviceId) {
      return;
    }
    const source = new EventSource(`/studio/services/${encodeURIComponent(serviceId)}/events/stream`);
    source.addEventListener("event", (message) => {
      try {
        const parsed = JSON.parse((message as MessageEvent<string>).data) as StudioControlPlaneEvent;
        startTransition(() => {
          setServiceEvents((current) => {
            const items = mergeControlPlaneEvent(current?.items || [], parsed);
            return { count: items.length, items, next_cursor: current?.next_cursor || null };
          });
        });
      } catch {
        return;
      }
    });
    return () => source.close();
  }, [serviceId]);

  async function loadServiceEvents(targetServiceId: string) {
    setServiceEventsLoading(true);
    setServiceEventsError(null);
    try {
      const payload = await fetchServiceEvents(targetServiceId, {
        limit: 20,
        from: activeServiceEventWindow.from,
        to: activeServiceEventWindow.to,
      });
      setServiceEvents(payload);
    } catch (fetchError) {
      setServiceEventsError(fetchError instanceof Error ? fetchError.message : "Unable to load service activity.");
    } finally {
      setServiceEventsLoading(false);
    }
  }

  async function loadServiceLogs() {
    if (!service) {
      return;
    }
    setServiceLogsLoading(true);
    setServiceLogsError(null);
    try {
      const payload = await fetchServiceLogs(service.service_id, {
        query: serviceLogQuery,
        level: serviceLogLevel,
        source: serviceLogSource,
        limit: parseLimit(serviceLogLimit, 20),
        from: activeServiceLogWindow.from,
        to: activeServiceLogWindow.to,
      });
      setServiceLogs(payload);
    } catch (fetchError) {
      setServiceLogsError(fetchError instanceof Error ? fetchError.message : "Unable to load service logs.");
    } finally {
      setServiceLogsLoading(false);
    }
  }

  async function handleRefreshService() {
    if (!service || refreshingService) {
      return;
    }
    setRefreshingService(true);
    try {
      await servicesState.refresh(service.service_id);
    } catch {
      // Shared services context populates the error banner for failed mutations.
    } finally {
      setRefreshingService(false);
    }
  }

  const filteredServiceEvents = (serviceEvents?.items || []).filter((item) => {
    if (serviceEventTaskFilter.trim() && !item.task_id.includes(serviceEventTaskFilter.trim())) {
      return false;
    }
    if (serviceEventSourceFilter && item.source_kind !== serviceEventSourceFilter) {
      return false;
    }
    if (serviceEventTypeFilter.trim() && !item.event_type.includes(serviceEventTypeFilter.trim())) {
      return false;
    }
    return true;
  });
  const serviceLogSourceOptions = Array.from(new Set((serviceLogs?.items || []).map((item) => item.source).filter(Boolean))).sort();

  if (servicesState.error) {
    return <NoticeBanner tone="error">{servicesState.error}</NoticeBanner>;
  }

  if (!service) {
    return <NoticeBanner tone="error">Service `{serviceId}` is not present in the Studio registry.</NoticeBanner>;
  }

  const health = service.health || null;
  const latestObservedAt = latestTimestamp(
    health?.observation_freshness.latest_status_event_at,
    health?.observation_freshness.latest_observation_event_at,
  );
  const workerHealthLabel =
    health?.worker_health.state === "unsupported"
      ? "unsupported by service"
      : health?.worker_health.state === "unknown"
        ? health.worker_health.detail || "unknown"
        : health?.worker_health.state || "unknown";

  return (
    <div className="studio-stack-lg">
      {servicesState.notice ? <NoticeBanner>{servicesState.notice}</NoticeBanner> : null}

      <SectionCard
        title="Service Detail"
        subtitle="Inspect stored registry metadata, navigate to routed control-plane screens, and run existing registry actions."
        action={
          <div style={{ display: "flex", gap: 8 }}>
            <StatusBadge status={service.status} />
            <HealthBadge status={health?.overall_status || "unknown"} />
          </div>
        }
      >
        <div className="studio-action-row">
          <Link to="/services" style={{ ...secondaryButtonStyle, textDecoration: "none" }}>
            <StudioIcon name="back" />
            Back to Services
          </Link>
          <Link to={`/services/${encodeURIComponent(service.service_id)}/topology`} style={{ ...secondaryButtonStyle, textDecoration: "none" }}>
            <StudioIcon name="topology" />
            Topology
          </Link>
          <Link to={`/services/${encodeURIComponent(service.service_id)}/dlq`} style={{ ...secondaryButtonStyle, textDecoration: "none" }}>
            <StudioIcon name="dlq" />
            DLQ Explorer
          </Link>
          <Link to={`/tasks/search?service_id=${encodeURIComponent(service.service_id)}`} style={{ ...secondaryButtonStyle, textDecoration: "none" }}>
            <StudioIcon name="tasks" />
            Task Search
          </Link>
          <button type="button" onClick={() => void handleRefreshService()} style={secondaryButtonStyle} disabled={refreshingService}>
            <StudioIcon name="refresh" />
            {refreshingService ? "Refreshing..." : "Refresh"}
          </button>
          <button type="button" onClick={() => void servicesState.runHealthCheck(service.service_id)} style={secondaryButtonStyle}>
            <StudioIcon name="health" />
            Run Health Check
          </button>
          <button type="button" onClick={() => void servicesState.updateStatus(service.service_id, "registered")} style={secondaryButtonStyle}>
            <StudioIcon name="enable" />
            Enable
          </button>
          <button type="button" onClick={() => void servicesState.updateStatus(service.service_id, "unavailable")} style={secondaryButtonStyle}>
            <StudioIcon name="unavailable" />
            Mark Unavailable
          </button>
          <button type="button" onClick={() => void servicesState.updateStatus(service.service_id, "disabled")} style={secondaryButtonStyle}>
            <StudioIcon name="disable" />
            Disable
          </button>
          <button
            type="button"
            onClick={async () => {
              await servicesState.remove(service.service_id);
              navigate("/services");
            }}
            style={secondaryButtonStyle}
          >
            <StudioIcon name="delete" />
            Delete
          </button>
        </div>

        <div className="studio-detail-grid">
          <dl style={{ margin: 0, display: "grid", gap: 10, fontSize: 13 }}>
            <MetadataRow label="Service id" value={service.service_id} />
            <MetadataRow label="Name" value={service.name} />
            <MetadataRow label="Environment" value={service.environment} />
            <MetadataRow label="Base URL" value={service.base_url} />
            <MetadataRow label="Auth mode" value={service.auth_mode} />
            <MetadataRow label="Tags" value={service.tags.length ? service.tags.join(", ") : "none"} />
            <MetadataRow label="Last refresh" value={formatTimestamp(service.last_seen_at)} />
            <MetadataRow label="Log provider" value={service.log_config?.provider || "none"} />
          </dl>

          <div className="studio-stack-sm">
            <div>
              <h3 style={{ margin: 0, marginBottom: 8 }}>Runtime Health</h3>
              <dl style={{ margin: 0, display: "grid", gap: 10, fontSize: 13 }}>
                <MetadataRow label="Overall" value={<HealthBadge status={health?.overall_status || "unknown"} />} />
                <MetadataRow label="Last checked" value={formatTimestamp(health?.last_checked_at)} />
                <MetadataRow label="Capability" value={health?.capability_status.state || "missing"} />
                <MetadataRow label="Capability refreshed" value={formatTimestamp(health?.capability_status.last_successful_at)} />
                <MetadataRow label="HTTP reachability" value={health?.http_status.state || "unknown"} />
                <MetadataRow label="Latest observed activity" value={formatTimestamp(latestObservedAt)} />
                <MetadataRow label="Observation freshness" value={health?.observation_freshness.state || "missing"} />
                <MetadataRow label="Worker heartbeat" value={workerHealthLabel} />
                <MetadataRow label="Worker reported at" value={formatTimestamp(health?.worker_health.reported_at)} />
              </dl>
              {health?.http_status.error_detail ? (
                <p style={{ ...mutedTextStyle, marginTop: 8 }}>Reachability detail: {health.http_status.error_detail}</p>
              ) : null}
              {health?.worker_health.detail && health.worker_health.state !== "unsupported" ? (
                <p style={{ ...mutedTextStyle, marginTop: 8 }}>Worker detail: {health.worker_health.detail}</p>
              ) : null}
            </div>
            <div>
              <h3 style={{ margin: 0, marginBottom: 8 }}>Stored Capability Document</h3>
              {service.capabilities ? (
                <InlineCodeBox value={JSON.stringify(service.capabilities, null, 2)} />
              ) : (
                <p style={mutedTextStyle}>No capability document stored yet.</p>
              )}
            </div>
            <div>
              <h3 style={{ margin: 0, marginBottom: 8 }}>Stored Log Config</h3>
              {service.log_config ? (
                <InlineCodeBox value={JSON.stringify(service.log_config, null, 2)} minHeight={160} />
              ) : (
                <p style={mutedTextStyle}>No log provider configured for this service.</p>
              )}
            </div>
          </div>
        </div>
      </SectionCard>

      <div className="studio-two-column">
        <SectionCard
          title="Recent Activity"
          subtitle="Service-scoped Studio-ingested events with live SSE updates."
          action={
            <button type="button" onClick={() => void loadServiceEvents(service.service_id)} style={secondaryButtonStyle}>
              <StudioIcon name="refresh" />
              Reload Activity
            </button>
          }
        >
          <div className="studio-form-grid studio-form-grid--triple">
            <input
              value={serviceEventTaskFilter}
              onChange={(event) => setServiceEventTaskFilter(event.target.value)}
              placeholder="Filter task id"
              style={inputStyle}
            />
            <select
              value={serviceEventSourceFilter}
              onChange={(event) => setServiceEventSourceFilter(event.target.value as "" | ServiceEventSourceKind)}
              style={inputStyle}
            >
              <option value="">All sources</option>
              <option value="status">status</option>
              <option value="observation">observation</option>
            </select>
            <input
              value={serviceEventTypeFilter}
              onChange={(event) => setServiceEventTypeFilter(event.target.value)}
              placeholder="Filter event type"
              style={inputStyle}
            />
          </div>
          <div className="studio-log-filter-grid studio-log-window-grid" style={{ marginTop: 12 }}>
            <label className="studio-filter-field">
              <span>Activity Window</span>
              <select
                aria-label="Service event window mode"
                value={serviceEventWindowMode}
                onChange={(event) => {
                  const nextMode = event.target.value as TimeWindowMode;
                  if (nextMode === "manual") {
                    setServiceEventManualFrom(isoToLocalDateTime(activeServiceEventWindow.from));
                    setServiceEventManualTo(isoToLocalDateTime(activeServiceEventWindow.to));
                  }
                  setServiceEventWindowMode(nextMode);
                }}
                style={inputStyle}
              >
                <option value="auto">Auto window</option>
                <option value="15m">Last 15 minutes</option>
                <option value="1h">Last hour</option>
                <option value="24h">Last 24 hours</option>
                <option value="manual">Custom range</option>
              </select>
            </label>
            <label className="studio-filter-field">
              <span>From</span>
              <input
                aria-label="Service event from"
                type="datetime-local"
                value={
                  serviceEventWindowMode === "manual"
                    ? serviceEventManualFrom
                    : isoToLocalDateTime(activeServiceEventWindow.from)
                }
                onChange={(event) => setServiceEventManualFrom(event.target.value)}
                disabled={serviceEventWindowMode !== "manual"}
                style={inputStyle}
              />
            </label>
            <label className="studio-filter-field">
              <span>To</span>
              <input
                aria-label="Service event to"
                type="datetime-local"
                value={
                  serviceEventWindowMode === "manual"
                    ? serviceEventManualTo
                    : isoToLocalDateTime(activeServiceEventWindow.to)
                }
                onChange={(event) => setServiceEventManualTo(event.target.value)}
                disabled={serviceEventWindowMode !== "manual"}
                style={inputStyle}
              />
            </label>
          </div>
          <p style={mutedTextStyle}>
            {describeWindow(serviceEventWindowMode, activeServiceEventWindow.from, activeServiceEventWindow.to)}
          </p>
          {serviceEventsLoading ? <p style={mutedTextStyle}>Loading service activity...</p> : null}
          {serviceEventsError ? <p style={{ ...mutedTextStyle, color: "var(--studio-danger)" }}>{serviceEventsError}</p> : null}
          {!serviceEventsLoading && !serviceEventsError && !filteredServiceEvents.length ? (
            <p style={mutedTextStyle}>No Studio-ingested events for this service yet.</p>
          ) : null}
          {filteredServiceEvents.length ? (
            <div className="studio-stack-sm studio-surface-scroll">
              {filteredServiceEvents.map((item) => (
                <article
                  key={item.dedupe_key}
                  className="studio-subcard"
                  style={{ borderRadius: 14, padding: 12 }}
                >
                  <div className="studio-list-card__top">
                    <div style={{ display: "grid", gap: 4 }}>
                      <strong style={{ fontSize: 13 }}>{formatEventSummary(item)}</strong>
                      <span className="studio-inline-meta">
                        <Link
                          to={`/tasks/${encodeURIComponent(item.service_id)}/${encodeURIComponent(item.task_id)}`}
                          style={{ color: "inherit" }}
                        >
                          {item.task_id}
                        </Link>
                        {" · "}
                        {item.source_kind}
                        {" · "}
                        {item.component || "unknown"}
                      </span>
                    </div>
                    <span className="studio-inline-meta">{formatTimestamp(item.timestamp || item.ingested_at)}</span>
                  </div>
                  <p style={{ ...mutedTextStyle, marginTop: 8 }}>
                    {item.event_type}
                    {item.out_of_order ? " · out-of-order" : ""}
                  </p>
                </article>
              ))}
            </div>
          ) : null}
        </SectionCard>

        <SectionCard
          title="Service Logs"
          subtitle="Service-scoped log queries remain separate from Relayna status and observations."
          action={
            <button type="button" onClick={() => void loadServiceLogs()} style={secondaryButtonStyle}>
              <StudioIcon name="refresh" />
              Reload Logs
            </button>
          }
        >
          <div className="studio-log-filter-grid">
            <label className="studio-filter-field">
              <span>Text</span>
              <input
                aria-label="Service log text filter"
                value={serviceLogQuery}
                onChange={(event) => setServiceLogQuery(event.target.value)}
                placeholder="Search log text"
                style={inputStyle}
              />
            </label>
            <label className="studio-filter-field">
              <span>Level</span>
              <input
                aria-label="Service log level"
                value={serviceLogLevel}
                onChange={(event) => setServiceLogLevel(event.target.value)}
                placeholder="info, error"
                style={inputStyle}
              />
            </label>
            <label className="studio-filter-field">
              <span>Source</span>
              <input
                aria-label="Service log source"
                value={serviceLogSource}
                onChange={(event) => setServiceLogSource(event.target.value)}
                list={`service-log-sources-${service.service_id}`}
                placeholder={service.log_config?.source_label || "source"}
                disabled={!service.log_config?.source_label}
                style={inputStyle}
              />
            </label>
            <label className="studio-filter-field">
              <span>Limit</span>
              <input
                aria-label="Service log limit"
                value={serviceLogLimit}
                onChange={(event) => setServiceLogLimit(event.target.value)}
                placeholder="20"
                inputMode="numeric"
                style={inputStyle}
              />
            </label>
          </div>
          <div className="studio-log-filter-grid studio-log-window-grid" style={{ marginTop: 12 }}>
            <label className="studio-filter-field">
              <span>Log Window</span>
              <select
                aria-label="Service log window mode"
                value={serviceLogWindowMode}
                onChange={(event) => {
                  const nextMode = event.target.value as TimeWindowMode;
                  if (nextMode === "manual") {
                    setServiceLogManualFrom(isoToLocalDateTime(activeServiceLogWindow.from));
                    setServiceLogManualTo(isoToLocalDateTime(activeServiceLogWindow.to));
                  }
                  setServiceLogWindowMode(nextMode);
                }}
                style={inputStyle}
              >
                <option value="auto">Auto window</option>
                <option value="15m">Last 15 minutes</option>
                <option value="1h">Last hour</option>
                <option value="24h">Last 24 hours</option>
                <option value="manual">Custom range</option>
              </select>
            </label>
            <label className="studio-filter-field">
              <span>From</span>
              <input
                aria-label="Service log from"
                type="datetime-local"
                value={
                  serviceLogWindowMode === "manual"
                    ? serviceLogManualFrom
                    : isoToLocalDateTime(activeServiceLogWindow.from)
                }
                onChange={(event) => setServiceLogManualFrom(event.target.value)}
                disabled={serviceLogWindowMode !== "manual"}
                style={inputStyle}
              />
            </label>
            <label className="studio-filter-field">
              <span>To</span>
              <input
                aria-label="Service log to"
                type="datetime-local"
                value={
                  serviceLogWindowMode === "manual"
                    ? serviceLogManualTo
                    : isoToLocalDateTime(activeServiceLogWindow.to)
                }
                onChange={(event) => setServiceLogManualTo(event.target.value)}
                disabled={serviceLogWindowMode !== "manual"}
                style={inputStyle}
              />
            </label>
          </div>
          <p style={mutedTextStyle}>
            {describeWindow(serviceLogWindowMode, activeServiceLogWindow.from, activeServiceLogWindow.to)}
          </p>
          {serviceLogSourceOptions.length ? (
            <datalist id={`service-log-sources-${service.service_id}`}>
              {serviceLogSourceOptions.map((source) => (
                <option key={source} value={source} />
              ))}
            </datalist>
          ) : null}
          {!service.log_config?.source_label ? (
            <p style={mutedTextStyle}>Source filtering is unavailable until this service sets `log_config.source_label`.</p>
          ) : (
            <p style={mutedTextStyle}>
              Source filter matches the configured `{service.log_config.source_label}` Loki label exactly.
              {serviceLogSourceOptions.length ? ` Discovered values: ${serviceLogSourceOptions.join(", ")}.` : ""}
            </p>
          )}
          {serviceLogsLoading ? <p style={mutedTextStyle}>Loading service logs...</p> : null}
          {serviceLogsError ? <p style={{ ...mutedTextStyle, color: "var(--studio-danger)" }}>{serviceLogsError}</p> : null}
          {!serviceLogsLoading && !serviceLogsError && !(serviceLogs?.items.length || 0) ? (
            <p style={mutedTextStyle}>No service logs matched the current filters.</p>
          ) : null}
          {serviceLogs?.items.length ? (
            <div className="studio-stack-sm studio-surface-scroll">
              {serviceLogs.items.map((item, index) => (
                <article
                  key={`${item.timestamp}-${item.message}-${index}`}
                  className="studio-subcard"
                  style={{ borderRadius: 14, padding: 12 }}
                >
                  <div className="studio-list-card__top">
                    <div style={{ display: "grid", gap: 4 }}>
                      <div style={{ display: "flex", flexWrap: "wrap", alignItems: "center", gap: 8 }}>
                        <LogSourceBadge source={item.source} />
                        <span className="studio-inline-meta">
                          {formatLogLevel(item.level)} · {item.task_id || "service scope"}
                        </span>
                      </div>
                    </div>
                    <span className="studio-inline-meta">{formatTimestamp(item.timestamp)}</span>
                  </div>
                  <LogMessage message={item.message} />
                </article>
              ))}
            </div>
          ) : null}
        </SectionCard>
      </div>
    </div>
  );
}

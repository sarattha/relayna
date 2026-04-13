import { startTransition, useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";

import { fetchServiceEvents, fetchServiceLogs } from "../api";
import { useStudioServices } from "../services-context";
import {
  HealthBadge,
  InlineCodeBox,
  MetadataRow,
  NoticeBanner,
  SectionCard,
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

function latestTimestamp(...values: Array<string | null | undefined>) {
  const candidates = values.filter((value): value is string => Boolean(value));
  if (!candidates.length) {
    return null;
  }
  return candidates.reduce((latest, value) => (new Date(value).getTime() > new Date(latest).getTime() ? value : latest));
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

  const [serviceLogs, setServiceLogs] = useState<StudioLogListResponse | null>(null);
  const [serviceLogsLoading, setServiceLogsLoading] = useState(false);
  const [serviceLogsError, setServiceLogsError] = useState<string | null>(null);
  const [serviceLogQuery, setServiceLogQuery] = useState("");
  const [serviceLogLevel, setServiceLogLevel] = useState("");
  const [serviceLogLimit, setServiceLogLimit] = useState("20");

  useEffect(() => {
    if (!serviceId) {
      return;
    }
    void loadServiceEvents(serviceId);
  }, [serviceId]);

  useEffect(() => {
    if (!service?.log_config) {
      setServiceLogs(null);
      setServiceLogsError(service ? "No log provider configured for this service." : null);
      return;
    }
    void loadServiceLogs();
  }, [service?.service_id, service?.log_config]);

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
      const payload = await fetchServiceEvents(targetServiceId);
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
        limit: parseLimit(serviceLogLimit, 20),
      });
      setServiceLogs(payload);
    } catch (fetchError) {
      setServiceLogsError(fetchError instanceof Error ? fetchError.message : "Unable to load service logs.");
    } finally {
      setServiceLogsLoading(false);
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
            Back to Services
          </Link>
          <Link to={`/services/${encodeURIComponent(service.service_id)}/topology`} style={{ ...secondaryButtonStyle, textDecoration: "none" }}>
            Topology
          </Link>
          <Link to={`/services/${encodeURIComponent(service.service_id)}/dlq`} style={{ ...secondaryButtonStyle, textDecoration: "none" }}>
            DLQ Explorer
          </Link>
          <Link to={`/tasks/search?service_id=${encodeURIComponent(service.service_id)}`} style={{ ...secondaryButtonStyle, textDecoration: "none" }}>
            Task Search
          </Link>
          <button type="button" onClick={() => void servicesState.runHealthCheck(service.service_id)} style={secondaryButtonStyle}>
            Run Health Check
          </button>
          <button type="button" onClick={() => void servicesState.updateStatus(service.service_id, "registered")} style={secondaryButtonStyle}>
            Enable
          </button>
          <button type="button" onClick={() => void servicesState.updateStatus(service.service_id, "unavailable")} style={secondaryButtonStyle}>
            Mark Unavailable
          </button>
          <button type="button" onClick={() => void servicesState.updateStatus(service.service_id, "disabled")} style={secondaryButtonStyle}>
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
              Reload Logs
            </button>
          }
        >
          <div className="studio-log-filter-grid">
            <input
              aria-label="Service log text filter"
              value={serviceLogQuery}
              onChange={(event) => setServiceLogQuery(event.target.value)}
              placeholder="Search log text"
              style={inputStyle}
            />
            <input
              aria-label="Service log level"
              value={serviceLogLevel}
              onChange={(event) => setServiceLogLevel(event.target.value)}
              placeholder="level"
              style={inputStyle}
            />
            <input
              aria-label="Service log limit"
              value={serviceLogLimit}
              onChange={(event) => setServiceLogLimit(event.target.value)}
              placeholder="20"
              style={inputStyle}
            />
          </div>
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
                      <strong style={{ fontSize: 13 }}>{item.message}</strong>
                      <span className="studio-inline-meta">
                        {formatLogLevel(item.level)} · {item.task_id || "service scope"}
                      </span>
                    </div>
                    <span className="studio-inline-meta">{formatTimestamp(item.timestamp)}</span>
                  </div>
                </article>
              ))}
            </div>
          ) : null}
        </SectionCard>
      </div>
    </div>
  );
}

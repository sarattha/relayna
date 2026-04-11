import { startTransition, useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";

import { fetchServiceEvents, fetchServiceLogs } from "../api";
import { useStudioServices } from "../services-context";
import {
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

  return (
    <div style={{ display: "grid", gap: 20 }}>
      {servicesState.notice ? <NoticeBanner>{servicesState.notice}</NoticeBanner> : null}

      <SectionCard
        title="Service Detail"
        subtitle="Inspect stored registry metadata, navigate to routed control-plane screens, and run existing registry actions."
        action={<StatusBadge status={service.status} />}
      >
        <div style={{ display: "flex", flexWrap: "wrap", gap: 10 }}>
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
          <button type="button" onClick={() => void servicesState.refresh(service.service_id)} style={secondaryButtonStyle}>
            Refresh Capabilities
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

        <div style={{ display: "grid", gap: 20, gridTemplateColumns: "minmax(0, 1fr) minmax(320px, 1fr)" }}>
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

          <div style={{ display: "grid", gap: 12 }}>
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

      <div style={{ display: "grid", gap: 20, gridTemplateColumns: "minmax(0, 1fr) minmax(0, 1fr)" }}>
        <SectionCard
          title="Recent Activity"
          subtitle="Service-scoped Studio-ingested events with live SSE updates."
          action={
            <button type="button" onClick={() => void loadServiceEvents(service.service_id)} style={secondaryButtonStyle}>
              Reload Activity
            </button>
          }
        >
          <div style={{ display: "grid", gap: 10, gridTemplateColumns: "repeat(3, minmax(0, 1fr))" }}>
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
          {serviceEventsError ? <p style={{ ...mutedTextStyle, color: "#7a2424" }}>{serviceEventsError}</p> : null}
          {!serviceEventsLoading && !serviceEventsError && !filteredServiceEvents.length ? (
            <p style={mutedTextStyle}>No Studio-ingested events for this service yet.</p>
          ) : null}
          {filteredServiceEvents.length ? (
            <div style={{ display: "grid", gap: 10, maxHeight: 420, overflowY: "auto" }}>
              {filteredServiceEvents.map((item) => (
                <article
                  key={item.dedupe_key}
                  style={{ border: "1px solid rgba(99, 83, 57, 0.14)", borderRadius: 14, padding: 12 }}
                >
                  <div style={{ display: "flex", justifyContent: "space-between", gap: 12, alignItems: "start" }}>
                    <div style={{ display: "grid", gap: 4 }}>
                      <strong style={{ fontSize: 13 }}>{formatEventSummary(item)}</strong>
                      <span style={{ fontSize: 12, color: "#62584b" }}>
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
                    <span style={{ fontSize: 12, color: "#62584b", textAlign: "right" }}>
                      {formatTimestamp(item.timestamp || item.ingested_at)}
                    </span>
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
          <div style={{ display: "grid", gap: 10, gridTemplateColumns: "minmax(0, 1.4fr) 140px 110px" }}>
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
          {serviceLogsError ? <p style={{ ...mutedTextStyle, color: "#7a2424" }}>{serviceLogsError}</p> : null}
          {!serviceLogsLoading && !serviceLogsError && !(serviceLogs?.items.length || 0) ? (
            <p style={mutedTextStyle}>No service logs matched the current filters.</p>
          ) : null}
          {serviceLogs?.items.length ? (
            <div style={{ display: "grid", gap: 10, maxHeight: 420, overflowY: "auto" }}>
              {serviceLogs.items.map((item, index) => (
                <article
                  key={`${item.timestamp}-${item.message}-${index}`}
                  style={{ border: "1px solid rgba(99, 83, 57, 0.14)", borderRadius: 14, padding: 12 }}
                >
                  <div style={{ display: "flex", justifyContent: "space-between", gap: 12, alignItems: "start" }}>
                    <div style={{ display: "grid", gap: 4 }}>
                      <strong style={{ fontSize: 13 }}>{item.message}</strong>
                      <span style={{ fontSize: 12, color: "#62584b" }}>
                        {formatLogLevel(item.level)} · {item.task_id || "service scope"}
                      </span>
                    </div>
                    <span style={{ fontSize: 12, color: "#62584b", textAlign: "right" }}>
                      {formatTimestamp(item.timestamp)}
                    </span>
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

import { useEffect, useState } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";

import { fetchBrokerDlq, fetchDlq } from "../api";
import { useStudioServices } from "../services-context";
import {
  NoticeBanner,
  SectionCard,
  StudioIcon,
  TaskRefLink,
  formatTimestamp,
  inputStyle,
  mutedTextStyle,
  parseLimit,
  primaryButtonStyle,
  secondaryButtonStyle,
  supportsCapability,
} from "../ui";
import type { BrokerDlqMessageListResponse, DlqMessageListResponse, DlqMode, DlqQueryState } from "../types";

const emptyState: DlqQueryState = {
  queue_name: "",
  task_id: "",
  reason: "",
  source_queue_name: "",
  state: "",
  limit: "50",
  cursor: null,
};

export function DlqPage() {
  const { serviceId = "" } = useParams();
  const [searchParams, setSearchParams] = useSearchParams();
  const servicesState = useStudioServices();
  const service = servicesState.servicesById.get(serviceId) || null;

  const [mode, setMode] = useState<DlqMode>("indexed");
  const [query, setQuery] = useState<DlqQueryState>(emptyState);
  const [indexedPayload, setIndexedPayload] = useState<DlqMessageListResponse | null>(null);
  const [brokerPayload, setBrokerPayload] = useState<BrokerDlqMessageListResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const brokerSupported = supportsCapability(service?.capabilities || null, "broker.dlq.messages");

  useEffect(() => {
    if (!serviceId) {
      return;
    }
    const nextMode = parseDlqMode(searchParams.get("mode"));
    const nextQuery = buildQueryState(searchParams);
    setMode(nextMode);
    setQuery(nextQuery);
    void load(nextQuery, nextMode, false);
  }, [serviceId]);

  function syncSearch(nextQuery: DlqQueryState, nextMode: DlqMode) {
    const params = new URLSearchParams();
    if (nextMode === "broker") {
      params.set("mode", "broker");
    }
    if (nextQuery.queue_name.trim()) {
      params.set("queue_name", nextQuery.queue_name.trim());
    }
    if (nextQuery.task_id.trim()) {
      params.set("task_id", nextQuery.task_id.trim());
    }
    params.set("limit", nextQuery.limit || "50");
    if (nextMode === "indexed") {
      if (nextQuery.reason.trim()) {
        params.set("reason", nextQuery.reason.trim());
      }
      if (nextQuery.source_queue_name.trim()) {
        params.set("source_queue_name", nextQuery.source_queue_name.trim());
      }
      if (nextQuery.state.trim()) {
        params.set("state", nextQuery.state.trim());
      }
      if (nextQuery.cursor?.trim()) {
        params.set("cursor", nextQuery.cursor.trim());
      }
    }
    setSearchParams(params, { replace: true });
  }

  async function load(nextQuery: DlqQueryState, nextMode = mode, syncUrl = true) {
    setLoading(true);
    setError(null);
    try {
      const sanitized = { ...nextQuery, limit: String(parseLimit(nextQuery.limit, 50)) };
      if (nextMode === "broker") {
        const nextPayload = await fetchBrokerDlq(serviceId, sanitized);
        setBrokerPayload(nextPayload);
        setIndexedPayload(null);
      } else {
        const nextPayload = await fetchDlq(serviceId, sanitized);
        setIndexedPayload(nextPayload);
        setBrokerPayload(null);
      }
      setMode(nextMode);
      setQuery(sanitized);
      if (syncUrl) {
        syncSearch(sanitized, nextMode);
      }
    } catch (fetchError) {
      setError(fetchError instanceof Error ? fetchError.message : "Unable to load DLQ messages.");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="studio-stack-lg">
      {error ? <NoticeBanner tone="error">{error}</NoticeBanner> : null}

      <SectionCard
        title="DLQ Explorer"
        subtitle="Service-scoped DLQ explorer with explicit indexed and broker-backed inspection modes."
        action={
          <Link to={`/services/${encodeURIComponent(serviceId)}`} style={{ ...secondaryButtonStyle, textDecoration: "none" }}>
            <StudioIcon name="back" />
            Back to Service
          </Link>
        }
      >
        {service ? (
          <p style={mutedTextStyle}>
            Inspecting DLQ messages for <strong>{service.name}</strong> ({service.service_id}).
          </p>
        ) : null}
        <div className="studio-action-row">
          <button
            type="button"
            style={mode === "indexed" ? primaryButtonStyle : secondaryButtonStyle}
            onClick={() => void load({ ...query, cursor: null }, "indexed")}
          >
            <StudioIcon name="filter" />
            Indexed Mode
          </button>
          <button
            type="button"
            style={mode === "broker" ? primaryButtonStyle : secondaryButtonStyle}
            onClick={() => void load({ ...query, cursor: null }, "broker")}
            disabled={!brokerSupported}
          >
            <StudioIcon name="dlq" />
            Broker Mode
          </button>
        </div>
        {mode === "broker" ? (
          <NoticeBanner>
            Live broker inspection mode is active. These results come directly from broker DLQ queues, not indexed DLQ
            records, so `dlq_id`, replay state, retention, and pagination do not apply.
          </NoticeBanner>
        ) : null}
        {mode === "broker" && !brokerSupported ? (
          <NoticeBanner tone="error">This service does not advertise broker-backed DLQ inspection.</NoticeBanner>
        ) : null}

        <form
          onSubmit={(event) => {
            event.preventDefault();
            void load({ ...query, cursor: null }, mode);
          }}
          className="studio-form-grid studio-form-grid--triple"
        >
          <input
            value={query.queue_name}
            onChange={(event) => setQuery((current) => ({ ...current, queue_name: event.target.value }))}
            placeholder="Queue name"
            style={inputStyle}
          />
          <input
            value={query.task_id}
            onChange={(event) => setQuery((current) => ({ ...current, task_id: event.target.value }))}
            placeholder="Task id"
            style={inputStyle}
          />
          <input
            value={query.reason}
            onChange={(event) => setQuery((current) => ({ ...current, reason: event.target.value }))}
            placeholder="Reason"
            style={inputStyle}
            disabled={mode === "broker"}
          />
          <input
            value={query.source_queue_name}
            onChange={(event) => setQuery((current) => ({ ...current, source_queue_name: event.target.value }))}
            placeholder="Source queue"
            style={inputStyle}
            disabled={mode === "broker"}
          />
          <input
            value={query.state}
            onChange={(event) => setQuery((current) => ({ ...current, state: event.target.value }))}
            placeholder="State"
            style={inputStyle}
            disabled={mode === "broker"}
          />
          <input
            value={query.limit}
            onChange={(event) => setQuery((current) => ({ ...current, limit: event.target.value }))}
            placeholder="50"
            style={inputStyle}
          />
          <button type="submit" style={primaryButtonStyle}>
            <StudioIcon name="filter" />
            Apply Filters
          </button>
        </form>

        {loading ? <p style={mutedTextStyle}>Loading DLQ messages...</p> : null}
        {!loading && mode === "indexed" && !(indexedPayload?.items.length || 0) ? (
          <p style={mutedTextStyle}>No DLQ messages matched the current filters.</p>
        ) : null}
        {!loading && mode === "broker" && !(brokerPayload?.items.length || 0) ? (
          <p style={mutedTextStyle}>No live broker DLQ messages matched the current filters.</p>
        ) : null}
        {mode === "indexed" && indexedPayload?.items.length ? (
          <div className="studio-stack-sm">
            {indexedPayload.items.map((item) => (
              <article
                key={item.dlq_id}
                className="studio-subcard"
                style={{ borderRadius: 14, padding: 14, display: "grid", gap: 8 }}
              >
                <div className="studio-list-card__top">
                  <div style={{ display: "grid", gap: 4 }}>
                    <strong style={{ fontSize: 13 }}>{item.reason}</strong>
                    <span className="studio-inline-meta">
                      {item.queue_name} · {item.source_queue_name} · {item.state}
                    </span>
                  </div>
                  <span className="studio-inline-meta">{formatTimestamp(item.dead_lettered_at)}</span>
                </div>
                <div style={{ display: "grid", gap: 4, fontSize: 13 }}>
                  <span>DLQ id: {item.dlq_id}</span>
                  <span>Retry: {item.retry_attempt} / {item.max_retries}</span>
                  <span>Correlation: {item.correlation_id || "none"}</span>
                  <span>
                    Task:{" "}
                    {item.task_ref ? <TaskRefLink taskRef={item.task_ref} /> : item.task_id || "unattributed"}
                  </span>
                </div>
              </article>
            ))}
            {indexedPayload.next_cursor ? (
              <button
                type="button"
                style={secondaryButtonStyle}
                onClick={() => void load({ ...query, cursor: indexedPayload.next_cursor || null }, "indexed")}
              >
                <StudioIcon name="next" />
                Load Next Page
              </button>
            ) : null}
          </div>
        ) : null}
        {mode === "broker" && brokerPayload?.items.length ? (
          <div className="studio-stack-sm">
            {brokerPayload.items.map((item) => (
              <article
                key={item.message_key}
                className="studio-subcard"
                style={{ borderRadius: 14, padding: 14, display: "grid", gap: 8 }}
              >
                <div className="studio-list-card__top">
                  <div style={{ display: "grid", gap: 4 }}>
                    <strong style={{ fontSize: 13 }}>{item.reason || "broker_dead_letter"}</strong>
                    <span className="studio-inline-meta">
                      {item.queue_name} · {item.source_queue_name || "source unknown"} · {item.body_encoding}
                    </span>
                  </div>
                  <span className="studio-inline-meta">{formatTimestamp(item.dead_lettered_at || null)}</span>
                </div>
                <div style={{ display: "grid", gap: 4, fontSize: 13 }}>
                  <span>Message key: {item.message_key}</span>
                  <span>Redelivered: {item.redelivered == null ? "unknown" : String(item.redelivered)}</span>
                  <span>Correlation: {item.correlation_id || "none"}</span>
                  <span>
                    Task: {item.task_ref ? <TaskRefLink taskRef={item.task_ref} /> : item.task_id || "unattributed"}
                  </span>
                </div>
              </article>
            ))}
          </div>
        ) : null}
      </SectionCard>
    </div>
  );
}

function parseDlqMode(value: string | null): DlqMode {
  return value === "broker" ? "broker" : "indexed";
}

function buildQueryState(searchParams: URLSearchParams): DlqQueryState {
  return {
    queue_name: searchParams.get("queue_name") || "",
    task_id: searchParams.get("task_id") || "",
    reason: searchParams.get("reason") || "",
    source_queue_name: searchParams.get("source_queue_name") || "",
    state: searchParams.get("state") || "",
    limit: searchParams.get("limit") || "50",
    cursor: searchParams.get("cursor"),
  };
}

import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";

import { fetchDlq } from "../api";
import { useStudioServices } from "../services-context";
import {
  NoticeBanner,
  SectionCard,
  TaskRefLink,
  formatTimestamp,
  inputStyle,
  mutedTextStyle,
  parseLimit,
  primaryButtonStyle,
  secondaryButtonStyle,
} from "../ui";
import type { DlqMessageListResponse, DlqQueryState } from "../types";

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
  const servicesState = useStudioServices();
  const service = servicesState.servicesById.get(serviceId) || null;

  const [query, setQuery] = useState<DlqQueryState>(emptyState);
  const [payload, setPayload] = useState<DlqMessageListResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!serviceId) {
      return;
    }
    void load(emptyState);
  }, [serviceId]);

  async function load(nextQuery: DlqQueryState) {
    setLoading(true);
    setError(null);
    try {
      const sanitized = { ...nextQuery, limit: String(parseLimit(nextQuery.limit, 50)) };
      const nextPayload = await fetchDlq(serviceId, sanitized);
      setPayload(nextPayload);
      setQuery(sanitized);
    } catch (fetchError) {
      setError(fetchError instanceof Error ? fetchError.message : "Unable to load DLQ messages.");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div style={{ display: "grid", gap: 20 }}>
      {error ? <NoticeBanner tone="error">{error}</NoticeBanner> : null}

      <SectionCard
        title="DLQ Explorer"
        subtitle="First-class service-scoped dead-letter queue view using `/studio/services/:serviceId/dlq/messages`."
        action={
          <Link to={`/services/${encodeURIComponent(serviceId)}`} style={{ ...secondaryButtonStyle, textDecoration: "none" }}>
            Back to Service
          </Link>
        }
      >
        {service ? (
          <p style={mutedTextStyle}>
            Inspecting DLQ messages for <strong>{service.name}</strong> ({service.service_id}).
          </p>
        ) : null}

        <form
          onSubmit={(event) => {
            event.preventDefault();
            void load({ ...query, cursor: null });
          }}
          style={{ display: "grid", gap: 10, gridTemplateColumns: "repeat(3, minmax(0, 1fr))" }}
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
          />
          <input
            value={query.source_queue_name}
            onChange={(event) => setQuery((current) => ({ ...current, source_queue_name: event.target.value }))}
            placeholder="Source queue"
            style={inputStyle}
          />
          <input
            value={query.state}
            onChange={(event) => setQuery((current) => ({ ...current, state: event.target.value }))}
            placeholder="State"
            style={inputStyle}
          />
          <input
            value={query.limit}
            onChange={(event) => setQuery((current) => ({ ...current, limit: event.target.value }))}
            placeholder="50"
            style={inputStyle}
          />
          <button type="submit" style={primaryButtonStyle}>
            Apply Filters
          </button>
        </form>

        {loading ? <p style={mutedTextStyle}>Loading DLQ messages...</p> : null}
        {!loading && !(payload?.items.length || 0) ? (
          <p style={mutedTextStyle}>No DLQ messages matched the current filters.</p>
        ) : null}
        {payload?.items.length ? (
          <div style={{ display: "grid", gap: 10 }}>
            {payload.items.map((item) => (
              <article
                key={item.dlq_id}
                style={{ border: "1px solid rgba(99, 83, 57, 0.14)", borderRadius: 14, padding: 14, display: "grid", gap: 8 }}
              >
                <div style={{ display: "flex", justifyContent: "space-between", gap: 12, alignItems: "start" }}>
                  <div style={{ display: "grid", gap: 4 }}>
                    <strong style={{ fontSize: 13 }}>{item.reason}</strong>
                    <span style={{ fontSize: 12, color: "#62584b" }}>
                      {item.queue_name} · {item.source_queue_name} · {item.state}
                    </span>
                  </div>
                  <span style={{ fontSize: 12, color: "#62584b" }}>{formatTimestamp(item.dead_lettered_at)}</span>
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
            {payload.next_cursor ? (
              <button
                type="button"
                style={secondaryButtonStyle}
                onClick={() => void load({ ...query, cursor: payload.next_cursor || null })}
              >
                Load Next Page
              </button>
            ) : null}
          </div>
        ) : null}
      </SectionCard>
    </div>
  );
}

import type { FormEvent } from "react";
import { useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";

import { searchTasks } from "../api";
import {
  NoticeBanner,
  SectionCard,
  inputStyle,
  mutedTextStyle,
  primaryButtonStyle,
  secondaryButtonStyle,
} from "../ui";
import type { StudioTaskSearchQuery, StudioTaskSearchResponse } from "../types";

function buildInitialQuery(searchParams: URLSearchParams): StudioTaskSearchQuery {
  return {
    service_id: searchParams.get("service_id") || "",
    task_id: searchParams.get("task_id") || "",
    correlation_id: searchParams.get("correlation_id") || "",
    status: searchParams.get("status") || "",
    stage: searchParams.get("stage") || "",
    from: searchParams.get("from") || "",
    to: searchParams.get("to") || "",
    limit: 50,
    cursor: null,
  };
}

export function TaskSearchPage() {
  const [searchParams] = useSearchParams();
  const [query, setQuery] = useState<StudioTaskSearchQuery>(() => buildInitialQuery(searchParams));
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<StudioTaskSearchResponse | null>(null);

  const backServiceId = useMemo(() => searchParams.get("service_id") || query.service_id || "", [query.service_id, searchParams]);

  async function loadSearch(nextQuery: StudioTaskSearchQuery) {
    setLoading(true);
    setError(null);
    try {
      const payload = await searchTasks(nextQuery);
      setResult((current) =>
        nextQuery.cursor && current
          ? {
              count: current.count + payload.count,
              items: [...current.items, ...payload.items],
              next_cursor: payload.next_cursor || null,
            }
          : payload,
      );
    } catch (fetchError) {
      setError(fetchError instanceof Error ? fetchError.message : "Unable to search tasks.");
      if (!nextQuery.cursor) {
        setResult(null);
      }
    } finally {
      setLoading(false);
    }
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const nextQuery = { ...query, cursor: null };
    setQuery(nextQuery);
    await loadSearch(nextQuery);
  }

  return (
    <div className="studio-stack-lg">
      {error ? <NoticeBanner tone="error">{error}</NoticeBanner> : null}

      <SectionCard
        title="Task Search"
        subtitle="Indexed cross-service task search over retained Studio task summaries."
        action={
          backServiceId ? (
            <Link to={`/services/${encodeURIComponent(backServiceId)}`} style={{ ...secondaryButtonStyle, textDecoration: "none" }}>
              Back to Service
            </Link>
          ) : undefined
        }
      >
        <form onSubmit={handleSubmit} className="studio-form-grid studio-form-grid--task-search">
          <input
            value={query.service_id || ""}
            onChange={(event) => setQuery((current) => ({ ...current, service_id: event.target.value }))}
            placeholder="service_id"
            style={inputStyle}
          />
          <input
            value={query.task_id || ""}
            onChange={(event) => setQuery((current) => ({ ...current, task_id: event.target.value }))}
            placeholder="task_id"
            style={inputStyle}
          />
          <input
            value={query.correlation_id || ""}
            onChange={(event) => setQuery((current) => ({ ...current, correlation_id: event.target.value }))}
            placeholder="correlation_id"
            style={inputStyle}
          />
          <input
            value={query.status || ""}
            onChange={(event) => setQuery((current) => ({ ...current, status: event.target.value }))}
            placeholder="status"
            style={inputStyle}
          />
          <input
            value={query.stage || ""}
            onChange={(event) => setQuery((current) => ({ ...current, stage: event.target.value }))}
            placeholder="stage"
            style={inputStyle}
          />
          <input
            type="datetime-local"
            value={query.from || ""}
            onChange={(event) => setQuery((current) => ({ ...current, from: event.target.value }))}
            style={inputStyle}
          />
          <input
            type="datetime-local"
            value={query.to || ""}
            onChange={(event) => setQuery((current) => ({ ...current, to: event.target.value }))}
            style={inputStyle}
          />
          <button type="submit" style={primaryButtonStyle}>
            Search
          </button>
        </form>

        {loading ? <p style={mutedTextStyle}>Searching retained task summaries...</p> : null}
        {!loading && result ? (
          <div className="studio-stack-md">
            <section className="studio-subcard" style={{ padding: 16 }}>
              Matches: <strong>{result.items.length}</strong>
            </section>
            {!result.items.length ? <p style={mutedTextStyle}>No retained task matches found.</p> : null}
            {result.items.map((item) => (
              <article
                key={`${item.service_id}-${item.task_id}`}
                className="studio-subcard"
                style={{ borderRadius: 14, padding: 14, display: "grid", gap: 6 }}
              >
                <strong>{item.service_name}</strong>
                <span className="studio-inline-meta" style={{ fontSize: 13 }}>
                  {item.service_id} · {item.environment}
                </span>
                <span className="studio-inline-meta" style={{ fontSize: 13 }}>
                  task={item.task_id}
                  {item.correlation_id ? ` · correlation=${item.correlation_id}` : ""}
                  {item.status ? ` · status=${item.status}` : ""}
                  {item.stage ? ` · stage=${item.stage}` : ""}
                </span>
                <span className="studio-inline-meta" style={{ fontSize: 13 }}>
                  last seen: {item.last_seen_at ? new Date(item.last_seen_at).toLocaleString() : "unknown"}
                </span>
                <Link to={`/tasks/${encodeURIComponent(item.service_id)}/${encodeURIComponent(item.task_id)}`} style={{ ...secondaryButtonStyle, textDecoration: "none", width: "fit-content" }}>
                  Open Task Detail
                </Link>
              </article>
            ))}
            {result.next_cursor ? (
              <button
                type="button"
                onClick={() => void loadSearch({ ...query, cursor: result.next_cursor || null })}
                style={secondaryButtonStyle}
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

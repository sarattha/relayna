import type { FormEvent } from "react";
import { useEffect, useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";

import { searchTasks } from "../api";
import {
  NoticeBanner,
  SectionCard,
  StudioIcon,
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
  const [searchParams, setSearchParams] = useSearchParams();
  const [query, setQuery] = useState<StudioTaskSearchQuery>(() => buildInitialQuery(searchParams));
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<StudioTaskSearchResponse | null>(null);

  const backServiceId = useMemo(() => searchParams.get("service_id") || query.service_id || "", [query.service_id, searchParams]);

  useEffect(() => {
    setQuery(buildInitialQuery(searchParams));
  }, [searchParams]);

  function writeQueryToUrl(nextQuery: StudioTaskSearchQuery) {
    const nextParams = new URLSearchParams();
    for (const [key, value] of Object.entries(nextQuery)) {
      if (key !== "cursor" && key !== "limit" && typeof value === "string" && value.trim()) {
        nextParams.set(key, value.trim());
      }
    }
    setSearchParams(nextParams);
  }

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
    writeQueryToUrl(nextQuery);
    await loadSearch(nextQuery);
  }

  function clearSearch() {
    const nextQuery = buildInitialQuery(new URLSearchParams());
    setQuery(nextQuery);
    setSearchParams(new URLSearchParams());
    setResult(null);
    setError(null);
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
              <StudioIcon name="back" />
              Back to Service
            </Link>
          ) : undefined
        }
      >
        <form onSubmit={handleSubmit} className="studio-form-grid studio-form-grid--task-search">
          <label className="studio-filter-field">
            <span>Service ID</span>
            <input
              value={query.service_id || ""}
              onChange={(event) => setQuery((current) => ({ ...current, service_id: event.target.value }))}
              placeholder="service_id"
              style={inputStyle}
            />
          </label>
          <label className="studio-filter-field">
            <span>Task ID</span>
            <input
              value={query.task_id || ""}
              onChange={(event) => setQuery((current) => ({ ...current, task_id: event.target.value }))}
              placeholder="task_id"
              style={inputStyle}
            />
          </label>
          <label className="studio-filter-field">
            <span>Correlation ID</span>
            <input
              value={query.correlation_id || ""}
              onChange={(event) => setQuery((current) => ({ ...current, correlation_id: event.target.value }))}
              placeholder="correlation_id"
              style={inputStyle}
            />
          </label>
          <label className="studio-filter-field">
            <span>Status</span>
            <input
              value={query.status || ""}
              onChange={(event) => setQuery((current) => ({ ...current, status: event.target.value }))}
              placeholder="status"
              style={inputStyle}
            />
          </label>
          <label className="studio-filter-field">
            <span>Stage</span>
            <input
              value={query.stage || ""}
              onChange={(event) => setQuery((current) => ({ ...current, stage: event.target.value }))}
              placeholder="stage"
              style={inputStyle}
            />
          </label>
          <label className="studio-filter-field">
            <span>From (local time)</span>
            <input
              type="datetime-local"
              value={query.from || ""}
              onChange={(event) => setQuery((current) => ({ ...current, from: event.target.value }))}
              style={inputStyle}
            />
          </label>
          <label className="studio-filter-field">
            <span>To (local time)</span>
            <input
              type="datetime-local"
              value={query.to || ""}
              onChange={(event) => setQuery((current) => ({ ...current, to: event.target.value }))}
              style={inputStyle}
            />
          </label>
          <div className="studio-search-actions">
            <button type="submit" style={primaryButtonStyle}>
              <StudioIcon name="search" />
              Search
            </button>
            <button type="button" onClick={clearSearch} style={secondaryButtonStyle}>
              <StudioIcon name="clear" />
              Clear
            </button>
          </div>
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
                </span>
                {item.status || item.stage ? (
                  <div className="studio-chip-row">
                    {item.status ? <span className={`studio-task-chip studio-task-chip--${item.status}`}>{item.status}</span> : null}
                    {item.stage ? <span className="studio-task-chip">Stage: {item.stage}</span> : null}
                  </div>
                ) : null}
                <span className="studio-inline-meta" style={{ fontSize: 13 }}>
                  last seen: {item.last_seen_at ? new Date(item.last_seen_at).toLocaleString() : "unknown"}
                </span>
                {item.source === "loki_fallback" ? (
                  <p style={{ ...mutedTextStyle, margin: 0 }}>
                    Task not found in the recent index — result sourced from Loki log metadata.
                  </p>
                ) : null}
                <Link to={`/tasks/${encodeURIComponent(item.service_id)}/${encodeURIComponent(item.task_id)}`} style={{ ...secondaryButtonStyle, textDecoration: "none", width: "fit-content" }}>
                  <StudioIcon name="open" />
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
                <StudioIcon name="next" />
                Load Next Page
              </button>
            ) : null}
          </div>
        ) : null}
      </SectionCard>
    </div>
  );
}

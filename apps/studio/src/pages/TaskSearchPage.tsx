import type { FormEvent } from "react";
import { useState } from "react";
import { Link, useSearchParams } from "react-router-dom";

import { searchTasks } from "../api";
import {
  NoticeBanner,
  SectionCard,
  TaskRefLink,
  formatJoinKind,
  inputStyle,
  mutedTextStyle,
  primaryButtonStyle,
  secondaryButtonStyle,
} from "../ui";
import type { StudioTaskSearchResponse } from "../types";

export function TaskSearchPage() {
  const [searchParams] = useSearchParams();
  const [taskId, setTaskId] = useState("");
  const [join, setJoin] = useState<"none" | "correlation" | "lineage" | "all">("all");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<StudioTaskSearchResponse | null>(null);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setLoading(true);
    setError(null);
    try {
      const payload = await searchTasks(taskId, join);
      setResult(payload);
    } catch (fetchError) {
      setError(fetchError instanceof Error ? fetchError.message : "Unable to search tasks.");
      setResult(null);
    } finally {
      setLoading(false);
    }
  }

  return (
    <div style={{ display: "grid", gap: 20 }}>
      {error ? <NoticeBanner tone="error">{error}</NoticeBanner> : null}

      <SectionCard
        title="Task Search"
        subtitle="Exact task-id federated search using the existing `/studio/tasks/search` endpoint."
        action={
          searchParams.get("service_id") ? (
            <Link
              to={`/services/${encodeURIComponent(searchParams.get("service_id") || "")}`}
              style={{ ...secondaryButtonStyle, textDecoration: "none" }}
            >
              Back to Service
            </Link>
          ) : undefined
        }
      >
        <form onSubmit={handleSubmit} style={{ display: "grid", gap: 10, gridTemplateColumns: "minmax(0, 1.6fr) 180px 160px" }}>
          <input
            value={taskId}
            onChange={(event) => setTaskId(event.target.value)}
            placeholder="task-123"
            style={inputStyle}
          />
          <select value={join} onChange={(event) => setJoin(event.target.value as typeof join)} style={inputStyle}>
            <option value="none">join=none</option>
            <option value="correlation">join=correlation</option>
            <option value="lineage">join=lineage</option>
            <option value="all">join=all</option>
          </select>
          <button type="submit" style={primaryButtonStyle}>
            Search
          </button>
        </form>

        {loading ? <p style={mutedTextStyle}>Searching tasks...</p> : null}
        {!loading && result ? (
          <div style={{ display: "grid", gap: 20 }}>
            <div style={{ display: "grid", gap: 10, gridTemplateColumns: "repeat(3, minmax(0, 1fr))" }}>
              <section style={{ ...secondaryButtonStyle, padding: 16, cursor: "default" }}>
                Exact matches: <strong>{result.count}</strong>
              </section>
              <section style={{ ...secondaryButtonStyle, padding: 16, cursor: "default" }}>
                Joined matches: <strong>{result.joined_count}</strong>
              </section>
              <section style={{ ...secondaryButtonStyle, padding: 16, cursor: "default" }}>
                Scanned services: <strong>{result.scanned_services.length}</strong>
              </section>
            </div>

            <div style={{ display: "grid", gap: 10 }}>
              <h3 style={{ margin: 0 }}>Exact Matches</h3>
              {!result.items.length ? <p style={mutedTextStyle}>No exact matches found.</p> : null}
              {result.items.map((item) => (
                <article
                  key={`${item.service_id}-${item.task_id}`}
                  style={{ border: "1px solid rgba(99, 83, 57, 0.14)", borderRadius: 14, padding: 14, display: "grid", gap: 6 }}
                >
                  <strong>{item.service_name}</strong>
                  <span style={{ fontSize: 13, color: "#62584b" }}>
                    {item.service_id} · {item.environment}
                  </span>
                  <TaskRefLink taskRef={item.task_ref} />
                </article>
              ))}
            </div>

            <div style={{ display: "grid", gap: 10 }}>
              <h3 style={{ margin: 0 }}>Joined Matches</h3>
              {!result.joined_items.length ? <p style={mutedTextStyle}>No joined matches returned for this join mode.</p> : null}
              {result.joined_items.map((item) => (
                <article
                  key={`${item.service_id}-${item.task_id}-${item.join_kind}-${item.matched_value}`}
                  style={{ border: "1px solid rgba(99, 83, 57, 0.14)", borderRadius: 14, padding: 14, display: "grid", gap: 6 }}
                >
                  <strong>
                    {item.service_name} via {formatJoinKind(item.join_kind)}
                  </strong>
                  <span style={{ fontSize: 13, color: "#62584b" }}>matched value: {item.matched_value}</span>
                  <TaskRefLink taskRef={item.task_ref} />
                </article>
              ))}
            </div>

            {result.join_warnings.length ? (
              <div style={{ display: "grid", gap: 10 }}>
                <h3 style={{ margin: 0 }}>Join Warnings</h3>
                {result.join_warnings.map((warning, index) => (
                  <article
                    key={`${warning.code}-${index}`}
                    style={{ border: "1px solid rgba(179, 136, 56, 0.28)", borderRadius: 14, padding: 14 }}
                  >
                    <strong>{warning.join_kind ? formatJoinKind(warning.join_kind) : warning.code}</strong>
                    <p style={{ ...mutedTextStyle, marginTop: 8 }}>{warning.detail}</p>
                  </article>
                ))}
              </div>
            ) : null}
          </div>
        ) : null}
      </SectionCard>
    </div>
  );
}

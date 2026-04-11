import { startTransition, useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";

import { fetchTaskDetail, fetchTaskEvents, fetchTaskLogs } from "../api";
import {
  GraphSurface,
  InlineCodeBox,
  MetadataRow,
  MetricCard,
  NoticeBanner,
  SectionCard,
  TaskRefLink,
  buildMermaid,
  formatDuration,
  formatEventSummary,
  formatJoinKind,
  formatLogLevel,
  formatTaskPointerList,
  formatTimestamp,
  inputStyle,
  mergeControlPlaneEvent,
  mutedTextStyle,
  parseLimit,
  secondaryButtonStyle,
} from "../ui";
import type { StudioControlPlaneEvent, StudioEventListResponse, StudioLogListResponse, StudioTaskDetail } from "../types";

export function TaskDetailPage() {
  const { serviceId = "", taskId = "" } = useParams();
  const [taskDetail, setTaskDetail] = useState<StudioTaskDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [taskTimeline, setTaskTimeline] = useState<StudioEventListResponse | null>(null);
  const [taskTimelineLoading, setTaskTimelineLoading] = useState(false);
  const [taskTimelineError, setTaskTimelineError] = useState<string | null>(null);
  const [taskLogs, setTaskLogs] = useState<StudioLogListResponse | null>(null);
  const [taskLogsLoading, setTaskLogsLoading] = useState(false);
  const [taskLogsError, setTaskLogsError] = useState<string | null>(null);
  const [taskLogQuery, setTaskLogQuery] = useState("");
  const [taskLogLevel, setTaskLogLevel] = useState("");
  const [taskLogLimit, setTaskLogLimit] = useState("50");

  useEffect(() => {
    if (!serviceId || !taskId) {
      return;
    }
    void loadTaskDetail();
  }, [serviceId, taskId]);

  useEffect(() => {
    if (!taskDetail) {
      setTaskTimeline(null);
      setTaskLogs(null);
      setTaskLogsError(null);
      return;
    }
    void loadTaskTimeline(taskDetail.service_id, taskDetail.task_id);
    if (taskDetail.service.log_config) {
      void loadTaskLogs(taskDetail.service_id, taskDetail.task_id, taskDetail.task_ref.correlation_id || null);
    } else {
      setTaskLogs(null);
      setTaskLogsError("No log provider configured for this service.");
    }
  }, [taskDetail]);

  useEffect(() => {
    if (typeof EventSource === "undefined" || !serviceId || !taskId) {
      return;
    }
    const source = new EventSource(`/studio/tasks/${encodeURIComponent(serviceId)}/${encodeURIComponent(taskId)}/events/stream`);
    source.addEventListener("event", (message) => {
      try {
        const parsed = JSON.parse((message as MessageEvent<string>).data) as StudioControlPlaneEvent;
        startTransition(() => {
          setTaskTimeline((current) => {
            const items = mergeControlPlaneEvent(current?.items || [], parsed);
            return { count: items.length, items, next_cursor: current?.next_cursor || null };
          });
        });
      } catch {
        return;
      }
    });
    return () => source.close();
  }, [serviceId, taskId]);

  async function loadTaskDetail() {
    setLoading(true);
    setError(null);
    try {
      const payload = await fetchTaskDetail(serviceId, taskId, "all");
      setTaskDetail(payload);
    } catch (fetchError) {
      setTaskDetail(null);
      setError(fetchError instanceof Error ? fetchError.message : "Unable to load task detail.");
    } finally {
      setLoading(false);
    }
  }

  async function loadTaskTimeline(targetServiceId: string, targetTaskId: string) {
    setTaskTimelineLoading(true);
    setTaskTimelineError(null);
    try {
      const payload = await fetchTaskEvents(targetServiceId, targetTaskId);
      setTaskTimeline(payload);
    } catch (fetchError) {
      setTaskTimelineError(fetchError instanceof Error ? fetchError.message : "Unable to load task timeline.");
    } finally {
      setTaskTimelineLoading(false);
    }
  }

  async function loadTaskLogs(targetServiceId: string, targetTaskId: string, correlationId?: string | null) {
    setTaskLogsLoading(true);
    setTaskLogsError(null);
    try {
      const payload = await fetchTaskLogs(targetServiceId, targetTaskId, {
        query: taskLogQuery,
        level: taskLogLevel,
        limit: parseLimit(taskLogLimit, 50),
        correlation_id: correlationId,
      });
      setTaskLogs(payload);
    } catch (fetchError) {
      setTaskLogsError(fetchError instanceof Error ? fetchError.message : "Unable to load task logs.");
    } finally {
      setTaskLogsLoading(false);
    }
  }

  const graph = taskDetail?.execution_graph || null;
  const mermaid = graph ? buildMermaid(graph) : "";
  const latestStatusValue = String(taskDetail?.latest_status?.event?.status || graph?.summary.status || "unknown");
  const historyCount = taskDetail?.history?.count ?? 0;
  const dlqCount = taskDetail?.dlq_messages?.items.length ?? 0;
  const taskTimelineItems = taskTimeline?.items || [];
  const identityRef = taskDetail?.task_ref || graph?.task_ref || null;

  return (
    <div style={{ display: "grid", gap: 20 }}>
      {error ? <NoticeBanner tone="error">{error}</NoticeBanner> : null}

      <SectionCard
        title="Task Detail"
        subtitle="Canonical federated task route backed by `/studio/tasks/:serviceId/:taskId`."
        action={
          <div style={{ display: "flex", gap: 10 }}>
            <Link to={`/services/${encodeURIComponent(serviceId)}`} style={{ ...secondaryButtonStyle, textDecoration: "none" }}>
              Back to Service
            </Link>
            <button type="button" onClick={() => void loadTaskDetail()} style={secondaryButtonStyle}>
              Reload
            </button>
          </div>
        }
      >
        {loading ? <p style={mutedTextStyle}>Loading task detail...</p> : null}
        {!loading && !taskDetail ? <p style={mutedTextStyle}>No task detail is available for this task.</p> : null}
        {taskDetail ? (
          <div style={{ display: "grid", gap: 20 }}>
            <div style={{ display: "grid", gap: 14, gridTemplateColumns: "repeat(auto-fit, minmax(180px, 1fr))" }}>
              <MetricCard label="Status" value={latestStatusValue} />
              <MetricCard label="History Events" value={String(historyCount)} />
              <MetricCard label="Timeline Events" value={String(taskTimelineItems.length)} />
              <MetricCard label="DLQ Messages" value={String(dlqCount)} />
              <MetricCard label="Graph" value={graph ? `${graph.nodes.length} nodes` : "Unavailable"} />
            </div>

            <div style={{ display: "grid", gap: 20, gridTemplateColumns: graph ? "minmax(0, 1.8fr) minmax(320px, 0.9fr)" : "1fr" }}>
              <div style={{ display: "grid", gap: 18 }}>
                {graph ? (
                  <GraphSurface graph={graph} />
                ) : (
                  <NoticeBanner tone="error">Studio loaded task detail, but the federated execution-graph read returned no graph for this task.</NoticeBanner>
                )}

                <SectionCard
                  title="Task Timeline"
                  action={
                    <button
                      type="button"
                      onClick={() => void loadTaskTimeline(taskDetail.service_id, taskDetail.task_id)}
                      style={secondaryButtonStyle}
                    >
                      Reload Timeline
                    </button>
                  }
                >
                  {taskTimelineLoading ? <p style={mutedTextStyle}>Loading task timeline...</p> : null}
                  {taskTimelineError ? <p style={{ ...mutedTextStyle, color: "#7a2424" }}>{taskTimelineError}</p> : null}
                  {!taskTimelineLoading && !taskTimelineItems.length ? (
                    <p style={mutedTextStyle}>No Studio-ingested task events yet.</p>
                  ) : null}
                  {taskTimelineItems.length ? (
                    <div style={{ display: "grid", gap: 10, maxHeight: 420, overflowY: "auto" }}>
                      {taskTimelineItems.map((item) => (
                        <article
                          key={item.dedupe_key}
                          style={{ border: "1px solid rgba(99, 83, 57, 0.14)", borderRadius: 14, padding: 12 }}
                        >
                          <div style={{ display: "flex", justifyContent: "space-between", gap: 12 }}>
                            <strong style={{ fontSize: 13 }}>{formatEventSummary(item)}</strong>
                            <span style={{ fontSize: 12, color: "#62584b" }}>{formatTimestamp(item.timestamp || item.ingested_at)}</span>
                          </div>
                          <p style={{ ...mutedTextStyle, marginTop: 8 }}>
                            {item.source_kind} · {item.event_type} · {item.component || "unknown"}
                            {item.out_of_order ? " · out-of-order" : ""}
                          </p>
                        </article>
                      ))}
                    </div>
                  ) : null}
                </SectionCard>
              </div>

              <aside style={{ display: "grid", gap: 18 }}>
                <SectionCard title="Task Metadata">
                  <dl style={{ margin: 0, display: "grid", gap: 10, fontSize: 13 }}>
                    <MetadataRow label="Service" value={`${taskDetail.service.name} (${taskDetail.service_id})`} />
                    <MetadataRow label="Task id" value={taskDetail.task_id} />
                    <MetadataRow label="Correlation id" value={identityRef?.correlation_id || "none"} />
                    <MetadataRow label="Parent refs" value={formatTaskPointerList(identityRef?.parent_refs || [])} />
                    <MetadataRow label="Child refs" value={formatTaskPointerList(identityRef?.child_refs || [])} />
                    <MetadataRow label="Latest status" value={latestStatusValue} />
                    <MetadataRow label="History events" value={String(historyCount)} />
                    <MetadataRow label="DLQ messages" value={String(dlqCount)} />
                    <MetadataRow label="Duration" value={formatDuration(graph?.summary.duration_ms)} />
                  </dl>
                  {identityRef?.parent_refs.length ? (
                    <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
                      {identityRef.parent_refs.map((pointer) => (
                        <TaskRefLink
                          key={`${pointer.service_id}-${pointer.task_id}`}
                          taskRef={pointer}
                        >
                          Parent: {pointer.service_id}/{pointer.task_id}
                        </TaskRefLink>
                      ))}
                    </div>
                  ) : null}
                  {identityRef?.child_refs.length ? (
                    <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
                      {identityRef.child_refs.map((pointer) => (
                        <TaskRefLink
                          key={`${pointer.service_id}-${pointer.task_id}`}
                          taskRef={pointer}
                        >
                          Child: {pointer.service_id}/{pointer.task_id}
                        </TaskRefLink>
                      ))}
                    </div>
                  ) : null}
                </SectionCard>

                <SectionCard title="Task Logs" action={
                  <button
                    type="button"
                    onClick={() => void loadTaskLogs(taskDetail.service_id, taskDetail.task_id, taskDetail.task_ref.correlation_id || null)}
                    style={secondaryButtonStyle}
                  >
                    Reload Logs
                  </button>
                }>
                  <div style={{ display: "grid", gap: 10, gridTemplateColumns: "minmax(0, 1.4fr) 140px 110px" }}>
                    <input
                      aria-label="Task log text filter"
                      value={taskLogQuery}
                      onChange={(event) => setTaskLogQuery(event.target.value)}
                      placeholder="Search task logs"
                      style={inputStyle}
                    />
                    <input
                      aria-label="Task log level"
                      value={taskLogLevel}
                      onChange={(event) => setTaskLogLevel(event.target.value)}
                      placeholder="level"
                      style={inputStyle}
                    />
                    <input
                      aria-label="Task log limit"
                      value={taskLogLimit}
                      onChange={(event) => setTaskLogLimit(event.target.value)}
                      placeholder="50"
                      style={inputStyle}
                    />
                  </div>
                  {!taskDetail.task_ref.correlation_id ? (
                    <p style={mutedTextStyle}>Correlation filter unavailable for this task; Studio is filtering by task id only.</p>
                  ) : null}
                  {taskLogsLoading ? <p style={mutedTextStyle}>Loading task logs...</p> : null}
                  {taskLogsError ? <p style={{ ...mutedTextStyle, color: "#7a2424" }}>{taskLogsError}</p> : null}
                  {!taskLogsLoading && !taskLogsError && !(taskLogs?.items.length || 0) ? (
                    <p style={mutedTextStyle}>No task logs matched the current filters.</p>
                  ) : null}
                  {taskLogs?.items.length ? (
                    <div style={{ display: "grid", gap: 10, maxHeight: 420, overflowY: "auto" }}>
                      {taskLogs.items.map((item, index) => (
                        <article
                          key={`${item.timestamp}-${item.message}-${index}`}
                          style={{ border: "1px solid rgba(99, 83, 57, 0.14)", borderRadius: 14, padding: 12 }}
                        >
                          <div style={{ display: "flex", justifyContent: "space-between", gap: 12 }}>
                            <div style={{ display: "grid", gap: 4 }}>
                              <strong style={{ fontSize: 13 }}>{item.message}</strong>
                              <span style={{ fontSize: 12, color: "#62584b" }}>
                                {formatLogLevel(item.level)}
                                {item.correlation_id ? ` · ${item.correlation_id}` : ""}
                              </span>
                            </div>
                            <span style={{ fontSize: 12, color: "#62584b" }}>{formatTimestamp(item.timestamp)}</span>
                          </div>
                        </article>
                      ))}
                    </div>
                  ) : null}
                </SectionCard>

                {graph ? (
                  <SectionCard title="Mermaid Export">
                    <InlineCodeBox value={mermaid} minHeight={260} />
                  </SectionCard>
                ) : null}

                {taskDetail.joined_refs.length ? (
                  <SectionCard title="Joined Refs">
                    <div style={{ display: "grid", gap: 10 }}>
                      {taskDetail.joined_refs.map((joinRef, index) => (
                        <div key={`${joinRef.task_ref.service_id}-${joinRef.task_ref.task_id}-${index}`} style={{ display: "grid", gap: 4 }}>
                          <strong style={{ fontSize: 13 }}>
                            <TaskRefLink taskRef={joinRef.task_ref} />
                            {" via "}
                            {formatJoinKind(joinRef.join_kind)}
                          </strong>
                          <span style={{ fontSize: 13, lineHeight: 1.5 }}>
                            matched value: <code>{joinRef.matched_value}</code>
                          </span>
                        </div>
                      ))}
                    </div>
                  </SectionCard>
                ) : null}

                {taskDetail.join_warnings.length ? (
                  <SectionCard title="Join Warnings">
                    <div style={{ display: "grid", gap: 10 }}>
                      {taskDetail.join_warnings.map((warning, index) => (
                        <div key={`${warning.code}-${index}`} style={{ display: "grid", gap: 4 }}>
                          <strong style={{ fontSize: 13 }}>
                            {warning.join_kind ? formatJoinKind(warning.join_kind) : warning.code}
                          </strong>
                          <span style={{ fontSize: 13, lineHeight: 1.5 }}>{warning.detail}</span>
                        </div>
                      ))}
                    </div>
                  </SectionCard>
                ) : null}

                {taskDetail.errors.length ? (
                  <SectionCard title="Section Errors">
                    <div style={{ display: "grid", gap: 10 }}>
                      {taskDetail.errors.map((item, index) => (
                        <article key={`${item.code}-${index}`} style={{ display: "grid", gap: 4 }}>
                          <strong style={{ fontSize: 13 }}>{item.code}</strong>
                          <span style={{ fontSize: 13 }}>{item.detail}</span>
                        </article>
                      ))}
                    </div>
                  </SectionCard>
                ) : null}
              </aside>
            </div>
          </div>
        ) : null}
      </SectionCard>
    </div>
  );
}

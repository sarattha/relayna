import { startTransition, useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";

import { fetchTaskDetail, fetchTaskEvents, fetchTaskLogs } from "../api";
import {
  GraphSurface,
  InlineCodeBox,
  LogSourceBadge,
  LogMessage,
  MetadataRow,
  MetricCard,
  NoticeBanner,
  SectionCard,
  StudioIcon,
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
  supportsCapability,
} from "../ui";
import type { StudioControlPlaneEvent, StudioEventListResponse, StudioLogListResponse, StudioTaskDetail } from "../types";

const TASK_QUEUED_STATUSES = new Set(["queued"]);
const TASK_TERMINAL_STATUSES = new Set([
  "cancelled",
  "canceled",
  "complete",
  "completed",
  "dead_lettered",
  "dead-lettered",
  "error",
  "errored",
  "failed",
  "timeout",
  "timed_out",
  "timed-out",
]);

type TaskLogWindowMode = "auto" | "15m" | "1h" | "24h" | "manual";
type TaskLogWindow = { from: string; to: string };

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

function resolveQuickTaskLogWindow(mode: TaskLogWindowMode) {
  if (mode === "auto" || mode === "manual") {
    return null;
  }
  const now = Date.now();
  const durationMs = mode === "15m" ? 15 * 60 * 1000 : mode === "1h" ? 60 * 60 * 1000 : 24 * 60 * 60 * 1000;
  return {
    from: new Date(now - durationMs).toISOString(),
    to: new Date(now).toISOString(),
  };
}

function describeTaskLogWindow(mode: TaskLogWindowMode, from: string, to: string, warning?: string | null) {
  if (mode === "auto") {
    return (
      warning ||
      `Auto window: ${from ? new Date(from).toLocaleString() : "unbounded"} to ${
        to ? new Date(to).toLocaleString() : "unbounded"
      }.`
    );
  }
  if (mode === "manual") {
    return "Custom range is active. Use local date and time fields; empty bounds stay unbounded.";
  }
  const label = mode === "15m" ? "15 minutes" : mode === "1h" ? "1 hour" : "24 hours";
  return `Quick window: last ${label} (${from ? new Date(from).toLocaleString() : "unbounded"} to ${
    to ? new Date(to).toLocaleString() : "unbounded"
  }).`;
}

function isInTimeWindow(value: string, window: TaskLogWindow) {
  if (!value.trim()) {
    return true;
  }
  const timestamp = new Date(value).getTime();
  if (Number.isNaN(timestamp)) {
    return true;
  }
  if (window.from) {
    const fromTimestamp = new Date(window.from).getTime();
    if (!Number.isNaN(fromTimestamp) && timestamp < fromTimestamp) {
      return false;
    }
  }
  if (window.to) {
    const toTimestamp = new Date(window.to).getTime();
    if (!Number.isNaN(toTimestamp) && timestamp > toTimestamp) {
      return false;
    }
  }
  return true;
}

function normalizeStatusValue(value: unknown) {
  return typeof value === "string" ? value.trim().toLowerCase() : "";
}

function parseTimestamp(value: string | null | undefined) {
  if (!value) {
    return null;
  }
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? null : parsed;
}

function extractRecordTimestamp(record: Record<string, unknown> | null | undefined) {
  if (!record) {
    return null;
  }
  for (const key of ["timestamp", "event_timestamp", "created_at", "updated_at", "ingested_at"]) {
    const value = record[key];
    if (typeof value === "string" && parseTimestamp(value)) {
      return value;
    }
  }
  return null;
}

function statusFromTimelineEvent(item: StudioControlPlaneEvent) {
  const payloadStatus =
    item.payload && typeof item.payload === "object" ? normalizeStatusValue((item.payload as Record<string, unknown>).status) : "";
  if (payloadStatus) {
    return payloadStatus;
  }
  const eventType = normalizeStatusValue(item.event_type);
  if (!eventType) {
    return "";
  }
  const segments = eventType.split(".");
  return segments[segments.length - 1] || eventType;
}

function deriveTaskLogWindow(taskDetail: StudioTaskDetail, taskTimeline: StudioEventListResponse | null, fallbackNow: string) {
  const taskId = taskDetail.task_id;
  const timelineItems = (taskTimeline?.items || [])
    .filter((item) => item.task_id === taskId)
    .map((item) => ({ item, timestamp: item.timestamp || item.ingested_at || null }))
    .filter((item): item is { item: StudioControlPlaneEvent; timestamp: string } => Boolean(item.timestamp))
    .sort((left, right) => new Date(left.timestamp).getTime() - new Date(right.timestamp).getTime());

  const queuedTimeline = timelineItems.find(({ item }) => TASK_QUEUED_STATUSES.has(statusFromTimelineEvent(item)))?.timestamp || null;
  const earliestTimeline = timelineItems[0]?.timestamp || null;
  const terminalTimeline =
    timelineItems.find(
      ({ item, timestamp }) =>
        TASK_TERMINAL_STATUSES.has(statusFromTimelineEvent(item)) &&
        (!queuedTimeline || new Date(timestamp).getTime() >= new Date(queuedTimeline).getTime()),
    )?.timestamp || null;

  const historyEvents = (taskDetail.history?.events || []).filter(
    (event): event is Record<string, unknown> => Boolean(event) && typeof event === "object",
  );
  const queuedHistory =
    historyEvents.find(
      (event) =>
        String(event.task_id || taskId) === taskId && TASK_QUEUED_STATUSES.has(normalizeStatusValue(event.status)),
    ) || null;
  const earliestHistory = historyEvents.find((event) => String(event.task_id || taskId) === taskId) || null;

  const start =
    queuedTimeline ||
    extractRecordTimestamp(queuedHistory) ||
    earliestTimeline ||
    extractRecordTimestamp(earliestHistory) ||
    taskDetail.execution_graph?.summary.started_at ||
    null;

  if (!start) {
    return {
      from: "",
      to: "",
      warning: "Studio could not derive a task log window from this task yet. Logs will stay unbounded until you provide one manually.",
    };
  }

  const endedAt = taskDetail.execution_graph?.summary.ended_at || null;
  const candidateEnd = terminalTimeline || endedAt || fallbackNow;
  const end = new Date(candidateEnd).getTime() >= new Date(start).getTime() ? candidateEnd : fallbackNow;
  return {
    from: start,
    to: end,
    warning: null,
  };
}

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
  const [taskLogSource, setTaskLogSource] = useState("");
  const [taskLogLimit, setTaskLogLimit] = useState("50");
  const [taskLogWindowMode, setTaskLogWindowMode] = useState<TaskLogWindowMode>("auto");
  const [taskLogManualFrom, setTaskLogManualFrom] = useState("");
  const [taskLogManualTo, setTaskLogManualTo] = useState("");
  const [taskLogAutoNow, setTaskLogAutoNow] = useState(() => new Date().toISOString());
  const [mermaidCopyState, setMermaidCopyState] = useState<"idle" | "copied" | "selected" | "failed">("idle");

  useEffect(() => {
    if (!serviceId || !taskId) {
      return;
    }
    void loadTaskDetail();
  }, [serviceId, taskId]);

  useEffect(() => {
    setTaskLogSource("");
    setTaskLogWindowMode("auto");
    setTaskLogManualFrom("");
    setTaskLogManualTo("");
    setTaskLogAutoNow(new Date().toISOString());
    if (!taskDetail) {
      setTaskTimeline(null);
      setTaskLogs(null);
      setTaskLogsError(null);
      return;
    }
    setTaskTimeline(null);
    void loadTaskTimeline(taskDetail.service_id, taskDetail.task_id);
  }, [taskDetail]);

  const derivedTaskLogWindow = taskDetail ? deriveTaskLogWindow(taskDetail, taskTimeline, taskLogAutoNow) : null;
  const quickTaskLogWindow = resolveQuickTaskLogWindow(taskLogWindowMode);
  const activeTaskLogFrom =
    taskLogWindowMode === "manual" ? localDateTimeToIso(taskLogManualFrom) : quickTaskLogWindow?.from || derivedTaskLogWindow?.from || "";
  const activeTaskLogTo =
    taskLogWindowMode === "manual" ? localDateTimeToIso(taskLogManualTo) : quickTaskLogWindow?.to || derivedTaskLogWindow?.to || "";

  useEffect(() => {
    if (!taskDetail || taskTimelineLoading) {
      return;
    }
    const window = getTaskLogWindow();
    if (taskDetail.service.log_config) {
      void loadTaskLogs(taskDetail.service_id, taskDetail.task_id, taskDetail.task_ref.correlation_id || null, window);
    } else {
      setTaskLogs(null);
      setTaskLogsError("No log provider configured for this service.");
    }
  }, [
    taskDetail,
    taskTimelineLoading,
  ]);

  function getTaskLogWindow({ refreshAutoNow = false }: { refreshAutoNow?: boolean } = {}) {
    return getTaskLogWindowForMode(taskLogWindowMode, taskLogManualFrom, taskLogManualTo, { refreshAutoNow });
  }

  function getTaskLogWindowForMode(
    mode: TaskLogWindowMode,
    manualFrom: string,
    manualTo: string,
    { refreshAutoNow = false }: { refreshAutoNow?: boolean } = {},
  ) {
    const quickWindow = resolveQuickTaskLogWindow(mode);
    if (mode === "manual") {
      return {
        from: localDateTimeToIso(manualFrom),
        to: localDateTimeToIso(manualTo),
      };
    }
    if (quickWindow) {
      return {
        from: quickWindow.from,
        to: quickWindow.to,
      };
    }
    const autoNow = refreshAutoNow ? new Date().toISOString() : taskLogAutoNow;
    if (refreshAutoNow) {
      setTaskLogAutoNow(autoNow);
    }
    const window = taskDetail ? deriveTaskLogWindow(taskDetail, taskTimeline, autoNow) : null;
    return {
      from: window?.from || "",
      to: window?.to || "",
    };
  }

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

  async function loadTaskLogs(
    targetServiceId: string,
    targetTaskId: string,
    correlationId?: string | null,
    window?: { from?: string; to?: string },
  ) {
    setTaskLogsLoading(true);
    setTaskLogsError(null);
    try {
      const payload = await fetchTaskLogs(targetServiceId, targetTaskId, {
        query: taskLogQuery,
        level: taskLogLevel,
        source: taskLogSource,
        limit: parseLimit(taskLogLimit, 50),
        correlation_id: correlationId,
        from: window?.from,
        to: window?.to,
      });
      setTaskLogs(payload);
    } catch (fetchError) {
      setTaskLogsError(fetchError instanceof Error ? fetchError.message : "Unable to load task logs.");
    } finally {
      setTaskLogsLoading(false);
    }
  }

  async function copyMermaidToClipboard() {
    if (!mermaid) {
      return;
    }
    const selectVisibleMermaid = () => {
      const visibleTarget = document.querySelector<HTMLTextAreaElement>(".studio-mermaid-export-card textarea");
      if (!visibleTarget) {
        return false;
      }
      visibleTarget.focus();
      visibleTarget.select();
      visibleTarget.setSelectionRange(0, visibleTarget.value.length);
      return true;
    };
    const copyWithSelection = () => {
      const clipboardTarget = document.createElement("textarea");
      clipboardTarget.value = mermaid;
      clipboardTarget.setAttribute("readonly", "");
      clipboardTarget.style.position = "fixed";
      clipboardTarget.style.left = "-9999px";
      clipboardTarget.style.top = "0";
      document.body.appendChild(clipboardTarget);
      clipboardTarget.focus();
      clipboardTarget.select();
      clipboardTarget.setSelectionRange(0, clipboardTarget.value.length);
      const copied = document.execCommand("copy");
      document.body.removeChild(clipboardTarget);
      if (!copied) {
        throw new Error("Clipboard copy was blocked.");
      }
    };

    try {
      if (navigator.clipboard?.writeText) {
        try {
          await navigator.clipboard.writeText(mermaid);
        } catch {
          copyWithSelection();
        }
      } else {
        copyWithSelection();
      }
      setMermaidCopyState("copied");
    } catch {
      setMermaidCopyState(selectVisibleMermaid() ? "selected" : "failed");
    }
    window.setTimeout(() => setMermaidCopyState("idle"), 1800);
  }

  const graph = taskDetail?.execution_graph || null;
  const mermaid = graph ? buildMermaid(graph) : "";
  const latestStatusValue = String(taskDetail?.latest_status?.event?.status || graph?.summary.status || "unknown");
  const historyCount = taskDetail?.history?.count ?? 0;
  const dlqCount = taskDetail?.dlq_messages?.items.length ?? 0;
  const taskTimelineItems = taskTimeline?.items || [];
  const identityRef = taskDetail?.task_ref || graph?.task_ref || null;
  const brokerDlqSupported = supportsCapability(taskDetail?.service.capabilities || null, "broker.dlq.messages");
  const filteredTaskLogs = (taskLogs?.items || []).filter((item) =>
    isInTimeWindow(item.timestamp, { from: activeTaskLogFrom, to: activeTaskLogTo }),
  );
  const taskLogSourceOptions = Array.from(new Set((taskLogs?.items || []).map((item) => item.source).filter(Boolean))).sort();

  return (
    <div className="studio-stack-lg">
      {error ? <NoticeBanner tone="error">{error}</NoticeBanner> : null}

      <SectionCard
        title="Task Detail"
        subtitle="Canonical federated task route backed by `/studio/tasks/:serviceId/:taskId`."
        action={
          <div style={{ display: "flex", gap: 10 }}>
            <Link to={`/services/${encodeURIComponent(serviceId)}`} style={{ ...secondaryButtonStyle, textDecoration: "none" }}>
              <StudioIcon name="back" />
              Back to Service
            </Link>
            <button type="button" onClick={() => void loadTaskDetail()} style={secondaryButtonStyle}>
              <StudioIcon name="refresh" />
              Reload
            </button>
          </div>
        }
      >
        {loading ? <p style={mutedTextStyle}>Loading task detail...</p> : null}
        {!loading && !taskDetail ? <p style={mutedTextStyle}>No task detail is available for this task.</p> : null}
        {taskDetail ? (
          <div className="studio-stack-lg">
            <div className="studio-metrics-grid">
              <MetricCard label="Status" value={latestStatusValue} />
              <MetricCard label="History Events" value={String(historyCount)} />
              <MetricCard label="Timeline Events" value={String(taskTimelineItems.length)} />
              <MetricCard label="DLQ Messages" value={String(dlqCount)} />
              <MetricCard label="Graph" value={graph ? `${graph.nodes.length} nodes` : "Unavailable"} />
            </div>

            <div className={graph ? "studio-content-split studio-content-split--graph" : "studio-stack-lg"}>
              <div className="studio-stack-md">
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
                      <StudioIcon name="refresh" />
                      Reload Timeline
                    </button>
                  }
                >
                  {taskTimelineLoading ? <p style={mutedTextStyle}>Loading task timeline...</p> : null}
                  {taskTimelineError ? <p style={{ ...mutedTextStyle, color: "var(--studio-danger)" }}>{taskTimelineError}</p> : null}
                  {!taskTimelineLoading && !taskTimelineItems.length ? (
                    <p style={mutedTextStyle}>No Studio-ingested task events yet.</p>
                  ) : null}
                  {taskTimelineItems.length ? (
                    <div className="studio-stack-sm studio-surface-scroll">
                      {taskTimelineItems.map((item) => (
                        <article
                          key={item.dedupe_key}
                          className="studio-subcard"
                          style={{ borderRadius: 14, padding: 12 }}
                        >
                          <div className="studio-list-card__top">
                            <strong style={{ fontSize: 13 }}>{formatEventSummary(item)}</strong>
                            <span className="studio-inline-meta">{formatTimestamp(item.timestamp || item.ingested_at)}</span>
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

              <aside className="studio-stack-md">
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
                  {brokerDlqSupported && dlqCount === 0 ? (
                    <div style={{ marginTop: 14 }}>
                      <NoticeBanner>
                        Indexed DLQ data is empty for this task.{" "}
                        <Link
                          to={`/services/${encodeURIComponent(taskDetail.service_id)}/dlq?mode=broker&task_id=${encodeURIComponent(taskDetail.task_id)}`}
                        >
                          <StudioIcon name="dlq" />
                          Inspect live broker DLQ messages
                        </Link>
                        .
                      </NoticeBanner>
                    </div>
                  ) : null}
                  {identityRef?.parent_refs.length ? (
                    <div className="studio-action-row">
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
                    <div className="studio-action-row">
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
                    onClick={() =>
                      void loadTaskLogs(taskDetail.service_id, taskDetail.task_id, taskDetail.task_ref.correlation_id || null, {
                        ...getTaskLogWindow({ refreshAutoNow: true }),
                      })
                    }
                    style={secondaryButtonStyle}
                  >
                    <StudioIcon name="refresh" />
                    Reload Logs
                  </button>
                }>
                  <div className="studio-log-filter-grid">
                    <label className="studio-filter-field">
                      <span>Text</span>
                      <input
                        aria-label="Task log text filter"
                        value={taskLogQuery}
                        onChange={(event) => setTaskLogQuery(event.target.value)}
                        placeholder="Search task logs"
                        style={inputStyle}
                      />
                    </label>
                    <label className="studio-filter-field">
                      <span>Level</span>
                      <input
                        aria-label="Task log level"
                        value={taskLogLevel}
                        onChange={(event) => setTaskLogLevel(event.target.value)}
                        placeholder="info, error"
                        style={inputStyle}
                      />
                    </label>
                    <label className="studio-filter-field">
                      <span>Source</span>
                      <input
                        aria-label="Task log source"
                        value={taskLogSource}
                        onChange={(event) => setTaskLogSource(event.target.value)}
                        list={`task-log-sources-${taskDetail.service_id}-${taskDetail.task_id}`}
                        placeholder={taskDetail.service.log_config?.source_label || "source"}
                        disabled={!taskDetail.service.log_config?.source_label}
                        style={inputStyle}
                      />
                    </label>
                    <label className="studio-filter-field">
                      <span>Limit</span>
                      <input
                        aria-label="Task log limit"
                        value={taskLogLimit}
                        onChange={(event) => setTaskLogLimit(event.target.value)}
                        placeholder="50"
                        inputMode="numeric"
                        style={inputStyle}
                      />
                    </label>
                  </div>
                  {taskLogSourceOptions.length ? (
                    <datalist id={`task-log-sources-${taskDetail.service_id}-${taskDetail.task_id}`}>
                      {taskLogSourceOptions.map((source) => (
                        <option key={source} value={source} />
                      ))}
                    </datalist>
                  ) : null}
                  <div className="studio-log-filter-grid studio-log-window-grid" style={{ marginTop: 12 }}>
                    <label className="studio-filter-field">
                      <span>Log Window</span>
                      <select
                        aria-label="Task log window mode"
                        value={taskLogWindowMode}
                        onChange={(event) => {
                          const nextMode = event.target.value as TaskLogWindowMode;
                          let nextManualFrom = taskLogManualFrom;
                          let nextManualTo = taskLogManualTo;
                          if (nextMode === "manual") {
                            nextManualFrom = isoToLocalDateTime(activeTaskLogFrom);
                            nextManualTo = isoToLocalDateTime(activeTaskLogTo);
                            setTaskLogManualFrom(nextManualFrom);
                            setTaskLogManualTo(nextManualTo);
                          }
                          setTaskLogWindowMode(nextMode);
                          if (taskDetail.service.log_config) {
                            void loadTaskLogs(
                              taskDetail.service_id,
                              taskDetail.task_id,
                              taskDetail.task_ref.correlation_id || null,
                              getTaskLogWindowForMode(nextMode, nextManualFrom, nextManualTo),
                            );
                          }
                        }}
                        style={inputStyle}
                      >
                        <option value="auto">Auto task window</option>
                        <option value="15m">Last 15 minutes</option>
                        <option value="1h">Last hour</option>
                        <option value="24h">Last 24 hours</option>
                        <option value="manual">Custom range</option>
                      </select>
                    </label>
                    <label className="studio-filter-field">
                      <span>From</span>
                      <input
                        aria-label="Task log from"
                        type="datetime-local"
                        value={
                          taskLogWindowMode === "manual"
                            ? taskLogManualFrom
                            : isoToLocalDateTime(activeTaskLogFrom)
                        }
                        onChange={(event) => {
                          const nextFrom = event.target.value;
                          setTaskLogManualFrom(nextFrom);
                          if (taskDetail.service.log_config) {
                            void loadTaskLogs(
                              taskDetail.service_id,
                              taskDetail.task_id,
                              taskDetail.task_ref.correlation_id || null,
                              getTaskLogWindowForMode("manual", nextFrom, taskLogManualTo),
                            );
                          }
                        }}
                        disabled={taskLogWindowMode !== "manual"}
                        style={inputStyle}
                      />
                    </label>
                    <label className="studio-filter-field">
                      <span>To</span>
                      <input
                        aria-label="Task log to"
                        type="datetime-local"
                        value={
                          taskLogWindowMode === "manual"
                            ? taskLogManualTo
                            : isoToLocalDateTime(activeTaskLogTo)
                        }
                        onChange={(event) => {
                          const nextTo = event.target.value;
                          setTaskLogManualTo(nextTo);
                          if (taskDetail.service.log_config) {
                            void loadTaskLogs(
                              taskDetail.service_id,
                              taskDetail.task_id,
                              taskDetail.task_ref.correlation_id || null,
                              getTaskLogWindowForMode("manual", taskLogManualFrom, nextTo),
                            );
                          }
                        }}
                        disabled={taskLogWindowMode !== "manual"}
                        style={inputStyle}
                      />
                    </label>
                  </div>
                  <p style={mutedTextStyle}>
                    {describeTaskLogWindow(
                      taskLogWindowMode,
                      activeTaskLogFrom,
                      activeTaskLogTo,
                      derivedTaskLogWindow?.warning,
                    )}
                  </p>
                  {!taskDetail.task_ref.correlation_id ? (
                    <p style={mutedTextStyle}>Correlation filter unavailable for this task; Studio is filtering by task id only.</p>
                  ) : null}
                  {!taskDetail.service.log_config?.source_label ? (
                    <p style={mutedTextStyle}>Source filtering is unavailable until this service sets `log_config.source_label`.</p>
                  ) : (
                    <p style={mutedTextStyle}>
                      Source filter matches the configured `{taskDetail.service.log_config.source_label}` Loki label exactly.
                      {taskLogSourceOptions.length ? ` Discovered values: ${taskLogSourceOptions.join(", ")}.` : ""}
                    </p>
                  )}
                  {taskLogsLoading ? <p style={mutedTextStyle}>Loading task logs...</p> : null}
                  {taskLogsError ? <p style={{ ...mutedTextStyle, color: "var(--studio-danger)" }}>{taskLogsError}</p> : null}
                  {!taskLogsLoading && !taskLogsError && !filteredTaskLogs.length ? (
                    <p style={mutedTextStyle}>No task logs matched the current filters.</p>
                  ) : null}
                  {filteredTaskLogs.length ? (
                    <div className="studio-stack-sm studio-surface-scroll">
                      {filteredTaskLogs.map((item, index) => (
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
                                  {formatLogLevel(item.level)}
                                  {item.correlation_id ? ` · ${item.correlation_id}` : ""}
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

                {graph ? (
                  <SectionCard
                    title="Mermaid Export"
                    className="studio-section-card--compact studio-mermaid-export-card"
                    action={
                      <button type="button" onClick={() => void copyMermaidToClipboard()} style={secondaryButtonStyle}>
                        <StudioIcon name="copy" />
                        {mermaidCopyState === "copied"
                          ? "Copied"
                          : mermaidCopyState === "selected"
                            ? "Selected"
                            : mermaidCopyState === "failed"
                              ? "Copy Failed"
                              : "Copy"}
                      </button>
                    }
                  >
                    <InlineCodeBox value={mermaid} minHeight={120} />
                  </SectionCard>
                ) : null}

                {taskDetail.joined_refs.length ? (
                  <SectionCard title="Joined Refs">
                    <div className="studio-stack-sm">
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
                    <div className="studio-stack-sm">
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
                    <div className="studio-stack-sm">
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

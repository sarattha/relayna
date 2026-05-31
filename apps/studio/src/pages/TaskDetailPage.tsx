import { startTransition, useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";

import { fetchTaskDetail, fetchTaskEvents, fetchTaskLogs, fetchTaskMetrics, fetchTaskTracePath } from "../api";
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
import type {
  StudioControlPlaneEvent,
  StudioEventListResponse,
  StudioLogListResponse,
  StudioMetricsResponse,
  StudioTaskDetail,
  StudioTracePathNode,
  StudioTracePathResponse,
  StudioTraceSpan,
} from "../types";

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

const traceStatePalette: Record<string, { background: string; border: string; color: string }> = {
  dead_lettered: { background: "#fff2f0", border: "#c65f55", color: "#7a2621" },
  failed: { background: "#fff2f0", border: "#c65f55", color: "#7a2621" },
  retrying: { background: "#fff7e6", border: "#c1832f", color: "#6a4312" },
  running: { background: "#eef7ff", border: "#5e8fc4", color: "#204765" },
  succeeded: { background: "#effaf1", border: "#5f9e6b", color: "#24552e" },
  queued: { background: "#f3f1ff", border: "#8271ba", color: "#40346f" },
  unknown: { background: "#f7f7f4", border: "#b8b0a2", color: "#4b453d" },
};

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

function describeTaskMetricWindow(mode: TaskLogWindowMode, from: string, to: string, warning?: string | null) {
  const description = describeTaskLogWindow(mode, from, to, warning);
  return description.startsWith("Auto window:") ? description.replace("Auto window:", "Metrics auto window:") : description;
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

function metricLabel(value: string) {
  return value
    .split("_")
    .map((part) => part.slice(0, 1).toUpperCase() + part.slice(1))
    .join(" ");
}

function formatMetricValue(value: number | null | undefined, unit: string) {
  if (value === null || value === undefined || Number.isNaN(value)) {
    return "n/a";
  }
  if (unit === "bytes") {
    if (Math.abs(value) >= 1024 * 1024 * 1024) {
      return `${(value / (1024 * 1024 * 1024)).toFixed(2)} GiB`;
    }
    if (Math.abs(value) >= 1024 * 1024) {
      return `${(value / (1024 * 1024)).toFixed(2)} MiB`;
    }
    return `${value.toFixed(0)} B`;
  }
  if (unit === "bytes_per_second") {
    return `${(value / 1024).toFixed(2)} KiB/s`;
  }
  if (unit === "cores") {
    return `${value.toFixed(3)} cores`;
  }
  if (unit === "per_second") {
    return `${value.toFixed(3)}/s`;
  }
  if (unit === "seconds") {
    return `${value.toFixed(3)}s`;
  }
  if (unit === "unix_seconds") {
    return value > 0 ? formatTimestamp(new Date(value * 1000).toISOString()) : "n/a";
  }
  return Number.isInteger(value) ? String(value) : value.toFixed(2);
}

function metricLatestValue(metrics: StudioMetricsResponse | null, metric: string) {
  const matchingSeries = metrics?.series.filter((item) => item.metric === metric && item.points.length) || [];
  let unit = "";
  let total = 0;
  let hasValue = false;
  for (const series of matchingSeries) {
    const point = series.points[series.points.length - 1];
    if (point.value === null || point.value === undefined || Number.isNaN(point.value)) {
      continue;
    }
    unit ||= series.unit;
    total += point.value;
    hasValue = true;
  }
  return formatMetricValue(hasValue ? total : null, unit);
}

function extractTaskResourceDelta(taskDetail: StudioTaskDetail | null) {
  const taskId = taskDetail?.task_id;
  const samples = (taskDetail?.execution_graph?.nodes || [])
    .filter((node) => node.kind === "resource_sample" && (!taskId || node.task_id === taskId))
    .map((node) => ({
      sampleKind: String(node.annotations?.sample_kind || ""),
      cpu: Number(node.annotations?.cpu_process_seconds),
      rss: Number(node.annotations?.memory_rss_bytes),
      timestamp: node.timestamp || "",
    }))
    .filter((sample) => sample.sampleKind && !Number.isNaN(sample.cpu))
    .sort((left, right) => left.timestamp.localeCompare(right.timestamp));
  const start = samples.find((sample) => sample.sampleKind === "start");
  const end = [...samples].reverse().find((sample) => sample.sampleKind === "end");
  if (!start || !end) {
    return null;
  }
  return {
    cpuSeconds: Math.max(0, end.cpu - start.cpu),
    rssDeltaBytes: Number.isNaN(end.rss) || Number.isNaN(start.rss) ? null : end.rss - start.rss,
    endRssBytes: Number.isNaN(end.rss) ? null : end.rss,
  };
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

function traceNodePalette(state: string | null | undefined) {
  return traceStatePalette[state || "unknown"] || traceStatePalette.unknown;
}

function timestampMs(value: string | null | undefined) {
  if (!value) {
    return null;
  }
  const parsed = new Date(value).getTime();
  return Number.isNaN(parsed) ? null : parsed;
}

function traceNodeOffset(node: StudioTracePathNode, path: StudioTracePathResponse) {
  const start = timestampMs(path.summary.started_at);
  const current = timestampMs(node.started_at);
  const duration = path.summary.duration_ms || 0;
  if (start === null || current === null || duration <= 0) {
    return 0;
  }
  return Math.max(0, Math.min(92, ((current - start) / duration) * 100));
}

function traceNodeWidth(node: StudioTracePathNode, path: StudioTracePathResponse) {
  const duration = path.summary.duration_ms || 0;
  const nodeDuration = node.duration_ms || 0;
  if (duration <= 0 || nodeDuration <= 0) {
    return 8;
  }
  return Math.max(8, Math.min(100, (nodeDuration / duration) * 100));
}

function traceNodeKindRank(kind: StudioTracePathNode["kind"]) {
  if (kind === "task") {
    return 0;
  }
  if (kind === "task_attempt") {
    return 1;
  }
  if (kind === "stage") {
    return 2;
  }
  if (kind === "span") {
    return 3;
  }
  if (kind === "event") {
    return 4;
  }
  if (kind === "dlq_record") {
    return 5;
  }
  return 6;
}

function orderTracePathNodes(nodes: StudioTracePathNode[], edges: StudioTracePathResponse["edges"]) {
  const indexById = new Map(nodes.map((node, index) => [node.id, index]));
  const outgoing = new Map<string, string[]>();
  const incomingCount = new Map(nodes.map((node) => [node.id, 0]));

  edges.forEach((edge) => {
    if (!indexById.has(edge.source) || !indexById.has(edge.target)) {
      return;
    }
    outgoing.set(edge.source, [...(outgoing.get(edge.source) || []), edge.target]);
    incomingCount.set(edge.target, (incomingCount.get(edge.target) || 0) + 1);
  });

  const queue = nodes
    .filter((node) => (incomingCount.get(node.id) || 0) === 0)
    .sort((left, right) => traceNodeKindRank(left.kind) - traceNodeKindRank(right.kind) || indexById.get(left.id)! - indexById.get(right.id)!);
  const ordered: StudioTracePathNode[] = [];
  const seen = new Set<string>();

  while (queue.length) {
    const current = queue.shift()!;
    if (seen.has(current.id)) {
      continue;
    }
    seen.add(current.id);
    ordered.push(current);
    (outgoing.get(current.id) || []).forEach((targetId) => {
      const nextCount = (incomingCount.get(targetId) || 0) - 1;
      incomingCount.set(targetId, nextCount);
      if (nextCount === 0) {
        const target = nodes[indexById.get(targetId)!];
        queue.push(target);
        queue.sort(
          (left, right) =>
            traceNodeKindRank(left.kind) - traceNodeKindRank(right.kind) || indexById.get(left.id)! - indexById.get(right.id)!,
        );
      }
    });
  }

  nodes.forEach((node) => {
    if (!seen.has(node.id)) {
      ordered.push(node);
    }
  });
  return ordered;
}

function preferredTracePathNodeId(path: StudioTracePathResponse) {
  const orderedNodes = orderTracePathNodes(path.nodes, path.edges);
  return orderedNodes.find((node) => node.span_id || node.trace_id)?.id || orderedNodes[0]?.id || null;
}

function TracePathExplorer({
  tracePath,
  selectedNode,
  onSelectNode,
  onSelectSpan,
  onFilterLogs,
}: {
  tracePath: StudioTracePathResponse;
  selectedNode: StudioTracePathNode | null;
  onSelectNode: (node: StudioTracePathNode) => void;
  onSelectSpan: (span: StudioTraceSpan) => void;
  onFilterLogs: (traceId: string) => void;
}) {
  const displayNodes = orderTracePathNodes(tracePath.nodes, tracePath.edges);
  const activeNode = selectedNode || displayNodes[0] || null;
  const relatedSpans = activeNode
    ? tracePath.spans.filter((span) => span.span_id === activeNode.span_id || span.trace_id === activeNode.trace_id)
    : [];

  return (
    <div className="studio-stack-md">
      <div className="studio-metrics-grid studio-metrics-grid--4">
        <MetricCard label="Trace Status" value={tracePath.summary.status || "unknown"} />
        <MetricCard label="Path Duration" value={formatDuration(tracePath.summary.duration_ms ?? null)} />
        <MetricCard label="Spans" value={String(tracePath.summary.span_count)} />
        <MetricCard label="DLQ Evidence" value={String(tracePath.summary.dlq_count)} />
      </div>

      <div className="studio-stack-sm">
        {displayNodes.map((node) => {
          const palette = traceNodePalette(node.state);
          const selected = activeNode?.id === node.id;
          return (
            <button
              type="button"
              key={node.id}
              onClick={() => onSelectNode(node)}
              className="studio-subcard"
              style={{
                display: "grid",
                gridTemplateColumns: "minmax(150px, 0.7fr) minmax(180px, 1fr) auto",
                alignItems: "center",
                gap: 12,
                padding: 12,
                borderRadius: 8,
                textAlign: "left",
                border: `1px solid ${selected ? palette.border : "var(--studio-border)"}`,
                background: selected ? palette.background : "var(--studio-surface)",
                color: "var(--studio-text)",
                cursor: "pointer",
              }}
            >
              <span style={{ display: "grid", gap: 4, minWidth: 0 }}>
                <strong style={{ fontSize: 13, overflowWrap: "anywhere" }}>{node.label}</strong>
                <span className="studio-inline-meta">
                  {node.kind}
                  {node.queue_name ? ` · ${node.queue_name}` : ""}
                </span>
              </span>
              <span
                aria-hidden="true"
                style={{
                  position: "relative",
                  height: 12,
                  borderRadius: 999,
                  background: "rgba(34, 43, 50, 0.08)",
                  overflow: "hidden",
                }}
              >
                <span
                  style={{
                    position: "absolute",
                    left: `${traceNodeOffset(node, tracePath)}%`,
                    width: `${traceNodeWidth(node, tracePath)}%`,
                    top: 0,
                    bottom: 0,
                    borderRadius: 999,
                    background: palette.border,
                  }}
                />
              </span>
              <span className="studio-inline-meta" style={{ justifySelf: "end" }}>
                {formatDuration(node.duration_ms ?? null)}
              </span>
            </button>
          );
        })}
      </div>

      {activeNode ? (
        <div className="studio-subcard" style={{ borderRadius: 8, padding: 14, display: "grid", gap: 12 }}>
          <div className="studio-list-card__top">
            <strong>{activeNode.label}</strong>
            <span className="studio-inline-meta">{activeNode.state || "unknown"}</span>
          </div>
          <dl style={{ margin: 0, display: "grid", gap: 8, fontSize: 13 }}>
            <MetadataRow label="Queue" value={activeNode.queue_name || "none"} />
            <MetadataRow label="Stage" value={activeNode.stage || "none"} />
            <MetadataRow label="Trace id" value={activeNode.trace_id || "none"} />
            <MetadataRow label="Span id" value={activeNode.span_id || "none"} />
            <MetadataRow label="Started" value={formatTimestamp(activeNode.started_at || null)} />
            <MetadataRow label="Duration" value={formatDuration(activeNode.duration_ms ?? null)} />
          </dl>
          {activeNode.trace_id ? (
            <div className="studio-action-row">
              <button type="button" onClick={() => onFilterLogs(activeNode.trace_id || "")} style={secondaryButtonStyle}>
                Filter Logs
              </button>
            </div>
          ) : null}
          {relatedSpans.length ? (
            <div className="studio-stack-sm">
              <strong style={{ fontSize: 13 }}>Related Spans</strong>
              {relatedSpans.map((span) => (
                <div
                  key={`${span.trace_id}-${span.span_id}`}
                  className="studio-subcard"
                  style={{ borderRadius: 8, padding: 10, display: "flex", justifyContent: "space-between", gap: 12 }}
                >
                  <span style={{ display: "grid", gap: 4 }}>
                    <strong style={{ fontSize: 13 }}>{span.name}</strong>
                    <span style={mutedTextStyle}>
                      {span.service || span.source || "unknown"} · {formatDuration(span.duration_ms ?? null)}
                    </span>
                  </span>
                  <button type="button" onClick={() => onSelectSpan(span)} style={secondaryButtonStyle}>
                    View Span
                  </button>
                </div>
              ))}
            </div>
          ) : null}
          <div className="studio-stack-sm">
            <strong style={{ fontSize: 13 }}>Evidence</strong>
            {activeNode.evidence.map((item, index) => (
              <div key={`${item.source}-${item.source_id}-${index}`} style={{ display: "grid", gap: 2 }}>
                <span className="studio-inline-meta">
                  {item.source} · {item.label}
                  {item.timestamp ? ` · ${formatTimestamp(item.timestamp)}` : ""}
                </span>
              </div>
            ))}
          </div>
        </div>
      ) : null}

      <div className="studio-action-row">
        {tracePath.summary.trace_ids.map((traceId) => (
          <span key={traceId} className="studio-inline-meta">
            trace_id={traceId}
          </span>
        ))}
        {tracePath.log_metadata.configured ? (
          <span className="studio-inline-meta">logs={tracePath.log_metadata.provider || "configured"}</span>
        ) : null}
      </div>
    </div>
  );
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
  const [taskMetrics, setTaskMetrics] = useState<StudioMetricsResponse | null>(null);
  const [taskMetricsLoading, setTaskMetricsLoading] = useState(false);
  const [taskMetricsError, setTaskMetricsError] = useState<string | null>(null);
  const [taskTracePath, setTaskTracePath] = useState<StudioTracePathResponse | null>(null);
  const [taskTracePathLoading, setTaskTracePathLoading] = useState(false);
  const [taskTracePathError, setTaskTracePathError] = useState<string | null>(null);
  const [selectedTracePathNodeId, setSelectedTracePathNodeId] = useState<string | null>(null);
  const [selectedTraceSpan, setSelectedTraceSpan] = useState<StudioTraceSpan | null>(null);
  const [taskLogQuery, setTaskLogQuery] = useState("");
  const [taskLogLevel, setTaskLogLevel] = useState("");
  const [taskLogSource, setTaskLogSource] = useState("");
  const [taskLogLimit, setTaskLogLimit] = useState("50");
  const [taskLogWindowMode, setTaskLogWindowMode] = useState<TaskLogWindowMode>("auto");
  const [taskLogManualFrom, setTaskLogManualFrom] = useState("");
  const [taskLogManualTo, setTaskLogManualTo] = useState("");
  const [taskLogAutoNow, setTaskLogAutoNow] = useState(() => new Date().toISOString());
  const [taskMetricWindowMode, setTaskMetricWindowMode] = useState<TaskLogWindowMode>("auto");
  const [taskMetricManualFrom, setTaskMetricManualFrom] = useState("");
  const [taskMetricManualTo, setTaskMetricManualTo] = useState("");
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
    setTaskMetricWindowMode("auto");
    setTaskMetricManualFrom("");
    setTaskMetricManualTo("");
    if (!taskDetail) {
      setTaskTimeline(null);
      setTaskLogs(null);
      setTaskLogsError(null);
      setTaskMetrics(null);
      setTaskMetricsError(null);
      setTaskTracePath(null);
      setTaskTracePathError(null);
      setSelectedTracePathNodeId(null);
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
  const quickTaskMetricWindow = resolveQuickTaskLogWindow(taskMetricWindowMode);
  const activeTaskMetricFrom =
    taskMetricWindowMode === "manual"
      ? localDateTimeToIso(taskMetricManualFrom)
      : quickTaskMetricWindow?.from || derivedTaskLogWindow?.from || "";
  const activeTaskMetricTo =
    taskMetricWindowMode === "manual"
      ? localDateTimeToIso(taskMetricManualTo)
      : quickTaskMetricWindow?.to || derivedTaskLogWindow?.to || "";

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
    if (taskDetail.service.metrics_config) {
      void loadTaskMetrics(taskDetail.service_id, taskDetail.task_id, getTaskMetricWindowForMode("auto", "", ""));
    } else {
      setTaskMetrics(null);
      setTaskMetricsError("No metrics provider configured for this service.");
    }
    void loadTaskTracePath(taskDetail.service_id, taskDetail.task_id);
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

  function getTaskMetricWindowForMode(mode: TaskLogWindowMode, manualFrom: string, manualTo: string) {
    const quickWindow = resolveQuickTaskLogWindow(mode);
    if (mode === "manual") {
      return {
        from: localDateTimeToIso(manualFrom),
        to: localDateTimeToIso(manualTo),
      };
    }
    if (quickWindow) {
      return quickWindow;
    }
    const window = taskDetail ? deriveTaskLogWindow(taskDetail, taskTimeline, new Date().toISOString()) : null;
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
    queryOverride?: string,
  ) {
    setTaskLogsLoading(true);
    setTaskLogsError(null);
    try {
      const payload = await fetchTaskLogs(targetServiceId, targetTaskId, {
        query: queryOverride ?? taskLogQuery,
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

  function applyTraceLogFilter(traceId: string) {
    setTaskLogQuery(traceId);
    void loadTaskLogs(
      taskDetail?.service_id || "",
      taskDetail?.task_id || "",
      taskDetail?.task_ref.correlation_id || null,
      getTaskLogWindow(),
      traceId,
    );
  }

  async function loadTaskMetrics(
    targetServiceId: string,
    targetTaskId: string,
    window: { from?: string; to?: string } = {},
  ) {
    setTaskMetricsLoading(true);
    setTaskMetricsError(null);
    try {
      const payload = await fetchTaskMetrics(targetServiceId, targetTaskId, window);
      setTaskMetrics(payload);
    } catch (fetchError) {
      setTaskMetrics(null);
      setTaskMetricsError(fetchError instanceof Error ? fetchError.message : "Unable to load task metrics.");
    } finally {
      setTaskMetricsLoading(false);
    }
  }

  async function loadTaskTracePath(targetServiceId: string, targetTaskId: string) {
    setTaskTracePathLoading(true);
    setTaskTracePathError(null);
    try {
      const payload = await fetchTaskTracePath(targetServiceId, targetTaskId);
      setTaskTracePath(payload);
      setSelectedTracePathNodeId((current) => current || preferredTracePathNodeId(payload));
    } catch (fetchError) {
      setTaskTracePath(null);
      setTaskTracePathError(fetchError instanceof Error ? fetchError.message : "Unable to load task trace path.");
    } finally {
      setTaskTracePathLoading(false);
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
  const resourceDelta = extractTaskResourceDelta(taskDetail);
  const filteredTaskLogs = (taskLogs?.items || []).filter((item) =>
    isInTimeWindow(item.timestamp, { from: activeTaskLogFrom, to: activeTaskLogTo }),
  );
  const taskLogSourceOptions = Array.from(new Set((taskLogs?.items || []).map((item) => item.source).filter(Boolean))).sort();
  const selectedTracePathNode =
    taskTracePath?.nodes.find((node) => node.id === selectedTracePathNodeId) || taskTracePath?.nodes[0] || null;

  return (
    <>
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

                <SectionCard
                  title="Task Trace"
                  action={
                    <button
                      type="button"
                      onClick={() => void loadTaskTracePath(taskDetail.service_id, taskDetail.task_id)}
                      style={secondaryButtonStyle}
                    >
                      <StudioIcon name="refresh" />
                      Reload Trace
                    </button>
                  }
                >
                  {taskTracePathLoading ? <p style={mutedTextStyle}>Loading task trace...</p> : null}
                  {taskTracePathError ? <p style={{ ...mutedTextStyle, color: "var(--studio-danger)" }}>{taskTracePathError}</p> : null}
                  {taskTracePath?.warnings.length ? <p style={mutedTextStyle}>{taskTracePath.warnings.join(" ")}</p> : null}
                  {!taskTracePathLoading && !taskTracePathError && taskTracePath && !taskTracePath.nodes.length ? (
                    <p style={mutedTextStyle}>No task trace path evidence was found for this task.</p>
                  ) : null}
                  {taskTracePath?.nodes.length ? (
                    <TracePathExplorer
                      tracePath={taskTracePath}
                      selectedNode={selectedTracePathNode}
                      onSelectNode={(node) => setSelectedTracePathNodeId(node.id)}
                      onSelectSpan={setSelectedTraceSpan}
                      onFilterLogs={applyTraceLogFilter}
                    />
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

                <SectionCard title="Task Kubernetes Metrics" action={
                  <button
                    type="button"
                    onClick={() =>
                      void loadTaskMetrics(
                        taskDetail.service_id,
                        taskDetail.task_id,
                        getTaskMetricWindowForMode(taskMetricWindowMode, taskMetricManualFrom, taskMetricManualTo),
                      )
                    }
                    style={secondaryButtonStyle}
                  >
                    <StudioIcon name="refresh" />
                    Reload Metrics
                  </button>
                }>
                  <NoticeBanner>
                    Task metrics are pod/container metrics over the task lifecycle window. They are approximate for long-running workers that process multiple tasks.
                  </NoticeBanner>
                  <div className="studio-log-filter-grid studio-log-window-grid" style={{ marginTop: 12 }}>
                    <label className="studio-filter-field">
                      <span>Metrics Window</span>
                      <select
                        aria-label="Task metrics window mode"
                        value={taskMetricWindowMode}
                        onChange={(event) => {
                          const nextMode = event.target.value as TaskLogWindowMode;
                          let nextManualFrom = taskMetricManualFrom;
                          let nextManualTo = taskMetricManualTo;
                          if (nextMode === "manual") {
                            nextManualFrom = isoToLocalDateTime(activeTaskMetricFrom);
                            nextManualTo = isoToLocalDateTime(activeTaskMetricTo);
                            setTaskMetricManualFrom(nextManualFrom);
                            setTaskMetricManualTo(nextManualTo);
                          }
                          setTaskMetricWindowMode(nextMode);
                          if (taskDetail.service.metrics_config) {
                            void loadTaskMetrics(
                              taskDetail.service_id,
                              taskDetail.task_id,
                              getTaskMetricWindowForMode(nextMode, nextManualFrom, nextManualTo),
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
                        aria-label="Task metrics from"
                        type="datetime-local"
                        value={
                          taskMetricWindowMode === "manual"
                            ? taskMetricManualFrom
                            : isoToLocalDateTime(activeTaskMetricFrom)
                        }
                        onChange={(event) => {
                          const nextFrom = event.target.value;
                          setTaskMetricManualFrom(nextFrom);
                          if (taskDetail.service.metrics_config) {
                            void loadTaskMetrics(
                              taskDetail.service_id,
                              taskDetail.task_id,
                              getTaskMetricWindowForMode("manual", nextFrom, taskMetricManualTo),
                            );
                          }
                        }}
                        disabled={taskMetricWindowMode !== "manual"}
                        style={inputStyle}
                      />
                    </label>
                    <label className="studio-filter-field">
                      <span>To</span>
                      <input
                        aria-label="Task metrics to"
                        type="datetime-local"
                        value={
                          taskMetricWindowMode === "manual"
                            ? taskMetricManualTo
                            : isoToLocalDateTime(activeTaskMetricTo)
                        }
                        onChange={(event) => {
                          const nextTo = event.target.value;
                          setTaskMetricManualTo(nextTo);
                          if (taskDetail.service.metrics_config) {
                            void loadTaskMetrics(
                              taskDetail.service_id,
                              taskDetail.task_id,
                              getTaskMetricWindowForMode("manual", taskMetricManualFrom, nextTo),
                            );
                          }
                        }}
                        disabled={taskMetricWindowMode !== "manual"}
                        style={inputStyle}
                      />
                    </label>
                  </div>
                  <p style={mutedTextStyle}>
                    {describeTaskMetricWindow(taskMetricWindowMode, activeTaskMetricFrom, activeTaskMetricTo, derivedTaskLogWindow?.warning)}
                  </p>
                  {taskMetrics?.warnings.length ? (
                    <p style={mutedTextStyle}>{taskMetrics.warnings.join(" ")}</p>
                  ) : null}
                  {taskMetricsLoading ? <p style={mutedTextStyle}>Loading task metrics...</p> : null}
                  {taskMetricsError ? <p style={{ ...mutedTextStyle, color: "var(--studio-danger)" }}>{taskMetricsError}</p> : null}
                  {!taskMetricsLoading && !taskMetricsError && taskMetrics && !taskMetrics.series.length ? (
                    <p style={mutedTextStyle}>No task metrics matched the current window.</p>
                  ) : null}
                  {taskMetrics ? (
                    <div className="studio-metrics-grid studio-metrics-grid--4">
                      {[
                        "cpu_usage",
                        "memory_usage",
                        "restarts",
                        "oom_killed",
                        "pod_phase",
                        "readiness",
                        "network_receive",
                        "network_transmit",
                      ].map((metric) => (
                        <div key={metric} className="studio-subcard" style={{ borderRadius: 14, padding: 14 }}>
                          <span className="studio-inline-meta">{metricLabel(metric)}</span>
                          <strong style={{ display: "block", marginTop: 6 }}>{metricLatestValue(taskMetrics, metric)}</strong>
                        </div>
                      ))}
                    </div>
                  ) : null}
                </SectionCard>

                <SectionCard
                  title="Exact Task Resources"
                  subtitle="Relayna task resource samples are observations, not Prometheus task labels."
                >
                  {resourceDelta ? (
                    <div className="studio-metrics-grid studio-metrics-grid--3">
                      <MetricCard label="CPU Process Delta" value={`${resourceDelta.cpuSeconds.toFixed(3)}s`} />
                      <MetricCard
                        label="RSS Delta"
                        value={
                          resourceDelta.rssDeltaBytes === null
                            ? "n/a"
                            : formatMetricValue(resourceDelta.rssDeltaBytes, "bytes")
                        }
                      />
                      <MetricCard
                        label="End RSS"
                        value={
                          resourceDelta.endRssBytes === null
                            ? "n/a"
                            : formatMetricValue(resourceDelta.endRssBytes, "bytes")
                        }
                      />
                    </div>
                  ) : (
                    <p style={mutedTextStyle}>
                      No exact Relayna task resource samples were found. Use the Kubernetes task-window metrics above as
                      an approximate fallback.
                    </p>
                  )}
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

      {selectedTraceSpan ? (
        <div
          className="studio-dialog-backdrop"
          role="presentation"
          onClick={() => setSelectedTraceSpan(null)}
        >
          <section
            className="studio-dialog"
            role="dialog"
            aria-modal="true"
            aria-labelledby="studio-span-detail-title"
            style={{ width: "min(760px, 100%)" }}
            onClick={(event) => event.stopPropagation()}
          >
            <h2 id="studio-span-detail-title" className="studio-dialog__title">
              Span Details
            </h2>
            <div className="studio-dialog__body">
              <p>
                <strong>{selectedTraceSpan.name}</strong>
              </p>
              <dl style={{ margin: 0, display: "grid", gap: 10, fontSize: 13 }}>
                <MetadataRow label="Trace id" value={selectedTraceSpan.trace_id} />
                <MetadataRow label="Span id" value={selectedTraceSpan.span_id} />
                <MetadataRow label="Parent span" value={selectedTraceSpan.parent_span_id || "none"} />
                <MetadataRow label="Kind" value={selectedTraceSpan.kind || "unknown"} />
                <MetadataRow label="Service" value={selectedTraceSpan.service || "unknown"} />
                <MetadataRow label="Source" value={selectedTraceSpan.source || "unknown"} />
                <MetadataRow label="Started" value={formatTimestamp(selectedTraceSpan.start_time || null)} />
                <MetadataRow label="Ended" value={formatTimestamp(selectedTraceSpan.end_time || null)} />
                <MetadataRow label="Duration" value={formatDuration(selectedTraceSpan.duration_ms ?? null)} />
              </dl>
              {selectedTraceSpan.backend_url ? (
                <>
                  <strong style={{ color: "var(--studio-text)" }}>Backend query URL</strong>
                  <InlineCodeBox value={selectedTraceSpan.backend_url} minHeight={64} />
                </>
              ) : null}
              <strong style={{ color: "var(--studio-text)" }}>Attributes</strong>
              <InlineCodeBox value={JSON.stringify(selectedTraceSpan.attributes || {}, null, 2)} minHeight={180} />
              <div className="studio-dialog__actions">
                <button type="button" onClick={() => setSelectedTraceSpan(null)} style={secondaryButtonStyle}>
                  Close
                </button>
              </div>
            </div>
          </section>
        </div>
      ) : null}
    </>
  );
}

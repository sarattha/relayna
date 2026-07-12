import { afterEach, describe, expect, it, vi } from "vitest";

import {
  describeWindow,
  emptyLogResponse,
  emptyMetricsResponse,
  eventTimestamp,
  fetchServicePods,
  formatChartOffset,
  formatChartStartTime,
  formatMetricValue as formatServiceMetricValue,
  hasLoadedPodsForService,
  isInWindow,
  isoToLocalDateTime as serviceIsoToLocal,
  latestTimestamp,
  localDateTimeToIso as serviceLocalToIso,
  mergeLogResponses,
  mergeMetricResponses,
  metricLabel as serviceMetricLabel,
  metricLatestValue as serviceMetricLatestValue,
  metricSeriesFor,
  metricStepSeconds,
  normalizeSelectedPods,
  podMetricGroups,
  podMetricLineColor,
  resolveWindow,
  selectedPodLabel,
  seriesLabel,
  seriesPodLabel,
  serviceMetricSummaryGroups,
  servicePodSource,
  type ServicePod,
} from "./pages/ServiceDetailPage";
import { formatBatchWait } from "./pages/FailedTasksPage";
import {
  deriveTaskLogWindow,
  describeTaskLogWindow,
  describeTaskMetricWindow,
  extractRecordTimestamp,
  extractTaskResourceDelta,
  formatMetricValue as formatTaskMetricValue,
  isInTimeWindow,
  isoToLocalDateTime as taskIsoToLocal,
  localDateTimeToIso as taskLocalToIso,
  metricLabel as taskMetricLabel,
  metricLatestValue as taskMetricLatestValue,
  normalizeStatusValue,
  orderTracePathNodes,
  parseTimestamp,
  preferredTracePathNodeId,
  resolveQuickTaskLogWindow,
  statusFromTimelineEvent,
  timestampMs,
  traceNodeKindRank,
  traceNodeOffset,
  traceNodePalette,
  traceNodeTimingLabel,
  traceNodeWidth,
} from "./pages/TaskDetailPage";
import type {
  ServiceRecord,
  StudioControlPlaneEvent,
  StudioEventListResponse,
  StudioMetricSeries,
  StudioMetricsResponse,
  StudioTaskDetail,
  StudioTracePathNode,
  StudioTracePathResponse,
} from "./types";

function metricResponse(series: StudioMetricsResponse["series"] = []): StudioMetricsResponse {
  return {
    service_id: "payments-api",
    task_id: null,
    from: "2026-01-01T00:00:00Z",
    to: "2026-01-01T01:00:00Z",
    step_seconds: 30,
    approximate: false,
    warnings: [],
    series,
  };
}

function serviceRecord(): ServiceRecord {
  return {
    service_id: "payments-api",
    name: "Payments",
    base_url: "https://payments.example.test",
    environment: "prod",
    tags: [],
    auth_mode: "internal_network",
    status: "healthy",
    capabilities: null,
    last_seen_at: null,
    log_config: null,
    metrics_config: null,
    trace_config: null,
    health: null,
  };
}

function traceNode(overrides: Partial<StudioTracePathNode> = {}): StudioTracePathNode {
  return {
    id: "node-1",
    kind: "task",
    label: "Task",
    task_id: "task-1",
    state: "running",
    evidence: [],
    ...overrides,
  };
}

function tracePath(overrides: Partial<StudioTracePathResponse> = {}): StudioTracePathResponse {
  return {
    service_id: "payments-api",
    task_id: "task-1",
    summary: {
      status: "running",
      started_at: "2026-01-01T00:00:00Z",
      ended_at: "2026-01-01T00:00:10Z",
      duration_ms: 10_000,
      graph_completeness: "complete",
      trace_ids: [], node_count: 0, edge_count: 0, span_count: 0, event_count: 0, dlq_count: 0, live_state_counts: {},
    },
    nodes: [], edges: [], spans: [], events: [], dlq_messages: [],
    log_metadata: { configured: false, task_id: "task-1" },
    warnings: [],
    ...overrides,
  };
}

function timelineEvent(overrides: Partial<StudioControlPlaneEvent> = {}): StudioControlPlaneEvent {
  return {
    service_id: "payments-api",
    ingest_method: "pull",
    ingested_at: "2026-01-01T00:00:00Z",
    dedupe_key: "event-1",
    out_of_order: false,
    task_id: "task-1",
    event_type: "task.running",
    source_kind: "status",
    timestamp: "2026-01-01T00:00:00Z",
    payload: {},
    ...overrides,
  };
}

function taskDetail(overrides: Partial<StudioTaskDetail> = {}): StudioTaskDetail {
  return {
    service: serviceRecord(),
    service_id: "payments-api",
    task_id: "task-1",
    task_ref: { service_id: "payments-api", task_id: "task-1", parent_refs: [], child_refs: [] },
    latest_status: null,
    history: { service_id: "payments-api", task_id: "task-1", count: 0, events: [] },
    execution_graph: null,
    dlq_messages: { service_id: "payments-api", items: [], next_cursor: null },
    joined_refs: [],
    join_warnings: [],
    errors: [],
    ...overrides,
  };
}

describe("service detail helpers", () => {
  it("formats every failed-task email batching scale", () => {
    expect(formatBatchWait(0)).toBe("0 sec");
    expect(formatBatchWait(30)).toBe("30 sec");
    expect(formatBatchWait(120)).toBe("2 min");
    expect(formatBatchWait(7200)).toBe("2 hr");
    expect(formatBatchWait(172800)).toBe("2 days");
  });
  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it("normalizes timestamps and every supported quick window", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-01-02T00:00:00Z"));
    expect(latestTimestamp()).toBeNull();
    expect(latestTimestamp(null, "2026-01-01T00:00:00Z", "2026-01-02T00:00:00Z")).toBe("2026-01-02T00:00:00Z");
    expect(serviceLocalToIso("")).toBe("");
    expect(serviceLocalToIso("invalid")).toBe("");
    expect(serviceLocalToIso("2026-01-01T00:00")).toMatch(/^2025-12-31T|^2026-01-01T/);
    expect(serviceIsoToLocal("")).toBe("");
    expect(serviceIsoToLocal("invalid")).toBe("");
    expect(serviceIsoToLocal("2026-01-01T00:00:00Z")).toHaveLength(16);
    expect(resolveWindow("auto", "", "")).toEqual({ from: "", to: "" });
    expect(resolveWindow("manual", "2026-01-01T00:00", "2026-01-01T01:00").from).toMatch(/^2025-12-31T|^2026-01-01T/);
    for (const mode of ["15m", "1h", "12h", "24h", "1w", "1mo"] as const) {
      expect(resolveWindow(mode, "", "").to).toBe("2026-01-02T00:00:00.000Z");
    }
    expect(describeWindow("auto", "", "")).toBe("Auto window: unbounded to unbounded.");
    expect(describeWindow("manual", "", "")).toContain("Manual window");
    for (const mode of ["15m", "1h", "12h", "24h", "1w", "1mo"] as const) {
      expect(describeWindow(mode, "", "")).toContain("Quick window");
    }
    expect(describeWindow("15m", "2026-01-01T00:00:00Z", "2026-01-01T00:15:00Z")).toContain("15 minutes");
  });

  it("filters windows and formats all metric units", () => {
    expect(eventTimestamp({ timestamp: "time", ingested_at: "ingested" })).toBe("time");
    expect(eventTimestamp({ ingested_at: "ingested" })).toBe("ingested");
    expect(eventTimestamp({})).toBe("");
    const window = { from: "2026-01-01T01:00:00Z", to: "2026-01-01T02:00:00Z" };
    expect(isInWindow("", window)).toBe(true);
    expect(isInWindow("invalid", window)).toBe(true);
    expect(isInWindow("2026-01-01T00:00:00Z", window)).toBe(false);
    expect(isInWindow("2026-01-01T03:00:00Z", window)).toBe(false);
    expect(isInWindow("2026-01-01T01:30:00Z", window)).toBe(true);
    expect(isInWindow("2026-01-01T01:30:00Z", { from: "invalid", to: "invalid" })).toBe(true);
    expect(serviceMetricLabel("task_duration_p95")).toBe("Task Duration P95");
    expect(formatServiceMetricValue(null, "bytes")).toBe("n/a");
    expect(formatServiceMetricValue(undefined, "bytes")).toBe("n/a");
    expect(formatServiceMetricValue(Number.NaN, "bytes")).toBe("n/a");
    expect(formatServiceMetricValue(2 * 1024 ** 3, "bytes")).toBe("2.00 GiB");
    expect(formatServiceMetricValue(-2 * 1024 ** 2, "bytes")).toBe("-2.00 MiB");
    expect(formatServiceMetricValue(512, "bytes")).toBe("512 B");
    expect(formatServiceMetricValue(2048, "bytes_per_second")).toBe("2.00 KiB/s");
    expect(formatServiceMetricValue(0.25, "cores")).toBe("0.250 cores");
    expect(formatServiceMetricValue(2, "per_second")).toBe("2.000/s");
    expect(formatServiceMetricValue(2, "seconds")).toBe("2.000s");
    expect(formatServiceMetricValue(0, "unix_seconds")).toBe("n/a");
    expect(formatServiceMetricValue(1_767_225_600, "unix_seconds")).not.toBe("n/a");
    expect(formatServiceMetricValue(2, "count")).toBe("2");
    expect(formatServiceMetricValue(2.5, "count")).toBe("2.50");
  });

  it("aggregates metric series and resolves pod labels and steps", () => {
    const series: StudioMetricSeries[] = [
      { metric: "cpu_usage" as const, unit: "cores", labels: { pod: "pod-a" }, points: [{ timestamp: "2026-01-01T00:00:00Z", value: 1 }] },
      { metric: "cpu_usage" as const, unit: "cores", labels: { pod_name: "pod-b" }, points: [{ timestamp: "2026-01-01T00:00:00Z", value: 2 }] },
      { metric: "cpu_usage" as const, unit: "cores", labels: {}, points: [{ timestamp: "2026-01-01T00:00:00Z", value: null }] },
      { metric: "memory_usage" as const, unit: "bytes", labels: {}, points: [] },
    ];
    const metrics = metricResponse(series);
    expect(serviceMetricLatestValue(null, "cpu_usage")).toBe("n/a");
    expect(serviceMetricLatestValue(metrics, "cpu_usage")).toBe("3.000 cores");
    expect(metricSeriesFor(metrics, "cpu_usage")).toHaveLength(3);
    expect(metricSeriesFor(null, "cpu_usage")).toEqual([]);
    expect(seriesPodLabel(series[0], "custom")).toBe("pod-a");
    expect(seriesPodLabel({ ...series[0], labels: { custom: "chosen" } }, "custom")).toBe("chosen");
    expect(seriesPodLabel(series[1])).toBe("pod-b");
    expect(seriesPodLabel({ ...series[0], labels: { kubernetes_pod_name: "pod-c" } })).toBe("pod-c");
    expect(seriesPodLabel({ ...series[0], labels: {} })).toBe("service");
    expect(seriesLabel({ ...series[0], metric: "pod_phase", labels: { pod: "pod-a", phase: "Running" } })).toBe("pod-a · Running");
    expect(seriesLabel(series[0])).toBe("pod-a");
    expect(metricStepSeconds("15m")).toBe(30);
    expect(metricStepSeconds("1h")).toBe(60);
    expect(metricStepSeconds("12h")).toBe(300);
    expect(metricStepSeconds("24h")).toBe(600);
    expect(metricStepSeconds("1w")).toBe(3600);
    expect(metricStepSeconds("1mo")).toBe(3600);
    expect(metricStepSeconds("auto")).toBeUndefined();
    expect(podMetricGroups).toContain("cpu_usage");
    expect(serviceMetricSummaryGroups).toContain("active_tasks");
  });

  it("normalizes pods, combines responses, and formats chart offsets", async () => {
    expect(servicePodSource({ name: "a", namespace: "n", labels: { app: "app" } })).toBe("app");
    expect(servicePodSource({ name: "a", namespace: "n", labels: { component: "component" } })).toBe("component");
    expect(servicePodSource({ name: "a", namespace: "n", labels: { container: "container" } })).toBe("container");
    expect(servicePodSource({ name: "a", namespace: "n", labels: { label_team: "team" } })).toBe("team");
    expect(servicePodSource({ name: "a", namespace: "n", labels: { label_empty: "" } })).toBe("unknown");
    expect(servicePodSource({ name: "a", namespace: "n" })).toBe("unknown");
    const pods: ServicePod[] = [{ name: "a", namespace: "n" }, { name: "b", namespace: "n" }];
    expect(selectedPodLabel([])).toBe("");
    expect(selectedPodLabel(["a"])).toBe("a");
    expect(selectedPodLabel(["a", "b"])).toBe("2 pods");
    expect(normalizeSelectedPods(["a"], [])).toEqual([]);
    expect(normalizeSelectedPods(["a"], pods, [])).toEqual(["a"]);
    expect(normalizeSelectedPods([], pods, [])).toEqual(["a", "b"]);
    expect(normalizeSelectedPods([], pods, pods)).toEqual([]);
    expect(normalizeSelectedPods(["a", "b"], [...pods, { name: "c", namespace: "n" }], pods)).toEqual(["a", "b", "c"]);
    expect(normalizeSelectedPods(["missing"], pods, pods)).toEqual(["a", "b"]);
    expect(hasLoadedPodsForService(null, serviceRecord())).toBe(false);
    expect(hasLoadedPodsForService({ service_id: "other", count: 1, pods }, serviceRecord())).toBe(false);
    expect(hasLoadedPodsForService({ service_id: "payments-api", count: 0, pods: [] }, serviceRecord())).toBe(false);
    expect(hasLoadedPodsForService({ service_id: "payments-api", count: 2, pods }, serviceRecord())).toBe(true);

    const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(new Response(JSON.stringify({ service_id: "service/id", count: 0, pods: [] }), { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);
    await expect(fetchServicePods("service/id")).resolves.toMatchObject({ count: 0 });
    expect(fetchMock).toHaveBeenCalledWith("/studio/services/service%2Fid/pods", undefined);

    const logs = mergeLogResponses([
      { count: 1, items: [{ service_id: "s", timestamp: "2026-01-01T00:00:00Z", source: "a", message: "old", fields: {} }], next_cursor: "a" },
      { count: 1, items: [{ service_id: "s", timestamp: "2026-01-02T00:00:00Z", source: "b", message: "new", fields: {} }], next_cursor: "b" },
    ], 1);
    expect(logs.items.map((item) => item.message)).toEqual(["new"]);
    expect(emptyLogResponse()).toEqual({ count: 0, items: [], next_cursor: null });
    expect(mergeMetricResponses([]).series).toEqual([]);
    const merged = mergeMetricResponses([
      { ...metricResponse(), warnings: ["one"], approximate: false },
      { ...metricResponse(), warnings: ["one", "two"], approximate: true },
    ]);
    expect(merged.approximate).toBe(true);
    expect(merged.warnings).toEqual(["one", "two"]);
    expect(emptyMetricsResponse(serviceRecord(), { from: "from", to: "to" })).toMatchObject({ service_id: "payments-api", from: "from", to: "to" });
    expect(podMetricLineColor(0)).toBe(podMetricLineColor(6));
    expect(formatChartStartTime(Date.parse("2026-01-01T00:00:00Z"))).toContain("Start");
    expect(formatChartOffset(-1)).toBe("+0s");
    expect(formatChartOffset(30_000)).toBe("+30s");
    expect(formatChartOffset(120_000)).toBe("+2m");
    expect(formatChartOffset(7_200_000)).toBe("+2h");
    expect(formatChartOffset(172_800_000)).toBe("+2d");
  });
});

describe("task detail helpers", () => {
  afterEach(() => vi.useRealTimers());

  it("normalizes local timestamps and describes every log window", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-01-02T00:00:00Z"));
    expect(taskIsoToLocal("")).toBe("");
    expect(taskIsoToLocal("invalid")).toBe("");
    expect(taskIsoToLocal("2026-01-01T00:00:00Z")).toHaveLength(16);
    expect(taskLocalToIso("")).toBe("");
    expect(taskLocalToIso("invalid")).toBe("");
    expect(taskLocalToIso("2026-01-01T00:00")).toMatch(/^2025-12-31T|^2026-01-01T/);
    expect(resolveQuickTaskLogWindow("auto")).toBeNull();
    expect(resolveQuickTaskLogWindow("manual")).toBeNull();
    expect(resolveQuickTaskLogWindow("15m")?.from).toBe("2026-01-01T23:45:00.000Z");
    expect(resolveQuickTaskLogWindow("1h")?.from).toBe("2026-01-01T23:00:00.000Z");
    expect(resolveQuickTaskLogWindow("24h")?.from).toBe("2026-01-01T00:00:00.000Z");
    expect(describeTaskLogWindow("auto", "", "", "warning")).toBe("warning");
    expect(describeTaskLogWindow("auto", "", "")).toContain("unbounded");
    expect(describeTaskLogWindow("manual", "", "")).toContain("Custom range");
    expect(describeTaskLogWindow("15m", "", "")).toContain("15 minutes");
    expect(describeTaskLogWindow("1h", "2026-01-01T00:00:00Z", "2026-01-01T01:00:00Z")).toContain("1 hour");
    expect(describeTaskLogWindow("24h", "", "")).toContain("24 hours");
    expect(describeTaskMetricWindow("auto", "", "")).toContain("Metrics auto window");
    expect(describeTaskMetricWindow("manual", "", "")).toContain("Custom range");
  });

  it("formats task metrics and extracts resource samples", () => {
    const window = { from: "2026-01-01T01:00:00Z", to: "2026-01-01T02:00:00Z" };
    expect(isInTimeWindow("", window)).toBe(true);
    expect(isInTimeWindow("invalid", window)).toBe(true);
    expect(isInTimeWindow("2026-01-01T00:00:00Z", window)).toBe(false);
    expect(isInTimeWindow("2026-01-01T03:00:00Z", window)).toBe(false);
    expect(isInTimeWindow("2026-01-01T01:30:00Z", { from: "invalid", to: "invalid" })).toBe(true);
    expect(taskMetricLabel("cpu_usage")).toBe("Cpu Usage");
    for (const [value, unit, expected] of [
      [null, "bytes", "n/a"], [undefined, "bytes", "n/a"], [Number.NaN, "bytes", "n/a"],
      [2 * 1024 ** 3, "bytes", "2.00 GiB"], [2 * 1024 ** 2, "bytes", "2.00 MiB"], [512, "bytes", "512 B"],
      [2048, "bytes_per_second", "2.00 KiB/s"], [0.25, "cores", "0.250 cores"], [2, "per_second", "2.000/s"],
      [2, "seconds", "2.000s"], [0, "unix_seconds", "n/a"], [2, "count", "2"], [2.5, "count", "2.50"],
    ] as const) {
      expect(formatTaskMetricValue(value, unit)).toBe(expected);
    }
    const metrics = metricResponse([
      { metric: "cpu_usage", unit: "cores", labels: {}, points: [{ timestamp: "2026-01-01T00:00:00Z", value: 1 }] },
      { metric: "cpu_usage", unit: "cores", labels: {}, points: [{ timestamp: "2026-01-01T00:00:00Z", value: null }] },
    ]);
    expect(taskMetricLatestValue(metrics, "cpu_usage")).toBe("1.000 cores");
    expect(taskMetricLatestValue(null, "cpu_usage")).toBe("n/a");
    expect(extractTaskResourceDelta(null)).toBeNull();
    expect(extractTaskResourceDelta(taskDetail())).toBeNull();
    const nodes = [
      { id: "other", kind: "resource_sample", task_id: "other", timestamp: "2026-01-01T00:00:00Z", annotations: { sample_kind: "start", cpu_process_seconds: 1 } },
      { id: "invalid", kind: "resource_sample", task_id: "task-1", timestamp: "2026-01-01T00:00:00Z", annotations: { sample_kind: "", cpu_process_seconds: "bad" } },
      { id: "start", kind: "resource_sample", task_id: "task-1", timestamp: "2026-01-01T00:00:01Z", annotations: { sample_kind: "start", cpu_process_seconds: 5, memory_rss_bytes: "bad" } },
      { id: "end", kind: "resource_sample", task_id: "task-1", timestamp: "2026-01-01T00:00:02Z", annotations: { sample_kind: "end", cpu_process_seconds: 3, memory_rss_bytes: 100 } },
    ];
    expect(extractTaskResourceDelta(taskDetail({ execution_graph: { nodes, edges: [], summary: {} } as never }))).toEqual({ cpuSeconds: 0, rssDeltaBytes: null, endRssBytes: 100 });
  });

  it("derives statuses, record timestamps, and bounded task windows", () => {
    expect(normalizeStatusValue(" RUNNING ")).toBe("running");
    expect(normalizeStatusValue(3)).toBe("");
    expect(parseTimestamp(null)).toBeNull();
    expect(parseTimestamp("invalid")).toBeNull();
    expect(parseTimestamp("2026-01-01T00:00:00Z")).toBeInstanceOf(Date);
    expect(extractRecordTimestamp(null)).toBeNull();
    expect(extractRecordTimestamp({ timestamp: "invalid", event_timestamp: "2026-01-01T00:00:00Z" })).toBe("2026-01-01T00:00:00Z");
    expect(extractRecordTimestamp({ created_at: 3, updated_at: "invalid" })).toBeNull();
    expect(statusFromTimelineEvent(timelineEvent({ payload: { status: " QUEUED " } }))).toBe("queued");
    expect(statusFromTimelineEvent(timelineEvent({ event_type: "" }))).toBe("");
    expect(statusFromTimelineEvent(timelineEvent({ event_type: "task.failed" }))).toBe("failed");

    expect(deriveTaskLogWindow(taskDetail(), null, "2026-01-02T00:00:00Z")).toMatchObject({ from: "", to: "", warning: expect.any(String) });
    const timeline: StudioEventListResponse = {
      count: 3,
      items: [
        timelineEvent({ task_id: "other", timestamp: "2025-12-31T00:00:00Z" }),
        timelineEvent({ event_type: "task.queued", timestamp: "2026-01-01T00:00:00Z" }),
        timelineEvent({ event_type: "task.completed", timestamp: "2026-01-01T00:10:00Z" }),
      ],
    };
    expect(deriveTaskLogWindow(taskDetail(), timeline, "2026-01-02T00:00:00Z")).toEqual({ from: "2026-01-01T00:00:00Z", to: "2026-01-01T00:10:00Z", warning: null });
    const historyDetail = taskDetail({
      history: {
        service_id: "payments-api", task_id: "task-1", count: 2,
        events: [{ task_id: "task-1", status: "queued", created_at: "2026-01-01T01:00:00Z" }, { task_id: "other", status: "failed" }],
      },
      execution_graph: { nodes: [], edges: [], summary: { ended_at: "2025-12-31T00:00:00Z" } } as never,
    });
    expect(deriveTaskLogWindow(historyDetail, null, "2026-01-02T00:00:00Z")).toEqual({ from: "2026-01-01T01:00:00Z", to: "2026-01-02T00:00:00Z", warning: null });
  });

  it("orders trace nodes and calculates timing, width, palette, and preferred selection", () => {
    expect(traceNodePalette("failed").color).toBe("#7a2621");
    expect(traceNodePalette("missing").color).toBe("#4b453d");
    expect(traceNodePalette(null).color).toBe("#4b453d");
    expect(timestampMs(null)).toBeNull();
    expect(timestampMs("invalid")).toBeNull();
    expect(timestampMs("2026-01-01T00:00:00Z")).toBeTypeOf("number");
    const path = tracePath();
    expect(traceNodeOffset(traceNode({ started_at: null }), path)).toBe(0);
    expect(traceNodeOffset(traceNode({ started_at: "2025-12-31T23:59:59Z" }), path)).toBe(0);
    expect(traceNodeOffset(traceNode({ started_at: "2026-01-01T00:00:20Z" }), path)).toBe(92);
    expect(traceNodeOffset(traceNode({ started_at: "2026-01-01T00:00:05Z" }), path)).toBe(50);
    expect(traceNodeWidth(traceNode({ duration_ms: null }), path)).toBe(8);
    expect(traceNodeWidth(traceNode({ duration_ms: 500 }), path)).toBe(8);
    expect(traceNodeWidth(traceNode({ duration_ms: 20_000 }), path)).toBe(100);
    expect(traceNodeTimingLabel(traceNode({ duration_ms: 1500 }))).toContain("1.5");
    expect(traceNodeTimingLabel(traceNode({ duration_ms: null, started_at: null }))).toBe("Never");
    expect(["task", "task_attempt", "stage", "span", "event", "dlq_record", "unknown"].map(traceNodeKindRank)).toEqual([0, 1, 2, 3, 4, 5, 6]);

    const nodes = [
      traceNode({ id: "stage", kind: "stage" }),
      traceNode({ id: "task", kind: "task", trace_id: "trace-1" }),
      traceNode({ id: "span", kind: "span", span_id: "span-1" }),
      traceNode({ id: "cycle", kind: "event" }),
    ];
    const edges = [
      { id: "e1", source: "task", target: "stage", kind: "contains", evidence: [] },
      { id: "e2", source: "stage", target: "span", kind: "contains", evidence: [] },
      { id: "e3", source: "missing", target: "span", kind: "invalid", evidence: [] },
      { id: "e4", source: "cycle", target: "cycle", kind: "cycle", evidence: [] },
    ];
    expect(orderTracePathNodes(nodes, edges).map((node) => node.id)).toEqual(["task", "stage", "span", "cycle"]);
    expect(preferredTracePathNodeId(tracePath({ nodes, edges }))).toBe("task");
    expect(preferredTracePathNodeId(tracePath({ nodes: [traceNode({ id: "plain", trace_id: null })], edges: [] }))).toBe("plain");
    expect(preferredTracePathNodeId(tracePath())).toBeNull();
    const sortableNodes = [
      traceNode({ id: "root-a", kind: "task" }),
      traceNode({ id: "root-b", kind: "task" }),
      traceNode({ id: "root-c", kind: "event" }),
      traceNode({ id: "child", kind: "stage" }),
    ];
    expect(
      orderTracePathNodes(sortableNodes, [
        { id: "a-child", source: "root-a", target: "child", kind: "contains", evidence: [] },
        { id: "b-child", source: "root-b", target: "child", kind: "contains", evidence: [] },
      ]).map((node) => node.id),
    ).toEqual(["root-a", "root-b", "child", "root-c"]);
  });
});

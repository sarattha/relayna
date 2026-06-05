import { startTransition, useEffect, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";

import { fetchServiceEvents, fetchServiceLogs, fetchServiceMetrics, requestJson } from "../api";
import { useStudioServices } from "../services-context";
import {
  ConfirmationDialog,
  HealthBadge,
  InlineCodeBox,
  LogSourceBadge,
  LogMessage,
  MetadataRow,
  NoticeBanner,
  SectionCard,
  StudioIcon,
  StatusBadge,
  destructiveButtonStyle,
  formatEventSummary,
  formatLogLevel,
  formatTimestamp,
  inputStyle,
  mergeControlPlaneEvent,
  mutedTextStyle,
  parseLimit,
  secondaryButtonStyle,
} from "../ui";
import type {
  ServiceEventSourceKind,
  ServiceRecord,
  StudioControlPlaneEvent,
  StudioEventListResponse,
  StudioLogListResponse,
  StudioMetricGroup,
  StudioMetricSeries,
  StudioMetricsResponse,
} from "../types";

type TimeWindowMode = "auto" | "15m" | "1h" | "12h" | "24h" | "1w" | "1mo" | "manual";
type TimeWindow = { from: string; to: string };
type ServicePod = {
  name: string;
  namespace: string;
  phase?: string | null;
  labels?: Record<string, string>;
};
type ServicePodListResponse = {
  service_id: string;
  count: number;
  pods: ServicePod[];
};
type ConfirmationRequest = {
  title: string;
  body: string;
  confirmLabel: string;
  challengeText?: string;
  challengeLabel?: string;
  onConfirm: () => Promise<void>;
};

function latestTimestamp(...values: Array<string | null | undefined>) {
  const candidates = values.filter((value): value is string => Boolean(value));
  if (!candidates.length) {
    return null;
  }
  return candidates.reduce((latest, value) => (new Date(value).getTime() > new Date(latest).getTime() ? value : latest));
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

function resolveWindow(mode: TimeWindowMode, manualFrom: string, manualTo: string) {
  if (mode === "manual") {
    return {
      from: localDateTimeToIso(manualFrom),
      to: localDateTimeToIso(manualTo),
    };
  }
  if (mode === "auto") {
    return { from: "", to: "" };
  }

  const now = Date.now();
  const durationMs =
    mode === "15m"
      ? 15 * 60 * 1000
      : mode === "1h"
        ? 60 * 60 * 1000
        : mode === "12h"
          ? 12 * 60 * 60 * 1000
          : mode === "24h"
            ? 24 * 60 * 60 * 1000
            : mode === "1w"
              ? 7 * 24 * 60 * 60 * 1000
              : 30 * 24 * 60 * 60 * 1000;
  return {
    from: new Date(now - durationMs).toISOString(),
    to: new Date(now).toISOString(),
  };
}

const emptyWindow: TimeWindow = { from: "", to: "" };

function describeWindow(mode: TimeWindowMode, from: string, to: string) {
  if (mode === "auto") {
    return "Auto window: unbounded to unbounded.";
  }
  if (mode === "manual") {
    return "Manual window is active. Use the local date and time fields; empty bounds stay unbounded.";
  }
  const label =
    mode === "15m"
      ? "15 minutes"
      : mode === "1h"
        ? "1 hour"
        : mode === "12h"
          ? "12 hours"
          : mode === "24h"
            ? "24 hours"
            : mode === "1w"
              ? "1 week"
              : "1 month";
  return `Quick window: last ${label} (${from ? new Date(from).toLocaleString() : "unbounded"} to ${
    to ? new Date(to).toLocaleString() : "unbounded"
  }).`;
}

function eventTimestamp(item: { timestamp?: string | null; ingested_at?: string | null }) {
  return item.timestamp || item.ingested_at || "";
}

function isInWindow(value: string, window: TimeWindow) {
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

function metricStepSeconds(mode: TimeWindowMode) {
  if (mode === "15m") {
    return 30;
  }
  if (mode === "1h") {
    return 60;
  }
  if (mode === "12h") {
    return 300;
  }
  if (mode === "24h") {
    return 600;
  }
  if (mode === "1w") {
    return 3600;
  }
  if (mode === "1mo") {
    return 3600;
  }
  return undefined;
}

function servicePodSource(pod: ServicePod) {
  const labels = pod.labels || {};
  return (
    labels.app ||
    labels.component ||
    labels.container ||
    Object.entries(labels).find(([key, value]) => key.startsWith("label_") && Boolean(value))?.[1] ||
    "unknown"
  );
}

async function fetchServicePods(serviceId: string) {
  return requestJson<ServicePodListResponse>(`/studio/services/${encodeURIComponent(serviceId)}/pods`);
}

const podMetricGroups: StudioMetricGroup[] = [
  "cpu_usage",
  "memory_usage",
  "network_receive",
  "network_transmit",
  "restarts",
  "oom_killed",
  "readiness",
  "pod_phase",
];

const serviceMetricSummaryGroups: StudioMetricGroup[] = [
  "tasks_started_rate",
  "tasks_failed_rate",
  "tasks_retried_rate",
  "tasks_dlq_rate",
  "task_duration_p95",
  "active_tasks",
  "worker_heartbeat",
  "queue_publish_rate",
  "status_events_rate",
  "observation_events_rate",
];

const podMetricLineColors = ["#2f6fed", "#14966b", "#b7791f", "#9f4acb", "#d64545", "#506070"];

function seriesPodLabel(series: StudioMetricSeries, podLabel?: string | null) {
  const candidateLabels = [podLabel, "pod", "pod_name", "kubernetes_pod_name"].filter(
    (label): label is string => Boolean(label?.trim()),
  );
  for (const label of candidateLabels) {
    const value = series.labels[label];
    if (value?.trim()) {
      return value;
    }
  }
  return "service";
}

function seriesLabel(series: StudioMetricSeries, podLabel?: string | null) {
  const pod = seriesPodLabel(series, podLabel);
  const phase = series.metric === "pod_phase" && series.labels.phase ? ` · ${series.labels.phase}` : "";
  return `${pod}${phase}`;
}

function metricSeriesFor(metrics: StudioMetricsResponse | null, metric: StudioMetricGroup) {
  return (metrics?.series || []).filter((series) => series.metric === metric && series.points.length);
}

function mergeLogResponses(responses: StudioLogListResponse[], limit: number): StudioLogListResponse {
  const items = responses
    .flatMap((response) => response.items)
    .sort((left, right) => new Date(right.timestamp).getTime() - new Date(left.timestamp).getTime())
    .slice(0, limit);
  return { count: items.length, items, next_cursor: null };
}

function mergeMetricResponses(responses: StudioMetricsResponse[]): StudioMetricsResponse {
  const first = responses[0];
  if (!first) {
    return {
      service_id: "",
      task_id: null,
      from: "",
      to: "",
      step_seconds: 0,
      approximate: false,
      warnings: [],
      series: [],
    };
  }
  return {
    ...first,
    approximate: responses.some((response) => response.approximate),
    warnings: Array.from(new Set(responses.flatMap((response) => response.warnings))),
    series: responses.flatMap((response) => response.series),
  };
}

function emptyLogResponse(): StudioLogListResponse {
  return {
    count: 0,
    items: [],
    next_cursor: null,
  };
}

function emptyMetricsResponse(targetService: ServiceRecord, window: TimeWindow): StudioMetricsResponse {
  return {
    service_id: targetService.service_id,
    task_id: null,
    from: window.from,
    to: window.to,
    step_seconds: 0,
    approximate: false,
    warnings: [],
    series: [],
  };
}

function selectedPodLabel(pods: string[]) {
  if (!pods.length) {
    return "";
  }
  if (pods.length === 1) {
    return pods[0];
  }
  return `${pods.length} pods`;
}

function normalizeSelectedPods(pods: string[], availablePods: ServicePod[], previousAvailablePods = availablePods) {
  const availableNames = availablePods.map((pod) => pod.name);
  if (!availableNames.length) {
    return [];
  }
  const previousAvailableNames = previousAvailablePods.map((pod) => pod.name);
  const selectedSet = new Set(pods);
  const availableSet = new Set(availableNames);
  const filtered = pods.filter((pod) => availableSet.has(pod));
  if (!previousAvailableNames.length) {
    return filtered.length ? filtered : availableNames;
  }
  if (!pods.length) {
    return [];
  }
  const wasAllPodsSelected = previousAvailableNames.every((pod) => selectedSet.has(pod));
  if (wasAllPodsSelected) {
    return availableNames;
  }
  return filtered.length ? filtered : availableNames;
}

function hasLoadedPodsForService(servicePods: ServicePodListResponse | null, targetService: ServiceRecord) {
  return servicePods?.service_id === targetService.service_id && servicePods.pods.length > 0;
}

function podMetricLineColor(index: number) {
  return podMetricLineColors[index % podMetricLineColors.length];
}

function formatChartStartTime(timestamp: number) {
  return `Start ${new Date(timestamp).toLocaleTimeString([], {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  })}`;
}

function formatChartOffset(milliseconds: number) {
  const seconds = Math.max(0, Math.round(milliseconds / 1000));
  if (seconds < 60) {
    return `+${seconds}s`;
  }
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) {
    return `+${minutes}m`;
  }
  const hours = Math.round(minutes / 60);
  if (hours < 48) {
    return `+${hours}h`;
  }
  const days = Math.round(hours / 24);
  return `+${days}d`;
}

function MetricLineChart({ series, podLabel }: { series: StudioMetricSeries[]; podLabel?: string | null }) {
  const width = 640;
  const height = 220;
  const paddingTop = 28;
  const paddingRight = 24;
  const paddingBottom = 62;
  const paddingLeft = 46;
  const unit = series[0]?.unit || "value";
  const yAxisLabel = unit === "boolean" ? "state" : unit || "value";
  const points = series.flatMap((item) =>
    item.points
      .map((point) => ({ timestamp: new Date(point.timestamp).getTime(), value: point.value }))
      .filter((point): point is { timestamp: number; value: number } => !Number.isNaN(point.timestamp) && point.value !== null),
  );
  if (!points.length) {
    return <p style={mutedTextStyle}>No graph points in this range.</p>;
  }
  const minTime = Math.min(...points.map((point) => point.timestamp));
  const maxTime = Math.max(...points.map((point) => point.timestamp));
  const minValue = Math.min(0, ...points.map((point) => point.value));
  const maxValue = Math.max(...points.map((point) => point.value));
  const valueSpan = maxValue - minValue || 1;
  const timeSpan = maxTime - minTime || 1;
  const xTicks = [0, 0.33, 0.66, 1].map((ratio) => {
    const timestamp = minTime + timeSpan * ratio;
    return {
      label: ratio === 0 ? formatChartStartTime(minTime) : formatChartOffset(timestamp - minTime),
      timestamp,
    };
  });

  function x(timestamp: number) {
    return paddingLeft + ((timestamp - minTime) / timeSpan) * (width - paddingLeft - paddingRight);
  }

  function y(value: number) {
    return height - paddingBottom - ((value - minValue) / valueSpan) * (height - paddingTop - paddingBottom);
  }

  return (
    <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label="Pod metric graph" style={{ width: "100%", height: 220 }}>
      <line
        x1={paddingLeft}
        y1={height - paddingBottom}
        x2={width - paddingRight}
        y2={height - paddingBottom}
        stroke="var(--studio-border)"
      />
      <line x1={paddingLeft} y1={paddingTop} x2={paddingLeft} y2={height - paddingBottom} stroke="var(--studio-border)" />
      <text x={paddingLeft} y={18} fill="var(--studio-muted)" fontSize="11">
        {formatMetricValue(maxValue, unit)}
      </text>
      <text x={paddingLeft} y={height - paddingBottom + 16} fill="var(--studio-muted)" fontSize="11">
        {formatMetricValue(minValue, unit)}
      </text>
      {xTicks.map((tick, index) => (
        <g key={`${tick.timestamp}-${tick.label}`}>
          <line
            x1={x(tick.timestamp)}
            y1={height - paddingBottom}
            x2={x(tick.timestamp)}
            y2={height - paddingBottom + 5}
            stroke="var(--studio-border)"
          />
          <text
            x={index === 0 ? x(tick.timestamp) - 12 : x(tick.timestamp)}
            y={index === 0 ? height - 8 : height - paddingBottom + 20}
            textAnchor={index === 0 ? "start" : index === xTicks.length - 1 ? "end" : "middle"}
            fill="var(--studio-muted)"
            fontSize="10"
            transform={index === 0 ? `rotate(-90 ${x(tick.timestamp) - 12} ${height - 8})` : undefined}
          >
            {tick.label}
          </text>
        </g>
      ))}
      <text x={width / 2} y={height - 8} textAnchor="middle" fill="var(--studio-muted)" fontSize="11">
        Time
      </text>
      <text
        x={13}
        y={height / 2}
        textAnchor="middle"
        fill="var(--studio-muted)"
        fontSize="11"
        transform={`rotate(-90 13 ${height / 2})`}
      >
        {yAxisLabel}
      </text>
      {series.map((item, index) => {
        const path = item.points
          .filter((point): point is { timestamp: string; value: number } => point.value !== null)
          .map((point, pointIndex) => {
            const command = pointIndex === 0 ? "M" : "L";
            return `${command}${x(new Date(point.timestamp).getTime()).toFixed(1)},${y(point.value).toFixed(1)}`;
          })
          .join(" ");
        return (
          <path
            key={`${item.metric}-${seriesLabel(item, podLabel)}`}
            d={path}
            fill="none"
            stroke={podMetricLineColor(index)}
            strokeWidth="2"
          />
        );
      })}
    </svg>
  );
}

export function ServiceDetailPage() {
  const navigate = useNavigate();
  const { serviceId = "" } = useParams();
  const servicesState = useStudioServices();
  const service = servicesState.servicesById.get(serviceId) || null;
  const serviceLogConfigKey = `${service?.service_id || ""}:${service?.log_config ? "configured" : "unconfigured"}`;
  const serviceMetricsConfigKey = `${service?.service_id || ""}:${service?.metrics_config ? "configured" : "unconfigured"}`;

  const [serviceEvents, setServiceEvents] = useState<StudioEventListResponse | null>(null);
  const [serviceEventsLoading, setServiceEventsLoading] = useState(false);
  const [serviceEventsError, setServiceEventsError] = useState<string | null>(null);
  const [serviceEventTaskFilter, setServiceEventTaskFilter] = useState("");
  const [serviceEventSourceFilter, setServiceEventSourceFilter] = useState<"" | ServiceEventSourceKind>("");
  const [serviceEventTypeFilter, setServiceEventTypeFilter] = useState("");
  const [serviceEventWindowMode, setServiceEventWindowMode] = useState<TimeWindowMode>("auto");
  const [serviceEventManualFrom, setServiceEventManualFrom] = useState("");
  const [serviceEventManualTo, setServiceEventManualTo] = useState("");

  const [serviceLogs, setServiceLogs] = useState<StudioLogListResponse | null>(null);
  const [serviceLogsLoading, setServiceLogsLoading] = useState(false);
  const [serviceLogsError, setServiceLogsError] = useState<string | null>(null);
  const [serviceLogQuery, setServiceLogQuery] = useState("");
  const [serviceLogLevel, setServiceLogLevel] = useState("");
  const [serviceLogSource, setServiceLogSource] = useState("");
  const [serviceLogLimit, setServiceLogLimit] = useState("20");
  const [serviceLogWindowMode, setServiceLogWindowMode] = useState<TimeWindowMode>("auto");
  const [serviceLogManualFrom, setServiceLogManualFrom] = useState("");
  const [serviceLogManualTo, setServiceLogManualTo] = useState("");
  const [servicePods, setServicePods] = useState<ServicePodListResponse | null>(null);
  const [servicePodsLoading, setServicePodsLoading] = useState(false);
  const [servicePodsError, setServicePodsError] = useState<string | null>(null);
  const [selectedServicePods, setSelectedServicePods] = useState<string[]>([]);
  const servicePodsRef = useRef<ServicePodListResponse | null>(null);
  const selectedServicePodsRef = useRef<string[]>([]);
  const [serviceMetrics, setServiceMetrics] = useState<StudioMetricsResponse | null>(null);
  const [serviceMetricsLoading, setServiceMetricsLoading] = useState(false);
  const [serviceMetricsError, setServiceMetricsError] = useState<string | null>(null);
  const [serviceMetricWindowMode, setServiceMetricWindowMode] = useState<TimeWindowMode>("1h");
  const [serviceMetricManualFrom, setServiceMetricManualFrom] = useState("");
  const [serviceMetricManualTo, setServiceMetricManualTo] = useState("");
  const [podMetrics, setPodMetrics] = useState<StudioMetricsResponse | null>(null);
  const [podMetricsLoading, setPodMetricsLoading] = useState(false);
  const [podMetricsError, setPodMetricsError] = useState<string | null>(null);
  const [podMetricWindowMode, setPodMetricWindowMode] = useState<TimeWindowMode>("1h");
  const [podMetricManualFrom, setPodMetricManualFrom] = useState("");
  const [podMetricManualTo, setPodMetricManualTo] = useState("");
  const [refreshingService, setRefreshingService] = useState(false);
  const [confirmation, setConfirmation] = useState<ConfirmationRequest | null>(null);
  const [confirmationPending, setConfirmationPending] = useState(false);

  function updateServicePods(nextPods: ServicePodListResponse | null) {
    servicePodsRef.current = nextPods;
    setServicePods(nextPods);
  }

  function updateSelectedServicePods(nextPods: string[]) {
    selectedServicePodsRef.current = nextPods;
    setSelectedServicePods(nextPods);
  }

  useEffect(() => {
    if (!serviceId) {
      return;
    }
    setServiceEventWindowMode("auto");
    setServiceEventManualFrom("");
    setServiceEventManualTo("");
    void loadServiceEvents(serviceId, emptyWindow);
  }, [serviceId]);

  const activeServiceEventWindow = resolveWindow(serviceEventWindowMode, serviceEventManualFrom, serviceEventManualTo);

  useEffect(() => {
    setServiceLogSource("");
    updateSelectedServicePods([]);
    setServiceLogWindowMode("auto");
    setServiceLogManualFrom("");
    setServiceLogManualTo("");
    if (!service?.log_config) {
      setServiceLogs(null);
      setServiceLogsError(service ? "No log provider configured for this service." : null);
      return;
    }
    void loadServiceLogs({
      targetService: service,
      source: "",
      window: emptyWindow,
    });
  }, [serviceLogConfigKey]);

  useEffect(() => {
    if (!service?.metrics_config) {
      updateServicePods(null);
      setServicePodsError(service ? "No metrics provider configured for this service." : null);
      return;
    }
    void loadServicePods(service);
    const interval = window.setInterval(() => {
      void loadServicePods(service, { quiet: true });
    }, 10000);
    return () => window.clearInterval(interval);
  }, [serviceMetricsConfigKey]);

  const activeServiceLogWindow = resolveWindow(serviceLogWindowMode, serviceLogManualFrom, serviceLogManualTo);
  const activeServiceMetricWindow = resolveWindow(
    serviceMetricWindowMode,
    serviceMetricManualFrom,
    serviceMetricManualTo,
  );
  const activePodMetricWindow = resolveWindow(podMetricWindowMode, podMetricManualFrom, podMetricManualTo);

  useEffect(() => {
    setServiceMetricWindowMode("1h");
    setServiceMetricManualFrom("");
    setServiceMetricManualTo("");
    setPodMetricWindowMode("1h");
    setPodMetricManualFrom("");
    setPodMetricManualTo("");
    if (!service?.metrics_config) {
      setServiceMetrics(null);
      setServiceMetricsError(service ? "No metrics provider configured for this service." : null);
      setPodMetrics(null);
      setPodMetricsError(service ? "No metrics provider configured for this service." : null);
      return;
    }
    void loadServiceMetrics({ targetService: service, window: resolveWindow("1h", "", "") });
    void loadPodMetrics({ targetService: service, window: resolveWindow("1h", "", ""), pods: [] });
  }, [serviceMetricsConfigKey]);

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

  async function loadServiceEvents(targetServiceId: string, window = activeServiceEventWindow) {
    setServiceEventsLoading(true);
    setServiceEventsError(null);
    try {
      const payload = await fetchServiceEvents(targetServiceId, {
        limit: 20,
        from: window.from,
        to: window.to,
      });
      setServiceEvents(payload);
    } catch (fetchError) {
      setServiceEventsError(fetchError instanceof Error ? fetchError.message : "Unable to load service activity.");
    } finally {
      setServiceEventsLoading(false);
    }
  }

  async function loadServiceLogs({
    targetService = service,
    source = serviceLogSource,
    pods = selectedServicePods,
    window = activeServiceLogWindow,
  }: {
    targetService?: ServiceRecord | null;
    source?: string;
    pods?: string[];
    window?: TimeWindow;
  } = {}) {
    if (!targetService) {
      return;
    }
    setServiceLogsLoading(true);
    setServiceLogsError(null);
    try {
      const limit = parseLimit(serviceLogLimit, 20);
      const podFilters = pods.filter((pod) => pod.trim());
      if (!podFilters.length && hasLoadedPodsForService(servicePodsRef.current, targetService)) {
        setServiceLogs(emptyLogResponse());
        return;
      }
      const payload = podFilters.length
        ? mergeLogResponses(
            await Promise.all(
              podFilters.map((pod) =>
                fetchServiceLogs(targetService.service_id, {
                  query: serviceLogQuery,
                  level: serviceLogLevel,
                  source,
                  pod,
                  limit,
                  from: window.from,
                  to: window.to,
                }),
              ),
            ),
            limit,
          )
        : await fetchServiceLogs(targetService.service_id, {
            query: serviceLogQuery,
            level: serviceLogLevel,
            source,
            limit,
            from: window.from,
            to: window.to,
          });
      setServiceLogs(payload);
    } catch (fetchError) {
      setServiceLogsError(fetchError instanceof Error ? fetchError.message : "Unable to load service logs.");
    } finally {
      setServiceLogsLoading(false);
    }
  }

  async function loadServicePods(targetService = service, options: { quiet?: boolean } = {}) {
    if (!targetService?.metrics_config) {
      updateServicePods(null);
      setServicePodsError("No metrics provider configured for this service.");
      return;
    }
    if (!options.quiet) {
      setServicePodsLoading(true);
    }
    setServicePodsError(null);
    try {
      const payload = await fetchServicePods(targetService.service_id);
      const currentSelectedPods = selectedServicePodsRef.current;
      const previousServicePods = servicePodsRef.current;
      const previousAvailablePods =
        previousServicePods?.service_id === targetService.service_id ? previousServicePods.pods : [];
      updateServicePods(payload);
      if (!payload.pods.length && previousAvailablePods.length) {
        setPodMetrics(emptyMetricsResponse(targetService, activePodMetricWindow));
        return;
      }
      const nextPods = normalizeSelectedPods(currentSelectedPods, payload.pods, previousAvailablePods);
      const selectionChanged = nextPods.join("\u0000") !== currentSelectedPods.join("\u0000");
      const podsRestored = !previousAvailablePods.length && payload.pods.length > 0 && nextPods.length > 0;
      if (selectionChanged || podsRestored) {
        updateSelectedServicePods(nextPods);
        void loadServiceLogs({ targetService, pods: nextPods });
        void loadPodMetrics({ targetService, pods: nextPods });
      }
    } catch (fetchError) {
      setServicePodsError(fetchError instanceof Error ? fetchError.message : "Unable to load service pods.");
    } finally {
      if (!options.quiet) {
        setServicePodsLoading(false);
      }
    }
  }

  async function loadServiceMetrics({
    targetService = service,
    window = activeServiceMetricWindow,
  }: {
    targetService?: ServiceRecord | null;
    window?: TimeWindow;
  } = {}) {
    if (!targetService?.metrics_config) {
      setServiceMetrics(null);
      setServiceMetricsError("No metrics provider configured for this service.");
      return;
    }
    setServiceMetricsLoading(true);
    setServiceMetricsError(null);
    try {
      const payload = await fetchServiceMetrics(targetService.service_id, {
        from: window.from,
        to: window.to,
        groups: serviceMetricSummaryGroups,
      });
      setServiceMetrics(payload);
    } catch (fetchError) {
      setServiceMetrics(null);
      setServiceMetricsError(fetchError instanceof Error ? fetchError.message : "Unable to load service metrics.");
    } finally {
      setServiceMetricsLoading(false);
    }
  }

  async function loadPodMetrics({
    targetService = service,
    window = activePodMetricWindow,
    pods = selectedServicePods,
    mode = podMetricWindowMode,
  }: {
    targetService?: ServiceRecord | null;
    window?: TimeWindow;
    pods?: string[];
    mode?: TimeWindowMode;
  } = {}) {
    if (!targetService?.metrics_config) {
      setPodMetrics(null);
      setPodMetricsError("No metrics provider configured for this service.");
      return;
    }
    setPodMetricsLoading(true);
    setPodMetricsError(null);
    try {
      const podFilters = pods.filter((pod) => pod.trim());
      if (!podFilters.length && hasLoadedPodsForService(servicePodsRef.current, targetService)) {
        setPodMetrics(emptyMetricsResponse(targetService, window));
        return;
      }
      const baseQuery = {
        from: window.from,
        to: window.to,
        step: metricStepSeconds(mode),
        groups: podMetricGroups,
        split_by_pod: true,
      };
      const payload = podFilters.length
        ? mergeMetricResponses(
            await Promise.all(
              podFilters.map((pod) =>
                fetchServiceMetrics(targetService.service_id, {
                  ...baseQuery,
                  pod,
                }),
              ),
            ),
          )
        : await fetchServiceMetrics(targetService.service_id, baseQuery);
      setPodMetrics(payload);
    } catch (fetchError) {
      setPodMetrics(null);
      setPodMetricsError(fetchError instanceof Error ? fetchError.message : "Unable to load pod metrics.");
    } finally {
      setPodMetricsLoading(false);
    }
  }

  async function handleRefreshService() {
    if (!service || refreshingService) {
      return;
    }
    setRefreshingService(true);
    try {
      await servicesState.refresh(service.service_id);
    } catch {
      // Shared services context populates the error banner for failed mutations.
    } finally {
      setRefreshingService(false);
    }
  }

  function requestStatusChange(nextStatus: "unavailable" | "disabled") {
    if (!service) {
      return;
    }
    const serviceId = service.service_id;
    setConfirmation({
      title: nextStatus === "disabled" ? "Disable service" : "Mark service unavailable",
      body:
        nextStatus === "disabled"
          ? `Disable '${serviceId}' in the Studio registry. Federated reads for this service will be blocked while it is disabled.`
          : `Mark '${serviceId}' as unavailable in the Studio registry. Operators will see it as unavailable until it is enabled or refreshed.`,
      confirmLabel: nextStatus === "disabled" ? "Disable Service" : "Mark Unavailable",
      onConfirm: async () => {
        await runConfirmedAction(async () => {
          await servicesState.updateStatus(serviceId, nextStatus);
        });
      },
    });
  }

  function requestDeleteService() {
    if (!service) {
      return;
    }
    const serviceId = service.service_id;
    setConfirmation({
      title: "Delete service",
      body: `Delete '${serviceId}' from the Studio registry. This also removes retained task search documents for the service.`,
      confirmLabel: "Delete Service",
      challengeText: serviceId,
      challengeLabel: `Type ${serviceId} to confirm deletion`,
      onConfirm: async () => {
        await runConfirmedAction(async () => {
          await servicesState.remove(serviceId);
          navigate("/services");
        });
      },
    });
  }

  async function runConfirmedAction(action: () => Promise<void>) {
    setConfirmationPending(true);
    try {
      await action();
    } catch {
      // Shared services context populates the error banner for failed mutations.
    } finally {
      setConfirmationPending(false);
      setConfirmation(null);
    }
  }

  const filteredServiceEvents = (serviceEvents?.items || []).filter((item) => {
    if (!isInWindow(eventTimestamp(item), activeServiceEventWindow)) {
      return false;
    }
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
  const filteredServiceLogs = (serviceLogs?.items || []).filter((item) => isInWindow(item.timestamp, activeServiceLogWindow));
  const serviceLogSourceOptions = Array.from(new Set((serviceLogs?.items || []).map((item) => item.source).filter(Boolean))).sort();

  if (servicesState.error) {
    return <NoticeBanner tone="error">{servicesState.error}</NoticeBanner>;
  }

  if (!service) {
    return <NoticeBanner tone="error">Service `{serviceId}` is not present in the Studio registry.</NoticeBanner>;
  }

  const health = service.health || null;
  const latestObservedAt = latestTimestamp(
    health?.observation_freshness.latest_status_event_at,
    health?.observation_freshness.latest_observation_event_at,
  );
  const workerHealthLabel =
    health?.worker_health.state === "unsupported"
      ? "unsupported by service"
      : health?.worker_health.state === "unknown"
        ? health.worker_health.detail || "unknown"
        : health?.worker_health.state || "unknown";

  return (
    <div className="studio-stack-lg">
      {servicesState.notice ? <NoticeBanner>{servicesState.notice}</NoticeBanner> : null}

      <SectionCard
        title="Service Detail"
        subtitle="Inspect stored registry metadata, navigate to routed control-plane screens, and run existing registry actions."
        action={
          <div className="studio-badge-row">
            <StatusBadge status={service.status} />
            <HealthBadge status={health?.overall_status || "unknown"} />
          </div>
        }
      >
        <div className="studio-action-row">
          <Link to="/services" style={{ ...secondaryButtonStyle, textDecoration: "none" }}>
            <StudioIcon name="back" />
            Back to Services
          </Link>
          <Link to={`/services/${encodeURIComponent(service.service_id)}/topology`} style={{ ...secondaryButtonStyle, textDecoration: "none" }}>
            <StudioIcon name="topology" />
            Topology
          </Link>
          <Link to={`/services/${encodeURIComponent(service.service_id)}/dlq`} style={{ ...secondaryButtonStyle, textDecoration: "none" }}>
            <StudioIcon name="dlq" />
            DLQ Explorer
          </Link>
          <Link to={`/tasks/search?service_id=${encodeURIComponent(service.service_id)}`} style={{ ...secondaryButtonStyle, textDecoration: "none" }}>
            <StudioIcon name="tasks" />
            Task Search
          </Link>
          <button type="button" onClick={() => void handleRefreshService()} style={secondaryButtonStyle} disabled={refreshingService}>
            <StudioIcon name="refresh" />
            {refreshingService ? "Refreshing..." : "Refresh"}
          </button>
          <button type="button" onClick={() => void servicesState.runHealthCheck(service.service_id)} style={secondaryButtonStyle}>
            <StudioIcon name="health" />
            Run Health Check
          </button>
          <button type="button" onClick={() => void servicesState.updateStatus(service.service_id, "registered")} style={secondaryButtonStyle}>
            <StudioIcon name="enable" />
            Enable
          </button>
          <button type="button" onClick={() => requestStatusChange("unavailable")} style={secondaryButtonStyle}>
            <StudioIcon name="unavailable" />
            Mark Unavailable
          </button>
          <button type="button" onClick={() => requestStatusChange("disabled")} style={secondaryButtonStyle}>
            <StudioIcon name="disable" />
            Disable
          </button>
          <button
            type="button"
            onClick={requestDeleteService}
            style={destructiveButtonStyle}
          >
            <StudioIcon name="delete" />
            Delete
          </button>
        </div>

        <div className="studio-detail-grid">
          <dl style={{ margin: 0, display: "grid", gap: 10, fontSize: 13 }}>
            <MetadataRow label="Service id" value={service.service_id} />
            <MetadataRow label="Name" value={service.name} />
            <MetadataRow label="Environment" value={service.environment} />
            <MetadataRow label="Base URL" value={service.base_url} />
            <MetadataRow label="Auth mode" value={service.auth_mode} />
            <MetadataRow label="Tags" value={service.tags.length ? service.tags.join(", ") : "none"} />
            <MetadataRow label="Last refresh" value={formatTimestamp(service.last_seen_at)} />
            <MetadataRow label="Log provider" value={service.log_config?.provider || "none"} />
            <MetadataRow label="Metrics provider" value={service.metrics_config?.provider || "none"} />
          </dl>

          <div className="studio-stack-sm">
            <div>
              <h3 style={{ margin: 0, marginBottom: 8 }}>Runtime Health</h3>
              <dl style={{ margin: 0, display: "grid", gap: 10, fontSize: 13 }}>
                <MetadataRow label="Overall" value={<HealthBadge status={health?.overall_status || "unknown"} />} />
                <MetadataRow label="Last checked" value={formatTimestamp(health?.last_checked_at)} />
                <MetadataRow label="Capability" value={health?.capability_status.state || "missing"} />
                <MetadataRow label="Capability refreshed" value={formatTimestamp(health?.capability_status.last_successful_at)} />
                <MetadataRow label="HTTP reachability" value={health?.http_status.state || "unknown"} />
                <MetadataRow label="Latest observed activity" value={formatTimestamp(latestObservedAt)} />
                <MetadataRow label="Observation freshness" value={health?.observation_freshness.state || "missing"} />
                <MetadataRow label="Worker heartbeat" value={workerHealthLabel} />
                <MetadataRow label="Worker reported at" value={formatTimestamp(health?.worker_health.reported_at)} />
              </dl>
              {health?.http_status.error_detail ? (
                <p style={{ ...mutedTextStyle, marginTop: 8 }}>Reachability detail: {health.http_status.error_detail}</p>
              ) : null}
              {health?.worker_health.detail && health.worker_health.state !== "unsupported" ? (
                <p style={{ ...mutedTextStyle, marginTop: 8 }}>Worker detail: {health.worker_health.detail}</p>
              ) : null}
            </div>
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
            <div>
              <h3 style={{ margin: 0, marginBottom: 8 }}>Stored Metrics Config</h3>
              {service.metrics_config ? (
                <InlineCodeBox value={JSON.stringify(service.metrics_config, null, 2)} minHeight={160} />
              ) : (
                <p style={mutedTextStyle}>No metrics provider configured for this service.</p>
              )}
            </div>
            <div>
              <h3 style={{ margin: 0, marginBottom: 8 }}>Stored Trace Config</h3>
              {service.trace_config ? (
                <InlineCodeBox value={JSON.stringify(service.trace_config, null, 2)} minHeight={120} />
              ) : (
                <p style={mutedTextStyle}>No trace provider configured for this service.</p>
              )}
            </div>
          </div>
        </div>
      </SectionCard>

      <SectionCard
        title="Service Metrics"
        subtitle="Service-wide Relayna runtime metrics and pod-level Kubernetes charts for this registered service."
        action={
          <button
            type="button"
            onClick={() => {
              void loadServiceMetrics();
              void loadPodMetrics();
            }}
            style={secondaryButtonStyle}
          >
            <StudioIcon name="refresh" />
            Reload Metrics
          </button>
        }
      >
        <div className="studio-log-filter-grid studio-log-window-grid">
          <label className="studio-filter-field">
            <span>Metrics Window</span>
            <select
              aria-label="Service metrics window mode"
              value={serviceMetricWindowMode}
              onChange={(event) => {
                const nextMode = event.target.value as TimeWindowMode;
                let nextManualFrom = serviceMetricManualFrom;
                let nextManualTo = serviceMetricManualTo;
                if (nextMode === "manual") {
                  nextManualFrom = isoToLocalDateTime(activeServiceMetricWindow.from);
                  nextManualTo = isoToLocalDateTime(activeServiceMetricWindow.to);
                  setServiceMetricManualFrom(nextManualFrom);
                  setServiceMetricManualTo(nextManualTo);
                }
                setServiceMetricWindowMode(nextMode);
                void loadServiceMetrics({ window: resolveWindow(nextMode, nextManualFrom, nextManualTo) });
              }}
              style={inputStyle}
            >
              <option value="15m">Last 15 minutes</option>
              <option value="1h">Last hour</option>
              <option value="24h">Last 24 hours</option>
              <option value="manual">Custom range</option>
            </select>
          </label>
          <label className="studio-filter-field">
            <span>From</span>
            <input
              aria-label="Service metrics from"
              type="datetime-local"
              value={
                serviceMetricWindowMode === "manual"
                  ? serviceMetricManualFrom
                  : isoToLocalDateTime(activeServiceMetricWindow.from)
              }
              onChange={(event) => {
                const nextFrom = event.target.value;
                setServiceMetricManualFrom(nextFrom);
                void loadServiceMetrics({ window: resolveWindow("manual", nextFrom, serviceMetricManualTo) });
              }}
              disabled={serviceMetricWindowMode !== "manual"}
              style={inputStyle}
            />
          </label>
          <label className="studio-filter-field">
            <span>To</span>
            <input
              aria-label="Service metrics to"
              type="datetime-local"
              value={
                serviceMetricWindowMode === "manual"
                  ? serviceMetricManualTo
                  : isoToLocalDateTime(activeServiceMetricWindow.to)
              }
              onChange={(event) => {
                const nextTo = event.target.value;
                setServiceMetricManualTo(nextTo);
                void loadServiceMetrics({ window: resolveWindow("manual", serviceMetricManualFrom, nextTo) });
              }}
              disabled={serviceMetricWindowMode !== "manual"}
              style={inputStyle}
            />
          </label>
        </div>
        <div style={{ marginBottom: 12 }}>
          <strong>Service Metrics Summary</strong>
          <p style={{ ...mutedTextStyle, margin: "4px 0 0" }}>
            Aggregated across the logical service runtime; Kubernetes pod and container metrics are shown in the charts below.
          </p>
        </div>
        <p style={mutedTextStyle}>
          {describeWindow(serviceMetricWindowMode, activeServiceMetricWindow.from, activeServiceMetricWindow.to)}
        </p>
        {serviceMetricsLoading ? <p style={mutedTextStyle}>Loading service metrics...</p> : null}
        {serviceMetricsError ? <p style={{ ...mutedTextStyle, color: "var(--studio-danger)" }}>{serviceMetricsError}</p> : null}
        {!serviceMetricsLoading && !serviceMetricsError && serviceMetrics && !serviceMetrics.series.length ? (
          <p style={mutedTextStyle}>No service metrics matched the current window.</p>
        ) : null}
        {serviceMetrics ? (
          <div className="studio-metrics-grid studio-metrics-grid--4">
            {serviceMetricSummaryGroups.map((metric) => (
              <div key={metric} className="studio-subcard" style={{ borderRadius: 14, padding: 14 }}>
                <span className="studio-inline-meta">{metricLabel(metric)}</span>
                <strong style={{ display: "block", marginTop: 6 }}>{metricLatestValue(serviceMetrics, metric)}</strong>
              </div>
            ))}
          </div>
        ) : null}

        <div style={{ borderTop: "1px solid var(--studio-border)", margin: "18px 0" }} />
        <div className="studio-list-card__top" style={{ marginBottom: 12 }}>
          <div>
            <strong>Pod Metric Charts</strong>
            <p style={{ ...mutedTextStyle, margin: "4px 0 0" }}>
              {selectedServicePods.length
                ? `Filtered to ${selectedPodLabel(selectedServicePods)}.`
                : servicePods?.pods.length
                  ? "No pods selected."
                  : "Showing every current pod matched by this service."}
            </p>
          </div>
          <button type="button" onClick={() => void loadPodMetrics()} style={secondaryButtonStyle}>
            <StudioIcon name="refresh" />
            Reload Charts
          </button>
        </div>
        <div className="studio-log-filter-grid studio-log-window-grid">
          <label className="studio-filter-field">
            <span>Metrics Window</span>
            <select
              aria-label="Pod metrics window mode"
              value={podMetricWindowMode}
              onChange={(event) => {
                const nextMode = event.target.value as TimeWindowMode;
                let nextManualFrom = podMetricManualFrom;
                let nextManualTo = podMetricManualTo;
                if (nextMode === "manual") {
                  nextManualFrom = isoToLocalDateTime(activePodMetricWindow.from);
                  nextManualTo = isoToLocalDateTime(activePodMetricWindow.to);
                  setPodMetricManualFrom(nextManualFrom);
                  setPodMetricManualTo(nextManualTo);
                }
                setPodMetricWindowMode(nextMode);
                void loadPodMetrics({
                  window: resolveWindow(nextMode, nextManualFrom, nextManualTo),
                  mode: nextMode,
                });
              }}
              style={inputStyle}
            >
              <option value="15m">Last 15 minutes</option>
              <option value="1h">Last hour</option>
              <option value="12h">Last 12 hours</option>
              <option value="24h">Last 24 hours</option>
              <option value="1w">Last week</option>
              <option value="1mo">Last month</option>
              <option value="manual">Custom range</option>
            </select>
          </label>
          <label className="studio-filter-field">
            <span>From</span>
            <input
              aria-label="Pod metrics from"
              type="datetime-local"
              value={podMetricWindowMode === "manual" ? podMetricManualFrom : isoToLocalDateTime(activePodMetricWindow.from)}
              onChange={(event) => {
                const nextFrom = event.target.value;
                setPodMetricManualFrom(nextFrom);
                void loadPodMetrics({
                  window: resolveWindow("manual", nextFrom, podMetricManualTo),
                  mode: "manual",
                });
              }}
              disabled={podMetricWindowMode !== "manual"}
              style={inputStyle}
            />
          </label>
          <label className="studio-filter-field">
            <span>To</span>
            <input
              aria-label="Pod metrics to"
              type="datetime-local"
              value={podMetricWindowMode === "manual" ? podMetricManualTo : isoToLocalDateTime(activePodMetricWindow.to)}
              onChange={(event) => {
                const nextTo = event.target.value;
                setPodMetricManualTo(nextTo);
                void loadPodMetrics({
                  window: resolveWindow("manual", podMetricManualFrom, nextTo),
                  mode: "manual",
                });
              }}
              disabled={podMetricWindowMode !== "manual"}
              style={inputStyle}
            />
          </label>
        </div>
        <p style={mutedTextStyle}>
          {describeWindow(podMetricWindowMode, activePodMetricWindow.from, activePodMetricWindow.to)}
        </p>
        {podMetricsLoading ? <p style={mutedTextStyle}>Loading pod metrics...</p> : null}
        {podMetricsError ? <p style={{ ...mutedTextStyle, color: "var(--studio-danger)" }}>{podMetricsError}</p> : null}
        {!podMetricsLoading && !podMetricsError && podMetrics && !podMetrics.series.length ? (
          <p style={mutedTextStyle}>No pod metrics matched the selected pod and window.</p>
        ) : null}
        {podMetrics ? (
          <div className="studio-metrics-grid studio-metrics-grid--2">
            {podMetricGroups.map((metric) => {
              const series = metricSeriesFor(podMetrics, metric);
              return (
                <div key={metric} className="studio-subcard" style={{ borderRadius: 8, padding: 14 }}>
                  <div className="studio-list-card__top">
                    <strong>{metricLabel(metric)}</strong>
                    <span className="studio-inline-meta">{series.length} series</span>
                  </div>
                  <MetricLineChart series={series} podLabel={service.metrics_config?.pod_label} />
                  {series.length ? (
                    <div className="studio-chart-legend" aria-label={`${metricLabel(metric)} legend`}>
                      {series.slice(0, 6).map((item, index) => (
                        <span key={`${item.metric}-${seriesLabel(item, service.metrics_config?.pod_label)}`} className="studio-chart-legend__item">
                          <span
                            className="studio-chart-legend__swatch"
                            style={{ backgroundColor: podMetricLineColor(index) }}
                            aria-hidden="true"
                          />
                          {seriesLabel(item, service.metrics_config?.pod_label)}
                        </span>
                      ))}
                    </div>
                  ) : null}
                </div>
              );
            })}
          </div>
        ) : null}
      </SectionCard>

      <SectionCard
        title="Service Pods"
        subtitle="Current Kubernetes pods matched by this service's metrics selector; all pods are selected by default, and clicking a pod toggles it in the log and metric chart filter."
        action={
          <button type="button" onClick={() => void loadServicePods()} style={secondaryButtonStyle}>
            <StudioIcon name="refresh" />
            Reload Pods
          </button>
        }
      >
        {servicePodsLoading ? <p style={mutedTextStyle}>Loading service pods...</p> : null}
        {servicePodsError ? <p style={{ ...mutedTextStyle, color: "var(--studio-danger)" }}>{servicePodsError}</p> : null}
        {!service.metrics_config ? (
          <p style={mutedTextStyle}>Pod discovery is unavailable until this service sets `metrics_config`.</p>
        ) : null}
        {service.metrics_config && !servicePodsLoading && !servicePodsError && !servicePods?.pods.length ? (
          <p style={mutedTextStyle}>No current pods matched this service selector.</p>
        ) : null}
        {servicePods?.pods.length ? (
          <div className="studio-action-row" style={{ marginBottom: 12 }}>
            <span className="studio-inline-meta">
              Selected pods: {selectedServicePods.length ? selectedServicePods.join(", ") : "none"}
            </span>
            <button
              type="button"
              onClick={() => {
                const allPods = servicePods.pods.map((pod) => pod.name);
                updateSelectedServicePods(allPods);
                void loadServiceLogs({ pods: allPods });
                void loadPodMetrics({ pods: allPods });
              }}
              style={secondaryButtonStyle}
            >
              <StudioIcon name="clear" />
              Select All Pods
            </button>
            <button
              type="button"
              onClick={() => {
                updateSelectedServicePods([]);
                void loadServiceLogs({ pods: [] });
                void loadPodMetrics({ pods: [] });
              }}
              style={secondaryButtonStyle}
              disabled={!selectedServicePods.length}
            >
              <StudioIcon name="clear" />
              Deselect All Pods
            </button>
          </div>
        ) : null}
        {servicePods?.pods.length ? (
          <div className="studio-metrics-grid studio-metrics-grid--4">
            {servicePods.pods.map((pod) => {
              const selected = selectedServicePods.includes(pod.name);
              return (
                <button
                  key={`${pod.namespace}-${pod.name}`}
                  type="button"
                  onClick={() => {
                    const rawNextPods = selected
                      ? selectedServicePods.filter((selectedPod) => selectedPod !== pod.name)
                      : [...selectedServicePods, pod.name];
                    const nextPods = normalizeSelectedPods(rawNextPods, servicePods.pods);
                    updateSelectedServicePods(nextPods);
                    void loadServiceLogs({ pods: nextPods });
                    void loadPodMetrics({ pods: nextPods });
                  }}
                  style={{
                    ...secondaryButtonStyle,
                    alignItems: "center",
                    background: selected
                      ? "linear-gradient(135deg, rgba(15, 124, 123, 0.22), rgba(218, 107, 43, 0.12))"
                      : "var(--studio-surface-muted)",
                    borderColor: selected ? "var(--studio-secondary-strong)" : "var(--studio-border)",
                    borderWidth: selected ? 2 : 1,
                    boxShadow: selected
                      ? "0 0 0 2px rgba(15, 124, 123, 0.14), 0 8px 20px rgba(15, 85, 84, 0.14)"
                      : undefined,
                    color: selected ? "var(--studio-text)" : undefined,
                    display: "flex",
                    gap: 10,
                    justifyContent: "flex-start",
                    minHeight: 56,
                    padding: "9px 11px",
                    position: "relative",
                    textAlign: "left",
                  }}
                  aria-pressed={selected}
                >
                  <span
                    aria-hidden="true"
                    style={{
                      background: selected ? "var(--studio-secondary-strong)" : "transparent",
                      border: `1px solid ${selected ? "var(--studio-secondary-strong)" : "var(--studio-border-strong)"}`,
                      borderRadius: 999,
                      boxShadow: selected ? "inset 0 0 0 3px var(--studio-primary-contrast)" : undefined,
                      flex: "0 0 auto",
                      height: 14,
                      width: 14,
                    }}
                  />
                  <span style={{ display: "grid", gap: 2, minWidth: 0 }}>
                    <strong style={{ lineHeight: 1.2 }}>{pod.name}</strong>
                    <span className="studio-inline-meta" style={{ fontSize: 12, lineHeight: 1.25 }}>
                      {pod.namespace} · {pod.phase || "unknown"} · {servicePodSource(pod)}
                    </span>
                  </span>
                </button>
              );
            })}
          </div>
        ) : null}
      </SectionCard>

      <div className="studio-two-column">
        <SectionCard
          title="Recent Activity"
          subtitle="Service-scoped Studio-ingested events with live SSE updates."
          action={
            <button type="button" onClick={() => void loadServiceEvents(service.service_id)} style={secondaryButtonStyle}>
              <StudioIcon name="refresh" />
              Reload Activity
            </button>
          }
        >
          <div className="studio-form-grid studio-form-grid--triple">
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
          <div className="studio-log-filter-grid studio-log-window-grid" style={{ marginTop: 12 }}>
            <label className="studio-filter-field">
              <span>Activity Window</span>
              <select
                aria-label="Service event window mode"
                value={serviceEventWindowMode}
                onChange={(event) => {
                  const nextMode = event.target.value as TimeWindowMode;
                  let nextManualFrom = serviceEventManualFrom;
                  let nextManualTo = serviceEventManualTo;
                  if (nextMode === "manual") {
                    nextManualFrom = isoToLocalDateTime(activeServiceEventWindow.from);
                    nextManualTo = isoToLocalDateTime(activeServiceEventWindow.to);
                    setServiceEventManualFrom(nextManualFrom);
                    setServiceEventManualTo(nextManualTo);
                  }
                  setServiceEventWindowMode(nextMode);
                  void loadServiceEvents(service.service_id, resolveWindow(nextMode, nextManualFrom, nextManualTo));
                }}
                style={inputStyle}
              >
                <option value="auto">Auto window</option>
                <option value="15m">Last 15 minutes</option>
                <option value="1h">Last hour</option>
                <option value="24h">Last 24 hours</option>
                <option value="manual">Custom range</option>
              </select>
            </label>
            <label className="studio-filter-field">
              <span>From</span>
              <input
                aria-label="Service event from"
                type="datetime-local"
                value={
                  serviceEventWindowMode === "manual"
                    ? serviceEventManualFrom
                    : isoToLocalDateTime(activeServiceEventWindow.from)
                }
                onChange={(event) => {
                  const nextFrom = event.target.value;
                  setServiceEventManualFrom(nextFrom);
                  void loadServiceEvents(service.service_id, resolveWindow("manual", nextFrom, serviceEventManualTo));
                }}
                disabled={serviceEventWindowMode !== "manual"}
                style={inputStyle}
              />
            </label>
            <label className="studio-filter-field">
              <span>To</span>
              <input
                aria-label="Service event to"
                type="datetime-local"
                value={
                  serviceEventWindowMode === "manual"
                    ? serviceEventManualTo
                    : isoToLocalDateTime(activeServiceEventWindow.to)
                }
                onChange={(event) => {
                  const nextTo = event.target.value;
                  setServiceEventManualTo(nextTo);
                  void loadServiceEvents(service.service_id, resolveWindow("manual", serviceEventManualFrom, nextTo));
                }}
                disabled={serviceEventWindowMode !== "manual"}
                style={inputStyle}
              />
            </label>
          </div>
          <p style={mutedTextStyle}>
            {describeWindow(serviceEventWindowMode, activeServiceEventWindow.from, activeServiceEventWindow.to)}
          </p>
          {serviceEventsLoading ? <p style={mutedTextStyle}>Loading service activity...</p> : null}
          {serviceEventsError ? <p style={{ ...mutedTextStyle, color: "var(--studio-danger)" }}>{serviceEventsError}</p> : null}
          {!serviceEventsLoading && !serviceEventsError && !filteredServiceEvents.length ? (
            <p style={mutedTextStyle}>No Studio-ingested events for this service yet.</p>
          ) : null}
          {filteredServiceEvents.length ? (
            <div className="studio-stack-sm studio-surface-scroll">
              {filteredServiceEvents.map((item) => (
                <article
                  key={item.dedupe_key}
                  className="studio-subcard"
                  style={{ borderRadius: 14, padding: 12 }}
                >
                  <div className="studio-list-card__top">
                    <div style={{ display: "grid", gap: 4 }}>
                      <strong style={{ fontSize: 13 }}>{formatEventSummary(item)}</strong>
                      <span className="studio-inline-meta">
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
                    <span className="studio-inline-meta">{formatTimestamp(item.timestamp || item.ingested_at)}</span>
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
          subtitle={
            selectedServicePods.length
              ? `Service-scoped logs filtered to ${selectedPodLabel(selectedServicePods)}.`
              : "Service-scoped log queries remain separate from Relayna status and observations."
          }
          action={
            <button type="button" onClick={() => void loadServiceLogs()} style={secondaryButtonStyle}>
              <StudioIcon name="refresh" />
              Reload Logs
            </button>
          }
        >
          <div className="studio-log-filter-grid">
            <label className="studio-filter-field">
              <span>Text</span>
              <input
                aria-label="Service log text filter"
                value={serviceLogQuery}
                onChange={(event) => setServiceLogQuery(event.target.value)}
                placeholder="Search log text"
                style={inputStyle}
              />
            </label>
            <label className="studio-filter-field">
              <span>Level</span>
              <input
                aria-label="Service log level"
                value={serviceLogLevel}
                onChange={(event) => setServiceLogLevel(event.target.value)}
                placeholder="info, error"
                style={inputStyle}
              />
            </label>
            <label className="studio-filter-field">
              <span>Source</span>
              <input
                aria-label="Service log source"
                value={serviceLogSource}
                onChange={(event) => setServiceLogSource(event.target.value)}
                list={`service-log-sources-${service.service_id}`}
                placeholder={service.log_config?.source_label || "source"}
                disabled={!service.log_config?.source_label}
                style={inputStyle}
              />
            </label>
            <label className="studio-filter-field">
              <span>Limit</span>
              <input
                aria-label="Service log limit"
                value={serviceLogLimit}
                onChange={(event) => setServiceLogLimit(event.target.value)}
                placeholder="20"
                inputMode="numeric"
                style={inputStyle}
              />
            </label>
          </div>
          <div className="studio-log-filter-grid studio-log-window-grid" style={{ marginTop: 12 }}>
            <label className="studio-filter-field">
              <span>Log Window</span>
              <select
                aria-label="Service log window mode"
                value={serviceLogWindowMode}
                onChange={(event) => {
                  const nextMode = event.target.value as TimeWindowMode;
                  let nextManualFrom = serviceLogManualFrom;
                  let nextManualTo = serviceLogManualTo;
                  if (nextMode === "manual") {
                    nextManualFrom = isoToLocalDateTime(activeServiceLogWindow.from);
                    nextManualTo = isoToLocalDateTime(activeServiceLogWindow.to);
                    setServiceLogManualFrom(nextManualFrom);
                    setServiceLogManualTo(nextManualTo);
                  }
                  setServiceLogWindowMode(nextMode);
                  void loadServiceLogs({ window: resolveWindow(nextMode, nextManualFrom, nextManualTo) });
                }}
                style={inputStyle}
              >
                <option value="auto">Auto window</option>
                <option value="15m">Last 15 minutes</option>
                <option value="1h">Last hour</option>
                <option value="24h">Last 24 hours</option>
                <option value="manual">Custom range</option>
              </select>
            </label>
            <label className="studio-filter-field">
              <span>From</span>
              <input
                aria-label="Service log from"
                type="datetime-local"
                value={
                  serviceLogWindowMode === "manual"
                    ? serviceLogManualFrom
                    : isoToLocalDateTime(activeServiceLogWindow.from)
                }
                onChange={(event) => {
                  const nextFrom = event.target.value;
                  setServiceLogManualFrom(nextFrom);
                  void loadServiceLogs({ window: resolveWindow("manual", nextFrom, serviceLogManualTo) });
                }}
                disabled={serviceLogWindowMode !== "manual"}
                style={inputStyle}
              />
            </label>
            <label className="studio-filter-field">
              <span>To</span>
              <input
                aria-label="Service log to"
                type="datetime-local"
                value={
                  serviceLogWindowMode === "manual"
                    ? serviceLogManualTo
                    : isoToLocalDateTime(activeServiceLogWindow.to)
                }
                onChange={(event) => {
                  const nextTo = event.target.value;
                  setServiceLogManualTo(nextTo);
                  void loadServiceLogs({ window: resolveWindow("manual", serviceLogManualFrom, nextTo) });
                }}
                disabled={serviceLogWindowMode !== "manual"}
                style={inputStyle}
              />
            </label>
          </div>
          <p style={mutedTextStyle}>
            {describeWindow(serviceLogWindowMode, activeServiceLogWindow.from, activeServiceLogWindow.to)}
          </p>
          {serviceLogSourceOptions.length ? (
            <datalist id={`service-log-sources-${service.service_id}`}>
              {serviceLogSourceOptions.map((source) => (
                <option key={source} value={source} />
              ))}
            </datalist>
          ) : null}
          {!service.log_config?.source_label ? (
            <p style={mutedTextStyle}>Source filtering is unavailable until this service sets `log_config.source_label`.</p>
          ) : (
            <p style={mutedTextStyle}>
              Source filter matches the configured `{service.log_config.source_label}` Loki label exactly.
              {serviceLogSourceOptions.length ? ` Discovered values: ${serviceLogSourceOptions.join(", ")}.` : ""}
            </p>
          )}
          {serviceLogsLoading ? <p style={mutedTextStyle}>Loading service logs...</p> : null}
          {serviceLogsError ? <p style={{ ...mutedTextStyle, color: "var(--studio-danger)" }}>{serviceLogsError}</p> : null}
          {!serviceLogsLoading && !serviceLogsError && !filteredServiceLogs.length ? (
            <p style={mutedTextStyle}>No service logs matched the current filters.</p>
          ) : null}
          {filteredServiceLogs.length ? (
            <div className="studio-stack-sm studio-surface-scroll">
              {filteredServiceLogs.map((item, index) => (
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
                          {formatLogLevel(item.level)} · {item.task_id || "service scope"}
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
      </div>
      {confirmation ? (
        <ConfirmationDialog
          title={confirmation.title}
          body={<p>{confirmation.body}</p>}
          confirmLabel={confirmation.confirmLabel}
          challengeText={confirmation.challengeText}
          challengeLabel={confirmation.challengeLabel}
          pending={confirmationPending}
          tone={confirmation.challengeText ? "danger" : "default"}
          onCancel={() => setConfirmation(null)}
          onConfirm={() => void confirmation.onConfirm()}
        />
      ) : null}
    </div>
  );
}

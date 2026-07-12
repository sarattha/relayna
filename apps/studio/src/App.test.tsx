import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { buildServicePayload, serviceToDraft } from "./api";
import { App } from "./App";
import type { ExecutionGraph, ServiceRecord } from "./types";

vi.mock("@xyflow/react", async () => {
  const React = await import("react");

  return {
    Background: () => React.createElement("div", { "data-testid": "rf-background" }),
    Controls: () => React.createElement("div", { "data-testid": "rf-controls" }),
    MiniMap: () => React.createElement("div", { "data-testid": "rf-minimap" }),
    Panel: ({ children }: { children: React.ReactNode }) =>
      React.createElement("div", { "data-testid": "rf-panel" }, children),
    ReactFlowProvider: ({ children }: { children: React.ReactNode }) =>
      React.createElement("div", { "data-testid": "rf-provider" }, children),
    ReactFlow: ({
      children,
      edges,
      nodes,
    }: {
      children: React.ReactNode;
      edges: Array<{ id: string; label?: string }>;
      nodes: Array<{ id: string; data: { label: React.ReactNode } }>;
    }) =>
      React.createElement(
        "div",
        { "data-testid": "rf-root" },
        React.createElement(
          "div",
          { "data-testid": "rf-nodes" },
          nodes.map((node) => React.createElement("div", { key: node.id }, node.data.label)),
        ),
        React.createElement(
          "div",
          { "data-testid": "rf-edges" },
          edges.map((edge) => React.createElement("div", { key: edge.id }, edge.label)),
        ),
        children,
      ),
  };
});

type MockServiceRecord = {
  service_id: string;
  name: string;
  base_url: string;
  environment: string;
  tags: string[];
  auth_mode: string;
  status: "registered" | "healthy" | "unavailable" | "disabled";
  capabilities?: Record<string, unknown> | null;
  last_seen_at?: string | null;
  log_config?: Record<string, unknown> | null;
  metrics_config?: Record<string, unknown> | null;
  trace_config?: Record<string, unknown> | null;
  health?: Record<string, unknown> | null;
};

const fetchMock = vi.fn<typeof fetch>();

class MockEventSource {
  static instances: MockEventSource[] = [];

  url: string;
  listeners = new Map<string, Array<(event: MessageEvent<string>) => void>>();
  closed = false;

  constructor(url: string) {
    this.url = url;
    MockEventSource.instances.push(this);
  }

  addEventListener(type: string, listener: (event: MessageEvent<string>) => void) {
    this.listeners.set(type, [...(this.listeners.get(type) || []), listener]);
  }

  close() {
    this.closed = true;
  }

  emit(type: string, data: unknown) {
    const listeners = this.listeners.get(type) || [];
    const event = { data: JSON.stringify(data) } as MessageEvent<string>;
    listeners.forEach((listener) => listener(event));
  }

  emitRaw(type: string, data: string) {
    const listeners = this.listeners.get(type) || [];
    const event = { data } as MessageEvent<string>;
    listeners.forEach((listener) => listener(event));
  }
}

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function isoToLocalDateTime(value: string) {
  const date = new Date(value);
  const offsetMs = date.getTimezoneOffset() * 60 * 1000;
  return new Date(date.getTime() - offsetMs).toISOString().slice(0, 16);
}

function serviceListResponse(services: MockServiceRecord[]) {
  return jsonResponse({ count: services.length, services });
}

function buildMockService(): MockServiceRecord {
  return {
    service_id: "payments-api",
    name: "Payments API",
    base_url: "https://payments.example.test",
    environment: "prod",
    tags: ["core", "money"],
    auth_mode: "internal_network",
    status: "registered",
    capabilities: { supported_routes: ["status.latest", "workflow.topology", "broker.dlq.messages"] },
    last_seen_at: "2026-04-08T12:00:00Z",
    health: {
      service_id: "payments-api",
      registry_status: "registered",
      http_status: { state: "reachable", checked_at: "2026-04-08T12:00:00Z", error_detail: null },
      capability_status: {
        state: "unknown",
        checked_at: "2026-04-08T12:00:00Z",
        last_successful_at: null,
        error_detail: null,
      },
      observation_freshness: {
        state: "fresh",
        latest_status_event_at: "2026-04-08T11:00:00Z",
        latest_observation_event_at: "2026-04-08T12:34:56Z",
        latest_ingested_at: "2026-04-08T12:00:01Z",
      },
      worker_health: {
        state: "unknown",
        reported_at: null,
        latest_heartbeat_at: null,
        workers: [],
        detail: "unknown",
      },
      last_checked_at: "2026-04-08T12:00:00Z",
      overall_status: "unknown",
    },
    log_config: {
      provider: "loki",
      base_url: "https://loki.example.test",
      service_selector_labels: { app: "payments-api" },
      source_label: "component",
      pod_label: "pod",
      pod_match_mode: "exact",
      pod_value_template: "{pod}",
      task_id_label: "task_id",
      correlation_id_label: "correlation_id",
      level_label: "level",
      task_match_mode: "label",
      task_match_template: null,
    },
    trace_config: {
      provider: "tempo",
      base_url: "https://tempo.example.test",
      public_base_url: "https://tempo-public.example.test",
    },
  };
}

const services: MockServiceRecord[] = [buildMockService()];

function taskDetailResponse(options?: { taskId?: string; dlqItems?: Array<Record<string, unknown>> }) {
  const taskId = options?.taskId || "task-123";
  const dlqItems =
    options?.dlqItems ||
    [
      {
        service_id: "payments-api",
        dlq_id: "dlq-1",
        queue_name: "payments.dlq",
        source_queue_name: "payments.stage",
        retry_queue_name: "payments.retry",
        task_id: taskId,
        correlation_id: "corr-123",
        reason: "upstream_timeout",
        retry_attempt: 2,
        max_retries: 5,
        body_encoding: "json",
        dead_lettered_at: "2026-04-08T10:00:00Z",
        state: "dead_lettered",
        replay_count: 0,
      },
    ];
  return {
    service: services[0],
    service_id: "payments-api",
    task_id: taskId,
    task_ref: {
      service_id: "payments-api",
      task_id: taskId,
      correlation_id: "corr-123",
      parent_refs: [{ service_id: "upstream-api", task_id: "parent-1" }],
      child_refs: [{ service_id: "payments-api", task_id: "child-1" }],
    },
    latest_status: {
      service_id: "payments-api",
      task_id: taskId,
      task_ref: {
        service_id: "payments-api",
        task_id: taskId,
        correlation_id: "corr-123",
        parent_refs: [],
        child_refs: [],
      },
      event: { status: "running" },
    },
    history: {
      service_id: "payments-api",
      task_id: taskId,
      count: 2,
      events: [{ task_id: taskId, status: "queued" }, { task_id: taskId, status: "running" }],
    },
    dlq_messages: {
      service_id: "payments-api",
      items: dlqItems,
      next_cursor: null,
    },
    execution_graph: {
      service_id: "payments-api",
      task_id: taskId,
      task_ref: {
        service_id: "payments-api",
        task_id: taskId,
        correlation_id: "corr-123",
        parent_refs: [],
        child_refs: [],
      },
      topology_kind: "shared_tasks_shared_status",
      summary: {
        status: "running",
        started_at: "2026-04-08T10:00:00Z",
        ended_at: null,
        duration_ms: 3200,
        graph_completeness: "complete",
      },
      nodes: [
        { id: "task", kind: "task", label: taskId, task_id: taskId },
        { id: "attempt", kind: "task_attempt", label: "attempt-1", task_id: taskId },
        {
          id: "resource-start",
          kind: "resource_sample",
          label: "start resource sample",
          task_id: taskId,
          timestamp: "2026-04-08T10:00:00Z",
          annotations: { sample_kind: "start", cpu_process_seconds: 10, memory_rss_bytes: 268435456 },
        },
        {
          id: "resource-end",
          kind: "resource_sample",
          label: "end resource sample",
          task_id: taskId,
          timestamp: "2026-04-08T10:00:03Z",
          annotations: { sample_kind: "end", cpu_process_seconds: 10.75, memory_rss_bytes: 402653184 },
        },
      ],
      edges: [{ source: "task", target: "attempt", kind: "stage_transitioned_to" }],
      annotations: {},
      related_task_ids: [taskId, "child-1"],
    },
    joined_refs: [
      {
        task_ref: {
          service_id: "fraud-api",
          task_id: "fraud-999",
          correlation_id: "corr-123",
          parent_refs: [],
          child_refs: [],
        },
        join_kind: "correlation_id",
        matched_value: "corr-123",
      },
    ],
    join_warnings: [
      {
        code: "ambiguous_lineage",
        detail: "Multiple lineage candidates were available.",
        join_kind: "workflow_lineage",
      },
    ],
    errors: [{ code: "unsupported_route", detail: "history route missing", retryable: false }],
  };
}

function taskTracePathResponse(taskId = "task-123") {
  return {
    service_id: "payments-api",
    task_id: taskId,
    summary: {
      status: "running",
      started_at: "2026-04-08T10:00:00Z",
      ended_at: "2026-04-08T10:00:03Z",
      duration_ms: 3000,
      graph_completeness: "complete",
      trace_ids: ["trace-abc"],
      node_count: 2,
      edge_count: 1,
      span_count: 1,
      event_count: 2,
      dlq_count: 1,
      live_state_counts: { running: 1, dead_lettered: 1 },
    },
    nodes: [
      {
        id: "dlq",
        kind: "dlq_record",
        label: "gateway_timeout",
        task_id: taskId,
        state: "dead_lettered",
        queue_name: "payments.dlq",
        stage: null,
        attempt: 3,
        trace_id: null,
        span_id: null,
        parent_span_id: null,
        started_at: "2026-04-08T09:59:58Z",
        ended_at: null,
        duration_ms: null,
        evidence: [{ source: "dlq", source_id: "dlq", label: "gateway_timeout", timestamp: "2026-04-08T09:59:58Z", payload: {} }],
      },
      {
        id: "task",
        kind: "task",
        label: taskId,
        task_id: taskId,
        state: "running",
        queue_name: null,
        stage: null,
        attempt: null,
        trace_id: null,
        span_id: null,
        parent_span_id: null,
        started_at: "2026-04-08T10:00:00Z",
        ended_at: null,
        duration_ms: null,
        evidence: [{ source: "graph_node", source_id: "task", label: "task", timestamp: null, payload: {} }],
      },
      {
        id: "attempt",
        kind: "task_attempt",
        label: "attempt-1",
        task_id: taskId,
        state: "running",
        queue_name: "payments.stage",
        stage: null,
        attempt: 1,
        trace_id: "trace-abc",
        span_id: "span-123",
        parent_span_id: "parent-456",
        started_at: "2026-04-08T10:00:00Z",
        ended_at: "2026-04-08T10:00:01Z",
        duration_ms: 1000,
        evidence: [
          { source: "graph_node", source_id: "attempt", label: "task_attempt", timestamp: "2026-04-08T10:00:00Z", payload: {} },
          { source: "span", source_id: "span-123", label: "payments.process_payment", timestamp: "2026-04-08T10:00:00Z", payload: {} },
        ],
      },
    ],
    edges: [
      { id: "task->attempt:1", source: "task", target: "attempt", kind: "stage_transitioned_to", evidence: [] },
      { id: "attempt->dlq:2", source: "attempt", target: "dlq", kind: "dead_lettered_to", evidence: [] },
    ],
    spans: [
      {
        trace_id: "trace-abc",
        span_id: "span-123",
        parent_span_id: "parent-456",
        name: "payments.process_payment",
        kind: "consumer",
        service: "payments-api",
        source: "tempo",
        start_time: "2026-04-08T10:00:00Z",
        end_time: "2026-04-08T10:00:01Z",
        duration_ms: 1000,
        attributes: { "messaging.system": "rabbitmq", task_id: taskId },
        backend_url: "https://tempo-public.example.test/api/traces/trace-abc",
      },
    ],
    events: [],
    dlq_messages: [],
    log_metadata: {
      configured: true,
      provider: "loki",
      source_label: "component",
      task_id_label: "task_id",
      correlation_id_label: "correlation_id",
      task_id: taskId,
      correlation_id: "corr-123",
      query: `${taskId} OR corr-123`,
      from_time: "2026-04-08T10:00:00Z",
      to_time: "2026-04-08T10:00:03Z",
    },
    warnings: [],
  };
}

function metricsResponse(taskId?: string | null) {
  return {
    service_id: "payments-api",
    task_id: taskId || null,
    from: "2026-04-08T10:00:00Z",
    to: "2026-04-08T10:05:00Z",
    step_seconds: 30,
    approximate: Boolean(taskId),
    warnings: taskId
      ? ["Task-window Kubernetes pod/container metrics are approximate for long-running workers that process many tasks."]
      : [],
    series: [
      {
        metric: "cpu_usage",
        unit: "cores",
        labels: { pod: "payments-abc", container: "worker" },
        points: [{ timestamp: "2026-04-08T10:05:00Z", value: 0.25 }],
      },
      {
        metric: "cpu_usage",
        unit: "cores",
        labels: { pod: "payments-def", container: "worker" },
        points: [{ timestamp: "2026-04-08T10:05:00Z", value: 0.5 }],
      },
      {
        metric: "memory_usage",
        unit: "bytes",
        labels: { pod: "payments-abc", container: "worker" },
        points: [{ timestamp: "2026-04-08T10:05:00Z", value: 268435456 }],
      },
      {
        metric: "memory_usage",
        unit: "bytes",
        labels: { pod: "payments-def", container: "worker" },
        points: [{ timestamp: "2026-04-08T10:05:00Z", value: 134217728 }],
      },
    ],
  };
}

async function flushRenderPromises() {
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
    await Promise.resolve();
    await Promise.resolve();
  });
}

function customPodLabelMetricsResponse() {
  return {
    ...metricsResponse(null),
    series: [
      {
        metric: "cpu_usage",
        unit: "cores",
        labels: { kubernetes_pod_name: "payments-worker-prometheus", container: "worker", phase: "Running" },
        points: [{ timestamp: "2026-04-08T10:05:00Z", value: 0.5 }],
      },
    ],
  };
}

describe("App", () => {
  beforeEach(() => {
    fetchMock.mockReset();
    MockEventSource.instances = [];
    vi.stubGlobal("fetch", fetchMock);
    vi.stubGlobal("EventSource", MockEventSource as unknown as typeof EventSource);
    window.history.replaceState({}, "", "/services");
    services.splice(0, services.length, buildMockService());

    fetchMock.mockImplementation(async (input, init) => {
      const url = String(input);
      const method = init?.method || "GET";

      if (url === "/studio/services" && method === "GET") {
        return serviceListResponse(services);
      }
      if (url.startsWith("/studio/services/payments-api/events?") && method === "GET") {
        return jsonResponse({ count: 0, items: [], next_cursor: null });
      }
      if (url.startsWith("/studio/services/payments-api/logs?") && method === "GET") {
        const parsed = new URL(url, "http://studio.test");
        const source = parsed.searchParams.get("source");
        if (source && source !== "runtime-worker") {
          throw new Error(`Unhandled service log source filter: ${source}`);
        }
        return jsonResponse({
          count: 6,
          items: [
            {
              service_id: "payments-api",
              timestamp: "2026-04-08T10:00:00Z",
              level: "info",
              source: "runtime-worker",
              message: "\u001b[32mservice log line\u001b[0m\nsecond line",
              fields: {},
            },
            {
              service_id: "payments-api",
              timestamp: "2026-04-08T10:01:00Z",
              level: "info",
              source: "api",
              message: "api log line",
              fields: {},
            },
            {
              service_id: "payments-api",
              timestamp: "2026-04-08T10:02:00Z",
              level: "info",
              source: "runtime-worker",
              message: "{\"event\":\"service_json\",\"details\":{\"attempt\":2,\"status\":\"ok\"}}",
              fields: {},
            },
            {
              service_id: "payments-api",
              timestamp: "2026-04-08T10:03:00Z",
              level: "info",
              source: "runtime-worker",
              message: "[{\"step\":\"queued\"},{\"step\":\"running\"}]",
              fields: {},
            },
            {
              service_id: "payments-api",
              timestamp: "2026-04-08T10:04:00Z",
              level: "warn",
              source: "api",
              message: "{\"broken\": true",
              fields: {},
            },
            {
              service_id: "payments-api",
              timestamp: "2026-04-08T10:05:00Z",
              level: "info",
              source: "api",
              message: "  true  ",
              fields: {},
            },
          ],
          next_cursor: null,
        });
      }
      if (url === "/studio/services/payments-api/refresh" && method === "POST") {
        services[0] = {
          ...services[0],
          status: "healthy",
          last_seen_at: "2026-04-08T12:45:00Z",
          health: {
            ...services[0].health,
            registry_status: "healthy",
            capability_status: {
              state: "fresh",
              checked_at: "2026-04-08T12:45:00Z",
              last_successful_at: "2026-04-08T12:45:00Z",
              error_detail: null,
            },
            worker_health: {
              state: "healthy",
              reported_at: "2026-04-08T12:44:59Z",
              latest_heartbeat_at: "2026-04-08T12:44:59Z",
              workers: [],
              detail: null,
            },
            last_checked_at: "2026-04-08T12:45:00Z",
            overall_status: "healthy",
          },
        };
        return jsonResponse(services[0]);
      }
      if (url === "/studio/services/payments-api/workflow/topology" && method === "GET") {
        return jsonResponse({
          workflow_exchange: "payments.workflow",
          status_queue: "payments.status",
          stages: [
            {
              id: "validate",
              name: "Validate",
              queue: "payments.validate",
              binding_keys: ["payments.validate"],
              publish_routing_key: "payments.authorize",
              queue_arguments: {},
              tags: [],
              accepted_actions: [],
              produced_actions: [],
              allowed_next_stages: ["authorize"],
              terminal: false,
              dedup_key_fields: [],
            },
          ],
          entry_routes: [{ name: "start", routing_key: "payments.validate", target_stage: "validate" }],
          edges: [{ source: "validate", target: "authorize", routing_key: "payments.authorize" }],
        });
      }
      if (url === "/studio/services/payments-api/dlq/messages?limit=50" && method === "GET") {
        return jsonResponse({
          service_id: "payments-api",
          items: [
            {
              service_id: "payments-api",
              dlq_id: "dlq-1",
              queue_name: "payments.dlq",
              source_queue_name: "payments.stage",
              retry_queue_name: "payments.retry",
              task_id: "task-123",
              correlation_id: "corr-123",
              reason: "upstream_timeout",
              retry_attempt: 2,
              max_retries: 5,
              body_encoding: "json",
              dead_lettered_at: "2026-04-08T10:00:00Z",
              state: "dead_lettered",
              replay_count: 0,
              task_ref: {
                service_id: "payments-api",
                task_id: "task-123",
                correlation_id: "corr-123",
                parent_refs: [],
                child_refs: [],
              },
            },
          ],
          next_cursor: "cursor-2",
        });
      }
      if (url === "/studio/services/payments-api/dlq/messages?limit=50&cursor=cursor-2" && method === "GET") {
        return jsonResponse({ service_id: "payments-api", items: [], next_cursor: null });
      }
      if (url === "/studio/services/payments-api/broker/dlq/messages?limit=50&task_id=task-123" && method === "GET") {
        return jsonResponse({
          service_id: "payments-api",
          items: [
            {
              service_id: "payments-api",
              queue_name: "payments.dlq",
              message_key: "msg-1",
              task_id: "task-123",
              correlation_id: "corr-123",
              reason: "broker_rejected",
              source_queue_name: "payments.stage",
              content_type: "application/json",
              body_encoding: "json",
              dead_lettered_at: "2026-04-08T10:00:00Z",
              headers: { task_id: "task-123" },
              body: { task_id: "task-123" },
              raw_body_b64: "eyJ0YXNrX2lkIjoidGFzay0xMjMifQ==",
              redelivered: false,
              task_ref: {
                service_id: "payments-api",
                task_id: "task-123",
                correlation_id: "corr-123",
                parent_refs: [],
                child_refs: [],
              },
            },
          ],
        });
      }
      if (url === "/studio/failed-task-email-settings" && method === "GET") {
        return jsonResponse({
          configured: true,
          enabled: false,
          batch_wait_seconds: 0,
          max_batch_wait_seconds: 604800,
          receivers: ["ops@example.com"],
        });
      }
      if (url === "/studio/failed-task-email-settings" && method === "PATCH") {
        const body = JSON.parse(String(init?.body || "{}")) as {
          enabled?: boolean;
          batch_wait_seconds?: number;
        };
        return jsonResponse({
          configured: true,
          enabled: body.enabled ?? true,
          batch_wait_seconds: body.batch_wait_seconds ?? 0,
          max_batch_wait_seconds: 604800,
          receivers: ["ops@example.com"],
        });
      }
      if (url === "/studio/failed-tasks?limit=50&investigation_status=unreviewed" && method === "GET") {
        return jsonResponse({
          items: [
            {
              service_id: "payments-api",
              service_name: "Payments API",
              failure_id: "failure-1",
              task_id: "task-123",
              correlation_id: "corr-123",
              queue_name: "payments.stage",
              source_queue_name: "payments.stage",
              retry_queue_name: "payments.retry",
              dlq_name: "payments.dlq",
              status: "DLQ",
              attempt: 3,
              max_attempts: 3,
              failed_at: "2026-05-26T10:30:00Z",
              error_type: "RuntimeError",
              error_message: "boom",
              investigation_status: "unreviewed",
              retry_status: "not_retried",
              payload_available: true,
              task_ref: {
                service_id: "payments-api",
                task_id: "task-123",
                correlation_id: "corr-123",
                parent_refs: [],
                child_refs: [],
              },
            },
          ],
          next_cursor: null,
          errors: [],
          scanned_services: ["payments-api"],
        });
      }
      if (url === "/studio/failed-tasks/payments-api/failure-1" && method === "GET") {
        return jsonResponse({
          service_id: "payments-api",
          service_name: "Payments API",
          failure_id: "failure-1",
          task_id: "task-123",
          correlation_id: "corr-123",
          queue_name: "payments.stage",
          source_queue_name: "payments.stage",
          retry_queue_name: "payments.retry",
          dlq_name: "payments.dlq",
          status: "DLQ",
          attempt: 3,
          max_attempts: 3,
          failed_at: "2026-05-26T10:30:00Z",
          error_type: "RuntimeError",
          error_message: "boom",
          investigation_status: "unreviewed",
          retry_status: "not_retried",
          payload_available: true,
          body: { task_id: "task-123" },
          input_preview: { task_id: "task-123" },
          metadata: { tenant: "default" },
          last_logs: [{ message: "failed" }],
          raw_body_b64: "e30=",
          body_encoding: "json",
          task_ref: {
            service_id: "payments-api",
            task_id: "task-123",
            correlation_id: "corr-123",
            parent_refs: [],
            child_refs: [],
          },
        });
      }
      if (
        url.startsWith("/studio/tasks/search?") &&
        new URL(url, "http://studio.test").searchParams.get("task_id") === "task-123" &&
        method === "GET"
      ) {
        return jsonResponse({
          count: 1,
          items: [
            {
              service_id: "payments-api",
              service_name: "Payments API",
              environment: "prod",
              task_id: "task-123",
              correlation_id: "corr-123",
              status: "running",
              stage: "authorize",
              first_seen_at: "2026-04-08T10:00:00Z",
              last_seen_at: "2026-04-08T10:05:00Z",
              latest_event_type: "task.running",
              latest_event_at: "2026-04-08T10:05:00Z",
              latest_ingested_at: "2026-04-08T10:05:01Z",
              detail_path: "/studio/tasks/payments-api/task-123",
            },
          ],
          next_cursor: null,
        });
      }
      if (url === "/studio/tasks/search?task_id=task-loki&limit=50" && method === "GET") {
        return jsonResponse({
          count: 1,
          items: [
            {
              service_id: "payments-api",
              service_name: "Payments API",
              environment: "prod",
              task_id: "task-loki",
              correlation_id: "corr-loki",
              first_seen_at: "2026-04-08T10:00:00Z",
              last_seen_at: "2026-04-08T10:05:00Z",
              latest_event_type: "loki.log",
              latest_event_at: "2026-04-08T10:05:00Z",
              latest_ingested_at: null,
              detail_path: "/studio/tasks/payments-api/task-loki",
              source: "loki_fallback",
            },
          ],
          next_cursor: null,
        });
      }
      if (url === "/studio/tasks/payments-api/task-123?join=all" && method === "GET") {
        return jsonResponse(taskDetailResponse());
      }
      if (url === "/studio/tasks/payments-api/task-123/events?limit=50" && method === "GET") {
        return jsonResponse({
          count: 1,
          items: [
            {
              service_id: "payments-api",
              ingest_method: "pull",
              ingested_at: "2026-04-08T10:00:00Z",
              dedupe_key: "evt-1",
              out_of_order: false,
              task_id: "task-123",
              event_type: "task.running",
              source_kind: "status",
              component: "worker",
              timestamp: "2026-04-08T10:00:00Z",
              payload: { status: "running" },
            },
          ],
          next_cursor: null,
        });
      }
      if (url.startsWith("/studio/tasks/payments-api/task-123/logs?") && method === "GET") {
        return jsonResponse({
          count: 5,
          items: [
            {
              service_id: "payments-api",
              task_id: "task-123",
              correlation_id: "corr-123",
              timestamp: "2026-04-08T10:00:00Z",
              level: "info",
              source: "api",
              message: "\u001b[31mtask log line\u001b[0m",
              fields: {},
            },
            {
              service_id: "payments-api",
              task_id: "task-123",
              correlation_id: "corr-123",
              timestamp: "2026-04-08T10:01:00Z",
              level: "info",
              source: "api",
              message: "{\"event\":\"task_json\",\"details\":{\"retry\":1,\"status\":\"running\"}}",
              fields: {},
            },
            {
              service_id: "payments-api",
              task_id: "task-123",
              correlation_id: "corr-123",
              timestamp: "2026-04-08T10:02:00Z",
              level: "info",
              source: "api",
              message: "[{\"stage\":\"received\"},{\"stage\":\"running\"}]",
              fields: {},
            },
            {
              service_id: "payments-api",
              task_id: "task-123",
              correlation_id: "corr-123",
              timestamp: "2026-04-08T10:03:00Z",
              level: "warn",
              source: "api",
              message: "{\"oops\":",
              fields: {},
            },
            {
              service_id: "payments-api",
              task_id: "task-123",
              correlation_id: "corr-123",
              timestamp: "2026-04-08T10:04:00Z",
              level: "info",
              source: "api",
              message: "  null  ",
              fields: {},
            },
          ],
          next_cursor: null,
        });
      }
      if (url === "/studio/tasks/payments-api/task-123/traces" && method === "GET") {
        return jsonResponse({
          service_id: "payments-api",
          task_id: "task-123",
          trace_ids: ["trace-abc"],
          warnings: [],
          spans: [
            {
              trace_id: "trace-abc",
              span_id: "span-123",
              parent_span_id: "parent-456",
              name: "payments.process_payment",
              kind: "consumer",
              service: "payments-api",
              source: "tempo",
              start_time: "2026-04-08T10:00:00Z",
              end_time: "2026-04-08T10:00:01Z",
              duration_ms: 1000,
              attributes: { "messaging.system": "rabbitmq", task_id: "task-123" },
              backend_url: "https://tempo-public.example.test/api/traces/trace-abc",
            },
          ],
        });
      }
      if (url === "/studio/tasks/payments-api/task-123/trace-path" && method === "GET") {
        return jsonResponse(taskTracePathResponse());
      }
      if (url === "/studio/tasks/payments-api/task-empty-dlq/trace-path" && method === "GET") {
        return jsonResponse(taskTracePathResponse("task-empty-dlq"));
      }
      if (url.startsWith("/studio/tasks/payments-api/task-empty-dlq/logs?") && method === "GET") {
        return jsonResponse({ count: 0, items: [], next_cursor: null });
      }
      throw new Error(`Unhandled fetch: ${method} ${url}`);
    });
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it("renders the incident-first overview at the default route", async () => {
    window.history.replaceState({}, "", "/");
    services[0] = {
      ...services[0],
      health: { ...(services[0].health || {}), overall_status: "degraded" },
    };
    services.push({
      ...buildMockService(),
      service_id: "unrefreshed-api",
      name: "Unrefreshed API",
      base_url: "https://unrefreshed.example.test",
      health: { ...(buildMockService().health || {}), overall_status: "unknown" },
    });
    render(<App />);

    expect(await screen.findByRole("heading", { name: "What needs attention now" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Open service" })).toHaveAttribute("href", "/services/payments-api");
    expect(window.location.pathname).toBe("/");
    expect(screen.getByRole("link", { name: "Manage registry" })).toHaveAttribute("href", "/services");
    expect(screen.getByRole("link", { name: "Overview" })).toHaveAttribute("aria-current", "page");
    expect(within(screen.getByText("Unknown").closest("article")!).getByText("1")).toBeInTheDocument();
    const brandLink = screen.getByRole("link", { name: "Relayna Studio overview" });
    expect(brandLink.querySelector("img.studio-brand__mark")).toHaveAttribute("src", expect.stringContaining("relayna-mark"));
    fireEvent.change(screen.getByLabelText("Environment"), { target: { value: "prod" } });
    await waitFor(() => expect(window.location.search).toBe("?environment=prod"));
    fireEvent.change(screen.getByLabelText("Find task"), { target: { value: "task-123" } });
    fireEvent.click(screen.getByRole("button", { name: "Search tasks" }));
    await waitFor(() => expect(window.location.pathname).toBe("/tasks/search"));
    expect(window.location.search).toBe("?task_id=task-123");
  });

  it("polls the registered services list silently and stops after unmount", async () => {
    vi.useFakeTimers();
    await import("./pages/ServicesPage");

    const baseImpl = fetchMock.getMockImplementation();
    let serviceListCalls = 0;
    let resolveRefresh: ((response: Response) => void) | null = null;
    async function flushServicesRender() {
      await act(async () => {
        await Promise.resolve();
        await Promise.resolve();
      });
    }

    fetchMock.mockImplementation(async (input, init) => {
      const url = String(input);
      const method = init?.method || "GET";

      if (url === "/studio/services" && method === "GET") {
        serviceListCalls += 1;
        if (serviceListCalls === 1) {
          return serviceListResponse(services);
        }
        if (serviceListCalls === 2) {
          return await new Promise<Response>((resolve) => {
            resolveRefresh = resolve;
          });
        }
      }

      return await baseImpl!(input, init);
    });

    const { unmount } = render(<App />);

    await flushServicesRender();
    expect(screen.getByText("Registered Services")).toBeInTheDocument();

    expect(serviceListCalls).toBe(1);
    expect(screen.getAllByText("Payments API").length).toBeGreaterThan(0);
    expect(screen.queryByText("Loading services...")).not.toBeInTheDocument();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(60_000);
    });

    expect(serviceListCalls).toBe(2);
    expect(screen.queryByText("Loading services...")).not.toBeInTheDocument();

    services[0] = {
      ...services[0],
      status: "healthy",
      health: {
        ...services[0].health,
        registry_status: "healthy",
        overall_status: "healthy",
      },
    };
    await act(async () => {
      resolveRefresh?.(serviceListResponse(services));
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(screen.getAllByText("healthy").length).toBeGreaterThan(0);
    expect(screen.queryByText("Loading services...")).not.toBeInTheDocument();

    unmount();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(60_000);
    });

    expect(serviceListCalls).toBe(2);
  });

  it("surfaces registry save failures instead of silently swallowing them", async () => {
    const baseImpl = fetchMock.getMockImplementation();
    fetchMock.mockImplementation(async (input, init) => {
      const url = String(input);
      const method = init?.method || "GET";
      if (url === "/studio/services" && method === "POST") {
        return jsonResponse({ detail: "Service id already exists." }, 409);
      }
      return await baseImpl!(input, init);
    });

    render(<App />);

    await screen.findByText("Registered Services");
    fireEvent.click(screen.getByRole("button", { name: "New Service" }));
    fireEvent.change(screen.getByLabelText("Service id"), { target: { value: "payments-api" } });
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Payments API" } });
    fireEvent.click(screen.getByRole("button", { name: "Register Service" }));

    expect(await screen.findByText("Service id already exists.")).toBeInTheDocument();
    expect(window.location.pathname).toBe("/services");
  });

  it("maps AKS-friendly app and service labels into the generic log configuration payload", async () => {
    let observedPayload:
      | {
          log_config?: {
            pod_label?: string | null;
            pod_match_mode?: string;
            pod_value_template?: string | null;
            source_label?: string | null;
            service_selector_labels?: Record<string, string>;
          } | null;
        }
      | null = null;
    const baseImpl = fetchMock.getMockImplementation();
    fetchMock.mockImplementation(async (input, init) => {
      const url = String(input);
      const method = init?.method || "GET";
      if (url === "/studio/services" && method === "POST") {
        observedPayload = JSON.parse(String(init?.body || "{}"));
        const created = {
          ...buildMockService(),
          service_id: "orders-api",
          name: "Orders API",
          log_config: observedPayload?.log_config ?? null,
        };
        services.push(created);
        return jsonResponse(created);
      }
      return await baseImpl!(input, init);
    });

    render(<App />);

    await screen.findByText("Registered Services");
    fireEvent.click(screen.getByRole("button", { name: "New Service" }));
    fireEvent.change(screen.getByLabelText("Service id"), { target: { value: "orders-api" } });
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Orders API" } });
    fireEvent.change(screen.getByLabelText("Service label key"), { target: { value: "service" } });
    fireEvent.change(screen.getByLabelText("Service label value"), { target: { value: "orders-service" } });
    fireEvent.change(screen.getByLabelText("App label key"), { target: { value: "app" } });
    fireEvent.change(screen.getByLabelText("Log pod label"), { target: { value: "instance" } });
    fireEvent.change(screen.getByLabelText("Log pod match"), { target: { value: "regex" } });
    fireEvent.change(screen.getByLabelText("Log pod value template"), { target: { value: "{namespace}/{pod}:.*" } });
    fireEvent.click(screen.getByRole("button", { name: "Register Service" }));

    await screen.findByText("Registered service 'orders-api'.");
    expect(observedPayload).not.toBeNull();
    if (!observedPayload) {
      throw new Error("Expected the service create payload to be captured.");
    }
    const capturedPayload = observedPayload as {
      log_config?: {
        pod_label?: string | null;
        pod_match_mode?: string;
        pod_value_template?: string | null;
        source_label?: string | null;
        service_selector_labels?: Record<string, string>;
      } | null;
    };
    expect(capturedPayload.log_config?.source_label).toBe("app");
    expect(capturedPayload.log_config?.pod_label).toBe("instance");
    expect(capturedPayload.log_config?.pod_match_mode).toBe("regex");
    expect(capturedPayload.log_config?.pod_value_template).toBe("{namespace}/{pod}:.*");
    expect(capturedPayload.log_config?.service_selector_labels).toEqual({ service: "orders-service" });
  });

  it("clears source_label when the app label key is removed in the editor", async () => {
    let observedPayload:
      | {
          log_config?: {
            source_label?: string | null;
            service_selector_labels?: Record<string, string>;
          } | null;
        }
      | null = null;
    const baseImpl = fetchMock.getMockImplementation();
    fetchMock.mockImplementation(async (input, init) => {
      const url = String(input);
      const method = init?.method || "GET";
      if (url === "/studio/services/payments-api" && method === "PATCH") {
        observedPayload = JSON.parse(String(init?.body || "{}"));
        const updated = {
          ...services[0],
          log_config: observedPayload?.log_config ?? null,
        };
        services[0] = updated;
        return jsonResponse(updated);
      }
      return await baseImpl!(input, init);
    });

    render(<App />);

    await screen.findByText("Registered Services");
    fireEvent.click(screen.getByRole("button", { name: "Edit" }));
    fireEvent.change(screen.getByLabelText("App label key"), { target: { value: "" } });
    fireEvent.click(screen.getByRole("button", { name: "Save Service" }));

    await screen.findByText("Updated service 'payments-api'.");
    expect(observedPayload).not.toBeNull();
    if (!observedPayload) {
      throw new Error("Expected the service update payload to be captured.");
    }
    const capturedPayload: {
      log_config?: {
        source_label?: string | null;
        service_selector_labels?: Record<string, string>;
      } | null;
    } = observedPayload;
    expect(capturedPayload.log_config?.source_label).toBeNull();
    expect(capturedPayload.log_config?.service_selector_labels).toEqual({ app: "payments-api" });
  });

  it("navigates from the service list to the routed service detail page", async () => {
    render(<App />);

    fireEvent.click(await screen.findByRole("button", { name: "Reload List" }));
    fireEvent.click(await screen.findByRole("link", { name: "View" }));

    expect(await screen.findByRole("heading", { name: "Payments API" })).toBeInTheDocument();
    await waitFor(() => expect(window.location.pathname).toBe("/services/payments-api"));
    expect(screen.getByText("Recent Activity")).toBeInTheDocument();
    expect(screen.getByText("Service Logs")).toBeInTheDocument();
    expect(screen.getByText(new Date("2026-04-08T12:34:56Z").toLocaleString())).toBeInTheDocument();
    expect(screen.getAllByText("runtime-worker").length).toBeGreaterThan(0);
    expect(screen.getByText("service log line")).toBeInTheDocument();
    expect(screen.getByText("second line")).toBeInTheDocument();
    expect(screen.getByText(/"event": "service_json"/)).toBeInTheDocument();
    expect(screen.getByText(/"attempt": 2/)).toBeInTheDocument();
    expect(screen.getByText(/"step": "queued"/)).toBeInTheDocument();
    expect(screen.getByText("{\"broken\": true")).toBeInTheDocument();
    expect(screen.getByText("true")).toBeInTheDocument();
    expect(screen.getAllByText(/Auto window:/).length).toBeGreaterThan(0);
    expect(document.body.textContent).not.toContain("\u001b[32m");
  });

  it("applies the service log source filter through the Studio route", async () => {
    window.history.replaceState({}, "", "/services/payments-api");

    render(<App />);

    expect(await screen.findByRole("heading", { name: "Payments API" })).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Service log source"), { target: { value: "runtime-worker" } });
    fireEvent.click(screen.getByRole("button", { name: "Reload Logs" }));

    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith("/studio/services/payments-api/logs?limit=20&source=runtime-worker", undefined),
    );
  });

  it("renders current service pods and filters logs and metric charts by selected pods", async () => {
    window.history.replaceState({}, "", "/services/payments-api");
    services[0] = {
      ...services[0],
      metrics_config: {
        provider: "prometheus",
        base_url: "https://prometheus.example.test",
        namespace: "prod",
        service_selector_labels: { app: "payments-api" },
        namespace_label: "namespace",
        pod_label: "kubernetes_pod_name",
        container_label: "container",
        step_seconds: 30,
        task_window_padding_seconds: 120,
      },
    };

    const baseImpl = fetchMock.getMockImplementation();
    fetchMock.mockImplementation(async (input, init) => {
      const url = String(input);
      const method = init?.method || "GET";
      if (url === "/studio/services/payments-api/pods" && method === "GET") {
        return jsonResponse({
          service_id: "payments-api",
          count: 2,
          pods: [
            {
              name: "payments-api-abc",
              namespace: "prod",
              phase: "Running",
              labels: { app: "service-api" },
            },
            {
              name: "payments-worker-def",
              namespace: "prod",
              phase: "Running",
              labels: { app: "worker" },
            },
          ],
        });
      }
      return baseImpl?.(input, init) ?? jsonResponse({});
    });

    render(<App />);

    expect(await screen.findByText("Service Pods")).toBeInTheDocument();
    expect((await screen.findAllByText("payments-worker-def")).length).toBeGreaterThan(0);
    expect(await screen.findByText("Selected pods: payments-api-abc, payments-worker-def")).toBeInTheDocument();

    await waitFor(() => {
      const matchingWorkerLogCall = fetchMock.mock.calls.find(([input]) => {
        const parsed = new URL(String(input), "http://studio.test");
        return (
          parsed.pathname === "/studio/services/payments-api/logs" &&
          parsed.searchParams.get("pod") === "payments-worker-def"
        );
      });
      const matchingApiLogCall = fetchMock.mock.calls.find(([input]) => {
        const parsed = new URL(String(input), "http://studio.test");
        return parsed.pathname === "/studio/services/payments-api/logs" && parsed.searchParams.get("pod") === "payments-api-abc";
      });
      const matchingWorkerMetricCall = fetchMock.mock.calls.find(([input]) => {
        const parsed = new URL(String(input), "http://studio.test");
        return (
          parsed.pathname === "/studio/services/payments-api/metrics" &&
          parsed.searchParams.get("pod") === "payments-worker-def" &&
          parsed.searchParams.get("split_by_pod") === "true"
        );
      });
      const matchingApiMetricCall = fetchMock.mock.calls.find(([input]) => {
        const parsed = new URL(String(input), "http://studio.test");
        return (
          parsed.pathname === "/studio/services/payments-api/metrics" &&
          parsed.searchParams.get("pod") === "payments-api-abc" &&
          parsed.searchParams.get("split_by_pod") === "true"
        );
      });
      expect(matchingWorkerLogCall).toBeTruthy();
      expect(matchingApiLogCall).toBeTruthy();
      expect(matchingWorkerMetricCall).toBeTruthy();
      expect(matchingApiMetricCall).toBeTruthy();
    });

    fireEvent.click(screen.getByRole("button", { name: /payments-worker-def/ }));

    await waitFor(() => {
      expect(screen.getByText("Selected pods: payments-api-abc")).toBeInTheDocument();
    });

    const callsBeforeDeselectAll = fetchMock.mock.calls.length;
    fireEvent.click(screen.getByRole("button", { name: "Deselect All Pods" }));

    await waitFor(() => {
      expect(screen.getByText("Selected pods: none")).toBeInTheDocument();
    });
    expect(screen.getByRole("button", { name: "Deselect All Pods" })).toBeDisabled();
    await waitFor(() => {
      expect(screen.getByText("No service logs matched the current filters.")).toBeInTheDocument();
      expect(screen.getByText("No pod metrics matched the selected pod and window.")).toBeInTheDocument();
      expect(screen.getByText("No pods selected.")).toBeInTheDocument();
    });
    const callsAfterDeselectAll = fetchMock.mock.calls.slice(callsBeforeDeselectAll);
    const matchingUnfilteredLogCall = callsAfterDeselectAll.find(([input]) => {
      const parsed = new URL(String(input), "http://studio.test");
      return parsed.pathname === "/studio/services/payments-api/logs" && !parsed.searchParams.has("pod");
    });
    const matchingUnfilteredMetricCall = callsAfterDeselectAll.find(([input]) => {
      const parsed = new URL(String(input), "http://studio.test");
      return (
        parsed.pathname === "/studio/services/payments-api/metrics" &&
        parsed.searchParams.get("split_by_pod") === "true" &&
        !parsed.searchParams.has("pod")
      );
    });
    expect(matchingUnfilteredLogCall).toBeFalsy();
    expect(matchingUnfilteredMetricCall).toBeFalsy();

    fireEvent.click(screen.getByRole("button", { name: "Select All Pods" }));
    expect(await screen.findByText("Selected pods: payments-api-abc, payments-worker-def")).toBeInTheDocument();
  });

  it("does not carry explicit empty pod selection across service navigation", async () => {
    window.history.replaceState({}, "", "/services/payments-api");
    const metricsConfig = {
      provider: "prometheus",
      base_url: "https://prometheus.example.test",
      namespace: "prod",
      service_selector_labels: { app: "payments-api" },
      namespace_label: "namespace",
      pod_label: "kubernetes_pod_name",
      container_label: "container",
      step_seconds: 30,
      task_window_padding_seconds: 120,
    };
    services[0] = {
      ...services[0],
      metrics_config: metricsConfig,
    };
    services.push({
      ...buildMockService(),
      service_id: "orders-api",
      name: "Orders API",
      base_url: "https://orders.example.test",
      metrics_config: {
        ...metricsConfig,
        service_selector_labels: { app: "orders-api" },
      },
    });

    const baseImpl = fetchMock.getMockImplementation();
    fetchMock.mockImplementation(async (input, init) => {
      const url = String(input);
      const method = init?.method || "GET";
      if (url === "/studio/services/payments-api/pods" && method === "GET") {
        return jsonResponse({
          service_id: "payments-api",
          count: 2,
          pods: [
            { name: "payments-api-abc", namespace: "prod", phase: "Running", labels: { app: "service-api" } },
            { name: "payments-worker-def", namespace: "prod", phase: "Running", labels: { app: "worker" } },
          ],
        });
      }
      if (url === "/studio/services/orders-api/pods" && method === "GET") {
        return jsonResponse({
          service_id: "orders-api",
          count: 2,
          pods: [
            { name: "orders-api-abc", namespace: "prod", phase: "Running", labels: { app: "service-api" } },
            { name: "orders-worker-def", namespace: "prod", phase: "Running", labels: { app: "worker" } },
          ],
        });
      }
      if (url.startsWith("/studio/services/orders-api/events?") && method === "GET") {
        return jsonResponse({ count: 0, items: [], next_cursor: null });
      }
      if (url.startsWith("/studio/services/orders-api/logs?") && method === "GET") {
        return jsonResponse({ count: 0, items: [], next_cursor: null });
      }
      if (url.startsWith("/studio/services/orders-api/metrics") && method === "GET") {
        return jsonResponse(metricsResponse(null));
      }
      return baseImpl?.(input, init) ?? jsonResponse({});
    });

    render(<App />);

    expect(await screen.findByText("Selected pods: payments-api-abc, payments-worker-def")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Deselect All Pods" }));
    expect(await screen.findByText("Selected pods: none")).toBeInTheDocument();

    act(() => {
      window.history.pushState({}, "", "/services/orders-api");
      window.dispatchEvent(new PopStateEvent("popstate"));
    });

    expect(await screen.findByRole("heading", { name: "Orders API" })).toBeInTheDocument();
    expect(await screen.findByText("Selected pods: orders-api-abc, orders-worker-def")).toBeInTheDocument();
    await waitFor(() => {
      const matchingOrdersLogCall = fetchMock.mock.calls.find(([input]) => {
        const parsed = new URL(String(input), "http://studio.test");
        return parsed.pathname === "/studio/services/orders-api/logs" && parsed.searchParams.get("pod") === "orders-api-abc";
      });
      const matchingOrdersMetricCall = fetchMock.mock.calls.find(([input]) => {
        const parsed = new URL(String(input), "http://studio.test");
        return (
          parsed.pathname === "/studio/services/orders-api/metrics" &&
          parsed.searchParams.get("pod") === "orders-worker-def" &&
          parsed.searchParams.get("split_by_pod") === "true"
        );
      });
      expect(matchingOrdersLogCall).toBeTruthy();
      expect(matchingOrdersMetricCall).toBeTruthy();
    });
  });

  it("keeps default all-pods selection when pod refresh discovers a new pod", async () => {
    vi.useFakeTimers();
    window.history.replaceState({}, "", "/services/payments-api");
    services[0] = {
      ...services[0],
      metrics_config: {
        provider: "prometheus",
        base_url: "https://prometheus.example.test",
        namespace: "prod",
        service_selector_labels: { app: "payments-api" },
        namespace_label: "namespace",
        pod_label: "kubernetes_pod_name",
        container_label: "container",
        step_seconds: 30,
        task_window_padding_seconds: 120,
      },
    };

    const baseImpl = fetchMock.getMockImplementation();
    let podRequestCount = 0;
    fetchMock.mockImplementation(async (input, init) => {
      const url = String(input);
      const method = init?.method || "GET";
      if (url === "/studio/services/payments-api/pods" && method === "GET") {
        podRequestCount += 1;
        const pods = [
          { name: "payments-api-abc", namespace: "prod", phase: "Running", labels: { app: "service-api" } },
          { name: "payments-worker-def", namespace: "prod", phase: "Running", labels: { app: "worker" } },
        ];
        if (podRequestCount > 1) {
          pods.push({ name: "payments-worker-ghi", namespace: "prod", phase: "Running", labels: { app: "worker" } });
        }
        return jsonResponse({ service_id: "payments-api", count: pods.length, pods });
      }
      return baseImpl?.(input, init) ?? jsonResponse({});
    });

    render(<App />);

    await flushRenderPromises();
    expect(screen.getByText("Selected pods: payments-api-abc, payments-worker-def")).toBeInTheDocument();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(10_000);
    });

    await flushRenderPromises();
    expect(screen.getByText("Selected pods: payments-api-abc, payments-worker-def, payments-worker-ghi")).toBeInTheDocument();
    const matchingNewPodLogCall = fetchMock.mock.calls.find(([input]) => {
      const parsed = new URL(String(input), "http://studio.test");
      return (
        parsed.pathname === "/studio/services/payments-api/logs" &&
        parsed.searchParams.get("pod") === "payments-worker-ghi"
      );
    });
    const matchingNewPodMetricCall = fetchMock.mock.calls.find(([input]) => {
      const parsed = new URL(String(input), "http://studio.test");
      return (
        parsed.pathname === "/studio/services/payments-api/metrics" &&
        parsed.searchParams.get("pod") === "payments-worker-ghi" &&
        parsed.searchParams.get("split_by_pod") === "true"
      );
    });
    expect(matchingNewPodLogCall).toBeTruthy();
    expect(matchingNewPodMetricCall).toBeTruthy();
  });

  it("preserves manual pod selection across background pod refreshes", async () => {
    vi.useFakeTimers();
    window.history.replaceState({}, "", "/services/payments-api");
    services[0] = {
      ...services[0],
      metrics_config: {
        provider: "prometheus",
        base_url: "https://prometheus.example.test",
        namespace: "prod",
        service_selector_labels: { app: "payments-api" },
        namespace_label: "namespace",
        pod_label: "kubernetes_pod_name",
        container_label: "container",
        step_seconds: 30,
        task_window_padding_seconds: 120,
      },
    };

    const baseImpl = fetchMock.getMockImplementation();
    let podRequestCount = 0;
    fetchMock.mockImplementation(async (input, init) => {
      const url = String(input);
      const method = init?.method || "GET";
      if (url === "/studio/services/payments-api/pods" && method === "GET") {
        podRequestCount += 1;
        const pods = [
          { name: "payments-api-abc", namespace: "prod", phase: "Running", labels: { app: "service-api" } },
          { name: "payments-worker-def", namespace: "prod", phase: "Running", labels: { app: "worker" } },
        ];
        if (podRequestCount > 1) {
          pods.push({ name: "payments-worker-ghi", namespace: "prod", phase: "Running", labels: { app: "worker" } });
        }
        return jsonResponse({ service_id: "payments-api", count: pods.length, pods });
      }
      return baseImpl?.(input, init) ?? jsonResponse({});
    });

    render(<App />);

    await flushRenderPromises();
    expect(screen.getByText("Selected pods: payments-api-abc, payments-worker-def")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /payments-worker-def/ }));
    expect(screen.getByText("Selected pods: payments-api-abc")).toBeInTheDocument();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(10_000);
    });

    await flushRenderPromises();
    expect(screen.getByText("Selected pods: payments-api-abc")).toBeInTheDocument();
    expect(screen.queryByText("Selected pods: payments-api-abc, payments-worker-def, payments-worker-ghi")).not.toBeInTheDocument();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(50_000);
    });

    await flushRenderPromises();
    expect(screen.getByText("Selected pods: payments-api-abc")).toBeInTheDocument();
    expect(screen.queryByText("Selected pods: none")).not.toBeInTheDocument();
  });

  it("preserves manual pod selection when repeated pod refreshes temporarily return no pods", async () => {
    vi.useFakeTimers();
    window.history.replaceState({}, "", "/services/payments-api");
    services[0] = {
      ...services[0],
      metrics_config: {
        provider: "prometheus",
        base_url: "https://prometheus.example.test",
        namespace: "prod",
        service_selector_labels: { app: "payments-api" },
        namespace_label: "namespace",
        pod_label: "kubernetes_pod_name",
        container_label: "container",
        step_seconds: 30,
        task_window_padding_seconds: 120,
      },
    };

    const baseImpl = fetchMock.getMockImplementation();
    let podRequestCount = 0;
    fetchMock.mockImplementation(async (input, init) => {
      const url = String(input);
      const method = init?.method || "GET";
      if (url === "/studio/services/payments-api/pods" && method === "GET") {
        podRequestCount += 1;
        if (podRequestCount === 2 || podRequestCount === 3) {
          return jsonResponse({ service_id: "payments-api", count: 0, pods: [] });
        }
        return jsonResponse({
          service_id: "payments-api",
          count: 2,
          pods: [
            { name: "payments-api-abc", namespace: "prod", phase: "Running", labels: { app: "service-api" } },
            { name: "payments-worker-def", namespace: "prod", phase: "Running", labels: { app: "worker" } },
          ],
        });
      }
      return baseImpl?.(input, init) ?? jsonResponse({});
    });

    render(<App />);

    await flushRenderPromises();
    expect(screen.getByText("Selected pods: payments-api-abc, payments-worker-def")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /payments-worker-def/ }));
    expect(screen.getByText("Selected pods: payments-api-abc")).toBeInTheDocument();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(10_000);
    });

    await flushRenderPromises();
    expect(screen.getByText("No current pods matched this service selector.")).toBeInTheDocument();
    expect(screen.queryByText("Selected pods: none")).not.toBeInTheDocument();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(10_000);
    });

    await flushRenderPromises();
    expect(screen.getByText("No current pods matched this service selector.")).toBeInTheDocument();
    expect(screen.queryByText("Selected pods: none")).not.toBeInTheDocument();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(10_000);
    });

    await flushRenderPromises();
    expect(screen.getByText("Selected pods: payments-api-abc")).toBeInTheDocument();
    expect(screen.queryByText("Selected pods: payments-api-abc, payments-worker-def")).not.toBeInTheDocument();
  });

  it("uses the manual service log window override when provided", async () => {
    window.history.replaceState({}, "", "/services/payments-api");

    render(<App />);

    expect(await screen.findByRole("heading", { name: "Payments API" })).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Service log window mode"), { target: { value: "manual" } });
    await waitFor(() => expect(screen.getByLabelText("Service log from")).toBeEnabled());
    const serviceLogFrom = isoToLocalDateTime("2026-04-08T09:50:00Z");
    const serviceLogTo = isoToLocalDateTime("2026-04-08T10:10:00Z");
    fireEvent.change(screen.getByLabelText("Service log from"), { target: { value: serviceLogFrom } });
    fireEvent.change(screen.getByLabelText("Service log to"), { target: { value: serviceLogTo } });
    await waitFor(() => expect(screen.getByLabelText("Service log from")).toHaveValue(serviceLogFrom));
    await waitFor(() => expect(screen.getByLabelText("Service log to")).toHaveValue(serviceLogTo));
    fireEvent.click(screen.getByRole("button", { name: "Reload Logs" }));

    await waitFor(() => {
      const matchingCall = fetchMock.mock.calls.find(([input]) => {
        const parsed = new URL(String(input), "http://studio.test");
        return (
          parsed.pathname === "/studio/services/payments-api/logs" &&
          parsed.searchParams.get("from") === new Date("2026-04-08T09:50:00Z").toISOString() &&
          parsed.searchParams.get("to") === new Date("2026-04-08T10:10:00Z").toISOString()
        );
      });
      expect(matchingCall).toBeTruthy();
    });
  });

  it("applies quick service log windows immediately", async () => {
    window.history.replaceState({}, "", "/services/payments-api");

    const baseImpl = fetchMock.getMockImplementation();
    fetchMock.mockImplementation(async (input, init) => {
      const url = String(input);
      const method = init?.method || "GET";

      if (url.startsWith("/studio/services/payments-api/logs?") && method === "GET") {
        const parsed = new URL(url, "http://studio.test");
        if (parsed.searchParams.get("from") || parsed.searchParams.get("to")) {
          return jsonResponse({ count: 0, items: [] });
        }
        return jsonResponse({
          count: 1,
          items: [
            {
              service_id: "payments-api",
              timestamp: "2026-04-25T15:46:04Z",
              level: "info",
              source: "runtime-worker",
              message: "old service log line",
              fields: {},
            },
          ],
        });
      }

      return baseImpl?.(input, init) ?? jsonResponse({});
    });

    render(<App />);

    expect(await screen.findByText("old service log line")).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Service log window mode"), { target: { value: "15m" } });

    await waitFor(() => {
      const matchingCall = fetchMock.mock.calls.find(([input]) => {
        const parsed = new URL(String(input), "http://studio.test");
        const from = parsed.searchParams.get("from");
        const to = parsed.searchParams.get("to");
        return (
          parsed.pathname === "/studio/services/payments-api/logs" &&
          Boolean(from) &&
          Boolean(to) &&
          new Date(to || "").getTime() - new Date(from || "").getTime() === 15 * 60 * 1000
        );
      });
      expect(matchingCall).toBeTruthy();
    });
    await waitFor(() => expect(screen.queryByText("old service log line")).not.toBeInTheDocument());
  });

  it("renders service log loading, empty, and error states", async () => {
    window.history.replaceState({}, "", "/services/payments-api");

    const baseImpl = fetchMock.getMockImplementation();
    let serviceLogCalls = 0;
    let resolveServiceLogs: ((response: Response) => void) | null = null;
    fetchMock.mockImplementation(async (input, init) => {
      const url = String(input);
      const method = init?.method || "GET";

      if (url.startsWith("/studio/services/payments-api/logs?") && method === "GET") {
        serviceLogCalls += 1;
        if (serviceLogCalls === 1) {
          return await new Promise<Response>((resolve) => {
            resolveServiceLogs = resolve;
          });
        }
        return jsonResponse({ detail: "Loki query failed." }, 502);
      }

      return baseImpl?.(input, init) ?? jsonResponse({});
    });

    render(<App />);

    expect(await screen.findByRole("heading", { name: "Payments API" })).toBeInTheDocument();
    expect(await screen.findByText("Loading service logs...")).toBeInTheDocument();

    await act(async () => {
      resolveServiceLogs?.(jsonResponse({ count: 0, items: [], next_cursor: null }));
      await Promise.resolve();
    });

    expect(await screen.findByText("No service logs matched the current filters.")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Reload Logs" }));

    expect(await screen.findByText("Loki query failed.")).toBeInTheDocument();
  });

  it("renders service metrics and manual metrics window requests", async () => {
    window.history.replaceState({}, "", "/services/payments-api");
    services[0] = {
      ...services[0],
      metrics_config: {
        provider: "prometheus",
        base_url: "https://prometheus.example.test",
        namespace: "prod",
        service_selector_labels: { app: "payments-api" },
        namespace_label: "namespace",
        pod_label: "pod",
        container_label: "container",
        step_seconds: 30,
        task_window_padding_seconds: 120,
      },
    };

    const baseImpl = fetchMock.getMockImplementation();
    fetchMock.mockImplementation(async (input, init) => {
      const url = String(input);
      const method = init?.method || "GET";
      if (url === "/studio/services/payments-api/pods" && method === "GET") {
        return jsonResponse({ service_id: "payments-api", count: 0, pods: [] });
      }
      if (url.startsWith("/studio/services/payments-api/metrics") && method === "GET") {
        const parsed = new URL(url, "http://studio.test");
        if (parsed.searchParams.get("split_by_pod") === "true") {
          return jsonResponse(metricsResponse(null));
        }
        return jsonResponse({
          ...metricsResponse(null),
          series: [
            {
              metric: "tasks_started_rate",
              unit: "per_second",
              labels: { service: "payments-api" },
              points: [{ timestamp: "2026-04-08T10:05:00Z", value: 1.25 }],
            },
          ],
        });
      }
      return baseImpl?.(input, init) ?? jsonResponse({});
    });

    render(<App />);

    expect(await screen.findByText("Service Metrics")).toBeInTheDocument();
    expect(await screen.findByText("Service Metrics Summary")).toBeInTheDocument();
    expect(await screen.findByText("1.250/s")).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Service metrics window mode"), { target: { value: "manual" } });
    const from = isoToLocalDateTime("2026-04-08T09:45:00Z");
    const to = isoToLocalDateTime("2026-04-08T10:15:00Z");
    fireEvent.change(screen.getByLabelText("Service metrics from"), { target: { value: from } });
    fireEvent.change(screen.getByLabelText("Service metrics to"), { target: { value: to } });

    await waitFor(() => {
      const matchingCall = fetchMock.mock.calls.find(([input]) => {
        const parsed = new URL(String(input), "http://studio.test");
        return (
          parsed.pathname === "/studio/services/payments-api/metrics" &&
          parsed.searchParams.get("from") === new Date("2026-04-08T09:45:00Z").toISOString() &&
          parsed.searchParams.get("to") === new Date("2026-04-08T10:15:00Z").toISOString() &&
          parsed.searchParams.getAll("group").includes("tasks_started_rate") &&
          !parsed.searchParams.getAll("group").includes("cpu_usage")
        );
      });
      expect(matchingCall).toBeTruthy();
    });
  });

  it("renders pod metric charts and applies Service Pods and long-range filters", async () => {
    window.history.replaceState({}, "", "/services/payments-api");
    services[0] = {
      ...services[0],
      metrics_config: {
        provider: "prometheus",
        base_url: "https://prometheus.example.test",
        namespace: "prod",
        service_selector_labels: { app: "payments-api" },
        namespace_label: "namespace",
        pod_label: "kubernetes_pod_name",
        container_label: "container",
        step_seconds: 30,
        task_window_padding_seconds: 120,
      },
    };

    const baseImpl = fetchMock.getMockImplementation();
    fetchMock.mockImplementation(async (input, init) => {
      const url = String(input);
      const method = init?.method || "GET";
      if (url === "/studio/services/payments-api/pods" && method === "GET") {
        return jsonResponse({
          service_id: "payments-api",
          count: 2,
          pods: [
            { name: "payments-api-abc", namespace: "prod", phase: "Running", labels: { label_component: "service-api" } },
            { name: "payments-worker-def", namespace: "prod", phase: "Running", labels: { label_component: "worker" } },
          ],
        });
      }
      if (url.startsWith("/studio/services/payments-api/metrics") && method === "GET") {
        return jsonResponse(customPodLabelMetricsResponse());
      }
      return baseImpl?.(input, init) ?? jsonResponse({});
    });

    render(<App />);

    expect(await screen.findByText("Pod Metric Charts")).toBeInTheDocument();
    await waitFor(() => {
      const matchingDefaultCall = fetchMock.mock.calls.find(([input]) => {
        const parsed = new URL(String(input), "http://studio.test");
        return (
          parsed.pathname === "/studio/services/payments-api/metrics" &&
          parsed.searchParams.get("split_by_pod") === "true" &&
          !parsed.searchParams.has("pod")
        );
      });
      expect(matchingDefaultCall).toBeTruthy();
    });
    expect(screen.queryByLabelText("Pod metrics pod filter")).not.toBeInTheDocument();
    expect((await screen.findAllByText("payments-worker-def")).length).toBeGreaterThan(0);
    fireEvent.click(screen.getByRole("button", { name: /payments-api-abc/ }));
    fireEvent.change(screen.getByLabelText("Pod metrics window mode"), { target: { value: "1w" } });

    await waitFor(() => {
      const matchingCall = fetchMock.mock.calls.find(([input]) => {
        const parsed = new URL(String(input), "http://studio.test");
        return (
          parsed.pathname === "/studio/services/payments-api/metrics" &&
          parsed.searchParams.get("pod") === "payments-worker-def" &&
          parsed.searchParams.get("split_by_pod") === "true" &&
          parsed.searchParams.get("step") === "3600"
        );
      });
      expect(matchingCall).toBeTruthy();
    });
    expect(screen.getAllByLabelText("Pod metric graph").length).toBeGreaterThan(0);
    expect(await screen.findByText("payments-worker-prometheus")).toBeInTheDocument();
    const cpuLegend = screen.getByLabelText("Cpu Usage legend");
    expect(within(cpuLegend).getByText("payments-worker-prometheus")).toBeInTheDocument();
    expect(within(cpuLegend).queryByText("payments-worker-prometheus · Running")).not.toBeInTheDocument();
  });

  it("uses the manual service activity window override when provided", async () => {
    window.history.replaceState({}, "", "/services/payments-api");

    render(<App />);

    expect(await screen.findByRole("heading", { name: "Payments API" })).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Service event window mode"), { target: { value: "manual" } });
    await waitFor(() => expect(screen.getByLabelText("Service event from")).toBeEnabled());
    const serviceEventFrom = isoToLocalDateTime("2026-04-08T09:45:00Z");
    const serviceEventTo = isoToLocalDateTime("2026-04-08T10:15:00Z");
    fireEvent.change(screen.getByLabelText("Service event from"), { target: { value: serviceEventFrom } });
    fireEvent.change(screen.getByLabelText("Service event to"), { target: { value: serviceEventTo } });
    await waitFor(() => expect(screen.getByLabelText("Service event from")).toHaveValue(serviceEventFrom));
    await waitFor(() => expect(screen.getByLabelText("Service event to")).toHaveValue(serviceEventTo));
    fireEvent.click(screen.getByRole("button", { name: "Reload Activity" }));

    await waitFor(() => {
      const matchingCall = fetchMock.mock.calls.find(([input]) => {
        const parsed = new URL(String(input), "http://studio.test");
        return (
          parsed.pathname === "/studio/services/payments-api/events" &&
          parsed.searchParams.get("from") === new Date("2026-04-08T09:45:00Z").toISOString() &&
          parsed.searchParams.get("to") === new Date("2026-04-08T10:15:00Z").toISOString()
        );
      });
      expect(matchingCall).toBeTruthy();
    });
  });

  it("applies quick service activity windows immediately", async () => {
    window.history.replaceState({}, "", "/services/payments-api");

    const baseImpl = fetchMock.getMockImplementation();
    fetchMock.mockImplementation(async (input, init) => {
      const url = String(input);
      const method = init?.method || "GET";

      if (url.startsWith("/studio/services/payments-api/events?") && method === "GET") {
        const parsed = new URL(url, "http://studio.test");
        if (parsed.searchParams.get("from") || parsed.searchParams.get("to")) {
          return jsonResponse({ count: 0, items: [], next_cursor: null });
        }
        return jsonResponse({
          count: 1,
          items: [
            {
              service_id: "payments-api",
              ingest_method: "pull",
              ingested_at: "2026-04-25T15:46:04Z",
              dedupe_key: "evt-old",
              out_of_order: false,
              task_id: "task-old",
              event_type: "status.completed",
              source_kind: "status",
              component: "mock-service",
              timestamp: "2026-04-25T15:46:04Z",
              payload: { status: "completed" },
            },
          ],
          next_cursor: null,
        });
      }

      return baseImpl?.(input, init) ?? jsonResponse({});
    });

    render(<App />);

    expect(await screen.findByText("task-old")).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Service event window mode"), { target: { value: "15m" } });

    await waitFor(() => {
      const matchingCall = fetchMock.mock.calls.find(([input]) => {
        const parsed = new URL(String(input), "http://studio.test");
        const from = parsed.searchParams.get("from");
        const to = parsed.searchParams.get("to");
        return (
          parsed.pathname === "/studio/services/payments-api/events" &&
          Boolean(from) &&
          Boolean(to) &&
          new Date(to || "").getTime() - new Date(from || "").getTime() === 15 * 60 * 1000
        );
      });
      expect(matchingCall).toBeTruthy();
    });
    await waitFor(() => expect(screen.queryByText("task-old")).not.toBeInTheDocument());
  });

  it("derives service log source options from returned log entries", async () => {
    window.history.replaceState({}, "", "/services/payments-api");

    render(<App />);

    expect(await screen.findByRole("heading", { name: "Payments API" })).toBeInTheDocument();
    const discoveredOptions = Array.from(
      document.querySelectorAll("#service-log-sources-payments-api option"),
    ).map((item) => item.getAttribute("value"));
    expect(discoveredOptions).toContain("api");
    expect(discoveredOptions).toContain("runtime-worker");
  });

  it("refreshes a registered service from the detail page", async () => {
    window.history.replaceState({}, "", "/services/payments-api");

    render(<App />);

    expect(await screen.findByRole("heading", { name: "Payments API" })).toBeInTheDocument();
    expect(screen.getAllByText("unknown").length).toBeGreaterThan(0);

    fireEvent.click(screen.getByRole("button", { name: "Refresh" }));

    expect(await screen.findByText("Refreshed 'payments-api'.")).toBeInTheDocument();
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith("/studio/services/payments-api/refresh", { method: "POST" }));
    expect(screen.getAllByText("healthy").length).toBeGreaterThan(0);
  });

  it("shows stable fallback messages for non-Error service telemetry failures and ignores malformed SSE", async () => {
    window.history.replaceState({}, "", "/services/payments-api");
    services[0] = {
      ...services[0],
      metrics_config: {
        provider: "prometheus",
        base_url: "https://prometheus.example.test",
        namespace: "prod",
        service_selector_labels: { app: "payments-api" },
      },
    };
    const baseImpl = fetchMock.getMockImplementation();
    fetchMock.mockImplementation(async (input, init) => {
      const url = String(input);
      if (
        url.startsWith("/studio/services/payments-api/events?") ||
        url.startsWith("/studio/services/payments-api/logs?") ||
        url.startsWith("/studio/services/payments-api/metrics?") ||
        url === "/studio/services/payments-api/pods"
      ) {
        throw "offline";
      }
      return await baseImpl!(input, init);
    });

    render(<App />);

    expect(await screen.findByText("Unable to load service activity.")).toBeInTheDocument();
    expect(await screen.findByText("Unable to load service logs.")).toBeInTheDocument();
    expect(await screen.findByText("Unable to load service pods.")).toBeInTheDocument();
    expect((await screen.findAllByText(/Unable to load (service|pod) metrics\./)).length).toBeGreaterThan(0);
    const source = MockEventSource.instances.find((item) => item.url.includes("/services/payments-api/events/stream"));
    expect(source).toBeDefined();
    act(() => source?.emitRaw("event", "not-json"));
    expect(screen.getByRole("heading", { name: "Payments API" })).toBeInTheDocument();
  });

  it("renders explicit no-provider states for a registry-only service", async () => {
    window.history.replaceState({}, "", "/services/payments-api");
    services[0] = { ...services[0], log_config: null, metrics_config: null, health: null };

    render(<App />);

    expect(await screen.findByText("No log provider configured for this service.")).toBeInTheDocument();
    expect((await screen.findAllByText("No metrics provider configured for this service.")).length).toBeGreaterThan(1);
    expect(screen.getAllByText("unknown").length).toBeGreaterThan(0);
    for (const label of ["Reload Metrics", "Reload Charts", "Reload Pods"]) {
      fireEvent.click(screen.getByRole("button", { name: label }));
    }
  });

  it("operates every service observe filter, reload, health, SSE, and delete control", async () => {
    window.history.replaceState({}, "", "/services/payments-api");
    services[0] = {
      ...services[0],
      metrics_config: {
        provider: "prometheus",
        base_url: "https://prometheus.example.test",
        namespace: "prod",
        service_selector_labels: { app: "payments-api" },
      },
    };
    const baseImpl = fetchMock.getMockImplementation();
    fetchMock.mockImplementation(async (input, init) => {
      const url = String(input);
      const method = init?.method || "GET";
      if (url === "/studio/services/payments-api/health/refresh" && method === "POST") {
        return jsonResponse({});
      }
      if (url === "/studio/services/payments-api" && method === "DELETE") {
        return new Response(null, { status: 204 });
      }
      return await baseImpl!(input, init);
    });

    render(<App />);
    expect(await screen.findByRole("heading", { name: "Payments API" })).toBeInTheDocument();
    await screen.findByText("Service Logs");

    const source = MockEventSource.instances.find((item) => item.url.includes("/services/payments-api/events/stream"));
    act(() => source?.emit("event", {
      service_id: "payments-api", ingest_method: "pull", ingested_at: "2026-04-08T12:00:00Z",
      dedupe_key: "live", out_of_order: true, task_id: "live-task", event_type: "task.live",
      source_kind: "observation", component: null, timestamp: null, payload: {},
    }));
    expect(await screen.findByText("live-task")).toBeInTheDocument();

    fireEvent.change(screen.getByPlaceholderText("Filter task id"), { target: { value: "missing" } });
    fireEvent.change(document.querySelector(".studio-form-grid--triple select")!, { target: { value: "status" } });
    fireEvent.change(screen.getByPlaceholderText("Filter event type"), { target: { value: "missing" } });
    fireEvent.change(screen.getByLabelText("Service log text filter"), { target: { value: "needle" } });
    fireEvent.change(screen.getByLabelText("Service log level"), { target: { value: "error" } });
    fireEvent.change(screen.getByLabelText("Service log source"), { target: { value: "api" } });
    fireEvent.change(screen.getByLabelText("Service log limit"), { target: { value: "7" } });

    fireEvent.change(screen.getByLabelText("Service metrics window mode"), { target: { value: "manual" } });
    fireEvent.change(screen.getByLabelText("Service metrics from"), { target: { value: "2026-04-08T09:00" } });
    fireEvent.change(screen.getByLabelText("Service metrics to"), { target: { value: "2026-04-08T10:00" } });
    fireEvent.change(screen.getByLabelText("Pod metrics window mode"), { target: { value: "manual" } });
    fireEvent.change(screen.getByLabelText("Pod metrics from"), { target: { value: "2026-04-08T09:00" } });
    fireEvent.change(screen.getByLabelText("Pod metrics to"), { target: { value: "2026-04-08T10:00" } });

    for (const label of ["Reload Metrics", "Reload Charts", "Reload Pods", "Reload Activity", "Reload Logs", "Run Health Check"]) {
      fireEvent.click(screen.getByRole("button", { name: label }));
    }

    fireEvent.click(screen.getByText("Lifecycle actions"));
    fireEvent.click(screen.getByRole("button", { name: "Delete" }));
    const dialog = screen.getByRole("dialog", { name: "Delete service" });
    fireEvent.change(within(dialog).getByLabelText("Type payments-api to confirm deletion"), { target: { value: "payments-api" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "Delete Service" }));
    await waitFor(() => expect(window.location.pathname).toBe("/services"));
  });

  it("keeps lifecycle enablement inside the labelled service action menu", async () => {
    window.history.replaceState({}, "", "/services/payments-api");
    const baseImpl = fetchMock.getMockImplementation();
    let requestedStatus = "";
    fetchMock.mockImplementation(async (input, init) => {
      const url = String(input);
      const method = init?.method || "GET";
      if (url === "/studio/services/payments-api" && method === "PATCH") {
        requestedStatus = String((JSON.parse(String(init?.body || "{}")) as { status?: string }).status || "");
        return jsonResponse({ ...services[0], status: requestedStatus });
      }
      return await baseImpl!(input, init);
    });

    render(<App />);
    expect(await screen.findByRole("heading", { name: "Payments API" })).toBeInTheDocument();
    fireEvent.click(screen.getByText("Lifecycle actions"));
    fireEvent.click(screen.getByRole("button", { name: "Enable" }));

    await waitFor(() => expect(requestedStatus).toBe("registered"));
  });

  it.each([
    ["Disable", "Disable Service", "disabled"],
    ["Mark Unavailable", "Mark Unavailable", "unavailable"],
  ])("confirms the %s service action before patching registry status", async (buttonLabel, confirmLabel, status) => {
    window.history.replaceState({}, "", "/services/payments-api");
    const baseImpl = fetchMock.getMockImplementation();
    const patches: Array<Record<string, unknown>> = [];
    fetchMock.mockImplementation(async (input, init) => {
      const url = String(input);
      const method = init?.method || "GET";
      if (url === "/studio/services/payments-api" && method === "PATCH") {
        const payload = JSON.parse(String(init?.body || "{}")) as Record<string, unknown>;
        patches.push(payload);
        services[0] = { ...services[0], status: payload.status as MockServiceRecord["status"] };
        return jsonResponse(services[0]);
      }
      return await baseImpl!(input, init);
    });

    render(<App />);

    expect(await screen.findByRole("heading", { name: "Payments API" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: buttonLabel }));

    const dialog = screen.getByRole("dialog");
    expect(patches).toEqual([]);

    fireEvent.click(within(dialog).getByRole("button", { name: confirmLabel }));

    expect(await screen.findByText(`Marked 'payments-api' as ${status}.`)).toBeInTheDocument();
    expect(patches).toEqual([{ status }]);
  });

  it("traps focus inside service action confirmation dialogs", async () => {
    window.history.replaceState({}, "", "/services/payments-api");
    const baseImpl = fetchMock.getMockImplementation();
    const patches: Array<Record<string, unknown>> = [];
    fetchMock.mockImplementation(async (input, init) => {
      const url = String(input);
      const method = init?.method || "GET";
      if (url === "/studio/services/payments-api" && method === "PATCH") {
        patches.push(JSON.parse(String(init?.body || "{}")) as Record<string, unknown>);
        return jsonResponse(services[0]);
      }
      return await baseImpl!(input, init);
    });

    render(<App />);

    expect(await screen.findByRole("heading", { name: "Payments API" })).toBeInTheDocument();
    const disableTrigger = screen.getByRole("button", { name: "Disable" });
    disableTrigger.focus();
    fireEvent.click(disableTrigger);

    const dialog = screen.getByRole("dialog", { name: "Disable service" });
    const cancelButton = within(dialog).getByRole("button", { name: "Cancel" });
    const confirmButton = within(dialog).getByRole("button", { name: "Disable Service" });
    expect(cancelButton).toHaveFocus();

    fireEvent.keyDown(dialog, { key: "Tab", shiftKey: true });
    expect(confirmButton).toHaveFocus();

    fireEvent.keyDown(dialog, { key: "Tab" });
    expect(cancelButton).toHaveFocus();

    fireEvent.keyDown(dialog, { key: "Escape" });
    expect(screen.queryByRole("dialog", { name: "Disable service" })).not.toBeInTheDocument();
    expect(disableTrigger).toHaveFocus();
    expect(patches).toEqual([]);
  });

  it("supersedes a stale background services poll when a mutation triggers reload", async () => {
    vi.useFakeTimers();
    window.history.replaceState({}, "", "/services/payments-api");

    const baseImpl = fetchMock.getMockImplementation();
    let serviceListCalls = 0;
    let resolveBackgroundPoll: ((response: Response) => void) | null = null;
    const staleServices = [buildMockService()];
    async function flushRender() {
      await act(async () => {
        await Promise.resolve();
        await Promise.resolve();
      });
    }

    fetchMock.mockImplementation(async (input, init) => {
      const url = String(input);
      const method = init?.method || "GET";

      if (url === "/studio/services" && method === "GET") {
        serviceListCalls += 1;
        if (serviceListCalls === 1) {
          return serviceListResponse(services);
        }
        if (serviceListCalls === 2) {
          return await new Promise<Response>((resolve) => {
            resolveBackgroundPoll = resolve;
          });
        }
        if (serviceListCalls === 3) {
          return serviceListResponse(services);
        }
      }

      return await baseImpl!(input, init);
    });

    render(<App />);

    await flushRender();

    expect(screen.getByRole("heading", { name: "Payments API" })).toBeInTheDocument();
    expect(screen.getAllByText("unknown").length).toBeGreaterThan(0);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(60_000);
    });

    expect(serviceListCalls).toBe(2);

    fireEvent.click(screen.getByRole("button", { name: "Refresh" }));

    await flushRender();

    expect(screen.getByText("Refreshed 'payments-api'.")).toBeInTheDocument();
    expect(serviceListCalls).toBe(3);
    expect(screen.getAllByText("healthy").length).toBeGreaterThan(0);

    await act(async () => {
      resolveBackgroundPoll?.(serviceListResponse(staleServices));
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(screen.getAllByText("healthy").length).toBeGreaterThan(0);
  });

  it("opens the service editor with an allowlisted default base URL", async () => {
    render(<App />);

    fireEvent.click(await screen.findByRole("button", { name: "New Service" }));

    expect(screen.getByLabelText("Base URL")).toHaveValue("https://service.example.test");
  });

  it("clears trace config when the trace provider is disabled", () => {
    const draft = serviceToDraft(buildMockService() as unknown as ServiceRecord);
    const payloadWithTrace = buildServicePayload(draft);
    expect(payloadWithTrace.trace_config).toMatchObject({
      provider: "tempo",
      base_url: "https://tempo.example.test",
    });

    draft.trace_provider = "";
    const payloadWithoutTrace = buildServicePayload(draft);
    expect(payloadWithoutTrace.trace_config).toBeNull();
  });

  it("edits every registry form field and resets the editor to a new draft", async () => {
    window.history.replaceState({}, "", "/services");
    render(<App />);

    fireEvent.click(await screen.findByRole("button", { name: "Edit payments-api" }));
    const editorHeading = await screen.findByRole("heading", { name: "Edit Service" });
    const editor = editorHeading.closest("section");
    expect(editor).not.toBeNull();
    const form = within(editor as HTMLElement);

    const textUpdates: Array<[string, string]> = [
      ["Name", "Payments Updated"],
      ["Base URL", "https://updated.example.test"],
      ["Environment", "staging"],
      ["Tags", "core, updated"],
      ["Auth mode", "mtls"],
      ["Log base URL", "https://loki.updated.test"],
      ["Loki tenant id", "tenant-b"],
      ["Service label key", "service_name"],
      ["Service label value", "payments-updated"],
      ["App label key", "component"],
      ["Log pod label", "pod_name"],
      ["Log pod value template", "^{pod}$"],
      ["Task id label", "task"],
      ["Correlation id label", "correlation"],
      ["Level label", "severity"],
      ["Task match template", "task={task_id}"],
      ["Prometheus base URL", "https://prometheus.updated.test"],
      ["Namespace", "payments"],
      ["Prometheus selector key", "service"],
      ["Prometheus selector value", "payments"],
      ["Relayna runtime service label", "payments-runtime"],
      ["Namespace label", "kube_namespace"],
      ["Pod label", "kube_pod"],
      ["Container label", "kube_container"],
      ["Step seconds", "45"],
      ["Task padding seconds", "180"],
      ["Tempo base URL", "https://tempo.updated.test"],
      ["Public Tempo URL", "https://traces.updated.test"],
      ["Tenant ID", "tenant-b"],
      ["Query path", "/trace/{trace_id}"],
    ];
    for (const [label, value] of textUpdates) {
      fireEvent.change(form.getByLabelText(label), { target: { value } });
    }
    const additionalSelectors = form.getAllByLabelText("Additional selector labels");
    expect(additionalSelectors).toHaveLength(2);
    fireEvent.change(additionalSelectors[0], { target: { value: "zone=east" } });
    fireEvent.change(additionalSelectors[1], { target: { value: "zone=east" } });
    fireEvent.change(form.getByLabelText("Log provider"), { target: { value: "loki" } });
    fireEvent.change(form.getByLabelText("Log pod match"), { target: { value: "regex" } });
    fireEvent.change(form.getByLabelText("Task match mode"), { target: { value: "structured_metadata" } });
    fireEvent.change(form.getByLabelText("Metrics provider"), { target: { value: "prometheus" } });
    fireEvent.change(form.getByLabelText("Trace provider"), { target: { value: "tempo" } });

    expect(form.getByLabelText("Name")).toHaveValue("Payments Updated");
    expect(form.getByLabelText("Task match mode")).toHaveValue("structured_metadata");
    fireEvent.click(form.getByRole("button", { name: "New Draft" }));
    expect(await screen.findByRole("heading", { name: "Register Service" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Close" }));
    expect(screen.queryByRole("heading", { name: "Register Service" })).not.toBeInTheDocument();
  });

  it("covers service search result, empty, and non-error failure states", async () => {
    window.history.replaceState({}, "", "/services");
    const baseImpl = fetchMock.getMockImplementation();
    let searchCalls = 0;
    fetchMock.mockImplementation(async (input, init) => {
      const url = String(input);
      if (url.startsWith("/studio/services/search?") && (init?.method || "GET") === "GET") {
        searchCalls += 1;
        if (searchCalls === 1) {
          return jsonResponse({
            count: 1,
            items: [{
              service_id: "payments-api",
              name: "Payments API",
              environment: "prod",
              tags: ["core"],
              status: "healthy",
              health_status: null,
              base_url: "https://payments.example.test",
              auth_mode: "internal_network",
              matched_fields: [],
            }],
            next_cursor: null,
          });
        }
        if (searchCalls === 2) {
          return jsonResponse({ count: 0, items: [], next_cursor: null });
        }
        throw "offline";
      }
      return await baseImpl!(input, init);
    });
    render(<App />);

    const searchHeading = await screen.findByRole("heading", { name: "Service Search" });
    const searchSection = searchHeading.closest("section");
    expect(searchSection).not.toBeNull();
    const search = within(searchSection as HTMLElement);
    fireEvent.change(search.getByLabelText("Keyword"), { target: { value: "payments" } });
    fireEvent.change(search.getByLabelText("Environment"), { target: { value: "prod" } });
    fireEvent.change(search.getByLabelText("Registry"), { target: { value: "healthy" } });
    fireEvent.change(search.getByLabelText("Runtime Health"), { target: { value: "stale" } });
    fireEvent.change(search.getByLabelText("Tag"), { target: { value: "core" } });
    fireEvent.click(search.getByRole("button", { name: "Search Services" }));
    expect(await search.findByText("matched fields: structured filters only")).toBeInTheDocument();

    fireEvent.click(search.getByRole("button", { name: "Search Services" }));
    expect(await search.findByText("No matching services found.")).toBeInTheDocument();
    fireEvent.click(search.getByRole("button", { name: "Search Services" }));
    expect(await search.findByText("Unable to search services.")).toBeInTheDocument();
    fireEvent.click(search.getByRole("button", { name: "Clear" }));
    expect(search.getByLabelText("Keyword")).toHaveValue("");
    expect(search.queryByText("Unable to search services.")).not.toBeInTheDocument();
  });

  it("keeps the edit context visible when service deletion fails", async () => {
    const baseImpl = fetchMock.getMockImplementation();
    let deleteCalls = 0;
    fetchMock.mockImplementation(async (input, init) => {
      const url = String(input);
      const method = init?.method || "GET";
      if (url === "/studio/services/payments-api" && method === "DELETE") {
        deleteCalls += 1;
        return jsonResponse({ detail: "Delete failed." }, 500);
      }
      return await baseImpl!(input, init);
    });

    render(<App />);

    fireEvent.click(await screen.findByRole("button", { name: "Edit" }));
    expect(screen.getByRole("heading", { name: "Editing Target" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Delete Service" }));

    const dialog = screen.getByRole("dialog", { name: "Delete service" });
    const confirmButton = within(dialog).getByRole("button", { name: "Delete Service" });
    expect(confirmButton).toBeDisabled();
    expect(deleteCalls).toBe(0);

    fireEvent.click(within(dialog).getByRole("button", { name: "Cancel" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(deleteCalls).toBe(0);

    fireEvent.click(screen.getByRole("button", { name: "Delete Service" }));
    const reopenedDialog = screen.getByRole("dialog", { name: "Delete service" });
    const reopenedConfirmButton = within(reopenedDialog).getByRole("button", { name: "Delete Service" });

    fireEvent.change(within(reopenedDialog).getByLabelText("Type payments-api to confirm deletion"), {
      target: { value: "wrong-service" },
    });
    expect(reopenedConfirmButton).toBeDisabled();
    expect(deleteCalls).toBe(0);

    fireEvent.change(within(reopenedDialog).getByLabelText("Type payments-api to confirm deletion"), {
      target: { value: "payments-api" },
    });
    expect(reopenedConfirmButton).not.toBeDisabled();
    fireEvent.click(reopenedConfirmButton);

    expect(await screen.findByText("Delete failed.")).toBeInTheDocument();
    expect(deleteCalls).toBe(1);
    expect(screen.getByRole("heading", { name: "Editing Target" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Open Detail Page" })).toBeInTheDocument();
  });

  it("renders the topology page from the service-scoped route", async () => {
    window.history.replaceState({}, "", "/services/payments-api/topology");

    render(<App />);

    expect(await screen.findByText("Workflow Topology")).toBeInTheDocument();
    expect(screen.getByText("validate")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Entry Routes" })).toBeInTheDocument();
  });

  it("reports non-Error topology failures and retries them", async () => {
    window.history.replaceState({}, "", "/services/payments-api/topology");
    const baseImpl = fetchMock.getMockImplementation();
    let attempts = 0;
    fetchMock.mockImplementation(async (input, init) => {
      if (String(input) === "/studio/services/payments-api/workflow/topology") {
        attempts += 1;
        throw "offline";
      }
      return await baseImpl!(input, init);
    });

    render(<App />);

    expect(await screen.findByText("Unable to load workflow topology.")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Reload" }));
    await waitFor(() => expect(attempts).toBe(2));
  });

  it("renders the DLQ explorer, applies pagination, and links back to task detail", async () => {
    window.history.replaceState({}, "", "/services/payments-api/dlq");

    render(<App />);

    expect(await screen.findByText("DLQ Explorer")).toBeInTheDocument();
    expect(await screen.findByText("upstream_timeout")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "payments-api/task-123" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Load Next Page" }));

    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith("/studio/services/payments-api/dlq/messages?limit=50&cursor=cursor-2", undefined),
    );
  });

  it("switches the DLQ explorer into broker mode and hides indexed-only affordances", async () => {
    window.history.replaceState({}, "", "/services/payments-api/dlq?mode=broker&task_id=task-123");

    render(<App />);

    expect(await screen.findByText("DLQ Explorer")).toBeInTheDocument();
    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        "/studio/services/payments-api/broker/dlq/messages?limit=50&task_id=task-123",
        undefined,
      ),
    );
    expect(screen.getByText(/Live broker inspection mode is active/)).toBeInTheDocument();
    expect(screen.getByText("broker_rejected")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Load Next Page" })).not.toBeInTheDocument();
    expect(screen.getByPlaceholderText("Reason")).toBeDisabled();
    expect(screen.getByPlaceholderText("Source queue")).toBeDisabled();
    expect(screen.getByPlaceholderText("State")).toBeDisabled();
  });

  it("applies every indexed DLQ filter, switches modes, and reports non-error failures", async () => {
    window.history.replaceState({}, "", "/services/payments-api/dlq");
    const baseImpl = fetchMock.getMockImplementation();
    let filteredCalls = 0;
    fetchMock.mockImplementation(async (input, init) => {
      const url = String(input);
      if (url.includes("/dlq/messages?") && !url.includes("/broker/") && url.includes("queue_name=payments.dlq") && (init?.method || "GET") === "GET") {
        filteredCalls += 1;
        if (filteredCalls === 1) {
          return jsonResponse({ service_id: "payments-api", items: [], next_cursor: null });
        }
        throw "dlq-offline";
      }
      if (url === "/studio/services/payments-api/broker/dlq/messages?limit=7&queue_name=payments.dlq&task_id=task-123") {
        return jsonResponse({ service_id: "payments-api", items: [] });
      }
      return await baseImpl!(input, init);
    });
    render(<App />);

    expect(await screen.findByText("upstream_timeout")).toBeInTheDocument();
    fireEvent.change(screen.getByPlaceholderText("Queue name"), { target: { value: "payments.dlq" } });
    fireEvent.change(screen.getByPlaceholderText("Task id"), { target: { value: "task-123" } });
    fireEvent.change(screen.getByPlaceholderText("Reason"), { target: { value: "timeout" } });
    fireEvent.change(screen.getByPlaceholderText("Source queue"), { target: { value: "payments.stage" } });
    fireEvent.change(screen.getByPlaceholderText("State"), { target: { value: "dead_lettered" } });
    fireEvent.change(screen.getByPlaceholderText("50"), { target: { value: "7" } });
    fireEvent.click(screen.getByRole("button", { name: "Apply Filters" }));
    expect(await screen.findByText("No DLQ messages matched the current filters.")).toBeInTheDocument();
    expect(window.location.search).toContain("queue_name=payments.dlq");

    fireEvent.click(screen.getByRole("button", { name: "Broker Mode" }));
    expect(await screen.findByText(/Live broker inspection mode is active/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Indexed Mode" }));
    expect(await screen.findByText("Unable to load DLQ messages.")).toBeInTheDocument();
  });

  it("shows unsupported broker mode and broker fallback fields for missing metadata", async () => {
    window.history.replaceState({}, "", "/services/unknown/dlq?mode=broker");
    const baseImpl = fetchMock.getMockImplementation();
    fetchMock.mockImplementation(async (input, init) => {
      const url = String(input);
      if (url === "/studio/services/unknown/broker/dlq/messages?limit=50") {
        return jsonResponse({
          service_id: "unknown",
          items: [{
            service_id: "unknown", queue_name: "unknown.dlq", message_key: "message-1", task_id: null,
            correlation_id: null, reason: null, source_queue_name: null, body_encoding: "base64",
            dead_lettered_at: null, headers: {}, body: null, raw_body_b64: "", redelivered: null, task_ref: null,
          }],
        });
      }
      return await baseImpl!(input, init);
    });
    render(<App />);

    expect(await screen.findByText("This service does not advertise broker-backed DLQ inspection.")).toBeInTheDocument();
    expect(screen.getByText("broker_dead_letter")).toBeInTheDocument();
    expect(screen.getByText(/source unknown/)).toBeInTheDocument();
    expect(screen.getByText("Redelivered: unknown")).toBeInTheDocument();
    expect(screen.getByText("Task: unattributed")).toBeInTheDocument();
  });

  it("renders the global failed tasks page and opens a failure detail", async () => {
    window.history.replaceState({}, "", "/failed-tasks");

    render(<App />);

    expect(await screen.findByRole("heading", { name: "Failed Tasks" })).toBeInTheDocument();
    expect(await screen.findByRole("heading", { name: "Email Notifications" })).toBeInTheDocument();
    expect(await screen.findByText("RuntimeError")).toBeInTheDocument();
    expect(screen.getByText("boom")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "View" }));

    expect(await screen.findByText("Failure Detail")).toBeInTheDocument();
    expect(screen.getByDisplayValue(/task-123/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Close" }));
    expect(screen.queryByText("Failure Detail")).not.toBeInTheDocument();
  });

  it("uses stable fallbacks for non-Error failed-task settings and detail failures", async () => {
    window.history.replaceState({}, "", "/failed-tasks");
    const baseImpl = fetchMock.getMockImplementation();
    fetchMock.mockImplementation(async (input, init) => {
      const url = String(input);
      if (url === "/studio/failed-task-email-settings") {
        throw "settings-offline";
      }
      if (url === "/studio/failed-tasks/payments-api/failure-1") {
        throw "detail-offline";
      }
      return await baseImpl!(input, init);
    });

    render(<App />);

    expect(await screen.findByText("Unable to load email notification settings.")).toBeInTheDocument();
    expect(await screen.findByText("RuntimeError")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "View" }));
    expect(await screen.findByText("Unable to load failed task detail.")).toBeInTheDocument();
  });

  it("updates failed-task email notification settings", async () => {
    window.history.replaceState({}, "", "/failed-tasks");

    render(<App />);

    expect(await screen.findByRole("heading", { name: "Email Notifications" })).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Email batch wait seconds"), { target: { value: "60" } });
    fireEvent.click(screen.getByLabelText("Enabled"));

    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        "/studio/failed-task-email-settings",
        expect.objectContaining({
          method: "PATCH",
          body: JSON.stringify({ enabled: true }),
        }),
      ),
    );

    fireEvent.change(screen.getByPlaceholderText("0"), { target: { value: "3600" } });
    fireEvent.click(screen.getByRole("button", { name: "Save Wait" }));

    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        "/studio/failed-task-email-settings",
        expect.objectContaining({
          method: "PATCH",
          body: JSON.stringify({ batch_wait_seconds: 3600 }),
        }),
      ),
    );
  });

  it("shows a validation error instead of retrying with malformed failed-task payload override", async () => {
    window.history.replaceState({}, "", "/failed-tasks");
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(true);

    render(<App />);

    expect(await screen.findByRole("heading", { name: "Failed Tasks" })).toBeInTheDocument();
    fireEvent.click(await screen.findByRole("button", { name: "View" }));
    expect(await screen.findByText("Failure Detail")).toBeInTheDocument();

    fireEvent.change(screen.getByPlaceholderText("Optional JSON payload override"), {
      target: { value: '{"task_id": "task-123",}' },
    });
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));

    expect(await screen.findByText("Override payload must be valid JSON.")).toBeInTheDocument();
    expect(confirmSpy).not.toHaveBeenCalled();
    expect(fetchMock).not.toHaveBeenCalledWith(
      "/studio/failed-tasks/payments-api/failure-1/retry",
      expect.objectContaining({ method: "POST" }),
    );
    confirmSpy.mockRestore();
  });

  it("operates the full failed-task investigation, retry, copy, download, pagination, and delete workflow", async () => {
    window.history.replaceState({}, "", "/failed-tasks");
    const baseImpl = fetchMock.getMockImplementation();
    let listCalls = 0;
    const detail = {
      service_id: "payments-api",
      service_name: "Payments API",
      failure_id: "failure-1",
      task_id: "task-123",
      correlation_id: "corr-123",
      queue_name: "payments.stage",
      source_queue_name: "payments.stage",
      retry_queue_name: "payments.retry",
      dlq_name: "payments.dlq",
      status: "DLQ",
      attempt: 3,
      max_attempts: 3,
      failed_at: "2026-05-26T10:30:00Z",
      error_type: "RuntimeError",
      error_message: "boom",
      traceback: "traceback",
      investigation_status: "unreviewed",
      investigation_note: "initial note",
      retry_status: "not_retried",
      retry_note: "retry later",
      payload_available: true,
      body: { task_id: "task-123" },
      input_preview: null,
      metadata: {},
      last_logs: [],
      task_ref: null,
    };
    fetchMock.mockImplementation(async (input, init) => {
      const url = String(input);
      const method = init?.method || "GET";
      if (url.startsWith("/studio/failed-tasks?") && method === "GET") {
        listCalls += 1;
        if (listCalls === 1) {
          return jsonResponse({
            items: [{ ...detail, error_type: null, error_message: null, task_id: null }],
            next_cursor: "next-page",
            errors: [{ service_id: "shipping-api", code: "unreachable", detail: "offline" }],
          });
        }
        if (listCalls === 2) {
          return jsonResponse({ items: [{ ...detail, failure_id: "failure-2" }], next_cursor: null, errors: [] });
        }
        return jsonResponse({ items: [{ ...detail }], next_cursor: null, errors: [] });
      }
      if (url === "/studio/failed-tasks/payments-api/failure-1" && method === "GET") {
        return jsonResponse(detail);
      }
      if (url.endsWith("/mark-investigated") && method === "POST") {
        return jsonResponse({ ...detail, investigation_status: "investigated" });
      }
      if (url.endsWith("/mark-uninvestigated") && method === "POST") {
        return jsonResponse({ ...detail, investigation_status: "unreviewed" });
      }
      if (url.endsWith("/retry") && method === "POST") {
        return jsonResponse({ failure_id: "failure-1", target_queue: "payments.retry", retry_status: "retried", retried_at: "2026-05-26T11:00:00Z" });
      }
      if (url === "/studio/failed-tasks/payments-api/failure-1" && method === "DELETE") {
        return jsonResponse({ service_id: "payments-api", failure_id: "failure-1", deleted: true });
      }
      return await baseImpl!(input, init);
    });
    const clipboardWrite = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", { configurable: true, value: { writeText: clipboardWrite } });
    Object.defineProperty(URL, "createObjectURL", { configurable: true, value: vi.fn(() => "blob:failed-task") });
    Object.defineProperty(URL, "revokeObjectURL", { configurable: true, value: vi.fn() });
    const anchorClick = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => undefined);
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(true);

    render(<App />);

    expect(await screen.findByText("1 service read failed while loading failed tasks.")).toBeInTheDocument();
    expect(screen.getByText("No error message captured.")).toBeInTheDocument();
    expect(screen.getByText("unattributed")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Load Next Page" }));
    await waitFor(() => expect(listCalls).toBe(2));

    fireEvent.change(screen.getByPlaceholderText("Service id"), { target: { value: "payments-api" } });
    fireEvent.change(screen.getByPlaceholderText("Queue"), { target: { value: "payments.stage" } });
    fireEvent.change(screen.getByPlaceholderText("DLQ"), { target: { value: "payments.dlq" } });
    fireEvent.change(screen.getByPlaceholderText("Error type"), { target: { value: "RuntimeError" } });
    fireEvent.change(screen.getByPlaceholderText("Status"), { target: { value: "DLQ" } });
    fireEvent.change(screen.getByPlaceholderText("Task id"), { target: { value: "task-123" } });
    fireEvent.change(screen.getByPlaceholderText("Worker"), { target: { value: "worker-1" } });
    fireEvent.change(screen.getByDisplayValue("Unreviewed"), { target: { value: "investigated" } });
    fireEvent.change(screen.getByPlaceholderText("50"), { target: { value: "10" } });
    fireEvent.click(screen.getByRole("button", { name: "Apply Filters" }));
    await waitFor(() => expect(listCalls).toBe(3));

    fireEvent.click(screen.getByRole("button", { name: "View" }));
    expect(await screen.findByText("Failure Detail")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Copy Payload" }));
    fireEvent.click(screen.getByRole("button", { name: "Copy Error" }));
    await waitFor(() => expect(clipboardWrite).toHaveBeenCalledTimes(2));
    fireEvent.click(screen.getByRole("button", { name: "Download JSON" }));
    expect(anchorClick).toHaveBeenCalled();

    fireEvent.change(screen.getByPlaceholderText("Operator"), { target: { value: "oncall" } });
    fireEvent.change(screen.getByPlaceholderText("Investigation note"), { target: { value: "checked" } });
    fireEvent.click(screen.getByRole("button", { name: "Mark Investigated" }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(
      "/studio/failed-tasks/payments-api/failure-1/mark-investigated",
      expect.objectContaining({ method: "POST" }),
    ));
    fireEvent.click(screen.getByRole("button", { name: "Mark Unreviewed" }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(
      "/studio/failed-tasks/payments-api/failure-1/mark-uninvestigated",
      expect.objectContaining({ method: "POST" }),
    ));

    fireEvent.change(screen.getByPlaceholderText("Target queue"), { target: { value: "payments.retry" } });
    fireEvent.change(screen.getByPlaceholderText("Retry note"), { target: { value: "retry now" } });
    fireEvent.change(screen.getByPlaceholderText("Optional JSON payload override"), { target: { value: '{"safe":true}' } });
    confirmSpy.mockReturnValueOnce(false).mockReturnValue(true);
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(
      "/studio/failed-tasks/payments-api/failure-1/retry",
      expect.objectContaining({ method: "POST" }),
    ));

    fireEvent.click(screen.getByRole("button", { name: "Delete" }));
    await waitFor(() => expect(screen.queryByText("Failure Detail")).not.toBeInTheDocument());
    anchorClick.mockRestore();
    confirmSpy.mockRestore();
  });

  it("renders failed-task loading failures, disabled email settings, and update failures", async () => {
    window.history.replaceState({}, "", "/failed-tasks");
    const baseImpl = fetchMock.getMockImplementation();
    fetchMock.mockImplementation(async (input, init) => {
      const url = String(input);
      const method = init?.method || "GET";
      if (url.startsWith("/studio/failed-tasks?") && method === "GET") {
        throw "failed-list";
      }
      if (url === "/studio/failed-task-email-settings" && method === "GET") {
        return jsonResponse({ configured: false, enabled: false, batch_wait_seconds: 172800, max_batch_wait_seconds: 604800, receivers: [] });
      }
      if (url === "/studio/failed-task-email-settings" && method === "PATCH") {
        throw "failed-settings";
      }
      return await baseImpl!(input, init);
    });
    render(<App />);

    expect(await screen.findByText("Unable to load failed tasks.")).toBeInTheDocument();
    expect(await screen.findByText("Not configured")).toBeInTheDocument();
    expect(screen.getByText("Wait: 2 days")).toBeInTheDocument();
    expect(screen.getByLabelText("Enabled")).toBeDisabled();

    fetchMock.mockImplementation(async (input, init) => {
      const url = String(input);
      if (url === "/studio/failed-task-email-settings" && (init?.method || "GET") === "PATCH") {
        throw "failed-settings";
      }
      return await baseImpl!(input, init);
    });
  });

  it("submits task search and renders indexed task results", async () => {
    window.history.replaceState({}, "", "/tasks/search");

    render(<App />);

    fireEvent.change(await screen.findByLabelText("Service ID"), { target: { value: "payments-api" } });
    fireEvent.change(screen.getByLabelText("Task ID"), { target: { value: "task-123" } });
    fireEvent.change(screen.getByLabelText("Correlation ID"), { target: { value: "corr-123" } });
    fireEvent.change(screen.getByLabelText("Status"), { target: { value: "running" } });
    fireEvent.change(screen.getByLabelText("Stage"), { target: { value: "authorize" } });
    fireEvent.change(screen.getByLabelText("From (local time)"), { target: { value: "2026-04-08T09:00" } });
    fireEvent.change(screen.getByLabelText("To (local time)"), { target: { value: "2026-04-08T11:00" } });
    fireEvent.click(screen.getByRole("button", { name: "Search" }));

    expect(await screen.findByText(/Matches:/)).toBeInTheDocument();
    expect(Object.fromEntries(new URLSearchParams(window.location.search))).toEqual({
      service_id: "payments-api",
      task_id: "task-123",
      correlation_id: "corr-123",
      status: "running",
      stage: "authorize",
      from: "2026-04-08T09:00",
      to: "2026-04-08T11:00",
    });
    expect(screen.getByRole("link", { name: "Open Task Detail" })).toBeInTheDocument();
    expect(screen.getByText(/correlation=corr-123/)).toBeInTheDocument();
    expect(screen.getByText("running", { selector: ".studio-task-chip" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Clear" }));
    expect(window.location.search).toBe("");
    expect(screen.getByLabelText("Task ID")).toHaveValue("");
  });

  it("renders the Loki fallback notice for task search results", async () => {
    window.history.replaceState({}, "", "/tasks/search");

    render(<App />);

    fireEvent.change(await screen.findByPlaceholderText("task_id"), { target: { value: "task-loki" } });
    fireEvent.click(screen.getByRole("button", { name: "Search" }));

    expect(
      await screen.findByText("Task not found in the recent index — result sourced from Loki log metadata."),
    ).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Open Task Detail" })).toHaveAttribute(
      "href",
      "/tasks/payments-api/task-loki",
    );
  });

  it("appends task search pages and preserves results when a cursor request fails", async () => {
    window.history.replaceState({}, "", "/tasks/search");
    let calls = 0;
    fetchMock.mockImplementation(async (input) => {
      const url = String(input);
      if (url === "/studio/services") {
        return serviceListResponse(services);
      }
      if (url.startsWith("/studio/tasks/search?") && new URL(url, "http://studio.test").searchParams.get("task_id") === "paged" && !url.includes("cursor=")) {
        return jsonResponse({
          count: 1,
          items: [{
            service_id: "payments-api", service_name: "Payments API", environment: "prod", task_id: "page-1",
            first_seen_at: null, last_seen_at: null, latest_event_type: null, latest_event_at: null,
            latest_ingested_at: null, detail_path: "/studio/tasks/payments-api/page-1",
          }],
          next_cursor: "next",
        });
      }
      if (url.includes("cursor=next")) {
        calls += 1;
        throw "offline";
      }
      throw new Error(`Unhandled fetch: ${url}`);
    });

    render(<App />);
    fireEvent.change(await screen.findByPlaceholderText("task_id"), { target: { value: "paged" } });
    fireEvent.click(screen.getByRole("button", { name: "Search" }));
    expect(await screen.findByText(/page-1/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Load Next Page" }));
    expect(await screen.findByText("Unable to search tasks.")).toBeInTheDocument();
    expect(screen.getByText(/page-1/)).toBeInTheDocument();
    expect(calls).toBe(1);
  });

  it("renders the direct task detail route with graph, timeline, logs, joins, and SSE cleanup", async () => {
    window.history.replaceState({}, "", "/tasks/payments-api/task-123");

    const { unmount } = render(<App />);

    expect(await screen.findByText("Task Detail")).toBeInTheDocument();
    expect(screen.getByText("Failure summary")).toBeInTheDocument();
    expect(screen.getByText("upstream_timeout")).toBeInTheDocument();
    expect(await screen.findByText("Task Trace")).toBeInTheDocument();
    expect(screen.queryByTestId("rf-root")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Open Graph" }));
    expect(await screen.findByTestId("rf-root")).toBeInTheDocument();
    expect(screen.getByText("Task Timeline")).toBeInTheDocument();
    expect(screen.getByText("Task Logs")).toBeInTheDocument();
    expect(screen.getByText("Joined Refs")).toBeInTheDocument();
    expect(screen.getByText("Join Warnings")).toBeInTheDocument();
    expect(screen.getByText("Section Errors")).toBeInTheDocument();
    expect(screen.getAllByText("api").length).toBeGreaterThan(0);
    expect(screen.getByText("task log line")).toBeInTheDocument();
    expect(screen.getByText(/"event": "task_json"/)).toBeInTheDocument();
    expect(screen.getByText(/"retry": 1/)).toBeInTheDocument();
    expect(screen.getByText(/"stage": "received"/)).toBeInTheDocument();
    expect(screen.getByText("{\"oops\":")).toBeInTheDocument();
    expect(screen.getAllByText("null").length).toBeGreaterThan(0);
    expect(await screen.findByText("payments.process_payment")).toBeInTheDocument();
    expect(screen.getByText("Path Duration")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /gateway_timeout/ }));
    fireEvent.click(screen.getByRole("button", { name: /attempt-1/ }));
    fireEvent.click(screen.getByRole("button", { name: "Filter Logs" }));
    expect(document.body.textContent?.indexOf("attempt-1")).toBeLessThan(document.body.textContent?.indexOf("gateway_timeout") ?? 0);
    expect(screen.queryByRole("link", { name: "Open Span" })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "View Span" }));
    const spanDialog = await screen.findByRole("dialog", { name: "Span Details" });
    expect(within(spanDialog).getByText("span-123")).toBeInTheDocument();
    expect(within(spanDialog).getByText("parent-456")).toBeInTheDocument();
    expect(within(spanDialog).getByDisplayValue(/"messaging.system": "rabbitmq"/)).toBeInTheDocument();
    expect(within(spanDialog).getByDisplayValue("https://tempo-public.example.test/api/traces/trace-abc")).toBeInTheDocument();
    fireEvent.click(within(spanDialog).getByRole("button", { name: "Close" }));
    expect(screen.queryByRole("dialog", { name: "Span Details" })).not.toBeInTheDocument();
    expect(document.body.textContent).not.toContain("\u001b[31m");
    expect(MockEventSource.instances.some((item) => item.url === "/studio/tasks/payments-api/task-123/events/stream")).toBe(true);

    MockEventSource.instances[0]?.emit("event", {
      service_id: "payments-api",
      ingest_method: "pull",
      ingested_at: "2026-04-08T10:05:00Z",
      dedupe_key: "evt-2",
      out_of_order: false,
      task_id: "task-123",
      event_type: "task.completed",
      source_kind: "status",
      component: "worker",
      timestamp: "2026-04-08T10:05:00Z",
      payload: { status: "completed" },
    });

    expect(await screen.findByText("completed")).toBeInTheDocument();

    unmount();

    expect(MockEventSource.instances[0]?.closed).toBe(true);
  });

  it("does not present a completed task as a failure", async () => {
    window.history.replaceState({}, "", "/tasks/payments-api/task-123");
    const baseImpl = fetchMock.getMockImplementation();
    fetchMock.mockImplementation(async (input, init) => {
      if (String(input) === "/studio/tasks/payments-api/task-123?join=all") {
        const detail = taskDetailResponse({ dlqItems: [] });
        detail.latest_status.event.status = "completed";
        detail.execution_graph.summary.status = "completed";
        return jsonResponse(detail);
      }
      return await baseImpl!(input, init);
    });

    render(<App />);

    expect(await screen.findByText("Task Detail")).toBeInTheDocument();
    expect(screen.getByText("Current signal")).toBeInTheDocument();
    expect(screen.getByText("Task has no terminal failure signal.")).toBeInTheDocument();
    expect(screen.queryByText("Failure summary")).not.toBeInTheDocument();
  });

  it("shows stable fallback messages for non-Error task telemetry failures and malformed SSE", async () => {
    window.history.replaceState({}, "", "/tasks/payments-api/task-123");
    services[0] = {
      ...services[0],
      metrics_config: {
        provider: "prometheus",
        base_url: "https://prometheus.example.test",
        namespace: "prod",
        service_selector_labels: { app: "payments-api" },
      },
    };
    const baseImpl = fetchMock.getMockImplementation();
    fetchMock.mockImplementation(async (input, init) => {
      const url = String(input);
      if (
        url === "/studio/tasks/payments-api/task-123/events?limit=50" ||
        url.startsWith("/studio/tasks/payments-api/task-123/logs?") ||
        url.startsWith("/studio/tasks/payments-api/task-123/metrics?") ||
        url === "/studio/tasks/payments-api/task-123/trace-path"
      ) {
        throw "offline";
      }
      return await baseImpl!(input, init);
    });

    render(<App />);

    expect(await screen.findByText("Unable to load task timeline.")).toBeInTheDocument();
    expect(await screen.findByText("Unable to load task logs.")).toBeInTheDocument();
    expect(await screen.findByText("Unable to load task metrics.")).toBeInTheDocument();
    expect(await screen.findByText("Unable to load task trace path.")).toBeInTheDocument();
    const source = MockEventSource.instances.find((item) => item.url.includes("/tasks/payments-api/task-123/events/stream"));
    act(() => source?.emitRaw("event", "not-json"));
    expect(screen.getByText("Task Detail")).toBeInTheDocument();
  });

  it("falls back to selection-based Mermaid clipboard copying", async () => {
    window.history.replaceState({}, "", "/tasks/payments-api/task-123");
    const writeText = vi.fn().mockRejectedValue(new Error("blocked"));
    const execCommand = vi.fn().mockReturnValue(true);
    Object.defineProperty(navigator, "clipboard", { configurable: true, value: { writeText } });
    Object.defineProperty(document, "execCommand", { configurable: true, value: execCommand });

    render(<App />);

    expect(await screen.findByText("Task Detail")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Copy" }));
    expect(await screen.findByRole("button", { name: "Copied" })).toBeInTheDocument();
    expect(writeText).toHaveBeenCalled();
    expect(execCommand).toHaveBeenCalledWith("copy");
    Object.defineProperty(navigator, "clipboard", { configurable: true, value: undefined });
  });

  it("selects the visible Mermaid export when browser copy APIs are blocked", async () => {
    window.history.replaceState({}, "", "/tasks/payments-api/task-123");
    Object.defineProperty(navigator, "clipboard", { configurable: true, value: undefined });
    Object.defineProperty(document, "execCommand", { configurable: true, value: vi.fn().mockReturnValue(false) });

    render(<App />);

    expect(await screen.findByText("Task Detail")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Copy" }));
    expect(await screen.findByRole("button", { name: "Selected" })).toBeInTheDocument();
  });

  it("renders task no-provider states and a non-Error detail failure", async () => {
    window.history.replaceState({}, "", "/tasks/payments-api/task-123");
    services[0] = { ...services[0], log_config: null, metrics_config: null };
    const first = render(<App />);
    expect(await screen.findByText("No log provider configured for this service.")).toBeInTheDocument();
    expect(await screen.findByText("No metrics provider configured for this service.")).toBeInTheDocument();
    first.unmount();

    fetchMock.mockImplementation(async (input) => {
      if (String(input) === "/studio/services") {
        return serviceListResponse(services);
      }
      if (String(input) === "/studio/tasks/payments-api/task-123?join=all") {
        throw "offline";
      }
      throw new Error(`Unhandled fetch: ${String(input)}`);
    });
    render(<App />);
    expect(await screen.findByText("Unable to load task detail.")).toBeInTheDocument();
  });

  it("lays out an execution graph with no root node in its lazy graph surface", async () => {
    const { GraphSurface } = await import("./graph-surface");
    const cyclicGraph: ExecutionGraph = {
      task_id: "task-cycle",
      topology_kind: "workflow",
      summary: { graph_completeness: "complete" },
      nodes: [
        { id: "one", kind: "task" },
        { id: "two", kind: "task_attempt" },
      ],
      edges: [
        { source: "one", target: "two", kind: "spawned" },
        { source: "two", target: "one", kind: "retried_as" },
      ],
      annotations: {},
      related_task_ids: [],
    };

    render(<GraphSurface graph={cyclicGraph} />);
    expect(screen.getByTestId("rf-root")).toBeInTheDocument();
    expect(screen.getByText("task_attempt")).toBeInTheDocument();
  });

  it("applies the task log source filter through the Studio route", async () => {
    window.history.replaceState({}, "", "/tasks/payments-api/task-123");

    render(<App />);

    expect(await screen.findByText("Task Detail")).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Task log source"), { target: { value: "api" } });
    fireEvent.click(screen.getByRole("button", { name: "Reload Logs" }));

    await waitFor(() => {
      const matchingCall = fetchMock.mock.calls.find(([input]) => {
        const parsed = new URL(String(input), "http://studio.test");
        return (
          parsed.pathname === "/studio/tasks/payments-api/task-123/logs" &&
          parsed.searchParams.get("source") === "api" &&
          parsed.searchParams.get("correlation_id") === "corr-123" &&
          parsed.searchParams.get("from") === "2026-04-08T10:00:00Z" &&
          Boolean(parsed.searchParams.get("to"))
        );
      });
      expect(matchingCall).toBeTruthy();
    });
  });

  it("auto-derives the task log window from the task lifecycle", async () => {
    window.history.replaceState({}, "", "/tasks/payments-api/task-123");

    render(<App />);

    expect(await screen.findByText("Task Detail")).toBeInTheDocument();
    await waitFor(() => {
      const matchingCall = fetchMock.mock.calls.find(([input]) => {
        const parsed = new URL(String(input), "http://studio.test");
        return (
          parsed.pathname === "/studio/tasks/payments-api/task-123/logs" &&
          parsed.searchParams.get("correlation_id") === "corr-123" &&
          parsed.searchParams.get("from") === "2026-04-08T10:00:00Z" &&
          Boolean(parsed.searchParams.get("to"))
        );
      });
      expect(matchingCall).toBeTruthy();
    });
    expect(screen.getByText(/Auto window:/)).toBeInTheDocument();
  });

  it("uses the manual task log window override when provided", async () => {
    window.history.replaceState({}, "", "/tasks/payments-api/task-123");

    render(<App />);

    expect(await screen.findByText("Task Detail")).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Task log window mode"), { target: { value: "manual" } });
    await waitFor(() => expect(screen.getByLabelText("Task log from")).toBeEnabled());
    const taskLogFrom = isoToLocalDateTime("2026-04-08T09:55:00Z");
    const taskLogTo = isoToLocalDateTime("2026-04-08T10:06:00Z");
    fireEvent.change(screen.getByLabelText("Task log from"), { target: { value: taskLogFrom } });
    fireEvent.change(screen.getByLabelText("Task log to"), { target: { value: taskLogTo } });
    await waitFor(() => expect(screen.getByLabelText("Task log from")).toHaveValue(taskLogFrom));
    await waitFor(() => expect(screen.getByLabelText("Task log to")).toHaveValue(taskLogTo));
    fireEvent.click(screen.getByRole("button", { name: "Reload Logs" }));

    await waitFor(() => {
      const matchingCall = fetchMock.mock.calls.find(([input]) => {
        const parsed = new URL(String(input), "http://studio.test");
        return (
          parsed.pathname === "/studio/tasks/payments-api/task-123/logs" &&
          parsed.searchParams.get("from") === new Date("2026-04-08T09:55:00Z").toISOString() &&
          parsed.searchParams.get("to") === new Date("2026-04-08T10:06:00Z").toISOString()
        );
      });
      expect(matchingCall).toBeTruthy();
    });
  });

  it("applies quick task log windows immediately", async () => {
    window.history.replaceState({}, "", "/tasks/payments-api/task-123");

    const baseImpl = fetchMock.getMockImplementation();
    fetchMock.mockImplementation(async (input, init) => {
      const url = String(input);
      const method = init?.method || "GET";

      if (url.startsWith("/studio/tasks/payments-api/task-123/logs?") && method === "GET") {
        const parsed = new URL(url, "http://studio.test");
        const from = parsed.searchParams.get("from");
        const to = parsed.searchParams.get("to");
        const isQuickWindow =
          Boolean(from) && Boolean(to) && new Date(to || "").getTime() - new Date(from || "").getTime() === 15 * 60 * 1000;
        if (isQuickWindow) {
          return jsonResponse({ count: 0, items: [], next_cursor: null });
        }
        return jsonResponse({
          count: 1,
          items: [
            {
              service_id: "payments-api",
              task_id: "task-123",
              correlation_id: "corr-123",
              timestamp: "2026-04-25T15:46:04Z",
              level: "info",
              source: "api",
              message: "old task log line",
              fields: {},
            },
          ],
          next_cursor: null,
        });
      }

      return baseImpl?.(input, init) ?? jsonResponse({});
    });

    render(<App />);

    expect(await screen.findByText("old task log line")).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Task log window mode"), { target: { value: "15m" } });

    await waitFor(() => {
      const matchingCall = fetchMock.mock.calls.find(([input]) => {
        const parsed = new URL(String(input), "http://studio.test");
        const from = parsed.searchParams.get("from");
        const to = parsed.searchParams.get("to");
        return (
          parsed.pathname === "/studio/tasks/payments-api/task-123/logs" &&
          Boolean(from) &&
          Boolean(to) &&
          new Date(to || "").getTime() - new Date(from || "").getTime() === 15 * 60 * 1000
        );
      });
      expect(matchingCall).toBeTruthy();
    });
    await waitFor(() => expect(screen.queryByText("old task log line")).not.toBeInTheDocument());
  });

  it("renders task log loading, empty, and error states", async () => {
    window.history.replaceState({}, "", "/tasks/payments-api/task-123");

    const baseImpl = fetchMock.getMockImplementation();
    let taskLogCalls = 0;
    let resolveTaskLogs: ((response: Response) => void) | null = null;
    fetchMock.mockImplementation(async (input, init) => {
      const url = String(input);
      const method = init?.method || "GET";

      if (url.startsWith("/studio/tasks/payments-api/task-123/logs?") && method === "GET") {
        taskLogCalls += 1;
        if (taskLogCalls === 1) {
          return await new Promise<Response>((resolve) => {
            resolveTaskLogs = resolve;
          });
        }
        return jsonResponse({ detail: "Task Loki query failed." }, 502);
      }

      return baseImpl?.(input, init) ?? jsonResponse({});
    });

    render(<App />);

    expect(await screen.findByText("Task Detail")).toBeInTheDocument();
    expect(await screen.findByText("Loading task logs...")).toBeInTheDocument();

    await act(async () => {
      resolveTaskLogs?.(jsonResponse({ count: 0, items: [], next_cursor: null }));
      await Promise.resolve();
    });

    expect(await screen.findByText("No task logs matched the current filters.")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Reload Logs" }));

    expect(await screen.findByText("Task Loki query failed.")).toBeInTheDocument();
  });

  it("renders approximate task metrics and manual metrics window requests", async () => {
    window.history.replaceState({}, "", "/tasks/payments-api/task-123");
    services[0] = {
      ...services[0],
      metrics_config: {
        provider: "prometheus",
        base_url: "https://prometheus.example.test",
        namespace: "prod",
        service_selector_labels: { app: "payments-api" },
        namespace_label: "namespace",
        pod_label: "pod",
        container_label: "container",
        step_seconds: 30,
        task_window_padding_seconds: 120,
      },
    };

    const baseImpl = fetchMock.getMockImplementation();
    fetchMock.mockImplementation(async (input, init) => {
      const url = String(input);
      const method = init?.method || "GET";
      if (url === "/studio/tasks/payments-api/task-123?join=all" && method === "GET") {
        const detail = taskDetailResponse();
        detail.execution_graph.nodes.push(
          {
            id: "child-resource-start",
            kind: "resource_sample",
            label: "child start resource sample",
            task_id: "child-1",
            timestamp: "2026-04-08T09:59:00Z",
            annotations: { sample_kind: "start", cpu_process_seconds: 0, memory_rss_bytes: 0 },
          },
          {
            id: "child-resource-end",
            kind: "resource_sample",
            label: "child end resource sample",
            task_id: "child-1",
            timestamp: "2026-04-08T10:06:00Z",
            annotations: { sample_kind: "end", cpu_process_seconds: 99, memory_rss_bytes: 99 },
          },
        );
        return jsonResponse(detail);
      }
      if (url.startsWith("/studio/tasks/payments-api/task-123/metrics") && method === "GET") {
        return jsonResponse(metricsResponse("task-123"));
      }
      return baseImpl?.(input, init) ?? jsonResponse({});
    });

    render(<App />);

    expect(await screen.findByText("Task Kubernetes Metrics")).toBeInTheDocument();
    expect(await screen.findByText("Exact Task Resources")).toBeInTheDocument();
    expect(await screen.findByText("0.750s")).toBeInTheDocument();
    await waitFor(() => expect(screen.getAllByText(/approximate for long-running workers/i).length).toBeGreaterThan(0));
    await waitFor(() => expect(screen.getAllByText("384.00 MiB").length).toBeGreaterThan(0));
    fireEvent.change(screen.getByLabelText("Task metrics window mode"), { target: { value: "manual" } });
    const from = isoToLocalDateTime("2026-04-08T09:45:00Z");
    const to = isoToLocalDateTime("2026-04-08T10:15:00Z");
    fireEvent.change(screen.getByLabelText("Task metrics from"), { target: { value: from } });
    fireEvent.change(screen.getByLabelText("Task metrics to"), { target: { value: to } });

    await waitFor(() => {
      const matchingCall = fetchMock.mock.calls.find(([input]) => {
        const parsed = new URL(String(input), "http://studio.test");
        return (
          parsed.pathname === "/studio/tasks/payments-api/task-123/metrics" &&
          parsed.searchParams.get("from") === new Date("2026-04-08T09:45:00Z").toISOString() &&
          parsed.searchParams.get("to") === new Date("2026-04-08T10:15:00Z").toISOString()
        );
      });
      expect(matchingCall).toBeTruthy();
    });
  });

  it("advances the auto task log window end when reload logs is clicked on a running task", async () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-04-08T10:01:00Z"));
    window.history.replaceState({}, "", "/tasks/payments-api/task-123");

    async function flushRender() {
      await act(async () => {
        await Promise.resolve();
        await Promise.resolve();
      });
    }

    render(<App />);

    await flushRender();
    expect(screen.getByText("Task Detail")).toBeInTheDocument();
    expect(
      fetchMock.mock.calls.find(([input]) => {
        const parsed = new URL(String(input), "http://studio.test");
        return (
          parsed.pathname === "/studio/tasks/payments-api/task-123/logs" &&
          parsed.searchParams.get("to") === "2026-04-08T10:01:00.000Z"
        );
      }),
    ).toBeTruthy();

    fetchMock.mockClear();
    await act(async () => {
      vi.setSystemTime(new Date("2026-04-08T10:03:00Z"));
    });
    fireEvent.click(screen.getByRole("button", { name: "Reload Logs" }));
    await flushRender();

    expect(
      fetchMock.mock.calls.find(([input]) => {
        const parsed = new URL(String(input), "http://studio.test");
        return (
          parsed.pathname === "/studio/tasks/payments-api/task-123/logs" &&
          parsed.searchParams.get("to") === "2026-04-08T10:03:00.000Z"
        )
      }),
    ).toBeTruthy();
  });

  it("shows a broker inspection CTA in task detail when indexed DLQ data is empty", async () => {
    const baseImpl = fetchMock.getMockImplementation();
    fetchMock.mockImplementation(async (input, init) => {
      const url = String(input);
      const method = init?.method || "GET";
      if (url === "/studio/tasks/payments-api/task-empty-dlq?join=all" && method === "GET") {
        return jsonResponse(taskDetailResponse({ taskId: "task-empty-dlq", dlqItems: [] }));
      }
      if (url === "/studio/tasks/payments-api/task-empty-dlq/events?limit=50" && method === "GET") {
        return jsonResponse({ count: 0, items: [], next_cursor: null });
      }
      return await baseImpl!(input, init);
    });
    window.history.replaceState({}, "", "/tasks/payments-api/task-empty-dlq");

    render(<App />);

    expect(await screen.findByText("Task Detail")).toBeInTheDocument();
    expect(await screen.findByRole("link", { name: "Inspect live broker DLQ messages" })).toHaveAttribute(
      "href",
      "/services/payments-api/dlq?mode=broker&task_id=task-empty-dlq",
    );
  });
});

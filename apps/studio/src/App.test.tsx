import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { App } from "./App";

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
      task_id_label: "task_id",
      correlation_id_label: "correlation_id",
      level_label: "level",
      task_match_mode: "label",
      task_match_template: null,
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

describe("App", () => {
  beforeEach(() => {
    fetchMock.mockReset();
    MockEventSource.instances = [];
    vi.stubGlobal("fetch", fetchMock);
    vi.stubGlobal("EventSource", MockEventSource as unknown as typeof EventSource);
    window.history.replaceState({}, "", "/");
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
      if (url === "/studio/tasks/search?task_id=task-123&limit=50" && method === "GET") {
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

  it("redirects the default route to /services and renders the registry screen", async () => {
    render(<App />);

    expect(await screen.findByText("Registered Services")).toBeInTheDocument();
    await waitFor(() => expect(window.location.pathname).toBe("/services"));
    expect(screen.getAllByText("Payments API").length).toBeGreaterThan(0);
  });

  it("polls the registered services list silently and stops after unmount", async () => {
    vi.useFakeTimers();

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

    expect(screen.getByText("Registered Services")).toBeInTheDocument();
    await flushServicesRender();

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
    fireEvent.click(screen.getByRole("button", { name: "Register Service" }));

    await screen.findByText("Registered service 'orders-api'.");
    expect(observedPayload).not.toBeNull();
    if (!observedPayload) {
      throw new Error("Expected the service create payload to be captured.");
    }
    const capturedPayload = observedPayload as {
      log_config?: { source_label?: string | null; service_selector_labels?: Record<string, string> } | null;
    };
    expect(capturedPayload.log_config?.source_label).toBe("app");
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

    fireEvent.click(await screen.findByRole("link", { name: "View" }));

    expect(await screen.findByText("Service Detail")).toBeInTheDocument();
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

    expect(await screen.findByText("Service Detail")).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Service log source"), { target: { value: "runtime-worker" } });
    fireEvent.click(screen.getByRole("button", { name: "Reload Logs" }));

    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith("/studio/services/payments-api/logs?limit=20&source=runtime-worker", undefined),
    );
  });

  it("uses the manual service log window override when provided", async () => {
    window.history.replaceState({}, "", "/services/payments-api");

    render(<App />);

    expect(await screen.findByText("Service Detail")).toBeInTheDocument();
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

    expect(await screen.findByText("Service Detail")).toBeInTheDocument();
    expect(await screen.findByText("Loading service logs...")).toBeInTheDocument();

    await act(async () => {
      resolveServiceLogs?.(jsonResponse({ count: 0, items: [], next_cursor: null }));
      await Promise.resolve();
    });

    expect(await screen.findByText("No service logs matched the current filters.")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Reload Logs" }));

    expect(await screen.findByText("Loki query failed.")).toBeInTheDocument();
  });

  it("uses the manual service activity window override when provided", async () => {
    window.history.replaceState({}, "", "/services/payments-api");

    render(<App />);

    expect(await screen.findByText("Service Detail")).toBeInTheDocument();
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

    expect(await screen.findByText("Service Detail")).toBeInTheDocument();
    const discoveredOptions = Array.from(
      document.querySelectorAll("#service-log-sources-payments-api option"),
    ).map((item) => item.getAttribute("value"));
    expect(discoveredOptions).toContain("api");
    expect(discoveredOptions).toContain("runtime-worker");
  });

  it("refreshes a registered service from the detail page", async () => {
    window.history.replaceState({}, "", "/services/payments-api");

    render(<App />);

    expect(await screen.findByText("Service Detail")).toBeInTheDocument();
    expect(screen.getAllByText("unknown").length).toBeGreaterThan(0);

    fireEvent.click(screen.getByRole("button", { name: "Refresh" }));

    expect(await screen.findByText("Refreshed 'payments-api'.")).toBeInTheDocument();
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith("/studio/services/payments-api/refresh", { method: "POST" }));
    expect(screen.getAllByText("healthy").length).toBeGreaterThan(0);
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

    expect(await screen.findByText("Service Detail")).toBeInTheDocument();
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

    expect(await screen.findByText("Service Detail")).toBeInTheDocument();
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

    expect(screen.getByText("Service Detail")).toBeInTheDocument();
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

  it("submits task search and renders indexed task results", async () => {
    window.history.replaceState({}, "", "/tasks/search");

    render(<App />);

    fireEvent.change(await screen.findByPlaceholderText("task_id"), { target: { value: "task-123" } });
    fireEvent.click(screen.getByRole("button", { name: "Search" }));

    expect(await screen.findByText(/Matches:/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Open Task Detail" })).toBeInTheDocument();
    expect(screen.getByText(/correlation=corr-123/)).toBeInTheDocument();
  });

  it("renders the direct task detail route with graph, timeline, logs, joins, and SSE cleanup", async () => {
    window.history.replaceState({}, "", "/tasks/payments-api/task-123");

    const { unmount } = render(<App />);

    expect(await screen.findByText("Task Detail")).toBeInTheDocument();
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

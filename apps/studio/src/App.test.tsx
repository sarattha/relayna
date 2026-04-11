import { fireEvent, render, screen, waitFor } from "@testing-library/react";
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

function serviceListResponse(services: MockServiceRecord[]) {
  return jsonResponse({ count: services.length, services });
}

const services: MockServiceRecord[] = [
  {
    service_id: "payments-api",
    name: "Payments API",
    base_url: "https://payments.example.test",
    environment: "prod",
    tags: ["core", "money"],
    auth_mode: "internal_network",
    status: "registered",
    capabilities: { supported_routes: ["status", "workflow"] },
    last_seen_at: "2026-04-08T12:00:00Z",
    log_config: {
      provider: "loki",
      base_url: "https://loki.example.test",
      service_selector_labels: { app: "payments-api" },
      task_id_label: "task_id",
      correlation_id_label: "correlation_id",
      level_label: "level",
    },
  },
];

function taskDetailResponse() {
  return {
    service: services[0],
    service_id: "payments-api",
    task_id: "task-123",
    task_ref: {
      service_id: "payments-api",
      task_id: "task-123",
      correlation_id: "corr-123",
      parent_refs: [{ service_id: "upstream-api", task_id: "parent-1" }],
      child_refs: [{ service_id: "payments-api", task_id: "child-1" }],
    },
    latest_status: {
      service_id: "payments-api",
      task_id: "task-123",
      task_ref: {
        service_id: "payments-api",
        task_id: "task-123",
        correlation_id: "corr-123",
        parent_refs: [],
        child_refs: [],
      },
      event: { status: "running" },
    },
    history: {
      service_id: "payments-api",
      task_id: "task-123",
      count: 2,
      events: [{ task_id: "task-123", status: "queued" }, { task_id: "task-123", status: "running" }],
    },
    dlq_messages: {
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
        },
      ],
      next_cursor: null,
    },
    execution_graph: {
      service_id: "payments-api",
      task_id: "task-123",
      task_ref: {
        service_id: "payments-api",
        task_id: "task-123",
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
        { id: "task", kind: "task", label: "task-123", task_id: "task-123" },
        { id: "attempt", kind: "task_attempt", label: "attempt-1", task_id: "task-123" },
      ],
      edges: [{ source: "task", target: "attempt", kind: "stage_transitioned_to" }],
      annotations: {},
      related_task_ids: ["task-123", "child-1"],
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

    fetchMock.mockImplementation(async (input, init) => {
      const url = String(input);
      const method = init?.method || "GET";

      if (url === "/studio/services" && method === "GET") {
        return serviceListResponse(services);
      }
      if (url === "/studio/services/payments-api/events?limit=20" && method === "GET") {
        return jsonResponse({ count: 0, items: [], next_cursor: null });
      }
      if (url === "/studio/services/payments-api/logs?limit=20" && method === "GET") {
        return jsonResponse({
          count: 1,
          items: [
            {
              service_id: "payments-api",
              timestamp: "2026-04-08T10:00:00Z",
              level: "info",
              message: "service log line",
              fields: {},
            },
          ],
          next_cursor: null,
        });
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
      if (url === "/studio/tasks/search?task_id=task-123&join=all" && method === "GET") {
        return jsonResponse({
          count: 1,
          items: [
            {
              service_id: "payments-api",
              task_id: "task-123",
              task_ref: {
                service_id: "payments-api",
                task_id: "task-123",
                correlation_id: "corr-123",
                parent_refs: [],
                child_refs: [],
              },
              service_name: "Payments API",
              environment: "prod",
              latest_status: { event: { status: "running" } },
              detail_path: "/studio/tasks/payments-api/task-123",
            },
          ],
          joined_count: 1,
          joined_items: [
            {
              service_id: "fraud-api",
              task_id: "fraud-999",
              task_ref: {
                service_id: "fraud-api",
                task_id: "fraud-999",
                correlation_id: "corr-123",
                parent_refs: [],
                child_refs: [],
              },
              service_name: "Fraud API",
              environment: "prod",
              latest_status: { event: { status: "running" } },
              detail_path: "/studio/tasks/fraud-api/fraud-999",
              join_kind: "correlation_id",
              matched_value: "corr-123",
            },
          ],
          join_warnings: [],
          errors: [],
          scanned_services: ["payments-api", "fraud-api"],
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
      if (url === "/studio/tasks/payments-api/task-123/logs?limit=50&correlation_id=corr-123" && method === "GET") {
        return jsonResponse({
          count: 1,
          items: [
            {
              service_id: "payments-api",
              task_id: "task-123",
              correlation_id: "corr-123",
              timestamp: "2026-04-08T10:00:00Z",
              level: "info",
              message: "task log line",
              fields: {},
            },
          ],
          next_cursor: null,
        });
      }
      throw new Error(`Unhandled fetch: ${method} ${url}`);
    });
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("redirects the default route to /services and renders the registry screen", async () => {
    render(<App />);

    expect(await screen.findByText("Registered Services")).toBeInTheDocument();
    await waitFor(() => expect(window.location.pathname).toBe("/services"));
    expect(screen.getAllByText("Payments API").length).toBeGreaterThan(0);
  });

  it("navigates from the service list to the routed service detail page", async () => {
    render(<App />);

    fireEvent.click(await screen.findByRole("link", { name: "View" }));

    expect(await screen.findByText("Service Detail")).toBeInTheDocument();
    await waitFor(() => expect(window.location.pathname).toBe("/services/payments-api"));
    expect(screen.getByText("Recent Activity")).toBeInTheDocument();
    expect(screen.getByText("Service Logs")).toBeInTheDocument();
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

  it("submits task search and renders exact and joined task results", async () => {
    window.history.replaceState({}, "", "/tasks/search");

    render(<App />);

    fireEvent.change(await screen.findByPlaceholderText("task-123"), { target: { value: "task-123" } });
    fireEvent.click(screen.getByRole("button", { name: "Search" }));

    expect(await screen.findByText("Exact Matches")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "payments-api/task-123" })).toBeInTheDocument();
    expect(screen.getByText("Fraud API via correlation id")).toBeInTheDocument();
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
});

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
};

const fetchMock = vi.fn<typeof fetch>();

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function serviceListResponse(services: MockServiceRecord[]) {
  return jsonResponse({ count: services.length, services });
}

describe("App", () => {
  beforeEach(() => {
    fetchMock.mockReset();
    vi.stubGlobal("fetch", fetchMock);
    window.history.replaceState({}, "", "/");
    fetchMock.mockImplementation(async (input, init) => {
      const url = String(input);
      const method = init?.method || "GET";
      if (url === "/studio/services" && method === "GET") {
        return serviceListResponse([]);
      }
      throw new Error(`Unhandled fetch: ${method} ${url}`);
    });
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("loads registry data and renders the selected service detail panel", async () => {
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
      },
      {
        service_id: "billing-api",
        name: "Billing API",
        base_url: "https://billing.example.test",
        environment: "prod",
        tags: ["finance"],
        auth_mode: "internal_network",
        status: "disabled",
        capabilities: null,
        last_seen_at: null,
      },
    ];

    fetchMock.mockImplementation(async (input, init) => {
      const url = String(input);
      const method = init?.method || "GET";
      if (url === "/studio/services" && method === "GET") {
        return serviceListResponse(services);
      }
      throw new Error(`Unhandled fetch: ${method} ${url}`);
    });

    render(<App />);

    expect((await screen.findAllByText("Payments API")).length).toBeGreaterThanOrEqual(2);
    expect(screen.getAllByText("payments-api").length).toBeGreaterThanOrEqual(2);
    expect(screen.getAllByText("prod").length).toBeGreaterThanOrEqual(3);
    expect(screen.getAllByText("registered").length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText("Billing API").length).toBeGreaterThanOrEqual(1);
    expect(screen.getByDisplayValue(/supported_routes/)).toBeInTheDocument();
    expect(screen.getAllByText(/2026/).length).toBeGreaterThanOrEqual(1);
  });

  it("creates, edits, and updates service status through the registry UI", async () => {
    let services: MockServiceRecord[] = [];

    fetchMock.mockImplementation(async (input, init) => {
      const url = String(input);
      const method = init?.method || "GET";
      if (url === "/studio/services" && method === "GET") {
        return serviceListResponse(services);
      }
      if (url === "/studio/services" && method === "POST") {
        const payload = JSON.parse(String(init?.body)) as Omit<MockServiceRecord, "status">;
        const created: MockServiceRecord = { ...payload, status: "registered", capabilities: null, last_seen_at: null };
        services = [created];
        return jsonResponse(created, 201);
      }
      if (url === "/studio/services/payments-api" && method === "PATCH") {
        const payload = JSON.parse(String(init?.body)) as Partial<MockServiceRecord>;
        services = services.map((service) =>
          service.service_id === "payments-api" ? { ...service, ...payload, service_id: service.service_id } : service,
        );
        return jsonResponse(services[0]);
      }
      throw new Error(`Unhandled fetch: ${method} ${url}`);
    });

    render(<App />);

    fireEvent.change(screen.getByLabelText("Service id"), { target: { value: "payments-api" } });
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Payments API" } });
    fireEvent.change(screen.getByLabelText("Base URL"), { target: { value: "https://payments.example.test/" } });
    fireEvent.change(screen.getByLabelText("Environment"), { target: { value: "prod" } });
    fireEvent.change(screen.getByLabelText("Tags"), { target: { value: "core, money" } });
    fireEvent.click(screen.getByRole("button", { name: "Register Service" }));

    expect(await screen.findByText("Registered service 'payments-api'.")).toBeInTheDocument();
    expect((await screen.findAllByText("Payments API")).length).toBeGreaterThanOrEqual(2);

    fireEvent.click(screen.getByRole("button", { name: "Edit" }));
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Payments Control API" } });
    fireEvent.click(screen.getByRole("button", { name: "Save Service" }));

    expect(await screen.findByText("Updated service 'payments-api'.")).toBeInTheDocument();
    expect(screen.getAllByText("Payments Control API").length).toBeGreaterThanOrEqual(2);

    fireEvent.click(screen.getByRole("button", { name: "Mark Unavailable" }));

    expect(await screen.findByText("Marked 'payments-api' as unavailable.")).toBeInTheDocument();
    expect(screen.getAllByText("unavailable").length).toBeGreaterThanOrEqual(2);
  });

  it("refreshes a service and renders the returned capability document", async () => {
    let services: MockServiceRecord[] = [
      {
        service_id: "payments-api",
        name: "Payments API",
        base_url: "https://payments.example.test",
        environment: "prod",
        tags: ["core"],
        auth_mode: "internal_network",
        status: "registered",
        capabilities: null,
        last_seen_at: null,
      },
    ];

    fetchMock.mockImplementation(async (input, init) => {
      const url = String(input);
      const method = init?.method || "GET";
      if (url === "/studio/services" && method === "GET") {
        return serviceListResponse(services);
      }
      if (url === "/studio/services/payments-api/refresh" && method === "POST") {
        services = [
          {
            ...services[0],
            status: "healthy",
            capabilities: {
              relayna_version: "1.3.4",
              topology_kind: "shared_tasks_shared_status",
              alias_config_summary: {
                aliasing_enabled: false,
                payload_aliases: {},
                http_aliases: {},
              },
              supported_routes: ["status.latest"],
              feature_flags: [],
              service_metadata: {
                service_title: "Payments API",
                capability_path: "/relayna/capabilities",
                discovery_source: "fallback",
                compatibility: "legacy_no_capabilities_endpoint",
              },
            },
            last_seen_at: "2026-04-09T10:00:00Z",
          },
        ];
        return jsonResponse(services[0]);
      }
      throw new Error(`Unhandled fetch: ${method} ${url}`);
    });

    render(<App />);

    fireEvent.click(await screen.findByRole("button", { name: "Refresh Capabilities" }));

    expect(await screen.findByText("Refreshed 'payments-api'.")).toBeInTheDocument();
    expect(screen.getAllByText("healthy").length).toBeGreaterThanOrEqual(2);
    expect(screen.getByDisplayValue(/legacy_no_capabilities_endpoint/)).toBeInTheDocument();
  });

  it("surfaces refresh failures from the registry backend", async () => {
    const services: MockServiceRecord[] = [
      {
        service_id: "payments-api",
        name: "Payments API",
        base_url: "https://payments.example.test",
        environment: "prod",
        tags: ["core"],
        auth_mode: "internal_network",
        status: "registered",
        capabilities: null,
        last_seen_at: null,
      },
    ];

    fetchMock.mockImplementation(async (input, init) => {
      const url = String(input);
      const method = init?.method || "GET";
      if (url === "/studio/services" && method === "GET") {
        return serviceListResponse(services);
      }
      if (url === "/studio/services/payments-api/refresh" && method === "POST") {
        return jsonResponse({ detail: "Capability endpoint returned invalid JSON." }, 502);
      }
      throw new Error(`Unhandled fetch: ${method} ${url}`);
    });

    render(<App />);

    fireEvent.click(await screen.findByRole("button", { name: "Refresh Capabilities" }));

    expect(await screen.findByText("Capability endpoint returned invalid JSON.")).toBeInTheDocument();
  });

  it("shows duplicate registration errors from the registry backend", async () => {
    const services: MockServiceRecord[] = [
      {
        service_id: "billing-api",
        name: "Billing API",
        base_url: "https://billing.example.test",
        environment: "prod",
        tags: [],
        auth_mode: "internal_network",
        status: "registered",
        capabilities: null,
        last_seen_at: null,
      },
    ];

    fetchMock.mockImplementation(async (input, init) => {
      const url = String(input);
      const method = init?.method || "GET";
      if (url === "/studio/services" && method === "GET") {
        return serviceListResponse(services);
      }
      if (url === "/studio/services" && method === "POST") {
        return jsonResponse(
          {
            detail:
              "A service is already registered for environment 'prod' and base_url 'https://billing.example.test'.",
          },
          409,
        );
      }
      throw new Error(`Unhandled fetch: ${method} ${url}`);
    });

    render(<App />);

    fireEvent.click(screen.getByRole("button", { name: "New Service" }));
    fireEvent.change(screen.getByLabelText("Service id"), { target: { value: "billing-copy" } });
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Billing API Copy" } });
    fireEvent.change(screen.getByLabelText("Base URL"), { target: { value: "https://billing.example.test" } });
    fireEvent.change(screen.getByLabelText("Environment"), { target: { value: "prod" } });
    fireEvent.click(screen.getByRole("button", { name: "Register Service" }));

    expect(
      await screen.findByText(
        "A service is already registered for environment 'prod' and base_url 'https://billing.example.test'.",
      ),
    ).toBeInTheDocument();
  });

  it("loads and renders an execution graph after form submission", async () => {
    const services: MockServiceRecord[] = [
      {
        service_id: "payments-api",
        name: "Payments API",
        base_url: "https://payments.example.test",
        environment: "prod",
        tags: ["core"],
        auth_mode: "internal_network",
        status: "healthy",
        capabilities: { supported_routes: ["status.latest", "execution.graph"] },
        last_seen_at: "2026-04-09T10:00:00Z",
      },
    ];

    fetchMock.mockImplementation(async (input, init) => {
      const url = String(input);
      const method = init?.method || "GET";
      if (url === "/studio/services" && method === "GET") {
        return serviceListResponse(services);
      }
      if (url === "/studio/tasks/payments-api/task-123?join=all" && method === "GET") {
        return jsonResponse({
          service: services[0],
          service_id: "payments-api",
          task_id: "task-123",
          task_ref: {
            service_id: "payments-api",
            task_id: "task-123",
            correlation_id: "corr-123",
            parent_refs: [{ service_id: "payments-api", task_id: "parent-1" }],
            child_refs: [{ service_id: "payments-api", task_id: "child-1" }],
          },
          latest_status: {
            service_id: "payments-api",
            task_id: "task-123",
            task_ref: {
              service_id: "payments-api",
              task_id: "task-123",
              correlation_id: "corr-123",
              parent_refs: [{ service_id: "payments-api", task_id: "parent-1" }],
              child_refs: [],
            },
            event: { status: "completed" },
          },
          history: {
            service_id: "payments-api",
            task_id: "task-123",
            task_ref: {
              service_id: "payments-api",
              task_id: "task-123",
              correlation_id: "corr-123",
              parent_refs: [{ service_id: "payments-api", task_id: "parent-1" }],
              child_refs: [{ service_id: "payments-api", task_id: "child-1" }],
            },
            count: 1,
            events: [{ task_id: "task-123", status: "completed" }],
          },
          dlq_messages: {
            service_id: "payments-api",
            items: [],
            next_cursor: null,
          },
          execution_graph: {
            service_id: "payments-api",
            task_id: "task-123",
            task_ref: {
              service_id: "payments-api",
              task_id: "task-123",
              correlation_id: "corr-123",
              parent_refs: [{ service_id: "payments-api", task_id: "parent-1" }],
              child_refs: [{ service_id: "payments-api", task_id: "child-1" }],
            },
            topology_kind: "shared_tasks_shared_status",
            summary: {
              status: "completed",
              duration_ms: 1250,
              graph_completeness: "full",
            },
            nodes: [
              { id: "task:task-123", kind: "task", label: "task-123" },
              { id: "attempt:1", kind: "task_attempt", label: "task-123 attempt 1" },
              { id: "status:1", kind: "status_event", label: "completed" },
            ],
            edges: [
              { source: "task:task-123", target: "attempt:1", kind: "received_by" },
              { source: "attempt:1", target: "status:1", kind: "published_status" },
            ],
            annotations: {},
            related_task_ids: [],
          },
          joined_refs: [
            {
              task_ref: {
                service_id: "billing-api",
                task_id: "corr-123",
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
              code: "ambiguous_join_candidate",
              detail: "Skipped parent task id join for 'parent-1' because it matched multiple services.",
              join_kind: "parent_task_id",
              matched_value: "parent-1",
            },
          ],
          errors: [],
        });
      }
      throw new Error(`Unhandled fetch: ${method} ${url}`);
    });

    render(<App />);

    expect((await screen.findAllByText("Payments API")).length).toBeGreaterThanOrEqual(1);
    fireEvent.change(screen.getByLabelText("Task id"), {
      target: { value: "task-123" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Load Execution Graph" }));

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith("/studio/tasks/payments-api/task-123?join=all", undefined);
    });

    expect((await screen.findAllByText("completed")).length).toBeGreaterThanOrEqual(2);
    expect(screen.getByDisplayValue(/flowchart LR/)).toBeInTheDocument();
    expect(screen.getByDisplayValue(/published_status/)).toBeInTheDocument();
    expect(screen.getAllByText("shared_tasks_shared_status").length).toBeGreaterThanOrEqual(2);
    expect(screen.getByText("full graph")).toBeInTheDocument();
    expect(screen.getByText("task-123 attempt 1")).toBeInTheDocument();
    expect(screen.getAllByText("corr-123").length).toBeGreaterThanOrEqual(2);
    expect(screen.getByText("payments-api/parent-1")).toBeInTheDocument();
    expect(screen.getByText("payments-api/child-1")).toBeInTheDocument();
    expect(screen.getByText("billing-api/corr-123 via correlation id")).toBeInTheDocument();
    expect(screen.getByText("Skipped parent task id join for 'parent-1' because it matched multiple services.")).toBeInTheDocument();
    expect(window.location.search).toContain("service_id=payments-api");
    expect(window.location.search).toContain("task_id=task-123");
  });

  it("renders section-level task detail errors without dropping successful task data", async () => {
    const services: MockServiceRecord[] = [
      {
        service_id: "payments-api",
        name: "Payments API",
        base_url: "https://payments.example.test",
        environment: "prod",
        tags: ["core"],
        auth_mode: "internal_network",
        status: "healthy",
        capabilities: { supported_routes: ["status.latest", "status.history"] },
        last_seen_at: "2026-04-09T10:00:00Z",
      },
    ];

    fetchMock.mockImplementation(async (input, init) => {
      const url = String(input);
      const method = init?.method || "GET";
      if (url === "/studio/services" && method === "GET") {
        return serviceListResponse(services);
      }
      if (url === "/studio/tasks/payments-api/task-123?join=all" && method === "GET") {
        return jsonResponse({
          service: services[0],
          service_id: "payments-api",
          task_id: "task-123",
          task_ref: {
            service_id: "payments-api",
            task_id: "task-123",
            correlation_id: "corr-123",
            parent_refs: [],
            child_refs: [],
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
            event: { status: "completed" },
          },
          history: {
            service_id: "payments-api",
            task_id: "task-123",
            task_ref: {
              service_id: "payments-api",
              task_id: "task-123",
              correlation_id: "corr-123",
              parent_refs: [],
              child_refs: [],
            },
            count: 2,
            events: [
              { task_id: "task-123", status: "completed" },
              { task_id: "task-123", status: "processing" },
            ],
          },
          dlq_messages: {
            service_id: "payments-api",
            items: [{ dlq_id: "dlq-1" }],
            next_cursor: null,
          },
          execution_graph: null,
          joined_refs: [],
          join_warnings: [],
          errors: [
            {
              detail: "No execution graph found for task_id 'task-123'.",
              code: "upstream_not_found",
              service_id: "payments-api",
              upstream_status: 404,
              retryable: false,
            },
          ],
        });
      }
      throw new Error(`Unhandled fetch: ${method} ${url}`);
    });

    render(<App />);

    expect((await screen.findAllByText("Payments API")).length).toBeGreaterThanOrEqual(1);
    fireEvent.change(screen.getByLabelText("Task id"), {
      target: { value: "task-123" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Load Execution Graph" }));

    expect(await screen.findByText("Execution Graph Unavailable")).toBeInTheDocument();
    expect(screen.getByText("History events")).toBeInTheDocument();
    expect(screen.getByText("DLQ messages")).toBeInTheDocument();
    expect(screen.getByText("upstream_not_found")).toBeInTheDocument();
    expect(screen.getByText("No execution graph found for task_id 'task-123'.")).toBeInTheDocument();
    expect(screen.getAllByText("Payments API (payments-api)").length).toBeGreaterThanOrEqual(1);
  });
});

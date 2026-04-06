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

const fetchMock = vi.fn<typeof fetch>();

describe("App", () => {
  beforeEach(() => {
    fetchMock.mockReset();
    vi.stubGlobal("fetch", fetchMock);
    window.history.replaceState({}, "", "/");
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("loads and renders an execution graph after form submission", async () => {
    fetchMock.mockResolvedValue(
      new Response(
        JSON.stringify({
          task_id: "task-123",
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
        }),
        {
          status: 200,
          headers: { "Content-Type": "application/json" },
        },
      ),
    );

    render(<App />);

    fireEvent.change(screen.getByLabelText("API base URL"), {
      target: { value: "http://api.example.test" },
    });
    fireEvent.change(screen.getByLabelText("Task id"), {
      target: { value: "task-123" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Load Execution Graph" }));

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith("http://api.example.test/executions/task-123/graph");
    });

    expect((await screen.findAllByText("completed")).length).toBeGreaterThanOrEqual(2);
    expect(screen.getByText("1.25 s")).toBeInTheDocument();
    expect(screen.getByDisplayValue(/flowchart LR/)).toBeInTheDocument();
    expect(screen.getByDisplayValue(/published_status/)).toBeInTheDocument();
    expect(screen.getAllByText("shared_tasks_shared_status").length).toBeGreaterThanOrEqual(2);
    expect(screen.getByText("full graph")).toBeInTheDocument();
    expect(screen.getByText("task-123 attempt 1")).toBeInTheDocument();
  });

  it("auto-loads from query params and normalizes aliased task ids", async () => {
    window.history.replaceState({}, "", "/?task_id=attempt-456&base_url=http://api.example.test");
    fetchMock.mockResolvedValue(
      new Response(
        JSON.stringify({
          attempt_id: "attempt-456",
          topology_kind: "shared_status_workflow",
          summary: {
            status: "planning",
            duration_ms: 900,
            graph_completeness: "partial",
          },
          nodes: [
            { id: "task:attempt-456", kind: "task", label: "attempt-456" },
            { id: "msg:1", kind: "workflow_message", label: "planner" },
          ],
          edges: [{ source: "task:attempt-456", target: "msg:1", kind: "received_by" }],
          annotations: {},
          related_task_ids: ["child-1"],
        }),
        {
          status: 200,
          headers: { "Content-Type": "application/json" },
        },
      ),
    );

    render(<App />);

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith("http://api.example.test/executions/attempt-456/graph");
    });

    expect(await screen.findByText("planning")).toBeInTheDocument();
    expect(screen.getByDisplayValue("attempt-456")).toBeInTheDocument();
    expect(screen.getAllByText("shared_status_workflow").length).toBeGreaterThanOrEqual(2);
    expect(screen.getByText("partial graph")).toBeInTheDocument();
    expect(screen.getByText("child-1")).toBeInTheDocument();
    expect(screen.getByText("planner")).toBeInTheDocument();
  });
});

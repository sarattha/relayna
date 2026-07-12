import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import {
  ConfirmationDialog,
  EmptyState,
  HealthBadge,
  LogMessage,
  MetadataRow,
  MetricCard,
  NoticeBanner,
  SectionCard,
  StatusBadge,
  StudioIcon,
  buildMermaid,
  formatDuration,
  formatEventSummary,
  formatJoinKind,
  formatLogLevel,
  formatLogSource,
  formatTaskPointerList,
  formatTimestamp,
  mergeControlPlaneEvent,
  parseLimit,
  sortControlPlaneEvents,
  supportsCapability,
} from "./ui";
import type { ExecutionGraph, StudioControlPlaneEvent } from "./types";

function event(overrides: Partial<StudioControlPlaneEvent> = {}): StudioControlPlaneEvent {
  return {
    service_id: "payments-api", ingest_method: "pull", ingested_at: "2026-01-01T00:00:00Z",
    dedupe_key: "one", out_of_order: false, task_id: "task-1", event_type: "task.running",
    source_kind: "status", timestamp: null, payload: {}, ...overrides,
  };
}

describe("shared UI helpers", () => {
  it("renders every icon and badge palette", () => {
    const names = [
      "add", "back", "clear", "copy", "delete", "disable", "dlq", "edit", "enable", "filter", "health",
      "next", "open", "refresh", "save", "search", "services", "tasks", "topology", "unavailable",
    ] as const;
    const { container } = render(<div>{names.map((name) => <StudioIcon key={name} name={name} />)}</div>);
    expect(container.querySelectorAll("svg")).toHaveLength(names.length);
    render(<div>
      {(["registered", "healthy", "unavailable", "disabled"] as const).map((status) => <StatusBadge key={status} status={status} />)}
      {(["healthy", "degraded", "stale", "unreachable", "disabled", "unknown"] as const).map((status) => <HealthBadge key={status} status={status} />)}
    </div>);
    expect(screen.getAllByText("healthy")).toHaveLength(2);
  });

  it("covers limits, timestamps, labels, events, and capability checks", () => {
    expect(parseLimit("invalid", 50)).toBe(50);
    expect(parseLimit("0", 50)).toBe(1);
    expect(parseLimit("500", 50)).toBe(200);
    expect(parseLimit("25", 50)).toBe(25);
    expect(formatTimestamp()).toBe("Never");
    expect(formatTimestamp("2026-01-01T00:00:00Z")).not.toBe("Never");
    expect(formatLogLevel()).toBe("unlabeled");
    expect(formatLogLevel("error")).toBe("error");
    expect(formatLogSource()).toBe("unknown");
    expect(formatLogSource("worker")).toBe("worker");
    expect(formatDuration(null)).toBe("n/a");
    expect(formatDuration(500)).toBe("500 ms");
    expect(formatDuration(1500)).toBe("1.50 s");
    expect(formatDuration(12_000)).toBe("12 s");
    expect(formatTaskPointerList([])).toBe("none");
    expect(formatTaskPointerList([{ service_id: "s", task_id: "t" }])).toBe("s/t");
    expect(formatJoinKind(null)).toBe("join");
    expect(formatJoinKind("parent_task_id")).toBe("parent task id");
    expect(supportsCapability(null, "route")).toBe(false);
    expect(supportsCapability({ supported_routes: "route" }, "route")).toBe(false);
    expect(supportsCapability({ supported_routes: ["route"] }, "route")).toBe(true);

    const old = event({ dedupe_key: "old", timestamp: "2026-01-01T00:00:00Z" });
    const newer = event({ dedupe_key: "new", timestamp: "2026-01-02T00:00:00Z" });
    expect(sortControlPlaneEvents([old, newer]).map((item) => item.dedupe_key)).toEqual(["new", "old"]);
    expect(mergeControlPlaneEvent([old, newer], { ...newer }).map((item) => item.dedupe_key)).toEqual(["new", "old"]);
    expect(formatEventSummary(event({ payload: { status: "completed" } }))).toBe("completed");
    expect(formatEventSummary(event({ source_kind: "observation", payload: { status: "completed" } }))).toBe("task.running");
  });

  it("builds Mermaid with safe unique ids and skips dangling edges", () => {
    const graph: ExecutionGraph = {
      topology_kind: "shared_tasks_shared_status",
      task_id: "task-1",
      related_task_ids: [],
      nodes: [
        { id: "node!", kind: "task", label: 'Quoted "task"' },
        { id: "node?", kind: "stage", label: null },
        { id: "!!!", kind: "event", label: "" },
      ],
      edges: [
        { source: "node!", target: "node?", kind: 'routes "to"' },
        { source: "missing", target: "node?", kind: "dangling" },
      ],
      annotations: {},
      summary: { graph_completeness: "complete" },
    };
    const mermaid = buildMermaid(graph);
    expect(mermaid).toContain("node_node_");
    expect(mermaid).toContain("node_node_2");
    expect(mermaid).toContain("node_item");
    expect(mermaid).toContain('Quoted \\"task\\"');
    expect(mermaid).not.toContain("dangling");
  });

  it("renders structured, ANSI, primitive, empty, and malformed log messages", () => {
    const { container, rerender } = render(<LogMessage message={'{"event":"failed","count":1}'} />);
    expect(screen.getByText(/"event": "failed"/)).toBeInTheDocument();
    rerender(<LogMessage message="true" />);
    expect(screen.getByText("true")).toBeInTheDocument();
    rerender(<LogMessage message="" />);
    expect(container.textContent).toBe("");
    rerender(<LogMessage message={'{"broken":'} />);
    expect(screen.getByText('{"broken":')).toBeInTheDocument();
    rerender(<LogMessage message={'\u001b[1;31mbold red\u001b[22;39m plain\u001b[0m\u001b[999m'} />);
    expect(screen.getByText("bold red")).toHaveStyle({ fontWeight: "700" });
    expect(screen.getByText("plain")).toBeInTheDocument();
  });

  it("covers confirmation focus, challenge, pending, keyboard, and visual defaults", () => {
    const cancel = vi.fn();
    const confirm = vi.fn();
    const { rerender } = render(
      <ConfirmationDialog title="Confirm" body={<p>Body</p>} confirmLabel="Proceed" onCancel={cancel} onConfirm={confirm} />,
    );
    fireEvent.keyDown(screen.getByRole("dialog"), { key: "Escape" });
    expect(cancel).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByRole("button", { name: "Proceed" }));
    expect(confirm).toHaveBeenCalledTimes(1);

    rerender(
      <ConfirmationDialog
        title="Delete" body={<p>Danger</p>} confirmLabel="Delete" cancelLabel="Back" challengeText="payments-api"
        tone="danger" onCancel={cancel} onConfirm={confirm}
      />,
    );
    const dialog = screen.getByRole("dialog");
    const challenge = within(dialog).getByLabelText("Type payments-api to confirm");
    expect(within(dialog).getByRole("button", { name: "Delete" })).toBeDisabled();
    fireEvent.change(challenge, { target: { value: "payments-api" } });
    expect(within(dialog).getByRole("button", { name: "Delete" })).toBeEnabled();
    fireEvent.keyDown(dialog, { key: "x" });
    fireEvent.keyDown(dialog, { key: "Tab", shiftKey: true });
    fireEvent.keyDown(dialog, { key: "Tab" });

    rerender(
      <ConfirmationDialog title="Pending" body="Body" confirmLabel="Save" challengeLabel="Exact value" challengeText="x" pending onCancel={cancel} onConfirm={confirm} />,
    );
    expect(screen.getByText("Working...")).toBeDisabled();
    fireEvent.keyDown(screen.getByRole("dialog"), { key: "Escape" });
    fireEvent.keyDown(screen.getByRole("dialog"), { key: "Tab" });
  });

  it("renders optional card and notice branches", () => {
    render(<div>
      <MetricCard label="Count" value="3" className="compact" />
      <NoticeBanner>Info</NoticeBanner>
      <NoticeBanner tone="error">Error</NoticeBanner>
      <MetadataRow label="Service" value="payments" />
      <EmptyState title="Nothing here" body="Create one." />
      <SectionCard title="Basic">Body</SectionCard>
      <SectionCard title="Complete" subtitle="Subtitle" action={<button>Action</button>} className="featured">Body</SectionCard>
    </div>);
    expect(screen.getByText("Nothing here")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Action" })).toBeInTheDocument();
  });
});

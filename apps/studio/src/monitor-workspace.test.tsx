import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { MemoryRouter, useLocation } from "react-router-dom";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import * as api from "./api";
import { MonitorWorkspace, WorkspaceNavigation } from "./monitor-workspace";
import {
  monitorTimeWindow,
  readMonitorPreferences,
  validCustomWindow,
} from "./monitor-state";
import type {
  ServiceRecord,
  StudioControlPlaneEvent,
  StudioTaskDetail,
  StudioTaskSearchItem,
} from "./types";
vi.mock("./api", () => ({
  fetchTaskDetail: vi.fn(),
  fetchTaskEvents: vi.fn(),
  fetchTaskLogs: vi.fn(),
  fetchServiceLogs: vi.fn(),
  fetchTaskMetrics: vi.fn(),
  searchTasks: vi.fn(),
}));
const service: ServiceRecord = {
  service_id: "orders",
  name: "Orders API",
  environment: "production",
  base_url: "https://orders.test",
  status: "healthy",
  tags: [],
  auth_mode: "none",
  log_config: {
    provider: "loki",
    base_url: "https://loki.test",
    service_selector_labels: { app: "orders" },
  },
};
const task: StudioTaskSearchItem = {
  service_id: "orders",
  service_name: "Orders API",
  environment: "production",
  task_id: "order-7842",
  status: "failed",
  stage: "charge-payment",
  first_seen_at: "2026-10-02T07:32:03Z",
  last_seen_at: "2026-10-02T07:32:09Z",
  detail_path: "/tasks/orders/order-7842",
};
const event: StudioControlPlaneEvent = {
  service_id: "orders",
  task_id: task.task_id,
  ingest_method: "push",
  ingested_at: "2026-10-02T07:32:08Z",
  timestamp: "2026-10-02T07:32:08Z",
  dedupe_key: "failure",
  out_of_order: false,
  event_type: "task_failed",
  source_kind: "status",
  payload: { status: "failed", reason: "Payment timeout" },
};
function detail(id = task.task_id): StudioTaskDetail {
  return {
    service,
    service_id: "orders",
    task_id: id,
    task_ref: {
      service_id: "orders",
      task_id: id,
      correlation_id: "corr-7842",
      parent_refs: [],
      child_refs: [],
    },
    latest_status: {
      service_id: "orders",
      task_id: id,
      event: {
        status: "failed",
        stage: "charge-payment",
        timestamp: task.last_seen_at,
      },
    },
    joined_refs: [],
    join_warnings: [],
    errors: [],
  };
}
function logs(message = "Payment timed out") {
  return {
    count: 1,
    items: [
      {
        service_id: "orders",
        task_id: task.task_id,
        timestamp: event.timestamp!,
        level: "ERROR",
        source: "worker",
        message,
        fields: {},
      },
    ],
  };
}
function mount(
  route = "/?monitor_task=order-7842",
  overrides: Partial<ServiceRecord> = {},
) {
  return render(
    <MemoryRouter initialEntries={[route]}>
      <MonitorWorkspace service={{ ...service, ...overrides }} />
    </MemoryRouter>,
  );
}
beforeEach(() => {
  vi.resetAllMocks();
  const stored = new Map<string, string>();
  vi.stubGlobal("localStorage", {
    getItem: (key: string) => stored.get(key) ?? null,
    setItem: (key: string, value: string) => stored.set(key, value),
    clear: () => stored.clear(),
  });
  vi.mocked(api.fetchTaskDetail).mockImplementation(async (_, id) =>
    detail(id),
  );
  vi.mocked(api.fetchTaskEvents).mockResolvedValue({
    count: 1,
    items: [event],
  });
  vi.mocked(api.fetchTaskLogs).mockResolvedValue(logs());
  vi.mocked(api.fetchServiceLogs).mockResolvedValue(logs("Service log"));
  vi.mocked(api.searchTasks).mockResolvedValue({
    count: 1,
    items: [task],
    next_cursor: null,
  });
});
afterEach(() => {
  vi.useRealTimers();
  localStorage.clear();
  vi.unstubAllGlobals();
});

it("retains task, filters and scroll across layouts and focuses logs around an event", async () => {
  mount();
  await screen.findByText("Payment timed out");
  fireEvent.change(screen.getByLabelText("Search monitor logs"), {
    target: { value: "timeout" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Search" }));
  await waitFor(() =>
    expect(api.fetchTaskLogs).toHaveBeenLastCalledWith(
      "orders",
      task.task_id,
      expect.objectContaining({ query: "timeout", limit: 200 }),
    ),
  );
  const reader = screen.getByLabelText("Log entries");
  reader.scrollTop = 80;
  fireEvent.click(screen.getByRole("button", { name: "Logs focus" }));
  expect(screen.getByLabelText("Search monitor logs")).toHaveValue("timeout");
  expect(screen.getByLabelText("Log entries")).toBe(reader);
  expect(reader.scrollTop).toBe(80);
  expect(
    screen.queryByRole("heading", { name: "Tasks" }),
  ).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Investigation" }));
  fireEvent.click(await screen.findByRole("button", { name: /task failed/ }));
  await waitFor(() =>
    expect(api.fetchTaskLogs).toHaveBeenLastCalledWith(
      "orders",
      task.task_id,
      expect.objectContaining({
        query: "timeout",
        from: "2026-10-02T07:31:38.000Z",
        to: "2026-10-02T07:32:38.000Z",
      }),
    ),
  );
  fireEvent.click(screen.getByRole("button", { name: "Reset window" }));
  await waitFor(() =>
    expect(api.fetchTaskLogs).toHaveBeenLastCalledWith(
      "orders",
      task.task_id,
      expect.objectContaining({ from: "2026-10-02T07:30:08.000Z" }),
    ),
  );
});

it("pages service tasks and switches scope without losing selected task", async () => {
  vi.mocked(api.searchTasks).mockImplementation(async (query) => ({
    count: 1,
    items: [{ ...task, task_id: query?.cursor ? "order-next" : task.task_id }],
    next_cursor: query?.cursor ? null : "page-2",
  }));
  mount("/");
  await screen.findByText("Service log");
  fireEvent.click(screen.getByRole("button", { name: "Next" }));
  fireEvent.click(await screen.findByRole("button", { name: /order-next/ }));
  await waitFor(() =>
    expect(api.fetchTaskLogs).toHaveBeenCalledWith(
      "orders",
      "order-next",
      expect.any(Object),
    ),
  );
  fireEvent.change(screen.getByLabelText("Scope"), {
    target: { value: "service" },
  });
  await screen.findByText("Service log");
  fireEvent.change(screen.getByLabelText("Scope"), {
    target: { value: "task" },
  });
  await waitFor(() =>
    expect(api.fetchTaskLogs).toHaveBeenLastCalledWith(
      "orders",
      "order-next",
      expect.any(Object),
    ),
  );
});

it("ignores late evidence belonging to the previous task", async () => {
  let finish!: (value: ReturnType<typeof logs>) => void;
  vi.mocked(api.fetchTaskLogs).mockImplementation((_, id) =>
    id === task.task_id
      ? new Promise((resolve) => {
          finish = resolve;
        })
      : Promise.resolve(logs("New task evidence")),
  );
  vi.mocked(api.searchTasks).mockResolvedValue({
    count: 2,
    items: [task, { ...task, task_id: "order-next" }],
    next_cursor: null,
  });
  mount();
  await waitFor(() => expect(finish).toBeDefined());
  fireEvent.click(screen.getByRole("button", { name: /order-next/ }));
  await screen.findByText("New task evidence");
  await act(async () => finish(logs("Old task evidence")));
  expect(screen.queryByText("Old task evidence")).not.toBeInTheDocument();
});

it("pauses polling, refreshes manually, and retains evidence on refresh failure", async () => {
  mount();
  await screen.findByText("Payment timed out");
  vi.useFakeTimers();
  fireEvent.click(screen.getByRole("button", { name: "Auto-refresh" }));
  const before = vi.mocked(api.fetchTaskLogs).mock.calls.length;
  await act(async () => {
    await vi.advanceTimersByTimeAsync(5000);
  });
  expect(api.fetchTaskLogs).toHaveBeenCalledTimes(before + 1);
  fireEvent.click(screen.getByRole("button", { name: "Pause" }));
  await act(async () => {
    await vi.advanceTimersByTimeAsync(10000);
  });
  expect(api.fetchTaskLogs).toHaveBeenCalledTimes(before + 1);
  vi.useRealTimers();
  vi.mocked(api.fetchTaskLogs).mockRejectedValueOnce(
    new Error("Loki unavailable"),
  );
  fireEvent.click(screen.getByRole("button", { name: "Refresh" }));
  expect(await screen.findByText(/Refresh failed/)).toHaveTextContent(
    "Loki unavailable",
  );
  expect(screen.getByText("Payment timed out")).toBeInTheDocument();
});

it("waits for slow reads before polling again", async () => {
  let finish!: (value: ReturnType<typeof logs>) => void;
  vi.mocked(api.fetchTaskLogs).mockImplementationOnce(
    () =>
      new Promise((resolve) => {
        finish = resolve;
      }),
  );
  mount();
  await waitFor(() => expect(api.fetchTaskLogs).toHaveBeenCalledTimes(1));
  vi.useFakeTimers();
  fireEvent.click(screen.getByRole("button", { name: "Auto-refresh" }));
  await act(async () => {
    await vi.advanceTimersByTimeAsync(10000);
  });
  expect(api.fetchTaskLogs).toHaveBeenCalledTimes(1);
  await act(async () => finish(logs("Slow provider evidence")));
  expect(screen.getByText("Slow provider evidence")).toBeInTheDocument();
  await act(async () => {
    await vi.advanceTimersByTimeAsync(5000);
  });
  expect(api.fetchTaskLogs).toHaveBeenCalledTimes(2);
});

it("validates custom ranges and saves layout, wrap and pane preferences", async () => {
  mount();
  await screen.findByText("Payment timed out");
  fireEvent.change(screen.getByLabelText("Pane width"), {
    target: { value: "35" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Wrap" }));
  fireEvent.change(screen.getByLabelText("Monitor time window"), {
    target: { value: "custom" },
  });
  fireEvent.change(screen.getByLabelText("Monitor from"), {
    target: { value: "2026-10-02T10:00" },
  });
  fireEvent.change(screen.getByLabelText("Monitor to"), {
    target: { value: "2026-10-02T09:00" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Apply range" }));
  expect(
    screen.getByText("Enter a valid range with From before To."),
  ).toBeInTheDocument();
  fireEvent.change(screen.getByLabelText("Monitor to"), {
    target: { value: "2026-10-02T11:00" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Apply range" }));
  await waitFor(() =>
    expect(api.fetchTaskLogs).toHaveBeenLastCalledWith(
      "orders",
      task.task_id,
      expect.objectContaining({
        from: new Date("2026-10-02T10:00").toISOString(),
        to: new Date("2026-10-02T11:00").toISOString(),
      }),
    ),
  );
  fireEvent.click(screen.getByRole("button", { name: "Logs focus" }));
  expect(readMonitorPreferences()).toEqual({
    layout: "logs",
    pane: 35,
    wrap: false,
    refresh: false,
  });
});

it("expands, traps keyboard focus, and returns focus on Escape", async () => {
  mount();
  await screen.findByText("Payment timed out");
  const button = screen.getByRole("button", { name: "Expand" });
  button.focus();
  fireEvent.click(button);
  const dialog = screen.getByRole("dialog", { name: "Monitoring workspace" });
  expect(
    within(dialog).getByRole("button", { name: "Exit expand" }),
  ).toHaveFocus();
  const first = within(dialog).getByRole("button", { name: "Task explorer" });
  first.focus();
  fireEvent.keyDown(first, { key: "Tab", shiftKey: true });
  expect(screen.getByLabelText("Log entries")).toHaveFocus();
  fireEvent.keyDown(screen.getByLabelText("Log entries"), { key: "Tab" });
  expect(first).toHaveFocus();
  fireEvent.keyDown(window, { key: "Escape" });
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  expect(button).toHaveFocus();
});

it("shows provider, failed-search and empty states without issuing log requests", async () => {
  vi.mocked(api.searchTasks).mockRejectedValueOnce(
    new Error("Index unavailable"),
  );
  mount("/", { log_config: null });
  await screen.findByText("Index unavailable");
  expect(screen.getByText(/No log provider configured/)).toBeInTheDocument();
  expect(api.fetchServiceLogs).not.toHaveBeenCalled();
  vi.mocked(api.searchTasks).mockResolvedValue({ count: 0, items: [] });
  fireEvent.click(screen.getByRole("button", { name: "Refresh tasks" }));
  await screen.findByText("No tasks match these filters.");
});

it("handles corrupt preferences and terminal/active/rolling window boundaries", () => {
  localStorage.setItem("studio:monitor-preferences", "broken");
  expect(readMonitorPreferences().layout).toBe("explorer");
  localStorage.setItem(
    "studio:monitor-preferences",
    JSON.stringify({ layout: "invalid", pane: 999 }),
  );
  expect(readMonitorPreferences().pane).toBe(40);
  const now = Date.parse("2026-10-02T08:00:00Z");
  expect(monitorTimeWindow("task", now, task, [event])).toEqual({
    from: "2026-10-02T07:30:03.000Z",
    to: "2026-10-02T07:34:09.000Z",
  });
  expect(
    monitorTimeWindow("task", now, { ...task, status: "running" }, [event]).to,
  ).toBe("2026-10-02T08:02:00.000Z");
  expect(monitorTimeWindow("1h", now).from).toBe("2026-10-02T07:00:00.000Z");
  expect(validCustomWindow("bad", "")).toBe(false);
  expect(validCustomWindow("", "")).toBe(false);
  expect(validCustomWindow("", "2026-10-02T08:00:00Z")).toBe(true);
});

it("renders CPU and memory in the selected event window, expands log details and handles clipboard feedback", async () => {
  vi.mocked(api.fetchTaskMetrics).mockResolvedValue({
    service_id: "orders",
    from: "",
    to: "",
    step_seconds: 30,
    approximate: true,
    warnings: ["Shared worker CPU"],
    series: [
      {
        metric: "cpu_usage",
        unit: "cores",
        labels: { pod: "worker-1" },
        points: [
          { timestamp: "2026-10-02T07:32:00Z", value: 0.2 },
          { timestamp: "2026-10-02T07:32:10Z", value: 0.5 },
        ],
      },
      {
        metric: "memory_usage",
        unit: "bytes",
        labels: { pod: "worker-1" },
        points: [
          { timestamp: "2026-10-02T07:32:00Z", value: 256 * 1024 ** 2 },
          { timestamp: "2026-10-02T07:32:10Z", value: 512 * 1024 ** 2 },
        ],
      },
    ],
  });
  vi.mocked(api.fetchTaskLogs).mockResolvedValue(
    logs("Payment timed out\nStack trace"),
  );
  const clipboard = vi.fn().mockResolvedValue(undefined);
  Object.defineProperty(navigator, "clipboard", {
    configurable: true,
    value: { writeText: clipboard },
  });
  mount("/?layout=investigation&monitor_task=order-7842", {
    metrics_config: {
      provider: "prometheus",
      base_url: "https://prom.test",
      namespace: "prod",
      service_selector_labels: {},
      namespace_label: "namespace",
      pod_label: "pod",
      container_label: "container",
      step_seconds: 30,
      task_window_padding_seconds: 120,
    },
  });
  fireEvent.click(await screen.findByRole("button", { name: /task failed/ }));
  expect(
    await screen.findByRole("img", {
      name: "Pod CPU in cores; selected event marked",
    }),
  ).toBeInTheDocument();
  expect(
    screen.getByRole("img", {
      name: "Pod memory in MiB; selected event marked",
    }),
  ).toBeInTheDocument();
  expect(screen.getByText("Shared worker CPU")).toBeInTheDocument();
  await waitFor(() =>
    expect(api.fetchTaskMetrics).toHaveBeenLastCalledWith(
      "orders",
      task.task_id,
      expect.objectContaining({
        from: "2026-10-02T07:31:38.000Z",
        to: "2026-10-02T07:32:38.000Z",
        groups: ["cpu_usage", "memory_usage"],
      }),
    ),
  );
  fireEvent.click(screen.getByRole("button", { name: "Payment timed out" }));
  expect(screen.getByLabelText("Selected log details")).toHaveTextContent(
    "Stack trace",
  );
  fireEvent.click(screen.getByRole("button", { name: "Copy message" }));
  await screen.findByText("Copied");
  expect(clipboard).toHaveBeenCalledWith("Payment timed out\nStack trace");
  clipboard.mockRejectedValueOnce(new Error("Denied"));
  fireEvent.click(screen.getByRole("button", { name: "Copy message" }));
  await screen.findByText("Copy unavailable; select the message instead.");
  fireEvent.click(screen.getByRole("button", { name: "Close log details" }));
  expect(
    screen.queryByLabelText("Selected log details"),
  ).not.toBeInTheDocument();
});

it("navigates legacy configuration hashes through Monitor and Overview while retaining URL context", () => {
  function Current() {
    const location = useLocation();
    return (
      <output aria-label="URL">
        {location.search}
        {location.hash}
      </output>
    );
  }
  render(
    <MemoryRouter
      initialEntries={[
        "/services/orders?environment=prod&monitor_task=order-7842#service-configure",
      ]}
    >
      <WorkspaceNavigation service />
      <Current />
    </MemoryRouter>,
  );
  expect(screen.getByRole("button", { name: "Configure" })).toHaveAttribute(
    "aria-current",
    "page",
  );
  fireEvent.click(screen.getByRole("button", { name: "Monitor" }));
  expect(screen.getByRole("button", { name: "Monitor" })).toHaveAttribute(
    "aria-current",
    "page",
  );
  fireEvent.click(screen.getByRole("button", { name: "Overview" }));
  expect(screen.getByRole("button", { name: "Overview" })).toHaveAttribute(
    "aria-current",
    "page",
  );
  expect(screen.getByLabelText("URL")).toHaveTextContent(
    "environment=prod&monitor_task=order-7842",
  );
  expect(screen.getByLabelText("URL")).not.toHaveTextContent("#");
});


it("applies exact task and log filters, sorts evidence and pages back without losing selection", async () => {
  vi.mocked(api.searchTasks).mockImplementation(async (query) => ({ count: 1, items: [task], next_cursor: query.cursor ? null : "page2" }));
  vi.mocked(api.fetchTaskEvents).mockResolvedValue({ count: 2, items: [event, { ...event, dedupe_key: "earlier", timestamp: "2026-10-02T07:32:03Z", event_type: "task_started" }] });
  vi.mocked(api.fetchTaskLogs).mockResolvedValue({ count: 2, items: [logs().items[0], { ...logs("Earlier").items[0], timestamp: "2026-10-02T07:32:03Z" }] });
  mount(); await screen.findByText("Payment timed out");
  fireEvent.change(screen.getByLabelText("Find service task"), { target: { value: "order-7842" } }); fireEvent.click(screen.getByRole("button", { name: "Find" }));
  await waitFor(() => expect(api.searchTasks).toHaveBeenLastCalledWith(expect.objectContaining({ task_id: "order-7842", cursor: null })));
  fireEvent.change(screen.getByLabelText("Task list status"), { target: { value: "failed" } });
  await waitFor(() => expect(api.searchTasks).toHaveBeenLastCalledWith(expect.objectContaining({ status: "failed" })));
  fireEvent.click(screen.getByRole("button", { name: "Next" })); await screen.findByText("Page 2"); fireEvent.click(screen.getByRole("button", { name: "Previous" })); await screen.findByText("Page 1");
  fireEvent.change(screen.getByLabelText("Log level"), { target: { value: "ERROR" } }); fireEvent.change(screen.getByLabelText("Log source"), { target: { value: "worker" } });
  await waitFor(() => expect(api.fetchTaskLogs).toHaveBeenLastCalledWith("orders", task.task_id, expect.objectContaining({ level: "ERROR", source: "worker" })));
  expect(document.body.textContent!.indexOf("Earlier")).toBeLessThan(document.body.textContent!.indexOf("Payment timed out"));
});

it("keeps scroll position for new logs and jumps only when the operator requests it", async () => {
  mount(); await screen.findByText("Payment timed out");
  const reader = screen.getByLabelText("Log entries");
  Object.defineProperty(reader, "scrollHeight", { configurable: true, value: 900 }); reader.scrollTop = 100;
  vi.mocked(api.fetchTaskLogs).mockResolvedValue({ count: 2, items: [logs().items[0], { ...logs("New result").items[0], timestamp: "2026-10-02T07:33:00Z" }] });
  fireEvent.click(screen.getByRole("button", { name: "Refresh" })); await screen.findByText("New result");
  expect(reader.scrollTop).toBe(100); fireEvent.click(screen.getByRole("button", { name: "New log results · Jump to latest" })); expect(reader.scrollTop).toBe(900);
  expect(screen.queryByRole("button", { name: "New log results · Jump to latest" })).not.toBeInTheDocument();
});


it.each(["complete", "completed", "succeeded", "success", "failed", "failure", "error", "errored", "cancelled", "canceled", "dead_lettered", "dead-lettered", "dlq", "timeout", "timed_out", "timed-out", "expired", "lease_expired", " ERRORED "])("keeps the task window fixed for terminal status %s", (status) => {
  const first = monitorTimeWindow("task", Date.parse("2026-10-02T08:00:00Z"), { ...task, status }, [event]);
  const later = monitorTimeWindow("task", Date.parse("2026-10-03T08:00:00Z"), { ...task, status }, [event]);
  expect(first.to).toBe("2026-10-02T07:34:09.000Z");
  expect(later).toEqual(first);
});

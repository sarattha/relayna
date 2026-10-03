import { act, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter, useSearchParams } from "react-router-dom";
import { RunMonitor } from "./RunMonitor";
import type { LoadRun } from "../load-testing";
import type { ServiceRecord } from "../types";
const mocks = vi.hoisted(() => ({ workspace: vi.fn(), request: vi.fn() }));
vi.mock("../api", () => ({ requestJson: mocks.request }));
vi.mock("../monitor-workspace", () => ({ MonitorWorkspace: (props: unknown) => {
  mocks.workspace(props);
  const [params] = useSearchParams();
  return <output data-testid="window">{params.get("monitor_window")} / {params.get("monitor_from")} / {params.get("monitor_to")}</output>;
} }));
const service = { service_id: "svc", name: "orders" } as ServiceRecord;
const run = { id: "run1", created_at: "2026-10-03T00:00:00Z", started_at: "2026-10-03T00:01:00Z", finished_at: "2026-10-03T00:03:00Z" } as LoadRun;
afterEach(() => { vi.useRealTimers(); vi.clearAllMocks(); });
describe("run monitoring window", () => {
  it("opens completed runs at their exact run window without overwriting initialization", async () => {
    render(<MemoryRouter><RunMonitor base="/load" service={service} run={run} active /></MemoryRouter>);
    await waitFor(() => expect(screen.getByTestId("window")).toHaveTextContent("custom / 2026-10-03T00:01:00Z / 2026-10-03T00:03:00Z"));
  });
  it("preserves the operator's chosen window when reopening the same run", async () => {
    render(<MemoryRouter initialEntries={["/?monitor_run=run1&monitor_window=custom&monitor_from=2026-10-02T00:00:00Z&monitor_to=2026-10-04T00:00:00Z"]}><RunMonitor base="/load" service={service} run={run} active /></MemoryRouter>);
    expect(screen.getByTestId("window")).toHaveTextContent("2026-10-02T00:00:00Z / 2026-10-04T00:00:00Z");
  });
  it("advances active windows and pins the final finish time", async () => {
    vi.useFakeTimers(); vi.setSystemTime(new Date("2026-10-03T00:02:00Z"));
    const live = { ...run, finished_at: undefined };
    const view = render(<MemoryRouter><RunMonitor base="/load" service={service} run={live} active /></MemoryRouter>);
    expect(screen.getByTestId("window")).toHaveTextContent("00:02:00.000Z");
    await act(async () => { vi.advanceTimersByTime(30000); });
    expect(screen.getByTestId("window")).toHaveTextContent("00:02:30.000Z");
    view.rerender(<MemoryRouter><RunMonitor base="/load" service={service} run={run} active /></MemoryRouter>);
    expect(screen.getByTestId("window")).toHaveTextContent("00:03:00Z");
  });
  it("uses paginated exact run tasks rather than global service search", async () => {
    mocks.request.mockResolvedValue({ items: [{ task_id: "task/exact.304", correlation_id: "corr", terminal_status: "failed", completed_at: "2026-10-03T00:03:00Z" }], pagination: { page: 2, total_pages: 3 } });
    render(<MemoryRouter><RunMonitor base="/load" service={service} run={{ ...run, environment: "dev", chamber: { run_id: "execution/1" } }} active /></MemoryRouter>);
    const result = await mocks.workspace.mock.lastCall![0].taskSearch({ cursor: "2", task_id: "task/exact.304", status: "failed" });
    expect(mocks.request).toHaveBeenCalledWith(expect.stringContaining("/execution%2F1/tasks?reference=run1&page=2&page_size=50&search=task%2Fexact.304&status=failed&failed_first=true"));
    expect(result).toMatchObject({ count: 1, next_cursor: "3", items: [{ task_id: "task/exact.304", correlation_id: "corr", environment: "dev", status: "failed", detail_path: "/tasks/svc/task%2Fexact.304" }] });
    mocks.request.mockResolvedValue({ items: [], pagination: { page: 3, total_pages: 3 } });
    expect((await mocks.workspace.mock.lastCall![0].taskSearch({})).next_cursor).toBeNull();
  });
  it("retains bounded legacy tasks and marks missing lifecycle fields honestly", async () => {
    render(<MemoryRouter><RunMonitor base="/load" service={service} run={{ ...run, started_at: undefined, tasks: [{ task_id: "legacy" }] as unknown as LoadRun["tasks"] }} active={false} /></MemoryRouter>);
    const result = await mocks.workspace.mock.lastCall![0].taskSearch({});
    expect(result.items[0]).toMatchObject({ task_id: "legacy", status: "pending", correlation_id: null, first_seen_at: run.created_at, last_seen_at: run.finished_at }); expect(result.next_cursor).toBeNull(); expect(mocks.request).not.toHaveBeenCalled();
  });
});

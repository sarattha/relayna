import { render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { MemoryRouter, useSearchParams } from "react-router-dom";
import { RunMonitor } from "./RunMonitor";
import type { LoadRun } from "../load-testing";
import type { ServiceRecord } from "../types";
vi.mock("../monitor-workspace", () => ({ MonitorWorkspace: () => {
  const [params] = useSearchParams();
  return <output data-testid="window">{params.get("monitor_window")} / {params.get("monitor_from")} / {params.get("monitor_to")}</output>;
} }));
const service = { service_id: "svc", name: "orders" } as ServiceRecord;
const run = { id: "run1", created_at: "2026-10-03T00:00:00Z", started_at: "2026-10-03T00:01:00Z", finished_at: "2026-10-03T00:03:00Z" } as LoadRun;
describe("run monitoring window", () => {
  it("opens completed runs at their exact run window without overwriting initialization", async () => {
    render(<MemoryRouter><RunMonitor base="/load" service={service} run={run} active /></MemoryRouter>);
    await waitFor(() => expect(screen.getByTestId("window")).toHaveTextContent("custom / 2026-10-03T00:01:00Z / 2026-10-03T00:03:00Z"));
  });
  it("preserves the operator's chosen window when reopening the same run", async () => {
    render(<MemoryRouter initialEntries={["/?monitor_run=run1&monitor_window=custom&monitor_from=2026-10-02T00:00:00Z&monitor_to=2026-10-04T00:00:00Z"]}><RunMonitor base="/load" service={service} run={run} active /></MemoryRouter>);
    expect(screen.getByTestId("window")).toHaveTextContent("2026-10-02T00:00:00Z / 2026-10-04T00:00:00Z");
  });
});

import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { MemoryRouter, useNavigate } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fetchCockpit } from "../api";
import { useStudioServices } from "../services-context";
import { cockpitFixture, cockpitService } from "../test/cockpit-fixtures";
import type { CockpitResponse } from "../types";
import { QueueWorkerPage } from "./QueueWorkerPage";

vi.mock("../api", () => ({ fetchCockpit: vi.fn() }));
vi.mock("../services-context", () => ({ useStudioServices: vi.fn() }));
const fetcher = vi.mocked(fetchCockpit);
const registry = vi.mocked(useStudioServices);
function ScopeSwitch() {
  const navigate = useNavigate();
  return (
    <button onClick={() => navigate("/queues?environment=dev")}>
      Switch environment
    </button>
  );
}
function mount(path = "/queues?environment=prod") {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <ScopeSwitch />
      <QueueWorkerPage />
    </MemoryRouter>,
  );
}
beforeEach(() => {
  registry.mockReturnValue({
    services: [cockpitService],
    loading: false,
    error: null,
  } as ReturnType<typeof useStudioServices>);
  fetcher.mockResolvedValue(cockpitFixture());
});
afterEach(() => {
  vi.useRealTimers();
  vi.clearAllMocks();
});

describe("queue and worker cockpit", () => {
  it("explains capacity, ownership and expiry and preserves investigation scope", async () => {
    mount();
    expect(await screen.findByText("orders.tasks")).toBeInTheDocument();
    expect(
      screen.getByText("Drain estimate: Not draining"),
    ).toBeInTheDocument();
    expect(screen.getByText("Expired lease")).toBeInTheDocument();
    expect(screen.getByText("pod-orders-1")).toBeInTheDocument();
    expect(
      screen.getByRole("link", { name: "orders/task/123" }),
    ).toHaveAttribute("href", "/tasks/orders/task%2F123?environment=prod");
    expect(screen.getByRole("link", { name: "Failed tasks" })).toHaveAttribute(
      "href",
      "/failed-tasks?service_id=orders&environment=prod",
    );
    expect(
      screen.getByRole("link", { name: "Investigate service" }),
    ).toHaveAttribute("href", "/services/orders?view=monitor&environment=prod");
    expect(
      screen.getByRole("img", { name: /Retry trend per second/ }),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByText("Backpressure signals (1)"));
    expect(
      screen.getByText("Inspect consumer coverage and handler latency."),
    ).toBeInTheDocument();
    expect(fetcher).toHaveBeenCalledWith("orders");
  });
  it("preserves observed zeros and unknown provider fields", async () => {
    const data = cockpitFixture();
    data.queues[0] = {
      ...data.queues[0],
      ready: 0,
      consumers: 0,
      unacknowledged: null,
      oldest_message_age_seconds: null,
      arrival_rate: null,
      completion_rate: null,
      rate_window_seconds: null,
      diagnosis: "No consumers observed for this queue.",
      drain: {
        state: "unknown",
        seconds: null,
        reason: "Incomplete queue evidence.",
      },
    };
    data.workers = [];
    data.metrics = null;
    data.sources.metrics.state = "not_configured";
    fetcher.mockResolvedValue(data);
    mount();
    expect(
      await screen.findByText("Drain estimate: Unknown"),
    ).toBeInTheDocument();
    expect(
      screen.getByText("No consumers observed for this queue."),
    ).toBeInTheDocument();
    expect(screen.getAllByText("Not reported").length).toBeGreaterThan(3);
    expect(
      screen.getByText("No compatible rate window reported."),
    ).toBeInTheDocument();
    expect(screen.getByText(/No worker records reported/)).toBeInTheDocument();
    expect(screen.getByText("not configured")).toBeInTheDocument();
  });
  it("shows finite drain estimates with scope and window", async () => {
    const data = cockpitFixture();
    data.queues[0].drain = {
      state: "draining",
      seconds: 50,
      reason: "Assumes constant rates.",
    };
    data.workers[0].active_leases = [];
    fetcher.mockResolvedValue(data);
    mount();
    expect(await screen.findByText("Drain estimate: ~50s")).toBeInTheDocument();
    expect(
      screen.getByText(
        "Rate observation window: 300s · this queue/source only.",
      ),
    ).toBeInTheDocument();
    expect(
      screen.getByText("No active leases reported by this worker."),
    ).toBeInTheDocument();
  });
  it("labels stale snapshots and stopped workers without a live estimate", async () => {
    const data = cockpitFixture(Date.now() - 180_000);
    data.workers[0].running = false;
    data.signals[0].scope_id = null;
    data.signals[0].reason = null;
    data.signals[0].recommended_action = null;
    fetcher.mockResolvedValue(data);
    mount();
    expect(
      await screen.findByText(/Displayed snapshot is stale/),
    ).toBeInTheDocument();
    expect(screen.getByText("Drain estimate: Unknown")).toBeInTheDocument();
    expect(screen.getByText("stopped")).toBeInTheDocument();
    expect(screen.getAllByText("Stale").length).toBe(2);
    expect(screen.getByText("No reason reported.")).toBeInTheDocument();
  });
  it("shows a stale worker heartbeat and lease independently of current fleet health", async () => {
    const data = cockpitFixture();
    data.workers[0].last_heartbeat_at = null;
    data.workers[0].active_leases[0].expires_at = new Date(
      Date.now() + 60_000,
    ).toISOString();
    data.workers[0].active_leases[0].heartbeat_at = new Date().toISOString();
    data.workers[0].active_leases[0].stage = null;
    fetcher.mockResolvedValue(data);
    mount();
    expect(await screen.findByText("stale heartbeat")).toBeInTheDocument();
    expect(screen.getByText("Lease heartbeat fresh")).toBeInTheDocument();
    expect(screen.getByText("Not reported / 2")).toBeInTheDocument();
  });
  it("retains unsupported, partial and unavailable source states", async () => {
    const data = cockpitFixture();
    data.queues = [];
    data.workers = [];
    data.signals = [];
    data.sources.pressure.state = "unsupported";
    data.sources.workers.state = "unavailable";
    data.sources.metrics.state = "partial";
    data.metrics!.series = [];
    data.metrics!.warnings = ["tasks_dlq_rate: source unavailable."];
    fetcher.mockResolvedValue(data);
    mount();
    expect(
      await screen.findByText("No queue observations"),
    ).toBeInTheDocument();
    for (const text of [
      "unsupported",
      "unavailable",
      "partial",
      "tasks_dlq_rate: source unavailable.",
      "Retry trend: No samples reported.",
      "DLQ trend: No samples reported.",
    ])
      expect(screen.getByText(text)).toBeInTheDocument();
  });
  it("keeps one failed service separate from a successful service and can refresh", async () => {
    registry.mockReturnValue({
      services: [
        cockpitService,
        { ...cockpitService, service_id: "billing", name: "Billing" },
      ],
      loading: false,
    } as ReturnType<typeof useStudioServices>);
    fetcher.mockImplementation(async (id) => {
      if (id === "billing") throw new Error("Timed out");
      return cockpitFixture();
    });
    mount();
    expect(
      await screen.findByText("Cockpit unavailable: Timed out"),
    ).toBeInTheDocument();
    expect(screen.getByText("orders.tasks")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Refresh evidence" }));
    await waitFor(() => expect(fetcher).toHaveBeenCalledTimes(4));
  });
  it("filters by service and environment and excludes out-of-scope requests", async () => {
    registry.mockReturnValue({
      services: [
        cockpitService,
        { ...cockpitService, service_id: "dev-only", environment: "dev" },
      ],
      loading: false,
    } as ReturnType<typeof useStudioServices>);
    mount();
    await screen.findByText("orders.tasks");
    expect(fetcher).toHaveBeenCalledTimes(1);
    fireEvent.change(
      screen.getByRole("combobox", { name: "Cockpit service" }),
      { target: { value: "orders" } },
    );
    await waitFor(() => expect(fetcher).toHaveBeenCalledTimes(2));
    fireEvent.change(
      screen.getByRole("combobox", { name: "Cockpit service" }),
      { target: { value: "" } },
    );
    await waitFor(() => expect(fetcher).toHaveBeenCalledTimes(3));
  });
  it("ignores late results after scope changes", async () => {
    let resolve!: (data: CockpitResponse) => void;
    fetcher.mockReturnValue(
      new Promise((r) => {
        resolve = r;
      }),
    );
    mount();
    fireEvent.click(screen.getByRole("button", { name: "Switch environment" }));
    expect(
      await screen.findByText("No services in this scope"),
    ).toBeInTheDocument();
    await act(async () => {
      resolve(cockpitFixture());
    });
    expect(screen.queryByText("orders.tasks")).not.toBeInTheDocument();
  });
  it("discloses registry failures and an empty scope", () => {
    registry.mockReturnValue({
      services: [],
      loading: false,
      error: "registry failed",
    } as unknown as ReturnType<typeof useStudioServices>);
    mount("/queues");
    expect(
      screen.getByText(/Registry unavailable: registry failed/),
    ).toBeInTheDocument();
    expect(screen.getByText("No services in this scope")).toBeInTheDocument();
    expect(fetcher).not.toHaveBeenCalled();
  });
  it("automatically refreshes visible pages", async () => {
    vi.useFakeTimers();
    mount();
    await act(async () => {
      await Promise.resolve();
    });
    expect(fetcher).toHaveBeenCalledTimes(1);
    await act(async () => {
      vi.advanceTimersByTime(30_000);
    });
    expect(fetcher).toHaveBeenCalledTimes(2);
  });
  it("shows registry loading and handles non-Error failures", async () => {
    registry.mockReturnValue({
      services: [cockpitService],
      loading: true,
    } as ReturnType<typeof useStudioServices>);
    fetcher.mockRejectedValue("failed");
    mount();
    expect(screen.getByText("Loading service registry…")).toBeInTheDocument();
    expect(
      await screen.findByText("Cockpit unavailable: Service read failed."),
    ).toBeInTheDocument();
  });
  it("bounds service fanout to four pending reads", async () => {
    registry.mockReturnValue({
      services: Array.from({ length: 5 }, (_, i) => ({
        ...cockpitService,
        service_id: `s${i}`,
      })),
      loading: false,
    } as ReturnType<typeof useStudioServices>);
    const resolvers: Array<(data: CockpitResponse) => void> = [];
    fetcher.mockImplementation(
      () =>
        new Promise((resolve) => {
          resolvers.push(resolve);
        }),
    );
    mount();
    expect(fetcher).toHaveBeenCalledTimes(4);
    await act(async () => {
      resolvers.slice(0, 4).forEach((resolve) => resolve(cockpitFixture()));
    });
    expect(fetcher).toHaveBeenCalledTimes(5);
    await act(async () => {
      resolvers[4](cockpitFixture());
    });
    expect(
      within(screen.getByLabelText("Cockpit service")).getAllByRole("option"),
    ).toHaveLength(6);
  });
  it("finishes a slow sweep before admitting an automatic refresh", async () => {
    vi.useFakeTimers();
    const resolveReads: Array<(data: CockpitResponse) => void> = [];
    registry.mockReturnValue({
      services: Array.from({ length: 5 }, (_, i) => ({
        ...cockpitService,
        service_id: `slow${i}`,
      })),
      loading: false,
    } as ReturnType<typeof useStudioServices>);
    fetcher.mockImplementation(
      () => new Promise((resolve) => resolveReads.push(resolve)),
    );
    mount();
    await act(async () => {
      vi.advanceTimersByTime(30_000);
    });
    expect(fetcher).toHaveBeenCalledTimes(4);
    await act(async () => {
      resolveReads.slice(0, 4).forEach((resolve) => resolve(cockpitFixture()));
    });
    expect(fetcher).toHaveBeenCalledTimes(5);
    await act(async () => {
      vi.advanceTimersByTime(30_000);
    });
    expect(fetcher).toHaveBeenCalledTimes(5);
    await act(async () => {
      resolveReads[4](cockpitFixture());
    });
  });
  it("breaks trend paths for null samples and missing intervals", async () => {
    const data = cockpitFixture();
    const now = Date.now();
    data.metrics!.series[0].points = [
      { timestamp: new Date(now - 150_000).toISOString(), value: 1 },
      { timestamp: new Date(now - 120_000).toISOString(), value: 2 },
      { timestamp: new Date(now - 90_000).toISOString(), value: null },
      { timestamp: new Date(now - 60_000).toISOString(), value: 1 },
      { timestamp: new Date(now).toISOString(), value: 2 },
    ];
    fetcher.mockResolvedValue(data);
    mount();
    const chart = await screen.findByRole("img", {
      name: /Retry trend per second/,
    });
    const path = chart.querySelector("path")!.getAttribute("d")!;
    expect((path.match(/M/g) || []).length).toBe(3);
    expect((path.match(/L/g) || []).length).toBe(1);
  });
});

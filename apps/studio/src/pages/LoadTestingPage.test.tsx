import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { LoadTestingPage } from "./LoadTestingPage";
import type { LoadProfile, LoadRun } from "../load-testing";

const mocks = vi.hoisted(() => ({ requestJson: vi.fn(), fetchServiceLogs: vi.fn(), fetchServiceMetrics: vi.fn(), isAdmin: true }));
vi.mock("../api", () => ({ requestJson: mocks.requestJson, fetchServiceLogs: mocks.fetchServiceLogs, fetchServiceMetrics: mocks.fetchServiceMetrics }));
vi.mock("../auth-context", () => ({ useStudioAuth: () => ({ isAdmin: mocks.isAdmin }) }));
vi.mock("../services-context", () => ({ useStudioServices: () => ({ loading: false, servicesById: new Map([["svc", { service_id: "svc", name: "Translation", environment: "staging", status: "healthy", log_config: {}, metrics_config: { pod_label: "pod" } }]]) }) }));
const profile: LoadProfile = {
  id: "translate", name: "Translate text", method: "POST", path: "/translations", adapter: "relayna", namespace: "staging",
  max_vus: 8, max_iterations: 100, max_duration_seconds: 300,
  input_schema: { type: "object", required: ["text", "language"], properties: { text: { type: "string", title: "Text", minLength: 1 }, language: { type: "string", enum: ["Thai", "English"] }, priority: { type: "integer", minimum: 1, maximum: 10 } } },
};
const planned: LoadRun = {
  id: "plan-1", profile_name: "Translate text", method: "POST", path: "/translations", adapter: "relayna", environment: "staging", namespace: "staging", created_at: "2026-09-14T01:00:00Z", state: "planned",
  request: { profile_id: "translate", inputs: { text: "Hello", language: "Thai" }, vus: 2, iterations: 4, duration_seconds: 30 },
};
let current: LoadRun;
function show(route = "/services/svc/load-tests") {
  return render(<MemoryRouter initialEntries={[route]}><Routes><Route path="/services/:serviceId/load-tests" element={<LoadTestingPage />} /></Routes></MemoryRouter>);
}
beforeEach(() => {
  vi.clearAllMocks(); mocks.isAdmin = true; current = structuredClone(planned);
  mocks.requestJson.mockImplementation(async (path: string, init?: RequestInit) => {
    if (path.endsWith("/profiles")) return { profiles: [profile], message: "" };
    if (path.endsWith("/plans")) { current = { ...planned, request: JSON.parse(String(init?.body)) }; return current; }
    if (path.endsWith("/start")) { current = { ...current, state: "running", started_at: planned.created_at, output: "Load started" }; return current; }
    if (path.endsWith("/cancel")) { current = { ...current, state: "cancelled", finished_at: "2026-09-14T01:01:00Z", output: "Cancelled" }; return current; }
    if (path.endsWith("/plan-1")) return current;
    return { items: [current] };
  });
  mocks.fetchServiceLogs.mockResolvedValue({ items: [{ timestamp: planned.created_at, source: "worker", level: "INFO", task_id: "task-1", message: "Task accepted" }] });
  mocks.fetchServiceMetrics.mockResolvedValue({ warnings: ["Telemetry is approximate"], series: [{ metric: "cpu_usage", unit: "cores", labels: { pod: "worker-1" }, points: [{ timestamp: planned.created_at, value: 0.5 }] }] });
});

describe("service load testing", () => {
  it("uses typed inputs, reviews without starting, then starts and cancels the selected plan", async () => {
    show();
    fireEvent.change(await screen.findByLabelText("Text *"), { target: { value: "Sample text" } });
    fireEvent.change(screen.getByLabelText("Concurrent users"), { target: { value: "2" } });
    fireEvent.change(screen.getByLabelText("Task iterations"), { target: { value: "4" } });
    fireEvent.click(screen.getByLabelText("Include priority"));
    fireEvent.change(screen.getByLabelText("priority *"), { target: { value: "7" } });
    fireEvent.click(screen.getByRole("button", { name: "Review load test" }));
    expect(await screen.findByRole("button", { name: "Start load test" })).toBeEnabled();
    const planCall = mocks.requestJson.mock.calls.find(([path]) => path.endsWith("/plans"));
    expect(JSON.parse(planCall![1].body)).toEqual({ profile_id: "translate", inputs: { text: "Sample text", language: "Thai", priority: 7 }, vus: 2, iterations: 4, duration_seconds: 30 });
    expect(mocks.requestJson.mock.calls.some(([path]) => path.endsWith("/start"))).toBe(false);
    fireEvent.click(screen.getByRole("button", { name: "Start load test" }));
    expect(await screen.findByText("Load started")).toBeInTheDocument();
    expect(await screen.findByText("Task accepted")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "task-1" })).toHaveAttribute("href", "/tasks/svc/task-1");
    expect(screen.getByRole("img", { name: "Pod metric graph" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Cancel load test" }));
    expect(await screen.findByText("Cancelled")).toBeInTheDocument();
    await waitFor(() => expect(mocks.fetchServiceMetrics).toHaveBeenLastCalledWith("svc", expect.objectContaining({ from: planned.created_at, to: "2026-09-14T01:01:00Z", split_by_pod: true })));
  });

  it("restores a run from its URL and keeps service scope on task links", async () => {
    current = { ...planned, state: "completed", started_at: planned.created_at, finished_at: "2026-09-14T01:01:00Z", tasks: [{ task_id: "task/exact", terminal_status: "completed", success: true, total_duration_ms: 12 }], result: { status: "inconclusive", evidence_coverage_percent: 0 } };
    show("/services/svc/load-tests?run=plan-1&environment=staging");
    expect(await screen.findByRole("link", { name: /task\/exact/ })).toHaveAttribute("href", "/tasks/svc/task%2Fexact?environment=staging");
    expect(screen.getByText("Assessment: inconclusive")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Cancel load test" })).not.toBeInTheDocument();
  });

  it("prevents read-only users from planning or starting", async () => {
    mocks.isAdmin = false;
    show();
    expect(await screen.findByRole("button", { name: "Review load test" })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: /Translate text planned/ }));
    expect(await screen.findByRole("button", { name: "Start load test" })).toBeDisabled();
  });

  it("shows an explicit setup state without inventing a request form", async () => {
    mocks.requestJson.mockResolvedValue({ profiles: [], message: "Ask an administrator to configure this service.", items: [] });
    show();
    expect(await screen.findByText("Ask an administrator to configure this service.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Review load test" })).not.toBeInTheDocument();
  });

  it("retains the same plan after a start failure so retry is safe", async () => {
    const original = mocks.requestJson.getMockImplementation()!;
    let failed = false;
    mocks.requestJson.mockImplementation(async (path, init) => {
      if (path.endsWith("/start") && !failed) { failed = true; throw new Error("Chamber unavailable; retry safely"); }
      return original(path, init);
    });
    show("/services/svc/load-tests?run=plan-1");
    fireEvent.click(await screen.findByRole("button", { name: "Start load test" }));
    expect(await screen.findByText("Chamber unavailable; retry safely")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Start load test" }));
    expect(await screen.findByText("Load started")).toBeInTheDocument();
    expect(mocks.requestJson.mock.calls.filter(([path]) => path.endsWith("/start")).map(([path]) => path)).toEqual(["/studio/services/svc/load-tests/plan-1/start", "/studio/services/svc/load-tests/plan-1/start"]);
  });

  it("reports telemetry failures and filters logs without hiding runner output", async () => {
    current = { ...planned, state: "failed", started_at: planned.created_at, finished_at: planned.created_at, output: "Worker failed", cleanup_required: true };
    mocks.fetchServiceMetrics.mockRejectedValue(new Error("Prometheus unavailable"));
    show("/services/svc/load-tests?run=plan-1");
    expect(await screen.findByText("Worker failed")).toBeInTheDocument();
    expect(await screen.findByText("Prometheus unavailable")).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Filter logs"), { target: { value: "task-1" } });
    fireEvent.click(screen.getByRole("button", { name: "Apply filter" }));
    await waitFor(() => expect(mocks.fetchServiceLogs).toHaveBeenLastCalledWith("svc", expect.objectContaining({ query: "task-1" })));
  });
});

it("shows OpenAPI provenance and submits its revision with the request", async () => {
  const original = mocks.requestJson.getMockImplementation()!;
  mocks.requestJson.mockImplementation(async (path, init) => path.endsWith("/profiles") ? { profiles: [{ ...profile, schema_source: "openapi", schema_revision: "revision-1" }], errors: ["SDK status route excluded"], message: "" } : original(path, init));
  show();
  expect(await screen.findByText("Request fields imported from this service’s OpenAPI definition.")).toBeInTheDocument();
  expect(screen.getByText("SDK status route excluded")).toBeInTheDocument();
  fireEvent.change(screen.getByLabelText("Text *"), { target: { value: "Hello" } });
  fireEvent.click(screen.getByRole("button", { name: "Review load test" }));
  await screen.findByRole("button", { name: "Start load test" });
  expect(JSON.parse(mocks.requestJson.mock.calls.find(([path]) => path.endsWith("/plans"))![1].body).schema_revision).toBe("revision-1");
});

it("keeps cancellation available across an environment edit without querying new-environment telemetry", async () => {
  current = { ...planned, state: "running", environment: "previous-environment", output: "Original run output" };
  show("/services/svc/load-tests?run=plan-1");
  expect(await screen.findByText("Original run output")).toBeInTheDocument();
  expect(screen.getByText(/Current service telemetry is hidden/)).toBeInTheDocument();
  expect(mocks.fetchServiceMetrics).not.toHaveBeenCalled();
  expect(mocks.fetchServiceLogs).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "Cancel load test" }));
  expect(await screen.findByText("Cancelled")).toBeInTheDocument();
});

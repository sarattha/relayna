import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { StudioDiagnosticReport } from "../types";
import { StudioApiError } from "../api";
import { HealthPage } from "./HealthPage";

const mocks = vi.hoisted(() => ({ health: vi.fn(), doctor: vi.fn(), check: vi.fn(), audit: vi.fn(), auth: vi.fn(), registry: vi.fn() }));
vi.mock("../api", async (original) => ({ ...await original<typeof import("../api")>(), fetchStudioHealth: mocks.health, fetchIntegrationDoctor: mocks.doctor, checkIntegration: mocks.check, fetchStudioAudit: mocks.audit }));
vi.mock("../auth-context", () => ({ useStudioAuth: mocks.auth }));
vi.mock("../services-context", () => ({ useStudioServices: mocks.registry }));

const timestamp = "2026-10-26T10:00:00Z";
function report(source: string, state: "unknown" | "empty" | "ok" | "unavailable", service_id: string | null = null): StudioDiagnosticReport {
  return { observed_at: timestamp, service_id, environment: service_id ? "production" : null, checks: [{
    source, state, scope: service_id ? `service=${service_id}; environment=production` : "Studio dependencies", observed_at: timestamp,
    checked_at: null, last_successful_at: null, evidence_at: null, value: null, unit: null,
    detail: `${source} evidence description`, next_check: "Inspect the configured source and retention.",
  }] };
}
const services = [{ service_id: "payments", name: "Payments", environment: "production", status: "healthy" }, { service_id: "staging", name: "Staging", environment: "staging", status: "registered" }];
function mount(path = "/health") { return render(<MemoryRouter initialEntries={[path]}><HealthPage /></MemoryRouter>); }
beforeEach(() => {
  vi.clearAllMocks();
  mocks.auth.mockReturnValue({ isAdmin: true });
  mocks.registry.mockReturnValue({ services, error: null, loading: false });
  mocks.health.mockResolvedValue(report("readiness", "ok"));
  mocks.doctor.mockResolvedValue(report("indexed_events", "empty", "payments"));
  mocks.check.mockResolvedValue(report("log_sample", "unavailable", "payments"));
  mocks.audit.mockResolvedValue({ count: 0, items: [] });
});

describe("Studio Health and Integration Doctor", () => {
  it("shows empty scoped evidence without converting missing values to zero, then runs a known task probe", async () => {
    mount("/health?environment=production&service_id=payments");
    expect(await screen.findByText("indexed_events evidence description")).toBeInTheDocument();
    expect(screen.getByText("empty")).toBeInTheDocument();
    expect(screen.getAllByText("Value: not established")).toHaveLength(2);
    expect(screen.queryByRole("option", { name: /Staging/ })).not.toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Known task ID (optional)"), { target: { value: "known-task" } });
    fireEvent.click(screen.getByRole("button", { name: "Run integration check" }));
    expect(await screen.findByText("log_sample evidence description")).toBeInTheDocument();
    expect(mocks.check).toHaveBeenCalledWith("payments", "known-task");
    expect(screen.getByText("unavailable")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Open service investigation" })).toHaveAttribute("href", "/services/payments?environment=production");
  });

  it("keeps successful service evidence when Studio health fails and supports retry", async () => {
    mocks.health.mockRejectedValueOnce(new Error("Redis unavailable"));
    mount("/health?service_id=payments");
    expect(await screen.findByText("Redis unavailable")).toBeInTheDocument();
    expect(await screen.findByText("indexed_events evidence description")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Refresh Studio evidence" }));
    expect(await screen.findByText("readiness evidence description")).toBeInTheDocument();
    expect(screen.queryByText("Redis unavailable")).not.toBeInTheDocument();
  });

  it("restricts probes and audit to admins while readers can inspect evidence", async () => {
    mocks.auth.mockReturnValue({ isAdmin: false });
    mount("/health?service_id=payments");
    expect(await screen.findByText("indexed_events evidence description")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Run integration check" })).not.toBeInTheDocument();
    expect(screen.queryByText("Operator audit")).not.toBeInTheDocument();
    expect(mocks.check).not.toHaveBeenCalled(); expect(mocks.audit).not.toHaveBeenCalled();
  });

  it("does not show a previous service result after selecting another environment/source", async () => {
    let resolveFirst!: (value: StudioDiagnosticReport) => void;
    mocks.doctor.mockImplementationOnce(() => new Promise((resolve) => { resolveFirst = resolve; }));
    mocks.doctor.mockResolvedValueOnce(report("metrics", "unknown", "staging"));
    mount("/health?service_id=payments");
    await waitFor(() => expect(mocks.doctor).toHaveBeenCalledWith("payments"));
    fireEvent.change(screen.getByLabelText("Service & environment"), { target: { value: "staging" } });
    expect(await screen.findByText("metrics evidence description")).toBeInTheDocument();
    resolveFirst(report("indexed_events", "empty", "payments"));
    await waitFor(() => expect(screen.queryByText("indexed_events evidence description")).not.toBeInTheDocument());
  });

  it("shows registry absence, failures and out-of-scope selection honestly", async () => {
    mocks.registry.mockReturnValue({ services: [], error: null, loading: false });
    const empty = mount();
    expect(screen.getByText(/No registered services in this environment/)).toBeInTheDocument();
    empty.unmount();
    mocks.registry.mockReturnValue({ services, error: "Registry failed", loading: false });
    mount("/health?environment=staging&service_id=payments");
    expect(screen.getByText("Registry unavailable: Registry failed")).toBeInTheDocument();
    expect(screen.getByText(/selected service is outside/)).toBeInTheDocument();
    expect(mocks.doctor).not.toHaveBeenCalled();
  });

  it("browses exact audit filters, renders details and distinguishes unavailable storage from empty results", async () => {
    mount();
    fireEvent.change(screen.getByLabelText("Action (exact)"), { target: { value: "service.update" } });
    fireEvent.click(screen.getByRole("button", { name: "Browse audit" }));
    expect(await screen.findByText(/No audit entries matched/)).toBeInTheDocument();
    expect(mocks.audit).toHaveBeenLastCalledWith({ actor_user_id: "", action: "service.update", target_type: "" });
    mocks.audit.mockRejectedValueOnce(new StudioApiError("Not found", 404));
    fireEvent.click(screen.getByRole("button", { name: "Browse audit" }));
    expect(await screen.findByText(/PostgreSQL is required/)).toBeInTheDocument();
    expect(screen.queryByText(/No audit entries matched/)).not.toBeInTheDocument();
    mocks.audit.mockResolvedValueOnce({ count: 1, items: [{ id: 1, occurred_at: timestamp, actor_user_id: "admin", action: "service.update", target_type: "service", target_id: "payments", details: { approved: true } }] });
    fireEvent.click(screen.getByRole("button", { name: "Browse audit" }));
    expect(await screen.findByText("service.update")).toBeInTheDocument();
    expect(screen.getByText(/"approved": true/)).toBeInTheDocument();
  });
});

it("renders zero, fractional values and timestamp evidence without hiding them", async () => {
  const evidence = report("custom_source", "ok", "payments");
  evidence.checks[0] = { ...evidence.checks[0], value: 0, unit: "seconds", checked_at: timestamp, last_successful_at: timestamp, evidence_at: timestamp };
  evidence.checks.push({ ...evidence.checks[0], source: "ingestion_lag", value: 1.25 });
  mocks.doctor.mockResolvedValue(evidence);
  mount("/health?service_id=payments");
  expect(await screen.findByText("custom_source")).toBeInTheDocument();
  expect(screen.getByText("0")).toBeInTheDocument();
  expect(screen.getByText("1.3")).toBeInTheDocument();
  expect(screen.getAllByText(/10\/26\/2026|26\/10\/2026|Oct 26/).length).toBeGreaterThan(1);
});

it.each([new Error("Evidence failed"), "unstructured"])("shows service evidence failures (%s)", async (failure) => {
  mocks.doctor.mockRejectedValue(failure);
  mount("/health?service_id=payments");
  expect(await screen.findByText(failure instanceof Error ? failure.message : "Service evidence unavailable.")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Run integration check" })).toBeEnabled();
});

it.each([new Error("Probe failed"), "unstructured"])("shows active probe failures (%s)", async (failure) => {
  mocks.check.mockRejectedValue(failure);
  mount("/health?service_id=payments");
  await screen.findByText("indexed_events evidence description");
  fireEvent.click(screen.getByRole("button", { name: "Run integration check" }));
  expect(await screen.findByText(failure instanceof Error ? failure.message : "Integration check failed.")).toBeInTheDocument();
});

it("shows nonstandard health failures, loading registry and deselection", async () => {
  mocks.health.mockRejectedValue("no response");
  mocks.registry.mockReturnValue({ services, error: null, loading: true });
  mount("/health?service_id=payments");
  expect(await screen.findByText("Studio health unavailable.")).toBeInTheDocument();
  expect(screen.getByText("Loading registry…")).toBeInTheDocument();
  fireEvent.change(screen.getByLabelText("Service & environment"), { target: { value: "" } });
  expect(screen.getByRole("button", { name: "Run integration check" })).toBeDisabled();
});

it.each([new Error("Audit rejected"), "unstructured"])("supports audit actor/target filtering and error recovery (%s)", async (failure) => {
  mocks.audit.mockRejectedValueOnce(failure);
  mount();
  fireEvent.change(screen.getByLabelText("Actor ID"), { target: { value: "operator" } });
  fireEvent.change(screen.getByLabelText("Target type (exact)"), { target: { value: "service" } });
  fireEvent.click(screen.getByRole("button", { name: "Browse audit" }));
  expect(await screen.findByText(failure instanceof Error ? failure.message : "Audit read failed.")).toBeInTheDocument();
  mocks.audit.mockResolvedValueOnce({ items: [{ id: 1, occurred_at: timestamp, action: "changed", actor_user_id: null, target_type: "service", target_id: null, details: {} }] });
  fireEvent.click(screen.getByRole("button", { name: "Browse audit" }));
  expect(await screen.findByText(/Actor not recorded/)).toBeInTheDocument();
  expect(screen.getByText(/Target not recorded/)).toBeInTheDocument();
  expect(mocks.audit).toHaveBeenLastCalledWith({ actor_user_id: "operator", action: "", target_type: "service" });
});

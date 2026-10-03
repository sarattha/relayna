import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import { ConnectionPanel } from "./ConnectionPanel";
import { AssessmentBuilder, initialAssessment } from "./AssessmentBuilder";
import { RunResults } from "./RunResults";
import { TargetProfiles } from "./TargetProfiles";
import type { ServiceRecord } from "../types";
import type { LoadRun } from "../load-testing";

const mocks = vi.hoisted(() => ({ requestJson: vi.fn() }));
vi.mock("../api", () => ({ requestJson: mocks.requestJson }));
const service = { service_id: "svc", name: "orders-api", environment: "staging", status: "healthy" } as ServiceRecord;
const base = "/studio/services/svc/load-tests";
const capabilities = { api_features: ["managed_uploads", "scenario_document", "validate_document"], goals: [{ id: "baseline_readiness", label: "Baseline readiness" }], experiment_families: { dependency_delay: "Dependency delay" } };
const connection = { id: "connection-1", source: "ui", url: "http://chamber.internal", token_configured: true, ui_settings_available: true, status: "unchecked" };
const run: LoadRun = { id: "studio-1", profile_name: "Capacity", environment: "staging", namespace: "sandbox", method: "POST", path: "/orders", adapter: "relayna", state: "completed", created_at: "2026-10-03T00:00:00Z", cleanup_required: true, chamber: { run_id: "execution-1", plan_id: "plan-1", job_id: "job-1", connection_id: "connection-1" }, request: { profile_id: "", inputs: {}, vus: 2, iterations: 305, duration_seconds: 30 } };
const result = { summary: { truncated_fields: [{ path: "/evidence_explorer/facets/task_id" }] }, run: { state: "created", tags: ["baseline"] }, result: { status: "inconclusive", evidence_coverage_percent: 40, conclusive: false, limitations: ["Prometheus evidence is missing"] }, config: initialAssessment(service), findings: [{ finding_id: "f1", title: "Missing telemetry", severity: "medium" }], evidence: [{ evidence_id: "relayna-summary", digest: "abc123" }], agents: [{ name: "observability-analyst", status: "completed", findings: [] }] };
beforeEach(() => {
  vi.clearAllMocks();
  mocks.requestJson.mockImplementation(async (path: string, init?: RequestInit) => {
    if (path.endsWith("/connection/test")) return { ...connection, status: "ready", message: "API verified; target unchecked", checked_at: "2026-10-03T00:00:00Z", capabilities };
    if (path.endsWith("/connection")) return connection;
    if (path.endsWith("/catalog/capabilities")) return capabilities;
    if (path.endsWith("/catalog/chambers")) return { chambers: [] };
    if (path.endsWith("/catalog/scenarios")) return { scenarios: [{ id: "release.baseline", source: "user", name: "Saved advanced suite", trafficAdapters: ["http", "relayna"], revision: "rev1", journeyCount: 2 }] };
    if (path.includes("/tasks?")) { const params = new URLSearchParams(path.split("?")[1]); return { items: [{ task_id: `task/exact-${params.get("page")}`, terminal_status: "failed", success: false, total_duration_ms: 123 }], total_count: 305, pagination: { page: Number(params.get("page")), total_pages: 13 } }; }
    if (path.includes("/evidence-explorer?")) return { events: [{ event_id: "event1", timestamp: "2026-10-03T00:00:00Z", signal: "timeout", correlation: "exact", source_identity: { evidence_id: "relayna-summary" } }], source_event_count: 1, facets: { workload: ["orders-worker"], task_id: ["task/exact-1"], severity: ["error"] }, pagination: { page: 1, total_pages: 1, total_items: 1 } };
    if (path.includes("/runs?")) return { runs: [], pagination: { page: 1, total_pages: 1 }, facets: {} };
    if (path.includes("/runs/execution-1?") && !init?.method) return result;
    if (path.includes("/actions/compare")) return { changes: [{ field: "readiness", delta: -10 }] };
    if (path.endsWith("/plans")) return { ...run, id: "new-plan", state: "planned" };
    return {};
  });
});
const show = (element: React.ReactNode, route = "/") => render(<MemoryRouter initialEntries={[route]}>{element}</MemoryRouter>);

describe("Chamber connection", () => {
  it("never prefills secrets, tests an unsaved draft, and saves explicit credentials", async () => {
    const onChanged = vi.fn(); show(<ConnectionPanel base={base} isAdmin onChanged={onChanged} />);
    const token = await screen.findByLabelText("Integration token"); expect(token).toHaveValue("");
    fireEvent.change(screen.getByLabelText("Chamber API URL"), { target: { value: "http://other.internal" } });
    fireEvent.change(token, { target: { value: "fresh-token" } });
    fireEvent.click(screen.getByRole("button", { name: "Test draft connection" }));
    expect(await screen.findByText("API verified; target unchecked")).toBeInTheDocument();
    const test = mocks.requestJson.mock.calls.find(([path]) => path.endsWith("/connection/test"));
    expect(JSON.parse(test![1].body)).toEqual({ mode: "ui", url: "http://other.internal", token: "fresh-token" });
    expect(onChanged).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Save connection" }));
    await waitFor(() => expect(onChanged).toHaveBeenCalledOnce()); expect(token).toHaveValue("");
    const save = mocks.requestJson.mock.calls.find(([path, init]) => path.endsWith("/connection") && init?.method === "PUT"); expect(save).toBeTruthy();
  });
  it("allows readers to see diagnostics without mutation controls", async () => {
    show(<ConnectionPanel base={base} isAdmin={false} onChanged={vi.fn()} />);
    expect(await screen.findByText("Configured · hidden")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Save connection" })).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Integration token")).not.toBeInTheDocument();
  });
  it("explains missing secure storage while preserving deployment fallback", async () => {
    mocks.requestJson.mockResolvedValue({ ...connection, source: "deployment", ui_settings_available: false });
    show(<ConnectionPanel base={base} isAdmin onChanged={vi.fn()} />);
    fireEvent.change(await screen.findByLabelText("Connection source"), { target: { value: "ui" } });
    expect(screen.getByRole("button", { name: "Save connection" })).toBeDisabled();
    expect(screen.getByText(/Saving a credential requires PostgreSQL/)).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Connection source"), { target: { value: "deployment" } });
    expect(screen.getByRole("button", { name: "Save connection" })).toBeEnabled();
  });
});

describe("full assessment configuration", () => {
  it("uses form fields rather than JSON body for form encoded journeys", async () => {
    const onPlan = vi.fn(); show(<AssessmentBuilder base={base} service={service} isAdmin onPlan={onPlan} />);
    fireEvent.change(await screen.findByLabelText("Kubernetes context"), { target: { value: "cluster" } });
    fireEvent.change(screen.getByLabelText("Namespace"), { target: { value: "dev" } });
    fireEvent.click(screen.getByRole("button", { name: "Traffic" }));
    fireEvent.change(screen.getByLabelText("Request encoding"), { target: { value: "form" } });
    fireEvent.change(screen.getByLabelText("Journey 1 form fields"), { target: { value: '{"name":"synthetic"}' } });
    fireEvent.click(screen.getByRole("button", { name: "Apply journey 1 form fields" }));
    fireEvent.click(screen.getByRole("button", { name: "Review assessment" }));
    await waitFor(() => expect(onPlan).toHaveBeenCalled());
    const call = mocks.requestJson.mock.calls.find(([path]) => path.endsWith("/plans"));
    expect(JSON.parse(call![1].body).config.traffic.journeys[0].form).toEqual({ name: "synthetic" });
  });
  it("plans managed uploads in the canonical multipart files field", async () => {
    const original = mocks.requestJson.getMockImplementation()!;
    mocks.requestJson.mockImplementation(async (path, init) => path.endsWith("/uploads") ? { file: { field: "file", filename: "input.txt", path: "/managed/input", pathToken: "signed" } } : original(path, init));
    const onPlan = vi.fn(); show(<AssessmentBuilder base={base} service={service} isAdmin onPlan={onPlan} />);
    fireEvent.change(await screen.findByLabelText("Kubernetes context"), { target: { value: "cluster" } });
    fireEvent.change(screen.getByLabelText("Namespace"), { target: { value: "dev" } });
    fireEvent.click(screen.getByRole("button", { name: "Traffic" }));
    fireEvent.change(screen.getByLabelText("Request encoding"), { target: { value: "multipart" } });
    fireEvent.change(screen.getByLabelText("Upload test file"), { target: { files: [new File(["fixture"], "input.txt", { type: "text/plain" })] } });
    await screen.findByText("file: input.txt");
    fireEvent.click(screen.getByRole("button", { name: "Review assessment" }));
    await waitFor(() => expect(onPlan).toHaveBeenCalled());
    const call = mocks.requestJson.mock.calls.find(([path]) => path.endsWith("/plans"));
    const journey = JSON.parse(call![1].body).config.traffic.journeys[0];
    expect(journey.multipart.files[0]).toMatchObject({ field: "file", pathToken: "signed" });
    expect(journey.files).toBeUndefined();
  });
  it("preserves multiple journeys and capacity gates when planning without starting", async () => {
    const onPlan = vi.fn(); show(<AssessmentBuilder base={base} service={service} isAdmin onPlan={onPlan} />);
    fireEvent.change(await screen.findByLabelText("Kubernetes context"), { target: { value: "aks-dev" } });
    fireEvent.change(screen.getByLabelText("Namespace"), { target: { value: "sandbox" } });
    fireEvent.click(screen.getByRole("button", { name: "Traffic" })); fireEvent.click(screen.getByRole("button", { name: "Add journey" }));
    fireEvent.click(screen.getByRole("button", { name: "Performance" }));
    fireEvent.change(screen.getByLabelText("Load model"), { target: { value: "capacity" } });
    fireEvent.change(screen.getByLabelText("Arrivals per second"), { target: { value: "7" } });
    fireEvent.click(screen.getByRole("button", { name: "Review assessment" }));
    await waitFor(() => expect(onPlan).toHaveBeenCalled());
    const call = mocks.requestJson.mock.calls.find(([path]) => path.endsWith("/plans")); const config = JSON.parse(call![1].body).config;
    expect(config.traffic.journeys).toHaveLength(2); expect(config.traffic.load.ratePerSecond).toBe(7); expect(config.traffic.load.thresholds.p95Ms).toBe(500); expect(config.runtime.kubernetesContext).toBe("aks-dev");
    expect(mocks.requestJson.mock.calls.some(([path]) => path.endsWith("/start"))).toBe(false);
  });
  it("imports advanced YAML through the full document API, preserving experiments and agents", async () => {
    const full = { ...initialAssessment(service), experiment: { family: "dependency_delay", latencyMs: 200 }, agents: { mode: "live", exclude: ["planner"] } };
    const original = mocks.requestJson.getMockImplementation()!;
    mocks.requestJson.mockImplementation(async (path, init) => path.endsWith("/actions/validate-document") ? { document: full, normalized: { kind: "ChamberConfig", identity: { name: "Full YAML" }, targetService: "orders-api", configuredFaults: ["dependency_delay"], warnings: [], revision: "rev2" } } : original(path, init));
    const onPlan = vi.fn(); show(<AssessmentBuilder base={base} service={service} isAdmin onPlan={onPlan} />);
    fireEvent.click(await screen.findByRole("button", { name: "Scenario" }));
    fireEvent.change(screen.getByLabelText("Import scenario YAML or JSON"), { target: { value: "kind: ChamberConfig\nexperiment:\n  family: dependency_delay" } });
    await waitFor(() => expect(mocks.requestJson.mock.calls.some(([path]) => path.endsWith("/catalog/capabilities"))).toBe(true));
    fireEvent.click(screen.getByRole("button", { name: "Validate scenario" }));
    fireEvent.click(await screen.findByRole("button", { name: "Load reviewed scenario" }));
    fireEvent.click(screen.getByRole("button", { name: "Review assessment" }));
    await waitFor(() => expect(onPlan).toHaveBeenCalled());
    const call = mocks.requestJson.mock.calls.find(([path]) => path.endsWith("/plans"));
    expect(JSON.parse(call![1].body).config.experiment.latencyMs).toBe(200);
    expect(JSON.parse(call![1].body).config.agents.exclude).toEqual(["planner"]);
  });
  it("disables assessment mutation for a read-only operator", async () => {
    show(<AssessmentBuilder base={base} service={service} isAdmin={false} onPlan={vi.fn()} />);
    expect(await screen.findByRole("button", { name: "Review assessment" })).toBeDisabled();
    expect(screen.getByLabelText("Kubernetes context")).toBeDisabled();
  });
});

describe("native results", () => {
  it("shows assessment separately from execution and requires cleanup confirmation", async () => {
    show(<RunResults base={base} serviceId="svc" run={run} isAdmin onPlan={vi.fn()} onRefresh={vi.fn()} />);
    expect(await screen.findByText("Assessment: inconclusive")).toBeInTheDocument(); expect(screen.getByText("Execution: completed")).toBeInTheDocument();
    expect(screen.getByText("Prometheus evidence is missing")).toBeInTheDocument(); expect(screen.getByText(/Some rendered fields are bounded: \/evidence_explorer\/facets\/task_id/)).toBeInTheDocument();
    const button = screen.getByRole("button", { name: "Confirm cleanup verified" }); expect(button).toBeDisabled();
    fireEvent.click(screen.getByLabelText("I verified fault restoration and cleanup on this target.")); fireEvent.click(button);
    await waitFor(() => expect(mocks.requestJson.mock.calls.some(([path]) => path.includes("/jobs/job-1/cleanup-verified?reference=studio-1"))).toBe(true));
    expect(screen.getByRole("link", { name: "Download HTML" })).toHaveAttribute("href", expect.stringContaining("/chamber/download/execution-1/report?format=html&reference=studio-1"));
  });
  it("pages beyond the bounded preview and keeps exact task IDs and duration", async () => {
    show(<RunResults base={base} serviceId="svc" run={run} isAdmin onPlan={vi.fn()} onRefresh={vi.fn()} />);
    await screen.findByText("Assessment: inconclusive"); fireEvent.click(screen.getByRole("button", { name: "Tasks" }));
    expect(await screen.findByText("task/exact-1")).toBeInTheDocument(); expect(screen.getByText("123 ms")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Open task" })).toHaveAttribute("href", "/tasks/svc/task%2Fexact-1");
    const taskRegion = screen.getByLabelText("Run task evidence").parentElement!; fireEvent.click(within(taskRegion).getByRole("button", { name: "Next page" }));
    expect(await screen.findByText("task/exact-2")).toBeInTheDocument();
    expect(mocks.requestJson.mock.calls.some(([path]) => path.includes("page=2") && path.includes("failed_first=true"))).toBe(true);
  });
  it("uses cited findings and workload filters for correlated evidence", async () => {
    show(<RunResults base={base} serviceId="svc" run={run} isAdmin onPlan={vi.fn()} onRefresh={vi.fn()} />);
    await screen.findByText("Assessment: inconclusive"); fireEvent.click(screen.getByRole("button", { name: "Findings" }));
    fireEvent.click(await screen.findByRole("button", { name: "Investigate cited evidence" }));
    expect(await screen.findByText("timeout")).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("workload"), { target: { value: "orders-worker" } });
    await waitFor(() => expect(mocks.requestJson.mock.calls.some(([path]) => path.includes("finding=f1") && path.includes("workload=orders-worker"))).toBe(true));
    expect(screen.getByRole("link", { name: "Download relayna-summary" })).toHaveAttribute("download");
  });
  it("prepares reruns for review and forwards baseline comparison to the pinned reference", async () => {
    const onPlan = vi.fn(); const original = mocks.requestJson.getMockImplementation()!; mocks.requestJson.mockImplementation(async (path, init) => path.includes("/rerun?") ? { ...run, id: "rerun-plan", state: "planned" } : original(path, init));
    show(<RunResults base={base} serviceId="svc" run={run} isAdmin onPlan={onPlan} onRefresh={vi.fn()} />);
    await screen.findByText("Assessment: inconclusive"); fireEvent.change(screen.getByLabelText("Baseline run ID"), { target: { value: "baseline-1" } }); fireEvent.click(screen.getByRole("button", { name: "Compare runs" }));
    const compare = await screen.findByText("Baseline comparison, regressions and evidence alignment"); expect(compare).toBeInTheDocument();
    const call = mocks.requestJson.mock.calls.find(([path]) => path.endsWith("/actions/compare")); expect(JSON.parse(call![1].body)).toEqual({ baseline_run_id: "baseline-1", candidate_run_id: "execution-1", reference: "studio-1" });
    fireEvent.click(screen.getByRole("button", { name: "Prepare rerun for review" })); await waitFor(() => expect(onPlan).toHaveBeenCalledWith(expect.objectContaining({ state: "planned" })));
  });
});


describe("target cleanup recovery", () => {
  it("requires explicit verification before releasing a native job", async () => {
    const original = mocks.requestJson.getMockImplementation()!;
    mocks.requestJson.mockImplementation(async (path, init) => path.endsWith("/catalog/chambers") ? { chambers: [{ id: "target1", name: "Recovery target", attention_jobs: [{ job_id: "native-job" }] }] } : original(path, init));
    show(<TargetProfiles base={base} isAdmin />);
    const button = await screen.findByRole("button", { name: "Confirm job cleanup verified" });
    expect(button).toBeDisabled();
    fireEvent.click(screen.getByLabelText("I verified fault restoration and cleanup for this job."));
    fireEvent.click(button);
    await waitFor(() => expect(mocks.requestJson).toHaveBeenCalledWith(`${base}/chamber/jobs/native-job/cleanup-verified`, expect.objectContaining({ body: JSON.stringify({ confirmed: true }) })));
  });
});

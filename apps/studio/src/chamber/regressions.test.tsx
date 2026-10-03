import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import { AssessmentBuilder, initialAssessment } from "./AssessmentBuilder";
import { ConnectionPanel } from "./ConnectionPanel";
import { TargetProfiles } from "./TargetProfiles";
import { RunResults, TaskEvidence } from "./RunResults";
import { JsonEditor, JsonView, Pager } from "./common";
import type { ServiceRecord } from "../types";
import type { LoadRun } from "../load-testing";

const mocks = vi.hoisted(() => ({ requestJson: vi.fn() }));
vi.mock("../api", () => ({ requestJson: mocks.requestJson }));
const service = { service_id: "svc", name: "orders", environment: "dev" } as ServiceRecord;
const base = "/studio/services/svc/load-tests";
const target = { id: "target-1", name: "Sandbox", context: "aks", namespace: "dev", service: "orders", workload: "worker", prometheus_url: "http://prometheus", max_vus: 25, max_duration_seconds: 300, occupancy: "idle", allow_faults: true, chaos_mesh: true };
const config = () => initialAssessment(service);
const normalized = { identity: { name: "Imported" }, targetService: "orders", warnings: ["Review target"], configuredFaults: [], journeys: [{ name: "imported", adapter: "relayna", path: "/tasks", method: "POST" }], load: { model: "arrival", ratePerSecond: 2 }, agentMode: "off", agentExclusions: ["planner"], requiredSignals: ["task"], origin: { source: "user" } };
const connection = { source: "ui", url: "http://chamber", token_configured: true, ui_settings_available: true };
const run = { id: "reference", created_at: "2026-10-03T00:00:00Z", state: "completed", chamber: { run_id: "run-1" } } as LoadRun;
const response = { run: { state: "completed", tags: [], archived: false }, result: { status: "pass", readiness_score: 99, conclusive: true, confidence: "high" }, events: [{ timestamp: "2026-10-03T00:00:00Z", event: "finished", message: "Done" }], agents: [{ agent: "reviewer" }], evidence: [{ type: "unregistered" }], findings: [{ description: "No id", severity: "low" }], metadata: { origin: { studio_reference: "reference" } } };
beforeEach(() => {
  vi.clearAllMocks();
  mocks.requestJson.mockImplementation(async (path: string) => {
    if (path.endsWith("/connection/test")) return { ...connection, status: "ready", last_success_at: "2026-10-03T00:00:00Z" };
    if (path.endsWith("/connection")) return connection;
    if (path.endsWith("/catalog/capabilities")) return { api_features: ["scenario_document", "validate_document", "managed_uploads"], goals: [{ goal: "baseline", name: "Baseline" }], experiment_families: { dependency_delay: "Dependency delay" } };
    if (path.endsWith("/catalog/chambers")) return { chambers: [target] };
    if (path.endsWith("/catalog/scenarios")) return { scenarios: [{ id: "suite", source: "user", name: "Saved suite", description: "Validated suite", faults: ["delay"], warnings: ["Review target"] }] };
    if (path.endsWith("/actions/discover")) return { services: [{ name: "discovered" }], workloads: [{ name: "worker" }] };
    if (path.endsWith("/actions/inspect")) return { ...config(), service: { name: "inspected", repo: "/repo" }, runtime: { namespace: "inferred" } };
    if (path.endsWith("/actions/propose")) return { label: "Observe", description: "No fault required", journeys: normalized.journeys, requiredEvidence: ["task"], expectedOutcomes: ["Completed tasks"] };
    if (path.endsWith("/actions/validate-document")) return { normalized, document: config() };
    if (path.endsWith("/actions/validate") || path.endsWith("/scenarios/user/suite")) return normalized;
    if (path.endsWith("/scenarios/user/suite/document")) return { document: { ...config(), experiment: { family: "dependency_delay" } } };
    if (path.endsWith("/plans")) return { ...run, state: "planned" };
    if (path.includes("/runs?")) return { runs: [{ run_id: "run-1", service_name: "orders", state: "completed", environment: "dev" }], pagination: { page: 1, total_pages: 2 }, facets: { states: ["completed"], outcomes: ["pass"], environments: ["dev"], faults: ["delay"] }, partial_index: true };
    if (path.includes("/tasks?")) return { items: [{ task_id: "exact/1", success: true }], pagination: { page: 1, total_pages: 2 }, total_count: 51 };
    if (path.includes("/evidence-explorer?")) return { events: [{ observed_at: "2026-10-03T00:00:00Z", state: "sampled", summary: "Pod observation", correlation: "run-window" }], facets: { workload: ["worker"] }, pagination: { page: 1, total_pages: 2 }, truncated: true };
    if (path.includes("/runs/run-1?") || path.includes("/runs/native?")) return response;
    return {};
  });
});
function show(element: React.ReactNode, route = "/") { return render(<MemoryRouter initialEntries={[route]}>{element}</MemoryRouter>); }
function change(label: string, value: string) { fireEvent.change(screen.getByLabelText(label), { target: { value } }); }
function click(name: string) { fireEvent.click(screen.getByRole("button", { name })); }
function edit(label: string, value: unknown) { change(label, JSON.stringify(value)); click(`Apply ${label.toLowerCase()}`); }
async function builder() { const onPlan = vi.fn(); show(<AssessmentBuilder base={base} service={service} isAdmin onPlan={onPlan} />); await screen.findByRole("option", { name: /Sandbox/ }); return onPlan; }
async function planned(onPlan: ReturnType<typeof vi.fn>) { click("Review assessment"); await waitFor(() => expect(onPlan).toHaveBeenCalled()); return JSON.parse(mocks.requestJson.mock.calls.find(([path]) => path.endsWith("/plans"))![1].body); }

describe("assessment regressions", () => {
  it("binds reusable targets and synchronizes discovery, ports and inspected runtime", async () => {
    const onPlan = await builder(); change("Assessment name", "Reviewed target"); change("Reusable target", "target-1");
    expect(screen.getByLabelText("Kubernetes context")).toHaveValue("aks");
    change("Reusable target", ""); change("Target service", "changed"); change("Service port", "9090"); change("Workload", "worker-v2"); change("Prometheus URL", "http://metrics");
    fireEvent.click(screen.getByLabelText("Clean up deployed resources after execution"));
    click("Discover namespace"); fireEvent.click(await screen.findByRole("button", { name: "Use Service discovered" }));
    expect(screen.getByLabelText("Target service")).toHaveValue("discovered");
    change("Repository path on Chamber", "/repo"); click("Inspect repository"); await screen.findByText(/Repository inspected/);
    const plan = await planned(onPlan); expect(plan.config.runtime).toMatchObject({ namespace: "inferred", kubernetesContext: "aks", cleanup: true, prometheusUrl: "http://metrics" });
    expect(plan.config.service).toMatchObject({ name: "inspected", repo: "/repo" });
  });
  it("switches deploy and local providers without retaining a named target", async () => {
    const onPlan = await builder(); change("Reusable target", "target-1"); change("Execution environment", "kubernetes-deploy");
    change("Execution environment", "local"); expect(screen.queryByLabelText("Kubernetes context")).not.toBeInTheDocument();
    const plan = await planned(onPlan); expect(plan.mode).toBe("local"); expect(plan.config.chamber).toBeUndefined(); expect(plan.config.runtime).toMatchObject({ provider: "docker", mode: "local" });
  });
  it("applies proposed traffic without automatically enabling faults and saves full scenarios", async () => {
    const onPlan = await builder(); click("Scenario"); change("Assessment goal", "baseline"); click("Propose goal");
    await screen.findByText("Completed tasks"); click("Apply proposal traffic");
    change("Search scenarios", "suite"); change("Scenario ID", "saved-id"); fireEvent.click(screen.getByLabelText("Replace an existing user scenario with this ID"));
    click("Save reusable scenario"); await screen.findByText(/Complete scenario saved/);
    const save = JSON.parse(mocks.requestJson.mock.calls.find(([path]) => path.endsWith("/actions/save-scenario"))![1].body);
    expect(save).toMatchObject({ replace: true, document: { scenario: { id: "saved-id", requiredSignals: ["task"] } } });
    const plan = await planned(onPlan); expect(plan.config.traffic.journeys).toEqual(normalized.journeys); expect(plan.config.experiment).toBeUndefined();
  });
  it.each([true, false])("reviews saved scenarios with full-document support=%s", async (full) => {
    const original = mocks.requestJson.getMockImplementation()!;
    if (!full) mocks.requestJson.mockImplementation((path, init) => path.endsWith("/catalog/capabilities") ? Promise.resolve({ api_features: [] }) : original(path, init));
    const onPlan = await builder(); click("Scenario"); change("Saved scenario", "user/suite"); click("Preview scenario compatibility");
    await screen.findByText("Compatibility review"); click("Load reviewed scenario"); const plan = await planned(onPlan);
    if (full) expect(plan.config.experiment.family).toBe("dependency_delay");
    else { expect(plan.config.traffic.load).toEqual(normalized.load); expect(plan.config.agents).toMatchObject({ mode: "off", exclude: ["planner"] }); }
  });
  it("validates legacy imports and rejects oversized files before sending them", async () => {
    const original = mocks.requestJson.getMockImplementation()!;
    mocks.requestJson.mockImplementation((path, init) => path.endsWith("/catalog/capabilities") ? Promise.resolve({}) : original(path, init));
    const onPlan = await builder(); click("Scenario"); change("Import scenario YAML or JSON", "kind: Scenario"); click("Validate scenario");
    await screen.findByText("Compatibility review"); click("Load reviewed scenario");
    const file = new File(["x".repeat(256 * 1024 + 1)], "large.yaml");
    fireEvent.change(screen.getByLabelText("Choose scenario file"), { target: { files: [file] } }); await screen.findByText("Scenario files must be under 256 KiB.");
    const small = new File(["{}"], "small.json"); Object.defineProperty(small, "text", { value: () => Promise.resolve('{"kind":"Scenario"}') });
    fireEvent.change(screen.getByLabelText("Choose scenario file"), { target: { files: [small] } });
    await waitFor(() => expect(screen.getByLabelText("Import scenario YAML or JSON")).toHaveValue('{"kind":"Scenario"}'));
    expect((await planned(onPlan)).config.scenario.origin).toEqual(normalized.origin);
  });
  it("preserves lifecycle controls, raw bodies and HTTP stages when adapters change", async () => {
    const onPlan = await builder(); click("Traffic"); change("Journey name", "charge"); change("Traffic adapter", "relayna");
    change("Request method", "POST"); change("Request path", "/charge"); change("Expected status", "202"); change("Task concurrency", "3"); change("Total task iterations", "7"); change("Scheduling window seconds", "60");
    edit("Journey 1 lifecycle", { taskIdPath: "id", eventsPath: "/events/{task_id}" }); edit("Journey 1 headers", { "x-test": "fixture" });
    change("Request encoding", "raw"); change("Journey 1 raw request body", "raw-data");
    edit("Journey 1 advanced options", { name: "charge", adapter: "relayna", method: "POST", path: "/charge", vus: 3, iterations: 7, durationSeconds: 60, body: "raw-data", requestEncoding: "raw" });
    click("Add journey"); click("Remove journey 2");
    const plan = await planned(onPlan); expect(plan.config.traffic.journeys).toHaveLength(1); expect(plan.config.traffic.journeys[0]).toMatchObject({ body: "raw-data", vus: 3, iterations: 7 });
  });
  it("returns from lifecycle to HTTP and applies JSON body and stages", async () => {
    const onPlan = await builder(); click("Traffic"); change("Traffic adapter", "relayna"); change("Traffic adapter", "http"); change("Request encoding", "json");
    edit("Journey 1 request body", { name: "fixture" }); edit("Journey 1 traffic stages", [{ duration: "10s", targetVus: 2 }]);
    const journey = (await planned(onPlan)).config.traffic.journeys[0]; expect(journey.adapter).toBeUndefined(); expect(journey.relayna).toBeUndefined(); expect(journey.body).toEqual({ name: "fixture" }); expect(journey.stages[0].targetVus).toBe(2);
  });
  it("keeps multipart fields separate from signed files, supports removal and reports upload failures", async () => {
    const original = mocks.requestJson.getMockImplementation()!;
    mocks.requestJson.mockImplementation((path, init) => path.endsWith("/uploads") ? Promise.resolve({ file: { field: "attachment", filename: "test.txt", pathToken: "signed" } }) : original(path, init));
    const onPlan = await builder(); click("Traffic"); change("Request encoding", "multipart"); change("Multipart field name", "attachment"); edit("Journey 1 multipart fields", { name: "fixture" });
    fireEvent.change(screen.getByLabelText("Upload test file"), { target: { files: [] } });
    const file = new File(["test"], "test.txt"); fireEvent.change(screen.getByLabelText("Upload test file"), { target: { files: [file] } });
    await screen.findByText("attachment: test.txt"); click("Remove file 1");
    mocks.requestJson.mockImplementation((path, init) => path.endsWith("/uploads") ? Promise.reject("opaque failure") : original(path, init));
    fireEvent.change(screen.getByLabelText("Upload test file"), { target: { files: [file] } }); await screen.findByText("The Chamber request failed.");
    const journey = (await planned(onPlan)).config.traffic.journeys[0]; expect(journey.multipart).toEqual({ fields: { name: "fixture" }, files: [] });
  });
  it("edits advanced performance, faults and agents and rejects an invalid complete document", async () => {
    const onPlan = await builder(); click("Performance"); change("Load model", "soak");
    for (const [label, value] of [["Arrivals per second", "2.5"], ["Load duration seconds", "90"], ["Maximum in-flight tasks", "10"], ["Request timeout seconds", "20"], ["Warmup seconds", "5"], ["Recovery seconds", "5"], ["Observation window seconds", "10"], ["Consecutive failure windows", "3"]]) change(label, value);
    edit("Advanced load options", { model: "soak", ratePerSecond: 2.5, durationSeconds: 90, maxInFlight: 10, thresholds: { p95Ms: 200 } });
    change("Load model", "journeys"); expect(screen.queryByLabelText("Arrivals per second")).not.toBeInTheDocument(); change("Load model", "arrival");
    click("Experiments"); change("Experiment family", "dependency_delay"); edit("Experiment configuration", { family: "dependency_delay", durationSeconds: 15 });
    change("Experiment family", ""); edit("Legacy fault configuration", [{ type: "delay" }]);
    click("Advanced"); change("Agent mode", "live"); edit("Agent configuration", { mode: "live", exclude: ["planner"] });
    edit("Complete assessment configuration", { service: {}, deployment: {}, traffic: { journeys: [null] }, runtime: {} });
    await screen.findByText(/Include service, deployment, traffic and runtime objects/);
    const edited = { ...config(), agents: { mode: "off" } }; edit("Complete assessment configuration", edited);
    const plan = await planned(onPlan); expect(plan.config).toEqual(edited);
  });
  it("shows catalog failures without disabling available configuration and reports plan rejection", async () => {
    const original = mocks.requestJson.getMockImplementation()!;
    mocks.requestJson.mockImplementation((path, init) => path.endsWith("/catalog/scenarios") ? Promise.reject(new Error("Catalog unavailable")) : path.endsWith("/plans") ? Promise.reject(new Error("Target not ready")) : original(path, init));
    await builder(); await screen.findByText("Catalog unavailable"); click("Review assessment"); await screen.findByText("Target not ready");
    expect(screen.getByRole("button", { name: "Review assessment" })).toBeEnabled();
  });
  it("ignores catalog responses after a builder is removed", async () => {
    const pending: Array<(value: object) => void> = [];
    mocks.requestJson.mockImplementation(() => new Promise(resolve => pending.push(resolve)));
    const view = show(<AssessmentBuilder base={base} service={service} isAdmin onPlan={vi.fn()} />);
    expect(pending).toHaveLength(3); view.unmount(); pending.forEach(resolve => resolve({})); await Promise.resolve();
    expect(screen.queryByText("Full Chamber assessment")).not.toBeInTheDocument();
  });
});

describe("connection and reusable target regressions", () => {
  it("checks the saved connection without forwarding draft credentials, supports disable/fallback and recovers from errors", async () => {
    const onChanged = vi.fn(); show(<ConnectionPanel base={base} isAdmin onChanged={onChanged} />);
    await screen.findByLabelText("Integration token"); click("Check saved connection"); await screen.findByText("ready");
    expect(mocks.requestJson).toHaveBeenCalledWith(`${base}/chamber/connection/test`, { method: "POST" });
    change("Connection source", "disabled"); expect(screen.getByRole("button", { name: "Test draft connection" })).toBeDisabled(); click("Save connection"); await waitFor(() => expect(onChanged).toHaveBeenCalledOnce());
    const saved = mocks.requestJson.mock.calls.find(([path, init]) => path.endsWith("/connection") && init?.method === "PUT"); expect(JSON.parse(saved![1].body)).toEqual({ mode: "disabled" });
    change("Connection source", "deployment"); mocks.requestJson.mockRejectedValueOnce(new Error("Connection refused")); click("Test draft connection"); await screen.findByText("Connection refused");
  });
  it("does not update an unmounted panel from failed reads", async () => {
    let reject!: (error: Error) => void; mocks.requestJson.mockReturnValue(new Promise((_, failure) => { reject = failure; }));
    const view = show(<ConnectionPanel base={base} isAdmin onChanged={vi.fn()} />); view.unmount(); reject(new Error("Late request"));
    await Promise.resolve(); expect(screen.queryByText("Late request")).not.toBeInTheDocument();
  });
  it("surfaces initial errors and keeps administrator overrides disabled without storage", async () => {
    mocks.requestJson.mockRejectedValue(new Error("Settings unavailable")); show(<ConnectionPanel base={base} isAdmin onChanged={vi.fn()} />); await screen.findByText("Settings unavailable");
  });
  it("clones only editable target values and persists all budgets and prerequisites", async () => {
    show(<TargetProfiles base={base} isAdmin />); await screen.findByText("Sandbox"); click("Clone target settings");
    expect(screen.getByLabelText("Target name")).toHaveValue("Sandbox copy");
    for (const [label, value] of [["Target name", "Copy"], ["Cluster context", "aks-copy"], ["Target namespace", "qa"], ["Target Service", "api"], ["Target workload", "worker-copy"], ["Target Prometheus URL", "http://metrics"], ["Maximum users", "12"], ["Maximum duration seconds", "90"]]) change(label, value);
    fireEvent.click(screen.getByLabelText("Allow explicitly selected faults within this target")); fireEvent.click(screen.getByLabelText("Chaos Mesh is configured for this target")); click("Save reusable target");
    await waitFor(() => expect(screen.getByLabelText("Target name")).toHaveValue(""));
    const payload = JSON.parse(mocks.requestJson.mock.calls.find(([path]) => path.endsWith("/actions/create-chamber"))![1].body);
    expect(payload).toMatchObject({ name: "Copy", context: "aks-copy", max_vus: 12, max_duration_seconds: 90, allow_faults: false, chaos_mesh: false }); expect(payload.id).toBeUndefined(); expect(payload.occupancy).toBeUndefined();
  });
  it("shows target read/save failures and cleanup failures without releasing admission", async () => {
    const original = mocks.requestJson.getMockImplementation()!;
    mocks.requestJson.mockImplementation((path, init) => path.endsWith("/catalog/chambers") ? Promise.resolve({ chambers: [{ ...target, attention_jobs: [{ job_id: "blocked" }, {}] }] }) : path.includes("cleanup-verified") ? Promise.reject(new Error("Restore faults first")) : path.endsWith("/actions/create-chamber") ? Promise.reject(new Error("Duplicate target")) : original(path, init));
    show(<TargetProfiles base={base} isAdmin />); await screen.findByText("Sandbox"); fireEvent.click(screen.getByLabelText("I verified fault restoration and cleanup for this job.")); click("Confirm job cleanup verified"); await screen.findByText("Restore faults first");
    click("Clone target settings"); click("Save reusable target"); await screen.findByText("Duplicate target");
    expect(screen.getByLabelText("Target name")).toHaveValue("Sandbox copy");
  });
  it("reports failed target reads and remains available for a fresh configuration", async () => {
    mocks.requestJson.mockRejectedValue(new Error("Target catalog unavailable")); show(<TargetProfiles base={base} isAdmin />);
    await screen.findByText("Target catalog unavailable"); expect(screen.getByLabelText("Target name")).toBeEnabled();
  });
});

describe("result and evidence regressions", () => {
  it("does not invent verdicts, coverage or agent conclusions when evidence is absent", async () => {
    const original = mocks.requestJson.getMockImplementation()!;
    mocks.requestJson.mockImplementation((path, init) => path.includes("/runs/run-1?") ? Promise.resolve({ run: {}, summary: { truncated_fields: ["/tasks", { field: "load" }, {}] }, findings: [], evidence: [], agents: {} }) : original(path, init));
    show(<RunResults base={base} serviceId="svc" run={{ ...run, state: undefined } as unknown as LoadRun} isAdmin={false} onPlan={vi.fn()} onRefresh={vi.fn()} />);
    await screen.findByText("Assessment: Awaiting assessment"); expect(screen.getByText("Execution: unknown")).toBeInTheDocument(); expect(screen.getByText("Not reported")).toBeInTheDocument();
    click("Findings"); expect(screen.getByText(/No findings have been reported/)).toBeInTheDocument(); click("Evidence"); await screen.findByText("No artifacts are registered yet.");
    click("Agents"); expect(screen.getByText(/No agent outputs are available/)).toBeInTheDocument(); expect(screen.getByText("Agent outputs, findings and run state")).toBeInTheDocument();
  });
  it("saves normalized tags, archives/restores and preserves last successful results on refresh failure", async () => {
    let archived = false, fail = false; const original = mocks.requestJson.getMockImplementation()!;
    mocks.requestJson.mockImplementation((path, init) => {
      if (path.includes("/archive?")) { archived = JSON.parse(init.body).archived; return Promise.resolve({}); }
      if (path.includes("/runs/run-1?")) return fail ? Promise.reject(new Error("Transport lost")) : Promise.resolve({ ...response, run: { ...response.run, archived } });
      return original(path, init);
    });
    show(<RunResults base={base} serviceId="svc" run={run} isAdmin onPlan={vi.fn()} onRefresh={vi.fn()} />); await screen.findByText("Assessment: pass");
    change("Run tags", " baseline, ,release "); click("Save tags"); await waitFor(() => expect(mocks.requestJson.mock.calls.some(([path]) => path.includes("/tags?"))).toBe(true));
    expect(JSON.parse(mocks.requestJson.mock.calls.find(([path]) => path.includes("/tags?"))![1].body)).toEqual({ tags: ["baseline", "release"] });
    click("Archive run"); await screen.findByRole("button", { name: "Restore archived run" }); click("Restore archived run"); await screen.findByRole("button", { name: "Archive run" });
    fail = true; click("Refresh assessment"); await screen.findByText(/Transport lost.*last successful assessment snapshot/); expect(screen.getByText("Assessment: pass")).toBeInTheDocument();
  });
  it("supports timeline, config, agent output and evidence filters with pagination", async () => {
    show(<RunResults base={base} serviceId="svc" run={run} isAdmin onPlan={vi.fn()} onRefresh={vi.fn()} />); await screen.findByText("Assessment: pass");
    click("Timeline"); await screen.findByText("Pod observation"); change("Evidence from (ISO time)", "2026-10-03T00:00:00Z"); change("Evidence to (ISO time)", "2026-10-03T01:00:00Z");
    const explorer = screen.getByText("Correlated evidence explorer").parentElement!; fireEvent.click(within(explorer).getByRole("button", { name: "Next page" }));
    await waitFor(() => expect(mocks.requestJson.mock.calls.some(([path]) => path.includes("evidence-explorer?") && path.includes("page=2") && path.includes("end="))).toBe(true));
    click("Configuration"); expect(screen.getByText("Caller provenance")).toBeInTheDocument(); click("Agents"); expect(screen.getByText("reviewer · Output")).toBeInTheDocument();
    click("Findings"); click("Investigate cited evidence"); await screen.findByText("Registered evidence artifacts"); expect(screen.queryByRole("link", { name: "Download unregistered" })).not.toBeInTheDocument();
  });
  it("clears cited finding filters and reports evidence errors", async () => {
    show(<RunResults base={base} serviceId="svc" run={run} isAdmin onPlan={vi.fn()} onRefresh={vi.fn()} />, "/?result_tab=Evidence&finding=cited");
    await screen.findByText("Pod observation"); click("Clear finding context");
    await waitFor(() => expect(mocks.requestJson.mock.calls.some(([path]) => path.includes("finding=&"))).toBe(true));
    const original = mocks.requestJson.getMockImplementation()!; mocks.requestJson.mockImplementation((path, init) => path.includes("evidence-explorer?") ? Promise.reject(new Error("Evidence unavailable")) : original(path, init));
    change("workload", "worker"); await screen.findByText("Evidence unavailable");
  });
  it("filters history and opens external runs without a Studio reference", async () => {
    show(<RunResults base={base} serviceId="svc" run={null} isAdmin={false} onPlan={vi.fn()} onRefresh={vi.fn()} />);
    await screen.findByText(/workspace index is bounded/); change("Search runs", "orders");
    for (const [label, value] of [["state", "completed"], ["outcome", "pass"], ["environment", "dev"], ["fault", "delay"], ["Coverage", "partial"], ["Created from", "2026-10-01"], ["Created to", "2026-10-03"]]) change(label, value);
    fireEvent.click(screen.getByLabelText("Include archived runs"));
    const history = screen.getByRole("heading", { name: "Chamber history" }).closest("section")!;
    fireEvent.click(within(history).getByRole("button", { name: "Next page" }));
    await waitFor(() => expect(mocks.requestJson.mock.calls.some(([path]) => path.includes("archived=true") && path.includes("page=2"))).toBe(true));
    fireEvent.click(screen.getByRole("button", { name: /orders.*run-1/ })); await screen.findByText("Assessment: pass");
    expect(screen.getByRole("link", { name: "Download JSON" })).not.toHaveAttribute("href", expect.stringContaining("reference=")); expect(screen.getByRole("button", { name: "Archive run" })).toBeDisabled();
  });
  it("reports read, history and mutation errors without implying a passing run", async () => {
    const original = mocks.requestJson.getMockImplementation()!;
    mocks.requestJson.mockImplementation((path, init) => path.includes("/runs?") ? Promise.reject(new Error("History unavailable")) : path.includes("/rerun?") ? Promise.reject(new Error("Plan rejected")) : original(path, init));
    show(<RunResults base={base} serviceId="svc" run={run} isAdmin onPlan={vi.fn()} onRefresh={vi.fn()} />); await screen.findByText("History unavailable"); await screen.findByText("Assessment: pass"); click("Prepare rerun for review"); await screen.findByText(/Plan rejected/);
  });
  it("resets task pages after filter changes, handles unknown durations and empty/error responses", async () => {
    let fail = false; mocks.requestJson.mockImplementation((path: string) => fail ? Promise.reject(new Error("Task evidence unavailable")) : Promise.resolve({ items: path.includes("search=missing") ? [] : [{ task_id: "exact/1" }], pagination: { page: Number(new URLSearchParams(path.split("?")[1]).get("page")), total_pages: 3 } }));
    show(<TaskEvidence api="/runs/run" reference="reference=ref" serviceId="svc" />); await screen.findByText("exact/1"); expect(screen.getByText("Unavailable")).toBeInTheDocument();
    click("Next page"); await screen.findByText("Page 2 of 3"); click("Previous page"); change("Task status", "timeout"); fireEvent.click(screen.getByLabelText("Failed tasks first")); change("Search exact task IDs", "missing"); await screen.findByText("No tasks match these filters.");
    expect(mocks.requestJson.mock.calls.some(([path]) => path.includes("failed_first=false") && path.includes("status=timeout") && path.includes("page=1"))).toBe(true);
    fail = true; change("Search exact task IDs", "failed"); await screen.findByText("Task evidence unavailable");
  });
});

describe("configuration editor validation", () => {
  it("rejects malformed and primitive JSON, then clears errors and applies valid edits", async () => {
    const onChange = vi.fn(); show(<JsonEditor label="Settings" value={{}} onChange={onChange} />);
    for (const value of ["not-json", "null", "1"]) { change("Settings", value); click("Apply settings"); expect(onChange).not.toHaveBeenCalled(); }
    expect(screen.getByText("Enter a JSON object or array.")).toBeInTheDocument(); edit("Settings", [1]); expect(onChange).toHaveBeenCalledWith([1]); expect(screen.queryByText("Enter a JSON object or array.")).not.toBeInTheDocument();
  });
  it("renders absent JSON and guards pager bounds and busy navigation", () => {
    const page = vi.fn(); show(<><JsonView value={undefined} /><Pager page={2} pages={3} onPage={page} busy /></>); expect(screen.getByText("No data available.")).toBeInTheDocument(); expect(screen.getByRole("button", { name: "Next page" })).toBeDisabled();
  });
});

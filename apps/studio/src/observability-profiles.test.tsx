import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { useState } from "react";
import { MemoryRouter } from "react-router-dom";
import * as api from "./api";
import { ObservabilityProfileManager, ProfileSelector, useObservabilityProfiles } from "./observability-profiles";
import { ServicesPage } from "./pages/ServicesPage";
import type { ObservabilityProfile, ServiceDraft } from "./types";

vi.mock("./api", async (original) => ({ ...await original<typeof import("./api")>(), requestJson: vi.fn() }));
const auth = vi.hoisted(() => ({ isAdmin: true }));
vi.mock("./auth-context", () => ({ useStudioAuth: () => auth }));
const services = vi.hoisted(() => ({ services: [], emptyDraft: {}, clearMessages: vi.fn(), create: vi.fn(), update: vi.fn(), reload: vi.fn() }));
vi.mock("./services-context", () => ({ useStudioServices: () => services }));
const initial = api.serviceToDraft({ service_id: "orders", name: "Orders", base_url: "http://orders.internal", environment: "prod", tags: [], auth_mode: "internal_network", status: "registered" });
const profile = (provider: ObservabilityProfile["provider"] = "loki", overrides = {}): ObservabilityProfile => ({ id: provider, name: `Shared ${provider}`, environment: "prod", provider, base_url: "http://telemetry.internal", tenant_id: "tenant", auth_mode: "none", defaults: {}, revision: 1, updated_at: "now", used_by: [], credential_configured: false, ...overrides });
let saved: ObservabilityProfile[];
let calls: Array<{ path: string; init?: RequestInit }>;
function Harness({ enabled = true }: { enabled?: boolean }) {
  const state = useObservabilityProfiles(enabled);
  const [draft, setDraft] = useState<ServiceDraft>(initial);
  return <><ObservabilityProfileManager state={state} />{(["loki", "prometheus", "tempo"] as const).map(provider => <ProfileSelector key={provider} provider={provider} draft={draft} onChange={setDraft} state={state} />)}<output>{JSON.stringify(draft)}</output></>;
}
function change(label: string, value: string) { fireEvent.change(screen.getByLabelText(label), { target: { value } }); }
function open() { fireEvent.click(screen.getByRole("button", { name: "Manage observability profiles" })); }

beforeEach(() => {
  vi.clearAllMocks(); auth.isAdmin = true; saved = []; calls = []; services.emptyDraft = initial;
  vi.mocked(api.requestJson).mockImplementation(async (path, init) => {
    calls.push({ path, init });
    if (init?.method === "POST" && path.endsWith("/test")) return { ok: true, detail: "Connection verified." } as never;
    if (init?.method === "DELETE") { saved = []; return null as never; }
    if (init?.method) { const body = JSON.parse(init.body as string); saved = [profile(body.provider, { ...body, id: body.provider })]; return saved[0] as never; }
    return { profiles: saved } as never;
  });
});

describe("observability profiles", () => {
  it("creates bearer/basic profiles, tests, rotates without leaking read metadata, and deletes with confirmation", async () => {
    render(<Harness />); open();
    await screen.findByText("No saved profiles yet. Create your first connection below.");
    change("Profile environment", "production");
    change("Profile environment", "prod");
    change("Profile name", "Production logs"); change("Profile endpoint", "http://telemetry.internal"); change("Profile tenant ID", "tenant");
    change("Authentication", "bearer"); change("Bearer token", "synthetic-private");
    change("Provider defaults (JSON)", '{"pod_label":"instance"}');
    fireEvent.click(screen.getByRole("button", { name: "Save profile" }));
    await screen.findByText("Observability profile saved.");
    const created = calls.find(c => c.init?.method === "POST")!;
    expect(JSON.parse(created.init!.body as string).token).toBe("synthetic-private");
    expect(screen.queryByLabelText("Bearer token")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Test Production logs" }));
    await screen.findByText("Production logs: Connection verified.");
    fireEvent.click(screen.getByRole("button", { name: "Edit Production logs" }));
    expect(screen.getByLabelText("Bearer token")).toHaveValue("");
    expect(screen.getByLabelText("Profile environment")).toBeDisabled();
    change("Authentication", "basic"); change("Username", "user"); change("Password", "synthetic-password");
    change("Profile name", "Renamed");
    fireEvent.click(screen.getByRole("button", { name: "Save profile" }));
    await screen.findByRole("button", { name: "Edit Renamed" });
    const updated = JSON.parse(calls.find(c => c.init?.method === "PUT")!.init!.body as string);
    expect(updated).toMatchObject({ revision: 1, username: "user", password: "synthetic-password" });
    expect(updated).not.toHaveProperty("id"); expect(updated).not.toHaveProperty("used_by");
    fireEvent.click(screen.getByRole("button", { name: "Edit Renamed" }));
    fireEvent.click(screen.getByRole("button", { name: "Cancel profile edit" }));
    fireEvent.click(screen.getByRole("button", { name: "Delete Renamed" }));
    fireEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Cancel" }));
    expect(calls.some(c => c.init?.method === "DELETE")).toBe(false);
    fireEvent.click(screen.getByRole("button", { name: "Edit Renamed" }));
    fireEvent.click(screen.getByRole("button", { name: "Delete Renamed" }));
    fireEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Delete profile" }));
    await screen.findByText("Profile deleted.");
  });

  it("selects only matching providers and environments, applies defaults, detaches, and keeps selectors", async () => {
    saved = [profile("loki", { defaults: { pod_label: "instance", level_label: null } }), profile("loki", { id: "stage", environment: "stage", name: "Staging" }), profile("prometheus", { defaults: { namespace: "payments", step_seconds: 15 } }), profile("tempo", { tenant_id: null, defaults: { query_path: "/trace/{trace_id}" } })];
    render(<Harness />);
    await screen.findByRole("option", { name: "Shared loki" });
    expect(screen.queryByRole("option", { name: "Staging" })).not.toBeInTheDocument();
    for (const label of ["Loki", "Prometheus", "Tempo"]) change(`${label} saved profile`, label.toLowerCase() === "loki" ? "loki" : label.toLowerCase());
    const draft = JSON.parse(screen.getByRole("status").textContent || "{}");
    expect(draft).toMatchObject({ log_profile_id: "loki", log_pod_label: "instance", log_level_label: "", metrics_namespace: "payments", metrics_step_seconds: "15", trace_query_path: "/trace/{trace_id}" });
    expect(draft.log_service_label_value).toBe(initial.log_service_label_value);
    expect(api.buildServicePayload(draft).log_config?.profile_id).toBe("loki");
    change("Loki saved profile", "");
    expect(JSON.parse(screen.getByRole("status").textContent || "{}").log_profile_id).toBe("");
  });

  it("shows loading, failures and retry without destroying the draft, and handles invalid JSON and failed operations", async () => {
    vi.mocked(api.requestJson).mockRejectedValueOnce(new Error("Storage unavailable"));
    render(<Harness />); open();
    await screen.findAllByText("Storage unavailable");
    fireEvent.click(screen.getAllByRole("button", { name: "Retry profiles" })[0]);
    await screen.findByText("No saved profiles yet. Create your first connection below.");
    change("Profile name", "Tempo"); change("Profile endpoint", "http://tempo.internal"); change("Profile provider", "tempo");
    expect(screen.queryByLabelText("Profile tenant ID")).toBeInTheDocument();
    change("Provider defaults (JSON)", "invalid JSON");
    fireEvent.submit(screen.getByRole("form", { name: "Create observability profile" }));
    await screen.findByText("Provider defaults must be a valid JSON object.");
    for (const value of ["null", "[]", "42"]) {
      change("Provider defaults (JSON)", value);
      fireEvent.submit(screen.getByRole("form", { name: "Create observability profile" }));
      await screen.findByText("Provider defaults must be a valid JSON object.");
    }
    change("Provider defaults (JSON)", "{}");
    vi.mocked(api.requestJson).mockRejectedValueOnce(new Error("Invalid endpoint"));
    fireEvent.submit(screen.getByRole("form", { name: "Create observability profile" }));
    await screen.findByText("Invalid endpoint");
    fireEvent.click(screen.getByRole("button", { name: "Close observability profiles" })); open();
    expect(screen.getByLabelText("Profile name")).toHaveValue("Tempo");
    fireEvent.click(screen.getByRole("button", { name: "Reload profiles" }));
  });

  it("shows referenced profiles, disables deletion, reports connection errors, and preserves blank credentials", async () => {
    saved = [profile("prometheus", { name: "Metrics", used_by: ["orders"], credential_configured: true })];
    render(<Harness />); open(); await screen.findByText("Used by: orders");
    expect(screen.getByRole("button", { name: "Delete Metrics" })).toBeDisabled();
    vi.mocked(api.requestJson).mockResolvedValueOnce({ ok: false, detail: "Unauthorized" });
    fireEvent.click(screen.getByRole("button", { name: "Test Metrics" })); await screen.findByText("Metrics: Unauthorized");
    fireEvent.click(screen.getByRole("button", { name: "Edit Metrics" }));
    expect(screen.queryByLabelText("Profile tenant ID")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Save profile" }));
    await screen.findByText("Observability profile saved.");
    expect(JSON.parse(calls.find(c => c.init?.method === "PUT")!.init!.body as string)).not.toHaveProperty("token");
  });

  it("handles non-Error failures and recovers", async () => {
    vi.mocked(api.requestJson).mockRejectedValueOnce("offline");
    render(<Harness />); open();
    await screen.findAllByText("Unable to load observability profiles.");
    fireEvent.click(screen.getAllByRole("button", { name: "Retry profiles" })[0]);
    await screen.findByText("No saved profiles yet. Create your first connection below.");
    change("Profile name", "Temporary"); change("Profile endpoint", "http://telemetry.internal");
    vi.mocked(api.requestJson).mockRejectedValueOnce(null);
    fireEvent.submit(screen.getByRole("form", { name: "Create observability profile" }));
    await screen.findByText("Profile operation failed.");
  });

  it("does not load when disabled and presents stale references explicitly", async () => {
    const state = { profiles: [], loading: false, error: "", reload: vi.fn() };
    render(<><Harness enabled={false} /><ProfileSelector provider="loki" draft={{ ...initial, log_profile_id: "missing" }} onChange={vi.fn()} state={state} /></>);
    expect(api.requestJson).not.toHaveBeenCalled();
    expect(screen.getByRole("option", { name: "Unavailable profile — choose another or detach" })).toBeInTheDocument();
  });

  it("integrates with service registration, locks inherited endpoints, clears cross-environment bindings, and hides admin UI for readers", async () => {
    saved = [profile(), profile("prometheus"), profile("tempo")];
    const view = render(<MemoryRouter><ServicesPage /></MemoryRouter>);
    fireEvent.click(screen.getByRole("button", { name: /Register first service|Add Service|Register Service|New Service/i }));
    await screen.findByRole("option", { name: "Shared loki" });
    change("Loki saved profile", "loki"); change("Prometheus saved profile", "prometheus"); change("Tempo saved profile", "tempo");
    expect(screen.getByLabelText("Log base URL")).toHaveAttribute("readonly");
    fireEvent.change(screen.getAllByLabelText("Environment")[1], { target: { value: "stage" } });
    expect(screen.getByLabelText("Loki saved profile")).toHaveValue("");
    expect(screen.getByLabelText("Prometheus saved profile")).toHaveValue("");
    expect(screen.getByLabelText("Tempo saved profile")).toHaveValue("");
    view.unmount(); auth.isAdmin = false;
    render(<MemoryRouter><ServicesPage /></MemoryRouter>);
    expect(screen.queryByText("Observability Profiles")).not.toBeInTheDocument();
  });
});

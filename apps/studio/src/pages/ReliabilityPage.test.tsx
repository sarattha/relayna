import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ReliabilityPage } from "./ReliabilityPage";
import * as api from "../api";

const state = vi.hoisted(() => ({ isAdmin: true }));
vi.mock("../auth-context", () => ({ useStudioAuth: () => state }));
vi.mock("../services-context", () => ({ useStudioServices: () => ({ services: [
  { service_id: "orders", name: "Orders", environment: "production", status: "active" },
  { service_id: "test-orders", name: "Test Orders", environment: "staging", status: "active" },
] }) }));
vi.mock("../api", () => ({ fetchReliabilityGoals: vi.fn(), createReliabilityGoal: vi.fn(), deleteReliabilityGoal: vi.fn(), exportReliabilityGoal: vi.fn(), previewReliabilityGoal: vi.fn() }));
const goal = { id: "g1", revision: "r1", created_at: "2026-10-25T10:00:00Z", service_id: "orders", environment: "production", name: "Orders on time", owner: "Orders team", kind: "end_to_end_latency" as const, threshold: 99, window_seconds: 86400, deadline_seconds: 120, freshness_seconds: 180, hold_seconds: 60, runtime_scope_confirmed: false, receivers: ["orders@example.com"] };
const data = { configured: true, webhook_configured: true, email_configured: true, items: [{ goal, delivery: { received: 2, sent: 1, duplicates: 1, state: "duplicate", investigation_url: "/tasks/search?service_id=orders&environment=production&from=2026-10-24T10%3A00%3A00Z&to=2026-10-25T10%3A00%3A00Z" } }] };
function mount() { return render(<MemoryRouter initialEntries={["/reliability?environment=production"]}><ReliabilityPage /></MemoryRouter>); }

beforeEach(() => { vi.resetAllMocks(); state.isAdmin = true; vi.mocked(api.fetchReliabilityGoals).mockResolvedValue(data); });
describe("Reliability goals", () => {
  it("scopes goals and service options and presents missing evidence without zero", async () => {
    vi.mocked(api.previewReliabilityGoal).mockResolvedValue({ state: "no_data", value: null, eligible: null, good: null, detail: "No eligible work.", evaluated_at: "2026-10-25T12:00:00Z", from: "2026-10-24", to: "2026-10-25", investigation_url: "/tasks/search?service_id=orders&environment=production" });
    mount(); await screen.findByText("Orders on time");
    expect(api.fetchReliabilityGoals).toHaveBeenCalledWith({ environment: "production", service_id: "" });
    expect(screen.queryByText("Test Orders · staging")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Preview evidence" }));
    expect(await screen.findByText("no data")).toBeInTheDocument();
    expect(screen.getByText("No eligible work.")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Investigate this evidence window" })).toHaveAttribute("href", "/tasks/search?service_id=orders&environment=production");
    expect(screen.getByText("1 sent · 2 callbacks · 1 duplicates suppressed")).toBeInTheDocument();
  });
  it("exports configuration with a clear deployment boundary", async () => {
    vi.mocked(api.exportReliabilityGoal).mockResolvedValue({ prometheus: { groups: [] } });
    mount(); fireEvent.click(await screen.findByRole("button", { name: "Export rules & routing" }));
    expect(await screen.findByText("Deployment snippets (JSON / YAML compatible)")).toBeInTheDocument();
    expect(screen.getByText(/Saving a goal does not deploy rules/)).toBeInTheDocument();
  });
  it("hides mutations for read-only users", async () => {
    state.isAdmin = false; mount(); await screen.findByText("Orders on time");
    expect(screen.queryByRole("button", { name: "Save goal" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Delete goal" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Preview evidence" })).toBeInTheDocument();
  });
  it("creates with the registered environment and explicit owner recipients", async () => {
    vi.mocked(api.createReliabilityGoal).mockResolvedValue(goal); mount(); await screen.findByText("Define a goal");
    fireEvent.change(screen.getByLabelText("Registered service"), { target: { value: "orders" } });
    fireEvent.change(screen.getByLabelText("Goal name"), { target: { value: "Fast orders" } });
    fireEvent.change(screen.getByLabelText("Owner"), { target: { value: "Order team" } });
    fireEvent.change(screen.getByLabelText("Recipient emails, comma separated"), { target: { value: "owner@example.com, backup@example.com" } });
    fireEvent.click(screen.getByRole("button", { name: "Save goal" }));
    await waitFor(() => expect(api.createReliabilityGoal).toHaveBeenCalledWith(expect.objectContaining({ service_id: "orders", environment: "production", owner: "Order team", receivers: ["owner@example.com", "backup@example.com"] })));
  });
  it("surfaces provider failure instead of retaining a fresh-looking preview", async () => {
    vi.mocked(api.previewReliabilityGoal).mockRejectedValue(new Error("Provider unavailable"));
    mount(); fireEvent.click(await screen.findByRole("button", { name: "Preview evidence" }));
    expect(await screen.findByText("Error: Provider unavailable")).toBeInTheDocument();
  });
});

it("reports storage and delivery setup gaps and supports refresh after a load error", async () => {
  vi.mocked(api.fetchReliabilityGoals).mockRejectedValueOnce(new Error("Database unavailable")).mockResolvedValue({ ...data, configured: false, webhook_configured: false, email_configured: false, items: [] });
  mount(); expect(await screen.findByText("Error: Database unavailable")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Refresh delivery status" }));
  expect(await screen.findByText(/PostgreSQL required/)).toBeInTheDocument();
  expect(screen.getByText("No goals in this scope")).toBeInTheDocument();
});
it("lets an operator configure objective boundaries and sees creation errors", async () => {
  vi.mocked(api.createReliabilityGoal).mockRejectedValue(new Error("Invalid recipients"));
  mount(); await screen.findByText("Define a goal");
  fireEvent.change(screen.getByLabelText("Registered service"), { target: { value: "orders" } });
  fireEvent.change(screen.getByLabelText("Goal name"), { target: { value: "Business success" } });
  fireEvent.change(screen.getByLabelText("Owner"), { target: { value: "Orders" } });
  fireEvent.change(screen.getByLabelText("Signal or objective"), { target: { value: "completion_success" } });
  fireEvent.change(screen.getByLabelText("Threshold (%)"), { target: { value: "99.5" } });
  fireEvent.change(screen.getByLabelText("Window (seconds)"), { target: { value: "3600" } });
  fireEvent.change(screen.getByLabelText("Completion deadline (seconds)"), { target: { value: "180" } });
  fireEvent.change(screen.getByLabelText("Evidence freshness (seconds)"), { target: { value: "120" } });
  fireEvent.change(screen.getByLabelText("Hold before alert (seconds)"), { target: { value: "0" } });
  fireEvent.click(screen.getByRole("checkbox"));
  fireEvent.change(screen.getByLabelText("Recipient emails, comma separated"), { target: { value: "orders@example.com" } });
  fireEvent.click(screen.getByRole("button", { name: "Save goal" }));
  expect(await screen.findByText("Error: Invalid recipients")).toBeInTheDocument();
  expect(api.createReliabilityGoal).toHaveBeenCalledWith(expect.objectContaining({ threshold: 99.5, window_seconds: 3600, deadline_seconds: 180, freshness_seconds: 120, hold_seconds: 0, runtime_scope_confirmed: true }));
});
it("confirms deletion and surfaces delivery diagnostics and measured denominator", async () => {
  vi.mocked(api.fetchReliabilityGoals).mockResolvedValue({ ...data, items: [{ goal, delivery: { state: "failed", detail: "Email service unavailable", last_received_at: "2026-10-25T12:00:00Z" } }] });
  vi.mocked(api.previewReliabilityGoal).mockResolvedValue({ state: "breaching", value: 98, good: 98, eligible: 100, evaluated_at: "2026-10-25T12:00:00Z", from: "2026-10-24", to: "2026-10-25", detail: "Point-in-time preview", investigation_url: "/tasks/search?service_id=orders", queries: { eligible: "eligible_work" } });
  mount(); expect(await screen.findByText("Email service unavailable")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Preview evidence" }));
  expect(await screen.findByText(/Good 98 \/ eligible 100/)).toBeInTheDocument();
  expect(screen.getByText("Metric identity & queries")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Delete goal" }));
  fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
  fireEvent.click(screen.getByRole("button", { name: "Delete goal" }));
  vi.mocked(api.deleteReliabilityGoal).mockRejectedValueOnce(new Error("Delete failed")).mockResolvedValue({ deleted: true });
  const confirm = screen.getAllByRole("button", { name: "Delete goal" });
  fireEvent.click(confirm[confirm.length - 1]);
  expect(await screen.findByText("Error: Delete failed")).toBeInTheDocument();
  const retry = screen.getAllByRole("button", { name: "Delete goal" }); fireEvent.click(retry[retry.length - 1]);
  await waitFor(() => expect(api.deleteReliabilityGoal).toHaveBeenCalledTimes(2));
});
it("changes service scope without keeping another scope's goals", async () => {
  mount(); await screen.findByText("Orders on time");
  fireEvent.change(screen.getByLabelText("Service"), { target: { value: "orders" } });
  await waitFor(() => expect(api.fetchReliabilityGoals).toHaveBeenLastCalledWith({ environment: "production", service_id: "orders" }));
  fireEvent.change(screen.getByLabelText("Service"), { target: { value: "" } });
  await waitFor(() => expect(api.fetchReliabilityGoals).toHaveBeenLastCalledWith({ environment: "production", service_id: "" }));
});

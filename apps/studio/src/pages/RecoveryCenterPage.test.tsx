import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { Link, MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { RecoveryCenterPage } from "./RecoveryCenterPage";
import * as api from "../api";
import type { RecoveryPlan, RecoveryPreview } from "../types";

vi.mock("../api", () => ({ previewRecovery: vi.fn(), createRecoveryPlan: vi.fn(), fetchRecoveryPlans: vi.fn(), fetchRecoveryPlan: vi.fn(), controlRecoveryPlan: vi.fn() }));
const auth = { isAdmin: true };
vi.mock("../auth-context", () => ({ useStudioAuth: () => auth }));
const service = { service_id: "svc", name: "Payments", environment: "staging" };
vi.mock("../services-context", () => ({ useStudioServices: () => ({ services: [service], servicesById: new Map([["svc", service]]) }) }));
const preview: RecoveryPreview = { service_id: "svc", service_name: "Payments", environment: "staging", base_url: "https://payments.test", interval_seconds: 2, note: "", preview_digest: "a".repeat(64), eligible: true, warning: "Preview checks eligibility.", items: [
  { failure_id: "f0", original_task_id: "old-0", target_queue: "payments", payload_available: true, eligible: true, errors: [] },
  { failure_id: "f1", original_task_id: "old-1", target_queue: "payments", payload_available: true, eligible: true, errors: [] },
] };
const plan: RecoveryPlan = { ...preview, plan_id: "plan-1", state: "ready", version: 0, allowance: 0, created_at: "2026-10-25T00:00:00Z", started_at: null, dispatch_finished_at: null,
  items: preview.items.map((item) => ({ ...item, dispatch_state: "pending", outcome: "not_requested" })),
  report: { selected: 2, requested: 0, accepted: 0, observed_success: 0, observed_failure: 0, unknown_acceptance: 0, unknown_outcome: 0, awaiting_outcome: 0, not_dispatched: 2, repeated_failures: 0, cohort_recovery_seconds: null } };
function mount(path = "/recovery", state?: unknown) {
  return render(<MemoryRouter initialEntries={[{ pathname: path.split("?")[0], search: path.includes("?") ? `?${path.split("?")[1]}` : "", state }]}><Link to="/recovery?environment=production">Change test scope</Link><Routes><Route path="/recovery" element={<RecoveryCenterPage />} /><Route path="/recovery/:planId" element={<RecoveryCenterPage />} /></Routes></MemoryRouter>);
}
beforeEach(() => {
  vi.clearAllMocks(); auth.isAdmin = true;
  vi.mocked(api.previewRecovery).mockResolvedValue(structuredClone(preview));
  vi.mocked(api.createRecoveryPlan).mockResolvedValue(structuredClone(plan));
  vi.mocked(api.fetchRecoveryPlans).mockResolvedValue({ items: [], count: 0 });
  vi.mocked(api.fetchRecoveryPlan).mockResolvedValue(structuredClone(plan));
  vi.mocked(api.controlRecoveryPlan).mockResolvedValue({ ...structuredClone(plan), state: "running", version: 1, allowance: 2 });
});
describe("Recovery Center", () => {
  it("previews the exact handoff, saves without dispatching, then requires batch confirmation", async () => {
    mount("/recovery", { recovery: { service_id: "svc", failure_ids: ["f0", "f1"] } });
    fireEvent.click(screen.getByRole("button", { name: "Preview cohort" }));
    await screen.findByRole("button", { name: "Save pinned plan" });
    expect(api.previewRecovery).toHaveBeenCalledWith({ service_id: "svc", environment: "staging", items: [{ failure_id: "f0" }, { failure_id: "f1" }], interval_seconds: 2, note: "" });
    expect(api.controlRecoveryPlan).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Save pinned plan" }));
    await screen.findByRole("button", { name: "Review next batch" });
    fireEvent.click(screen.getByRole("button", { name: "Review next batch" }));
    expect(api.controlRecoveryPlan).not.toHaveBeenCalled();
    expect(screen.getByText("Next failure IDs: f0, f1")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Authorize batch" }));
    await waitFor(() => expect(api.controlRecoveryPlan).toHaveBeenCalledWith("plan-1", "batch", 0, 5));
    expect(api.createRecoveryPlan).toHaveBeenCalledWith(expect.objectContaining({ service_id: "svc" }), preview.preview_digest, expect.any(String));
  });
  it("invalidates previews on edits and rejects duplicate IDs and invalid override JSON", async () => {
    mount("/recovery", { recovery: { service_id: "svc", failure_ids: ["f0"] } });
    fireEvent.change(screen.getByLabelText("Overrides by failure ID (optional JSON)"), { target: { value: "invalid" } });
    fireEvent.click(screen.getByRole("button", { name: "Preview cohort" }));
    await screen.findByText("Overrides must be a JSON object keyed by failure ID.");
    expect(api.previewRecovery).not.toHaveBeenCalled();
    fireEvent.change(screen.getByLabelText("Overrides by failure ID (optional JSON)"), { target: { value: '{"f0":{"target_queue":"alternate","override_payload":{"task_id":"new"}}}' } });
    fireEvent.click(screen.getByRole("button", { name: "Preview cohort" }));
    await screen.findByRole("button", { name: "Save pinned plan" });
    expect(api.previewRecovery).toHaveBeenCalledWith(expect.objectContaining({ items: [{ failure_id: "f0", target_queue: "alternate", override_payload: { task_id: "new" } }] }));
    fireEvent.change(screen.getByLabelText("Operator note"), { target: { value: "Changed" } });
    expect(screen.queryByRole("button", { name: "Save pinned plan" })).toBeNull();
    fireEvent.change(screen.getByLabelText("Failure IDs in batch order"), { target: { value: "f0\nf0" } });
    fireEvent.click(screen.getByRole("button", { name: "Preview cohort" }));
    await screen.findByText("Select 1–200 distinct failure IDs in batch order.");
  });
  it("shows missing payloads and blocks saving an ineligible cohort", async () => {
    vi.mocked(api.previewRecovery).mockResolvedValue({ ...preview, eligible: false, items: [{ ...preview.items[0]!, eligible: false, payload_available: false, errors: ["Retained payload missing"] }] });
    mount("/recovery", { recovery: { service_id: "svc", failure_ids: ["f0"] } });
    fireEvent.click(screen.getByRole("button", { name: "Preview cohort" }));
    await screen.findByText("Retained payload missing");
    expect(screen.getByRole("button", { name: "Save pinned plan" })).toBeDisabled();
    expect(screen.getByText("Retained payload: missing")).toBeInTheDocument();
  });
  it("separates acceptance and terminal outcomes and links both task identities", async () => {
    vi.mocked(api.fetchRecoveryPlan).mockResolvedValue({ ...plan, state: "completed", items: [{ ...plan.items[0]!, dispatch_state: "accepted", outcome: "pending", replacement_task_id: "replacement/0" }], report: { ...plan.report, requested: 1, accepted: 1, awaiting_outcome: 1 } });
    mount("/recovery/plan-1");
    await screen.findByText("Awaiting terminal evidence");
    const report = screen.getByLabelText("Recovery report");
    expect(within(report).getByText("Accepted").previousSibling).toHaveTextContent("1");
    expect(within(report).getByText("Observed success").previousSibling).toHaveTextContent("0");
    expect(screen.getByRole("link", { name: "old-0" })).toHaveAttribute("href", "/tasks/svc/old-0");
    expect(screen.getByRole("link", { name: "replacement/0" })).toHaveAttribute("href", "/tasks/svc/replacement%2F0");
    expect(screen.getByText(/completed \(outcomes tracked separately\)/)).toBeInTheDocument();
  });
  it("pauses and confirms stopping while preserving visible unknown evidence", async () => {
    vi.mocked(api.fetchRecoveryPlan).mockResolvedValue({ ...plan, state: "running", version: 2, items: [{ ...plan.items[0]!, dispatch_state: "unknown", outcome: "unknown", message: "Acceptance unknown; never resent automatically." }] });
    vi.mocked(api.controlRecoveryPlan).mockResolvedValue({ ...plan, state: "paused", version: 3, allowance: 2 });
    mount("/recovery/plan-1");
    await screen.findByText("Acceptance unknown; never resent automatically.");
    fireEvent.click(screen.getByRole("button", { name: "Pause new dispatch" }));
    await waitFor(() => expect(api.controlRecoveryPlan).toHaveBeenCalledWith("plan-1", "pause", 2, 5));
    fireEvent.click(await screen.findByRole("button", { name: "Stop new dispatch" }));
    expect(api.controlRecoveryPlan).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByRole("button", { name: "Confirm stop" }));
    await waitFor(() => expect(api.controlRecoveryPlan).toHaveBeenLastCalledWith("plan-1", "stop", 3, 5));
  });
  it("keeps readonly and cross-environment plans inspectable without dispatch controls", async () => {
    auth.isAdmin = false;
    const first = mount("/recovery/plan-1");
    await screen.findByLabelText("Recovery report");
    expect(screen.queryByRole("button", { name: "Review next batch" })).toBeNull();
    first.unmount(); auth.isAdmin = true;
    mount("/recovery/plan-1?environment=production");
    await screen.findByText(/This plan is pinned to staging/);
    expect(screen.queryByRole("button", { name: "Review next batch" })).toBeNull();
  });
  it("holds the reviewed version across a concurrent operator update", async () => {
    vi.useFakeTimers();
    try {
      mount("/recovery/plan-1");
      await act(async () => { await Promise.resolve(); });
      fireEvent.click(screen.getByRole("button", { name: "Review next batch" }));
      vi.mocked(api.fetchRecoveryPlan).mockResolvedValue({ ...plan, version: 2 });
      await act(async () => { await vi.advanceTimersByTimeAsync(3000); });
      fireEvent.click(screen.getByRole("button", { name: "Authorize batch" }));
      await act(async () => { await Promise.resolve(); });
      expect(api.controlRecoveryPlan).toHaveBeenCalledWith("plan-1", "batch", 0, 5);
    } finally { vi.useRealTimers(); }
  });
  it("shows service failures and never submits a duplicate action while pending", async () => {
    vi.mocked(api.fetchRecoveryPlan).mockRejectedValue(new Error("Database unavailable"));
    mount("/recovery/plan-1");
    await screen.findByText(/Database unavailable/);
    expect(screen.queryByRole("button", { name: "Review next batch" })).toBeNull();
  });
});

describe("Recovery operator workflows", () => {
  it("edits a manual selection, reviews overrides and preserves a save request ID after response loss", async () => {
    mount();
    fireEvent.change(screen.getByLabelText("Recovery service"), { target: { value: "svc" } });
    fireEvent.change(screen.getByLabelText("Failure IDs in batch order"), { target: { value: "f0,f1" } });
    fireEvent.change(screen.getByLabelText("Seconds between dispatches"), { target: { value: "4" } });
    fireEvent.change(screen.getByLabelText("Operator note"), { target: { value: "Provider recovered" } });
    fireEvent.change(screen.getByLabelText("Overrides by failure ID (optional JSON)"), { target: { value: '{"f0":{"override_payload":{"task_id":"replacement"}}}' } });
    vi.mocked(api.previewRecovery).mockResolvedValue({ ...preview, items: [{ ...preview.items[0]!, override_payload: { task_id: "replacement" } }, preview.items[1]!] });
    fireEvent.click(screen.getByRole("button", { name: "Preview cohort" }));
    await screen.findByText("Payload override");
    expect(api.previewRecovery).toHaveBeenCalledWith(expect.objectContaining({ interval_seconds: 4, note: "Provider recovered" }));
    vi.mocked(api.createRecoveryPlan).mockRejectedValueOnce(new Error("Response lost"));
    fireEvent.click(screen.getByRole("button", { name: "Save pinned plan" }));
    await screen.findByText("Response lost");
    fireEvent.click(screen.getByRole("button", { name: "Save pinned plan" }));
    await screen.findByRole("button", { name: "Review next batch" });
    expect(vi.mocked(api.createRecoveryPlan).mock.calls[0]?.[2]).toBe(vi.mocked(api.createRecoveryPlan).mock.calls[1]?.[2]);
  });
  it("pages through retained plans in the exact environment", async () => {
    vi.mocked(api.fetchRecoveryPlans).mockResolvedValue({ items: [plan], count: 51 });
    mount("/recovery?environment=staging");
    await screen.findByText("51 plans in staging");
    expect(screen.getByRole("link", { name: /Payments · staging ready/ })).toHaveAttribute("href", "/recovery/plan-1?environment=staging");
    fireEvent.click(screen.getByRole("button", { name: "Next plans" }));
    await waitFor(() => expect(api.fetchRecoveryPlans).toHaveBeenCalledWith("staging", 50));
    await screen.findByText("51 plans in staging");
    fireEvent.click(screen.getByRole("button", { name: "Previous plans" }));
    await waitFor(() => expect(api.fetchRecoveryPlans).toHaveBeenLastCalledWith("staging", 0));
  });
  it("reviews all remaining IDs, cancels and resumes only the previously authorized allowance", async () => {
    vi.mocked(api.fetchRecoveryPlan).mockResolvedValue({ ...plan, state: "paused", version: 3, allowance: 2 });
    mount("/recovery/plan-1");
    fireEvent.click(await screen.findByRole("button", { name: "Review remaining 2" }));
    expect(screen.getByText(/Authorize 2 retries/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(api.controlRecoveryPlan).not.toHaveBeenCalled();
    fireEvent.change(screen.getByLabelText("Next batch size"), { target: { value: "1" } });
    fireEvent.click(screen.getByRole("button", { name: "Review next batch" }));
    expect(screen.getByText("Next failure IDs: f0")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    fireEvent.click(screen.getByRole("button", { name: "Resume authorized batch" }));
    await waitFor(() => expect(api.controlRecoveryPlan).toHaveBeenCalledWith("plan-1", "resume", 3, 1));
  });
  it("retains action errors while polling and ignores double submits", async () => {
    vi.useFakeTimers();
    try {
      mount("/recovery/plan-1");
      await act(async () => { await Promise.resolve(); });
      fireEvent.click(screen.getByRole("button", { name: "Review next batch" }));
      let reject!: (error: Error) => void;
      vi.mocked(api.controlRecoveryPlan).mockImplementation(() => new Promise((_, fail) => { reject = fail; }));
      fireEvent.click(screen.getByRole("button", { name: "Authorize batch" }));
      fireEvent.click(screen.getByRole("button", { name: "Authorize batch" }));
      expect(api.controlRecoveryPlan).toHaveBeenCalledOnce();
      await act(async () => reject(new Error("Plan changed. Refresh before authorizing.")));
      await act(async () => { await vi.advanceTimersByTimeAsync(3000); });
      expect(screen.getByText("Plan changed. Refresh before authorizing.")).toBeInTheDocument();
    } finally { vi.useRealTimers(); }
  });
  it("shows a verified complete cohort and same-ID evidence while readonly", async () => {
    auth.isAdmin = false;
    vi.mocked(api.fetchRecoveryPlan).mockResolvedValue({ ...plan, state: "completed", dispatch_finished_at: "2026-10-25T01:00:00Z", items: [{ ...plan.items[0]!, original_task_id: "same", replacement_task_id: "same", outcome: "succeeded", dispatch_state: "accepted", requested_at: "2026-10-25T00:00:00Z", accepted_at: "2026-10-25T00:00:01Z", observed_at: "2026-10-25T00:00:02Z" }], report: { ...plan.report, cohort_recovery_seconds: 2 } });
    mount("/recovery/plan-1");
    await screen.findByText("Cohort recovery time: 2.0 seconds");
    expect(screen.getByText(/Task ID reused/)).toBeInTheDocument();
    expect(screen.getByText(/Requested .*Accepted .*Observed/)).toBeInTheDocument();
  });
});

describe("Recovery input boundaries", () => {
  it("rejects missing service, unselected overrides, invalid intervals and invalid batches", async () => {
    const composer = mount();
    fireEvent.click(screen.getByRole("button", { name: "Preview cohort" }));
    await screen.findByText("Choose a service in the current environment.");
    fireEvent.change(screen.getByLabelText("Recovery service"), { target: { value: "svc" } });
    fireEvent.change(screen.getByLabelText("Failure IDs in batch order"), { target: { value: "f0" } });
    fireEvent.change(screen.getByLabelText("Overrides by failure ID (optional JSON)"), { target: { value: '{"other":{}}' } });
    fireEvent.click(screen.getByRole("button", { name: "Preview cohort" }));
    await screen.findByText("Overrides must refer only to selected failure IDs.");
    fireEvent.change(screen.getByLabelText("Overrides by failure ID (optional JSON)"), { target: { value: "{}" } });
    fireEvent.change(screen.getByLabelText("Seconds between dispatches"), { target: { value: "0" } });
    fireEvent.click(screen.getByRole("button", { name: "Preview cohort" }));
    await screen.findByText("Dispatch interval must be 1–60 seconds.");
    composer.unmount();
    mount("/recovery/plan-1");
    await screen.findByLabelText("Next batch size");
    fireEvent.change(screen.getByLabelText("Next batch size"), { target: { value: "201" } });
    fireEvent.click(screen.getByRole("button", { name: "Review next batch" }));
    fireEvent.click(screen.getByRole("button", { name: "Authorize batch" }));
    await screen.findByText("Batch size must be 1–200.");
    expect(api.controlRecoveryPlan).not.toHaveBeenCalled();
  });
});


it("discards a preview that returns after the environment scope changes", async () => {
  let resolve!: (value: RecoveryPreview) => void;
  vi.mocked(api.previewRecovery).mockImplementation(() => new Promise((done) => { resolve = done; }));
  mount("/recovery", { recovery: { service_id: "svc", failure_ids: ["f0"] } });
  fireEvent.click(screen.getByRole("button", { name: "Preview cohort" }));
  fireEvent.click(screen.getByRole("link", { name: "Change test scope" }));
  await act(async () => resolve(preview));
  expect(screen.queryByRole("button", { name: "Save pinned plan" })).toBeNull();
  expect(api.createRecoveryPlan).not.toHaveBeenCalled();
});

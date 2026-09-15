import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { LoadProfileManager } from "./LoadProfileManager";
import { requestJson } from "../api";
vi.mock("../api", () => ({ requestJson: vi.fn() }));
const request = vi.mocked(requestJson);
const base = "/studio/services/svc/load-tests";
const preview = { preview_id: "preview", name: "Translate", environment: "staging", method: "POST", path: "/translations", adapter: "relayna", context: "aks", namespace: "common", target_service: "translation", target_port: 8080, workloads: ["translation-api"], files: [], input_schema: { type: "object", properties: { text: { type: "string" } }, additionalProperties: false }, max_vus: 4, max_iterations: 20, max_duration_seconds: 300 };
beforeEach(() => {
  request.mockReset();
  request.mockImplementation(async (url, options) => {
    if (url.includes("/sources")) return { items: [{ run_id: "run-1", service_name: "Translation", state: "planned" }], pagination: { page: 1, total_pages: 1 } } as never;
    if (url.endsWith("/inspect")) return { operations: [{ index: 0, name: "Translate", method: "POST", path: "/translations" }] } as never;
    if (url.endsWith("/preview")) return preview as never;
    if (options?.method === "POST") return { id: "saved" } as never;
    return { profiles: [] } as never;
  });
});
async function choose() {
  fireEvent.click(screen.getByRole("button", { name: "Manage profiles" }));
  await screen.findByRole("option", { name: /Translation · run-1/ });
  await waitFor(() => expect(screen.getByLabelText("Source plan or run")).not.toBeDisabled());
  fireEvent.change(screen.getByLabelText("Source plan or run"), { target: { value: "run-1" } });
  await screen.findByRole("button", { name: "Preview import" });
  await waitFor(() => expect(screen.getByRole("button", { name: "Preview import" })).not.toBeDisabled());
  fireEvent.click(screen.getByRole("button", { name: "Preview import" }));
}
describe("Chamber profile manager", () => {
  it("requires target confirmation and saves approved limits without execution config", async () => {
    const saved = vi.fn(); render(<LoadProfileManager base={base} onSaved={saved} />);
    await choose(); await screen.findByText("Studio environment: staging");
    expect(screen.getByRole("button", { name: "Save imported profile" })).toBeDisabled();
    fireEvent.change(screen.getByLabelText("Maximum concurrent users"), { target: { value: "16" } });
    fireEvent.click(screen.getByRole("checkbox", { name: /I have checked/ }));
    fireEvent.click(screen.getByRole("button", { name: "Save imported profile" }));
    await waitFor(() => expect(saved).toHaveBeenCalledOnce());
    const call = request.mock.calls.find(([url, options]) => url === base + "/profile-import" && options?.method === "POST");
    expect(JSON.parse(String(call?.[1]?.body))).toEqual({ preview_id: "preview", name: "Translate", max_vus: 16, max_iterations: 20, max_duration_seconds: 300 });
    expect(request.mock.calls.some(([url]) => url.endsWith("/start") || url.endsWith("/plans"))).toBe(false);
  });
  it("shows schema import failure and prevents saving", async () => {
    const original = request.getMockImplementation()!;
    request.mockImplementation((url, options) => url.endsWith("/preview") ? Promise.reject(new Error("Schema is unsupported")) : original(url, options));
    render(<LoadProfileManager base={base} onSaved={() => {}} />);
    await choose(); await screen.findByText("Schema is unsupported");
    expect(screen.queryByRole("button", { name: "Save imported profile" })).toBeNull();
  });
  it("requires explicit removal confirmation", async () => {
    request.mockImplementation(async () => ({ profiles: [{ id: "saved", name: "Translate", max_vus: 8, max_iterations: 20, max_duration_seconds: 300 }], items: [], pagination: {} }) as never);
    const saved = vi.fn(); render(<LoadProfileManager base={base} onSaved={saved} />);
    fireEvent.click(screen.getByRole("button", { name: "Manage profiles" }));
    await screen.findByRole("button", { name: "Remove" });
    await waitFor(() => expect(screen.getByRole("button", { name: "Remove" })).not.toBeDisabled());
    fireEvent.click(screen.getByRole("button", { name: "Remove" }));
    expect(request.mock.calls.some(([, options]) => options?.method === "DELETE")).toBe(false);
    fireEvent.click(screen.getByRole("button", { name: "Confirm removal" }));
    await waitFor(() => expect(saved).toHaveBeenCalledOnce());
  });
});

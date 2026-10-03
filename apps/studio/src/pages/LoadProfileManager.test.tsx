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
  it("searches and pages sources, resets source selection and closes the manager", async () => {
    const original = request.getMockImplementation()!;
    request.mockImplementation(async (url, options) => {
      if (url.includes("/sources")) return { items: [{ run_id: "run-1", service_name: "Translation", state: "planned" }], pagination: { page: Number(new URLSearchParams(url.split("?")[1]).get("page")), total_pages: 3 } } as never;
      return original(url, options);
    });
    render(<LoadProfileManager base={base} onSaved={vi.fn()} />); await choose(); await screen.findByText("Studio environment: staging");
    fireEvent.change(screen.getByLabelText("Source plan or run"), { target: { value: "" } });
    await waitFor(() => expect(screen.queryByRole("button", { name: "Preview import" })).not.toBeInTheDocument());
    fireEvent.change(screen.getByLabelText("Search Chamber plans"), { target: { value: "baseline" } }); fireEvent.click(screen.getByRole("button", { name: "Search" }));
    await waitFor(() => expect(request).toHaveBeenCalledWith(expect.stringContaining("search=baseline&page=1")));
    await waitFor(() => expect(screen.getByRole("button", { name: "Next" })).toBeEnabled()); fireEvent.click(screen.getByRole("button", { name: "Next" })); await screen.findByText("Page 2 of 3");
    fireEvent.click(screen.getByRole("button", { name: "Previous" })); await screen.findByText("Page 1 of 3");
    fireEvent.click(screen.getByRole("button", { name: "Close profile manager" })); expect(screen.queryByLabelText("Source plan or run")).not.toBeInTheDocument();
  });
  it("changes selected operations and applies every approved limit without copying source secrets", async () => {
    const original = request.getMockImplementation()!;
    request.mockImplementation(async (url, options) => {
      if (url.endsWith("/inspect")) return { operations: [{ index: 0, name: "Read", method: "GET", path: "/health" }, { index: 1, name: "Translate", method: "POST", path: "/translations" }] } as never;
      if (url.endsWith("/preview")) return { ...preview, files: [{ field: "input", filename: "fixture.txt" }] } as never;
      return original(url, options);
    });
    const saved = vi.fn(); render(<LoadProfileManager base={base} onSaved={saved} />); await choose(); await screen.findByText("Studio environment: staging");
    fireEvent.change(screen.getByLabelText("Import operation"), { target: { value: "1" } }); expect(screen.queryByText("Review service binding")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Preview import" })); await screen.findByText("input: fixture.txt");
    for (const [label, value] of [["Profile name", "Approved"], ["Maximum task iterations", "25"], ["Maximum scheduling window (seconds)", "60"]]) fireEvent.change(screen.getByLabelText(label), { target: { value } });
    fireEvent.click(screen.getByLabelText("I have checked the target, operation, files and limits for this service.")); fireEvent.click(screen.getByRole("button", { name: "Save imported profile" }));
    await waitFor(() => expect(saved).toHaveBeenCalledOnce()); const call = request.mock.calls.find(([url, options]) => url.endsWith("/profile-import") && options?.method === "POST")!;
    expect(JSON.parse(String(call[1]?.body))).toMatchObject({ name: "Approved", max_iterations: 25, max_duration_seconds: 60 });
    expect(JSON.parse(String(request.mock.calls.filter(([url]) => url.endsWith("/preview")).slice(-1)[0]![1]?.body))).toMatchObject({ operation: 1 });
  });
  it("handles empty operations, opaque errors and cancelled removal", async () => {
    const original = request.getMockImplementation()!;
    request.mockImplementation(async (url, options) => {
      if (url.endsWith("/inspect")) return { operations: [] } as never;
      if (url === base + "/profile-import") return { profiles: [{ id: "saved", name: "Saved", max_vus: 4, max_iterations: 10, max_duration_seconds: 30 }] } as never;
      return original(url, options);
    });
    render(<LoadProfileManager base={base} onSaved={vi.fn()} />); fireEvent.click(screen.getByRole("button", { name: "Manage profiles" })); await screen.findByRole("option", { name: /run-1/ });
    await waitFor(() => expect(screen.getByLabelText("Source plan or run")).toBeEnabled()); fireEvent.change(screen.getByLabelText("Source plan or run"), { target: { value: "run-1" } }); await screen.findByText("This source has no request operations.");
    fireEvent.click(screen.getByRole("button", { name: "Remove" })); fireEvent.click(screen.getByRole("button", { name: "Keep profile" })); expect(screen.queryByRole("button", { name: "Confirm removal" })).not.toBeInTheDocument();
    request.mockRejectedValue("opaque"); fireEvent.click(screen.getByRole("button", { name: "Search" })); await screen.findByText("Profile request failed.");
  });
});

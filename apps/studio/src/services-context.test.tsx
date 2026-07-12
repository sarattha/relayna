import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import * as api from "./api";
import { StudioServicesProvider, useStudioServices } from "./services-context";
import type { ServiceRecord } from "./types";

vi.mock("./api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("./api")>();
  return {
    ...actual,
    listServices: vi.fn(),
    createService: vi.fn(),
    updateService: vi.fn(),
    refreshService: vi.fn(),
    runHealthCheck: vi.fn(),
    updateServiceStatus: vi.fn(),
    deleteService: vi.fn(),
  };
});

function service(overrides: Partial<ServiceRecord> = {}): ServiceRecord {
  return {
    service_id: "payments-api",
    name: "Payments",
    base_url: "https://payments.example.test",
    environment: "prod",
    tags: [],
    auth_mode: "internal_network",
    status: "healthy",
    capabilities: null,
    last_seen_at: null,
    log_config: null,
    metrics_config: null,
    trace_config: null,
    health: null,
    ...overrides,
  };
}

function Harness() {
  const context = useStudioServices();
  const draft = { ...context.emptyDraft, service_id: "payments-api", name: "Payments" };
  const swallow = (promise: Promise<unknown>) => void promise.catch(() => undefined);
  return (
    <div>
      <span data-testid="loading">{String(context.loading)}</span>
      <span data-testid="count">{context.services.length}</span>
      <span data-testid="map-count">{context.servicesById.size}</span>
      <span data-testid="error">{context.error || ""}</span>
      <span data-testid="notice">{context.notice || ""}</span>
      <button onClick={() => swallow(context.reload())}>reload</button>
      <button onClick={() => swallow(context.create(draft))}>create</button>
      <button onClick={() => swallow(context.update("payments-api", draft))}>update</button>
      <button onClick={() => swallow(context.refresh("payments-api"))}>refresh</button>
      <button onClick={() => swallow(context.runHealthCheck("payments-api"))}>health</button>
      <button onClick={() => swallow(context.updateStatus("payments-api", "disabled"))}>status</button>
      <button onClick={() => swallow(context.remove("payments-api"))}>remove</button>
      <button onClick={context.clearMessages}>clear</button>
    </div>
  );
}

function renderHarness() {
  return render(<StudioServicesProvider><Harness /></StudioServicesProvider>);
}

describe("StudioServicesProvider", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(api.listServices).mockResolvedValue({ count: 1, services: [service()] });
    vi.mocked(api.createService).mockResolvedValue(service());
    vi.mocked(api.updateService).mockResolvedValue(service());
    vi.mocked(api.refreshService).mockResolvedValue(service());
    vi.mocked(api.runHealthCheck).mockResolvedValue({} as never);
    vi.mocked(api.updateServiceStatus).mockResolvedValue(service({ status: "disabled" }));
    vi.mocked(api.deleteService).mockResolvedValue({});
  });

  it("requires the provider", () => {
    const consoleError = vi.spyOn(console, "error").mockImplementation(() => undefined);
    const preventWindowError = (event: ErrorEvent) => event.preventDefault();
    window.addEventListener("error", preventWindowError);
    function MissingProvider() {
      useStudioServices();
      return null;
    }
    expect(() => render(<MissingProvider />)).toThrow("useStudioServices must be used within StudioServicesProvider");
    window.removeEventListener("error", preventWindowError);
    consoleError.mockRestore();
  });

  it("loads services and completes every successful mutation", async () => {
    renderHarness();
    await waitFor(() => expect(screen.getByTestId("loading")).toHaveTextContent("false"));
    expect(screen.getByTestId("count")).toHaveTextContent("1");
    expect(screen.getByTestId("map-count")).toHaveTextContent("1");

    fireEvent.click(screen.getByRole("button", { name: "reload" }));
    await waitFor(() => expect(api.listServices).toHaveBeenCalledTimes(2));
    fireEvent.click(screen.getByRole("button", { name: "create" }));
    await waitFor(() => expect(screen.getByTestId("notice")).toHaveTextContent("Registered service 'payments-api'."));
    fireEvent.click(screen.getByRole("button", { name: "update" }));
    await waitFor(() => expect(screen.getByTestId("notice")).toHaveTextContent("Updated service 'payments-api'."));
    fireEvent.click(screen.getByRole("button", { name: "refresh" }));
    await waitFor(() => expect(screen.getByTestId("notice")).toHaveTextContent("Refreshed 'payments-api'."));
    fireEvent.click(screen.getByRole("button", { name: "health" }));
    await waitFor(() => expect(screen.getByTestId("notice")).toHaveTextContent("Ran health check for 'payments-api'."));
    fireEvent.click(screen.getByRole("button", { name: "status" }));
    await waitFor(() => expect(screen.getByTestId("notice")).toHaveTextContent("Marked 'payments-api' as disabled."));
    fireEvent.click(screen.getByRole("button", { name: "remove" }));
    await waitFor(() => expect(screen.getByTestId("notice")).toHaveTextContent("Deleted service 'payments-api'."));
    fireEvent.click(screen.getByRole("button", { name: "clear" }));
    expect(screen.getByTestId("notice")).toHaveTextContent("");
    expect(screen.getByTestId("error")).toHaveTextContent("");
  });

  it("uses fallback messages for non-error loading and mutation failures", async () => {
    vi.mocked(api.listServices).mockRejectedValueOnce("load-failed");
    renderHarness();
    await waitFor(() => expect(screen.getByTestId("error")).toHaveTextContent("Unable to load services."));

    const cases: Array<[string, ReturnType<typeof vi.fn>, string]> = [
      ["create", vi.mocked(api.createService), "Unable to register service."],
      ["update", vi.mocked(api.updateService), "Unable to update service 'payments-api'."],
      ["refresh", vi.mocked(api.refreshService), "Unable to refresh service 'payments-api'."],
      ["health", vi.mocked(api.runHealthCheck), "Unable to run health check for 'payments-api'."],
      ["status", vi.mocked(api.updateServiceStatus), "Unable to update service 'payments-api'."],
      ["remove", vi.mocked(api.deleteService), "Unable to delete service 'payments-api'."],
    ];
    for (const [button, mock, message] of cases) {
      mock.mockRejectedValueOnce("mutation-failed");
      fireEvent.click(screen.getByRole("button", { name: button }));
      await waitFor(() => expect(screen.getByTestId("error")).toHaveTextContent(message));
    }
  });

  it("reports an error when health refresh cannot find the updated service", async () => {
    vi.mocked(api.listServices)
      .mockResolvedValueOnce({ count: 1, services: [service()] })
      .mockResolvedValue({ count: 0, services: [] });
    renderHarness();
    await waitFor(() => expect(screen.getByTestId("loading")).toHaveTextContent("false"));
    fireEvent.click(screen.getByRole("button", { name: "health" }));
    await waitFor(() => expect(screen.getByTestId("error")).toHaveTextContent("was not found after health refresh"));
  });
});

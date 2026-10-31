import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { useMonitorRead } from "./monitor-data";

beforeEach(() => {
  vi.useFakeTimers();
  vi.spyOn(Math, "random").mockReturnValue(0.5);
});
afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
});
async function settle() {
  await act(async () => { await Promise.resolve(); });
}

it("pauses unavailable telemetry across window changes while core reads continue, then recovers", async () => {
  const logs = vi.fn().mockRejectedValueOnce(new Error("Loki offline")).mockResolvedValue("recovered");
  const core = vi.fn().mockResolvedValue("task detail");
  const { result, rerender } = renderHook(({ revision }) => ({
    logs: useMonitorRead(`window-${revision}`, true, revision, logs, "service:loki"),
    core: useMonitorRead("task", true, revision, core),
  }), { initialProps: { revision: 0 } });
  await settle();
  expect(result.current.logs.paused).toBe(true);
  expect(result.current.core.data).toBe("task detail");
  rerender({ revision: 1 });
  await settle();
  expect(core).toHaveBeenCalledTimes(2);
  expect(logs).toHaveBeenCalledTimes(1);
  expect(result.current.logs.error).toBe("Loki offline");
  await act(async () => { await vi.advanceTimersByTimeAsync(44_999); });
  expect(logs).toHaveBeenCalledTimes(1);
  await act(async () => { await vi.advanceTimersByTimeAsync(1); });
  expect(logs).toHaveBeenCalledTimes(2);
  expect(result.current.logs.data).toBe("recovered");
  expect(result.current.logs.paused).toBe(false);
});

it("resets availability and data ownership when provider configuration changes", async () => {
  let resolve: (value: string) => void = () => {};
  const read = vi.fn().mockResolvedValueOnce("old target").mockImplementationOnce(() => new Promise<string>((done) => { resolve = done; }));
  const { result, rerender } = renderHook(({ provider }) => useMonitorRead("same-query", true, 0, read, provider), { initialProps: { provider: "old" } });
  await settle();
  expect(result.current.data).toBe("old target");
  rerender({ provider: "new" });
  expect(result.current.data).toBeNull();
  await act(async () => { resolve("new target"); });
  expect(result.current.data).toBe("new target");
});

it("allows corrected configuration immediately during cooldown", async () => {
  const read = vi.fn().mockRejectedValueOnce(new Error("offline")).mockResolvedValue("healthy");
  const { result, rerender } = renderHook(({ provider }) => useMonitorRead("query", true, 0, read, provider), { initialProps: { provider: "old" } });
  await settle();
  expect(result.current.paused).toBe(true);
  rerender({ provider: "new" });
  await settle();
  expect(read).toHaveBeenCalledTimes(2);
  expect(result.current.paused).toBe(false);
  expect(result.current.data).toBe("healthy");
});

it("does not classify validation errors as provider outages", async () => {
  const read = vi.fn().mockRejectedValue(Object.assign(new Error("Invalid time range"), { status: 422 }));
  const { result, rerender } = renderHook(({ revision }) => useMonitorRead("query", true, revision, read, "provider"), { initialProps: { revision: 0 } });
  await settle();
  expect(result.current.paused).toBe(false);
  rerender({ revision: 1 });
  await settle();
  expect(read).toHaveBeenCalledTimes(2);
});

it("backs off repeated failures and stops recovery work when disabled or unmounted", async () => {
  const read = vi.fn().mockRejectedValue(new Error("offline"));
  const { rerender, unmount } = renderHook(({ enabled }) => useMonitorRead("query", enabled, 0, read, "provider"), { initialProps: { enabled: true } });
  await settle();
  await act(async () => { await vi.advanceTimersByTimeAsync(45_000); });
  expect(read).toHaveBeenCalledTimes(2);
  await act(async () => { await vi.advanceTimersByTimeAsync(89_999); });
  expect(read).toHaveBeenCalledTimes(2);
  rerender({ enabled: false });
  await act(async () => { await vi.advanceTimersByTimeAsync(300_000); });
  expect(read).toHaveBeenCalledTimes(2);
  rerender({ enabled: true });
  await settle();
  expect(read).toHaveBeenCalledTimes(3);
  unmount();
  await act(async () => { await vi.advanceTimersByTimeAsync(300_000); });
  expect(read).toHaveBeenCalledTimes(3);
});

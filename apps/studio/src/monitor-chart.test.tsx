import { act, render, screen, within } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import { MonitorResourceChart } from "./monitor-chart";
import type { StudioMetricSeries } from "./types";

const from = "2026-10-02T07:32:00Z";
const to = "2026-10-02T07:33:00Z";
const series: StudioMetricSeries[] = [
  {
    metric: "cpu_usage",
    unit: "cores",
    labels: { pod: "worker-1" },
    points: [{ timestamp: from, value: 0.5 }, { timestamp: to, value: 1 }],
  },
  {
    metric: "memory_usage",
    unit: "bytes",
    labels: { pod: "worker-1" },
    points: [
      { timestamp: from, value: 256 * 1024 ** 2 },
      { timestamp: to, value: 512 * 1024 ** 2 },
    ],
  },
];

it("converts memory bytes to MiB without mixing CPU series or changing input data", () => {
  render(
    <MonitorResourceChart metric="memory_usage" series={series}
      from={from} to={to} selectedTime="" />,
  );
  const chart = screen.getByRole("img", { name: "Pod memory in MiB" });
  expect(within(chart).getByText("512.0")).toBeInTheDocument();
  expect(chart.querySelectorAll("path")).toHaveLength(1);
  expect(chart.querySelector("path")?.getAttribute("d")).toBe("M48,65 L978,30");
  expect(screen.getByText("worker-1 · MiB")).toBeInTheDocument();
  expect(series[1].points[1].value).toBe(512 * 1024 ** 2);
});

it("keeps CPU visible when memory is absent", () => {
  render(
    <>
      <MonitorResourceChart metric="cpu_usage" series={series.slice(0, 1)}
        from={from} to={to} selectedTime="" />
      <MonitorResourceChart metric="memory_usage" series={series.slice(0, 1)}
        from={from} to={to} selectedTime="" />
    </>,
  );
  expect(screen.getByRole("img", { name: "Pod CPU in cores" })).toBeInTheDocument();
  expect(screen.getByText("No memory samples reported in this window.")).toBeInTheDocument();
});

it("preserves memory gaps and ignores invalid values", () => {
  const memory: StudioMetricSeries = {
    ...series[1],
    points: [
      { timestamp: from, value: 256 * 1024 ** 2 },
      { timestamp: "2026-10-02T07:32:20Z", value: null },
      { timestamp: "2026-10-02T07:32:30Z", value: Infinity },
      { timestamp: "invalid", value: 900 * 1024 ** 2 },
      { timestamp: to, value: 512 * 1024 ** 2 },
    ],
  };
  render(
    <MonitorResourceChart metric="memory_usage" series={[memory]}
      from={from} to={to} selectedTime="" />,
  );
  const chart = screen.getByRole("img", { name: "Pod memory in MiB" });
  expect(chart.querySelector("path")?.getAttribute("d")).toMatch(/^M48,65\s+M978,30$/);
  expect(within(chart).getByText("512.0")).toBeInTheDocument();
});

it("reduces time ticks after container resize and disconnects observation on unmount", () => {
  let resized!: ResizeObserverCallback;
  const disconnect = vi.fn();
  vi.stubGlobal("ResizeObserver", class { constructor(callback: ResizeObserverCallback) { resized = callback; } observe() {} disconnect = disconnect; });
  const view = render(<MonitorResourceChart metric="cpu_usage" series={series} from={from} to={to} selectedTime={from} />);
  const chart = screen.getByRole("img", { name: /Pod CPU/ });
  act(() => resized([{ contentRect: { width: 390 } } as ResizeObserverEntry], {} as ResizeObserver));
  expect(chart).toHaveAttribute("viewBox", "0 0 390 140");
  expect(chart.querySelectorAll('text[text-anchor="middle"]')).toHaveLength(2);
  view.unmount(); expect(disconnect).toHaveBeenCalledOnce(); vi.unstubAllGlobals();
});

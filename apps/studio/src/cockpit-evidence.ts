import type { StudioMetricSeries } from "./types";

export function isFresh(
  timestamp: string | null | undefined,
  now: number,
  seconds = 90,
) {
  const age = now - Date.parse(timestamp || "");
  return Number.isFinite(age) && age >= -30_000 && age <= seconds * 1000;
}

export function duration(seconds: number | null | undefined) {
  if (seconds == null || !Number.isFinite(seconds)) return "Not reported";
  if (seconds < 60) return `${Math.ceil(seconds)}s`;
  if (seconds < 3600) return `${Math.ceil(seconds / 60)}m`;
  if (seconds < 86400) return `${(seconds / 3600).toFixed(1)}h`;
  return `${(seconds / 86400).toFixed(1)}d`;
}

export function latestMetric(
  series: StudioMetricSeries[],
  metric: string,
  now: number,
) {
  const matches = series.filter((item) => item.metric === metric);
  // Current queries produce one aggregate; multiple unexpected series are ambiguous.
  if (matches.length !== 1) return null;
  const latest = [...matches[0].points].sort(
    (a, b) => Date.parse(b.timestamp) - Date.parse(a.timestamp),
  )[0];
  return latest &&
    latest.value !== null &&
    latest.value >= 0 &&
    Number.isFinite(latest.value) &&
    isFresh(latest.timestamp, now)
    ? latest.value
    : null;
}

import type { StudioControlPlaneEvent, StudioTaskSearchItem } from "./types";

export type MonitorLayout = "explorer" | "logs" | "investigation";
export type MonitorWindow = "task" | "15m" | "1h" | "24h" | "custom";
export type MonitorPreferences = {
  layout: MonitorLayout;
  pane: number;
  wrap: boolean;
  refresh: boolean;
};
const preferenceKey = "studio:monitor-preferences";
export const defaultPreferences: MonitorPreferences = {
  layout: "explorer",
  pane: 28,
  wrap: true,
  refresh: false,
};

export function parseLayout(value: unknown): MonitorLayout {
  return value === "logs" || value === "investigation" ? value : "explorer";
}

export function readMonitorPreferences(): MonitorPreferences {
  try {
    const saved = JSON.parse(localStorage.getItem(preferenceKey) || "null");
    return {
      layout: parseLayout(saved?.layout),
      pane:
        typeof saved?.pane === "number" && Number.isFinite(saved.pane)
          ? Math.min(40, Math.max(20, saved.pane))
          : 28,
      wrap: typeof saved?.wrap === "boolean" ? saved.wrap : true,
      refresh: typeof saved?.refresh === "boolean" ? saved.refresh : false,
    };
  } catch {
    return defaultPreferences;
  }
}

export function saveMonitorPreferences(value: MonitorPreferences) {
  try {
    localStorage.setItem(preferenceKey, JSON.stringify(value));
  } catch {
    /* Preferences are optional. */
  }
}

export function eventTime(event: StudioControlPlaneEvent) {
  return event.timestamp || event.ingested_at;
}

export function monitorTimeWindow(
  mode: MonitorWindow,
  now: number,
  task?: StudioTaskSearchItem | null,
  events: StudioControlPlaneEvent[] = [],
  selectedTime = "",
  from = "",
  to = "",
) {
  if (selectedTime && Number.isFinite(Date.parse(selectedTime))) {
    const time = Date.parse(selectedTime);
    return {
      from: new Date(time - 30_000).toISOString(),
      to: new Date(time + 30_000).toISOString(),
    };
  }
  if (mode === "custom") {
    return {
      from: from ? new Date(from).toISOString() : "",
      to: to ? new Date(to).toISOString() : "",
    };
  }
  if (mode === "task" && task) {
    const timestamps = [
      task.first_seen_at,
      task.last_seen_at,
      ...events.map(eventTime),
    ]
      .filter(
        (value): value is string =>
          Boolean(value) && Number.isFinite(Date.parse(value!)),
      )
      .map(Date.parse);
    const terminal =
      /^(completed?|succeeded|failed|error|cancelled|canceled|dead_lettered|timeout|timed_out)$/i.test(
        task.status || "",
      );
    if (timestamps.length)
      return {
        from: new Date(Math.min(...timestamps) - 120_000).toISOString(),
        to: new Date(
          (terminal ? Math.max(...timestamps) : now) + 120_000,
        ).toISOString(),
      };
  }
  const minutes = mode === "1h" ? 60 : mode === "24h" ? 1440 : 15;
  return {
    from: new Date(now - minutes * 60_000).toISOString(),
    to: new Date(now).toISOString(),
  };
}

export function validCustomWindow(from: string, to: string) {
  if (
    (!from && !to) ||
    [from, to].some((value) => value && !Number.isFinite(Date.parse(value)))
  )
    return false;
  return !from || !to || Date.parse(from) <= Date.parse(to);
}

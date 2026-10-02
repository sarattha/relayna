import {
  useEffect,
  useMemo,
  useRef,
  useState,
  type CSSProperties,
  type FormEvent,
} from "react";
import { useLocation, useSearchParams } from "react-router-dom";
import {
  fetchTaskDetail,
  fetchTaskEvents,
  fetchTaskLogs,
  fetchServiceLogs,
  fetchTaskMetrics,
  searchTasks,
} from "./api";
import { MonitorCpuChart } from "./monitor-chart";
import { useMonitorRead } from "./monitor-data";
import {
  eventTime,
  monitorTimeWindow,
  parseLayout,
  readMonitorPreferences,
  saveMonitorPreferences,
  validCustomWindow,
  type MonitorLayout,
  type MonitorWindow,
} from "./monitor-state";
import { Link } from "./scoped-link";
import {
  formatEventSummary,
  formatLogLevel,
  formatTimestamp,
  NoticeBanner,
  StudioIcon,
} from "./ui";
import type {
  ServiceRecord,
  StudioLogEntry,
  StudioTaskSearchItem,
} from "./types";

const modes: Array<{ value: MonitorLayout; label: string }> = [
  { value: "explorer", label: "Task explorer" },
  { value: "logs", label: "Logs focus" },
  { value: "investigation", label: "Investigation" },
];

export function useWorkspaceView() {
  const [params] = useSearchParams();
  const location = useLocation();
  return params.get("view") === "monitor"
    ? "monitor"
    : params.get("view") === "configure" ||
        location.hash === "#service-configure"
      ? "configure"
      : "overview";
}

export function WorkspaceNavigation({
  service = false,
}: {
  service?: boolean;
}) {
  const [params, setParams] = useSearchParams();
  const view = useWorkspaceView();
  return (
    <nav
      className="monitor-page-nav"
      aria-label={service ? "Service workspace" : "Task workspace"}
    >
      {[
        { value: "overview", label: "Overview" },
        { value: "monitor", label: "Monitor" },
        ...(service ? [{ value: "configure", label: "Configure" }] : []),
      ].map((item) => (
        <button
          type="button"
          key={item.value}
          aria-current={view === item.value ? "page" : undefined}
          onClick={() => {
            const next = new URLSearchParams(params);
            if (item.value === "overview") next.delete("view");
            else next.set("view", item.value);
            setParams(next);
          }}
        >
          {item.label}
        </button>
      ))}
    </nav>
  );
}

export function MonitorWorkspace({
  service,
  initialTaskId = "",
  active = true,
}: {
  service: ServiceRecord;
  initialTaskId?: string;
  active?: boolean;
}) {
  const [params, setParams] = useSearchParams();
  const [preferences, setPreferences] = useState(readMonitorPreferences);
  const layout = params.has("layout")
    ? parseLayout(params.get("layout"))
    : preferences.layout;
  const selectedTaskId = params.get("monitor_task") || initialTaskId;
  const scope =
    params.get("monitor_scope") === "service" || !selectedTaskId
      ? "service"
      : "task";
  const taskScope = scope === "task";
  const [expanded, setExpanded] = useState(false);
  const keyword = params.get("monitor_query") || "";
  const level = params.get("monitor_level") || "";
  const source = params.get("monitor_source") || "";
  const [queryDraft, setQueryDraft] = useState(keyword);
  const [taskDraft, setTaskDraft] = useState("");
  const [taskFilter, setTaskFilter] = useState("");
  const [status, setStatus] = useState("");
  const [cursors, setCursors] = useState<Array<string | null>>([null]);
  const [page, setPage] = useState(0);
  const [revision, setRevision] = useState(0);
  const [clock, setClock] = useState(Date.now);
  const [unread, setUnread] = useState(0);
  const [inspectedLog, setInspectedLog] = useState<StudioLogEntry | null>(null);
  const logScroll = useRef<HTMLDivElement>(null);
  const workspace = useRef<HTMLElement>(null);
  const expandButton = useRef<HTMLButtonElement>(null);
  const logIdentity = useRef("");
  const readsPending = useRef(false);
  const timeValue = params.get("monitor_window");
  const windowMode: MonitorWindow =
    timeValue === "1h" ||
    timeValue === "24h" ||
    timeValue === "custom" ||
    timeValue === "15m"
      ? timeValue
      : taskScope
        ? "task"
        : "15m";
  const selectedTime = params.get("monitor_event") || "";
  const customFrom = params.get("monitor_from") || "";
  const customTo = params.get("monitor_to") || "";
  const [fromDraft, setFromDraft] = useState(customFrom);
  const [toDraft, setToDraft] = useState(customTo);
  const [rangeError, setRangeError] = useState("");

  function updateParams(values: Record<string, string | null>, replace = true) {
    setParams(
      (old) => {
        const next = new URLSearchParams(old);
        for (const [key, value] of Object.entries(values)) {
          if (value) next.set(key, value);
          else next.delete(key);
        }
        return next;
      },
      { replace },
    );
  }

  useEffect(() => {
    if (active) document.body.classList.add("studio-monitor-active");
    return () => {
      if (active) document.body.classList.remove("studio-monitor-active");
    };
  }, [active]);
  useEffect(() => {
    if (!active) setExpanded(false);
  }, [active]);
  useEffect(() => {
    saveMonitorPreferences(preferences);
  }, [preferences]);
  useEffect(() => {
    setQueryDraft(keyword);
  }, [keyword]);
  useEffect(() => {
    setFromDraft(customFrom);
    setToDraft(customTo);
  }, [customFrom, customTo]);
  useEffect(() => {
    setPage(0);
    setCursors([null]);
  }, [service.service_id, taskFilter, status]);
  useEffect(() => {
    if (!active || !preferences.refresh) return;
    const timer = window.setInterval(() => {
      if (document.visibilityState === "visible" && !readsPending.current) {
        setClock(Date.now());
        setRevision((old) => old + 1);
      }
    }, 5000);
    return () => window.clearInterval(timer);
  }, [active, preferences.refresh]);
  useEffect(() => {
    if (!expanded) return;
    const previousFocus = document.activeElement as HTMLElement | null;
    expandButton.current?.focus();
    function escape(event: KeyboardEvent) {
      if (event.key === "Escape") setExpanded(false);
      if (event.key === "Tab") {
        const controls = [
          ...(workspace.current?.querySelectorAll<HTMLElement>(
            'button:not(:disabled), input, select, a[href], [tabindex="0"], summary',
          ) || []),
        ].filter((item) => !item.closest("[hidden]"));
        const first = controls[0];
        const last = controls[controls.length - 1];
        if (event.shiftKey && document.activeElement === first) {
          event.preventDefault();
          last?.focus();
        } else if (!event.shiftKey && document.activeElement === last) {
          event.preventDefault();
          first?.focus();
        }
      }
    }
    window.addEventListener("keydown", escape);
    return () => {
      window.removeEventListener("keydown", escape);
      previousFocus?.focus();
    };
  }, [expanded]);

  const taskKey = `${service.service_id}:${selectedTaskId}`;
  const details = useMonitorRead(
    taskKey,
    active && Boolean(selectedTaskId),
    revision,
    () => fetchTaskDetail(service.service_id, selectedTaskId, "none"),
  );
  const events = useMonitorRead(taskKey, active && taskScope, revision, () =>
    fetchTaskEvents(service.service_id, selectedTaskId),
  );
  const listKey = JSON.stringify([
    service.service_id,
    taskFilter,
    status,
    cursors[page],
  ]);
  // Page at most 50 tasks; the reader is independent of list pagination.
  const tasks = useMonitorRead(
    listKey,
    active && layout === "explorer",
    revision,
    () =>
      searchTasks({
        service_id: service.service_id,
        task_id: taskFilter,
        status,
        cursor: cursors[page],
        limit: 50,
      }),
  );
  const task = useMemo<StudioTaskSearchItem | null>(() => {
    const detail = details.data;
    if (!detail)
      return (
        tasks.data?.items.find((item) => item.task_id === selectedTaskId) ||
        null
      );
    const event = detail.latest_status?.event || {};
    return {
      service_id: service.service_id,
      service_name: service.name,
      environment: service.environment,
      task_id: selectedTaskId,
      correlation_id: detail.task_ref.correlation_id,
      status: String(event.status || "unknown"),
      stage: String(event.stage || ""),
      first_seen_at: String(
        event.created_at || event.started_at || event.timestamp || "",
      ),
      last_seen_at: String(
        event.completed_at || event.updated_at || event.timestamp || "",
      ),
      detail_path: "",
    };
  }, [details.data, tasks.data, selectedTaskId, service.service_id]);
  const customValid =
    windowMode !== "custom" || validCustomWindow(customFrom, customTo);
  const windowBounds = useMemo(
    () =>
      customValid
        ? monitorTimeWindow(
            windowMode,
            clock,
            taskScope ? task : null,
            taskScope ? events.data?.items || [] : [],
            selectedTime,
            customFrom,
            customTo,
          )
        : { from: "", to: "" },
    [
      windowMode,
      clock,
      task,
      taskScope,
      events.data,
      selectedTime,
      customFrom,
      customTo,
      customValid,
    ],
  );
  const logKey = JSON.stringify([
    service.service_id,
    scope,
    selectedTaskId,
    keyword,
    level,
    source,
    windowBounds,
  ]);
  const logsReady =
    active &&
    Boolean(service.log_config) &&
    customValid &&
    (!taskScope ||
      (!(details.loading && !details.data) &&
        !(events.loading && !events.data)));
  const logs = useMonitorRead(logKey, logsReady, revision, () => {
    const query = {
      query: keyword,
      level,
      source,
      limit: 200,
      ...windowBounds,
    };
    return taskScope
      ? fetchTaskLogs(service.service_id, selectedTaskId, query)
      : fetchServiceLogs(service.service_id, query);
  });
  const metrics = useMonitorRead(
    JSON.stringify([taskKey, windowBounds]),
    active &&
      layout === "investigation" &&
      taskScope &&
      Boolean(service.metrics_config) &&
      customValid &&
      !(events.loading && !events.data) &&
      !(details.loading && !details.data),
    revision,
    () =>
      fetchTaskMetrics(service.service_id, selectedTaskId, {
        ...windowBounds,
        groups: ["cpu_usage"],
      }),
  );
  // Slow providers must finish before the next automatic refresh starts.
  readsPending.current =
    (Boolean(selectedTaskId) && details.loading) ||
    (taskScope && events.loading) ||
    (layout === "explorer" && tasks.loading) ||
    (logsReady && logs.loading) ||
    (layout === "investigation" &&
      taskScope &&
      Boolean(service.metrics_config) &&
      customValid &&
      metrics.loading);
  const items = useMemo(
    () =>
      (logs.data?.items || [])
        .slice(0, 200)
        .sort((a, b) => Date.parse(a.timestamp) - Date.parse(b.timestamp)),
    [logs.data],
  );
  const timeline = useMemo(
    () =>
      [...(events.data?.items || [])].sort(
        (a, b) => Date.parse(eventTime(a)) - Date.parse(eventTime(b)),
      ),
    [events.data],
  );
  useEffect(() => {
    setUnread(0);
    setInspectedLog(null);
    logIdentity.current = "";
    if (logScroll.current) logScroll.current.scrollTop = 0;
  }, [
    scope,
    selectedTaskId,
    keyword,
    level,
    source,
    windowMode,
    selectedTime,
    customFrom,
    customTo,
  ]);
  useEffect(() => {
    const latest = items[items.length - 1];
    const identity = latest
      ? `${latest.timestamp}:${latest.source}:${latest.message}`
      : "";
    if (logIdentity.current && identity && identity !== logIdentity.current)
      setUnread((old) => old + 1);
    logIdentity.current = identity;
  }, [items]);

  function chooseTask(id: string) {
    setExpanded(false);
    updateParams(
      { monitor_task: id, monitor_scope: "task", monitor_event: null },
      false,
    );
  }
  function chooseLayout(value: MonitorLayout) {
    setExpanded(false);
    setPreferences((old) => ({ ...old, layout: value }));
    updateParams({ layout: value }, false);
  }
  function refresh() {
    setClock(Date.now());
    setRevision((old) => old + 1);
  }
  function applyRange(event: FormEvent) {
    event.preventDefault();
    if (!validCustomWindow(fromDraft, toDraft)) {
      setRangeError("Enter a valid range with From before To.");
      return;
    }
    setRangeError("");
    updateParams({
      monitor_from: fromDraft,
      monitor_to: toDraft,
      monitor_event: null,
    });
  }

  return (
    <section
      ref={workspace}
      role={expanded ? "dialog" : undefined}
      aria-modal={expanded ? true : undefined}
      className={`monitor-workspace${expanded ? " monitor-workspace--expanded" : ""}`}
      aria-label="Monitoring workspace"
      style={{ "--monitor-pane": `${preferences.pane}%` } as CSSProperties}
    >
      <div className="monitor-layout-bar">
        <div className="monitor-layout-control">
          <span>Layout</span>
          <div
            className="monitor-segments"
            role="group"
            aria-label="Monitoring layout"
          >
            {modes.map((mode) => (
              <button
                type="button"
                key={mode.value}
                aria-pressed={layout === mode.value}
                onClick={() => chooseLayout(mode.value)}
              >
                {mode.label}
              </button>
            ))}
          </div>
        </div>
        <label className="monitor-inline-field">
          Scope{" "}
          <select
            value={scope}
            onChange={(event) =>
              updateParams({
                monitor_scope: event.target.value,
                monitor_event: null,
              })
            }
          >
            <option value="service">Entire service</option>
            <option value="task" disabled={!selectedTaskId}>
              Selected task
            </option>
          </select>
        </label>
      </div>
      <div className="monitor-identity">
        <strong>{taskScope ? selectedTaskId : service.name}</strong>
        {taskScope && (
          <>
            <span
              className={`monitor-status monitor-status--${/fail|error|timeout/i.test(task?.status || "") ? "failed" : "neutral"}`}
            >
              {task?.status || "Loading status…"}
            </span>
            <span>
              Stage <b>{task?.stage || "—"}</b>
            </span>
            <span>
              Correlation <b>{task?.correlation_id || "—"}</b>
            </span>
            <Link
              to={`/tasks/${encodeURIComponent(service.service_id)}/${encodeURIComponent(selectedTaskId)}`}
            >
              Open task overview
            </Link>
          </>
        )}
        {!taskScope && (
          <span>
            {service.environment} · Service logs may include traffic from
            multiple tasks.
          </span>
        )}
      </div>
      {details.error && taskScope && (
        <NoticeBanner tone="error">
          Task status unavailable: {details.error}
        </NoticeBanner>
      )}
      {layout === "investigation" && !taskScope && (
        <NoticeBanner>
          Select a task in Task explorer, then choose Selected task to
          investigate its timeline.
        </NoticeBanner>
      )}
      <div className="monitor-window-bar">
        <label className="monitor-inline-field">
          Time window{" "}
          <select
            aria-label="Monitor time window"
            value={windowMode}
            onChange={(event) => {
              setClock(Date.now());
              updateParams({
                monitor_window: event.target.value,
                monitor_event: null,
              });
            }}
          >
            {taskScope && <option value="task">Task lifetime</option>}
            <option value="15m">Last 15 minutes</option>
            <option value="1h">Last hour</option>
            <option value="24h">Last 24 hours</option>
            <option value="custom">Custom range</option>
          </select>
        </label>
        {selectedTime ? (
          <>
            <span className="monitor-event-focus">
              Selected event: {formatTimestamp(selectedTime)} · ±30s
            </span>
            <button
              type="button"
              onClick={() => updateParams({ monitor_event: null })}
            >
              Reset window
            </button>
          </>
        ) : (
          <span className="monitor-window-caption">
            {formatTimestamp(windowBounds.from)} –{" "}
            {formatTimestamp(windowBounds.to)}
          </span>
        )}
        {layout !== "logs" && (
          <label className="monitor-pane-control">
            Pane width{" "}
            <input
              type="range"
              aria-label="Pane width"
              min="20"
              max="40"
              value={preferences.pane}
              onChange={(event) =>
                setPreferences((old) => ({
                  ...old,
                  pane: Number(event.target.value),
                }))
              }
            />
          </label>
        )}
      </div>
      {windowMode === "custom" && (
        <form className="monitor-custom-range" onSubmit={applyRange}>
          <label>
            From
            <input
              type="datetime-local"
              aria-label="Monitor from"
              value={fromDraft}
              onChange={(event) => setFromDraft(event.target.value)}
            />
          </label>
          <label>
            To
            <input
              type="datetime-local"
              aria-label="Monitor to"
              value={toDraft}
              onChange={(event) => setToDraft(event.target.value)}
            />
          </label>
          <button type="submit">Apply range</button>
          <span>{Intl.DateTimeFormat().resolvedOptions().timeZone}</span>
        </form>
      )}
      {(rangeError || !customValid) && (
        <NoticeBanner tone="error">
          {rangeError || "Choose and apply a valid custom range."}
        </NoticeBanner>
      )}
      <div className={`monitor-canvas monitor-canvas--${layout}`}>
        <aside className="monitor-sidebar" hidden={layout !== "explorer"}>
          <header>
            <h2>Tasks</h2>
            <button type="button" onClick={refresh} aria-label="Refresh tasks">
              <StudioIcon name="refresh" />
            </button>
          </header>
          <form
            className="monitor-task-search"
            onSubmit={(event) => {
              event.preventDefault();
              setTaskFilter(taskDraft.trim());
              setPage(0);
              setCursors([null]);
            }}
          >
            <label
              className="studio-sr-only"
              htmlFor={`monitor-tasks-${service.service_id}`}
            >
              Find service task
            </label>
            <input
              id={`monitor-tasks-${service.service_id}`}
              placeholder="Task ID"
              value={taskDraft}
              onChange={(event) => setTaskDraft(event.target.value)}
            />
            <button type="submit">Find</button>
          </form>
          <label className="monitor-inline-field">
            Status{" "}
            <select
              aria-label="Task list status"
              value={status}
              onChange={(event) => {
                setStatus(event.target.value);
                setPage(0);
                setCursors([null]);
              }}
            >
              <option value="">All statuses</option>
              <option value="running">Running</option>
              <option value="failed">Failed</option>
              <option value="completed">Completed</option>
              <option value="queued">Queued</option>
            </select>
          </label>
          <div className="monitor-pagination">
            <button
              type="button"
              disabled={!page || tasks.loading}
              onClick={() => setPage((old) => old - 1)}
            >
              Previous
            </button>
            <span>Page {page + 1}</span>
            <button
              type="button"
              disabled={!tasks.data?.next_cursor || tasks.loading}
              onClick={() => {
                const next = tasks.data?.next_cursor;
                if (next) {
                  setCursors((old) => [...old.slice(0, page + 1), next]);
                  setPage((old) => old + 1);
                }
              }}
            >
              Next
            </button>
          </div>
          {tasks.error && (
            <NoticeBanner tone="error">{tasks.error}</NoticeBanner>
          )}
          {tasks.loading && <p role="status">Loading tasks…</p>}
          <div className="monitor-task-list" aria-label="Service tasks">
            {tasks.data?.items.map((item) => (
              <button
                type="button"
                className="monitor-task-row"
                key={`${item.service_id}:${item.task_id}`}
                aria-pressed={item.task_id === selectedTaskId && taskScope}
                onClick={() => chooseTask(item.task_id)}
              >
                <span>
                  <strong>{item.task_id}</strong>
                  <span
                    className={`monitor-status monitor-status--${/fail|error|timeout/i.test(item.status || "") ? "failed" : "neutral"}`}
                  >
                    {item.status || "Unknown"}
                  </span>
                </span>
                <small>{item.stage || "No stage"}</small>
                <time>
                  {formatTimestamp(item.last_seen_at || item.first_seen_at)}
                </time>
              </button>
            ))}
            {!tasks.loading && !tasks.error && !tasks.data?.items.length && (
              <p>No tasks match these filters.</p>
            )}
          </div>
        </aside>
        <aside
          className="monitor-sidebar monitor-timeline"
          hidden={layout !== "investigation"}
        >
          <h2>Task timeline</h2>
          {events.loading && <p role="status">Loading timeline…</p>}
          {events.error && (
            <NoticeBanner tone="error">{events.error}</NoticeBanner>
          )}
          {!timeline.length && !events.loading && (
            <p>
              {taskScope
                ? "No retained events for this task."
                : "Select a task to see events."}
            </p>
          )}
          <div className="monitor-event-list">
            {timeline.map((event) => (
              <button
                type="button"
                className="monitor-event"
                key={event.dedupe_key}
                aria-pressed={selectedTime === eventTime(event)}
                onClick={() =>
                  updateParams({ monitor_event: eventTime(event) })
                }
              >
                <time dateTime={eventTime(event)}>
                  {new Date(eventTime(event)).toLocaleTimeString([], {
                    hour12: false,
                  })}
                </time>
                <strong>{event.event_type.replace(/_/g, " ")}</strong>
                <span>
                  {formatEventSummary(event)} ·{" "}
                  {String(event.payload.stage || "")}
                </span>
                {typeof event.payload.reason === "string" && (
                  <span>{event.payload.reason}</span>
                )}
                {selectedTime === eventTime(event) && (
                  <small>Selected event · ±30 seconds</small>
                )}
              </button>
            ))}
          </div>
        </aside>
        <div className="monitor-log-panel">
          <header>
            <h2>
              {selectedTime
                ? "Logs around selected event"
                : taskScope
                  ? "Task logs"
                  : "Service logs"}
            </h2>
            <span className="monitor-freshness" role="status">
              {logs.updatedAt
                ? `Fetched ${formatTimestamp(logs.updatedAt)}`
                : "Not fetched"}
              {logs.loading
                ? " · Refreshing…"
                : preferences.refresh
                  ? " · Auto-refresh 5s"
                  : " · Paused"}
            </span>
          </header>
          <form
            className="monitor-log-toolbar"
            onSubmit={(event) => {
              event.preventDefault();
              updateParams({ monitor_query: queryDraft.trim() });
            }}
          >
            <label
              className="studio-sr-only"
              htmlFor={`monitor-log-search-${service.service_id}`}
            >
              Search monitor logs
            </label>
            <input
              id={`monitor-log-search-${service.service_id}`}
              placeholder="Search logs…"
              value={queryDraft}
              onChange={(event) => setQueryDraft(event.target.value)}
            />
            <button type="submit">Search</button>
            <label
              className="studio-sr-only"
              htmlFor={`monitor-level-${service.service_id}`}
            >
              Log level
            </label>
            <select
              id={`monitor-level-${service.service_id}`}
              value={level}
              onChange={(event) =>
                updateParams({ monitor_level: event.target.value })
              }
            >
              <option value="">All levels</option>
              {["DEBUG", "INFO", "WARN", "ERROR", "CRITICAL"].map((value) => (
                <option key={value}>{value}</option>
              ))}
            </select>
            <label
              className="studio-sr-only"
              htmlFor={`monitor-source-${service.service_id}`}
            >
              Log source
            </label>
            <input
              id={`monitor-source-${service.service_id}`}
              placeholder="Source"
              value={source}
              onChange={(event) =>
                updateParams({ monitor_source: event.target.value })
              }
            />
            <button type="button" onClick={refresh} disabled={logs.loading}>
              <StudioIcon name="refresh" />
              Refresh
            </button>
            <button
              type="button"
              aria-pressed={preferences.refresh}
              onClick={() =>
                setPreferences((old) => ({ ...old, refresh: !old.refresh }))
              }
            >
              {preferences.refresh ? "Pause" : "Auto-refresh"}
            </button>
            <button
              type="button"
              aria-pressed={preferences.wrap}
              onClick={() =>
                setPreferences((old) => ({ ...old, wrap: !old.wrap }))
              }
            >
              Wrap
            </button>
            <button
              ref={expandButton}
              type="button"
              aria-pressed={expanded}
              onClick={() => setExpanded((old) => !old)}
            >
              {expanded ? "Exit expand" : "Expand"}
            </button>
          </form>
          {!service.log_config && (
            <p className="monitor-empty">
              No log provider configured.{" "}
              <Link
                to={`/services/${encodeURIComponent(service.service_id)}?view=configure#service-configure`}
              >
                Configure telemetry
              </Link>
              .
            </p>
          )}
          {logs.error && (
            <NoticeBanner tone="error">
              Refresh failed; displayed logs may be stale. {logs.error}
            </NoticeBanner>
          )}
          {unread > 0 && (
            <button
              type="button"
              className="monitor-new-entries"
              onClick={() => {
                setUnread(0);
                if (logScroll.current)
                  logScroll.current.scrollTop = logScroll.current.scrollHeight;
              }}
            >
              New log results · Jump to latest
            </button>
          )}
          <div
            className={`monitor-log-scroll${preferences.wrap ? " monitor-log-scroll--wrap" : ""}`}
            ref={logScroll}
            tabIndex={0}
            aria-label="Log entries"
          >
            <table className="monitor-log-table">
              <thead>
                <tr>
                  <th scope="col">Time</th>
                  <th scope="col">Level</th>
                  <th scope="col">Source</th>
                  <th scope="col">Message</th>
                </tr>
              </thead>
              <tbody>
                {items.map((item, index) => (
                  <MonitorLogRow
                    key={`${item.timestamp}:${item.source}:${index}`}
                    item={item}
                    expanded={
                      inspectedLog?.timestamp === item.timestamp &&
                      inspectedLog?.message === item.message &&
                      inspectedLog?.source === item.source
                    }
                    onInspect={() =>
                      setInspectedLog((old) =>
                        old?.timestamp === item.timestamp &&
                        old?.message === item.message &&
                        old?.source === item.source
                          ? null
                          : item,
                      )
                    }
                  />
                ))}
              </tbody>
            </table>
            {service.log_config &&
              !logs.loading &&
              !logs.error &&
              customValid &&
              !items.length && (
                <p className="monitor-empty">
                  No logs matched this scope, time window, and filters.
                </p>
              )}
          </div>
          {inspectedLog && (
            <MonitorLogDetail
              key={`${inspectedLog.timestamp}:${inspectedLog.source}:${inspectedLog.message}`}
              item={inspectedLog}
              onClose={() => setInspectedLog(null)}
            />
          )}
          <footer>
            {items.length} entries shown · Latest 200 matching entries ·{" "}
            {Intl.DateTimeFormat().resolvedOptions().timeZone}
          </footer>
        </div>
      </div>
      {layout === "investigation" && taskScope && (
        <section
          className="monitor-metrics"
          aria-label="Metrics in shared time window"
        >
          <header>
            <h2>Pod CPU</h2>
            <span>
              {formatTimestamp(windowBounds.from)} –{" "}
              {formatTimestamp(windowBounds.to)}
            </span>
          </header>
          {!service.metrics_config ? (
            <p>No metrics provider configured.</p>
          ) : metrics.error ? (
            <NoticeBanner tone="error">
              Metrics unavailable: {metrics.error}
            </NoticeBanner>
          ) : metrics.loading ? (
            <p role="status">Loading metrics…</p>
          ) : metrics.data?.series.length ? (
            <MonitorCpuChart
              series={metrics.data.series}
              from={windowBounds.from}
              to={windowBounds.to}
              selectedTime={selectedTime}
            />
          ) : (
            <p>No CPU samples reported in this window.</p>
          )}
          {metrics.data?.warnings.map((warning) => (
            <p key={warning}>{warning}</p>
          ))}
        </section>
      )}
    </section>
  );
}

function MonitorLogRow({
  item,
  expanded,
  onInspect,
}: {
  item: StudioLogEntry;
  expanded: boolean;
  onInspect: () => void;
}) {
  const severity = formatLogLevel(item.level);
  const kind = /ERROR|CRITICAL|FATAL/i.test(severity)
    ? "error"
    : /WARN/i.test(severity)
      ? "warning"
      : "neutral";
  const rich =
    item.message.includes("\n") ||
    Object.keys(item.fields || {}).length > 0 ||
    item.message.length > 200;
  return (
    <tr className={`monitor-log-row monitor-log-row--${kind}`}>
      <td>
        <time dateTime={item.timestamp}>
          {new Date(item.timestamp).toLocaleTimeString([], { hour12: false })}
        </time>
      </td>
      <td>
        <span className={`monitor-level monitor-level--${kind}`}>
          {severity}
        </span>
      </td>
      <td>{item.source}</td>
      <td>
        <span className="monitor-mobile-source">{item.source}</span>
        {rich ? (
          <button
            type="button"
            className="monitor-log-summary"
            aria-expanded={expanded}
            aria-controls="monitor-log-detail"
            onClick={onInspect}
          >
            {item.message.split("\n")[0].slice(0, 200)}
          </button>
        ) : (
          item.message
        )}
      </td>
    </tr>
  );
}

function MonitorLogDetail({
  item,
  onClose,
}: {
  item: StudioLogEntry;
  onClose: () => void;
}) {
  const [copied, setCopied] = useState("");
  async function copy() {
    try {
      await navigator.clipboard.writeText(item.message);
      setCopied("Copied");
    } catch {
      setCopied("Copy unavailable; select the message instead.");
    }
  }
  return (
    <section
      id="monitor-log-detail"
      className={`monitor-log-detail${/error|critical|fatal/i.test(item.level || "") ? " monitor-log-detail--error" : ""}`}
      aria-label="Selected log details"
    >
      <header>
        <strong>{formatLogLevel(item.level)} details</strong>
        <time>{formatTimestamp(item.timestamp)}</time>
        <button type="button" onClick={() => void copy()}>
          Copy message
        </button>
        <button type="button" onClick={onClose} aria-label="Close log details">
          Close
        </button>
      </header>
      <div>
        <pre>{item.message}</pre>
        {Object.keys(item.fields || {}).length > 0 && (
          <pre>{JSON.stringify(item.fields, null, 2)}</pre>
        )}
      </div>
      <span role="status">{copied}</span>
    </section>
  );
}

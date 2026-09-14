import { useSearchParams } from "react-router-dom";
import { scopedResults } from "../scoped-results";
import { useStudioServices } from "../services-context";
import type { FormEvent } from "react";
import { useEffect, useRef, useState } from "react";

import {
  deleteFailedTask,
  fetchFailedTaskEmailSettings,
  fetchFailedTaskDetail,
  fetchFailedTasks,
  markFailedTaskInvestigated,
  markFailedTaskUninvestigated,
  retryFailedTask,
  updateFailedTaskEmailSettings,
} from "../api";
import { useStudioAuth } from "../auth-context";
import {
  InlineCodeBox,
  NoticeBanner,
  SectionCard,
  StudioIcon,
  TaskRefLink,
  formatTimestamp,
  inputStyle,
  mutedTextStyle,
  primaryButtonStyle,
  secondaryButtonStyle,
} from "../ui";
import type { FailedTaskDetail, FailedTaskEmailSettings, FailedTaskQueryState, FailedTaskSummary } from "../types";

const initialQuery: FailedTaskQueryState = {
  service_id: "",
  queue_name: "",
  dlq_name: "",
  error_type: "",
  status: "",
  task_id: "",
  worker_id: "",
  investigation_status: "unreviewed",
  failed_from: "",
  failed_to: "",
  limit: "50",
  cursor: null,
};

export function formatBatchWait(seconds: number) {
  if (seconds <= 0) {
    return "0 sec";
  }
  if (seconds < 60) {
    return `${seconds} sec`;
  }
  if (seconds < 3600) {
    return `${Math.round(seconds / 60)} min`;
  }
  if (seconds < 86400) {
    return `${Math.round(seconds / 3600)} hr`;
  }
  return `${Math.round(seconds / 86400)} days`;
}

export function FailedTasksPage() {
  const { isAdmin, user } = useStudioAuth();
  const services = useStudioServices();
  const [params] = useSearchParams();
  const environment = params.get("environment") || "";
  const [checkedFailures, setCheckedFailures] = useState<string[]>([]);
  const [pending, setPending] = useState(false);
  const mutationLock = useRef(false);
  const listVersion = useRef(0);
  const detailVersion = useRef(0);
  const [readErrors, setReadErrors] = useState<string[]>([]);
  const [unavailable, setUnavailable] = useState(false);
  const [query, setQuery] = useState<FailedTaskQueryState>(initialQuery);
  const [items, setItems] = useState<FailedTaskSummary[]>([]);
  const [selected, setSelected] = useState<FailedTaskDetail | null>(null);
  const [emailSettings, setEmailSettings] = useState<FailedTaskEmailSettings | null>(null);
  const [emailSettingsLoading, setEmailSettingsLoading] = useState(true);
  const [emailBatchWaitSeconds, setEmailBatchWaitSeconds] = useState("0");
  const [loading, setLoading] = useState(true);
  const [detailLoading, setDetailLoading] = useState(false);
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [investigationNote, setInvestigationNote] = useState("");
  const operator = user.email;
  const [retryNote, setRetryNote] = useState("");
  const [retryTargetQueue, setRetryTargetQueue] = useState("");
  const [overridePayload, setOverridePayload] = useState("");

  useEffect(() => {
    if (environment && services.loading) return;
    const scopedQuery = { ...initialQuery };
    setQuery(scopedQuery); setSelected(null);
    void load(scopedQuery);
    void loadEmailSettings();
    return () => { listVersion.current++; detailVersion.current++; };
  }, [environment, environment ? services.loading : false]);

  async function loadEmailSettings() {
    setEmailSettingsLoading(true);
    try {
      const settings = await fetchFailedTaskEmailSettings();
      setEmailSettings(settings);
      setEmailBatchWaitSeconds(String(settings.batch_wait_seconds));
    } catch (fetchError) {
      setNotice(fetchError instanceof Error ? fetchError.message : "Unable to load email notification settings.");
    } finally {
      setEmailSettingsLoading(false);
    }
  }

  async function load(nextQuery: FailedTaskQueryState, append = false) {
    const version = ++listVersion.current;
    setLoading(true);
    setError(null);
    try {
      const ids = services.services.filter((service) => service.status !== "disabled" && service.environment === environment && (!nextQuery.service_id || service.service_id === nextQuery.service_id)).map((service) => service.service_id);
      const payload = environment ? await scopedResults(ids, nextQuery.cursor, Number(nextQuery.limit) || 50, (serviceId, cursor) => fetchFailedTasks({ ...nextQuery, service_id: serviceId, cursor, limit: "50" }), (item) => `${item.failed_at || ""}|${item.failure_id}`) : await fetchFailedTasks(nextQuery);
      if (version !== listVersion.current) return;
      if (!append) setCheckedFailures([]);
      const visible = payload.items.filter((item) => !environment || services.servicesById.get(item.service_id)?.environment === environment);
      setReadErrors((payload.errors || []).map((item) => `${item.service_id || "Service"}: ${item.detail}`));
      setUnavailable(Boolean(payload.errors?.length && !payload.scanned_services?.length));
      setItems((current) => (append ? [...current, ...visible] : visible));
      setNextCursor(payload.next_cursor || null);

    } catch (fetchError) {
      if (version !== listVersion.current) return;
      setUnavailable(true);
      setError(fetchError instanceof Error ? fetchError.message : "Unable to load failed tasks.");
    } finally {
      if (version === listVersion.current) setLoading(false);
    }
  }

  async function openDetail(item: FailedTaskSummary) {
    const version = ++detailVersion.current;
    setDetailLoading(true);
    setSelected(null);
    setError(null);
    try {
      const detail = await fetchFailedTaskDetail(item.service_id, item.failure_id);
      if (version !== detailVersion.current) return;
      setSelected(detail);
      setInvestigationNote(detail.investigation_note || "");
      setRetryNote(detail.retry_note || "");
      setRetryTargetQueue(detail.queue_name || "");
      setOverridePayload("");
    } catch (fetchError) {
      setError(fetchError instanceof Error ? fetchError.message : "Unable to load failed task detail.");
    } finally {
      if (version === detailVersion.current) setDetailLoading(false);
    }
  }

  async function submitFilters(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const nextQuery = { ...query, cursor: null };
    setQuery(nextQuery);
    await load(nextQuery);
  }

  function supports(action: string) {
    const routes = selected && services.servicesById.get(selected.service_id)?.capabilities?.supported_routes;
    return Array.isArray(routes) && routes.includes(`failed_tasks.${action}`);
  }

  async function mutate(action: () => Promise<void>) {
    if (mutationLock.current) return;
    mutationLock.current = true;
    setPending(true); setError(null);
    try { await action(); }
    catch (failure) { setError(failure instanceof Error ? failure.message : "Operation failed. Please retry."); }
    finally { mutationLock.current = false; setPending(false); }
  }

  async function reviewSelected() {
    const targets = items.filter((item) => checkedFailures.includes(`${item.service_id}:${item.failure_id}`)).slice(0, 20);
    if (!targets.length || !window.confirm(`Mark ${targets.length} selected failures investigated as ${operator}?`)) return;
    await mutate(async () => {
      const failures: string[] = [];
      for (const item of targets) {
        try { await markFailedTaskInvestigated(item.service_id, item.failure_id, { investigated_by: operator, note: investigationNote }); }
        catch (error) { failures.push(`${item.failure_id}: ${error instanceof Error ? error.message : "Operation failed"}`); }
      }
      await load({ ...query, cursor: null });
      if (failures.length) setError(`${targets.length - failures.length} reviewed. ${failures.join("; ")}`);
      else setNotice(`Reviewed ${targets.length} failures.`);
    });
  }

  async function investigate() {
    if (!selected) {
      return;
    }
    const detail = await markFailedTaskInvestigated(selected.service_id, selected.failure_id, {
      investigated_by: operator,
      note: investigationNote,
    });
    setSelected(detail);
    setQuery((current) => ({ ...current, cursor: null }));
    await load({ ...query, cursor: null });
  }

  async function uninvestigate() {
    if (!selected) {
      return;
    }
    const detail = await markFailedTaskUninvestigated(selected.service_id, selected.failure_id);
    setSelected(detail);
    setQuery((current) => ({ ...current, cursor: null }));
    await load({ ...query, cursor: null });
  }

  async function retry() {
    if (!selected) {
      return;
    }
    let parsedOverride: unknown;
    try {
      parsedOverride = overridePayload.trim() ? JSON.parse(overridePayload) : undefined;
    } catch {
      setError("Override payload must be valid JSON.");
      return;
    }
    if (!window.confirm(`Retry ${selected.failure_id} on ${selected.service_name || selected.service_id} (${services.servicesById.get(selected.service_id)?.environment || "unknown environment"})? Target queue: ${retryTargetQueue || selected.queue_name}. ${parsedOverride === undefined ? "Use retained payload." : "Replace payload with the JSON override."}`)) {
      return;
    }
    setError(null);
    try {
      await retryFailedTask(selected.service_id, selected.failure_id, {
        target_queue: retryTargetQueue || selected.queue_name,
        override_payload: parsedOverride,
        retried_by: operator,
        note: retryNote,
      });
      const detail = await fetchFailedTaskDetail(selected.service_id, selected.failure_id);
      setSelected(detail);
      setQuery((current) => ({ ...current, cursor: null }));
    await load({ ...query, cursor: null });
    } catch (retryError) {
      setError(retryError instanceof Error ? retryError.message : "Unable to retry failed task.");
    }
  }

  async function remove() {
    if (!selected || !window.confirm(`Delete snapshot ${selected.failure_id} from ${selected.service_id}? This removes its retained failure evidence.`)) {
      return;
    }
    await deleteFailedTask(selected.service_id, selected.failure_id);
    setSelected(null);
    setQuery((current) => ({ ...current, cursor: null }));
    await load({ ...query, cursor: null });
  }

  function downloadSelected() {
    if (!selected) {
      return;
    }
    const blob = new Blob([JSON.stringify(selected, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = `${selected.failure_id}.json`;
    anchor.click();
    URL.revokeObjectURL(url);
  }

  async function copySelected(value: unknown) {
    try {
      if (!navigator.clipboard) throw new Error("Clipboard unavailable. Use Download JSON instead.");
      await navigator.clipboard.writeText(JSON.stringify(value, null, 2));
      setNotice("Copied failed-task data.");
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : "Unable to copy. Use Download JSON instead.");
    }
  }

  async function updateEmailSettings(payload: { enabled?: boolean; batch_wait_seconds?: number }) {
    setError(null);
    try {
      const settings = await updateFailedTaskEmailSettings(payload);
      setEmailSettings(settings);
      setEmailBatchWaitSeconds(String(settings.batch_wait_seconds));
      setNotice("Updated email notification settings.");
    } catch (updateError) {
      setError(updateError instanceof Error ? updateError.message : "Unable to update email notification settings.");
    }
  }

  function submitEmailBatchWait(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const max = emailSettings?.max_batch_wait_seconds ?? 604800;
    const parsed = Number.parseInt(emailBatchWaitSeconds || "0", 10);
    const nextValue = Math.max(0, Math.min(max, Number.isFinite(parsed) ? parsed : 0));
    setEmailBatchWaitSeconds(String(nextValue));
    void updateEmailSettings({ batch_wait_seconds: nextValue });
  }

  return (
    <div className="studio-stack-lg">
      {error ? <NoticeBanner tone="error">{error}</NoticeBanner> : null}
      {notice ? <NoticeBanner>{notice}</NoticeBanner> : null}

      {isAdmin ? <details><summary>Email notification settings</summary><SectionCard title="Email Notifications" subtitle="Failed-task alert delivery.">
        {emailSettingsLoading ? <p style={mutedTextStyle}>Loading email settings...</p> : null}
        {emailSettings ? (
          <div className="studio-stack-sm">
            <div className="studio-action-row">
              <label className="studio-inline-meta" style={{ display: "inline-flex", alignItems: "center", gap: 8 }}>
                <input
                  type="checkbox"
                  checked={emailSettings.enabled}
                  disabled={!emailSettings.configured}
                  onChange={(event) => void updateEmailSettings({ enabled: event.target.checked })}
                />
                Enabled
              </label>
              <span className="studio-inline-meta">
                {emailSettings.configured ? `${emailSettings.receivers.length} receivers` : "Not configured"}
              </span>
              <span className="studio-inline-meta">Wait: {formatBatchWait(emailSettings.batch_wait_seconds)}</span>
            </div>
            <form onSubmit={submitEmailBatchWait} className="studio-form-grid studio-form-grid--triple">
              <input
                type="range"
                min={0}
                max={emailSettings.max_batch_wait_seconds}
                step={60}
                value={Math.min(Number(emailBatchWaitSeconds) || 0, emailSettings.max_batch_wait_seconds)}
                disabled={!emailSettings.configured}
                onChange={(event) => setEmailBatchWaitSeconds(event.target.value)}
                style={{ width: "100%" }}
                aria-label="Email batch wait seconds"
              />
              <input
                type="number"
                min={0}
                max={emailSettings.max_batch_wait_seconds}
                value={emailBatchWaitSeconds}
                disabled={!emailSettings.configured}
                onChange={(event) => setEmailBatchWaitSeconds(event.target.value)}
                placeholder="0"
                style={inputStyle}
              />
              <button type="submit" style={secondaryButtonStyle} disabled={!emailSettings.configured}>
                Save Wait
              </button>
            </form>
          </div>
        ) : null}
      </SectionCard></details> : null}

      <SectionCard title="Failed Tasks" subtitle="Review failed work, inspect its evidence and retry supported tasks.">
        <form onSubmit={(event) => void submitFilters(event)} className="studio-form-grid studio-form-grid--triple">
          <label className="studio-filter-field"><span>Service id</span><input value={query.service_id} onChange={(event) => setQuery((current) => ({ ...current, service_id: event.target.value }))} placeholder="Service id" style={inputStyle} /></label>
          <label className="studio-filter-field"><span>Queue</span><input value={query.queue_name} onChange={(event) => setQuery((current) => ({ ...current, queue_name: event.target.value }))} placeholder="Queue" style={inputStyle} /></label>
          <label className="studio-filter-field"><span>DLQ</span><input value={query.dlq_name} onChange={(event) => setQuery((current) => ({ ...current, dlq_name: event.target.value }))} placeholder="DLQ" style={inputStyle} /></label>
          <label className="studio-filter-field"><span>Error type</span><input value={query.error_type} onChange={(event) => setQuery((current) => ({ ...current, error_type: event.target.value }))} placeholder="Error type" style={inputStyle} /></label>
          <label className="studio-filter-field"><span>Status</span><input value={query.status} onChange={(event) => setQuery((current) => ({ ...current, status: event.target.value }))} placeholder="Status" style={inputStyle} /></label>
          <label className="studio-filter-field"><span>Task id</span><input value={query.task_id} onChange={(event) => setQuery((current) => ({ ...current, task_id: event.target.value }))} placeholder="Task id" style={inputStyle} /></label>
          <label className="studio-filter-field"><span>Worker</span><input value={query.worker_id} onChange={(event) => setQuery((current) => ({ ...current, worker_id: event.target.value }))} placeholder="Worker" style={inputStyle} /></label>
          <label className="studio-filter-field"><span>Investigation status</span><select value={query.investigation_status} onChange={(event) => setQuery((current) => ({ ...current, investigation_status: event.target.value }))} style={inputStyle}>
            <option value="unreviewed">Unreviewed</option>
            <option value="investigated">Investigated</option>
            <option value="">All</option>
          </select></label>
          <label className="studio-filter-field"><span>Page size</span><input value={query.limit} onChange={(event) => setQuery((current) => ({ ...current, limit: event.target.value }))} placeholder="50" style={inputStyle} /></label>
          <button type="submit" style={primaryButtonStyle}>
            <StudioIcon name="filter" />
            Apply Filters
          </button>
        </form>

        {loading ? <p style={mutedTextStyle}>Loading failed tasks...</p> : null}
        {readErrors.length ? <NoticeBanner tone="error"><strong>{unavailable ? "Failed tasks unavailable" : "Partial results"}</strong><ul>{readErrors.map((message) => <li key={message}>{message}</li>)}</ul><button type="button" style={secondaryButtonStyle} disabled={loading} onClick={() => void load({ ...query, cursor: null })}>Retry service reads</button></NoticeBanner> : null}
        {!loading && !items.length && !unavailable ? <p style={mutedTextStyle}>{nextCursor ? "No matches on this page in the selected scope. Continue to the next page." : "No failed tasks matched the current filters."}</p> : null}
        {isAdmin ? <button type="button" style={secondaryButtonStyle} disabled={pending || !checkedFailures.length} onClick={() => void reviewSelected()}>Mark selected investigated ({checkedFailures.length}/20)</button> : null}
        <div className="studio-stack-sm">
          {items.map((item) => (
            <article key={`${item.service_id}:${item.failure_id}`} className="studio-subcard" style={{ borderRadius: 14, padding: 14, display: "grid", gap: 8 }}>
              {isAdmin ? <label className="studio-inline-meta"><input type="checkbox" aria-label={`Select ${item.failure_id}`} checked={checkedFailures.includes(`${item.service_id}:${item.failure_id}`)} disabled={pending || !Array.isArray(services.servicesById.get(item.service_id)?.capabilities?.supported_routes) || !(services.servicesById.get(item.service_id)?.capabilities?.supported_routes as string[]).includes("failed_tasks.investigate") || (checkedFailures.length >= 20 && !checkedFailures.includes(`${item.service_id}:${item.failure_id}`))} onChange={(event) => setCheckedFailures((current) => event.target.checked ? [...current, `${item.service_id}:${item.failure_id}`] : current.filter((id) => id !== `${item.service_id}:${item.failure_id}`))} /> Select for review</label> : null}
              <div className="studio-list-card__top">
                <div style={{ display: "grid", gap: 4 }}>
                  <strong style={{ fontSize: 13 }}>{item.error_type || item.status}</strong>
                  <span className="studio-inline-meta">{item.service_name || item.service_id} · {item.queue_name} · {item.dlq_name}</span>
                </div>
                <span className="studio-inline-meta">{formatTimestamp(item.failed_at)}</span>
              </div>
              <p style={mutedTextStyle}>{item.error_message || "No error message captured."}</p>
              <div className="studio-action-row">
                {item.task_ref ? <TaskRefLink taskRef={item.task_ref} /> : <span style={mutedTextStyle}>{item.task_id || "unattributed"}</span>}
                <span className="studio-inline-meta">{item.investigation_status} · {item.retry_status}</span>
                <button type="button" style={secondaryButtonStyle} disabled={pending} onClick={() => void openDetail(item)}>
                  <StudioIcon name="open" />
                  View
                </button>
              </div>
            </article>
          ))}
          {nextCursor ? (
            <button
              type="button"
              style={secondaryButtonStyle}
              onClick={() => {
                const nextQuery = { ...query, cursor: nextCursor };
                setQuery(nextQuery);
                void load(nextQuery, true);
              }}
            >
              Load Next Page
            </button>
          ) : null}
        </div>
      </SectionCard>

      {selected ? (
        <SectionCard
          title="Failure Detail"
          subtitle={`${selected.service_name || selected.service_id} · ${selected.failure_id}`}
          action={
            <button type="button" style={secondaryButtonStyle} disabled={pending} onClick={() => { detailVersion.current++; setSelected(null); }}>
              <StudioIcon name="clear" />
              Close
            </button>
          }
        >
          {detailLoading ? <p style={mutedTextStyle}>Loading detail...</p> : null}
          <div className="studio-action-row">
            <button type="button" style={secondaryButtonStyle} onClick={() => void copySelected(selected.body)}>
              <StudioIcon name="copy" />
              Copy Payload
            </button>
            <button type="button" style={secondaryButtonStyle} onClick={() => void copySelected({ error_type: selected.error_type, error_message: selected.error_message, traceback: selected.traceback })}>
              <StudioIcon name="copy" />
              Copy Error
            </button>
            <button type="button" style={secondaryButtonStyle} onClick={downloadSelected}>
              <StudioIcon name="save" />
              Download JSON
            </button>
            {isAdmin ? <button type="button" style={secondaryButtonStyle} disabled={pending || !supports("delete")} onClick={() => void mutate(remove)}>
              <StudioIcon name="delete" />
              Delete
            </button> : null}
          </div>

          {isAdmin ? <><div className="studio-form-grid studio-form-grid--triple">
            <p style={mutedTextStyle}>Actions recorded as {operator}. Disabled actions are not supported by this service.</p>
            <label className="studio-filter-field"><span>Investigation note</span><input value={investigationNote} onChange={(event) => setInvestigationNote(event.target.value)} placeholder="Investigation note" style={inputStyle} /></label>
            <button type="button" style={primaryButtonStyle} disabled={pending || !supports("investigate")} onClick={() => void mutate(investigate)}>Mark Investigated</button>
            <button type="button" style={secondaryButtonStyle} disabled={pending || !supports("uninvestigate")} onClick={() => void mutate(uninvestigate)}>Mark Unreviewed</button>
          </div>

          <div className="studio-form-grid studio-form-grid--triple">
            <label className="studio-filter-field"><span>Target queue</span><input value={retryTargetQueue} onChange={(event) => setRetryTargetQueue(event.target.value)} placeholder="Target queue" style={inputStyle} /></label>
            <label className="studio-filter-field"><span>Retry note</span><input value={retryNote} onChange={(event) => setRetryNote(event.target.value)} placeholder="Retry note" style={inputStyle} /></label>
            <button type="button" style={primaryButtonStyle} disabled={pending || !supports("retry") || !selected.payload_available || !["DLQ", "retry_exhausted", "terminal_failed"].includes(selected.status)} onClick={() => void mutate(retry)}>
              <StudioIcon name="refresh" />
              Retry
            </button>
          </div>
          <label className="studio-filter-field"><span>Optional JSON payload override</span><textarea value={overridePayload} onChange={(event) => setOverridePayload(event.target.value)} placeholder="Optional JSON payload override" style={{ ...inputStyle, minHeight: 96, fontFamily: "'SFMono-Regular', Menlo, monospace" }} /></label></> : null}

          <div className="studio-detail-grid">
            <section><h3>Payload</h3><InlineCodeBox label="Payload" value={JSON.stringify(selected.input_preview ?? selected.body ?? null, null, 2)} minHeight={160} /></section>
            <section><h3>Error and traceback</h3><InlineCodeBox label="Error and traceback" value={selected.traceback || selected.error_message || ""} minHeight={160} /></section>
            <section><h3>Captured logs</h3><InlineCodeBox label="Captured logs" value={JSON.stringify(selected.last_logs || [], null, 2)} minHeight={160} /></section>
            <section><h3>Metadata</h3><InlineCodeBox label="Metadata" value={JSON.stringify(selected.metadata || {}, null, 2)} minHeight={160} /></section>
          </div>
        </SectionCard>
      ) : null}
    </div>
  );
}

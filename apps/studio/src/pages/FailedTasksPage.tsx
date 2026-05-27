import type { FormEvent } from "react";
import { useEffect, useState } from "react";

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

function formatBatchWait(seconds: number) {
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
  const [operator, setOperator] = useState("operator");
  const [retryNote, setRetryNote] = useState("");
  const [retryTargetQueue, setRetryTargetQueue] = useState("");
  const [overridePayload, setOverridePayload] = useState("");

  useEffect(() => {
    void load(initialQuery);
    void loadEmailSettings();
  }, []);

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
    setLoading(true);
    setError(null);
    try {
      const payload = await fetchFailedTasks(nextQuery);
      setItems((current) => (append ? [...current, ...payload.items] : payload.items));
      setNextCursor(payload.next_cursor || null);
      if (payload.errors?.length) {
        setNotice(`${payload.errors.length} service read failed while loading failed tasks.`);
      }
    } catch (fetchError) {
      setError(fetchError instanceof Error ? fetchError.message : "Unable to load failed tasks.");
    } finally {
      setLoading(false);
    }
  }

  async function openDetail(item: FailedTaskSummary) {
    setDetailLoading(true);
    setError(null);
    try {
      const detail = await fetchFailedTaskDetail(item.service_id, item.failure_id);
      setSelected(detail);
      setInvestigationNote(detail.investigation_note || "");
      setRetryNote(detail.retry_note || "");
      setRetryTargetQueue(detail.queue_name || "");
      setOverridePayload("");
    } catch (fetchError) {
      setError(fetchError instanceof Error ? fetchError.message : "Unable to load failed task detail.");
    } finally {
      setDetailLoading(false);
    }
  }

  async function submitFilters(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const nextQuery = { ...query, cursor: null };
    setQuery(nextQuery);
    await load(nextQuery);
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
    await load(query);
  }

  async function uninvestigate() {
    if (!selected) {
      return;
    }
    const detail = await markFailedTaskUninvestigated(selected.service_id, selected.failure_id);
    setSelected(detail);
    await load(query);
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
    if (!window.confirm("Retry this terminal failed task?")) {
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
      await load(query);
    } catch (retryError) {
      setError(retryError instanceof Error ? retryError.message : "Unable to retry failed task.");
    }
  }

  async function remove() {
    if (!selected || !window.confirm("Delete this failed-task snapshot?")) {
      return;
    }
    await deleteFailedTask(selected.service_id, selected.failure_id);
    setSelected(null);
    await load(query);
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
    await navigator.clipboard?.writeText(JSON.stringify(value, null, 2));
    setNotice("Copied failed-task data.");
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

      <SectionCard title="Email Notifications" subtitle="Failed-task alert delivery.">
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
      </SectionCard>

      <SectionCard title="Failed Tasks" subtitle="Cross-service terminal failure registry backed by Relayna DLQ snapshots.">
        <form onSubmit={(event) => void submitFilters(event)} className="studio-form-grid studio-form-grid--triple">
          <input value={query.service_id} onChange={(event) => setQuery((current) => ({ ...current, service_id: event.target.value }))} placeholder="Service id" style={inputStyle} />
          <input value={query.queue_name} onChange={(event) => setQuery((current) => ({ ...current, queue_name: event.target.value }))} placeholder="Queue" style={inputStyle} />
          <input value={query.dlq_name} onChange={(event) => setQuery((current) => ({ ...current, dlq_name: event.target.value }))} placeholder="DLQ" style={inputStyle} />
          <input value={query.error_type} onChange={(event) => setQuery((current) => ({ ...current, error_type: event.target.value }))} placeholder="Error type" style={inputStyle} />
          <input value={query.status} onChange={(event) => setQuery((current) => ({ ...current, status: event.target.value }))} placeholder="Status" style={inputStyle} />
          <input value={query.task_id} onChange={(event) => setQuery((current) => ({ ...current, task_id: event.target.value }))} placeholder="Task id" style={inputStyle} />
          <input value={query.worker_id} onChange={(event) => setQuery((current) => ({ ...current, worker_id: event.target.value }))} placeholder="Worker" style={inputStyle} />
          <select value={query.investigation_status} onChange={(event) => setQuery((current) => ({ ...current, investigation_status: event.target.value }))} style={inputStyle}>
            <option value="unreviewed">Unreviewed</option>
            <option value="investigated">Investigated</option>
            <option value="">All</option>
          </select>
          <input value={query.limit} onChange={(event) => setQuery((current) => ({ ...current, limit: event.target.value }))} placeholder="50" style={inputStyle} />
          <button type="submit" style={primaryButtonStyle}>
            <StudioIcon name="filter" />
            Apply Filters
          </button>
        </form>

        {loading ? <p style={mutedTextStyle}>Loading failed tasks...</p> : null}
        {!loading && !items.length ? <p style={mutedTextStyle}>No failed tasks matched the current filters.</p> : null}
        <div className="studio-stack-sm">
          {items.map((item) => (
            <article key={`${item.service_id}:${item.failure_id}`} className="studio-subcard" style={{ borderRadius: 14, padding: 14, display: "grid", gap: 8 }}>
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
                <button type="button" style={secondaryButtonStyle} onClick={() => void openDetail(item)}>
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
            <button type="button" style={secondaryButtonStyle} onClick={() => setSelected(null)}>
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
            <button type="button" style={secondaryButtonStyle} onClick={() => void remove()}>
              <StudioIcon name="delete" />
              Delete
            </button>
          </div>

          <div className="studio-form-grid studio-form-grid--triple">
            <input value={operator} onChange={(event) => setOperator(event.target.value)} placeholder="Operator" style={inputStyle} />
            <input value={investigationNote} onChange={(event) => setInvestigationNote(event.target.value)} placeholder="Investigation note" style={inputStyle} />
            <button type="button" style={primaryButtonStyle} onClick={() => void investigate()}>Mark Investigated</button>
            <button type="button" style={secondaryButtonStyle} onClick={() => void uninvestigate()}>Mark Unreviewed</button>
          </div>

          <div className="studio-form-grid studio-form-grid--triple">
            <input value={retryTargetQueue} onChange={(event) => setRetryTargetQueue(event.target.value)} placeholder="Target queue" style={inputStyle} />
            <input value={retryNote} onChange={(event) => setRetryNote(event.target.value)} placeholder="Retry note" style={inputStyle} />
            <button type="button" style={primaryButtonStyle} disabled={!selected.payload_available || !["DLQ", "retry_exhausted", "terminal_failed"].includes(selected.status)} onClick={() => void retry()}>
              <StudioIcon name="refresh" />
              Retry
            </button>
          </div>
          <textarea value={overridePayload} onChange={(event) => setOverridePayload(event.target.value)} placeholder="Optional JSON payload override" style={{ ...inputStyle, minHeight: 96, fontFamily: "'SFMono-Regular', Menlo, monospace" }} />

          <div className="studio-detail-grid">
            <InlineCodeBox value={JSON.stringify(selected.input_preview ?? selected.body ?? null, null, 2)} minHeight={160} />
            <InlineCodeBox value={selected.traceback || selected.error_message || ""} minHeight={160} />
            <InlineCodeBox value={JSON.stringify(selected.last_logs || [], null, 2)} minHeight={160} />
            <InlineCodeBox value={JSON.stringify(selected.metadata || {}, null, 2)} minHeight={160} />
          </div>
        </SectionCard>
      ) : null}
    </div>
  );
}

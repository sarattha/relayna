import { useEffect, useRef, useState, type FormEvent } from "react";
import { useSearchParams } from "react-router-dom";

import {
  fetchIncident,
  fetchIncidentInbox,
  mergeIncident,
  splitIncident,
  updateIncident,
} from "../api";
import { useStudioAuth } from "../auth-context";
import { Link } from "../scoped-link";
import { useStudioServices } from "../services-context";
import type {
  IncidentCoverage,
  IncidentDetail,
  IncidentEvidence,
  IncidentInboxResponse,
  IncidentMutationResult,
  IncidentStatus,
} from "../types";
import {
  EmptyState,
  HealthBadge,
  NoticeBanner,
  SectionCard,
  formatTimestamp,
  inputStyle,
  primaryButtonStyle,
  secondaryButtonStyle,
} from "../ui";

function Coverage({
  coverage,
  complete,
  scannedAt,
}: {
  coverage: IncidentCoverage[];
  complete: boolean;
  scannedAt: string;
}) {
  const completed = coverage.filter(
    (item) => item.status === "complete",
  ).length;
  return (
    <div className="inbox-coverage">
      <p>
        <strong>
          {complete ? "Complete retained query" : "Incomplete evidence"}
        </strong>{" "}
        · {completed}/{coverage.length} enabled services fully scanned ·{" "}
        {formatTimestamp(scannedAt)}
      </p>
      <p>
        Counts cover unique failures returned by this scan, across all its
        pages. Each service is bounded to 1,000 records and a time budget.
        Upstream retention and concurrent changes can limit evidence.
      </p>
      {!complete ? (
        <NoticeBanner tone="info">
          Available counts are lower bounds. Unavailable or truncated services
          may contain more failures.
        </NoticeBanner>
      ) : null}
      <details>
        <summary>Service coverage</summary>
        {coverage.length ? (
          <ul>
            {coverage.map((item) => (
              <li key={`${item.environment}:${item.service_id}`}>
                <strong>{item.service_id}</strong> ({item.environment}) ·{" "}
                {item.status} · {item.observed_failures} observed · {item.pages}{" "}
                pages{item.detail ? ` · ${item.detail}` : ""}
              </li>
            ))}
          </ul>
        ) : (
          <p>No enabled services in this environment.</p>
        )}
      </details>
    </div>
  );
}

function EvidenceLink({ item }: { item: IncidentEvidence }) {
  return item.task_id ? (
    <Link
      to={`/tasks/${encodeURIComponent(item.service_id)}/${encodeURIComponent(item.task_id)}?view=monitor&layout=investigation`}
      className="studio-button-link"
    >
      Investigate {item.task_id}
    </Link>
  ) : (
    <span>No task ID available</span>
  );
}

export function IncidentInboxPage() {
  const { isAdmin } = useStudioAuth();
  const services = useStudioServices();
  const [params, setParams] = useSearchParams();
  const environment = params.get("environment") || "";
  const status = params.get("status") || "active";
  const selectedId = params.get("incident") || "";
  const [offset, setOffset] = useState(0);
  const [inboxData, setInbox] = useState<IncidentInboxResponse | null>(null);
  const [detailData, setDetail] = useState<IncidentDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [detailLoading, setDetailLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [detailError, setDetailError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [refresh, setRefresh] = useState(0);
  const [owner, setOwner] = useState("");
  const [nextStatus, setNextStatus] = useState<IncidentStatus>("open");
  const [note, setNote] = useState("");
  const [checked, setChecked] = useState<string[]>([]);
  const [mergeTarget, setMergeTarget] = useState("");
  const [pending, setPending] = useState(false);
  const mutationLock = useRef(false);
  const loadedScope = useRef("");
  const loadedView = useRef("");
  const scope = `${environment}|${status}|${offset}`;
  const currentView = useRef(`${environment}|${selectedId}`);
  const currentParams = useRef(params);
  currentView.current = `${environment}|${selectedId}`;
  currentParams.current = params;
  const inbox = loadedScope.current === scope ? inboxData : null;
  const detail = loadedView.current === currentView.current ? detailData : null;

  useEffect(() => {
    let cancelled = false;
    const savedScope = scope;
    setLoading(true);
    setError(null);
    setInbox((previous) =>
      savedScope === loadedScope.current ? previous : null,
    );
    void fetchIncidentInbox(environment, status, offset)
      .then((payload) => {
        if (cancelled) return;
        setInbox(payload);
        loadedScope.current = savedScope;
      })
      .catch((failure) => {
        if (!cancelled)
          setError(
            failure instanceof Error
              ? failure.message
              : "Unable to load Incident Inbox.",
          );
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [environment, status, offset, refresh]);
  useEffect(() => {
    setOffset(0);
    setNotice(null);
  }, [environment]);

  useEffect(() => {
    let cancelled = false;
    setDetail(null);
    setDetailError(null);
    setChecked([]);
    setMergeTarget("");
    setNote("");
    if (!selectedId) {
      setDetailLoading(false);
      return;
    }
    setDetailLoading(true);
    void fetchIncident(selectedId)
      .then((payload) => {
        if (cancelled) return;
        if (environment && payload.environment !== environment)
          throw new Error("This incident is outside the selected environment.");
        loadedView.current = `${environment}|${selectedId}`;
        setDetail(payload);
        setOwner(payload.owner || "");
        setNextStatus(payload.status);
      })
      .catch((failure) => {
        if (!cancelled)
          setDetailError(
            failure instanceof Error
              ? failure.message
              : "Unable to load incident evidence.",
          );
      })
      .finally(() => {
        if (!cancelled) setDetailLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [environment, selectedId, refresh]);

  function select(incidentId: string) {
    const next = new URLSearchParams(params);
    next.set("incident", incidentId);
    setParams(next);
    setNotice(null);
  }

  async function mutate(action: () => Promise<IncidentMutationResult>) {
    if (mutationLock.current) return;
    const view = currentView.current;
    mutationLock.current = true;
    setPending(true);
    setDetailError(null);
    setNotice(null);
    try {
      const result = await action();
      if (currentView.current !== view) return;
      const next = new URLSearchParams(currentParams.current);
      next.set("incident", result.incident_id);
      setParams(next);
      setNotice("Incident saved.");
      setRefresh((value) => value + 1);
    } catch (failure) {
      if (currentView.current === view)
        setDetailError(
          failure instanceof Error
            ? failure.message
            : "Unable to save incident.",
        );
    } finally {
      mutationLock.current = false;
      setPending(false);
    }
  }

  function save(event: FormEvent) {
    event.preventDefault();
    if (!detail) return;
    void mutate(() =>
      updateIncident(detail.incident_id, {
        revision: detail.revision,
        note,
        ...(owner.trim() !== (detail.owner || "")
          ? { owner: owner.trim() || null }
          : {}),
        ...(nextStatus !== detail.status ? { status: nextStatus } : {}),
      }),
    );
  }

  const candidates = (inbox?.items || []).filter(
    (item) =>
      detail &&
      item.incident_id !== detail.incident_id &&
      item.environment === detail.environment &&
      item.service_id === detail.service_id,
  );
  const target = candidates.find((item) => item.incident_id === mergeTarget);

  return (
    <div className="studio-stack-lg">
      <section className="studio-operations-header">
        <div>
          <p className="studio-page-eyebrow">Failure triage</p>
          <h1>Incident Inbox</h1>
          <p>
            Find the largest failure cohort, assign it, and open the evidence.
          </p>
        </div>
        <div className="inbox-actions">
          <Link to="/failed-tasks" className="studio-button-link">
            Individual failed tasks
          </Link>
          <button
            type="button"
            style={secondaryButtonStyle}
            disabled={loading || pending}
            onClick={() => setRefresh((value) => value + 1)}
          >
            Refresh evidence
          </button>
        </div>
      </section>
      <p className="inbox-help">
        Groups suggest related failures; they do not prove a shared root cause.
        Ownership and resolution belong to Studio and do not retry or clear
        tasks.
      </p>
      <div className="inbox-toolbar">
        <label>
          Status{" "}
          <select
            aria-label="Incident status filter"
            style={inputStyle}
            value={status}
            onChange={(event) => {
              const next = new URLSearchParams(params);
              next.set("status", event.target.value);
              setOffset(0);
              setParams(next);
            }}
          >
            <option value="active">Open &amp; acknowledged</option>
            <option value="all">All statuses</option>
            <option value="open">Open</option>
            <option value="acknowledged">Acknowledged</option>
            <option value="resolved">Resolved</option>
          </select>
        </label>
        <span>
          {environment || "All environments"}
          {inbox
            ? ` · ${inbox.total_incidents} incidents · ${inbox.observed_failures} observed failures in the full scan`
            : ""}
        </span>
      </div>
      {error ? (
        <NoticeBanner tone="error">
          {error}
          {inbox ? " Showing the previous scan; counts may be stale." : ""}
        </NoticeBanner>
      ) : null}
      {notice ? <NoticeBanner tone="info">{notice}</NoticeBanner> : null}
      {loading ? <p role="status">Scanning failure evidence…</p> : null}
      {inbox ? (
        <Coverage
          coverage={inbox.coverage}
          complete={inbox.complete}
          scannedAt={inbox.scanned_at}
        />
      ) : null}
      <div className="inbox-layout">
        <SectionCard
          title="Failure cohorts"
          subtitle="Largest observed cohorts first. Counts include every page fetched in the bounded scan."
        >
          {inbox && !inbox.items.length && !loading ? (
            <EmptyState
              title={
                inbox.complete
                  ? "No incidents in this view"
                  : "No incidents found in available evidence"
              }
              body={
                inbox.complete
                  ? "Try another status or environment. A resolved incident stays resolved when failures are seen again."
                  : "Some sources could not be fully scanned. This does not establish that there are no failures."
              }
            />
          ) : null}
          <div className="inbox-cohorts">
            {(inbox?.items || []).map((item) => (
              <button
                type="button"
                className={`inbox-cohort${item.incident_id === selectedId ? " inbox-cohort--selected" : ""}`}
                aria-pressed={item.incident_id === selectedId}
                key={item.incident_id}
                onClick={() => select(item.incident_id)}
              >
                <div className="inbox-cohort__heading">
                  <strong>{item.dimensions.error_type}</strong>
                  <span className="inbox-count">{item.failure_count}</span>
                </div>
                <span className="inbox-signature">
                  {item.dimensions.signature}
                </span>
                <span>
                  {item.environment} · {item.service_id} ·{" "}
                  {item.dimensions.queue_name}
                </span>
                <span>
                  {item.status} · {item.owner || "Unassigned"}
                  {item.grouping === "manual" ? " · Manual group" : ""}
                </span>
                <span className="inbox-help">
                  {item.failure_count
                    ? `${item.affected_tasks} affected tasks · ${item.trend.recent_hour} last hour / ${item.trend.preceding_hour} preceding hour`
                    : "No matching evidence in this scan; state retained"}
                </span>
              </button>
            ))}
          </div>
          {inbox && (offset > 0 || inbox.next_offset !== null) ? (
            <div className="inbox-actions">
              <button
                type="button"
                style={secondaryButtonStyle}
                disabled={offset === 0 || loading}
                onClick={() => setOffset(Math.max(0, offset - 100))}
              >
                Previous cohorts
              </button>
              <span>
                Showing {offset + 1}–{offset + inbox.items.length} of{" "}
                {inbox.total_incidents}
              </span>
              <button
                type="button"
                style={secondaryButtonStyle}
                disabled={inbox.next_offset === null || loading}
                onClick={() => setOffset(inbox.next_offset || 0)}
              >
                Next cohorts
              </button>
            </div>
          ) : null}
        </SectionCard>
        <div className="studio-stack-lg" aria-label="Incident evidence">
          {detailError ? (
            <NoticeBanner tone="error">{detailError}</NoticeBanner>
          ) : null}
          {detailLoading ? (
            <p role="status">Loading incident evidence…</p>
          ) : null}
          {!selectedId ? (
            <SectionCard title="Choose a cohort">
              <p>
                Start with the largest count. Open a cohort to inspect its
                evidence and triage history.
              </p>
            </SectionCard>
          ) : null}
          {detail ? (
            <>
              <SectionCard
                title={detail.dimensions.error_type}
                subtitle={`${detail.environment} · ${detail.service_id} · ${detail.dimensions.queue_name} · stage: ${detail.dimensions.stage}`}
              >
                <div className="inbox-actions">
                  <span className="inbox-status">{detail.status}</span>
                  <span>{detail.owner || "Unassigned"}</span>
                  <HealthBadge
                    status={
                      services.servicesById.get(detail.service_id)?.health
                        ?.overall_status || "unknown"
                    }
                  />
                  <Link
                    to={`/services/${encodeURIComponent(detail.service_id)}`}
                  >
                    Service health
                  </Link>
                </div>
                <p className="inbox-signature">
                  {detail.grouping === "manual" ? "Grouping anchor: " : ""}
                  {detail.dimensions.signature}
                </p>
                {detail.grouping === "manual" ? (
                  <p className="inbox-help">
                    This group has a manual split or merge. Its evidence can
                    include different signatures or queues; inspect each
                    representative before drawing conclusions.
                  </p>
                ) : null}
                <dl className="inbox-facts">
                  <div>
                    <dt>Observed failures</dt>
                    <dd>{detail.failure_count}</dd>
                  </div>
                  <div>
                    <dt>Affected tasks</dt>
                    <dd>{detail.affected_tasks}</dd>
                  </div>
                  <div>
                    <dt>First seen in scan</dt>
                    <dd>{formatTimestamp(detail.first_seen)}</dd>
                  </div>
                  <div>
                    <dt>Last seen in scan</dt>
                    <dd>{formatTimestamp(detail.last_seen)}</dd>
                  </div>
                </dl>
                <p>
                  Last hour: <strong>{detail.trend.recent_hour}</strong> ·
                  preceding hour: <strong>{detail.trend.preceding_hour}</strong>{" "}
                  · outside these buckets: {detail.trend.outside_two_hours} ·
                  missing timestamps: {detail.trend.unknown_timestamps}
                </p>
                {detail.missing_task_ids ? (
                  <p>
                    {detail.missing_task_ids} failures have no task ID and are
                    excluded from affected-task counts.
                  </p>
                ) : null}
                {!detail.failure_count ? (
                  <NoticeBanner tone="info">
                    No matching failures were observed. The source may have
                    expired or removed records, changed environment, or be
                    unavailable. Durable incident state is retained.
                  </NoticeBanner>
                ) : null}
                {detail.merged_into ? (
                  <NoticeBanner tone="info">
                    This cohort was merged.{" "}
                    <button
                      type="button"
                      style={secondaryButtonStyle}
                      onClick={() => select(detail.merged_into!)}
                    >
                      Open target incident
                    </button>
                  </NoticeBanner>
                ) : null}
                <Coverage
                  coverage={detail.coverage}
                  complete={detail.complete}
                  scannedAt={detail.scanned_at}
                />
              </SectionCard>
              <SectionCard
                title="Representative evidence"
                subtitle="Newest observed failures. Investigation opens the existing task workspace."
              >
                {detail.representative.length ? (
                  detail.representative.map((item) => (
                    <article key={item.failure_id} className="inbox-evidence">
                      <strong>{item.error_type || "Unknown error"}</strong>
                      <pre>
                        {item.error_message || "No error message available."}
                      </pre>
                      <span>
                        {formatTimestamp(item.failed_at)} · {item.failure_id}
                      </span>
                      <EvidenceLink item={item} />
                    </article>
                  ))
                ) : (
                  <p>No representative evidence is available from this scan.</p>
                )}
              </SectionCard>
              {isAdmin && !detail.merged_into ? (
                <SectionCard
                  title="Triage incident"
                  subtitle="Resolution is explicit. New observations do not automatically reopen an incident."
                >
                  <form onSubmit={save} className="studio-stack">
                    <label>
                      Owner
                      <input
                        aria-label="Incident owner"
                        maxLength={320}
                        style={inputStyle}
                        value={owner}
                        disabled={pending}
                        onChange={(event) => setOwner(event.target.value)}
                        placeholder="Person or team"
                      />
                    </label>
                    <label>
                      Status
                      <select
                        aria-label="Incident lifecycle status"
                        style={inputStyle}
                        value={nextStatus}
                        disabled={pending}
                        onChange={(event) =>
                          setNextStatus(event.target.value as IncidentStatus)
                        }
                      >
                        <option value="open">Open</option>
                        <option value="acknowledged">Acknowledged</option>
                        <option value="resolved">Resolved</option>
                      </select>
                    </label>
                    <label>
                      Note
                      <textarea
                        aria-label="Incident note"
                        maxLength={4000}
                        style={inputStyle}
                        value={note}
                        disabled={pending}
                        onChange={(event) => setNote(event.target.value)}
                      />
                    </label>
                    <button
                      type="submit"
                      style={primaryButtonStyle}
                      disabled={
                        pending ||
                        (!note.trim() &&
                          owner.trim() === (detail.owner || "") &&
                          nextStatus === detail.status)
                      }
                    >
                      {pending ? "Saving…" : "Save incident"}
                    </button>
                  </form>
                </SectionCard>
              ) : null}
              <SectionCard
                title="Affected failures"
                subtitle={`${detail.evidence.length} records in this scan. Task counts are distinct within this cohort.`}
              >
                <div className="inbox-failures">
                  {detail.evidence.map((item) => (
                    <article key={item.failure_id} className="inbox-failure">
                      {isAdmin && !detail.merged_into ? (
                        <input
                          type="checkbox"
                          aria-label={`Select failure ${item.failure_id}`}
                          checked={checked.includes(item.failure_id)}
                          disabled={pending}
                          onChange={(event) =>
                            setChecked((current) =>
                              event.target.checked
                                ? [...current, item.failure_id]
                                : current.filter(
                                    (fid) => fid !== item.failure_id,
                                  ),
                            )
                          }
                        />
                      ) : null}
                      <div>
                        <strong>{item.failure_id}</strong>
                        <span>
                          {item.queue_name || "Unknown queue"} ·{" "}
                          {formatTimestamp(item.failed_at)}
                        </span>
                        <EvidenceLink item={item} />
                      </div>
                    </article>
                  ))}
                </div>
                {isAdmin && !detail.merged_into ? (
                  <div className="inbox-corrections">
                    <p>
                      Split moves the selected records into a new, unassigned
                      open incident. Future failures keep their automatic
                      grouping.
                    </p>
                    <button
                      type="button"
                      style={secondaryButtonStyle}
                      disabled={
                        pending ||
                        !checked.length ||
                        checked.length >= detail.evidence.length
                      }
                      onClick={() => {
                        if (
                          window.confirm(
                            `Split ${checked.length} observed failures into a new incident in ${detail.environment}?`,
                          )
                        )
                          void mutate(() =>
                            splitIncident(detail.incident_id, {
                              revision: detail.revision,
                              failure_ids: checked,
                              note,
                            }),
                          );
                      }}
                    >
                      Split selected failures
                    </button>
                    <label>
                      Merge into
                      <select
                        aria-label="Merge target"
                        style={inputStyle}
                        value={mergeTarget}
                        disabled={pending}
                        onChange={(event) => setMergeTarget(event.target.value)}
                      >
                        <option value="">Choose another cohort</option>
                        {candidates.map((item) => (
                          <option
                            key={item.incident_id}
                            value={item.incident_id}
                          >
                            {item.dimensions.error_type}:{" "}
                            {item.dimensions.signature.slice(0, 70)} (
                            {item.failure_count})
                          </option>
                        ))}
                      </select>
                    </label>
                    <p>
                      Targets are cohorts on this inbox page in the same
                      environment and service. The target keeps its owner and
                      status; future source matches join it. Each source's
                      history stays accessible.
                    </p>
                    <button
                      type="button"
                      style={secondaryButtonStyle}
                      disabled={pending || !target}
                      onClick={() => {
                        if (
                          target &&
                          window.confirm(
                            `Merge this cohort into ${target.dimensions.error_type} in ${detail.environment}? The target keeps its owner and status.`,
                          )
                        )
                          void mutate(() =>
                            mergeIncident(detail.incident_id, {
                              revision: detail.revision,
                              target_id: target.incident_id,
                              target_revision: target.revision,
                              note,
                            }),
                          );
                      }}
                    >
                      Merge incident
                    </button>
                  </div>
                ) : null}
              </SectionCard>
              <SectionCard
                title="Incident history"
                subtitle={`Latest ${detail.history_limit} entries. Full history is retained in PostgreSQL and the operator audit log.`}
              >
                {!detail.history.length ? (
                  <p>No operator changes yet.</p>
                ) : (
                  <ol className="inbox-history">
                    {detail.history.map((entry) => (
                      <li key={entry.id}>
                        <strong>{entry.action.replace(/_/g, " ")}</strong>
                        <span>
                          {formatTimestamp(entry.occurred_at)} ·{" "}
                          {entry.actor_user_id || "System"}
                        </span>
                        {typeof entry.details.note === "string" &&
                        entry.details.note ? (
                          <p>{entry.details.note}</p>
                        ) : null}
                        {["source_id", "target_id"].map((key) =>
                          typeof entry.details[key] === "string" &&
                          entry.details[key] !== detail.incident_id ? (
                            <button
                              type="button"
                              key={key}
                              style={secondaryButtonStyle}
                              onClick={() =>
                                select(entry.details[key] as string)
                              }
                            >
                              Open {key === "source_id" ? "source" : "target"}{" "}
                              incident
                            </button>
                          ) : null,
                        )}
                        <details>
                          <summary>Change details</summary>
                          <pre>{JSON.stringify(entry.details, null, 2)}</pre>
                        </details>
                      </li>
                    ))}
                  </ol>
                )}
              </SectionCard>
            </>
          ) : null}
        </div>
      </div>
    </div>
  );
}

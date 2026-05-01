import type { FormEvent } from "react";
import { useEffect, useRef, useState } from "react";
import { Link, useNavigate } from "react-router-dom";

import { searchServices } from "../api";
import { useStudioServices } from "../services-context";
import {
  ConfirmationDialog,
  EmptyState,
  HealthBadge,
  MetricCard,
  NoticeBanner,
  SectionCard,
  StudioIcon,
  StatusBadge,
  destructiveButtonStyle,
  insetSurfaceStyle,
  inputStyle,
  mutedTextStyle,
  primaryButtonStyle,
  secondaryButtonStyle,
} from "../ui";
import type { ServiceDraft, ServiceRecord, StudioServiceSearchItem } from "../types";

type ConfirmationRequest = {
  title: string;
  body: string;
  confirmLabel: string;
  challengeText?: string;
  challengeLabel?: string;
  onConfirm: () => Promise<void>;
};

export function ServicesPage() {
  const navigate = useNavigate();
  const servicesState = useStudioServices();
  const [showEditor, setShowEditor] = useState(false);
  const [editingServiceId, setEditingServiceId] = useState<string | null>(null);
  const [draft, setDraft] = useState<ServiceDraft>(servicesState.emptyDraft);
  const editorRef = useRef<HTMLDivElement | null>(null);
  const [saving, setSaving] = useState(false);
  const [searchDraft, setSearchDraft] = useState({
    query: "",
    environment: "",
    status: "",
    health: "",
    tag: "",
  });
  const [searchLoading, setSearchLoading] = useState(false);
  const [searchError, setSearchError] = useState<string | null>(null);
  const [searchResults, setSearchResults] = useState<StudioServiceSearchItem[] | null>(null);
  const [confirmation, setConfirmation] = useState<ConfirmationRequest | null>(null);

  function startCreate() {
    setShowEditor(true);
    setEditingServiceId(null);
    setDraft(servicesState.emptyDraft);
    servicesState.clearMessages();
  }

  function startEdit(service: ServiceRecord) {
    setShowEditor(true);
    setEditingServiceId(service.service_id);
    setDraft(servicesState.serviceToDraft(service));
    servicesState.clearMessages();
  }

  function closeEditor() {
    setShowEditor(false);
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSaving(true);
    try {
      const saved = editingServiceId
        ? await servicesState.update(editingServiceId, draft)
        : await servicesState.create(draft);
      setEditingServiceId(saved.service_id);
      setDraft(servicesState.serviceToDraft(saved));
      navigate(`/services/${encodeURIComponent(saved.service_id)}`);
    } catch {
      // Shared services context populates the error banner for failed mutations.
    } finally {
      setSaving(false);
    }
  }

  function requestDeleteEditingService() {
    if (!editingServiceId) {
      return;
    }
    const serviceId = editingServiceId;
    setConfirmation({
      title: "Delete service",
      body: `Delete '${serviceId}' from the Studio registry. This also removes retained task search documents for the service.`,
      confirmLabel: "Delete Service",
      challengeText: serviceId,
      challengeLabel: `Type ${serviceId} to confirm deletion`,
      onConfirm: async () => {
        await handleDeleteEditingService(serviceId);
      },
    });
  }

  async function handleDeleteEditingService(serviceId: string) {
    setSaving(true);
    try {
      await servicesState.remove(serviceId);
      startCreate();
    } catch {
      // Shared services context populates the error banner for failed mutations.
    } finally {
      setSaving(false);
      setConfirmation(null);
    }
  }

  async function handleServiceSearch(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSearchLoading(true);
    setSearchError(null);
    try {
      const payload = await searchServices({ ...searchDraft, limit: 25 });
      setSearchResults(payload.items);
    } catch (fetchError) {
      setSearchResults(null);
      setSearchError(fetchError instanceof Error ? fetchError.message : "Unable to search services.");
    } finally {
      setSearchLoading(false);
    }
  }

  function clearServiceSearch() {
    setSearchDraft({
      query: "",
      environment: "",
      status: "",
      health: "",
      tag: "",
    });
    setSearchError(null);
    setSearchResults(null);
  }

  const unreachableCount = servicesState.services.filter((service) => service.health?.overall_status === "unreachable").length;
  const staleOrDegradedCount = servicesState.services.filter((service) =>
    service.health ? ["stale", "degraded"].includes(service.health.overall_status) : false,
  ).length;
  const disabledCount = servicesState.services.filter((service) => service.status === "disabled").length;

  useEffect(() => {
    if (showEditor) {
      editorRef.current?.scrollIntoView?.({ behavior: "smooth", block: "start" });
    }
  }, [editingServiceId, showEditor]);

  return (
    <div className="studio-stack-lg">
      {servicesState.error ? <NoticeBanner tone="error">{servicesState.error}</NoticeBanner> : null}
      {servicesState.notice ? <NoticeBanner>{servicesState.notice}</NoticeBanner> : null}

      <SectionCard
        title="Registry Overview"
        subtitle="The Studio backend serves the registry from `/studio/services`."
        className="studio-section-card--compact"
        action={
          <button type="button" onClick={() => void servicesState.reload()} style={secondaryButtonStyle}>
            <StudioIcon name="refresh" />
            Reload List
          </button>
        }
      >
        <div className="studio-metrics-grid studio-metrics-grid--4 studio-metrics-grid--compact">
          <MetricCard label="Services" value={String(servicesState.services.length)} className="studio-metric-card--compact" />
          <MetricCard label="Unreachable" value={String(unreachableCount)} className="studio-metric-card--compact" />
          <MetricCard label="Stale / Degraded" value={String(staleOrDegradedCount)} className="studio-metric-card--compact" />
          <MetricCard label="Disabled" value={String(disabledCount)} className="studio-metric-card--compact" />
        </div>
      </SectionCard>

      <SectionCard
        title="Service Search"
        subtitle="Find services by name, id, environment, registry state, runtime health, or tag."
        className="studio-section-card--compact"
      >
        <form onSubmit={handleServiceSearch} className="studio-form-grid studio-form-grid--service-search">
          <label className="studio-filter-field studio-filter-field--wide">
            <span>Keyword</span>
            <input
              value={searchDraft.query}
              onChange={(event) => setSearchDraft((current) => ({ ...current, query: event.target.value }))}
              placeholder="orders, payments, base URL"
              style={inputStyle}
            />
          </label>
          <label className="studio-filter-field">
            <span>Environment</span>
            <input
              value={searchDraft.environment}
              onChange={(event) => setSearchDraft((current) => ({ ...current, environment: event.target.value }))}
              placeholder="mock, prod"
              style={inputStyle}
            />
          </label>
          <label className="studio-filter-field">
            <span>Registry</span>
            <select
              value={searchDraft.status}
              onChange={(event) => setSearchDraft((current) => ({ ...current, status: event.target.value }))}
              style={inputStyle}
            >
              <option value="">Any registry state</option>
              <option value="registered">Registered</option>
              <option value="healthy">Healthy</option>
              <option value="unavailable">Unavailable</option>
              <option value="disabled">Disabled</option>
            </select>
          </label>
          <label className="studio-filter-field">
            <span>Runtime Health</span>
            <select
              value={searchDraft.health}
              onChange={(event) => setSearchDraft((current) => ({ ...current, health: event.target.value }))}
              style={inputStyle}
            >
              <option value="">Any health</option>
              <option value="healthy">Healthy</option>
              <option value="degraded">Degraded</option>
              <option value="stale">Stale</option>
              <option value="unreachable">Unreachable</option>
              <option value="disabled">Disabled</option>
              <option value="unknown">Unknown</option>
            </select>
          </label>
          <label className="studio-filter-field">
            <span>Tag</span>
            <input
              value={searchDraft.tag}
              onChange={(event) => setSearchDraft((current) => ({ ...current, tag: event.target.value }))}
              placeholder="core, checkout"
              style={inputStyle}
            />
          </label>
          <div className="studio-search-actions">
            <button type="submit" style={primaryButtonStyle}>
              <StudioIcon name="search" />
              Search Services
            </button>
            <button type="button" onClick={clearServiceSearch} style={secondaryButtonStyle}>
              <StudioIcon name="clear" />
              Clear
            </button>
          </div>
        </form>
        {searchError ? <p style={{ ...mutedTextStyle, color: "var(--studio-danger)" }}>{searchError}</p> : null}
        {searchLoading ? <p style={mutedTextStyle}>Searching services...</p> : null}
        {searchResults ? (
          <div className="studio-stack-sm">
            {!searchResults.length ? <p style={mutedTextStyle}>No matching services found.</p> : null}
            {searchResults.map((service) => (
              <article
                key={`${service.service_id}-search`}
                className="studio-subcard"
                style={{ borderRadius: 14, padding: 14, display: "grid", gap: 6 }}
              >
                <div className="studio-list-card__top">
                  <strong>{service.name}</strong>
                  <Link to={`/services/${encodeURIComponent(service.service_id)}`} style={{ ...secondaryButtonStyle, textDecoration: "none" }}>
                    <StudioIcon name="open" />
                    Open
                  </Link>
                </div>
                <span className="studio-inline-meta" style={{ fontSize: 13 }}>
                  {service.service_id} · {service.environment} · registry={service.status}
                  {service.health_status ? ` · health=${service.health_status}` : ""}
                </span>
                <span className="studio-inline-meta" style={{ fontSize: 13 }}>
                  matched fields: {service.matched_fields.length ? service.matched_fields.join(", ") : "structured filters only"}
                </span>
              </article>
            ))}
          </div>
        ) : null}
      </SectionCard>

      {showEditor ? (
        <div ref={editorRef}>
          <SectionCard
            title={editingServiceId ? "Edit Service" : "Register Service"}
            subtitle={
              editingServiceId
                ? "Update operator-managed metadata or move the service to a different environment."
                : "Create a durable service entry in the Studio registry."
            }
            action={
              <div className="studio-action-row">
                {editingServiceId ? (
                  <button type="button" onClick={startCreate} style={secondaryButtonStyle}>
                    <StudioIcon name="add" />
                    New Draft
                  </button>
                ) : null}
                <button type="button" onClick={closeEditor} style={secondaryButtonStyle}>
                  <StudioIcon name="clear" />
                  Close
                </button>
              </div>
            }
          >
          <form onSubmit={handleSubmit} className="studio-stack-sm">
            <label style={{ display: "grid", gap: 6, fontSize: 13 }}>
              Service id
              <input
                value={draft.service_id}
                onChange={(event) => setDraft((current) => ({ ...current, service_id: event.target.value }))}
                placeholder="payments-api"
                style={inputStyle}
                disabled={Boolean(editingServiceId)}
              />
            </label>
            <label style={{ display: "grid", gap: 6, fontSize: 13 }}>
              Name
              <input
                value={draft.name}
                onChange={(event) => setDraft((current) => ({ ...current, name: event.target.value }))}
                placeholder="Payments API"
                style={inputStyle}
              />
            </label>
            <label style={{ display: "grid", gap: 6, fontSize: 13 }}>
              Base URL
              <input
                value={draft.base_url}
                onChange={(event) => setDraft((current) => ({ ...current, base_url: event.target.value }))}
                placeholder="https://service.example.test"
                style={inputStyle}
              />
            </label>
            <label style={{ display: "grid", gap: 6, fontSize: 13 }}>
              Environment
              <input
                value={draft.environment}
                onChange={(event) => setDraft((current) => ({ ...current, environment: event.target.value }))}
                placeholder="prod"
                style={inputStyle}
              />
            </label>
            <label style={{ display: "grid", gap: 6, fontSize: 13 }}>
              Tags
              <input
                value={draft.tags}
                onChange={(event) => setDraft((current) => ({ ...current, tags: event.target.value }))}
                placeholder="core, edge, latency-sensitive"
                style={inputStyle}
              />
            </label>
            <label style={{ display: "grid", gap: 6, fontSize: 13 }}>
              Auth mode
              <input
                value={draft.auth_mode}
                onChange={(event) => setDraft((current) => ({ ...current, auth_mode: event.target.value }))}
                placeholder="internal_network"
                style={inputStyle}
              />
            </label>

            <details style={insetSurfaceStyle}>
              <summary style={{ cursor: "pointer", fontSize: 16, fontWeight: 700, listStyle: "none" }}>Log Configuration</summary>
              <div className="studio-stack-sm" style={{ marginTop: 12 }}>
                <p style={mutedTextStyle}>Optional per-service Loki query settings for Studio log panels.</p>
                <label style={{ display: "grid", gap: 6, fontSize: 13 }}>
                  Log provider
                  <select
                    value={draft.log_provider}
                    onChange={(event) =>
                      setDraft((current) => ({ ...current, log_provider: event.target.value as "" | "loki" }))
                    }
                    style={inputStyle}
                  >
                    <option value="">Disabled</option>
                    <option value="loki">Loki</option>
                  </select>
                </label>
                <label style={{ display: "grid", gap: 6, fontSize: 13 }}>
                  Log base URL
                  <input
                    value={draft.log_base_url}
                    onChange={(event) => setDraft((current) => ({ ...current, log_base_url: event.target.value }))}
                    placeholder="https://loki.example.test"
                    style={inputStyle}
                  />
                </label>
                <label style={{ display: "grid", gap: 6, fontSize: 13 }}>
                  Loki tenant id
                  <input
                    value={draft.log_tenant_id}
                    onChange={(event) => setDraft((current) => ({ ...current, log_tenant_id: event.target.value }))}
                    placeholder="optional"
                    style={inputStyle}
                  />
                </label>
                <label style={{ display: "grid", gap: 6, fontSize: 13 }}>
                  Service label key
                  <input
                    value={draft.log_service_label_key}
                    onChange={(event) =>
                      setDraft((current) => ({ ...current, log_service_label_key: event.target.value }))
                    }
                    placeholder="service"
                    style={inputStyle}
                  />
                </label>
                <label style={{ display: "grid", gap: 6, fontSize: 13 }}>
                  Service label value
                  <input
                    value={draft.log_service_label_value}
                    onChange={(event) =>
                      setDraft((current) => ({ ...current, log_service_label_value: event.target.value }))
                    }
                    placeholder="checker-service"
                    style={inputStyle}
                  />
                </label>
                <label style={{ display: "grid", gap: 6, fontSize: 13 }}>
                  App label key
                  <input
                    value={draft.log_app_label_key}
                    onChange={(event) => setDraft((current) => ({ ...current, log_app_label_key: event.target.value }))}
                    placeholder="app"
                    style={inputStyle}
                  />
                </label>
                <label style={{ display: "grid", gap: 6, fontSize: 13 }}>
                  Additional selector labels
                  <input
                    value={draft.log_service_selector_labels}
                    onChange={(event) =>
                      setDraft((current) => ({ ...current, log_service_selector_labels: event.target.value }))
                    }
                    placeholder="namespace=prod"
                    style={inputStyle}
                  />
                </label>
                <label style={{ display: "grid", gap: 6, fontSize: 13 }}>
                  Task id label
                  <input
                    value={draft.log_task_id_label}
                    onChange={(event) => setDraft((current) => ({ ...current, log_task_id_label: event.target.value }))}
                    placeholder="task_id"
                    style={inputStyle}
                  />
                </label>
                <label style={{ display: "grid", gap: 6, fontSize: 13 }}>
                  Correlation id label
                  <input
                    value={draft.log_correlation_id_label}
                    onChange={(event) =>
                      setDraft((current) => ({ ...current, log_correlation_id_label: event.target.value }))
                    }
                    placeholder="correlation_id"
                    style={inputStyle}
                  />
                </label>
                <label style={{ display: "grid", gap: 6, fontSize: 13 }}>
                  Level label
                  <input
                    value={draft.log_level_label}
                    onChange={(event) => setDraft((current) => ({ ...current, log_level_label: event.target.value }))}
                    placeholder="level"
                    style={inputStyle}
                  />
                </label>
                <label style={{ display: "grid", gap: 6, fontSize: 13 }}>
                  Task match mode
                  <select
                    value={draft.log_task_match_mode}
                    onChange={(event) =>
                      setDraft((current) => ({
                        ...current,
                        log_task_match_mode: event.target.value as "label" | "contains" | "regex",
                      }))
                    }
                    style={inputStyle}
                  >
                    <option value="label">label</option>
                    <option value="contains">contains</option>
                    <option value="regex">regex</option>
                  </select>
                </label>
                <label style={{ display: "grid", gap: 6, fontSize: 13 }}>
                  Task match template
                  <input
                    value={draft.log_task_match_template}
                    onChange={(event) =>
                      setDraft((current) => ({ ...current, log_task_match_template: event.target.value }))
                    }
                    placeholder="{task_id}"
                    style={inputStyle}
                  />
                </label>
              </div>
            </details>

            <button type="submit" disabled={saving} style={primaryButtonStyle}>
              <StudioIcon name="save" />
              {saving ? "Saving..." : editingServiceId ? "Save Service" : "Register Service"}
            </button>
          </form>

          {editingServiceId ? (
            <SectionCard title="Editing Target" subtitle="Open the detail route for the service you are modifying.">
              <div className="studio-action-row">
                <Link to={`/services/${encodeURIComponent(editingServiceId)}`} style={{ ...secondaryButtonStyle, textDecoration: "none" }}>
                  <StudioIcon name="open" />
                  Open Detail Page
                </Link>
                <button
                  type="button"
                  onClick={requestDeleteEditingService}
                  disabled={saving}
                  style={destructiveButtonStyle}
                >
                  <StudioIcon name="delete" />
                  {saving ? "Deleting..." : "Delete Service"}
                </button>
              </div>
            </SectionCard>
          ) : null}
          </SectionCard>
        </div>
      ) : null}

      <SectionCard
        title="Registered Services"
        subtitle="Choose a service to open the routed detail view, topology page, or DLQ explorer."
        className="studio-section-card--featured"
        action={
          <button type="button" onClick={startCreate} style={secondaryButtonStyle}>
            <StudioIcon name="add" />
            New Service
          </button>
        }
      >
        {servicesState.loading ? <p style={mutedTextStyle}>Loading services...</p> : null}
        {!servicesState.loading && servicesState.services.length === 0 ? (
          <EmptyState title="No services registered" body="Create the first service entry to give Studio a control-plane inventory." />
        ) : null}
        {servicesState.services.length > 0 ? (
          <>
            <div className="studio-table-wrap studio-desktop-only">
              <table className="studio-table">
                <thead>
                  <tr>
                    <th>Service</th>
                    <th>Environment</th>
                    <th>Registry</th>
                    <th>Runtime Health</th>
                    <th>Base URL</th>
                    <th style={{ textAlign: "right" }}>Action</th>
                  </tr>
                </thead>
                <tbody>
                  {servicesState.services.map((service) => (
                    <tr key={service.service_id}>
                      <td>
                        <div style={{ display: "grid", gap: 4 }}>
                          <strong>{service.name}</strong>
                          <span className="studio-inline-meta">{service.service_id}</span>
                        </div>
                      </td>
                      <td>{service.environment}</td>
                      <td>
                        <StatusBadge status={service.status} />
                      </td>
                      <td>
                        <HealthBadge status={service.health?.overall_status || "unknown"} />
                      </td>
                      <td style={{ color: "var(--studio-text-muted)" }}>{service.base_url}</td>
                      <td style={{ textAlign: "right" }}>
                        <div style={{ display: "flex", justifyContent: "end", gap: 8, flexWrap: "wrap" }}>
                          <Link
                            to={`/services/${encodeURIComponent(service.service_id)}`}
                            style={{ ...secondaryButtonStyle, textDecoration: "none" }}
                          >
                            <StudioIcon name="open" />
                            View
                          </Link>
                          <button type="button" onClick={() => startEdit(service)} style={secondaryButtonStyle}>
                            <StudioIcon name="edit" />
                            Edit
                          </button>
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            <div className="studio-card-list studio-mobile-only">
              {servicesState.services.map((service) => (
                <article key={`${service.service_id}-card`} className="studio-subcard studio-list-card">
                  <div className="studio-list-card__top">
                    <div style={{ display: "grid", gap: 4 }}>
                      <strong>{service.name}</strong>
                      <span className="studio-inline-meta">{service.service_id}</span>
                    </div>
                    <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
                      <StatusBadge status={service.status} />
                      <HealthBadge status={service.health?.overall_status || "unknown"} />
                    </div>
                  </div>
                  <div className="studio-list-card__meta">
                    <span className="studio-inline-meta">{service.environment}</span>
                    <span className="studio-inline-meta">{service.base_url}</span>
                  </div>
                  <div className="studio-action-row">
                    <Link
                      to={`/services/${encodeURIComponent(service.service_id)}`}
                      aria-label={`View ${service.service_id}`}
                      style={{ ...secondaryButtonStyle, textDecoration: "none" }}
                    >
                      <StudioIcon name="open" />
                      View
                    </Link>
                    <button
                      type="button"
                      onClick={() => startEdit(service)}
                      aria-label={`Edit ${service.service_id}`}
                      style={secondaryButtonStyle}
                    >
                      <StudioIcon name="edit" />
                      Edit
                    </button>
                  </div>
                </article>
              ))}
            </div>
          </>
        ) : null}
      </SectionCard>
      {confirmation ? (
        <ConfirmationDialog
          title={confirmation.title}
          body={<p>{confirmation.body}</p>}
          confirmLabel={confirmation.confirmLabel}
          challengeText={confirmation.challengeText}
          challengeLabel={confirmation.challengeLabel}
          pending={saving}
          tone="danger"
          onCancel={() => setConfirmation(null)}
          onConfirm={() => void confirmation.onConfirm()}
        />
      ) : null}
    </div>
  );
}

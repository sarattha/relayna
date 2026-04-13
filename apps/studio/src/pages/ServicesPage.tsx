import type { FormEvent } from "react";
import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";

import { searchServices } from "../api";
import { useStudioServices } from "../services-context";
import {
  EmptyState,
  HealthBadge,
  MetricCard,
  NoticeBanner,
  SectionCard,
  StatusBadge,
  destructiveButtonStyle,
  insetSurfaceStyle,
  inputStyle,
  mutedTextStyle,
  primaryButtonStyle,
  secondaryButtonStyle,
} from "../ui";
import type { ServiceDraft, ServiceRecord, StudioServiceSearchItem } from "../types";

export function ServicesPage() {
  const navigate = useNavigate();
  const servicesState = useStudioServices();
  const [showEditor, setShowEditor] = useState(false);
  const [editingServiceId, setEditingServiceId] = useState<string | null>(null);
  const [draft, setDraft] = useState<ServiceDraft>(servicesState.emptyDraft);
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

  async function handleDeleteEditingService() {
    if (!editingServiceId) {
      return;
    }
    setSaving(true);
    try {
      await servicesState.remove(editingServiceId);
      startCreate();
    } catch {
      // Shared services context populates the error banner for failed mutations.
    } finally {
      setSaving(false);
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

  const unreachableCount = servicesState.services.filter((service) => service.health?.overall_status === "unreachable").length;
  const staleOrDegradedCount = servicesState.services.filter((service) =>
    service.health ? ["stale", "degraded"].includes(service.health.overall_status) : false,
  ).length;
  const disabledCount = servicesState.services.filter((service) => service.status === "disabled").length;

  return (
    <div className="studio-stack-lg">
      {servicesState.error ? <NoticeBanner tone="error">{servicesState.error}</NoticeBanner> : null}
      {servicesState.notice ? <NoticeBanner>{servicesState.notice}</NoticeBanner> : null}

      <SectionCard
        title="Registry Overview"
        subtitle="The Studio backend serves the registry from `/studio/services`."
        action={
          <button type="button" onClick={() => void servicesState.reload()} style={secondaryButtonStyle}>
            Reload List
          </button>
        }
      >
        <div className="studio-metrics-grid studio-metrics-grid--4">
          <MetricCard label="Services" value={String(servicesState.services.length)} />
          <MetricCard label="Unreachable" value={String(unreachableCount)} />
          <MetricCard label="Stale / Degraded" value={String(staleOrDegradedCount)} />
          <MetricCard label="Disabled" value={String(disabledCount)} />
        </div>
      </SectionCard>

      <SectionCard title="Service Search" subtitle="Search registered services with lightweight fuzzy matching plus structured filters.">
        <form onSubmit={handleServiceSearch} className="studio-form-grid studio-form-grid--service-search">
          <input
            value={searchDraft.query}
            onChange={(event) => setSearchDraft((current) => ({ ...current, query: event.target.value }))}
            placeholder="query"
            style={inputStyle}
          />
          <input
            value={searchDraft.environment}
            onChange={(event) => setSearchDraft((current) => ({ ...current, environment: event.target.value }))}
            placeholder="environment"
            style={inputStyle}
          />
          <input
            value={searchDraft.status}
            onChange={(event) => setSearchDraft((current) => ({ ...current, status: event.target.value }))}
            placeholder="status"
            style={inputStyle}
          />
          <input
            value={searchDraft.health}
            onChange={(event) => setSearchDraft((current) => ({ ...current, health: event.target.value }))}
            placeholder="health"
            style={inputStyle}
          />
          <input
            value={searchDraft.tag}
            onChange={(event) => setSearchDraft((current) => ({ ...current, tag: event.target.value }))}
            placeholder="tag"
            style={inputStyle}
          />
          <button type="submit" style={primaryButtonStyle}>
            Search Services
          </button>
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
                  New Draft
                </button>
              ) : null}
              <button type="button" onClick={closeEditor} style={secondaryButtonStyle}>
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
                  Service selector labels
                  <input
                    value={draft.log_service_selector_labels}
                    onChange={(event) =>
                      setDraft((current) => ({ ...current, log_service_selector_labels: event.target.value }))
                    }
                    placeholder="app=payments-api, namespace=prod"
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
              </div>
            </details>

            <button type="submit" disabled={saving} style={primaryButtonStyle}>
              {saving ? "Saving..." : editingServiceId ? "Save Service" : "Register Service"}
            </button>
          </form>

          {editingServiceId ? (
            <SectionCard title="Editing Target" subtitle="Open the detail route for the service you are modifying.">
              <div className="studio-action-row">
                <Link to={`/services/${encodeURIComponent(editingServiceId)}`} style={{ ...secondaryButtonStyle, textDecoration: "none" }}>
                  Open Detail Page
                </Link>
                <button
                  type="button"
                  onClick={() => void handleDeleteEditingService()}
                  disabled={saving}
                  style={destructiveButtonStyle}
                >
                  {saving ? "Deleting..." : "Delete Service"}
                </button>
              </div>
            </SectionCard>
          ) : null}
        </SectionCard>
      ) : null}

      <SectionCard
        title="Registered Services"
        subtitle="Choose a service to open the routed detail view, topology page, or DLQ explorer."
        action={
          <button type="button" onClick={startCreate} style={secondaryButtonStyle}>
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
                            View
                          </Link>
                          <button type="button" onClick={() => startEdit(service)} style={secondaryButtonStyle}>
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
                      View
                    </Link>
                    <button
                      type="button"
                      onClick={() => startEdit(service)}
                      aria-label={`Edit ${service.service_id}`}
                      style={secondaryButtonStyle}
                    >
                      Edit
                    </button>
                  </div>
                </article>
              ))}
            </div>
          </>
        ) : null}
      </SectionCard>
    </div>
  );
}

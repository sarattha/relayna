import { Link, useSearchParams } from "react-router-dom";

import { useStudioServices } from "../services-context";
import {
  EmptyState,
  HealthBadge,
  MetricCard,
  NoticeBanner,
  SectionCard,
  StudioIcon,
  formatTimestamp,
  secondaryButtonStyle,
} from "../ui";

const incidentStatuses = new Set(["degraded", "stale", "unreachable"]);

export function OverviewPage() {
  const servicesState = useStudioServices();
  const [searchParams] = useSearchParams();
  const environment = searchParams.get("environment") || "";
  const scopedServices = environment
    ? servicesState.services.filter((service) => service.environment === environment)
    : servicesState.services;
  const incidents = scopedServices.filter((service) =>
    incidentStatuses.has(service.health?.overall_status || "unknown"),
  );
  const healthy = scopedServices.filter((service) => service.health?.overall_status === "healthy").length;
  const unknown = scopedServices.filter((service) => {
    const status = service.health?.overall_status;
    return !status || status === "unknown";
  }).length;

  return (
    <div className="studio-stack-lg">
      {servicesState.error ? <NoticeBanner tone="error">{servicesState.error}</NoticeBanner> : null}

      <section className="studio-operations-header" aria-labelledby="operations-title">
        <div>
          <p className="studio-page-eyebrow">Operational overview</p>
          <h1 id="operations-title">What needs attention now</h1>
          <p>
            {environment ? `Environment: ${environment}. ` : "All environments. "}
            Services with degraded, stale, or unreachable health appear first.
          </p>
        </div>
        <Link to="/failed-tasks" className="studio-button-link studio-button-link--primary">
          <StudioIcon name="dlq" />
          Investigate failed tasks
        </Link>
      </section>

      <div className="studio-metrics-grid studio-metrics-grid--4 studio-metrics-grid--compact">
        <MetricCard label="Scoped services" value={String(scopedServices.length)} className="studio-metric-card--compact" />
        <MetricCard label="Needs attention" value={String(incidents.length)} className="studio-metric-card--compact" />
        <MetricCard label="Healthy" value={String(healthy)} className="studio-metric-card--compact" />
        <MetricCard label="Unknown" value={String(unknown)} className="studio-metric-card--compact" />
      </div>

      <SectionCard
        title="Service incidents"
        subtitle="Runtime health signals ordered for operator triage."
        className="studio-section-card--compact"
        action={
          <Link to="/services" style={{ ...secondaryButtonStyle, textDecoration: "none" }}>
            Manage registry
          </Link>
        }
      >
        {servicesState.loading ? <p>Loading service health…</p> : null}
        {!servicesState.loading && !incidents.length ? (
          <EmptyState
            title="No active service incidents"
            body="No degraded, stale, or unreachable services are visible in the selected environment."
          />
        ) : null}
        {incidents.length ? (
          <div className="studio-incident-list">
            {incidents.map((service) => (
              <article key={service.service_id} className="studio-incident-row">
                <div>
                  <strong>{service.name}</strong>
                  <span>{service.environment} · {service.service_id}</span>
                </div>
                <HealthBadge status={service.health?.overall_status || "unknown"} />
                <span className="studio-inline-meta">
                  Checked {formatTimestamp(service.health?.last_checked_at)}
                </span>
                <Link
                  to={`/services/${encodeURIComponent(service.service_id)}`}
                  className="studio-button-link"
                >
                  Open service
                </Link>
              </article>
            ))}
          </div>
        ) : null}
      </SectionCard>
    </div>
  );
}

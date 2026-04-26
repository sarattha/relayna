import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";

import { fetchTopology } from "../api";
import { useStudioServices } from "../services-context";
import {
  InlineCodeBox,
  NoticeBanner,
  SectionCard,
  StudioIcon,
  WorkflowTopologySurface,
  mutedTextStyle,
  secondaryButtonStyle,
} from "../ui";
import type { WorkflowTopologyResponse } from "../types";

export function TopologyPage() {
  const { serviceId = "" } = useParams();
  const servicesState = useStudioServices();
  const service = servicesState.servicesById.get(serviceId) || null;

  const [topology, setTopology] = useState<WorkflowTopologyResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!serviceId) {
      return;
    }
    void load();
  }, [serviceId]);

  async function load() {
    setLoading(true);
    setError(null);
    try {
      const payload = await fetchTopology(serviceId);
      setTopology(payload);
    } catch (fetchError) {
      setError(fetchError instanceof Error ? fetchError.message : "Unable to load workflow topology.");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="studio-stack-lg">
      {error ? <NoticeBanner tone="error">{error}</NoticeBanner> : null}

      <SectionCard
        title="Workflow Topology"
        subtitle="Service-scoped topology route backed by `/studio/services/:serviceId/workflow/topology`."
        action={
          <div style={{ display: "flex", gap: 10 }}>
            <Link to={`/services/${encodeURIComponent(serviceId)}`} style={{ ...secondaryButtonStyle, textDecoration: "none" }}>
              <StudioIcon name="back" />
              Back to Service
            </Link>
            <button type="button" onClick={() => void load()} style={secondaryButtonStyle}>
              <StudioIcon name="refresh" />
              Reload
            </button>
          </div>
        }
      >
        {service ? (
          <p style={mutedTextStyle}>
            Viewing topology for <strong>{service.name}</strong> ({service.service_id}).
          </p>
        ) : null}
        {loading ? <p style={mutedTextStyle}>Loading topology...</p> : null}
        {!loading && topology ? <WorkflowTopologySurface topology={topology} /> : null}
        {!loading && topology ? (
          <div className="studio-stack-sm">
            <h3 className="studio-code-title">Raw Topology Payload</h3>
            <InlineCodeBox value={JSON.stringify(topology, null, 2)} minHeight={240} />
          </div>
        ) : null}
      </SectionCard>
    </div>
  );
}

import type { CSSProperties, FormEvent } from "react";
import { startTransition, useEffect, useState } from "react";
import {
  Background,
  Controls,
  Edge,
  MiniMap,
  Node,
  Panel,
  ReactFlow,
  ReactFlowProvider,
} from "@xyflow/react";

type ServiceStatus = "registered" | "healthy" | "unavailable" | "disabled";

type ServiceRecord = {
  service_id: string;
  name: string;
  base_url: string;
  environment: string;
  tags: string[];
  auth_mode: string;
  status: ServiceStatus;
  capabilities?: Record<string, unknown> | null;
  last_seen_at?: string | null;
};

type ServiceListResponse = {
  count: number;
  services: ServiceRecord[];
};

type ServiceDraft = {
  service_id: string;
  name: string;
  base_url: string;
  environment: string;
  tags: string;
  auth_mode: string;
};

type ExecutionGraphNode = {
  id: string;
  kind: string;
  task_id?: string | null;
  label?: string | null;
  timestamp?: string | null;
  annotations?: Record<string, unknown>;
};

type ExecutionGraphEdge = {
  source: string;
  target: string;
  kind: string;
  timestamp?: string | null;
  annotations?: Record<string, unknown>;
};

type ExecutionGraphSummary = {
  status?: string | null;
  started_at?: string | null;
  ended_at?: string | null;
  duration_ms?: number | null;
  graph_completeness: string;
};

type ExecutionGraph = {
  service_id?: string;
  task_id: string;
  topology_kind: string;
  summary: ExecutionGraphSummary;
  nodes: ExecutionGraphNode[];
  edges: ExecutionGraphEdge[];
  annotations: Record<string, unknown>;
  related_task_ids: string[];
};

type FederatedError = {
  detail: string;
  code: string;
  service_id?: string | null;
  upstream_status?: number | null;
  retryable: boolean;
};

type StatusPayload = {
  service_id: string;
  task_id: string;
  event: Record<string, unknown>;
};

type HistoryPayload = {
  service_id: string;
  count: number;
  events: Array<Record<string, unknown>>;
};

type DlqMessagesPayload = {
  service_id: string;
  items: Array<Record<string, unknown>>;
  next_cursor?: string | null;
};

type StudioTaskDetail = {
  service: ServiceRecord;
  service_id: string;
  task_id: string;
  latest_status?: StatusPayload | null;
  history?: HistoryPayload | null;
  dlq_messages?: DlqMessagesPayload | null;
  execution_graph?: ExecutionGraph | null;
  errors: FederatedError[];
};

const frameStyle = {
  border: "1px solid rgba(99, 83, 57, 0.18)",
  borderRadius: 18,
  background: "rgba(255,255,255,0.84)",
  boxShadow: "0 18px 44px rgba(55, 43, 26, 0.10)",
};

const inputStyle: CSSProperties = {
  width: "100%",
  borderRadius: 14,
  border: "1px solid rgba(104, 88, 64, 0.25)",
  padding: "12px 14px",
  background: "#fffdfa",
  color: "#2d2923",
  fontSize: 14,
};

const mutedTextStyle: CSSProperties = {
  margin: 0,
  fontSize: 13,
  lineHeight: 1.55,
  color: "#5f564a",
};

const primaryButtonStyle: CSSProperties = {
  border: "none",
  borderRadius: 14,
  background: "#2f3c53",
  color: "#f8f3e9",
  padding: "12px 16px",
  fontSize: 14,
  fontWeight: 700,
  cursor: "pointer",
};

const secondaryButtonStyle: CSSProperties = {
  borderRadius: 14,
  border: "1px solid rgba(79, 67, 48, 0.22)",
  background: "rgba(255, 251, 244, 0.92)",
  color: "#342b21",
  padding: "10px 14px",
  fontSize: 13,
  fontWeight: 600,
  cursor: "pointer",
};

const destructiveButtonStyle: CSSProperties = {
  ...secondaryButtonStyle,
  borderColor: "rgba(139, 69, 69, 0.28)",
  color: "#7a2424",
};

const kindPalette: Record<string, { background: string; border: string; color: string }> = {
  task: { background: "#f9f4df", border: "#8a7443", color: "#3f3320" },
  aggregation_child: { background: "#e9f0f7", border: "#6c88a8", color: "#233447" },
  task_attempt: { background: "#fff7ec", border: "#cc8d44", color: "#513114" },
  workflow_message: { background: "#eef6ee", border: "#5f8b62", color: "#223725" },
  stage_attempt: { background: "#f4ecfa", border: "#8864a9", color: "#38214f" },
  status_event: { background: "#fffdf5", border: "#a99862", color: "#4c4123" },
  retry: { background: "#fff1f1", border: "#bb6c6c", color: "#582222" },
  dlq_record: { background: "#2f1f1f", border: "#d68f8f", color: "#fff4f4" },
};

const statusPalette: Record<ServiceStatus, { background: string; border: string; color: string }> = {
  registered: { background: "#f5ecda", border: "#aa8640", color: "#5f4312" },
  healthy: { background: "#e9f5ea", border: "#5f8b62", color: "#214226" },
  unavailable: { background: "#fff0e3", border: "#c47d38", color: "#68330d" },
  disabled: { background: "#f3edf0", border: "#897183", color: "#47343f" },
};

const emptyServiceDraft: ServiceDraft = {
  service_id: "",
  name: "",
  base_url: "http://localhost:8000",
  environment: "dev",
  tags: "",
  auth_mode: "internal_network",
};

function serviceToDraft(service: ServiceRecord): ServiceDraft {
  return {
    service_id: service.service_id,
    name: service.name,
    base_url: service.base_url,
    environment: service.environment,
    tags: service.tags.join(", "),
    auth_mode: service.auth_mode,
  };
}

function buildServicePayload(draft: ServiceDraft) {
  return {
    service_id: draft.service_id.trim(),
    name: draft.name.trim(),
    base_url: draft.base_url.trim(),
    environment: draft.environment.trim(),
    tags: draft.tags
      .split(",")
      .map((item) => item.trim())
      .filter(Boolean),
    auth_mode: draft.auth_mode.trim(),
  };
}

function formatTimestamp(value?: string | null) {
  if (!value) {
    return "Never";
  }
  return new Date(value).toLocaleString();
}

async function requestJson<T>(input: string, init?: RequestInit): Promise<T | null> {
  const response = await fetch(input, init);
  const payload = (await response.json().catch(() => null)) as { detail?: string } | null;
  if (!response.ok) {
    throw new Error(payload?.detail || `Request failed with status ${response.status}.`);
  }
  return payload as T | null;
}

function buildFlowNodes(graph: ExecutionGraph): Node[] {
  const incoming = new Map<string, number>();
  const outgoing = new Map<string, string[]>();

  for (const node of graph.nodes) {
    incoming.set(node.id, 0);
    outgoing.set(node.id, []);
  }

  for (const edge of graph.edges) {
    incoming.set(edge.target, (incoming.get(edge.target) ?? 0) + 1);
    outgoing.set(edge.source, [...(outgoing.get(edge.source) ?? []), edge.target]);
  }

  const levels = new Map<string, number>();
  const queue = graph.nodes
    .filter((node) => (incoming.get(node.id) ?? 0) === 0)
    .map((node) => node.id);

  if (queue.length === 0 && graph.nodes[0] !== undefined) {
    queue.push(graph.nodes[0].id);
  }

  for (const rootId of queue) {
    levels.set(rootId, 0);
  }

  let index = 0;
  while (index < queue.length) {
    const currentId = queue[index];
    index += 1;
    const currentLevel = levels.get(currentId) ?? 0;
    for (const targetId of outgoing.get(currentId) ?? []) {
      const nextLevel = currentLevel + 1;
      const existingLevel = levels.get(targetId);
      if (existingLevel === undefined || nextLevel > existingLevel) {
        levels.set(targetId, nextLevel);
      }
      if (!queue.includes(targetId)) {
        queue.push(targetId);
      }
    }
  }

  const columns = new Map<number, ExecutionGraphNode[]>();
  for (const node of graph.nodes) {
    const level = levels.get(node.id) ?? 0;
    columns.set(level, [...(columns.get(level) ?? []), node]);
  }

  const positionedNodes: Node[] = [];
  const sortedLevels = [...columns.keys()].sort((left, right) => left - right);
  for (const level of sortedLevels) {
    const columnNodes = columns.get(level) ?? [];
    columnNodes.sort((left, right) => left.id.localeCompare(right.id));
    columnNodes.forEach((node, row) => {
      const palette = kindPalette[node.kind] ?? {
        background: "#f6f0e7",
        border: "#8f7e65",
        color: "#352d25",
      };
      const labelLines = [node.label || node.id, node.kind];
      if (node.timestamp) {
        labelLines.push(new Date(node.timestamp).toLocaleString());
      }
      positionedNodes.push({
        id: node.id,
        position: { x: level * 310, y: row * 168 },
        data: {
          label: (
            <div style={{ display: "grid", gap: 6 }}>
              <strong style={{ fontSize: 13, lineHeight: 1.2 }}>{labelLines[0]}</strong>
              <span style={{ fontSize: 11, textTransform: "uppercase", letterSpacing: 1.1, opacity: 0.76 }}>
                {labelLines[1]}
              </span>
              {labelLines[2] ? <span style={{ fontSize: 11, opacity: 0.72 }}>{labelLines[2]}</span> : null}
            </div>
          ),
        },
        style: {
          width: 210,
          borderRadius: 18,
          border: `1px solid ${palette.border}`,
          background: palette.background,
          color: palette.color,
          padding: 12,
          boxShadow: "0 10px 24px rgba(47, 39, 28, 0.08)",
        },
      });
    });
  }

  return positionedNodes;
}

function buildFlowEdges(graph: ExecutionGraph): Edge[] {
  return graph.edges.map((edge, index) => ({
    id: `${edge.source}-${edge.target}-${index}`,
    source: edge.source,
    target: edge.target,
    label: edge.kind,
    type: "smoothstep",
    animated: edge.kind === "retried_as" || edge.kind === "stage_transitioned_to",
    labelStyle: {
      fill: "#4a3f32",
      fontSize: 11,
      fontWeight: 600,
    },
    style: {
      stroke: edge.kind === "dead_lettered_to" ? "#a34848" : "#6d6251",
      strokeWidth: edge.kind === "stage_transitioned_to" ? 2.2 : 1.5,
    },
  }));
}

function buildMermaid(graph: ExecutionGraph) {
  const ids = new Map<string, string>();
  const used = new Set<string>();

  for (const node of graph.nodes) {
    const base = `node_${node.id.replace(/[^0-9A-Za-z_]/g, "_").replace(/^_+|_+$/g, "") || "item"}`;
    let candidate = base;
    let suffix = 2;
    while (used.has(candidate)) {
      candidate = `${base}_${suffix}`;
      suffix += 1;
    }
    ids.set(node.id, candidate);
    used.add(candidate);
  }

  const lines = ["flowchart LR"];
  for (const node of graph.nodes) {
    const label = [node.label || node.id, node.kind].filter(Boolean).join("\\n").replace(/"/g, '\\"');
    lines.push(`    ${ids.get(node.id)}["${label}"]`);
  }
  for (const edge of graph.edges) {
    const source = ids.get(edge.source);
    const target = ids.get(edge.target);
    if (!source || !target) {
      continue;
    }
    lines.push(`    ${source} -->|${edge.kind.replace(/"/g, '\\"')}| ${target}`);
  }
  return lines.join("\n");
}

function formatDuration(durationMs?: number | null) {
  if (durationMs == null) {
    return "n/a";
  }
  if (durationMs < 1000) {
    return `${durationMs} ms`;
  }
  return `${(durationMs / 1000).toFixed(durationMs >= 10_000 ? 0 : 2)} s`;
}

function StatusBadge({ status }: { status: ServiceStatus }) {
  const palette = statusPalette[status];
  return (
    <span
      style={{
        display: "inline-flex",
        alignItems: "center",
        justifyContent: "center",
        borderRadius: 999,
        padding: "6px 10px",
        border: `1px solid ${palette.border}`,
        background: palette.background,
        color: palette.color,
        fontSize: 12,
        fontWeight: 700,
        textTransform: "uppercase",
        letterSpacing: 0.9,
      }}
    >
      {status}
    </span>
  );
}

function MetadataRow({ label, value }: { label: string; value: string }) {
  return (
    <div style={{ display: "grid", gap: 2 }}>
      <dt style={{ fontWeight: 700 }}>{label}</dt>
      <dd style={{ margin: 0, opacity: 0.85 }}>{value}</dd>
    </div>
  );
}

function MetricCard({ label, value }: { label: string; value: string }) {
  return (
    <article style={{ ...frameStyle, padding: 16 }}>
      <p style={{ margin: 0, fontSize: 12, textTransform: "uppercase", letterSpacing: 1.2 }}>{label}</p>
      <strong style={{ display: "block", marginTop: 10, fontSize: 26 }}>{value}</strong>
    </article>
  );
}

function GraphSurface({ graph }: { graph: ExecutionGraph }) {
  const nodes = buildFlowNodes(graph);
  const edges = buildFlowEdges(graph);

  return (
    <div style={{ ...frameStyle, overflow: "hidden", minHeight: 560 }}>
      <ReactFlow nodes={nodes} edges={edges} fitView proOptions={{ hideAttribution: true }}>
        <Background gap={20} color="rgba(117, 103, 79, 0.18)" />
        <Controls />
        <MiniMap
          pannable
          zoomable
          style={{ background: "rgba(249, 245, 236, 0.96)", border: "1px solid rgba(110, 95, 70, 0.2)" }}
        />
        <Panel
          position="top-right"
          style={{
            ...frameStyle,
            margin: 14,
            padding: "10px 12px",
            borderRadius: 14,
            fontSize: 12,
            background: "rgba(255,248,235,0.9)",
          }}
        >
          <strong>{graph.topology_kind}</strong>
          <div>{graph.summary.graph_completeness} graph</div>
        </Panel>
      </ReactFlow>
    </div>
  );
}

export function App() {
  const search = typeof window !== "undefined" ? new URLSearchParams(window.location.search) : null;
  const [services, setServices] = useState<ServiceRecord[]>([]);
  const [selectedServiceId, setSelectedServiceId] = useState<string | null>(null);
  const [inspectorServiceId, setInspectorServiceId] = useState<string | null>(search?.get("service_id") || null);
  const [draft, setDraft] = useState<ServiceDraft>(emptyServiceDraft);
  const [editingServiceId, setEditingServiceId] = useState<string | null>(null);
  const [registryLoading, setRegistryLoading] = useState(true);
  const [registrySaving, setRegistrySaving] = useState(false);
  const [registryError, setRegistryError] = useState<string | null>(null);
  const [registryNotice, setRegistryNotice] = useState<string | null>(null);

  const [taskId, setTaskId] = useState(search?.get("task_id") || "");
  const [taskDetail, setTaskDetail] = useState<StudioTaskDetail | null>(null);
  const [loadingGraph, setLoadingGraph] = useState(false);
  const [graphError, setGraphError] = useState<string | null>(null);

  const selectedService = services.find((service) => service.service_id === selectedServiceId) ?? null;
  const graph = taskDetail?.execution_graph || null;

  useEffect(() => {
    void loadServices();
  }, []);

  useEffect(() => {
    if (!search) {
      return;
    }
    const initialTaskId = search.get("task_id");
    const initialServiceId = search.get("service_id");
    if (!initialTaskId || !initialServiceId) {
      return;
    }
    void loadTaskDetail(initialTaskId, initialServiceId);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function loadServices(preferredServiceId?: string | null) {
    setRegistryLoading(true);
    try {
      const payload = await requestJson<ServiceListResponse>("/studio/services");
      const nextServices = payload?.services || [];
      startTransition(() => {
        setServices(nextServices);
        const requestedId = preferredServiceId ?? selectedServiceId;
        const nextSelected =
          nextServices.find((service) => service.service_id === requestedId) ||
          nextServices.find((service) => service.service_id === editingServiceId) ||
          nextServices[0] ||
          null;
        setSelectedServiceId(nextSelected?.service_id || null);
        setInspectorServiceId((current) => {
          if (current && nextServices.some((service) => service.service_id === current)) {
            return current;
          }
          return nextSelected?.service_id || null;
        });
        if (editingServiceId) {
          const editing = nextServices.find((service) => service.service_id === editingServiceId);
          if (editing) {
            setDraft(serviceToDraft(editing));
          } else {
            setEditingServiceId(null);
            setDraft(emptyServiceDraft);
          }
        }
      });
      setRegistryError(null);
    } catch (fetchError) {
      setRegistryError(fetchError instanceof Error ? fetchError.message : "Unable to load services.");
    } finally {
      setRegistryLoading(false);
    }
  }

  async function loadTaskDetail(nextTaskId: string, nextServiceId: string | null) {
    const normalizedTaskId = nextTaskId.trim();
    const normalizedServiceId = (nextServiceId || "").trim();
    if (!normalizedTaskId) {
      setGraphError("Enter a task id to load an execution graph.");
      return;
    }
    if (!normalizedServiceId) {
      setGraphError("Select a registered service to inspect task details.");
      return;
    }

    setLoadingGraph(true);
    setGraphError(null);
    try {
      const payload = await requestJson<StudioTaskDetail>(
        `/studio/tasks/${encodeURIComponent(normalizedServiceId)}/${encodeURIComponent(normalizedTaskId)}`,
      );
      startTransition(() => {
        setTaskDetail(payload);
        setSelectedServiceId(payload?.service_id || normalizedServiceId);
        setInspectorServiceId(payload?.service_id || normalizedServiceId);
      });
      if (typeof window !== "undefined") {
        const params = new URLSearchParams(window.location.search);
        params.set("task_id", normalizedTaskId);
        params.set("service_id", normalizedServiceId);
        params.delete("base_url");
        window.history.replaceState({}, "", `${window.location.pathname}?${params.toString()}`);
      }
    } catch (fetchError) {
      setTaskDetail(null);
      setGraphError(fetchError instanceof Error ? fetchError.message : "Unable to load execution graph.");
    } finally {
      setLoadingGraph(false);
    }
  }

  async function handleRegistrySubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const payload = buildServicePayload(draft);
    const isEditing = Boolean(editingServiceId);
    const url = isEditing ? `/studio/services/${encodeURIComponent(editingServiceId || "")}` : "/studio/services";
    const body = isEditing
      ? JSON.stringify({
          name: payload.name,
          base_url: payload.base_url,
          environment: payload.environment,
          tags: payload.tags,
          auth_mode: payload.auth_mode,
        })
      : JSON.stringify(payload);

    setRegistrySaving(true);
    setRegistryNotice(null);
    try {
      const saved = await requestJson<ServiceRecord>(url, {
        method: isEditing ? "PATCH" : "POST",
        headers: { "Content-Type": "application/json" },
        body,
      });
      setEditingServiceId(saved?.service_id || editingServiceId);
      if (saved) {
        setDraft(serviceToDraft(saved));
      }
      setRegistryError(null);
      setRegistryNotice(
        isEditing
          ? `Updated service '${saved?.service_id || editingServiceId}'.`
          : `Registered service '${saved?.service_id || payload.service_id}'.`,
      );
      await loadServices(saved?.service_id || editingServiceId || payload.service_id);
    } catch (fetchError) {
      setRegistryError(fetchError instanceof Error ? fetchError.message : "Unable to save the service.");
    } finally {
      setRegistrySaving(false);
    }
  }

  async function updateSelectedStatus(nextStatus: ServiceStatus) {
    if (!selectedService) {
      return;
    }
    setRegistrySaving(true);
    setRegistryNotice(null);
    try {
      await requestJson<ServiceRecord>(`/studio/services/${encodeURIComponent(selectedService.service_id)}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ status: nextStatus }),
      });
      setRegistryError(null);
      setRegistryNotice(`Marked '${selectedService.service_id}' as ${nextStatus}.`);
      await loadServices(selectedService.service_id);
    } catch (fetchError) {
      setRegistryError(fetchError instanceof Error ? fetchError.message : "Unable to update service status.");
    } finally {
      setRegistrySaving(false);
    }
  }

  async function refreshSelectedService() {
    if (!selectedService) {
      return;
    }
    setRegistrySaving(true);
    setRegistryNotice(null);
    try {
      const refreshed = await requestJson<ServiceRecord>(
        `/studio/services/${encodeURIComponent(selectedService.service_id)}/refresh`,
        {
        method: "POST",
      },
      );
      setRegistryError(null);
      setRegistryNotice(`Refreshed '${refreshed?.service_id || selectedService.service_id}'.`);
      await loadServices(refreshed?.service_id || selectedService.service_id);
    } catch (fetchError) {
      setRegistryError(fetchError instanceof Error ? fetchError.message : "Unable to refresh service.");
    } finally {
      setRegistrySaving(false);
    }
  }

  async function deleteSelectedService() {
    if (!selectedService) {
      return;
    }
    setRegistrySaving(true);
    setRegistryNotice(null);
    try {
      await requestJson(`/studio/services/${encodeURIComponent(selectedService.service_id)}`, {
        method: "DELETE",
      });
      const deletedServiceId = selectedService.service_id;
      setEditingServiceId((current) => (current === deletedServiceId ? null : current));
      setDraft((current) => (editingServiceId === deletedServiceId ? emptyServiceDraft : current));
      setRegistryError(null);
      setRegistryNotice(`Deleted service '${deletedServiceId}'.`);
      await loadServices(null);
    } catch (fetchError) {
      setRegistryError(fetchError instanceof Error ? fetchError.message : "Unable to delete service.");
    } finally {
      setRegistrySaving(false);
    }
  }

  function startCreateService() {
    setEditingServiceId(null);
    setDraft(emptyServiceDraft);
    setRegistryNotice(null);
    setRegistryError(null);
  }

  function startEditService(service: ServiceRecord) {
    setSelectedServiceId(service.service_id);
    setEditingServiceId(service.service_id);
    setDraft(serviceToDraft(service));
    setRegistryNotice(null);
    setRegistryError(null);
  }

  function handleExecutionSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    void loadTaskDetail(taskId, inspectorServiceId);
  }

  const mermaid = graph ? buildMermaid(graph) : "";
  const latestStatusValue = String(taskDetail?.latest_status?.event?.status || graph?.summary.status || "unknown");
  const historyCount = taskDetail?.history?.count ?? 0;
  const dlqCount = taskDetail?.dlq_messages?.items.length ?? 0;

  return (
    <ReactFlowProvider>
      <main
        style={{
          minHeight: "100vh",
          padding: "36px 22px 48px",
          background: "linear-gradient(180deg, #f3dd9a 0%, #f5f1e9 34%, #d8e4ed 100%)",
          color: "#2f271f",
          fontFamily: "Georgia, 'Iowan Old Style', serif",
        }}
      >
        <div style={{ maxWidth: 1380, margin: "0 auto", display: "grid", gap: 20 }}>
          <header
            style={{
              display: "grid",
              gap: 14,
              gridTemplateColumns: "minmax(0, 1.3fr) minmax(280px, 0.7fr)",
              alignItems: "end",
            }}
          >
            <div>
              <p style={{ letterSpacing: 2, textTransform: "uppercase", fontSize: 12, margin: 0 }}>
                Relayna Studio
              </p>
              <h1 style={{ margin: "6px 0 10px", fontSize: "clamp(2.6rem, 6vw, 4.8rem)", lineHeight: 0.95 }}>
                Service Registry
              </h1>
              <p style={{ margin: 0, maxWidth: 760, fontSize: 18, lineHeight: 1.5 }}>
                Keep a canonical inventory of Relayna services across environments, manage operator-visible
                status, and store the capability document once discovery lands.
              </p>
            </div>
            <section style={{ ...frameStyle, padding: 18, display: "grid", gap: 12 }}>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 12 }}>
                <div>
                  <h2 style={{ margin: 0, fontSize: 20 }}>Registry Overview</h2>
                  <p style={mutedTextStyle}>The Studio backend serves the registry from `/studio/services`.</p>
                </div>
                <button type="button" onClick={() => void loadServices()} style={secondaryButtonStyle}>
                  Reload List
                </button>
              </div>
              <div style={{ display: "grid", gridTemplateColumns: "repeat(3, minmax(0, 1fr))", gap: 10 }}>
                <MetricCard label="Services" value={String(services.length)} />
                <MetricCard
                  label="Unavailable"
                  value={String(services.filter((service) => service.status === "unavailable").length)}
                />
                <MetricCard
                  label="Disabled"
                  value={String(services.filter((service) => service.status === "disabled").length)}
                />
              </div>
            </section>
          </header>

          {registryError ? (
            <section style={{ ...frameStyle, padding: 16, borderColor: "rgba(152, 66, 66, 0.34)", color: "#6f2525" }}>
              {registryError}
            </section>
          ) : null}

          {registryNotice ? (
            <section style={{ ...frameStyle, padding: 16, borderColor: "rgba(79, 133, 85, 0.28)", color: "#224a28" }}>
              {registryNotice}
            </section>
          ) : null}

          <section
            style={{
              display: "grid",
              gap: 20,
              gridTemplateColumns: "minmax(0, 1.35fr) minmax(360px, 0.95fr)",
              alignItems: "start",
            }}
          >
            <section style={{ ...frameStyle, padding: 18, display: "grid", gap: 14 }}>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 12 }}>
                <div>
                  <h2 style={{ margin: 0 }}>Registered Services</h2>
                  <p style={mutedTextStyle}>Select a row to inspect details or start editing an existing entry.</p>
                </div>
                <button type="button" onClick={startCreateService} style={secondaryButtonStyle}>
                  New Service
                </button>
              </div>

              {registryLoading ? <p style={mutedTextStyle}>Loading services...</p> : null}

              {!registryLoading && services.length === 0 ? (
                <section style={{ ...frameStyle, padding: 24, background: "rgba(255,250,243,0.72)" }}>
                  <h3 style={{ marginTop: 0 }}>No services registered</h3>
                  <p style={{ ...mutedTextStyle, marginBottom: 0 }}>
                    Create the first service entry to give Studio a control-plane inventory.
                  </p>
                </section>
              ) : null}

              {services.length > 0 ? (
                <div style={{ overflowX: "auto" }}>
                  <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 14 }}>
                    <thead>
                      <tr style={{ textAlign: "left", borderBottom: "1px solid rgba(97, 84, 62, 0.16)" }}>
                        <th style={{ padding: "0 0 10px" }}>Service</th>
                        <th style={{ padding: "0 0 10px" }}>Environment</th>
                        <th style={{ padding: "0 0 10px" }}>Status</th>
                        <th style={{ padding: "0 0 10px" }}>Base URL</th>
                        <th style={{ padding: "0 0 10px", textAlign: "right" }}>Action</th>
                      </tr>
                    </thead>
                    <tbody>
                      {services.map((service) => {
                        const selected = service.service_id === selectedServiceId;
                        return (
                          <tr
                            key={service.service_id}
                            style={{
                              borderBottom: "1px solid rgba(97, 84, 62, 0.12)",
                              background: selected ? "rgba(247, 239, 222, 0.54)" : "transparent",
                            }}
                          >
                            <td style={{ padding: "14px 8px 14px 0" }}>
                              <div style={{ display: "grid", gap: 4 }}>
                                <strong>{service.name}</strong>
                                <span style={{ fontSize: 12, color: "#62584b" }}>{service.service_id}</span>
                              </div>
                            </td>
                            <td style={{ padding: "14px 8px" }}>{service.environment}</td>
                            <td style={{ padding: "14px 8px" }}>
                              <StatusBadge status={service.status} />
                            </td>
                            <td style={{ padding: "14px 8px", color: "#514739" }}>{service.base_url}</td>
                            <td style={{ padding: "14px 0 14px 8px", textAlign: "right" }}>
                              <button
                                type="button"
                                onClick={() => {
                                  setSelectedServiceId(service.service_id);
                                  setInspectorServiceId(service.service_id);
                                }}
                                style={secondaryButtonStyle}
                              >
                                View
                              </button>
                            </td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
              ) : null}
            </section>

            <aside style={{ display: "grid", gap: 18 }}>
              <section style={{ ...frameStyle, padding: 18, display: "grid", gap: 12 }}>
                <div style={{ display: "flex", justifyContent: "space-between", gap: 12, alignItems: "start" }}>
                  <div>
                    <h2 style={{ margin: 0 }}>{editingServiceId ? "Edit Service" : "Register Service"}</h2>
                    <p style={mutedTextStyle}>
                      {editingServiceId
                        ? "Update operator-managed metadata or move the service to a different environment."
                        : "Create a durable service entry in the Studio registry."}
                    </p>
                  </div>
                  {editingServiceId ? (
                    <button type="button" onClick={startCreateService} style={secondaryButtonStyle}>
                      Clear Form
                    </button>
                  ) : null}
                </div>

                <form onSubmit={handleRegistrySubmit} style={{ display: "grid", gap: 12 }}>
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
                      placeholder="https://payments.internal"
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
                  <button type="submit" disabled={registrySaving} style={primaryButtonStyle}>
                    {registrySaving ? "Saving..." : editingServiceId ? "Save Service" : "Register Service"}
                  </button>
                </form>
              </section>

              <section style={{ ...frameStyle, padding: 18, display: "grid", gap: 14 }}>
                <div style={{ display: "flex", justifyContent: "space-between", gap: 12, alignItems: "start" }}>
                  <div>
                    <h2 style={{ margin: 0 }}>Service Detail</h2>
                    <p style={mutedTextStyle}>Inspect the stored registry metadata and operator actions.</p>
                  </div>
                  {selectedService ? <StatusBadge status={selectedService.status} /> : null}
                </div>

                {!selectedService ? (
                  <p style={mutedTextStyle}>Select a registered service to inspect metadata.</p>
                ) : (
                  <>
                    <dl style={{ margin: 0, display: "grid", gap: 10, fontSize: 13 }}>
                      <MetadataRow label="Service id" value={selectedService.service_id} />
                      <MetadataRow label="Name" value={selectedService.name} />
                      <MetadataRow label="Environment" value={selectedService.environment} />
                      <MetadataRow label="Base URL" value={selectedService.base_url} />
                      <MetadataRow label="Auth mode" value={selectedService.auth_mode} />
                      <MetadataRow
                        label="Tags"
                        value={selectedService.tags.length ? selectedService.tags.join(", ") : "none"}
                      />
                      <MetadataRow label="Last refresh" value={formatTimestamp(selectedService.last_seen_at)} />
                    </dl>

                    <div style={{ display: "flex", flexWrap: "wrap", gap: 10 }}>
                      <button type="button" onClick={() => startEditService(selectedService)} style={secondaryButtonStyle}>
                        Edit
                      </button>
                      <button
                        type="button"
                        onClick={() => void updateSelectedStatus("registered")}
                        style={secondaryButtonStyle}
                      >
                        Enable
                      </button>
                      <button
                        type="button"
                        onClick={() => void updateSelectedStatus("unavailable")}
                        style={secondaryButtonStyle}
                      >
                        Mark Unavailable
                      </button>
                      <button
                        type="button"
                        onClick={() => void updateSelectedStatus("disabled")}
                        style={secondaryButtonStyle}
                      >
                        Disable
                      </button>
                      <button type="button" onClick={() => void refreshSelectedService()} style={secondaryButtonStyle}>
                        Refresh Capabilities
                      </button>
                      <button type="button" onClick={() => void deleteSelectedService()} style={destructiveButtonStyle}>
                        Delete
                      </button>
                    </div>

                    <div style={{ display: "grid", gap: 8 }}>
                      <h3 style={{ margin: 0 }}>Stored Capability Document</h3>
                      {selectedService.capabilities ? (
                        <textarea
                          value={JSON.stringify(selectedService.capabilities, null, 2)}
                          readOnly
                          spellCheck={false}
                          style={{
                            ...inputStyle,
                            minHeight: 180,
                            resize: "vertical",
                            fontFamily: "'SFMono-Regular', Menlo, monospace",
                            fontSize: 12,
                          }}
                        />
                      ) : (
                        <p style={mutedTextStyle}>No capability document stored yet.</p>
                      )}
                    </div>
                  </>
                )}
              </section>
            </aside>
          </section>

          <section style={{ ...frameStyle, padding: 20, display: "grid", gap: 18 }}>
            <div
              style={{
                display: "grid",
                gap: 12,
                gridTemplateColumns: "minmax(0, 1.15fr) minmax(320px, 0.85fr)",
                alignItems: "end",
              }}
            >
              <div>
                <p style={{ letterSpacing: 1.7, textTransform: "uppercase", fontSize: 12, margin: 0 }}>
                  Secondary Tool
                </p>
                <h2 style={{ margin: "6px 0 10px", fontSize: "clamp(2rem, 4vw, 3rem)", lineHeight: 1.02 }}>
                  Execution Graph Inspector
                </h2>
                <p style={{ margin: 0, fontSize: 17, lineHeight: 1.55, maxWidth: 760 }}>
                  Studio now reads task details and execution graphs through the federated backend surface, so the
                  browser only talks to `/studio/*` control-plane routes.
                </p>
              </div>
              <form
                onSubmit={handleExecutionSubmit}
                style={{
                  ...frameStyle,
                  padding: 18,
                  display: "grid",
                  gap: 12,
                  alignSelf: "stretch",
                  background: "rgba(255, 248, 235, 0.9)",
                }}
              >
                <label style={{ display: "grid", gap: 6, fontSize: 13 }}>
                  Registered service
                  <select
                    value={inspectorServiceId || ""}
                    onChange={(event) => setInspectorServiceId(event.target.value || null)}
                    style={inputStyle}
                  >
                    <option value="">Select a service</option>
                    {services.map((service) => (
                      <option key={service.service_id} value={service.service_id}>
                        {service.name} ({service.service_id})
                      </option>
                    ))}
                  </select>
                </label>
                <label style={{ display: "grid", gap: 6, fontSize: 13 }}>
                  Task id
                  <input
                    value={taskId}
                    onChange={(event) => setTaskId(event.target.value)}
                    placeholder="task-123"
                    style={inputStyle}
                  />
                </label>
                <button type="submit" disabled={loadingGraph} style={primaryButtonStyle}>
                  {loadingGraph ? "Loading Graph..." : "Load Execution Graph"}
                </button>
              </form>
            </div>

            {graphError ? (
              <section style={{ ...frameStyle, padding: 16, borderColor: "rgba(152, 66, 66, 0.34)", color: "#6f2525" }}>
                {graphError}
              </section>
            ) : null}

            <section
              style={{
                display: "grid",
                gap: 20,
                gridTemplateColumns: graph ? "minmax(0, 1.8fr) minmax(320px, 0.9fr)" : "1fr",
              }}
            >
              <div style={{ display: "grid", gap: 18 }}>
                {taskDetail ? (
                  <>
                    <div
                      style={{
                        display: "grid",
                        gap: 14,
                        gridTemplateColumns: "repeat(auto-fit, minmax(180px, 1fr))",
                      }}
                    >
                      <MetricCard label="Status" value={latestStatusValue} />
                      <MetricCard label="History Events" value={String(historyCount)} />
                      <MetricCard label="DLQ Messages" value={String(dlqCount)} />
                      <MetricCard
                        label="Graph"
                        value={graph ? `${graph.nodes.length} nodes` : "Unavailable"}
                      />
                    </div>
                    {graph ? (
                      <GraphSurface graph={graph} />
                    ) : (
                      <section style={{ ...frameStyle, padding: 28 }}>
                        <h3 style={{ marginTop: 0 }}>Execution Graph Unavailable</h3>
                        <p style={{ marginBottom: 0, lineHeight: 1.6 }}>
                          Studio loaded task detail successfully, but the federated execution-graph read did not
                          return a graph for this task.
                        </p>
                      </section>
                    )}
                  </>
                ) : (
                  <section style={{ ...frameStyle, padding: 28 }}>
                    <h3 style={{ marginTop: 0 }}>No Task Loaded</h3>
                    <p style={{ marginBottom: 0, lineHeight: 1.6 }}>
                      Select a registered service and load a task id. Studio will call
                      <code> /studio/tasks/&lt;service_id&gt;/&lt;task_id&gt;</code> and render the federated task view.
                    </p>
                  </section>
                )}
              </div>

              <aside style={{ display: "grid", gap: 18 }}>
                <section style={{ ...frameStyle, padding: 18 }}>
                  <h3 style={{ marginTop: 0, marginBottom: 12 }}>Legend</h3>
                  <div style={{ display: "grid", gap: 10 }}>
                    {Object.entries(kindPalette).map(([kind, palette]) => (
                      <div key={kind} style={{ display: "flex", alignItems: "center", gap: 10 }}>
                        <span
                          style={{
                            width: 16,
                            height: 16,
                            borderRadius: 999,
                            background: palette.background,
                            border: `1px solid ${palette.border}`,
                            display: "inline-block",
                          }}
                        />
                        <span style={{ fontSize: 13 }}>{kind}</span>
                      </div>
                    ))}
                  </div>
                </section>

                <section style={{ ...frameStyle, padding: 18 }}>
                  <h3 style={{ marginTop: 0, marginBottom: 12 }}>Mermaid Export</h3>
                  <p style={{ marginTop: 0, fontSize: 13, lineHeight: 1.55 }}>
                    Use this in docs, bug reports, or review comments when you need a lightweight debug artifact.
                  </p>
                  <textarea
                    value={mermaid}
                    readOnly
                    spellCheck={false}
                    style={{
                      width: "100%",
                      minHeight: 260,
                      resize: "vertical",
                      borderRadius: 14,
                      border: "1px solid rgba(104, 88, 64, 0.25)",
                      padding: 12,
                      fontFamily: "'SFMono-Regular', Menlo, monospace",
                      fontSize: 12,
                      background: "#fffdfa",
                      color: "#2d2923",
                    }}
                  />
                </section>

                {graph ? (
                  <section style={{ ...frameStyle, padding: 18 }}>
                    <h3 style={{ marginTop: 0, marginBottom: 12 }}>Graph Metadata</h3>
                    <dl style={{ margin: 0, display: "grid", gap: 10, fontSize: 13 }}>
                      <MetadataRow label="Task id" value={graph.task_id} />
                      <MetadataRow label="Topology" value={graph.topology_kind} />
                      <MetadataRow label="Completeness" value={graph.summary.graph_completeness} />
                      <MetadataRow
                        label="Related tasks"
                        value={graph.related_task_ids.length ? graph.related_task_ids.join(", ") : "none"}
                      />
                    </dl>
                  </section>
                ) : null}

                {taskDetail ? (
                  <section style={{ ...frameStyle, padding: 18 }}>
                    <h3 style={{ marginTop: 0, marginBottom: 12 }}>Task Detail</h3>
                    <dl style={{ margin: 0, display: "grid", gap: 10, fontSize: 13 }}>
                      <MetadataRow label="Service" value={`${taskDetail.service.name} (${taskDetail.service_id})`} />
                      <MetadataRow label="Task id" value={taskDetail.task_id} />
                      <MetadataRow label="Latest status" value={latestStatusValue} />
                      <MetadataRow label="History events" value={String(historyCount)} />
                      <MetadataRow label="DLQ messages" value={String(dlqCount)} />
                      <MetadataRow
                        label="Related tasks"
                        value={graph?.related_task_ids.length ? graph.related_task_ids.join(", ") : "none"}
                      />
                    </dl>
                  </section>
                ) : null}

                {taskDetail?.errors.length ? (
                  <section style={{ ...frameStyle, padding: 18, borderColor: "rgba(152, 66, 66, 0.24)" }}>
                    <h3 style={{ marginTop: 0, marginBottom: 12 }}>Section Errors</h3>
                    <div style={{ display: "grid", gap: 10 }}>
                      {taskDetail.errors.map((error, index) => (
                        <div key={`${error.code}-${index}`} style={{ display: "grid", gap: 4 }}>
                          <strong style={{ fontSize: 13 }}>{error.code}</strong>
                          <span style={{ fontSize: 13, lineHeight: 1.5 }}>{error.detail}</span>
                        </div>
                      ))}
                    </div>
                  </section>
                ) : null}
              </aside>
            </section>
          </section>
        </div>
      </main>
    </ReactFlowProvider>
  );
}

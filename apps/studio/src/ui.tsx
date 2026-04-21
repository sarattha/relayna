import type { CSSProperties, ReactNode } from "react";
import {
  Background,
  Controls,
  type Edge,
  MiniMap,
  type Node,
  Panel,
  ReactFlow,
} from "@xyflow/react";
import { Link } from "react-router-dom";

import type {
  ExecutionGraph,
  HealthStatus,
  JoinKind,
  ServiceStatus,
  StudioControlPlaneEvent,
  StudioTaskPointer,
  StudioTaskRef,
  WorkflowTopologyGraph,
} from "./types";

export const frameStyle = {
  border: "1px solid var(--studio-border)",
  borderRadius: 20,
  background: "var(--studio-surface)",
  boxShadow: "var(--studio-shadow)",
};

export const inputStyle: CSSProperties = {
  width: "100%",
  minWidth: 0,
  borderRadius: 14,
  border: "1px solid var(--studio-border)",
  padding: "12px 14px",
  background: "var(--studio-surface-strong)",
  color: "var(--studio-text)",
  fontSize: 14,
};

export const mutedTextStyle: CSSProperties = {
  margin: 0,
  fontSize: 13,
  lineHeight: 1.55,
  color: "var(--studio-text-muted)",
};

export const primaryButtonStyle: CSSProperties = {
  border: "none",
  borderRadius: 14,
  background: "linear-gradient(135deg, var(--studio-primary) 0%, var(--studio-primary-strong) 100%)",
  color: "var(--studio-primary-contrast)",
  padding: "12px 16px",
  fontSize: 14,
  fontWeight: 700,
  cursor: "pointer",
  boxShadow: "0 14px 28px rgba(218, 107, 43, 0.24)",
};

export const secondaryButtonStyle: CSSProperties = {
  borderRadius: 14,
  border: "1px solid var(--studio-border)",
  background: "var(--studio-surface-muted)",
  color: "var(--studio-secondary-strong)",
  padding: "10px 14px",
  fontSize: 13,
  fontWeight: 600,
  cursor: "pointer",
};

export const destructiveButtonStyle: CSSProperties = {
  ...secondaryButtonStyle,
  borderColor: "rgba(168, 60, 40, 0.26)",
  background: "var(--studio-danger-soft)",
  color: "var(--studio-danger)",
};

export const insetSurfaceStyle: CSSProperties = {
  borderRadius: 16,
  border: "1px solid var(--studio-border)",
  background: "linear-gradient(180deg, rgba(239, 250, 248, 0.92), rgba(255, 247, 236, 0.9))",
  padding: 14,
};

const kindPalette: Record<string, { background: string; border: string; color: string }> = {
  task: { background: "#fff3dd", border: "#cb7b2d", color: "#5d3110" },
  aggregation_child: { background: "#e4f6f3", border: "#3b8f8d", color: "#184a49" },
  task_attempt: { background: "#fff1e5", border: "#e08b48", color: "#663414" },
  workflow_message: { background: "#e6f7f4", border: "#2d8a80", color: "#184841" },
  stage_attempt: { background: "#eff3f2", border: "#6b8d8c", color: "#284443" },
  status_event: { background: "#f9f8ef", border: "#b08a51", color: "#57411d" },
  retry: { background: "#fff0eb", border: "#c46c56", color: "#6a2b1c" },
  dlq_record: { background: "#233f45", border: "#ffb295", color: "#fff5ef" },
};

const statusPalette: Record<ServiceStatus, { background: string; border: string; color: string }> = {
  registered: { background: "#fff2df", border: "#c67a2d", color: "#5f3410" },
  healthy: { background: "#e4f7f2", border: "#27887e", color: "#154741" },
  unavailable: { background: "#fff1e5", border: "#d2823b", color: "#6b3610" },
  disabled: { background: "#edf3f2", border: "#7c8f8d", color: "#314544" },
};

const healthPalette: Record<HealthStatus, { background: string; border: string; color: string }> = {
  healthy: { background: "#e4f7f2", border: "#2b8e7f", color: "#145047" },
  degraded: { background: "#fff2de", border: "#d38434", color: "#6b3d0f" },
  stale: { background: "#fff0e8", border: "#cf7446", color: "#6f3117" },
  unreachable: { background: "#fdebe6", border: "#bc5c48", color: "#69271e" },
  disabled: { background: "#edf3f2", border: "#7a8c8b", color: "#304342" },
  unknown: { background: "#edf5f4", border: "#6f9291", color: "#264645" },
};

export function parseLimit(value: string, fallback: number) {
  const parsed = Number.parseInt(value, 10);
  if (!Number.isFinite(parsed)) {
    return fallback;
  }
  return Math.min(200, Math.max(1, parsed));
}

export function formatTimestamp(value?: string | null) {
  if (!value) {
    return "Never";
  }
  return new Date(value).toLocaleString();
}

export function formatLogLevel(value?: string | null) {
  if (!value) {
    return "unlabeled";
  }
  return value;
}

export function supportsCapability(capabilities: Record<string, unknown> | null | undefined, capabilityId: string) {
  const routes = capabilities && typeof capabilities === "object" ? capabilities.supported_routes : null;
  return Array.isArray(routes) && routes.includes(capabilityId);
}

export function sortControlPlaneEvents(items: StudioControlPlaneEvent[]) {
  return [...items].sort((left, right) => {
    const leftKey = `${left.timestamp || ""}|${left.ingested_at}|${left.dedupe_key}`;
    const rightKey = `${right.timestamp || ""}|${right.ingested_at}|${right.dedupe_key}`;
    return rightKey.localeCompare(leftKey);
  });
}

export function mergeControlPlaneEvent(items: StudioControlPlaneEvent[], incoming: StudioControlPlaneEvent) {
  const seen = new Set<string>();
  const merged = [incoming, ...items].filter((item) => {
    if (seen.has(item.dedupe_key)) {
      return false;
    }
    seen.add(item.dedupe_key);
    return true;
  });
  return sortControlPlaneEvents(merged);
}

export function formatEventSummary(item: StudioControlPlaneEvent) {
  const status = typeof item.payload.status === "string" ? item.payload.status : null;
  if (item.source_kind === "status" && status) {
    return status;
  }
  return item.event_type;
}

export function formatDuration(durationMs?: number | null) {
  if (durationMs == null) {
    return "n/a";
  }
  if (durationMs < 1000) {
    return `${durationMs} ms`;
  }
  return `${(durationMs / 1000).toFixed(durationMs >= 10_000 ? 0 : 2)} s`;
}

export function formatTaskPointer(pointer: StudioTaskPointer) {
  return `${pointer.service_id}/${pointer.task_id}`;
}

export function formatTaskPointerList(pointers: StudioTaskPointer[]) {
  if (!pointers.length) {
    return "none";
  }
  return pointers.map(formatTaskPointer).join(", ");
}

export function formatJoinKind(joinKind: JoinKind | null | undefined) {
  if (!joinKind) {
    return "join";
  }
  return joinKind.replace(/_/g, " ");
}

export function buildMermaid(graph: ExecutionGraph) {
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
  const queue = graph.nodes.filter((node) => (incoming.get(node.id) ?? 0) === 0).map((node) => node.id);
  if (!queue.length && graph.nodes[0]) {
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

  const columns = new Map<number, typeof graph.nodes>();
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

export function StatusBadge({ status }: { status: ServiceStatus }) {
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

export function HealthBadge({ status }: { status: HealthStatus }) {
  const palette = healthPalette[status];
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

export function MetadataRow({ label, value }: { label: string; value: ReactNode }) {
  return (
    <div style={{ display: "grid", gap: 2 }}>
      <dt style={{ fontWeight: 700 }}>{label}</dt>
      <dd style={{ margin: 0, opacity: 0.85 }}>{value}</dd>
    </div>
  );
}

export function MetricCard({ label, value }: { label: string; value: string }) {
  return (
    <article className="studio-card" style={{ ...frameStyle, padding: 16 }}>
      <p style={{ margin: 0, fontSize: 12, textTransform: "uppercase", letterSpacing: 1.2, color: "var(--studio-text-soft)" }}>{label}</p>
      <strong style={{ display: "block", marginTop: 10, fontSize: 26 }}>{value}</strong>
    </article>
  );
}

export function NoticeBanner({ tone = "info", children }: { tone?: "info" | "error"; children: ReactNode }) {
  return (
    <section
      style={{
        ...frameStyle,
        padding: 16,
        borderColor: tone === "error" ? "rgba(168, 60, 40, 0.3)" : "rgba(15, 124, 123, 0.24)",
        color: tone === "error" ? "var(--studio-danger)" : "var(--studio-secondary-strong)",
      }}
    >
      {children}
    </section>
  );
}

export function EmptyState({ title, body }: { title: string; body: string }) {
  return (
    <section className="studio-subcard" style={{ ...frameStyle, padding: 24, background: "var(--studio-surface-strong)" }}>
      <h3 style={{ marginTop: 0 }}>{title}</h3>
      <p style={{ ...mutedTextStyle, marginBottom: 0 }}>{body}</p>
    </section>
  );
}

export function SectionCard({
  title,
  subtitle,
  action,
  children,
  className,
}: {
  title: string;
  subtitle?: string;
  action?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section className={`studio-card studio-section-card${className ? ` ${className}` : ""}`} style={frameStyle}>
      <div className="studio-section-header">
        <div className="studio-section-header__copy">
          <h2 className="studio-section-title">{title}</h2>
          {subtitle ? <p className="studio-section-subtitle">{subtitle}</p> : null}
        </div>
        {action}
      </div>
      {children}
    </section>
  );
}

export function InlineCodeBox({ value, minHeight = 180 }: { value: string; minHeight?: number }) {
  return (
    <textarea
      value={value}
      readOnly
      spellCheck={false}
      style={{
        ...inputStyle,
        minHeight,
        resize: "vertical",
        fontFamily: "'SFMono-Regular', Menlo, monospace",
        fontSize: 12,
        background: "rgba(249, 253, 252, 0.98)",
      }}
    />
  );
}

export function AppChrome({ children }: { children: ReactNode }) {
  return (
    <main className="studio-app">
      <div className="studio-shell">{children}</div>
    </main>
  );
}

export function AppHeader() {
  const navStyle: CSSProperties = {
    ...secondaryButtonStyle,
    textDecoration: "none",
    display: "inline-flex",
    alignItems: "center",
    justifyContent: "center",
  };

  return (
    <header className="studio-header">
      <div>
        <p className="studio-header__eyebrow">Relayna Studio</p>
        <h1 className="studio-header__title">Control Plane</h1>
        <p className="studio-header__body">
          Navigate services, topology, DLQ state, federated task detail, live timelines, and logs from one routed
          operator console.
        </p>
      </div>
      <section className="studio-card studio-section-card studio-nav-card" style={frameStyle}>
        <div>
          <h2 className="studio-section-title" style={{ fontSize: 20 }}>Routes</h2>
          <p className="studio-section-subtitle">Studio now treats route paths as the primary UI contract.</p>
        </div>
        <div className="studio-nav-links">
          <Link to="/services" style={navStyle}>
            Services
          </Link>
          <Link to="/tasks/search" style={navStyle}>
            Task Search
          </Link>
        </div>
      </section>
    </header>
  );
}

export function TaskRefLink({
  taskRef,
  children,
}: {
  taskRef: Pick<StudioTaskRef, "service_id" | "task_id">;
  children?: ReactNode;
}) {
  return (
    <Link
      to={`/tasks/${encodeURIComponent(taskRef.service_id)}/${encodeURIComponent(taskRef.task_id)}`}
      style={{ color: "var(--studio-secondary-strong)", fontWeight: 700 }}
    >
      {children || formatTaskPointer(taskRef)}
    </Link>
  );
}

export function GraphSurface({ graph }: { graph: ExecutionGraph }) {
  const nodes = buildFlowNodes(graph);
  const edges = buildFlowEdges(graph);

  return (
    <div className="studio-card studio-flow-surface" style={frameStyle}>
      <ReactFlow nodes={nodes} edges={edges} fitView proOptions={{ hideAttribution: true }}>
        <Background gap={20} color="rgba(15, 124, 123, 0.14)" />
        <Controls />
        <MiniMap
          pannable
          zoomable
          style={{ background: "rgba(250, 253, 252, 0.96)", border: "1px solid var(--studio-border)" }}
        />
        <Panel
          position="top-right"
          style={{
            ...frameStyle,
            margin: 14,
            padding: "10px 12px",
            borderRadius: 14,
            fontSize: 12,
            background: "rgba(255, 248, 236, 0.92)",
          }}
        >
          <strong>{graph.topology_kind}</strong>
          <div>{graph.summary.graph_completeness} graph</div>
        </Panel>
      </ReactFlow>
    </div>
  );
}

export function WorkflowTopologySurface({ topology }: { topology: WorkflowTopologyGraph }) {
  return (
    <section className="studio-stack-md">
      <div className="studio-metrics-grid studio-metrics-grid--3">
        <MetricCard label="Stages" value={String(topology.stages.length)} />
        <MetricCard label="Entry Routes" value={String(topology.entry_routes.length)} />
        <MetricCard label="Edges" value={String(topology.edges.length)} />
      </div>
      <div className="studio-content-split studio-content-split--topology">
        <div className="studio-stack-sm">
          {topology.stages.map((stage) => (
            <article
              key={stage.id || stage.name}
              className="studio-subcard"
              style={{ borderRadius: 14, padding: 14, display: "grid", gap: 8 }}
            >
              <div style={{ display: "flex", justifyContent: "space-between", gap: 12 }}>
                <strong>{stage.id || stage.name}</strong>
                {stage.terminal ? <span style={{ fontSize: 12, color: "var(--studio-text-soft)" }}>terminal</span> : null}
              </div>
              <p style={{ ...mutedTextStyle, margin: 0 }}>{stage.queue}</p>
              <p style={{ ...mutedTextStyle, margin: 0 }}>
                Publishes: <code>{stage.publish_routing_key}</code>
              </p>
              <p style={{ ...mutedTextStyle, margin: 0 }}>
                Next: {stage.allowed_next_stages.length ? stage.allowed_next_stages.join(", ") : "none"}
              </p>
            </article>
          ))}
        </div>
        <aside className="studio-stack-sm">
          <section className="studio-card" style={{ ...frameStyle, padding: 16 }}>
            <h3 style={{ marginTop: 0, marginBottom: 10 }}>Entry Routes</h3>
            <div className="studio-stack-sm">
              {topology.entry_routes.length ? (
                topology.entry_routes.map((route) => (
                  <div key={`${route.name}-${route.routing_key}`} style={{ display: "grid", gap: 2 }}>
                    <strong style={{ fontSize: 13 }}>{route.name}</strong>
                    <span style={{ fontSize: 12, color: "var(--studio-text-soft)" }}>
                      <code>{route.routing_key}</code> to {route.target_stage}
                    </span>
                  </div>
                ))
              ) : (
                <p style={mutedTextStyle}>No entry routes defined.</p>
              )}
            </div>
          </section>
          <section className="studio-card" style={{ ...frameStyle, padding: 16 }}>
            <h3 style={{ marginTop: 0, marginBottom: 10 }}>Queues</h3>
            <dl style={{ margin: 0, display: "grid", gap: 10, fontSize: 13 }}>
              <MetadataRow label="Workflow exchange" value={topology.workflow_exchange || "none"} />
              <MetadataRow label="Status queue" value={topology.status_queue} />
            </dl>
          </section>
        </aside>
      </div>
    </section>
  );
}

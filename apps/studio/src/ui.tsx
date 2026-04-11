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
  JoinKind,
  ServiceStatus,
  StudioControlPlaneEvent,
  StudioTaskPointer,
  StudioTaskRef,
  WorkflowTopologyGraph,
} from "./types";

export const frameStyle = {
  border: "1px solid rgba(99, 83, 57, 0.18)",
  borderRadius: 18,
  background: "rgba(255,255,255,0.84)",
  boxShadow: "0 18px 44px rgba(55, 43, 26, 0.10)",
};

export const inputStyle: CSSProperties = {
  width: "100%",
  borderRadius: 14,
  border: "1px solid rgba(104, 88, 64, 0.25)",
  padding: "12px 14px",
  background: "#fffdfa",
  color: "#2d2923",
  fontSize: 14,
};

export const mutedTextStyle: CSSProperties = {
  margin: 0,
  fontSize: 13,
  lineHeight: 1.55,
  color: "#5f564a",
};

export const primaryButtonStyle: CSSProperties = {
  border: "none",
  borderRadius: 14,
  background: "#2f3c53",
  color: "#f8f3e9",
  padding: "12px 16px",
  fontSize: 14,
  fontWeight: 700,
  cursor: "pointer",
};

export const secondaryButtonStyle: CSSProperties = {
  borderRadius: 14,
  border: "1px solid rgba(79, 67, 48, 0.22)",
  background: "rgba(255, 251, 244, 0.92)",
  color: "#342b21",
  padding: "10px 14px",
  fontSize: 13,
  fontWeight: 600,
  cursor: "pointer",
};

export const destructiveButtonStyle: CSSProperties = {
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
    <article style={{ ...frameStyle, padding: 16 }}>
      <p style={{ margin: 0, fontSize: 12, textTransform: "uppercase", letterSpacing: 1.2 }}>{label}</p>
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
        borderColor: tone === "error" ? "rgba(152, 66, 66, 0.34)" : "rgba(79, 133, 85, 0.28)",
        color: tone === "error" ? "#6f2525" : "#224a28",
      }}
    >
      {children}
    </section>
  );
}

export function EmptyState({ title, body }: { title: string; body: string }) {
  return (
    <section style={{ ...frameStyle, padding: 24, background: "rgba(255,250,243,0.72)" }}>
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
}: {
  title: string;
  subtitle?: string;
  action?: ReactNode;
  children: ReactNode;
}) {
  return (
    <section style={{ ...frameStyle, padding: 18, display: "grid", gap: 14 }}>
      <div style={{ display: "flex", justifyContent: "space-between", gap: 12, alignItems: "start" }}>
        <div>
          <h2 style={{ margin: 0 }}>{title}</h2>
          {subtitle ? <p style={mutedTextStyle}>{subtitle}</p> : null}
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
      }}
    />
  );
}

export function AppChrome({ children }: { children: ReactNode }) {
  return (
    <main
      style={{
        minHeight: "100vh",
        padding: "36px 22px 48px",
        background: "linear-gradient(180deg, #f3dd9a 0%, #f5f1e9 34%, #d8e4ed 100%)",
        color: "#2f271f",
        fontFamily: "Georgia, 'Iowan Old Style', serif",
      }}
    >
      <div style={{ maxWidth: 1380, margin: "0 auto", display: "grid", gap: 20 }}>{children}</div>
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
    <header
      style={{
        display: "grid",
        gap: 14,
        gridTemplateColumns: "minmax(0, 1.3fr) minmax(280px, 0.7fr)",
        alignItems: "end",
      }}
    >
      <div>
        <p style={{ letterSpacing: 2, textTransform: "uppercase", fontSize: 12, margin: 0 }}>Relayna Studio</p>
        <h1 style={{ margin: "6px 0 10px", fontSize: "clamp(2.6rem, 6vw, 4.8rem)", lineHeight: 0.95 }}>
          Control Plane
        </h1>
        <p style={{ margin: 0, maxWidth: 760, fontSize: 18, lineHeight: 1.5 }}>
          Navigate services, topology, DLQ state, federated task detail, live timelines, and logs from one routed
          operator console.
        </p>
      </div>
      <section style={{ ...frameStyle, padding: 18, display: "grid", gap: 12 }}>
        <div>
          <h2 style={{ margin: 0, fontSize: 20 }}>Routes</h2>
          <p style={mutedTextStyle}>Studio now treats route paths as the primary UI contract.</p>
        </div>
        <div style={{ display: "flex", flexWrap: "wrap", gap: 10 }}>
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
      style={{ color: "#2f3c53", fontWeight: 700 }}
    >
      {children || formatTaskPointer(taskRef)}
    </Link>
  );
}

export function GraphSurface({ graph }: { graph: ExecutionGraph }) {
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

export function WorkflowTopologySurface({ topology }: { topology: WorkflowTopologyGraph }) {
  return (
    <section style={{ ...frameStyle, padding: 18, display: "grid", gap: 16 }}>
      <div style={{ display: "grid", gap: 10, gridTemplateColumns: "repeat(3, minmax(0, 1fr))" }}>
        <MetricCard label="Stages" value={String(topology.stages.length)} />
        <MetricCard label="Entry Routes" value={String(topology.entry_routes.length)} />
        <MetricCard label="Edges" value={String(topology.edges.length)} />
      </div>
      <div style={{ display: "grid", gap: 14, gridTemplateColumns: "minmax(0, 1.5fr) minmax(280px, 0.9fr)" }}>
        <div style={{ display: "grid", gap: 12 }}>
          {topology.stages.map((stage) => (
            <article
              key={stage.id || stage.name}
              style={{ border: "1px solid rgba(99, 83, 57, 0.14)", borderRadius: 14, padding: 14, display: "grid", gap: 8 }}
            >
              <div style={{ display: "flex", justifyContent: "space-between", gap: 12 }}>
                <strong>{stage.id || stage.name}</strong>
                {stage.terminal ? <span style={{ fontSize: 12, color: "#62584b" }}>terminal</span> : null}
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
        <aside style={{ display: "grid", gap: 12 }}>
          <section style={{ ...frameStyle, padding: 16 }}>
            <h3 style={{ marginTop: 0, marginBottom: 10 }}>Entry Routes</h3>
            <div style={{ display: "grid", gap: 8 }}>
              {topology.entry_routes.length ? (
                topology.entry_routes.map((route) => (
                  <div key={`${route.name}-${route.routing_key}`} style={{ display: "grid", gap: 2 }}>
                    <strong style={{ fontSize: 13 }}>{route.name}</strong>
                    <span style={{ fontSize: 12, color: "#62584b" }}>
                      <code>{route.routing_key}</code> to {route.target_stage}
                    </span>
                  </div>
                ))
              ) : (
                <p style={mutedTextStyle}>No entry routes defined.</p>
              )}
            </div>
          </section>
          <section style={{ ...frameStyle, padding: 16 }}>
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

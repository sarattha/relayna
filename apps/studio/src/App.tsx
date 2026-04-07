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
  task_id: string;
  topology_kind: string;
  summary: ExecutionGraphSummary;
  nodes: ExecutionGraphNode[];
  edges: ExecutionGraphEdge[];
  annotations: Record<string, unknown>;
  related_task_ids: string[];
};

const frameStyle = {
  border: "1px solid rgba(99, 83, 57, 0.18)",
  borderRadius: 18,
  background: "rgba(255,255,255,0.84)",
  boxShadow: "0 18px 44px rgba(55, 43, 26, 0.10)",
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

function defaultBaseUrl() {
  if (typeof window === "undefined") {
    return "http://localhost:8000";
  }
  return window.location.origin;
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
  const [baseUrl, setBaseUrl] = useState(search?.get("base_url") || defaultBaseUrl());
  const [taskId, setTaskId] = useState(search?.get("task_id") || "");
  const [graph, setGraph] = useState<ExecutionGraph | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!search) {
      return;
    }
    const initialTaskId = search.get("task_id");
    if (!initialTaskId) {
      return;
    }
    void loadGraph(initialTaskId, search.get("base_url") || defaultBaseUrl());
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function loadGraph(nextTaskId: string, nextBaseUrl: string) {
    const normalizedTaskId = nextTaskId.trim();
    const normalizedBaseUrl = nextBaseUrl.trim().replace(/\/+$/, "");
    if (!normalizedTaskId) {
      setError("Enter a task id to load an execution graph.");
      return;
    }

    setLoading(true);
    setError(null);
    try {
      const response = await fetch(
        `${normalizedBaseUrl}/executions/${encodeURIComponent(normalizedTaskId)}/graph`,
      );
      if (!response.ok) {
        const payload = (await response.json().catch(() => null)) as { detail?: string } | null;
        throw new Error(payload?.detail || `Request failed with status ${response.status}.`);
      }
      const payload = (await response.json()) as ExecutionGraph & { attempt_id?: string };
      const normalizedGraph: ExecutionGraph = {
        ...payload,
        task_id: payload.task_id || payload.attempt_id || normalizedTaskId,
      };
      startTransition(() => {
        setGraph(normalizedGraph);
      });
      if (typeof window !== "undefined") {
        const params = new URLSearchParams(window.location.search);
        params.set("task_id", normalizedTaskId);
        params.set("base_url", normalizedBaseUrl);
        window.history.replaceState({}, "", `${window.location.pathname}?${params.toString()}`);
      }
    } catch (fetchError) {
      setGraph(null);
      setError(fetchError instanceof Error ? fetchError.message : "Unable to load execution graph.");
    } finally {
      setLoading(false);
    }
  }

  const mermaid = graph ? buildMermaid(graph) : "";

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    void loadGraph(taskId, baseUrl);
  }

  return (
    <ReactFlowProvider>
      <main
        style={{
          minHeight: "100vh",
          padding: "36px 22px 48px",
          background:
            "linear-gradient(180deg, #f3dd9a 0%, #f5f1e9 34%, #d8e4ed 100%)",
          color: "#2f271f",
          fontFamily: "Georgia, 'Iowan Old Style', serif",
        }}
      >
        <div style={{ maxWidth: 1380, margin: "0 auto", display: "grid", gap: 20 }}>
          <header
            style={{
              display: "grid",
              gap: 10,
              gridTemplateColumns: "minmax(0, 1.2fr) minmax(320px, 0.8fr)",
              alignItems: "end",
            }}
          >
            <div>
              <p style={{ letterSpacing: 2, textTransform: "uppercase", fontSize: 12, margin: 0 }}>
                Relayna Studio
              </p>
              <h1 style={{ margin: "6px 0 10px", fontSize: "clamp(2.6rem, 6vw, 4.8rem)", lineHeight: 0.95 }}>
                Execution Graph
              </h1>
              <p style={{ margin: 0, maxWidth: 740, fontSize: 18, lineHeight: 1.5 }}>
                Render live execution graphs with React Flow in the app and keep a Mermaid export on hand
                for docs, issue threads, and operator debugging.
              </p>
            </div>
            <form
              onSubmit={handleSubmit}
              style={{
                ...frameStyle,
                padding: 18,
                display: "grid",
                gap: 12,
                alignSelf: "stretch",
              }}
            >
              <label style={{ display: "grid", gap: 6, fontSize: 13 }}>
                API base URL
                <input
                  value={baseUrl}
                  onChange={(event) => setBaseUrl(event.target.value)}
                  placeholder="http://localhost:8000"
                  style={inputStyle}
                />
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
              <button
                type="submit"
                disabled={loading}
                style={{
                  border: "none",
                  borderRadius: 14,
                  background: loading ? "#8f8a80" : "#2f3c53",
                  color: "#f8f3e9",
                  padding: "12px 16px",
                  fontSize: 14,
                  fontWeight: 700,
                  cursor: loading ? "wait" : "pointer",
                }}
              >
                {loading ? "Loading Graph..." : "Load Execution Graph"}
              </button>
            </form>
          </header>

          {error ? (
            <section style={{ ...frameStyle, padding: 16, borderColor: "rgba(152, 66, 66, 0.34)", color: "#6f2525" }}>
              {error}
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
              {graph ? (
                <>
                  <div
                    style={{
                      display: "grid",
                      gap: 14,
                      gridTemplateColumns: "repeat(auto-fit, minmax(180px, 1fr))",
                    }}
                  >
                    <MetricCard label="Status" value={graph.summary.status || "unknown"} />
                    <MetricCard label="Duration" value={formatDuration(graph.summary.duration_ms)} />
                    <MetricCard label="Nodes" value={String(graph.nodes.length)} />
                    <MetricCard label="Edges" value={String(graph.edges.length)} />
                  </div>
                  <GraphSurface graph={graph} />
                </>
              ) : (
                <section style={{ ...frameStyle, padding: 28 }}>
                  <h2 style={{ marginTop: 0 }}>No Graph Loaded</h2>
                  <p style={{ marginBottom: 0, lineHeight: 1.6 }}>
                    Point Studio at a Relayna API and load a task id. The app will call
                    <code> /executions/&lt;task_id&gt;/graph</code> and render the returned execution graph with
                    React Flow.
                  </p>
                </section>
              )}
            </div>

            <aside style={{ display: "grid", gap: 18 }}>
              <section style={{ ...frameStyle, padding: 18 }}>
                <h2 style={{ marginTop: 0, marginBottom: 12 }}>Legend</h2>
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
                <h2 style={{ marginTop: 0, marginBottom: 12 }}>Mermaid Export</h2>
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
                  <h2 style={{ marginTop: 0, marginBottom: 12 }}>Graph Metadata</h2>
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
            </aside>
          </section>
        </div>
      </main>
    </ReactFlowProvider>
  );
}

function MetricCard({ label, value }: { label: string; value: string }) {
  return (
    <section style={{ ...frameStyle, padding: 18 }}>
      <div style={{ fontSize: 12, letterSpacing: 1.2, textTransform: "uppercase", opacity: 0.72 }}>{label}</div>
      <div style={{ marginTop: 8, fontSize: 26, lineHeight: 1.05 }}>{value}</div>
    </section>
  );
}

function MetadataRow({ label, value }: { label: string; value: string }) {
  return (
    <div style={{ display: "grid", gap: 4 }}>
      <dt style={{ fontSize: 12, textTransform: "uppercase", letterSpacing: 1.1, opacity: 0.7 }}>{label}</dt>
      <dd style={{ margin: 0 }}>{value}</dd>
    </div>
  );
}

const inputStyle = {
  borderRadius: 12,
  border: "1px solid rgba(95, 81, 59, 0.24)",
  background: "#fffdf9",
  color: "#2f271f",
  padding: "11px 12px",
  fontSize: 14,
} satisfies CSSProperties;

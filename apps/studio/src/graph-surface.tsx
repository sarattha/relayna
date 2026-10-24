import {
  Background,
  Controls,
  type Edge,
  MiniMap,
  type Node,
  Panel,
  ReactFlow,
  ReactFlowProvider,
} from "@xyflow/react";

import type { ExecutionGraph } from "./types";
import { frameStyle } from "./ui";

const kindPalette: Record<string, { background: string; border: string; color: string }> = {
  task: { background: "var(--rn-color-neutral-surface)", border: "var(--rn-color-neutral)", color: "var(--rn-color-neutral)" },
  aggregation_child: { background: "var(--rn-color-info-surface)", border: "var(--rn-color-info)", color: "var(--rn-color-info)" },
  task_attempt: { background: "var(--rn-color-neutral-surface)", border: "var(--rn-color-neutral)", color: "var(--rn-color-neutral)" },
  workflow_message: { background: "var(--rn-color-info-surface)", border: "var(--rn-color-info)", color: "var(--rn-color-info)" },
  stage_attempt: { background: "var(--rn-color-neutral-surface)", border: "var(--rn-color-neutral)", color: "var(--rn-color-neutral)" },
  status_event: { background: "var(--rn-color-neutral-surface)", border: "var(--rn-color-neutral)", color: "var(--rn-color-neutral)" },
  retry: { background: "var(--rn-color-warning-surface)", border: "var(--rn-color-warning)", color: "var(--rn-color-warning)" },
  dlq_record: { background: "var(--rn-color-error-surface)", border: "var(--rn-color-error)", color: "var(--rn-color-error)" },
};

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
        background: "var(--rn-color-neutral-surface)",
        border: "var(--rn-color-neutral)",
        color: "var(--rn-color-neutral)",
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
            <div className="studio-flow-node-label">
              <strong>{labelLines[0]}</strong>
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
          boxShadow: "var(--rn-shadow-rest)",
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
    labelBgStyle: { fill: "var(--studio-surface)" },
    labelStyle: {
      fill: "var(--studio-text-muted)",
      fontSize: 11,
      fontWeight: 600,
    },
    style: {
      stroke: edge.kind === "dead_lettered_to" ? "var(--studio-danger)" : "var(--studio-border-strong)",
      strokeWidth: edge.kind === "stage_transitioned_to" ? 2.2 : 1.5,
    },
  }));
}

export function GraphSurface({ graph }: { graph: ExecutionGraph }) {
  const nodes = buildFlowNodes(graph);
  const edges = buildFlowEdges(graph);

  return (
    <ReactFlowProvider>
      <div className="studio-card studio-flow-surface" style={frameStyle}>
        <ReactFlow nodes={nodes} edges={edges} fitView proOptions={{ hideAttribution: true }}>
          <Background gap={20} color="var(--studio-border)" />
          <Controls />
          <MiniMap
            pannable
            zoomable
            style={{ background: "var(--studio-surface)", border: "1px solid var(--studio-border)" }}
          />
          <Panel
            position="top-right"
            style={{
              ...frameStyle,
              margin: 14,
              padding: "10px 12px",
              borderRadius: 14,
              fontSize: 12,
              background: "var(--studio-surface-muted)",
            }}
          >
            <strong>{graph.topology_kind}</strong>
            <div>{graph.summary.graph_completeness} graph</div>
          </Panel>
        </ReactFlow>
      </div>
    </ReactFlowProvider>
  );
}

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
  task: { background: "#fff3dd", border: "#cb7b2d", color: "#5d3110" },
  aggregation_child: { background: "#e4f6f3", border: "#3b8f8d", color: "#184a49" },
  task_attempt: { background: "#fff1e5", border: "#e08b48", color: "#663414" },
  workflow_message: { background: "#e6f7f4", border: "#2d8a80", color: "#184841" },
  stage_attempt: { background: "#eff3f2", border: "#6b8d8c", color: "#284443" },
  status_event: { background: "#f9f8ef", border: "#b08a51", color: "#57411d" },
  retry: { background: "#fff0eb", border: "#c46c56", color: "#6a2b1c" },
  dlq_record: { background: "#233f45", border: "#ffb295", color: "#fff5ef" },
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

export function GraphSurface({ graph }: { graph: ExecutionGraph }) {
  const nodes = buildFlowNodes(graph);
  const edges = buildFlowEdges(graph);

  return (
    <ReactFlowProvider>
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
    </ReactFlowProvider>
  );
}

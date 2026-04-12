"use client";

import { useMemo, useCallback, useState } from "react";
import {
  ReactFlow,
  Background,
  MiniMap,
  Controls,
  Handle,
  Position,
  type Node,
  type Edge,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import dagre from "dagre";
import { RunEvent, SkillUI } from "./types";

// -- Types ------------------------------------------------------------------

interface GraphNode {
  id: string;
  title: string;
  category: string;
  state: "queued" | "blocked" | "active" | "completed" | "escalated" | "skipped";
  attempts: number;
  wall_time_s: number;
  escalation_reason: string | null;
}

interface GraphEdge {
  from: string;
  to: string;
}

interface GraphSnapshot {
  nodes: GraphNode[];
  edges: GraphEdge[];
  counts: Record<string, number>;
}

// -- Styling ----------------------------------------------------------------

const STATE_STYLES: Record<string, { bg: string; border: string; text: string; glow?: string }> = {
  queued:    { bg: "#1F2937", border: "#374151", text: "#6B7280" },
  blocked:   { bg: "#451A03", border: "#92400E", text: "#FCD34D" },
  active:    { bg: "#1E3A5F", border: "#2563EB", text: "#93C5FD", glow: "0 0 12px rgba(37,99,235,0.5)" },
  completed: { bg: "#052E16", border: "#16A34A", text: "#4ADE80" },
  escalated: { bg: "#450A0A", border: "#DC2626", text: "#FCA5A5" },
  skipped:   { bg: "#1F2937", border: "#4B5563", text: "#6B7280" },
};

const CATEGORY_COLORS: Record<string, string> = {
  "authentication": "#8B5CF6",
  "kernel": "#F59E0B",
  "package-management": "#10B981",
  "logging": "#06B6D4",
  "cryptography": "#EC4899",
  "privileged-access": "#EF4444",
  "filesystem": "#6366F1",
  "integrity-monitoring": "#F97316",
  "user-account": "#14B8A6",
  "banner": "#A855F7",
  "service-config": "#22D3EE",
  "ssh": "#FB923C",
  "audit": "#84CC16",
  "network-firewall": "#E11D48",
  "mac": "#7C3AED",
};

// -- Custom Node Component --------------------------------------------------

function WorkItemNode({ data }: { data: GraphNode & { stripped: string; selected: boolean } }) {
  const style = STATE_STYLES[data.state] || STATE_STYLES.queued;
  const isActive = data.state === "active";
  const catColor = CATEGORY_COLORS[data.category] || "#6B7280";

  return (
    <div
      className={`rounded-md border-2 transition-all duration-300 ${isActive ? "animate-pulse" : ""}`}
      style={{
        backgroundColor: style.bg,
        borderColor: data.selected ? "#E8EAED" : style.border,
        boxShadow: style.glow || "none",
        minWidth: 160,
        maxWidth: 220,
      }}
    >
      <Handle type="target" position={Position.Top} style={{ background: style.border, width: 6, height: 6 }} />

      {/* Category stripe */}
      <div className="h-1 rounded-t-sm" style={{ backgroundColor: catColor }} />

      <div className="px-2.5 py-1.5">
        {/* ID */}
        <div className="text-[10px] font-mono font-bold truncate" style={{ color: style.text }}>
          {data.stripped}
        </div>
        {/* Title */}
        <div className="text-[9px] text-[#9CA3AF] truncate mt-0.5">
          {data.title}
        </div>
        {/* Stats row */}
        <div className="flex items-center gap-2 mt-1">
          <span
            className="text-[8px] font-mono px-1 py-0.5 rounded-sm"
            style={{ backgroundColor: style.border + "33", color: style.text }}
          >
            {data.state}
          </span>
          {data.attempts > 0 && (
            <span className="text-[8px] font-mono text-[#6B7280]">
              {data.attempts} att
            </span>
          )}
          {data.wall_time_s > 0 && (
            <span className="text-[8px] font-mono text-[#6B7280]">
              {Math.round(data.wall_time_s)}s
            </span>
          )}
        </div>
      </div>

      <Handle type="source" position={Position.Bottom} style={{ background: style.border, width: 6, height: 6 }} />
    </div>
  );
}

const nodeTypes = { workItem: WorkItemNode };

// -- Layout with Dagre ------------------------------------------------------

function layoutGraph(
  graphNodes: GraphNode[],
  graphEdges: GraphEdge[],
  stripId: (id: string) => string,
): { nodes: Node[]; edges: Edge[] } {
  // Group by category for cluster layout
  const categories: Record<string, GraphNode[]> = {};
  const catOrder: string[] = [];
  for (const node of graphNodes) {
    const cat = node.category || "uncategorized";
    if (!categories[cat]) {
      categories[cat] = [];
      catOrder.push(cat);
    }
    categories[cat].push(node);
  }

  // Sort categories: most items first (creates visual weight)
  catOrder.sort((a, b) => categories[b].length - categories[a].length);

  const NODE_W = 190;
  const NODE_H = 68;
  const GAP_X = 12;
  const GAP_Y = 12;
  const COLS = 6; // items per row within a category cluster
  const CLUSTER_GAP = 50; // vertical gap between category clusters
  const LABEL_H = 30; // space for category label

  const nodes: Node[] = [];
  let clusterY = 20;

  for (const cat of catOrder) {
    const items = categories[cat];
    const catColor = CATEGORY_COLORS[cat] || "#6B7280";

    // Category label node (non-interactive)
    nodes.push({
      id: `__label_${cat}`,
      type: "default",
      position: { x: 0, y: clusterY },
      data: { label: `${cat.toUpperCase()} (${items.length})` },
      style: {
        background: "transparent",
        border: "none",
        color: catColor,
        fontSize: 11,
        fontWeight: 700,
        fontFamily: "monospace",
        letterSpacing: "0.05em",
        textTransform: "uppercase" as const,
        width: COLS * (NODE_W + GAP_X),
        pointerEvents: "none" as const,
      },
      selectable: false,
      draggable: false,
    });

    clusterY += LABEL_H;

    // Layout items in a grid within this cluster
    items.forEach((gn, idx) => {
      const col = idx % COLS;
      const row = Math.floor(idx / COLS);
      nodes.push({
        id: gn.id,
        type: "workItem",
        position: {
          x: col * (NODE_W + GAP_X),
          y: clusterY + row * (NODE_H + GAP_Y),
        },
        data: { ...gn, stripped: stripId(gn.id), selected: false },
      });
    });

    const rows = Math.ceil(items.length / COLS);
    clusterY += rows * (NODE_H + GAP_Y) + CLUSTER_GAP;
  }

  // Edges for any dependencies
  const nodeIds = new Set(graphNodes.map(n => n.id));
  const edges: Edge[] = graphEdges
    .filter((e) => nodeIds.has(e.from) && nodeIds.has(e.to))
    .map((e, i) => ({
      id: `e-${i}`,
      source: e.from,
      target: e.to,
      animated: true,
      style: { stroke: "#F59E0B", strokeWidth: 2 },
      type: "smoothstep",
    }));

  return { nodes, edges };
}

// -- Detail Panel -----------------------------------------------------------

function DetailPanel({ node, onClose, skillUI }: {
  node: GraphNode & { stripped: string };
  onClose: () => void;
  skillUI: SkillUI;
}) {
  const style = STATE_STYLES[node.state] || STATE_STYLES.queued;
  const catColor = CATEGORY_COLORS[node.category] || "#6B7280";

  return (
    <div className="absolute right-0 top-0 w-80 h-full bg-[#12141A] border-l border-[#1C1F26] z-50 overflow-y-auto">
      <div className="p-4">
        <div className="flex justify-between items-start mb-3">
          <div>
            <div className="text-xs font-mono font-bold" style={{ color: style.text }}>
              {node.stripped}
            </div>
            <div className="text-[10px] text-[#9CA3AF] mt-0.5">{node.title}</div>
          </div>
          <button onClick={onClose} className="text-[#6B7280] hover:text-[#E8EAED] text-sm">✕</button>
        </div>

        {/* Category */}
        <div className="flex items-center gap-2 mb-3">
          <span className="w-2 h-2 rounded-sm" style={{ backgroundColor: catColor }} />
          <span className="text-[10px] text-[#9CA3AF]">{node.category}</span>
        </div>

        {/* Stats */}
        <div className="space-y-2 text-[10px]">
          <div className="flex justify-between">
            <span className="text-[#6B7280]">State</span>
            <span className="font-mono px-1.5 py-0.5 rounded-sm"
              style={{ backgroundColor: style.border + "33", color: style.text }}>
              {node.state}
            </span>
          </div>
          {node.attempts > 0 && (
            <div className="flex justify-between">
              <span className="text-[#6B7280]">Attempts</span>
              <span className="font-mono text-[#E8EAED]">{node.attempts}</span>
            </div>
          )}
          {node.wall_time_s > 0 && (
            <div className="flex justify-between">
              <span className="text-[#6B7280]">Wall Time</span>
              <span className="font-mono text-[#E8EAED]">{Math.round(node.wall_time_s)}s</span>
            </div>
          )}
          {node.escalation_reason && (
            <div className="flex justify-between">
              <span className="text-[#6B7280]">Escalation</span>
              <span className="font-mono text-[#FCA5A5]">{node.escalation_reason}</span>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

// -- Main Component ---------------------------------------------------------

export default function TaskGraphFlow({
  events,
  skillUI,
}: {
  events: RunEvent[];
  skillUI: SkillUI;
}) {
  const [selectedNode, setSelectedNode] = useState<string | null>(null);

  const stripId = useCallback((id: string) => {
    if (skillUI.id_prefix_strip && id.startsWith(skillUI.id_prefix_strip)) {
      return id.slice(skillUI.id_prefix_strip.length);
    }
    return id;
  }, [skillUI.id_prefix_strip]);

  // Find latest graph state
  const graphState: GraphSnapshot | null = useMemo(() => {
    for (let i = events.length - 1; i >= 0; i--) {
      if (events[i].event_type === "graph_state" && events[i].data?.nodes) {
        return events[i].data as unknown as GraphSnapshot;
      }
    }
    return null;
  }, [events]);

  // Layout
  const { nodes, edges } = useMemo(() => {
    if (!graphState) return { nodes: [], edges: [] };
    return layoutGraph(graphState.nodes, graphState.edges, stripId);
  }, [graphState, stripId]);

  // Highlight selected
  const displayNodes = useMemo(() => {
    return nodes.map((n) => ({
      ...n,
      data: { ...n.data, selected: n.id === selectedNode },
    }));
  }, [nodes, selectedNode]);

  const selectedGraphNode = useMemo(() => {
    if (!selectedNode || !graphState) return null;
    const gn = graphState.nodes.find((n) => n.id === selectedNode);
    if (!gn) return null;
    return { ...gn, stripped: stripId(gn.id) };
  }, [selectedNode, graphState, stripId]);

  if (!graphState || graphState.nodes.length === 0) {
    return (
      <div className="flex items-center justify-center h-full text-[#4B5563] text-xs font-mono">
        Awaiting task graph data...
      </div>
    );
  }

  const counts = graphState.counts;
  const total = graphState.nodes.length;

  return (
    <div className="relative h-full w-full" style={{ backgroundColor: "#0B0D11" }}>
      {/* Stats overlay */}
      <div className="absolute top-3 left-3 z-10 flex gap-2">
        {Object.entries(counts)
          .filter(([, v]) => v > 0)
          .map(([state, count]) => {
            const s = STATE_STYLES[state] || STATE_STYLES.queued;
            return (
              <span
                key={state}
                className="px-2 py-0.5 rounded-sm text-[9px] font-mono"
                style={{ backgroundColor: s.bg, color: s.text, border: `1px solid ${s.border}` }}
              >
                {count} {state}
              </span>
            );
          })}
        <span className="px-2 py-0.5 rounded-sm text-[9px] font-mono bg-[#12141A] text-[#9CA3AF] border border-[#1C1F26]">
          {counts.completed}/{total} ({Math.round((counts.completed / total) * 100)}%)
        </span>
      </div>

      <ReactFlow
        nodes={displayNodes}
        edges={edges}
        nodeTypes={nodeTypes}
        onNodeClick={(_, node) => setSelectedNode(node.id === selectedNode ? null : node.id)}
        fitView
        fitViewOptions={{ padding: 0.2 }}
        minZoom={0.1}
        maxZoom={2}
        proOptions={{ hideAttribution: true }}
        style={{ backgroundColor: "#0B0D11" }}
      >
        <Background color="#1C1F26" gap={20} size={1} />
        <Controls
          showInteractive={false}
          style={{ backgroundColor: "#12141A", borderColor: "#1C1F26" }}
        />
        <MiniMap
          nodeColor={(node) => {
            const data = node.data as unknown as GraphNode;
            const s = STATE_STYLES[data?.state] || STATE_STYLES.queued;
            return s.border;
          }}
          maskColor="rgba(11, 13, 17, 0.85)"
          style={{ backgroundColor: "#12141A", borderColor: "#1C1F26" }}
        />
      </ReactFlow>

      {/* Detail panel */}
      {selectedGraphNode && (
        <DetailPanel
          node={selectedGraphNode}
          onClose={() => setSelectedNode(null)}
          skillUI={skillUI}
        />
      )}
    </div>
  );
}

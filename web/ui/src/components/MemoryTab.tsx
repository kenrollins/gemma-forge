"use client";

/**
 * MemoryTab — the Reflective tier, visualized.
 *
 * Rules as blue nodes, lessons as purple cards, edges from each
 * lesson back to the rule it was distilled from. The graph grows
 * during a live/replay stream as new reflections land, with a
 * short amber flash on freshly-created nodes so the eye catches
 * the write. Clicking a node reveals its details in a side panel.
 *
 * V1 scope: derives the graph directly from the event stream
 * (reflection/rule_selected events). V2 will hit a new Neo4j-backed
 * API endpoint that returns the full Graphiti graph with bi-temporal
 * provenance + per-(tip, rule) hit edges. Shape of this component
 * stays the same; only the data source changes.
 *
 * Deliberately NOT wiring a full force-layout library yet — dagre
 * hierarchical (rules on top, lessons below) is stable, readable,
 * and ships today. A true force-directed pass can layer on in UI-6.1
 * once we've learned what the user wants to see.
 */

import { useCallback, useMemo, useState, useEffect } from "react";
import {
  ReactFlow,
  Background,
  Controls,
  Handle,
  Position,
  type Node,
  type Edge,
  type NodeProps,
  MarkerType,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import dagre from "dagre";
import type { RunEvent, SkillUI } from "./types";

// ============================================================================
// Node types
// ============================================================================

type MemoryNodeType = "rule" | "lesson";

interface RuleNodeData extends Record<string, unknown> {
  type: "rule";
  ruleId: string;
  title?: string;
  category?: string;
  outcome?: "completed" | "escalated" | "skip" | "active" | "unknown";
}

interface LessonNodeData extends Record<string, unknown> {
  type: "lesson";
  text: string;
  category?: string;
  freshMs: number; // ms since this lesson arrived, for the "just-distilled" highlight
  sourceRuleId?: string;
}

const RULE_COLOR = "#3B82F6";
const LESSON_COLOR = "#A855F7";
const FRESH_HIGHLIGHT_WINDOW_MS = 4000; // how long a new node pulses before settling

// ============================================================================
// Layout (dagre) — top-down, rules above their lessons
// ============================================================================

const NODE_WIDTH = 200;
const RULE_HEIGHT = 56;
const LESSON_HEIGHT = 80;

function layout(nodes: Node[], edges: Edge[]) {
  const g = new dagre.graphlib.Graph();
  g.setGraph({ rankdir: "TB", nodesep: 40, ranksep: 80, marginx: 40, marginy: 40 });
  g.setDefaultEdgeLabel(() => ({}));

  for (const n of nodes) {
    const h = (n.data as { type?: string })?.type === "rule" ? RULE_HEIGHT : LESSON_HEIGHT;
    g.setNode(n.id, { width: NODE_WIDTH, height: h });
  }
  for (const e of edges) g.setEdge(e.source, e.target);

  dagre.layout(g);

  return nodes.map((n) => {
    const d = g.node(n.id);
    const h = (n.data as { type?: string })?.type === "rule" ? RULE_HEIGHT : LESSON_HEIGHT;
    return {
      ...n,
      position: { x: d.x - NODE_WIDTH / 2, y: d.y - h / 2 },
    };
  });
}

// ============================================================================
// Custom node components
// ============================================================================

const OUTCOME_BG: Record<string, string> = {
  completed: "rgba(34,197,94,0.14)",
  escalated: "rgba(245,158,11,0.14)",
  skip: "rgba(75,85,99,0.14)",
  active: "rgba(59,130,246,0.18)",
  unknown: "rgba(59,130,246,0.08)",
};

const OUTCOME_BORDER: Record<string, string> = {
  completed: "#22C55E",
  escalated: "#F59E0B",
  skip: "#4B5563",
  active: "#60A5FA",
  unknown: "#3B82F6",
};

function RuleNode({ data }: NodeProps) {
  const d = data as RuleNodeData;
  const outcome = d.outcome || "unknown";
  return (
    <div
      className="rounded-md border text-center"
      style={{
        width: NODE_WIDTH,
        height: RULE_HEIGHT,
        background: OUTCOME_BG[outcome],
        borderColor: OUTCOME_BORDER[outcome],
        boxShadow: `0 0 10px ${OUTCOME_BORDER[outcome]}33`,
      }}
    >
      <Handle type="target" position={Position.Top} style={{ background: RULE_COLOR }} />
      <div className="px-2 py-1.5">
        <div className="text-[8px] uppercase tracking-[0.18em] font-semibold" style={{ color: OUTCOME_BORDER[outcome] }}>
          rule · {d.category || "?"}
        </div>
        <div
          className="text-[11px] font-mono text-[#E8EAED] truncate mt-0.5"
          title={d.title || d.ruleId}
        >
          {d.title || shortRuleId(d.ruleId)}
        </div>
      </div>
      <Handle type="source" position={Position.Bottom} style={{ background: RULE_COLOR }} />
    </div>
  );
}

function LessonNode({ data }: NodeProps) {
  const d = data as LessonNodeData;
  const freshFactor = Math.max(0, 1 - d.freshMs / FRESH_HIGHLIGHT_WINDOW_MS);
  const glow = freshFactor > 0
    ? `0 0 ${8 + 14 * freshFactor}px rgba(168,85,247,${0.3 + 0.5 * freshFactor})`
    : "0 0 6px rgba(168,85,247,0.15)";
  return (
    <div
      className="rounded-md border text-left p-2"
      style={{
        width: NODE_WIDTH,
        height: LESSON_HEIGHT,
        background: freshFactor > 0 ? "rgba(168,85,247,0.18)" : "rgba(168,85,247,0.06)",
        borderColor: freshFactor > 0 ? "#C084FC" : "rgba(168,85,247,0.5)",
        boxShadow: glow,
        transition: "background 400ms, box-shadow 400ms, border-color 400ms",
      }}
    >
      <Handle type="target" position={Position.Top} style={{ background: LESSON_COLOR }} />
      <div className="text-[8px] uppercase tracking-[0.18em] font-semibold text-[#C4B5FD]">
        lesson · {d.category || "?"}
      </div>
      <div
        className="text-[10px] font-mono leading-snug text-[#E8EAED] mt-1 line-clamp-3"
        title={d.text}
      >
        {d.text}
      </div>
    </div>
  );
}

const nodeTypes = {
  rule: RuleNode,
  lesson: LessonNode,
};

// ============================================================================
// Graph derivation from events
// ============================================================================

interface DerivedGraph {
  nodes: Node[];
  edges: Edge[];
  ruleCount: number;
  lessonCount: number;
}

const MAX_RULES = 40;
const MAX_LESSONS = 60;

function deriveGraph(events: RunEvent[], now: number, skillUI: SkillUI): DerivedGraph {
  // Walk events once, collecting rules and lessons. Keep the most
  // recently touched N of each so the graph stays readable without
  // pagination. When V2's Neo4j-backed API ships, this function gets
  // replaced with a fetch + a viewport query.

  // rule_id → last seen data
  const ruleState = new Map<string, { title?: string; category?: string; outcome?: string; tsMs: number }>();

  interface LessonRec {
    id: string;
    text: string;
    category?: string;
    sourceRuleId?: string;
    tsMs: number;
  }
  const lessons: LessonRec[] = [];
  let lessonCounter = 0;

  for (const e of events) {
    const tsMs = new Date(e.timestamp).getTime();
    const d = e.data || {};
    const ruleId = (d.rule_id as string | undefined) || undefined;

    if (ruleId && (e.event_type === "rule_selected" || e.event_type === "attempt_start")) {
      const prev = ruleState.get(ruleId);
      ruleState.set(ruleId, {
        title: (d.title as string) || prev?.title,
        category: (d.category as string) || prev?.category,
        outcome: prev?.outcome || "active",
        tsMs,
      });
    }
    if (ruleId && e.event_type === "remediated") {
      const prev = ruleState.get(ruleId) || { tsMs };
      ruleState.set(ruleId, { ...prev, outcome: "completed", tsMs });
    }
    if (ruleId && e.event_type === "escalated") {
      const prev = ruleState.get(ruleId) || { tsMs };
      ruleState.set(ruleId, { ...prev, outcome: "escalated", tsMs });
    }
    if (ruleId && e.event_type === "skip") {
      const prev = ruleState.get(ruleId) || { tsMs };
      ruleState.set(ruleId, { ...prev, outcome: "skip", tsMs });
    }

    if (e.event_type === "reflection" && typeof d.lesson === "string" && d.lesson.length > 0) {
      lessons.push({
        id: `lesson-${++lessonCounter}`,
        text: d.lesson as string,
        category: (d.category as string) || undefined,
        sourceRuleId: ruleId,
        tsMs,
      });
    }
  }

  // Keep most-recently-touched rules/lessons
  const sortedRules = [...ruleState.entries()]
    .sort((a, b) => b[1].tsMs - a[1].tsMs)
    .slice(0, MAX_RULES);
  const keptRuleIds = new Set(sortedRules.map(([id]) => id));

  const sortedLessons = lessons
    .filter((l) => !l.sourceRuleId || keptRuleIds.has(l.sourceRuleId))
    .slice(-MAX_LESSONS);

  const nodes: Node[] = [];
  for (const [ruleId, s] of sortedRules) {
    nodes.push({
      id: `rule-${ruleId}`,
      type: "rule",
      position: { x: 0, y: 0 },
      data: {
        type: "rule",
        ruleId,
        title: s.title,
        category: s.category,
        outcome: (s.outcome as RuleNodeData["outcome"]) || "unknown",
      } satisfies RuleNodeData,
    });
  }
  for (const l of sortedLessons) {
    nodes.push({
      id: l.id,
      type: "lesson",
      position: { x: 0, y: 0 },
      data: {
        type: "lesson",
        text: l.text,
        category: l.category,
        freshMs: Math.max(0, now - l.tsMs),
        sourceRuleId: l.sourceRuleId,
      } satisfies LessonNodeData,
    });
  }

  const edges: Edge[] = [];
  for (const l of sortedLessons) {
    if (!l.sourceRuleId || !keptRuleIds.has(l.sourceRuleId)) continue;
    edges.push({
      id: `e-${l.id}`,
      source: `rule-${l.sourceRuleId}`,
      target: l.id,
      style: { stroke: "rgba(168,85,247,0.35)", strokeWidth: 1 },
      markerEnd: { type: MarkerType.ArrowClosed, color: "rgba(168,85,247,0.6)" },
      animated: Math.max(0, now - l.tsMs) < FRESH_HIGHLIGHT_WINDOW_MS,
    });
  }

  return {
    nodes: layout(nodes, edges),
    edges,
    ruleCount: sortedRules.length,
    lessonCount: sortedLessons.length,
  };
}

// ============================================================================
// Main component
// ============================================================================

export interface MemoryTabProps {
  events: RunEvent[];
  skillUI: SkillUI;
}

export default function MemoryTab({ events, skillUI }: MemoryTabProps) {
  // Tick every 500ms so the fresh-glow decays smoothly; no tick at all
  // when there are no recent events (saves needless re-layouts).
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const freshExists = events.some((e) => {
      if (e.event_type !== "reflection") return false;
      return Date.now() - new Date(e.timestamp).getTime() < FRESH_HIGHLIGHT_WINDOW_MS;
    });
    if (!freshExists) return;
    const t = setInterval(() => setNow(Date.now()), 500);
    return () => clearInterval(t);
  }, [events]);

  const graph = useMemo(() => deriveGraph(events, now, skillUI), [events, now, skillUI]);

  const onNodeClick = useCallback(
    (_: React.MouseEvent, node: Node) => {
      // TODO (UI-6.1): open a side panel with full lesson provenance —
      // DERIVED_FROM attempt, APPLIES_TO other rules, HELPED counts.
      // V1 just logs so the click path exists for future wiring.
      // eslint-disable-next-line no-console
      console.log("memory node clicked:", node.id, node.data);
    },
    [],
  );

  return (
    <div className="flex-1 flex flex-col overflow-hidden bg-[#0B0D11]">
      <div className="px-5 py-3 border-b border-[#1C1F26] flex items-center gap-4">
        <div>
          <div className="text-[10px] font-semibold tracking-[0.2em] uppercase text-[#4B5563]">
            Reflective memory
          </div>
          <div className="text-[13px] font-bold text-[#E8EAED]">
            Rules, lessons, and the edges between them
          </div>
        </div>
        <div className="ml-auto flex items-center gap-4 text-[10px] font-mono text-[#9CA3AF]">
          <span className="flex items-center gap-1.5">
            <span
              className="inline-block w-2 h-2 rounded"
              style={{ background: RULE_COLOR, boxShadow: `0 0 6px ${RULE_COLOR}` }}
            />
            {graph.ruleCount} rules
          </span>
          <span className="flex items-center gap-1.5">
            <span
              className="inline-block w-2 h-2 rounded"
              style={{ background: LESSON_COLOR, boxShadow: `0 0 6px ${LESSON_COLOR}` }}
            />
            {graph.lessonCount} lessons
          </span>
          <span className="text-[#3F4451]">·</span>
          <span className="text-[#6B7280]">
            V1: derived from current event stream. V2 will query Neo4j directly.
          </span>
        </div>
      </div>

      <div className="flex-1 relative">
        {graph.nodes.length === 0 ? (
          <div className="absolute inset-0 flex items-center justify-center text-[11px] text-[#4B5563]">
            Waiting for the first reflection to distill a lesson...
          </div>
        ) : (
          <ReactFlow
            nodes={graph.nodes}
            edges={graph.edges}
            nodeTypes={nodeTypes}
            onNodeClick={onNodeClick}
            fitView
            fitViewOptions={{ padding: 0.15 }}
            proOptions={{ hideAttribution: true }}
            minZoom={0.2}
            maxZoom={2}
          >
            <Background color="#1C1F26" gap={24} />
            <Controls
              position="bottom-right"
              showInteractive={false}
              style={{
                background: "#0F1217",
                border: "1px solid #2A2F38",
                borderRadius: 6,
              }}
            />
          </ReactFlow>
        )}
      </div>
    </div>
  );
}

// ============================================================================
// Helpers
// ============================================================================

function shortRuleId(id: string): string {
  const prefix = "xccdf_org.ssgproject.content_rule_";
  return id.startsWith(prefix) ? id.slice(prefix.length) : id;
}

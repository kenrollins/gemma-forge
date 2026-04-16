"use client";

/**
 * MemoryTab — the Reflective tier, visualized as a live graph.
 *
 * Three node types connected in a tree:
 *   Category (gray)   →   Rule (blue, outcome-colored)   →   Lesson (purple)
 *
 * Categories cluster the rules so the graph branches visibly instead
 * of stacking into one horizontal line of boxes. Rules animate their
 * color as outcomes land. Lessons pulse for ~4 seconds after they're
 * distilled so the eye catches new writes even at 1000x replay.
 *
 * V1 derives the graph from the current event stream. V2 will fetch
 * from a Neo4j-backed /api/memory/graph endpoint so we can see
 * cross-run provenance (DERIVED_FROM attempts, HELPED edges,
 * SUPERSEDED_BY chains) and lesson retrieval frequency.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
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
// Shape + layout tuning
// ============================================================================

const RULE_COLOR = "#3B82F6";
const LESSON_COLOR = "#A855F7";
const CATEGORY_COLOR = "#4B5563";
const FRESH_WINDOW_MS = 4000;

const CATEGORY_WIDTH = 180;
const CATEGORY_HEIGHT = 44;
const RULE_WIDTH = 180;
const RULE_HEIGHT = 48;
const LESSON_WIDTH = 200;
const LESSON_HEIGHT = 82;

// Keep the graph big enough to read but small enough to fit on screen.
// Rules capped by total; lessons capped per rule so one noisy rule
// doesn't dominate. Categories show whatever is referenced.
const MAX_RULES = 40;
const MAX_LESSONS_PER_RULE = 4;

// ============================================================================
// Layout — dagre top-down, with per-level spacing tuned so the tree
// visibly branches rather than stringing out in one row.
// ============================================================================

function layout(nodes: Node[], edges: Edge[]) {
  const g = new dagre.graphlib.Graph({ compound: false });
  g.setGraph({
    rankdir: "TB",
    nodesep: 24,
    ranksep: 110,
    marginx: 40,
    marginy: 40,
    ranker: "tight-tree",
  });
  g.setDefaultEdgeLabel(() => ({}));

  for (const n of nodes) {
    const t = (n.data as { type?: string })?.type;
    const w = t === "lesson" ? LESSON_WIDTH : t === "category" ? CATEGORY_WIDTH : RULE_WIDTH;
    const h = t === "lesson" ? LESSON_HEIGHT : t === "category" ? CATEGORY_HEIGHT : RULE_HEIGHT;
    g.setNode(n.id, { width: w, height: h });
  }
  for (const e of edges) g.setEdge(e.source, e.target);

  dagre.layout(g);

  return nodes.map((n) => {
    const t = (n.data as { type?: string })?.type;
    const w = t === "lesson" ? LESSON_WIDTH : t === "category" ? CATEGORY_WIDTH : RULE_WIDTH;
    const h = t === "lesson" ? LESSON_HEIGHT : t === "category" ? CATEGORY_HEIGHT : RULE_HEIGHT;
    const d = g.node(n.id);
    return {
      ...n,
      position: { x: d.x - w / 2, y: d.y - h / 2 },
    };
  });
}

// ============================================================================
// Custom node components
// ============================================================================

interface CategoryNodeData extends Record<string, unknown> {
  type: "category";
  category: string;
  ruleCount: number;
}

interface RuleNodeData extends Record<string, unknown> {
  type: "rule";
  ruleId: string;
  title?: string;
  category?: string;
  outcome?: "completed" | "escalated" | "skip" | "active" | "unknown";
  lessonCount: number;
  isLatest: boolean;
}

interface LessonNodeData extends Record<string, unknown> {
  type: "lesson";
  text: string;
  category?: string;
  freshMs: number;
  sourceRuleId?: string;
}

const OUTCOME_BG: Record<string, string> = {
  completed: "rgba(34,197,94,0.16)",
  escalated: "rgba(245,158,11,0.16)",
  skip: "rgba(75,85,99,0.14)",
  active: "rgba(59,130,246,0.22)",
  unknown: "rgba(59,130,246,0.08)",
};

const OUTCOME_BORDER: Record<string, string> = {
  completed: "#22C55E",
  escalated: "#F59E0B",
  skip: "#4B5563",
  active: "#60A5FA",
  unknown: "#3B82F6",
};

function CategoryNode({ data }: NodeProps) {
  const d = data as CategoryNodeData;
  return (
    <div
      className="rounded-md border text-center px-3 py-2"
      style={{
        width: CATEGORY_WIDTH,
        height: CATEGORY_HEIGHT,
        background: "rgba(75,85,99,0.12)",
        borderColor: CATEGORY_COLOR,
      }}
    >
      <div className="text-[10px] uppercase tracking-[0.2em] font-bold text-[#9CA3AF] truncate">
        {d.category}
      </div>
      <div className="text-[9px] text-[#6B7280] font-mono tabular-nums mt-0.5">
        {d.ruleCount} rule{d.ruleCount === 1 ? "" : "s"}
      </div>
      <Handle type="source" position={Position.Bottom} style={{ background: CATEGORY_COLOR }} />
    </div>
  );
}

function RuleNode({ data }: NodeProps) {
  const d = data as RuleNodeData;
  const outcome = d.outcome || "unknown";
  const border = OUTCOME_BORDER[outcome];
  const glow = d.isLatest ? `0 0 18px ${border}99` : `0 0 6px ${border}33`;
  return (
    <div
      className="rounded-md border px-2.5 py-1.5 text-left"
      style={{
        width: RULE_WIDTH,
        height: RULE_HEIGHT,
        background: OUTCOME_BG[outcome],
        borderColor: border,
        boxShadow: glow,
        transition: "box-shadow 600ms, background 400ms",
      }}
    >
      <Handle type="target" position={Position.Top} style={{ background: RULE_COLOR }} />
      <div className="flex items-center gap-1.5">
        <span
          className="inline-block w-1.5 h-1.5 rounded-full shrink-0"
          style={{ background: border, boxShadow: d.isLatest ? `0 0 6px ${border}` : undefined }}
        />
        <span
          className="text-[10px] font-mono text-[#E8EAED] truncate flex-1"
          title={d.title || d.ruleId}
        >
          {shortRuleId(d.ruleId)}
        </span>
        {d.lessonCount > 0 && (
          <span
            className="text-[8px] font-mono tabular-nums shrink-0"
            style={{ color: "#C084FC" }}
            title={`${d.lessonCount} lesson${d.lessonCount === 1 ? "" : "s"}`}
          >
            {d.lessonCount}L
          </span>
        )}
      </div>
      <div className="text-[8px] uppercase tracking-wider mt-0.5" style={{ color: border }}>
        {outcome}
      </div>
      <Handle type="source" position={Position.Bottom} style={{ background: RULE_COLOR }} />
    </div>
  );
}

function LessonNode({ data }: NodeProps) {
  const d = data as LessonNodeData;
  const freshFactor = Math.max(0, 1 - d.freshMs / FRESH_WINDOW_MS);
  const border = freshFactor > 0 ? "#C084FC" : "rgba(168,85,247,0.55)";
  const bg = freshFactor > 0 ? "rgba(168,85,247,0.2)" : "rgba(168,85,247,0.06)";
  const glow =
    freshFactor > 0
      ? `0 0 ${8 + 16 * freshFactor}px rgba(168,85,247,${0.35 + 0.5 * freshFactor})`
      : "0 0 4px rgba(168,85,247,0.12)";
  return (
    <div
      className="rounded-md border px-2 py-1.5 text-left"
      style={{
        width: LESSON_WIDTH,
        height: LESSON_HEIGHT,
        background: bg,
        borderColor: border,
        boxShadow: glow,
        transition: "background 400ms, border-color 400ms, box-shadow 400ms",
      }}
    >
      <Handle type="target" position={Position.Top} style={{ background: LESSON_COLOR }} />
      <div className="text-[8px] uppercase tracking-[0.18em] font-semibold text-[#C4B5FD]">
        lesson
      </div>
      <div
        className="text-[10px] font-mono leading-snug text-[#E8EAED] mt-0.5 line-clamp-3"
        title={d.text}
      >
        {d.text}
      </div>
    </div>
  );
}

const nodeTypes = {
  category: CategoryNode,
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
  categoryCount: number;
}

interface RuleRec {
  ruleId: string;
  title?: string;
  category?: string;
  outcome: RuleNodeData["outcome"];
  tsMs: number;
}

interface LessonRec {
  id: string;
  text: string;
  category?: string;
  sourceRuleId?: string;
  tsMs: number;
}

function deriveGraph(events: RunEvent[], now: number): DerivedGraph {
  const ruleState = new Map<string, RuleRec>();
  const lessons: LessonRec[] = [];
  let lessonCounter = 0;
  let latestRuleId: string | undefined;

  for (const e of events) {
    const tsMs = new Date(e.timestamp).getTime();
    const d = e.data || {};
    const ruleId = (d.rule_id as string | undefined) || undefined;

    if (ruleId && (e.event_type === "rule_selected" || e.event_type === "attempt_start")) {
      const prev = ruleState.get(ruleId);
      ruleState.set(ruleId, {
        ruleId,
        title: (d.title as string) || prev?.title,
        category: (d.category as string) || prev?.category,
        outcome: prev?.outcome === "completed" || prev?.outcome === "escalated" || prev?.outcome === "skip" ? prev.outcome : "active",
        tsMs,
      });
      latestRuleId = ruleId;
    }
    if (ruleId && e.event_type === "remediated") {
      const prev = ruleState.get(ruleId) || { ruleId, outcome: "active" as const, tsMs };
      ruleState.set(ruleId, { ...prev, outcome: "completed", tsMs });
    }
    if (ruleId && e.event_type === "escalated") {
      const prev = ruleState.get(ruleId) || { ruleId, outcome: "active" as const, tsMs };
      ruleState.set(ruleId, { ...prev, outcome: "escalated", tsMs });
    }
    if (ruleId && e.event_type === "skip") {
      const prev = ruleState.get(ruleId) || { ruleId, outcome: "active" as const, tsMs };
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

  // Sort rules by recency; take the most-recent MAX_RULES.
  const sortedRules = [...ruleState.values()]
    .sort((a, b) => b.tsMs - a.tsMs)
    .slice(0, MAX_RULES);
  const keptRuleIds = new Set(sortedRules.map((r) => r.ruleId));

  // Per-rule lesson buckets, capped at MAX_LESSONS_PER_RULE with the
  // most recent lessons winning. This guarantees lessons of recently-
  // touched rules remain visible even as new rules are added.
  const byRule = new Map<string, LessonRec[]>();
  for (let i = lessons.length - 1; i >= 0; i--) {
    const l = lessons[i];
    if (!l.sourceRuleId || !keptRuleIds.has(l.sourceRuleId)) continue;
    const arr = byRule.get(l.sourceRuleId) || [];
    if (arr.length >= MAX_LESSONS_PER_RULE) continue;
    arr.push(l);
    byRule.set(l.sourceRuleId, arr);
  }
  const keptLessons: LessonRec[] = [];
  for (const arr of byRule.values()) keptLessons.push(...arr);

  // Categories present among kept rules; edge from category node to
  // each of its rules so the graph branches instead of stringing out.
  const categories = new Map<string, number>();
  for (const r of sortedRules) {
    if (!r.category) continue;
    categories.set(r.category, (categories.get(r.category) || 0) + 1);
  }

  const nodes: Node[] = [];
  const edges: Edge[] = [];

  for (const [cat, count] of categories.entries()) {
    nodes.push({
      id: `cat-${cat}`,
      type: "category",
      position: { x: 0, y: 0 },
      data: { type: "category", category: cat, ruleCount: count } satisfies CategoryNodeData,
    });
  }

  for (const r of sortedRules) {
    const lessonCount = byRule.get(r.ruleId)?.length || 0;
    nodes.push({
      id: `rule-${r.ruleId}`,
      type: "rule",
      position: { x: 0, y: 0 },
      data: {
        type: "rule",
        ruleId: r.ruleId,
        title: r.title,
        category: r.category,
        outcome: r.outcome,
        lessonCount,
        isLatest: r.ruleId === latestRuleId,
      } satisfies RuleNodeData,
    });
    if (r.category) {
      edges.push({
        id: `e-cat-${cat_key(r.category, r.ruleId)}`,
        source: `cat-${r.category}`,
        target: `rule-${r.ruleId}`,
        style: { stroke: "rgba(75,85,99,0.5)", strokeWidth: 1 },
      });
    }
  }

  for (const l of keptLessons) {
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
    if (l.sourceRuleId && keptRuleIds.has(l.sourceRuleId)) {
      const fresh = now - l.tsMs < FRESH_WINDOW_MS;
      edges.push({
        id: `e-${l.id}`,
        source: `rule-${l.sourceRuleId}`,
        target: l.id,
        animated: fresh,
        style: {
          stroke: fresh ? "rgba(192,132,252,0.9)" : "rgba(168,85,247,0.35)",
          strokeWidth: fresh ? 1.5 : 1,
        },
        markerEnd: {
          type: MarkerType.ArrowClosed,
          color: fresh ? "rgba(192,132,252,1)" : "rgba(168,85,247,0.6)",
        },
      });
    }
  }

  return {
    nodes: layout(nodes, edges),
    edges,
    ruleCount: sortedRules.length,
    lessonCount: keptLessons.length,
    categoryCount: categories.size,
  };
}

function cat_key(category: string, ruleId: string): string {
  return `${category}-${ruleId}`.replace(/[^a-zA-Z0-9]/g, "_");
}

// ============================================================================
// Main component
// ============================================================================

export interface MemoryTabProps {
  events: RunEvent[];
  skillUI: SkillUI;
}

export default function MemoryTab({ events }: MemoryTabProps) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const freshExists = events.some(
      (e) =>
        e.event_type === "reflection" &&
        Date.now() - new Date(e.timestamp).getTime() < FRESH_WINDOW_MS,
    );
    if (!freshExists) return;
    const t = setInterval(() => setNow(Date.now()), 500);
    return () => clearInterval(t);
  }, [events]);

  const graph = useMemo(() => deriveGraph(events, now), [events, now]);

  const onNodeClick = useCallback((_: React.MouseEvent, node: Node) => {
    // Hook for UI-6.1's side panel with full provenance.
    // eslint-disable-next-line no-console
    console.log("memory node clicked:", node.id, node.data);
  }, []);

  return (
    <div className="flex-1 flex flex-col overflow-hidden bg-[#0B0D11]">
      <div className="px-5 py-3 border-b border-[#1C1F26] flex items-center gap-4">
        <div>
          <div className="text-[10px] font-semibold tracking-[0.2em] uppercase text-[#4B5563]">
            Reflective memory
          </div>
          <div className="text-[13px] font-bold text-[#E8EAED]">
            Categories → rules → distilled lessons
          </div>
        </div>
        <div className="ml-auto flex items-center gap-4 text-[10px] font-mono text-[#9CA3AF]">
          <LegendSwatch color={CATEGORY_COLOR} label={`${graph.categoryCount} categories`} />
          <LegendSwatch color={RULE_COLOR} label={`${graph.ruleCount} rules`} />
          <LegendSwatch color={LESSON_COLOR} label={`${graph.lessonCount} lessons`} />
          <span className="text-[#3F4451]">·</span>
          <span className="text-[#6B7280]">
            V1: current run only. V2 will query Neo4j for full cross-run graph.
          </span>
        </div>
      </div>

      <div className="flex-1 relative">
        {graph.nodes.length === 0 ? (
          <div className="absolute inset-0 flex items-center justify-center text-[11px] text-[#4B5563]">
            Waiting for the first rule to select...
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
            minZoom={0.15}
            maxZoom={2}
            nodesDraggable={false}
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

function LegendSwatch({ color, label }: { color: string; label: string }) {
  return (
    <span className="flex items-center gap-1.5">
      <span
        className="inline-block w-2 h-2 rounded"
        style={{ background: color, boxShadow: `0 0 6px ${color}` }}
      />
      <span>{label}</span>
    </span>
  );
}

function shortRuleId(id: string): string {
  const prefix = "xccdf_org.ssgproject.content_rule_";
  return id.startsWith(prefix) ? id.slice(prefix.length) : id;
}

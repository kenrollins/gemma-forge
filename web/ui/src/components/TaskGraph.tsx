"use client";

import { useMemo } from "react";
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

interface GraphEdge { from: string; to: string; }

interface GraphSnapshot {
  nodes: GraphNode[];
  edges: GraphEdge[];
  counts: Record<string, number>;
}

// -- SpaceX-style semantic colors (4 + grayscale) ---------------------------

const STATE_COLOR: Record<string, string> = {
  queued:    "#3B4252",   // dim gray — waiting
  blocked:   "#92400E",   // deep amber — dependency blocked
  active:    "#22D3EE",   // cyan — the SpaceX telemetry blue
  completed: "#10B981",   // emerald — confirmed good
  escalated: "#F59E0B",   // amber — attention/warning (NOT red)
  skipped:   "#4B5563",   // medium gray
};

const STATE_LABEL: Record<string, string> = {
  queued: "Queued", blocked: "Blocked", active: "Active",
  completed: "Done", escalated: "Escalated", skipped: "Skipped",
};

const CELL_SIZE = 18;  // px — each task is one square
const CELL_GAP = 2;    // px

// -- Component --------------------------------------------------------------

export default function TaskGraph({
  events,
  skillUI,
}: {
  events: RunEvent[];
  skillUI: SkillUI;
}) {
  const graphState: GraphSnapshot | null = useMemo(() => {
    for (let i = events.length - 1; i >= 0; i--) {
      if (events[i].event_type === "graph_state" && events[i].data?.nodes) {
        return events[i].data as unknown as GraphSnapshot;
      }
    }
    return null;
  }, [events]);

  const categories = useMemo(() => {
    if (!graphState) return [];
    const groups: Record<string, GraphNode[]> = {};
    for (const node of graphState.nodes) {
      const cat = node.category || "uncategorized";
      if (!groups[cat]) groups[cat] = [];
      groups[cat].push(node);
    }
    return Object.entries(groups).sort((a, b) => {
      const rateA = a[1].filter(n => n.state === "completed").length / a[1].length;
      const rateB = b[1].filter(n => n.state === "completed").length / b[1].length;
      return rateB - rateA;
    });
  }, [graphState]);

  if (!graphState || graphState.nodes.length === 0) {
    return (
      <div className="flex items-center justify-center h-full text-[#4B5563] text-xs font-mono">
        Awaiting task graph...
      </div>
    );
  }

  const counts = graphState.counts;
  const total = graphState.nodes.length;
  // "Processed" = remediated + escalated + skipped (all done, won't be retried)
  const processed = (counts.completed || 0) + (counts.escalated || 0) + (counts.skipped || 0);
  const processedPct = total > 0 ? Math.round((processed / total) * 100) : 0;

  const stripId = (id: string) => {
    if (skillUI.id_prefix_strip && id.startsWith(skillUI.id_prefix_strip))
      return id.slice(skillUI.id_prefix_strip.length);
    return id;
  };

  return (
    <div className="flex flex-col h-full overflow-hidden">
      {/* Header with hero metric + state legend */}
      <div className="px-4 py-2.5 border-b border-[#2A2F3A] bg-[#12151A]">
        <div className="flex items-center gap-4 mb-2">
          {/* Hero completion metric */}
          <div className="flex items-baseline gap-1.5">
            <span className="text-2xl font-light tabular-nums tracking-tight text-[#10B981]">
              {counts.completed || 0}
            </span>
            <span className="text-[10px] text-[#4B5563]">remediated</span>
            <span className="text-xl font-light tabular-nums tracking-tight text-[#F59E0B] ml-1">
              {counts.escalated || 0}
            </span>
            <span className="text-[10px] text-[#4B5563]">escalated</span>
            <span className="text-[10px] text-[#4B5563] ml-1">
              of {total}
            </span>
          </div>
          <span className="text-[10px] uppercase tracking-[0.15em] text-[#8B95A5]">
            {processedPct}% processed
          </span>

          {/* State legend */}
          <div className="flex gap-2.5 ml-auto">
            {(["completed", "active", "escalated", "queued"] as const)
              .filter(s => (counts[s] || 0) > 0)
              .map(state => (
                <span key={state} className="flex items-center gap-1">
                  <span
                    className="w-2.5 h-2.5 rounded-[3px]"
                    style={{ backgroundColor: STATE_COLOR[state] }}
                  />
                  <span className="text-[9px] font-mono tabular-nums text-[#8B95A5]">
                    {counts[state]} {STATE_LABEL[state]}
                  </span>
                </span>
              ))}
          </div>
        </div>

        {/* Segmented progress bar — remediated | escalated | active | remaining */}
        <div className="h-2 rounded-full bg-[#1F2937] overflow-hidden flex">
          {["completed", "escalated", "skipped", "active", "blocked", "queued"].map(state => {
            const pct = total > 0 ? ((counts[state] || 0) / total) * 100 : 0;
            if (pct === 0) return null;
            return (
              <div
                key={state}
                className={`h-full transition-all duration-700 ease-out ${
                  state === "active" ? "animate-pulse" : ""
                }`}
                style={{ width: `${pct}%`, backgroundColor: STATE_COLOR[state] }}
              />
            );
          })}
        </div>
      </div>

      {/* Heatmap grid — full width */}
      <div className="flex-1 overflow-hidden">
        <div className="h-full overflow-y-auto px-4 py-3 space-y-3">
          {categories.map(([category, nodes]) => {
            const done = nodes.filter(n => n.state === "completed").length;
            const esc = nodes.filter(n => n.state === "escalated").length;
            const cols = Math.max(6, Math.ceil(Math.sqrt(nodes.length * 1.5)));
            return (
              <div key={category}>
                {/* Category label */}
                <div className="flex items-center gap-2 mb-1.5">
                  <span className="text-[10px] uppercase tracking-[0.12em] font-semibold text-[#8B95A5]">
                    {category}
                  </span>
                  <span className="text-[9px] font-mono tabular-nums text-[#4B5563]">
                    {done}/{nodes.length}
                    {esc > 0 && <span className="text-[#F59E0B] ml-1">{esc} esc</span>}
                  </span>
                  <div className="flex-1 h-px bg-[#2A2F3A]" />
                </div>
                {/* Waffle grid */}
                <div
                  className="grid"
                  style={{
                    gridTemplateColumns: `repeat(${cols}, ${CELL_SIZE}px)`,
                    gap: `${CELL_GAP}px`,
                  }}
                >
                  {nodes.map(node => (
                    <div
                      key={node.id}
                      className={`rounded-[3px] cursor-pointer transition-all duration-200
                        hover:scale-[1.8] hover:z-20 hover:ring-2 hover:ring-white/40
                        ${node.state === "active" ? "animate-pulse" : ""}
                        group relative
                      `}
                      style={{
                        width: CELL_SIZE,
                        height: CELL_SIZE,
                        backgroundColor: STATE_COLOR[node.state],
                        opacity: node.state === "queued" ? 0.4 : 1,
                        boxShadow: node.state === "active"
                          ? `0 0 8px ${STATE_COLOR.active}40`
                          : node.state === "completed"
                          ? `0 0 3px ${STATE_COLOR.completed}30`
                          : "none",
                      }}
                    >
                      {/* Hover tooltip */}
                      {node.state !== "queued" && (
                        <div className="absolute bottom-full left-1/2 -translate-x-1/2 mb-2
                          hidden group-hover:block z-30 pointer-events-none">
                          <div className="bg-[#1a1e26] border border-[#2A2F3A] rounded-md px-3 py-2
                            shadow-xl min-w-[200px] max-w-[300px]">
                            <div className="text-[10px] font-mono font-bold text-[#E2E8F0] break-words">
                              {stripId(node.id)}
                            </div>
                            <div className="text-[9px] text-[#8B95A5] mt-0.5">
                              {node.title}
                            </div>
                            <div className="flex items-center gap-2 mt-1.5">
                              <span className="text-[8px] font-mono px-1.5 py-0.5 rounded"
                                style={{
                                  backgroundColor: STATE_COLOR[node.state] + "33",
                                  color: STATE_COLOR[node.state],
                                }}>
                                {node.state}
                              </span>
                              {node.attempts > 0 && (
                                <span className="text-[9px] font-mono text-[#6B7280]">
                                  {node.attempts} attempts
                                </span>
                              )}
                              {node.wall_time_s > 0 && (
                                <span className="text-[9px] font-mono text-[#6B7280]">
                                  {Math.round(node.wall_time_s)}s
                                </span>
                              )}
                            </div>
                            {node.escalation_reason && (
                              <div className="text-[9px] text-[#F59E0B] mt-1">
                                {node.escalation_reason}
                              </div>
                            )}
                          </div>
                        </div>
                      )}
                    </div>
                  ))}
                </div>
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}

"use client";

import { useMemo } from "react";
import { RunEvent, SkillUI } from "./types";

// -- Types for graph state --------------------------------------------------

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

// -- State colors -----------------------------------------------------------

const STATE_COLORS: Record<string, string> = {
  queued: "#374151",     // gray-700
  blocked: "#92400E",    // amber-900
  active: "#2563EB",     // blue-600
  completed: "#16A34A",  // green-600
  escalated: "#DC2626",  // red-600
  skipped: "#4B5563",    // gray-600
};

const STATE_BG: Record<string, string> = {
  queued: "bg-[#1a1d24]",
  blocked: "bg-[#3d2800]",
  active: "bg-[#1a3a6b]",
  completed: "bg-[#0a3d1a]",
  escalated: "bg-[#3d0a0a]",
  skipped: "bg-[#1a1d24]",
};

const STATE_BORDER: Record<string, string> = {
  queued: "border-[#2a2e38]",
  blocked: "border-[#b45309]",
  active: "border-[#3B82F6] animate-pulse",
  completed: "border-[#22C55E]",
  escalated: "border-[#EF4444]",
  skipped: "border-[#374151]",
};

const STATE_GLOW: Record<string, string> = {
  active: "shadow-[0_0_10px_rgba(59,130,246,0.5)]",
  completed: "shadow-[0_0_4px_rgba(34,197,94,0.2)]",
  escalated: "shadow-[0_0_4px_rgba(239,68,68,0.2)]",
  queued: "",
  blocked: "",
  skipped: "",
};

// -- Component --------------------------------------------------------------

export default function TaskGraph({
  events,
  skillUI,
}: {
  events: RunEvent[];
  skillUI: SkillUI;
}) {
  // Find the latest graph_state event
  const graphState: GraphSnapshot | null = useMemo(() => {
    for (let i = events.length - 1; i >= 0; i--) {
      if (events[i].event_type === "graph_state" && events[i].data?.nodes) {
        return events[i].data as unknown as GraphSnapshot;
      }
    }
    return null;
  }, [events]);

  // Group nodes by category — must be called unconditionally (Rules of Hooks)
  const categories = useMemo(() => {
    if (!graphState) return [];
    const groups: Record<string, GraphNode[]> = {};
    for (const node of graphState.nodes) {
      const cat = node.category || "uncategorized";
      if (!groups[cat]) groups[cat] = [];
      groups[cat].push(node);
    }
    // Sort categories by completion rate (most complete first)
    return Object.entries(groups).sort((a, b) => {
      const rateA = a[1].filter(n => n.state === "completed").length / a[1].length;
      const rateB = b[1].filter(n => n.state === "completed").length / b[1].length;
      return rateB - rateA;
    });
  }, [graphState]);

  if (!graphState || graphState.nodes.length === 0) {
    return (
      <div className="flex items-center justify-center h-full text-[#4B5563] text-xs">
        Awaiting task graph data...
      </div>
    );
  }

  const counts = graphState.counts;
  const total = graphState.nodes.length;
  const completedPct = total > 0 ? Math.round((counts.completed / total) * 100) : 0;

  // Strip ID prefix for display
  const stripId = (id: string) => {
    if (skillUI.id_prefix_strip && id.startsWith(skillUI.id_prefix_strip)) {
      return id.slice(skillUI.id_prefix_strip.length);
    }
    return id;
  };

  return (
    <div className="flex flex-col h-full overflow-hidden">
      {/* Progress bar */}
      <div className="px-4 py-2 border-b border-[#1C1F26] bg-[#0D0F14]">
        <div className="flex items-center gap-3 mb-1.5">
          <span className="text-[10px] font-semibold uppercase tracking-wider text-[#6B7280]">
            Task Graph
          </span>
          <button
            onClick={() => {
              // Open task graph in a new window with full detail
              const w = window.open("", "taskgraph", "width=1200,height=800");
              if (w) {
                w.document.title = "GemmaForge — Task Graph";
                w.document.body.style.cssText = "margin:0;background:#0B0D11;color:#E8EAED;font-family:monospace;";
                const pre = w.document.createElement("pre");
                pre.style.cssText = "padding:24px;font-size:11px;line-height:1.6;overflow:auto;height:100vh;";
                const lines: string[] = [];
                lines.push(`TASK GRAPH — ${graphState.nodes.length} items\n`);
                lines.push(`${"=".repeat(60)}\n`);
                for (const [cat, nodes] of categories) {
                  const done = nodes.filter(n => n.state === "completed").length;
                  lines.push(`\n▸ ${cat.toUpperCase()} (${done}/${nodes.length})\n`);
                  for (const n of nodes) {
                    const icon = n.state === "completed" ? "✓" : n.state === "escalated" ? "✗" : n.state === "active" ? "⟳" : n.state === "blocked" ? "⊘" : "·";
                    const detail = [
                      n.attempts > 0 ? `${n.attempts} attempts` : "",
                      n.wall_time_s > 0 ? `${Math.round(n.wall_time_s)}s` : "",
                      n.escalation_reason || "",
                    ].filter(Boolean).join(", ");
                    lines.push(`  ${icon} ${stripId(n.id)} — ${n.title}${detail ? ` (${detail})` : ""}\n`);
                  }
                }
                // Edges
                if (graphState.edges.length > 0) {
                  lines.push(`\n${"=".repeat(60)}\nDEPENDENCIES (${graphState.edges.length})\n`);
                  for (const e of graphState.edges) {
                    lines.push(`  ${stripId(e.from)} → ${stripId(e.to)}\n`);
                  }
                }
                pre.textContent = lines.join("");
                w.document.body.appendChild(pre);
              }
            }}
            className="text-[9px] text-[#4B5563] hover:text-[#9CA3AF] transition-colors"
            title="Open full detail in new window"
          >
            ↗ Detail
          </button>
          <span className="text-[10px] font-mono text-[#9CA3AF]">
            {counts.completed}/{total} complete ({completedPct}%)
          </span>
          <div className="flex gap-3 ml-auto">
            {Object.entries(counts)
              .filter(([, v]) => v > 0)
              .map(([state, count]) => (
                <span key={state} className="flex items-center gap-1">
                  <span
                    className="w-2 h-2 rounded-sm"
                    style={{ backgroundColor: STATE_COLORS[state] || "#4B5563" }}
                  />
                  <span className="text-[9px] font-mono text-[#6B7280]">
                    {count} {state}
                  </span>
                </span>
              ))}
          </div>
        </div>
        {/* Segmented progress bar */}
        <div className="h-1.5 rounded-full bg-[#1F2937] overflow-hidden flex">
          {["completed", "active", "escalated", "skipped", "blocked", "queued"].map((state) => {
            const pct = total > 0 ? (counts[state] / total) * 100 : 0;
            if (pct === 0) return null;
            return (
              <div
                key={state}
                className={`h-full transition-all duration-700 ease-out ${
                  state === "active" ? "animate-pulse" : ""
                }`}
                style={{
                  width: `${pct}%`,
                  backgroundColor: STATE_COLORS[state],
                }}
              />
            );
          })}
        </div>
      </div>

      {/* Category grid */}
      <div className="flex-1 overflow-y-auto px-3 py-2 space-y-2">
        {categories.map(([category, nodes]) => {
          const catCompleted = nodes.filter(n => n.state === "completed").length;
          const catPct = Math.round((catCompleted / nodes.length) * 100);
          return (
            <div key={category}>
              {/* Category header */}
              <div className="flex items-center gap-2 mb-1">
                <span className="text-[10px] font-semibold text-[#9CA3AF] uppercase tracking-wide">
                  {category}
                </span>
                <span className="text-[9px] font-mono text-[#4B5563]">
                  {catCompleted}/{nodes.length} ({catPct}%)
                </span>
                {/* Mini progress for category */}
                <div className="flex-1 h-0.5 rounded-full bg-[#1F2937] overflow-hidden">
                  <div
                    className="h-full bg-[#16A34A] transition-all duration-500"
                    style={{ width: `${catPct}%` }}
                  />
                </div>
              </div>
              {/* Item cells */}
              <div className="flex flex-wrap gap-1">
                {nodes.map((node) => (
                  <div
                    key={node.id}
                    className={`
                      group relative px-2 py-1 rounded border text-[9px] font-mono font-medium
                      transition-all duration-500 cursor-default
                      ${STATE_BG[node.state]} ${STATE_BORDER[node.state]} ${STATE_GLOW[node.state]}
                    `}
                    title={`${stripId(node.id)}: ${node.title}\nState: ${node.state}${
                      node.attempts > 0 ? `\nAttempts: ${node.attempts}` : ""
                    }${
                      node.wall_time_s > 0 ? `\nTime: ${Math.round(node.wall_time_s)}s` : ""
                    }${
                      node.escalation_reason ? `\nReason: ${node.escalation_reason}` : ""
                    }`}
                  >
                    <span className={`
                      ${node.state === "completed" ? "text-[#4ADE80]" : ""}
                      ${node.state === "escalated" ? "text-[#FCA5A5]" : ""}
                      ${node.state === "active" ? "text-[#93C5FD]" : ""}
                      ${node.state === "queued" ? "text-[#6B7280]" : ""}
                      ${node.state === "blocked" ? "text-[#FCD34D]" : ""}
                      ${node.state === "skipped" ? "text-[#6B7280]" : ""}
                    `}>
                      {stripId(node.id).slice(0, 24)}
                    </span>
                    {node.state === "active" && (
                      <span className="ml-1 inline-block w-1 h-1 rounded-full bg-[#3B82F6] animate-ping" />
                    )}
                  </div>
                ))}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

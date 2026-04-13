"use client";

import { useMemo, useState } from "react";
import { RunEvent, SkillUI, CrossRunData, GraphNode } from "./types";

// -- Colors --

const STATE_COLOR: Record<string, string> = {
  queued:    "#3B4252",
  blocked:   "#92400E",
  active:    "#22D3EE",
  completed: "#10B981",
  escalated: "#F59E0B",
  skipped:   "#4B5563",
};

const STATE_LABEL: Record<string, string> = {
  queued: "Queued", blocked: "Blocked", active: "Active",
  completed: "Done", escalated: "Escalated", skipped: "Skipped",
};

function formatTime(s: number): string {
  if (s < 60) return `${Math.round(s)}s`;
  if (s < 3600) return `${Math.floor(s / 60)}m ${Math.round(s % 60)}s`;
  return `${Math.floor(s / 3600)}h ${Math.floor((s % 3600) / 60)}m`;
}

function shortId(id: string, prefix: string): string {
  if (!id) return "\u2014";
  if (prefix && id.startsWith(prefix)) return id.slice(prefix.length);
  return id;
}

// -- Cross-run insight bar --

function CrossRunInsight({ data }: { data: CrossRunData }) {
  const [expanded, setExpanded] = useState(false);
  return (
    <div className="border-t border-[#1C1F26] bg-[#0D0F14]">
      <button
        onClick={() => setExpanded(!expanded)}
        className="w-full px-3 py-1.5 flex items-center gap-2 text-left hover:bg-[#12141A] transition-colors"
      >
        <span className="text-[9px] uppercase tracking-wider font-semibold text-[#22D3EE]">
          {"\u2728"} Cross-Run
        </span>
        <span className="text-[9px] text-[#4B5563]">
          {data.prior_runs} run{data.prior_runs !== 1 ? "s" : ""} {"\u00B7"} {data.loaded_bans} bans {"\u00B7"} {data.loaded_lessons} lessons
        </span>
        <span className="text-[10px] text-[#4B5563] ml-auto">{expanded ? "\u25B2" : "\u25BC"}</span>
      </button>
      {expanded && (
        <div className="px-3 pb-2 space-y-0.5">
          {[...data.category_stats].sort((a, b) => b.success_rate - a.success_rate).map(cat => {
            const pct = Math.round(cat.success_rate * 100);
            return (
              <div key={cat.category} className="flex items-center gap-1.5">
                <span className="text-[8px] text-[#6B7280] w-24 truncate text-right shrink-0">
                  {cat.category}
                </span>
                <div className="flex-1 h-1.5 rounded-full bg-[#1A1D24] overflow-hidden">
                  <div
                    className="h-full rounded-full"
                    style={{
                      width: `${Math.max(pct, 1)}%`,
                      background: pct >= 80 ? "#10B981" : pct >= 40 ? "#F59E0B" : "#EF4444",
                    }}
                  />
                </div>
                <span className="text-[8px] text-[#4B5563] tabular-nums w-8 shrink-0">{pct}%</span>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

// -- List view for small item counts --

function ListView({
  categories,
  skillUI,
  selectedItemId,
  onSelectItem,
}: {
  categories: [string, GraphNode[]][];
  skillUI: SkillUI;
  selectedItemId: string | null;
  onSelectItem: (id: string) => void;
}) {
  return (
    <div className="space-y-3">
      {categories.map(([category, nodes]) => {
        const done = nodes.filter(n => n.state === "completed").length;
        const esc = nodes.filter(n => n.state === "escalated").length;
        return (
          <div key={category}>
            <div className="flex items-center gap-2 mb-1">
              <span className="text-[10px] uppercase tracking-wider font-semibold text-[#8B95A5]">
                {category}
              </span>
              <span className="text-[9px] text-[#4B5563] tabular-nums">
                {done}/{nodes.length}
                {esc > 0 && <span className="text-[#F59E0B] ml-1">{esc} esc</span>}
              </span>
              <div className="flex-1 h-px bg-[#2A2F3A]" />
            </div>
            <div className="space-y-0.5">
              {nodes.map(node => {
                const isSelected = node.id === selectedItemId;
                const color = STATE_COLOR[node.state];
                return (
                  <button
                    key={node.id}
                    onClick={() => onSelectItem(node.id)}
                    className={`w-full text-left flex items-center gap-2 px-2 py-1 rounded-sm transition-colors
                      ${isSelected ? "bg-[#1A1D24] ring-1 ring-[#2A2E38]" : "hover:bg-[#1A1D24]"}
                      ${node.state === "active" ? "animate-pulse" : ""}
                    `}
                  >
                    <div
                      className="w-2 h-2 rounded-full shrink-0"
                      style={{ background: color }}
                    />
                    <span className="text-[11px] font-mono text-[#E8EAED] truncate flex-1">
                      {shortId(node.id, skillUI.id_prefix_strip)}
                    </span>
                    {node.title && (
                      <span className="text-[10px] text-[#6B7280] truncate max-w-[180px]">
                        {node.title}
                      </span>
                    )}
                    {node.attempts > 0 && (
                      <span className="text-[9px] text-[#4B5563] tabular-nums shrink-0">
                        {node.attempts}att
                      </span>
                    )}
                    {node.wall_time_s > 0 && (
                      <span className="text-[9px] text-[#4B5563] tabular-nums shrink-0">
                        {formatTime(node.wall_time_s)}
                      </span>
                    )}
                    <span
                      className="text-[8px] font-mono px-1 py-0.5 rounded shrink-0"
                      style={{ background: `${color}25`, color }}
                    >
                      {STATE_LABEL[node.state] || node.state}
                    </span>
                  </button>
                );
              })}
            </div>
          </div>
        );
      })}
    </div>
  );
}

// -- Waffle view for large item counts --

function WaffleView({
  categories,
  skillUI,
  selectedItemId,
  onSelectItem,
  totalItems,
}: {
  categories: [string, GraphNode[]][];
  skillUI: SkillUI;
  selectedItemId: string | null;
  onSelectItem: (id: string) => void;
  totalItems: number;
}) {
  // Scale cell size — smaller for dense maps, comfortable for sparse
  const cellSize = Math.max(10, Math.min(18, Math.floor(500 / Math.sqrt(totalItems))));
  const cellGap = 1;

  return (
    <div className="space-y-3">
      {categories.map(([category, nodes]) => {
        const done = nodes.filter(n => n.state === "completed").length;
        const esc = nodes.filter(n => n.state === "escalated").length;
        const cols = Math.max(6, Math.ceil(Math.sqrt(nodes.length * 1.5)));
        return (
          <div key={category}>
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
            <div
              className="grid"
              style={{
                gridTemplateColumns: `repeat(${cols}, ${cellSize}px)`,
                gap: `${cellGap}px`,
              }}
            >
              {nodes.map(node => {
                const isSelected = node.id === selectedItemId;
                return (
                  <div
                    key={node.id}
                    onClick={() => onSelectItem(node.id)}
                    className={`rounded-[3px] cursor-pointer transition-all duration-200
                      hover:scale-[1.6] hover:z-20 hover:ring-2 hover:ring-white/40
                      ${node.state === "active" ? "animate-pulse" : ""}
                      ${isSelected ? "ring-2 ring-white/60 scale-[1.4] z-20" : ""}
                      group relative
                    `}
                    style={{
                      width: cellSize,
                      height: cellSize,
                      backgroundColor: STATE_COLOR[node.state],
                      opacity: node.state === "queued" ? 0.4 : 1,
                      boxShadow: node.state === "active"
                        ? `0 0 8px ${STATE_COLOR.active}40`
                        : node.state === "completed"
                        ? `0 0 3px ${STATE_COLOR.completed}30`
                        : "none",
                    }}
                  >
                    {/* Hover tooltip — positioned left of cell since we're in the right sidebar */}
                    {node.state !== "queued" && (
                      <div className="absolute right-full top-1/2 -translate-y-1/2 mr-2
                        hidden group-hover:block z-50 pointer-events-none">
                        <div className="bg-[#1a1e26] border border-[#3A3F4A] rounded px-2.5 py-1.5
                          shadow-2xl w-[220px]"
                          style={{ boxShadow: "0 4px 20px rgba(0,0,0,0.6)" }}>
                          <div className="text-[11px] font-mono font-bold text-[#E8EAED] break-words leading-snug">
                            {shortId(node.id, skillUI.id_prefix_strip)}
                          </div>
                          <div className="text-[10px] text-[#9CA3AF] mt-0.5 leading-snug">{node.title}</div>
                          <div className="flex items-center gap-2 mt-1.5">
                            <span className="text-[9px] font-mono font-bold px-1.5 py-0.5 rounded"
                              style={{ backgroundColor: STATE_COLOR[node.state] + "30", color: STATE_COLOR[node.state] }}>
                              {node.state}
                            </span>
                            {node.attempts > 0 && (
                              <span className="text-[10px] font-mono text-[#9CA3AF]">{node.attempts} att</span>
                            )}
                            {node.wall_time_s > 0 && (
                              <span className="text-[10px] font-mono text-[#9CA3AF]">{formatTime(node.wall_time_s)}</span>
                            )}
                          </div>
                          {node.escalation_reason && (
                            <div className="text-[10px] text-[#F59E0B] mt-1">{node.escalation_reason}</div>
                          )}
                        </div>
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          </div>
        );
      })}
    </div>
  );
}

// -- Main TaskMap --

export default function TaskMap({
  events,
  skillUI,
  selectedItemId,
  onSelectItem,
  crossRunData,
}: {
  events: RunEvent[];
  skillUI: SkillUI;
  selectedItemId: string | null;
  onSelectItem: (id: string) => void;
  crossRunData: CrossRunData | null;
}) {
  // Extract latest graph state
  const graphNodes: GraphNode[] = useMemo(() => {
    for (let i = events.length - 1; i >= 0; i--) {
      if (events[i].event_type === "graph_state" && events[i].data?.nodes) {
        return events[i].data.nodes as GraphNode[];
      }
    }
    return [];
  }, [events]);

  // Group by category, sort by completion rate descending
  const categories: [string, GraphNode[]][] = useMemo(() => {
    const groups: Record<string, GraphNode[]> = {};
    for (const node of graphNodes) {
      const cat = node.category || "uncategorized";
      if (!groups[cat]) groups[cat] = [];
      groups[cat].push(node);
    }
    return Object.entries(groups).sort((a, b) => {
      const rateA = a[1].filter(n => n.state === "completed").length / a[1].length;
      const rateB = b[1].filter(n => n.state === "completed").length / b[1].length;
      return rateB - rateA;
    });
  }, [graphNodes]);

  const totalItems = graphNodes.length;

  // State legend counts
  const counts = useMemo(() => {
    const c: Record<string, number> = {};
    for (const n of graphNodes) {
      c[n.state] = (c[n.state] || 0) + 1;
    }
    return c;
  }, [graphNodes]);

  if (totalItems === 0) {
    return (
      <div className="flex items-center justify-center h-full text-[#4B5563] text-xs font-mono">
        Awaiting task graph\u2026
      </div>
    );
  }

  const useListView = totalItems <= 40;

  return (
    <div className="flex flex-col h-full overflow-hidden">
      {/* State legend strip */}
      <div className="px-4 py-2 border-b border-[#1C1F26] bg-[#0D0F14] flex items-center gap-3 shrink-0">
        <span className="text-[10px] text-[#4B5563] uppercase tracking-wider">
          {totalItems} {skillUI.work_item_plural}
        </span>
        <div className="flex gap-2.5 ml-auto">
          {(["completed", "active", "escalated", "skipped", "queued"] as const)
            .filter(s => (counts[s] || 0) > 0)
            .map(state => (
              <span key={state} className="flex items-center gap-1">
                <span
                  className="w-2 h-2 rounded-[2px]"
                  style={{ backgroundColor: STATE_COLOR[state] }}
                />
                <span className="text-[9px] font-mono tabular-nums text-[#6B7280]">
                  {counts[state]} {STATE_LABEL[state]}
                </span>
              </span>
            ))}
        </div>
      </div>

      {/* Main content area */}
      <div className="flex-1 overflow-y-auto px-4 py-3">
        {useListView ? (
          <ListView
            categories={categories}
            skillUI={skillUI}
            selectedItemId={selectedItemId}
            onSelectItem={onSelectItem}
          />
        ) : (
          <WaffleView
            categories={categories}
            skillUI={skillUI}
            selectedItemId={selectedItemId}
            onSelectItem={onSelectItem}
            totalItems={totalItems}
          />
        )}
      </div>

      {/* Cross-run insight (conditional) */}
      {crossRunData && <CrossRunInsight data={crossRunData} />}
    </div>
  );
}

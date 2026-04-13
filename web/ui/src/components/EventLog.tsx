"use client";

import { useRef, useEffect, useState } from "react";
import { RunEvent, AGENT_COLORS } from "./types";

// -- Filter tabs --

const FILTER_TABS = [
  { key: "all", label: "All" },
  { key: "architect", label: "Architect" },
  { key: "worker", label: "Worker" },
  { key: "reflector", label: "Reflector" },
  { key: "harness", label: "Harness" },
  { key: "errors", label: "Errors" },
] as const;

type FilterKey = (typeof FILTER_TABS)[number]["key"];

function matchesFilter(event: RunEvent, filter: FilterKey): boolean {
  if (filter === "all") return true;
  if (filter === "errors") return event.event_type === "error" || event.event_type === "tool_error";
  return event.agent === filter;
}

// -- Left border color by event type --

function leftBorderColor(event: RunEvent): string {
  switch (event.event_type) {
    case "tool_call":
    case "tool_result": return "#F59E0B40";
    case "evaluation": return event.data?.passed ? "#22C55E40" : "#EF444440";
    case "remediated": return "#22C55E60";
    case "escalated": return "#F59E0B60";
    case "reflection": return "#A855F740";
    case "revert": return "#EF444440";
    case "error":
    case "tool_error": return "#EF444460";
    case "iteration_start": return "#3B82F640";
    default: return "transparent";
  }
}

// -- Log entry --

function LogEntry({ event, expanded, onToggle }: { event: RunEvent; expanded: boolean; onToggle: () => void }) {
  const color = AGENT_COLORS[event.agent] || "#6B7280";
  const data = event.data;

  let summary = "";
  let detail = "";

  if (event.event_type === "agent_response") {
    const text = (data.text as string) || "";
    summary = text.slice(0, 120);
    detail = text;
  } else if (event.event_type === "tool_call") {
    summary = `\u2192 ${data.tool}(${JSON.stringify(data.args || {}).slice(0, 80)})`;
    detail = `Tool: ${data.tool}\nArguments:\n${JSON.stringify(data.args || {}, null, 2)}`;
  } else if (event.event_type === "tool_result") {
    const result = (data.result as string) || JSON.stringify(data.result || "");
    summary = `\u2190 ${result.slice(0, 120)}`;
    detail = result;
  } else if (event.event_type === "iteration_start") {
    // Render as a divider
    return (
      <div className="border-b border-[#2A2E38] bg-[#0D0F14] py-1 px-3">
        <div className="flex items-center gap-2 text-[10px] font-mono">
          <span className="text-[#3B82F6] font-bold">ITER {data.iteration}</span>
          <div className="flex-1 h-px bg-[#2A2E38]" />
          <span className="text-[#22C55E]">{data.remediated} fixed</span>
          <span className="text-[#F59E0B]">{data.escalated} esc</span>
          <span className="text-[#6B7280]">{data.failing} remaining</span>
          <span className="text-[#4B5563]">{Math.round(data.run_elapsed_s as number || 0)}s</span>
        </div>
      </div>
    );
  } else if (event.event_type === "skip") {
    summary = `\u2298 SKIP: ${data.rule_id} \u2014 ${data.reason}`;
  } else if (event.event_type === "revert") {
    summary = `\u2717 REVERT: ${(data.reason as string || "").slice(0, 120)}`;
    detail = data.reason as string;
  } else if (event.event_type === "reflection") {
    summary = `\u25C6 REFLECTION: ${(data.text as string || "").slice(0, 120)}`;
    detail = data.text as string;
  } else if (event.event_type === "error") {
    summary = `\u26A0 ERROR: ${(data.error as string || "").slice(0, 120)}`;
    detail = data.error as string;
  } else if (event.event_type === "remediated") {
    summary = `\u2713 REMEDIATED: ${data.rule_id} (attempt ${data.attempt}, ${Math.round(data.wall_time_s as number || 0)}s)`;
  } else if (event.event_type === "escalated") {
    summary = `\u2717 ESCALATED: ${data.rule_id} (${data.attempts} attempts, ${data.reason})`;
  } else if (event.event_type === "rule_selected") {
    summary = `\u25C9 SELECTED: ${data.rule_id} \u2014 ${data.title}`;
  } else if (event.event_type === "architect_reengaged") {
    const verdictColor = data.verdict === "ESCALATE" ? "#EF4444" : data.verdict === "PIVOT" ? "#F59E0B" : "#22C55E";
    summary = `\u26A1 ARCHITECT VERDICT: ${data.verdict} (trigger: ${data.trigger})`;
    detail = data.full_response as string || "";
    // Custom colored rendering
    return (
      <div
        className="border-b border-[#1C1F26] hover:bg-[#1A1D24] cursor-pointer"
        style={{ borderLeft: `2px solid ${verdictColor}` }}
        onClick={onToggle}
      >
        <div className="flex gap-2.5 py-1.5 px-3 text-[12px] font-mono leading-relaxed">
          <span className="text-[#4B5563] shrink-0 w-14">{event.elapsed_s.toFixed(0)}s</span>
          <span className="shrink-0 w-16 font-semibold uppercase text-[10px]" style={{ color: "#3B82F6" }}>
            {event.agent}
          </span>
          <span className="truncate flex-1" style={{ color: verdictColor }}>{summary}</span>
        </div>
        {expanded && detail && (
          <div className="px-3 pb-2 ml-32">
            <pre className="text-[10px] text-[#6B7280] whitespace-pre-wrap leading-relaxed max-h-48 overflow-y-auto bg-[#0B0D11] p-2 rounded-sm border border-[#1C1F26]">
              {detail}
            </pre>
          </div>
        )}
      </div>
    );
  } else {
    summary = JSON.stringify(data).slice(0, 120);
  }

  const isRevert = event.event_type === "revert";
  const isReflection = event.event_type === "reflection";
  const isError = event.event_type === "error";

  const bgClass = isRevert ? "bg-[#1a0808]"
    : isReflection ? "bg-[#110a1a]"
    : isError ? "bg-[#1a1008]"
    : "hover:bg-[#1A1D24]";

  return (
    <div
      className={`border-b border-[#1C1F26] ${bgClass} cursor-pointer`}
      style={{ borderLeft: `2px solid ${leftBorderColor(event)}` }}
      onClick={onToggle}
    >
      <div className="flex gap-2.5 py-1.5 px-3 text-[12px] font-mono leading-relaxed">
        <span className="text-[#4B5563] shrink-0 w-14">{event.elapsed_s.toFixed(0)}s</span>
        <span className="shrink-0 w-16 font-semibold uppercase text-[10px]" style={{ color }}>
          {event.agent}
        </span>
        <span className="text-[#9CA3AF] truncate flex-1">{summary}</span>
      </div>
      {expanded && detail && (
        <div className="px-3 pb-2 ml-32">
          <pre className="text-[10px] text-[#6B7280] whitespace-pre-wrap leading-relaxed max-h-48 overflow-y-auto bg-[#0B0D11] p-2 rounded-sm border border-[#1C1F26]">
            {detail}
          </pre>
        </div>
      )}
    </div>
  );
}

// -- Main EventLog --

export default function EventLog({ events }: { events: RunEvent[] }) {
  const bottomRef = useRef<HTMLDivElement>(null);
  const [expandedIdx, setExpandedIdx] = useState<number | null>(null);
  const [autoScroll, setAutoScroll] = useState(true);
  const [filter, setFilter] = useState<FilterKey>("all");
  const [search, setSearch] = useState("");

  useEffect(() => {
    if (autoScroll) {
      bottomRef.current?.scrollIntoView({ behavior: "smooth" });
    }
  }, [events.length, autoScroll]);

  // Filter events
  const filtered = events.filter(e => {
    if (!matchesFilter(e, filter)) return false;
    if (search) {
      const text = JSON.stringify(e.data).toLowerCase();
      if (!text.includes(search.toLowerCase())) return false;
    }
    return true;
  });

  // Count events per filter tab
  const counts: Record<string, number> = {};
  for (const tab of FILTER_TABS) {
    counts[tab.key] = events.filter(e => matchesFilter(e, tab.key)).length;
  }

  return (
    <div className="flex flex-col h-full overflow-hidden">
      {/* Filter bar */}
      <div className="px-3 py-1 border-b border-[#1C1F26] bg-[#0D0F14] flex items-center gap-1 shrink-0">
        {FILTER_TABS.map(tab => (
          <button
            key={tab.key}
            onClick={() => setFilter(tab.key)}
            className={`px-2 py-0.5 text-[9px] font-mono rounded-sm transition-colors ${
              filter === tab.key
                ? "bg-[#1A1D24] text-[#E8EAED]"
                : "text-[#6B7280] hover:text-[#9CA3AF]"
            }`}
          >
            {tab.label}
            {counts[tab.key] > 0 && (
              <span className="ml-1 text-[#4B5563]">{counts[tab.key]}</span>
            )}
          </button>
        ))}
        <div className="flex-1" />
        <input
          type="text"
          value={search}
          onChange={e => setSearch(e.target.value)}
          placeholder="Search\u2026"
          className="w-32 px-2 py-0.5 text-[10px] font-mono bg-[#12141A] border border-[#1C1F26] rounded-sm text-[#9CA3AF] outline-none focus:border-[#3B82F6] placeholder:text-[#4B5563]"
        />
      </div>

      {/* Event list */}
      <div className="flex-1 overflow-y-auto" onWheel={() => setAutoScroll(false)}>
        <div className="py-0.5">
          {filtered.length === 0 ? (
            <div className="text-center text-[#4B5563] py-4 text-[11px] font-mono">
              {events.length === 0 ? "Waiting for events\u2026" : "No matching events"}
            </div>
          ) : (
            filtered.map((event, i) => (
              <LogEntry
                key={`${event.elapsed_s}-${event.event_type}-${i}`}
                event={event}
                expanded={expandedIdx === i}
                onToggle={() => setExpandedIdx(expandedIdx === i ? null : i)}
              />
            ))
          )}
          <div ref={bottomRef} />
        </div>
        {!autoScroll && events.length > 0 && (
          <button
            onClick={() => { setAutoScroll(true); bottomRef.current?.scrollIntoView({ behavior: "smooth" }); }}
            className="fixed bottom-12 left-1/2 -translate-x-1/2 px-3 py-1 bg-[#3B82F6] text-white text-[11px] font-mono rounded-sm z-10"
          >
            {"\u2193"} Resume auto-scroll
          </button>
        )}
      </div>
    </div>
  );
}

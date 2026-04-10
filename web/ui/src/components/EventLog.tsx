"use client";

import { useRef, useEffect, useState } from "react";
import { RunEvent, AGENT_COLORS } from "./types";

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
    summary = `→ ${data.tool}(${JSON.stringify(data.args || {}).slice(0, 80)})`;
    detail = `Tool: ${data.tool}\nArguments:\n${JSON.stringify(data.args || {}, null, 2)}`;
  } else if (event.event_type === "tool_result") {
    const result = (data.result as string) || JSON.stringify(data.result || "");
    summary = `← ${result.slice(0, 120)}`;
    detail = result;
  } else if (event.event_type === "iteration_start") {
    summary = `━━ Iteration ${data.iteration} | fixed:${data.remediated} reverted:${data.reverted} remaining:${data.failing} ━━`;
  } else if (event.event_type === "skip") {
    summary = `⊘ SKIP: ${data.rule_id} — ${data.reason}`;
  } else if (event.event_type === "revert") {
    summary = `✗ REVERT: ${(data.reason as string || "").slice(0, 120)}`;
    detail = data.reason as string;
  } else if (event.event_type === "reflection") {
    summary = `◆ REFLECTION: ${(data.text as string || "").slice(0, 120)}`;
    detail = data.text as string;
  } else if (event.event_type === "error") {
    summary = `⚠ ERROR: ${(data.error as string || "").slice(0, 120)}`;
    detail = data.error as string;
  } else {
    summary = JSON.stringify(data).slice(0, 120);
  }

  const isIteration = event.event_type === "iteration_start";
  const isRevert = event.event_type === "revert";
  const isReflection = event.event_type === "reflection";
  const isError = event.event_type === "error";

  const bgClass = isIteration ? "bg-[#0D0F14] border-[#2A2E38]"
    : isRevert ? "bg-[#1a0808] border-[#3a1515]"
    : isReflection ? "bg-[#110a1a] border-[#2a1540]"
    : isError ? "bg-[#1a1008] border-[#3a2a15]"
    : "hover:bg-[#1A1D24] border-[#1C1F26]";

  return (
    <div className={`border-b ${bgClass} cursor-pointer`} onClick={onToggle}>
      <div className="flex gap-2 py-1 px-3 text-[11px] font-mono">
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

export default function EventLog({ events }: { events: RunEvent[] }) {
  const bottomRef = useRef<HTMLDivElement>(null);
  const [expandedIdx, setExpandedIdx] = useState<number | null>(null);
  const [autoScroll, setAutoScroll] = useState(true);

  useEffect(() => {
    if (autoScroll) {
      bottomRef.current?.scrollIntoView({ behavior: "smooth" });
    }
  }, [events.length, autoScroll]);

  return (
    <div className="flex-1 overflow-y-auto" onWheel={() => setAutoScroll(false)}>
      <div className="py-0.5">
        {events.length === 0 ? (
          <div className="text-center text-[#4B5563] py-8 text-sm">Waiting for events...</div>
        ) : (
          events.map((event, i) => (
            <LogEntry
              key={i}
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
          className="fixed bottom-4 left-1/2 -translate-x-1/2 px-3 py-1 bg-[#3B82F6] text-white text-[11px] font-mono rounded-sm z-10"
        >
          ↓ Resume auto-scroll
        </button>
      )}
    </div>
  );
}

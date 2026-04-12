"use client";

import { RunEvent, AGENT_COLORS, AGENT_MODELS, AGENT_GPUS } from "./types";

export default function ActiveAgent({ events }: { events: RunEvent[] }) {
  // Find the most recent agent_response with timing data
  const agentResponses = events.filter(e => e.event_type === "agent_response" && e.data.timing);
  const latest = agentResponses[agentResponses.length - 1];
  
  // Find the most recent tool calls for this agent
  const latestAgent = latest?.agent || "system";
  const recentTools = events
    .filter(e => (e.event_type === "tool_call" || e.event_type === "tool_result") && e.agent === latestAgent)
    .slice(-4);

  const color = AGENT_COLORS[latestAgent] || "#6B7280";
  const modelInfo = AGENT_MODELS[latestAgent] || "Unknown";
  const gpuInfo = AGENT_GPUS[latestAgent] || "";
  
  const timing = latest?.data?.timing as Record<string, number> | undefined;
  const tokens = latest?.data?.tokens as Record<string, number> | undefined;
  const text = (latest?.data?.text as string) || "";

  // Calculate running averages per agent type  
  const gemmaResponses = agentResponses.filter(e => e.agent === "architect" || e.agent === "worker");
  const nemotronResponses = agentResponses.filter(e => e.agent === "auditor");
  
  const avgGemmaTps = gemmaResponses.length > 0
    ? (gemmaResponses.reduce((s, e) => s + ((e.data.timing as any)?.tok_per_sec || 0), 0) / gemmaResponses.length).toFixed(1)
    : "—";
  const avgNemotronTps = nemotronResponses.length > 0
    ? (nemotronResponses.reduce((s, e) => s + ((e.data.timing as any)?.tok_per_sec || 0), 0) / nemotronResponses.length).toFixed(1)
    : "—";

  if (!latest) {
    return (
      <div className="bg-[#12141A] border border-[#1C1F26] rounded-sm p-6 text-center text-[#4B5563] text-sm">
        Waiting for first agent response...
      </div>
    );
  }

  return (
    <div className="bg-[#12141A] border rounded-sm overflow-hidden" style={{ borderColor: `${color}33` }}>
      {/* Agent header bar */}
      <div className="flex items-center justify-between px-4 py-2 border-b" style={{ borderColor: `${color}22`, background: `${color}08` }}>
        <div className="flex items-center gap-3">
          <div className="w-2.5 h-2.5 rounded-full animate-pulse" style={{ background: color }} />
          <span className="text-sm font-bold uppercase tracking-wider" style={{ color }}>{latestAgent}</span>
          <span className="text-[10px] font-mono text-[#6B7280]">{modelInfo} · {gpuInfo}</span>
        </div>
        <span className="text-[10px] font-mono text-[#4B5563]">Iteration {latest.iteration}</span>
      </div>

      <div className="flex">
        {/* Left: agent output + tool calls */}
        <div className="flex-1 p-4 border-r border-[#1C1F26]">
          {/* Tool calls */}
          {recentTools.length > 0 && (
            <div className="mb-3 space-y-1">
              {recentTools.map((t, i) => (
                <div key={i} className="flex items-start gap-2 text-[11px] font-mono">
                  <span className="text-[#4B5563] shrink-0">{t.event_type === "tool_call" ? "→" : "←"}</span>
                  <span className={t.event_type === "tool_call" ? "text-[#F59E0B]" : "text-[#6B7280]"}>
                    {t.event_type === "tool_call"
                      ? `${t.data.tool}(${JSON.stringify(t.data.args || {}).slice(0, 60)})`
                      : `${((t.data.result as string) || "").slice(0, 120)}`}
                  </span>
                </div>
              ))}
            </div>
          )}
          {/* Agent text */}
          <div className="text-[12px] font-mono text-[#9CA3AF] leading-relaxed max-h-24 overflow-hidden">
            {text.slice(0, 400)}
          </div>
        </div>

        {/* Right: metrics panel */}
        <div className="w-64 shrink-0 p-4 space-y-3">
          {/* Current agent metrics */}
          <div className="grid grid-cols-2 gap-2">
            <div className="bg-[#0D0F14] rounded-sm p-2 text-center">
              <div className="font-mono text-xl font-bold" style={{ color }}>{timing?.tok_per_sec || "—"}</div>
              <div className="text-[9px] text-[#6B7280] uppercase">tok/s</div>
            </div>
            <div className="bg-[#0D0F14] rounded-sm p-2 text-center">
              <div className="font-mono text-xl font-bold text-[#9CA3AF]">{timing?.ttft_s?.toFixed(1) || "—"}</div>
              <div className="text-[9px] text-[#6B7280] uppercase">TTFT (s)</div>
            </div>
            <div className="bg-[#0D0F14] rounded-sm p-2 text-center">
              <div className="font-mono text-lg font-bold text-[#6B7280]">{tokens?.prompt || "—"}</div>
              <div className="text-[9px] text-[#6B7280] uppercase">Input tok</div>
            </div>
            <div className="bg-[#0D0F14] rounded-sm p-2 text-center">
              <div className="font-mono text-lg font-bold text-[#6B7280]">{tokens?.completion || "—"}</div>
              <div className="text-[9px] text-[#6B7280] uppercase">Output tok</div>
            </div>
          </div>
          
          {/* Run averages */}
          <div className="border-t border-[#1C1F26] pt-2">
            <div className="text-[9px] text-[#4B5563] uppercase tracking-wider mb-1.5">Run Average (bf16 TP=4)</div>
            <div className="flex gap-2">
              <div className="flex-1 bg-[#0D0F14] rounded-sm p-1.5 text-center">
                <div className="text-[10px] font-mono font-bold text-[#3B82F6]">{avgGemmaTps}</div>
                <div className="text-[8px] text-[#4B5563]">avg tok/s</div>
              </div>
              <div className="flex-1 bg-[#0D0F14] rounded-sm p-1.5 text-center">
                <div className="text-[10px] font-mono font-bold text-[#9CA3AF]">{agentResponses.length}</div>
                <div className="text-[8px] text-[#4B5563]">LLM calls</div>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

"use client";

import { AGENT_COLORS } from "./types";

function PipelineNode({ role, active, gpuLabel }: { role: string; active: boolean; gpuLabel: string }) {
  const color = AGENT_COLORS[role] || "#6B7280";
  return (
    <div className="flex flex-col items-center gap-1">
      <div
        className={`w-14 h-14 rounded-sm border-2 flex flex-col items-center justify-center transition-all duration-300 ${active ? "animate-pulse" : ""}`}
        style={{
          borderColor: active ? color : "#2A2E38",
          background: active ? `color-mix(in srgb, ${color} 10%, transparent)` : "#12141A",
          color: active ? color : "#6B7280",
        }}
      >
        <span className="text-[10px] font-bold uppercase">{role.slice(0, 5)}</span>
        <span className="text-[7px] text-[#4B5563]">{gpuLabel}</span>
      </div>
      <div className="w-2 h-2 rounded-full" style={{ background: active ? color : "#2A2E38" }} />
    </div>
  );
}

function Arrow({ revert }: { revert?: boolean }) {
  return (
    <div className={`text-lg ${revert ? "text-[#EF4444]" : "text-[#2A2E38]"}`}>
      {revert ? "↩" : "→"}
    </div>
  );
}

export default function Pipeline({ activeAgent, hasRevert }: { activeAgent: string; hasRevert: boolean }) {
  return (
    <div className="flex items-center justify-center gap-4 py-3">
      <PipelineNode role="architect" active={activeAgent === "architect"} gpuLabel="0+1" />
      <Arrow />
      <PipelineNode role="worker" active={activeAgent === "worker"} gpuLabel="0+1" />
      <Arrow />
      <PipelineNode role="auditor" active={activeAgent === "auditor"} gpuLabel="2+3" />
      {hasRevert ? <Arrow revert /> : <Arrow />}
      <PipelineNode role="reflector" active={activeAgent === "reflector"} gpuLabel="0+1" />
    </div>
  );
}

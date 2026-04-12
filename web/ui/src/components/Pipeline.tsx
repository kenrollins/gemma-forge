"use client";

import { AGENT_COLORS } from "./types";

function PipelineNode({ role, label, active, subtitle, isHarness }: {
  role: string; label: string; active: boolean; subtitle: string; isHarness?: boolean;
}) {
  const color = isHarness ? "#6B7280" : (AGENT_COLORS[role] || "#6B7280");
  return (
    <div className="flex flex-col items-center gap-1">
      <div
        className={`w-20 h-12 rounded border-2 flex flex-col items-center justify-center transition-all duration-300 ${active ? "shadow-lg" : ""}`}
        style={{
          borderColor: active ? color : "#2A2E38",
          background: active ? `color-mix(in srgb, ${color} 12%, #12141A)` : "#12141A",
          color: active ? color : "#6B7280",
          boxShadow: active ? `0 0 12px ${color}40` : "none",
        }}
      >
        <span className="text-[10px] font-bold uppercase tracking-wide">{label}</span>
        <span className="text-[7px] text-[#4B5563]">{subtitle}</span>
      </div>
      {active && (
        <div className="w-1.5 h-1.5 rounded-full animate-pulse" style={{ background: color }} />
      )}
    </div>
  );
}

function Arrow({ label, active }: { label?: string; active?: boolean }) {
  return (
    <div className="flex flex-col items-center px-1">
      <div className={`text-sm ${active ? "text-[#9CA3AF]" : "text-[#2A2E38]"}`}>{"\u2192"}</div>
      {label && <span className="text-[7px] text-[#4B5563]">{label}</span>}
    </div>
  );
}

function RetryArrow() {
  return (
    <div className="flex items-center gap-1 ml-2">
      <div className="text-sm text-[#EF4444]">{"\u21A9"}</div>
      <span className="text-[8px] text-[#EF4444] font-bold uppercase">retry</span>
    </div>
  );
}

export default function Pipeline({ activeAgent, hasRevert }: { activeAgent: string; hasRevert: boolean }) {
  // Eval node lights up for harness/system, but NOT for "none" (empty/idle state)
  const evalActive = activeAgent === "harness" || activeAgent === "system";
  const anyActive = activeAgent !== "none";

  return (
    <div className="flex items-center justify-center gap-1.5 py-2.5">
      <PipelineNode role="architect" label="Architect" active={activeAgent === "architect"} subtitle="Gemma 31B" />
      <Arrow active={activeAgent === "worker"} />
      <PipelineNode role="worker" label="Worker" active={activeAgent === "worker"} subtitle="Gemma 31B" />
      <Arrow active={evalActive} />
      <PipelineNode role="harness" label="Eval" active={evalActive && anyActive} subtitle="Python" isHarness />
      <Arrow label="fail?" active={activeAgent === "reflector"} />
      <PipelineNode role="reflector" label="Reflector" active={activeAgent === "reflector"} subtitle="Gemma 31B" />
      {hasRevert && <RetryArrow />}
    </div>
  );
}

"use client";

/**
 * AgentFlow — the visual centerpiece of the Mission view.
 *
 * Shows the four pipeline stages (Architect → Worker → Eval → Reflector)
 * as horizontally-arranged cards with arrows. The active stage gets a
 * pulsing accent ring and a brighter title; past stages within the
 * current attempt fade to a dim "completed" treatment; future stages
 * are outline-only.
 *
 * At 100x replay the eye lands on whichever stage is glowing — the
 * pulse and color rotation through Architect → Worker → Eval →
 * Reflector becomes the heartbeat of the screen.
 *
 * Designed to be sturdy at high-speed replay: every metric is
 * fixed-width and uses tabular-nums, no text reflow between frames.
 */

import { AGENT_COLORS } from "./types";

export type Stage = "architect" | "worker" | "eval" | "reflector" | "idle";

export interface AgentFlowProps {
  stage: Stage;
  /** When the stage is "eval", whether the most recent eval passed. */
  evalPassed?: boolean;
  /** Optional one-line description shown beneath the stages. Color-coded. */
  narrative?: { text: string; color?: string };
}

interface StageMeta {
  key: Exclude<Stage, "idle">;
  label: string;
  /** Short verb-form sub-label shown in active state. */
  activeLabel: string;
  /** Sub-label when this stage is the most recently completed one. */
  completedLabel: string;
}

const STAGES: StageMeta[] = [
  { key: "architect", label: "Architect", activeLabel: "planning", completedLabel: "selected" },
  { key: "worker", label: "Worker", activeLabel: "applying fix", completedLabel: "applied" },
  { key: "eval", label: "Eval", activeLabel: "scanning", completedLabel: "checked" },
  { key: "reflector", label: "Reflector", activeLabel: "analyzing", completedLabel: "noted" },
];

function stageColor(s: Stage, evalPassed?: boolean): string {
  if (s === "eval") return evalPassed === false ? "#EF4444" : "#22C55E";
  if (s === "architect") return AGENT_COLORS.architect;
  if (s === "worker") return AGENT_COLORS.worker;
  if (s === "reflector") return AGENT_COLORS.reflector;
  return "#6B7280";
}

export default function AgentFlow({ stage, evalPassed, narrative }: AgentFlowProps) {
  // Index of the active stage (or -1 for idle). Used to determine which
  // stages render as completed vs upcoming.
  const activeIdx = STAGES.findIndex((s) => s.key === stage);

  return (
    <div className="flex flex-col gap-2">
      <div className="flex items-stretch gap-1.5">
        {STAGES.map((s, idx) => {
          const isActive = idx === activeIdx;
          const isPast = activeIdx >= 0 && idx < activeIdx;
          const color = stageColor(s.key, evalPassed);
          return (
            <div key={s.key} className="flex items-stretch flex-1">
              <StageCard
                meta={s}
                color={color}
                state={isActive ? "active" : isPast ? "past" : "upcoming"}
                evalPassed={s.key === "eval" ? evalPassed : undefined}
              />
              {idx < STAGES.length - 1 && (
                <FlowArrow
                  active={isActive}
                  past={isPast}
                  color={color}
                />
              )}
            </div>
          );
        })}
      </div>

      {/* Narrative line — the prose summary of what's happening RIGHT
          NOW. Color reflects the current stage so the eye picks up
          state at a glance even before reading the words. */}
      <div className="h-5 flex items-center px-1">
        {narrative ? (
          <span
            className="text-[12px] font-mono leading-tight truncate"
            style={{ color: narrative.color || "#9CA3AF" }}
          >
            {narrative.text}
          </span>
        ) : (
          <span className="text-[12px] font-mono text-[#3F4451] italic">
            standing by\u2026
          </span>
        )}
      </div>
    </div>
  );
}

// =====================================================================

function StageCard({
  meta,
  color,
  state,
  evalPassed,
}: {
  meta: StageMeta;
  color: string;
  state: "active" | "past" | "upcoming";
  evalPassed?: boolean;
}) {
  const active = state === "active";
  const past = state === "past";

  // Background and border treatment per state
  const bg = active
    ? `linear-gradient(180deg, ${color}24 0%, ${color}10 100%)`
    : past
    ? `${color}08`
    : "#0D0F14";
  const borderColor = active ? color : past ? `${color}40` : "#23262E";
  const titleColor = active ? color : past ? `${color}99` : "#4B5563";
  const subColor = active ? "#E8EAED" : past ? "#6B7280" : "#3F4451";
  const subText = active
    ? meta.activeLabel
    : past
    ? meta.key === "eval" && evalPassed === false
      ? "failed"
      : meta.completedLabel
    : "—";

  return (
    <div
      className="flex-1 px-3 py-2 rounded-md border transition-all duration-300 relative overflow-hidden"
      style={{
        background: bg,
        borderColor,
        boxShadow: active ? `0 0 14px ${color}25, inset 0 0 0 1px ${color}40` : undefined,
      }}
    >
      {/* Inner pulsing accent for the active stage */}
      {active && (
        <div
          className="absolute inset-0 rounded-md pointer-events-none"
          style={{
            background: `radial-gradient(circle at 50% 100%, ${color}28 0%, transparent 70%)`,
            animation: "agentFlowPulse 1.6s ease-in-out infinite",
          }}
        />
      )}

      <div className="relative flex items-center gap-2">
        {/* Status dot */}
        <div className="flex items-center gap-0.5">
          {active ? (
            <ActiveDots color={color} />
          ) : past ? (
            <span
              className="text-[11px] font-bold leading-none"
              style={{ color: meta.key === "eval" && evalPassed === false ? "#EF4444" : color }}
            >
              ●
            </span>
          ) : (
            <span
              className="block w-1.5 h-1.5 rounded-full"
              style={{ background: "#23262E" }}
            />
          )}
        </div>

        {/* Stage title */}
        <span
          className="text-[10px] font-bold uppercase tracking-[0.18em] truncate"
          style={{ color: titleColor }}
        >
          {meta.label}
        </span>
      </div>

      {/* Sub-label (verb form) — fixed line height to prevent reflow at 100x */}
      <div
        className="relative text-[10px] font-mono mt-1 truncate leading-none h-3"
        style={{ color: subColor }}
      >
        {subText}
      </div>

      <style jsx>{`
        @keyframes agentFlowPulse {
          0%, 100% { opacity: 1; }
          50% { opacity: 0.45; }
        }
      `}</style>
    </div>
  );
}

function ActiveDots({ color }: { color: string }) {
  return (
    <span className="flex items-center gap-0.5" aria-hidden>
      {[0, 1, 2].map((i) => (
        <span
          key={i}
          className="block w-1 h-1 rounded-full"
          style={{
            background: color,
            boxShadow: `0 0 4px ${color}`,
            animation: `agentFlowDot 1.2s ease-in-out ${i * 0.18}s infinite`,
          }}
        />
      ))}
      <style jsx>{`
        @keyframes agentFlowDot {
          0%, 100% { transform: scale(0.6); opacity: 0.4; }
          50% { transform: scale(1.1); opacity: 1; }
        }
      `}</style>
    </span>
  );
}

function FlowArrow({ active, past, color }: { active: boolean; past: boolean; color: string }) {
  // Arrow connector between cards. When the leftward stage is the
  // active one, the arrow shows motion (animated dashes flowing right).
  // Past arrows are statically lit. Upcoming arrows are dim.
  const lit = active || past;
  return (
    <div className="w-6 flex items-center justify-center self-stretch px-0.5">
      <div className="relative w-full h-px overflow-hidden">
        <div
          className="absolute inset-0"
          style={{
            background: lit
              ? `linear-gradient(90deg, ${color}80, ${color}40)`
              : "#1A1D24",
          }}
        />
        {active && (
          <div
            className="absolute top-0 left-0 right-0 h-px"
            style={{
              background: `linear-gradient(90deg, transparent 30%, ${color} 50%, transparent 70%)`,
              animation: "agentFlowArrow 1.6s linear infinite",
            }}
          />
        )}
      </div>
      <style jsx>{`
        @keyframes agentFlowArrow {
          0% { transform: translateX(-100%); }
          100% { transform: translateX(100%); }
        }
      `}</style>
    </div>
  );
}

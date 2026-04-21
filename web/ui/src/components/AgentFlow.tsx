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

/** Per-agent detail block shown inside each card. Lets the boxes carry
 *  real information density instead of being mostly empty label slots. */
export interface StageDetail {
  /** Freshest concrete signal — rule title, tool name, verdict word, etc. */
  headline?: string;
  /** Secondary line, usually a running counter or context. */
  sub?: string;
  /** Optional per-rule token spend for this agent. When provided,
   *  the active card grows a third line showing prompt+completion
   *  tokens (and a live tok/s when available) — the raw-intelligence
   *  signal that makes "how hard are we thinking on this rule"
   *  immediately felt. */
  tokens?: { prompt: number; completion: number; tokPerSec?: number };
}

export type StageDetails = Partial<Record<Exclude<Stage, "idle">, StageDetail>>;

export interface AgentFlowProps {
  stage: Stage;
  /** When the stage is "eval", whether the most recent eval passed. */
  evalPassed?: boolean;
  /** Optional one-line description shown beneath the stages. Color-coded. */
  narrative?: { text: string; color?: string };
  /** Per-agent detail lines to give each card real content. */
  details?: StageDetails;
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
  // `activeLabel` intentionally says "scanning" because the eval is
  // in-flight when the card is active with evalPassed=undefined.
  // When the verdict lands, the stage is no longer "active" — the
  // pipeline has moved on to reflector (fail) or remediated (pass).
  { key: "eval", label: "Eval", activeLabel: "scanning", completedLabel: "checked" },
  { key: "reflector", label: "Reflector", activeLabel: "analyzing", completedLabel: "noted" },
];

// Eval color depends on whether we know the outcome yet:
//   undefined → neutral light gray (scan in progress, no verdict)
//   true      → green (passed)
//   false     → red (failed)
// Deliberately NOT cyan for the pending state — cyan is used
// elsewhere for accent color (tok/s, live hydration) and the user
// read it as a status signal, which is confusing. Neutral gray
// reads correctly as "still working, outcome unknown."
function stageColor(s: Stage, evalPassed?: boolean): string {
  if (s === "eval") {
    if (evalPassed === true) return "#22C55E";
    if (evalPassed === false) return "#EF4444";
    return "#9CA3AF"; // neutral gray — "evaluating, verdict pending"
  }
  if (s === "architect") return AGENT_COLORS.architect;
  if (s === "worker") return AGENT_COLORS.worker;
  if (s === "reflector") return AGENT_COLORS.reflector;
  return "#6B7280";
}

// Compact token counts for the active-card's token line. The cards
// are narrow (~220px) so "12,345" wastes horizontal space and the
// viewer doesn't care about the last three digits on big numbers.
function formatTokens(n: number): string {
  if (n < 1000) return n.toString();
  if (n < 10000) return (n / 1000).toFixed(1) + "k";
  return Math.round(n / 1000) + "k";
}

export default function AgentFlow({ stage, evalPassed, narrative, details }: AgentFlowProps) {
  // Index of the active stage (or -1 for idle). Used to determine which
  // stages render as completed vs upcoming.
  const activeIdx = STAGES.findIndex((s) => s.key === stage);

  return (
    // max-w cap stops the four cards from stretching edge-to-edge on
    // wide monitors. Without this, each card was >400px wide on a 4K
    // display and read as a row of mostly-empty boxes. Centered so the
    // flow still lines up under the rule headline above.
    <div className="flex flex-col gap-2 max-w-[1280px] mx-auto w-full">
      {/* items-stretch so all four cards share the same height. Detail
          blocks are rendered on inactive cards too (with invisible
          content) which keeps heights identical frame-to-frame —
          the row no longer shifts every time the active stage cycles
          at 100x/1000x replay. */}
      <div className="flex items-stretch gap-1.5">
        {STAGES.map((s, idx) => {
          const isActive = idx === activeIdx;
          const isPast = activeIdx >= 0 && idx < activeIdx;
          const color = stageColor(s.key, evalPassed);
          return (
            <div key={s.key} className="flex items-stretch flex-1 min-w-0">
              <StageCard
                meta={s}
                color={color}
                state={isActive ? "active" : isPast ? "past" : "upcoming"}
                evalPassed={s.key === "eval" ? evalPassed : undefined}
                detail={details?.[s.key]}
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
  detail,
}: {
  meta: StageMeta;
  color: string;
  state: "active" | "past" | "upcoming";
  evalPassed?: boolean;
  detail?: StageDetail;
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
  // Eval's active sub-label depends on the verdict state:
  //   undefined → "scanning" (verdict in-flight)
  //   true      → "passed"
  //   false     → "failed"
  // Everything else uses the canned active/past labels.
  let subText: string;
  if (active) {
    if (meta.key === "eval") {
      subText =
        evalPassed === true ? "passed" : evalPassed === false ? "failed" : "scanning";
    } else {
      subText = meta.activeLabel;
    }
  } else if (past) {
    subText =
      meta.key === "eval" && evalPassed === false ? "failed" : meta.completedLabel;
  } else {
    subText = "—";
  }

  return (
    <div
      className="flex-1 min-w-0 px-4 py-2.5 rounded-md border transition-all duration-300 relative overflow-hidden"
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

      <div className="relative flex items-center gap-2.5">
        {/* Status dot */}
        <div className="flex items-center gap-0.5">
          {active ? (
            <ActiveDots color={color} />
          ) : past ? (
            <span
              className="text-[12px] font-bold leading-none"
              style={{ color: meta.key === "eval" && evalPassed === false ? "#EF4444" : color }}
            >
              ●
            </span>
          ) : (
            <span
              className="block w-2 h-2 rounded-full"
              style={{ background: "#23262E" }}
            />
          )}
        </div>

        {/* Stage title */}
        <span
          className="text-[12px] font-bold uppercase tracking-[0.2em] truncate"
          style={{ color: titleColor }}
        >
          {meta.label}
        </span>
      </div>

      {/* Sub-label (verb form) — fixed line height to prevent reflow at 100x */}
      <div
        className="relative text-[11px] font-mono mt-1 truncate leading-none h-3"
        style={{ color: subColor }}
      >
        {subText}
      </div>

      {/* Detail block. Rendered on ALL cards (not just active) so the
          row's total height is constant — the active card fills it
          with headline/sub/tokens while inactive cards render an
          invisible placeholder of the same size. Without this, the
          row height oscillated every few frames at 100x/1000x as the
          active stage cycled, shaking everything below it. */}
      <div
        className="relative mt-2 pt-2 border-t"
        style={{ borderColor: active ? `${color}30` : "transparent" }}
      >
        <div
          className="text-[10.5px] font-mono truncate leading-tight h-[13px] text-[#E8EAED]"
          title={active ? detail?.headline || "" : ""}
          style={{ visibility: active ? "visible" : "hidden" }}
        >
          {active ? (detail?.headline || "\u2014") : "\u00a0"}
        </div>
        <div
          className="text-[9px] font-mono uppercase tracking-wider truncate leading-tight h-[12px] mt-0.5"
          style={{
            color: active ? `${color}CC` : "transparent",
            visibility: active ? "visible" : "hidden",
          }}
          title={active ? detail?.sub || "" : ""}
        >
          {active ? (detail?.sub || "\u00a0") : "\u00a0"}
        </div>
        {/* Tokens line — always-reserved height so the active card
            itself doesn't grow/shrink when tokens go from 0 → n. */}
        <div
          className="text-[9px] font-mono tabular-nums truncate leading-tight h-[12px] mt-0.5 text-[#9CA3AF]"
          style={{ visibility: active && detail?.tokens && (detail.tokens.prompt > 0 || detail.tokens.completion > 0) ? "visible" : "hidden" }}
          title={active && detail?.tokens ? `input (prompt) ${detail.tokens.prompt.toLocaleString()} tokens / output (completion) ${detail.tokens.completion.toLocaleString()} tokens${detail.tokens.tokPerSec ? ` @ ${detail.tokens.tokPerSec.toFixed(1)} tok/s` : ""}` : ""}
        >
          {active && detail?.tokens ? (
            <>
              <span style={{ color: "#60A5FA" }}>{formatTokens(detail.tokens.prompt)}</span>
              <span className="text-[#6B7280] ml-0.5">in</span>
              <span className="text-[#4B5563] mx-1.5">+</span>
              <span style={{ color: "#22D3EE" }}>{formatTokens(detail.tokens.completion)}</span>
              <span className="text-[#6B7280] ml-0.5">out</span>
              {detail.tokens.tokPerSec !== undefined && detail.tokens.tokPerSec > 0 && (
                <>
                  <span className="text-[#4B5563] mx-1.5">{"\u00b7"}</span>
                  <span style={{ color: "#22D3EE" }}>{detail.tokens.tokPerSec.toFixed(1)} tok/s</span>
                </>
              )}
            </>
          ) : (
            "\u00a0"
          )}
        </div>
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

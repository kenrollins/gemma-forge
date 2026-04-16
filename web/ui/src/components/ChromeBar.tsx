"use client";

/**
 * ChromeBar — single-row chrome combining mode toggle, speed control,
 * and tab navigation.
 *
 * Designed for presentation: each control group has its own visual
 * envelope (background + subtle border) so the eye reads them as
 * distinct widgets rather than a single dense strip. Mode is always
 * color-coded (green=live, blue=replay) including the bar's own
 * bottom border so peripheral vision tells which mode we're in.
 * Speed is always visible; grayed when in live mode (you can't speed
 * up real time). Tabs use a clear "selected pill" treatment so it's
 * obvious they're clickable.
 *
 * Note for future maintainers: JSX attribute strings do NOT process
 * \u escape sequences. Use either a JS expression (dotChar={"\u25CF"})
 * or the literal character. We use the literal characters here.
 */

import type { Tab } from "./types";

export type ChromeMode = "live" | "replay";

export const SPEEDS = [1, 5, 20, 100] as const;
export type ReplaySpeed = typeof SPEEDS[number];

export interface ChromeBarProps {
  mode: ChromeMode;
  setMode: (m: ChromeMode) => void;
  liveAvailable: boolean;
  replaySpeed: ReplaySpeed;
  setReplaySpeed: (s: ReplaySpeed) => void;
  connected: boolean;
  activeTab: Tab;
  setActiveTab: (t: Tab) => void;
  // Optional human-readable name of what's currently being replayed,
  // shown next to the speed control (e.g. "20260414 · 13.5h").
  replayLabel?: string;
}

const MODE = {
  live: { fg: "#22C55E", bg: "rgba(34, 197, 94, 0.15)", border: "rgba(34, 197, 94, 0.45)", soft: "rgba(34, 197, 94, 0.08)" },
  replay: { fg: "#3B82F6", bg: "rgba(59, 130, 246, 0.15)", border: "rgba(59, 130, 246, 0.45)", soft: "rgba(59, 130, 246, 0.08)" },
};

// Tab labels are stored as title-case for readability of the rendered
// text (no CSS-uppercase shenanigans that would mangle special chars).
const TAB_LABELS: Record<Tab, string> = {
  live: "Mission",
  memory: "Memory",
  runs: "Runs",
  events: "Events",
};

export default function ChromeBar({
  mode,
  setMode,
  liveAvailable,
  replaySpeed,
  setReplaySpeed,
  connected,
  activeTab,
  setActiveTab,
  replayLabel,
}: ChromeBarProps) {
  const speedActive = mode === "replay";
  const palette = MODE[mode];

  return (
    <div
      className="flex items-center gap-10 px-7 py-3 border-b bg-[#0A0C10]"
      style={{ borderColor: palette.border, transition: "border-color 200ms" }}
    >
      {/* === Mode toggle ============================================ */}
      <div className="flex items-center gap-2">
        <span
          className="text-[9px] font-semibold tracking-[0.2em] uppercase"
          style={{ color: "#4B5563" }}
        >
          Mode
        </span>
        <div className="flex items-stretch rounded-md overflow-hidden bg-[#0F1217] border border-[#2A2F38] shadow-inner">
          <ModeButton
            label="Live"
            dot="●"
            active={mode === "live"}
            disabled={!liveAvailable}
            color={MODE.live.fg}
            bg={MODE.live.bg}
            onClick={() => liveAvailable && setMode("live")}
            title={
              liveAvailable
                ? "Connect to the running harness"
                : "No live run is currently writing"
            }
          />
          <div className="w-px bg-[#23262E]" />
          <ModeButton
            label="Replay"
            dot="▶"
            active={mode === "replay"}
            disabled={false}
            color={MODE.replay.fg}
            bg={MODE.replay.bg}
            onClick={() => setMode("replay")}
            title="Replay a recorded run at accelerated speed"
          />
        </div>
      </div>

      {/* === Speed control ========================================= */}
      <div className="flex items-center gap-2">
        <span
          className="text-[9px] font-semibold tracking-[0.2em] uppercase"
          style={{ color: speedActive ? "#9CA3AF" : "#3F4451" }}
        >
          Speed
        </span>
        <div className="flex items-stretch rounded-md overflow-hidden bg-[#0F1217] border border-[#2A2F38] shadow-inner">
          {SPEEDS.map((s, idx) => (
            <SpeedButton
              key={s}
              speed={s}
              active={speedActive && replaySpeed === s}
              enabled={speedActive}
              onClick={() => speedActive && setReplaySpeed(s)}
              hasDivider={idx > 0}
            />
          ))}
        </div>
        {replayLabel && speedActive && (
          <span
            className="text-[10px] font-mono text-[#6B7280] ml-1 truncate max-w-[180px]"
            title={replayLabel}
          >
            {replayLabel}
          </span>
        )}
      </div>

      {/* === Tabs ==================================================
           Read as clearly clickable: each tab is a discrete pill with
           visible border + comfortable padding + hover affordance.
           Active tab gets a filled background + brighter text + a
           thin accent bar at the bottom edge so the choice reads
           even at low contrast or in peripheral vision. The whole
           group is pushed right with ml-auto and given its own
           breathing room from the connection indicator. */}
      <nav className="ml-auto flex items-center gap-3">
        <span
          className="text-[9px] font-semibold tracking-[0.2em] uppercase mr-2"
          style={{ color: "#4B5563" }}
        >
          View
        </span>
        {(["live", "memory", "runs", "events"] as Tab[]).map((t) => (
          <TabButton
            key={t}
            label={TAB_LABELS[t]}
            isActive={activeTab === t}
            onClick={() => setActiveTab(t)}
            accentColor={palette.fg}
          />
        ))}
      </nav>

      {/* === Connection indicator =================================== */}
      <div className="flex items-center gap-2.5 pl-6 ml-2 border-l border-[#1C1F26]">
        <div
          className="w-2 h-2 rounded-full"
          style={{
            background: connected ? palette.fg : "#3F4451",
            boxShadow: connected ? `0 0 8px ${palette.fg}` : undefined,
            animation: connected ? "chromebarPulse 2s ease-in-out infinite" : undefined,
          }}
        />
        <span
          className="text-[10px] font-semibold tracking-[0.18em] uppercase"
          style={{ color: connected ? palette.fg : "#3F4451", minWidth: "60px" }}
        >
          {connected ? (mode === "live" ? "Live" : "Replay") : "Idle"}
        </span>
      </div>

      <style jsx>{`
        @keyframes chromebarPulse {
          0%, 100% { opacity: 1; }
          50% { opacity: 0.55; }
        }
      `}</style>
    </div>
  );
}

function ModeButton({
  label,
  dot,
  active,
  disabled,
  color,
  bg,
  onClick,
  title,
}: {
  label: string;
  dot: string;
  active: boolean;
  disabled: boolean;
  color: string;
  bg: string;
  onClick: () => void;
  title: string;
}) {
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      title={title}
      className="flex items-center gap-2 px-4 py-1.5 text-[11px] font-bold transition-colors hover:bg-[#1A1E27]"
      style={{
        color: active ? color : disabled ? "#3F4451" : "#9CA3AF",
        background: active ? bg : "transparent",
        cursor: disabled ? "not-allowed" : "pointer",
      }}
    >
      <span style={{ fontSize: "12px", lineHeight: 1 }}>{dot}</span>
      {label}
    </button>
  );
}

/**
 * TabButton — a discrete clickable pill that reads as interactive
 * even at rest (visible border + opaque background) and lights up
 * with the active mode's accent color when selected.
 */
function TabButton({
  label,
  isActive,
  onClick,
  accentColor,
}: {
  label: string;
  isActive: boolean;
  onClick: () => void;
  accentColor: string;
}) {
  return (
    <button
      onClick={onClick}
      title={`Switch to ${label}`}
      className="relative px-4 py-1.5 text-[11px] font-semibold rounded-md border transition-all duration-150"
      style={{
        color: isActive ? "#E8EAED" : "#9CA3AF",
        background: isActive ? "#1A1E27" : "#0F1217",
        borderColor: isActive ? accentColor : "#2A2F38",
        boxShadow: isActive ? `0 0 0 1px ${accentColor}40, 0 1px 3px rgba(0,0,0,0.5)` : undefined,
        cursor: "pointer",
      }}
      onMouseEnter={(e) => {
        if (!isActive) {
          e.currentTarget.style.background = "#13161D";
          e.currentTarget.style.color = "#E8EAED";
          e.currentTarget.style.borderColor = "#3F4451";
        }
      }}
      onMouseLeave={(e) => {
        if (!isActive) {
          e.currentTarget.style.background = "#0F1217";
          e.currentTarget.style.color = "#9CA3AF";
          e.currentTarget.style.borderColor = "#2A2F38";
        }
      }}
    >
      {label}
      {isActive && (
        <span
          className="absolute left-2 right-2 -bottom-px h-px rounded-full"
          style={{ background: accentColor, boxShadow: `0 0 6px ${accentColor}` }}
        />
      )}
    </button>
  );
}

function SpeedButton({
  speed,
  active,
  enabled,
  onClick,
  hasDivider,
}: {
  speed: ReplaySpeed;
  active: boolean;
  enabled: boolean;
  onClick: () => void;
  hasDivider: boolean;
}) {
  return (
    <>
      {hasDivider && <div className="w-px bg-[#23262E]" />}
      <button
        onClick={onClick}
        disabled={!enabled}
        className="px-3 py-1.5 text-[10px] font-mono font-semibold tabular-nums transition-colors"
        style={{
          color: active ? MODE.replay.fg : enabled ? "#8B95A5" : "#3F4451",
          background: active ? MODE.replay.bg : "transparent",
          cursor: enabled ? "pointer" : "not-allowed",
          minWidth: "44px",
        }}
        title={enabled ? `Replay at ${speed}x` : "Speed control is only meaningful in replay mode"}
      >
        {speed}x
      </button>
    </>
  );
}

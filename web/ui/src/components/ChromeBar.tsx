"use client";

/**
 * ChromeBar — single-row chrome combining mode toggle, speed control,
 * and tab navigation.
 *
 * Replaces the original ModeBar + Activity Ticker stack. Designed for
 * presentation: mode is always color-coded (green=live, blue=replay),
 * speed control is always visible (grayed when irrelevant), tabs are
 * the primary depth-explorer, and there is no "Connect" button —
 * connection is automatic per page-load behavior in page.tsx.
 *
 * Heights and densities are tuned for 1600×1000 demo viewports.
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
  // shown next to the speed control (e.g. "Run 4 · 2026-04-16").
  replayLabel?: string;
}

const MODE_COLORS = {
  live: { fg: "#22C55E", bg: "rgba(34, 197, 94, 0.18)", border: "rgba(34, 197, 94, 0.5)" },
  replay: { fg: "#3B82F6", bg: "rgba(59, 130, 246, 0.18)", border: "rgba(59, 130, 246, 0.5)" },
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
  const palette = MODE_COLORS[mode];

  return (
    <div
      className="flex items-center gap-4 px-4 py-1.5 border-b bg-[#0A0C10]"
      style={{ borderColor: palette.border, transition: "border-color 200ms" }}
    >
      {/* Mode toggle — always shows current mode in color, click to swap */}
      <div className="flex items-stretch rounded-md overflow-hidden bg-[#0F1217] border border-[#23262E]">
        <ModeButton
          label="LIVE"
          dotChar="\u25CF"
          active={mode === "live"}
          disabled={!liveAvailable}
          color={MODE_COLORS.live.fg}
          bg={MODE_COLORS.live.bg}
          onClick={() => liveAvailable && setMode("live")}
          title={liveAvailable ? "Connect to the running harness" : "No live run is currently writing"}
        />
        <ModeButton
          label="REPLAY"
          dotChar="\u25B6"
          active={mode === "replay"}
          disabled={false}
          color={MODE_COLORS.replay.fg}
          bg={MODE_COLORS.replay.bg}
          onClick={() => setMode("replay")}
          title="Replay a recorded run at accelerated speed"
        />
      </div>

      {/* Speed control — always visible, grayed when irrelevant */}
      <div className="flex items-center gap-1.5">
        <span
          className="text-[9px] font-semibold tracking-[0.18em] uppercase"
          style={{ color: speedActive ? "#9CA3AF" : "#3F4451" }}
        >
          Speed
        </span>
        <div className="flex items-stretch rounded-md overflow-hidden bg-[#0F1217] border border-[#23262E]">
          {SPEEDS.map((s) => (
            <button
              key={s}
              onClick={() => speedActive && setReplaySpeed(s)}
              disabled={!speedActive}
              className="px-2.5 py-1 text-[10px] font-mono font-semibold tracking-wider transition-colors"
              style={{
                color:
                  speedActive && replaySpeed === s
                    ? MODE_COLORS.replay.fg
                    : speedActive
                    ? "#6B7280"
                    : "#3F4451",
                background:
                  speedActive && replaySpeed === s ? MODE_COLORS.replay.bg : "transparent",
                cursor: speedActive ? "pointer" : "not-allowed",
              }}
              title={
                speedActive
                  ? `Replay at ${s}x`
                  : "Speed control is only meaningful in replay mode"
              }
            >
              {s}x
            </button>
          ))}
        </div>
        {replayLabel && speedActive && (
          <span
            className="text-[10px] font-mono text-[#6B7280] ml-2 truncate max-w-[180px]"
            title={replayLabel}
          >
            {replayLabel}
          </span>
        )}
      </div>

      {/* Tabs — primary depth-explorer */}
      <nav className="ml-auto flex items-center gap-0.5 bg-[#0F1217] border border-[#23262E] rounded-md p-0.5">
        {(["live", "memory", "runs", "events"] as Tab[]).map((t) => (
          <button
            key={t}
            onClick={() => setActiveTab(t)}
            className="px-3 py-1 text-[11px] font-semibold uppercase tracking-wider rounded transition-colors"
            style={{
              color: activeTab === t ? "#E8EAED" : "#6B7280",
              background: activeTab === t ? "#23262E" : "transparent",
            }}
          >
            {t === "live" ? "Mission" : t}
          </button>
        ))}
      </nav>

      {/* Connection indicator — quiet, single dot */}
      <div className="flex items-center gap-1.5">
        <div
          className="w-1.5 h-1.5 rounded-full"
          style={{
            background: connected ? palette.fg : "#3F4451",
            boxShadow: connected ? `0 0 6px ${palette.fg}` : undefined,
            animation: connected ? "pulse 2s ease-in-out infinite" : undefined,
          }}
        />
        <span
          className="text-[9px] font-semibold tracking-[0.18em] uppercase"
          style={{ color: connected ? palette.fg : "#3F4451" }}
        >
          {connected ? (mode === "live" ? "Live" : "Replaying") : "Idle"}
        </span>
      </div>
    </div>
  );
}

function ModeButton({
  label,
  dotChar,
  active,
  disabled,
  color,
  bg,
  onClick,
  title,
}: {
  label: string;
  dotChar: string;
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
      className="px-3 py-1 text-[10px] font-bold uppercase tracking-[0.15em] transition-colors"
      style={{
        color: active ? color : disabled ? "#3F4451" : "#6B7280",
        background: active ? bg : "transparent",
        cursor: disabled ? "not-allowed" : "pointer",
      }}
    >
      <span className="mr-1.5">{dotChar}</span>
      {label}
    </button>
  );
}

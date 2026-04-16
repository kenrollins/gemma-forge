"use client";

/**
 * MemoryPulsePanel — "memory is load-bearing" at a glance.
 *
 * Shows the harness's cross-run memory state: how many prior runs
 * feed the current attempt, how many lessons were hydrated, how many
 * banned patterns, and the top lessons by weight. This is what lets a
 * presenter say "the Worker isn't starting from scratch — it's drawing
 * from 1,700 lessons learned across 18 prior runs" and have the panel
 * back them up visually.
 *
 * Today this reads from the cross_run_hydration event + the in-stream
 * banned_patterns count. When V2 ships per-tip retrieval logging,
 * this panel will show the exact tips retrieved for the current
 * attempt with their source-run provenance.
 */

import { useMemo } from "react";
import type { RunEvent, SkillUI, CrossRunData } from "./types";

export interface MemoryPulsePanelProps {
  events: RunEvent[];
  skillUI: SkillUI;
  crossRunData: CrossRunData | null;
}

interface HydrationState {
  priorRuns: number;
  loadedLessons: number;
  loadedBans: number;
}

interface TopLesson {
  text: string;
  category: string;
  weight: number;
}

export default function MemoryPulsePanel({ events, crossRunData }: MemoryPulsePanelProps) {
  const state = useMemo<HydrationState | null>(() => {
    if (!crossRunData) return null;
    return {
      priorRuns: crossRunData.prior_runs ?? 0,
      loadedLessons: crossRunData.loaded_lessons ?? 0,
      loadedBans: crossRunData.loaded_bans ?? 0,
    };
  }, [crossRunData]);

  // Pull top lessons from either the hydration event's payload (if
  // it includes them) or from nearby lesson/ban events. Right now
  // the harness doesn't log per-attempt retrievals (V2 will); this
  // approximates by showing the most-recent lesson texts observed.
  const topLessons = useMemo<TopLesson[]>(() => {
    const seen = new Set<string>();
    const result: TopLesson[] = [];
    for (let i = events.length - 1; i >= 0 && result.length < 3; i--) {
      const e = events[i];
      if (e.event_type === "reflection" && typeof e.data?.lesson === "string") {
        const text = e.data.lesson as string;
        if (!seen.has(text) && text.length > 0) {
          seen.add(text);
          result.push({
            text,
            category: (e.data.category as string) || "unknown",
            weight: 0.55, // Placeholder — the harness doesn't emit per-lesson
                         // weight in events yet. V2 adds tip_retrievals with
                         // per-(tip, rule) hit rates, which will replace this.
          });
        }
      }
    }
    return result;
  }, [events]);

  if (!state) {
    return (
      <div className="border-b border-[#1C1F26] bg-[#0A0C10] px-4 py-3">
        <Header />
        <div className="text-[10px] text-[#4B5563] italic py-2">
          Waiting for cross-run hydration...
        </div>
      </div>
    );
  }

  return (
    <div className="border-b border-[#1C1F26] bg-[#0A0C10] px-4 py-3">
      <Header />

      {/* Three-stat summary */}
      <div className="flex items-stretch gap-2 mb-3">
        <Stat value={state.priorRuns} label="prior runs" />
        <Divider />
        <Stat value={state.loadedLessons} label="lessons loaded" accent="#A855F7" />
        <Divider />
        <Stat value={state.loadedBans} label="bans loaded" accent="#F59E0B" />
      </div>

      {/* Top lessons in scope for the current attempt (approximation) */}
      {topLessons.length > 0 ? (
        <div className="space-y-1.5">
          <div className="text-[9px] font-semibold tracking-[0.18em] uppercase text-[#4B5563]">
            Recently surfaced
          </div>
          {topLessons.map((l, idx) => (
            <div
              key={idx}
              className="flex items-start gap-2 px-2 py-1.5 rounded-sm bg-[#0F1217] border border-[#1C1F26]"
            >
              <span
                className="mt-0.5 shrink-0 w-1 h-1 rounded-full"
                style={{ background: "#A855F7", boxShadow: "0 0 4px rgba(168,85,247,0.5)" }}
              />
              <div className="flex-1 min-w-0">
                <div
                  className="text-[10px] font-mono leading-snug text-[#9CA3AF] line-clamp-2"
                  title={l.text}
                >
                  {l.text}
                </div>
                <div className="text-[8px] text-[#4B5563] mt-0.5 uppercase tracking-wider">
                  {l.category}
                </div>
              </div>
            </div>
          ))}
        </div>
      ) : (
        <div className="text-[10px] text-[#4B5563] italic py-1">
          Lessons in scope — Reflector hasn&apos;t distilled anything new yet this run.
        </div>
      )}
    </div>
  );
}

function Header() {
  return (
    <div className="flex items-center gap-2 mb-2.5">
      <span className="text-[9px] font-semibold tracking-[0.2em] uppercase text-[#6B7280]">
        Memory
      </span>
      <span className="text-[9px] text-[#4B5563] font-mono">Postgres · Neo4j</span>
      <span
        className="ml-auto text-[9px] font-mono tracking-wider uppercase text-[#A855F7]"
        title="Three memory tiers: working (per-turn), episodic (per-item), semantic (cross-run). The dream pass curates between runs."
      >
        ● 3 tiers
      </span>
    </div>
  );
}

function Stat({ value, label, accent = "#E8EAED" }: { value: number; label: string; accent?: string }) {
  return (
    <div className="flex-1 flex flex-col items-center py-1">
      <div className="text-[16px] font-bold tabular-nums leading-none" style={{ color: accent }}>
        {value.toLocaleString()}
      </div>
      <div className="text-[8px] uppercase tracking-wider text-[#4B5563] mt-0.5 text-center">
        {label}
      </div>
    </div>
  );
}

function Divider() {
  return <div className="w-px bg-[#1C1F26] my-1" />;
}

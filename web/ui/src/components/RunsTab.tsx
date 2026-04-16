"use client";

/**
 * RunsTab — gallery of past runs as clickable cards.
 *
 * Each card summarizes a single run at a glance: date, duration, fix
 * rate (color-coded by tier so the eye groups strong from weak runs),
 * event count, and rem/esc breakdown. Clicking a card switches the
 * dashboard to the Mission view in Replay mode with that run loaded.
 *
 * Deliberately denser than the ChromeBar's RunPicker dropdown —
 * this is the "browse" surface, while the picker is "switch
 * quickly." Both share the same RunOption shape so the data flows
 * through without a mapping layer.
 */

import { useMemo, useState } from "react";
import type { RunOption } from "./ChromeBar";

export type RunsSort = "newest" | "oldest" | "fix-rate-desc" | "fix-rate-asc" | "duration-desc";

export interface RunsTabProps {
  runs: RunOption[];
  activeRun: string;
  onLaunch: (filename: string) => void;
}

function formatDuration(s: number): string {
  if (s < 60) return `${Math.round(s)}s`;
  if (s < 3600) return `${Math.floor(s / 60)}m`;
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  return `${h}h ${m}m`;
}

function formatDate(stamp: string): { date: string; time: string } {
  // Filename stamp is like "20260414-012052" — YYYYMMDD-HHMMSS.
  if (stamp.length >= 8) {
    const y = stamp.slice(0, 4);
    const mo = stamp.slice(4, 6);
    const d = stamp.slice(6, 8);
    const time = stamp.length >= 15 ? stamp.slice(9, 11) + ":" + stamp.slice(11, 13) : "";
    return { date: `${y}-${mo}-${d}`, time };
  }
  return { date: stamp, time: "" };
}

function fixRateTier(rate: number): { color: string; label: string } {
  if (rate >= 60) return { color: "#22C55E", label: "strong" };
  if (rate >= 40) return { color: "#F59E0B", label: "mixed" };
  return { color: "#EF4444", label: "weak" };
}

interface Summarized extends RunOption {
  stamp: string;
  dateObj: Date;
  remediated: number;
  escalated: number;
  skipped: number;
  fixRate: number | null;
  isDemoCandidate: boolean; // has a run_complete summary
}

function summarize(r: RunOption): Summarized {
  const stamp = r.filename.replace(/^run-|\.jsonl$/g, "");
  const { date, time } = formatDate(stamp);
  const iso = `${date}T${time || "00:00"}:00Z`;
  const dateObj = new Date(iso);
  const summary = (r.summary || {}) as Record<string, unknown>;
  const remediated = (summary["remediated"] as number) ?? 0;
  const escalated = (summary["escalated"] as number) ?? 0;
  const skipped = (summary["skipped"] as number) ?? 0;
  const fixRate =
    remediated + escalated > 0 ? (remediated / (remediated + escalated)) * 100 : null;
  return {
    ...r,
    stamp,
    dateObj,
    remediated,
    escalated,
    skipped,
    fixRate,
    isDemoCandidate: fixRate !== null && (remediated + escalated + skipped) >= 20,
  };
}

export default function RunsTab({ runs, activeRun, onLaunch }: RunsTabProps) {
  const [sort, setSort] = useState<RunsSort>("newest");
  const [hideSmoke, setHideSmoke] = useState(true);

  const rows = useMemo<Summarized[]>(() => {
    const summarized = runs.map(summarize);
    const filtered = hideSmoke ? summarized.filter((r) => r.isDemoCandidate) : summarized;
    const cmp: Record<RunsSort, (a: Summarized, b: Summarized) => number> = {
      newest: (a, b) => b.dateObj.getTime() - a.dateObj.getTime(),
      oldest: (a, b) => a.dateObj.getTime() - b.dateObj.getTime(),
      "fix-rate-desc": (a, b) => (b.fixRate ?? -1) - (a.fixRate ?? -1),
      "fix-rate-asc": (a, b) => (a.fixRate ?? 999) - (b.fixRate ?? 999),
      "duration-desc": (a, b) => b.elapsed_s - a.elapsed_s,
    };
    return [...filtered].sort(cmp[sort]);
  }, [runs, sort, hideSmoke]);

  const smokeCount = runs.length - rows.length;

  return (
    <div className="flex-1 overflow-y-auto bg-[#0B0D11]">
      {/* Header strip: sort + filter */}
      <div className="sticky top-0 z-10 bg-[#0B0D11]/95 backdrop-blur border-b border-[#1C1F26] px-6 py-3">
        <div className="flex items-baseline gap-4 max-w-[1400px] mx-auto">
          <div>
            <div className="text-[10px] font-semibold tracking-[0.2em] uppercase text-[#4B5563]">
              Past runs
            </div>
            <div className="text-[14px] font-bold text-[#E8EAED]">
              {rows.length} <span className="text-[#6B7280] font-normal">on record</span>
              {hideSmoke && smokeCount > 0 && (
                <span className="text-[11px] font-normal text-[#4B5563] ml-2">
                  ({smokeCount} smoke tests hidden)
                </span>
              )}
            </div>
          </div>

          <div className="ml-auto flex items-center gap-3 text-[10px] font-mono">
            <label className="flex items-center gap-1.5 text-[#9CA3AF] cursor-pointer">
              <input
                type="checkbox"
                checked={hideSmoke}
                onChange={(e) => setHideSmoke(e.target.checked)}
                className="accent-[#3B82F6]"
              />
              <span>hide smoke tests</span>
            </label>
            <span className="text-[#3F4451]">·</span>
            <span className="text-[#4B5563] uppercase tracking-wider">Sort</span>
            <select
              value={sort}
              onChange={(e) => setSort(e.target.value as RunsSort)}
              className="bg-[#0F1217] border border-[#2A2F38] rounded-md px-2 py-1 text-[#E8EAED] focus:outline-none focus:border-[#3B82F6]"
            >
              <option value="newest">Newest first</option>
              <option value="oldest">Oldest first</option>
              <option value="fix-rate-desc">Highest fix rate</option>
              <option value="fix-rate-asc">Lowest fix rate</option>
              <option value="duration-desc">Longest first</option>
            </select>
          </div>
        </div>
      </div>

      {/* Grid */}
      <div className="max-w-[1400px] mx-auto px-6 py-5">
        {rows.length === 0 ? (
          <div className="text-center py-20 text-[#4B5563]">
            <div className="text-[12px] uppercase tracking-[0.2em] mb-2">No runs</div>
            <div className="text-[11px]">
              {hideSmoke
                ? "Every record so far is under 20 work items — untick \"hide smoke tests\" to show them."
                : "Launch a run from the harness CLI to start populating history."}
            </div>
          </div>
        ) : (
          <div
            className="grid gap-4"
            style={{ gridTemplateColumns: "repeat(auto-fill, minmax(320px, 1fr))" }}
          >
            {rows.map((r) => (
              <RunCard
                key={r.filename}
                row={r}
                active={r.filename === activeRun}
                onLaunch={onLaunch}
              />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

function RunCard({
  row,
  active,
  onLaunch,
}: {
  row: Summarized;
  active: boolean;
  onLaunch: (filename: string) => void;
}) {
  const { stamp, remediated, escalated, skipped, fixRate, elapsed_s, events } = row;
  const { date, time } = formatDate(stamp);
  const tier = fixRate !== null ? fixRateTier(fixRate) : { color: "#6B7280", label: "incomplete" };
  const total = remediated + escalated + skipped;

  return (
    <button
      onClick={() => onLaunch(row.filename)}
      className="text-left bg-[#0F1217] border rounded-lg p-4 transition-all duration-150 hover:-translate-y-0.5"
      style={{
        borderColor: active ? "#3B82F6" : "#1C1F26",
        boxShadow: active
          ? "0 0 0 1px #3B82F6, 0 4px 12px rgba(59,130,246,0.25)"
          : "0 1px 2px rgba(0,0,0,0.4)",
      }}
      onMouseEnter={(e) => {
        if (!active) e.currentTarget.style.borderColor = "#2A2F38";
      }}
      onMouseLeave={(e) => {
        if (!active) e.currentTarget.style.borderColor = "#1C1F26";
      }}
    >
      {/* Top row: timestamp + fix rate tier */}
      <div className="flex items-start justify-between gap-3 mb-3">
        <div>
          <div className="text-[13px] font-bold text-[#E8EAED] font-mono tabular-nums">
            {date}
            <span className="text-[#4B5563] font-normal ml-1.5">{time}</span>
          </div>
          <div className="text-[9px] uppercase tracking-wider text-[#6B7280] mt-0.5">
            {formatDuration(elapsed_s)} · {events.toLocaleString()} events
          </div>
        </div>
        {fixRate !== null ? (
          <div className="text-right">
            <div className="text-[22px] font-bold tabular-nums leading-none" style={{ color: tier.color }}>
              {fixRate.toFixed(0)}<span className="text-[12px]">%</span>
            </div>
            <div className="text-[8px] uppercase tracking-[0.15em] mt-0.5" style={{ color: tier.color }}>
              {tier.label}
            </div>
          </div>
        ) : (
          <div className="text-[10px] text-[#4B5563] italic">no summary</div>
        )}
      </div>

      {/* Horizontal segmented bar: remediated / escalated / skipped */}
      {total > 0 && (
        <div className="h-2 rounded-full bg-[#15181F] overflow-hidden flex mb-2">
          {remediated > 0 && (
            <div
              style={{
                width: `${(remediated / total) * 100}%`,
                background: "#22C55E",
              }}
            />
          )}
          {escalated > 0 && (
            <div
              style={{
                width: `${(escalated / total) * 100}%`,
                background: "#F59E0B",
              }}
            />
          )}
          {skipped > 0 && (
            <div
              style={{
                width: `${(skipped / total) * 100}%`,
                background: "#4B5563",
              }}
            />
          )}
        </div>
      )}

      {/* Bottom row: breakdown counts */}
      <div className="flex items-center gap-3 text-[10px] font-mono tabular-nums text-[#6B7280]">
        <span>
          <span className="text-[#22C55E]">{remediated}</span> remediated
        </span>
        <span>
          <span className="text-[#F59E0B]">{escalated}</span> escalated
        </span>
        {skipped > 0 && <span>{skipped} skipped</span>}
        <span className="ml-auto text-[9px] uppercase tracking-wider" style={{ color: active ? "#3B82F6" : "#3F4451" }}>
          {active ? "now playing" : "click to replay"}
        </span>
      </div>
    </button>
  );
}

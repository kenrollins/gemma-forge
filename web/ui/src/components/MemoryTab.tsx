"use client";

/**
 * MemoryTab — the Reflective tier surfaced as tangible artifacts.
 *
 * Previously rendered as a Category → Rule → Lesson graph, but that
 * painted rule outcomes (green/amber boxes) rather than memory
 * content. The three artifacts the system actually produces are:
 *
 *   1. BAN PATTERNS — regex-style strings the Reflector forbids after
 *      they fail. Carried as ban_added events, one per new pattern.
 *   2. REFLECTIONS — prose observations the Reflector writes when a
 *      rule fails. Emitted as reflection events with a markdown
 *      "Pattern identified: X / Root cause: Y" structure.
 *   3. CROSS-RUN HYDRATION — counts of bans/lessons loaded from
 *      prior runs, plus per-category success rates.
 *
 * This layout puts those three front and center. The top strip is
 * "what the system brought into this run" (prior-run state). Below it,
 * two feeds grow as the Reflector writes: banned approaches on the
 * left, reflection insights on the right. Both highlight freshly
 * written entries with a pulsing accent so the eye tracks growth even
 * at 1000x replay.
 *
 * V2 (future) will fetch lesson provenance + hit-rate from the
 * Neo4j graph endpoint so we can show "this lesson helped N times."
 */

import { useEffect, useMemo, useState } from "react";
import type { RunEvent, SkillUI, CrossRunData, CategoryStat } from "./types";

// ============================================================================
// Types
// ============================================================================

interface BanEntry {
  id: string;
  pattern: string;
  ruleId?: string;
  category?: string;
  total: number;
  tsMs: number;
  elapsed_s: number;
}

interface ReflectionEntry {
  id: string;
  patternIdentified?: string;
  rootCause?: string;
  rest?: string;
  ruleId?: string;
  category?: string;
  attempt?: number;
  newBansThisReflection: number;
  plateaued: boolean;
  tsMs: number;
  elapsed_s: number;
}

const FRESH_WINDOW_MS = 5000;
const BAN_COLOR = "#F87171";
const REFLECTION_COLOR = "#C084FC";
const CATEGORY_ACCENT = "#60A5FA";

// ============================================================================
// Event parsing
// ============================================================================

/** Strip code fences + leading/solo labels, return { pattern, rootCause, rest }. */
function parseReflectionBlob(raw: string | undefined): {
  pattern?: string;
  rootCause?: string;
  rest?: string;
} {
  if (!raw) return {};
  const stripped = raw
    .replace(/^\s*```[a-z]*\s*\n?/i, "")
    .replace(/\n?\s*```\s*$/, "")
    .trim();
  const lines = stripped
    .split(/\r?\n+/)
    .map((s) => s.trim())
    .filter(Boolean);

  let pattern: string | undefined;
  let rootCause: string | undefined;
  const restLines: string[] = [];

  for (const line of lines) {
    if (/^REFLECTION\s*:?\s*$/i.test(line)) continue;
    const patMatch = /^Pattern\s+(identified|noted|discovered|found)\s*:\s*(.+)$/i.exec(line);
    if (patMatch && !pattern) {
      pattern = patMatch[2].trim();
      continue;
    }
    const rootMatch = /^Root\s+cause\s*:\s*(.+)$/i.exec(line);
    if (rootMatch && !rootCause) {
      rootCause = rootMatch[1].trim();
      continue;
    }
    restLines.push(line);
  }

  return { pattern, rootCause, rest: restLines.join(" ") };
}

function deriveMemory(events: RunEvent[]): {
  bans: BanEntry[];
  reflections: ReflectionEntry[];
  ruleCategoryMap: Map<string, string>;
  crossRun?: CrossRunData;
  bansThisRun: number;
} {
  const ruleCategoryMap = new Map<string, string>();
  const bans: BanEntry[] = [];
  const reflections: ReflectionEntry[] = [];
  let crossRun: CrossRunData | undefined;
  let banCounter = 0;
  let reflectionCounter = 0;

  for (const e of events) {
    const d = e.data || {};
    const tsMs = e.timestamp ? new Date(e.timestamp).getTime() : 0;

    if (e.event_type === "rule_selected") {
      const rid = d.rule_id as string | undefined;
      const cat = d.category as string | undefined;
      if (rid && cat) ruleCategoryMap.set(rid, cat);
    }
    if (e.event_type === "cross_run_hydration") {
      crossRun = d as unknown as CrossRunData;
    }
    if (e.event_type === "ban_added") {
      const ruleId = d.rule_id as string | undefined;
      bans.push({
        id: `b${++banCounter}`,
        pattern: (d.pattern as string) || "",
        ruleId,
        category: ruleId ? ruleCategoryMap.get(ruleId) : undefined,
        total: (d.banned_patterns_total as number) || 0,
        tsMs,
        elapsed_s: e.elapsed_s,
      });
    }
    if (e.event_type === "reflection") {
      const ruleId = d.rule_id as string | undefined;
      const parsed = parseReflectionBlob(d.text as string | undefined);
      reflections.push({
        id: `r${++reflectionCounter}`,
        patternIdentified: parsed.pattern,
        rootCause: parsed.rootCause,
        rest: parsed.rest,
        ruleId,
        category: ruleId ? ruleCategoryMap.get(ruleId) : undefined,
        attempt: d.attempt as number | undefined,
        newBansThisReflection: (d.new_bans_this_reflection as number) || 0,
        plateaued: Boolean(d.plateaued),
        tsMs,
        elapsed_s: e.elapsed_s,
      });
    }
  }

  // Second pass: categories referenced by rule_id that wasn't seen in a
  // rule_selected (e.g. architect_reengaged carries category too). Cheap.
  for (const e of events) {
    const d = e.data || {};
    if (e.event_type === "architect_reengaged") {
      const rid = d.rule_id as string | undefined;
      const cat = d.category as string | undefined;
      if (rid && cat && !ruleCategoryMap.has(rid)) ruleCategoryMap.set(rid, cat);
    }
  }
  // Backfill category on entries that were captured before rule_selected.
  for (const b of bans) if (!b.category && b.ruleId) b.category = ruleCategoryMap.get(b.ruleId);
  for (const r of reflections) if (!r.category && r.ruleId) r.category = ruleCategoryMap.get(r.ruleId);

  return { bans, reflections, ruleCategoryMap, crossRun, bansThisRun: bans.length };
}

// ============================================================================
// Top strip — cross-run hydration + this-run counters
// ============================================================================

function shortRuleId(id?: string): string {
  if (!id) return "";
  const prefix = "xccdf_org.ssgproject.content_rule_";
  return id.startsWith(prefix) ? id.slice(prefix.length) : id;
}

function formatElapsed(s: number): string {
  if (s < 60) return `${Math.round(s)}s`;
  if (s < 3600) return `${Math.floor(s / 60)}m`;
  return `${Math.floor(s / 3600)}h ${Math.floor((s % 3600) / 60)}m`;
}

function TopStrip({
  crossRun,
  bansThisRun,
  reflectionsThisRun,
}: {
  crossRun?: CrossRunData;
  bansThisRun: number;
  reflectionsThisRun: number;
}) {
  const cats: CategoryStat[] = crossRun?.category_stats || [];
  return (
    <div className="border-b border-[#1C1F26] bg-[#0A0C10] px-5 py-3">
      <div className="flex items-baseline gap-4 mb-2">
        <div>
          <div className="text-[10px] font-semibold tracking-[0.2em] uppercase text-[#4B5563]">
            Reflective memory
          </div>
          <div className="text-[13px] font-bold text-[#E8EAED]">
            What the system learned &mdash; and what it&rsquo;s writing right now
          </div>
        </div>
        <div className="ml-auto flex items-center gap-5 text-[11px] font-mono">
          <Counter
            label="Prior runs"
            value={crossRun?.prior_runs ?? 0}
            color={CATEGORY_ACCENT}
          />
          <Counter
            label="Loaded bans"
            value={crossRun?.loaded_bans ?? 0}
            color={BAN_COLOR}
            hint="Ban patterns hydrated from prior runs at startup"
          />
          <Counter
            label="Loaded lessons"
            value={crossRun?.loaded_lessons ?? 0}
            color={REFLECTION_COLOR}
            hint="Distilled lessons carried over from prior runs"
          />
          <span className="text-[#3F4451]">|</span>
          <Counter
            label="Bans this run"
            value={bansThisRun}
            color={BAN_COLOR}
            glow
            hint="New ban patterns the Reflector has added in this run"
          />
          <Counter
            label="Reflections"
            value={reflectionsThisRun}
            color={REFLECTION_COLOR}
            glow
            hint="Prose observations the Reflector has written in this run"
          />
        </div>
      </div>
      {cats.length > 0 && (
        <div className="flex items-center gap-1.5 flex-wrap mt-2">
          <span className="text-[9px] uppercase tracking-wider text-[#4B5563] shrink-0">
            Category success (prior runs):
          </span>
          {cats.map((c) => (
            <CategoryBar key={c.category} stat={c} />
          ))}
        </div>
      )}
    </div>
  );
}

function Counter({
  label,
  value,
  color,
  glow,
  hint,
}: {
  label: string;
  value: number;
  color: string;
  glow?: boolean;
  hint?: string;
}) {
  return (
    <span className="flex items-baseline gap-1.5" title={hint}>
      <span
        className="text-[15px] font-bold tabular-nums leading-none"
        style={{
          color,
          textShadow: glow && value > 0 ? `0 0 10px ${color}80` : undefined,
        }}
      >
        {value}
      </span>
      <span className="text-[9px] uppercase tracking-wider text-[#6B7280]">{label}</span>
    </span>
  );
}

function CategoryBar({ stat }: { stat: CategoryStat }) {
  const pct = Math.max(0, Math.min(1, stat.success_rate));
  const color =
    pct >= 0.75 ? "#22C55E" : pct >= 0.5 ? "#F59E0B" : "#EF4444";
  return (
    <div
      className="flex items-center gap-1.5 border border-[#1C1F26] rounded-sm px-1.5 py-0.5"
      title={`${stat.category}: ${(pct * 100).toFixed(0)}% success across ${stat.total_items} items (avg ${stat.avg_attempts.toFixed(1)} attempts)`}
    >
      <span className="text-[9px] font-mono text-[#9CA3AF]">
        {stat.category.replace(/_/g, "\u00a0")}
      </span>
      <div className="w-10 h-1 rounded-full overflow-hidden bg-[#15181F]">
        <div
          className="h-full"
          style={{ width: `${pct * 100}%`, background: color }}
        />
      </div>
      <span
        className="text-[9px] font-mono tabular-nums"
        style={{ color }}
      >
        {Math.round(pct * 100)}%
      </span>
    </div>
  );
}

// ============================================================================
// Bans panel
// ============================================================================

function BansPanel({ bans, now }: { bans: BanEntry[]; now: number }) {
  const sorted = useMemo(() => [...bans].reverse(), [bans]);
  const latestTsMs = sorted.length > 0 ? sorted[0].tsMs : 0;

  return (
    <div className="flex-1 min-w-0 overflow-hidden flex flex-col bg-[#0B0D11]">
      <div className="px-5 py-2.5 border-b border-[#1C1F26] flex items-baseline gap-3 bg-[#0A0C10]">
        <span className="text-[10px] font-semibold tracking-[0.2em] uppercase" style={{ color: BAN_COLOR }}>
          Banned approaches
        </span>
        <span className="text-[10px] font-mono text-[#6B7280]">
          {bans.length} pattern{bans.length === 1 ? "" : "s"}
        </span>
        <span className="ml-auto text-[9px] text-[#4B5563]">
          the Reflector forbids these after they fail
        </span>
      </div>
      <div className="flex-1 overflow-y-auto px-4 py-3">
        {sorted.length === 0 ? (
          <div className="text-[11px] text-[#4B5563] italic py-8 text-center">
            No bans written yet in this run. They appear here the instant the Reflector adds one.
          </div>
        ) : (
          <div
            className="grid gap-2"
            style={{ gridTemplateColumns: "repeat(auto-fill, minmax(320px, 1fr))" }}
          >
            {sorted.map((b) => (
              <BanCard key={b.id} ban={b} now={now} isLatest={b.tsMs === latestTsMs} />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

function BanCard({ ban, now, isLatest }: { ban: BanEntry; now: number; isLatest: boolean }) {
  const age = Math.max(0, now - ban.tsMs);
  const fresh = age < FRESH_WINDOW_MS;
  const freshFactor = fresh ? Math.max(0, 1 - age / FRESH_WINDOW_MS) : 0;
  const glow =
    isLatest && fresh
      ? `0 0 ${8 + 18 * freshFactor}px rgba(248,113,113,${0.3 + 0.5 * freshFactor})`
      : "0 1px 2px rgba(0,0,0,0.4)";
  const border = fresh ? "#F87171" : "#2A2A2F";
  const bg = fresh ? "rgba(248,113,113,0.08)" : "#0F1217";

  return (
    <div
      className="rounded-md border px-3 py-2.5"
      style={{
        borderColor: border,
        background: bg,
        boxShadow: glow,
        transition: "border-color 600ms, background 600ms, box-shadow 600ms",
      }}
    >
      <div className="flex items-center gap-2 mb-1.5">
        <span
          className="inline-block w-1.5 h-1.5 rounded-full"
          style={{
            background: BAN_COLOR,
            boxShadow: fresh ? `0 0 6px ${BAN_COLOR}` : undefined,
          }}
        />
        {ban.category && (
          <span
            className="text-[9px] font-mono uppercase tracking-wider px-1.5 py-0.5 rounded-sm"
            style={{ background: "rgba(96,165,250,0.12)", color: CATEGORY_ACCENT }}
          >
            {ban.category}
          </span>
        )}
        <span className="ml-auto text-[9px] font-mono tabular-nums text-[#6B7280]">
          #{ban.total} &middot; at {formatElapsed(ban.elapsed_s)}
        </span>
      </div>
      <div
        className="text-[11px] font-mono text-[#FECACA] leading-snug break-words"
        title={ban.pattern}
      >
        {ban.pattern}
      </div>
      {ban.ruleId && (
        <div
          className="text-[9px] font-mono text-[#4B5563] mt-1.5 truncate"
          title={ban.ruleId}
        >
          from {shortRuleId(ban.ruleId)}
        </div>
      )}
    </div>
  );
}

// ============================================================================
// Reflections panel
// ============================================================================

function ReflectionsPanel({
  reflections,
  now,
}: {
  reflections: ReflectionEntry[];
  now: number;
}) {
  const sorted = useMemo(() => [...reflections].reverse(), [reflections]);
  const latestTsMs = sorted.length > 0 ? sorted[0].tsMs : 0;

  return (
    <div className="flex-1 min-w-0 overflow-hidden flex flex-col bg-[#0B0D11] border-l border-[#1C1F26]">
      <div className="px-5 py-2.5 border-b border-[#1C1F26] flex items-baseline gap-3 bg-[#0A0C10]">
        <span
          className="text-[10px] font-semibold tracking-[0.2em] uppercase"
          style={{ color: REFLECTION_COLOR }}
        >
          Reflection insights
        </span>
        <span className="text-[10px] font-mono text-[#6B7280]">
          {reflections.length} observation{reflections.length === 1 ? "" : "s"}
        </span>
        <span className="ml-auto text-[9px] text-[#4B5563]">
          what the Reflector noticed when attempts failed
        </span>
      </div>
      <div className="flex-1 overflow-y-auto px-4 py-3">
        {sorted.length === 0 ? (
          <div className="text-[11px] text-[#4B5563] italic py-8 text-center">
            No reflections yet. They land here whenever the Reflector writes one.
          </div>
        ) : (
          <div className="flex flex-col gap-2.5">
            {sorted.map((r) => (
              <ReflectionCard
                key={r.id}
                reflection={r}
                now={now}
                isLatest={r.tsMs === latestTsMs}
              />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

function ReflectionCard({
  reflection,
  now,
  isLatest,
}: {
  reflection: ReflectionEntry;
  now: number;
  isLatest: boolean;
}) {
  const age = Math.max(0, now - reflection.tsMs);
  const fresh = age < FRESH_WINDOW_MS;
  const freshFactor = fresh ? Math.max(0, 1 - age / FRESH_WINDOW_MS) : 0;
  const glow =
    isLatest && fresh
      ? `0 0 ${8 + 18 * freshFactor}px rgba(192,132,252,${0.3 + 0.5 * freshFactor})`
      : "0 1px 2px rgba(0,0,0,0.4)";
  const border = fresh ? REFLECTION_COLOR : "#2A2A2F";
  const bg = fresh ? "rgba(168,85,247,0.07)" : "#0F1217";

  return (
    <div
      className="rounded-md border px-3 py-2.5"
      style={{
        borderColor: border,
        background: bg,
        boxShadow: glow,
        transition: "border-color 600ms, background 600ms, box-shadow 600ms",
      }}
    >
      <div className="flex items-center gap-2 mb-1.5">
        <span
          className="inline-block w-1.5 h-1.5 rounded-full"
          style={{
            background: REFLECTION_COLOR,
            boxShadow: fresh ? `0 0 6px ${REFLECTION_COLOR}` : undefined,
          }}
        />
        {reflection.category && (
          <span
            className="text-[9px] font-mono uppercase tracking-wider px-1.5 py-0.5 rounded-sm"
            style={{ background: "rgba(96,165,250,0.12)", color: CATEGORY_ACCENT }}
          >
            {reflection.category}
          </span>
        )}
        {reflection.attempt !== undefined && (
          <span className="text-[9px] font-mono text-[#6B7280]">
            attempt {reflection.attempt}
          </span>
        )}
        {reflection.plateaued && (
          <span
            className="text-[9px] font-mono uppercase tracking-wider px-1.5 py-0.5 rounded-sm"
            style={{ background: "rgba(245,158,11,0.15)", color: "#FBBF24" }}
          >
            plateaued
          </span>
        )}
        {reflection.newBansThisReflection > 0 && (
          <span
            className="text-[9px] font-mono tabular-nums px-1.5 py-0.5 rounded-sm"
            style={{ background: "rgba(248,113,113,0.15)", color: BAN_COLOR }}
            title="Ban patterns added alongside this reflection"
          >
            +{reflection.newBansThisReflection} ban{reflection.newBansThisReflection === 1 ? "" : "s"}
          </span>
        )}
        <span className="ml-auto text-[9px] font-mono tabular-nums text-[#6B7280]">
          at {formatElapsed(reflection.elapsed_s)}
        </span>
      </div>

      {reflection.patternIdentified ? (
        <div className="mb-1.5">
          <div className="text-[8px] uppercase tracking-wider text-[#6B7280] mb-0.5">
            Pattern identified
          </div>
          <div className="text-[11.5px] font-mono text-[#E8EAED] leading-snug">
            {reflection.patternIdentified}
          </div>
        </div>
      ) : null}

      {reflection.rootCause ? (
        <div className="mb-1.5">
          <div className="text-[8px] uppercase tracking-wider text-[#6B7280] mb-0.5">
            Root cause
          </div>
          <div className="text-[11px] font-mono text-[#D1D5DB] leading-snug">
            {reflection.rootCause}
          </div>
        </div>
      ) : null}

      {!reflection.patternIdentified && !reflection.rootCause && reflection.rest ? (
        <div className="text-[11px] font-mono text-[#D1D5DB] leading-snug line-clamp-4">
          {reflection.rest}
        </div>
      ) : null}

      {reflection.ruleId && (
        <div
          className="text-[9px] font-mono text-[#4B5563] mt-1.5 truncate"
          title={reflection.ruleId}
        >
          from {shortRuleId(reflection.ruleId)}
        </div>
      )}
    </div>
  );
}

// ============================================================================
// Main
// ============================================================================

export interface MemoryTabProps {
  events: RunEvent[];
  skillUI: SkillUI;
}

export default function MemoryTab({ events }: MemoryTabProps) {
  const { bans, reflections, crossRun } = useMemo(() => deriveMemory(events), [events]);

  // Drive the fresh-glow animation for recent ban/reflection entries.
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const anyFresh =
      bans.some((b) => Date.now() - b.tsMs < FRESH_WINDOW_MS) ||
      reflections.some((r) => Date.now() - r.tsMs < FRESH_WINDOW_MS);
    if (!anyFresh) return;
    const t = setInterval(() => setNow(Date.now()), 500);
    return () => clearInterval(t);
  }, [bans, reflections]);

  return (
    <div className="flex-1 flex flex-col overflow-hidden bg-[#0B0D11]">
      <TopStrip
        crossRun={crossRun}
        bansThisRun={bans.length}
        reflectionsThisRun={reflections.length}
      />
      <div className="flex-1 flex overflow-hidden min-h-0">
        <BansPanel bans={bans} now={now} />
        <ReflectionsPanel reflections={reflections} now={now} />
      </div>
    </div>
  );
}

"use client";

/**
 * MemoryTab — the Reflective tier surfaced as tangible artifacts.
 *
 * The three artifacts the system produces are:
 *
 *   1. TIPS — the Reflector's typed memory writes. Today only `ban_added`
 *      events are emitted (type = warning), but the V2 refactor
 *      (docs/drafts/v2-architecture-plan.md) adds `tip_added` events with
 *      four tip_types: strategy (green, "do this"), recovery (amber,
 *      "when X fails, try Y"), optimization (blue, faster/better), and
 *      warning (red, current bans). The left panel is already wired to
 *      color-code by tip_type so the moment the refactor lands, strategy
 *      and optimization tips will start rendering in their own colors
 *      without a UI change.
 *   2. REFLECTIONS — prose observations the Reflector writes when a
 *      rule fails. Emitted as reflection events with a markdown
 *      "Pattern identified: X / Root cause: Y" structure.
 *   3. CROSS-RUN HYDRATION — counts of bans/lessons loaded from
 *      prior runs, plus per-category success rates.
 *
 * Top strip: what the system brought into this run. Below it, two
 * feeds grow as the Reflector writes: typed tips on the left,
 * reflection insights on the right. Freshest entry glows so the eye
 * tracks growth even at 1000x replay.
 */

import { useEffect, useMemo, useState } from "react";
import type { RunEvent, SkillUI, CrossRunData, CategoryStat } from "./types";

// ============================================================================
// Types
// ============================================================================

/** V2 tip_type values per docs/drafts/v2-architecture-plan.md §2.1.
 *  Unknown strings fall through to "warning" — the conservative default
 *  also used for migrated V1 lessons.  */
export type TipType = "strategy" | "recovery" | "optimization" | "warning";

interface TipEntry {
  id: string;
  /** For warnings (ban_added): the regex string. For typed tips: the actionable advice. */
  text: string;
  tipType: TipType;
  /** Whether the underlying signal is literally a ban regex — lets the
   *  card render the text as monospace code vs. natural-language advice. */
  isBanPattern: boolean;
  ruleId?: string;
  category?: string;
  triggerConditions?: string[];
  /** Running count at the time this tip was written (ban_added carries
   *  banned_patterns_total; tip_added will carry a similar total). */
  total?: number;
  tsMs: number;
  elapsed_s: number;
}

const TIP_COLOR: Record<TipType, string> = {
  strategy: "#22C55E",     // green — "do this, it worked"
  optimization: "#3B82F6", // blue  — "do it faster / better"
  recovery: "#F59E0B",     // amber — "when X fails, try Y"
  warning: "#F87171",      // red   — "never do X" (current bans)
};

const TIP_BG: Record<TipType, string> = {
  strategy: "rgba(34,197,94,0.08)",
  optimization: "rgba(59,130,246,0.08)",
  recovery: "rgba(245,158,11,0.08)",
  warning: "rgba(248,113,113,0.08)",
};

const TIP_TEXT_COLOR: Record<TipType, string> = {
  strategy: "#BBF7D0",
  optimization: "#BFDBFE",
  recovery: "#FDE68A",
  warning: "#FECACA",
};

const TIP_LABEL: Record<TipType, string> = {
  strategy: "strategy",
  optimization: "optimization",
  recovery: "recovery",
  warning: "warning",
};

function normalizeTipType(raw: unknown): TipType {
  const s = typeof raw === "string" ? raw.toLowerCase() : "";
  if (s === "strategy" || s === "recovery" || s === "optimization" || s === "warning") {
    return s;
  }
  return "warning";
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
  tips: TipEntry[];
  reflections: ReflectionEntry[];
  ruleCategoryMap: Map<string, string>;
  crossRun?: CrossRunData;
  countsByType: Record<TipType, number>;
} {
  const ruleCategoryMap = new Map<string, string>();
  const tips: TipEntry[] = [];
  const reflections: ReflectionEntry[] = [];
  let crossRun: CrossRunData | undefined;
  let tipCounter = 0;
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

    // V1: ban_added is a warning-type tip. The text IS a regex pattern,
    // so cards render it as monospace code rather than natural-language
    // advice.
    if (e.event_type === "ban_added") {
      const ruleId = d.rule_id as string | undefined;
      tips.push({
        id: `t${++tipCounter}`,
        text: (d.pattern as string) || "",
        tipType: "warning",
        isBanPattern: true,
        ruleId,
        category: ruleId ? ruleCategoryMap.get(ruleId) : undefined,
        total: (d.banned_patterns_total as number) || 0,
        tsMs,
        elapsed_s: e.elapsed_s,
      });
    }

    // V2: tip_added carries tip_type + actionable advice. Ready before
    // the backend starts emitting it so the left panel colors update
    // the moment the refactor lands.
    if (e.event_type === "tip_added") {
      const ruleId = d.rule_id as string | undefined;
      const tipType = normalizeTipType(d.tip_type);
      tips.push({
        id: `t${++tipCounter}`,
        text: (d.text as string) || (d.tip as string) || "",
        tipType,
        isBanPattern: tipType === "warning" && looksLikeRegex(d.text as string),
        ruleId,
        category:
          (d.category as string) ||
          (ruleId ? ruleCategoryMap.get(ruleId) : undefined),
        triggerConditions: Array.isArray(d.trigger_conditions)
          ? (d.trigger_conditions as string[])
          : undefined,
        total:
          (d.tips_total as number) ||
          (d.total as number) ||
          undefined,
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

  // Second pass: some categories only appear on architect_reengaged.
  for (const e of events) {
    const d = e.data || {};
    if (e.event_type === "architect_reengaged") {
      const rid = d.rule_id as string | undefined;
      const cat = d.category as string | undefined;
      if (rid && cat && !ruleCategoryMap.has(rid)) ruleCategoryMap.set(rid, cat);
    }
  }
  // Backfill category on entries captured before rule_selected.
  for (const t of tips) if (!t.category && t.ruleId) t.category = ruleCategoryMap.get(t.ruleId);
  for (const r of reflections) if (!r.category && r.ruleId) r.category = ruleCategoryMap.get(r.ruleId);

  const countsByType: Record<TipType, number> = {
    strategy: 0,
    optimization: 0,
    recovery: 0,
    warning: 0,
  };
  for (const t of tips) countsByType[t.tipType]++;

  return { tips, reflections, ruleCategoryMap, crossRun, countsByType };
}

/** Heuristic: treat a V2 warning-type tip as a regex if it opens with
 *  backtick-delimited content or contains escape sequences typical of
 *  the current ban grammar. Pure safety net — V2 `tip_type: warning`
 *  may carry natural-language advice too. */
function looksLikeRegex(s: string | undefined): boolean {
  if (!s) return false;
  return /^`.*`/.test(s.trim()) || /\\[bswd]/.test(s);
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
  tipsThisRun,
  countsByType,
  reflectionsThisRun,
}: {
  crossRun?: CrossRunData;
  tipsThisRun: number;
  countsByType: Record<TipType, number>;
  reflectionsThisRun: number;
}) {
  const cats: CategoryStat[] = crossRun?.category_stats || [];
  // Show type chips only for types that have fired at least once. Keeps
  // the strip honest — pre-V2 runs only have `warning`, so there's no
  // point showing four zero pills. Post-V2, strategy/optimization appear
  // automatically as the backend starts emitting them.
  const activeTypes = (Object.keys(countsByType) as TipType[]).filter(
    (t) => countsByType[t] > 0,
  );
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
            color={TIP_COLOR.warning}
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
            label="Tips this run"
            value={tipsThisRun}
            color="#E8EAED"
            glow
            hint="Typed memory writes (strategy, recovery, optimization, warning)"
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

      {activeTypes.length > 0 && (
        <div className="flex items-center gap-1.5 flex-wrap mt-2 mb-2">
          <span className="text-[9px] uppercase tracking-wider text-[#4B5563] shrink-0">
            Tip breakdown:
          </span>
          {activeTypes.map((t) => (
            <TipTypePill key={t} type={t} count={countsByType[t]} />
          ))}
          {activeTypes.length === 1 && activeTypes[0] === "warning" && (
            <span className="text-[9px] text-[#4B5563] italic ml-1">
              V2 refactor will add strategy &middot; recovery &middot; optimization tips
            </span>
          )}
        </div>
      )}

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

function TipTypePill({ type, count }: { type: TipType; count: number }) {
  const color = TIP_COLOR[type];
  return (
    <span
      className="flex items-center gap-1 border rounded-sm px-1.5 py-0.5"
      style={{
        borderColor: `${color}55`,
        background: TIP_BG[type],
      }}
      title={`${count} ${TIP_LABEL[type]} tip${count === 1 ? "" : "s"} this run`}
    >
      <span
        className="inline-block w-1.5 h-1.5 rounded-full"
        style={{ background: color, boxShadow: `0 0 4px ${color}` }}
      />
      <span className="text-[9px] font-mono tabular-nums" style={{ color }}>
        {count}
      </span>
      <span className="text-[9px] font-mono uppercase tracking-wider text-[#9CA3AF]">
        {TIP_LABEL[type]}
      </span>
    </span>
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
// Tips panel (unified, color-coded by tip_type)
// ============================================================================

function TipsPanel({ tips, now }: { tips: TipEntry[]; now: number }) {
  const sorted = useMemo(() => [...tips].reverse(), [tips]);
  const latestTsMs = sorted.length > 0 ? sorted[0].tsMs : 0;

  return (
    <div className="flex-1 min-w-0 overflow-hidden flex flex-col bg-[#0B0D11]">
      <div className="px-5 py-2.5 border-b border-[#1C1F26] flex items-baseline gap-3 bg-[#0A0C10]">
        <span className="text-[10px] font-semibold tracking-[0.2em] uppercase text-[#E8EAED]">
          Learned tips
        </span>
        <span className="text-[10px] font-mono text-[#6B7280]">
          {tips.length} tip{tips.length === 1 ? "" : "s"}
        </span>
        <span className="ml-auto text-[9px] text-[#4B5563]">
          strategies, recoveries, warnings &mdash; everything the Reflector commits to memory
        </span>
      </div>
      <div className="flex-1 overflow-y-auto px-4 py-3">
        {sorted.length === 0 ? (
          <div className="text-[11px] text-[#4B5563] italic py-8 text-center">
            No tips written yet in this run. They appear here the instant the Reflector adds one.
          </div>
        ) : (
          <div
            className="grid gap-2"
            style={{ gridTemplateColumns: "repeat(auto-fill, minmax(320px, 1fr))" }}
          >
            {sorted.map((t) => (
              <TipCard key={t.id} tip={t} now={now} isLatest={t.tsMs === latestTsMs} />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

function TipCard({ tip, now, isLatest }: { tip: TipEntry; now: number; isLatest: boolean }) {
  const age = Math.max(0, now - tip.tsMs);
  const fresh = age < FRESH_WINDOW_MS;
  const freshFactor = fresh ? Math.max(0, 1 - age / FRESH_WINDOW_MS) : 0;
  const color = TIP_COLOR[tip.tipType];
  const borderColor = fresh ? color : "#2A2A2F";
  const bg = fresh ? TIP_BG[tip.tipType] : "#0F1217";
  const glow =
    isLatest && fresh
      ? `0 0 ${8 + 18 * freshFactor}px ${color}${hexAlpha(0.3 + 0.5 * freshFactor)}`
      : "0 1px 2px rgba(0,0,0,0.4)";

  return (
    <div
      className="rounded-md border px-3 py-2.5"
      style={{
        borderColor,
        background: bg,
        boxShadow: glow,
        transition: "border-color 600ms, background 600ms, box-shadow 600ms",
      }}
    >
      <div className="flex items-center gap-2 mb-1.5">
        <span
          className="inline-block w-1.5 h-1.5 rounded-full"
          style={{
            background: color,
            boxShadow: fresh ? `0 0 6px ${color}` : undefined,
          }}
        />
        <span
          className="text-[9px] font-mono uppercase tracking-wider px-1.5 py-0.5 rounded-sm"
          style={{ background: `${color}22`, color }}
          title={`tip_type = ${tip.tipType}`}
        >
          {TIP_LABEL[tip.tipType]}
        </span>
        {tip.category && (
          <span
            className="text-[9px] font-mono uppercase tracking-wider px-1.5 py-0.5 rounded-sm"
            style={{ background: "rgba(96,165,250,0.12)", color: CATEGORY_ACCENT }}
          >
            {tip.category}
          </span>
        )}
        <span className="ml-auto text-[9px] font-mono tabular-nums text-[#6B7280]">
          {tip.total ? `#${tip.total} \u00b7 ` : ""}at {formatElapsed(tip.elapsed_s)}
        </span>
      </div>

      <div
        className={
          tip.isBanPattern
            ? "text-[11px] font-mono leading-snug break-words"
            : "text-[11.5px] leading-snug break-words"
        }
        style={{ color: TIP_TEXT_COLOR[tip.tipType] }}
        title={tip.text}
      >
        {tip.text}
      </div>

      {tip.triggerConditions && tip.triggerConditions.length > 0 && (
        <div className="flex flex-wrap gap-1 mt-1.5">
          {tip.triggerConditions.slice(0, 4).map((tc, i) => (
            <span
              key={i}
              className="text-[8px] font-mono px-1 py-0.5 rounded-sm"
              style={{ background: "#15181F", color: "#9CA3AF", border: "1px solid #23262E" }}
              title={tc}
            >
              {tc}
            </span>
          ))}
          {tip.triggerConditions.length > 4 && (
            <span className="text-[8px] text-[#6B7280]">
              +{tip.triggerConditions.length - 4}
            </span>
          )}
        </div>
      )}

      {tip.ruleId && (
        <div
          className="text-[9px] font-mono text-[#4B5563] mt-1.5 truncate"
          title={tip.ruleId}
        >
          from {shortRuleId(tip.ruleId)}
        </div>
      )}
    </div>
  );
}

function hexAlpha(a: number): string {
  const v = Math.round(Math.max(0, Math.min(1, a)) * 255);
  return v.toString(16).padStart(2, "0");
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
  const { tips, reflections, crossRun, countsByType } = useMemo(
    () => deriveMemory(events),
    [events],
  );

  // Drive the fresh-glow animation for recent tip/reflection entries.
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const anyFresh =
      tips.some((t) => Date.now() - t.tsMs < FRESH_WINDOW_MS) ||
      reflections.some((r) => Date.now() - r.tsMs < FRESH_WINDOW_MS);
    if (!anyFresh) return;
    const t = setInterval(() => setNow(Date.now()), 500);
    return () => clearInterval(t);
  }, [tips, reflections]);

  return (
    <div className="flex-1 flex flex-col overflow-hidden bg-[#0B0D11]">
      <TopStrip
        crossRun={crossRun}
        tipsThisRun={tips.length}
        countsByType={countsByType}
        reflectionsThisRun={reflections.length}
      />
      <div className="flex-1 flex overflow-hidden min-h-0">
        <TipsPanel tips={tips} now={now} />
        <ReflectionsPanel reflections={reflections} now={now} />
      </div>
    </div>
  );
}

# Journal style — how entries should read

Read this before writing a new entry. The voice pass across 34 entries
(April 17, 2026) consolidated these rules; they exist so the 35th entry
doesn't drift.

## The one-line test

Every entry earns its place by answering a specific question. If you can't
state the question in one sentence, the entry isn't ready. The `one_line`
frontmatter field is the question's answer compressed to a paragraph —
write it first, not last.

## Voice

The target, verbatim from the conversation that set it:

> **Dan Luu's observational restraint + Andy Weir's predicament-first
> immediacy + Matt Levine's willingness to name the absurd out loud,
> written by a senior practitioner for other senior practitioners.**

Three influences, each doing a specific job:

- **Dan Luu is the spine** — observational restraint. Don't tell the
  reader what to feel. Don't editorialize. Describe the data, the
  decisions, the consequences, and let the reader draw the conclusion.
  Section headers are punchlines (`## The hypothesis was wrong in a
  specific way`), not scaffolding (`## What we expected`).
- **Andy Weir gives you the openings** — drop the reader into the
  smallest concrete failure, let the stakes be felt before they're named.
  "I thought the reactor would cool. It didn't. Now my biggest problem
  is that the atmosphere is turning to fluorine." Entry 28 does this:
  `Fix rate 56.18% — 3.4 percentage points below Run 3.`
- **Matt Levine gives you permission to be funny when something is
  genuinely absurd** — not a personality blogger, not jokes-for-jokes'-sake,
  but when the Architect reads a prompt that says "process this rule LAST"
  and then picks it second, you're allowed to notice that is absurd.

**What we're not.** Not Gwern (too encyclopedic, too footnote-heavy for
a reader on a plane). Not pure Weir (we don't have an astronaut dying; we
have a system getting dumber — close enough to borrow the structure, not
the vibe). Not pure Levine (we're not personality bloggers). Not Gladwell
(not voice-driven writers whose sentences are the product).

**Narrator voice, usually "we", occasionally "I" for first-person
decisions.** Never "Ken" in third person. When the decision was genuinely
one person's call, "I" is fine ("I built a reflexion loop with a 3-retry
cap because..." — entry 13). When the work was collaborative or the
pronoun would distract, "we" or an implicit narrator.

**Specifics over abstraction.** Numbers, file paths, function names,
commit hashes. "56.3% fix rate" beats "a regression." "audit_rules_immutable
was picked at position 11/83" beats "the Architect picked it too early."

**Honesty about failures and surprises.** If a bet lost, grade it lost.
If the number landed outside the predicted band, say so. The credibility
of the entry is the credibility of the project.

## Structure

**Predicament-first opening.** State the problem or tension in the first
paragraph, not "in this chapter we will...". Entry 28 opens with the
verdict (`Fix rate 56.18% — 3.4 percentage points below Run 3`). Entry 13
opens with the trap (`the Reflexion paper uses a fixed retry cap...`).

**No scaffolding H2s.** Don't write `## The story in one sentence` or
`## Why this is its own entry`. Both were purged in the voice pass. If
the entry needs to justify itself, the one-liner already does it.

**Section headers are specific, not generic.** `## The first surprise:
the NVFP4 VRAM math is wrong` beats `## Findings`. `## The bets — scored
later` beats `## Predictions`.

**Pull-quote callouts for the sentence you want remembered.** Use
mkdocs-material's `!!! quote ""` admonition for one-line distillations:

```markdown
!!! quote ""
    Day-0 from one vendor is not Day-0 from the stack.
```

Use sparingly — one or two per entry. Each pull-quote should read clean
out of context. No cryptic koans ("Run the command. Watch the memory."
was cut for this reason). No sales copy ("architect-grade reasoning"
was cut for this reason). No slogans.

**Related links at end.** List prior entries the current one builds on
or responds to, with a short phrase each so the reader knows which link
to follow.

## Frontmatter (required)

```yaml
---
id: journey-NN-short-slug
type: journey
title: "Title without 'Journey:' prefix"
date: YYYY-MM-DD
tags: [L-layer, concept, discovery|decision|postmortem|predictions]
related:
  - journey/NN-related-entry-slug
  - adr/NNNN-adr-slug
one_line: "The compressed-paragraph answer to the question this entry asks."
---
```

Tags roughly cluster into:
- Layer: `L1-data-infrastructure`, `L3-model`, `L4-orchestration`, `UI`
- Concept: `reflexion-loop`, `cross-run-learning`, `memory`, `quantization`
- Shape: `discovery`, `decision`, `postmortem`, `predictions`, `failure-mode`

## What to avoid

- **Time-scale hallucinations.** Always verify actual elapsed time before
  writing "eight weeks later" or "after three days." Entry 26's "eight weeks"
  was actually three days. Use `git log --format='%ai' <hash>` if unsure.
- **"Journey:" title prefix.** Redundant with the `type: journey` frontmatter.
  Cut during the voice pass.
- **Over-long paragraphs.** If a paragraph is more than five sentences, it
  usually wants to be two paragraphs or a list.
- **Emojis.** Never unless you are told to. No emojis in prose, no emojis
  in callouts, no emojis in frontmatter.
- **Dropping jargon without explanation.** "The clutch" gets defined on
  first use. "NVFP4" gets a parenthetical. Assume the reader is smart but
  hasn't memorized the project's vocabulary.
- **Leaving the reader at the cliff.** Every entry ends with either an
  answer (what we did / what we learned / what we'll watch for) or an
  honestly-flagged open question ("Entry 31 will grade the bets").

## Writing cadence

Write entries **as you go**, not at the end. The feedback-note in memory
is explicit about this: "journal is the most valuable artifact; write
entries as we go, include honest failures." Two deliberate patterns:

1. **Prediction entries land before outcome entries.** Entry 30 laid down
   bets for Run 5 before Run 5 completed. Entry 31 grades them. This is
   non-negotiable — a prediction entry written after the outcome is a
   fiction, not a prediction.
2. **Architectural work gets a same-day entry.** If you discover a bug
   in production code, flag the entry in `deferred.md` immediately and
   write the journal entry within 24 hours. The clutch-is-dead-code
   discovery (entry 32) was written the day of discovery for exactly
   this reason.

## Length

No hard limit. Long-form entries (28, 29) run 1200–2000 words when the
material warrants it. Short entries (a gotcha catalog, a build log) can
be 300–500. The test is: does every paragraph earn its place?

## The pain signal

**If a new entry gets written without reading this file first, the voice
drifts.** That's the pain signal. If you're reviewing an entry and it
reads flat, generic, or unconvincing, check whether it opened with
predicament-first, whether the section headers are specific, whether the
callouts earn their admonitions. Drift usually starts there.

## Related

- [`deferred.md`](../deferred.md) — the debt/opportunity registry. Style
  guide follows the same discipline: curate, keep small, pain signal per entry.
- [`drafts/journal-editorial-review.md`](https://github.com/kenrollins/gemma-forge/blob/main/docs/drafts/journal-editorial-review.md)
  — the 2026-04-12 editorial findings that drove the voice pass. Longer
  than this file, organized as a review rather than a guide.
- [`journey/`](journey/) — the 34 voice-passed entries as exemplars.
  Entry 28 (post-mortem shape), entry 13 (decision shape), entry 22
  (research arc), entry 29 (methodology near-miss) cover the main modes.

## Sources for the voice target

- Dan Luu — [danluu.com](https://danluu.com). Engineering postmortems,
  observational restraint, punchline-headers.
- Andy Weir — *The Martian*, *Project Hail Mary*. Predicament-first
  openings; specific consequences before abstract stakes.
- Matt Levine — *Money Stuff* newsletter. Permission to call out the
  genuinely absurd without editorializing about it.

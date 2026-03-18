# Repository Cleanup Implementation Plan

Status: Proposed  
Last reviewed: 2026-03-18

## Purpose

This plan defines a cleanup track to make the repository easier to read,
cleaner to maintain, and more human in tone.

The goal is not cosmetic churn. The goal is to remove residue that makes the
repo feel provisional, overly generated, or harder to trust than it should be.

---

## Cleanup goals

- remove legacy code that no longer carries real compatibility value
- remove outdated design leftovers from docs and product wording
- reduce "AI-like" meta language, scaffolding, and repetitive commentary
- make docs read like product and engineering docs, not implementation logs
- simplify naming where transitional wording has lingered too long
- keep comments useful, brief, and written for humans

---

## What "cleaner" means here

The cleanup track should push the repo toward:

- direct wording
- fewer implementation-history artifacts in public docs
- smaller compatibility surface where possible
- clearer ownership of canonical vs derived material
- comments that explain intent, not obvious code

Examples of what should usually be removed or rewritten:

- phase-tracking language inside public reference docs
- "this was added in phase X" wording outside implementation plans
- meta language like "the goal is not only..."
- over-explained obvious comments
- duplicated compatibility helpers that are no longer needed

Examples of what should stay:

- explicit compatibility notes that matter to users
- migration notes that still protect production safety
- design constraints that prevent real regressions

---

## Cleanup areas

## 1. Public documentation cleanup

Priority:

- API docs
- overview docs
- roadmap docs that are customer- or product-facing

Work:

- remove implementation-phase references from public docs
- simplify intros and section labels
- reduce repetitive status/meta framing
- keep implementation-history details inside improvement plans only

Success criteria:

- public docs read as current reference material, not as work logs

## 2. Code comment cleanup

Work:

- remove comments that restate the code
- rewrite generated-sounding comments into human explanations
- keep only comments that explain intent, edge cases, or non-obvious behavior

Success criteria:

- comments help a teammate understand why something exists

## 3. Legacy compatibility review

Work:

- review compatibility aliases and wrappers
- remove dead compatibility code where clients no longer need it
- keep only compatibility paths with an explicit reason to exist

Success criteria:

- compatibility code is deliberate, not accidental residue

## 4. Naming cleanup

Work:

- remove transitional naming that survived past the transition
- prefer product terms that match the glossary and product contract
- eliminate implementation shorthand from public surfaces where possible

Success criteria:

- the same concept is named the same way across docs, API, and UI

## 5. Structural cleanup

Work:

- identify duplicated helper logic
- identify stale modules or endpoints superseded by blueprint paths
- reduce doc duplication where one canonical doc should exist

Success criteria:

- fewer parallel sources of truth

---

## Execution order

1. Public docs cleanup
2. Naming cleanup
3. Comment cleanup in touched modules
4. Legacy compatibility review
5. Structural cleanup and removals

---

## Initial implementation slices

1. Clean API docs so they stop carrying phase language and implementation
   scaffolding.
2. Clean roadmap and overview docs where product-facing material still reads
   like build notes.
3. Review `apps/flask_api/` for duplicated or stale compatibility helpers.
4. Review the frontend wording for transitional internal concepts that should
   stay internal.
5. Build a removal list for dead compatibility code before deleting anything.

---

## Guardrails

1. Do not remove safety-critical compatibility behavior without evidence.
2. Do not collapse canonical and derived docs into one file unless it clearly
   reduces duplication.
3. Prefer small cleanup passes with verification over sweeping rewrites.
4. Keep implementation history in improvement plans, not in reference docs.

---

## Recommended next step

Start with a repo-wide public-doc cleanup pass, then move to compatibility and
comment cleanup in the API/backend layer.

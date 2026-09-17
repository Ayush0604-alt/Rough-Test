---
name: requirement-and-graph-validation-gates
description: Run deterministic integrity and grounding checks over a code knowledge graph and over generated requirements — duplicate ids, dangling references, orphan endpoints, unresolved or fabricated evidence, missing interaction-boundary coverage — before a requirements document is published. Use to validate, audit, or sanity-check a knowledge graph or a requirements set for hallucination risk.
---

# Requirement and Graph Validation Gates

## Purpose

Independently re-check the graph and the requirements before anything is published, so a structural
mistake or an ungrounded claim fails loudly instead of reaching the final document.

## Role

Two gates, both deterministic — no model reasoning, just checks against facts already established
(the graph, the file system). Gate 1 runs after extraction; Gate 2 runs after requirement synthesis.

## Gate 1 — Graph validation

Run these checks over the extracted graph:

| Check | Failure means |
|---|---|
| Duplicate entity/relationship ids | Extraction produced non-unique identities — a bug, not data |
| Dangling relationship | An edge references an entity id that doesn't exist |
| Self-relationship | An edge's source equals its target (usually a warning, not fatal) |
| Location resolvability | An entity's cited file does not exist under the codebase root, or the line number is invalid |
| Orphan endpoints | An `ENDPOINT` entity with no relationship exposing it — extraction is inconsistent |
| No analyzable source found | Zero files in any language you can actually parse — **fail closed**, don't proceed to fabricate requirements from nothing |
| High ambiguity | More than ~30% of relationships are `AMBIGUOUS` — extraction is guessing more than resolving; flag as a quality warning |
| No file entities at all | Something is badly wrong with ingestion |

Only the checks marked as producing an outright failure should block progress; ambiguity ratio and
self-relationships are warnings, not hard stops.

## Gate 2 — Requirement validation

Run these checks over the synthesized requirements:

**Per-requirement:**
- Unique id.
- Not a near-duplicate of another requirement's statement (compare normalized, whitespace-collapsed
  text).
- Has at least one evidence citation.
- **Every cited file actually exists in the target codebase** — resolve the path and confirm.
- **Every cited line/line-range is plausible against the real file** — open the file and check the
  line count is in range, and if a symbol name was cited, confirm it appears near that location.
  *(Do not skip this step. Checking only "does the file exist" is not enough — a fabricated line
  number inside a real file must still be caught.)*
- Has a non-empty `graph_path` that resolves to real graph node names.
- (Business / functional requirement, success criterion) the traced graph path reaches a real
  interaction boundary.
- (Business / functional requirement, success criterion) at least one citation lands on a
  boundary-adjacent location or supporting documentation evidence — not internal processing alone.
- (Business rule) traces to an explicit rule/policy/config node, not just any conditional.
- Every `parent` names a requirement that actually exists at a tier that tier may hang from, under
  a relation those two tiers may hold. A link that can name no relation is code proximity wearing
  a parent's clothes.
- No item is its own parent, and no chain of parents forms a loop. Both are invisible to a check
  that only looks one link up — every parent exists, nothing is dangling — and both produce a
  document that hangs any consumer walking upward from a leaf.
- Nothing rejected, unresolved, or classified as an implementation observation appears in the
  requirements list.
- Every acceptance criterion belongs to the requirement it hangs from, and cites nothing that
  requirement does not.

**Whole-set / BRD-level:**
- A product purpose was established (or explicitly marked as not established — both are valid
  outcomes, an empty confident-sounding purpose is not).
- At least one business requirement exists, or the document explicitly states why none could be
  grounded.
- Every business requirement has at least one functional requirement realising it, and ideally
  one success criterion validating it.
- Every functional requirement realises a business requirement; every business rule constrains a
  functional requirement. An item attached to nothing above it is a warning, not a rejection: the
  claim itself is grounded, and what is missing is the reader's ability to see why it matters.
- No business requirement is its one functional requirement reworded — same evidence, same words,
  two tiers. That is one finding published twice.
- No statement is duplicated verbatim across sections.

### What a rejection may and may not cost

A gate rejects claims; it must not be the thing that shapes the document.

- A **grounding** failure — no evidence, a citation that does not resolve, a trace reaching no
  boundary, an empty statement — means the claim was never established. Remove it, on the record.
- A **wording** failure — the statement or a criterion names the implementation — means the claim
  *was* established and is described badly. Reword it and record what was removed. Deleting a
  checked, cited, boundary-traced requirement because of one word discards evidence to satisfy a
  linter, and repeated across a document it silently turns a full analysis into a thin one.
- A **structural** failure — a parent that resolves to nothing — is repaired by clearing the link,
  never by deleting the child. The child did nothing wrong; it pointed at something that was
  removed, and whatever removed it owned that link.

Whatever removes an item is responsible for the links into it. A removal that leaves a dangling
parent converts one rejection into a second, and the second falls on an innocent requirement.

## Rules and constraints

- Both gates are deterministic. Don't "reason" your way past a failed check — either fix the
  extraction/synthesis and re-run, or report the failure honestly in the final document.
- A gate failure should **stop and explain**, not silently produce a partial or degraded document
  without saying so.
- An **empty requirement set is a valid, passing outcome** only when you explicitly state why (e.g.
  "no interaction boundaries were found; this appears to be a library, not an application") — never
  let an empty result pass silently as if it were success.
- Warnings (ambiguity ratio, self-relationships, missing acceptance criteria on a rule) do not need
  to block the whole run, but must be visible in the final gap-analysis / limitations section.

## Output

A short validation summary: `{valid: bool, errors: [...], warnings: [...]}` for each gate, to include
in the final deliverables (see `traceable-requirements-deliverables`).

## Common failure modes to watch for specifically

- A requirement whose only "evidence" is a README line, with no corresponding code path — reject it,
  it's aspirational documentation, not implemented functionality.
- A requirement grounded in a function that is never called by anything reachable from a boundary —
  looks plausible, isn't actually reachable; reject.
- A cited line number that's technically inside the file but nowhere near the claimed symbol —
  catch this by actually opening the file, not by trusting the graph's location field blindly.
- Two requirements describing the same outcome with slightly different wording — de-duplicate by
  meaning, not just exact string match, when it's clearly the same claim.

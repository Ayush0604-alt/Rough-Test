---
name: test-coverage-and-duplication-gates
description: Run deterministic quality, duplication, contradiction, and coverage checks over a generated test suite before it is published — vague expected results, cases restating their requirement, missing preconditions and inputs, unsupported assumptions, framework leakage, duplicate and overlapping cases, uncovered acceptance criteria, and requirements with only happy-path coverage. Use to validate or audit a test specification before anyone relies on it.
---

# Test Coverage and Duplication Gates

## Purpose

Independently re-check the suite before anything is published, so a case that checks nothing fails
loudly instead of reaching a test plan and being trusted.

## Role

Deterministic. No model reasoning — just checks against facts already established: what the cases
say, what the requirements said, and what the analysis decided each requirement was worth.

Three stages, each seeing something the others cannot:

1. **Deduplication**, before the gate, because a duplicate is arithmetic rather than judgement.
2. **The gate**, which judges cases one at a time and can send failures to a bounded repair pass.
3. **The final check**, after everything else has stopped moving, which is the only stage that can
   see properties of the whole.

## Deduplication

Compare cases **only within one requirement and one dimension**. Two cases about different
requirements are not duplicates however similar their wording — they are evidence about different
things, and folding them silently removes a requirement's only coverage.

Compare **steps and expected results, never titles.** The title is the most likely thing to differ
between two cases that check exactly the same behaviour, so comparing titles misses the duplicates
that matter most.

When two cases are duplicates, keep the better-grounded one: a case tied to a stated acceptance
criterion beats one resting on the statement; a direct derivation beats an assumed one; more
expected results beats fewer. Record the folded id on the case that absorbed it. **Nothing is
deleted** — a merge must be visible, or it looks like a case went missing.

Cases that *overlap* without being duplicates are reported and both kept. Merging them is a
judgement about what the product means, which wording alone cannot settle.

## The gate

Every check answers one question: **would this case fail against a broken implementation?**

| Code | Severity | What is wrong |
|---|---|---|
| `TC_NO_REQUIREMENT` | error | Traces to nothing, so nothing it finds could be acted on |
| `TC_LINKED_TO_EXCLUDED` | error | Validates a requirement excluded from generation |
| `TC_NO_OBJECTIVE` | error | States no reason for existing |
| `TC_NO_STEPS` / `TC_NO_EXPECTED_RESULT` | error | Cannot be run, or can neither pass nor fail |
| `TC_VAGUE_EXPECTED_RESULT` | error | "Works correctly", "no errors occur" — passes against a broken implementation |
| `TC_RESTATES_REQUIREMENT` | error | Repeats the requirement instead of saying how to check it |
| `TC_MISSING_INPUTS` | error | A validation, boundary, negative, or authorization case names no input, though those dimensions *are* about the input |
| `TC_MISSING_PRECONDITION` | error | Acts on something that must already exist without establishing it |
| `TC_UNSUPPORTED_ASSUMPTION` | error | Derived indirectly with no assumption recorded |
| `TC_FRAMEWORK_LEAK` | error | Names a tool, framework, protocol verb, or language |
| `COV_POSITIVE_ONLY` | warning | States a refusal or a limit; only the happy path is tested |
| `COV_REQUIREMENT_UNTESTED` | warning | Planned for and received no case |
| `COV_CRITERION_UNCOVERED` | warning | An acceptance criterion nothing checks |

### Why coverage findings are never errors

The repair pass sees **only the rejected cases and the issue list** — never the requirement set,
never the source. It can reword a case; it cannot design one that does not exist. A coverage gap
therefore has no fix available to the step that would receive it, and making it blocking would
spend the entire repair budget failing at something rewording cannot touch.

Errors are exactly the findings a repair pass could act on. Everything else is reported for a
person, which is the honest place for it.

## What a rejection may and may not cost

- A **wording** failure — vague, restating, leaking a framework — means the case was worth writing
  and is described badly. Reword it. Deleting a traced, grounded case over one phrase discards real
  coverage to satisfy a linter.
- A **grounding** failure — no requirement, an excluded requirement — means the case was never
  established. Remove it, on the record.
- A **missing part** — no precondition, no input, no assumption — is supplied, not deleted. What is
  missing is a sentence, and the behaviour under test is still worth checking.

When the repair budget is spent, what still fails is pruned deterministically and each pruned case
becomes a recorded gap. A thinner honest suite beats a larger wrong one.

## The final check

Run once, when nothing is going to move again. Properties of the whole are invisible until then:
a duplicate id, a case pointing at a scenario that no longer exists, a case with no coverage link,
and — the one nothing upstream can see — **a requirement that had cases when coverage was measured
and has none now**, because they were folded, repaired away, or pruned afterwards.

These are reported rather than corrected. A suite that quietly repairs itself at the last moment is
one nobody can reason about.

## Rules and constraints

- Deterministic throughout. Do not reason past a failed check.
- Sufficiency is measured against the dimensions a requirement **activated**, never the full
  vocabulary. Otherwise the report demands an authorization test for a requirement that mentions no
  access condition, and the only way to satisfy it is to invent one.
- **Do not optimise for the number of cases.** A suite that shrinks because duplicates were folded
  and empty cases pruned has improved.
- An empty or thin suite is a valid outcome when the report says why.

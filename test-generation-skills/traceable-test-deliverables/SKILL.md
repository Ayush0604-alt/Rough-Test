---
name: traceable-test-deliverables
description: Render a validated test specification into the documents its readers need — a machine-readable spec set, a human test plan, a coverage matrix, a flattened traceability chain, and honest reports of what will not be tested and what has to be answered first. Use when publishing generated test cases for review, automation, or a test-management tool.
---

# Traceable Test Deliverables

## Purpose

Publish the finished suite in the forms its different readers actually use, and make what is *not*
covered as visible as what is.

## Role

Deterministic, and last. Every stage before this changes the suite; this one only reports it. It
**always runs**, including when little or nothing qualified — a thin suite that explains itself is
a result, and a thin suite that does not is a failure wearing a result's clothes.

## The six documents

| File | Reader | Holds |
|---|---|---|
| `test_specs.json` | an adapter, a test-management tool | every case, self-contained, in a stable key order |
| `test_plan.md` | a person | the summary, what to read first, then the cases grouped by area |
| `coverage_matrix.csv` | a reviewer, a spreadsheet | one row per requirement — covered or not |
| `traceability_tests.csv` | a traceability tool | requirement → criterion → case, one fact per row |
| `excluded_requirements.md` | whoever owns the requirements | everything that will not be tested, grouped by why |
| `clarifications.md` | the same person | the questions blocking further coverage |

## Rules the shape follows

**The JSON is a flat array of self-contained cases, not a nested tree.** Every downstream use
addresses one case by its id, and a consumer that must walk to a parent to find a case's
preconditions will eventually forget to. Each case therefore carries its scenario's context and
preconditions merged in. Grouping stays fully recoverable from `scenario_id`.

**The traceability CSV is exploded, not nested** — one row per (case, requirement, covered thing).
A spreadsheet, a traceability tool, and a reviewer filtering by requirement all want one fact per
row.

**A requirement with no case still gets a row.** A traceability matrix listing only what was tested
cannot answer the question it exists to answer.

**Advisory metadata is published but never acted on.** `interface_hint`, `automation_readiness`, and
each input's `value_kind` exist so a later adapter has something to read. Nothing in this pack
branches on them, and if anything ever does, the technology-independence claim is gone.

## What the plan leads with

Not the case count. A reader needs, in this order:

1. **`positive_only` requirements.** The coverage failure that hides rather than announces itself:
   the requirement states a refusal, an error, or a limit, only the happy path is tested, and it
   reads as covered.
2. **Acceptance criteria nothing checks.** Each is a condition somebody wrote down and nothing
   verifies.
3. **What the quality gate reported** but did not block on.
4. Then the cases, grouped by area so a reader works through one part of the product at a time.

## Verify at the moment of publishing

Re-check that no case names a framework, tool, protocol verb, or language. The gate checked this
upstream, but `test_specs.json` is the file that leaves the building and the one an adapter will
consume, so it is checked again as it is written. A leak here is reported, not silently corrected —
a document that quietly repairs itself at the last moment is one nobody can reason about.

## Rules and constraints

- Say what is missing as plainly as what is present. Half the value of the pack is the gap report.
- Never inflate. A published case count that includes cases the gate rejected would be a lie about
  the suite's quality.
- An empty suite is a valid outcome **when the documents say why**, requirement by requirement.

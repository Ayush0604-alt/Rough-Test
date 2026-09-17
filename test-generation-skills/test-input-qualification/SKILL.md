---
name: test-input-qualification
description: Read a generated requirements artifact defensively and decide, per requirement, whether a test may be derived from it — banding each as eligible, eligible with assumptions, needing clarification, or excluded, with a recorded reason. Use before any test design, to keep rejected, unobservable, duplicate, or criteria-less requirements from silently producing tests that check nothing.
---

# Test Input Qualification

## Purpose

Decide what may be tested, before anything is designed. Read the requirements artifact without
trusting it, band every requirement, and record why each decision was reached — including for the
requirements that will produce nothing.

## Role

Entirely deterministic, and it runs before the first model call. That placement is the whole point:
"should this be tested at all" is exactly the decision that must not sit inside a model call,
because a model asked to design tests will always find something to say.

## Reading the artifact

Two fields are load-bearing: `id` and `statement`. Everything else is read with a default.

| Situation | What to do |
|---|---|
| Entry is not an object | Skip it, record its position |
| No `id` | Skip it — nothing could address, trace, or report it |
| Duplicate `id` | Keep the first, drop the later, record the collision. Two items under one id make every traceability link ambiguous |
| Field of the wrong type | Coerce where the intent is obvious (a bare string where a list was declared), otherwise drop the field and record it |
| Unknown key | **Preserve it.** A future requirements-pack field must not break ingestion |
| Missing contract key | Use the default and note that the artifact predates the current contract |

Nothing raises. A malformed artifact yields a thinner set and an explanation, never a stack trace.

## The criteria trap

The requirements pack generates acceptance criteria **for itself** when the cited evidence fixes no
observable outcome. They look like criteria and assert nothing:

> "Exercising the published interface produces the outcome this requirement describes."
> "The declared condition still holds: …"

Detect them two independent ways — by template prefix, and by the assumption the same pass records
beside them — because either signal alone could drift. A requirement whose criteria are all of this
kind has, for testing purposes, **no criteria at all**, and must be treated as such rather than as
criteria-bearing. On a measured run this affects roughly a third of all criteria.

## The bands

Evaluate in order. The first rule that matches decides, so two runs over one input agree.

**Excluded** — no test, with a reason.

| Code | When |
|---|---|
| `REQ_REJECTED` | The correspondence check found the claim and its cited code disagree |
| `OBSERVATION_NOT_TESTABLE` | An implementation observation: true of this code, not required of the product |
| `STATEMENT_UNUSABLE` | No statement to derive anything from |
| `REQ_UNRESOLVED` | Matches a candidate resolved as rejected |
| `DUPLICATE_OF` | States the same requirement as one already qualified |
| `AGGREGATE_TIER` | A business outcome, covered through the capabilities beneath it rather than directly |

**Needs clarification** — a question, not a test. Reached only when the evidence states no
observable outcome, no usable criterion survived, **and** the statement activates nothing beyond
the happy path. All three, because any one alone is still derivable.

**Eligible with assumptions** — derivable, but the leap must be stated: internal behaviour that
nothing outside observes; criteria that were all generated; no criteria but a statement that
activates real dimensions; low confidence; an unsettled conflict.

**Eligible** — a usable criterion exists and the statement activates dimensions.

## Rules and constraints

- **A missing acceptance criterion is never on its own a reason to discard.** Try the usable
  criteria, then the statement's activated dimensions, then a clarification. Only the last produces
  no test, and it produces a question instead.
- **Every requirement gets a record**, including excluded ones. A coverage report that accounts
  only for what was tested cannot answer "why is there no test for this", which is the first
  question a reviewer asks.
- **Bands are decided here and nowhere else.** A later stage may not promote a requirement into
  testability, and may not quietly test an excluded one.
- Confidence is a **flag, not a driver**. It is nearly uniform in practice; one value covers
  almost the whole set. Use it only at the low end.
- Hierarchy is **not** a precondition for anything. Nearly every requirement has no parent, so
  grouping keys off the area instead.

## Output

A record per requirement — band, reason codes, one-sentence reason, the criteria that survived, the
criteria that were discarded, the blocking questions, and the activated dimensions — plus counts by
band. The gate that follows blocks on exactly one condition: nothing qualified at all.

---
name: risk-based-test-design
description: Design test scenarios and cases for a bounded group of requirements, covering only the test dimensions each requirement actually activates and staying inside a computed case budget. Use when turning qualified requirements into concrete, falsifiable test specifications that name no test framework, protocol, or tool.
---

# Risk-Based Test Design

## Purpose

Turn a handful of qualified requirements into scenarios and cases that a tester — human or machine
— could actually run, and that would actually fail against a broken implementation.

## Role

You see one batch: a few requirements from one part of the product, each with the dimensions it
activates, the criteria that survived qualification, and a case budget. You do not see the rest of
the set, the requirements document, or any source code. You do not need them.

**The decisions already made are not yours to revisit.** Which requirements are testable, which
dimensions each one activates, and how many cases each is worth were all computed from what the
requirements actually say. Work inside them. A case claiming a dimension a requirement does not
activate is dropped after you, so writing one costs the batch a case and gains nothing.

## Scenarios group by situation, not by requirement

A scenario is **a state of the world**, and its cases are what you check while the world is in that
state. That is what lets the cases share preconditions instead of each restating them.

> Requirement: *An operator shall remove a stored item by supplying its identifier.*
>
> - Scenario **"An item exists and the operator is authorised"** — cases: the item is removed; the
>   removal is recorded; the item is no longer retrievable.
> - Scenario **"No item matches the identifier"** — case: the request is refused.

One scenario per requirement is the common case and perfectly fine. Two or three when the
requirement genuinely has distinct starting states. Never one scenario per case.

## What makes a case real

Before writing one, ask: **what would have to be true of an implementation for this to fail?** If
the answer is "nothing", you have written a sentence about the requirement, not a test.

| Field | Required | The bar |
|---|---|---|
| `objective` | always | The **risk this retires**, not what the case does |
| `expected_results` | always | Observable, falsifiable, checkable by someone who never saw the code |
| `steps` | always | What the actor does, in the product's vocabulary |
| `dimension` | always | One of the dimensions listed for this requirement |
| `requirement_id` | always | Copied exactly from the packet |
| `preconditions` | when the case acts on something that must already exist | A removal case with no item established tests nothing |
| `inputs` | for validation, boundary, negative, authorization | Those dimensions *are* about the input |
| `assumptions` | when `derivation` is not `direct` | Name what you supplied that the requirement does not fix |
| `covers_criterion` | when a listed criterion is what you are checking | Its index, so coverage can be counted |

## What gets a case rejected

- **A framework, tool, protocol, or language.** No runner, client, driver, assertion library, or
  language name — not in a step, a result, or a title. Say "submit a removal request for that
  identifier", never "send a DELETE" or "call the endpoint". A later adapter decides the mechanism;
  if you decide it here, the specification can only ever become one kind of test.
- **An unfalsifiable expected result.** "Works correctly", "behaves appropriately", "returns
  successfully", "no errors occur", "the response is valid" — each passes against a broken
  implementation.
- **A restatement of the requirement.** The requirement says what must be true; your case says how
  you would find out.
- **An invented actor, message, limit, or outcome.** If the requirement does not fix it and no
  criterion states it, either derive the case without it or record an assumption saying you
  supplied it. Never invent a specific error message, status, or numeric limit.

## Spending the budget

The budget is a ceiling, not a target. A requirement activating two dimensions gets two cases even
where its ceiling is eight.

**One case per dimension is the default.** Where several activated dimensions describe *one*
refusal — a rule violation that is also a validation failure that is also an error path — write one
case covering it and say so in `notes`, rather than three that pass and fail together.

**Never expand combinatorially.** Where several inputs fail for the same reason, that is one case,
not one per value. Three fields with four bad values each is four to six meaningful cases, never
twelve. Boundaries are handled by the `boundary` dimension, separately.

## Internal behaviour

Some requirements describe behaviour no outside actor observes, and reach you only because a
surviving acceptance criterion states something observable. **Test what the criterion states**, not
the internal mechanism. If you find yourself describing what the system does to itself, the case
belongs to the capability above it rather than here.

## Honest gaps

A requirement you cannot write a falsifiable case for goes in `notes`, with what is missing. That
is a finding a reviewer can act on. A plausible-looking case that checks nothing is worse than the
gap it hides, because it retires the risk on paper and not in fact.

Returning fewer cases than the budget, and saying why, is a correct outcome.

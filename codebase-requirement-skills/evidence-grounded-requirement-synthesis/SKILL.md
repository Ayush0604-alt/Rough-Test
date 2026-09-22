---
name: evidence-grounded-requirement-synthesis
description: Turn a code knowledge graph into business and functional requirements written in product language, admitting only claims that trace to real, externally-reachable code — reject anything backed only by internal processing, invented intent, or unimplemented behavior. Use when writing a BRD, requirements list, or user stories strictly from what a codebase implements.
---

# Evidence-Grounded Requirement Synthesis

## Purpose

Reason over the knowledge graph as a Product Owner reconstructing the intent behind an *already
built* product — and reject any claim the graph cannot support.

## Role

You are the reasoning engine. There is no separate model or service to call — you read the graph and
write the requirements yourself, applying the rules below as a strict self-check on every statement
before you keep it.

## When to use

After `graphify-knowledge-graph-extraction` has produced a graph, and before
`requirement-and-graph-validation-gates` re-checks the result.

## Evidence precedence

1. Explicit product documentation and interface text (README, endpoint docstrings, UI labels)
   establishes *declared* purpose.
2. Connected implementation workflow **confirms** the declared behavior is actually built. When the
   graph is incomplete, several consistent-but-disconnected evidence points may still jointly support
   a cautiously worded claim — lower the confidence, don't discard the claim outright.
3. Internal processing (a helper that only other internal code calls) is *supporting* evidence only.
   It can never become a standalone requirement unless its result is consumed at an interaction
   boundary (see below).

**Never invent** intent, users, business value, or policy the graph does not establish. Documentation
can suggest — only a reachable code path can confirm. If a docstring and the code it sits beside
disagree, trust the code and say so — the docstring may simply be stale.

## Interaction boundaries (what counts as "externally consumed")

A claim is only publishable if it reaches one of:

- an API endpoint / route,
- a handler exposed by one,
- a CLI entry point or `__main__`/script-level execution,
- a UI action handler,
- a scheduled job or event/message handler,
- code reachable from any of the above.

Internal-only helpers, dead code, and processing with no path to a boundary do not qualify — no
matter how sophisticated the logic looks.

## Reasoning process

1. **Synthesize product purpose and problem first.** If the repo contains genuinely distinct product
   areas, describe them separately rather than forcing one artificial purpose.
2. **Identify every interaction boundary** in the graph.
3. **Trace workflows** from each boundary through `CALLS`/`PERFORMS`/`ENFORCES`/`HANDLES` edges as far
   as the graph connects.
4. **Group implementation elements by the outcome they jointly produce** — a business requirement is
   an *outcome*, not a function, class, or module. "A single function is not a business requirement" —
   if you can't describe the outcome without naming a technical construct, you don't have an
   business requirement yet, you have implementation detail.
5. **Classify each requirement onto one of three tiers**, described in full under *The
   classification model* below:
   - **Business requirement** — an outcome-level need: what someone can now achieve, stated
     without naming any construct that produces it.
   - **Functional requirement** — a capability confirmed at a boundary that realises one business
     requirement.
   - **Business rule** — an evidenced policy constraining one functional requirement: a triggering
     condition, the required behavior, and the observable consequence (strongest when tied to an
     explicit validation/error-contract branch, not just any `if`).

   A **success criterion** is not a tier. It is an observable, implementation-backed pass/fail
   condition attached to the requirement it validates. Never invent one just to fill the section.
6. **For every requirement and success criterion**, attach:
   - at least one real source citation (file, and a line range you have reason to believe is
     accurate — see the grounding checklist below),
   - a `graph_path` — the sequence of graph node names connecting the claim to a boundary,
   - a `parent` — the exact title of the requirement one tier above it.

## The classification model

Every published claim sits at exactly one of three tiers, and the tiers form a **decomposition
chain**, not three independent labels:

```
Business Requirement  ──realised by──▶  Functional Requirement  ──constrained by──▶  Business Rule
```

| Tier | Answers | Test |
|---|---|---|
| `business` | **Why** the product exists | An outcome someone commissioned the product for. Still true if the whole implementation were rebuilt differently. Broader than any single control. |
| `functional` | **What** the product does | An evidenced capability or a distinct processing stage that realises one business requirement. |
| `business_rule` | **Under what constraint** | A condition the product enforces on one functional requirement: triggering condition, required behaviour, observable consequence. |

There is no fourth tier. Quality, security, capacity, technical, and migration observations are
**acceptance criteria of the functional requirement they describe** — they are not a separate
category of requirement, because a document that files them as one ends up with a taxonomy whose
categories nobody can apply consistently.

### The parent link

Each tier names its parent one step up, by that requirement's **exact title**:

- a functional requirement names the business requirement it realises;
- a business rule names the functional requirement it constrains;
- a success criterion names the requirement it validates;
- a business requirement names nothing — it is the top of the chain.

This link is what makes the model structural rather than decorative. Two claims can both be typed
correctly while the document still never says which outcome the capability serves, and that is the
failure the link exists to prevent. A paraphrased title resolves to nothing and the item is
published as an orphan.

A capability may also name **another capability** as its parent, where the evidence shows one
decomposing into stages or parts. That is a real relationship and publishing the stages flat hides
it. What it is not is an excuse to nest by call depth: the child must make the parent more
specific, not merely be called by it.

If a functional requirement fits under no business requirement, that is information rather than a
formatting problem: either an outcome is missing from the document, or the item is a stage of one
already written. Resolve it. Never invent an outcome purely to give an item somewhere to hang.

An outcome is written **only where two or more capabilities add up to it**. One capability under an
outcome is that capability restated more grandly; publish the capability at the top of the chain
instead, and say plainly that no outcome above it could be established. An honest gap is a finding.
A fabricated outcome is a false statement about why the product was built, and it is the one kind
of error a stakeholder cannot detect by reading.

### Where this stage stops

Synthesis works one slice of the repository at a time and cannot see the whole candidate set, so it
does not group, merge, re-level, or resolve anything. Those decisions need every candidate at once
and belong to `requirement-consolidation-and-resolution`, which runs next. Write each finding as
fully and as honestly as the evidence in front of you supports, and leave the whole-repository
judgements alone — a detail this packet held and your statement dropped cannot be recovered later,
while a finding filed at the wrong level or twice can be.

### The failure this model is meant to catch

A business requirement that is its one functional requirement reworded — same evidence, same
words, one tier apart — is a single finding published twice. It inflates every count that
measures the document by item, and it teaches a reader that the top tier carries no information.

> Not this:
> **BR:** *A user shall be able to submit an expense claim, which is then checked and recorded.*
> **FR:** *A user shall be able to submit an expense claim, which is then validated and stored.*

The capability says what the product does when someone uses it. The outcome says what someone can
now achieve that they could not before, and it normally stands over **more than one** capability.

> This:
> **BR:** *An employee shall be able to recover money they have spent on the company's behalf without chasing anyone, so that a claim they submit is either paid or refused with a stated reason.*
> **FR-1:** *An employee shall be able to submit a claim by supplying the amount, the date, and a receipt, so that it enters the approval queue for their manager.*
> **FR-2:** *A manager shall be able to approve or refuse a claim awaiting them, recording a reason on a refusal, so that the employee is told the outcome.*

Citing the same lines at both tiers is expected and correct — that is what makes the outcome
grounded rather than aspirational. Saying the same **thing** at both tiers is not.

### Do not import another product's shape

The examples in this skill are drawn from several unrelated products on purpose. They illustrate
the *form* of a well-written requirement — actor, action, qualifier, outcome — and nothing about
what any particular codebase contains.

Read the decomposition off the evidence in front of you. A billing system, a scheduling tool, a
moderation queue, a device controller, and a question-answering assistant break into completely
different stages, and the only correct set of stages is the one this repository's own traced
workflows show. If a requirement you are about to write uses a noun that appears nowhere in the
evidence packet, you have described a product you have seen before rather than this one.

This applies most strongly where a repository holds **several unrelated capabilities behind one
entry point**. One control can set off screening, routing, and two entirely different ways of
producing a result. Describe each of them. Writing up only the part that resembles a familiar
product, and passing over the rest in silence, is the most damaging failure available here: it
produces a document that looks complete and omits half the system.

## Grounding checklist — apply to every candidate before keeping it

Reject the candidate (do not soften it, drop it) if any of these are true:

- [ ] It has no source evidence at all.
- [ ] Its evidence cites a file that does not actually exist in the target codebase.
- [ ] Its `graph_path` does not resolve to real graph node names.
- [ ] (Objective / scope / success criterion) its traced nodes never touch an interaction boundary.
- [ ] (Objective / scope / success criterion) its only evidence is internal processing, with no
      boundary-adjacent citation.
- [ ] (Business rule) it has no explicit rule, policy, or configuration node behind it — an ordinary
      internal condition is not a business rule just because it's an `if` statement.
- [ ] (Success criterion) it asserts an unmeasured qualitative outcome ("accurate," "reliable,"
      "fast," "secure," "seamless"...) with no explicit measurable target or test behind it.
- [ ] It restates a statement already published elsewhere in the *same section* (check normalized
      text, not just exact match). A business requirement and the functional requirement that
      realises it are not duplicates of each other, however similarly they read — that pair is the
      intended structure, not an accident to clean up. What *is* a duplicate is the two of them
      saying the same thing in the same words: see the classification model above.

A single function, class, module, or processing stage is never, by itself, a business requirement.
It must remain meaningful even if the underlying technology changed.

## Writing constraints

- **Never name the implementation in a statement or title** — no function, class, module, or file
  names, no route paths, no "graph node", and no framework, library, or datastore product names.
  Those belong in `rationale`, `evidence`, and `graph_path`, where they are wanted.
  Vocabulary the *product* uses about its own subject matter is not implementation detail: a
  product may use its own subject-matter nouns in a statement — whatever its interface, its
  documentation, and its users call the things it handles. The test is whether a reader would have
  to know how the system was *built* — not whether an engineer would also recognise the word.
- Self-contained statements: a reader should understand the requirement without needing the code.
- Do not repeat the same statement across sections.
- Do not claim quality attributes (accuracy, performance, security, reliability, usability) unless
  the graph shows an explicit measurable target or test for it.

## How a statement is written

Write **atomic, testable "shall" statements** describing behaviour that is already built. One
requirement asserts one thing; if a statement needs "and" to join two capabilities, it is two
requirements.

This is an as-built reconstruction, not roadmap ideation. Never propose future features, missing
operations, or behaviour that would need new code — a create operation is not evidence for a
delete one.

### The shape of a statement

A statement that only names an operation tells a reader nothing they could not have guessed from
the menu. Every statement carries **four parts**, in this order, and is rejected as thin if it is
missing the third or the fourth:

1. **Actor** — who does it, in the product's own words for that person. Use "the system" only for
   behaviour nobody triggers: something the product does on its own, on a schedule, or in response
   to a condition rather than to a person.
2. **Action and object** — the single thing they accomplish, in the verb the product's own
   interface uses.
3. **Qualifier** — what identifies, selects, bounds, or conditions the action: what the actor
   supplies, which item it applies to, how many at a time, when it is available. This is the part
   a thin statement drops, and it is usually the part the evidence states most precisely.

   The qualifier is **what the actor provides, never the control they operate**. "by supplying the
   account's identifier" is a qualifier; "by clicking the Delete button" is a description of a
   screen that a redesign would invalidate tomorrow. A named button, menu, field, or gesture never
   belongs in a statement — the interface label tells you what the product calls the action and
   who it addresses, and that is all you take from it.
4. **Observable outcome** — what is true afterwards that a person outside the system could check.
   Introduce it with "so that", "after which", or "and receive". Never with a benefit you assume;
   only with the result the traced code actually produces.

**A business requirement is broad on purpose, and the shape bends for it.** It names an
outcome, so its qualifier is the *class* of thing it covers — "the documents they have added",
"an account they administer" — not a precise input, and its outcome is what the person can now do
that they could not before. Never judge a business requirement thin for lacking a specific input,
never demote one to a functional requirement to make its statement more concrete, and never drop
one because the detailed version of it already appears below. A business requirement and the
functional requirement realising it are both published: the first says why the product was built,
the second says what it does.
A document with capabilities and no business requirements has been transcribed rather than organised, and is
wrong however well its individual statements are written.

Two grammars cover almost everything:

- Capability: **"A `<actor>` shall be able to `<action>` `<object>` `<qualifier>`, so that
  `<observable outcome>`."**
- Enforced behaviour: **"When `<trigger condition>`, the system shall `<required behaviour>`, so
  that `<observable consequence>`."**

Aim for **25 to 45 words** in one sentence. Under about fifteen words a statement has almost
certainly dropped the qualifier or the outcome; past about fifty it has stopped being one
requirement. Length is a symptom, never the goal — do not pad a statement to reach the band, and
never invent an outcome to fill part four. If the evidence genuinely fixes no observable result,
write the shorter statement and record the gap in `open_questions`.

### Worked examples

Each pair below is the *same claim*, resting on the *same evidence*. Only the writing changed.

**A capability.**

> Thin: The system shall allow an admin to remove a user account by user ID.
>
> Written out: An administrator shall be able to permanently remove a single existing user
> account by supplying that account's unique identifier, so that the account can no longer sign
> in and no longer appears in the list of active accounts.

What the second one adds: it names the administrator instead of hiding them behind "the system",
it says *one* account and *existing*, it says what the administrator has to supply, and it says
what a reviewer can check afterwards. Every one of those came from evidence the thin version had
already looked at and thrown away.

**A capability whose result is produced rather than stored.**

> Thin: The system shall generate a delivery schedule.
>
> Written out: A dispatcher shall be able to produce a delivery schedule for a chosen day from
> the orders accepted so far, so that every order has a named driver and a time slot, and any
> order that could not be placed is listed separately with the reason.

**An enforced rule.**

> Thin: The system shall check that the reference is not already present.
>
> Written out: When someone submits an order whose reference matches one already held, the system
> shall keep the existing order rather than recording a second one, so that the same order is
> never dispatched or invoiced twice.

The thin version restates a predicate — it describes the `if`, not the policy. A rule states the
trigger, the behaviour, and the consequence someone outside can observe. A rule that stops at
"the system checks whether…" has not said what the check *does*, and is rejected.

**An outcome that is not a requirement at all.**

> Thin: The system shall return an error message if scheduling fails.

This is not a requirement in any form. A failure path is an acceptance criterion of the
requirement it protects — write it as one, on the requirement whose work failed, and publish
nothing separate for it.

Do not over-apply this. What is folded in is a *failure* — something went wrong and the product
reported it. A condition the product deliberately enforces on correct input is the opposite: it is
the business rule above, and it stays a published requirement. "The schedule could not be produced"
is a failure. "An item already held is not stored twice" is a policy. If a fragment found one,
carry it through: a document with capabilities and no rules has usually dissolved its rules into
criteria, which is where a reviewer will never look for them.

### Readability rules

- **Active voice, present-tense "shall".** "A reviewer shall be able to approve…", never
  "approval shall be able to be performed".
- **One sentence, one assertion.** A second sentence is a sign the statement is carrying an
  acceptance criterion that belongs in the list below it.
- **No lists inside a statement.** Three things joined by commas are three requirements, or one
  requirement with three acceptance criteria.
- **Name the actor the way the product names them.** Take the wording from interface labels and
  documentation. Where no actor is evidenced, say what the product does — do not invent a role.
- **Spell out what an abbreviation means** the first time, unless it is the product's own term
  for the thing.
- **A statement must stand alone.** A reader who has seen no other requirement, no code, and no
  screen should be able to follow it. Nothing may depend on the title above it, on the item
  before it, or on a section heading.
- **Prefer the concrete word.** "Remove", not "manage". "Ask a question", not "interact with".
  Umbrella verbs — manage, handle, support, process, enable, provide — usually mark a statement
  that has not decided what it asserts.

A later step may re-file a claim, retype it, merge it with another, or reject it. **No step
shortens a statement that already has its four parts.** Compressing "an administrator shall be
able to remove a single existing account by supplying its identifier, so that it can no longer
sign in" back to "the system shall allow account removal" throws away detail the evidence
established and no later step can recover. When merging two versions of one claim, keep the fuller
wording and add whatever the other one had that it lacks. A statement written out in full is
never the thing to trim to save room.

### Persona and business value

`persona` is the actor in part one, whenever the evidence names one — an interface label
addressing someone, a documented audience, an authorisation check naming a role. `business_value`
is the outcome in part four, stated as what the actor gets. Both stay null when the evidence names
nobody and produces no stated outcome; neither is ever a guess about who *would* use a product
like this. When a fragment established either one, carry it through every later step: they are
what turn a list of operations into a document someone can read.

## Acceptance criteria

Every functional requirement normally carries **three to eight** observable criteria, ordered or
independently checkable, describing its behaviour at the level the evidence actually shows. Draw
them from what the graph establishes: evidenced call parameters, configured defaults, the
processing stages a workflow passes through, return outcomes, fallbacks, and error handling.

A criterion someone cannot check by running the system is not a criterion. Never invent one to
fill the list — a requirement with two real criteria beats one with six invented ones.

**Criteria are held to the same language bar as statements.** They are the part of the document a
reviewer actually works through, so a criterion that names the mechanism has failed at the only
job it has. Two failures recur:

- *Naming the implementation.* "The answer is produced by the retrieval-and-generation pipeline
  defined in the processing logic" says nothing checkable and names the build. Write what a person
  would observe: "The answer repeats only wording that appears in the documents supplied."
- *Asserting an unmeasured quality.* "Retrieval is efficient and accurate", "the file is handled
  correctly", "the response is appropriate". None of these can be failed, so none of them are
  criteria. If the evidence fixes a number, a limit, a fixed message, or a specific returned
  outcome, state that instead; if it fixes nothing, drop the criterion.

Write each one as a condition that is either true or false after a described action, phrased so
someone who has never seen the code could carry it out.

## Coverage

Account for every principal function, method, endpoint, and executable module workflow the
evidence contains. Combine two of them into one requirement only when call edges show they form a
single capability.

Do **not** collapse distinct stages — ingestion, transformation, persistence, retrieval, answer
generation, user interaction — into one vague umbrella requirement. They are separately observable
and separately testable, so they are separate requirements.

Conversely, low-level collection, serialization, and file-handling verbs are *supporting* evidence,
not requirements of their own. Promote one only when the graph shows it producing an observable,
stakeholder-facing outcome.

## Where a conditional belongs

A genuine policy — a condition the product enforces, with a consequence someone outside can
observe — is a business rule. An ordinary processing branch or fallback is not: it belongs inside
the related functional requirement's acceptance criteria.

**Never restate a raw predicate as a requirement.** "The system checks whether the field is
present" describes the code, not the product.

Implementation uncertainty belongs in `assumptions` or `open_questions`, never hidden inside a
confident statement.

## Output format (per requirement)

```text
title, statement (one atomic "shall" statement), rationale (why the evidence supports this),
persona (the evidenced actor, or null), business_value (the evidenced outcome, or null),
acceptance_criteria: [observable pass/fail conditions],
confidence (0.0–1.0; 1.0 only for explicit, unambiguous intent),
evidence: [{file, line_start, line_end, symbol}],
graph_path: [ordered exact qualified names from source to boundary],
parent (exact title of the requirement one tier up: the business requirement a functional
  requirement realises, the functional requirement a rule constrains or a criterion validates),
assumptions: [what this rests on and needs validating],
open_questions: [what the evidence cannot settle]
```

Cite a definition at its own span — the line range the graph gives for that node, not a single
line inside it — and set `symbol` to the exact qualified name of the node being cited.

Group into: `product_purpose`, `business_problem` (or explicitly null if not established),
`business_requirements[]`, `functional_requirements[]`, `business_rules[]`, `success_criteria[]`.

## If nothing grounds

If the codebase has no discoverable interaction boundary (e.g. it's a pure library, or extraction
found nothing reliable), **say so directly** in `business_problem`/`product_purpose` rather than
inventing a plausible-sounding purpose. An honest "insufficient evidence" is always better than a
fabricated requirement.

## Self-check before finalizing

For every requirement you're about to keep, ask:
- Could I explain this to a stakeholder without naming a single file, function, or framework?
- If I deleted the underlying implementation and rebuilt it differently, would this requirement
  still describe the same real-world outcome?
- Can I point to the exact file and approximate line where this is enforced — and have I actually
  looked, not just trusted the graph label?

If any answer is no, revise or drop the requirement.

Then read the statement once more, as prose:
- Does it name **who** acts, or is it hiding a person behind "the system"?
- Does it say **what they supply or which item it applies to**, or does it stop at the verb?
- Does it say **what is true afterwards** that someone could check?
- Would a reader who has seen nothing else in this document — no title, no heading, no code —
  understand it on one reading?

A statement failing any of those is thin, not wrong. Rewrite it from the same evidence rather than
dropping it: the grounding is already done, and what is missing is almost always a detail the
evidence packet stated and the first draft left out.

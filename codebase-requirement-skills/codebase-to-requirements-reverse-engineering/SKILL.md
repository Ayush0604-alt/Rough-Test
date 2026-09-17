---
name: codebase-to-requirements-reverse-engineering
description: Reverse-engineer ANY codebase (any language, any framework) into an evidence-grounded requirements document describing only what is actually implemented — never assumptions, TODOs, comments, or README claims on their own. Use whenever asked to generate a requirements doc, BRD, spec, or user stories from an existing codebase's source code.
---

# Codebase-to-Requirements Reverse Engineering

## Purpose

Given an arbitrary target codebase, produce a requirements document where **every statement traces
back to real, implemented, externally-reachable code**. This is the master workflow. It is
self-contained; the companion skills (`graphify-knowledge-graph-extraction`,
`evidence-grounded-requirement-synthesis`, `requirement-consolidation-and-resolution`,
`requirement-and-graph-validation-gates`, `traceable-requirements-deliverables`) go deeper on
individual phases but this file alone is enough to run the whole thing.

## Core principle

> **Only implemented functionality becomes a requirement.**
> Never derive a requirement from: comments, TODOs, docstrings alone, README/marketing claims alone,
> function/class names alone, unimplemented interfaces, or "this is probably what it's for."
> Documentation may *suggest* intent — it must be *confirmed* by a reachable, executable code path
> before it becomes a requirement statement.

## What makes the output a requirements document rather than an inventory

These are the properties a reader uses to tell one from the other. They are derived from what a
good business requirements document does, and every one of them is independent of the domain: they
hold for a payments platform, a scheduling tool, a document-search assistant, and a device
controller alike. Nothing below prescribes a section list — the sections follow from the product.

**1. The unit of the document is a capability, not a construct.** A function existing, an endpoint
being declared, a value being normalised, a query being run, a field being checked before it is
read: none of these is a requirement because the code does it. First decide whether the behaviour
is something a person or another system asked for; only then write it up. The source code is
*evidence*, never the outline.

**2. Requirements are grouped by what they achieve, not by where their code sits.** Files, folders,
modules, classes, layers, and the entry point a behaviour happens to be reached through are facts
about the build. A group must be a thing the product does for someone, named in the product's own
words, and a reader who has never seen the code must be able to say what they would get from it.
Behaviour found in five places that serves one capability belongs in one group.

**3. Business intent is separated from functional behaviour, and the separation is a real
relationship.** An outcome says what someone can now achieve. A capability says what they can do.
The link between them is what makes the document say *why* the product does something — two claims
can both be typed correctly while the document never says which outcome a capability serves.

**4. An outcome is written only where the evidence carries one.** An outcome standing over a single
capability is that capability restated more grandly. Where no outcome can be established, the
capability is published at the top and the gap is stated. A fabricated objective is the one error a
stakeholder cannot detect by reading, because it is exactly what they expected to see.

**5. Hierarchy where it is real, flat where it is not.** Not every requirement has a parent, and
forcing one produces structure that records which code calls which. A link must name what it does
to its parent — makes it more specific, makes it possible, limits when it applies, proves it. A
link that can name none of those is not a hierarchy.

**6. Each statement carries actor, action, qualifier and outcome.** Who, doing what to what, under
which condition, with what observable result. A statement missing the qualifier or the outcome has
described a function signature. A statement in the passive voice with "the system" as its only
actor is usually describing scaffolding.

**7. Implementation vocabulary stays out of the statement and out of the criteria.** Route paths,
verbs, function and class names, framework and datastore names, serialisation formats, the names of
controls on a screen. The test is whether the statement would still be true after a rebuild on a
different stack. That detail is not discarded — it belongs in the evidence, where it is the warrant
for the claim rather than part of it.

**8. Rules are stated as rules.** A trigger, a required behaviour, an observable consequence, in one
place a reviewer can work through. A rule constrains a named capability; a rule attached to nothing
tells the reader that a condition is enforced somewhere and leaves them to guess where.

**9. Acceptance criteria are observable, and belong to the requirement they hang from.** Each one
true or false after a described action, checkable by someone who has never seen the code, covering
the success path and — where the evidence fixes them — the refusal, error and boundary behaviour.
A criterion that restates its requirement checks nothing.

**10. The document says what it does not know.** Assumptions that change how it should be read,
capabilities a reader would assume are present and which are not, and questions a person has to
settle. Each recorded because the evidence forced it, never to fill a heading.

**11. Only settled requirements appear.** Rejected candidates, competing readings of one piece of
evidence, and observations about the current implementation are kept — in a companion artifact,
with their evidence and the reason. Mixing them into the requirements list means a reader has to
know which field to distrust.

**12. Every section earns its place.** A heading that says nothing was found for it teaches the
reader nothing and makes the document read as a form. Where an absence is meaningful, it is stated
as an evidenced claim, not as an empty section.

**13. Identifiers are assigned last.** An id given to a candidate that is later merged, reclassified
or dropped either vanishes from the document or is silently reused by whatever ends up in that
position.

### Two things a readable document is not

A document can satisfy every rule above and still be wrong in two ways worth naming, because both
look like quality:

- **Short is not the same as consolidated.** Removing duplication is progress; removing coverage to
  make the page tidier is a loss disguised as one. The count should track the product, not the
  page.
- **Confident is not the same as correct.** A document with no assumptions, no open questions, and
  uniform certainty has not established that it is right — it has stopped saying which parts it is
  unsure of.

## When to use

- "Generate a requirements document / BRD / spec for this repo."
- "What does this codebase actually do, based only on the code?"
- "Reverse-engineer the business rules / API / workflows of this project."
- Any request to document a system's *actual* behavior from its source, as opposed to writing new
  requirements or redesigning it.

Do **not** use this to design new functionality, refactor code, or fix bugs — this skill only
observes and documents what already exists.

## Inputs

- A path to a codebase (a directory already on disk, or an archive to extract first).
- No assumption about language or framework. Extraction is delegated to an external tree-sitter
  parser covering 36 languages, so every language gets the same structural treatment (see
  extraction skill).

## Workflow (six phases, each a gate)

```text
Phase 0  Ingestion & Inventory
            ↓  (fails closed if nothing analysable is found)
Phase 1  Static Structural & Behavioral Extraction  →  Knowledge Graph
            ↓  (fails closed if the graph is structurally broken)
Phase 2  Graph Validation
            ↓
Phase 3  Evidence-Grounded Requirement Synthesis  →  candidate requirements
            ↓  (each candidate is individually rejected unless grounded)
Phase 3b Requirement Consolidation & Resolution   →  a settled requirement set
            ↓  (grouped, deduplicated, levelled, every candidate dispositioned)
Phase 4  Requirement Validation
            ↓  (fails closed if unresolved evidence remains)
Phase 5  Deliverable Generation  →  requirements.md, gap_analysis.md, traceability_matrix.csv, …
```

A "gate" means: if the phase's validation fails, **stop and report why** rather than silently
producing a weaker document. Never quietly degrade a hard failure into a soft one.

### Phase 0 — Ingestion & Inventory

1. If given an archive, extract it safely first (path traversal, symlinks, zip-bomb checks — see
   the ingestion section of `graphify-knowledge-graph-extraction`). Never execute or import any
   code from the target — read/parse only.
2. Walk the tree, skip VCS/build/vendor/dependency directories (`.git`, `node_modules`, `vendor`,
   `dist`, `build`, `.venv`, `target`, `__pycache__`, lockfile-managed caches, etc.).
3. Classify every file by extension into a language. Record size and a content hash if useful for
   later diffing.
4. **Gate:** if there is no source code you can meaningfully analyze (only binaries, docs, configs),
   stop and say so — do not proceed to fabricate a document from file names.

### Phase 1 — Static Structural & Behavioral Extraction

Build an in-memory (or on-disk JSON) **knowledge graph**: entities (files, modules, classes,
functions/methods, API endpoints, configuration keys, conditionals-as-business-rules, error
handlers, data-persistence operations) and relationships between them (contains, calls, exposes,
enforces, handles, performs, reads-config, imports), each carrying a source location and a
provenance/confidence label.

Full technique — Python-first via `ast`, generalized heuristics for other languages — is in
`graphify-knowledge-graph-extraction/SKILL.md`. Do not skip straight to writing requirements
without this structural pass; requirements without a graph behind them cannot be grounded later.

### Phase 2 — Graph Validation

Before trusting the graph, check it structurally: no dangling relationship endpoints, every claimed
API endpoint actually has a handler exposing it, every entity's source location actually resolves to
a real file/line in the target codebase, and flag if ambiguous call resolution is unusually high
(a sign the extraction is guessing more than resolving). Full checklist in
`requirement-and-graph-validation-gates/SKILL.md`.

### Phase 3 — Evidence-Grounded Requirement Synthesis

Act as the reasoning engine yourself (you are the "LLM" here — there is no separate service to call).
Read the graph, reconstruct end-to-end workflows, and write requirements in **product/business
language**, following the evidence-precedence and grounding rules in
`evidence-grounded-requirement-synthesis/SKILL.md`. Every candidate must individually pass the
grounding test in that skill before it is kept — reject anything that only reaches internal
processing, cites no real evidence, or asserts something the graph doesn't support.

### Phase 3b — Requirement Consolidation & Resolution

Synthesis reasons about one slice of the repository at a time and cannot see what the candidates
add up to; validation judges claims one at a time and can only reject. Between them sits the stage
that turns a pile of candidates into a requirement set: grouping by capability rather than by code
location, merging duplicates by meaning, settling conflicting readings against the evidence,
holding implementation vocabulary out of the statements, giving every candidate a disposition, and
only then writing the acceptance criteria and stamping the identifiers.

Skipping it is what produces a behaviour inventory. Full technique in
`requirement-consolidation-and-resolution/SKILL.md`.

### Phase 4 — Requirement Validation

Independently re-check the surviving candidates (don't just trust your own Phase 3 output): does
every requirement cite a real file, a real line range, a real graph path reaching a real interaction
boundary? Are there duplicates? Is any business requirement left without a functional requirement
realising it, or without acceptance criteria coverage?
Full checklist in `requirement-and-graph-validation-gates/SKILL.md`.

**If nothing survives grounding, say so explicitly** — an empty requirements document with a clear
explanation ("no interaction boundaries were found; the codebase may be a library, not an
application") is the correct output, not a fabricated one.

### Phase 5 — Deliverable Generation

Render the validated requirements into the actual documents the user asked for — typically a
Markdown/Word BRD in business language, plus companion technical artifacts (technical spec, API
docs, traceability matrix, gap analysis, workflow diagram) that carry the file/line evidence the BRD
deliberately omits. Full structure in `traceable-requirements-deliverables/SKILL.md`.

## Rules and constraints (apply throughout, not just in one phase)

- **Never invent.** No assumed users, personas, business value, policies, or behavior beyond what
  the graph supports. If evidence is thin, say so in the document (an assumption or open question),
  don't state it as fact.
- **Never treat a single function/class/module as a business requirement.** A requirement must
  represent an outcome reachable from — or reaching — a point where the system is externally
  consumed (an API endpoint, a CLI entry point, a UI action, a scheduled job, a message handler).
- **Never publish a claim whose only support is internal processing** (a helper called only by other
  internal helpers, with no path to any interaction boundary).
- **Every requirement must cite real evidence**: a file that exists in the target codebase, and
  ideally a line range you have actually checked against the file's real content — not just assumed
  from the graph label. (This addresses a known failure mode: don't just check "does this file
  exist," check "does this line/symbol actually say what I'm claiming.")
- **Business-facing documents must not contain technical vocabulary** — no function names, file
  paths, class names, frameworks, or database terms in the requirement *statement* itself. Keep that
  detail in the technical/traceability companion documents.
- **This works for any language**, and structure is extracted identically for all of them. What is
  *not* uniform is intent: nothing in the extractor marks a route, a command, or a scheduled job,
  so which nodes are interaction boundaries is inferred rather than parsed. Mark those `INFERRED`,
  keep their confidence low, and do not let a requirement resting on one read as though a decorator
  had been read out of the source.
- **Fail loudly, not silently.** A phase that can't produce grounded output should say so rather than
  quietly filling the document with weak or invented content.

## Output

A requirements document (and, unless told otherwise, its companion technical artifacts) whose every
claim can be traced: `Requirement → Knowledge Graph path → Source file/line`. See
`traceable-requirements-deliverables/SKILL.md` for exact document structure.

## What this skill is NOT

- Not a redesign or improvement tool — it documents current behavior only.
- Not tied to any specific target codebase, file layout, or previously-analyzed project. It must
  work identically whether pointed at a Python API, a JavaScript frontend, a Java service, or a
  polyglot monorepo.
- Not dependent on any external analyzer application, database, or API existing in the target repo —
  you (the agent) perform ingestion, extraction, grounding, validation and rendering yourself using
  your own file-reading, code-parsing (e.g. running a short Python `ast` script), and reasoning
  capabilities.

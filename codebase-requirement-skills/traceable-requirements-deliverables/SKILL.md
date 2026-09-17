---
name: traceable-requirements-deliverables
description: Render a validated, evidence-grounded requirements analysis into final documents — a business-language requirements document/BRD, plus technical companion artifacts (traceability matrix, gap analysis, technical specification, API documentation, workflow diagram) that carry the file/line evidence the BRD deliberately omits. Use when producing the final output of a codebase reverse-engineering/requirements task.
---

# Traceable Requirements Deliverables

## Purpose

Turn validated requirements into the documents a stakeholder or engineer actually reads, keeping a
hard separation: business language in the requirements doc, technical traceability in the companion
files.

## When to use

Last step of the reverse-engineering workflow, after `requirement-and-graph-validation-gates` has
passed (or explicitly reported why it couldn't).

## Deliverables to produce (unless the user asked for something narrower)

### 1. Requirements document / BRD (`requirements.md`, business language only)

The document is **shaped by the product**, not by a fixed template. Two rules decide its outline,
and between them they produce a different table of contents for a payments platform, a document
search tool, and a scheduling system — which is correct, because those are different products.

**Rule 1 — a section exists only if the evidence produced something for it.** A section that says
"nothing in this category was evidenced" teaches the reader nothing and makes the document read as
a form somebody filled in. Number the sections as you write them, so the numbering is a property of
this document rather than of the template. Where an *absence* is itself meaningful, it goes in
"Explicitly out of scope", which is an evidenced claim.

**Rule 2 — the order is outcome, then capability, then rule.** A reader arrives wanting to know
what the product is for, then what it does, then under what constraints. That is also the
decomposition chain, so the document's shape and the requirement model agree.

```markdown
# Business Requirements Document — <Project Name>

| Field | Value |
|---|---|
| Status | As-built. Reconstructed from implementation evidence. |
| Review state | Candidate. Nothing here has been confirmed by a stakeholder. |

## How to read this document
<what a requirement here is, that nothing was assumed, what the priority column means>

## 1. Executive summary
<what the product lets someone do, and roughly how much of it there is — or an explicit
 statement that the purpose could not be established>

## 2. Business context and problem          <only if established>

## 3. Business objectives
### <Outcome title>
<statement, in full, no technical vocabulary>
- **Who it serves:** …            <only where the evidence names an actor>
- **Value delivered:** …          <only where the traced behaviour fixes a result>
- **Acceptance criteria:** …      <observable conditions across the capabilities beneath it>
- **Delivered by:** <the capabilities that realise it, by id and name>
- **Traced to:** <citations>

## 4. Scope
- **<capability group>:** <what this part of the product covers, in its own words>

## 5. Capabilities
### 5.1 <capability group>
_Serves <outcome>._                <only where an outcome was established>
| ID | Requirement | Pri | Evidence |
**Within <capability>:**           <only where a capability decomposes into stages>
**Rules that apply here**          <only where rules constrain these capabilities>
<acceptance criteria per requirement>

<then, each only where the evidence produced one:>
## How the system must behave internally
## Data requirements
## Integration requirements
## Service-quality requirements
## Reporting and analytics
## Technical constraints
## Business rules and constraints     <the consolidated catalogue>
## Assumptions
## Explicitly out of scope
## Open questions for stakeholder validation
## Implementation observations (not requirements)
## Traceability summary
```

**Grouping.** Capabilities are grouped by the part of the *product* they belong to — something a
reader would recognise as a thing the product does — never by file, folder, module, class, layer,
or the entry point that happens to reach them. Two groups whose headings mean the same thing are
one group. A group holding unrelated outcomes is more than one group.

**Language.** Never mention file names, function names, route paths, frameworks, or storage
mechanics in a requirement statement or in an acceptance criterion — a reader signs off on those
sentences, and both are held to the same bar. That detail belongs in the Evidence cell and in the
companion documents, where it is wanted.

**What is not in this document.** Rejected candidates, unresolved questions, competing readings of
one piece of evidence, and implementation observations are not requirements and must not be mixed
into the requirements list. They go to the companion analysis document (see 7 below), with their
evidence and the reason, so nothing is lost and nothing unsettled is published as settled.

### 2. Traceability matrix (`traceability_matrix.csv`)

One row per requirement per evidence citation:
`requirement_id, type, statement, confidence, source_file, source_line, source_symbol, graph_path`

### 3. Gap analysis (`gap_analysis.md`)

Be honest about coverage. Report:
- Reviewable constructs (endpoints, classes, functions, business rules, config keys, data operations)
  found vs. covered by at least one requirement's `graph_path`.
- Requirements below a confidence threshold (e.g. 0.8), listed for review.
- Files that couldn't be parsed, or that were inventory-only (non-primary language).
- Unresolved external references.
- **State plainly that "covered" means a requirement traces to it — not that the requirement is
  necessarily correct, complete, or reviewed.**

### 4. Technical specification (`technical_specification.md`)

Coverage stats (files/parsed/coverage %, entity/relationship counts) and a component inventory
grouped by kind (modules, classes, functions, config keys, data operations) — the technical detail
deliberately excluded from the BRD.

### 5. API documentation (`api_documentation.md`)

A table of every detected endpoint: method, path, handler, source location. State plainly if none
were found ("no statically declared HTTP endpoints were detected") rather than leaving it blank.

### 6. Workflow diagram (`workflows.mmd`, Mermaid `flowchart`)

The most-connected subset of the call/behavior graph (cap at a readable size, e.g. 40 nodes, ranked
by degree), so a reader can see the shape of the system at a glance.

### 7. Requirement analysis (`requirement_analysis.md`)

Everything the analysis produced and could not stand behind, kept out of the requirements document
and not thrown away: candidates rejected by their own evidence, questions the evidence cannot
settle, competing readings of one piece of evidence, and the structural findings the finished set
still carries. Each with its evidence and the reason it is here, so a reviewer can check the
judgement rather than take it on trust.

Keeping these in the requirements list, marked with a field, meant a reader had to know which field
to distrust. Deleting them meant nobody could check the call. A second document is the only
arrangement that is honest to both.

## Rules and constraints

- The BRD must contain zero technical vocabulary in requirement statements **or in acceptance
  criteria** — verify this yourself before finalizing, don't just trust the synthesis step. A
  criterion is what a reviewer signs off against, line by line, so it is read at least as closely
  as the statement above it.
- A requirement rejected for the *words* it used is a wording problem, not a grounding problem.
  Reword it and record what was removed; deleting a checked, cited, traced requirement over a
  phrase discards evidence to satisfy a linter.
- Every artifact must reflect the *actual* validated requirement set — don't add narrative flourishes
  or claims beyond what was grounded.
- If a gate failed or produced warnings, surface that honestly in the gap analysis rather than
  hiding it to make the output look cleaner.
- If the analysis produced few or zero grounded requirements, still produce the documents — with an
  honest explanation of why coverage is thin (e.g. "primarily a library with no discovered
  interaction boundary," "most code is in an unsupported language and received only inventory-level
  analysis").
- Keep the BRD skimmable — a stakeholder should get the gist from the executive summary alone.

## Output

The set of files above (or whichever subset the user asked for), each internally consistent with the
same underlying validated requirement set and knowledge graph.

# `instructions.py` — Phase 1b(iii): prose that *is* the behaviour

> **Added 2026-09-22.** For a conventional product a Markdown file describes the code. For an
> LLM-powered product the instruction file **is** the implementation, and the code around it opens
> the file and posts it to an API. The supporting-outline reader matched a hardcoded filename
> whitelist whose only Markdown pattern was `readme*`, so a `SKILL.md` returned **nothing at all**.
> The change itself is in
> [changes/instruction_artifacts.md](../changes/instruction_artifacts.md).

Module: [src/agentic/capabilities/instructions.py](../../src/agentic/capabilities/instructions.py)
Registered capability: `extract_instruction_artifacts` (step `extract_instruction_artifacts`,
`on_fail: warn_and_continue`)
Setting: `instruction_artifacts: bool = True` — off reproduces the previous graph exactly
Consumers: `compact_entity`, `declaration_groups`, `partition_modules` (`instruction_rules` per
module), workflow traversal (via the `LOADS` edge), `report_family_coverage`

---

## 1. What was measured before it existed

```
analysed repository (381 files)   24 markdown nodes · 17 of them agent skill files
                                   1 carried any text — the README, because of its name
                                   0 edges to anything

this tool, analysing itself      137 markdown nodes ·  54 SKILL.md nodes
                                   0 carried any text ·  0 edges to anything
```

The second row is the one that decided this. This project deliberately keeps its instructions in
Markdown *"never inside prompt string literals"* — and run on itself it read **none of them**. It
would have documented the loader and said nothing about what is loaded.

The cause was a filename whitelist:

```
dockerfile · docker-compose* · requirements* · pyproject.toml · package.json
setup.py · makefile · cargo.toml · go.mod · pom.xml · build.gradle · gemfile
composer.json · readme*.{md,rst,txt}        ← the only Markdown pattern
```

Anything else returned empty — not truncated, **empty**. Its docstring records that it was *"ported
unchanged in behaviour"* from the deleted Python extractor, which is exactly the era when treating
Markdown as secondary was correct.

## 2. What it emits — structure, following the schema precedent

A schema file became tables, columns and constraints rather than a blob of SQL text. An instruction
file gets the same treatment rather than fifty lines of flattened prose.

| Kind | Is | Attributes |
|---|---|---|
| `INSTRUCTION_ARTIFACT` | the file | `artifact_role`, `declared_name`, `description`, `sections`, `rule_count` |
| `INSTRUCTION_RULE` | one declared prohibition or obligation | `rule_source`, `modality`, `statement`, `section` |

Edges: `DECLARES` from the owning file to the artifact, `ENFORCES` from artifact to rule, and
**`LOADS` from the code that opens it** — see §4.

`INSTRUCTION_RULE` is the counterpart of a schema's `CHECK` constraint: not any sentence, only one
stating an obligation or prohibition outright. Explanatory prose stays part of the artifact rather
than being promoted, for the same reason most conditionals are not business rules — promoting
everything buries the declarations that matter.

Measured on the reference repository: **19 artifacts, 315 declared rules**, and **280 instruction
nodes now reaching evidence packets** (from zero).

```
Do not generate generic logos.              [do not]
The visual system must be based on meaning. [must]
Never emit a placeholder comment.           [never]
```

## 3. What is deliberately not a rule

Four filters, each added because it fired on real files:

- **Inside a fenced code block, nothing counts.** A banned-pattern example illustrates a rule; it
  is not one. Counting each would turn one prohibition into a dozen requirements.
- **A line ending in a colon introduces rather than states.** *"The output must feel:"* captured
  alone reads as a complete requirement that says nothing. The real rule is the list beneath, and
  each item is judged on its own.
- **A modality past the first 80 characters is descriptive.** A `must` deep inside an explanatory
  sentence describes something; it does not declare it.
- **Headings inside a fence are comments, not sections.** A skill file carrying a Python example
  gained phantom sections named after its own comments, which then mis-attributed the rules
  beneath real headings.

## 4. The `LOADS` edge, and why it is the important part

Before this, these files were file-nodes with **zero edges** — as orphaned as the schema nodes were
before a producer existed, and for the same reason: nothing calls a Markdown file.

`LOADS` runs from the code that opens an instruction file to the artifact. It points in the same
direction the evidence walk already goes — outward from a boundary, through the calls, to the
loader, then to what the loader loads — so **no traversal rule had to be inverted.**

### The grounding decision

`LOADS` is in `WORKFLOW_EDGE_KINDS`. That means **a requirement may be grounded in an instruction
file when traced code loads it.** This is a policy choice, not a bug fix, and the alternative is
defensible: leave it out and LLM-native products stay undocumentable by this pipeline.

It was chosen on the schema precedent. Nothing *calls* a schema either; a declared constraint
earns citability because the database enforces it. An instruction file earns it because the model
is given it on every request. **The enforcement is weaker** — an instruction asks, a constraint
enforces — which is why rules carry `RESOLVED` provenance and 0.7 confidence rather than
`EXTRACTED` certainty, and why the synthesis instruction requires them worded as *declared policy*:
"the system is instructed to refuse X", never "the system refuses X".

To reverse: remove `"LOADS"` from `WORKFLOW_EDGE_KINDS`. Artifacts and rules stay in the graph and
stay visible in declaration clusters; they simply stop grounding a claim alone. A test asserts the
entry is present, so removing it is a deliberate act with a failing test attached.

## 5. Loader matching — three rules tried, two discarded

This is where the module's real risk lives. A false `LOADS` edge is walked like any other, so **one
wrong link lets a requirement ground itself in an unrelated instruction file** — the exact
fabrication the rest of the pipeline prevents. Recall is cheap by comparison: a missing edge costs
an artifact only its ability to ground a claim alone, and it still reaches a model through its
declaration cluster with the step saying out loud that no loader was found.

| Rule | Result on the real repository |
|---|---|
| Every path segment | **114 edges, all false.** The repository root was a literal for every artifact, so any line mentioning the repository name linked to all nineteen. |
| The distinctive leaf directory | **12 edges, all false.** A directory is named after a domain concept and a concept is an ordinary English word: `extraction` matched `"extraction": result,` in an API response; `queue` matched a route path, a comment and a docstring. |
| **Path-shaped literal or location glob, inside quotes, not in a comment** | **0 edges on that repository — correct**, because those skill files are for the developer's coding agent and no product code loads them. On this project's own source it finds the real loader. |

The surviving rule has four parts, each earning its place:

1. **Path-shaped only.** A literal must carry a separator or be a filename with an instruction
   suffix. That distinguishes naming a *file* from mentioning a *concept*, and a loader cannot open
   a file without naming a path.
2. **Globs, requiring a separator.** A registry does not name each file — it globs the directory,
   which is exactly how this project loads its own skills. `*/SKILL.md` counts; `*.md` does not,
   because it names a file *type* and would link one loader to every artifact.
3. **Inside a quoted string.** A path in prose is a description; a path a loader uses is a string
   it passes to something.
4. **Not in a comment, and backticks are not quotes.** Backticks delimit a template literal in
   JavaScript but are markdown markup in Python docstrings — and this project's comments use them
   constantly, so including them made every sentence mentioning a glob look like a loader.

Every match records the literal that produced it, so a reviewer can tell a real load from a
coincidence.

## 6. Benefits

- **LLM-native products become describable at all.** The rules a user actually experiences live in
  the Markdown; previously none of it reached a model.
- **Structure, not a text blob** — headings and declared rules, each citable at a line.
- **A traceable path from behaviour to instruction.** A requirement can trace endpoint → handler →
  loader → the rule that causes the behaviour.
- **Honest degradation.** No loader found is reported, and the artifact still reaches a declaration
  cluster.
- **Zero model cost**, deterministic, and exactly reversible.
- **Weaker evidence is labelled as weaker** rather than silently mixed with parsed certainty.

## 7. Drawbacks and limits

- **Loader detection is precision-tuned and will miss real loaders.** A path assembled at runtime
  (`root / name / "SKILL.md"`), read from configuration, or passed through a variable produces no
  edge. On the reference repository the honest answer was zero, but a false zero and a true zero
  look identical from the output.
- **A docstring still counts as a quoted string.** A triple-quoted docstring naming a path can
  produce an edge. Narrower than the comment case and at least attached to the loading code, but it
  is a known false-positive channel.
- **Rule extraction is a keyword heuristic.** It keys on modality words. A prohibition phrased
  without one — "placeholders are unacceptable" — is missed, and a rhetorical "you must understand
  that…" can be captured. The line is always cited, so a reviewer can check.
- **Directory conventions are a list.** `skills`, `prompts`, `.agents`, `.claude`, `templates` and
  a few more. A project inventing its own name gets nothing unless the filename matches.
- **Role detection cannot read intent.** A design document under `prompts/` is treated as an
  instruction; a genuine instruction file named `notes.md` outside any known directory is treated
  as documentation.
- **Caps are arbitrary.** 30 rules and 40 sections per artifact, 256 KB per file. A very long skill
  file is truncated silently past those.
- **A declared rule is not an observed behaviour.** Nothing here proves the model complies. This is
  the most important limitation and the reason for the reduced confidence and the wording
  requirement — a requirement resting on an instruction file asserts what the product *is
  instructed* to do.

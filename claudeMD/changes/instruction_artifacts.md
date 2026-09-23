# Change log — Instruction artifacts, and the ranking defect beside them

Design writeup: [capabilities/instructions.md](../capabilities/instructions.md).
Follows [declared_policy_and_module_tier.md](declared_policy_and_module_tier.md), which restored
two of the three evidence classes the extractor swap removed. **This is the third.**

**One line:** For an LLM-powered product the skill or prompt file *is* the implementation, and the
code around it only opens the file and posts it to an API — yet the pipeline's Markdown reader
matched a hardcoded filename whitelist whose only Markdown pattern was `readme*`, so a `SKILL.md`
returned **nothing at all**. This reads instruction files as structure, connects each to the code
that loads it, and fixes an unranked-truncation defect found in the same region.

**Status:** Implemented and tested. Full suite **800 passed, 1 failed** — the one failure
(`test_entry_point_adapters.py::test_registered_handlers_become_entry_points`, `assert 4 == 3`) is
pre-existing and was verified against the untouched baseline. Previous state was 762/1, so this adds
38 tests and no regressions.

---

## The measurement

```
analysed repository (381 files)   24 markdown nodes · 17 of them agent skill files
                                   1 carried any text — the README, because of its name
                                   0 edges to anything

this tool, analysing itself      137 markdown nodes ·  54 SKILL.md nodes
                                   0 carried any text ·  0 edges to anything
```

The second row is the one that settled it. This project's own architecture states that skill
instructions live in Markdown *"never inside prompt string literals"* — a deliberate choice to move
behaviour out of code. Analysing itself, it read **none of them**: it would have documented the
loader and said nothing about what is loaded.

---

## 1. `extract_instruction_artifacts` — reading instruction files as structure

Two node kinds, following the schema precedent that a declaration file becomes structure rather
than a text blob:

- **`INSTRUCTION_ARTIFACT`** — the file, with its declared name, description and section headings.
- **`INSTRUCTION_RULE`** — one declared prohibition or obligation, with its modality, its section
  and its line.

Result on the reference repository: **19 artifacts, 315 rules, 280 instruction nodes now reaching
evidence packets** (from zero).

Role is decided by location first, then filename — because those are what projects actually use to
mean "these are instructions". A file under `.agents/skills/` is an instruction artifact whatever
it is called; a file called `SKILL.md` is one wherever it sits. A README *inside* a skill directory
stays documentation, since it describes the pack rather than instructing a model.

## 2. The `LOADS` edge — and the grounding decision it encodes

Before this, instruction files were file-nodes with **zero edges**, as orphaned as the schema nodes
were before a producer existed and for the same reason: nothing calls a Markdown file.

`LOADS` runs from the code that opens the file to the artifact, in the same direction the evidence
walk already goes — boundary → calls → loader → what it loads — so **no traversal rule had to be
inverted**.

Putting `LOADS` in `WORKFLOW_EDGE_KINDS` means **a requirement may be grounded in an instruction
file when traced code loads it.** That is a policy decision, taken on the schema precedent: nothing
calls a schema either, and a declared constraint earns citability because the database enforces it.
An instruction file earns it because the model is given it on every request.

**The enforcement is weaker and is labelled as weaker.** Rules carry `RESOLVED` provenance and 0.7
confidence rather than `EXTRACTED` certainty, and the synthesis instruction requires them worded as
declared policy: *"the system is instructed to refuse X"*, never *"the system refuses X"*. An
instruction asks; a constraint enforces.

Reversing it is one line — remove the entry — and a test asserts it is present, so removal is a
deliberate act with a failing test attached rather than a quiet change of policy.

## 3. Loader matching — two rules built, measured, and thrown away

This was the hard part, and the numbers are the argument. A false `LOADS` edge is walked like any
other, so one wrong link lets a requirement ground itself in an unrelated instruction file — the
exact fabrication the rest of the pipeline exists to prevent.

| Attempt | Edges found | Verdict |
|---|---|---|
| Every path segment is a match literal | **114** | **All false.** The repository root was a literal for every artifact, so any line mentioning the repository name linked to all nineteen. |
| Only the distinctive leaf directory | **12** | **All false.** A directory is named after a domain concept, and a concept is an ordinary English word. |
| Path-shaped literal or location glob, inside quotes, not in a comment | **0** here, real loader found on this project | **Correct.** Those skill files are for the developer's coding agent; no product code loads them. |

The second attempt's failures are worth recording verbatim, because they look like successes in a
summary table:

```
literal='extraction'  matched   "extraction": result,          ← a dict key in an API response
literal='queue'       matched   # …the Needs-Review queue…     ← a comment
literal='queue'       matched   @router.get("/queue")          ← a route path
```

The surviving rule has four parts, each of which removed a class of false edge:

1. **Path-shaped only** — a separator, or a filename with an instruction suffix. Distinguishes
   naming a *file* from mentioning a *concept*.
2. **Globs, requiring a separator** — a registry globs rather than naming each file (this project's
   own loader is exactly that shape), so without glob support only a hardcoded single path would be
   detectable. `*.md` is rejected: it names a file *type* and would link one loader to every
   artifact.
3. **Inside a quoted string** — a path in prose is a description, not a load.
4. **Not in a comment, and backticks are not quote characters** — backticks delimit a template
   literal in JavaScript but are markdown markup in Python docstrings, and this project's comments
   use them constantly.

Every edge records the literal that matched, so a reviewer can distinguish a real load from a
coincidence without re-deriving it.

## 4. The ranking defect found next to it

While tracing how Markdown reached a prompt, the documentation list turned out to be selected with
**no ordering at all** — the first four in graph order, which is sorted by content hash. So the
selection was deterministic and semantically arbitrary. On the reference run:

```
docker-compose.yml            ← three of four slots went to infrastructure
README.md                     ← survived by luck of hashing
docker-compose.override.yml
frontend/Dockerfile
```

What makes it clearly a defect rather than a judgement call: **ten lines below**, the module-intent
list does the same capped selection and ranks properly, with a comment explaining why — *"ranked by
graph degree rather than encountered order, so the modules most connected to the rest of the system
win the capped slots rather than the first N found."* The reasoning was written down, applied to one
of two adjacent capped lists, and not the other.

Now ranked by what a file actually states about the product: a README or an architecture document
outranks a deployment recipe, and an override file — a variant of a variant — ranks below the file
it overrides.

This is the **fourth** instance of the same pattern in this codebase: a capped list taken in
arbitrary order. The others were the boundary-candidate cap, the declaration-file ranking, and the
area codes each worker invented alone.

---

## Files changed

| File | Change | Risk |
|---|---|---|
| `src/agentic/capabilities/instructions.py` | **New**, ~600 lines | Medium — `LOADS` is walkable, so a false edge matters |
| `src/agentic/capabilities/graph_ops.py` | `LOADS` in workflow edges · two kinds in declaration kinds · two entries in the packet whitelist | Medium (traversal) |
| `src/agentic/llm/prompt.py` | documentation ranking · `instruction_rules` in the module payload | Low |
| `src/agentic/capabilities/modules.py` | modules carry the rules their own code loads | Low |
| `src/agentic/config.py` | `instruction_artifacts` switch | Low |
| `codebase-requirement-skills/pack.yaml` | new step · synthesis guidance on wording a declared rule | Low |
| `tests/test_instructions.py` | **New**, 38 tests | None |
| five test files | the extraction step sequence, by hand again | None |

## Bugs found during implementation

Each was caught by a test or by a run against real data:

1. **114 false LOADS edges** — the repository root was a match literal for every artifact.
2. **12 false LOADS edges** — a leaf directory name is an ordinary English word.
3. **Backticks treated as string delimiters** — every comment mentioning a glob became a loader,
   including this module's own.
4. **Comments containing quoted paths** read as loaders.
5. **Colon-terminated lines captured as rules** — *"The output must feel:"* is a complete-looking
   requirement that says nothing.
6. **Headings read from inside code fences** — a skill file with a Python example gained phantom
   sections named after its own comments, which mis-attributed the rules beneath real headings.
7. **Metrics and frontmatter collision** — a bulk rename broke the frontmatter parser, caught
   immediately by a run.

## What stays the same

- `instruction_artifacts=False` reproduces the previous graph node-for-node; a test asserts it.
- Purely additive — a test asserts every pre-existing entity survives byte-identical.
- The old outline reader is untouched, so READMEs and build manifests behave exactly as before.
- `clusters`, `synthesize`, assembly, gates and rendering keep their shapes.

## Known risks

- **A false zero and a true zero look identical.** Loader detection is precision-tuned, so a path
  assembled at runtime, read from configuration, or passed through a variable produces no edge. The
  step reports that none was found; it cannot report that none exists.
- **A docstring still counts as a quoted string**, so a docstring naming a path can produce an
  edge. Narrower than the comment case and at least attached to the loading code.
- **Rule extraction is a keyword heuristic.** A prohibition phrased without a modality word is
  missed; a rhetorical "you must understand" can be captured. Every rule cites its line.
- **A declared rule is not an observed behaviour.** Nothing proves the model complies. This is why
  confidence is reduced and the wording requirement exists, and it is the limitation most likely to
  be lost when a requirement is read out of context.
- **The extraction step sequence is now duplicated in five test files**, only one of which derives
  it from the manifest. This change edited all five by hand. It has now bitten three consecutive
  changes and should be fixed before the next one.

## Still open, and deliberately not done

**Documentation-as-policy.** The extraction doc records that the old extractors read *three* things
into policy nodes: the schema, the configuration, and the project documentation. Declared policy
restored the first two; this restored instruction files. **A plain README still becomes only an
outline** — a README stating "invoices over £10,000 require manager approval" produces no policy
node.

That is now a recorded decision rather than an omission: prose in a README is a much weaker claim
than either a schema constraint or an instruction handed to a model on every request, and turning
paragraphs into policy invites the over-extraction the whole design fights. Revisit it only with a
mechanism at least as constrained as the modality filter used here.

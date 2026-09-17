# Skill-Pack Agentic System

A LangGraph agent that reverse-engineers a codebase into an **evidence-grounded requirements
document**, where every statement traces to a real file and line.

The important part is not the requirements pack. It is that the workflow is **data**. A skill pack
is a directory of `SKILL.md` files plus a `pack.yaml` manifest; the manifest compiles into a
`StateGraph`. Adding a second pack — test-case generation, threat modelling, migration analysis —
means adding markdown and a manifest, not editing graph code.

```
pack.yaml ──► PackManifest ──► StateGraph.compile()
                   │
      ┌────────────┼─────────────┐
      ▼            ▼             ▼
 capability    skill body    output schema
 registry      registry      registry
```

## Quick start

```bash
python -m venv .venv
.venv/Scripts/activate          # Windows;  source .venv/bin/activate elsewhere
pip install -e ".[dev]"
cp .env.example .env            # then fill in the four AZURE_OPENAI_* values

pytest                          # 200 tests, no API calls
agentic packs                   # list installed skill packs
agentic show codebase-requirements
agentic run codebase-requirements ./path/to/repo
streamlit run app.py
```

## How it works

### The workflow

```
ingest → extract → extract_ui → extract_schema → extract_artifacts
                       │(fail)                          │
                       ▼                                ▼
                     halt          discover_entry_points → gate_graph → plan_clusters
                                                                            │
                                          Send(synthesize, …) per boundary   ← parallel fan-out
                                                        │
                                              cluster_candidates      ← group by meaning,
                                                        │               repository-wide
                                    digest_areas → elevate → assemble
                                     (titles only)  (1 call)  (no model)
                                                        │
   ┌─────────────────────── consolidation and resolution ───────────────────────┐
   │  consolidate → check_correspondence → score_confidence                     │
   │       → strip_implementation → resolve → derive_criteria                   │
   └────────────────────────────────────┬───────────────────────────────────────┘
                                        │
                            prepare_claims → gate_requirements
                                   ┌────────────────────┼────────────────┐
                                (repair ≤3)          (pass)       (repairs spent)
                                   │                    │                │
                       repair → apply_repairs ──────────┤            quarantine
                                   └───────────────────┬┴──────────────────┘
                                                       ▼
                                            final_check → render → END
```

The middle band is the stage the workflow used to skip. Synthesis reasons about one interaction
boundary at a time; validation judges one claim at a time. Neither is in a position to ask what the
candidates *add up to*, and without that question the output is a behaviour inventory: as many
requirements as the code had interesting places, in the order they were found, each one true and
the set as a whole unreadable. Six of these capabilities were built, tested, and named by no step
in the manifest, so the behaviour they implement was not in the product at all.

### What the consolidation stage decides

Each of these needs the whole candidate set, and each is a separate judgement:

| Step | Question it answers |
|---|---|
| `cluster_candidates` | Which candidates describe **one capability**, wherever in the repository they were found? Comparison before this required two candidates to share a citation or a call-path node, and two workers describing one capability from the entry point and from the work behind it cite different lines by construction — so their wording was never compared and both were published. |
| `consolidate` | What level is each claim at, which are duplicates, which are the same sentence with one subject changed, and which parent links mean anything? |
| `check_correspondence` | Does the claim say what the code it cites actually does? Every other check compares a statement to another statement or to a word list. |
| `strip_implementation` | Which words in the statement are this repository's own machinery, and what is the requirement once they are held out of it? |
| `resolve` | What happens to each candidate — kept, reclassified, merged, recorded as an observation, raised as an open question, or rejected? |
| `derive_criteria` | What observable conditions would show each **final** requirement is met, and what does the document still not know? |
| `final_check` | Does the finished set hold together — no duplicate, no cycle, no relation pointing at nothing, nothing unresolved published as settled? |

Two rules run through all of it. **Nothing is deleted**: a candidate that is not a requirement is
refiled, and one that cannot be published moves to `requirement_analysis.md` with its evidence and
the reason. And **a rejection over wording costs a wording, not a requirement**: a claim that was
cited, traced and boundary-checked and happens to name a control is reworded, with what was removed
recorded on the item — deleting it discards checked evidence to satisfy a linter.

### Three design decisions worth knowing

**Routing is deterministic, never model-driven.** The validation skill says a gate must not be
reasoned past. A supervisor LLM choosing the next node could route around a failed gate because
continuing looked more helpful. Here the router reads the manifest and the state, so it
structurally cannot.

**Requirement volume tracks the codebase, not an output token limit.** The merge step used to be
one model call handed every finding and asked to return the finished document. With an 8k output
ceiling that caps *any* repository at roughly a dozen requirements — a platform with two hundred
grounded findings had them silently compressed to five, and nothing counted the loss. Now the
fan-out workers' findings are already written and already grounded, so `assemble` carries every
one through with no model involved. The single remaining whole-repository call (`elevate`) reads a
digest of area names and requirement *titles* and writes only what needs the whole picture: the
purpose, the handful of business requirements, and which area realises which. Its input and output
stay small however large the repository is.

**A repair pass touches only what the gate rejected.** The repair step used to be typed
`ProductBRD`: a model asked to correct three wordings had to re-emit every requirement in the set
in one reply. At 200 requirements the document was ~145k characters — past the 60k input cap, so
the model saw a *truncated preview* — and the reply was capped at 8k tokens, so a fraction came
back and the rest was silently gone. A rejection over one phrase could delete most of the
analysis. Repair now receives only the rejected claims (~2k characters) and returns only the ones
it fixed; `apply_repairs` merges them by id and carries everything else across untouched. A claim
the gate never rejected cannot be altered there at all.

**The model never sees source code.** `plan_clusters` finds every interaction boundary (endpoint,
CLI entry, `__main__`, exposed handler), expands each into a bounded neighbourhood, and packs it
into a compact evidence packet. One model call per boundary, each carrying only its own slice.
Cost is linear in the number of boundaries, not in the size of the codebase.

**Large artifacts live on disk, not in state.** LangGraph checkpoints the whole state after every
superstep. A multi-megabyte knowledge graph held inline would be re-serialised on every node.
State carries paths; `ArtifactStore` carries the payloads.

### What is deterministic and what is not

| Concern | How |
|---|---|
| Ingestion and inventory | Pure Python. No model. |
| Structural extraction across 36 languages | The external `graphify` binary, local tree-sitter parsing. No model, no network. |
| Normalising that into the knowledge graph | Pure Python. No model. |
| Naming which nodes are interaction boundaries | A model, from graph shape. It names a node; the location always comes from the graph. |
| Merging fan-out findings into the document | Pure Python. Every grounded finding survives. |
| Grouping, deduplication, conflict resolution, dispositions | Pure Python. Nothing is deleted; what cannot be published is refiled. |
| Acceptance criteria, assumptions, open questions | Pure Python, derived from cited evidence and written after the set is final. |
| Both validation gates | Pure Python checks against the graph and the file system. |
| Quarantine of ungrounded claims | Pure Python. A rejected claim is dropped, never reworded. |
| Rendering all seven deliverables | Pure Python templates. |
| Requirement synthesis | A model step, governed by injected skill prose. |

## Skill packs

### Anatomy

```
codebase-requirement-skills/
  pack.yaml                                    # the workflow
  codebase-to-requirements-reverse-engineering/SKILL.md
  graphify-knowledge-graph-extraction/SKILL.md
  evidence-grounded-requirement-synthesis/SKILL.md
  requirement-consolidation-and-resolution/SKILL.md
  requirement-and-graph-validation-gates/SKILL.md
  traceable-requirements-deliverables/SKILL.md
```

`SKILL.md` files keep the standard `name` / `description` frontmatter and carry **no**
orchestration metadata, so they stay portable to any tool that reads the standard skill format.
Everything about ordering, gating, and fan-out lives in `pack.yaml`.

### A step

```yaml
- id: gate_requirements
  skill: requirement-and-graph-validation-gates
  section: "Gate 2"          # inject just this part of the skill
  mode: gate                 # deterministic | gate | llm
  capability: validate_requirements
  consumes: [brd, graph]
  produces: [requirement_gate]
  next: render               # the pass path
  on_fail: repair
  repair_step: repair
  max_repairs: 2
  exhausted_step: quarantine
```

### How skills reach runtime

Hybrid, by design:

- **LLM steps** load the skill body from disk at call time and inject it into the system prompt,
  after the pack's `master_skill`. Editing a `SKILL.md` changes behaviour on the next run — no
  restart, no code change.
- **Deterministic steps** implement their skill in Python, but record the skill name and its
  **sha256** in the run's step records. Every deliverable can therefore state which version of
  which instructions produced it, and drift between the markdown and the code is auditable rather
  than invisible.

### Adding a pack

1. Write the `SKILL.md` files.
2. Register any new capabilities with `@capability("name")` and any new output schema with
   `register_schema("Name", Model)`.
3. Write `pack.yaml`.

Nothing under `src/agentic/graph/`, `state.py`, `runtime.py`, or `app.py` changes.
`tests/test_extensibility.py` builds a genuinely new pack at runtime and runs it — if that test
ever needs a graph-code change to pass, the abstraction has leaked.

## Staying general across codebases

Three mechanisms decide what the model ever gets to see, and each one had a bias toward codebases
that call their work directly.

**Registration, not just calls.** A handler handed to a framework — `router.add("refund", handler)`,
`builder.add_node("sql_agent", run_sql)`, a task queue, a callback table — is never *called* by any
code in the repository. A call-graph walk cannot reach it, so it landed in no evidence packet and
no requirement was written about it. A `REGISTERS` edge is now recorded whenever a project-defined
function is passed as a value, matched on that *shape* rather than on any framework's name. The
edge carries lower confidence than a direct call: the reference is certain, that the framework
invokes it is an inference.

**A deeper horizon.** Workflow traversal was four hops and 120 nodes. Reaching a registered handler
costs three hops before any work is described, so the handler sat on the horizon and everything it
called lay past it. Now six hops and 250 nodes.

**Splitting instead of truncating.** A boundary that reaches most of an application is ordinary,
not pathological — one chat entry point dispatching through a graph of handlers is the common
case. Its neighbourhood used to be packed into one budgeted packet and whatever did not fit was
dropped silently, and the tail of a breadth-first walk is the deepest, most specific work.
Oversized neighbourhoods are now split across several packets, and anything still unplaced is
counted and reported.

Measured on a multi-agent codebase where one chat control fans out into screening, routing, a SQL
path and a document path: **35% → 88%** of functions reach an evidence packet, with every module
represented. On a directly-wired codebase the same changes are a small gain, not a regression
(17% → 19% of functions, 63 → 69 files).

Two further things keep the output from acquiring a favourite subject. Functional **areas are a
property of the requirement, not of the cluster**, so one entry point covering several unrelated
parts of a product publishes several headings rather than one lump. And the gap analysis now lists
**parts of the codebase no requirement describes**, file by file — a document can be internally
consistent, well written, and still describe half the system.

## The requirement classification model

Every published claim sits at exactly one of three tiers, and the tiers are a **decomposition
chain**, not three independent labels:

```
Business Requirement ──realised by──▶ Functional Requirement ──constrained by──▶ Business Rule
      (why)                                  (what)                            (under what constraint)
```

Each item names its `parent` one tier up, and that link is what makes the taxonomy structural
rather than decorative. Two claims can both be typed correctly while the document still never says
which outcome a capability serves — that is the failure the link exists to prevent.

| Concern | How it is settled |
|---|---|
| Which tier an item is | The model decides, then the graph overrides: a trace reaching a `BUSINESS_RULE` node and no boundary **is** a business rule, whatever it called itself. |
| Types outside the three | Folded onto a tier (`technical` → functional, `stakeholder` → business), never dropped — a grounded finding that named itself badly is still a finding. |
| Section vs type disagreeing | They cannot. The section **is** the tier; items are filed at whatever tier they declare. |
| A parent naming nothing | An error the repair step can fix. Failing that, the item is attached to the candidate above it sharing the most of its graph trace — and left unattached if it shares none, rather than guessed. |
| An outcome that is its one capability reworded | A warning, checked by comparing evidence spans and content-word overlap. Reported, never silently dropped. |
| Ids | Stamped once, per tier (`BR-001`, `FR-002`, `RULE-001`). The gate, the document, the matrix, and the JSON all name a requirement the same way. |

Earlier versions carried seven types kept separate from four document sections, so an item could be
filed in one place and typed another. Across repeated runs on one unchanged codebase the same
findings came back typed entirely functional, then mostly business, then a mix. Three tiers, with
the section being the tier and the chain checked at the gate, is a classification a reader can rely
on.

A success criterion is **not** a tier. It is an observable condition attached to the requirement it
validates, rendered as an acceptance criterion beneath it and published as `success_criterion`.

### Requirement families alongside the chain

Four families sit beside the chain rather than inside it, because they assert genuinely different
things and folding them into the functional tier both mislabelled and hid them. None takes a
parent — an integration is depended upon, not "realised by" anything.

| Family | Id | What it asserts |
|---|---|---|
| Data | `DATA-nn` | An invariant the stored data must hold: a declared vocabulary, a uniqueness guarantee, an identifier scheme |
| Integration | `INT-nn` | An external system depended on, what it is used for, and the behaviour when it is unavailable |
| Non-functional | `NFR-nn` | A quality property the code fixes concretely — a timeout, a cap, a concurrency guard. Never an aspiration |
| Reporting | `RPT-nn` | A view, export, or summary someone reads, with any ordering the evidence fixes |

Functional requirements are grouped into **areas** named by the fan-out worker that found them, so
their ids read `FR-INT-01`, `FR-VAL-07` — the form a reviewer cites. Each carries a **priority**
derived from the code's own defaults (`M` always-on, `S` behind a flag or role gate, `C` legacy or
opt-in), never from a stakeholder ranking.

### Evidence is published in the document, not only beside it

Every requirement row carries an Evidence cell naming the file, line range, and symbol it came
from. For a reverse-engineered document the citation *is* the warrant for the claim: a reader
checking whether a requirement was invented should not have to open a second file. The statement
itself still has to read as business language — the citation belongs in the Evidence cell, never
in the sentence a stakeholder signs off.

## Grounding rules the code enforces

- A requirement must reach an interaction boundary. A claim traced only to internal processing is
  rejected, however sophisticated the logic looks.
- Every citation is re-resolved against the real file system. Checking that the *file* exists is
  not enough: a fabricated line number inside a real file, or a symbol nowhere near the cited
  line, is caught too.
- **No technical vocabulary in a requirement statement.** Enforced by a linter
  (`capabilities/jargon.py`), not by prompt instruction — it catches file paths, extensions,
  identifiers, route paths, framework names, and any qualified name present in the graph.
- Confidence and provenance reflect real extraction certainty. A branch raising an HTTP error
  scores 0.85; a bare conditional scores 0.72; an unresolved call is `AMBIGUOUS` at 0.4.
- An empty requirements set is a valid outcome **when the document says why**. A repository with
  no interaction boundary is a library, and saying so beats inventing a purpose.

## Current limitations

Stated plainly, because the workflow's whole value is not overstating things:

- **Deep extraction covers Python and SQL schema.** A repository with no Python still fails Gate 1
  closed with `NO_ANALYSABLE_SOURCE` rather than guessing from file names. Other languages are
  inventoried only — notably TypeScript/JavaScript, so a React front end contributes its files but
  not its routes or its declared vocabularies. Adding a language means writing a new extractor
  capability and pointing a manifest at it; nothing under `src/agentic/graph/` changes.
- Reflection, string-based plugin discovery, and runtime dependency injection remain invisible to
  static analysis. A handler *passed as a value* is now followed; one looked up by name from a
  string at runtime is not. Endpoint detection still needs a literal path string.
- Call resolution is name-based with no type inference, so same-named methods on unrelated types
  stay ambiguous — and are labelled as such.
- Nothing is executed and no tests are run. "Implemented" means present and reachable in the
  source, not verified correct at runtime.

Three more belong to the consolidation stage specifically, and each is a limit on *judgement*
rather than on extraction:

- **Grouping is only as good as the names the workers invent.** Headings that mean the same thing
  are folded together, and a heading that narrows another is folded into it — so eleven ways of
  saying "candidate management" become one. Two groups that are genuinely one part of the product
  under two *unrelated* names are not detected, because nothing here knows the domain well enough
  to say they are the same. That is the honest boundary: the fold is a lexical judgement, and a
  semantic one would need to be told what the product is.
- **The top tier is the weakest link.** Outcomes are written by one model call reading area names
  and requirement titles, and under a large digest it reliably returns capability titles back as
  outcomes. Those are refused deterministically — an outcome that names a capability below it, or
  that stands over fewer than two, is not published — so the failure mode is a document with too
  *few* outcomes rather than invented ones. A large repository will often produce none, and say so.
- **Deduplication compares wording and evidence, never meaning.** "Ask about a document" and
  "inquire about a document" cite the same line and are one capability; nothing here can see that,
  because seeing it means knowing the two verbs are synonyms in this domain. Identical titles are
  folded at the end as a backstop.

## Verifying a run

The test suite proves the machinery. It does not prove any individual requirement is *right*.

For that: open `traceability_matrix.csv`, pick three rows at random, and confirm the cited file
**and line range** actually say what the requirement claims. The Requirements tab in the UI does
this for you — every claim expands to show the real source at the cited lines.

## Layout

```
app.py                       Streamlit UI
codebase-requirement-skills/ codebase -> requirements pack (6 wired skills + pack.yaml)
test-generation-skills/      requirements -> test cases pack (5 skills + pack.yaml)
examples/                    sample archive used by the test suite
src/agentic/
  config.py  state.py  store.py  runtime.py  cli.py
  schemas/       core graph types, BRD types, test-spec types, schema registry
  skills/        SKILL.md loader, pack manifest + validation
  capabilities/  ingest, graphify, graph_ops, entry_points, boundaries, clustering,
                 assemble, claims, consolidate, resolution, correspondence, confidence,
                 hedging, jargon, leakage, quarantine, gates, verify, render, registry
    testgen/     the second pack's capabilities: ingest, qualify, analyse, batching,
                 assemble, dedupe, coverage, quality, signals, validate, gates, render
  llm/           Azure client, prompt assembly, generic model step
  graph/         manifest -> StateGraph, node factories, deterministic router
tests/           680 tests; the model is stubbed, everything else is real
runs/            per-run artifacts and the LangGraph checkpoint database (generated)
```

# Files — what each one is for and why it matters

A significance map of the repository. For every file: what it is, and **what breaks if it is wrong
or missing**.

This is deliberately not an API reference — `MDfiles/SRC.md` and `MDfiles/CodeBase.md` already
describe what each function does. This answers a different question: *how load-bearing is this file,
and what depends on it being right?*

## Criticality legend

| | Meaning |
|---|---|
| 🔴 | **Load-bearing.** The system cannot run, or cannot be trusted, if this is wrong. |
| 🟠 | **Core.** A whole capability or an entire requirement family is lost. |
| 🟡 | **Supporting.** Output quality degrades, usually silently. |
| ⚪ | **Peripheral.** Convenience, generated output, or scaffolding. |

Totals: **18,119** lines of Python in `src/`, **12,346** in `tests/`, **3,013** across the two packs,
**781** in the UI.

---

## If you only read five files

In this order — each is the shortest path to the concept the next one assumes.

| # | File | Why this one |
|---|---|---|
| 1 | `src/agentic/skills/pack.py` | the manifest *is* the workflow; everything else is downstream of this shape |
| 2 | `src/agentic/graph/builder.py` | 129 lines that turn that manifest into the whole execution graph |
| 3 | `src/agentic/schemas/core.py` | the knowledge graph and `SourceLocation` — the vocabulary every capability speaks |
| 4 | `codebase-requirement-skills/pack.yaml` | the actual pipeline, with the reasoning for every step in its comments |
| 5 | `src/agentic/capabilities/graph_ops.py` | where cost is bounded and evidence packets are built |

---

## Root

### `pyproject.toml` — 43 lines 🔴
Packaging, the `agentic` console script, the dependency set. Its comments carry two facts you would
otherwise learn the hard way: `graphifyy[sql]` is **not optional in practice** (without the extra,
`.sql` files contribute nothing and a schema-bearing repository silently loses its tables), and the
PyPI name carries a double *y* while the command it installs is `graphify`.

Also sets `python_classes = []` for pytest, or the collector treats `TestCase`, `TestSuite`,
`TestScenario` and `TestDesignFragment` as test classes purely because of the `Test` prefix.

**Significance:** the dependency *ranges* here are the biggest unmanaged risk in the project —
`graphify.py` is written against measured behaviour of one extractor version and pinned loosely.

### `.env` — not committed 🔴
Four `AZURE_OPENAI_*` values plus optional overrides for `runs_dir`, `max_clusters`,
`graphify_binary` and the thirteen consolidation toggles. **Without all four Azure values, any pack
containing an LLM step refuses to start** — by design, in `runtime.preflight()`.

### `app.py` — 781 lines 🟠
The Streamlit UI. The tab that justifies the product is **Requirements**: every claim expands to show
the real source at the cited lines.

Two details matter more than the framework choice. `_views()` builds tabs from the **artifacts on
disk**, so each pack gets the tabs it produced and a third pack needs no change here.
`chain_panel()` offers the next pack as one primary button above the results and reads the successor
from the *manifests* — so this file names no pack.

**Significance:** not on the critical path (the CLI does everything), but it is where a reviewer
actually verifies a run, which is the only thing that makes the output trustworthy.

### `README.md` — 376 lines 🟡
Unusually honest: it states the limitations plainly and tells you how to verify a run. Note it claims
both "200 tests" and "680 tests" in different places.

### `.gitignore`, `myname.txt`, `myname1.txt` ⚪
The two `.txt` files are scratch and carry no meaning.

---

## `src/agentic/` — the core

### `config.py` — 110 lines 🔴
Every tunable in one place. The numbers that shape behaviour: `evidence_packet_chars` 40,000 ·
`max_clusters` 80 · `llm_max_output_tokens` 8,000 · `graphify_timeout_seconds` 600 · the archive
limits · and **thirteen booleans** that each switch off one consolidation pass so its effect can be
measured in isolation.

`llm_configured` is deliberately all-or-nothing: *"A partial configuration must fail before the graph
starts rather than part-way through, which would leave a half-written document behind."*

**Significance:** the comments record that six of those thirteen passes once shipped switched off and
three had no manifest step at all — *"which is what made the output a behaviour inventory."* Changing
a default here changes the shape of every document produced.

### `state.py` — 100 lines 🔴
`AgentState` and its channel reducers. Nothing in it mentions requirements or tests — steps exchange
*artifacts*, logical names mapped to paths.

The critical decision is in the docstring: **LangGraph checkpoints the entire state after every
superstep**, so large payloads live on disk and state carries only paths. `fragments` is the one
many-writer channel; `work_item` is written by the dispatcher and only read by a worker, so parallel
branches cannot conflict over it.

**Significance:** put a knowledge graph inline here and every node re-serialises it.

### `store.py` — 68 lines 🔴
`ArtifactStore`. Sixty-eight lines, and the reason capabilities are unit-testable: they read prior
work through the store rather than through graph state, so a test builds a context around a temp
directory and calls the function.

### `runtime.py` — 197 lines 🔴
Everything that must happen before state exists: pack discovery, manifest loading, `preflight()`,
`prepare_run()`, `stream_run()` / `run()`, `successor_packs()`.

Two significant choices live here. The target is **snapshotted**, so a run is reproducible and the
tree cannot change under the analysis. And because `prepare_run` accepts *any* directory, pointing
PACK 2 at a PACK 1 run's `artifacts/` folder works with **zero** additional code — that is what made
pack chaining free.

**Significance:** `DEFAULT_PACK_ROOT = Path(__file__).parents[2]` makes pack discovery relative to the
repository root, so an installed wheel finds no packs. Worth knowing before packaging.

### `cli.py` — 92 lines 🟡
`agentic packs | show | run`. `show` prints the compiled topology, the fastest way to see what a
manifest actually built. Accepts a directory or a `.zip` — it used to accept only a directory, so the
two entry points analysed different things from the same input.

### `__init__.py` — 8 lines ⚪
Version string and a docstring stating the thesis.

---

## `src/agentic/graph/` — execution

### `builder.py` — 129 lines 🔴
**The most important file for understanding the architecture.** It compiles a manifest into a
`StateGraph` and **names no step, no skill and no artifact** — the entire "workflows are data" claim,
in 129 lines.

Four cases cover every pack: a fan-out step is entered by parallel dispatch *from its predecessor*
(because `Send` must be returned by a router); a fan-out step's own outgoing edge is a plain edge,
which gives the fan-in barrier for free; a gate gets a conditional edge with pass/repair/exhausted/
halt targets; everything else gets onward-or-halt.

**Significance:** if this file ever needs to know a step name, the abstraction has leaked.
`tests/test_extensibility.py` exists to detect exactly that.

### `router.py` — 99 lines 🔴
Deterministic routing. The docstring states the rule: *a gate must not be reasoned past.* A
supervisor LLM choosing the next node could route around a failed gate because continuing looked more
helpful; here it **structurally cannot**.

**Significance:** 99 lines standing between this system and the most common failure mode in agentic
software. Do not put a model in this file.

### `nodes.py` — 295 lines 🔴
Three node shapes plus halt and finalise — and, more importantly, **all the failure semantics**. A
capability that raises under `warn_and_continue` must not take the run down. A fan-out worker that
fails must not discard every other boundary's analysis. A repair step that fails must not destroy the
run it exists to rescue, and its attempt is counted anyway so the loop still terminates.

**Significance:** every "the run died and nobody knows why" bug lives or dies here. The halt node
writes `run_failed.md` with the failing gate's first three errors, because the reason was previously
truncated to nothing.

---

## `src/agentic/skills/` — the pack abstraction

### `pack.py` — 286 lines 🔴
`PackManifest`, `StepSpec`, `StepMode`, `OnFail`, discovery, and **eager validation**. Every skill,
capability and schema a manifest names is resolved at load time, and `consumes` is checked against
what earlier steps `produce`.

**Significance:** that last check is what allows `CapabilityContext.artifact_path` to treat a runtime
miss as a genuine defect and raise, rather than every capability defensively handling `None`. A
manifest typo fails in milliseconds instead of twenty minutes into a run.

### `registry.py` — 131 lines 🟠
`SkillRegistry` and `LoadedSkill`. Loads `SKILL.md` from disk, caches on **mtime** (so editing a skill
takes effect on the next run with no restart), extracts a named section, and computes a **sha256**
recorded in every `StepRecord` — including for deterministic steps whose skill is implemented in
Python.

**Significance:** that sha is how drift between the prose and the code becomes auditable rather than
invisible. A small idea doing a lot of work.

---

## `src/agentic/llm/` — the only place a model is called

### `steps.py` — 100 lines 🔴
One generic executor for **every** LLM step in **every** pack. What differs is the skill loaded, the
packet assembled and the schema enforced — all named by the manifest. Handles three failure modes: a
wrong shape retries; a length-limit failure appends `SHORTEN_INSTRUCTION` and retries with a smaller
ask; anything else raises with the step named.

**Significance:** the length path matters more than it looks. A truncated *structured* reply cannot be
parsed at all, so the whole cluster is lost rather than shortened — and for a fan-out worker that loss
is silent unless something asks again for less.

### `prompt.py` — 336 lines 🔴
System-prompt assembly, and **the artifact renderers that decide how much of each artifact the model
may see**. This is where the cost model is enforced.

Two renderers carry the hardest-won lessons. `_render_graph` returns a **summary only** — *"The whole
graph must never enter a prompt — that is precisely the cost failure this architecture exists to
prevent."* `_render_brd` returns **only the rejected claims**, because handing a repair step the whole
document blew the input cap on the way in and the output ceiling on the way out, so a rejection over
one phrase could delete most of the analysis.

**Significance:** change a renderer carelessly and you either blow the budget or hand the model
something it can fabricate from.

### `client.py` — 51 lines 🟡
`AzureChatOpenAI` construction, `lru_cache`d so a client is reused across steps. Entirely Azure-shaped
— the seam is in the right place, but the module is not provider-polymorphic.

---

## `src/agentic/schemas/` — the contracts

### `core.py` — 168 lines 🔴
`Entity`, `Relationship`, `KnowledgeGraph`, `SourceLocation`, `GateReport`, `Provenance`, `Severity`.
Pack-agnostic by design.

The most consequential detail is the **`symbol` / `scope` split**: `symbol` asserts *a name that
appears at these lines* and verification re-reads the file to confirm it — the anti-fabrication check
— while `scope` names the enclosing definition, whose header may be hundreds of lines above, and is
never line-checked. *Collapsing the two makes every statement deep inside a long function look
fabricated.*

**Significance:** this is the vocabulary every capability speaks. Changing it changes everything.

### `brd.py` — 1,057 lines 🔴
The requirements output model **and the entire classification taxonomy**: `BASIS_TYPES`,
`TIER_SECTIONS`, `TIER_PREFIXES`, `PARENT_RULES`, `SUPPLEMENT_PREFIXES`, `PRIORITIES`,
`TIER_ALIASES`, plus `assign_ids()` and parent resolution.

Most of its length is **English, not code** — field descriptions are handed to the model verbatim as
JSON-schema descriptions under `.with_structured_output`, so they are prompt as much as type.

The design at its centre: `abstraction_basis` (the grounds) is asked first and *determines*
`requirement_type` (the tier) through a total function. *Asking for the grounds first makes "it exists
as a function in the code" an answer that leads somewhere other than the requirements list.*

**Significance:** this file is why over-extraction stopped being the path of least resistance.

### `testspec.py` — 474 lines 🟠
The test specification model. Names no framework, runner, protocol or language — *"a model that has
already decided it is talking HTTP cannot become any of the others."* Three fields exist purely for a
future adapter (`interface_hint`, `automation_readiness`, `TestInput.value_kind`) with an explicit
rule attached: **no logic in this pack branches on any of them.**

**Significance:** this is the contract a UI-test adapter would consume. Read it first if that is your
assignment.

### `testgen.py` — 216 lines 🟠
PACK 2's **input** model — a defensive reader of `requirements.json`. Imports nothing from `brd.py`,
deliberately: *"a downstream pack that reached into its internal models would be coupled to decisions
it does not control."* Holds `CONTRACT_KEYS` and the detection of PACK 1's self-generated acceptance
criteria, done two independent ways "because either signal alone could drift".

**Significance:** the inter-pack seam, and the template to copy for a third pack.

### `boundaries.py` — 72 lines 🟠
What the boundary classifier may return. Small, and one field carries most of the weight:
`BoundaryClass.NONE`. *"Without it the model has no way to decline and will promote helpers to
boundaries."*

### `registry.py` — 43 lines 🟡
Name → pydantic model for `output_schema:`. Hard-codes both shipped packs, so a third pack must edit
it — one of the two avoidable leaks in the "adding a pack is a data change" claim.

### `__init__.py` — 31 lines ⚪
Re-exports.

---

## `src/agentic/capabilities/` — PACK 1, the work

Listed in pipeline order rather than alphabetically, because that is how they make sense.

### `registry.py` — 163 lines 🔴
`@capability`, `CapabilityContext`, `CapabilityResult` — the Strategy pattern that keeps graph code
pack-agnostic. A manifest names a capability by string; the builder resolves it here and never
imports the implementing module.

**Significance:** `load_builtin_capabilities()` hard-codes imports of **both** shipped packs. A third
pack must add a line here — the second of the two avoidable abstraction leaks.

### `ingest.py` — 278 lines 🔴
Phase 0: safe archive extraction and the file inventory. **Every archive check runs across the whole
member list before a single byte is written**, so a malicious entry late in the archive cannot leave a
partially-extracted tree behind. Rejects traversal, absolute and drive-qualified paths, duplicates,
encryption, symlinks, implausible compression ratios.

`PARSEABLE_LANGUAGES` is the deep-analysis set; **if nothing matches, the run halts** rather than
guessing requirements from file names.

**Significance:** the security boundary of the whole system, and the first fail-closed gate.

### `graphify.py` — 1,028 lines 🔴
Runs the external extractor and normalises its output into `KnowledgeGraph`. The largest file in the
project, and roughly 600 of those lines exist to recover what Graphify does not emit: `classify_kind`
(there is no `kind` field), `_derive_line_ends` (there is no line range), and `_module_surface` (a
script-style UI declares its whole interaction surface as module-level calls, which a
definition-modelling extractor sees as nothing at all).

Carries the measured workarounds: the Go grammar reports `_callable: false`, so classifying on that
flag alone put every Go function under `SYMBOL` and produced an empty document.

**Significance:** the single most fragile file — reverse-engineered from one version of a third-party
tool's undocumented output, with nothing testing it against the real binary. **It is also where the
declaration layer is currently missing** — see `improvements.md` Tier 0.

### `graph_ops.py` — 836 lines 🔴
`GraphBuilder`, `GraphIndex`, `compact_entity`, `build_packets`, `_stratified`, and
`build_boundary_clusters`. **This is where cost is bounded.** Each packet is a walk from one boundary
(6 hops, 250 nodes) serialised into 40,000 characters.

`_stratified` is the most instructive function in the file: a plain `[:limit]` removed *whole classes*
of boundary because ties collapsed to an alphabetical sort — 147 state-changing endpoints were never
planned because they sorted after the reads.

**Significance:** change a bound here and you change both the bill and the coverage.

### `boundaries.py` — 323 lines 🟠
Proposes boundary candidates from graph shape — primarily **in-degree zero on `CALLS`**, a shape that
holds for every framework rather than a list of frameworks. Attaches the evidence a reader would use,
including string literals within three lines of the definition *and of every registration site* —
which is what lets a boundary be called `POST /orders` rather than the name of the function behind it.
Also owns `collect_boundary_assignments`, the deterministic reduce step that merges every
classification batch's reply back into one flat list (added 2026-09-18, see B3 in `flow.md`).

**Significance (fixed 2026-09-18):** ~~the `MAX_CANDIDATES = 120` cap here has the same
categorical-truncation bug that `_stratified` fixed in `graph_ops`, and reports far less about what
it dropped.~~ `MAX_CANDIDATES` is now `MAX_CANDIDATES_PER_BATCH`: the same value (120), but a batch
size rather than a hard cap. Every candidate is classified — split across batches and fanned out to
`classify_boundaries` one call per batch, the same mechanism `build_boundary_clusters` uses for
`synthesize`. Nothing is silently dropped any more; see `flow.md` §10 B3 for the before/after and
`changes/batch_boundary_classification.md` for the full diff.

### `entry_points.py` — 415 lines 🟠
The adapter seam and the coverage report. `@entry_point_adapter` registers an adapter **and extends
`BOUNDARY_KINDS`** with what it declares — those two facts belong together, because a class the
planner will not seed a cluster from is a class whose adapter cannot produce a requirement.

`ClassifiedBoundaryAdapter` turns the model's answers into entities: it resolves the returned name
against the graph and takes the location from the entity it found, so a hallucinated name is dropped.

**Significance:** this is the extension point for OpenAPI ingestion and JS/TS routing — the two
highest-value coverage wins available.

### `gates.py` — 724 lines 🔴
Both deterministic gates. Gate 1 checks graph integrity and re-verifies every entity location against
the file system; Gate 2 re-checks every claim independently of how it was produced — citations
re-resolved, graph paths re-resolved, boundary reachability, jargon, and the classification chain.

**Significance:** the severity choices here *are* the policy. `UNRESOLVED_PARENT` is a warning
because, treated as an error, it cost the **child** its place — one rejected claim taking a grounded
one down with it, and then that one's children in turn.

### `verify.py` — 166 lines 🔴
`verify_location` — the anti-fabrication check. Resolves the path inside the source root,
bounds-checks the line, and confirms a cited symbol appears within `SYMBOL_WINDOW = 25` lines. Also
`read_slice`, which is what the UI uses to show real source beside a claim.

**Significance:** 166 lines carrying the system's core promise. Everything else is bookkeeping around
this function being right.

### `clustering.py` — 486 lines 🟠
Repository-wide semantic clustering — the stage whose absence made the output a behaviour inventory.
Every earlier comparison required two candidates to **share code**, and two workers describing one
capability from the entry point and from the work behind it cite different lines *by construction*.

**Significance:** deterministic and evidence-blind, with nine tuned thresholds. It cannot see
synonyms — the known limit, and the best candidate for an embedding hybrid.

### `assemble.py` — 632 lines 🔴
`digest_areas` and `assemble_requirements` — the deterministic merge that replaced the old `reduce`
model call. Every grounded finding is carried through unchanged; `ground_outcomes()` derives each
outcome's citation from the capabilities beneath it and is idempotent so it can be re-run after every
change to the set.

**Significance:** the metric `findings_carried` exists here specifically because that number used to
vanish silently.

### `consolidate.py` — 940 lines 🟠
Seven ordered passes: rule classification, abstraction, criteria extraction, same-tier merge,
cross-tier merge, parameterised families, hierarchy validation. The ordering is argued in the
docstring and matters — criteria are extracted *before* the merge, or a success condition gets
absorbed into the requirement it was proving.

**Significance:** `_domain_bearing()` lives here, and it currently cannot return `True`, which demotes
100% of business rules in every run. The single highest-impact defect in the system.

### `correspondence.py` — 590 lines 🟠
The only check that compares a statement to **code** rather than to another statement or a word list.
Six registered checks plus one batched entailment call, with an **objective / interpretive** split: a
check that can be wrong makes a claim an open question rather than deleting it.

**Significance:** that split is the sharpest self-awareness in the codebase — the synthesis skill
demands business language and the anchoring check compares against code vocabulary, so the two are in
tension *by design*.

### `confidence.py` — 235 lines 🟡
Recomputes confidence from evidence, keeping the model's own number in `model_confidence` so the
recalibration can be judged. Ceiling 0.95 *because nothing here has been reviewed by a person.*

**Significance:** currently saturating — every run reports min = max = 0.95, so the review list is
empty. See `improvements.md` 1.1.

### `leakage.py` — 347 lines 🟡
Strips implementation vocabulary from statements, using a vocabulary drawn from **the repository
itself** rather than a word list. Refuses the rewrite when the result would be under six words or
grammatically mangled — *a requirement nobody can read is worse than one that names a route.*

### `hedging.py` — 275 lines 🟡
Populates assumptions, open questions and missing acceptance criteria — all derived from evidence
already gathered, never invented. Where no observable outcome exists it records the question instead
of a criterion that would check nothing.

**Significance:** its fallback criteria are exactly the ones PACK 2 then detects and discards — a
closed loop of wasted work worth revisiting.

### `resolution.py` — 792 lines 🟠
Gives every candidate exactly one disposition and partitions what cannot be published into
`unresolved_candidates`. Also `validate_synthesis`, the final structural check — the only pass that
can see the finished set, because every stage before it changes it.

**Significance:** "nothing is deleted" is implemented here. It is what makes aggressive checking safe.

### `claims.py` — 721 lines 🔴
Deterministic preparation before the gate: settle the tier, make classification coherent, file by
tier, correct stale relations, drop restatements, re-ground outcomes, **stamp ids**, resolve parents.
Also `apply_repairs`, which merges repaired claims by id and overwrites everything a repair is not
allowed to change.

**Significance:** ids exist from here onward. Before this step every item's id is `""` — which is why
the entailment call keys on batch position rather than on id.

### `jargon.py` — 282 lines 🟡
The technical-vocabulary linter. *"A prompt instruction is not a check."* Every nuance in it is a bug
someone hit: `.pdf` allowed (it is what the product accepts), `process` allowed (ordinary business
English), a path needing both a slash and a dot, and `_COMMON_WORDS` so a rejection over "update"
does not produce *"A user shall be able to the next action."*

### `quarantine.py` — 277 lines 🟠
Runs only when the repair budget is spent, and draws the distinction that matters: a **grounding**
failure deletes the claim; a **wording** failure strips the term and keeps it. Measured consequence of
getting it wrong: *"fourteen functional requirements entered the gate and one left."*

### `render.py` — 1,252 lines 🟠
Eight deliverables. Re-runs the jargon linter at the moment of publishing, because this is the
artifact that leaves the building. `_uncovered_files` is the quietly valuable part: coverage counted
by construct hides the shape of what is missing, so it reports **files** no requirement describes.

### `__init__.py` — 17 lines ⚪
Re-exports the registry.

---

## `src/agentic/capabilities/testgen/` — PACK 2

In pipeline order.

### `ingest.py` — 506 lines 🔴
The pack's only contact with PACK 1's output, and the only step that reads `source_root`. **Nothing
raises** — every problem becomes a `ReadIssue` carrying the array position, so a malformed artifact
produces a thinner set and an explanation rather than a stack trace. Unknown keys are preserved, so a
future upstream field cannot break ingestion.

**Significance:** the template for how any future pack should read an upstream artifact.

### `qualify.py` — 343 lines 🔴
Bands every requirement before anything is designed, deterministically, because *"a model asked to
design tests will always find something to say."* Produces a record for **every** requirement
including the excluded ones — a coverage report that only accounts for what was tested cannot answer
"why is there no test for this".

Also where the duplicate check is written against `SequenceMatcher`'s documented upper bounds, because
the naive form cost ~10 seconds on a 190-item set.

### `signals.py` — 139 lines 🟠
Which dimensions a requirement activates. **The anti-explosion mechanism.** `positive` is free;
everything else must be earned by a regex over the statement and its *usable* criteria.

**Significance:** the tuning notes are the valuable part — three patterns were narrowed after
measurement, each for a reason. `authorization` once fired on 34 requirements through the single word
"administrator", and generating an authorization case from that *invents a rule the requirement never
states*.

### `analyse.py` — 263 lines 🟠
Risk, routing and case budget — all computed before the first model call, so the designer works inside
a plan it did not make. Measured: the internal-behaviour routing rule alone takes 184 testable
requirements from 485 projected cases down to 280.

### `batching.py` — 157 lines 🟡
Bounded packets grouped by **area**, so the requirements in one call are about the same part of the
product and can share preconditions. Its docstring carries a trap worth knowing: the packet must sit
under the key `packet` because that is the generic fan-out contract — put it elsewhere and every
worker is handed `None`, which arrives as an empty suite rather than an error.

### `assemble.py` — 274 lines 🔴
Stamps ids, computes derived fields, resolves each case's coverage link against the criteria its
designer was actually shown, and **drops any case claiming a dimension its requirement does not
activate**. Records a requirement that was planned for and came back with nothing as `undesigned` —
*a worker that says nothing about a requirement is a gap, not a requirement that needed no tests.*

### `quality.py` — 157 lines 🟠
The lexicons and similarity thresholds, held as data so a false positive is a one-line edit.
`FRAMEWORK_WORDS` is word-boundary matched because substring matching flagged `PYTHONUNBUFFERED` as
the language "python", the repair pass could not remove it, and four model calls were spent before a
valid case was pruned.

### `dedupe.py` — 152 lines 🟡
Folds cases that check the same behaviour twice, before the gate — a duplicate is an arithmetic fact,
and asking a model which to keep invites it to keep both. A folded id is recorded on the case that
absorbed it.

### `coverage.py` — 176 lines 🟠
Accounts for every requirement and every criterion. The verdict that earns its place is
**`positive_only`**: a requirement stating a refusal, an error or a limit that got only a happy-path
case. It reads as covered and the behaviour most likely to be wrong has no test at all.

### `gates.py` — 149 lines 🟡
The input gate. **One** blocking question — is there anything here worth generating tests from — and
everything else a warning, because a thin input should still produce an honest thin suite.

### `validate.py` — 437 lines 🟠
The quality gate, its repair loop, and the final check. Errors are **exactly** the findings a repair
pass could act on, given it sees only the rejected cases; coverage gaps are warnings, because no
rewording can create a missing case. *Making an unfixable finding blocking would spend the whole
repair budget failing.*

### `render.py` — 557 lines 🟠
Six deliverables. Re-runs `framework_leaks` at publishing time, because `test_specs.json` is the file
an adapter will consume and therefore the one where the independence claim actually matters. Half the
value is in `excluded_requirements.md` and `clarifications.md` — what will not be tested, and what has
to be answered first.

### `__init__.py` — 6 lines ⚪
A docstring explaining why this is a subpackage: the two packs share the capability registry and
nothing else, so keeping them apart makes the boundary visible in the file tree as well as the
manifest.

---

## `codebase-requirement-skills/` — PACK 1 as data

### `pack.yaml` — 625 lines 🔴
**The pipeline.** 26 steps, and far more than a configuration file: roughly half its length is
comments explaining *why* each step exists and what broke without it, plus ~190 lines of
`instructions:` for the `synthesize` step alone that function as a second skill.

**Significance:** this is the most information-dense file in the repository. Read it before any
capability module — it tells you what each one is for. Note that its prompt content is versioned with
the manifest but **not** covered by the `skill_sha256` provenance trail, unlike the `SKILL.md` files.

### `codebase-to-requirements-reverse-engineering/SKILL.md` — 259 lines 🔴
The **master skill**, prepended to every LLM step in the pack, establishing the grounding rules once
rather than per step.

### `evidence-grounded-requirement-synthesis/SKILL.md` — 460 lines 🔴
The largest skill, governing the fan-out workers — evidence precedence, what counts as an interaction
boundary, the classification model, how a statement is written, where a conditional belongs.

**Significance:** the single biggest lever on output quality. Editing it changes behaviour on the next
run with no restart.

### `graphify-knowledge-graph-extraction/SKILL.md` — 252 lines 🟠
Governs extraction and boundary classification. Its "Classifying a boundary candidate" section is
injected into the classifier step by `section:`.

### `requirement-consolidation-and-resolution/SKILL.md` — 198 lines 🟠
Governs the consolidation band. Six of its sections are injected individually into six different
deterministic steps — which is what the sha256 provenance trail is for, since those steps implement
the skill in Python.

### `requirement-and-graph-validation-gates/SKILL.md` — 124 lines 🟠
Gate 1 and Gate 2 as prose. The source of the rule the router enforces structurally: *a gate must not
be reasoned past.*

### `traceable-requirements-deliverables/SKILL.md` — 170 lines 🟡
The document shape — which sections exist and in what order.

### `advisory-routing-orchestration/SKILL.md` — 183 lines ⚪
**Not wired.** No step names it and no code path loads it; its own header says so. It documents the
road not taken — a model that *proposes* the next step and a deterministic router that may overrule.
Keep it for the reasoning; be careful nobody wires it up by accident.

---

## `test-generation-skills/` — PACK 2 as data

### `pack.yaml` — 290 lines 🔴
15 steps. Declares `consumes_pack: codebase-requirements` and `consumes_artifact: requirements.json`
— the chaining declaration, made by the **downstream** pack so nothing upstream needs to know it
exists. Its header comment is also where the missing third pack is described: *"framework-specific
adapters are a later, separate pack."*

### `requirements-to-test-derivation/SKILL.md` — 88 lines 🟠
The master skill for PACK 2. Its "The input is imperfect, and you are told how" section is the posture
that makes the whole pack robust.

### `risk-based-test-design/SKILL.md` — 96 lines 🟠
Governs the one model step in the pack. What makes a case real; what gets a case rejected; how to
spend the budget.

### `test-input-qualification/SKILL.md` — 92 lines 🟠
The four bands, and "the criteria trap" — why PACK 1's self-generated criteria must be discarded.

### `test-coverage-and-duplication-gates/SKILL.md` — 104 lines 🟡
Deduplication, the gate, and what a rejection may and may not cost.

### `traceable-test-deliverables/SKILL.md` — 72 lines 🟡
The six documents and what the plan leads with.

---

## `tests/` — 12,346 lines

### `conftest.py` — 216 lines 🔴
The stub seam. `run_step_or_stub` is where the model is substituted; everything else — file system,
graph construction, gates, rendering — is real. Also defines `EXTRACTION_STEPS`, the six steps every
test needs to get a usable graph, *"because a graph without boundaries makes every downstream
assertion vacuous."*

**Significance:** if a capability bypasses this seam and calls the client directly, the suite makes
live API calls. `correspondence._entail()` carries a comment recording exactly that happening.

### `test_extensibility.py` — 190 lines 🔴
**The architecture's own acceptance test.** Authors a genuinely new pack at runtime — two `SKILL.md`
files and a `pack.yaml` — registers two capabilities, compiles and runs it. *"If these tests ever
need a change under `src/agentic/graph/` to pass, the abstraction has leaked."*

### `harness/metrics.py` — 611 lines 🟠 and `harness/__main__.py` — 225 lines 🟠
The measurement harness: `python -m tests.harness` prints the corpus metric table and the delta
against a **frozen baseline**; `--freeze` rewrites it. Structure and pipeline baselines are kept apart
because *"the two answer different questions and move for different reasons."*

**Significance:** for a system whose output is prose, unit tests prove only the machinery. This is the
only mechanism that makes a *quality* regression visible. Extending it with document-quality metrics
(tier distribution, rule survival, confidence spread) would have caught every Tier 0 defect in
`improvements.md`.

### `test_e2e.py` — 499 lines 🟠
The full pipeline against a fixture, model stubbed.

### `test_graphify_adapter.py` — 532 lines 🟠
Guards the reverse-engineered extractor contract — kind classification, line-end derivation, the Go
`_callable` workaround. **Note:** it tests the *adapter* against a recorded shape, never the real
binary. That gap is `improvements.md` 0.4.

### `test_consolidate.py` (899) · `test_consolidation_stage.py` (550) 🟠
The largest test modules, for the most judgement-heavy stage. Where a threshold change gets caught.

### `test_requirements_json.py` — 615 lines 🔴
Guards the **inter-pack contract**. If you change the published requirement shape, this is what tells
you PACK 2 will break.

### `test_testgen_pack.py` (554) · `test_testgen_ingest.py` (483) · `test_testgen_quality.py` (470) · `test_testgen_design.py` (405) · `test_testgen_qualify.py` (311) · `test_testgen_render.py` (260) 🟠
PACK 2's coverage, matching its pipeline stage for stage.

### `test_gates.py` (419) · `test_claims.py` (313) · `test_correspondence.py` (314) · `test_resolution.py` (373) · `test_hedging.py` (324) · `test_confidence.py` (258) · `test_assemble.py` (395) · `test_synthesis.py` (404) 🟠
One module per capability, each asserting the *behaviour the comments describe* rather than the
implementation.

### `test_boundary_classification.py` (321) · `test_entry_point_adapters.py` (247) · `test_module_surface.py` (233) · `test_scope_locations.py` (128) 🟠
The extraction-side behaviours that are easiest to break silently.

### `test_pack_compile.py` (195) · `test_pack_chaining.py` (149) · `test_schema_compatibility.py` (60) 🟡
The manifest machinery, the chaining declaration, and schema round-tripping.

### `test_apply_repairs.py` (151) · `test_jargon.py` (70) · `test_ui_sections.py` (81) · `test_requirement_set_fixtures.py` (138) · `test_harness_baseline.py` (214) · `test_harness_scored.py` (235) 🟡
Focused guards on the repair merge, the linter, the UI's section rendering, the five input fixtures,
and the harness itself.

### `fixtures/corpus/` + `fixtures/corpus_truth/` 🟠
Four ground-truthed fixtures (`py_service`, `py_cli`, `py_worker`, `go_service`). **Ground truth lives
outside the analysed tree** — keeping it inside made every fixture report a JSON file it does not
contain, and fed the harness's own answer key to the extractors.

### `fixtures/requirement_sets/` 🟠
Five shapes of PACK 2 input: `rich`, `thin`, `degenerate`, `malformed`, `legacy_20key`. The defensive
reader is written against exactly these.

### `fixtures/sample_app/`, `long_app/`, `polyglot/`, `new_stack/`, `schema_app/`, `graphify/` 🟡
Targeted fixtures: a small app, a file long enough to test scope resolution, Java/Go/JS together, a
Ruby/YAML/SQL stack nothing parses deeply, a schema-only repository, and recorded Graphify payloads.

---

## Generated and non-source

### `runs/` ⚪ (generated)
One directory per run: `source/` (the snapshot), `graphify/` (raw extractor output), `artifacts/`
(every intermediate plus the deliverables), and a shared `checkpoints.db`. **No retention policy** —
each run holds a full copy of the analysed tree.

**Significance:** `runs/*/artifacts/brd.json` is the best evidence available about what the system
actually produces, as opposed to what it is designed to produce. That is where the findings in
`improvements.md` came from.

### `MDfiles/` ⚪
Earlier notes: `Architecture.md`, `Decision.md`, `CodeBase.md`, `SRC.md`, `flow.md`, `Pack1.md`,
`Pack2.md`, `Pack3.md`, `Suggestions.md`, `check.md`, `work.md`, `questions.md`, `tasks.txt`. Five of
them describe the same pipeline, which is a drift liability — when a capability changes, several
documents become wrong and none is authoritative. `Pack3.md` and `work.md` design the UI-execution
pack that does not exist in `src/`.

### `examples/` ⚪
Two sample archives used by the suite.

### `src/agentic/capabilities/playwright/` ⚪ — **delete this**
Contains only a stale `__pycache__`; the `.py` files were removed. Compiled bytecode in a package
directory can still satisfy an import on some configurations.

### `src/agentic_skill_packs.egg-info/` ⚪ (generated)
Editable-install metadata.

---

## Reading order for a new maintainer

**Day one — the shape.**
`pyproject.toml` → `README.md` → `skills/pack.py` → `graph/builder.py` → `graph/router.py` →
`codebase-requirement-skills/pack.yaml` (skim the comments).

**Day two — the contracts.**
`schemas/core.py` → `schemas/brd.py` (the module docstring and the constants; skip the field prose) →
`state.py` → `store.py` → `capabilities/registry.py`.

**Day three — the work.**
`capabilities/graph_ops.py` → `capabilities/graphify.py` → `llm/prompt.py` → `capabilities/gates.py`
→ `capabilities/verify.py`.

**Day four — judgement.**
`capabilities/consolidate.py` → `capabilities/clustering.py` → `capabilities/resolution.py` →
`capabilities/claims.py`.

**Day five — the second pack.**
`test-generation-skills/pack.yaml` → `schemas/testgen.py` → `testgen/qualify.py` →
`testgen/signals.py` → `schemas/testspec.py`.

Then run `python -m tests.harness` and read `tests/test_extensibility.py`. Between them they tell you
what the system currently measures and what it promises structurally.

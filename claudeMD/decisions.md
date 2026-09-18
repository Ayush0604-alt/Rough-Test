# Decisions — why this system is built the way it is

A decision-by-decision review of TraceBackAnalysis (`agentic-skill-packs`). For each one:
**what was chosen**, **why** (from the code and its comments), **what else was on the table**, and a
**verdict** — was there a better approach?

I have tried to be genuinely critical. Several of these decisions are unusually well-argued; a few
are wrong, and a few are right for reasons the codebase does not state.

Legend for verdicts:

- ✅ **Right call** — I would make the same choice.
- 🟡 **Right for now** — defensible, but there is a better end state.
- 🔴 **Would do differently** — I think the alternative is better.

---

## 0. The decision behind all the others

> **Chosen:** every claim in the output must trace to a real file and a real line, and anything that
> cannot be grounded is dropped or refiled on the record rather than reworded into something
> plausible.

Almost every design choice in this repository is downstream of this one. The gates, the jargon
linter, the `symbol`/`scope` split, the evidence-packet discipline, the confidence ceiling of 0.95,
the "nothing is deleted" rule, the separate `requirement_analysis.md` — none of them make sense
except as defences against a document that reads convincingly and is partly invented.

**Verdict: ✅.** This is the correct organising principle for a reverse-engineering tool, and the
system is unusually disciplined about it. The failure mode it prevents — a stakeholder signing off a
requirements document containing capabilities that do not exist — is much worse than the failure mode
it accepts, which is a thinner document that says what it does not know.

---

## 1. Python 3.11+

**Chosen:** Python ≥ 3.11, `src/` layout, `setuptools` + `pyproject.toml`, console script `agentic`.

**Why:** the LLM-orchestration ecosystem (LangGraph, LangChain, Pydantic) is Python-first. 3.11
specifically for `StrEnum`, which the code uses for every closed vocabulary (`StepMode`, `OnFail`,
`Provenance`, `Band`, `Route`, `InterfaceHint`, `BoundaryClass`). The performance-critical work —
tree-sitter parsing across 36 languages — is delegated to an external binary, so the Python side only
orchestrates and post-processes.

**Alternatives:** TypeScript (better for a tool whose *targets* are often JS/TS); Go (single binary,
trivial distribution).

**Verdict: ✅.** The GIL is irrelevant here because the workload is I/O and subprocess. The one real
cost — deep extraction of TypeScript/JavaScript being weaker than it would be in a JS-native tool —
is a Graphify limitation, not a Python one.

**One concrete defect worth fixing:** there is no lockfile. Dependencies are range-pinned
(`langgraph>=0.2.60,<1`) and the project depends on **measured behaviour of a specific extractor
version** — `graphify.py`'s entire node-classification logic is documented as "measured, graphifyy
0.9.52", including the Go `_callable: false` workaround. A minor Graphify release could silently
change the shape of every graph. That deserves an exact pin or a lockfile, not a range.

---

## 2. LangGraph as the execution engine

**Chosen:** `langgraph` + `langgraph-checkpoint-sqlite`, with the graph **compiled from a YAML
manifest** rather than written in Python.

**Why the framework:**

- `Send()` gives real fan-out/fan-in — the mechanism that makes cost linear in the number of
  interaction boundaries.
- Conditional edges with a pure-Python router give deterministic routing (see §3).
- A SQLite checkpointer means a Streamlit rerun resumes rather than restarts.
- `Annotated[..., reducer]` channels give correct parallel state merging for free.

**Alternatives:** hand-rolled orchestration (a list of steps and a while loop); CrewAI / AutoGen
(agent-to-agent, supervisor-driven — the *opposite* of what this system wants); Prefect/Airflow
(designed for scheduled data pipelines, wrong granularity and far heavier); Temporal (durable
execution, correct semantics, enormous operational cost for a single-user tool).

**Verdict: 🟡 Right for now, with a caveat worth naming.**

The fan-out and the checkpointer are genuinely worth the dependency. But look at what LangGraph is
actually *used* for here: `add_node`, `add_conditional_edges`, `Send`, `stream`, and a SQLite saver.
The interesting machinery — routing policy, repair budgets, artifact passing, step records, halt
semantics — is all this project's own code sitting on top. A hand-rolled executor over a list of
`StepSpec` would be perhaps 200 lines and would remove a pre-1.0 dependency whose API is still
moving.

The counter-argument is `recursion_limit: 60` and the repair cycle: LangGraph's superstep model gives
correct semantics for a graph with a back-edge, and re-implementing that correctly (including the
fan-in barrier) is more subtle than it looks. I would keep it — but I would note that the project is
**one `Send()` away from not needing it**, and that is worth knowing if LangGraph ever breaks.

---

## 3. Routing is deterministic and the model can never influence it

**Chosen:** `graph/router.py` reads the manifest and the state. No model is consulted about what
happens next, ever. There is a fully-written `advisory-routing-orchestration/SKILL.md` in the
requirements pack marked `status: not-wired`, with a header stating that no step names it and no code
path loads it.

**Why, in the module's own words:** *"The validation skill is explicit that a gate must not be
reasoned past, and a supervisor that decided the next step could do exactly that: route around a
failed gate because continuing looked more helpful. Here it structurally cannot."*

**Alternatives:** an LLM supervisor choosing the next node (the standard agent pattern); a hybrid
where the model *proposes* and a validator may overrule — which is exactly what the unwired skill
describes.

**Verdict: ✅, and it is the best decision in the repository.**

Most "agentic" systems put a model in the control loop and then discover that the model's
helpfulness bias is indistinguishable from unreliability: asked whether a failed gate should block,
it will usually find a reason to continue. Making it structurally impossible is stronger than any
prompt.

**On keeping the unwired skill:** I would keep it too, but the file should say what it currently says
even more loudly — a 183-line skill sitting in a pack directory is a thing a future maintainer will
wire up by accident. The `status: not-wired` frontmatter key is invented (nothing reads it); a
`_not-wired/` directory prefix or a `docs/` location would be harder to wire by mistake. Minor.

---

## 4. Azure OpenAI, and only Azure OpenAI

**Chosen:** `AzureChatOpenAI` via `langchain-openai`, four required env vars, all-or-nothing
(`llm_configured` is `all(...)`), `temperature=0.0`, `max_tokens=8000`, `max_retries=2`, client
`lru_cache`d on its parameters.

**Why:** almost certainly an organisational constraint. Technically: `.with_structured_output(schema)`
gives **provider-enforced** structured output, so a malformed reply is a retry rather than a silently
partial document — which matters enormously for a system whose whole value is not making things up.

**Verdict: 🟡.** Two observations.

The *good* part is the `preflight()` check: a pack with LLM steps refuses to start when credentials
are incomplete, rather than discovering it after ingestion, extraction and validation have run and
leaving a half-populated run directory. That is exactly right and most systems get it wrong.

The *weak* part is that `llm/client.py` is 51 lines and entirely Azure-shaped. The rest of the system
calls `build_chat_model(settings)` and does not care — so the seam is in the right place — but
swapping providers means rewriting the module, and `Settings` has four Azure-named fields in it. A
`provider: str` discriminator plus a small factory would cost about 30 lines and would remove the
single-vendor coupling from the config surface. I would do that before this system is used anywhere
its LLM vendor is not already decided.

**One genuine inconsistency:** `correspondence._entail()` makes a model call from inside a step the
manifest declares `mode: deterministic`. The code is careful to route it through `llm.steps` rather
than `llm.client` — the comment explains that importing the client directly bypassed the seam every
test stubs, and the suite was quietly making live calls. But the manifest still lies about the step's
mode, and `preflight()` will not catch a missing credential for it (it only scans
`StepMode.LLM` steps). It works because the call is wrapped in `try/except` and the pass degrades to
structural checks — but a step that calls a model should be typed as one. **🔴 on the manifest
declaration specifically.**

---

## 5. Pydantic v2 everywhere

**Chosen:** one schema layer for LLM output contracts, internal models, and settings.

**Why:** field **descriptions become the JSON-schema descriptions handed to the model verbatim**
under `.with_structured_output`. `schemas/brd.py` says this explicitly, and it is why the field
descriptions in that file are 10–20 lines each — they are not documentation, they are prompt.

**Verdict: ✅ with one flag.** Collapsing "the prompt" and "the type" into one artifact is elegant and
prevents the classic drift where a model is told about a field the code no longer has.

The flag: this makes `schemas/brd.py` 1057 lines, most of it English. The project's stated philosophy
is that *instructions live in `SKILL.md` on disk so they can be edited without a code change and are
hashed for provenance* — and these descriptions violate both halves of that. They require a code
change to edit, and they are not covered by the `skill_sha256` provenance trail. The same is true of
the `instructions:` blocks in `pack.yaml`, which are ~190 lines of prompt for `synthesize` alone.

So prompt content lives in **three** places with three different editing and provenance stories:
`SKILL.md` (hashed, hot-reloaded), `pack.yaml:instructions` (versioned with the manifest, not
hashed), and pydantic field descriptions (code). That is a real inconsistency in a system that
otherwise takes provenance very seriously. I would at minimum hash the manifest alongside the skill
in every `StepRecord`.

**Also worth noting:** `ProductBRD.area_names` is a computed property over a `list[AreaLabel]`
instead of a `dict[str, str]` field, with a docstring explaining that a `dict[str,str]` becomes an
open-ended JSON-schema object which strict structured-output providers cannot represent — they drop
the property and then reject the schema for a missing required key. That is a hard-won detail and
exactly the sort of thing worth having written down.

---

## 6. Workflows as data (the skill-pack abstraction)

**Chosen:** `pack.yaml` compiles into a `StateGraph`; three registries (skills, capabilities,
schemas) resolve everything by name; `graph/builder.py` names no step, skill or artifact.

**Why:** adding a second analysis pack should be markdown and YAML, not graph surgery.

**Does it hold?** Mostly, and the project tests the claim rather than asserting it —
`tests/test_extensibility.py` authors a new pack at runtime and runs it.

**Verdict: 🟡 — the abstraction is real but it leaks in four measurable places.**

1. `capabilities/registry.load_builtin_capabilities()` hard-codes an import of every module of
   **both** shipped packs, including a `from agentic.capabilities.testgen import ...` block. A third
   pack must edit engine code.
2. `schemas/registry.SCHEMAS` hard-codes both packs' output models for the same reason.
3. `llm/prompt.py` registers artifact renderers named `brd`, `test_suite`, `clusters`,
   `test_batches`, `requirement_gate` — the engine knows pack vocabulary.
4. `app.py:_views` knows how to render `brd` and `test_suite` specifically.

(3) and (4) are honest and bounded: the renderer registry is a documented extension point
(`@renders("name")`), and the UI is explicitly artifact-driven with a fallback. (1) and (2) are the
avoidable ones — Python entry points, or a `capabilities.py`/`schemas.py` convention discovered
inside the pack directory, would close them and are maybe 20 lines of work.

The honest summary: **"adding a pack is a data change" is ~90% true.** That is a very good result for
this kind of abstraction, and the four leaks should be written down rather than papered over.

**Was there a better approach?** The realistic alternative is "just write the second pipeline in
Python". For two packs, that would have been less code. The abstraction pays off at pack three — and
this project's near-term future (a framework-adapter pack) is exactly pack three. So the bet looks
correct, but it is worth being clear that it *was* a bet.

---

## 7. Skills as `SKILL.md` on disk, loaded at call time and hashed

**Chosen:** skill bodies live in files, are cached on mtime, are injected into the system prompt at
call time, and their sha256 is recorded in every `StepRecord` — including for deterministic steps
that implement their skill in Python.

**Why:** editing behaviour should not need a restart or a code change; and every deliverable should
be able to state which version of which instructions produced it.

**Verdict: ✅, and the sha-on-deterministic-steps idea is genuinely clever.** Recording that
`validate_requirements` was governed by `requirement-and-graph-validation-gates@<sha>` while the
actual checks are Python makes the drift between prose and implementation *auditable* rather than
invisible. Most systems either duplicate the rules in both places and let them diverge silently, or
give up on the prose.

The caveat is the one in §5: two of the three prompt locations are not covered by this trail.

**Minor risk:** mtime-keyed caching is correct for a local dev loop and wrong under a checkout that
preserves timestamps or a container layer that resets them. Hashing the content would be strictly
better and the file is already being read.

---

## 8. Graphify instead of per-language AST extractors

**Chosen:** shell out to the external `graphify` binary (`--code-only --no-cluster --no-gitignore`),
then normalise its output into this project's own `KnowledgeGraph`. Extraction failure **halts** the
run.

**Why:** the previous design was a Python-only AST extractor, and every other language reached the
graph as a bare `FILE` entity and produced no requirement at all. One binary covers 36 languages via
local tree-sitter, with no API calls and no second LLM vendor.

**What it cost:** Graphify emits *structure, not intent*. It has no `kind` field, no line ranges, no
notion of a route or a command. Roughly 600 of `graphify.py`'s 1028 lines exist to recover what the
old AST extractors knew for free:

- `classify_kind()` derives kinds from label shape and flags — with a documented workaround for the
  Go grammar reporting `_callable: false`, which had put every Go function under `SYMBOL` and
  produced an empty document.
- `_derive_line_ends()` reconstructs end lines in two passes, with a third special case for
  docstring nodes that were truncating every documented function to five lines.
- Interaction boundaries now need a **model call** to identify at all (§9).

**Verdict: ✅ on the direction, with clear eyes about the price.**

The old constraint was fatal to the product — a requirements tool that only works on Python
repositories is not a requirements tool. Trading language coverage for a normalisation layer and one
model call is the right trade.

**But two things deserve attention.** First, the project has taken on a hard dependency on the
*undocumented output shape* of a third-party tool at one specific version, with the reverse-engineered
details recorded only in comments. That is a genuine fragility and argues strongly for an exact pin
plus a contract test that fails loudly when Graphify's shape changes (there is a fixture corpus, but
it exercises the adapter, not the binary).

Second, the module-surface scanner (`_module_surface`) is a **regex over source lines** that decides,
by whether a call's result is assigned or tested, whether it is an interaction point. It works, it is
carefully justified (a Streamlit app's entire surface is module-level widget calls, so without it such
a product looks like a library with nothing exposed), and every heuristic is defended in a comment.
But it is a regex parser living inside a project whose thesis is "we do not guess". A tree-sitter
pass over the same files — the grammar is already a transitive dependency via Graphify — would give
the same answer from a real syntax tree. **🟡 — right outcome, and the cheapest thing in this
codebase to do properly later.**

---

## 9. Interaction boundaries: shape + one model call, never a framework list

**Chosen:** propose candidates deterministically from graph shape — primarily **in-degree zero on
`CALLS`**, plus registration signals — attach evidence (call degrees, referenced undefined names,
string literals within ±3 lines), cap at 120, and ask a model to name the **class**. The model never
states a location; a name that resolves to nothing is dropped.

**Why:** a handler is invoked by a framework, a runtime, or a person — never by the code beside it.
That property is shared by an Express route, a Spring controller, a Sinatra handler, a gin handler,
a cron job, and a framework nobody here has heard of. It is a shape, not a list.

**Alternatives:** a framework registry (recognise `@app.route`, `@GetMapping`, `http.HandleFunc`, …)
— precise, but every new framework is a code change and the list is endless; pure heuristics with no
model — which is what the AST extractors did, and which was language-specific by construction.

**Verdict: ✅, and the guardrails around it are the reason it works.**

Three of them are worth copying into any similar system:

- **The model gets a `NONE` option and is told most candidates are not boundaries.** Without a way to
  decline, a classifier promotes helpers to boundaries. This single schema choice is doing a lot of
  work.
- **The model names a node; the graph supplies the location.** A hallucinated `qualified_name`
  resolves to nothing and is dropped, so the worst case is a missing boundary rather than a
  fabricated citation. This is the general pattern the whole system uses — *let the model classify,
  never let it locate.*
- **Boundaries created this way carry `confidence 0.55` and `provenance INFERRED`**, and Gate 1
  warns when more than half the boundaries rest on judgement. The uncertainty propagates instead of
  being laundered.

The remaining weakness is honestly documented: in-degree zero over-collects dead code and public
library functions, and 120 candidates is a hard cap with no reporting of *which* classes fell outside
it (unlike the cluster stratifier, which reports starvation explicitly). That asymmetry looks like an
oversight rather than a decision.

---

## 10. Cost control: evidence packets, not source code

**Chosen:** the model never reads source. It reads a compact record list built from the graph —
per-kind attribute whitelists, per-key truncation budgets, up to 12 outgoing edges — bounded at
40 000 characters per packet, one call per interaction boundary.

**Why:** cost becomes linear in the number of boundaries, not in the size of the codebase; and the
model cannot cite a line it was never shown.

**Verdict: ✅, and it is the second-best decision here.** It solves cost and hallucination with the
same mechanism, which is rare. A packet containing only graph-derived facts means an invented
citation is *detectable by construction*, because Gate 2 re-resolves every citation against the file
system.

**Three refinements around it, each fixing a real measured failure:**

- **Splitting instead of truncating.** An oversized neighbourhood used to be packed into one packet
  and the overflow dropped silently — and the tail of a breadth-first walk is the *deepest, most
  specific* work. Now it is split across up to 6 packets and anything still unplaced is counted and
  reported. ✅
- **Stratified sampling instead of `groups[:limit]`.** Boundaries of one kind carry identical degree,
  so the sort collapses to the alphabetical tiebreak, and truncation removes *whole classes*. In the
  run that exposed it, 147 state-changing endpoints were never planned because they sorted after the
  reads. The replacement allocates proportionally with a floor of one per class and reports every
  class starved or thinned. ✅ — and the general lesson (a cap applied to an ordering you did not
  design is a categorical filter, not a sample) is worth internalising.
- **Deeper horizon (4→6 hops, 120→250 nodes).** Reaching a registered handler costs three hops before
  any work is described, so the handler sat exactly on the old horizon. Measured: 35% → 88% of
  functions reaching a packet on a dispatch-architecture codebase, with a small gain (17% → 19%) and
  no regression on a directly-wired one. ✅

**The one thing I would change:** `max_clusters: 80` × 40 000 chars is a real spend, and nothing in
the system *estimates or reports cost before the run*. `plan_clusters` knows exactly how many calls
it is about to make and how many characters each carries — surfacing "this run will make 63 model
calls totalling ~1.9 M characters, continue?" in the UI would cost ten lines and would matter the
first time someone points this at a monorepo.

---

## 11. Killing the `reduce` step — the arithmetic argument

**Chosen:** the whole-repository merge is now **deterministic** (`assemble_requirements`). The one
remaining whole-repository model call (`elevate`) reads a digest of area names and requirement
*titles*, and writes only the purpose, the handful of outcomes, and which capability realises which.

**Why (the argument is arithmetic, not stylistic):** the old `reduce` was one call handed every
finding and asked to return the finished document. At an 8 000-token output ceiling and ~120 tokens
per written requirement, the reply holds about sixty items *including* every criterion, persona,
rationale and citation. A platform with two hundred grounded findings therefore had them compressed
to a handful — **and the compression was silent**, because nothing counted what went in against what
came out.

**Verdict: ✅, and this is the most instructive decision in the repository.**

Three lessons generalise well beyond this codebase:

1. **If a model step's output must grow with the input, it is not a model step.** Copying is
   deterministic work. Judgement is the model's work. `assemble` copies; `elevate` judges.
2. **An output ceiling is a silent data-loss mechanism.** The same failure appears three more times
   here and is fixed the same way each time: `repair` (was typed `ProductBRD` → now `RepairedItems`),
   `repair_tests` (→ `RepairedTestCases`, plus a 12-case-per-call cap), and the `_render_brd` /
   `_render_test_suite` prompt renderers that stop the whole document reaching a repair prompt.
3. **Count what goes in against what comes out.** The metric `findings_carried` exists specifically
   because that number used to vanish.

**Was there a better approach?** Map-reduce with a *hierarchical* reduce (merge clusters pairwise,
then merge the merges) is the textbook answer and would have kept the model in the merging role. It
would also have multiplied the number of calls, compounded paraphrase drift at every level, and still
lost information — deterministic copying loses none. The chosen approach is better.

---

## 12. The `abstraction_basis` / `requirement_type` split

**Chosen:** ask the model *on what grounds is this a requirement?* first, and derive the tier from
the answer via a fixed table (`BASIS_TYPES`). `implementation_detail` maps to `observation`, which is
retained in the traceability model and never published as a requirement.

**Why:** asked only for a *type*, the taxonomy offered nowhere to put a behaviour that was
interesting but not required — and discarding it would throw away real evidence. So every internal
helper, conditional and variable assignment got promoted to a functional requirement. Giving
`implementation_detail` a destination makes over-extraction stop being the path of least resistance.

**Verdict: ✅ — the best piece of prompt/schema engineering in the project.**

The insight is general: **over-extraction is usually a symptom of a taxonomy with no bin for "not
this".** A model that must classify and cannot decline will classify wrongly. Compare the `NONE`
option on the boundary classifier (§9) and the `notes` field on `TestDesignFragment` ("anything you
could not do honestly") — the same pattern, three times.

The consistency enforcement around it is also right: the basis wins wherever the two fields disagree
(`claims._agree_with_the_grounds`), `published_kind()` derives the published type from the basis
rather than the stored field so a document cannot publish a type that contradicts its own grounds,
and Gate 2 raises `CLASSIFICATION_INCOHERENT` as an **error**. Two fields that can disagree always
eventually do — usually on the one path nobody exercised.

**Related, and also right:** earlier versions carried seven types kept separate from four document
sections, so an item could be filed in one place and typed another; across repeated runs on one
unchanged codebase the same findings came back typed entirely functional, then mostly business, then
a mix. Collapsing to "the section **is** the tier" removes the possibility.

---

## 13. Adding a consolidation stage between synthesis and validation

**Chosen:** six deterministic steps (cluster → consolidate → correspondence → confidence → leakage →
resolve → criteria) that each need the *whole* candidate set.

**Why:** synthesis reasons about one boundary at a time and validation judges one claim at a time.
Neither is in a position to ask what the candidates *add up to* — and without that question the
output is a behaviour inventory: as many requirements as the code had interesting places, each one
true and the set as a whole unreadable.

**Verdict: ✅ on the stage existing.** The diagnosis is exactly right, and the observation that
*"every comparison before this required two candidates to share code, and two workers describing one
capability from the entry point and from the work behind it cite different lines by construction"* is
a real insight — it explains why a per-claim deduplicator can never find the duplicates that matter.

**On the implementation, more mixed. 🟡.**

The clustering signature (`_MODAL` split → enabler stripping → action stem → object terms) is a
hand-rolled sentence parser with hard-coded thresholds (0.90 / 0.80 / 0.72 / 0.55 / 0.45 / 0.85 /
0.65 / 0.82 / 0.92 / 0.85 …). The code defends each one, and several are documented as *measured*
against real pairs — `RESTATEMENT_SIMILARITY = 0.40` even records that genuine pairs scored 0.12,
0.20 and 0.33 while restatements scored 0.43 and 0.47, "with only 0.07 of headroom, which is
precisely why this produces a warning and not a rejection". That is exactly the right way to pick and
document a threshold.

But it is still a bag of English-language heuristics doing semantic work. The module's own limits
section admits it: *"Deduplication compares wording and evidence, never meaning. 'Ask about a
document' and 'inquire about a document' cite the same line and are one capability; nothing here can
see that."*

**Was there a better approach?** Sentence embeddings. Cosine similarity over a small local embedding
model would catch the synonym case the stemmer cannot, would need one threshold instead of nine, and
would not need a new rule for every grammatical form ("allow X to" vs "be able to"). The costs are a
model dependency (offline is fine — this need not be an API call), non-determinism across model
versions, and loss of the *explainability* the current approach has: today you can print exactly
which shared terms caused a merge.

Given this project's determinism and auditability commitments, I understand the choice. But
`_ENABLERS`, `_SUFFIXES`, `STOP_WORDS`, `_HEADING_NOISE`, `FRAME_WORDS` and `_COMMON_WORDS` are six
separate English word lists across four modules, and they will keep growing. **A hybrid — embeddings
to propose candidate pairs, the existing deterministic rules to confirm and explain them — would
keep the auditability and fix the synonym blindness.** That is what I would build next.

Two sub-decisions inside this stage are clearly right and worth calling out:

- **`_same_action` as a merge veto.** *"Staff shall **delete** a supplier bill"* and *"Staff shall
  **view** a supplier bill"* share five of six content words and score 0.83 against a 0.65 threshold —
  word overlap cannot tell that one word carries the entire difference. Without this guard a whole
  CRUD surface folds into one requirement. ✅
- **Rule classification as a promotion test, not a guard detector.** *"Detecting guards means
  recognising null checks, empty checks and not-found branches, which cannot be done without knowing
  a language's syntax… Inverting the question removes the need: nothing is a business rule unless
  something it cites was **declared** to be one."* Measured: 1 642 rule entities extracted, 1 632 of
  them ordinary conditionals. This is a genuinely elegant piece of reasoning. ✅

---

## 14. Repair budgets, and what a rejection is allowed to cost

**Chosen:** a bounded repair loop (3 passes for requirements, 4 for tests) that sees **only the
rejected items**, whose reply is merged by id, and whose non-wording fields are **overwritten from
the original**. When the budget is spent, `quarantine` / `prune_tests` removes what could not be
grounded — deterministically, so no model gets to argue for keeping a claim the gate rejected.

**Why:** three separate failures, each documented:

1. A repair typed as the whole document could not fit in one reply, so a rejection over one phrase
   deleted most of the analysis.
2. A repair allowed to return any field came back with altered grounds — *a rule returned with the
   grounds of an outcome*, which is a contradiction no rewording can fix, so the gate rejected it
   again on every pass until the budget ran out and it was deleted. **A repair pass must not be able
   to fail a claim it was asked to save.**
3. A repair returning items the gate had *not* rejected could quietly rewrite claims that already
   passed. Those are now discarded.

**Verdict: ✅ across the board.** The governing principle — stated in the quarantine module — is the
right one:

> **A grounding failure** (no evidence, a line that does not exist, a trace reaching no boundary)
> means the claim was never established: remove it, because a weaker wording over the same missing
> evidence would still read as a finding.
> **A wording failure** (the statement names the implementation) means the claim *was* established
> and is described badly: strip the term, record it, keep the requirement.

The measured consequence of getting that wrong: *"On one measured run fourteen functional
requirements entered the gate and one left, and the thirteen losses were almost entirely this."*

Four related details that are individually small and collectively the difference between a working
loop and a destructive one:

- The repair attempt is **counted even when the call fails**, so the bound still binds.
- A failed repair call is **not fatal** — it is a pass-through, because halting there threw away every
  grounded requirement in the set over one bad call and produced no deliverables at all.
- **Whatever removes an item owns the links into it.** Both `apply_repairs` and `quarantine` clear
  parent links pointing at what they removed, because otherwise the gate rejects the *child* for a
  dangling link and the next round of the budget deletes a perfectly good requirement.
- A citation fault on a **business requirement** is not fatal, because its citation is *derived* from
  the capabilities beneath it — the fault belongs to a child, and deleting the outcome orphaned the
  entire level below it.

**Was there a better approach?** Making the gate's fixable findings structurally unfixable-by-model —
i.e. deterministic repair for everything. Interestingly the system has drifted that way already:
`prepare_claims._agree_with_the_grounds` and `_settle_relations` exist precisely because *"an
incoherent pair is not something any rewording can repair"*, and `quarantine` strips jargon
deterministically. On the current evidence, the LLM repair pass is earning its keep on **exactly one
class of problem**: rewriting a statement in business language. I would consider whether it is worth
a model call at all, or whether it should be narrowed to that one job explicitly.

---

## 15. Confidence recomputed rather than self-reported

**Chosen:** discard the model's self-assessed confidence into `model_confidence` and compute a new
one from observable properties (correspondence verdict, symbol present, span width, corroborating
files, parsed-vs-inferred provenance, declared policy), floor 0.10, **ceiling 0.95**.

**Why:** in one measured run the model reported exactly 1.0 on 40 of 60 requirements — *"a document
declaring itself certain about two thirds of its contents, none of which any person had read"* — and
the low-confidence review list built from it was therefore empty of precisely the items most worth
reviewing.

**Verdict: ✅, and the ceiling is the part I would keep hardest.** 0.95 because nothing here has been
reviewed by a person, *"and saying otherwise is a lie the document tells its reader"*. Keeping the
original value alongside so the recalibration itself can be judged is the right instinct.

The weights are declared as a stated judgement rather than a fitted calibration, written as named
constants so they can be argued with, and anchored to one explicit fixed point (a claim that passed
correspondence, cites a readable span, and rests on parsed code must clear the 0.70 review
threshold without needing a model call). That is an honest way to ship an uncalibrated number.

**The limitation, unstated in the code:** these are additive weights over correlated signals — a
claim with a symbol, a tight span and parsed provenance is usually all three at once — so the
distribution will be bimodal rather than spread. Since the number is used as a *threshold* (0.70 for
the review list) and not as a probability, that is fine. It would stop being fine the moment anyone
reads it as one.

---

## 16. "Nothing is deleted" — the partitioning decision

**Chosen:** a candidate that cannot be published leaves the requirements document and lands in
`unresolved_candidates` → `requirement_analysis.md`, with its evidence, its disposition and the
reason. Merges record what they absorbed in `superseded`. Folded test cases record their id on the
case that absorbed them.

**Why:** the earlier behaviour published doubtful claims *inside* the requirements list marked
`status: "rejected"`, so a reader could not tell them apart without reading a field most tools ignore.
The module states the reasoning and then corrects itself: *"a reader can check a doubtful claim and
cannot recover a deleted one… is sound, but it argues for **partitioning** the output, not for
putting everything in one list."*

**Verdict: ✅.** Two documents is the only arrangement honest to both audiences: the stakeholder
reading `requirements.md` sees only what the analysis can stand behind; the reviewer auditing the
judgement opens `requirement_analysis.md` and finds every rejected candidate with its evidence.

This also makes the six dispositions (`keep` / `reclassify` / `merge` / `observation` /
`open_question` / `reject`) meaningful rather than decorative, and it is what allows the
correspondence check to be *aggressive* — a wrong rejection costs a document section, not a finding.

---

## 17. Objective vs interpretive checks

**Chosen:** `OBJECTIVE_CHECKS = {cites_something, resolves_to_known_code, span_adequate}`. Everything
else (`lexically_anchored`, `actor_reachable`, `effect_agreement`) is interpretive: failing one makes
a claim an **open question**, not a rejection.

**Why:** *"the anchoring check compares a statement written in the product's business words against
the code's own identifiers — a claim written exactly as instructed can share no vocabulary with the
thing it correctly describes."* The system's own instructions guarantee false positives on that
check, so it must not be able to delete a requirement on its own.

**Verdict: ✅, and it is the sharpest bit of self-awareness in the codebase.** The synthesis skill
demands business language; the anchoring check compares against code vocabulary; those two are in
direct tension **by design**, and rather than weakening either, the system classifies the check's
authority. *"Uncertain and wrong are different answers and get different dispositions."*

The abstention behaviour is equally right: `actor_reachable` abstains entirely when the repository
has no detectable boundary (otherwise it would reject every functional claim in any repository in a
language nothing here parses), and `effect_agreement` fires only when a statement asserts a change
**and every boundary cited is declared read-only** — absence of a declaration is not a declaration of
absence.

**One thing I would change:** an entailment rejection is marked `objective=True` on the grounds that
*"a model read the facts and disagreed — that is a judgement about the claim itself rather than a
proxy for one"*. I do not find that convincing. It is still a model judgement, it is the least
reproducible signal in the system, and it is the only one that can delete a requirement outright. I
would mark it interpretive and let it produce an open question. **🔴 on that one line.**

---

## 18. The jargon linter — a checker, not a prompt instruction

**Chosen:** a deterministic linter (`capabilities/jargon.py`) enforcing "no technical vocabulary in a
requirement statement", with three vocabularies at three strictness levels, shape patterns, graph
symbol matching, and an exclusion list of ordinary English.

**Why:** *"A prompt instruction is not a check: models comply most of the time, and 'most of the
time' is exactly how a file path reaches a document that a stakeholder signs off on."*

**Verdict: ✅ on the principle; 🟡 on the list-maintenance burden.**

The nuance in this module is real and hard-won:

- `.pdf`/`.docx`/`.xlsx` are **not** flagged — "accepts a PDF, a Word document, or a spreadsheet" is
  precisely what the product offers.
- A path needs a slash **and** a dot, because "admission/rejection decision" and
  "name/email/phone" were being rejected as file paths.
- `"process"` is deliberately absent from the technical terms — it is ordinary business English.
- Backticks are no longer a rule of their own, because they mark declared literals as often as code.
- External system names are allowed for integration/NFR/data requirements, because *"an integration
  requirement forbidden from saying which system it integrates with asserts nothing at all."*
- `_COMMON_WORDS` exists because rejecting "update" is wrong in both directions at once: the
  statement loses the only word for what the person does, and the repair — told which word to remove
  — produces *"A user shall be able to the next action."*

Every one of those is a bug someone hit. That is also the problem: it is three word lists that grow
forever, tuned against the repositories seen so far. There is no obviously better mechanism (an LLM
judge would be non-deterministic and is exactly what this linter exists to backstop), but I would
make the lists **loadable per pack or per project** rather than baked into the module, so a
domain-specific vocabulary does not require editing the engine.

---

## 19. Test specifications that name no technology

**Chosen:** `schemas/testspec.py` forbids naming any framework, runner, assertion library, protocol
client, browser or language, anywhere. Enforced by `framework_leaks()` at the gate **and again at
render time**. Three advisory fields (`interface_hint`, `automation_readiness`,
`TestInput.value_kind`) exist for a future adapter, with an explicit rule that **no logic in the pack
branches on them**.

**Why:** the specification layer has to survive being turned into API tests, UI tests, unit tests,
agent-trajectory evaluations, or a manual script — *"a model that has already decided it is talking
HTTP cannot become any of the others."*

**Verdict: ✅, and this is the decision most relevant to whatever you have been assigned.**

The discipline is real: the independence rule is stated in the schema, enforced twice, and the
advisory fields carry a written warning against exactly the drift that would kill it. `TestInput`
describing a value by *the role it plays* rather than by a literal is the detail that makes the
handover to an adapter actually workable — an adapter inventing concrete data needs to know *why*
this value was chosen.

**The open question — and it is yours to answer:** the payoff for all this independence is a third
pack that does not exist. Until it does, the cost is visible (a `test_plan.md` nobody can execute)
and the benefit is theoretical. If the assignment is "build the UI testing tool", the architecture is
waiting for you and the shape is already decided:

```
 requirements.json ──PACK 2──► test_specs.json ──PACK 3──► executable tests
                                     │                      (Playwright / pytest / …)
                          interface_hint: ui_action ────────► pick the adapter
                          automation_readiness      ────────► say what is not automatable
                          TestInput.value_kind      ────────► invent concrete data
                          preconditions/steps/expected_results ──► the test body
```

Pack 3 is a new directory with skill files, a `pack.yaml` declaring
`consumes_pack: testcase-generation` / `consumes_artifact: test_specs.json`, a defensive reader like
`testgen/ingest.py`, and a set of capabilities. It also needs the two engine leaks in §6 closed
(an import line each in `capabilities/registry.py` and `schemas/registry.py`).

**One caution on the word-boundary framework matcher:** `FRAMEWORK_WORDS` includes `"java"`,
`"python"`, `"javascript"`. A product whose *domain* is software — a CI tool, a package registry, a
code-review product — will have legitimate requirements naming those, and every derived case will be
rejected, repaired four times and pruned. The comment already records this happening once with
`PYTHONUNBUFFERED`. The word-boundary fix helps; it does not solve the underlying ambiguity.

---

## 20. The second pack consumes the published artifact, not the first pack's models

**Chosen:** `schemas/testgen.py` imports **nothing** from `schemas/brd.py`. PACK 2 reads
`requirements.json` through a defensive reader that records issues instead of raising, preserves
unknown keys in `extras`, and holds the expected contract as data (`CONTRACT_KEYS`).

**Why:** *"the requirements pack is frozen, and a downstream pack that reached into its internal
models would be coupled to decisions it does not control."*

**Verdict: ✅, textbook.** This is the difference between two packs and one pipeline with a seam
drawn on it. The defensive posture is the right one for an artifact whose producer will keep
changing: only `id` and `statement` are load-bearing, everything else has a default, coercion happens
where the intent is obvious (a bare string where a list is declared), and a bad `evidence` entry
costs provenance rather than a test.

Preserving unknown keys is the small detail that makes it durable — a future field added upstream
cannot break ingestion.

**And the payoff is concrete:** PACK 2 detects and discards the acceptance criteria PACK 1 *generated
for itself* (`BOILERPLATE_CRITERION_PREFIXES`, `DERIVED_CRITERION_MARKER`), because
*"Exercising the on-screen control produces the outcome this requirement describes"* reads as a
criterion and asserts nothing. On the rich fixture that removes 59 of 235 criteria. A pack reaching
into the other's internals would have had no natural place to notice that.

---

## 21. Qualification before design — "should this be tested at all"

**Chosen:** four bands (`eligible`, `eligible_with_assumptions`, `needs_clarification`, `excluded`),
decided by ordered first-match-wins rules, **before any model call**, with a `QualificationRecord`
produced for **every** requirement including the excluded ones.

**Why:** *"a model asked to design tests will always find something to say."* And: *"a coverage
report that only accounts for what was tested cannot answer 'why is there no test for this', which is
the first question a reviewer asks."*

**Verdict: ✅ on both halves.** The first is a correct read of model behaviour. The second is the
better insight — most test-generation tools report what they produced; this one is built to account
for what it did not, and `excluded_requirements.md` and `clarifications.md` are half the deliverable
set for that reason.

The same "account for everything" discipline shows up again in `assemble_test_specs`, which records a
requirement that was planned for and came back with nothing as `route: "undesigned"` — *"a worker
that simply says nothing about a requirement is a gap, not a requirement that needed no tests."*
That gap is invisible unless something looks for it.

---

## 22. Dimension activation as the anti-explosion mechanism

**Chosen:** eleven dimensions; `positive` free, everything else earned by a regex over the statement
and its **usable** criteria; risk and case budget computed deterministically; the designer works
inside a plan it did not make, and any case claiming an unactivated dimension is dropped at assembly.

**Why:** "cover every dimension" turns one requirement into eleven cases and a 190-item set into an
unusable suite. Measured: the internal-behaviour routing rule alone takes 485 projected cases down to
280.

**Verdict: ✅ on the mechanism, and the tuning notes are the valuable part.** Three patterns were
narrowed after measurement, each for a *reason* rather than to hit a number:

- `state_transition` matched generic create/update/delete and fired on 76 of 190 — but for "delete an
  item" the positive case already asserts the item is gone, so a transition case is the same test
  twice. Now requires a status vocabulary.
- `authorization` matched `admin`/`role` and fired on 34 through the word "administrator" — but
  naming *who* performs an action says nothing about whether anyone else is refused. Generating an
  authorization case from that **invents a rule the requirement never states**. Now requires an
  access condition.
- `data_integrity` matched `persist`/`stored` and fired on 31 — persisting is what a write does, not
  a guarantee anyone stated. Now requires an invariant.

That third clause in the middle bullet is the general principle, and it is the right one for a tool
whose whole claim is groundedness: **a test that asserts something the requirement does not say is a
fabrication in test's clothing.**

Excluding `rationale` from the searchable text is the same instinct applied to an input — it is the
one field where technical vocabulary is permitted, so reading it would let an implementation detail
activate a dimension.

**The honest weakness:** these are English regexes. They will under-fire on a requirements document
written in another language, and they will drift as vocabularies change. Same argument as §13, same
suggested fix (embeddings to propose, rules to confirm). But the deterministic-budget-before-model
structure is right regardless of how activation is computed, and that structure is the part worth
keeping.

---

## 23. Streamlit for the UI

**Chosen:** a single ~780-line `app.py`.

**Why:** the audience is an analyst reviewing a document, not a web user. Streamlit gives tabs,
tables, expanders, file upload and a progress stream for nearly nothing, and it makes the Requirements
tab — every claim expanding to show the real source at the cited lines — cheap to build.

**Verdict: ✅ for what this is.** Two decisions inside it are better than the framework choice:

- **Tabs are artifact-driven** (`_views`). A fixed six-tab list written for the requirements pack
  meant a test run showed an empty "Requirements" tab and no view of its own suite. Driving tabs from
  what is on disk means a third pack gets the tabs it produced, with no change here.
- **Chaining is offered as one primary button above the results**, not as three pre-filled sidebar
  widgets. The comment is worth quoting: *"A finished run that another pack consumes used to announce
  itself in the sidebar while the main area still showed the run that had just ended, so the next
  step read as nothing having happened."* And the successor is read from the *manifests*, so `app.py`
  names no pack.

**Where it breaks:** Streamlit's re-run-the-whole-script model means a long run holds the session.
The SQLite checkpointer mitigates it (a rerun resumes), but a genuinely long analysis of a large
monorepo will be uncomfortable. If this becomes a multi-user or long-running tool, the answer is a
job queue with the UI polling — not a different UI framework.

---

## 24. Snapshot the target into the run directory

**Chosen:** `prepare_run` copies the whole source tree into `runs/<uuid>/source/` (or extracts the
archive there) and analyses the copy.

**Why:** *"so a run is reproducible and the target cannot change under the analysis half-way
through."*

**Verdict: ✅ on the reasoning, 🟡 on the cost.** A run is genuinely reproducible: every citation
resolves against a tree that cannot move, and the graph, the packets and the gates all agree about
what the file system said. For a monorepo it is an expensive copy and the disk fills up with `runs/`
directories that nothing ever prunes. A content-addressed cache, a hardlink/reflink copy, or a
retention policy would fix that without giving up the invariant.

The unexpected dividend: because `prepare_run` accepts *any* directory, pointing PACK 2 at a PACK 1
run's `artifacts/` folder works with **zero** additional code. The test pack's manifest comment says
so explicitly. That is a good sign the seam was drawn in the right place.

---

## 25. Archive handling: validate everything before writing anything

**Chosen:** size, file-count, per-entry compression ratio, cumulative uncompressed size, path
traversal, absolute and drive-qualified paths, duplicate paths (case-insensitive), encrypted entries,
symlinks — **all checked across the whole member list before a single byte is written**, then a
resolved-target containment check during extraction.

**Verdict: ✅.** The "validate the whole list first" ordering is the part most implementations get
wrong: checking per-entry as you extract leaves a partially-extracted tree behind when a malicious
entry appears late in the archive. The case-insensitive duplicate check is a nice touch for Windows
targets.

---

## 26. Large artifacts on disk, not in graph state

**Chosen:** state carries logical-name → path; `ArtifactStore` carries the payloads.

**Why:** LangGraph checkpoints the entire state after every superstep, so a multi-megabyte knowledge
graph held inline would be re-serialised at every node and would make the UI crawl.

**Verdict: ✅, and it is a correct reading of the framework's cost model** rather than a generic
"keep state small" instinct. The consequence — capabilities read prior work through `store` rather
than through state — also makes every capability unit-testable by constructing a context around a
temp directory, which is what most of `tests/` does. Two benefits from one constraint.

The small wart: artifact names are strings with no namespace, so two packs using the same logical
name in one run directory would collide. They currently do not (`brd` vs `test_suite`), and packs get
separate run directories anyway, but nothing enforces it.

---

## 27. Testing strategy: stub the model, keep everything else real

**Chosen:** ~40 test modules where the model is stubbed and the file system, graph construction,
gates and rendering are real. A ground-truthed fixture corpus outside the analysed tree. A metrics
harness with **frozen baselines** (`python -m tests.harness`, `--freeze`). An extensibility test that
authors a new pack at runtime.

**Verdict: ✅, and the metrics harness is the part most projects skip.**

For a system whose output is prose, unit tests can only prove the machinery. The frozen-baseline
harness is what makes *quality* regressions visible — you change a threshold, you re-run the corpus,
you see the delta. Keeping structure and pipeline baselines apart because *"the two answer different
questions and move for different reasons"* is a real distinction: one says what the extractors found,
the other what the document made of it.

Two details worth copying:

- Ground truth lives **outside** the analysed tree, because keeping it inside made every fixture
  report a JSON file it does not contain and fed the harness's own answer key to the extractors.
- The README states the limit of the whole suite plainly: *"The test suite proves the machinery. It
  does not prove any individual requirement is right. For that: open `traceability_matrix.csv`, pick
  three rows at random, and confirm the cited file and line range actually say what the requirement
  claims."*

**The gap:** nothing tests against the **real** Graphify binary. Everything downstream of extraction
is fixtured on a recorded shape. Given how much of `graphify.py` is reverse-engineered from measured
output, a single contract test that runs the binary over one fixture and asserts the shape would be
cheap insurance. (Note also that the README says "200 tests" in one place and "680 tests" in another
— a small documentation drift.)

---

## 28. Decisions that are documented in the code but not in the manifests

A category rather than a single decision, and worth calling out on its own.

This codebase's comments are unusually good: almost every non-obvious choice records the failure that
motivated it, usually with a number. *"147 state-changing endpoints were never planned because they
sorted after the reads."* *"On one measured run fourteen functional requirements entered the gate and
one left."* *"31 claims were checked and 0 were reported supported, each carrying the same rejection
reason, which belonged to a different requirement."*

**Verdict: ✅ — keep doing this.** It is the reason this review was possible at all, and it is the
single highest-value habit in the repository.

Two risks to manage:

1. **Comment volume is now comparable to code volume.** `consolidate.py` is 940 lines of which a
   large fraction is prose; `graphify.py` opens with 30 lines of measured observations. That is
   sustainable while the measurements stay true and dangerous when they stop. A comment that says
   "measured: 1 642 rule entities, 1 632 conditionals" is a fact with a date on it, and nothing
   records the date.
2. **The prose is in the wrong place for some of it.** The tuning rationale in
   `testgen/signals.py`, the threshold derivation in `gates.py`, the packet-budget arithmetic in
   `assemble.py` — these are *design decisions*, and a new maintainer finds them only by reading
   1 000-line modules. Hence this document. A short `DECISIONS.md` entry per threshold, linked from
   the code, would be better than either extreme.

---

## Summary table

| # | Decision | Verdict | If I changed one thing |
|---|---|---|---|
| 0 | Evidence-grounded, fail-closed | ✅ | — |
| 1 | Python 3.11+, setuptools | ✅ | pin `graphifyy` exactly; add a lockfile |
| 2 | LangGraph as the engine | 🟡 | know that it is one `Send()` away from being replaceable |
| 3 | Deterministic routing, no supervisor LLM | ✅ | move the unwired routing skill out of the pack dir |
| 4 | Azure OpenAI only | 🟡 | a `provider` discriminator; and type the entailment step `llm` |
| 5 | Pydantic for schemas + prompts + settings | ✅ | hash the manifest alongside the skill in `StepRecord` |
| 6 | Workflows as data (skill packs) | 🟡 | close the two engine leaks (capability + schema registries) |
| 7 | Skills on disk, hashed, hot-reloaded | ✅ | key the cache on content, not mtime |
| 8 | Graphify instead of per-language ASTs | ✅ | a contract test against the real binary; tree-sitter for module surface |
| 9 | Boundaries: shape + one classification call | ✅ | report which candidate classes fell outside the 120 cap |
| 10 | Evidence packets, never source | ✅ | estimate and show cost before the run |
| 11 | Deterministic assembly; `elevate` on titles only | ✅ | — |
| 12 | `abstraction_basis` decides the tier | ✅ | — |
| 13 | A consolidation stage with hand-rolled similarity | 🟡 | embeddings to propose pairs, rules to confirm |
| 14 | Bounded repair; wording ≠ grounding | ✅ | consider narrowing repair to rewording only |
| 15 | Confidence recomputed, ceiling 0.95 | ✅ | never let anyone read it as a probability |
| 16 | Nothing deleted; unresolved partitioned out | ✅ | — |
| 17 | Objective vs interpretive checks | ✅ | mark entailment rejections interpretive |
| 18 | Jargon linter, not a prompt instruction | 🟡 | make the word lists loadable per pack |
| 19 | Technology-independent test specs | ✅ | the missing adapter pack is where the value lands |
| 20 | PACK 2 reads the artifact, not the models | ✅ | — |
| 21 | Qualification before design; account for everything | ✅ | — |
| 22 | Dimension activation as anti-explosion | ✅ | same embedding hybrid as #13 |
| 23 | Streamlit UI, artifact-driven tabs | ✅ | a job queue if runs get long |
| 24 | Snapshot the target | 🟡 | reflink/hardlink copy + a retention policy for `runs/` |
| 25 | Validate archives before writing | ✅ | — |
| 26 | Artifacts on disk, paths in state | ✅ | namespace artifact names per pack |
| 27 | Stub the model, freeze corpus baselines | ✅ | one contract test against the real Graphify |
| 28 | Decisions documented in comments | ✅ | date the measurements |

---

## The three things worth knowing before you touch anything

1. **The output is a document, and the product is the *trust* in that document.** Every strange-looking
   choice — the 0.95 ceiling, the jargon linter, the separate `requirement_analysis.md`, the refusal
   to let `elevate` write a citation — exists to stop a plausible-sounding, partly-invented document
   reaching a stakeholder. Changes that make the output longer, more confident or more complete at
   the expense of that are regressions even when they look like improvements.

2. **Judgement is the model's job; copying, counting and routing are not.** The strongest pattern in
   the codebase is "let the model classify, never let it locate, never let it route, never let it
   re-emit what already exists". Every time that line was crossed, the code records what broke.

3. **The system you were told about — a UI testing tool — is the pack that does not exist yet.**
   Everything in PACK 2's specification layer, especially `interface_hint`, `automation_readiness`
   and `TestInput.value_kind`, is scaffolding for that adapter, and the manifest says so in its
   header comment. If that is your assignment, read §19 first, then `schemas/testspec.py`, then
   `testgen/ingest.py` as the template for reading an upstream artifact defensively.

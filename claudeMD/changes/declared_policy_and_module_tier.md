# Change log — Declared policy, a module analysis tier, and complete graph coverage

Implements the plan in [questions.md §M Q111](../questions.md#q111-so-what-is-the-actual-bottleneck--the-graph-the-packets-or-the-prompt).
Design writeups of the two new modules: [capabilities/declarations.md](../capabilities/declarations.md),
[capabilities/modules.md](../capabilities/modules.md).

**One line:** Seven of thirteen requirement families had **never** been populated in any of the
twenty stored runs — not sparse, never — and the cause was not the prompts (which ask for all of
them) but that the graph carried no evidence class they could rest on, that 87.5 % of the graph
reached no model at all, and that nothing anywhere reasoned about a *part* of the product. This
change adds the missing producer for six orphaned node kinds, a module analysis altitude between
one boundary and the whole product, residual clustering so every meaningful node reaches a packet,
and a completeness report so an empty family says *why* it is empty.

**Status:** Implemented and tested in the working tree. Base commit `204eae8` ("Batching and
Semantic Graph"). Full suite: **762 passed, 1 failed** — the one failure
(`test_entry_point_adapters.py::test_registered_handlers_become_entry_points`, `assert 4 == 3`) is
pre-existing and unrelated, confirmed by stashing every file this change touches and rerunning it
against `204eae8`, where it fails identically. Baseline before this change was 703 passed / 1
failed, so this adds 59 tests and no regressions.

---

## The measurement that motivated all of it

Across **all twenty** runs in `runs/`:

```
                              populated in          |                        populated in
functional_requirements          19/20  ✅           | business_rules              0/20  ❌
business_requirements            13/20  ✅           | success_criteria            0/20  ❌
system_requirements               5/20  ⚠            | technical_constraints       0/20  ❌
out_of_scope, open_questions      3/20  ⚠            | data_requirements           0/20  ❌
                                                     | integration_requirements    0/20  ❌
                                                     | non_functional_requirements 0/20  ❌
                                                     | reporting_requirements      0/20  ❌
                                                     | assumptions                 0/20  ❌
```

And on the 381-file reference run (`runs/2584e3a7…`):

```
distinct entities reaching any packet   634 of 5,084  = 12.5%
median packet                           9 records / 3,343 chars
average packet                          8,103 chars against a 40,000 budget  = 20%
packets reporting an omission           0 of 84
findings per cluster                    1.42 against a stated ceiling of 34
```

The packets were not trimmed — they were **empty**, and the budget to carry more was already being
paid for and left unspent. Three root causes, each fixed below.

---

## Files changed

| File | Kind of change | Risk |
|---|---|---|
| `src/agentic/capabilities/declarations.py` | **New**, 601 lines — the missing producer | Low (additive; kill switch verified exact) |
| `src/agentic/capabilities/modules.py` | **New**, 503 lines — the module altitude | Medium (new LLM tier) |
| `src/agentic/capabilities/graph_ops.py` | +237 — `external_dependencies`, `_residual_groups`, coverage metrics, `DECLARATION_KINDS` widened, `EXTERNAL_SERVICE` in `compact_entity`, module codes on clusters | Medium (core clustering) |
| `src/agentic/capabilities/resolution.py` | +98 — `report_family_coverage` | None (reports only) |
| `src/agentic/schemas/brd.py` | +102 — `ModuleAnalysis` | Low (additive) |
| `src/agentic/llm/prompt.py` | +33 — `modules` work-item renderer | Low (registry entry; others untouched) |
| `src/agentic/config.py` | +31 — 6 settings, all defaulting to current behaviour | Low |
| `src/agentic/state.py` | +7 — `module_fragments` channel | Low |
| `src/agentic/schemas/registry.py`, `capabilities/registry.py` | +5 — registrations | None |
| `codebase-requirement-skills/pack.yaml` | +182/−35 — 4 new steps, `elevate` rewired, 3 instruction corrections | Medium (topology) |
| `tests/` | +59 tests across 2 new files; 5 existing files updated | None |

---

## 1. `extract_declarations` — six node kinds that had consumers and no producer

`compact_entity`, `render.py`, `gates.py` and `graph_ops.declaration_groups` all carried
hand-written support for `CONFIG_KEY`, `DATA_ENTITY`, `DATA_FIELD`, `DATA_INDEX`, `DOMAIN_POLICY`
and `EXTERNAL_SERVICE`. **Nothing emitted any of them** — they were orphaned when the Python-only
AST extractors were replaced by Graphify, which buys 36 languages and carries no declarations.

The consequence was not merely a gap. `DECLARATION_KINDS` was the ranking key in
`declaration_groups`:

```python
key=lambda item: (-sum(1 for e in item[1] if e.kind in DECLARATION_KINDS), item[0])
```

Always zero, so the sort collapsed to `item[0]` — the file path — which the comment three lines
above calls out as the exact defect `_stratified` exists to correct. A live, correct, fully wired
mechanism ran inert.

The inputs were **already in the inventory and never opened**: `schema.sql`, `.env.example`,
`docker-compose.yml`, `requirements.txt`, `package.json`, three `Dockerfile`s.

**Measured:** 633 nodes on the reference repository (141 `CONFIG_KEY`, 34 `DATA_ENTITY`, 363
`DATA_FIELD`, 35 `DATA_INDEX`, 4 `DOMAIN_POLICY`, 56 `EXTERNAL_SERVICE`), 12 credentials redacted.
Policy recovered that no call graph could carry:

```
app_users.role  accepts only admin, recruiter, sysadmin      ARCHIVE_AFTER_DAYS = 30
review_queue.state  accepts only open, resolved, discarded, approved
```

Deterministic, no model call, no tree-sitter. Full design and the four invariants it holds to
(fixed-value-only, credential redaction, additive-only, runtime-deps-only) in
[capabilities/declarations.md](../capabilities/declarations.md).

## 2. `partition_modules` / `analyse_module` — the missing altitude

`synthesize` sees one boundary (median nine nodes); `elevate` sees finding *titles*; between them
`digest_areas` is deterministic. So nothing reasoned about a part of the product, and four
requirement families had no vantage point from which they could be written.

**The partition was chosen by measurement, not preference.** Three candidates on the reference run:

- **Directories** → a *layer* split (`app/api` 1,081 nodes, `app/services` 1,299). Rejected.
- **Community detection** → one 1,244-node hairball spanning api+services+repositories. Rejected.
  (Also why Graphify's Leiden pass stays off.)
- **Boundary reach overlap** (Jaccard over `workflow_from`) → `/rounds` + `create_round` +
  `create_slot_invite`; `/review-queue/{id}` + `approve_review_item`. **Features.** Adopted.

293 boundaries → 21 modules (`PARS REVI ROUN ORCH INTA QUES GATH BOOK BACK LINK AUTH …`).

**The area-code inversion is the part that pays off immediately.** `pack.yaml` told every worker
"clusters covering the same part of the product must use the same code" — which parallel workers
*cannot* obey. The result was `ARCH`/`BAP`/`BATC`/`BCH`, four areas for batches, and 14 of 22 areas
holding one capability. Codes are now handed down from the partition.

Infrastructure note: the batching change's `produces[0]` channel routing and the
`_WORK_ITEM_RENDERERS` registry meant this second fan-out tier needed **no new dispatch code** —
a state channel, a renderer entry, a manifest step and one reducer.

## 3. Residual clustering — 12.5 % → 67.3 % coverage, for fewer calls

Boundary clustering answers "what can an outside actor cause to run", which is right for a
*requirement* and wrong for *coverage*. Background jobs, migrations, admin scripts and whole
subsystems no route touches reached no model at all.

`_residual_groups` groups meaningful unreached code **by directory** (one packet per file would
cost a call per file), ranks by degree, and carries its own `RESIDUAL` class so the stratifier's
per-class floor guarantees it is described. Residual clusters are flagged so the worker skips the
"work outward from the boundary" frame instead of inventing a boundary for code nothing reaches —
which Gate 2 would reject anyway.

```
                        before      after
graph coverage           12.5%      67.3%
clusters                    84        141
packet budget used         20%      25.5%
```

Declarations are excluded from residual (they have their own path) — without that, one `schema.sql`
produced both a declaration cluster and a residual cluster describing the same tables twice.
`declaration_groups`' per-file limit was also raised from `max_clusters` to 400: a 34-table,
363-column schema against a limit of 80 dropped most of the columns, and a column is where a
`NOT NULL` or a default actually lives.

## 4. `external_dependencies` — integration requirements become possible

`EXTERNAL_SYMBOL` is in `NOISE_KINDS` and `IMPORTS`/`REFERENCES` are outside
`WORKFLOW_EDGE_KINDS`. **Both exclusions were right and both stay** — import edges *"connect almost
everything to everything and would make every cluster the whole graph."* But they made
`integration_requirements` impossible rather than hard: the manifest asks for "one per external
system this cluster depends on" while every node naming one was filtered out before the packet was
built.

Fixed by carrying them as a deduplicated **`depends_on` fact** on the packet rather than as
walkable nodes: nothing is reachable *through* a dependency, and the fact is in front of the model.

## 5. `report_family_coverage` — the reason this went unnoticed for twenty runs

Gate 1 checks the graph. Gate 2 checks every surviving claim is grounded. **Neither ever checked
the document was complete**, so a BRD with seven families permanently empty passed everything —
each claim in it was individually well-evidenced. The quality bar was all precision, no recall.

Per family, it now answers the question that actually matters to a reader: empty because the
product **declares nothing** of that kind (an honest result), or empty because evidence sits in the
graph **unused** (a defect in this pipeline, and named as one). Reported, never enforced — a thin
document is a fact about a codebase as often as it is a bug.

## 6. Three instruction corrections in `pack.yaml`

- **`requirement_type`.** The step said *"`functional` or `business_rule`, the two tiers available
  to you here"* while the same step asked for seven further families and stated that field "is what
  gets published". A worker obeying it literally could not return a data, integration, quality or
  reporting finding at all. Corrected to set `abstraction_basis` and leave `requirement_type` alone
  — `BASIS_TYPES` is a total function from basis to tier and `consolidate.py:226` already enforces
  it, so the field was never the model's to set.
- **Area codes** now copied from `module_code` when present.
- **Residual clusters** get an explicit frame telling the worker not to invent a boundary.

---

## Bugs found and fixed during implementation

Recorded because each was caught by a test or a run against real data, not by review:

1. **`http://minio:9000` → `http:`** — `//` was stripped as a comment marker unconditionally. A
   wrong value is worse than no value, because nothing downstream can tell it is wrong.
2. **Absolute paths rejected** — `_MEANINGFUL_VALUE` required a leading letter, so `/var/lib/data`
   was dropped. A declared storage location is exactly the policy `declared_policy` exists to
   carry.
3. **Module codes collapsed to `RES2`…`RES14`** — codes were derived from `qualified_name`, which
   carries the file path, which carries the repository name, so every module on `resume-agent-PROD`
   scored "resume". Now derived from the boundary label only, and ranked by **lift** rather than
   frequency: raw frequency picks the commonest word, which is by definition the least informative.
4. **Junk module heads** (`APIRouter@17`, `msg.get@306`) — `boundary_ids()` includes exposers, which
   is right for reachability and wrong for partitioning. `build_boundary_clusters` already guarded
   this with `primary`; `partition_modules` now does too.
5. **Every module shown the same 60 declarations** — directory-ancestry scoping put repository-root
   policy in scope for all 21 modules. Replaced with a partition: nothing duplicated, nothing
   selected out. (Scoping by *file* was tried first and gives every module an empty list, since a
   schema holds no code.)
6. **One `schema.sql`, two clusters** — declaration and residual clustering both claimed it.
7. **Metrics counted pre-dedup** — `add_entity` is content-addressed, so the read count overstated
   what a reviewer could actually cite.

## What stays exactly the same

- **Every new setting defaults to on, and each has a verified-exact off switch.**
  `declaration_extraction=False` reproduces the previous graph node-for-node
  (`test_switching_declaration_extraction_off_reproduces_the_previous_graph`);
  `module_analysis=False` reproduces the previous two-tier pipeline.
- **Purely additive to the graph.** `test_declarations_only_add_and_never_rewrite` asserts every
  pre-existing entity survives byte-identical.
- **`NOISE_KINDS` and `WORKFLOW_EDGE_KINDS` are unchanged.** The dependency fix works around them
  rather than relaxing them.
- **`clusters` / `synthesize` / `elevate` / assembly / gates / rendering** keep their shapes.
  Clusters gain three optional keys; `elevate` gains one consumed artifact.
- **`clusters` and `test_batches` fan-out payloads are byte-identical** — only `modules` has a
  renderer entry.

## Known costs and risks — read before merging

- **Call count rises, then falls.** +21 module calls and +47 residual clusters against 84 boundary
  calls at 20 % budget. Net direction depends on repository shape; on the reference run clusters
  went 84 → 141, so this run costs *more* calls carrying ~2× the evidence. The earlier promise that
  coverage would be strictly cheaper assumed packing existing clusters fuller; residual clustering
  adds genuinely new work instead, and that is a real cost this change does not recover.
- **Consolidation has never run at this volume or across tiers.** ~2× the evidence and a new
  altitude mean `consolidate` / `resolve` / `score_confidence` will need tuning. **Expect the first
  full run to look noisier before it looks deeper.**
- **Cross-altitude duplication is managed by instruction, not construction.** `generalises` lets
  assembly collapse an empty module statement; nothing prevents one being written.
- **`MERGE_SIMILARITY = 0.30` is measured on one repository** and has no tuning harness.
- **191 of 293 boundaries landed in the residual module** on the reference run and get no
  module-level analysis. Reported, not hidden.
- **The declaration parsers are a long tail.** ORM models and Prisma schemas are not read yet;
  unusual value shapes are dropped rather than guessed. `report_family_coverage` is what makes each
  miss visible instead of silent.
- **A declared value is not a used value.** There are no `READS_CONFIG` edges from `CONFIG_KEY`
  nodes, so a quality requirement resting on one asserts *declared* policy, not observed behaviour.

## Merge checklist

1. `pack.yaml`'s extraction and synthesis phases are reshaped, not line-inserted — reconcile by
   hand if `main` has touched them.
2. `graph_ops.py` changes cluster over `DECLARATION_KINDS`, `compact_entity`, `build_packets`,
   `declaration_groups`' call site and `build_boundary_clusters`. Keep the union on conflict.
3. **The extraction step sequence is still duplicated in three test files** and only
   `test_pack_compile` derives it from the manifest. This change touched all three by hand; the
   next one must too. Flagged in `batch_boundary_classification.md` and still unfixed.
4. `tests/test_harness_scored.py::test_a_language_the_corpus_never_contained_is_extracted_at_all`
   was **rewritten, not repaired.** It was a pinned regression recording the Graphify swap's loss,
   whose docstring said to rewrite it when the limitation lifted. It now asserts the recovered
   declarations *and* still pins the half that remains: Graphify finds no boundary in top-level DSL
   calls, so the Sinatra routes are still invisible and no functional requirement can be grounded
   there.
5. Run `tests/test_declarations.py tests/test_modules.py` as the fast targeted signal, then the
   full suite, and confirm the only failure is the pre-existing `assert 4 == 3`.
6. **Not yet done:** no real LLM run has exercised `analyse_module`. Every result above is from
   deterministic steps and stubbed model calls. The module tier's *output quality* is unmeasured —
   `python -m tests.harness` against the corpus is the next step before trusting it.

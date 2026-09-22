# `modules.py` — Phase 2b/3a(ii): the altitude between one workflow and the whole product

> **Added 2026-09-22.** The pipeline had two analysis altitudes and needed three. `synthesize` sees
> one interaction boundary (a median of **nine** graph nodes); `elevate` sees a list of finding
> *titles* for the whole repository. Between them sat `digest_areas`, which is **deterministic** —
> so nothing anywhere reasoned about a *part* of the product as a whole. The change itself is in
> [changes/declared_policy_and_module_tier.md](../changes/declared_policy_and_module_tier.md).

Module: [src/agentic/capabilities/modules.py](../../src/agentic/capabilities/modules.py)
Schema: [`ModuleAnalysis`](../../src/agentic/schemas/brd.py) — what a module worker may return
Registered capabilities: `partition_modules` (step `partition_modules`), `collect_module_analyses`
(step `collect_module_analyses`) — both `on_fail: warn_and_continue`
Consumers: `analyse_module` (LLM step, `fan_out: modules`, schema `ModuleAnalysis`) →
`collect_module_analyses` → `elevate`; and `build_boundary_clusters`, which reads
`boundary_modules` to hand each cluster its area code
Settings: `module_analysis` (kill switch), `min_modules` (6), `max_modules` (20),
`module_packet_chars` (30 000)

---

## 1. What was missing

```
84 boundary clusters ──LLM──> 119 findings          synthesize        ✅
         │
         ▼  deterministic grouping of TITLES by area code
22 areas ─────────────────────  NO LLM AT THIS LEVEL  ◄── the hole
         │
         ▼
1 elevate call over titles ──> business requirements  elevate         ✅
```

`digest_areas`' own docstring says *"Titles only… reading them is not what the remaining call is
for."* It is bookkeeping, not analysis. So a whole class of true statement had nowhere to be
written:

- *every write path in this part goes through one repository layer* → a system requirement
- *nothing here accepts a request without a tenant id* → a cross-cutting business rule
- *the retry bound is three across all six handlers* → a quality property
- *this part is the only thing that talks to S3* → an integration requirement

Each is true of a module and **invisible from inside any one of its boundaries**, and each belongs
to a family no stored run had ever populated.

## 2. Why the partition is rooted in boundaries

Three candidate partitions were measured on the 381-file reference run *before* anything was built.
This is the load-bearing design decision in the module.

| Partition | Result | Verdict |
|---|---|---|
| **Directories** | `app/api` 1,081 nodes · `app/services` 1,299 · `app/repositories` 138 · `app/db` 129 | A **layer** split. "All the services" is a horizontal slice through every feature at once — worse evidence than one boundary, not better. |
| **Community detection** (label propagation over `CALLS`/`IMPORTS`/`REFERENCES`) | one community of **1,244 nodes** spanning api+services+repositories+extraction; another of 700 covering the whole frontend; 317 communities but only 18 with ≥20 nodes | A **hairball**. A layered application is densely interconnected by construction, so modularity-based clustering cannot separate features. Also why Graphify's own Leiden pass stays off (`--no-cluster`). |
| **Boundary reach overlap** (Jaccard over `workflow_from` sets) | `/rounds` + `create_round` + `create_slot_invite` + `/slots/{id}/propose`; `/review-queue/{id}` + `approve_review_item` + `rerun_skill_override`; `/question-banks/generate` + `interview_questions` | **Features.** Adopted. |

The reason the third works is not subtle: **an interaction boundary is the product's own unit of
offered function**, so boundaries that run the same code are offering parts of the same feature.
Feature structure is recoverable from behaviour even where it is absent from the directory tree.

Result on the reference repository — 293 boundaries → 21 modules:

```
PARS  REVI  ROUN  ORCH  INTA  QUES  GATH  BOOK  BACK  LINK  AUTH
RELO  EXEC  UPLO  INTE  STAF  AGEN  CONF  BANK  NOTE  MISC
```

## 3. How it works

1. **`workflow_from`** per boundary (depth 6, ≤600 nodes) — the same traversal clustering uses, so
   a module is grounded in the same reachability notion as everything downstream.
2. **Agglomerate** by Jaccard ≥ `MERGE_SIMILARITY` (0.30), **best pair each round**. Deliberately
   not a single threshold pass: one pass is order-dependent, so two runs over one graph could
   produce different modules and the reproducibility claim for the deterministic half would be
   false. `min_modules` floors the merge so a codebase whose boundaries share a bootstrap path
   cannot collapse into one module.
3. **Only real boundaries seed it.** `boundary_ids()` deliberately includes *exposers* (a handler
   counts, because a requirement citing it is citing the consumption point) — right for
   reachability, wrong for partitioning. Including both puts a route and its handler in as separate
   seeds, halving measured similarity between real features and promoting junk leads
   (`APIRouter@17`, `msg.get@306`) to module heads. `build_boundary_clusters` guards the same way.
4. **Codes by distinctiveness, not frequency.** Raw frequency picks whichever word is commonest,
   which on any real repository is the least informative one — every boundary on `resume-agent-PROD`
   scored "resume", so every code after the first collided into `RES2`…`RES14`. Scoring by **lift**
   picks the word that tells a module apart. `qualified_name` is never read (it carries the path,
   and the path carries the repository name); only the boundary's own label. No usable word falls
   back to a positional `M07`, which is honest — it does not pretend to mean anything, and the
   heading a reader sees is written by the model anyway.
5. **Declarations are partitioned, not scoped.** Each declaration goes to whichever module holds
   the most code in the directory declaring it; one with no module beside it belongs to the
   repository and is described by its own `declaration_groups` cluster. See §5.
6. **`boundary_modules`** maps boundary → code, read by `build_boundary_clusters`.

## 4. The area-code inversion — the part that pays off immediately

`pack.yaml` told every synthesis worker: *"Clusters covering the same part of the product must use
the same code."* **They cannot comply.** They run in parallel and none sees another's reply. On the
reference run, 84 isolated workers produced:

```
ARCH  Batch Archiving             1 boundary,  1 capability
BAP   Batch Application Process   1 boundary,  1 capability
BATC  Batch Restoration           1 boundary,  1 capability
BCH   Batch Archive Management    3 boundaries, 3 capabilities
```

Four areas for batches; 14 of 22 areas held a single capability. A code **handed down** from the
partition is the only version of that instruction a parallel worker can actually obey. This is
worth shipping even without the L2 analysis call.

## 5. Declarations: partitioned, not scoped

Two scoping rules were tried and both fail on real layouts, which is worth recording because both
look obviously right:

- **By file.** Every module gets an empty list — a schema and a settings file hold no code, so they
  appear in no module's member files.
- **By directory ancestry.** Every module gets the *same* list — repository-root policy is
  genuinely in scope for all of them. On the reference repository that meant 21 modules each shown
  the same 60 declarations: an invitation to publish the same data invariant 21 times and leave
  consolidation to clean up work that should never have been commissioned.

So it **partitions** — the same move `collect_boundary_assignments` made for candidates and
`_residual_groups` made for unreached code: nothing duplicated, nothing selected out. A declaration
no module keeps code beside belongs to the repository and is left out here rather than copied
everywhere, because `declaration_groups` already describes it once.

## 6. Benefits

- **The missing altitude exists.** Four requirement families gain the only vantage point from which
  they can be written.
- **Area fragmentation is structurally fixed**, not mitigated — a worker is told its code.
- **The two evidence classes a boundary packet cannot carry** (`depends_on`, `declared_policy`)
  reach a model at the level where they make sense.
- **`elevate` reads module purposes** written by workers that saw declared configuration, schema
  constraints and external dependencies — none of which reaches a title list.
- **Cheap.** ~12–21 calls at ~30 k chars. Against 84 boundary calls running at 20 % of budget,
  and with the residual-coverage change, total calls go *down*.
- **Built on existing machinery.** The `produces[0]` channel routing from the batching change and
  the `_WORK_ITEM_RENDERERS` registry mean a second fan-out tier needed no new dispatch code.

## 7. Drawbacks and limits

- **The residual module is large and mostly inert.** On the reference run 191 of 293 boundaries
  overlapped with nothing at 0.30 and landed in `MISC` (931 nodes). They keep their findings and
  their area code, but get **no module-level analysis** — the prompt tells `residual` workers to
  return a code and stop. Reported, not hidden, and the honest statement is that most of this
  repository's boundaries are one-off behaviours.
- **`MERGE_SIMILARITY = 0.30` is measured on one repository.** It separates scheduling from review
  from intake *there*. A codebase with heavy shared utilities will over-merge; one with very thin
  handlers will under-merge and inflate `MISC`. There is no tuning harness for it yet.
- **Agglomeration is O(n²) per round.** 293 boundaries is fine; several thousand would need a
  neighbour index.
- **Duplication across altitudes is managed by instruction, not by construction.** `generalises`
  lets assembly collapse a module statement that adds nothing, and the prompt is explicit, but
  nothing *prevents* a module worker restating a boundary capability. `consolidate` has never run
  at this volume or across tiers.
- **`max_modules = 20` is a real cap.** Modules past it fold into the residual module. Their
  boundaries and findings survive; their module-level analysis does not.
- **A module is not always a feature.** The partition recovers feature structure from behaviour,
  and a product whose boundaries genuinely all share one pipeline will produce one module and a
  large residual — correct, and not very useful.
- **`module_capabilities` is the field most likely to produce noise.** "True of the module as a
  whole" is a judgement, and the failure mode is a generalisation of one boundary's work dressed up
  as a module property. The prompt's test ("would this be just as true written from one endpoint?")
  is the only guard.

# New fixes: duplication, synthesis coverage, multi-call elevate, cost

What changed in the requirements pipeline, why, and how each change was checked. It was done in two
rounds, each starting from a real run on `neuleap_talent_scooper_v1.6.4` with model `gpt-4o-mini`:

- **Round 1** (§2–§8) started from run `c488c497`: 153 published requirements, run before any of
  these fixes.
- **Round 2** (§10) started from run `8d93e96f`, the first live run with the round-1 fixes. It
  confirmed most of them and exposed two defects in the new top tier, which are now fixed.

§11 explains how run cost is measured and how far the number can be trusted.

---

## Contents

| § | | |
|---|---|---|
| [1](#1-before-and-after-at-a-glance) | Before and after at a glance | the numbers, measured where possible |
| [2](#2-engine-fixes) | Engine fixes | fan-out bug, module renderer, work-item renderer, token metering |
| [3](#3-duplication-d1d7-and-the-duplicate-report) | Duplication | D1–D7 and the new duplicate report |
| [4](#4-more-boundaries-reach-synthesis) | Synthesis coverage | twins, packing, a cap that grows, module sampling |
| [5](#5-areas-are-modules) | Areas are modules | 54 invented areas → the module partition |
| [6](#6-the-top-tier-in-several-calls-e1e5) | Multi-call elevate | E1–E5 |
| [7](#7-new-settings) | New settings | every knob, with its default |
| [8](#8-tests) | Tests | what guards each fix |
| [9](#9-not-done-and-how-to-verify-on-the-next-run) | Not done, and how to verify | what's left open and how to check it |
| [10](#10-round-2-what-the-first-live-run-showed-and-what-was-fixed) | Round 2 | outcomes rejected against borrowed code; proposals dropped by the merge |
| [11](#11-run-cost-how-it-is-measured-and-how-far-to-trust-it) | Run cost | per-call metering, per-model pricing, the app's cost panel |

---

## 1. Before and after at a glance

| | Before (run `c488c497`) | Live run with round 1 (`8d93e96f`) | After round 2 |
|---|---|---|---|
| `analyse_module` model calls | **3,171** (151 × 21) | **21** ✔ measured | 21 |
| Total model cost per run | **≈ $11**, estimated by rebuilding every prompt | **$0.73**, measured from provider token counts (§11) | about the same; round 2 adds no calls |
| Model calls, whole run | ≈ 3,340 | **275** | about the same; the sweep has fewer units, so slightly fewer calls |
| Modules `elevate` could see | 1 of 21 | all 21 (`module_analyses.json` 7.7 MB → 87 KB) | same |
| Requirements from coding-agent guides | 68 of 153 (44%) | 0; 17 files excluded and listed | same |
| Interaction boundaries with a packet | 80 of 303 (26%) | **338 of 338 (100%)** | same |
| Areas in the document | 54 invented codes | **18**, named after the modules | same |
| Real duplicate pairs published | 3, plus the design-guide repeats | the duplicate report lists 2 near-duplicate statements | same |
| Capabilities bound to an outcome | 8 of 374, one invented | **59 of 312** (merge used 6 of 80 proposals) | expected to rise sharply; to be confirmed on the next run (§10) |
| Business objectives published | 2 of 4 | **2 of 6** (4 rejected against borrowed code) | outcomes can no longer be rejected that way (§10) |
| Capabilities linked to an objective | not measured | **5 of 95** | follows the two rows above |
| Test suite | 801 tests, 1 failing | 837, same failure | **844, same failure** |

The pre-existing failure is `test_entry_point_adapters.py::test_registered_handlers_become_entry_points`
(expects 3 entry points, finds 4). It failed before any of these changes and is unrelated.

`runs/checkpoints.db` is still 12.5 GB. It accumulates every run ever made, including the ones from
before the fan-out fix, so deleting it (with no run in progress) reclaims the space.

---

## 2. Engine fixes

### 2.1 Fan-out behind fan-out: 3,171 calls where 21 were meant

**Problem.** `synthesize` and `analyse_module` are both fan-out steps and sit next to each other in
`pack.yaml`. `builder.py` dispatched the second fan-out from a conditional edge on the first. LangGraph
evaluates a conditional edge once per *worker*, so every one of the 151 synthesis workers dispatched
all 21 module analyses.

**Change.** [builder.py](../src/agentic/graph/builder.py): when a fan-out step is followed by another
fan-out step, the builder inserts a join node (`__join__<step>`) behind a plain edge. The join runs once,
after every worker has finished, and the second dispatch hangs off it. As a safety net,
[modules.py](../src/agentic/capabilities/modules.py) `collect_module_analyses` keeps one analysis per
module (the fullest) and reports how many repeats it dropped (`module_replies_duplicated`).

**Why it's better.** About 95% of the spend disappears, run time and Azure rate-limit pressure drop,
and checkpoints stop storing thousands of copies of the state. The fix lives in the engine, so any
future pack that puts two fan-outs back to back is also protected.

### 2.2 `elevate` saw one module of twenty-one

**Problem.** `module_analyses` (7.7 MB) had no prompt renderer, so it went through the generic
fallback: cut at `MAX_ARTIFACT_CHARS = 60_000` and sent as a truncated JSON preview. Because the list
is sorted by module code, that preview was about 25 copies of the first module.

**Change.** [prompt.py](../src/agentic/llm/prompt.py) `_render_module_analyses`: one compact entry per
module (code, name, purpose, and up to five finding titles per family), with duplicates skipped.

### 2.3 Synthesis workers never saw their `module_code`

**Problem.** The default work-item renderer sent a synthesis worker only the boundary, its kind, its
location and the evidence. The instructions say "copy `module_code` exactly" and "a `residual`
cluster has no boundary above it", but neither field was ever in the payload. Workers therefore
invented area codes (54 areas for 21 modules), and a worker reading slice 2 of 3 had no way to know
slice 1 existed.

**Change.** `_render_cluster_work_item` in [prompt.py](../src/agentic/llm/prompt.py) now sends
`module_code`, `residual`, `part`/`of_parts`, `directory` and `packed_boundaries`. It adds a note when
the work item is a continuation slice or a packed cluster (§3 D2, §4). The testgen pack's fan-out
target has a different name, so it is unaffected.

### 2.4 Every run now records what it cost

**Change.** Round 2 replaced this mechanism with a dedicated meter; see §11.
- [nodes.py](../src/agentic/graph/nodes.py) runs every node inside a usage meter. That includes
  deterministic steps, because the correspondence check calls a model. It also means the test
  stubs needed no changes.
- Each step record now carries `input_tokens`, `output_tokens` and `cached_input_tokens`.
- The finalise node and the halt node write `artifacts/llm_usage.json`, with calls, tokens and an
  estimated cost per step and in total.
- Rates are configurable (§7) and default to gpt-4o-mini list prices.

**Per-step model deployment.** Set `STEP_DEPLOYMENTS='{"elevate": "gpt-4o"}'` in `.env` to give one
step a stronger deployment ([steps.py](../src/agentic/llm/steps.py) `_settings_for`). The merge step
(E2) is a single call, so moving it to gpt-4o costs cents while every fan-out step stays on the cheap
default.

---

## 3. Duplication (D1–D7) and the duplicate report

### D1 — Developer-tooling instruction files are not product policy

**Problem.** `.agents/skills/*/DESIGN.md` and `SKILL.md` are UI style guides for the coding agents the
developers use. They became declaration clusters, and 68 requirements came from them ("enforce a
single colour palette", "no overlapping elements" ×3, because the guide repeats its own rules).

**Change.** [graph_ops.py](../src/agentic/capabilities/graph_ops.py):
- `is_dev_tooling_path` recognises tool conventions: `.agents`, `.claude`, `.cursor`, `.github`,
  `.windsurf`, `.codex`, `.gemini`, `.kiro`, `.roo`, `.clinerules`, `.junie`, `.amazonq`, …, plus
  `CLAUDE.md`, `AGENTS.md`, `GEMINI.md`, `.cursorrules` and `copilot-instructions.md`.
- `dev_tooling_instruction_files` returns such files **only when no product code `LOADS` them**. A
  skill the product actually loads at runtime stays product behaviour, wherever it lives. This is the
  same rule the existing `LOADS` edge already encodes.
- `declaration_groups` skips those files. The planner names them in the new `cluster_plan` artifact,
  and the gap analysis lists them under "Instruction files for developers' coding agents (not product
  requirements)".

Extraction is untouched: the graph still holds these files, and they are only kept out of synthesis.
On the real graph, 17 files were excluded.

### D2 — Slices after the first re-described the entry point

**Problem.** `resolve_reapplication` was split into packets `#1` and `#2`. Both workers wrote up the
entry point, and the result was FR-AP-04 and FR-AP-05.

**Change.** Slice position now reaches the worker (§2.3), and `pack.yaml` `synthesize` gains the rule
"A slice after the first is a continuation… do not write the boundary's own capability again."

### D3 / D5 — Identical evidence as a merge signal

**Problem.** Every existing merge rule makes the wording decide, and wording is exactly what varies
when two workers describe one behaviour. FR-CND-11 and FR-CND-12 cite the same line and scored **0.64
against a 0.65 bar**. FR-IM-04 and SYS-071 cite the same 15 lines, but the cross-tier merge needs 0.85.

**Change.** [consolidate.py](../src/agentic/capabilities/consolidate.py) `_merge_identical_evidence`
runs last among the merges, as a sweep for what the thresholds let through. Two claims merge only
when all of the following hold:

1. their evidence is **exactly** the same set of spans, not merely overlapping;
2. they assert the **same operation** (D4);
3. neither declares the other as its parent;
4. the wording test for their case passes:
   - **same level, not flagged as conflicting:** statement similarity ≥ **0.40** over a function
     body, or ≥ **0.50** when the citation is a single line (single lines are often decorators or
     registrations that several capabilities pass through);
   - **different levels:** only when repository-wide capability clustering had independently put
     both in the same group.

The more abstract claim survives and records the one it absorbed in `superseded`.

The cross-level guard came from a measured regression. The scored harness caught a true system
requirement ("save an order so its details are retained") being folded into a mis-levelled
capability on the same lines. The rule was tightened until that could not happen, and the harness
passes again.

### D4 — Verbs are compared as operations

**Problem.** The verb guard, which stops "view a bill" merging with "delete a bill", compared literal
words. So `validate`/`check` and `retrieve`/`get` were treated as different, and `_action_of` returned
adverbs ("consistently", "exclusively") as the verb.

**Change.**
- New [actions.py](../src/agentic/capabilities/actions.py) holds about 27 operation classes (read,
  search, create, update, delete, validate, enforce, send, schedule, approve, reject, publish,
  retract, …). They are the ordinary verbs of software requirements and name no domain.
- Two verbs in one class are one operation. Two verbs in different classes never merge. A verb in no
  class is compared as itself, which is the old behaviour.
- Inflections and the capability clusterer's stems (`retriev`) are handled.
- `_action_of` skips `-ly` adverbs, with exceptions for verbs such as `apply` and `supply`.
- Used by consolidation (`_same_action`), capability clustering (`relate` no longer calls a synonym
  pair "conflicting") and resolution.

One existing test pinned the old trade-off ("submit" and "lodge" must stay apart). It was replaced by
two tests: synonyms now merge, and different operations on the same code still stay apart.

### D6 — Conflicts are grouped by evidence and operation

**Problem.** `resolution.py` grouped competing readings by *(capability cluster, spans, literal verb)*.
Readings written with synonyms escaped the group, and so did readings filed in different capability
clusters, so both got published.

**Change.**
- The key is now *(spans, operation class)*.
- When readings tie on support and each sits at a **different level** (one behaviour filed as a
  capability and as the internal behaviour behind it), the most abstract one is kept and the rest are
  folded in with disposition `merge`. Before, both were withheld and the behaviour vanished from the
  document.
- Same-level ties keep the existing, tested policy: merged when the wording agrees, withheld as open
  questions when it genuinely diverges.

### D7 — One behaviour, one requirement

**Change.** `pack.yaml` `synthesize` now says: when the same operation on the same code serves several
actors (a recruiter and a candidate both retrieving slots), write **one** requirement naming each
actor.

### Duplicate report in `final_check`

**Change.** [resolution.py](../src/agentic/capabilities/resolution.py) `_duplicate_report` lists the
published pairs that still look duplicated. These are **reported, never merged**, because every rule
that could have merged them declined for a reason:

| Code | Signal | Threshold |
|---|---|---|
| `DUPLICATE_EVIDENCE` | identical citations, same operation, some wording overlap | similarity ≥ 0.25 |
| `NEAR_DUPLICATE_TITLE` | titles in one area share their meaningful words (plural and possessive normalised) | word Jaccard ≥ 0.80 |
| `NEAR_DUPLICATE_STATEMENT` | same operation, same cited file, statements alike | similarity ≥ 0.75 |

The pairs appear in `requirement_analysis.md` under "Structural findings". The counts become run
metrics (`duplicate_pairs_identical_evidence`, `duplicate_pairs_similar_title`,
`duplicate_pairs_similar_statement`), so duplication is a number that can be driven down.

The thresholds were tuned on the real document. A first cut used a character ratio for titles and
flagged 26 pairs, most of them false ("Retrieve candidate details" against "Retrieve candidate email
drafts" scores 0.84 by characters). The final version flags exactly the three real pairs and nothing
else.

**Result on the real pairs:**

| Pair | Before | After |
|---|---|---|
| FR-CND-11 / FR-CND-12 (same line, retrieve/retrieve, 0.64) | both published | merged (D3) |
| FR-IM-04 / SYS-071 (same body, validate/check, same capability group) | both published | merged (D4 + D5) |
| FR-AP-04 / FR-AP-05 (resolve/reapply) | both published | prevented at the source (D2), reported if it recurs |
| SYS-031 / 034 / 040, SYS-007 / NFR-01, … (design guides) | published | never synthesised (D1) |

---

## 4. More boundaries reach synthesis

**Problem.** `max_clusters = 80` was a fixed cap, and 223 of 303 boundaries never got a packet. At the
same time packets used a median of 6.6k of their 40k-character budget, so most of what each call was
paying for went unused. Two further problems:
- Boundary "twins" were grouped only when their workflow sets were *exactly equal*. Every boundary's
  own handler is in its own set, so this never fired (`also_exposed_by` was empty on all 151 clusters).
- The cap sampled by class but not by part of the product.

**Change** ([graph_ops.py](../src/agentic/capabilities/graph_ops.py) `build_boundary_clusters`):

1. **Near-identical twins.** Boundaries whose workflows have Jaccard ≥ 0.8 share one packet
   (`_workflow_twin`). An empty workflow is never a twin.
2. **Packing.** Up to `boundaries_per_packet` (default 4) small boundary groups **of the same module**
   share a packet, never more than 85% of the character budget (`_pack_small_groups`). Large groups
   travel alone, as before. The worker is told each packed boundary is its own entry point.
3. **A cap that grows.** `max_clusters` is now a floor. The boundary cap rises to cover every group,
   up to `max_clusters_ceiling` (default 250). Setting the ceiling to 0 restores the fixed cap.
4. **Module-aware sampling.** When the cap does bind, `_stratified` still guarantees every entry-point
   class a share. Within each class it now takes members round-robin across modules, so one densely
   connected module can't consume the whole quota (`_spread_across_modules`).
5. **Coverage is reported.** `cluster_plan` records boundaries planned against total, coverage percent,
   the cap, packing and twins. The gap analysis gains an "Interaction boundaries analysed" section.

**Measured on the real graph** (deterministic, no model calls):

| | clusters | boundary packets | boundaries covered | median boundary packet |
|---|---:|---:|---:|---:|
| Original run | 151 | 84 | 80 / 303 (26%) | 6.6k chars |
| Old cap, with D1 and twins | 143 | 91 | 96 / 303 (32%) | 11.2k chars |
| **New defaults** | **166** | **114** | **303 / 303 (100%)** | **16.2k chars** |

That is 15 more calls than the original run for 3.8× the boundary coverage.

---

## 5. Areas are modules

**Problem.** Fifty-four areas for twenty-one modules. Workers invented codes because they never saw
their module code (§2.3), and each requirement could name a code of its own.

**Change.**
- [steps.py](../src/agentic/llm/steps.py) copies the work item's `module_code` onto each fan-out
  fragment.
- In [assemble.py](../src/agentic/capabilities/assemble.py), `_area_index` files a fragment under its
  module, and the module worker's business name (from `module_analyses`) names the area.
- `item_area` honours a requirement's own `area` only when it names *another* module.
- Declaration and residual clusters, which have no module, keep the worker's code and the existing
  heading reconciliation. Module codes are never folded into one another.

---

## 6. The top tier in several calls (E1–E5)

**Problem.** One gpt-4o-mini call was handed every capability title (a 50 KB digest) and asked for the
product's purpose, its outcomes, and an exact-title binding for every capability. It bound 8 of 374
capabilities, invented a title, and 2 of its 4 outcomes died downstream because the one or two
capabilities under them did. BR-001's only acceptance criterion was then derived from an unrelated
boundary, which is how "collect feedback" came to be checked against a sysadmin health check.

**Change.** Nine steps replace one call. Four of them call a model, each small; the rest are
deterministic. See [elevation.py](../src/agentic/capabilities/elevation.py) and `pack.yaml`.

| Step | Model? | What it does |
|---|---|---|
| `index_capabilities` | no | Every capability gets an id (`C001`…) and one line of statement. Cuts one work item per area (≤ 60 capabilities each). |
| `propose_outcomes` **(E1)** | fan-out, about 1 per module | "Which 1–3 outcomes do these capabilities serve?" Binds **by id**. `unplaced` is an honest answer. |
| `collect_outcome_proposals` | no | Discards ids a worker was never given (counted), and numbers proposals `P01`… |
| `elevate` **(E2)** | 1 call | Merges proposals into the product's outcomes, **by proposal id** (`from_proposals`). Writes purpose, problem and `personas`. Falls back to title binding if E1 produced nothing. |
| `compose_outcomes` **(E3)** | no | Expands proposal ids into capability titles. A title that doesn't exist is dropped and counted (`capability_titles_invented`). |
| `bind_orphans` **(E4)** | fan-out, batches of 40 | For every capability still unbound: outcome `O1`… or `NONE`. |
| `collect_orphan_bindings` | no | Folds those answers in; reports `binding_coverage_percent`. |
| `review_outcomes` **(E5)** | fan-out, 1 per outcome | Writes 2–5 acceptance criteria from the capabilities actually bound, and flags an outcome that only names an area. |
| `apply_outcome_reviews` | no | Sets the criteria (so hedging no longer invents one) and turns flags into open questions. |

**Why it's better:**
- **No model call names a capability by title.** Invented bindings become impossible, and any
  invalid id is counted.
- **Each judgement is small.** One part of the product at a time, with a one-line statement per
  capability rather than a bare title.
- **Almost every capability ends up attached,** so an outcome no longer dies because the one or two
  capabilities under it failed. The rest are unattached deliberately, and the count is reported.
- **Criteria come from the outcome's own capabilities,** not from whichever boundary its first
  citation reached.
- **Degrades safely.** Every collector is `warn_and_continue`, and `multi_call_elevation=false`
  restores the single call.
- **Cost:** roughly 21 + 1 + 3 + 8 ≈ 35 small calls, a few cents (§1).

`elevate` also gets compact renderers. Once proposals exist, the 50 KB title digest shrinks to area
names and counts, so the model isn't invited to bind by title again.

**Personas.** `TopTier.personas` is carried into `ProductBRD.personas` and rendered in the BRD under
"Who the product serves".

---

## 7. New settings

All are in [config.py](../src/agentic/config.py) and can be set in `.env`.

| Setting | Default | Effect |
|---|---|---|
| `MAX_CLUSTERS` | 80 | Now the *floor* of the boundary cap |
| `MAX_CLUSTERS_CEILING` | 250 | The cap grows to cover every boundary group up to this; 0 = old fixed cap |
| `BOUNDARIES_PER_PACKET` | 4 | Small same-module boundaries per packet; 1 = no packing |
| `MULTI_CALL_ELEVATION` | true | E1/E4/E5 on; false = the old single `elevate` call |
| `OUTCOME_CAPABILITIES_PER_ITEM` | 60 | Capabilities per E1 worker |
| `ORPHAN_BATCH_SIZE` | 40 | Capabilities per E4 call |
| `STEP_DEPLOYMENTS` | `{}` | JSON map step → deployment, e.g. `{"elevate": "gpt-4o"}` |
| `LLM_RATES` | `{}` | JSON map model-name prefix → `{input, cached_input, output}` USD per million, merged over the built-in list prices (§11) |
| `LLM_INPUT_USD_PER_MILLION` / `LLM_CACHED_INPUT_USD_PER_MILLION` / `LLM_OUTPUT_USD_PER_MILLION` | 0.15 / 0.075 / 0.60 | Fallback rates for a model no `LLM_RATES` prefix matches |

Thresholds that are code constants, each documented at its definition:

| Constant | Value |
|---|---|
| `TWIN_JACCARD` | 0.8 |
| `PACK_FILL` | 0.85 |
| `IDENTICAL_EVIDENCE_SIMILARITY` | 0.40 |
| `IDENTICAL_LINE_SIMILARITY` | 0.50 |
| `NEAR_DUPLICATE_TITLE` | 0.80 |
| `NEAR_DUPLICATE_STATEMENT` | 0.75 |
| `DUPLICATE_EVIDENCE_WORDING` | 0.25 |

---

## 8. Tests

New file [tests/test_new_fixes.py](../tests/test_new_fixes.py), 42 tests, each built from a measured
defect (round-2 tests are listed in §10 and §11):

- **Fan-out:** a toy pack with two adjacent fan-outs. The second must run 3 times, not 15. Checked
  against the old `builder.py` (runs 15, test fails) and the new one (runs 3, passes).
- **Module analyses:** one reply kept per module, and the fullest wins.
- **Usage metering:** tokens captured from a real LangChain chat model (the fake model), cost
  arithmetic, and per-step deployment override.
- **Renderers:** all 21 modules reach the prompt untruncated; a synthesis worker sees its module
  code, its slice position, and its packed boundaries.
- **D1:** path recognition; an unloaded dev-tooling file is excluded while a loaded one is kept;
  declaration clusters skip excluded files.
- **D3–D6:**
  - verbs are compared as operations;
  - adverbs are skipped;
  - the FR-CND-11/12 and FR-IM-04/SYS-071 pairs merge;
  - a system requirement is **not** folded into a mis-levelled capability;
  - a cross-level conflict tie keeps the abstract reading;
  - the duplicate report flags the real pair and not the false positive.
- **Coverage:** twin detection; packing respects module and size; module-aware sampling; the cap
  grows to 12/12 boundaries where the old cap gives 4.
- **Areas:** a finding is filed under its module rather than an invented code.
- **E1–E5:** the full chain run deterministically. An invalid proposal id is discarded, an invented
  title is dropped, an orphan is swept in to reach 100% coverage, criteria are applied, and an
  area-named outcome is flagged. With the flag off, no extra work items are produced.

**Existing tests updated deliberately:**

| Test | Change |
|---|---|
| `test_consolidate.py` | The "submit/lodge stay apart" trade-off is replaced by "synonyms merge" and "different operations on the same code stay apart" |
| `test_resolution.py` | The "clean set" fixture gets distinct spans and titles; its old children would now, correctly, be reported as near-duplicates |
| `test_e2e.py` | The stub model answers the three new schemas without changing any scripted outcome. The one-worker-per-boundary test pins `boundaries_per_packet=1` |
| `test_pack_compile.py` | Step list includes the new steps |

The scored harness (`test_harness_scored.py`) and the structure baseline pass unchanged.

---

## 9. Not done, and how to verify on the next run

**Deliberately not implemented** (optional in the plan):
- **Self-consistency on E2** (run the merge three times and keep outcomes that appear twice). At
  temperature 0 the samples would be near-identical; revisit if E2 proves unstable.
- **A model judge for flagged duplicate pairs.** The deterministic report comes first, so the judge
  can be sized against real counts.

**Live verification.** Run `8d93e96f` verified round 1; its findings are in §10. The paragraph
below is kept as it was written before that run.

**Not yet verified with a live model.** Nothing here has made a real Azure call. Everything above was
checked deterministically: the test suite, the planner run on the stored graph, and the new rules
applied to the stored requirements. To verify, re-run on `neuleap_talent_scooper_v1.6.4` and check:

1. `artifacts/llm_usage.json`: `analyse_module.calls` should be 21, and the total should be about
   $1 at gpt-4o-mini rates.
2. `cluster_plan.json`: `boundary_coverage_percent` should be 100.
3. `requirement_analysis.md` → Structural findings: the `DUPLICATE_*` / `NEAR_DUPLICATE_*` counts.
4. `requirements.md` §4 Scope: the number of areas should match the modules, plus any declaration or
   residual areas.
5. Step metrics for `collect_orphan_bindings`: `binding_coverage_percent`, and the number of business
   objectives in §3.
6. The gap analysis: the excluded developer-tooling files are listed.

**Stale documentation.** `claudeMD/Understandings.md` §4 ("5 model calls") and its `elevate` section
describe the old single-call top tier. It has uncommitted edits of yours, so it was left untouched.

---

## 10. Round 2: what the first live run showed and what was fixed

Run `8d93e96f` confirmed the round-1 fixes:

| Measurement | Result |
|---|---|
| `analyse_module` calls | 21 |
| Boundaries covered | 338 of 338 |
| Areas | 18, named after the modules |
| Coding-agent guides excluded | 17 files |
| Model calls, whole run | 275 |
| Cost | $0.73 |

It also showed a thin top tier: **2 business objectives, with 5 of 95 capabilities linked to them.**
The app rendered the run without errors (checked headlessly with Streamlit's test harness), so the
output itself was wrong. There were two causes.

### 10.1 Outcomes were rejected against borrowed code

**Problem.** An outcome has no code of its own. `ground_outcomes` lends it a sample of its
capabilities' citations (one each, at most six), so that it can be traced. The correspondence
check then read the outcome's sentence against those six lines as if they were its evidence. The
entailment model answered, reasonably, that "the evidence does not show candidates booking interview
slots", and rejected 4 of the 6 outcomes. A rejected outcome leaves the document and every
capability under it loses its parent, which is how 5 of 95 remained linked.

**Change.**
- [correspondence.py](../src/agentic/capabilities/correspondence.py) no longer checks business
  requirements against code. After every capability has been judged,
  `_judge_outcomes_by_capabilities` marks an outcome `supported` when at least
  `OUTCOME_MIN_SUPPORTED` (2) of its capabilities passed. Otherwise it is marked `unchecked`, never
  `rejected`.
- [resolution.py](../src/agentic/capabilities/resolution.py) then applies its existing rule: an
  outcome with fewer than two capabilities is republished as a capability. It now counts only
  capabilities that were **not** rejected, because a rejected capability is about to leave the
  document and cannot hold an outcome up.

**Why it's better.** An outcome's warrant is its capabilities, which is what `ground_outcomes`
already said. The check now matches that, so a sound outcome can no longer take its capabilities'
parent links down with it.

### 10.2 The merge used 6 of 80 proposals

**Problem.** E1 produced 80 candidate outcomes that together bound 206 of 312 capabilities by id.
The merge call (gpt-4o-mini) wrote 6 outcomes and cited only 6 proposals, although it was told to
place every one. The other 74 proposals were dropped, and their capabilities went to the orphan
sweep one at a time, stripped of the grouping and purpose E1 had given them. The sweep placed
almost none, leaving 59 of 312 capabilities bound.

**Change.**
- [elevation.py](../src/agentic/capabilities/elevation.py) `compose_outcomes`: a proposal the merge
  left out goes to the sweep **as a whole unit**, with its title, statement, capability count and a
  sample of its capability titles. Only capabilities that belong to no proposal are swept singly.
  New metrics: `proposals_total`, `proposals_merged`, `proposals_unmerged`.
- `collect_orphan_bindings`: an answer for a proposal id (`P..`) moves every still-unbound
  capability of that proposal to the chosen outcome (`orphan_proposals_placed`).
- The merge prompt now receives `every_proposal_id` as an explicit checklist, and its instructions
  say that every id must appear under exactly one outcome, aiming for four to ten outcomes.
- The `bind_orphans` instructions and the `CapabilityAssignment` schema accept P ids as well as
  C ids.

**Why it's better.** A skipped proposal is recovered by one small decision instead of lost. The 74
proposals from that run become 74 sweep answers, one per proposal, instead of roughly 150 separate
capability judgements. The sweep also shrinks overall, from 300 units to about 180. The checklist should also reduce how many are skipped in the first
place.

**Round-2 tests** (in `test_new_fixes.py`):
- an outcome is never rejected against borrowed code;
- an outcome is supported only by capabilities that passed;
- an unmerged proposal is swept whole, and its capabilities follow the answer;
- the merge receives the full proposal checklist.

**On the next run, check:**
- `compose_outcomes` metrics: `proposals_merged` against `proposals_total`;
- `collect_orphan_bindings` metrics: `binding_coverage_percent`;
- `check_correspondence` metrics: `outcomes_supported_by_capabilities`;
- the business objectives in `requirements.md` §3.

---

## 11. Run cost: how it is measured and how far to trust it

### What is measured

Every node runs inside a usage meter, [llm/usage.py](../src/agentic/llm/usage.py) `UsageMeter`. It
is a LangChain callback handler registered through a configure hook, so every model call made
inside the node reaches it, including calls in nested chains and in LangGraph's worker threads. For
each model response it records:

- **calls**: one per billed response (`on_llm_end`). A step record is not a call, because retries,
  a length-limit retry and the correspondence check's batched entailment all sit inside one
  record. Round 1 counted records; round 2 counts responses.
- **tokens**: input, output and cached input, taken from the provider's own usage report on each
  response. They are not estimated from characters.
- **model**: usage is kept per model name (`usage_by_model` on each step record), so a step routed
  to another deployment with `STEP_DEPLOYMENTS` is priced at its own rate.
- **failed calls**: counted separately (`on_llm_error`).

When the run finishes or halts, it writes `artifacts/llm_usage.json` with totals, per-step rows and
per-model rows.

### How it is priced

Each model name is matched to a rate by its longest prefix, so `gpt-4o-mini-2024-07-18` is priced as
`gpt-4o-mini` and not as `gpt-4o`. The built-in list prices are Azure OpenAI Global deployment
rates, in USD per million tokens:

| Model prefix | Input | Cached input | Output |
|---|---:|---:|---:|
| gpt-4o-mini | 0.15 | 0.075 | 0.60 |
| gpt-4o | 2.50 | 1.25 | 10.00 |
| gpt-4.1-nano | 0.10 | 0.025 | 0.40 |
| gpt-4.1-mini | 0.40 | 0.10 | 1.60 |
| gpt-4.1 | 2.00 | 0.50 | 8.00 |

Override them with `LLM_RATES` in `.env`. A model no prefix matches uses the fallback rates and is
labelled "fallback rate" in the summary rather than priced silently. The formula:

```
(input − cached) × input rate + cached × cached rate + output × output rate
```

### The app

The Run tab now opens with **Model usage and cost**: estimated cost, model calls, input tokens and
cached input tokens, then a per-step table (calls, failed calls, tokens, estimated USD) and the rate
used for each model. It reads the `llm_usage.json` the run wrote. A run from before metering existed
shows "Model usage was not recorded for this run."

### Measured cost of run `8d93e96f`

Prices are gpt-4o-mini list rates. The panel reports this run as **$0.7297**, and it checks by hand:
2.18M uncached input × 0.15 + 3.55M cached × 0.075 + 0.23M output × 0.60, per million tokens.

| Step | Calls | Input tokens | of which cached | Output tokens | Est. USD |
|---|---:|---:|---:|---:|---:|
| classify_boundaries | 17 | 310,569 | 7,168 | 68,004 | 0.0869 |
| synthesize | 171 | 4,389,999 | 2,850,560 | 127,863 | 0.5214 |
| analyse_module | 21 | 463,014 | 252,416 | 12,366 | 0.0579 |
| propose_outcomes (E1) | 47 | 276,635 | 239,616 | 4,968 | 0.0265 |
| elevate (E2) | 1 | 28,489 | 8,960 | 1,782 | 0.0047 |
| bind_orphans (E4) | 8 | 56,396 | 41,216 | 3,995 | 0.0078 |
| review_outcomes (E5) | 6 | 33,720 | 29,696 | 697 | 0.0032 |
| check_correspondence | 1 | 47,101 | 0 | 4,150 | 0.0096 |
| repair | 3 | 119,917 | 119,424 | 4,510 | 0.0117 |
| **Total** | **275** | **5,725,840** | **3,549,056** | **228,335** | **0.7297** |

Two things stand out:
- **Synthesis is 71% of the cost,** and the rest of the pipeline together costs about $0.21.
- **62% of all input tokens were served from the provider's prompt cache,** at half price. Every
  fan-out worker of one step sends the same system prompt, so the cache matters. It is also why
  the round-1 estimate ($1) came out higher than the measured $0.73.

For comparison, the run before the fixes (`c488c497`) was not metered. Its ≈ $11 is a
reconstruction from its artifacts, with about ±20% uncertainty.

### How far to trust the number

- **Tokens and calls are the provider's own figures.** For run `8d93e96f` the call counts are
  per-record, from the round-1 meter. Runs from now on count individual responses.
- **The price is only as good as the rates.** The defaults are Global list prices. Data Zone or
  Regional deployments cost about 10% more, and negotiated agreements differ. Put your actual rates
  in `LLM_RATES` and the estimate matches your contract. The Azure invoice for the deployment
  remains the authority.
- **A response cut off at the length limit** is billed but raises before its usage reaches the
  caller. It is counted under `failed_calls` and not priced, so a run with many length failures is
  slightly under-estimated. The column makes that visible.
- **Round-2 cost tests** (in `test_new_fixes.py`):
  - calls and tokens counted per model across a mixed gpt-4o-mini/gpt-4o workload;
  - a failing call is counted;
  - each model priced at its own rate;
  - configured rates override the defaults;
  - metering works inside a real compiled LangGraph run and writes `llm_usage.json`.

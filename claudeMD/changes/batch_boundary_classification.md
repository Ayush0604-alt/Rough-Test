# Change log — Batch boundary classification instead of truncating it

Implements the fix for [flow.md §10 B3](../flow.md#b3-the-boundary-classifier-is-a-single-point-of-failure-and-the-main-source-of-instability-partially-fixed-2026-09-18)
and [improvements.md §2.3](../improvements.md#23--resolved-2026-09-18-the-boundary-candidate-cap-had-the-categorical-truncation-bug-that-_stratified-already-fixed).
Full design writeup of the affected module: [capabilities/boundary_candidates.md](../capabilities/boundary_candidates.md).

**One line:** `propose_boundary_candidates` used to rank every interaction-boundary candidate and
silently **discard** everything past `MAX_CANDIDATES = 120` — on the largest run in the stored
evidence (`neuleap`, 381 files), that meant 1,868 of 1,988 candidates (94%) were never shown to the
classifier at all, no matter how many of them were real endpoints. This change stops discarding
candidates: the ranked list is now **chunked into batches** of up to 120
(`MAX_CANDIDATES_PER_BATCH`), and `classify_boundaries` **fans out** one LLM call per batch — the
same mechanism `synthesize` already uses per cluster — instead of one call over a truncated list. A
new deterministic step, `collect_boundary_assignments`, reduces every batch's reply back into the
exact `boundary_assignments` shape `discover_entry_points` already consumed, so nothing downstream
of classification changed at all.

**Status:** Implemented and tested on branch `1/Add-semantics-to-graph`. Not yet committed — all
changes below are in the working tree, alongside unrelated in-progress work on this branch (graph
semantics from docstrings — see [add_semantics_to_graph.md](add_semantics_to_graph.md)). Full test
suite: **703 passed, 1 failed** — the one failure
(`test_entry_point_adapters.py::test_registered_handlers_become_entry_points`) is pre-existing and
unrelated to this change, confirmed by stashing every file this change touches and rerunning that
one test against the unmodified baseline, where it fails identically (see `flow.md` §14).

---

## Why batching, not a bigger cap or `_stratified` sampling

Two other fixes were on the table and rejected:

- **Raise the cap.** `MAX_CANDIDATES` was already sized against the *reply's* output ceiling, not
  the *prompt's* — "each assignment is a few short fields" is what overruns a large repository's
  reply, not the input JSON. Raising the cap just moves the cliff edge and risks the same
  length-limit failure `SHORTEN_INSTRUCTION` in `llm/steps.py` exists to recover from, at a larger
  scale.
- **Reuse `_stratified` sampling** (as `improvements.md` §2.3 originally proposed) to choose a
  *better* 120 to keep. This fixes the categorical skew (147 state-changing endpoints sorting after
  reads, the same failure `_stratified` was written for in `graph_ops.py`) but still throws away
  real candidates on a large repository — just a better-chosen 120 instead of an alphabetically
  arbitrary one.

Batching is a strictly stronger fix: every candidate is classified, in some batch, so the
categorical-skew failure mode (a whole class of candidate losing every tiebreak and never being
asked about) cannot happen at all — not "happens less," but structurally cannot, because nothing is
selected out. The existing `synthesize`/`clusters` fan-out was already proof this pattern works at
this pipeline's scale.

---

## Files changed

| File | Kind of change | Risk |
|---|---|---|
| `src/agentic/capabilities/boundaries.py` | +94/−? — truncation replaced with batching; new `collect_boundary_assignments` capability | Low–medium (core logic change, thoroughly tested) |
| `codebase-requirement-skills/pack.yaml` | `classify_boundaries` step reshaped to `fan_out`; new `collect_boundary_assignments` step inserted | Low–medium (manifest topology change) |
| `src/agentic/graph/nodes.py` | 1 conditional generalized: fan-out fragments route to the channel named by `produces`, not a hardcoded `"fragments"` | Low (backward-compatible; `synthesize`'s `produces: [fragments]` behaves identically) |
| `src/agentic/state.py` | +1 new state channel, `boundary_fragments` | Low (additive) |
| `src/agentic/skills/pack.py` | Docstring-only update to `StepSpec.fan_out` | None |
| `src/agentic/llm/prompt.py` | New `_WORK_ITEM_RENDERERS` registry (mirrors the existing `_RENDERERS` pattern) for fan-out work-item rendering; old `boundary_candidates` whole-artifact renderer removed (now dead — nothing consumes it) | Low–medium (touches shared prompt-building code, but backward-compatible by design — see below) |
| `src/agentic/capabilities/entry_points.py` | Docstring-only update, 1 sentence | None |
| `tests/conftest.py` | `EXTRACTION_STEPS` gains the new step; `run_step_or_stub` simulates the fan-out for `classify_boundaries` | Test-only |
| `tests/test_boundary_classification.py` | Truncation tests replaced with batching tests; new tests for `collect_boundary_assignments` | Test-only |
| `tests/test_e2e.py` | `StubModel`'s `BoundaryAssignments` branch reads the new payload shape; `completed_steps` expectation updated; new fan-out-count test | Test-only |
| `tests/test_entry_point_adapters.py` | Local step list gains the new step | Test-only |
| `tests/test_pack_compile.py` | Expected step-id list gains the new step | Test-only |

---

## 1. `src/agentic/capabilities/boundaries.py`

### `MAX_CANDIDATES` → `MAX_CANDIDATES_PER_BATCH`
Same name-adjacent constant, same value (120), different meaning: a batch size instead of a
truncation cap. The comment at its definition explains the change and why the value didn't need to
move — it was always sized against the model's *output*, and a batch's output is exactly what the
old single (truncated) call's output was.

### `propose_boundary_candidates` — tail rewritten
The candidate-building loop and the ranking `sort()` are **unchanged**. What follows the sort no
longer truncates:

```python
batch_count = -(-len(candidates) // MAX_CANDIDATES_PER_BATCH) if candidates else 0
batches = [
    {"batch_id": i + 1, "batch_count": batch_count, "considered": len(candidates),
     "candidates": candidates[i * MAX_CANDIDATES_PER_BATCH : (i + 1) * MAX_CANDIDATES_PER_BATCH]}
    for i in range(batch_count)
]
```

Two artifacts are now produced instead of one:
- `boundary_candidates` — the full, **untruncated** `{candidates, considered}`, kept because
  `discover_entry_points` still reads it (unchanged) to attach registrar evidence back onto each
  classification.
- `boundary_candidate_batches` — a **plain top-level list**, one dict per batch, the shape
  `classify_boundaries`'s `fan_out` dispatches over (matching how `clusters` is a plain list for
  `synthesize`'s fan-out).

An empty candidate list produces `batch_count = 0` and an empty `boundary_candidate_batches`, which
the existing fan-out router (`graph/router.py`) already knows to skip past without dispatching any
worker — this is the same code path a repository with zero clusters already exercised, not a new one.

### New capability: `collect_boundary_assignments`
The reduce step. Reads the `boundary_fragments` state channel (see `state.py` below), concatenates
every batch's `assignments` list, and reports (but does not raise on) any fragment whose shape
doesn't parse — "dropped rather than guessed at," consistent with every other failure mode in this
module. Its output artifact, `{"assignments": [...]}`, is byte-for-byte the same shape
`classify_boundaries` used to produce directly, which is what makes this change invisible to
`discover_entry_points` and everything after it.

---

## 2. `codebase-requirement-skills/pack.yaml`

`classify_boundaries` changed from a single `consumes: [boundary_candidates]` LLM call to
`fan_out: boundary_candidate_batches`, `produces: [boundary_fragments]`. Its `instructions:` prose is
**unchanged** — "Classify each candidate below" reads correctly whether "below" is the whole
repository or one batch of it.

A new step, `collect_boundary_assignments`, was inserted immediately after it
(`consumes: [boundary_fragments]`, `produces: [boundary_assignments]`, `on_fail:
warn_and_continue`). `discover_entry_points`'s own step definition is **unchanged** — it still
`consumes: [inventory, graph, boundary_assignments]`.

**If `main` has also touched the extraction phase of this manifest** (Phase 1c/1d, lines roughly
55–98 before this change), this is the block to reconcile by hand — the step reordering isn't a
simple line insertion, it's `classify_boundaries` changing shape plus one new step.

---

## 3. `src/agentic/graph/nodes.py` — the one shared-code generalization

`_apply()`'s fan-out fragment routing was hardcoded to the `"fragments"` state key:

```diff
  if result.fragment is not None:
-     update["fragments"] = [result.fragment]
+     channel = step.produces[0] if step.produces else "fragments"
+     update[channel] = [result.fragment]
```

This is the one change in this diff that touches code shared with `synthesize` (this pack) and
`design_tests` (the test-generation pack's own fan-out step). It is backward-compatible by
construction: both of those declare `produces: [fragments]`, so `channel` resolves to `"fragments"`
for them exactly as before. Only `classify_boundaries`'s new `produces: [boundary_fragments]` takes
the new path. Full test suite (both packs) passes.

**Why a second channel, not the shared `fragments` channel:** `assemble_requirements` reads
`state["fragments"]` in its entirety and treats every entry as a `BRDFragment`
(`agentic.schemas.brd`). Every field on `BRDFragment` has a default, so a `BoundaryAssignments`
fragment landing in that channel would not fail validation — the fields simply wouldn't overlap, and
`model_validate({})` succeeds — it would silently become a spuriously empty `BRDFragment`, inflating
`assemble_requirements`'s `fragments_received` metric and its "N replies could not be read" counting
logic for a phase that has nothing to do with boundary classification. A dedicated channel
(`AgentState.boundary_fragments`) avoids this category of bug entirely rather than relying on
permissive-validation-happens-to-filter-it-out.

---

## 4. `src/agentic/state.py`

One additive field, mirroring `fragments`:

```python
boundary_fragments: Annotated[list[dict[str, Any]], operator.add]
```

Initialized to `[]` in `initial_state()`, same as `fragments`.

---

## 5. `src/agentic/llm/prompt.py`

### Removed: `_render_boundary_candidates` (the `@renders("boundary_candidates")` whole-artifact renderer)
Dead code after this change — nothing declares `consumes: [boundary_candidates]` any more, and
`render_artifact()` is only ever called from the `consumes` loop in `build_user_payload`. Its
"note" text (explaining `inbound_calls` and `references_undefined_names` to the model) was carried
forward into the new work-item renderer below, not lost.

### New: `_WORK_ITEM_RENDERERS` registry
`build_user_payload`'s work-item handling was hardcoded to always build a `"cluster"`-shaped payload
(`boundary`, `boundary_kind`, `boundary_location`, `also_exposed_by`, `evidence`) regardless of which
fan-out step was running. This was fine as long as every fan-out work item was *shaped* like a
cluster — which, before this change, both fan-out consumers in this codebase were: `clusters`
natively, and the test-generation pack's `test_batches` **deliberately mimics the cluster shape**
(`batching.py`'s `build_test_batches` sets `boundary`/`boundary_kind`/`packet` on each batch item
specifically so this renderer works unmodified for it too — see the comment there).

A `boundary_candidate_batches` work item (a batch of raw candidates) doesn't fit that shape
honestly, so rather than stretch it to fit (labelling a batch of 120 candidates a "boundary" would
be misleading to a reader debugging the run), `build_user_payload` now dispatches through a small
registry keyed by `ctx.step.fan_out`:

```python
renderer = _WORK_ITEM_RENDERERS.get(ctx.step.fan_out or "")
if renderer is not None:
    payload.update(renderer(work_item))
else:
    payload["cluster"] = { ... unchanged ... }
```

**Nothing is registered for `"clusters"` or `"test_batches"`** — both fall through to the unchanged
`else` branch, so their rendered payload is byte-for-byte identical to before. Only
`"boundary_candidate_batches"` has an entry, rendering `payload["candidates"]` (the batch's raw list)
and `payload["batch"]` (`batch_id`/`batch_count`/`considered`) instead of a fabricated "cluster."

---

## 6. Tests

All test changes are either (a) adding the new `collect_boundary_assignments` step to hardcoded
step-sequence lists that three different test files each maintain their own copy of
(`tests/conftest.py`'s `EXTRACTION_STEPS`, `tests/test_entry_point_adapters.py`'s local tuple,
`tests/test_pack_compile.py`'s expected id list), or (b) covering the new behaviour directly.

**A gap worth flagging for a future change:** the extraction-phase step sequence is duplicated in
three test files rather than derived from the manifest once. This diff touched all three by hand;
the next step added to this phase will have to as well, or one of these tests will silently stop
covering the true pipeline (see [`test_pack_compile.py`](../../tests/test_pack_compile.py)'s own
`test_pack_loads_and_resolves_every_reference`, which is the only one of the three actually generated
from `manifest.steps` — the other two are hand-maintained tuples).

`tests/conftest.py::run_step_or_stub` deserves a specific note: it runs one manifest step at a time,
standing in for LangGraph's real `Send`-based parallel dispatch. Rather than teach it to fan out for
real, the stub classifies every batch in a loop and appends each reply to
`ctx.state["boundary_fragments"]` directly — mutating the shared `state` dict in place, which works
because `CapabilityContext.state` is the same dict object across every step in one test's call
sequence (`_run`/`run` closures reuse one `state`). The next step,
`collect_boundary_assignments`, is then invoked exactly as production would (via
`get_capability(spec.capability)`), reducing that channel for real — so the reduce logic itself is
never stubbed, only the parallel dispatch.

`tests/test_e2e.py::test_boundary_classification_runs_one_worker_per_candidate_batch` is new,
mirroring the existing `test_fan_out_runs_one_worker_per_interaction_boundary` for `synthesize`: it
asserts the batch count, the `BoundaryAssignments` call count, and the reduced `boundary_assignments`
count all agree with each other — the property that would break first if the fan-out and the reduce
step ever fell out of step with one another.

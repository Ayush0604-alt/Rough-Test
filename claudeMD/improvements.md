# Improvements — making requirement and test generation robust

Concrete, prioritised changes to the existing codebase. Every item is grounded in a defect I could
observe in the source or measure in the stored runs under `runs/` — not in general best practice.

The headline: **the pipeline is architecturally sound and is currently producing a fraction of what
it is designed to produce.** Four of the seven requirement tiers and all four supplementary families
have produced *zero* items across every stored run, and the cause is a broken contract between
extraction and everything downstream — not a flaw in the design.

---

## Contents

- [What the stored runs actually show](#what-the-stored-runs-actually-show)
- [Tier 0 — the extraction contract is broken](#tier-0--the-extraction-contract-is-broken) (fix first)
- [Tier 1 — scoring and tiering that depend on Tier 0](#tier-1--scoring-and-tiering-that-depend-on-tier-0)
- [Tier 2 — coverage: find more of what is there](#tier-2--coverage-find-more-of-what-is-there)
- [Tier 3 — precision: fewer wrong and duplicated requirements](#tier-3--precision-fewer-wrong-and-duplicated-requirements)
- [Tier 4 — test generation](#tier-4--test-generation)
- [Tier 5 — feedback loops and measurement](#tier-5--feedback-loops-and-measurement)
- [Housekeeping](#housekeeping)
- [Suggested sequence](#suggested-sequence)
- [What I would not change](#what-i-would-not-change)

---

## What the stored runs actually show

Measured across the 11 completed runs in `runs/*/artifacts/brd.json`:

| Signal | Measured | Expected by design |
|---|---|---|
| `business_rules` published | **0 in every run** | a handful per repository |
| `success_criteria` section | **0 in every run** | observable conditions per requirement |
| `data_requirements` / `integration_requirements` / `non_functional_requirements` / `reporting_requirements` | **0 in every run** | present wherever a schema, an external call or a limit exists |
| `observations` | **0 in every run** | the traceability appendix the skills describe at length |
| abstraction bases present | `external_capability` 95%, `business_outcome` 5%, `internal_behaviour` ~1% | seven bases in use |
| `domain_constraint` candidates produced by workers | yes (1–2 per run) | — |
| …of those, surviving as rules | **0 of them, 100% demoted** | most should survive |
| confidence range | **min = max = 0.95** in most runs | a spread, with a review list below 0.70 |
| items carrying a resolved parent | 3 of 39, 0 of 21, 0 of 19, 0 of 84 | most capabilities bound to an outcome |

So the document this system currently produces is a **flat, single-tier list of functional
requirements with uniform confidence and almost no hierarchy** — which is precisely the "behaviour
inventory" the consolidation stage was built to prevent, arriving by a different route than the one
the code comments anticipated.

*(Caveat: some runs may predate recent changes. But 11 of 11 showing zero rules, and the code-level
cause below being unconditional, makes this a live defect rather than an artefact of old output.)*

---

## Tier 0 — the extraction contract is broken

**This is the root cause of most of the table above. Fix it before anything else; several later
items are worthless until it is done.**

### 0.1 — Nothing produces the declaration entity kinds that half the pipeline consumes

**Evidence.** Grepping `src/` for who *creates* entities with these kinds returns nothing — every
occurrence is a consumer (a set-membership test, a render label, an attribute whitelist):

```
DATA_ENTITY   DATA_FIELD   DATA_INDEX   CONFIG_KEY   DOMAIN_POLICY
BUSINESS_RULE   ERROR_HANDLER   DATA_OPERATION
```

`capabilities/graphify.py` is now the only producer of entities, and `classify_kind()` can only ever
return `RATIONALE`, `EXTERNAL_SYMBOL`, `FILE`, `CLASS`, `METHOD`, `FUNCTION`, `SYMBOL`, plus
`MODULE_SURFACE` from the surface scanner. The boundary kinds come from `entry_points.py`. Nothing
else creates anything.

The attribute side is the same. The adapter sets exactly `graphify_id`, `origin`,
`line_end_estimated`, `content_outline`, and on surface nodes `call` / `literals` / `module_level`.
It never sets `rule_source`, `statement`, `expression`, `allowed_values`, `rationale`, `path`,
`method`, `label`, `table`, `constraint`.

**What that silently disables:**

| Consumer | Intended behaviour | Actual behaviour |
|---|---|---|
| `consolidate._domain_bearing()` | promote a claim to a business rule when something it cites was *declared* to be one | **can never return `True`** → 100% of rule candidates demoted |
| `graph_ops.declaration_groups()` | seed clusters from schema/policy files no call path reaches | returns `[]` → the separate quarter-budget does nothing |
| `graph_ops.declared_policy()` | add module-level configuration to every packet | returns `[]` |
| `gates.py` `RULE_WITHOUT_POLICY` | reject a rule tracing to no policy node | unreachable — no rule ever gets that far |
| `claims._tier_from_the_graph()` | let the graph override a mis-typed rule | never fires |
| `confidence.DECLARED_POLICY` (+0.05) | reward declared evidence | never applies |
| `hedging._criterion_from()` declaration branch | derive a real criterion from a declared condition | never fires → falls through to boilerplate |
| `leakage.survives_redesign()` | keep internal behaviour that a declaration depends on | always `False` for that branch |
| `graph_ops.compact_entity` keep-lists for `BUSINESS_RULE`, `CONFIG_KEY`, `DATA_ENTITY`, `DATA_FIELD`, `DATA_INDEX`, `ENDPOINT`, `MODULE` | put policy evidence in the packet | dead code — the model never sees this evidence |

The measured consequence is the top row of the table above: workers *do* produce `domain_constraint`
candidates, and `_classify_rules` demotes every single one, in every run.

**Why it matters most.** A requirements document with no business rules, no data invariants and no
integrations is not a thinner version of the right document — it is a different and much less useful
artefact. It also cascades into PACK 2: a demoted rule becomes tier `system`, which routes to
`DEFERRED` with a 2-case cap, so the highest-risk test dimension (`business_rule`) loses its
strongest input.

**What to change.** Add one new deterministic capability — `enrich_declarations` — running between
`adapt_graph` and `propose_boundaries`, producing the missing kinds from evidence Graphify already
gives you, plus source the adapter already reads:

1. **SQL → `DATA_ENTITY` / `DATA_FIELD` / `DATA_INDEX`.** The `graphifyy[sql]` extra is already a
   hard dependency (`pyproject.toml` says it "is not optional in practice"). Map its `.sql` nodes to
   the declaration kinds with `table`, `not_null`, `default`, `allowed_values`, `unique` attributes.
   This alone resurrects `declaration_groups()`, which the comments say was written because "444
   entities were extracted from a schema and not one requirement cited it".
2. **Module-level constants → `CONFIG_KEY`.** The surface scanner already identifies ALL-CAPS
   module-level assignments and *excludes* them (`declares_constant`). Emit them as `CONFIG_KEY`
   with `value` and `secret_like` instead of discarding them, and add a `DECLARES_CONFIG` edge from
   the containing file. That resurrects `declared_policy()`.
3. **Prose → `DOMAIN_POLICY`.** `RATIONALE` nodes and `content_outline` lines already carry declared
   policy in the project's own words. Promote a `RATIONALE` whose text states a condition (a modal
   plus a constraint) to `DOMAIN_POLICY` with the sentence in `statement`.
4. **Raise/throw sites → `ERROR_HANDLER`.** Cheap to detect and it is what a negative test case needs.

**Effort:** M (2–4 days for all four; SQL alone is S and delivers most of the value).
**Impact:** Very high. Unblocks eight consumers and three requirement families.

### 0.2 — Add a kind/attribute contract test so this cannot happen again

**Evidence.** The defect above survived a full extractor swap and ~40 test modules because every
consumer degrades silently: an empty set membership test just returns `False`.

**What to change.** One test that inverts the relationship:

```python
# tests/test_kind_contract.py
CONSUMED_KINDS = collect_from(BOUNDARY_KINDS, DECLARATION_KINDS, gates, render, hedging, ...)
PRODUCED_KINDS = kinds_produced_over(corpus_fixtures())     # run the real adapters
UNPRODUCED = CONSUMED_KINDS - PRODUCED_KINDS
assert UNPRODUCED <= KNOWN_NOT_YET_PRODUCED    # an explicit, reviewed allow-list
```

Same shape for the attributes in `DOMAIN_SIGNAL_ATTRIBUTES`, `MECHANISM_ATTRIBUTES`,
`OBSERVABLE_ATTRIBUTES` and `compact_entity`'s keep-lists. The allow-list makes an intentional gap
visible in a diff instead of invisible in production.

**Effort:** S. **Impact:** High — it is the structural fix, not the symptomatic one.

### 0.3 — Make a 100% demotion rate a loud warning, not a silent one

**Evidence.** `_classify_rules` returns `(kept, demoted)` and reports both, but a run where
`kept == 0 and demoted > 0` is indistinguishable in the output from a repository that genuinely has
no declared policy.

**What to change.** In `consolidate_requirements`, when `rules_kept == 0` and `rules_demoted > 0`,
emit a message saying the evidence layer produced no declaration entities at all, and surface it in
`gap_analysis.md`. A pipeline that demotes everything is reporting on *itself*, not on the codebase.

**Effort:** XS. **Impact:** Medium — it is the tripwire that would have caught 0.1 on day one.

### 0.4 — Pin the extractor and test its output shape

**Evidence.** `graphify.py` is ~1000 lines reverse-engineered from "measured, graphifyy 0.9.52",
including a workaround for the Go grammar reporting `_callable: false`. The dependency is pinned as
a *range* (`>=0.9.52,<1`), and no test runs the real binary.

**What to change.** Pin exactly, and add one contract test that runs `graphify extract` over a small
fixture and asserts the node/edge shape the adapter depends on (`label`, `source_file`,
`source_location`, `_callable`, `relation` vocabulary). Mark it `@pytest.mark.integration` so it can
be skipped offline but runs in CI.

**Effort:** S. **Impact:** High — a silent shape change degrades every run with no error.

---

## Tier 1 — scoring and tiering that depend on Tier 0

### 1.1 — Confidence is saturated at the ceiling and carries no information

**Evidence.** Every stored run reports `confidence min = max = 0.95`. The arithmetic explains it
exactly:

```
BASE 0.35 + SUPPORTED 0.25 + ENTAILED 0.10 + NAMES_A_SYMBOL 0.10
          + TIGHT_SPAN 0.05 + PARSED_EVIDENCE 0.10  =  0.95  = CEILING
```

Any claim that passes correspondence, names a symbol, cites a tight span and rests on parsed code
hits the ceiling exactly — and after `prepare_claims._name_the_cited_node` upgrades symbols, nearly
every claim does. The review list (`< 0.70`) is therefore empty, and `confidence.py`'s own docstring
describes precisely this failure in the *model's* self-reported numbers: *"A number that is 1.0
whatever the evidence carries no information, and the review list built from it was therefore empty
of precisely the items most worth reviewing."* The recalibration reproduced it at 0.95.

**What to change.** Three options, in increasing order of effort:

- **Re-weight so the ceiling requires corroboration.** Drop `PARSED_EVIDENCE` and `NAMES_A_SYMBOL`
  to 0.05 each; the ceiling then needs multi-file corroboration or declared policy — both of which
  are genuinely discriminating. Ten minutes, and the distribution immediately spreads.
- **Add discriminating signals** that vary in practice: how many distinct entities the citation
  overlaps, whether the boundary it traces to was parsed or classified (0.55 vs 1.0 provenance),
  whether the requirement survived a merge, how far the trace is from a boundary.
- **Calibrate against the corpus.** Ground-truth a sample of the fixture corpus by hand once, then
  make the harness report confidence-vs-correctness so the weights can be argued with numbers.

**Effort:** XS / S / M. **Impact:** High — the review list is the mechanism by which a human finds
the claims worth checking, and it is currently empty.

### 1.2 — The outcome→capability hierarchy is almost entirely unresolved

**Evidence.** Items with a resolved `parent_id`: 3 of 39, 0 of 21, 0 of 19, 0 of 84. Yet
`assemble_requirements` binds by title and reports `bindings_unmatched` and `capabilities_unattached`
— so either `elevate` is returning no bindings, or its titles are not matching.

**What to change.** Instrument first, then fix:

1. Persist `top_tier.json` alongside `brd.json` (it is already produced) and check whether
   `bindings` is empty or whether `realised_by` titles fail `title_key()` matching.
2. If titles are drifting: match on `title_key` **plus** a fallback to best-overlap matching against
   the digest titles actually sent, and report the fallback count.
3. If `elevate` returns nothing: see 1.3.

**Effort:** S to diagnose. **Impact:** High — without the chain, the document is a flat list and
Gate 2's `FUNCTION_WITHOUT_BUSINESS_REQUIREMENT` warning fires on everything.

### 1.3 — `elevate` degrades on large digests, by its own admission

**Evidence.** The README states it plainly: *"under a large digest it reliably returns capability
titles back as outcomes… A large repository will often produce none, and say so."* The measured runs
bear it out — 1 outcome from 38 capabilities, 5 from 77, 0 from 19.

**What to change.** Make it a bounded map-reduce instead of one call:

```
per area-group (≤ ~8 areas per call)  ──►  candidate outcomes + bindings
                    │
       deterministic merge of outcomes by title_key + content overlap
                    │
       one final small call ONLY to name the product purpose
```

Each call sees a digest small enough to reason over, and the merge is deterministic — the same
pattern that fixed `reduce`. Keep the existing deterministic refusals (an outcome that names a
capability below it, or stands over fewer than two, is not published).

**Effort:** M. **Impact:** High — this is the tier the README calls "the weakest link".

---

## Tier 2 — coverage: find more of what is there

### 2.1 — Ingest declared interface specifications (OpenAPI / AsyncAPI / GraphQL)

**Evidence.** Boundaries are currently *inferred* from graph shape at `confidence 0.55`, and named
by whatever string literal sits within ±3 lines. Many services ship an authoritative description of
exactly this — `openapi.yaml`, `swagger.json`, `.graphql`, an AsyncAPI document — and nothing reads
them. `safe_file_outline` recognises README/Dockerfile/package.json and stops there.

**Why it matters more than anything else in Tier 2.** A declared spec converts the single weakest
link in the chain (inferred boundaries) into the strongest:

| | Inferred today | From a spec |
|---|---|---|
| identifier | `ENDPOINT app/orders.py::create_order` | `POST /orders` |
| confidence | 0.55, `INFERRED` | ~1.0, `EXTRACTED` |
| effect | rarely known | from the verb |
| parameters / responses | not in the packet | concrete → real acceptance criteria |
| error cases | invented by the model | declared status codes and schemas |

**What to change.** A new `@entry_point_adapter` — this is exactly the seam the registry exists for,
and registration extends `BOUNDARY_KINDS` automatically. Resolve each spec operation to its handler
where the graph can (by operationId or by path-literal match) and fall back to publishing the
boundary unattached, which is still better than not knowing it exists.

**Effort:** M. **Impact:** Very high for any API product.

### 2.2 — A JavaScript/TypeScript entry-point adapter

**Evidence.** The README's own limitations section: *"Other languages are inventoried only — notably
TypeScript/JavaScript, so a React front end contributes its files but not its routes or its declared
vocabularies."* For a product whose next step is UI test generation, that is the largest hole in the
system.

**What to change.** Structural detection only, no framework knowledge:

- **File-system routing** (Next.js `pages/`, `app/`; SvelteKit `routes/`; Remix) — a directory
  convention is a pure structural signal and needs no parsing at all.
- **Route tables** — an array or object literal of `{path, component}` shapes, which the existing
  "consumed call with a literal" heuristic already nearly detects.
- **Exported page/handler components** — a default export in a routed directory.

**Effort:** M. **Impact:** High, and it is the prerequisite for meaningful UI test cases.

### 2.3 — The boundary-candidate cap has the categorical-truncation bug that `_stratified` already fixed

**Evidence.** `boundaries.py` sorts candidates by
`(-len(references), -len(registrars), -outbound_calls, qualified_name)` and takes the first
`MAX_CANDIDATES = 120`. Where many candidates share a shape, the first three keys tie and the
ordering collapses to the **alphabetical tiebreak** — the exact condition that caused
`graph_ops._stratified` to be written, after 147 state-changing endpoints were dropped because they
sorted after the reads.

Worse, the reporting is asymmetric: `plan_clusters` reports every class **starved** and **thinned**;
`propose_boundary_candidates` reports only a boolean `truncated` and a total.

**What to change.** Reuse the stratifier. Group candidates by a cheap class key (entity kind + top
directory, or the module they sit in), allocate proportionally with a floor of one per class, and
report which classes were thinned — exactly as the cluster planner does.

**Effort:** S (the algorithm already exists). **Impact:** Medium–high on large repositories.

### 2.4 — Derive `graph_path` from evidence instead of deleting the claim

**Evidence.** `NO_GRAPH_PATH` and `UNRESOLVED_GRAPH_PATH` are **ERROR**s in Gate 2, so a claim with
perfectly good evidence is rejected — and then deleted by quarantine — purely because the model
omitted or mistyped a trace. Meanwhile `prepare_claims` already builds an `at_line` index mapping
`(file, line) → entities` and uses it to upgrade symbols.

**What to change.** In `prepare_claims`, when `graph_path` is empty or resolves to nothing, populate
it from the entities the citation actually overlaps, walking up to the nearest boundary. Record that
it was derived (so the confidence pass can weight it lower). This converts a class of *deletion*
into a class of *repair*, deterministically, with no model call.

**Effort:** S. **Impact:** High — it directly stops grounded claims being lost to a bookkeeping miss.

### 2.5 — Use declared documentation as a candidate source, not just for the purpose

**Evidence.** `content_outline` is capped at 4 files × 40 lines and reaches only the `elevate` step.
A README frequently states capabilities explicitly — and for a repository whose code is in a language
Graphify parses poorly, it may be the only evidence available.

**What to change.** A pass that extracts candidate claims from documentation, then **requires code
confirmation** before publishing — which is exactly what the synthesis skill's evidence-precedence
rule already says ("declared documentation states intent and may establish purpose, but only a
reachable code path confirms that a capability exists"). Unconfirmed candidates go to
`requirement_analysis.md` as open questions, which is honest and useful.

**Effort:** M. **Impact:** Medium, high for polyglot repositories.

---

## Tier 3 — precision: fewer wrong and duplicated requirements

### 3.1 — Embedding-assisted clustering to fix synonym blindness

**Evidence.** Admitted in the README: *"'Ask about a document' and 'inquire about a document' cite
the same line and are one capability; nothing here can see that."* The signature comparison is a
hand-rolled stemmer plus six separate English word lists across four modules (`STOP_WORDS`,
`_SUFFIXES`, `_ENABLERS`, `FRAME_WORDS`, `_HEADING_NOISE`, `_COMMON_WORDS`).

**What to change.** A **hybrid**, which keeps everything the current design is right about:

```
local sentence-embedding model  ──►  propose candidate pairs above a cosine threshold
                                          │
existing deterministic rules    ──►  confirm, classify the relation, and EXPLAIN it
```

Embeddings only widen the candidate set; the merge decision and its explanation stay deterministic,
so auditability is preserved. Run it offline (`sentence-transformers`, no API call), and gate it
behind one of the existing `.env` toggles so its effect can be measured on the corpus.

**Effort:** M. **Impact:** High on repositories with a rich vocabulary.

### 3.2 — Seed the area vocabulary instead of letting every worker invent one

**Evidence.** Each fan-out worker invents an area code independently, and `_reconcile_areas` folds
them afterwards — lexically. The docstring gives the real example: eleven ways of saying "candidate
management" under eleven codes. Folding catches same-meaning and narrowing headings but explicitly
*not* two unrelated names for one part of the product.

**What to change.** A cheap pre-pass (directory names, README headings, existing boundary labels)
that proposes a candidate area list, passed to every worker with "choose one of these, or propose a
new one and say why". Consistent areas also stabilise ids (`FR-INT-01`), which is what makes the
matrix readable and diffable.

**Effort:** S–M. **Impact:** Medium–high on document readability, which is the actual deliverable.

### 3.3 — The entailment pass fails open, silently

**Evidence.** `correspondence._entail()` ends with
`supported, reason = verdicts.get(position, (True, ""))`. If the model returns fewer verdicts than
items — which it will, since it is one batched call over every surviving claim with an 8k output
ceiling — every unanswered claim is silently marked **supported**, by an entailment pass that never
saw it.

**What to change.** Chunk the batch (the pattern already exists: `MAX_REPAIR_CASES = 12`), count
unanswered items, and record `checked_by: "deterministic"` rather than `"entailment"` for them so
the confidence pass does not award the `ENTAILED` bonus to a claim nothing read. Report the count.

**Effort:** S. **Impact:** Medium — it removes a false-confidence path and an unearned +0.10.

### 3.4 — Tighten the symbol-verification window

**Evidence.** `verify.SYMBOL_WINDOW = 25` means a symbol appearing anywhere within a 50-line span
satisfies the anti-fabrication check. In a long file, common identifiers will match by accident.

**What to change.** When the graph knows the entity's own span, require the symbol inside that span
and fall back to the window only when it does not. Report how often the fallback was used.

**Effort:** S. **Impact:** Medium — it strengthens the single most important check in the system.

---

## Tier 4 — test generation

### 4.1 — A targeted second design pass for `positive_only` requirements

**Evidence.** `analyse_test_coverage` singles out `positive_only` as the dangerous verdict — a
requirement stating a refusal, an error or a limit that got only a happy-path case, so it *reads* as
covered. Nothing acts on it: the verdict is reported and the run ends.

**What to change.** After `analyse_coverage`, route `positive_only` requirements into one more
bounded design call with a budget covering **only the missing dimensions** and an instruction naming
them. This is the cheapest large quality win in PACK 2 — it reuses the existing fan-out machinery and
targets exactly the cases most likely to catch a real defect.

**Effort:** S–M. **Impact:** High.

### 4.2 — Report coverage against *all* dimensions as an advisory

**Evidence.** Sufficiency is measured against the dimensions a requirement **activated**, which is
correct (otherwise the report demands an authorization test for a requirement that mentions no access
condition). But it is circular: if activation under-fires, coverage reports "sufficient" for a
requirement nobody tested properly, and there is no signal that activation was the problem.

**What to change.** Publish a second, advisory number — dimensions covered against the full
vocabulary — in `coverage_matrix.csv` only, clearly labelled as advisory and never gating. A
systematic gap between the two is how you discover a regex needs widening.

**Effort:** S. **Impact:** Medium — it makes the anti-explosion tuning self-monitoring.

### 4.3 — Concrete test data from declared schemas

**Evidence.** `TestInput.example` is populated "only where the requirement fixes one", and
`value_kind` carries the role (`valid` / `invalid` / `boundary` / `absent` / `malformed` /
`unauthorised`). With no schema in the graph, the designer has nothing to draw a concrete boundary
value from.

**What to change.** Once Tier 0.1 and 2.1 land, pass the relevant field constraints (type, length,
enum, nullability) into the batch packet for requirements whose evidence overlaps a `DATA_FIELD` or
a spec parameter. A `boundary` case can then say "a value one character over the declared 64" rather
than "an overly long value".

**Effort:** M, **and it depends on Tier 0/2.** **Impact:** High — it is what makes a specification
executable by an adapter later.

### 4.4 — Make the framework word list pack-configurable

**Evidence.** `FRAMEWORK_WORDS` includes `"java"`, `"python"`, `"javascript"`. Any product whose
*domain* is software — a CI tool, a package registry, a code-review product — will have legitimate
requirements naming them, and every derived case gets rejected, repaired four times, then pruned. The
code already records this happening once with `PYTHONUNBUFFERED`.

**What to change.** Move the lexicons (`FRAMEWORK_WORDS`, `FRAMEWORK_PHRASES`, and the jargon
module's three vocabularies) into loadable data with a per-pack or per-project override. Same fix
serves `capabilities/jargon.py`, which has the identical problem on the requirements side.

**Effort:** S. **Impact:** Medium, decisive for software-domain targets.

### 4.5 — Consume `automation_readiness`, or stop computing it

**Evidence.** `assemble.automation_readiness()` computes a four-way classification that nothing reads
— by explicit design ("no logic in this pack branches on any of them"). That is right *inside* the
pack, but nothing outside consumes it either.

**What to change.** Surface it in `test_plan.md` as a section — "what can be automated today, what
needs data, what needs an environment, what is manual only" — which is genuinely useful to a test
lead and costs a rendering change. The no-branching rule stays intact.

**Effort:** XS. **Impact:** Medium on the deliverable's usefulness.

---

## Tier 5 — feedback loops and measurement

### 5.1 — Extend the harness to measure *document* quality, not just extraction

**Evidence.** `tests/harness/metrics.py` measures structure (what the extractors found) and pipeline
(what the document made of it) — but every Tier 0 and Tier 1 defect above would have been caught by
four numbers nobody is tracking:

```
tier distribution           (rules ≠ 0, families ≠ 0)
rule survival rate          (kept / (kept + demoted))
confidence spread           (stdev, and the count below the review threshold)
parent resolution rate      (items with parent_id / items that should have one)
```

**What to change.** Add them to the frozen baseline. Any one of them going to zero should fail the
comparison loudly.

**Effort:** S. **Impact:** Very high — this is the change that makes all the others stick.

### 5.2 — A review ledger: capture the human verdict

**Evidence.** The Requirements tab lets a reviewer expand a claim and see the real source at the
cited lines — the single most convincing feature in the product. And when they conclude "this one is
wrong", nothing records it.

**What to change.** Two buttons per requirement (verified / disputed, with a note), persisted to
`runs/<id>/artifacts/review.json`. Then:

- feed verdicts into the confidence pass on re-run;
- accumulate disputed claims into a regression corpus — real examples of what this system gets wrong,
  which is the most valuable test data available and currently gets thrown away;
- report review coverage in the gap analysis ("12 of 84 requirements have been checked by a person").

**Effort:** M. **Impact:** High and compounding.

### 5.3 — Content-addressed ids so runs are actually diffable

**Evidence.** `assign_ids()` is positional within a section (`FR-<AREA>-01`, `02`, …). The README
claims this makes the matrix "diffable between runs" — but inserting one requirement renumbers every
subsequent id in that area, so a diff between two runs of a slightly-changed codebase is unreadable.

**What to change.** Keep the positional id as the *display* label, and add a stable
`key = sha1(area + title_key(title))[:8]` to `requirements.json` and the matrix. Diffing then works,
and a review verdict (5.2) can survive a re-run.

**Effort:** S. **Impact:** High for anyone using this iteratively rather than once.

### 5.4 — Cost preview and incremental re-run

**Evidence.** `plan_clusters` knows exactly how many model calls it is about to make and how many
characters each carries, and says nothing before spending. And every run starts from `ingest` — the
checkpointer is keyed on `run_id`, so re-running after a small code change re-extracts and re-analyses
everything.

**What to change.** Two independent items:

- **Preview:** after `plan_clusters`, show "63 model calls, ~1.9M characters, ~N tokens — continue?"
  in the UI and print it in the CLI. Ten lines.
- **Packet cache:** key each cluster's result on `sha256(packet)` in a store under `runs/.cache/`.
  On re-run, unchanged clusters are reused. Given that packets are content-derived and temperature is
  0, this is safe and makes quality iteration affordable — which is what actually unblocks everything
  else in this document.

**Effort:** XS / M. **Impact:** Medium / High.

---

## Housekeeping

- **`src/agentic/capabilities/playwright/` contains only a stale `__pycache__`** — the `.py` files
  were removed but the compiled bytecode remains, and `git status` shows `MDfiles/Pack3.md` deleted.
  Stale bytecode in a package directory can still satisfy an import on some Python configurations.
  Delete the directory.
- **README states both "200 tests" and "680 tests"** in the same file.
- **`runs/` has no retention policy** and each run holds a full copy of the analysed tree.
- **`Settings.runs_dir` is relative**, so output location depends on the working directory.
- **`runtime.DEFAULT_PACK_ROOT` is `Path(__file__).parents[2]`**, so an installed wheel finds no
  packs — worth knowing before anyone tries to ship this as a package.

---

## Suggested sequence

The ordering matters: several items are worthless before Tier 0, and 5.1 is what proves the rest
worked.

**Week 1 — stop the bleeding, and prove it.**
`0.3` (demotion tripwire) → `0.2` (kind contract test) → `5.1` (document-quality metrics) →
`1.1` (confidence re-weight, ten minutes) → `0.4` (pin + contract test).
*You will now be able to see every defect in this document as a number.*

**Week 2–3 — restore what is designed and disconnected.**
`0.1` (declaration enrichment, SQL first) → `2.4` (derive `graph_path`) → `1.2` (diagnose the
hierarchy) → `3.3` (entailment chunking).
*Expect business rules, data requirements and observations to appear for the first time.*

**Week 4–5 — coverage.**
`2.1` (OpenAPI adapter) → `2.3` (candidate stratification) → `2.2` (JS/TS adapter).
*This is where a UI-oriented product gets its real input.*

**Week 6 — the test side.**
`4.1` (`positive_only` second pass) → `4.4` (configurable lexicons) → `4.2` (advisory coverage) →
`4.5` (surface readiness).

**Ongoing — compounding.**
`5.2` (review ledger) → `5.3` (stable ids) → `5.4` (cache) → `1.3` (elevate map-reduce) →
`3.1` (embedding hybrid) → `3.2` (area seeding).

---

## What I would not change

Worth stating explicitly, because these look like obvious targets and are not:

- **Deterministic routing.** Do not put a model in the control loop, however convenient it looks.
- **The evidence-packet discipline.** Do not let source code into a prompt to "improve quality" — it
  removes the property that makes fabrication detectable.
- **Deterministic assembly.** Do not reintroduce a model merge, however much better the prose reads.
  The arithmetic has not changed.
- **The 0.95 confidence ceiling.** Re-weight the inputs (1.1); do not raise the ceiling.
- **"Nothing is deleted."** The partitioning into `requirement_analysis.md` is what makes aggressive
  checking safe.
- **Technology-independent test specs.** The moment a protocol verb is allowed into a case, the
  specification can only ever become one kind of test.
- **The `abstraction_basis` → `requirement_type` derivation.** It is the mechanism holding
  over-extraction back.

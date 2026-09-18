# Capabilities: What Each One Does, How It Works, and How to Make It Better

This document covers every deterministic **capability** in both skill packs, one by one. For each
capability it says what the capability is for, how the code actually does it, where it is weak,
and what to change. [flow.md](flow.md) covers the order of steps and the system-level defects.
This document goes one level down, into the code of each capability.

> **Scope:** `src/agentic/capabilities/**` (35 registered capabilities), the 6 LLM steps declared
> in the two `pack.yaml` files, and the stored runs under `runs/`.
>
> **Tags used below**
> - **[verified]**: confirmed against the artifacts of a stored run (the run id is given).
> - **[code]**: follows directly from reading the code, but was not reproduced in a run.
> - **[likely]**: an inference that needs a test to confirm.

---

## Contents

1. [What a capability is](#1-what-a-capability-is)
2. [Findings that cut across many capabilities](#2-findings-that-cut-across-many-capabilities)
3. [Pack 1: codebase-requirements (22 capabilities)](#3-pack-1--codebase-requirements)
4. [Pack 1: LLM steps (4)](#4-pack-1--llm-steps)
5. [Pack 2: testcase-generation (13 capabilities)](#5-pack-2--testcase-generation)
6. [Pack 2: LLM steps (2)](#6-pack-2--llm-steps)
7. [Shared helper modules](#7-shared-helper-modules)
8. [Improvement roadmap](#8-improvement-roadmap)
9. [Corrections to flow.md](#9-corrections-to-flowmd)
10. [How the evidence was gathered](#10-how-the-evidence-was-gathered)

---

## 1. What a capability is

A capability is a named, deterministic Python function that a `pack.yaml` step points to by
string. The graph never imports it directly. See
[registry.py](../src/agentic/capabilities/registry.py).

```python
@capability("ingest_repository")
def ingest_repository(ctx: CapabilityContext) -> CapabilityResult: ...
```

| Piece | What it gives the capability |
|---|---|
| `ctx.load(name, Model)` | Reads an artifact an earlier step produced, from disk and not from graph state |
| `ctx.source_root`, `ctx.run_dir` | Where the snapshot and the run live |
| `ctx.settings` | Feature flags and budgets, read from `config.py` |
| `ctx.skill` | The SKILL.md this step belongs to (its sha256 is recorded) |
| `ctx.payload["work_item"]` | The fan-out item, for fan-out workers only |

| A capability returns | Effect |
|---|---|
| `artifacts={name: obj}` | Saved as JSON; the path is recorded in `state.artifacts` |
| `files={name: path}` | Deliverable files |
| `gate=GateReport` | Only in `mode: gate`; routes to pass, repair, or halt |
| `fragment={...}` | Only for fan-out workers; appended to `state.fragments` |
| `halt_reason="..."` | Stops the run cleanly with an explanation |
| `metrics`, `messages` | Shown in the UI and step records |

**Modules are registered lazily.** `load_builtin_capabilities()` imports every module once. The
import is also what registers the entry-point adapters, which extend `BOUNDARY_KINDS` (see §9).

### What could be better about the contract itself

1. **Declare hidden model calls.** `validate_correspondence` calls Azure from inside a
   `deterministic` step. Add `@capability("x", uses_llm=True)` so that `preflight()` and
   `agentic show` can see it.
2. **Declare which settings flag disables a capability.** Seven capabilities start with
   `if not getattr(ctx.settings, "<flag>", True): return passthrough`. A `disabled_by="flag"`
   argument on the decorator would make the pass-through generic and list every switch in one
   place.
3. **Pass structured facts, not English text.** Several capabilities talk to later ones by
   searching for sentences, which breaks as soon as someone rewords a message. See §2.6.
4. **Record per-capability timing and input and output sizes as metrics.** Step records carry a
   duration, but nothing records how many items went in and came out, and that is the number
   that shows where requirements get lost.

---

## 2. Findings that cut across many capabilities

These are new: none of them is in [flow.md](flow.md). They are ordered by how much they limit
output quality.

### 2.1 The model writes requirements from names and edges only: packets carry no code [verified]

`build_boundary_clusters` builds each evidence packet with `compact_entity()`
([graph_ops.py:284](../src/agentic/capabilities/graph_ops.py#L284)). That function keeps a
hand-picked set of attributes per entity kind (`docstring`, `parameters`, `predicate`, `label`,
`triggered_outcomes`, …). Almost none of those attributes exist any more:

- The Graphify adapter sets only `graphify_id`, `origin`, `line_end_estimated` and
  `content_outline` (for files).
- `MODULE_SURFACE` is **not in the keep-list at all**. So the `call` and `literals` attributes
  of a Streamlit or Gradio control (the product's own label, such as "Add to Index") never
  reach the model.
- `prompt.py` adds no source text either ("The user message is an evidence packet, never source
  code").

Across **every** stored run, the only attribute that ever reached a packet was `handler`:

| Run | Clusters | Avg packet chars (budget 40,000) | Attributes that reached the model |
|---|---|---|---|
| 187a4de5 (fastapi) | 29 | 1,784 | `handler` ×24 |
| 81dc03df (fastapi) | 21 | 1,771 | `handler` ×21 |
| 59f14c50 (Streamlit Q&A) | 7 | **321** | none |
| 27843131 (biomedical RAG) | 5 | 1,195 | none |
| e1037dbd (neuleap) | 84 | 11,595 | `handler` ×81 |

A typical record looks like this:
`{"k":"FUNCTION","q":"routers/complaints.py::create_complaint()","at":"…:L194-219","a":{}}`
plus a list of call edges. The model is asked for "three to eight acceptance criteria drawn
from the evidenced call parameters, configured defaults, processing steps, return outcomes,
fallbacks, and error handling", but it is never shown any of those things. It infers them from
function names. This is the most likely root cause of the thin, generic requirements and of
business rules always being empty.

**Fix (high leverage, cheap):**

1. **Put docstrings back.** Graphify already emits them as `RATIONALE` nodes with an
   `EXPLAINS` edge (`rationale_for`) to the function they describe. In `adapt_graphify_graph`,
   copy the full label onto the target entity as `attributes["docstring"]`. `compact_entity`
   already keeps `docstring` for FUNCTION, METHOD, CLASS and MODULE.
2. **Add a bounded source excerpt.** Most packets use less than 10% of their budget. For the
   boundary's handler and the next N nodes by BFS distance, add `attributes["source"]`: the
   definition's lines, capped (for example 40 lines or 1,500 characters per node) and with
   secrets masked by the same `_SECRET` regex the outline reader uses. Keep `char_budget` as the
   hard ceiling. `build_packets` already splits oversized packets, so the cost stays linear.
3. **Add `MODULE_SURFACE: ("call", "literals")`** and the boundary's `rationale`, `effect` and
   `registrars` to the keep-list.

Once the model can see a `raise HTTPException(403)` or a `MAX_UPLOAD_MB = 20`, the business-rule,
NFR and data families can fill up. Today nothing can populate them.

### 2.2 Recalibrated confidence has collapsed to one value [verified]

`recalibrate_confidence` exists because "a number that is 1.0 whatever the evidence carries no
information". It now outputs **0.95 for almost every item**:

| Run | Confidence values on published items | `checked_by` |
|---|---|---|
| e1037dbd | 0.95 × 84 | entailment × 84 |
| 187a4de5 | 0.95 × 21 | entailment × 21 |
| 59f14c50 | 0.95 × 10, 0.80 × 1 | entailment × 10 |

The arithmetic ([confidence.py:106](../src/agentic/capabilities/confidence.py#L106)) shows why.
Under Graphify, every bonus signal is effectively always on:

```
BASE 0.35 + SUPPORTED 0.25 + ENTAILED 0.10 + NAMES_A_SYMBOL 0.10 + TIGHT_SPAN 0.05
          + PARSED_EVIDENCE 0.10 (every non-rationale entity is EXTRACTED)
          + DECLARED_POLICY 0.05 (see §2.4) = 1.00 → clamped to CEILING 0.95
```

Pack 2 has noticed: `analyse.py` says confidence "is nearly uniform in practice (one value
covers 98% of a measured set)" and refuses to use it. **Fix:** see capability P1.13.

### 2.3 `derive_criteria` asks a false question on every attached requirement [verified]

`hedge_requirements` adds *"This was filed as belonging under 'X', but no requirement of that
name was published"* whenever `item.parent and not item.parent_id`. But `parent_id` is only
stamped by `prepare_claims`, which runs **after** `derive_criteria`. At that point every item
still has `parent_id == ""` (assembly clears it).

In run **e1037dbd**, all 19 requirements that have a resolved `parent_id` also carry this
question. `FR-AN-01 → BR-001 "Manage Analysis Prompts"` resolved correctly, yet it asks which
requirement it belongs to. These false questions flow into Pack 2's clarifications as well.
**Fix:** check `title_key(item.parent)` against the set of published titles instead of
`parent_id`, or move `derive_criteria` after `prepare_claims`.

### 2.4 A model-written `rationale` counts as "declared policy" [code]

`DOMAIN_SIGNAL_ATTRIBUTES = ("rule_source", "rationale", "statement", "allowed_values")` is used
by the rule classifier (`consolidate._domain_bearing`) and by confidence (`DECLARED_POLICY`).
Every boundary entity created by `ClassifiedBoundaryAdapter` carries
`attributes["rationale"] = <the classifier model's one-line reason>`, and it has the same
location as its handler.

So any claim whose citation overlaps a handler counts as "backed by declared policy". That does
two things:

- It lets a guessed business rule pass the rule classifier.
- It adds the +0.05 that pushes every score to the ceiling.

**Fix:** only count attributes an extractor wrote from the source (`rule_source`,
`allowed_values`, and a docstring once §2.1 lands). Rename the classifier's field to
`classifier_rationale` so it cannot collide.

### 2.5 Two different handlers exposed on the same route string collapse into one boundary [verified]

`discover_entry_points` mints each boundary id as
`stable_id("entrypoint", adapter, entry_class, identifier)`. Graphify drops decorator
arguments, so the classifier often returns the same identifier for different handlers (`/` for
every router's root, or the same path for GET and POST). The second one hits
`if entity.id in known: continue` and is **silently dropped**:

| Run | Classified as boundary | Boundary entities created | Lost to identifier collisions |
|---|---|---|---|
| e1037dbd | 95 | 88 | 7 (`/candidates/{id}/resume`, `/{report_id}`, `/backup/config`, …) |
| 09563424 | 44 | 38 | ≥2 |
| 187a4de5 | 31 | 29 | 2 (`/`, `/complaints/{key}/attachments`) |
| 81dc03df | 23 | 21 | 2 |

**Fix:** include `item.handler` in the id. Where the identifier really is shared, prefix it with
the method or the file (`POST /x`, `routers/complaints.py /`) so each boundary's qualified name
stays unique.

### 2.6 Capabilities pass information to each other as English sentences [code]

| Producer | Consumer | Contract |
|---|---|---|
| `hedge_requirements` writes `DERIVED_NOTE` into `assumptions` | Pack 2 `usable_criteria()` searches for `"derived from the cited evidence rather than stated by the analysis"` | if found, **all** criteria of the item are discarded |
| `hedge_requirements` writes an open question | Pack 2 `blocking_questions()` searches for `"What observable outcome would show…"` | decides the NEEDS_CLARIFICATION band |
| `consolidate._classify_rules` writes an assumption | `leakage.review_implementation_leakage` checks `"rather than a business rule" in note` | decides whether to demote again |
| `validate_requirements` formats `kind: 'term'` into a message | `quarantine._quoted_terms` parses the message | decides which words to strip |

Rewording any of these messages silently changes behaviour in another module, or in another
pack. **Fix:** add structured fields, such as `criterion_source: "evidence" | "derived"`,
`blocking: bool` on questions, and `terms: list[str]` on `ValidationIssue`, and have the
consumers read those fields.

### 2.7 Code that depends on entity kinds nothing produces [code]

The Graphify adapter emits only FILE, FUNCTION, METHOD, CLASS, SYMBOL, EXTERNAL_SYMBOL,
RATIONALE and MODULE_SURFACE. Everything below is dead code, or always takes the same branch:

| Depends on | Where | Effect today |
|---|---|---|
| `MODULE` kind + `DECLARES_CONFIG` edge | `GraphIndex.declared_policy` | always returns `[]`, so no config ever reaches a packet |
| `DOMAIN_POLICY` / `DATA_ENTITY` / `CONFIG_KEY` | `declaration_groups`, `survives_redesign`, `_support_for`, `_criterion_from` | never seeds a cluster; hedging's "declared condition" branch never fires |
| `BUSINESS_RULE` kind | `prepare_claims._tier_from_the_graph`, Gate 2 `RULE_WITHOUT_POLICY` | tier inference never fires; every rule fails the gate (flow.md B2) |
| `ENDPOINT.method/path` attributes | `entry_point_class`, `leakage.find_leakage` | the class is only `ENDPOINT`, so stratification cannot split by verb |
| `UI_ACTION.label` | `hedging._boundary_phrase` | falls back to the generic "the on-screen control" |

**Fix:** either produce these kinds (flow.md §10 B2 describes how), or delete the branches so
readers stop reasoning about behaviour that cannot happen. A small **contract test** that loads
a fixture graph and asserts each consumed kind appears at least once would catch the next drift.

### 2.8 Five different "how alike are two sentences" implementations [code]

| Module | Function | Stop-word list | Measure |
|---|---|---|---|
| clustering.py | `overlap` | `STOP_WORDS` (≈120 words) + stemmer | shared terms / smaller set |
| consolidate.py | `_similarity` | `FRAME_WORDS` (≈50 words), no stemming | shared words / smaller set |
| correspondence.py | `_content_words` | its own `FRAME_WORDS` (≈110 words) | set intersection |
| gates.py | restatement check | its own list | shared / smaller, 0.40 |
| testgen/quality.py, qualify.py | `SequenceMatcher` | none | character ratio |

Each stage therefore disagrees with the others about whether two statements are "the same".
Clustering can group two items that consolidation then refuses to merge, and vice versa.
**Fix:** one `agentic/text.py` holding a single tokeniser, stemmer, stop list and `similarity()`,
with thresholds kept per call site. If a dependency is acceptable, an embedding model behind the
same interface would also fix the "submit" vs "upload" blindness that flow.md mentions.

---

## 3. Pack 1: codebase-requirements

Manifest: [codebase-requirement-skills/pack.yaml](../codebase-requirement-skills/pack.yaml).
The table lists the capabilities in execution order.

| # | Capability | Step id | Module | on_fail |
|---|---|---|---|---|
| P1.1 | `ingest_repository` | ingest | [ingest.py](../src/agentic/capabilities/ingest.py) | halt |
| P1.2 | `graphify_extract` | graphify_extract | [graphify.py](../src/agentic/capabilities/graphify.py) | halt |
| P1.3 | `adapt_graphify_graph` | adapt_graph | graphify.py | halt |
| P1.4 | `propose_boundary_candidates` | propose_boundaries | [boundaries.py](../src/agentic/capabilities/boundaries.py) | halt |
| P1.5 | `discover_entry_points` | discover_entry_points | [entry_points.py](../src/agentic/capabilities/entry_points.py) | warn |
| P1.6 | `validate_graph` | gate_graph (gate) | [gates.py](../src/agentic/capabilities/gates.py) | halt |
| P1.7 | `build_boundary_clusters` | plan_clusters | [graph_ops.py](../src/agentic/capabilities/graph_ops.py) | halt |
| P1.8 | `cluster_capabilities` | cluster_candidates | [clustering.py](../src/agentic/capabilities/clustering.py) | warn |
| P1.9 | `digest_areas` | digest_areas | [assemble.py](../src/agentic/capabilities/assemble.py) | halt |
| P1.10 | `assemble_requirements` | assemble | assemble.py | halt |
| P1.11 | `consolidate_requirements` | consolidate | [consolidate.py](../src/agentic/capabilities/consolidate.py) | halt |
| P1.12 | `validate_correspondence` | check_correspondence | [correspondence.py](../src/agentic/capabilities/correspondence.py) | warn |
| P1.13 | `recalibrate_confidence` | score_confidence | [confidence.py](../src/agentic/capabilities/confidence.py) | warn |
| P1.14 | `review_implementation_leakage` | strip_implementation | [leakage.py](../src/agentic/capabilities/leakage.py) | warn |
| P1.15 | `resolve_candidates` | resolve | [resolution.py](../src/agentic/capabilities/resolution.py) | warn |
| P1.16 | `hedge_requirements` | derive_criteria | [hedging.py](../src/agentic/capabilities/hedging.py) | warn |
| P1.17 | `prepare_claims` | prepare_claims | [claims.py](../src/agentic/capabilities/claims.py) | halt |
| P1.18 | `validate_requirements` | gate_requirements (gate) | gates.py | repair ×3 → quarantine |
| P1.19 | `apply_repairs` | apply_repairs | claims.py | halt |
| P1.20 | `quarantine_requirements` | quarantine | [quarantine.py](../src/agentic/capabilities/quarantine.py) | halt (default) |
| P1.21 | `validate_synthesis` | final_check | resolution.py | warn |
| P1.22 | `render_deliverables` | render | [render.py](../src/agentic/capabilities/render.py) | warn |

---

### P1.1 `ingest_repository`: inventory and fail closed

**Use.** Lists every file in the snapshot and its language, and stops the run if nothing can be
parsed. That prevents a requirements document from being invented out of file names.

**How it works** ([ingest.py:222](../src/agentic/capabilities/ingest.py#L222))
1. `build_inventory()` walks `sorted(root.rglob("*"))`. It skips symlinks and any path containing
   a directory in `IGNORED_DIRECTORIES` (`.git`, `node_modules`, `venv`, `dist`, …).
2. It SHA-256s each file up to 8 MB (larger files are recorded as `skipped:<size>`).
3. It maps the extension to a language using 26 extensions, and sets `parseable` for 14
   languages.
4. It halts if the root is not a directory, has no files, or has **no parseable file**.
   Otherwise it warns about how many files were inventoried but not analysed.

The same module also holds `extract_zip_safely()`, which `runtime.prepare_run` uses. It checks
every member (absolute paths, `..`, drive letters, symlinks, encryption, compression ratio,
size and count) before writing anything.

**Weak spots**
- `rglob` walks *into* `node_modules/.venv` and filters afterwards. On a real repository that
  means hundreds of thousands of `stat` calls for nothing [code].
- The extension map covers 14 parseable languages, but Graphify parses about 36. A repository
  written only in Swift, Scala, Lua, Dart or Vue **halts here** as "no analysable source", even
  though Graphify could parse it [code].
- The warning still says "not Python" ([ingest.py:266](../src/agentic/capabilities/ingest.py#L266)).

**Make it better**
- Walk with `os.walk` and prune ignored directories in place (`dirs[:] = [...]`).
- Build `PARSEABLE_LANGUAGES` from Graphify's own supported-extension list, read once from the
  binary or pinned to a known version, rather than a hand-kept set.
- Hash lazily: only Pack 2's `resolve_from_source` reads the hashes.

---

### P1.2 `graphify_extract`: run the external parser

**Use.** Runs `graphify extract` (local tree-sitter parsing, no API calls) and records where its
`graph.json` was written.

**How it works** ([graphify.py:567](../src/agentic/capabilities/graphify.py#L567))
1. `find_executable()` looks in the configured path, then `PATH`, then next to `sys.executable`
   (the Windows virtual-environment case).
2. It runs
   `graphify extract <abs root> --code-only --no-cluster --no-gitignore --out <run>/graphify`
   with a 600 s timeout.
3. It halts when the binary is missing, the exit code is non-zero, the run times out, or no
   `graph.json` exists.

**Weak spots**
- There is no version check. The adapter's comments describe the output of graphifyy 0.9.52 as
  measured ("no `type` field", "`_callable` untrustworthy for Go"). A newer release that changes
  the schema would produce a silently wrong graph rather than an error [code].
- `--no-gitignore` combined with a folder snapshot that still contains `node_modules/.venv`
  (flow.md §3.2) means Graphify may parse vendored code. Ingest would ignore those files, but
  the graph would still contain their nodes, unless Graphify has its own default excludes
  [likely].
- Nothing is cached. Re-running the same unchanged tree re-parses everything.

**Make it better**
- Record `graphify --version` in `graphify_raw`, and fail (or warn) outside a tested version
  range.
- Pass the ingest ignore list to Graphify (an exclude flag or a generated ignore file), so that
  the inventory and the graph describe the same files.
- Cache `graph.json` by a hash of the inventory (the sha256s already exist) plus the Graphify
  version.
- Check for the binary in `preflight()` (flow.md §3.3).

---

### P1.3 `adapt_graphify_graph`: normalise into `KnowledgeGraph`

**Use.** This is the only place Graphify's output format is understood. Everything downstream
reads the canonical `KnowledgeGraph`.

**How it works** ([graphify.py:669](../src/agentic/capabilities/graphify.py#L669))
1. **Kind classification** (`classify_kind`), most-certain signal first:
   `rationale` → RATIONALE; no file → EXTERNAL_SYMBOL; label is a path suffix → FILE;
   `_callable_class` → CLASS; label ends in `()` → METHOD (leading `.`) or FUNCTION;
   `_callable` → FUNCTION; otherwise SYMBOL.
2. **End-line reconstruction** (`_derive_line_ends`), because Graphify gives only a start line.
   Each definition ends where its next sibling starts, or at the end of the file. Containers are
   then extended to their deepest child until nothing changes. RATIONALE nodes are bounded
   separately so a docstring does not cut its function short.
3. **Location safety.** A node whose file does not resolve inside the tree becomes an
   EXTERNAL_SYMBOL, rather than an entity that would fail Gate 1.
4. **Verifiable symbols.** FILE and RATIONALE entities, and labels that are not identifiers, get
   `symbol=None`, so the evidence verifier never searches a file for a sentence.
5. **A FILE entity for every inventoried file**, including READMEs (dropped by `--code-only`),
   with a `content_outline` that has secrets masked. That outline feeds the
   `declared_documentation` that `elevate` reads.
6. **Relation mapping** (`RELATION_MAP`): `indirect_call` becomes **REGISTERS**, which is what
   keeps registered handlers visible as uncalled. Unknown relations become `RELATED_TO`. Edges
   whose endpoints were never emitted are counted and dropped.
7. **Module-level surface** (`_add_module_surface`). A regex scan of lines outside every
   definition span, where a call's result is *consumed* (`if st.button("…")`,
   `x = st.file_uploader("…")`), the line carries a literal, and the call does not target
   something defined locally. Each hit becomes a MODULE_SURFACE entity, with inferred CALLS edges
   to the functions named within the next 14 lines.
8. `_assert_structurally_sound` raises on duplicate ids or dangling edges, since those can only
   be adapter bugs.

**Weak spots**
- **It throws away the docstrings it already has** (§2.1). RATIONALE nodes carry the text, but
  it is truncated to 80 characters as a *name* and never attached to the function it explains.
- `by_bare_name.setdefault(bare, mapped)`: when two files define `process()`, a surface control
  in `b.py` gets a CALLS edge to `a.py::process` [code].
- Several loops scale quadratically or read the same file repeatedly [code]:
  - `any(entity.id == file_id for entity in builder.entities)` rebuilds the whole entity list for
    every surface node.
  - `_file_line_counts(ctx.source_root, {record.path})` is called once per inventoried file
    inside the FILE loop, although the counts were already computed.
  - `_module_surface`, `_tight_definition_spans` and `_names_between` each re-read the same file,
    and `_names_between` does so once per surface node.
  - `_derive_line_ends` rebuilds the `following` list for every node, which is O(n²) per file.
- Surface detection is shaped around Python syntax (the `if/elif/while` and `x =` forms) but runs
  over every parseable language.

**Make it better**
- Follow `EXPLAINS` edges and attach the full RATIONALE text as `docstring` on the target entity.
- Resolve `_names_between` names against the **same file first**, then imports, then globally.
- Read each file once into a `{path: lines}` cache shared by the three helpers. Keep a
  `set(builder ids)`. Compute line ends with a single sorted sweep.
- Record which grammar produced each surface hit, and restrict the surface scan to languages
  with module-level statements (Python, JavaScript/TypeScript, Ruby, PHP).

---

### P1.4 `propose_boundary_candidates`: which nodes might be entry points

**Use.** Picks the graph nodes that are worth asking the classifier about, and attaches the
evidence for each. The idea is language-agnostic: a handler is called by a framework, never by
the code beside it, so it has **in-degree 0 on CALLS**.

**How it works** ([boundaries.py:159](../src/agentic/capabilities/boundaries.py#L159))
1. `_shape()` computes, per node, the inbound and outbound CALLS counts, the external names it
   references (decorators and annotations show up as `references` to EXTERNAL_SYMBOLs), and its
   registrars (from REGISTERS edges).
2. A FUNCTION, METHOD, CLASS or MODULE_SURFACE with a location is kept if it is uncalled, **or**
   referenced, **or** registered.
3. `_SourceWindow.literals_near()` reads string literals within ±3 lines of the definition *and*
   of each registration site. That is how `http.HandleFunc("/x", h)` in `main` names `h`.
4. Candidates are sorted by (#references, #registrars, out-degree, name) and **capped at 120**.

**Weak spots**
- The cap cuts off whatever sorts last. neuleap had about 1,980 candidates, so 94% were never
  shown to the classifier (flow.md B3). The sort key rewards Python-style decorated functions.
- `_useful_literal` drops any literal containing a space. That is right for routes, but it
  throws away UI labels and CLI help text on non-surface candidates.
- An HTTP method is not captured as its own field, which feeds the identifier collisions in
  §2.5.

**Make it better**
- Batch the candidates (for example 30 per call as a fan-out) instead of truncating. If a cap
  must stay, stratify by directory or file with the existing `_stratified()`.
- Add a deterministic pre-classification pass for high-precision patterns (a decorator name in
  {`get`,`post`,`route`,`command`,`task`,…} together with a literal that starts with `/`).
  Candidates matched this way skip the model and get a higher confidence than 0.55.
- Extract `method` (GET, POST, …) from the literal window and pass it through, so boundaries get
  unique identifiers and `entry_point_class` can stratify by verb.

---

### P1.5 `discover_entry_points`: fold classified boundaries into the graph

**Use.** The single place where boundary entities are created. It runs every registered
`EntryPointAdapter` and publishes a coverage report, so a class of entry point that is missing
shows up as a line in the report rather than as silence.

**How it works** ([entry_points.py:248](../src/agentic/capabilities/entry_points.py#L248))
1. It loads `boundary_assignments` (the classifier's output) and merges each candidate's
   `references_undefined_names` and `registered_by` in as `registrars`. The leakage pass uses
   these later.
2. The only adapter, `ClassifiedBoundaryAdapter`, resolves each `qualified_name` against the
   graph. Unknown names are dropped, so a name the model made up cannot become a citation. It
   takes the location **from the graph** and never from the model. The identifier falls back to
   `module!__main__`, `module.fn!cli` or `"<CLASS> <qualified name>"`.
3. Each boundary becomes an entity (`provenance=INFERRED`, `confidence=0.55`) with an `EXPOSES`
   edge from its handler.
4. It reports `by_class`, `by_adapter`, `declared_but_empty`, `present_but_unclaimed`,
   `boundary_kinds_with_no_adapter` and `adapter_failures`.
5. Registering an adapter **adds its declared classes to `BOUNDARY_KINDS`**
   (`entry_point_adapter()`), so CLI_COMMAND, EVENT_CONSUMER and REGISTERED_ENTRY do seed
   clusters (see §9).

**Weak spots**
- Boundaries are lost to identifier collisions (§2.5) [verified].
- `BOUNDARY_KINDS` is a **mutable module global** that registration changes as a side effect.
  It works only because `load_builtin_capabilities()` imports this module first. Tests that
  register a test adapter have to `.discard()` its classes by hand afterwards, or the change
  leaks into later tests.
- `effect` (read or mutate) comes only from the model. `correspondence._effect_agreement` is the
  only thing that uses it.

**Make it better**
- Include the handler in `stable_id`. When two handlers really share one identifier, create one
  boundary with two `EXPOSES` edges rather than dropping one.
- Replace the global with `boundary_kinds()`, computed from the base set plus the declared
  classes of registered adapters.
- Add real deterministic adapters (FastAPI and Flask decorators, Express `app.get`, Spring
  `@GetMapping`, Go `HandleFunc`, argparse and click). The seam is already there, and it is the
  surest cure for the classifier's run-to-run variance.

---

### P1.6 `validate_graph`: Gate 1

**Use.** Structural checks on the graph before any money is spent on synthesis.

**How it works** ([gates.py:62](../src/agentic/capabilities/gates.py#L62)). It runs eight
checks. `UNRESOLVABLE_LOCATION` (`verify_location` on every located entity),
`ORPHAN_BOUNDARY` and `NO_FILE_ENTITIES` are ERRORs. Self-relationships, coverage below 50%,
more than 30% ambiguous edges, no boundary, and more than 50% inferred boundaries are warnings.

**Weak spots**
- One bad entity halts the whole run (flow.md B9).
- `INFERRED_BOUNDARY_SHARE` always fires now, because every boundary is inferred.
- `verify_location` reads files through `lru_cache(maxsize=512)`. A repository with more than
  512 files re-reads them constantly while it walks thousands of entities [code].

**Make it better**
- Quarantine the offending entity (drop it and record it) and continue. Halt only when more
  than a set share of locations fail, which is what a broken extractor actually looks like.
- Drop the always-true warning, or reword it as information.
- Size the cache from the inventory (`maxsize=len(files)`), or pre-load the line counts once.

---

### P1.7 `build_boundary_clusters`: one bounded evidence packet per boundary

**Use.** Controls cost: model cost grows with the number of boundaries, not with the size of
the codebase, and no model call ever contains a whole repository.

**How it works** ([graph_ops.py:639](../src/agentic/capabilities/graph_ops.py#L639))
1. `boundary_ids()` returns the boundary entities plus their `EXPOSES` sources, ranked by
   degree.
2. For each primary boundary, `workflow_from()` does a BFS over `WORKFLOW_EDGE_KINDS` (depth 6,
   250 nodes). It first steps back across `EXPOSES` to the handler, unless the boundary has
   workflow edges of its own (a surface control). Then `declared_policy()` is added.
3. **Grouping by signature.** Boundaries whose non-boundary member set is identical share one
   packet (two routes on one handler cost one call).
4. **Sampling.** `_stratified(groups, max_clusters=80)` gives every class a floor of one slot
   plus a proportional share, with the largest remainders going first.
5. **Declaration clusters** get a separate budget of `max_clusters // 4`.
6. `build_packets()` serialises members with `compact_entity()` plus up to 12 workflow edges
   each, and splits across up to 6 packets at 40,000 characters.

**Weak spots**
- The packets are thin (§2.1) [verified]. This is the biggest limiter in the whole pack.
- `declared_policy` and declaration clusters are dead (§2.7).
- `omitted = len(groups) - len(clusters)` counts packets, not groups, and the total number of
  packets is unbounded: 80 × 6 = 480 calls (flow.md B5).
- `entity_ids.index(entity_id)` in `build_packet` is O(n) per overflow and wrong if an id
  repeats [code].

**Make it better**
- Carry code evidence in the packets (§2.1). Rank what goes in by BFS distance from the boundary,
  so the budget is spent near the entry point first.
- Add a `max_total_packets` setting, and compute `omitted` from `groups` minus `planned`.
- Put the boundary's own evidence (identifier, literals, classifier rationale, effect) at the
  top of the packet as a `boundary` record. Today the worker sees only
  `cluster.boundary = "/"`.

---

### P1.8 `cluster_capabilities`: group candidates by meaning across the repository

**Use.** Fan-out workers see one boundary each, so the same capability described from the
endpoint and from the worker behind it cites different lines. This capability groups
candidates by *what they say*, regardless of which code they cite.

**How it works** ([clustering.py:424](../src/agentic/capabilities/clustering.py#L424))
1. `signature_of()` parses a requirement sentence into an actor (text before the modal), an
   action (the first content word after the modal, with "be able to" and "allow … to"
   stripped), object terms, and all terms, with light stemming.
2. `_index_by_term()` builds an inverted index, skipping terms that appear in more than 25% of
   candidates. Only pairs that share a term are compared.
3. `relate()` checks, in order: *conflicting* (same exact spans but diverging wording or
   actions); *duplicate* (≥0.90, same tier); *parent_child* (≥0.80 containment and 3 or more
   extra terms); *mergeable* (≥0.72).
4. Union-find over the non-independent pairs gives clusters `CAP-001…`. The relation of a
   cluster is the strongest relation among its pairs, and `conflicting` wins.
5. It outputs `clusters` and `by_title → cluster_id`.

**Weak spots**
- It works on sentence shape with regular expressions, so it is English-only and blind to
  synonyms.
- The module docstring says "elevation binds outcomes to cluster identifiers". In fact
  `digest_areas` never reads this artifact and `elevate` binds by title (see P1.9) [code].
- One cluster that contains a single conflicting pair is marked `conflicting` as a whole.
  `resolution` had to learn to work around this (see its docstring).

**Make it better**
- Use the shared text module (§2.8), and optionally embeddings.
- **Use the clusters where they help most:** send `CAP-id + lead title` to `elevate` and bind by
  CAP-id (P1.9 and P1.10). A paraphrased title then no longer leaves a capability unattached.
- Store the relation per pair, not only per cluster, so consumers can tell which pair
  conflicts.

---

### P1.9 `digest_areas`: the small input for the single whole-repository call

**Use.** Builds the input for `elevate`: per area, its code, name, boundaries, purpose hints
and **titles only**. That input stays small as the repository grows.

**How it works** ([assemble.py:243](../src/agentic/capabilities/assemble.py#L243))
1. `_area_index()` cleans each worker's area code (2–4 capital letters, falling back to `GEN`)
   and then runs `_reconcile_areas()`. Headings that mean the same thing ("Batch Management" /
   "Batch handling") are folded together, and so is a narrowed heading ("Candidate Notes
   Management") into a broader one someone actually named ("Candidate Management").
2. Items are grouped by **their own** `area`, not by their cluster's area.
3. Caps: 12 boundaries, 6 hints, 250 capability titles and 60 rule titles per area.

**Weak spots**
- The manifest declares `consumes: [fragments, capability_clusters]`, but the capability never
  loads `capability_clusters` [code].
- There is no prompt renderer for `area_digest`. Past 60,000 characters it is sent as a truncated
  JSON *string* (flow.md B10d).

**Make it better**
- Send one line per capability cluster (`CAP-017: Upload a medical document (+3 variants)`) in
  place of every raw title. That is smaller and gives the model stable handles to bind to.
- Register a renderer that trims by budget and never produces invalid JSON.

---

### P1.10 `assemble_requirements`: build the requirement set without a model

**Use.** Replaced the old LLM `reduce` step, which could only ever return about a dozen
requirements because of the 8,000-token output limit. Every grounded finding from every worker
is carried through unchanged.

**How it works** ([assemble.py:444](../src/agentic/capabilities/assemble.py#L444))
1. It creates a `ProductBRD` from `top_tier`: purpose, problem, business requirements,
   assumptions, and so on.
2. It builds `bindings`: the `title_key` of each capability → the outcome that claimed it. The
   first binding wins.
3. It carries every fragment item from all ten `FRAGMENT_SECTIONS` via `_carry()`: remapped
   area, cleared id and parent_id, `cluster_id` and `cluster_conflict` from `by_title`, priority
   normalised to M, S or C. Each functional requirement's `parent` is set from `bindings`.
4. It drops outcomes whose title equals a capability title (a restated capability) and clears
   links pointing at them.
5. `ground_outcomes()` derives each outcome's evidence from the capabilities beneath it: one
   citation per child, at most 6, and confidence equal to the lowest child's.

**Weak spots**
- Binding by exact `title_key`. In **e1037dbd** only 19 of 84 published items have a parent. The
  rest stay unattached because the model paraphrased titles, or did not bind them at all
  [verified].
- "First binding wins" silently hides a capability that the model put under two outcomes.

**Make it better**
- Bind by CAP-id (P1.8 and P1.9). As a fallback, match a paraphrased title to the nearest
  capability title with the shared `similarity()` above a threshold, and report each fuzzy match.
- Report double bindings instead of silently picking the first.

---

### P1.11 `consolidate_requirements`: level, merge, families, hierarchy

**Use.** Turns a pile of per-boundary findings into a set at the right level of abstraction.
Nothing is deleted: items are merged, refiled or demoted, and their evidence is kept.

**How it works** ([consolidate.py:825](../src/agentic/capabilities/consolidate.py#L825)): seven
passes, in this order.

| Pass | Function | Rule |
|---|---|---|
| 0 | `_classify_rules` | A "rule" survives only if a cited entity overlapping its lines is a DECLARATION_KIND or carries `rule_source/rationale/statement/allowed_values`. Otherwise it is demoted to internal behaviour, with a note |
| 1 | `_apply_abstraction` | `abstraction_basis` → tier → section (`BASIS_TYPES`), and the basis wins over the type |
| 2 | `_extract_acceptance_criteria` | Sentences matching `SUCCESS_RE` ("successfully", "without error", "is validated", …) are folded into their parent (or a sibling sharing evidence) as criteria |
| 3a | `_merge_duplicates` | Within a section: shared cluster or evidence, **same action verb**, and ≥0.65 word overlap |
| 3b | `_merge_across_tiers` | ≥0.85 overlap or an identical title; the more abstract tier keeps the item; never merges a declared parent and child |
| 4 | `_collapse_parameterised` | 3 or more statements identical except for one non-verb token become one statement ("A, B or C") that keeps every citation |
| 5 | `_consolidate_criteria` | Drops duplicate criteria (≥0.95) and criteria that restate the statement (≥0.80) |
| 6 | `_validate_hierarchy` | Keeps a parent link only if the parent's tier and the relation are permitted |

**Weak spots**
- In pass 0, a model-written `rationale` counts as declared policy (§2.4) [code].
- `SUCCESS_RE` includes `\bis validated\b`. "The uploaded document is validated against the
  accepted formats" is a real requirement, and it gets folded into another item as a criterion
  [code].
- The pass 2 fallback host is `next(... shares evidence ...)`, which is simply whichever item
  comes first. A criterion can land on an unrelated item that happens to cite the same line.
- `_merge_across_tiers` compares every pair of items in the document, O(n²) [code].
- In `_validate_hierarchy`, both branches of the `if len(allowed_relations) == 1` test do the
  same thing, so the branch is dead code [code].

**Make it better**
- Pass 0: see §2.4.
- Pass 2: require an explicit parent, or a host with the same action *and* shared evidence.
  Drop `is validated` from the pattern.
- Pass 3b: only compare pairs that share a CAP cluster or evidence, since those are the only
  pairs that can merge anyway.
- Use the shared text module (§2.8).

---

### P1.12 `validate_correspondence`: does the cited code do what the sentence says?

**Use.** Every other check compares a statement with another statement or with a word list.
This is the only one that compares it with the code.

**How it works** ([correspondence.py:472](../src/agentic/capabilities/correspondence.py#L472))
1. `EvidenceView` indexes entities by file, and precomputes the boundary ids and everything
   reachable from them.
2. Five registered checks run per item (observations are skipped):
   - `cites_something`
   - `span_adequate` (400 lines or fewer)
   - `resolves_to_known_code` (something other than a bare FILE or RATIONALE sits there)
   - `lexically_anchored` (at least one content word of the claim appears in the cited
     entities' names or text, or in the identifiers within ±25 lines)
   - `actor_reachable` (a functional requirement must cite code reachable from a boundary)
   - `effect_agreement` (a change verb must not cite only read-only boundaries)
3. Any failure marks the item `rejected`. `objective=True` if a factual check failed; otherwise
   the failure is only a matter of interpretation.
4. The survivors go to **one batched LLM entailment call**, with at most 14 "facts" per item
   (kind, name, and a detail from `rationale/statement/expression/value/predicate`). The prompt
   says "When in doubt, mark it supported".

**Weak spots**
- The **facts are nearly empty**. Graphify entities have none of those detail attributes, so
  the model is judging a sentence against a list of function names, and is told to default to
  "supported". Result: 100% supported by entailment in every run checked [verified].
- There is one call with no chunking, and a missing verdict counts as supported (flow.md B6).
- `cited_source_words()` re-reads each cited file for every item, with no cache [code].
- This is a hidden LLM call inside a deterministic step (§1).

**Make it better**
- **Send the cited source lines as the facts.** `cited_source_words()` already reads exactly
  those lines. Cap them at about 30 lines per citation.
- Chunk the call to about 25 items. A missing verdict should be `unchecked`, not `supported`.
- Move entailment into its own `mode: llm` step with an `EntailmentVerdicts` schema, so it shows
  in the topology, preflight covers it, and the step retry logic applies.
- Keep a `{path: lines}` cache on `EvidenceView`.

---

### P1.13 `recalibrate_confidence`: confidence from evidence, not self-report

**Use.** Replaces the model's own confidence with a score built from observable signals, and
keeps the original as `model_confidence`.

**How it works** ([confidence.py:153](../src/agentic/capabilities/confidence.py#L153)). An
additive score, clamped to [0.10, 0.95], with the reasons recorded. Items below 0.70 get an
assumption explaining why they are low.

**Weak spots**
- It has saturated at 0.95 (§2.2) [verified]. Every term in the formula is binary, and under
  Graphify each one is almost always true.
- The weights are "a stated judgement, not a fitted calibration", yet
  `tests/fixtures/corpus_truth/` holds labelled ground truth that could be used to fit them.

**Make it better**
- Use signals that **vary** across claims:
  - Boundary provenance: a deterministic adapter scores higher than the classifier's 0.55.
  - How many workers or clusters independently produced the claim (`superseded` length,
    CAP cluster size).
  - The lexical-anchoring overlap as a continuous ratio, not pass/fail.
  - The citation's distance from the boundary (BFS hops). Deep helper code is weaker evidence
    for an actor-facing claim.
  - Whether the claim went through repair or quarantine.
- Fit the weights with `tests/harness` against `corpus_truth`, and publish a calibration curve.
  The number means something only if a 0.7 is right about 70% of the time.

---

### P1.14 `review_implementation_leakage`: keep code names out of statements

**Use.** Removes identifiers, routes and mechanism words from requirement statements and
records them as `implementation_observations`. It also demotes internal behaviours that fail
the "would a rewrite still need this?" test.

**How it works** ([leakage.py:250](../src/agentic/capabilities/leakage.py#L250))
1. The vocabulary comes **from this repository**: the names and qualified names of entities
   within ±25 lines of the citation, their code-shaped parts, and mechanism attributes (`path`,
   `method`, `handler`, `registrars`, …).
2. `find_leakage()` finds route or file locators, code-shaped words that appear in that
   vocabulary, and mechanism values matched as whole words.
3. `strip_leakage()` removes them. It also removes connectives left stranded ("by", "via") and
   articles, and refuses the rewrite if the result is shorter than 6 words or matches the
   "mangled" patterns.
4. `survives_redesign()`: an `internal_behaviour` is kept only if it cites a boundary, a
   declaration, or a `rule_source`. Otherwise it becomes an observation.

**Weak spots**
- `survives_redesign` relies on declaration kinds and `rule_source`, which Graphify never
  produces (§2.7). So only internal behaviours that happen to cite a boundary survive [code].
- Any token starting with `/` is stripped even if it is *not* in the repository vocabulary [code].
- `EvidenceView` is built without `source_root`, so the neighbourhood is entities only, never
  the text on the lines.

**Make it better**
- Once §2.1 or declaration kinds exist, extend `survives_redesign` to count "something outside
  depends on it", for example a BFS path from a boundary together with a docstring that states
  intent.
- Require `/tokens` to appear in the vocabulary or to look like a route (a second segment, or
  `{param}`).

---

### P1.15 `resolve_candidates`: exactly one decision per candidate

**Use.** Gives every candidate one disposition: `keep | reclassify | observation |
open_question | reject | merge`. Anything that cannot be published moves to
`unresolved_candidates` along with its evidence and reason, so nothing is deleted.

**How it works** ([resolution.py:302](../src/agentic/capabilities/resolution.py#L302))
1. `_resolve_conflicts()` groups conflicting items by (cluster, exact spans, action verb) and
   ranks them by strong support, distinct spans, verdict and number of criteria. The best is
   kept. On a tie, near-identical wording is merged; otherwise every item becomes an open
   question.
2. `_scope_criteria()` drops criteria that name a repository file the item does not cite.
3. For each item: a degenerate statement is rejected. A correspondence rejection becomes
   `reject` (objective) or `open_question` (interpretive). Observations stay observations. A
   business outcome with fewer than 2 supporting capabilities is either merged into its single
   twin, or reclassified as a functional requirement and given the most common area of its
   children.
4. It partitions the set, re-files reclassified items, and clears orphaned parent links.

**Weak spots**
- With title binding as fragile as it is (P1.10), `MIN_SUPPORTING_CAPABILITIES = 2` often
  demotes real outcomes, which is why runs publish 0–5 business requirements.
- `_degenerate` looks for a double space, which cannot happen after
  `" ".join(statement.split())`. That check is dead code [code].

**Make it better**
- Fix binding first (P1.10). Then consider counting capabilities in the outcome's CAP clusters,
  not only direct children.
- Record the disposition as a structured enum history (`[(step, disposition, reason)]`), so
  that the UI and Pack 2 can show *why* an item changed across steps.

---

### P1.16 `hedge_requirements`: say what the document does not know

**Use.** Fills in `assumptions`, `open_questions` and missing acceptance criteria from the
evidence, so the document says which parts it is unsure of.

**How it works** ([hedging.py:206](../src/agentic/capabilities/hedging.py#L206)). For each item:
1. If no cited entity is EXTRACTED, it adds an "inferred from prose" assumption.
2. If correspondence rejected the item, it adds an open question.
3. If the parent is unresolved, it adds an open question. This check is broken (§2.3).
4. If there are no criteria, `_criterion_from()` derives one: a boundary gives *"Exercising
   {the published interface} produces the outcome this requirement describes."*; a declaration
   gives *"The declared condition still holds: …"*; a README line gives *"The documented
   condition still holds: …"*. Otherwise it adds an open question.

**Weak spots**
- It raises false "which requirement does it belong to?" questions (§2.3) [verified].
- The boundary-derived criterion **asserts nothing** ("…produces the outcome this requirement
  describes"). Pack 2 detects the marker and throws the criterion away. In other words, Pack 1
  generates text whose only purpose is to be discarded by Pack 2.

**Make it better**
- Fix the parent check (§2.3).
- Do not emit the tautological criterion. Emit the "What observable outcome…" open question with
  a structured `blocking=True` flag. Once §2.1 lands, derive real criteria from what is visible
  in the cited code: `raise`/`throw` with a status or message, returned constants, limits.

---

### P1.17 `prepare_claims`: bookkeeping before the gate

**Use.** Settles tiers, removes restatements, stamps ids, resolves parent titles to ids, and
fixes citation labels that the model filled in wrongly, without touching any claim's substance.

**How it works** ([claims.py:476](../src/agentic/capabilities/claims.py#L476)): in order,
`_tier_from_the_graph` → `_agree_with_the_grounds` (the basis decides the tier) →
`_file_by_tier` → `_settle_relations` → `_drop_restatements` (the same statement, or the same
evidence and the same verb, within a section) → `ground_outcomes` → `assign_ids()` (positional:
`BR-001`, `FR-<AREA>-01`, …).

Then, per citation, `_ground_location` corrects symbols that name the enclosing scope, or that
contain free text instead of a name, using the graph. `_name_the_cited_node` swaps a bare token
for the qualified name of the node at that line, but only if the citation still verifies.
Symbols the graph has never seen are left alone, because they are a sign the model invented
something.

**Weak spots**
- `_tier_from_the_graph` and `module_names` depend on kinds that do not exist (§2.7).
- Ids are positional and re-stamped on every pass, so they are unstable across repairs and
  across runs (flow.md B7).
- The `inferred` metric is always 0, and the message that reports it can never be printed.

**Make it better**
- Content-addressed ids (a hash of the area, the normalised title and the first citation) with
  a short readable prefix. That makes runs diffable and gives Pack 2 stable references.
- Delete the dead paths, or produce the kinds they need.

---

### P1.18 `validate_requirements`: Gate 2

**Use.** Checks every surviving claim again, independently of how it was produced: citations
against the real files, graph paths against the graph, and wording against the jargon linter.

**How it works** ([gates.py:274](../src/agentic/capabilities/gates.py#L274)). Per item,
`_validate_item`:
- **Wording:** `EMPTY_STATEMENT`; `TECHNICAL_VOCABULARY` / `CRITERION_VOCABULARY` from
  `find_jargon`.
- **Evidence:** `NO_EVIDENCE`; `verify_location` codes.
- **Graph trace:** `NO_GRAPH_PATH` / `UNRESOLVED_GRAPH_PATH`; `NO_INTERACTION_BOUNDARY` (the
  trace is a boundary, is downstream of one, or reaches one within 3 hops);
  `RULE_WITHOUT_POLICY`.

Whole-set checks: purpose, business requirements, duplicates, parent tier and relation
permissions, restatement. Supplement families need evidence but no boundary. Integration and
NFR families may name external systems.

**Weak spots**
- `RULE_WITHOUT_POLICY` can never be satisfied today (flow.md B2).
- There is no flag saying which errors a repair can actually fix, so the repair budget is spent
  on errors it cannot fix.
- The jargon lists contain ordinary business words [code]:
  - `method`, `class`, `function` and `package` are in `IMPLEMENTATION_TERMS`, so "choose a
    payment **method**" or "a **class** of service" is rejected.
  - `server`, `frontend` and `backend` appear in **both** `IMPLEMENTATION_TERMS` and
    `DOMAIN_TERMS`, so they are always rejected, despite the comment saying domain terms are
    rejected only in strict mode.
- `index.reaches_boundary()` recomputes `boundary_ids()` on every call, inside a loop over every
  item [code].

**Make it better**
- Add `repairable: bool` to `ValidationIssue`. The router should send only repairable errors to
  `repair` and quarantine the rest at once. (Pack 2 already defines `REPAIRABLE` but does not use
  it; see P2.9.)
- Downgrade `RULE_WITHOUT_POLICY` to a warning until declaration kinds exist.
- Move `method/class/function/package` to a "flag only when next to a code-shaped token" rule,
  and remove the three duplicated words from one of the two sets.
- Compute `boundary_ids()` once and pass it in.

---

### P1.19 `apply_repairs`: merge the repair pass back by id

**Use.** The repair model returns only the items it fixed. This capability swaps them in by id,
so a claim the gate never rejected cannot be altered.

**How it works** ([claims.py:586](../src/agentic/capabilities/claims.py#L586))
1. A missing `repairs` artifact means everything passes through.
2. Returned ids the gate did not reject are ignored.
3. For each accepted replacement, it copies back the **original** id, title, basis, type,
   parent, relation, area, priority, disposition, cluster, `model_confidence`, `superseded` and
   observations.
4. Deletions clear links to the deleted items.

**Weak spots**
- `evidence`, `graph_path` and `acceptance_criteria` are taken from the **model's reply**. The
  repair prompt says "never invent evidence", but nothing enforces it. A repair can re-point a
  citation at another real line, and the gate will accept it [code].
- A stale `repairs` artifact from an earlier attempt can be applied again after the ids have
  shifted (flow.md B7).

**Make it better**
- Copy `evidence` and `graph_path` from the original as well. A repair is allowed to change
  wording (the statement, and criteria text) and nothing else.
- Name the repair artifact per attempt (`repairs#2`), or clear it after it is applied.

---

### P1.20 `quarantine_requirements`: what happens when the repair budget runs out

**Use.** Deals with what the gate still rejects after 3 repairs. **Grounding** failures are
removed. **Wording** failures keep the claim, with the offending term stripped and recorded.

**How it works** ([quarantine.py:81](../src/agentic/capabilities/quarantine.py#L81))
1. Errors are split into `WORDING_ONLY` and grounding errors. For outcomes, errors in
   `DERIVED_CITATION` are forgiven, because an outcome's citation is derived from its children.
2. For wording failures: `_quoted_terms()` parses the terms out of the gate message and expands
   their inflections. `strip_leakage()` removes them, but never the sentence's main verb. The
   rejected criteria are dropped.
3. Grounding failures are removed. Orphans are unlinked. `ground_outcomes()` runs again. An
   outcome left with no capabilities is dropped. `assign_ids()` runs again.

**Weak spots**
- It parses the gate's message text (§2.6).
- If stripping cannot produce a valid sentence, the statement is left as it was, still carrying
  the rejected term, and it is published anyway.

**Make it better**
- Read `issue.terms` from a structured field.
- Add a last-resort path for an unfixable wording failure: publish the claim with
  `status: "wording_unresolved"` so a person can reword it.

---

### P1.21 `validate_synthesis`: whole-set checks at the end

**Use.** Checks properties that only exist once nothing else will change the set.

**How it works** ([resolution.py:633](../src/agentic/capabilities/resolution.py#L633))
1. `_fold_identical()` merges items with the same title in the same section, then runs
   `assign_ids()`.
2. It reports `DUPLICATE_STATEMENT`.
3. It **enforces** fixes for `RELATION_WITHOUT_PARENT` and `CIRCULAR_HIERARCHY` (by cutting the
   last link in the loop).
4. It reports: unresolved or rejected items in the document, observations published as
   requirements, business requirements with fewer than 2 capabilities, `UNRESOLVED_CONFLICT`,
   and missing ids.

**Weak spots**
- `UNRESOLVED_CONFLICT` fires for every item that still has `cluster_conflict=True`, including
  the **winners** that resolution deliberately kept. This is noise, and Pack 2 reads it
  (`_conflicted_ids`), which pushes those items into `ELIGIBLE_WITH_ASSUMPTIONS` [code].
- `assign_ids()` after the gate renumbers items, so ids in `requirement_gate.json` no longer
  match the published ids.

**Make it better**
- Clear `cluster_conflict` on the items resolution kept, or record `conflict_resolved=True` on
  them.
- Fixed by content-addressed ids (P1.17).

---

### P1.22 `render_deliverables`: write the documents

**Use.** Always runs, even on a thin result. It writes `requirements.md`, `requirements.json`
(the contract Pack 2 reads), `traceability_matrix.csv`, `gap_analysis.md`,
`technical_specification.md`, `api_documentation.md`, `workflows.mmd` (at most 40 nodes) and
`requirement_analysis.md`. It lints the business document for jargon once more.

**How it works** ([render.py:1111](../src/agentic/capabilities/render.py#L1111)).
`build_requirement_set()` flattens the sections into one typed list: `id`, `type` (via
`published_kind`), `parent_id`, `parent_relation`, `abstraction_basis`, statement, criteria,
`confidence`, `status`, evidence, `graph_path`, assumptions, `open_questions`, `superseded`,
`model_confidence`, area, area name and priority. Observations are left out on purpose.

**Weak spots**
- `requirements.json` is a bare array with **no schema version**. Pack 2 has to guess the
  contract from which keys are missing (`CONTRACT_KEYS`).
- The published record has no **boundary kind** and no **entry point**. Pack 2 then guesses the
  interface from `graph_path[0]` and gets it wrong (P2.4).

**Make it better**
- Wrap the output in an envelope: `{"schema_version": 2, "generator": …, "requirements": […]}`.
  Pack 2's reader should accept both shapes.
- Add `entry_points: [{kind, identifier}]` to each requirement, taken from `BoundaryReach`, which
  `hedging.py` already computes.

---

## 4. Pack 1: LLM steps

These are not capabilities, but they sit between capabilities and shape what the capabilities
receive.

| Step | Schema | Calls | Input the model sees | Main risk | Improvement |
|---|---|---|---|---|---|
| `classify_boundaries` | `BoundaryAssignments` | 1 | ≤120 candidates with shape evidence | Single point of failure; halts by default; causes the run-to-run variance (flow.md B3) | Batch as a fan-out, add `on_fail: warn_and_continue`, run deterministic adapters first (P1.5) |
| `synthesize` | `BRDFragment` | 1 per packet | A thin packet (§2.1) | Writes requirements from names; a failed worker disappears silently (flow.md B4) | Code evidence in packets; count failed workers in the gap analysis |
| `elevate` | `TopTier` | 1 | Area digest plus graph summary | Binds by title, so paraphrases leave capabilities unattached; halts by default | Bind by CAP-id; `on_fail` fallback of areas as outcomes, flagged |
| `repair` | `RepairedItems` | ≤3 | Rejected items plus ERRORs only | Spent on errors it cannot fix; may change evidence (P1.19) | Send only repairable issues; lock the evidence fields |

The synthesis instruction is about 200 lines of prose in YAML. Moving it into the
`evidence-grounded-requirement-synthesis` SKILL.md (with `section:`) would make it versioned and
diffable, and let the skill's sha256 cover it.

---

## 5. Pack 2: testcase-generation

Manifest: [test-generation-skills/pack.yaml](../test-generation-skills/pack.yaml). Its input is
Pack 1's `requirements.json`, not the code.

| # | Capability | Step id | Module | on_fail |
|---|---|---|---|---|
| P2.1 | `ingest_requirement_set` | ingest_requirements | [testgen/ingest.py](../src/agentic/capabilities/testgen/ingest.py) | halt |
| P2.2 | `qualify_requirements` | qualify | [testgen/qualify.py](../src/agentic/capabilities/testgen/qualify.py) | halt |
| P2.3 | `validate_requirement_input` | gate_input (gate) | [testgen/gates.py](../src/agentic/capabilities/testgen/gates.py) | halt |
| P2.4 | `analyse_test_signals` | analyse_signals | [testgen/analyse.py](../src/agentic/capabilities/testgen/analyse.py) | halt |
| P2.5 | `build_test_batches` | plan_batches | [testgen/batching.py](../src/agentic/capabilities/testgen/batching.py) | halt |
| P2.6 | `assemble_test_specs` | assemble_tests | [testgen/assemble.py](../src/agentic/capabilities/testgen/assemble.py) | halt |
| P2.7 | `deduplicate_test_cases` | dedupe | [testgen/dedupe.py](../src/agentic/capabilities/testgen/dedupe.py) | warn |
| P2.8 | `analyse_test_coverage` | analyse_coverage | [testgen/coverage.py](../src/agentic/capabilities/testgen/coverage.py) | warn |
| P2.9 | `validate_test_specs` | gate_tests (gate) | [testgen/validate.py](../src/agentic/capabilities/testgen/validate.py) | repair ×4 → prune |
| P2.10 | `apply_test_repairs` | apply_test_repairs | validate.py | **halt** |
| P2.11 | `prune_test_cases` | prune_tests | validate.py | halt (default) |
| P2.12 | `validate_test_suite` | final_check | validate.py | warn |
| P2.13 | `render_test_deliverables` | render_tests | [testgen/render.py](../src/agentic/capabilities/testgen/render.py) | warn |

What the stored test runs show [verified]:

| Run | Requirements | Generate / deferred | Projected cases | Cases produced | Gate |
|---|---|---|---|---|---|
| c9c957c9 | 39 | 38 / 1 | 123 | 76 | valid, 5 warnings |
| 088c68a6 | 21 | 20 / 1 | 64 | 33 | valid, 7 warnings (3 positive-only, 3 untested) |
| b569a7bd | 19 | 16 / 3 | 55 | 32 | valid, 3 warnings |

Designers return about 55% of the budget. That is allowed ("the budget is a ceiling"), but it
means `projected_cases` overstates the suite by nearly 2×.

---

### P2.1 `ingest_requirement_set`: read Pack 1's contract defensively

**Use.** The only step that reads `source_root`. It turns `requirements.json` into a
`RequirementSet`. A malformed entry becomes a `ReadIssue` with its array position, never an
exception.

**How it works** ([ingest.py:408](../src/agentic/capabilities/testgen/ingest.py#L408))
1. `_find()` takes the shallowest `requirements.json` within depth 4.
2. If there is none, `resolve_from_source()` checks whether the given path is a *codebase* whose
   byte-identical inventory (the sha256s from P1.1) matches a stored run. If it does, that run's
   artifacts are used, and the message says so.
3. `read_requirement()` coerces each field (`_as_str`, `_as_float`, `_as_evidence`, …) and
   tolerates the legacy 20-key shape.
4. It optionally reads `unresolved_candidates.json` and `synthesis_findings.json` beside it.
5. It halts if the file is not an array or no entry could be read.

**Weak spots**
- `_find` calls `root.rglob()` over the **whole tree** and filters by depth afterwards. Pointed
  at a large repository by mistake, it walks all of it before failing [code].
- The contract is detected by missing keys, because there is no version (P1.22).

**Make it better**
- Walk only to depth 4, for example `glob` with bounded patterns or a manual walk.
- Read the envelope and `schema_version` once Pack 1 emits them.

---

### P2.2 `qualify_requirements`: should this be tested at all?

**Use.** Puts every requirement into one band, deterministically and before any model call:
`eligible`, `eligible_with_assumptions`, `needs_clarification` or `excluded`. Each gets a reason
code, and every requirement gets a record.

**How it works** ([qualify.py:270](../src/agentic/capabilities/testgen/qualify.py#L270)). The
rules are ordered, and the first match decides.

- **Excluded:** `REQ_REJECTED` → `OBSERVATION_NOT_TESTABLE` → `STATEMENT_UNUSABLE` →
  `REQ_UNRESOLVED` (title in the unresolved-rejected list) → `DUPLICATE_OF` (a
  `SequenceMatcher` ratio of 0.92 or more against earlier kept items, sped up by length bounds
  and quick ratios) → `AGGREGATE_TIER` (business).
- **Needs clarification:** `NO_OBSERVABLE_OUTCOME` (a blocking question, no usable criteria, and
  nothing beyond positive).
- **With assumptions:** `INTERNAL_BEHAVIOUR` (system) → `DERIVED_CRITERIA_ONLY` →
  `NO_ACCEPTANCE_CRITERIA` → `LOW_CONFIDENCE` (<0.5) → `UNRESOLVED_CONFLICT`.
- Otherwise the requirement is `DIRECTLY_TESTABLE`.

`usable_criteria()` drops **every** criterion of an item whose assumptions contain Pack 1's
derived-criteria sentence, and drops boilerplate criteria too.

**Weak spots**
- It depends on English marker sentences from Pack 1 (§2.6).
- If the marker is on an item, *all* its criteria are dropped, including any that the synthesis
  model wrote from real evidence before a later merge added the marker.
- `UNRESOLVED_CONFLICT` is fed by Pack 1's noisy finding (P1.21), so correctly resolved winners
  are downgraded.
- `LOW_CONFIDENCE` never fires in practice, because confidence is always 0.95 (§2.2).

**Make it better**
- Read the structured `criterion_source` and `blocking` fields (§2.6) and discard per criterion,
  not per item.
- Once confidence varies (P1.13), confidence can drive a band.

---

### P2.3 `validate_requirement_input`: the input gate

**Use.** Asks one blocking question: *is anything testable?* Everything else is a warning,
because a thin input should still produce an honest thin suite.

**How it works** ([gates.py:32](../src/agentic/capabilities/testgen/gates.py#L32)). The ERROR is
`NO_TESTABLE_REQUIREMENTS`. The warnings are read issues, more than 50% excluded, needs
clarification, more than 50% assumed derivation, generated criteria discarded, and an older
contract.

**Make it better.** This is small and sound. One addition: warn when **every** requirement has
the same confidence (a sign that P1.13 has collapsed), since several bands depend on it.

---

### P2.4 `analyse_test_signals`: dimensions, risk and budget, decided before any model call

**Use.** Stops the test count from exploding. For each requirement it decides which test
dimensions its wording activates, how risky it is, and how many cases it may have.

**How it works** ([analyse.py:216](../src/agentic/capabilities/testgen/analyse.py#L216),
[signals.py](../src/agentic/capabilities/testgen/signals.py))
1. `activated_dimensions()`: `positive` always, plus 10 English regular expressions over the
   statement and the usable criteria (negative, validation, error_handling, boundary,
   state_transition, authorization, business_rule, data_integrity, integration, exceptional).
   Rationale is never read.
2. `assess_risk()`: high if any of business rule, authorization or data integrity is activated,
   or 4 or more dimensions; low for system requirements or 1 dimension; otherwise medium.
3. `route_for()`: a system requirement without usable criteria is attached to its parent if one
   resolves, otherwise deferred. Everything else goes to generation.
4. `budget_for()`: `min(#dimensions, cap)`, where the caps are high 8, medium 5, low 2, and
   internal 2.
5. `interface_hint()`: matches `graph_path[0]` against HTTP, CLI, UI and scheduled shapes.

**Weak spots**
- **`interface_hint` is almost always `internal`** [verified]. In c9c957c9 it was 38 of 39, in
  088c68a6 20 of 21, and in 6809c505 4 of 4. `graph_path[0]` is a qualified name like
  `routers/x.py::create()`, not a boundary identifier like `POST /x`. So every case is published
  with the wrong interface, and `automation_readiness` is computed from it.
- `boundary` fires on `count`, `size`, `length` and `range`. `state_transition` fires on
  `status`, `state` and `complete`. These are common words, so budgets inflate on requirements
  that state no limit or state at all [likely].
- The `ATTACH` route is effectively dead, because Pack 1's hierarchy is sparse (the module says
  so itself: "1 of 92").

**Make it better**
- Take the interface from Pack 1's published `entry_points` (P1.22) instead of guessing it from
  `graph_path[0]`.
- Require a number or a comparison near `size`, `length` or `count` before activating
  `boundary`.
- Log which pattern activated each dimension (`activated_by: {"boundary": "at most 5"}`), so the
  budget can be reviewed and tuned.

---

### P2.5 `build_test_batches`: bounded, area-coherent packets

**Use.** One LLM call per batch. Grouping by area lets the cases in one call share
preconditions.

**How it works** ([batching.py:121](../src/agentic/capabilities/testgen/batching.py#L121)). It
groups generating signals by `area`, sorts each group by risk and then id, and cuts a batch at
**14 cases** or **6 requirements**. Each packet uses the generic fan-out keys (`boundary`,
`boundary_kind="requirement_area"`, `packet`), and the packet holds each requirement's
statement, **usable** criteria, dimensions, budget, risk and derivation default.

**Make it better**
- `batch_id` is positional (`B001…`). Hash the requirement ids in it so a rerun can reuse cached
  replies.
- Pass the requirement's evidence **summary** (not code), such as its entry point, so the
  designer can write concrete steps ("from the upload screen…") without guessing.

---

### P2.6 `assemble_test_specs`: stamp ids, derive fields, drop what cannot be traced

**Use.** Turns the workers' drafts into a `TestSuite`. The model is never trusted with ids,
traceability or derived fields.

**How it works** ([assemble.py:242](../src/agentic/capabilities/testgen/assemble.py#L242))
1. It drops scenarios for unknown requirements, or for requirements not routed to generation,
   and records a note for each.
2. Scenarios are numbered **per area** (`TS-<AREA>-NN`) in sorted requirement order. Cases are
   numbered `TC-<AREA>-NN-MM`.
3. A case whose `dimension` is not activated is dropped.
4. `covers_criterion` becomes a `CoverageLink`: a criterion, or the statement.
5. `regression_candidate` and `automation_readiness` are derived.
6. A generating requirement that got no scenario is recorded as `undesigned`.

**Weak spots**
- A case with a non-activated dimension is **dropped**. Often the model picked a neighbouring
  label (for example `validation` instead of `negative`) for a good case.
- `covers_criterion` is a single index, so a case that checks two criteria counts once.
- Ids are positional per area, so they shift if a requirement is added before others.

**Make it better**
- Remap to the closest activated dimension when the mapping is unambiguous (negative ↔
  validation ↔ error_handling), and record the remap.
- Allow `covers_criteria: list[int]`.
- Base ids on the requirement id rather than the area position.

---

### P2.7 `deduplicate_test_cases`: fold cases that check the same thing

**Use.** A duplicate is a matter of arithmetic, not judgement. Folded cases are recorded on the
survivor (`superseded`).

**How it works** ([dedupe.py:105](../src/agentic/capabilities/testgen/dedupe.py#L105)). Within
each (requirement, dimension) group, it compares `case_fingerprint` (the steps plus the expected
results, never the title) using `SequenceMatcher`. A score of 0.85 or more is folded; the keeper
is the case with a criterion link, then a direct derivation, then more results, then the lower
id. A score from 0.65 to 0.85 is reported as an overlap. The fold count is **accumulated**
across repair loops.

**Make it better**
- Also *report* near-duplicates across different requirements (without folding them), since
  they often reveal two requirements that are really one.

---

### P2.8 `analyse_test_coverage`: account for everything

**Use.** Gives each requirement a verdict (`sufficient`, `positive_only`, `thin`, `none`,
`deferred`, `excluded`), measured only against the dimensions that requirement **activated**.
It also covers every usable criterion.

**How it works** ([coverage.py:144](../src/agentic/capabilities/testgen/coverage.py#L144)).
`sufficient` requires every activated dimension and every criterion to be covered.
`positive_only` means only `positive` is covered while a negative-type dimension was expected.

**Weak spots**
- It is not re-run after `prune_tests`, so the published coverage can count pruned cases
  (flow.md B8). `validate_test_suite` notices (`COVERAGE_LOST_DOWNSTREAM`) but does not fix it.

**Make it better**
- Call `build_coverage()` inside `prune_test_cases` and `validate_test_suite` (it is a pure
  function), and write the fresh `coverage` artifact.

---

### P2.9 `validate_test_specs`: the quality gate

**Use.** Asks one question of every case: *would this fail against a broken implementation?*

**How it works** ([validate.py:217](../src/agentic/capabilities/testgen/validate.py#L217)).
Per-case ERRORs: `TC_NO_REQUIREMENT`, `TC_LINKED_TO_EXCLUDED`, `TC_NO_OBJECTIVE`,
`TC_NO_STEPS`, `TC_NO_EXPECTED_RESULT`, `TC_VAGUE_EXPECTED_RESULT` (unfalsifiable qualifiers or
empty assertions), `TC_FRAMEWORK_LEAK` (tool, language or protocol verbs, word-bounded),
`TC_RESTATES_REQUIREMENT` (≥0.82), `TC_MISSING_INPUTS` (input-bearing dimensions),
`TC_MISSING_PRECONDITION` (the case acts on something that must already exist) and
`TC_UNSUPPORTED_ASSUMPTION`. Coverage findings are warnings only.

**Weak spots**
- **`REPAIRABLE` is defined but used only for a metric** [code]. `TC_NO_REQUIREMENT` and
  `TC_LINKED_TO_EXCLUDED` go to the repair model, which is not allowed to change
  `requirement_ids`. So those errors burn all 4 repair attempts before pruning.
- `checks_run = cases × 10 + requirements` is a made-up number.
- `PRE_EXISTING` matches `updat\w*`, `delet\w*` and `remov\w*` anywhere in the title, steps or
  results, so a case that merely *mentions* an update needs a precondition.

**Make it better**
- Prune non-`REPAIRABLE` errors at once, inside the gate step or in a small `prune_unrepairable`
  step before `repair_tests`, and send only repairable ones to the model.
- Count checks for real.
- Apply `PRE_EXISTING` to the steps only, not to the expected results.

---

### P2.10 `apply_test_repairs`: merge repaired cases by id

**Use.** The Pack 2 counterpart of P1.19. Only rejected cases can change. Deleted cases become
`DeferredRequirement(route="repair_deleted")`. Afterwards the flow loops back to dedupe →
coverage → gate.

**How it works** ([validate.py:250](../src/agentic/capabilities/testgen/validate.py#L250)).
For each rejected id in the reply, it copies the **non-empty** fields among title, objective,
steps, expected results, preconditions, assumptions, derivation, notes and inputs.

**Weak spots**
- **It halts the run if the first repair call fails** [code]. When `repair_tests` raises, the
  node records the failure and continues, as it is designed to. But `ctx.load("test_repairs")`
  then raises a `KeyError`, because that artifact was never produced, and this step has
  `on_fail: halt`. Pack 1's `apply_repairs` catches exactly this case and passes everything
  through. This one does not.
- On later attempts, a stale `test_repairs` from an earlier attempt is applied again.
- "A falsy value means left alone" means a repair can never *clear* a field, for example remove
  a wrong assumption.

**Make it better**
- Wrap the load like P1.19: on `KeyError`, return the suite unchanged with a message.
- Version the repair artifact per attempt.
- Use `None` to mean "untouched" and `[]` or `""` to mean "cleared".

---

### P2.11 `prune_test_cases`: when the repair budget runs out

**Use.** Drops the cases that are still rejected and records each one as a `pruned` gap.

**Make it better.** Recompute coverage here (P2.8). That fixes the stale coverage matrix on the
exhausted path.

---

### P2.12 `validate_test_suite`: whole-suite checks at the end

**Use.** Reports duplicate case ids, cases whose scenario no longer exists, cases with no
coverage link, and `COVERAGE_LOST_DOWNSTREAM`.

**Make it better.** Once coverage is recomputed (P2.8), `COVERAGE_LOST_DOWNSTREAM` becomes a
real regression signal instead of a fixed symptom.

---

### P2.13 `render_test_deliverables`: write the test documents

**Use.** Writes `test_specs.json`, `test_plan.md`, `coverage_matrix.csv`,
`traceability_tests.csv`, `excluded_requirements.md` and `clarifications.md`. The last two
("what will not be tested" and "what must be answered first") are half the value.

**Weak spots.** It consumes the stale `coverage` on the prune path (P2.8), and it publishes the
wrong `interface_hint` on every case (P2.4).

**Make it better.** Fix those upstream. Also add a short "how to review this suite" section to
`test_plan.md`, listing `positive_only` requirements first, since they are the most misleading.

---

## 6. Pack 2: LLM steps

| Step | Schema | Input | Risk | Improvement |
|---|---|---|---|---|
| `design_tests` | `TestDesignFragment` | One area batch: statements, usable criteria, dimensions, budget | Returns about 55% of the budget; mislabelled dimensions are dropped (P2.6) | Pass the entry point and interface (P2.4); remap dimensions |
| `repair_tests` | `RepairedTestCases` | At most 12 rejected cases plus their issues | Spent on errors it cannot fix (P2.9); a failure halts the run (P2.10) | Send only repairable errors; handle a missing repair artifact |

---

## 7. Shared helper modules

These are not registered capabilities, but several capabilities depend on them.

| Module | Used by | What it does | Improvement |
|---|---|---|---|
| [verify.py](../src/agentic/capabilities/verify.py) | Gate 1, Gate 2, claims, correspondence | `verify_location`: the file resolves inside the root, the line is in range, the symbol appears within ±25 lines (**substring** match); synthetic `rule@41` symbols are checked against the line | Match the symbol on word boundaries (`\bid\b`, not a substring match, which finds `id` inside `valid`); size the cache from the inventory |
| [jargon.py](../src/agentic/capabilities/jargon.py) | Gate 2, render | Pattern classes (snake, camel, Pascal, dotted, route, path, call) plus word lists with inflections; external systems allowed for integration and NFR families; source symbols matched unless they are common words | Fix the overlap between the two lists and the business-English false positives (P1.18); pre-compile one alternation regex over `known_symbols` instead of looping regexes per symbol |
| [testgen/quality.py](../src/agentic/capabilities/testgen/quality.py) | P2.7, P2.9 | Word lists for vague results and framework leaks; similarity thresholds; precondition heuristic | Fold into the shared text module (§2.8) |
| [testgen/signals.py](../src/agentic/capabilities/testgen/signals.py) | P2.2, P2.4 | The 11 dimensions and their regular expressions | Record which pattern activated each dimension (P2.4) |

---

## 8. Improvement roadmap

Ordered by value for effort. S = under a day, M = a few days.

| Priority | Change | Capabilities | Effort | Why |
|---|---|---|---|---|
| **P0** | Attach RATIONALE docstrings to their functions, and add bounded source excerpts and `MODULE_SURFACE` literals to packets | P1.3, P1.7 | M | The model currently writes from names only (§2.1); this is the root of thin output |
| **P0** | Fix `apply_test_repairs` halting when the repair call fails | P2.10 | S | One Azure error kills an otherwise good test run |
| **P0** | Fix the false "no requirement of that name" questions | P1.16 | S | 19 of 19 wrong in e1037dbd, and they leak into Pack 2 clarifications |
| **P0** | Include the handler in boundary ids | P1.5 | S | Up to 7% of endpoints silently lost |
| **P1** | Lock `evidence` and `graph_path` in repair merges | P1.19 | S | Stops the repair model re-pointing citations |
| **P1** | Send real cited lines to entailment; chunk; missing verdict counts as unchecked; own LLM step | P1.12 | M | Entailment currently approves 100% |
| **P1** | Stop treating model `rationale` as declared policy | P1.11, P1.13 | S | Removes a false rule-promotion path and the +0.05 |
| **P1** | Rebuild confidence from varying signals and fit it on `corpus_truth` | P1.13 | M | Confidence is 0.95 on every item |
| **P1** | Bind outcomes by CAP-id via `digest_areas`, with a fuzzy fallback | P1.8–P1.10 | M | Most capabilities are unattached |
| **P1** | Mark errors as repairable in both gates, and prune unrepairable errors at once | P1.18, P2.9 | S | Wasted LLM calls |
| **P1** | Publish `entry_points` and `schema_version` in `requirements.json`; use them in Pack 2 | P1.22, P2.1, P2.4 | S | The interface hint is wrong on about 97% of cases |
| **P1** | Recompute coverage after pruning | P2.8, P2.11 | S | Stale coverage matrix |
| **P2** | Structured fields instead of English marker strings | P1.14, P1.16, P1.20, P2.2 | M | Rewording a message changes behaviour in another pack |
| **P2** | One shared text-similarity module | P1.8, P1.11, P1.12, P1.18, P2.2, P2.7 | M | Five disagreeing definitions of "same" |
| **P2** | Deterministic entry-point adapters for common frameworks | P1.4, P1.5 | M | Stability; the classifier becomes a fallback |
| **P2** | Remove or implement dead-kind branches; add contract tests per seam | P1.7, P1.11, P1.14, P1.17 | S | Code that cannot run misleads readers |
| **P3** | Performance: one file cache, prune directories during the walk, O(n) line-end sweep | P1.1, P1.3, P1.6, P1.12 | S | Large repositories |
| **P3** | Content-addressed ids for requirements and test cases | P1.17, P2.6 | M | Diffable runs and stable Pack 2 references |
| **P3** | Graphify version check and graph cache | P1.2 | S | Silent schema drift, and repeated cost |

---

## 9. Corrections to flow.md

Reading the capabilities closely showed three claims in [flow.md](flow.md) that do not hold for
the current code.

1. **B1 ("three boundary classes are silently thrown away") is not the case.**
   `entry_point_adapter()` runs `BOUNDARY_KINDS.update(adapter.declares)` when it registers the
   classified adapter, so CLI_COMMAND, EVENT_CONSUMER and REGISTERED_ENTRY *are* boundary kinds
   at run time. In run 187a4de5, `clusters.json` has 21 ENDPOINT and **8 REGISTERED_ENTRY**
   clusters, and `entry_point_coverage.boundary_kinds_with_no_adapter` is empty [verified]. The
   design is still fragile: it is a mutable global that depends on import order (see P1.5).
2. **P1.1 "hashes every file although nothing uses the hashes" is incorrect.** Pack 2's
   `resolve_from_source()` compares inventory sha256s to find the run that analysed a
   byte-identical tree.
3. **Pack 1 has 22 capabilities, not 21.** Together with Pack 2's 13, 35 are registered.

---

## 10. How the evidence was gathered

Every [verified] claim comes from the stored artifacts under `runs/<id>/artifacts/`:

| Claim | Artifact read | What was computed |
|---|---|---|
| Thin packets (§2.1) | `clusters.json` | Average and max `packet.chars`; a count of attribute keys across all `records[].n.a` |
| Confidence saturation (§2.2) | `brd.json` | Histogram of `confidence` and `validation.checked_by` |
| False parent questions (§2.3) | `requirements.json` | Items with `parent_id` whose `open_questions` contain "was filed as belonging under" |
| Boundary id collisions (§2.5) | `boundary_assignments.json`, `entry_point_coverage.json` | Duplicate `(entry_class, identifier)` pairs vs. entities added |
| Unattached capabilities (P1.10) | `requirements.json` | Items with a non-null `parent_id` |
| Interface hint (P2.4) | `test_signals.json` | Histogram of `interface_hint` |
| Pack 2 yield (§5) | `test_signals.json`, `test_specs.json`, `test_gate.json` | Projected vs. produced cases; gate codes |
| flow.md B1 correction (§9) | `clusters.json`, `entry_point_coverage.json` | Histogram of `boundary_kind` |

Claims tagged [code] come from reading the cited functions. Each one is a candidate for a
regression test before it is fixed.

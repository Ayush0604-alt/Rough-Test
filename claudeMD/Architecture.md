# Architecture — TraceBackAnalysis (`agentic-skill-packs`)

A complete, implementation-level description of the system as it exists in this repository.
Everything here was read off the source, not off the existing `MDfiles/` notes. No Mermaid: all
diagrams are plain ASCII inside this file.

---

## 0. Read this first: what the product actually is

You said you understood this as "a UI testing tool". That is not what this repository builds, and
the difference matters before you read another line.

**What it is:** a two-stage document generator.

```
   a codebase                a requirements document              test specifications
  (zip or folder)   ──►   (evidence-grounded, every claim   ──►  (technology-independent,
                           traced to file:line)                   traced to requirement ids)
        PACK 1: codebase-requirements            PACK 2: testcase-generation
```

**What it is not:**

- It never runs the target code. Nothing is executed, imported, or evaluated.
- It never runs a test. It *writes test specifications* — prose cases with preconditions, steps and
  expected results — and is explicitly forbidden from naming a framework, protocol, browser, or
  language anywhere in them (`capabilities/testgen/quality.py:FRAMEWORK_WORDS`).
- It has no browser driver, no Selenium/Playwright dependency, no assertion runner.

**Where the "UI testing" idea comes from:** the test specification layer is deliberately built so
that a *later, separate pack* ("framework-specific adapters are a later, separate pack" —
`test-generation-skills/pack.yaml`, line 13) could turn one specification into a Playwright test, a
pytest test, an API test, or a manual script. `TestCase.interface_hint` can hold `ui_action`, and
`AutomationReadiness` says what stands between the spec and automation. That adapter pack **does
not exist in this repository**. If your assignment is "the UI testing tool", your work is most
likely that missing third pack — and the whole architecture below exists to make adding it a
data-and-capabilities change rather than an engine change.

---

## 1. The thesis the architecture is built around

One sentence: **the workflow is data, not code.**

A *skill pack* is a directory containing `SKILL.md` files plus a `pack.yaml` manifest. The manifest
compiles into a LangGraph `StateGraph`. Adding a new analysis capability means adding a directory
of markdown and YAML plus registering Python functions by name — it does not mean editing the graph
builder, the state, the runtime, or the UI.

```
  pack.yaml ──parse/validate──► PackManifest ──compile──► StateGraph.compile() ──► run
                                     │
              resolves every named reference at LOAD time, not run time
                                     │
        ┌────────────────┬───────────┴───────────┬─────────────────┐
        ▼                ▼                       ▼                 ▼
  SkillRegistry    CAPABILITIES dict        SCHEMAS dict      StepMode/OnFail
  (SKILL.md on     (@capability("name"))    (register_schema)  (routing policy)
   disk, sha256)
```

The architecture's own acceptance test is `tests/test_extensibility.py`, which *authors a brand new
pack at runtime* (two `SKILL.md` files + a `pack.yaml`), registers two capabilities, compiles it and
runs it. If that test ever needed a change under `src/agentic/graph/` to pass, the abstraction has
leaked.

Second thesis, equally load-bearing: **the model never sees source code and never decides control
flow.** It sees bounded "evidence packets" built from a knowledge graph, and routing is pure Python
reading the manifest and the state.

---

## 2. Physical layout

```
TraceBackAnalysis/
├── app.py                         Streamlit UI (single file, ~780 lines)
├── pyproject.toml                 packaging; `agentic` console script; pytest config
├── .env                           Azure OpenAI creds + optional toggles (not committed)
│
├── codebase-requirement-skills/   PACK 1 — 7 SKILL.md dirs + pack.yaml (626 lines of YAML)
├── test-generation-skills/        PACK 2 — 5 SKILL.md dirs + pack.yaml (291 lines of YAML)
│
├── src/agentic/
│   ├── config.py                  Settings (pydantic-settings), all tunables
│   ├── state.py                   AgentState TypedDict + channel reducers
│   ├── store.py                   ArtifactStore — JSON/text on disk under the run dir
│   ├── runtime.py                 prepare_run / run / stream_run / pack discovery / preflight
│   ├── cli.py                     `agentic packs|show|run`
│   │
│   ├── skills/
│   │   ├── pack.py                PackManifest, StepSpec, StepMode, OnFail, discover/load/validate
│   │   └── registry.py            SkillRegistry, LoadedSkill (frontmatter, body, sha256, section())
│   │
│   ├── graph/
│   │   ├── builder.py             manifest → StateGraph (names no step, skill or artifact)
│   │   ├── nodes.py               3 node factories + halt + finalise
│   │   └── router.py              linear / gate / fan-out routers — deterministic
│   │
│   ├── llm/
│   │   ├── client.py              AzureChatOpenAI construction, lru_cached
│   │   ├── prompt.py              system prompt assembly + per-artifact evidence renderers
│   │   └── steps.py               the single generic LLM step executor (retry, schema-enforced)
│   │
│   ├── schemas/
│   │   ├── core.py                Entity/Relationship/KnowledgeGraph/SourceLocation/GateReport
│   │   ├── brd.py                 PACK 1 output model + the whole classification taxonomy
│   │   ├── boundaries.py          boundary classification LLM schema
│   │   ├── testgen.py             PACK 2 *input* model (defensive reader of requirements.json)
│   │   ├── testspec.py            PACK 2 output model (scenarios, cases, coverage)
│   │   └── registry.py            name → pydantic model, for `output_schema:` in a manifest
│   │
│   └── capabilities/              ~19 modules, PACK 1
│       ├── registry.py            @capability decorator, CapabilityContext, CapabilityResult
│       ├── ingest.py  graphify.py  graph_ops.py  boundaries.py  entry_points.py
│       ├── clustering.py  assemble.py  consolidate.py  correspondence.py  confidence.py
│       ├── leakage.py  hedging.py  resolution.py  claims.py  jargon.py
│       ├── gates.py  verify.py  quarantine.py  render.py
│       └── testgen/               12 modules, PACK 2
│           ├── ingest.py  qualify.py  signals.py  analyse.py  batching.py
│           ├── assemble.py  dedupe.py  coverage.py  quality.py  validate.py
│           ├── gates.py  render.py
│
├── tests/                         ~40 test modules, fixtures corpus, metrics harness
│   ├── fixtures/corpus/           py_service, py_cli, py_worker, go_service (+ ground truth)
│   └── harness/                   frozen-baseline metric harness (`python -m tests.harness`)
│
├── runs/                          generated: one dir per run + checkpoints.db
└── MDfiles/                       earlier, hand/AI-written notes (not the source of truth)
```

---

## 3. Layer map

```
┌──────────────────────────────────────────────────────────────────────────────┐
│ ENTRY LAYER            app.py (Streamlit)          cli.py (argparse)         │
│                             └──────────┬──────────────────┘                  │
├────────────────────────────────────────▼─────────────────────────────────────┤
│ RUNTIME LAYER          runtime.py                                            │
│   prepare_run()  ── copy/extract target into runs/<uuid>/source              │
│   available_packs() / get_manifest() / preflight() / successor_packs()       │
│   run() / stream_run() ── opens SqliteSaver, compiles graph, invokes         │
├────────────────────────────────────────┬─────────────────────────────────────┤
│ COMPILATION LAYER      skills/pack.py ──┴──► graph/builder.py                 │
│   PackManifest (validated)                  add_node per step                │
│   StepSpec: mode, skill, section,           add_conditional_edges per step    │
│     capability|output_schema, fan_out,      + __halt__ and __finalise__       │
│     consumes/produces, on_fail, next        set_entry_point(steps[0])         │
├──────────────────────────────────────────────────────────────────────────────┤
│ EXECUTION LAYER        graph/nodes.py + graph/router.py                      │
│   capability node  ──► CAPABILITIES[name](ctx) ──► CapabilityResult          │
│   llm node         ──► llm/steps.run_llm_step(ctx) ──► pydantic model        │
│   routers          ──► next node id, or list[Send] for fan-out               │
├──────────────────────────────────────────────────────────────────────────────┤
│ WORK LAYER            capabilities/*  (pure Python, testable in isolation)   │
│                       llm/*          (the only place a model is called)      │
├──────────────────────────────────────────────────────────────────────────────┤
│ DATA LAYER            state.py  (small: ids, paths, counters, gate reports)  │
│                       store.py  (big: graphs, BRDs, suites, rendered docs)   │
└──────────────────────────────────────────────────────────────────────────────┘
```

The split between the last two boxes is deliberate and is stated in `state.py`'s docstring:
LangGraph checkpoints the *entire state* after every superstep, so a multi-megabyte knowledge graph
held inline would be re-serialised at every node. State carries **paths**; `ArtifactStore` carries
**payloads**.

---

## 4. The core engine, mechanism by mechanism

### 4.1 `AgentState` — the shared channel set

`src/agentic/state.py`. A `TypedDict(total=False)`. Nothing in it mentions requirements or tests.

| Channel | Type | Reducer | Purpose |
|---|---|---|---|
| `run_id`, `pack`, `run_dir`, `source_root`, `repository_name` | `str` | last-write | run identity |
| `artifacts` | `dict[str,str]` | `merge_dict` | logical name → path on disk |
| `completed_steps` | `list[str]` | `operator.add` | progress |
| `step_records` | `list[StepRecord]` | `operator.add` | audit trail (step, skill, **skill_sha256**, mode, status, ms, messages) |
| `metrics` | `dict` | `merge_dict` | per-step numbers, keyed `step:metric` |
| `fragments` | `list[dict]` | `operator.add` | fan-out worker outputs — the only many-writer channel |
| `work_item` | `dict\|None` | last-write | set per-branch by the fan-out dispatcher; read never written |
| `gate_reports` | `dict[str,dict]` | `merge_dict` | keyed by gate step id |
| `repair_attempts` | `int` | last-write | bounds the repair loop |
| `status`, `halt_reason` | — | last-write | terminal condition |

`merge_dict` is a shallow last-writer-wins merge; it is safe because parallel branches write
disjoint keys by construction (each worker writes only to `fragments` and `metrics[step:…]`).

### 4.2 `ArtifactStore`

`src/agentic/store.py`. Trivial by design:

- `save(name, value)` → `runs/<id>/artifacts/<name>.json`, pydantic dumped in JSON mode; returns path.
- `save_text(relative_name, text)` → rendered deliverables verbatim (`.md`, `.csv`, `.mmd`).
- `load_json` / `load_model(path_or_name, Model)`.

Naming convention: `artifacts["brd"]` is the *state key*; the file is `artifacts/brd.json`.
Rendered documents are recorded in state under `file:<name>` keys (see `nodes._apply`).

### 4.3 `SkillRegistry` and `LoadedSkill`

`src/agentic/skills/registry.py`.

- Discovery: `<pack_root>/<skill-name>/SKILL.md`. **The directory name is authoritative**, not the
  frontmatter `name:`.
- Cache keyed on file **mtime** → editing a `SKILL.md` takes effect on the next run with no restart.
  This is the entire point of keeping instructions on disk.
- `LoadedSkill.sha256` is the hash of the raw file including frontmatter. It is written into every
  `StepRecord`, so a deliverable can state *which exact version of which instructions produced it* —
  including for deterministic steps, where the skill is implemented in Python. Drift between the
  markdown and the code is therefore auditable rather than invisible.
- `LoadedSkill.section("Gate 2")` extracts one Markdown section by heading substring, so a manifest
  step can inject just the relevant part of a multi-part skill. A miss falls back to the whole body
  (a typo must not silently empty the instructions).

### 4.4 `PackManifest` / `StepSpec`

`src/agentic/skills/pack.py`. Three step modes and three failure policies:

```
StepMode.DETERMINISTIC   runs a registered capability
StepMode.GATE            runs a capability that must return a GateReport
StepMode.LLM             calls the model against the step's skill + output_schema

OnFail.HALT              stop with an honest failure report   (default)
OnFail.REPAIR            route to repair_step, bounded by max_repairs, then exhausted_step
OnFail.WARN              record warnings and continue
```

Validation at **load** time (`_check_steps` + `_resolve_references`), not run time:

1. no duplicate step ids;
2. every `next` / `repair_step` / `exhausted_step` names a real step;
3. `on_fail: repair` requires a `repair_step`;
4. **a step may only `consume` what an earlier step `produces`** — this is what lets
   `CapabilityContext.artifact_path` treat a runtime miss as a genuine defect and raise;
5. `fan_out` must name a previously-produced artifact;
6. every `skill`, `capability` and `output_schema` resolves.

A manifest typo therefore fails before ingestion, not twenty minutes into a run.

Pack-to-pack chaining is declared by the **downstream** pack:
`consumes_pack: codebase-requirements` + `consumes_artifact: requirements.json`. Nothing upstream
knows the successor exists. `runtime.successor_packs()` walks every pack on disk, matches
`consumes_pack`, and additionally checks the required artifact *is present on disk* — so a run that
halted before publishing is never offered as an input.

### 4.5 Graph compilation

`src/agentic/graph/builder.py`. ~90 lines, and it names no step, skill or artifact.

```
for each step:               add_node(step.id, make_node(step, manifest, settings,
                                                         is_repair_target=...))
add_node("__halt__"), add_node("__finalise__")
set_entry_point(steps[0].id)

for each step, decide its outgoing edge:

  (a) the NEXT step is a fan-out step
        add_conditional_edges(step, make_fanout_router(...), {fanout_step, beyond, __halt__})
        ── dispatch happens from the PREDECESSOR, because Send() must be returned by a router

  (b) THIS step is the fan-out step
        add_edge(step, next)                      ── a plain edge is the fan-in barrier:
                                                     all workers must finish first, and one
                                                     failed worker must not stop the others

  (c) THIS step is a gate
        add_conditional_edges(step, make_gate_router(...),
                              {next, repair_step?, exhausted_step?, __halt__})

  (d) anything else
        add_conditional_edges(step, make_linear_router(...), {next, __halt__})

add_edge(__halt__, END); add_edge(__finalise__, END)
```

`describe_topology(manifest)` renders the same information as a table for the UI and
`agentic show <pack>`.

### 4.6 Routing — deterministic by construction

`src/agentic/graph/router.py`. The design rule, stated in the module docstring: *a gate must not be
reasoned past*. A supervisor LLM choosing the next node could route around a failed gate because
continuing looked more helpful. Here the router reads only the manifest and the state, so it
structurally cannot.

```
linear:  halted? → __halt__          else → successor

gate:    halted? → __halt__
         report.valid (default True) → successor
         on_fail WARN                → successor
         on_fail REPAIR and attempts < max_repairs → repair_step
         on_fail REPAIR and exhausted             → exhausted_step or __halt__
         otherwise                                → __halt__

fanout:  halted? → __halt__
         load the fan_out artifact (a JSON list)
         empty/missing → skip the worker entirely, go to the step after it
         else → [Send(worker_id, {**state, "work_item": item}) for item in items]
```

Note the repair counter is incremented **inside the repair node** (`nodes.make_llm_node`,
`is_repair_target=True`), because a router cannot write state. If it were not, the bound would never
bind. The counter is incremented even when the repair *call fails*, so the loop still terminates.

### 4.7 Node factories

`src/agentic/graph/nodes.py`. Every node does the same four things: build a context, run the step,
persist what it produced, record what happened.

```
_context(state, step, manifest, settings) -> CapabilityContext(
     state, step, settings,
     store = ArtifactStore(run_dir),
     skill = manifest.skill_registry().load(step.skill),   # loaded fresh, per node, per run
     payload = {"manifest": manifest, "work_item": state.get("work_item")})

_apply(result, ctx, step) -> state update:
     result.artifacts {name: value}  → store.save(name, value); artifacts[name] = path
     result.files     {name: path}   → artifacts["file:"+name] = path
     result.fragment                 → fragments += [fragment]
     result.gate                     → gate_reports[step.id] = gate.model_dump()
     result.metrics                  → metrics[step.id] = {...}
```

Failure handling is where the real design lives:

| Situation | Behaviour |
|---|---|
| capability raises, `on_fail: halt` | record a `failed` StepRecord **with the traceback**, set `status=failed` + `halt_reason` |
| capability raises, `on_fail: warn_and_continue` | record it, continue — "optional" must hold when it raises, not only when it returns a warning |
| LLM step raises **and is a fan-out worker** | record and continue; one boundary failing must not discard every other boundary's analysis |
| LLM step raises **and is a repair target** | record, count the attempt, continue — "a recovery step must not be able to destroy the run it exists to rescue" |
| a gate returns `valid=False` | status `failed` on the record; the *router* decides what that means |

`__halt__` writes `artifacts/run_failed.md` summarising the failing gate's first three ERROR issues
plus the steps that completed. `__finalise__` sets the terminal status: `passed` unless any step
recorded `passed_with_warnings` or any gate was invalid, in which case `passed_with_warnings`.

### 4.8 Capability contract

`src/agentic/capabilities/registry.py`.

```python
@capability("build_boundary_clusters")
def build_boundary_clusters(ctx: CapabilityContext) -> CapabilityResult: ...
```

`CapabilityContext` exposes exactly: `state`, `step`, `settings`, `store`, `skill`, `payload`,
plus `source_root`, `run_dir`, `artifact_path(name)`, `load(name, Model=None)`. Capabilities read
prior work through `store`, never through graph state, which keeps them unit-testable — build a
context around a temp dir and call the function, which is what most of `tests/` does.

`CapabilityResult` fields: `artifacts`, `files`, `metrics`, `messages`, `gate`, `fragment`,
`halt_reason`. `halt_reason` lets a deterministic step stop the run with an explanation instead of
raising.

`load_builtin_capabilities()` is called lazily once from `get_capability()` and imports the
capability modules purely for their decorator side effects.

### 4.9 The single LLM step

`src/agentic/llm/steps.py` — there is exactly **one** worker for every LLM step in every pack. What
differs is the skill loaded, the packet assembled, and the schema enforced, all named by the
manifest.

```
schema  = SCHEMAS[step.output_schema]
model   = AzureChatOpenAI(...).with_structured_output(schema)   # provider-enforced, not parsed
messages = [system: master_skill + step skill (optionally one section) + step.instructions
                    + a fixed "Output" rule block,
            human:  json.dumps(evidence packet)]

for attempt in 1..3:
    ValidationError            → retry (shape was wrong)
    length-limit failure       → append SHORTEN_INSTRUCTION and retry with a smaller ask
    any other provider error   → raise RuntimeError naming the step and attempt
```

The length-failure path matters: a truncated *structured* reply cannot be parsed at all, so the
whole cluster is lost rather than shortened — and for a fan-out worker that loss is silent unless
something asks again for less. `SHORTEN_INSTRUCTION` explicitly caps the retry at "three functional
requirements, three business rules, two success criteria".

Output routing: a fan-out step's result becomes a `fragment` (`{cluster_id, boundary, **model
output}`) on the shared channel; a non-fan-out step's result is saved under every name in
`step.produces`.

### 4.10 Prompt assembly and the evidence-packet discipline

`src/agentic/llm/prompt.py`. This module is where the cost model is enforced.

System prompt = `master_skill` body (governs every LLM step in the pack) + this step's skill body or
one section of it + `step.instructions` from the manifest + a fixed block about citing only what is
in the evidence, and about what `symbol` means.

User message = **an evidence packet**, never source code. It is built from `step.consumes`, and
every artifact name can register a *renderer* that decides how much of it the model may see:

| Artifact | Renderer behaviour |
|---|---|
| `graph` | **summary only** — counts, entity-kind histogram, coverage, and up to 4 documentation outlines. The whole graph entering a prompt is precisely the failure this architecture exists to prevent. |
| `boundary_candidates` | passed through (already capped at 120 and scalar-valued) |
| `clusters` | boundary titles only — the packets themselves go to workers via `Send` |
| `test_batches` | batch identities only, for the same reason |
| `requirement_gate` | only the ERROR issues — a repair step needs what failed, not what passed |
| `brd` | **only the rejected claims** (`items_to_fix`), never the whole document |
| `test_suite` | only the rejected cases, and at most `MAX_REPAIR_CASES = 12` per call |
| anything unregistered | JSON, truncated at `MAX_ARTIFACT_CHARS = 60_000` with `truncated: true` |

The `brd` and `test_suite` renderers carry long docstrings explaining the bug they exist to prevent:
handing a repair step the whole document blew the input cap (so the model saw a *truncated preview*)
and blew the 8k output ceiling (so a fraction came back and the rest was silently gone) — meaning a
rejection over one phrase could delete most of the analysis.

### 4.11 Settings

`src/agentic/config.py` (`pydantic-settings`, reads `.env`). The tunables that shape behaviour:

| Setting | Default | Meaning |
|---|---|---|
| `runs_dir` | `runs` | **relative** — run output is cwd-dependent |
| `max_archive_bytes` / `max_uncompressed_bytes` / `max_archive_files` / `max_compression_ratio` | 50 MB / 200 MB / 10 000 / 100× | zip-bomb defence |
| `graphify_binary`, `graphify_timeout_seconds` | `""`, 600 | external extractor |
| `azure_openai_*` (4 values) | `""` | all four required; `llm_configured` is all-or-nothing |
| `llm_temperature` / `llm_timeout_seconds` / `llm_max_output_tokens` | 0.0 / 180 / 8000 | |
| `evidence_packet_chars` | 40 000 | per-packet budget for the fan-out worker |
| `max_clusters` | 80 | how many interaction boundaries are analysed at all (was 12) |
| `max_repairs` | 2 | overridden per-step by the manifest (3 for requirements, 4 for tests) |
| 13 consolidation toggles | all `True` | each pass can be switched off individually to measure its effect |

The 13 booleans (`stratified_cluster_selection`, `entry_point_adapters`, `declaration_clusters`,
`rule_classification`, `collapse_parameterised`, `dedup_pass`, `confidence_recalibration`,
`hedging_pass`, `semantic_clustering`, `leakage_review`, `candidate_resolution`,
`correspondence_validation`, `correspondence_entailment`) are read defensively inside their
capabilities with `getattr(ctx.settings, name, True)`, so a capability still works against a
`Settings` object that does not have the flag.

---

## 5. Run lifecycle, end to end

```
 UI/CLI
   │  pack name + (folder path | uploaded .zip)
   ▼
 runtime.prepare_run()
   ├── run_id = uuid4()
   ├── run_dir = runs/<run_id>/            (mkdir)
   ├── zip  → ingest.extract_zip_safely(stream, run_dir/source, settings)
   └── dir  → shutil.copytree(origin, run_dir/source)      ← a SNAPSHOT, so the run is
                                                             reproducible and the target cannot
                                                             change under the analysis
   ▼
 runtime.preflight(manifest, settings)
   └── any LLM step + incomplete Azure config → refuse before any work is done
   ▼
 runtime.stream_run()
   ├── SqliteSaver.from_conn_string(runs/checkpoints.db)
   ├── compile_graph(manifest, settings, checkpointer=saver)
   ├── config = {"configurable": {"thread_id": run_id}, "recursion_limit": 60}
   └── for event in graph.stream(state, stream_mode="updates"): yield (node, update)
   ▼
 UI accumulates updates into a plain dict (list channels appended, dict channels merged)
   ▼
 on success, successor_packs(pack, run_dir) → offer the next pack, pre-pointed at artifacts/
```

Checkpointing is per `thread_id = run_id`, so a Streamlit rerun resumes rather than restarts.

---

## 6. PACK 1 — `codebase-requirements`

26 steps. Master skill: `codebase-to-requirements-reverse-engineering` (prepended to every LLM
step). Four LLM steps in the manifest: `classify_boundaries`, `synthesize` (fan-out),
`elevate`, `repair`. Everything else is deterministic Python.

### 6.1 The pipeline

```
 PHASE 0  ingest ─────────────────────────────── ingest_repository        (halt)
              inventory: path, ext, language, size, sha256, parseable
              FAILS CLOSED when nothing is in a parseable language

 PHASE 1  graphify_extract ──────────────────── graphify_extract          (halt)
              subprocess: graphify extract <src> --code-only --no-cluster
                          --no-gitignore --out <run>/graphify
          adapt_graph ────────────────────────── adapt_graphify_graph     (halt)
              graph.json → KnowledgeGraph (kinds classified, line-ends derived,
              module-surface nodes synthesised, FILE entity per inventoried file)
          propose_boundaries ─────────────────── propose_boundary_candidates (halt)
              in-degree-0-on-CALLS + registration signals, ≤120 candidates
          classify_boundaries ────────────── LLM ─ BoundaryAssignments
              names a CLASS for each candidate; never a location
          discover_entry_points ──────────────── discover_entry_points    (warn)
              adapters fold assignments into the graph as boundary entities
              + EXPOSES edges; emits an adapter coverage report

 PHASE 2  gate_graph ───────────────────── GATE ─ validate_graph          (halt)

          plan_clusters ──────────────────────── build_boundary_clusters  (halt)
              one bounded evidence packet per boundary neighbourhood

 PHASE 3  synthesize ──────────── LLM FAN-OUT ─ BRDFragment  (one call per cluster)
          cluster_candidates ─────────────────── cluster_capabilities     (warn)
          digest_areas ───────────────────────── digest_areas             (halt)
          elevate ──────────────────────── LLM ─ TopTier   (titles in, outcomes out)
          assemble ───────────────────────────── assemble_requirements    (halt)

          ── consolidation band, all deterministic ──
          consolidate ────────────────────────── consolidate_requirements (halt)
          check_correspondence ───────────────── validate_correspondence  (warn)
          score_confidence ───────────────────── recalibrate_confidence   (warn)
          strip_implementation ───────────────── review_implementation_leakage (warn)
          resolve ────────────────────────────── resolve_candidates       (warn)
          derive_criteria ────────────────────── hedge_requirements       (warn)

          prepare_claims ─────────────────────── prepare_claims           (halt)

 PHASE 4  gate_requirements ────────────── GATE ─ validate_requirements
               pass       → final_check
               fail       → repair (max 3)
               exhausted  → quarantine
          repair ───────────────────────── LLM ─ RepairedItems
          apply_repairs ──────────────────────── apply_repairs → prepare_claims (loop)
          quarantine ─────────────────────────── quarantine_requirements → final_check
          final_check ────────────────────────── validate_synthesis       (warn)

 PHASE 5  render ────────────────────────────── render_deliverables       (warn)
```

The repair loop is the only cycle in the graph:

```
      ┌──────────────────── apply_repairs ◄──── repair ◄──┐
      ▼                                                    │  (attempts < 3)
 prepare_claims ──► gate_requirements ─────────────────────┘
                          │ pass                │ budget spent
                          ▼                     ▼
                      final_check  ◄────── quarantine
```

### 6.2 Phase 0 — ingestion (`capabilities/ingest.py`)

Archive validation runs across the **whole member list before a single byte is written**, so a
malicious entry late in the archive cannot leave a partially-extracted tree behind. Rejected:
absolute paths, `..` traversal, drive-qualified paths, duplicate paths (case-insensitive), encrypted
entries, symlinks, per-entry compression ratio above 100×, cumulative size above the cap, and a
resolved target outside the destination root.

Inventory: one walk, classifying every file by extension. `IGNORED_DIRECTORIES` skips VCS, editor
state, `node_modules`, `.venv`, build output, caches. `sha256` is computed for files ≤ 8 MB and
recorded as `skipped:<size>` above that.

`PARSEABLE_LANGUAGES` is the deep-analysis set (Python, JS/TS, Java, Kotlin, Go, Rust, C#, C/C++,
Ruby, PHP, SQL). **If nothing is parseable the run halts** rather than guessing requirements from
file names. This was previously `{"Python"}` — the entire point of the Graphify swap was to widen
this door.

### 6.3 Phase 1 — extraction (`capabilities/graphify.py`, 1028 lines)

Two capabilities, deliberately split so a failure is attributable: `graphify_extract` knows nothing
about the canonical schema; `adapt_graphify_graph` knows nothing about subprocesses.

What Graphify actually emits (measured against graphifyy 0.9.52) and the three consequences:

```
{"id":"app_main_audit_region","label":"audit_region()","file_type":"code",
 "source_file":"app/main.py","source_location":"L26","_callable":true,"_origin":"ast"}
```

1. **There is no `kind` field.** `classify_kind()` derives it, ordered most-certain first:
   `file_type == "rationale"` → RATIONALE; no `source_file` → EXTERNAL_SYMBOL; label is a path
   suffix of `source_file` → FILE; `_callable_class` → CLASS; label ends `()` → METHOD (leading dot)
   or FUNCTION; `_callable` → FUNCTION; else SYMBOL. The label-suffix test is checked **before**
   `_callable` because the Go grammar reports `_callable: false` for `func` — classifying on the
   flag alone put every Go function under SYMBOL, excluded it from call traversal, and produced an
   empty document.
2. **`source_location` is a start line, never a range.** `_derive_line_ends()` reconstructs ends in
   two passes: a sibling bound (a definition cannot extend past the next one; the last runs to EOF),
   then a containment repair iterated to a fixed point (a class must extend to the end of its last
   method). RATIONALE nodes are bounded separately because a docstring is *inside* the definition it
   describes, not a sibling of it — counting them as siblings truncated every documented function to
   the lines above its own docstring. The result is deliberately an over-approximation and is
   labelled `line_end_estimated: True`.
3. **Relation mapping.** `RELATION_MAP` normalises Graphify's vocabulary; anything unknown falls
   through to `RELATED_TO`, which no workflow walk follows. The important entry is
   `indirect_call → REGISTERS`: a function *handed somewhere as a value* rather than invoked.

`_verifiable_symbol()` decides what may be asserted as appearing on the cited line — FILE and
RATIONALE assert nothing, and neither does anything path-shaped or containing a space, because a
wrong symbol invalidates an otherwise sound citation while an absent one costs nothing.

**Module-surface synthesis** (`_module_surface`, `_add_module_surface`) closes a total gap: a
script-style UI (Streamlit, Gradio, a notebook-shaped app) declares its entire interaction surface as
top-level widget calls, which a definition-modelling extractor sees as nothing at all. The detection
is a *shape*, not a framework list — a module-level line whose call result is **consumed** (assigned,
or tested in a condition) and which carries a string literal, calling something the file did not
define and did not compute for itself:

```
uploaded = st.file_uploader("Choose a medical PDF")   ← consumed → a MODULE_SURFACE node
if st.button("Add to Index"):                         ← consumed → a MODULE_SURFACE node
st.success("Done!")                                   ← discarded → not a surface
RETENTION_DAYS = int(os.getenv("X"))                  ← ALL-CAPS constant → excluded
```

Each surface node is wired by inferred `CALLS` edges to functions named in the block below it,
within `SURFACE_WORKFLOW_WINDOW = 14` lines or until the next control.

`_assert_structurally_sound()` raises `AdapterError` on a duplicate id or a dangling edge — both are
defects in this module (ids are content-addressed, edges are built from an id map) and not findings
about the repository, so Gate 1 no longer checks for them.

### 6.4 Phase 1c/1d — finding interaction boundaries

This is the highest-leverage decision in the pipeline: an entry point class nobody detects is a
class of capability nobody documents.

`capabilities/boundaries.py` owns the deterministic half. The primary signal is **in-degree zero on
CALLS** — a handler is invoked by a framework, a runtime, or a person, never by the code around it,
so on a repository-local call graph a route handler, a CLI command, a scheduled job and a UI callback
all share that property. This is a shape, not a framework list, and it holds for Express, Spring,
Sinatra, Flask, gin and frameworks nobody has heard of. It over-collects (dead code and public
library functions look identical) which is exactly why the classifier is given `NONE` and told most
candidates are not boundaries.

Per candidate it attaches: in/out call degree, the undefined names it references (this is where a
decorator or annotation shows up — Graphify emits `@app.post(...)` as a `REFERENCES` edge to an
EXTERNAL_SYMBOL named `post`), who registers it, and **string literals within ±3 lines of the
definition and of every registration site**. That literal window is the only place this module reads
source, and it is what lets a boundary be called `POST /orders` instead of the name of the function
behind it. Candidates are ranked (references, registrars, out-degree) and capped at 120.

`classify_boundaries` (LLM, schema `BoundaryAssignments`) returns `{qualified_name, entry_class,
identifier?, effect?, rationale?}`. **The model is never trusted about where anything is** — it names
a node, and `ClassifiedBoundaryAdapter` resolves that name against the graph and takes the location
from the entity it found. A fabricated name resolves to nothing and is dropped, which makes a
hallucinated candidate harmless rather than a fabricated citation. Boundaries created this way carry
`confidence 0.55` and `provenance INFERRED`, deliberately lower than a syntactic detector would have
been.

`capabilities/entry_points.py` is the adapter seam: `@entry_point_adapter` registers an adapter, and
**registration also extends `BOUNDARY_KINDS`** with the classes it declares. Those two facts belong
together — "a class the planner will not seed a cluster from is a class whose adapter cannot produce
a requirement", which is how `SCHEDULED_JOB` came to be declared for years with nothing able to
produce one. The step emits `entry_point_coverage`, naming *classes declared but empty* and *boundary
kinds no adapter claims at all*, so a missing paradigm is a line in a report rather than a silence.

### 6.5 Phase 2 — Gate 1 (`capabilities/gates.py:validate_graph`)

Eight checks over the graph and the file system.

| Code | Severity | Condition |
|---|---|---|
| `SELF_RELATIONSHIP` | warning | edge starts and ends at one entity |
| `UNRESOLVABLE_LOCATION` | **error** | any entity's location fails `verify_location` against the real file system |
| `ORPHAN_BOUNDARY` | **error** | a boundary entity with no inbound `EXPOSES` |
| `LOW_EXTRACTION_COVERAGE` | warning | < 50 % of cited files yielded a definition |
| `HIGH_AMBIGUITY` | warning | > 30 % of relationships are `AMBIGUOUS` |
| `NO_FILE_ENTITIES` | **error** | no FILE entity at all |
| `NO_INTERACTION_BOUNDARY` | warning | none found — may legitimately be a library |
| `INFERRED_BOUNDARY_SHARE` | warning | > 50 % of boundaries were classified rather than parsed (the *normal* state under Graphify — the warning exists so the gap analysis says which document rests on judgement) |

### 6.6 The cost model — `plan_clusters` (`capabilities/graph_ops.py`)

This is the step that makes spend linear in the number of interaction boundaries rather than in the
size of the codebase.

```
 boundary_ids()            every entity of a BOUNDARY_KIND, plus anything that EXPOSES one
     │
 workflow_from(b)          BFS along WORKFLOW_EDGE_KINDS
     │                     = CALLS, ENFORCES, PERFORMS, HANDLES, READS_CONFIG,
     │                       EXPOSES, TRIGGERS, REGISTERS
     │                     max_depth 6, max_nodes 250   (was 4 / 120)
     │                     a boundary that declares no workflow of its own first steps
     │                     BACKWARDS across EXPOSES to whatever serves it
     │
 + declared_policy()       module-level configuration in scope for those members, which no
     │                     workflow walk reaches
     │
 group by signature        several boundaries routinely share one workflow (two routes on one
     │                     handler, an upload control and the button that consumes it) → one
     │                     packet, one call, `also_exposed_by` names the rest
     │
 _stratified(groups, 80)   SAMPLE, do not truncate — see below
     │
 + declaration_groups()    a separate ¼ budget for files that DECLARE policy (a schema, a
     │                     stated limit) that no call path reaches
     │
 build_packets(...,        serialise into as many 40 000-char packets as it takes,
   char_budget=40_000,     max 6 parts; each part is dispatched as its own cluster
   max_parts=6)            (`<id>#1`, `<id>#2`, …)
```

`_stratified` is worth understanding because it fixes a categorical omission. Boundaries of one kind
routinely carry the same degree, so the primary sort key is constant and the ordering collapses to
the alphabetical tiebreak on `qualified_name` — and where that name begins with a class
discriminator (an HTTP verb, a queue name), the list arrives grouped by class and a plain `[:limit]`
removes *whole classes*. In the run that exposed this, 147 state-changing endpoints were never
planned because they sorted after the reads. The replacement allocates the budget proportionally
with a floor of one per class, largest-remainder first, and reports every class that was **starved**
(zero clusters) or **thinned** (some).

Packet content is `compact_entity()`: kind, qualified name, `file:Lstart-Lend:symbol`, provenance,
confidence, and a per-kind whitelist of attributes with per-key truncation budgets (docstrings and
descriptions get 1200 chars because prose that states intent earns more than an incidental value),
plus up to 12 outgoing workflow edges.

### 6.7 Phase 3a — `synthesize` (the fan-out)

One model call per cluster, in parallel, each seeing one packet and nothing else. Output schema
`BRDFragment`. The manifest's `instructions:` block for this step is ~190 lines of YAML and is
effectively a second skill — it defines the *abstraction test*:

```
  ask, in order, stop at the first that is true          →  becomes
  1. business_outcome         (written later, not here)  →  business
  2. external_capability                                 →  functional
  3. internal_behaviour                                  →  system
  4. domain_constraint                                   →  business_rule
  5. platform_constraint                                 →  technical_constraint
  6. quality_property                                    →  non_functional
  7. implementation_detail                               →  observation   (kept, not published)
```

The split between `abstraction_basis` (the grounds) and `requirement_type` (the tier) is the whole
design (`schemas/brd.py`). Asked only for a type, the taxonomy offered nowhere to put a behaviour
that was interesting but not required, and discarding it would lose real evidence — so every
internal helper got promoted to a functional requirement. Giving `implementation_detail` a
destination (`observation`) makes over-extraction stop being the path of least resistance.

A fragment also carries `area_code`/`area_name` (the part of the product), `areas[]`, and the
supplementary families `data_requirements`, `integration_requirements`,
`non_functional_requirements`, `reporting_requirements`. It deliberately has **no** business
requirement list and no product purpose: one boundary cannot establish what a whole product is for.

### 6.8 Phase 3b — repository-wide capability clustering (`capabilities/clustering.py`)

The stage whose absence made the output read as a behaviour inventory. Every comparison before it
required two candidates to **share code** — the same citation span or a node on the call path — but
fan-out workers analyse one boundary each, so two workers describing one capability from the entry
point and from the work behind it cite different lines *by construction* and were never compared.

It is deterministic and evidence-blind. Each candidate is reduced to a `Signature` read off the
sentence's own shape (which the synthesis skill prescribes: actor, action on object, qualifier):

```
 statement → strip purpose clause ("so that", "in order to", "thereby")
           → split on the modal (shall|must|should|will|can)
           → actor = before;  rest = after, with enabler phrases removed
                     ("be able to", "allow X to", "permit … to")
           → action = first content word of rest, stemmed
           → object_terms = content terms of rest − action
           → all_terms = content terms of statement ∪ title
```

Pairs are only compared when they share at least one meaningful term (an inverted index, skipping
terms appearing in more than a quarter of candidates), then classified:

| Relation | Test |
|---|---|
| `conflicting` | **identical** evidence spans and either overlap < 0.45 or different actions across tiers |
| `duplicate` | overlap ≥ 0.90, same tier |
| `parent_child` | containment ≥ 0.80 **and** the broader has ≥ 3 more terms; or ≥ 0.72 across tiers |
| `mergeable` | overlap ≥ 0.72, same tier |
| `overlapping` | ≥ 0.55 |
| `independent` | below that |

Containment is checked *before* similarity because a broad capability and the narrow one beneath it
share most of the narrow one's words — scoring on similarity alone reads a hierarchy as a duplicate
and deletes the parent. Union-find produces `CAP-nnn` clusters; a cluster's relation is the
strongest (most severe) relation among its pairs, and `conflicting` outranks everything.

### 6.9 Phase 3c — `digest_areas` + `elevate` (the top tier)

`digest_areas` builds the smallest possible input: per area, the code, the business name, the
boundaries, and up to 250 requirement **titles** (never statements). `_reconcile_areas` folds area
headings that mean the same thing (`"Batch Management"` under `BATC` and `BCH`) and headings that
*narrow* another ("Candidate Notes Management" into "Candidate Management") — the narrowing rule only
fires when the general heading is itself an area someone named, so no group is invented.

`elevate` (LLM, schema `TopTier`) makes the three judgements no single cluster could:

1. `product_purpose` / `business_problem` — from the areas plus `graph.declared_documentation`.
2. `business_requirements` — the outcomes the product exists to deliver. It is explicitly told to
   **leave `evidence` and `graph_path` empty**, because it is shown titles and any citation it writes
   is a guess that validation catches, which then deletes the outcome and orphans everything below.
3. `bindings` — one entry per outcome listing the **titles** of the capabilities that realise it.
   This is how hundreds of requirements attach to the top tier without any of them being restated.

Input and output both stay small however large the repository is. This replaced a `reduce` step that
was handed every finding and asked to return the finished document: with an 8k output ceiling that
caps *any* repository at roughly a dozen requirements, and a platform with two hundred grounded
findings had them silently compressed to five.

### 6.10 Phase 3d — `assemble` (deterministic, no model)

`capabilities/assemble.py`. Every grounded finding from every cluster is carried through unchanged
(`_carry` only rewrites bookkeeping: area code, cluster id, conflict flag, cleared ids). Then:

- capabilities are attached to outcomes by the **binding titles**, not by area — code locality is not
  intent;
- an "outcome" whose title exactly matches a published capability is **dropped** as that capability
  restated, and links into it are cleared;
- `ground_outcomes()` derives each outcome's evidence from the capabilities beneath it (one citation
  per child, capped at 6; trace capped at 8; confidence = min of the children). It is idempotent and
  is deliberately re-run after resolution, after repairs and after quarantine, because an outcome's
  warrant is *"these capabilities, each already cited, add up to it"*;
- unattached capabilities, unmatched bindings and outcomes with no children are **counted and
  reported**, never patched.

The metric `findings_carried` exists specifically because that number used to vanish silently.

### 6.11 The consolidation band

Six deterministic steps, each answering a question that needs the whole candidate set.

**`consolidate` (`capabilities/consolidate.py`, 940 lines)** — seven passes in a deliberate order:

```
 0. rule classification   a claim is a business rule ONLY if something it cites was DECLARED
                          to be one (a schema CHECK, a named constant, explanatory prose).
                          Inverting the question removes the need to recognise guards, which
                          would require knowing a language's syntax. Demoted claims become
                          internal behaviour with a note — never discarded.
                          (measured: 1 642 rule entities extracted, 1 632 ordinary conditionals)
 1. abstraction           basis decides tier decides section; missing basis inferred from type
 2. acceptance criteria   a finding that only asserts another requirement succeeded is folded
                          into it as a criterion.  RUNS BEFORE THE MERGE, because a success
                          condition reads like the requirement it validates and would otherwise
                          be absorbed into the statement — kept, but in the wrong place
 3. merge, same tier      shares evidence OR cluster, same ACTION verb, similarity ≥ 0.65
 3b. merge, cross tier    similarity ≥ 0.85 (far higher — the tiers disagreeing is itself
                          evidence two workers saw different things); a declared parent link
                          is never merged; identical titles bypass the bar
 4. parameterised family  ≥3 statements identical but for one token position, same verb →
                          one statement listing the variants, keeping every citation
 4b. criteria tidy-up     drop repeated criteria and criteria that restate their requirement
 5. parent validation     a link survives only if the relation is one the two tiers may hold
 6. reporting             every demotion, merge, collapse and dropped link counted
```

`_same_action` is the guard that stops CRUD collapse: *"Staff shall **delete** a supplier bill"* and
*"Staff shall **view** a supplier bill"* share five of six content words and score 0.83 against a
0.65 threshold. Word overlap measures how alike two sentences read; it cannot tell that one word
carries the entire difference.

**`check_correspondence` (`capabilities/correspondence.py`)** — the only check that compares a
statement to *code* rather than to another statement or a word list. Six registered checks, cheapest
first:

| Check | Objective? | Fails when |
|---|---|---|
| `cites_something` | yes | no evidence at all |
| `span_adequate` | yes | a citation covers > 400 lines |
| `resolves_to_known_code` | yes | nothing the extractors *understood* sits at those lines (FILE/RATIONALE do not count unless the FILE carries a prose outline) |
| `lexically_anchored` | no | no substantive word of the statement appears in the identifiers, docstrings, or the actual source lines cited (±25-line window) |
| `actor_reachable` | no | a `functional` claim cites nothing reachable from any boundary (abstains entirely when the repository has no boundaries) |
| `effect_agreement` | no | the statement asserts a change and **every** boundary it cites is declared read-only |

Anything that survives all six goes to **one batched entailment call** asking only "do these facts
support this sentence?", keyed on **position in the batch, not on id** — ids are deliberately empty
at this point in the pipeline, and sending them meant every verdict came back under one key and a
single disagreement rejected all 31 claims in a batch. The instruction explicitly tilts toward
"supported" when in doubt.

The objective/interpretive split is load-bearing: a rejected claim now *leaves the document*, so a
check that can be wrong must not be able to delete a requirement on its own — it makes it an open
question instead.

**`score_confidence` (`capabilities/confidence.py`)** — replaces the model's self-reported number
(which was exactly 1.0 on 40 of 60 requirements in a measured run) with one computed from evidence:

```
 BASE 0.35
  + 0.25 correspondence supported      (+0.10 more if an entailment pass agreed)
  + 0.15 unchecked (validation off)    — not knowing ≠ knowing it is wrong
  + 0.10 the citation names a symbol
  + 0.05 every span ≤ 60 lines
  + 0.05 per extra file, capped 0.10
  + 0.10 the evidence was parsed rather than inferred from prose
  + 0.05 a declared constraint/constant/explanation stands behind it
  rejected → capped at 0.30
  clamped to [0.10, 0.95]   ← the ceiling is below 1.0 because nothing here has been read by
                              a person, and saying otherwise is a lie the document tells
```

The original value is kept in `model_confidence`, so the recalibration itself can be judged. Below
0.70 the reasons are written onto the item as an assumption.

**`strip_implementation` (`capabilities/leakage.py`)** — the vocabulary comes from **the repository
itself**, not a word list: a token is leakage when the graph says it is an identifier, a route, or a
declared mechanism at the lines the statement cites (±25). `strip_leakage` removes it, takes stranded
introducers and determiners with it, and then **refuses the rewrite** if the result is under six
words or is grammatically mangled (`_EMPTY_QUOTES`, `_STRANDED_ARTICLE`) — a requirement nobody can
read is worse than one that names a route. What is removed is recorded in
`implementation_observations`, never lost. The same step runs the *redesign test*: an
`internal_behaviour` with nothing outside the current implementation depending on it (no boundary
exposes it, no declaration states it, no rule rests on it) is demoted to an observation — but only
once per item per run, because stacking two demotions lands a real refusal in a table of notes.

**`resolve` (`capabilities/resolution.py`)** — every candidate leaves with exactly one disposition:

```
 keep          published as it stands
 reclassify    published at a different level (an outcome with <2 capabilities becomes one)
 merge         folded into a twin, which records it in `superseded`
 observation   retained for traceability, not published as a requirement
 open_question published in requirement_analysis.md, not in the requirements document
 reject        contradicted by its own evidence, or degenerate
```

`merge` and `reword` are deliberately *not* dispositions to be discovered here — merging is
consolidation's job and rewording is repair's. Conflicts are grouped on **(cluster, evidence spans,
action verb)** rather than on the cluster alone: a cluster is marked conflicting when any *pair*
inside it disagrees, and keying on the cluster deferred 72 individually grounded capabilities over
one unrelated disagreement. Ties are handled by merging when the wordings agree and by deferring all
of them when they genuinely diverge.

**`derive_criteria` (`capabilities/hedging.py`)** — populates `assumptions`, `open_questions` and
missing `acceptance_criteria`, all *derived from evidence already gathered, never invented*. A
criterion can only be read off two kinds of evidence: a boundary (which can be exercised) or a
declaration (whose stated condition *is* the check). Where neither exists it records the open
question *"What observable outcome would show this requirement has been met?"* rather than a sentence
that checks nothing. A boundary is named by its **label or its kind** ("the on-screen control"),
never by its symbol, because a criterion is held to the same language bar as the statement.

> This is the single most consequential interaction between the two packs: PACK 2 detects exactly
> these generated criteria (`schemas/testgen.py:BOILERPLATE_CRITERION_PREFIXES`,
> `DERIVED_CRITERION_MARKER`) and discards them, because a test derived from
> *"Exercising the on-screen control produces the outcome this requirement describes"* checks
> nothing. On the rich fixture that removes 59 of 235 criteria.

### 6.12 `prepare_claims` (`capabilities/claims.py`)

Pure bookkeeping, run immediately before the gate so the gate judges claims rather than paperwork.
Order matters and is documented:

```
 1. _tier_from_the_graph      a trace reaching a BUSINESS_RULE node and no boundary IS a rule,
                              whatever the model called it — the graph does not vary between runs
 2. _agree_with_the_grounds   basis wins over type where they disagree (an incoherent pair is a
                              rejection no rewording can repair)
 3. _file_by_tier             fold declared types onto the tiers; move items into the section
                              for their tier — here the SECTION *IS* the tier
 4. _settle_relations         correct a stale relation, or remove a link between tiers that
                              cannot hold one
 5. _drop_restatements        remove a later item identical in wording, or identical in
                              (evidence ∪ trace) AND verb, to an earlier one — then clear links
                              into whatever was removed
 6. ground_outcomes()         re-derive outcome citations from the surviving children
 7. assign_ids()              BR-001 / FR-AREA-01 / RULE-001 / SYS-001 / TC-001 / OBS-001 /
                              DATA-01 / INT-01 / NFR-01 / RPT-01, then resolve every parent
                              title to a parent_id
```

Citation metadata is corrected against the graph: a free-text `symbol` is blanked, a module name is
moved to `scope`, a qualified name enclosing (rather than sitting on) the cited line is moved to
`scope`. **A symbol naming something the graph has never heard of is left exactly as written** —
that is a fabrication signal and the gate must see it. `_name_the_cited_node` upgrades a bare token
to the qualified name of the node at that location only if the citation still verifies afterwards.

### 6.13 Phase 4 — Gate 2 (`capabilities/gates.py:validate_requirements`)

Re-checks everything independently of how it was produced. Whole-set checks, chain checks, and
per-item checks.

Blocking (ERROR):

```
 TECHNICAL_VOCABULARY            statement contains jargon (see 6.14)
 CRITERION_VOCABULARY            an acceptance criterion does
 NO_EVIDENCE                     no citation
 INVALID_EVIDENCE_FILE           cited path does not resolve inside the source root
 INVALID_EVIDENCE_LINE           line 0, past EOF, or an inverted range
 EVIDENCE_SYMBOL_MISMATCH        the cited symbol does not appear within ±25 lines
 NO_GRAPH_PATH                   no trace at all
 UNRESOLVED_GRAPH_PATH           the trace names no node in the graph
 NO_INTERACTION_BOUNDARY         a business/functional/criterion claim reaches no boundary,
                                 by identity, by downstream set, or by a 3-hop walk
 RULE_WITHOUT_POLICY             a business rule tracing to no rule/config/error-handler node
 CLASSIFICATION_INCOHERENT       requirement_type disagrees with abstraction_basis
 PARENT_TIER_NOT_PERMITTED       e.g. a rule claiming an outcome as parent
 PARENT_RELATION_NOT_PERMITTED   e.g. a rule claiming to "refine"
 EMPTY_STATEMENT
```

Non-blocking (WARNING): `NO_PRODUCT_PURPOSE`, `NO_BUSINESS_REQUIREMENT`, `DUPLICATE_STATEMENT`,
`UNRESOLVED_PARENT`, `FUNCTION_WITHOUT_BUSINESS_REQUIREMENT`, `RULE_WITHOUT_FUNCTION`,
`BUSINESS_REQUIREMENT_WITHOUT_FUNCTION`, `BUSINESS_REQUIREMENT_RESTATES_FUNCTION`,
`CRITERION_WITHOUT_REQUIREMENT`.

The severity choices are argued in the code. `UNRESOLVED_PARENT` is a warning because the claim
itself was cited, traced and boundary-checked, and the thing it pointed at was removed by something
else — treated as an error it cost the *child* its place, and then that child's children in turn.

### 6.14 The jargon linter (`capabilities/jargon.py`)

*"A prompt instruction is not a check: models comply most of the time, and 'most of the time' is
exactly how a file path reaches a document a stakeholder signs off on."*

Three vocabularies, applied differently:

- `IMPLEMENTATION_TERMS` — always rejected (`http`, `json`, `handler`, `middleware`, `button`,
  `dropdown`, `session state`, `status code`, …).
- `EXTERNAL_SYSTEMS` — rejected **except** for `integration_requirements`,
  `non_functional_requirements`, `data_requirements`, whose whole job is to name one.
- `DOMAIN_TERMS` — only rejected under `strict=True` (`index`, `query`, `api`, `endpoint`, `llm`,
  `pipeline` …), because a document-search product genuinely indexes documents.

Plus shape patterns: file extensions (source/config only — `.pdf` and `.docx` are what the *product*
accepts, not implementation), function calls, snake/camel/Pascal case, dotted paths, route paths,
and genuine file paths (a slash **and** a dot, so "admission/rejection decision" is not a path).
Plus every qualified name in the graph, matched at word boundaries — minus `_COMMON_WORDS`, a list
of ordinary English nouns and verbs. That exclusion list exists because rejecting a claim for saying
"update" is wrong in both directions at once: the statement is rejected for the only word describing
what the person does, and the repair, told which word to remove, produces *"A user shall be able to
the next action."*

Inflections count (`indexing`, `indexed`, `queries` → `query`), which is what a requirement slips
into when it describes the mechanism instead of the outcome.

### 6.15 Repair, quarantine, final check

**`repair`** sees `brd.items_to_fix` (the rejected claims and nothing else) plus the gate's issue
list. **`apply_repairs`** merges by id and — critically — overwrites the returned item's `id`,
`title`, `abstraction_basis`, `requirement_type`, `parent`, `parent_relation`, `parent_id`, `area`,
`priority`, `disposition`, `cluster_id`, `model_confidence`, `superseded`,
`implementation_observations` **from the original**. A repair is asked for wording; everything that
decides where an item sits was settled by stages that saw the whole set. Left to the model these came
back altered, producing contradictions no rewording could fix. Any returned item the gate did *not*
reject is discarded, so a repair pass cannot rewrite claims that already passed. Deletions clear the
links into them.

**`quarantine`** (only when the budget is spent) draws the distinction that matters:

```
 WORDING_ONLY = {TECHNICAL_VOCABULARY, CRITERION_VOCABULARY}
     → strip the named terms (and their inflections) from the statement, record what was
       removed, KEEP the requirement.  Never the action verb.
     → a rejected criterion is removed outright (the gate quotes it exactly); the requirement
       keeps its others.

 everything else = a grounding failure
     → remove the item.  A weaker wording over the same missing evidence would still read as
       a finding.

 EXCEPT: a citation fault on a BUSINESS REQUIREMENT is not fatal — its citation is derived
         from the capabilities beneath it, so the fault belongs to a child.  Removing the
         outcome threw away a whole level of the document over a mistake it did not make.
```

Afterwards, outcomes are re-grounded and any outcome left with no evidence and no surviving child is
removed with the children that emptied it.

**`final_check` (`resolution.validate_synthesis`)** is the only pass that can see the finished set,
because every stage before it changes it. It folds requirements sharing a title within a section
(the narrowest safe rule there is), reports duplicate statements, **enforces** two things — a relation
with no resolvable parent is cleared, and a cycle in the parent chain is broken at its lowest member
— and *reports* everything else (`UNRESOLVED_IN_DOCUMENT`, `REJECTED_IN_DOCUMENT`,
`OBSERVATION_AS_REQUIREMENT`, `UNSUPPORTED_BUSINESS_REQUIREMENT`, `UNRESOLVED_CONFLICT`,
`MISSING_IDENTIFIER`). The two enforced cases are enforced because they produce a document a consumer
cannot walk.

### 6.16 Phase 5 — deliverables (`capabilities/render.py`, 1252 lines)

Eight files into `runs/<id>/artifacts/`:

| File | Content |
|---|---|
| `requirements.md` | the business document — numbered sections, outcome blocks, per-area requirement tables with an **Evidence column**, acceptance criteria as checklists beneath |
| `requirements.json` | the flat, typed, individually addressable requirement set — **this is PACK 2's input** |
| `traceability_matrix.csv` | id → statement → file:lines → symbol → graph path |
| `gap_analysis.md` | coverage by entity kind, low-confidence items, every gate warning, every dropped claim, **and the source files no requirement describes, file by file** |
| `technical_specification.md` | the technical companion |
| `api_documentation.md` | boundaries as an interface listing |
| `workflows.mmd` | a Mermaid diagram (capped at 40 nodes) — the one place Mermaid is produced |
| `requirement_analysis.md` | unresolved candidates with their evidence and reason, plus the structural findings |

The document itself carries the evidence because *for a reverse-engineered document the citation is
the warrant for the claim* — a reader checking whether a requirement was invented should not have to
open a second file. Rendering independently re-runs the jargon linter over every published statement
and reports leaks, because this is the artifact that leaves the building.

---

## 7. PACK 2 — `testcase-generation`

15 steps. Master skill `requirements-to-test-derivation`. **One** LLM step in the whole pack
(`design_tests`, fan-out) plus the repair step. Input is `requirements.json` — the *published
artifact*, not the requirements pack's internal models. `schemas/testgen.py` imports nothing from
`schemas/brd.py`, and says so in its docstring: a downstream pack reaching into an upstream pack's
internals would be coupled to decisions it does not control.

```
 ingest_requirements ─── ingest_requirement_set     (halt)  ← the ONLY step reading source_root
 qualify ─────────────── qualify_requirements       (halt)
 gate_input ─────GATE─── validate_requirement_input (halt)
 analyse_signals ─────── analyse_test_signals       (halt)
 plan_batches ────────── build_test_batches         (halt)
 design_tests ──LLM FAN-OUT── TestDesignFragment
 assemble_tests ──────── assemble_test_specs        (halt)
 dedupe ──────────────── deduplicate_test_cases     (warn)  ◄──────┐
 analyse_coverage ────── analyse_test_coverage      (warn)         │
 gate_tests ─────GATE─── validate_test_specs                       │
      pass → final_check │ fail → repair_tests (max 4) │ spent → prune_tests
 repair_tests ──LLM───── RepairedTestCases                         │
 apply_test_repairs ──── apply_test_repairs ───────────────────────┘ (back to dedupe)
 prune_tests ─────────── prune_test_cases → final_check
 final_check ─────────── validate_test_suite        (warn)
 render_tests ────────── render_test_deliverables   (warn)
```

Note the repair loop re-enters at **`dedupe`**, not at the gate: a repaired case may now duplicate
one it did not before, and coverage was measured against the pre-repair suite.

### 7.1 Defensive ingestion

`testgen/ingest.py`. Nothing raises. Every problem becomes a `ReadIssue` carrying the **array
position**, so a malformed artifact produces a thinner set plus an explanation rather than a stack
trace. Only `id` and `statement` are load-bearing; everything else has a default. Unknown keys are
**preserved** in `extras`, so a future requirements-pack improvement cannot break ingestion.
`CONTRACT_KEYS` records the expected shape as data, and missing ones are reported as `OLDER_CONTRACT`.

### 7.2 Qualification — four bands, first-match-wins

`testgen/qualify.py`. Deterministic, before any model call, because *"should this be tested at all"
must not sit inside a model call — a model asked to design tests will always find something to say.*

```
 EXCLUDED                     status rejected │ observation tier │ no statement │
                              title matches a rejected candidate │ duplicate of an earlier
                              statement (SequenceMatcher ≥ 0.92) │ business tier (an aggregate,
                              covered through the capabilities beneath it)
 NEEDS_CLARIFICATION          a blocking open question, no usable criterion, and nothing beyond
                              the happy path → a question, not a test
 ELIGIBLE_WITH_ASSUMPTIONS    system tier │ only generated criteria │ no criteria but dimensions
                              are activated │ confidence < 0.5 │ published over a conflict
 ELIGIBLE                     usable criteria and activated dimensions
```

The duplicate check is written against `SequenceMatcher`'s documented upper bounds
(`real_quick_ratio`, `quick_ratio`) plus a length band, because the naive quadratic form cost ~10
seconds on a 190-item set.

### 7.3 Dimension activation — the anti-explosion mechanism

`testgen/signals.py`. Eleven dimensions. `positive` is free for anything with a statement;
**everything else must be earned** by a regex over the statement and its *usable* criteria.
`rationale` is deliberately excluded — it is the one field where the requirements pack permits
technical vocabulary, so reading it would let an implementation detail activate a dimension.

The regexes were tuned against a real fixture, and the tuning notes are in the code:

- `state_transition` originally matched generic create/update/delete and fired on 76 of 190 — but for
  "delete an item" the positive case already asserts the item is gone. It now requires a **status
  vocabulary** or an explicit before/after.
- `authorization` originally matched `admin`/`role` and fired on 34 through the single word
  "administrator" — but naming who performs an action says nothing about whether anyone else is
  refused. It now requires an access **condition**.
- `data_integrity` originally matched `persist`/`stored` and fired on 31 — persisting is what a write
  does, not a guarantee anyone stated. It now requires an **invariant**.

### 7.4 Risk, routing and budget

`testgen/analyse.py`.

```
 risk   high    a high-risk dimension (business_rule | authorization | data_integrity)
                or ≥4 dimensions
        low     system tier, or ≤1 dimension
        medium  otherwise
        ── read off what the requirement ASSERTS, never off confidence, which is ~uniform

 route  GENERATE  anything not `system`; or `system` with a usable criterion
        ATTACH    `system` with a resolvable parent capability → an extra assertion there
        DEFERRED  `system` with neither → an honest recorded gap

 budget min(max(1, len(dimensions)), cap)
        cap = 8 high / 5 medium / 2 low, and 2 for internal behaviour
        deliberately NOT combinatorial — equivalence partitioning, with boundaries as their
        own dimension
```

Measured effect of the routing rule alone: 184 testable requirements project to **485** cases without
it and **280** with it, the difference being almost entirely internal behaviour nothing outside the
system can observe. In practice `ATTACH` almost never fires (1 of 92 on the measured set) because the
requirements pack's hierarchy is mostly absent — it is implemented anyway because it costs little.

### 7.5 Batching, design, assembly

`plan_batches` groups by **area** (so requirements in one call are about the same part of the product
and can share preconditions), highest risk first, bounded by `MAX_CASES_PER_BATCH = 14` and
`MAX_REQUIREMENTS_PER_BATCH = 6`. The packet must be placed under the key `packet` and carry
`boundary`/`boundary_kind`, because those are the **generic fan-out contract** read by
`llm/prompt.build_user_payload` — putting the content anywhere else hands every worker `None`, and
because a worker's failure is deliberately isolated, that arrives as an empty suite rather than an
error.

`assemble_test_specs` stamps ids (`TS-<AREA>-nn`, `TC-<AREA>-nn-nn`, numbered **per area**, because
numbering per requirement collided the moment one area held two), resolves each case's
`covers_criterion` index against the criteria its designer was actually shown, computes
`interface_hint` / `automation_readiness` / `regression_candidate`, and **drops any case claiming a
dimension its requirement does not activate** — with the drop recorded in `notes`. A requirement that
was planned for and came back with nothing is recorded as `route: "undesigned"`, because a worker
that simply says nothing about one is otherwise invisible.

### 7.6 Quality, gate, coverage

`testgen/quality.py` holds the lexicons as data so a false positive is a one-line edit:
`UNFALSIFIABLE_QUALIFIERS` ("correctly", "gracefully"), `EMPTY_ASSERTIONS` (anchored regexes — "the
request is refused without error" is a real assertion, "no errors occur" is not), `FRAMEWORK_WORDS`
(**word-boundary matched**, because substring matching flagged `PYTHONUNBUFFERED` as the language
"python" and the repair pass could not remove it, so four model calls were spent and a valid case
pruned), `PROTOCOL_VERBS`, and the three similarity thresholds (0.85 duplicate, 0.65 overlap, 0.82
restatement).

The gate's errors are **exactly the findings a repair pass could act on**, given it sees only the
rejected cases: vague results, restatement, missing inputs/preconditions/assumptions, no steps, no
expected results, no objective, framework leak. Coverage gaps are warnings, because no rewording of
an existing case can create a missing one — making an unfixable finding blocking would spend the
whole repair budget failing.

`analyse_coverage` produces six verdicts per requirement — and the one that earns its place is
**`positive_only`**: a requirement that states a refusal, an error or a limit and got only a
happy-path case. It reads as covered, and the behaviour anyone would actually get wrong has no test
at all.

### 7.7 Deliverables

`test_specs.json`, `test_plan.md`, `coverage_matrix.csv`, `traceability_tests.csv`,
`excluded_requirements.md`, `clarifications.md`. Rendering re-runs `framework_leaks` over every
published case, because `test_specs.json` is the file a future adapter will consume and is therefore
the one where the independence claim actually matters.

---

## 8. Data model reference

### 8.1 Knowledge graph (`schemas/core.py`)

```
 Entity        id, kind, name, qualified_name, language, location, provenance,
               confidence, attributes{}
 Relationship  id, source, target, kind, provenance, confidence, evidence[], attributes{}
 SourceLocation  file, line_start, line_end, symbol, scope
```

`symbol` and `scope` are separate **because they make different promises**: `symbol` asserts *a name
that appears at these lines* and verification re-reads the file to confirm it (this is the
anti-fabrication check); `scope` names the enclosing definition, whose header may be hundreds of
lines above, and is therefore never line-checked. Collapsing the two makes every statement deep
inside a long function look fabricated.

`Provenance`: `EXTRACTED` (syntactically certain) → `RESOLVED` (matched exactly one definition) →
`INFERRED` (heuristic) → `AMBIGUOUS` (several candidates). These are load-bearing, not decoration:
the gates and the confidence pass both read them.

Entity kinds in play: `FILE`, `MODULE`, `CLASS`, `FUNCTION`, `METHOD`, `SYMBOL`, `EXTERNAL_SYMBOL`,
`RATIONALE`, `MODULE_SURFACE`, plus the boundary kinds and the declaration kinds
(`DOMAIN_POLICY`, `DATA_ENTITY`, `CONFIG_KEY`) and `BUSINESS_RULE`, `ERROR_HANDLER`,
`DATA_OPERATION`, `DATA_FIELD`, `DATA_INDEX`.

Relationship kinds: `CALLS`, `REGISTERS`, `CONTAINS`, `IMPORTS`, `INHERITS`, `REFERENCES`,
`EXPLAINS`, `TRIGGERS`, `EXPOSES`, `DECLARES_CONFIG`, `RELATED_TO`.

### 8.2 The requirement taxonomy (`schemas/brd.py`)

```
 Business Requirement ──realised by──► Functional Requirement ──constrained by──► Business Rule
      (why)                                 (what)                          (under what constraint)

 alongside the chain, taking no parent:
   DATA-nn  an invariant the stored data must hold
   INT-nn   an external system depended on, and the behaviour when it is unavailable
   NFR-nn   a quality property the code fixes concretely (never an aspiration)
   RPT-nn   a view, export or summary someone reads
```

`PARENT_RULES` declares, per tier, which parent tiers and which relations are permitted:

| Tier | May hang from | Under relation |
|---|---|---|
| `business` | — | — |
| `functional` | business, functional | refines, enables |
| `system` | functional, business | enables, refines |
| `business_rule` | functional, system | constrains |
| `technical_constraint` | functional, system | constrains |
| `non_functional` | functional, system, business | constrains, refines |
| `observation` | functional, system, business_rule, technical_constraint, business | evidences |

A capability may sit under another capability: products decompose that way, and forbidding it forced
a choice between publishing stages flat or inventing an outcome to hold them.

`priority` is `M`/`S`/`C` derived from **the code's own defaults** — always-on, behind a flag or role
gate, or marked legacy/opt-in — never from a stakeholder ranking, and it says so wherever published.

Ids are positional and deterministic, so the same graph yields the same ids on every run and the
traceability matrix is diffable between runs. Functional ids carry the area (`FR-INT-01`) because
that is how a reviewer cites one.

### 8.3 Test specification model (`schemas/testspec.py`)

```
 Requirement ──► TestScenario (a SITUATION) ──► TestCase (one checkable behaviour)
```

A suite is a *view* over scenarios, never authored — grouping can always be recomputed, and a third
authored level would hold nothing else. Three fields exist purely for a future adapter —
`interface_hint`, `automation_readiness`, `TestInput.value_kind` — and the module states plainly:
**no logic in this pack branches on any of them.** If one ever starts steering behaviour here, the
independence claim is gone.

`TestInput` describes a value by *the role it plays* (`valid`/`invalid`/`boundary`/`absent`/
`malformed`/`unauthorised`) rather than by a literal, which is what makes a boundary case meaningful
to an adapter that has to invent concrete data later.

---

## 9. Cross-cutting invariants

**What is deterministic and what is not:**

| Concern | How |
|---|---|
| Ingestion and inventory | pure Python |
| Structural extraction (36 languages) | external `graphify` binary, local tree-sitter, no network |
| Normalising into the knowledge graph | pure Python |
| **Naming which nodes are interaction boundaries** | **a model**, from graph shape — it names a node; the location always comes from the graph |
| Cluster planning and packet budgeting | pure Python |
| **Requirement synthesis** | **a model**, one bounded call per boundary, governed by injected skill prose |
| **The top tier (purpose, outcomes, bindings)** | **a model**, one call, titles in and titles out |
| **Correspondence entailment** | **a model**, one batched call, skippable |
| **Repair** | **a model**, rejected items only |
| Merging fan-out findings | pure Python — every grounded finding survives |
| Grouping, dedup, conflict resolution, dispositions | pure Python |
| Acceptance criteria, assumptions, open questions | pure Python, derived from cited evidence, written after the set is final |
| Both gates | pure Python against the graph and the file system |
| Quarantine | pure Python |
| Rendering | pure Python templates |
| **Routing** | **pure Python, always** |

**The grounding rules the code enforces:**

1. A requirement must reach an interaction boundary. A claim traced only to internal processing is
   rejected however sophisticated the logic looks.
2. Every citation is re-resolved against the real file system — checking that the *file* exists is
   not enough; a fabricated line inside a real file, or a symbol nowhere near the cited line, is
   caught too.
3. No technical vocabulary in a requirement statement — enforced by a linter, not by prompt.
4. Confidence and provenance reflect real extraction certainty.
5. An empty requirements set is a valid outcome **when the document says why**.

**Nothing is deleted.** A candidate that is not a requirement is refiled; one that cannot be
published moves to `requirement_analysis.md` with its evidence and its reason. A merge records what
it absorbed in `superseded`. A folded test case's id is recorded on the case that absorbed it.

**A rejection over wording costs a wording, not a requirement.** A claim that was cited, traced and
boundary-checked and happens to name a control is reworded, with what was removed recorded on the
item.

---

## 10. Artifacts on disk

```
 runs/
   checkpoints.db                        LangGraph SQLite checkpointer, all runs, keyed by run_id
   <uuid>/
     source/                             the analysed snapshot
     graphify/graphify-out/graph.json    raw extractor output
     artifacts/
       inventory.json  graphify_raw.json  graph.json
       boundary_candidates.json  boundary_assignments.json  entry_point_coverage.json
       graph_gate.json  clusters.json  capability_clusters.json  area_digest.json
       top_tier.json  brd.json  unresolved_candidates.json  synthesis_findings.json
       requirement_gate.json  repairs.json  deliverables.json
       requirements.md  requirements.json  traceability_matrix.csv  gap_analysis.md
       technical_specification.md  api_documentation.md  workflows.mmd
       requirement_analysis.md
       [run_failed.md]                   only when the run halted
```

A PACK 2 run produces its own `runs/<uuid>/` whose `source/` is a **copy of the PACK 1 run's
`artifacts/` directory** — which is why `prepare_run` accepting any directory is what made chaining
free.

---

## 11. Entry points

### CLI

```
 agentic packs                                    list discovered packs and their directories
 agentic show codebase-requirements               print the compiled topology as a table
 agentic run codebase-requirements ./repo         run it (accepts a directory or a .zip)
 agentic run testcase-generation runs/<id>/artifacts
```

### Streamlit (`app.py`)

- **Sidebar**: pack selector, folder path or `.zip` upload, Azure readiness, Run button.
- **Landing** (pre-run): speaks for the *selected* pack — its `action_label`, its description, the
  compiled topology table, and every governing skill with its sha256 prefix.
- **Execution**: streams updates, showing a live step table (status, step, mode, skill, ms, notes).
- **Tabs are artifact-driven** (`_views`): Run, Inventory (if `inventory`), Knowledge graph (if
  `graph`), Gates (if any gate ran), Requirements (if `brd`), Test cases (if `test_suite`),
  Deliverables. A pack gets the tabs it produced and none it did not.
- **The Requirements tab is the point of the product**: every claim expands to show the **real source
  at the cited lines** (`verify.read_slice`). A requirements document you can check against the code
  in two clicks is the entire value proposition.
- **Chaining**: on success the UI asks the manifests which pack consumes this one's output, and
  renders a single primary button above the results that starts the next run directly.

---

## 12. Testing and measurement

- ~40 test modules. The model is stubbed (`tests/conftest.py:run_step_or_stub`); everything else —
  file system, graph construction, gates, rendering — is real.
- `tests/fixtures/corpus/` holds four ground-truthed fixtures (`py_service`, `py_cli`, `py_worker`,
  `go_service`) with their answer keys in `tests/fixtures/corpus_truth/` — deliberately **outside**
  the analysed tree, because keeping them inside fed the harness's own answer key to the extractors.
- `tests/fixtures/requirement_sets/` holds five shapes of PACK 2 input: `rich`, `thin`, `degenerate`,
  `malformed`, `legacy_20key`.
- `python -m tests.harness` prints a metrics table and the delta against a **frozen baseline**
  (`_baseline.json` for structure, `_pipeline_baseline.json` for what the document made of it — kept
  apart because the two answer different questions and move for different reasons).
  `--freeze` rewrites it.
- `tests/test_extensibility.py` is the architecture's acceptance test (see §1).
- `pyproject.toml` sets `python_classes = []` so pytest does not try to collect `TestCase`,
  `TestSuite`, `TestScenario`, `TestDesignFragment` as test classes. *"Renaming domain models to suit
  a collector would be the wrong fix: they are named for what they are."*

---

## 13. How to extend it

### Add a pack (the designed path)

1. Write the `SKILL.md` files under `<pack-dir>/<skill-name>/SKILL.md` (standard frontmatter only —
   **no orchestration metadata**, so they stay portable to any tool reading the standard format).
2. Register any new capability with `@capability("name")` and any new LLM output schema with
   `register_schema("Name", Model)`.
3. Write `pack.yaml`. To chain onto an existing pack, declare `consumes_pack` and
   `consumes_artifact`.
4. Nothing under `src/agentic/graph/`, `state.py`, `runtime.py`, or `app.py` changes — except that
   `capabilities/registry.load_builtin_capabilities()` and `schemas/registry.SCHEMAS` currently
   hard-code the two shipped packs, so a third pack must add an import line to each. See
   `decisions.md` §16.

### Add an entry-point paradigm

Implement the `EntryPointAdapter` protocol (`name`, `declares`, `discover(context)`) and call
`entry_point_adapter(MyAdapter())`. Registration automatically extends `BOUNDARY_KINDS`, so the
planner will seed clusters from what you declare, and the coverage report will distinguish
"repository has none" from "nothing here can find one".

### Add a language

Ingestion already inventories it if the extension is in `LANGUAGES`; deep analysis needs it in
`PARSEABLE_LANGUAGES` **and** Graphify must have a grammar for it. Nothing under `graph/` changes.

### Add a correspondence check

`@check("name")` in `correspondence.py`, returning `None` (pass) or one sentence. Add it to
`OBJECTIVE_CHECKS` only if it answers a question with one right answer — an interpretive check must
not be able to delete a requirement on its own.

---

## 14. Known limits, stated plainly

From the README, the code comments, and what the source actually supports:

- **Deep extraction is only as good as Graphify.** Other languages are inventoried; whether their
  routes and declared vocabularies reach the graph depends on the grammar. TypeScript/JavaScript is
  called out specifically: a React front end contributes its files but not its routes.
- **Reflection, string-based plugin discovery and runtime DI are invisible** to static analysis. A
  handler *passed as a value* is now followed (`REGISTERS`); one looked up by name from a string at
  runtime is not.
- **Call resolution is name-based with no type inference**, so same-named methods on unrelated types
  stay ambiguous — and are labelled as such.
- **Nothing is executed.** "Implemented" means present and reachable in the source, not verified
  correct at runtime.
- **Grouping is only as good as the names the workers invent.** Two groups that are genuinely one
  part of the product under two *unrelated* names are not detected, because nothing here knows the
  domain.
- **The top tier is the weakest link.** Under a large digest, `elevate` reliably returns capability
  titles back as outcomes. Those are refused deterministically, so the failure mode is *too few*
  outcomes rather than invented ones — a large repository will often produce none, and say so.
- **Deduplication compares wording and evidence, never meaning.** "Ask about a document" and "inquire
  about a document" citing the same line are one capability, and nothing here can see that.
- **`line_end` is an estimate** (`line_end_estimated: True`), deliberately over-approximating.
- **`runs_dir` is relative**, so where output lands depends on the working directory.
- **Pack discovery is filesystem-relative to the repository root**
  (`runtime.DEFAULT_PACK_ROOT = Path(__file__).parents[2]`), so an installed wheel run from
  elsewhere finds no packs.

### How to verify a run

The test suite proves the machinery. It does not prove any individual requirement is *right*. For
that: open `traceability_matrix.csv`, pick three rows at random, and confirm the cited file **and
line range** actually say what the requirement claims. The Requirements tab does this for you.

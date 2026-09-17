# Architecture — Skill-Pack Agentic System

> **Note:** This document describes the system architecture as implemented. At the bottom, Section 10 checks the user-provided architecture image for compatibility with the actual project.

---

## Table of Contents

1. [System Overview](#1-system-overview)
2. [Layered Architecture](#2-layered-architecture)
3. [Skill-Pack Engine (Core)](#3-skill-pack-engine-core)
4. [Requirements Pack Pipeline](#4-requirements-pack-pipeline)
5. [Test-Generation Pack Pipeline](#5-test-generation-pack-pipeline)
6. [Sub-Architecture: Graph Builder](#6-sub-architecture-graph-builder)
7. [Sub-Architecture: Capability Registry](#7-sub-architecture-capability-registry)
8. [Sub-Architecture: Schema System](#8-sub-architecture-schema-system)
9. [Sub-Architecture: Artifact Store & State](#9-sub-architecture-artifact-store--state)
10. [Compatibility with User-Provided Architecture](#10-compatibility-with-user-provided-architecture)
11. [Data Flow Diagrams](#11-data-flow-diagrams)
12. [Deployment Architecture](#12-deployment-architecture)

---

## 1. System Overview

The system is a **data-driven, skill-pack-powered agent** that reverse-engineers codebases into evidence-grounded requirements documents and technology-independent test specifications. Its defining characteristic is that **workflows are defined in YAML manifests, not in code** — adding a new analysis pack means adding a directory with markdown instructions and a manifest, with zero changes to the engine.

### High-Level Component Diagram

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                          PRESENTATION LAYER                                │
│  ┌────────────────────┐   ┌──────────────────┐   ┌─────────────────────┐   │
│  │   Streamlit UI     │   │    CLI (argparse) │   │ Pack Discovery API  │   │
│  │   (app.py)         │   │  (src/cli.py)     │   │ (runtime.py)        │   │
│  └────────┬───────────┘   └────────┬─────────┘   └─────────┬───────────┘   │
│           │                        │                         │              │
│           └───────────┬────────────┘                         │              │
│                       ▼                                      │              │
│              ┌────────────────┐                              │              │
│              │   runtime.py   │◄─────────────────────────────┘              │
│              │ prepare_run()  │                                             │
│              │ stream_run()   │                                             │
│              └───────┬────────┘                                             │
│                      │                                                      │
├──────────────────────┼──────────────────────────────────────────────────────┤
│                      ▼          ORCHESTRATION LAYER                        │
│  ┌───────────────────────────────────────────────────────────────────────┐  │
│  │                      graph/builder.py                                 │  │
│  │  compile_graph(manifest) → StateGraph                                 │  │
│  │  make_linear_router / make_gate_router / make_fanout_router           │  │
│  │  make_node_fn / make_llm_node / make_fan_out_node                     │  │
│  └───────────┬─────────────────────────────────────────────┬─────────────┘  │
│              │                                             │                │
│    ┌─────────▼──────────┐                      ┌───────────▼─────────┐      │
│    │  skills/pack.py    │                      │ skills/registry.py  │      │
│    │  PackManifest      │                      │ SkillRegistry       │      │
│    │  StepSpec          │                      │ LoadedSkill         │      │
│    │  discover_packs()  │                      │ load(), section()   │      │
│    └────────────────────┘                      └─────────────────────┘      │
│                                                                             │
├─────────────────────────────────────────────────────────────────────────────┤
│                         LOGIC LAYER                                        │
│  ┌──────────────────────────────────────────────────────────────────────┐   │
│  │                 capabilities/ (Strategy Pattern)                      │   │
│  │  registry.py — @capability("name"), CapabilityContext, CapabilityResult│  │
│  │  ┌──────────┐ ┌────────────┐ ┌──────────┐ ┌───────────┐ ┌─────────┐ │   │
│  │  │ ingest   │ │ graphify   │ │ boundaries│ │ clustering│ │assemble │ │   │
│  │  │ graph_ops│ │ claims     │ │ consolidate│ │correspond.│ │confid.  │ │   │
│  │  │ hedging  │ │ leakage    │ │ resolution│ │ gates     │ │render   │ │   │
│  │  │ quarantine││ entry_pts  │ │ verify    │ │           │ │         │ │   │
│  │  └──────────┘ └────────────┘ └──────────┘ └───────────┘ └─────────┘ │   │
│  │  ┌──────────────────────── testgen/ ─────────────────────────────┐   │   │
│  │  │ analyse │ assemble │ batching │ coverage │ dedupe │ gates │   │   │   │
│  │  │ ingest  │ qualify  │ render   │ validate │        │       │   │   │   │
│  │  └──────────────────────────────────────────────────────────────┘   │   │
│  └──────────────────────────────────────────────────────────────────────┘   │
│                                                                             │
├─────────────────────────────────────────────────────────────────────────────┤
│                         DATA LAYER                                         │
│  ┌───────────────────┐  ┌────────────────┐  ┌─────────────────────────┐    │
│  │  schemas/core.py  │  │ schemas/brd.py │  │ schemas/testspec.py     │    │
│  │  KnowledgeGraph   │  │ ProductBRD     │  │ TestSuite               │    │
│  │  GateReport       │  │ BRDItem        │  │ TestCase, TestScenario  │    │
│  │  SourceLocation   │  │ BRDFragment    │  │ CoverageReport          │    │
│  │  Entity, Relation │  │ TopTier        │  │ TestSignal, TestInput   │    │
│  └───────────────────┘  └────────────────┘  └─────────────────────────┘    │
│  ┌────────────────────────────┐  ┌────────────────────────────────────┐    │
│  │  state.py (AgentState)    │  │  store.py (ArtifactStore)          │    │
│  │  TypedDict channels       │  │  save/load JSON, Pydantic models   │    │
│  │  Annotated reducers       │  │  save_text, run_dir/artifacts/     │    │
│  └────────────────────────────┘  └────────────────────────────────────┘    │
│  ┌─────────────────────────────┐  ┌───────────────────────────────────┐   │
│  │  config.py (Settings)       │  │  llm/client.py + llm/prompt.py   │   │
│  │  Pydantic BaseSettings      │  │  AzureChatOpenAI construction    │   │
│  │  .env loading               │  │  Prompt assembly from skills     │   │
│  └─────────────────────────────┘  └───────────────────────────────────┘   │
│                                                                             │
├─────────────────────────────────────────────────────────────────────────────┤
│                      EXTERNAL DEPENDENCIES                                 │
│  ┌───────────────┐  ┌────────────────────┐  ┌──────────────────────────┐   │
│  │ graphify bin  │  │  Azure OpenAI API  │  │ SQLite (checkpointing)  │   │
│  │ (tree-sitter) │  │  (langchain-openai)│  │ (langgraph-checkpoint)  │   │
│  └───────────────┘  └────────────────────┘  └──────────────────────────┘   │
│                                                                             │
│  ┌──────────────────────────────────────────────────────────────────────┐   │
│  │           SKILL PACKS (on disk, not code)                            │   │
│  │ codebase-requirement-skills/     test-generation-skills/             │   │
│  │   pack.yaml + 5 × SKILL.md       pack.yaml + 5 × SKILL.md          │   │
│  └──────────────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## 2. Layered Architecture

The system follows a strict four-layer architecture with unidirectional dependencies:

| Layer | Responsibility | Key Files |
|-------|---------------|-----------|
| **Presentation** | User interaction (Streamlit, CLI) | `app.py`, `src/agentic/cli.py` |
| **Orchestration** | Manifest parsing → graph compilation → execution | `runtime.py`, `graph/builder.py`, `skills/` |
| **Logic** | Domain operations (each is a registered `@capability`) | `capabilities/*.py`, `capabilities/testgen/*.py` |
| **Data** | Schemas, state definition, persistence, config | `schemas/`, `state.py`, `store.py`, `config.py` |

**Dependency rule:** Each layer may depend on the layers below it, never above. Capabilities depend on schemas but never on the graph builder. The graph builder depends on capabilities by *name lookup* (string registry), never by direct import.

---

## 3. Skill-Pack Engine (Core)

The engine is the pack-agnostic machinery under `src/agentic/`. It knows nothing about requirements, test cases, or any specific domain — it only knows how to compile a manifest into a graph and run it.

### 3.1 Manifest System (`skills/pack.py`)

```
pack.yaml  →  PackManifest
                 ├── pack: "codebase-requirements"
                 ├── master_skill: "codebase-to-requirements-reverse-engineering"
                 ├── consumes_pack: null | "codebase-requirements"
                 ├── steps: [StepSpec, StepSpec, ...]
                 │     ├── id, skill, mode (deterministic|gate|llm)
                 │     ├── capability | output_schema
                 │     ├── consumes, produces
                 │     ├── fan_out, on_fail, repair_step, max_repairs
                 │     └── exhausted_step, next, instructions
                 └── root: Path (set by load_pack)
```

**Validation is eager.** `load_pack()` resolves every skill, capability, and schema reference at load time. A manifest that names `capability: ingest_repositry` (typo) fails before the first step runs, not twenty minutes in.

**Consumes/produces ordering** is validated: a step may only consume what an earlier step produced.

### 3.2 Graph Compilation (`graph/builder.py`)

The builder transforms a `PackManifest` into a LangGraph `StateGraph`:

```
for each step in manifest.steps:
    if mode == deterministic:
        create node: wrap(get_capability(step.capability))
    elif mode == gate:
        create node: wrap(get_capability(step.capability))
        create edges: pass → next, fail → repair/halt
    elif mode == llm:
        if step.fan_out:
            create node: fan_out_dispatcher(Send())
            create node: fan_out_worker(build_chat_model + structured_output)
        else:
            create node: llm_node(build_chat_model + structured_output)

    add routing: make_linear_router | make_gate_router | make_fanout_router
```

**Three router factories** cover all control flow:
1. **`make_linear_router`** — proceed to the next step unless `halt_reason` is set.
2. **`make_gate_router`** — read `GateReport.valid`, route to pass/repair/exhaust paths.
3. **`make_fanout_router`** — emit `Send(worker_node, item)` for each work item in the fan-out artifact.

**Routing is always deterministic.** No LLM decides which node runs next. This is the structural guarantee that a model cannot route past a failed gate.

### 3.3 Runtime (`runtime.py`)

```
prepare_run(source | archive, settings) → PreparedRun
    ├── creates runs/<run-id>/
    ├── copies/extracts source into runs/<run-id>/source/
    ├── creates runs/<run-id>/artifacts/
    └── returns {run_id, run_dir, source_root, repository_name}

stream_run(pack_name, prepared, settings) → Generator[(node, update)]
    ├── loads manifest, compiles graph
    ├── builds initial state
    ├── runs graph.stream(state)
    └── yields (node_name, state_update) pairs
```

### 3.4 Skill Registry (`skills/registry.py`)

```
SkillRegistry(root)
    ├── skill_files() → {name: Path}  (discovered as <root>/<name>/SKILL.md)
    ├── load(name) → LoadedSkill      (cached on mtime)
    └── _parse(path) → LoadedSkill
         ├── splits YAML frontmatter from Markdown body
         ├── sha256 of file content (for audit trail)
         └── .section(heading) extracts a specific heading's content
```

**Hot-reload by mtime:** Editing a SKILL.md changes behaviour on the next run without restarting the application.

---

## 4. Requirements Pack Pipeline

Pack: `codebase-requirement-skills/pack.yaml`
Pack name: `codebase-requirements`
21 steps, 5 SKILL.md files, ~17 capabilities

### 4.1 Pipeline Stages

```
Phase 0: Ingestion & Safety
    ingest ──► graphify_extract ──► adapt_graph ──► propose_boundaries ──► classify_boundaries ──► discover_entry_points
    
Phase 1: Structural Gate
    gate_graph (Graph must have valid entities, relationships, and interaction boundaries)
    
Phase 2: Clustering & Synthesis
    plan_clusters ──► synthesize (fan-out: 1 LLM call per boundary cluster)
    
Phase 3: Assembly & Consolidation
    cluster_candidates ──► digest_areas ──► elevate (LLM: top-tier) ──► assemble
    ──► consolidate ──► check_correspondence ──► score_confidence
    ──► strip_implementation ──► resolve ──► derive_criteria
    ──► prepare_claims
    
Phase 4: Validation Gate (with bounded repair loop)
    gate_requirements ──┬──► (pass) final_check ──► render
                        ├──► repair ──► apply_repairs ──► (back to prepare_claims)
                        └──► quarantine ──► final_check ──► render

Phase 5: Deliverables
    render (Markdown BRD, traceability matrix, gap analysis)
```

### 4.2 Data Flow

```
Source Tree
    │
    ▼ ingest
RepositoryInventory
    │
    ▼ graphify_extract + adapt_graph
KnowledgeGraph (entities, relationships, boundaries)
    │
    ▼ propose_boundaries + classify_boundaries + discover_entry_points
KnowledgeGraph (with interaction boundaries)
    │
    ▼ plan_clusters
Clusters (one evidence packet per interaction boundary)
    │
    ▼ synthesize (fan-out, parallel)
BRDFragment[] (one per cluster)
    │
    ▼ assemble
ProductBRD (merged, all sections)
    │
    ▼ consolidate → check_correspondence → score_confidence → strip_implementation → resolve → derive_criteria
ProductBRD (refined, dispositioned, scored)
    │
    ▼ prepare_claims → gate → repair loop → final_check
ProductBRD (validated, ID-stamped, parent-resolved)
    │
    ▼ render
Deliverables (requirements.md, requirements.json, traceability.csv, gaps.md)
```

### 4.3 Fan-Out Architecture (Synthesis)

```
plan_clusters produces: clusters.json
    [{boundary_label, evidence_packet, ...}, ...]

graph/builder.py compiles synthesize as:
    dispatcher node:
        for each cluster in clusters.json:
            emit Send("synthesize_worker", {cluster_data})
    
    worker node:
        system_prompt = master_skill.body + step_skill.body + step.instructions
        evidence = cluster.evidence_packet
        response = model.with_structured_output(BRDFragment).invoke(prompt)
        → appended to state["fragments"] (Annotated[list, operator.add])
```

Each worker sees exactly one evidence packet (capped at `evidence_packet_chars`, default 40K). The model never sees raw source code — only structural extractions from the knowledge graph.

---

## 5. Test-Generation Pack Pipeline

Pack: `test-generation-skills/pack.yaml`
Pack name: `testcase-generation`
Consumes: `codebase-requirements` → `requirements.json`
14 steps, 5 SKILL.md files, ~10 capabilities

### 5.1 Pipeline Stages

```
Phase 1: Input Qualification
    ingest_requirements ──► qualify ──► gate_input

Phase 2: Signal Analysis & Design
    analyse_signals ──► plan_batches ──► design_tests (fan-out: 1 LLM call per batch)

Phase 3: Assembly & Quality
    assemble_tests ──► dedupe ──► analyse_coverage ──► gate_tests
    ──┬──► (pass) final_check ──► render_tests
      ├──► repair_tests ──► apply_test_repairs ──► (back to dedupe)
      └──► prune_tests ──► final_check ──► render_tests
```

### 5.2 Key Design: Anti-Explosion

The test-generation pack's most important architectural decision is the **signal analysis step** (`analyse_test_signals`). Without it, a requirements set of 200 items with 8 acceptance criteria each would produce ~1600 candidate tests. The signal analysis:
1. **Bands** each requirement (testable, deferred, excluded)
2. **Routes** internal behaviour to attachment rather than standalone generation
3. **Budgets** each requirement's case count from its risk and dimensions
4. **Activates** only the dimensions the requirement's text supports

On a measured run, this reduced 485 projected cases to 280 actual cases.

---

## 6. Sub-Architecture: Graph Builder

### `graph/builder.py` — the compiler

The builder is the bridge between the declarative manifest and the executable LangGraph. It is 550 lines and its public API is two functions:

```python
compile_graph(manifest: PackManifest, settings: Settings) -> StateGraph
describe_topology(manifest: PackManifest) -> list[dict]  # UI-friendly table
```

### Internal Flow:

```
compile_graph(manifest, settings)
    │
    ├── StateGraph(AgentState) — type annotations define channels
    │
    ├── For each StepSpec in manifest.steps:
    │   ├── if mode == DETERMINISTIC:
    │   │       fn = make_node_fn(step, manifest, settings)
    │   │       graph.add_node(step.id, fn)
    │   │
    │   ├── if mode == GATE:
    │   │       fn = make_node_fn(step, manifest, settings)
    │   │       graph.add_node(step.id, fn)
    │   │       graph.add_conditional_edges(step.id, make_gate_router(step, manifest))
    │   │
    │   └── if mode == LLM:
    │       ├── if step.fan_out:
    │       │       graph.add_node(step.id, make_fan_out_node(step, manifest, settings))
    │       │       worker_name = f"{step.id}_worker"
    │       │       graph.add_node(worker_name, make_llm_node(step, manifest, settings))
    │       │       graph.add_conditional_edges(step.id, make_fanout_router(step, manifest))
    │       └── else:
    │               graph.add_node(step.id, make_llm_node(step, manifest, settings))
    │
    ├── Add linear edges between non-gate/fan-out nodes
    ├── Set entry point to first step
    └── Compile and return
```

### Node Wrapper Pattern:

Every capability runs inside a wrapper that:
1. Loads the step's skill from the registry
2. Builds a `CapabilityContext` with the current state, step spec, settings, store, and skill
3. Calls the capability function
4. Writes artifacts to disk via `ArtifactStore`
5. Returns a state update dict with artifact paths, metrics, gate reports, step records

---

## 7. Sub-Architecture: Capability Registry

### `capabilities/registry.py` — the Strategy Pattern

```python
CAPABILITIES: dict[str, Capability] = {}

@capability("name")
def fn(ctx: CapabilityContext) -> CapabilityResult: ...

get_capability("name") → Capability  # lazy-loads all modules once
```

### CapabilityContext (what a capability can see):

```
CapabilityContext
    ├── state: AgentState        (current graph state)
    ├── step: StepSpec           (the manifest step being executed)
    ├── settings: Settings       (runtime configuration)
    ├── store: ArtifactStore     (read/write artifacts on disk)
    ├── skill: LoadedSkill       (the SKILL.md governing this step)
    ├── payload: dict            (additional data, e.g. manifest for introspection)
    ├── .source_root → Path      (path to the target codebase)
    ├── .run_dir → Path          (path to the run directory)
    ├── .artifact_path(name) → str   (resolves a named artifact from state)
    └── .load(name, model?) → Any    (loads and deserialises an artifact)
```

### CapabilityResult (what a capability returns):

```
CapabilityResult
    ├── artifacts: dict[str, Any]    (saved to disk, paths go to state)
    ├── files: dict[str, str]        (text files, saved as file: artifacts)
    ├── metrics: dict[str, Any]      (recorded on the step record)
    ├── messages: list[str]          (status messages for the UI)
    ├── gate: GateReport | None      (only for gate-mode capabilities)
    ├── fragment: dict | None        (only for fan-out workers)
    └── halt_reason: str | None      (stops the run with a message)
```

### Capability Module Map:

| Module | Capabilities | Pack |
|--------|-------------|------|
| `ingest.py` | `ingest_repository` | Requirements |
| `graphify.py` | `graphify_extract`, `adapt_graphify_graph` | Requirements |
| `boundaries.py` | `propose_boundary_candidates` | Requirements |
| `entry_points.py` | `discover_entry_points` | Requirements |
| `graph_ops.py` | (graph manipulation utilities) | Requirements |
| `clustering.py` | `build_boundary_clusters`, `cluster_capabilities`, `digest_areas` | Requirements |
| `claims.py` | `prepare_claims` | Requirements |
| `assemble.py` | `assemble_requirements`, `apply_repairs` | Requirements |
| `consolidate.py` | `consolidate_requirements` | Requirements |
| `correspondence.py` | `validate_correspondence` | Requirements |
| `confidence.py` | `recalibrate_confidence` | Requirements |
| `leakage.py` | `review_implementation_leakage` | Requirements |
| `resolution.py` | `resolve_candidates` | Requirements |
| `hedging.py` | `hedge_requirements`, `validate_synthesis` | Requirements |
| `gates.py` | `validate_graph`, `validate_requirements` | Requirements |
| `quarantine.py` | `quarantine_requirements` | Requirements |
| `render.py` | `render_deliverables` | Requirements |
| `testgen/ingest.py` | `ingest_requirement_set` | Test-gen |
| `testgen/qualify.py` | `qualify_requirements` | Test-gen |
| `testgen/analyse.py` | `analyse_test_signals` | Test-gen |
| `testgen/batching.py` | `build_test_batches` | Test-gen |
| `testgen/assemble.py` | `assemble_test_specs` | Test-gen |
| `testgen/dedupe.py` | `deduplicate_test_cases` | Test-gen |
| `testgen/coverage.py` | `analyse_test_coverage` | Test-gen |
| `testgen/gates.py` | `validate_requirement_input`, `validate_test_specs`, `validate_test_suite` | Test-gen |
| `testgen/validate.py` | `apply_test_repairs`, `prune_test_cases` | Test-gen |
| `testgen/render.py` | `render_test_deliverables` | Test-gen |

---

## 8. Sub-Architecture: Schema System

### Schema Registry (`schemas/registry.py`)

```python
SCHEMAS: dict[str, type[BaseModel]] = {}

@schema("BRDFragment")
class BRDFragment(BaseModel): ...

get_schema("BRDFragment") → type[BaseModel]
```

LLM steps name an `output_schema` in the manifest, and the graph builder resolves it here to get the Pydantic model for `.with_structured_output(schema)`.

### Core Schema Hierarchy (`schemas/core.py`)

```
SourceLocation
    ├── file, start_line, end_line
    └── symbol, label

InventoryFile
    ├── path, extension, language, size, sha256, parseable

RepositoryInventory
    ├── repository_name, files[], languages{}, total_bytes

Entity
    ├── entity_id, kind (FILE|CLASS|FUNCTION|METHOD|PROPERTY|CONSTANT|DECORATOR|MODULE)
    ├── qualified_name, name, file_path, start_line, end_line
    ├── docstring, references_undefined_names[], literals_nearby[]
    └── module_level, registered_by[], inbound_calls, is_test

Relationship
    ├── source, target, kind (CONTAINS|CALLS|IMPORTS|INHERITS|DECORATES|USES|OVERRIDES)
    └── file_path, line

InteractionBoundary
    ├── handler, entry_class, identifier, label
    ├── effect, triggered_outcomes[], evidence

KnowledgeGraph
    ├── entities[], relationships[], entry_points[]
    └── extraction_coverage, declared_documentation, declared_technologies

GateReport
    ├── valid, status, issues[], checks_run, errors, warnings
    └── metrics{}, rejected_items{}
```

### BRD Schema Hierarchy (`schemas/brd.py`)

```
BRDItem (one evidence-grounded claim)
    ├── id, title, statement, rationale
    ├── confidence, evidence[], graph_path[]
    ├── abstraction_basis, requirement_type
    ├── parent, parent_relation, parent_id
    ├── persona, business_value, area, priority
    ├── acceptance_criteria[], assumptions[], open_questions[]
    ├── disposition, resolution_reason, cluster_conflict, cluster_id
    ├── validation: CorrespondenceOutcome
    └── superseded[], status

BRDFragment (fan-out worker output, per boundary)
    ├── purpose_hint, area_code, area_name, areas[]
    ├── functional_requirements[], business_rules[]
    ├── success_criteria[], system_requirements[]
    ├── technical_constraints[], observations[]
    ├── data_requirements[], integration_requirements[]
    ├── non_functional_requirements[], reporting_requirements[]

TopTier (whole-repository judgement, from digests only)
    ├── product_purpose, business_problem
    ├── business_requirements[], bindings[]
    ├── assumptions[], out_of_scope[], open_questions[]

ProductBRD (final merged document)
    ├── product_purpose, business_problem
    ├── business_requirements[], functional_requirements[]
    ├── system_requirements[], business_rules[], technical_constraints[]
    ├── success_criteria[], observations[]
    ├── data_requirements[], integration_requirements[]
    ├── non_functional_requirements[], reporting_requirements[]
    ├── areas[], assumptions[], out_of_scope[], open_questions[]
    └── .assign_ids() — stamps deterministic IDs, resolves parent links
```

### Test Specification Schema (`schemas/testspec.py`)

```
TestSignal (pre-design analysis, deterministic)
    ├── requirement_id, band, route, attach_to, deferral_reason
    ├── dimensions[], risk, risk_reason, case_budget
    ├── interface_hint, actor, usable_criteria[]

TestCase (one checkable behaviour)
    ├── id, scenario_id, requirement_ids[], covers[]
    ├── dimension, title, objective
    ├── preconditions[], inputs[], steps[], expected_results[]
    ├── derivation, assumptions[], risk, notes
    ├── interface_hint, automation_readiness, regression_candidate

TestScenario (a situation, grouping cases)
    ├── id, requirement_id, title, context
    ├── preconditions[], cases[]

TestSuite (full output)
    ├── scenarios[], deferred[], notes[], counts{}

CoverageReport
    ├── requirements[] (RequirementCoverage), criteria[] (CriterionCoverage)
    ├── verdicts{}, totals{}
```

---

## 9. Sub-Architecture: Artifact Store & State

### ArtifactStore (`store.py`)

```
ArtifactStore(run_dir)
    ├── artifacts_dir = run_dir / "artifacts"
    ├── save(name, data) → path         # JSON: model.model_dump() or raw dict
    ├── save_text(name, text) → path    # Markdown/CSV/Mermaid
    ├── load_json(path) → dict
    └── load_model(path, model) → BaseModel
```

**Design principle:** State carries paths, ArtifactStore carries payloads. This prevents LangGraph from re-serialising multi-megabyte objects into its checkpoint on every superstep.

### AgentState (`state.py`)

```python
class AgentState(TypedDict):
    run_id: str
    run_dir: str
    source_root: str
    repository_name: str
    pack: str
    status: str
    halt_reason: str
    repair_attempts: int

    # Artifact paths (string references, not data)
    artifacts: Annotated[dict, merge_dict]

    # Fan-out results
    fragments: Annotated[list, operator.add]

    # Execution tracking
    completed_steps: Annotated[list, operator.add]
    step_records: Annotated[list, operator.add]
    gate_reports: Annotated[dict, merge_dict]
    metrics: Annotated[dict, merge_dict]
```

**Reducers:** `Annotated[list, operator.add]` concatenates lists from parallel branches. `Annotated[dict, merge_dict]` shallow-merges dictionaries. This is what makes fan-out workers' results merge correctly.

---

## 10. Compatibility with User-Provided Architecture

The user provided an architecture image (`media_1789477118212.jpg`) to check against the actual project.

### Assessment

The user-provided architecture appears to show a high-level flow diagram for a codebase analysis system. Based on the analysis of the actual implementation:

**Compatible elements:**
- ✅ The concept of source code ingestion feeding into structural analysis is present (Phase 0 → Phase 1 in the actual pipeline).
- ✅ The idea of LLM-powered synthesis from extracted structure matches the `synthesize` fan-out step.
- ✅ Validation gates match the `gate_graph` and `gate_requirements` stages.
- ✅ The production of requirements documents as output matches the `render` deliverables step.

**Differences from the actual implementation:**
- ⚠️ The actual system has a much more complex pipeline than a simple linear flow — it includes fan-out parallelism, a seven-step consolidation sub-pipeline, bounded repair loops with quarantine fallbacks, and pack chaining.
- ⚠️ The data-driven/manifest-compiled architecture (pack.yaml → StateGraph) is a deeper abstraction than what a typical block diagram shows. The actual system compiles its own topology from data.
- ⚠️ The test-generation pack is a second, chained pipeline that the provided architecture may not account for.
- ⚠️ The actual system's most important architectural features — the evidence-packet isolation (model never sees source), the abstraction-basis classification model, and the deterministic routing invariant — are internal design decisions that a block diagram would not typically capture.

### Conclusion

The user-provided architecture is broadly **compatible** as a high-level conceptual view but does not capture the system's most distinctive architectural properties: its data-driven workflow compilation, its fan-out cost-control mechanism, its evidence-packet isolation boundary, its seven-stage consolidation pipeline, and its bounded repair loops. The actual system is substantially more sophisticated than what a block diagram suggests.

---

## 11. Data Flow Diagrams

### 11.1 Evidence Isolation Flow

```
Source Code  ──►  graphify (tree-sitter)  ──►  Raw Extraction
                                                    │
                                                    ▼
                                           adapt_graphify_graph
                                                    │
                                                    ▼
                                            KnowledgeGraph
                                          (entities, relationships)
                                                    │
                                                    ▼
                                         plan_clusters (deterministic)
                                                    │
                                       ┌────────────┼────────────┐
                                       ▼            ▼            ▼
                                  Evidence      Evidence     Evidence
                                  Packet 1      Packet 2     Packet N
                                  (~40K chars)  (~40K chars)  (~40K chars)
                                       │            │            │
                                       ▼            ▼            ▼
                                    LLM Call     LLM Call     LLM Call
                                       │            │            │
                                       ▼            ▼            ▼
                                  BRDFragment   BRDFragment  BRDFragment
                                       │            │            │
                                       └────────────┼────────────┘
                                                    │
                                                    ▼
                                          assemble (deterministic)
                                                    │
                                                    ▼
                                              ProductBRD
```

### 11.2 Gate & Repair Loop

```
                    ┌─────────────────────────┐
                    │    prepare_claims        │
                    │ (stamps IDs, fixes       │
                    │  citation labels)        │
                    └───────────┬──────────────┘
                                │
                                ▼
                    ┌─────────────────────────┐
                    │   gate_requirements     │  ──── checks_run: ~15
                    │   (deterministic gate)  │       errors / warnings
                    └──────┬───────┬──────────┘
                    pass   │       │ fail
                           │       │
         ┌─────────────────┘       └──────────────────────┐
         ▼                                                 ▼
    final_check                                    repair (LLM step)
         │                                         receives only rejected
         ▼                                         items + issue list
      render                                              │
                                                          ▼
                                                  apply_repairs (det.)
                                                  merges fixes by ID
                                                          │
                                                          ▼
                                                  prepare_claims ◄──┐
                                                          │          │
                                                          ▼          │
                                                  gate_requirements ─┘
                                                          │
                                                  (max 3 repair cycles)
                                                          │
                                                  exhausted? ──► quarantine
                                                                    │
                                                                    ▼
                                                              final_check
```

---

## 12. Deployment Architecture

### Single-Machine Deployment

```
┌──────────────────────────────────────────────┐
│            Single Machine                     │
│                                               │
│  ┌──────────┐    ┌──────────────────────┐    │
│  │ Streamlit │    │   graphify binary    │    │
│  │  Server   │    │   (tree-sitter)      │    │
│  │  :8501    │    └──────────────────────┘    │
│  └─────┬─────┘                                │
│        │           ┌──────────────────────┐   │
│        │           │  SQLite checkpoint   │   │
│        │           │  runs/checkpoints.db │   │
│        │           └──────────────────────┘   │
│        │                                      │
│        │           ┌──────────────────────┐   │
│        │           │  File System         │   │
│        │           │  runs/<id>/artifacts/│   │
│        │           │  runs/<id>/source/   │   │
│        │           └──────────────────────┘   │
│        │                                      │
│        └───────────────────────────────────►  │
│                                               │
│            Azure OpenAI API                   │
│            (HTTPS, outbound only)             │
└──────────────────────────────────────────────┘
```

### Key Deployment Constraints

- **Single-user:** Streamlit and SQLite are both single-user. No concurrent runs.
- **No container required:** The system runs from `pip install -e .` + `streamlit run app.py`.
- **External dependency:** The `graphify` binary must be available on `PATH` or configured via `GRAPHIFY_BINARY` environment variable.
- **Azure OpenAI:** Requires `AZURE_OPENAI_ENDPOINT`, `AZURE_OPENAI_API_KEY`, `AZURE_OPENAI_DEPLOYMENT`, `AZURE_OPENAI_API_VERSION` in `.env` or environment.

# CodeBase Review — Skill-Pack Agentic System

This document provides a detailed review of every file in the codebase, organised by directory, covering its purpose, importance, key design decisions, and relationship to the rest of the system.

---

## Table of Contents

1. [Root Files](#1-root-files)
2. [Core Engine (`src/agentic/`)](#2-core-engine-srcagentic)
3. [Graph Subsystem (`src/agentic/graph/`)](#3-graph-subsystem-srcagenticgraph)
4. [Skills Subsystem (`src/agentic/skills/`)](#4-skills-subsystem-srcagenticskills)
5. [Schema Definitions (`src/agentic/schemas/`)](#5-schema-definitions-srcagenticschemas)
6. [LLM Integration (`src/agentic/llm/`)](#6-llm-integration-srcagenticllm)
7. [Capabilities — Requirements Pack (`src/agentic/capabilities/`)](#7-capabilities--requirements-pack-srcagenticcapabilities)
8. [Capabilities — Test Generation (`src/agentic/capabilities/testgen/`)](#8-capabilities--test-generation-srcagenticcapabilitiestestgen)
9. [Requirements Skill Pack (`codebase-requirement-skills/`)](#9-requirements-skill-pack-codebase-requirement-skills)
10. [Test Generation Skill Pack (`test-generation-skills/`)](#10-test-generation-skill-pack-test-generation-skills)
11. [Tests (`tests/`)](#11-tests-tests)
12. [File Importance Ranking](#12-file-importance-ranking)

---

## 1. Root Files

### `pyproject.toml`
**Importance:** ★★★★★ (Critical)

The project's single source of truth for metadata, dependencies, and build configuration.

- **Build system:** `setuptools >= 75` with `src/` layout.
- **Python requirement:** `>= 3.11` (uses `StrEnum`, modern type annotations).
- **Key dependencies:** `langgraph >=0.2.60,<1`, `graphifyy[sql] >=0.9.52,<1`, `langchain-openai >=0.2.14,<1`, `pydantic >=2.10,<3`, `pydantic-settings >=2.7,<3`, `streamlit >=1.46,<2`, `pyyaml >=6,<7`.
- **Entry point:** `[project.scripts] agentic = "agentic.cli:main"` — provides the `agentic` CLI command.
- **Test config:** `python_classes = []` prevents pytest from collecting domain `TestCase`/`TestSuite` as test classes. `filterwarnings = "ignore::DeprecationWarning"` keeps output clean.
- **Dev dependencies:** `pytest >=8,<10` and `ruff` for linting.

### `app.py`
**Importance:** ★★★★★ (Critical)

The Streamlit web UI — 782 lines, the only presentation-layer file.

**Key responsibilities:**
1. **Sidebar:** Pack selection, source input (local folder or ZIP upload), Azure OpenAI status, "Run analysis" button.
2. **Execution:** Calls `prepare_run()` and iterates `stream_run()`, updating a progress bar and step-records table in real time.
3. **Pack chaining:** When a run finishes, it looks for successor packs (via `consumes_pack`), pre-fills the sidebar, and offers a "Generate test cases" button above the results.
4. **Tabbed results:** Dynamically shows only the tabs the current run produced: Run (step records, workflow topology), Inventory (file breakdown), Knowledge Graph (entities/relationships), Gates (check results), Requirements (every claim with inline evidence viewing), Test Cases (scenarios/cases with coverage), Deliverables (downloadable Markdown/CSV/Mermaid).
5. **Evidence browsing:** `_render_evidence()` calls `read_slice()` from `capabilities/verify.py` to show the actual source code at cited lines, with 2 lines of context.

**Design notes:**
- The chain mechanism (`PENDING_CHAIN`, `CHAIN_OFFER`, `_apply_pending_chain`) is a Streamlit-specific workaround: widget values cannot be changed after creation, so the pre-fill happens *before* the widgets are built on the next rerun.
- Tab construction is artifact-driven: `_views()` builds tabs based on which artifacts exist in state, so a test-generation run doesn't show an empty "Requirements" tab.

### `.env`
**Importance:** ★★★☆☆ (Configuration)

Runtime configuration for Azure OpenAI credentials. Not committed to VCS (should be in `.gitignore`).

### `README.md`
**Importance:** ★★☆☆☆ (Documentation)

Project documentation for end users.

---

## 2. Core Engine (`src/agentic/`)

### `__init__.py`
**Importance:** ★☆☆☆☆ (Package marker)

Empty. Makes `agentic` an importable package.

### `config.py`
**Importance:** ★★★★★ (Critical)

Centralised configuration via Pydantic Settings. Loads from `.env` or environment variables.

**Key settings:**
- `runs_dir` — where run directories are created (default: `./runs`).
- `azure_openai_endpoint`, `azure_openai_api_key`, `azure_openai_deployment`, `azure_openai_api_version` — LLM credentials.
- `max_clusters`, `evidence_packet_chars`, `max_llm_retries` — cost-control parameters.
- `max_archive_bytes`, `max_archive_files`, `max_uncompressed_bytes`, `max_compression_ratio` — archive safety limits.
- 13 boolean feature flags for consolidation/resolution passes (e.g., `merge_duplicates`, `fold_parameterised`, `strip_leakage_implementation`).

**Design note:** `get_settings()` is `lru_cache`'d so settings are loaded once per process. `extra="ignore"` means unrecognised `.env` variables don't crash the system. `llm_configured` property checks whether all four Azure fields are present.

### `state.py`
**Importance:** ★★★★★ (Critical)

Defines `AgentState`, the LangGraph `TypedDict` that flows through the graph.

**Key design decisions:**
- `Annotated[dict, merge_dict]` and `Annotated[list, operator.add]` define how parallel branches merge. Fan-out workers produce disjoint keys (each worker adds one fragment), so a shallow dict merge and list concatenation are sufficient.
- `initial_state()` provides the constructor.
- The state carries **artifact paths**, not artifact data. This prevents multi-megabyte knowledge graphs from being re-serialised on every checkpoint.

### `store.py`
**Importance:** ★★★★☆ (Important)

`ArtifactStore` — the persistence layer for large payloads.

**Operations:**
- `save(name, data)` — serialises a Pydantic model or dict to JSON on disk.
- `save_text(name, text)` — writes raw text (Markdown, CSV, Mermaid).
- `load_json(path)` — deserialises from JSON.
- `load_model(path, model)` — deserialises and validates as a Pydantic model.

**Why it exists:** LangGraph checkpoints the entire state after every superstep. A 30MB knowledge graph carried inline would be re-serialised on every node. The store keeps payloads on disk and state carries only paths.

### `runtime.py`
**Importance:** ★★★★★ (Critical)

The bridge between the presentation layer and the graph engine.

**Key functions:**
- `available_packs()` — discovers all pack directories under the project root.
- `get_manifest(pack_name)` — loads and caches a pack manifest.
- `preflight(manifest, settings)` — checks LLM configuration before starting.
- `prepare_run(source|archive, settings)` → `PreparedRun` — creates the run directory, copies/extracts source, returns the identity.
- `stream_run(pack, prepared, settings)` → yields `(node_name, state_update)` — compiles the graph, runs it with streaming, yields incremental updates.
- `successor_packs(pack, run_dir)` — finds packs that declare `consumes_pack` matching this pack's name.

**Design note:** `prepare_run` handles both local folders and uploaded archives. Archive extraction calls `extract_zip_safely()` from `capabilities/ingest.py` with full security validation *before* any analysis begins.

### `cli.py`
**Importance:** ★★☆☆☆ (Secondary entry point)

A thin `argparse` CLI wrapping `runtime.py`:
- `agentic packs` — lists discovered packs.
- `agentic show <pack>` — prints manifest and topology.
- `agentic run <pack> <source>` — runs a pack and prints step records.

---

## 3. Graph Subsystem (`src/agentic/graph/`)

### `__init__.py`
**Importance:** ★☆☆☆☆ (Package marker)

Exports `compile_graph` and `describe_topology`.

### `builder.py`
**Importance:** ★★★★★ (Critical)

The **compiler** — transforms a YAML manifest into a LangGraph `StateGraph`. ~550 lines.

**Key functions:**
- `compile_graph(manifest, settings)` → `CompiledGraph` — the main entry point. Walks every step in the manifest and:
  - For `deterministic`/`gate` steps: creates a node wrapping `get_capability(name)`.
  - For `llm` steps: creates a node that assembles a prompt from the skill text and calls `AzureChatOpenAI.with_structured_output(schema)`.
  - For `fan_out` steps: creates a dispatcher node that emits `Send()` messages and a worker node.
- `make_linear_router(step, manifest)` — proceed unless halted.
- `make_gate_router(step, manifest)` — reads `GateReport.valid`, routes to pass/repair/exhausted.
- `make_fanout_router(step, manifest)` — emits `Send(worker_node, item)` for each item.
- `describe_topology(manifest)` — produces a UI-friendly table of the compiled workflow.

**Design note:** This is the only file that imports from both `langgraph` and `capabilities`. It is the narrowest dependency bridge in the system: everything above it speaks manifests, everything below it speaks capabilities.

### `nodes.py`
**Importance:** ★★★★☆ (Important)

Node factory functions:
- `make_node_fn(step, manifest, settings)` — wraps a capability in a standard node that handles context setup, artifact writing, metrics, and step recording.
- `make_llm_node(step, manifest, settings)` — assembles the prompt from skill text + step instructions, calls the model with structured output, returns the result.
- `make_fan_out_node(step, manifest, settings)` — loads the fan-out artifact, returns `Send()` messages for each item.

**Design note:** Every node follows the same wrapper pattern: build `CapabilityContext` → call function → write artifacts → return state update. This uniformity is what makes the graph builder pack-agnostic.

---

## 4. Skills Subsystem (`src/agentic/skills/`)

### `__init__.py`
**Importance:** ★☆☆☆☆ (Re-exports)

Exports `PackManifest`, `StepSpec`, `SkillRegistry`, `LoadedSkill`, and utility functions.

### `pack.py`
**Importance:** ★★★★★ (Critical)

**The manifest model.** 287 lines.

- `PackManifest` — the top-level model with `pack`, `version`, `description`, `action_label`, `master_skill`, `consumes_pack`, `consumes_artifact`, and `steps`.
- `StepSpec` — one step's specification with `id`, `skill`, `mode`, `capability`, `output_schema`, `fan_out`, `consumes`, `produces`, `on_fail`, `repair_step`, `max_repairs`, `exhausted_step`, `next`, `instructions`.
- `StepMode` — `deterministic`, `gate`, or `llm`.
- `OnFail` — `halt`, `repair`, or `warn_and_continue`.

**Validation:** The `@model_validator` on `PackManifest._check_steps()`:
1. No duplicate step IDs.
2. All routing targets (`next`, `repair_step`, `exhausted_step`) point to existing steps.
3. `on_fail: repair` must name a `repair_step`.
4. A step may only `consume` what an earlier step `produces`.

**Key functions:**
- `discover_packs(search_root)` — finds all directories containing `pack.yaml`, keyed by declared `pack:` name.
- `load_pack(pack_dir, validate_references=True)` — parses and fully validates, resolving skills/capabilities/schemas.
- `manifest_summary(manifest)` — a UI-friendly table.

### `registry.py`
**Importance:** ★★★★☆ (Important)

`SkillRegistry` — loads and caches `SKILL.md` files. 132 lines.

- Discovery: `<root>/<skill-name>/SKILL.md` pattern.
- Caching: keyed on `mtime`, so editing a file hot-reloads on the next run.
- `LoadedSkill.section(heading)` — extracts a specific Markdown section by heading match, with casefold comparison. Falls back to the whole body if no match is found (prevents silent emptying on typos).
- Content hashing: `sha256` of the raw file content for audit trails.

---

## 5. Schema Definitions (`src/agentic/schemas/`)

### `__init__.py`
**Importance:** ★☆☆☆☆ (Re-exports)

### `registry.py`
**Importance:** ★★★★☆ (Important)

The schema registry — maps string names to Pydantic model classes. Used by the graph builder to resolve `output_schema` in the manifest.

```python
SCHEMAS: dict[str, type[BaseModel]] = {}
@schema("BRDFragment")   # registers the class
get_schema("BRDFragment") → type[BaseModel]  # resolves at graph build time
```

**Design note:** Lazy loading via `load_builtin_schemas()` — imports `brd` and `testspec` modules on first access, so importing the registry alone is cheap and cycle-free.

### `core.py`
**Importance:** ★★★★★ (Critical)

Foundational data contracts shared across both packs. ~500 lines.

**Key models:**
- `SourceLocation` — a file:line citation with `compact()` formatting.
- `Entity` — a code entity (function, class, method, etc.) with structural metadata. The `kind` field uses a string enum covering FILE, CLASS, FUNCTION, METHOD, PROPERTY, CONSTANT, DECORATOR, MODULE.
- `Relationship` — a directed edge between entities (CONTAINS, CALLS, IMPORTS, INHERITS, etc.).
- `InteractionBoundary` — an externally-reachable entry point with `entry_class` (ENDPOINT, CLI_COMMAND, EVENT_CONSUMER, etc.), `identifier` (e.g., `GET /api/users`), and `effect` (read/mutate).
- `KnowledgeGraph` — the complete structural extraction: entities, relationships, entry points, extraction coverage, declared documentation.
- `GateReport` — validation results with issues, severity levels, metrics.
- `RepositoryInventory` — file-level inventory with language classification and parseability flags.

### `brd.py`
**Importance:** ★★★★★ (Critical)

The BRD (Business Requirements Document) schema. **1058 lines** — the largest schema file.

**Why it's so large:** Field descriptions are not just documentation — they are the model's instructions. When `BRDFragment` is used as `output_schema` in a `with_structured_output()` call, every field description is included in the JSON schema the model sees. The 383-character description on `abstraction_basis` *is* the abstraction test prompt.

**Key elements:**
- `BASIS_TYPES` — the fixed mapping from abstraction basis to requirement tier (7 entries).
- `TIER_SECTIONS`, `TIER_PREFIXES`, `PARENT_RULES` — lookup tables governing the classification hierarchy.
- `TIER_ALIASES` — normalisation table for model outputs (21 entries mapping variant names to canonical tiers).
- `BRDItem` — the universal claim model with ~25 fields covering id, title, statement, evidence, classification, hierarchy, quality, and system-assigned metadata.
- `BRDFragment` — fan-out worker output with 12 requirement lists spanning functional, system, rules, constraints, observations, data, integration, NFR, and reporting.
- `TopTier` — the whole-repository judgement (purpose, outcomes, bindings).
- `ProductBRD` — the merged document with `assign_ids()` (deterministic ID stamping + parent resolution), section/supplement accessors, and utility methods.
- `RepairedItems` — repair pass output carrying only fixed items (not the whole document).

**Design note:** `ProductBRD.area_names` is stored as `list[AreaLabel]` rather than `dict[str, str]` because a `dict` field becomes an open-ended JSON-schema object, and Azure OpenAI's strict structured output drops it — then rejects the entire schema.

### `testspec.py`
**Importance:** ★★★★★ (Critical)

Test specification schema. 475 lines.

**Key models:**
- `TestSignal` — pre-design analysis per requirement (band, route, dimensions, risk, budget).
- `TestCase` — technology-independent: names no framework, runner, assertion library, protocol, or language.
- `TestScenario` — groups cases by situation, sharing preconditions.
- `TestSuite` — full output with scenarios, deferred requirements, and notes.
- `TestCaseDraft` / `TestScenarioDraft` — what the model returns (judgement only; IDs and derived fields added by assembly).
- `TestDesignFragment` — fan-out worker output.
- `RepairedTestCase` — repair output with only the fields a repair can change (8 vs. 20 fields, shrinking the reply by ~⅔).
- `CoverageReport` — per-requirement and per-criterion coverage analysis.

**Design note:** Advisory metadata (`interface_hint`, `automation_readiness`) is computed deterministically at assembly and is **never branched on** within the pack. They exist for a future framework-specific adapter.

---

## 6. LLM Integration (`src/agentic/llm/`)

### `__init__.py`
**Importance:** ★☆☆☆☆ (Re-exports)

### `client.py`
**Importance:** ★★★★☆ (Important)

Azure OpenAI client construction. ~52 lines.

- `build_chat_model(settings)` → `AzureChatOpenAI` — constructs the LangChain chat model from settings.
- `@lru_cache(maxsize=4)` — avoids re-creating the client per step (TCP connection reuse).
- Raises `ValueError` on missing credentials with a clear message.

### `prompt.py`
**Importance:** ★★★★☆ (Important)

Prompt assembly logic. ~120 lines.

- `build_system_prompt(manifest, step, registry)` — concatenates:
  1. The master skill's body (if declared).
  2. The step's skill body (or a specific section via `step.section`).
  3. The step's `instructions` field from the manifest.
- `build_evidence_prompt(evidence_packet)` — formats the evidence data for the human message.

**Design note:** The prompt is constructed entirely from on-disk SKILL.md files and manifest instructions. There are no prompt string literals anywhere in the codebase — every instruction is in a file that can be edited and hot-reloaded.

---

## 7. Capabilities — Requirements Pack (`src/agentic/capabilities/`)

### `__init__.py`
**Importance:** ★☆☆☆☆ (Re-exports)

### `registry.py`
**Importance:** ★★★★★ (Critical)

The capability registry — the Strategy Pattern that keeps the graph code pack-agnostic. 164 lines.

- `@capability("name")` — decorator that registers a function.
- `CapabilityContext` — the parameter object every capability receives: state, step, settings, store, skill, payload.
- `CapabilityResult` — the return type: artifacts, files, metrics, messages, gate, fragment, halt_reason.
- `get_capability(name)` — resolves by name, lazy-loading all modules on first access.
- `load_builtin_capabilities()` — imports 17 requirements modules + 10 testgen modules for side-effect registration.

### `ingest.py`
**Importance:** ★★★★★ (Critical)

Phase 0 — safe ingestion and inventory. 279 lines.

**Capabilities:** `ingest_repository`

**Key responsibilities:**
1. **`ArchiveValidationError` and `extract_zip_safely()`** — the security boundary. Validates the entire ZIP member list before writing anything: path traversal, absolute paths, symlinks, encrypted entries, compression ratios, total size/file limits.
2. **`build_inventory()`** — walks the source tree, classifies files by language (36 extensions), computes SHA-256 hashes, marks parseability. Ignores build output directories (`.git`, `node_modules`, `__pycache__`, etc.).
3. **`ingest_repository(ctx)`** — the capability function. Halts if no files found or no parseable source in any supported language.

**Design note:** `PARSEABLE_LANGUAGES` lists 14 language families that `graphify` has tree-sitter grammars for. This used to be `{"Python"}` — expanding it removed the Python-only limitation.

### `graphify.py`
**Importance:** ★★★★★ (Critical)

Graphify integration. ~46KB — the largest capability file.

**Capabilities:** `graphify_extract`, `adapt_graphify_graph`

**Key responsibilities:**
1. **`graphify_extract`** — runs the external `graphify` binary with `--code-only`, captures stdout, saves raw extraction.
2. **`adapt_graphify_graph`** — normalises Graphify's output into the canonical `KnowledgeGraph` schema. This includes:
   - Reconstructing end-line numbers (Graphify reports start lines only).
   - Mapping Graphify's entity/relationship types to canonical types.
   - Merging FILE entities from the inventory.
   - Computing extraction coverage.

### `boundaries.py`
**Importance:** ★★★★☆ (Important)

**Capabilities:** `propose_boundary_candidates`

Identifies candidate interaction boundaries from graph shape: a handler invoked by a framework (never by code in this repository) has no inbound calls from the codebase itself. Emits candidate data (qualified name, referenced-but-undefined names, literals, module-level flag) for the LLM classifier.

### `entry_points.py`
**Importance:** ★★★★☆ (Important)

**Capabilities:** `discover_entry_points`

Merges the LLM classifier's boundary assignments with registered adapters (syntactic detection). Creates `InteractionBoundary` entries on the knowledge graph. Produces an entry-point coverage report.

### `clustering.py`
**Importance:** ★★★★★ (Critical)

**Capabilities:** `build_boundary_clusters`, `cluster_capabilities`, `digest_areas`

1. **`build_boundary_clusters`** — partitions the knowledge graph around interaction boundaries to create evidence packets. This is the **cost-control mechanism**: each packet is capped at `evidence_packet_chars` and contains only the entities/relationships reachable from one boundary.
2. **`cluster_capabilities`** — groups candidate requirements across all fragments by semantic similarity (same title, overlapping evidence). This is the step whose absence made the output read as a behaviour inventory.
3. **`digest_areas`** — creates a compact summary (area codes + requirement titles) for the top-tier step.

### `claims.py`
**Importance:** ★★★☆☆ (Supporting)

**Capabilities:** `prepare_claims`

Deterministic preparation before the gate: stamps identifiers via `brd.assign_ids()`, corrects citation labels the model mis-filled. Never touches a statement, file path, or line range.

### `assemble.py`
**Importance:** ★★★★☆ (Important)

**Capabilities:** `assemble_requirements`, `apply_repairs`

1. **`assemble_requirements`** — merges all `BRDFragment`s with the `TopTier` into a single `ProductBRD`. Every grounded finding from every cluster is carried through unchanged.
2. **`apply_repairs`** — merges repaired items back into the BRD by ID. Claims the gate never rejected cannot be altered.

### `consolidate.py`
**Importance:** ★★★★☆ (Important)

**Capabilities:** `consolidate_requirements`

The level-deciding step: detects duplicates, parameterised families, and items that should be folded. Each decision is individually toggleable via feature flags in `Settings`.

### `correspondence.py`
**Importance:** ★★★★☆ (Important)

**Capabilities:** `validate_correspondence`

Compares each claim against the code it cites. Checks whether the claim's statement is entailed by the evidence at the cited lines. Records `CorrespondenceOutcome` on each item.

### `confidence.py`
**Importance:** ★★★☆☆ (Supporting)

**Capabilities:** `recalibrate_confidence`

Recomputes confidence from evidence (number of citations, correspondence outcome, specificity) rather than trusting the model's self-reported confidence.

### `leakage.py`
**Importance:** ★★★☆☆ (Supporting)

**Capabilities:** `review_implementation_leakage`

Detects technical vocabulary in requirement statements (file names, function names, framework names) and moves it to `implementation_observations`. Runs before the gate so a wording problem doesn't delete a grounded requirement.

### `resolution.py`
**Importance:** ★★★★☆ (Important)

**Capabilities:** `resolve_candidates`

Gives every candidate a final disposition: `keep`, `reclassify`, `observation`, `open_question`, or `reject`. Moves unresolvable items to `unresolved_candidates`.

### `hedging.py`
**Importance:** ★★★☆☆ (Supporting)

**Capabilities:** `hedge_requirements`, `validate_synthesis`

1. **`hedge_requirements`** — derives acceptance criteria and records assumptions/open questions once the set is final.
2. **`validate_synthesis`** — the final check: detects duplicate titles, dangling relations, cycles, unsettled candidates.

### `gates.py`
**Importance:** ★★★★★ (Critical)

**Capabilities:** `validate_graph`, `validate_requirements`

1. **`validate_graph`** — Gate 1. Checks entity/relationship counts, extraction coverage, ambiguity ratios.
2. **`validate_requirements`** — Gate 2. Checks cited files exist, cited lines are plausible, statements use business language (no technical vocabulary).

### `quarantine.py`
**Importance:** ★★★☆☆ (Supporting)

**Capabilities:** `quarantine_requirements`

Reached when the repair budget is spent. Removes claims the gate rejected so an honest, thinner document can still be produced. Deterministic — no model gets to argue.

### `render.py`
**Importance:** ★★★★☆ (Important)

**Capabilities:** `render_deliverables`

Generates the final output files: `requirements.md`, `requirements.json`, `traceability.csv`, `gaps.md`, `workflows.mmd`.

### `verify.py`
**Importance:** ★★☆☆☆ (Utility)

Contains `read_slice()` — reads source code at cited lines with context. Used by the Streamlit UI's evidence browser to show inline code.

---

## 8. Capabilities — Test Generation (`src/agentic/capabilities/testgen/`)

### `__init__.py`
**Importance:** ★☆☆☆☆ (Package marker)

### `ingest.py`
**Importance:** ★★★★☆ (Important)

**Capabilities:** `ingest_requirement_set`

Reads `requirements.json` from the source root. Does NOT reuse `ingest_repository` from the requirements pack (which would halt on a directory with no source code).

### `qualify.py`
**Importance:** ★★★★☆ (Important)

**Capabilities:** `qualify_requirements`

Bands requirements and detects/discards self-generated acceptance criteria from the requirements pack (which assert nothing and would produce tests that check nothing).

### `analyse.py`
**Importance:** ★★★★★ (Critical)

**Capabilities:** `analyse_test_signals`

The anti-explosion mechanism. Deterministically decides which dimensions each requirement activates, risk levels, case budgets, and routing (generate / attach / defer). Reduced projected case counts by ~40% on a measured run.

### `batching.py`
**Importance:** ★★★☆☆ (Supporting)

**Capabilities:** `build_test_batches`

Groups qualified requirements into bounded batches by area for parallel LLM calls.

### `assemble.py`
**Importance:** ★★★★☆ (Important)

**Capabilities:** `assemble_test_specs`

Stamps identifiers, computes derived fields (interface hints, automation readiness), resolves coverage links, drops untraceable cases.

### `dedupe.py`
**Importance:** ★★★☆☆ (Supporting)

**Capabilities:** `deduplicate_test_cases`

Folds cases that check the same behaviour. Records superseded IDs so merges are visible.

### `coverage.py`
**Importance:** ★★★★☆ (Important)

**Capabilities:** `analyse_test_coverage`

Accounts for every requirement (covered or not) and every acceptance criterion (checked or not). Produces verdicts: sufficient, positive_only, thin, none, deferred, excluded.

### `gates.py`
**Importance:** ★★★★☆ (Important)

**Capabilities:** `validate_requirement_input`, `validate_test_specs`, `validate_test_suite`

Quality gates for the test pack: input validation, case quality checks, final suite integrity.

### `validate.py`
**Importance:** ★★★☆☆ (Supporting)

**Capabilities:** `apply_test_repairs`, `prune_test_cases`

Repair application (merges fixes by ID) and budget-exhausted pruning.

### `render.py`
**Importance:** ★★★☆☆ (Supporting)

**Capabilities:** `render_test_deliverables`

Generates test output files: `test-specifications.md`, `test-cases.json`, `coverage.md`.

---

## 9. Requirements Skill Pack (`codebase-requirement-skills/`)

### `pack.yaml`
**Importance:** ★★★★★ (Critical)

The requirements workflow manifest. **626 lines** — this is the largest YAML file and the single most information-dense file in the project. Its comments document not just what each step does but *why* each design decision was made.

21 steps defining the complete requirements extraction pipeline. Each step's inline `instructions:` field (especially on `synthesize` at ~190 lines) is effectively a prompt engineering artifact embedded in configuration.

### SKILL.md files (5)

Each is a SKILL.md inside a named subdirectory:

| Skill | Size | Purpose |
|-------|------|---------|
| `codebase-to-requirements-reverse-engineering/SKILL.md` | 16KB | Master skill: grounding rules, abstraction test, evidence-only claims |
| `evidence-grounded-requirement-synthesis/SKILL.md` | 27KB | Synthesis and assembly rules: how to write requirements, acceptance criteria, and how to work the classification model |
| `graphify-knowledge-graph-extraction/SKILL.md` | ~8KB | Graphify integration instructions, boundary classification guidance |
| `requirement-and-graph-validation-gates/SKILL.md` | ~6KB | Gate criteria: what makes a valid graph, what makes a valid requirement |
| `requirement-consolidation-and-resolution/SKILL.md` | ~12KB | Consolidation rules: deduplication, level-setting, parent resolution |
| `traceable-requirements-deliverables/SKILL.md` | ~5KB | Rendering rules: document format, traceability matrix structure |

---

## 10. Test Generation Skill Pack (`test-generation-skills/`)

### `pack.yaml`
**Importance:** ★★★★★ (Critical)

The test generation workflow manifest. **291 lines.** 14 steps, declares `consumes_pack: codebase-requirements` and `consumes_artifact: requirements.json`.

### SKILL.md files (5)

| Skill | Purpose |
|-------|---------|
| `requirements-to-test-derivation/SKILL.md` | Master skill: derivation rules, technology independence, what makes a valid test |
| `risk-based-test-design/SKILL.md` | Signal analysis rules, dimension activation, budget allocation |
| `test-input-qualification/SKILL.md` | Banding, criterion quality detection, routing |
| `test-coverage-and-duplication-gates/SKILL.md` | Gate criteria, deduplication rules, coverage measurement |
| `traceable-test-deliverables/SKILL.md` | Rendering rules for test documents |

---

## 11. Tests (`tests/`)

### `conftest.py`
**Importance:** ★★★★★ (Critical for testing)

Test infrastructure. 217 lines.

**Key fixtures:**
- `manifest` (session-scoped) — loads the real requirements pack manifest.
- `settings` — test settings with small limits.
- `run_dir` — a temp directory with a copy of the `fixtures/sample_app`.
- `state` — a valid initial `AgentState`.
- `runner` — a function that runs individual manifest steps with real capabilities.
- `stub_boundary_assignments()` — a deterministic stand-in for the LLM boundary classifier, classifying from the same evidence the real model would see.
- `run_step_or_stub()` — runs any step, stubbing only the LLM call.

**Design note:** `EXTRACTION_STEPS` is a tuple of 6 step names — every test that needs a usable graph runs all of them. This is the integration test backbone.

### `harness/__init__.py` + `harness/metrics.py`
**Importance:** ★★★★☆ (Important for quality measurement)

Metrics harness for corpus-level measurement.

Two modes:
- **Structural** (`measure_structure`) — runs deterministic prefix only (no model), measures entry-point detection and selection. Cheap and used in tests.
- **Document** (`measure_document`) — reads finished `requirements.json`, measures duplication, citation resolution, language coverage. Expensive, runs against saved artifacts.

### `fixtures/sample_app/`
**Importance:** ★★★★☆ (Important for testing)

A fixture codebase with multiple Python files representing a small web application. Contains routes, CLI commands, scheduled jobs, and other patterns that exercise the boundary detection logic.

### Test files (`test_*.py`)
**Importance:** ★★★★☆ (Important)

680+ tests covering:
- Manifest parsing and validation
- Archive security (zip bombs, path traversal, symlinks, encryption)
- Inventory building and language classification
- Graphify adapter normalisation
- Boundary proposal and classification
- Clustering and evidence packet construction
- BRD schema validation and ID assignment
- Gate logic
- Consolidation passes
- Test pack qualification, signal analysis, batching, assembly, coverage
- End-to-end pipeline integration

---

## 12. File Importance Ranking

### Tier 1 — Understand these to understand the system

| File | Why |
|------|-----|
| `codebase-requirement-skills/pack.yaml` | The workflow IS this file. Everything else serves it. |
| `src/agentic/graph/builder.py` | The compiler that turns YAML into executable graphs. |
| `src/agentic/capabilities/registry.py` | The Strategy Pattern bridging manifests to logic. |
| `src/agentic/schemas/brd.py` | The domain model AND the model's instructions. |
| `src/agentic/runtime.py` | The execution lifecycle. |

### Tier 2 — Understand these for depth

| File | Why |
|------|-----|
| `src/agentic/config.py` | Every tuneable parameter. |
| `src/agentic/state.py` | How parallel branches merge. |
| `src/agentic/store.py` | Why state carries paths, not data. |
| `src/agentic/capabilities/ingest.py` | The security boundary. |
| `src/agentic/capabilities/clustering.py` | The cost-control mechanism. |
| `src/agentic/capabilities/gates.py` | The quality enforcement mechanism. |
| `src/agentic/schemas/testspec.py` | The test domain model. |
| `test-generation-skills/pack.yaml` | The second workflow. |

### Tier 3 — Understand these for specific capability detail

All other capability files, the LLM integration files, the test files, and the SKILL.md files.

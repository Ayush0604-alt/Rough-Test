# 📁 Source Code Documentation — `src/`

> Complete reference for every file inside `src/agentic/`. Each entry describes the file's purpose, the functions and classes it exposes, and how it fits into the overall architecture.

---

## Table of Contents

- [Root Package](#root-package)
  - [`__init__.py`](#srcinit)
  - [`cli.py`](#srccli)
  - [`config.py`](#srcconfig)
  - [`runtime.py`](#srcruntime)
  - [`state.py`](#srcstate)
  - [`store.py`](#srcstore)
- [Graph Package (`graph/`)](#graph-package)
  - [`__init__.py`](#graphinit)
  - [`builder.py`](#graphbuilder)
  - [`nodes.py`](#graphnodes)
  - [`router.py`](#graphrouter)
- [LLM Package (`llm/`)](#llm-package)
  - [`__init__.py`](#llminit)
  - [`client.py`](#llmclient)
  - [`prompt.py`](#llmprompt)
  - [`steps.py`](#llmsteps)
- [Schemas Package (`schemas/`)](#schemas-package)
  - [`__init__.py`](#schemasinit)
  - [`core.py`](#schemascore)
  - [`registry.py`](#schemasregistry)
  - [`boundaries.py`](#schemasboundaries)
  - [`brd.py`](#schemasbrd)
  - [`testgen.py`](#schemastestgen)
  - [`testspec.py`](#schemastestspec)
- [Skills Package (`skills/`)](#skills-package)
  - [`__init__.py`](#skillsinit)
  - [`pack.py`](#skillspack)
  - [`registry.py`](#skillsregistry)
- [Capabilities Package (`capabilities/`)](#capabilities-package)
  - [`__init__.py`](#capabilitiesinit)
  - [`registry.py`](#capabilitiesregistry)
  - [`ingest.py`](#capabilitiesingest)
  - [`graphify.py`](#capabilitiesgraphify)
  - [`graph_ops.py`](#capabilitiesgraph_ops)
  - [`boundaries.py`](#capabilitiesboundaries)
  - [`entry_points.py`](#capabilitiesentry_points)
  - [`clustering.py`](#capabilitiesclustering)
  - [`claims.py`](#capabilitiesclaims)
  - [`assemble.py`](#capabilitiesassemble)
  - [`consolidate.py`](#capabilitiesconsolidate)
  - [`confidence.py`](#capabilitiesconfidence)
  - [`hedging.py`](#capabilitieshedging)
  - [`leakage.py`](#capabilitiesleakage)
  - [`resolution.py`](#capabilitiesresolution)
  - [`correspondence.py`](#capabilitiescorrespondence)
  - [`gates.py`](#capabilitiesgates)
  - [`quarantine.py`](#capabilitiesquarantine)
  - [`render.py`](#capabilitiesrender)
  - [`verify.py`](#capabilitiesverify)
  - [`jargon.py`](#capabilitiesjargon)
- [Capabilities — Test Generation Subpackage (`capabilities/testgen/`)](#capabilities-testgen-subpackage)
  - [`__init__.py`](#testgeninit)
  - [`ingest.py`](#testgeningest)
  - [`gates.py`](#testgengates)
  - [`qualify.py`](#testgenqualify)
  - [`analyse.py`](#testgenanalyse)
  - [`signals.py`](#testgensignals)
  - [`batching.py`](#testgenbatching)
  - [`assemble.py`](#testgenassemble)
  - [`dedupe.py`](#testgendedupe)
  - [`coverage.py`](#testgencoverage)
  - [`validate.py`](#testgenvalidate)
  - [`quality.py`](#testgenquality)
  - [`render.py`](#testgenrender)

---

## Root Package

<a id="srcinit"></a>
### `src/agentic/__init__.py`

| Field | Detail |
|-------|--------|
| **Purpose** | Top-level package initialiser for the `agentic` system. |
| **Size** | 324 bytes / 9 lines |

**Description:**
Declares the package-level docstring and version. The system is described as a *skill-pack-driven agentic system* where a skill pack is a directory of `SKILL.md` files plus a `pack.yaml` manifest that compiles into a LangGraph `StateGraph`.

**Exports:**
| Name | Type |
|------|------|
| `__version__` | `str` — currently `"0.1.0"` |

---

<a id="srccli"></a>
### `src/agentic/cli.py`

| Field | Detail |
|-------|--------|
| **Purpose** | Command-line entry point for running a pack headlessly or inspecting one. |
| **Size** | 3,370 bytes / 93 lines |

**Description:**
Provides the `agentic` CLI with three subcommands: `packs` (list available packs), `show` (display a pack's compiled workflow topology), and `run` (execute a pack against a codebase directory or `.zip` archive). Supports both directory and archive inputs.

**Functions:**

| Function | Signature | Description |
|----------|-----------|-------------|
| `main` | `(argv: list[str] \| None = None) -> int` | Parses CLI args, dispatches to the correct subcommand (`packs`, `show`, `run`), handles archive vs directory input, streams execution, and prints results. Returns `0` on success, `1` on failure. |

**Entry point:** `if __name__ == "__main__"` invokes `main()`.

---

<a id="srcconfig"></a>
### `src/agentic/config.py`

| Field | Detail |
|-------|--------|
| **Purpose** | Runtime settings, loaded from the environment or a local `.env` file. |
| **Size** | 4,603 bytes / 111 lines |

**Description:**
Centralises all configuration using Pydantic's `BaseSettings`. Covers archive ingestion limits, Graphify extractor settings, Azure OpenAI LLM credentials, cost defence parameters (evidence packet size, cluster limits), and 13 boolean feature flags controlling consolidation/resolution passes.

**Classes:**

| Class | Description |
|-------|-------------|
| `Settings` | Pydantic `BaseSettings` subclass with all configuration fields. |

**Key `Settings` Fields:**

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `runs_dir` | `Path` | `runs/` | Base directory for run outputs |
| `max_archive_bytes` | `int` | 50 MB | Upload size limit |
| `max_uncompressed_bytes` | `int` | 200 MB | Expanded archive limit |
| `max_archive_files` | `int` | 10,000 | File count limit |
| `graphify_binary` | `str` | `""` | Path to Graphify executable |
| `azure_openai_endpoint` | `str` | `""` | Azure OpenAI endpoint |
| `azure_openai_api_key` | `str` | `""` | Azure OpenAI API key |
| `azure_openai_deployment` | `str` | `""` | Azure OpenAI deployment name |
| `llm_temperature` | `float` | `0.0` | Model temperature |
| `evidence_packet_chars` | `int` | 40,000 | Max chars per evidence packet |
| `max_clusters` | `int` | 80 | Max interaction boundaries analysed |
| `max_repairs` | `int` | 2 | Bounded repair loop limit |
| *13 boolean flags* | `bool` | `True` | Feature toggles for consolidation passes |

**Methods / Properties:**

| Name | Description |
|------|-------------|
| `llm_configured` | Property — `True` only when all four Azure OpenAI settings are present. |
| `missing_llm_settings()` | Returns names of missing Azure OpenAI env vars. |

**Module-level Functions:**

| Function | Description |
|----------|-------------|
| `get_settings()` | Cached singleton — returns the `Settings` instance (loaded once). |

---

<a id="srcruntime"></a>
### `src/agentic/runtime.py`

| Field | Detail |
|-------|--------|
| **Purpose** | Orchestrates running a pack against a target codebase — preparation, compilation, streaming, and execution. |
| **Size** | 7,430 bytes / 198 lines |

**Description:**
Handles everything that must happen before graph state exists: resolving the target codebase into a directory (or extracting a `.zip`), creating the run directory, discovering successor packs for chaining, and wiring the LangGraph execution with SQLite checkpointing.

**Classes:**

| Class | Description |
|-------|-------------|
| `PreparedRun` | Dataclass holding `run_id`, `run_dir`, `source_root`, `repository_name`. |

**Functions:**

| Function | Signature | Description |
|----------|-----------|-------------|
| `safe_name` | `(value: str) -> str` | Sanitises a filename into a safe repository name. |
| `available_packs` | `(search_root) -> dict[str, Path]` | Discovers all packs under a root directory. |
| `get_manifest` | `(pack, search_root) -> PackManifest` | Loads and validates a named pack's manifest. |
| `successor_packs` | `(pack, run_dir, search_root) -> list[tuple[str, Path]]` | Finds packs that declare they consume this pack's output and whose required artifact exists. |
| `prepare_run` | `(*, source, archive, archive_name, settings) -> PreparedRun` | Creates run directory, resolves target (directory copy or archive extraction). |
| `preflight` | `(manifest, settings) -> list[str]` | Detects problems (e.g., missing LLM credentials) before work starts. |
| `stream_run` | `(pack, prepared, *, settings, search_root) -> Iterator[tuple[str, dict]]` | Executes a pack, yielding `(node_name, update)` as each step completes. |
| `run` | `(pack, prepared, *, settings, search_root) -> AgentState` | Executes a pack to completion and returns the final state. |
| `_checkpointer` | `(settings) -> Iterator[Any]` | Context manager providing a SQLite checkpointer for LangGraph. |

---

<a id="srcstate"></a>
### `src/agentic/state.py`

| Field | Detail |
|-------|--------|
| **Purpose** | Defines the shared graph state used by every pack — pack-agnostic by design. |
| **Size** | 3,030 bytes / 101 lines |

**Description:**
The state never mentions requirements or any pack-specific vocabulary. Steps exchange *artifacts* (logical names mapped to files on disk). Large payloads live in `run_dir` as paths, not inline, to keep LangGraph checkpoint serialisation cheap.

**Types:**

| Type | Description |
|------|-------------|
| `RunStatus` | Literal type: `"running"`, `"passed"`, `"passed_with_warnings"`, `"failed"` |

**Classes:**

| Class | Description |
|-------|-------------|
| `StepRecord` | TypedDict — one row of the run's audit trail (step name, skill hash, mode, status, duration, messages). |
| `AgentState` | TypedDict — the full graph state with identity, artifacts, progress, fragments, gate reports, repair counter, and terminal status. |

**Key `AgentState` Channels:**

| Channel | Type | Reducer | Description |
|---------|------|---------|-------------|
| `artifacts` | `dict[str, str]` | `merge_dict` | Logical name → path mapping |
| `completed_steps` | `list[str]` | `operator.add` | Ordered step completion list |
| `step_records` | `list[StepRecord]` | `operator.add` | Audit trail |
| `fragments` | `list[dict]` | `operator.add` | Fan-out worker outputs |
| `gate_reports` | `dict[str, Any]` | `merge_dict` | Gate outcomes keyed by step id |
| `repair_attempts` | `int` | — | Bounded repair loop counter |
| `status` | `RunStatus` | — | Terminal condition |

**Functions:**

| Function | Signature | Description |
|----------|-----------|-------------|
| `merge_dict` | `(left, right) -> dict` | Last-writer-wins shallow merge reducer for dict channels. |
| `initial_state` | `(*, run_id, pack, run_dir, source_root, repository_name) -> AgentState` | Creates a fresh initial state with all channels zeroed. |

---

<a id="srcstore"></a>
### `src/agentic/store.py`

| Field | Detail |
|-------|--------|
| **Purpose** | On-disk artifact storage for a single run. Steps exchange large payloads through here rather than graph state. |
| **Size** | 2,449 bytes / 69 lines |

**Description:**
Provides a simple interface for saving and loading named artifacts (JSON, Pydantic models, or raw text) under a run's `artifacts/` directory. Pydantic models are dumped in JSON mode for safe round-tripping.

**Classes:**

| Class | Description |
|-------|-------------|
| `ArtifactStore` | Reads and writes named artifacts under `<run_dir>/artifacts/`. |

**Methods on `ArtifactStore`:**

| Method | Description |
|--------|-------------|
| `ensure()` | Creates the artifacts directory if it doesn't exist. |
| `path_for(name)` | Returns `artifacts/<name>.json`. |
| `save(name, value)` | Persists an artifact (Pydantic model or plain dict) as JSON. Returns the path. |
| `save_text(relative_name, text)` | Writes a rendered deliverable (Markdown, CSV, Mermaid) verbatim. |
| `load_json(path_or_name)` | Loads a JSON artifact by path or logical name. |
| `load_model(path_or_name, model)` | Loads and validates a Pydantic model from a JSON artifact. |
| `read_text(path)` | Reads a file as UTF-8 text. |

---

## Graph Package

<a id="graphinit"></a>
### `src/agentic/graph/__init__.py`

| Field | Detail |
|-------|--------|
| **Purpose** | Package re-exports for the graph builder. |
| **Size** | 157 bytes / 4 lines |

**Exports:** `build_state_graph`, `compile_graph`, `describe_topology`

---

<a id="graphbuilder"></a>
### `src/agentic/graph/builder.py`

| Field | Detail |
|-------|--------|
| **Purpose** | Compiles a pack manifest into a LangGraph `StateGraph`. Nothing in this module names a step, skill, or artifact — every decision comes from the manifest. |
| **Size** | 4,607 bytes / 130 lines |

**Functions:**

| Function | Signature | Description |
|----------|-----------|-------------|
| `build_state_graph` | `(manifest, settings) -> StateGraph` | Constructs the full graph: adds nodes for each step, wires linear/gate/fan-out edges, and sets entry point. |
| `compile_graph` | `(manifest, settings, checkpointer) -> Any` | Builds and compiles the graph with an optional checkpointer. |
| `describe_topology` | `(manifest) -> list[dict]` | Human-readable edge list for UI display. Each row has `step`, `mode`, `skill`, `on_success`, `on_failure`, `fan_out`. |
| `_targets` | `(names) -> dict[str, str]` | Creates a path map for conditional edges (identity mapping). |

---

<a id="graphnodes"></a>
### `src/agentic/graph/nodes.py`

| Field | Detail |
|-------|--------|
| **Purpose** | Node factories for the three step shapes (deterministic, gate, LLM). |
| **Size** | 11,298 bytes / 296 lines |

**Description:**
Three node shapes cover every step any pack can declare. A node's job: build a context, run the step, persist what it produced, and record what happened. Handles error recovery for fan-out workers and repair targets.

**Constants:**

| Name | Value | Description |
|------|-------|-------------|
| `HALT_NODE` | `"__halt__"` | Sentinel node name for failed gates or stopped runs. |

**Types:**

| Name | Description |
|------|-------------|
| `NodeFn` | `Callable[[AgentState], dict[str, Any]]` — the signature every graph node conforms to. |

**Functions:**

| Function | Description |
|----------|-------------|
| `_context(state, step, manifest, settings)` | Builds a `CapabilityContext` from the current state and step. |
| `_record(step, ctx, status, started, messages)` | Creates a `StepRecord` with timing, skill hash, and messages. |
| `_apply(result, ctx, step)` | Persists a capability's output and translates it into a state update (artifacts, fragments, gate reports, metrics). |
| `make_capability_node(step, manifest, settings)` | Factory for deterministic/gate steps — runs the registered capability, handles errors and `on_fail: warn`. |
| `make_llm_node(step, manifest, settings, *, is_repair_target)` | Factory for model steps — loads the skill, assembles evidence, enforces the output schema. Handles fan-out failures and repair targets gracefully. |
| `make_halt_node()` | Creates the terminal node — writes an honest failure record with gate details into `run_failed.md`. |
| `make_finalise_node(manifest)` | Creates the finalisation node — sets terminal status (`passed` or `passed_with_warnings`). |
| `make_node(step, manifest, settings, *, is_repair_target)` | Dispatcher — returns `make_llm_node` for LLM steps, `make_capability_node` for everything else. |

---

<a id="graphrouter"></a>
### `src/agentic/graph/router.py`

| Field | Detail |
|-------|--------|
| **Purpose** | Deterministic routing — never involves a model. Routing reads the manifest and state only. |
| **Size** | 3,794 bytes / 100 lines |

**Description:**
A failed gate cannot be reasoned past, because routing is structural rather than interpretive. Three router factories cover: linear steps, gate steps (pass/repair/halt), and fan-out dispatch (one parallel worker per item via `Send`).

**Constants:**

| Name | Value |
|------|-------|
| `FINALISE_NODE` | `"__finalise__"` |

**Functions:**

| Function | Description |
|----------|-------------|
| `_halted(state)` | Returns `True` if the run has failed or has a halt reason. |
| `_successor(manifest, step)` | Returns the next step on the happy path, or `FINALISE_NODE`. |
| `make_linear_router(manifest, step)` | Routes onward unless the step halted the run. |
| `make_gate_router(manifest, step)` | Routes based on gate outcome: pass → successor, warn → successor, repair → repair step (bounded), exhausted → exhausted step or halt, halt → halt. |
| `make_fanout_router(manifest, source, target)` | Dispatches one `Send(target_id, {state, work_item})` per item in the fan-out artifact. Skips if the artifact is empty. |

---

## LLM Package

<a id="llminit"></a>
### `src/agentic/llm/__init__.py`

| Field | Detail |
|-------|--------|
| **Purpose** | Package re-exports for the LLM subsystem. |
| **Size** | 177 bytes / 5 lines |

**Exports:** `LLMNotConfigured`, `build_chat_model`, `run_llm_step`

---

<a id="llmclient"></a>
### `src/agentic/llm/client.py`

| Field | Detail |
|-------|--------|
| **Purpose** | Azure OpenAI chat client construction with caching. |
| **Size** | 1,602 bytes / 52 lines |

**Classes:**

| Class | Description |
|-------|-------------|
| `LLMNotConfigured` | `RuntimeError` subclass raised when a model step is reached without complete credentials. |

**Functions:**

| Function | Description |
|----------|-------------|
| `_cached_client(endpoint, api_key, deployment, ...)` | LRU-cached factory that returns an `AzureChatOpenAI` instance. |
| `build_chat_model(settings)` | Returns a configured chat model, or raises `LLMNotConfigured` before any work is done. |

---

<a id="llmprompt"></a>
### `src/agentic/llm/prompt.py`

| Field | Detail |
|-------|--------|
| **Purpose** | Prompt assembly — where skill files stop being documentation and become behaviour. |
| **Size** | 14,738 bytes / 337 lines |

**Description:**
The system prompt is built from the pack's master skill plus the step's own skill, loaded from disk at call time. The user message is an *evidence packet*, never source code, which keeps cost linear in the number of interaction boundaries rather than in codebase size.

**Constants:**

| Name | Value | Description |
|------|-------|-------------|
| `MAX_ARTIFACT_CHARS` | 60,000 | Maximum artifact chars before truncation |
| `MAX_REPAIR_CASES` | 12 | Max rejected test cases per repair call |
| `DOC_FILES` | 4 | Max doc files included in graph summary |
| `DOC_OUTLINE_LINES` | 40 | Max outline lines per doc file |

**Functions:**

| Function | Description |
|----------|-------------|
| `build_system_prompt(ctx)` | Assembles the system prompt: master skill prose + step skill prose + step-specific instructions + output rules. |
| `renders(name)` | Decorator to register a per-artifact renderer. |
| `render_artifact(ctx, name)` | Dispatches to the registered renderer, or falls back to bounded JSON. |
| `build_user_payload(ctx)` | Assembles the evidence this step is allowed to reason over (repository, cluster, consumed artifacts). |
| `build_messages(ctx)` | Returns `[(system, ...), (human, ...)]` message pair ready for the model. |

**Registered Renderers:**

| Artifact | Renderer | What it produces |
|----------|----------|-----------------|
| `graph` | `_render_graph` | Summary only — entity counts, extraction coverage, declared docs. Never the whole graph. |
| `boundary_candidates` | `_render_boundary_candidates` | Candidate list passed through as-is (already compact). |
| `clusters` | `_render_clusters` | Boundary titles only — packets are dispatched to fan-out workers. |
| `test_batches` | `_render_test_batches` | Batch identities only. |
| `requirement_gate` | `_render_gate` | Only blocking issues — errors and rejected items for repair. |
| `brd` | `_render_brd` | Only the claims a gate rejected, never the whole requirements set. |
| `test_suite` | `_render_test_suite` | Only rejected cases (bounded to `MAX_REPAIR_CASES`), never the whole suite. |

---

<a id="llmsteps"></a>
### `src/agentic/llm/steps.py`

| Field | Detail |
|-------|--------|
| **Purpose** | Generic execution of a model step — one worker for every LLM step in every pack. |
| **Size** | 4,550 bytes / 101 lines |

**Description:**
What differs between steps is the skill file loaded, the evidence packet assembled, and the output schema enforced — all named by the manifest. Structured output is enforced by the provider (`.with_structured_output()`), so malformed responses are retried, not silently parsed.

**Constants:**

| Name | Value |
|------|-------|
| `RETRY_ATTEMPTS` | 3 |
| `SHORTEN_INSTRUCTION` | Instruction appended after a length-exceeded reply, asking for a shorter response. |

**Functions:**

| Function | Description |
|----------|-------------|
| `_is_length_failure(exc)` | Detects if an exception was caused by exceeding the output length limit. |
| `run_llm_step(ctx)` | Main entry — calls the model with structured output, retries on validation errors and length failures, returns `CapabilityResult`. |
| `_to_result(ctx, value)` | Routes the model's output to either the fan-out fragment channel or a named artifact. |

---

## Schemas Package

<a id="schemasinit"></a>
### `src/agentic/schemas/__init__.py`

| Field | Detail |
|-------|--------|
| **Purpose** | Package re-exports for all shared schema types. |
| **Size** | 592 bytes / 32 lines |

**Exports:** `Entity`, `GateReport`, `InventoryFile`, `KnowledgeGraph`, `Provenance`, `Relationship`, `RepositoryInventory`, `SCHEMAS`, `Severity`, `SourceLocation`, `ValidationIssue`, `get_schema`, `register_schema`, `utc_now`

---

<a id="schemascore"></a>
### `src/agentic/schemas/core.py`

| Field | Detail |
|-------|--------|
| **Purpose** | Knowledge-graph and validation types shared by every skill pack — pack-agnostic by design. |
| **Size** | 5,301 bytes / 169 lines |

**Description:**
These are the foundational data models. A test-generation pack reasons over the same graph as a requirements pack, so the graph schema lives here rather than beside any one pack's output schema.

**Enums:**

| Enum | Values | Description |
|------|--------|-------------|
| `Provenance` | `EXTRACTED`, `RESOLVED`, `INFERRED`, `AMBIGUOUS` | How certain the extractor was about a node or edge. Load-bearing — gates and synthesis prompts read them. |
| `Severity` | `info`, `warning`, `error` | Severity levels for validation issues. |

**Models:**

| Model | Description |
|-------|-------------|
| `SourceLocation` | Where a claim points: `file`, `line_start`, `line_end`, `symbol`, `scope`. `symbol` and `scope` are separate because they make different promises. |
| `Entity` | A knowledge-graph entity with `id`, `kind`, `name`, `qualified_name`, `language`, `location`, `provenance`, `confidence`, `attributes`. |
| `Relationship` | A graph relationship: `source → target` with `kind`, `provenance`, `confidence`, `evidence`. |
| `KnowledgeGraph` | Complete graph: `entities`, `relationships`, `metadata`. Methods: `by_id()`, `by_qualified_name()`. |
| `InventoryFile` | One file in the repository inventory: `path`, `extension`, `language`, `size`, `sha256`, `parseable`. |
| `RepositoryInventory` | The full inventory: `files`, `languages`, `total_bytes`. |
| `ValidationIssue` | One validation problem: `code`, `severity`, `message`, optional `entity_id`, `subject_id`, `location`. |
| `GateReport` | Gate outcome: `valid`, `checks_run`, `errors`, `warnings`, `issues`, `metrics`. Class method `from_issues()`. |

**Functions:**

| Function | Description |
|----------|-------------|
| `utc_now()` | Returns `datetime.now(timezone.utc)`. |

---

<a id="schemasregistry"></a>
### `src/agentic/schemas/registry.py`

| Field | Detail |
|-------|--------|
| **Purpose** | Name → Pydantic model lookup for `output_schema:` entries in pack manifests. |
| **Size** | 1,345 bytes / 44 lines |

**Description:**
A new pack registers its own output shape here; the graph builder never imports it directly.

**Registered Schemas:**

| Name | Model |
|------|-------|
| `BRDItem` | Single requirement item |
| `BRDFragment` | Worker output fragment |
| `ProductBRD` | Complete requirements document |
| `TopTier` | Top-tier classification |
| `RepairedItems` | Repair pass output |
| `BoundaryAssignments` | Boundary classification results |
| `TestDesignFragment` | Test case design worker output |
| `RepairedTestCases` | Test case repair output |

**Functions:**

| Function | Description |
|----------|-------------|
| `get_schema(name)` | Looks up a schema by name; raises `KeyError` with available schemas if not found. |
| `register_schema(name, model)` | Registers a new schema for use in pack manifests. |

---

<a id="schemasboundaries"></a>
### `src/agentic/schemas/boundaries.py`

| Field | Detail |
|-------|--------|
| **Purpose** | Schema for the boundary-classification step — what the model is allowed to return. |
| **Size** | 2,890 bytes / 73 lines |

**Description:**
An interaction boundary is where a system is consumed from outside. Every requirement must trace to one. The model classifies candidates from a closed set of boundary classes.

**Enums:**

| Enum | Values |
|------|--------|
| `BoundaryClass` | `ENDPOINT`, `CLI_ENTRY`, `CLI_COMMAND`, `SCRIPT_ENTRY`, `UI_ACTION`, `SCHEDULED_JOB`, `EVENT_CONSUMER`, `REGISTERED_ENTRY`, `NONE` |

**Models:**

| Model | Description |
|-------|-------------|
| `BoundaryAssignment` | One candidate classified: `qualified_name`, `entry_class`, `identifier`, `effect`, `rationale`. |
| `BoundaryAssignments` | List of assignments. |

---

<a id="schemasbrd"></a>
### `src/agentic/schemas/brd.py`

| Field | Detail |
|-------|--------|
| **Purpose** | Output schemas for the `codebase-requirements` pack — the Business Requirements Document (BRD) model. |
| **Size** | 53,745 bytes / 1,058 lines |

**Description:**
The largest schema file. Defines the complete classification model for requirements extraction. Two fields decide classification: `abstraction_basis` (the test the behaviour passed) and `requirement_type` (read off the basis by a fixed table). This split prevents over-extraction — an implementation detail lands in `observation`, not the requirements list.

**Key Constants:**

| Constant | Description |
|----------|-------------|
| `BASIS_TYPES` | Maps 7 abstraction bases to requirement types (e.g., `business_outcome` → `business`) |
| `TIER_SECTIONS` | Maps tiers to document sections |
| `TIER_PREFIXES` | Maps tiers to ID prefixes (`BR`, `FR`, `SYS`, `RULE`, `TC`, `NFR`, `OBS`) |
| `PARENT_RULES` | Declares which tiers may hang from which, and under which relations |
| `TIER_ALIASES` | Normalises variant type names to canonical tiers |
| `SUPPLEMENT_PREFIXES` | Families outside the main chain: `DATA`, `INT`, `NFR`, `RPT` |
| `PRIORITIES` | Priority levels: `M` (core), `S` (behind config/role gate), `C` (legacy/opt-in) |

**Key Models:**

| Model | Description |
|-------|-------------|
| `CorrespondenceOutcome` | Result of checking a claim against cited code (`supported`, `rejected`, `unchecked`). |
| `BRDItem` | One requirement/observation with `id`, `title`, `statement`, `abstraction_basis`, `requirement_type`, `evidence`, `acceptance_criteria`, `confidence`, `parent`, `parent_relation`, `correspondence`, and more. |
| `BRDFragment` | What one fan-out worker returns — list of items with cluster metadata. |
| `TopTier` | Top-tier classification for business outcomes. |
| `RepairedItems` | What a repair pass returns — fixed items only. |
| `ProductBRD` | Complete BRD document with sections for business, functional, system requirements, business rules, technical constraints, observations, success criteria, and supplements. |

**Key Functions:**

| Function | Description |
|----------|-------------|
| `permitted_parents(tier)` | Which tiers an item may legitimately hang from. |
| `permitted_relations(tier)` | How an item may relate to its parent. |
| `canonical_basis(declared)` | Normalises a declared abstraction basis. |
| `canonical_tier(declared, *, default)` | Normalises a declared type to a canonical tier. |
| `published_kind(item, section)` | The classification a reader sees. |
| `title_key(title)` | Normalised title for comparison when resolving parent links. |

**`ProductBRD` Methods:**

| Method | Description |
|--------|-------------|
| `all_items()` | Iterates all items across all sections. |
| `items_in(section)` | Items in one section. |
| `by_id()` | ID → item lookup. |
| `reindex()` | Assigns sequential IDs with tier-appropriate prefixes. |
| `attach_criteria()` | Moves success criteria to `acceptance_criteria` of their parents. |
| `connect_supplements()` | Links supplement items to their parents. |

---

<a id="schemastestgen"></a>
### `src/agentic/schemas/testgen.py`

| Field | Detail |
|-------|--------|
| **Purpose** | Input-side schemas for the `testcase-generation` pack — reads the published requirements artifact defensively. |
| **Size** | 9,725 bytes / 217 lines |

**Description:**
Deliberately does **not** import from `schemas/brd.py`. The downstream pack depends on the *published artifact* (`requirements.json`), not internal models. The reading posture: two fields are load-bearing (`id` and `statement`); everything else has a default. Unknown keys are preserved, never rejected.

**Enums:**

| Enum | Values |
|------|--------|
| `Band` | `eligible`, `eligible_with_assumptions`, `needs_clarification`, `excluded` |

**Models:**

| Model | Description |
|-------|-------------|
| `ReadIssue` | One problem with the artifact, recorded rather than raised. |
| `IngestedRequirement` | One requirement read defensively. Key methods: `usable_criteria()`, `discarded_criteria()`, `blocking_questions()`, `evidence_text()`, `searchable_text()`. |
| `RequirementSet` | The normalised input plus all read issues. Versioned envelope. |
| `QualificationRecord` | What was decided about one requirement and why. Produced for *every* requirement including excluded ones. |
| `QualifiedSet` | The qualification stage's output: records + requirements + counts. |

**Constants:**

| Name | Description |
|------|-------------|
| `CONTRACT_KEYS` | The expected keys in the published artifact |
| `BOILERPLATE_CRITERION_PREFIXES` | Acceptance criteria patterns generated by the requirements pack that are semantically empty |
| `DERIVED_CRITERION_MARKER` | Assumption marker for derived criteria |
| `BLOCKER_QUESTION_MARKER` | Open question that genuinely blocks test derivation |

---

<a id="schemastestspec"></a>
### `src/agentic/schemas/testspec.py`

| Field | Detail |
|-------|--------|
| **Purpose** | The test specification model — technology-independent by construction. |
| **Size** | 19,948 bytes / 475 lines |

**Description:**
Nothing here names a test framework, runner, assertion library, protocol, or language. The specification must survive being turned into API tests, UI tests, unit tests, agent-trajectory evaluations, or a manual script. Two authored levels exist: `Requirement → TestScenario (situation) → TestCase (one checkable behaviour)`.

**Enums:**

| Enum | Values |
|------|--------|
| `Route` | `generate`, `attach`, `deferred` |
| `InterfaceHint` | `http_endpoint`, `command_line`, `ui_action`, `scheduled`, `internal`, `unknown` |
| `AutomationReadiness` | `ready`, `needs_data`, `needs_environment`, `manual_only` |

**Key Models:**

| Model | Description |
|-------|-------------|
| `TestSignal` | Pre-design decisions: `requirement_id`, `route`, `dimensions`, `risk`, `case_budget`, `interface_hint`. |
| `TestSignalSet` | Collection of signals with `by_id()` and `generating()` methods. |
| `TestInput` | One piece of test data described by role (`value_kind`: valid, invalid, boundary, absent, malformed, unauthorised). |
| `CoverageLink` | What a case actually checks (kind: acceptance_criterion, rule, condition, assumption, statement). |
| `TestCase` | One behaviour that could come out either way. The bar: *what would have to be true for this to fail?* |
| `TestScenario` | One situation under test, holding the cases that share its context. |
| `TestSuite` | Everything produced plus everything deferred. Methods: `cases()`, `cases_for(req_id)`, `covered_requirement_ids()`. |
| `RequirementCoverage` | What a requirement got. Verdicts: `sufficient`, `positive_only`, `thin`, `none`, `deferred`, `excluded`. |
| `CoverageReport` | Everything covered and everything not, at both requirement and criterion levels. |
| `TestCaseDraft` | One case as designed (before IDs and derived metadata). Fields carry detailed schema descriptions for the model. |
| `TestScenarioDraft` | A situation under test with its drafted cases. |
| `TestDesignFragment` | What one fan-out worker returns for one batch of requirements. |
| `RepairedTestCase` | One rejected case, fixed — carrying only fields a repair is allowed to change. |
| `RepairedTestCases` | Repair pass output with fixed cases and deliberately deleted IDs. |
| `DeferredRequirement` | An honest gap, recorded as one. |

---

## Skills Package

<a id="skillsinit"></a>
### `src/agentic/skills/__init__.py`

| Field | Detail |
|-------|--------|
| **Purpose** | Package re-exports for skills management. |
| **Size** | 398 bytes / 23 lines |

**Exports:** `LoadedSkill`, `OnFail`, `PackManifest`, `SkillRegistry`, `StepMode`, `StepSpec`, `discover_packs`, `load_pack`, `manifest_summary`

---

<a id="skillspack"></a>
### `src/agentic/skills/pack.py`

| Field | Detail |
|-------|--------|
| **Purpose** | The pack manifest — a declarative workflow that compiles into a LangGraph. |
| **Size** | 11,519 bytes / 287 lines |

**Description:**
A pack directory holds `SKILL.md` folders plus a `pack.yaml`. The manifest names, for each step, which skill governs it, how it executes, and what it consumes/produces. Validation is deliberately eager — every reference is resolved at load time, so a typo fails before ingestion.

**Constants:**

| Name | Value |
|------|-------|
| `MANIFEST_NAME` | `"pack.yaml"` |

**Enums:**

| Enum | Values | Description |
|------|--------|-------------|
| `StepMode` | `deterministic`, `gate`, `llm` | How a step executes |
| `OnFail` | `halt`, `repair`, `warn_and_continue` | What happens when a step fails |

**Models:**

| Model | Description |
|-------|-------------|
| `StepSpec` | One step declaration: `id`, `skill`, `mode`, `section`, `capability`, `output_schema`, `fan_out`, `consumes`, `produces`, `on_fail`, `repair_step`, `max_repairs`, `exhausted_step`, `next`, `instructions`. Validates mode requirements. |
| `PackManifest` | The complete workflow: `pack`, `version`, `description`, `action_label`, `master_skill`, `consumes_pack`, `consumes_artifact`, `steps`. Validates step ordering, unique IDs, reference integrity, and artifact consumption ordering. |

**`PackManifest` Methods:**

| Method | Description |
|--------|-------------|
| `step(step_id)` | Looks up a step by ID. |
| `next_step_id(step_id)` | Returns the next step on the happy path (respects `next` override). |
| `skill_registry()` | Returns a `SkillRegistry` rooted at the manifest's directory. |

**Module-level Functions:**

| Function | Description |
|----------|-------------|
| `discover_packs(search_root)` | Finds every pack under a root, keyed by the declared `pack:` name. |
| `load_pack(pack_dir, *, validate_references)` | Parses and fully validates a pack manifest; resolves all skill/capability/schema references. |
| `manifest_summary(manifest)` | UI-friendly table of the workflow. |
| `_declared_name(manifest_path)` | Reads just the `pack:` field for lightweight discovery. |
| `_resolve_references(manifest)` | Checks all skills, capabilities, and schemas the manifest names. |

---

<a id="skillsregistry"></a>
### `src/agentic/skills/registry.py`

| Field | Detail |
|-------|--------|
| **Purpose** | Dynamic skill loading — `SKILL.md` files loaded at call time, never from string literals. |
| **Size** | 4,904 bytes / 132 lines |

**Description:**
An LLM step loads the relevant skill body at call time and injects it into the system prompt. A deterministic step records which skill (and exact content hash) governed it, so every deliverable can state the skill version that produced it. Cache is keyed on modification time, so editing a skill file takes effect on the next run.

**Classes:**

| Class | Description |
|-------|-------------|
| `LoadedSkill` | Frozen dataclass: `name`, `description`, `body`, `path`, `sha256`, `frontmatter`. Method `section(heading)` extracts a specific markdown section. |
| `SkillRegistry` | Loads and caches `SKILL.md` files under a pack directory, keyed by modification time. |

**`SkillRegistry` Methods:**

| Method | Description |
|--------|-------------|
| `skill_files()` | Discovers `<root>/*/SKILL.md` files. |
| `available()` | Returns sorted list of skill names. |
| `load(name)` | Loads (or returns cached) skill by name. |
| `load_all()` | Loads all available skills. |

**Module-level Functions:**

| Function | Description |
|----------|-------------|
| `_split_frontmatter(raw)` | Separates YAML frontmatter from the Markdown body. |

---

## Capabilities Package

<a id="capabilitiesinit"></a>
### `src/agentic/capabilities/__init__.py`

| Field | Detail |
|-------|--------|
| **Purpose** | Package re-exports for the capability system. |
| **Size** | 333 bytes / 18 lines |

**Exports:** `CAPABILITIES`, `CapabilityContext`, `CapabilityResult`, `capability`, `get_capability`, `load_builtin_capabilities`

---

<a id="capabilitiesregistry"></a>
### `src/agentic/capabilities/registry.py`

| Field | Detail |
|-------|--------|
| **Purpose** | The capability registry — the Strategy Pattern that keeps the graph code pack-agnostic. |
| **Size** | 5,625 bytes / 164 lines |

**Description:**
A *capability* is a named deterministic operation. `pack.yaml` refers to one by string; the graph builder resolves it here. Every capability has the same signature: `@capability("name") def fn(ctx: CapabilityContext) -> CapabilityResult`.

**Classes:**

| Class | Description |
|-------|-------------|
| `CapabilityContext` | Everything a capability is allowed to see: `state`, `step`, `settings`, `store`, `skill`, `payload`. Properties: `source_root`, `run_dir`. Methods: `artifact_path(name)`, `load(name, model)`. |
| `CapabilityResult` | What a capability hands back: `artifacts`, `files`, `metrics`, `messages`, `gate`, `fragment`, `halt_reason`. |

**Functions:**

| Function | Description |
|----------|-------------|
| `capability(name)` | Decorator to register a capability by name. |
| `get_capability(name)` | Looks up a registered capability; triggers lazy loading on first call. |
| `load_builtin_capabilities()` | Lazily imports all capability modules (requirements pack + testgen pack) for side-effect registration. |

---

<a id="capabilitiesingest"></a>
### `src/agentic/capabilities/ingest.py`

| Field | Detail |
|-------|--------|
| **Purpose** | Phase 0 — safe ingestion and inventory. Target code is read and parsed, never executed or imported. |
| **Size** | 11,000 bytes / 279 lines |
| **Registered Capability** | `ingest_repository` |

**Description:**
Archives are validated before a single byte is written to disk. Checks for: path traversal, absolute/drive-qualified paths, symlinks, encrypted entries, and implausible compression ratios. Supports 14+ language extensions.

**Constants:**

| Name | Description |
|------|-------------|
| `LANGUAGES` | Extension → language mapping (`.py` → Python, `.js` → JavaScript, etc.) |
| `IGNORED_DIRECTORIES` | `.git`, `node_modules`, `__pycache__`, `venv`, `dist`, `build`, etc. |
| `PARSEABLE_LANGUAGES` | Languages Graphify has a tree-sitter grammar for (14 languages). |
| `MAX_HASHED_BYTES` | 8 MB — files above this get size-based identity instead of SHA-256. |

**Classes:**

| Class | Description |
|-------|-------------|
| `ArchiveValidationError` | `ValueError` subclass for rejected archives. |
| `ExtractionSummary` | Dataclass: `file_count`, `compressed_bytes`, `uncompressed_bytes`, `warnings`. |

**Functions:**

| Function | Description |
|----------|-------------|
| `extract_zip_safely(stream, destination, settings)` | Validates then extracts an uploaded archive with full security checks. |
| `build_inventory(root, repository_name)` | Walks the tree once, classifying every file by language. |
| `ingest_repository(ctx)` | **Registered capability** — inventories the target tree, halts if nothing worth analysing. |
| `_safe_member_path(name)` | Validates archive member paths against traversal attacks. |
| `_is_symlink(info)` | Detects Unix symlinks in ZIP entries. |

---

<a id="capabilitiesgraphify"></a>
### `src/agentic/capabilities/graphify.py`

| Field | Detail |
|-------|--------|
| **Purpose** | Graphify-backed extraction — the language-agnostic replacement for AST parsing. |
| **Size** | 46,941 bytes / ~700 lines |
| **Registered Capabilities** | `graphify_extract`, `adapt_graphify_graph` |

**Description:**
Runs the external Graphify binary (tree-sitter-based) to extract a knowledge graph from the codebase. Handles process invocation, output parsing, and graph adaptation into the internal `KnowledgeGraph` schema. Supports all 14+ parseable languages.

---

<a id="capabilitiesgraph_ops"></a>
### `src/agentic/capabilities/graph_ops.py`

| Field | Detail |
|-------|--------|
| **Purpose** | Knowledge-graph construction and traversal. |
| **Size** | 39,607 bytes / ~700 lines |
| **Registered Capability** | `build_boundary_clusters` |

**Description:**
Constructs interaction-boundary clusters from the knowledge graph. Each cluster represents a set of related code entities that form a user-facing boundary (endpoint, CLI command, UI action, etc.) and includes evidence packets for model consumption.

---

<a id="capabilitiesboundaries"></a>
### `src/agentic/capabilities/boundaries.py`

| Field | Detail |
|-------|--------|
| **Purpose** | Proposes boundary candidates from the knowledge graph for classification. |
| **Size** | 12,733 bytes / ~270 lines |
| **Registered Capability** | `propose_boundary_candidates` |

**Description:**
Identifies functions/methods that are potential interaction boundaries (uncalled functions, route handlers, CLI entries) and prepares evidence packets for the boundary classification step.

---

<a id="capabilitiesentry_points"></a>
### `src/agentic/capabilities/entry_points.py`

| Field | Detail |
|-------|--------|
| **Purpose** | Discovers entry points and adapts boundary classification results. |
| **Size** | 18,640 bytes / ~350 lines |
| **Registered Capability** | `discover_entry_points` |

**Description:**
Processes boundary classification results from the model, filters out `NONE`-classified candidates, and builds the final set of interaction boundaries used for clustering.

---

<a id="capabilitiesclustering"></a>
### `src/agentic/capabilities/clustering.py`

| Field | Detail |
|-------|--------|
| **Purpose** | Groups interaction boundaries into coherent clusters for analysis. |
| **Size** | 21,534 bytes / ~450 lines |
| **Registered Capability** | `cluster_capabilities` |

**Description:**
Performs semantic and structural clustering of boundaries. Supports stratified cluster selection and declaration-based clustering. Boundaries sharing one workflow are merged into a single evidence packet.

---

<a id="capabilitiesclaims"></a>
### `src/agentic/capabilities/claims.py`

| Field | Detail |
|-------|--------|
| **Purpose** | Prepares evidence claims and applies repair results. |
| **Size** | 34,867 bytes / ~620 lines |
| **Registered Capabilities** | `prepare_claims`, `apply_repairs` |

**Description:**
`prepare_claims` assembles the evidence packets that fan-out workers will receive. `apply_repairs` takes the model's fixed items from a repair pass and merges them back into the BRD, carrying across everything that passed untouched.

---

<a id="capabilitiesassemble"></a>
### `src/agentic/capabilities/assemble.py`

| Field | Detail |
|-------|--------|
| **Purpose** | Assembles the final requirements document from fan-out fragments. |
| **Size** | 30,404 bytes / ~500 lines |
| **Registered Capabilities** | `digest_areas`, `assemble_requirements` |

**Description:**
`digest_areas` processes cluster area information. `assemble_requirements` collects all fragments produced by parallel fan-out workers, deduplicates, assigns IDs, resolves parent links, and builds the complete `ProductBRD`.

---

<a id="capabilitiesconsolidate"></a>
### `src/agentic/capabilities/consolidate.py`

| Field | Detail |
|-------|--------|
| **Purpose** | Post-assembly consolidation passes — merging duplicates, enforcing classification consistency, collapsing parameterised claims. |
| **Size** | 46,130 bytes / ~850 lines |
| **Registered Capability** | `consolidate_requirements` |

**Description:**
The largest capability file. Runs 13 configurable consolidation passes (each controlled by a boolean setting flag): stratified cluster selection, entry-point adapters, declaration clusters, rule classification, parameterised collapse, deduplication, confidence recalibration, hedging, semantic clustering, leakage review, candidate resolution, correspondence validation, and entailment.

---

<a id="capabilitiesconfidence"></a>
### `src/agentic/capabilities/confidence.py`

| Field | Detail |
|-------|--------|
| **Purpose** | Recalibrates confidence scores after consolidation. |
| **Size** | 10,345 bytes / ~200 lines |
| **Registered Capability** | `recalibrate_confidence` |

**Description:**
Adjusts confidence values based on evidence strength, provenance quality, and the number of corroborating sources. A claim backed by multiple `EXTRACTED` evidence locations receives higher confidence than one with a single `INFERRED` source.

---

<a id="capabilitieshedging"></a>
### `src/agentic/capabilities/hedging.py`

| Field | Detail |
|-------|--------|
| **Purpose** | Says what the document does not know — adds open questions, assumptions, and derived acceptance criteria. |
| **Size** | 12,773 bytes / ~230 lines |
| **Registered Capability** | `hedge_requirements` |

**Description:**
Examines each requirement for gaps: missing acceptance criteria, unsupported confidence claims, evidence that fixes no observable outcome. Generates honest hedging — open questions and assumptions — rather than leaving gaps invisible.

---

<a id="capabilitiesleakage"></a>
### `src/agentic/capabilities/leakage.py`

| Field | Detail |
|-------|--------|
| **Purpose** | Keeping the implementation out of the requirement statement — detects and flags implementation leakage. |
| **Size** | 17,568 bytes / ~300 lines |
| **Registered Capability** | `review_implementation_leakage` |

**Description:**
Scans requirement statements for implementation-specific language (function names, class names, framework terms, HTTP verbs, etc.) that belongs in `rationale` but not in the user-facing statement. Flags violations without auto-rewriting.

---

<a id="capabilitiesresolution"></a>
### `src/agentic/capabilities/resolution.py`

| Field | Detail |
|-------|--------|
| **Purpose** | Deciding what happens to each candidate, and keeping the unresolved out of the document. |
| **Size** | 38,697 bytes / ~700 lines |
| **Registered Capabilities** | `resolve_candidates`, `validate_synthesis` |

**Description:**
`resolve_candidates` makes final decisions about each requirement candidate: merge duplicates, resolve conflicts between overlapping claims, scope acceptance criteria, and drop degenerate items. `validate_synthesis` runs post-synthesis structural checks (cycle detection, identical-item folding).

**Key Functions:**

| Function | Description |
|----------|-------------|
| `_degenerate(item)` | Detects items with empty or trivially restated content. |
| `_support_for(item, entities)` | Counts corroborating entities. |
| `_is_the_same_claim(outcome, capability)` | Detects duplicate claims across tiers. |
| `_absorb(keeper, absorbed)` | Merges one claim into another, preserving evidence. |
| `_resolve_conflicts(brd, view)` | Resolves contradictions between overlapping items. |
| `_cycles(brd)` | Detects parent-child cycles in the hierarchy. |
| `_fold_identical(brd)` | Folds items with identical statements. |

---

<a id="capabilitiescorrespondence"></a>
### `src/agentic/capabilities/correspondence.py`

| Field | Detail |
|-------|--------|
| **Purpose** | Validates that claims actually correspond to the code they cite — deterministic evidence checking. |
| **Size** | 28,561 bytes / ~500 lines |
| **Registered Capability** | `validate_correspondence` |

**Description:**
Checks each requirement's evidence citations against the actual source files: does the cited file exist? Is the cited line range valid? Does the cited symbol appear near the cited location? Runs both deterministic checks and optional entailment validation.

---

<a id="capabilitiesgates"></a>
### `src/agentic/capabilities/gates.py`

| Field | Detail |
|-------|--------|
| **Purpose** | The two deterministic validation gates for the requirements pack. |
| **Size** | 31,166 bytes / ~500 lines |
| **Registered Capabilities** | `validate_graph`, `validate_requirements` |

**Description:**
`validate_graph` validates the knowledge graph after extraction (entity integrity, relationship validity, coverage metrics). `validate_requirements` validates the assembled BRD (evidence grounding, structural consistency, parent-link validity, classification consistency). Gate failures can halt the run or trigger repair.

---

<a id="capabilitiesquarantine"></a>
### `src/agentic/capabilities/quarantine.py`

| Field | Detail |
|-------|--------|
| **Purpose** | Deals with what a gate rejected once the repair budget is spent — without discarding evidence. |
| **Size** | 12,793 bytes / ~250 lines |
| **Registered Capability** | `quarantine_requirements` |

**Description:**
When a gate fails and repair attempts are exhausted, this capability removes the rejected items from the published document but preserves them as quarantined evidence. The result is an honest, thinner document rather than no document at all.

---

<a id="capabilitiesrender"></a>
### `src/agentic/capabilities/render.py`

| Field | Detail |
|-------|--------|
| **Purpose** | Phase 5 — renders the validated analysis into the documents people actually read. |
| **Size** | 58,183 bytes / ~1,150 lines |
| **Registered Capability** | `render_deliverables` |

**Description:**
The largest capability file. Produces all output deliverables: requirements Markdown document, traceability CSV, requirements JSON, gap analysis Markdown, technical specification, API documentation, and workflow Mermaid diagrams.

**Key Functions:**

| Function | Description |
|----------|-------------|
| `render_requirements_md(brd, repository_name)` | Full requirements document in Markdown. |
| `render_traceability_csv(brd)` | Traceability matrix as CSV. |
| `build_requirement_set(brd, ...)` | Builds the structured JSON requirement set. |
| `render_requirements_json(brd, ...)` | Serialises requirements as JSON. |
| `render_gap_analysis_md(brd, graph, ...)` | Gap analysis documenting uncovered areas. |
| `render_technical_spec_md(graph, inventory)` | Technical specification from the knowledge graph. |
| `render_api_docs_md(graph)` | API documentation from graph entities. |
| `render_workflows_mmd(graph)` | Mermaid workflow diagrams. |
| `render_deliverables(ctx)` | **Registered capability** — orchestrates all rendering. |

---

<a id="capabilitiesverify"></a>
### `src/agentic/capabilities/verify.py`

| Field | Detail |
|-------|--------|
| **Purpose** | Evidence verification against the real file system — confirms that cited files, lines, and symbols actually exist. |
| **Size** | 7,464 bytes / 167 lines |

**Description:**
Checking that a cited file exists is not enough. A fabricated line number inside a real file still looks plausible, so these helpers open the file and confirm the line range is in bounds and that a cited symbol actually appears near the cited location (within a ±25-line window).

**Constants:**

| Name | Value | Description |
|------|-------|-------------|
| `SYMBOL_WINDOW` | 25 | How far from the cited line a symbol may appear and still count. |

**Classes:**

| Class | Description |
|-------|-------------|
| `EvidenceCheck` | Frozen dataclass: `ok`, `code`, `message`. |

**Functions:**

| Function | Description |
|----------|-------------|
| `_names_the_cited_file(symbol, file)` | Checks if a symbol is the cited file path. |
| `resolve_within(location, source_root)` | Resolves a cited path inside the source root; returns `None` if it escapes or doesn't exist. |
| `_read_lines(path)` | LRU-cached file reader (up to 512 files). |
| `verify_location(location, source_root)` | Full citation verification: file exists, line range valid, symbol present. Handles synthetic nodes. |
| `_symbol_near(lines, line, symbol)` | Checks if a symbol appears within ±`SYMBOL_WINDOW` lines. |
| `read_slice(source_root, location, context)` | Returns cited source lines plus context for UI display. |

---

<a id="capabilitiesjargon"></a>
### `src/agentic/capabilities/jargon.py`

| Field | Detail |
|-------|--------|
| **Purpose** | Technical-vocabulary linter for business-language statements. |
| **Size** | 15,904 bytes / ~300 lines |

**Description:**
Utility module (not a registered capability) that detects technical jargon in requirement statements. Used by the leakage review capability to identify implementation-specific language that should be in `rationale` rather than the user-facing `statement`.

---

## Capabilities — Test Generation Subpackage

<a id="testgeninit"></a>
### `src/agentic/capabilities/testgen/__init__.py`

| Field | Detail |
|-------|--------|
| **Purpose** | Subpackage for the `testcase-generation` pack — separate from the requirements pack but shares the capability registry. |
| **Size** | 289 bytes / 7 lines |

---

<a id="testgeningest"></a>
### `src/agentic/capabilities/testgen/ingest.py`

| Field | Detail |
|-------|--------|
| **Purpose** | Reads the published requirements artifact defensively. |
| **Size** | 21,312 bytes / ~450 lines |
| **Registered Capability** | `ingest_requirement_set` |

**Key Functions:**

| Function | Description |
|----------|-------------|
| `read_requirement(raw, index)` | Reads one requirement from raw data with type coercion and issue tracking. |
| `read_requirement_set(payload, source_file)` | Reads a complete requirement set with schema version detection. |
| `resolve_from_source(root, runs_dir)` | Locates the requirements artifact from the source tree or prior runs. |
| `available_requirement_runs(runs_dir, limit)` | Lists recent requirements runs that could be chained. |
| `ingest_requirement_set(ctx)` | **Registered capability** — orchestrates requirement ingestion. |

---

<a id="testgengates"></a>
### `src/agentic/capabilities/testgen/gates.py`

| Field | Detail |
|-------|--------|
| **Purpose** | The input gate — validates that ingested requirements are suitable for test generation. |
| **Size** | 5,784 bytes / ~100 lines |
| **Registered Capability** | `validate_requirement_input` |

---

<a id="testgenqualify"></a>
### `src/agentic/capabilities/testgen/qualify.py`

| Field | Detail |
|-------|--------|
| **Purpose** | Decides, per requirement, whether and how a test may be derived. |
| **Size** | 14,042 bytes / ~300 lines |
| **Registered Capability** | `qualify_requirements` |

**Key Functions:**

| Function | Description |
|----------|-------------|
| `qualify_one(requirement, ...)` | Qualifies a single requirement: checks for usable criteria, blocking questions, duplicates, conflicted IDs. |
| `qualify_requirements(ctx)` | **Registered capability** — qualifies all requirements and produces a `QualifiedSet`. |

---

<a id="testgenanalyse"></a>
### `src/agentic/capabilities/testgen/analyse.py`

| Field | Detail |
|-------|--------|
| **Purpose** | Decides, per requirement, what may be designed and how much of it — assigns test signals. |
| **Size** | 11,473 bytes / ~250 lines |
| **Registered Capability** | `analyse_test_signals` |

**Description:**
For each eligible requirement, determines: the test dimensions to activate, the risk level, the case budget, and the routing decision (generate, attach to parent, or defer).

---

<a id="testgensignals"></a>
### `src/agentic/capabilities/testgen/signals.py`

| Field | Detail |
|-------|--------|
| **Purpose** | Determines which test dimensions a requirement actually activates. |
| **Size** | 6,644 bytes / ~150 lines |

**Description:**
Utility module (not a registered capability). Analyses requirement text to detect signals for each dimension (negative, validation, error_handling, boundary, state_transition, authorization, etc.).

**Key Functions:**

| Function | Description |
|----------|-------------|
| `activated_dimensions(requirement)` | Returns the test dimensions activated by a requirement's text. |
| `beyond_positive(requirement)` | Returns dimensions beyond the default positive path. |

---

<a id="testgenbatching"></a>
### `src/agentic/capabilities/testgen/batching.py`

| Field | Detail |
|-------|--------|
| **Purpose** | Groups requirements into bounded packets, one model call each. |
| **Size** | 7,402 bytes / ~150 lines |
| **Registered Capability** | `build_test_batches` |

**Description:**
Creates work packets for fan-out test design workers. Each batch is sized to fit within the model's context window and output ceiling, carrying the requirements and their signals.

---

<a id="testgenassemble"></a>
### `src/agentic/capabilities/testgen/assemble.py`

| Field | Detail |
|-------|--------|
| **Purpose** | Turns the workers' drafts into the assembled test suite. |
| **Size** | 11,808 bytes / ~280 lines |
| **Registered Capability** | `assemble_test_specs` |

**Key Functions:**

| Function | Description |
|----------|-------------|
| `automation_readiness(case)` | Computes automation readiness from a case's preconditions and inputs. |
| `assemble(qualified, signals, fragments)` | Collects fragments, assigns IDs, stamps metadata, resolves coverage links, builds the `TestSuite`. |
| `assemble_test_specs(ctx)` | **Registered capability** — orchestrates test spec assembly. |

---

<a id="testgendedupe"></a>
### `src/agentic/capabilities/testgen/dedupe.py`

| Field | Detail |
|-------|--------|
| **Purpose** | Folds away cases that check the same thing twice. |
| **Size** | 6,517 bytes / ~150 lines |
| **Registered Capability** | `deduplicate_test_cases` |

**Description:**
Uses fingerprinting (normalised objective + expected results + dimension) to detect duplicate test cases. Merges duplicates by keeping the richer case and recording the absorbed IDs in `superseded`.

---

<a id="testgencoverage"></a>
### `src/agentic/capabilities/testgen/coverage.py`

| Field | Detail |
|-------|--------|
| **Purpose** | What is covered, what is not, and what is only half covered. |
| **Size** | 6,841 bytes / ~180 lines |
| **Registered Capability** | `analyse_test_coverage` |

**Description:**
Computes per-requirement and per-criterion coverage reports. Verdicts: `sufficient`, `positive_only`, `thin`, `none`, `deferred`, `excluded`. Identifies which dimensions were expected but not covered.

---

<a id="testgenvalidate"></a>
### `src/agentic/capabilities/testgen/validate.py`

| Field | Detail |
|-------|--------|
| **Purpose** | The quality gate, its bounded repair loop, and the last look at the finished suite. |
| **Size** | 17,503 bytes / ~400 lines |
| **Registered Capabilities** | `validate_test_specs`, `apply_test_repairs`, `prune_test_cases`, `validate_test_suite` |

**Key Functions:**

| Function | Description |
|----------|-------------|
| `check_cases(suite, qualified)` | Validates every test case: vague results, missing preconditions, framework leaks, restated requirements, missing inputs for dimension-specific cases. |
| `check_coverage(coverage)` | Validates coverage: flags requirements with no cases or positive-only coverage. |
| `validate_test_specs(ctx)` | **Gate capability** — runs all quality checks on the test suite. |
| `apply_test_repairs(ctx)` | **Capability** — merges model-repaired cases back into the suite. |
| `prune_test_cases(ctx)` | **Capability** — removes unrepairable cases when repair budget is exhausted. |
| `validate_test_suite(ctx)` | **Capability** — final validation of the finished suite. |

---

<a id="testgenquality"></a>
### `src/agentic/capabilities/testgen/quality.py`

| Field | Detail |
|-------|--------|
| **Purpose** | Judging one test case on its own terms — quality predicates used by the gate. |
| **Size** | 6,819 bytes / ~170 lines |

**Key Functions:**

| Function | Description |
|----------|-------------|
| `normalise(text)` | Normalises text for comparison (lowercase, collapsed whitespace). |
| `similarity(left, right)` | Token-overlap similarity between two texts. |
| `vague_results(expected_results)` | Detects vague expected results ("works correctly", "behaves appropriately", etc.). |
| `framework_leaks(*texts)` | Detects framework/protocol terms that don't belong in technology-independent specs. |
| `restates(case_text, requirement_statement)` | Detects when a case merely restates the requirement. |
| `needs_precondition(case)` | Detects cases that act on something that must exist first but establish nothing. |
| `case_fingerprint(case)` | Content-based fingerprint for deduplication. |

---

<a id="testgenrender"></a>
### `src/agentic/capabilities/testgen/render.py`

| Field | Detail |
|-------|--------|
| **Purpose** | Renders the finished suite for humans, tools, and future adapters. |
| **Size** | 23,571 bytes / ~550 lines |
| **Registered Capability** | `render_test_deliverables` |

**Key Functions:**

| Function | Description |
|----------|-------------|
| `build_spec_set(suite, coverage)` | Builds the structured JSON spec set. |
| `render_test_specs_json(suite, coverage, generator)` | Serialises the test suite as JSON. |
| `render_traceability_csv(suite, coverage)` | Test-to-requirement traceability matrix as CSV. |
| `render_coverage_csv(coverage)` | Coverage report as CSV. |
| `render_test_plan_md(suite, coverage, qualified, ...)` | Full test plan in Markdown. |
| `render_excluded_md(suite, coverage, qualified)` | Excluded requirements documentation. |
| `render_clarifications_md(qualified)` | Requirements needing clarification. |
| `render_test_deliverables(ctx)` | **Registered capability** — orchestrates all test deliverable rendering. |

---

> **Total files documented:** 46 (across 6 packages)
> **Total registered capabilities:** 33
> **Total Pydantic models:** 40+
> **Total module-level functions:** 100+

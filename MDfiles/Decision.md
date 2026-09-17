# Decision Log — Skill-Pack Agentic System

This document records every significant technology, tool, and architectural decision made across the project, along with the rationale — observed or inferred — behind each one.

---

## 1. Language & Runtime

### Decision: Python ≥ 3.11

**What was chosen:** Python 3.11+ as the sole implementation language.

**Why:** The project uses `StrEnum` (3.11+), type annotations with `from __future__ import annotations`, and generic syntax (`dict[str, Any]`, `list[str]`) throughout. Python's ecosystem is best-in-class for LLM integration (LangChain, LangGraph) and data modelling (Pydantic). The `graphify` binary that handles multi-language structural extraction is an external tool, so the Python runtime only needs to orchestrate and post-process its output.

**Trade-offs:**
- Python's GIL limits true parallelism for CPU-bound work, but the project's heavy work is I/O (LLM API calls, disk reads) where `asyncio` or subprocess-based concurrency suffices.
- Performance-critical code (tree-sitter parsing) is delegated to the `graphify` binary, written outside Python.

---

## 2. Build System & Packaging

### Decision: `setuptools ≥ 75` with `pyproject.toml`

**What was chosen:** A modern PEP 621–compliant `pyproject.toml` using `setuptools` as the build backend, with `[project.scripts]` for the CLI entry point `agentic`.

**Why:** `pyproject.toml` is the modern standard and avoids `setup.py` / `setup.cfg` fragmentation. `setuptools` was chosen over alternatives like `hatch`, `flit`, or `poetry` because it is the most widely-known backend and avoids an opinion on dependency resolution (no lockfile), keeping the project lightweight.

**Trade-offs:**
- No lockfile means reproducibility depends on pinning ranges (`>=0.2.60,<1`), not exact versions. A lockfile (via `pip-tools`, `uv`, or `poetry`) would strengthen CI reproducibility.
- The `src/` layout (`[tool.setuptools.packages.find] where = ["src"]`) is a best practice that prevents accidental shadowing of the package name in the root.

---

## 3. LLM Orchestration Framework

### Decision: LangGraph (`langgraph ≥ 0.2.60, <1`)

**What was chosen:** LangGraph as the graph-execution engine — compiling a `StateGraph` from each pack manifest and running it with stream support and checkpointing.

**Why this, not plain LangChain / CrewAI / Autogen / raw OpenAI:**
- **Deterministic routing:** The project's core invariant is that routing is *never* model-driven. A supervisor LLM choosing the next node could reason past a failed gate. LangGraph's `conditional_edges` with a pure-Python router function makes this structurally impossible — the graph reads `state` and `manifest`, not a model's opinion.
- **Fan-out/fan-in:** LangGraph's `Send()` primitive lets the `synthesize` step dispatch one parallel worker per interaction boundary. This is the cost-control mechanism — each worker sees one bounded evidence packet, not the entire codebase.
- **Checkpointing:** `langgraph-checkpoint-sqlite` gives the Streamlit UI the ability to resume a partially-completed run without re-executing previous steps.
- **State management:** `TypedDict` with `Annotated[..., operator.add]` / `merge_dict` reducers gives correct parallel state merging (fan-out branches write disjoint keys, so a shallow merge is sufficient).

**Trade-offs:**
- LangGraph's API surface is still evolving pre-1.0. The pinning (`<1`) acknowledges this.
- The graph is compiled from data (the YAML manifest) rather than hard-coded, which is an unusual use of LangGraph — most projects build graphs in Python code directly. This is a deliberate abstraction boundary.

---

## 4. LLM Provider

### Decision: Azure OpenAI (via `langchain-openai ≥ 0.2.14, <1`)

**What was chosen:** Azure OpenAI exclusively, configured via four environment variables: `AZURE_OPENAI_ENDPOINT`, `AZURE_OPENAI_API_KEY`, `AZURE_OPENAI_DEPLOYMENT`, `AZURE_OPENAI_API_VERSION`.

**Why Azure, not direct OpenAI / Anthropic / Google / local models:**
- Likely an organisational or compliance requirement — Azure OpenAI offers the same models behind an enterprise-grade endpoint with VNet/firewall support.
- The `AzureChatOpenAI` class from `langchain-openai` handles deployment-based model addressing (on Azure, the model is addressed by deployment name, not by model name).

**Why `langchain-openai` specifically:**
- LangGraph is built on LangChain's abstractions, so the `AzureChatOpenAI` integrates natively with `.with_structured_output(schema)` for provider-enforced structured output.
- Structured output enforcement means the model is asked to return data conforming to a Pydantic schema, and validation errors trigger retries — not post-hoc parsing from prose.

**Trade-offs:**
- **Single-vendor lock-in.** The `llm/client.py` is 52 lines long and exclusively Azure-shaped. Switching to Anthropic, Google, or a local model requires rewriting this module. The abstraction boundary is clean (the rest of the system calls `build_chat_model(settings)` and gets back a generic `AzureChatOpenAI`), but the module itself is not provider-polymorphic.
- The `lru_cache(maxsize=4)` on the client constructor avoids re-creating the client per step, which matters for TCP connection reuse.

---

## 5. Data Validation & Schemas

### Decision: Pydantic v2 (`pydantic ≥ 2.10, <3`) and Pydantic Settings (`pydantic-settings ≥ 2.7, <3`)

**What was chosen:** Pydantic v2 as the single schema definition and validation layer for:
- All LLM output schemas (enforced via `.with_structured_output(schema)`)
- All internal data models (knowledge graph, BRD, test specs, gate reports)
- All configuration (`Settings(BaseSettings)` with `.env` file loading)
- All artifact serialisation/deserialisation (`.model_dump(mode="json")` / `.model_validate(...)`)

**Why:**
- Pydantic v2's Rust-backed `pydantic-core` is materially faster than v1 for validation-heavy workloads.
- `model_dump(mode="json")` handles enums and datetimes correctly for JSON round-tripping.
- Field descriptions on Pydantic models are passed to the model as JSON-schema descriptions when structured output is used — so the docstrings on `BRDItem.statement`, `BRDItem.abstraction_basis`, etc. are not just documentation; they are the model's instructions.
- Pydantic Settings reads from `.env` with zero boilerplate, and the `extra="ignore"` option means unrecognised environment variables don't crash the system.

**Trade-offs:**
- The BRD schema (`schemas/brd.py`) is 1058 lines long. This is the price of exhaustive field descriptions that double as model instructions, and it is a conscious choice — editing a field description changes the model's behaviour without changing any prompt code.

---

## 6. Structural Code Extraction

### Decision: `graphify` (external binary) with `graphifyy[sql] ≥ 0.9.52, <1`

**What was chosen:** An external binary (`graphify`) that performs tree-sitter-based structural extraction across 36 programming languages. The Python package `graphifyy[sql]` provides the Python bridge and the SQL grammar.

**Why this, not hand-written AST parsers / Jedi / Pyright / Semgrep:**
- **Language coverage.** The old approach was Python-only AST parsing. `graphify` covers 36 languages via tree-sitter grammars, which is what removed the Python-only limitation. A TypeScript/Java/Go codebase now contributes real structure rather than bare file entities.
- **Code-only mode.** The `--code-only` flag keeps extraction local and free — no Graphify API key needed, no network calls, pure local tree-sitter parsing.
- **Separation of concerns.** Structural extraction is a complex, mature problem. Delegating it to a purpose-built binary and normalising its output into the canonical `KnowledgeGraph` schema (in `adapt_graph`) keeps the codebase focused on *reasoning about structure* rather than *extracting it*.

**Trade-offs:**
- External binary dependency. The binary must be on `PATH` or specified via `GRAPHIFY_BINARY`. If missing, the run fails at `graphify_extract` with a clear error.
- The adapter (`capabilities/graphify.py`, 46KB) is the largest capability because it normalises a complex third-party output format into the canonical schema, including reconstructing end-line numbers that `graphify` doesn't report.

---

## 7. Configuration Management

### Decision: `.env` files with Pydantic Settings

**What was chosen:** All runtime configuration through environment variables, with local overrides in a `.env` file. 13 consolidation/resolution feature flags (each a `bool`, defaulting `True`) let individual passes be toggled for measurement.

**Why:**
- Simple, 12-factor-app-style configuration. No config file hierarchy, no YAML settings, no CLI flags for tuning.
- Feature flags for the consolidation passes exist because the project's own development required measuring each pass's effect on a corpus — and the flags are how you isolate one pass.
- `lru_cache` on `get_settings()` means the settings are loaded once and shared.

**Trade-offs:**
- No config-file validation beyond what Pydantic does (type coercion + range checks). A typo in an `.env` variable name is silently ignored (`extra="ignore"`).

---

## 8. UI Framework

### Decision: Streamlit (`streamlit ≥ 1.46, <2`)

**What was chosen:** Streamlit as the web UI, providing a sidebar for pack selection and source configuration, a progress tracker during execution, and tabbed post-run views (Run, Inventory, Knowledge Graph, Gates, Requirements, Test Cases, Deliverables).

**Why:**
- **Rapid data-app prototyping.** The entire UI is one 782-line file (`app.py`) with no build step, no frontend framework, and no API layer.
- **Streaming integration.** The `stream_run()` generator yields `(node_name, update)` pairs, which Streamlit renders incrementally via `st.progress` and `st.dataframe`.
- **Evidence browsing.** The Requirements tab opens every citation to the actual source at the cited lines, using `read_slice()` — a requirements document you can check against the code in two clicks.

**Trade-offs:**
- Streamlit's execution model (full script re-run on every interaction) requires careful state management via `st.session_state`. The chain-panel mechanism (`_apply_pending_chain`, `CHAIN_OFFER`, `PENDING_CHAIN`) is a workaround for Streamlit not allowing widget values to be changed after creation.
- No multi-user support, no authentication, no role-based access.
- Limited customisation of layout and styling compared to a React/Next.js frontend.

---

## 9. Persistence & Checkpointing

### Decision: SQLite for checkpoints (`langgraph-checkpoint-sqlite ≥ 2, <3`), on-disk JSON for artifacts

**What was chosen:**
- **Checkpoints:** A single SQLite database (`runs/checkpoints.db`) for LangGraph state checkpointing, so a Streamlit re-run resumes rather than restarts.
- **Artifacts:** On-disk JSON files under `runs/<run-id>/artifacts/`, managed by `ArtifactStore`. Large payloads (knowledge graphs, inventories, BRDs) live on disk and are referenced by path in graph state, never carried inline.

**Why artifacts on disk, not in state:**
- LangGraph checkpoints the entire state after every superstep. A multi-megabyte knowledge graph held inline would be re-serialised on every node. `State carries paths; ArtifactStore carries the payloads.`
- Text deliverables (Markdown, CSV, Mermaid) are written verbatim via `save_text()` and referenced as `file:` artifacts.

**Trade-offs:**
- No database for artifacts — they are just files. No indexing, no querying across runs, no garbage collection of orphaned runs.
- SQLite's write-lock means only one run can checkpoint at a time. Concurrent runs would contend.

---

## 10. Workflow Definition

### Decision: YAML manifests (`pack.yaml`) compiled into `StateGraph`

**What was chosen:** Each skill pack is a directory containing:
- `pack.yaml` — the workflow manifest declaring steps, their modes (deterministic/gate/llm), capabilities, output schemas, consumes/produces dependencies, fan-out keys, repair loops, and failure handlers.
- `SKILL.md` files — Markdown instructions with YAML frontmatter, loaded at call time and injected into the system prompt for LLM steps.

The manifest compiles into a LangGraph `StateGraph` at runtime.

**Why YAML manifests, not hard-coded graphs:**
- **Data-driven extensibility.** Adding a second pack (test-case generation) or a third (threat modelling) means adding a directory with markdown and a manifest — no code changes under `src/agentic/graph/`, `state.py`, `runtime.py`, or `app.py`.
- **Eager validation.** Every skill, capability, and schema a manifest references is resolved at load time (`_resolve_references`). A typo in `pack.yaml` fails before ingestion rather than twenty minutes into a run.
- **Pack chaining.** A pack declares `consumes_pack` and `consumes_artifact` to say what it follows, so the UI can offer the next step automatically without hardcoding pack relationships.

**Why SKILL.md files on disk, not prompt string literals:**
- **Hot-reloadable.** Editing a `SKILL.md` changes behaviour on the next run without restarting Streamlit or rebuilding anything. The `SkillRegistry` caches on `mtime`.
- **Auditable.** Deterministic steps record the `sha256` of the skill that governed them, so every deliverable can state which version of which instructions produced it.
- **Portable.** SKILL.md files keep the standard `name`/`description` frontmatter and nothing more — orchestration metadata belongs in `pack.yaml`, so the skill files stay portable.

---

## 11. Routing Philosophy

### Decision: Deterministic routing, never model-driven

**What was chosen:** All routing decisions (which step runs next) are made by pure-Python functions that read the manifest and the state. Three router factories cover all cases:
1. `make_linear_router` — onward unless halted.
2. `make_gate_router` — pass on valid, repair/halt/exhaust on failure.
3. `make_fanout_router` — one parallel worker per item via `Send()`.

**Why:**
- `"The validation skill says a gate must not be reasoned past. A supervisor LLM choosing the next node could route around a failed gate because continuing looked more helpful. Here the router reads the manifest and the state, so it structurally cannot."`
- This is a safety-critical decision: the gate mechanism's integrity depends on it.

---

## 12. Evidence Architecture

### Decision: The model never sees source code

**What was chosen:** LLM steps receive *evidence packets* — compact JSON extractions from the knowledge graph — not raw source files.

**Why:**
- **Cost.** Cost is linear in the number of interaction boundaries, not in the size of the codebase. A repository with 80 boundaries costs 80 bounded calls, each carrying ~40K characters of evidence.
- **Safety.** Source code in a prompt risks leaking proprietary code to the LLM provider. Evidence packets contain only structural information (qualified names, call paths, cited line ranges) and documentation outlines.
- **Scalability.** The `evidence_packet_chars` setting (default 40K) caps each packet, leaving room for instructions and output in a 128K deployment context window.

---

## 13. Testing Strategy

### Decision: 680+ tests, model fully stubbed, everything else real

**What was chosen:**
- `pytest ≥ 8, <10` as the test framework.
- A session-scoped `manifest` fixture loading the real pack.
- A `runner` fixture that runs individual steps with real capabilities against a fixture codebase.
- The LLM is *stubbed*: the boundary classifier gets a `stub_boundary_assignments()` function that classifies candidates from the same evidence the real model would see.
- A metrics harness (`tests/harness/`) with two measurement modes: structural (deterministic, no model) and document (reads finished artifacts from real runs).

**Why:**
- `"The test suite proves the machinery. It does not prove any individual requirement is right."` — the tests verify that the pipeline's deterministic stages are correct, that the schemas validate properly, that the graph compiles, that the gate logic is correct, etc.
- By stubbing only the LLM, the entire extraction-through-rendering pipeline runs as a real integration test.
- `python_classes = []` in pytest config prevents pytest from collecting domain models named `TestCase`, `TestSuite`, `TestScenario` as test classes.

---

## 14. Repair Loop Architecture

### Decision: Bounded repair with selective data

**What was chosen:** When a gate rejects claims:
1. The repair step receives *only the rejected claims* (~2K characters), never the whole document.
2. `apply_repairs` merges fixes back by ID and carries everything else untouched.
3. The loop is bounded (`max_repairs: 3` for requirements, `4` for tests).
4. When repairs are exhausted, a `quarantine` step drops ungrounded claims so an honest, thinner document can still be produced.

**Why this replaced full-document repair:**
- The old repair step was typed `ProductBRD` — a model asked to correct three wordings had to re-emit every requirement. At 200 requirements (~145K characters), this was past the 60K input cap (truncated preview) and the 8K output cap (most of the document silently disappeared). A rejection over one phrase could delete most of the analysis.
- The new design: `RepairedItems` carries only the fixed items. A claim the gate never rejected cannot be altered.

---

## 15. Classification Model

### Decision: Abstraction-basis-first classification with fixed tier mapping

**What was chosen:** Every extracted behaviour answers an "abstraction test" (`abstraction_basis`) first, and the answer deterministically maps to a tier via `BASIS_TYPES`:

```
business_outcome      → business
external_capability   → functional
internal_behaviour    → system
domain_constraint     → business_rule
platform_constraint   → technical_constraint
quality_property      → non_functional
implementation_detail → observation
```

**Why:**
- When the model was asked only for a type, the taxonomy offered nowhere to put a behaviour that was interesting but not required of the product. Every internal step was promoted to a functional requirement. Asking for the *grounds* first gives `implementation_detail` a destination (`observation`), which is kept as evidence and never published as a requirement.
- The split makes over-extraction no longer the path of least resistance.

---

## 16. Pack Chaining

### Decision: Downstream-declared pack chaining

**What was chosen:** A pack declares `consumes_pack` and `consumes_artifact` to say what it follows. The UI reads this to offer a "Generate test cases" button when a requirements run finishes.

**Why:**
- The chaining rule lives in `runtime.py`, not in the UI. It is one generic mechanism: a third pack declaring `consumes_pack` is offered automatically without anything upstream knowing it exists.
- Presence of the artifact is checked on purpose: a requirements run that halted before publishing anything is not offered as input.

---

## 17. Archive Handling

### Decision: Paranoid ZIP extraction with quota-based limits

**What was chosen:** `extract_zip_safely()` validates the entire member list *before writing anything*:
- Path traversal rejection (`..` in paths)
- Absolute and drive-qualified path rejection
- Symlink rejection
- Encrypted entry rejection
- Per-entry compression ratio check (default 100x)
- Total file count limit (10,000)
- Total compressed size limit (50MB)
- Total uncompressed size limit (200MB)

**Why:** This is a web-facing upload endpoint. Zip bombs, path traversal, and symlink-following are standard attack vectors. The project treats archive extraction as an explicit trust boundary.

---

## 18. Consolidation Stage Design

### Decision: A full consolidation pipeline between synthesis and validation

**What was chosen:** Seven deterministic steps between `assemble` and `gate_requirements`:
1. `cluster_candidates` — group by capability, not by code location
2. `consolidate` — decide levels, find duplicates, parameterised families
3. `check_correspondence` — compare claims against the code they cite
4. `score_confidence` — recompute confidence from evidence
5. `strip_implementation` — remove technical vocabulary from statements
6. `resolve` — disposition every candidate
7. `derive_criteria` — acceptance criteria last, once the set is final

**Why:**
- `"Six of these capabilities were built, tested, and named by no step in the manifest, so the behaviour they implement was not in the product at all."` — these passes were developed but never wired into the workflow. Without them, the output was a behaviour inventory (as many requirements as the code had interesting places, in the order they were found).
- Each is a separate judgement, individually toggleable via feature flags, and each produces a measured effect on the corpus metrics.

---

## 19. YAML Processing

### Decision: PyYAML (`pyyaml ≥ 6, <7`)

**What was chosen:** `yaml.safe_load()` for all manifest and frontmatter parsing.

**Why:** `safe_load` prevents arbitrary code execution from YAML tags. PyYAML is the standard Python YAML library. The BOM-handling `encoding="utf-8-sig"` in manifest loading handles Windows-generated files.

---

## 20. CLI Design

### Decision: `argparse` with three subcommands

**What was chosen:** A minimal CLI (`agentic packs`, `agentic show <pack>`, `agentic run <pack> <source>`) using the standard library's `argparse`.

**Why:** The CLI is a thin wrapper around `runtime.py`. It does not duplicate the UI's logic — both call the same `prepare_run()` and `stream_run()` functions. No third-party CLI framework (Click, Typer) because the interface is three commands.


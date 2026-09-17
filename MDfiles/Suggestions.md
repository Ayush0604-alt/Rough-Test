# Suggestions — Skill-Pack Agentic System

Professional recommendations ranging from architectural improvements to redundancy removal and modern solution adoption.

---

## Table of Contents

1. [Architectural Improvements](#1-architectural-improvements)
2. [Redundancy & Dead Code](#2-redundancy--dead-code)
3. [Modern Alternatives](#3-modern-alternatives)
4. [Scalability & Production Readiness](#4-scalability--production-readiness)
5. [Developer Experience](#5-developer-experience)
6. [Testing Improvements](#6-testing-improvements)
7. [Security Hardening](#7-security-hardening)
8. [Documentation & Maintainability](#8-documentation--maintainability)

---

## 1. Architectural Improvements

### 1.1 ★★★ Provider Abstraction for LLM Client

**Current state:** `llm/client.py` is hardcoded to Azure OpenAI. The `build_chat_model(settings)` function returns an `AzureChatOpenAI` instance exclusively.

**Problem:** Switching to Anthropic Claude, Google Gemini, or a local model (Ollama/vLLM) requires rewriting the client module and adding new settings fields.

**Suggestion:** Introduce a thin provider abstraction:

```python
# llm/providers.py
def build_chat_model(settings: Settings) -> BaseChatModel:
    match settings.llm_provider:
        case "azure_openai":
            return AzureChatOpenAI(...)
        case "openai":
            return ChatOpenAI(...)
        case "anthropic":
            return ChatAnthropic(...)
        case "ollama":
            return ChatOllama(...)
```

The rest of the system already speaks `BaseChatModel` through LangChain's interface — only `client.py` needs to change. The `with_structured_output()` protocol is supported by Anthropic and Google models, so the structured output path works across providers.

**Impact:** Low-risk, high-value. Unlocks cost reduction (Anthropic Haiku for classification), local development (Ollama), and multi-provider redundancy.

---

### 1.2 ★★★ Configuration File Validation

**Current state:** `Settings` uses `extra="ignore"`, which silently ignores misspelled environment variable names.

**Problem:** Setting `AZUR_OPENAI_ENDPOINT` (typo) instead of `AZURE_OPENAI_ENDPOINT` produces no error — the system just reports LLM as unconfigured. This is a common source of confusion.

**Suggestion:** Add a startup validation step:

```python
# config.py
def _warn_on_similar_env_vars():
    """Warn when an env var looks like a misspelling of a known setting."""
    known = {field.upper() for field in Settings.model_fields}
    for key in os.environ:
        for setting in known:
            if key != setting and _edit_distance(key, setting) <= 2:
                warnings.warn(f"Environment variable {key!r} is similar to {setting!r} — typo?")
```

---

### 1.3 ★★☆ Async Execution Path

**Current state:** `stream_run()` is synchronous. LLM API calls, which are I/O-bound and take seconds each, block the thread.

**Problem:** Fan-out synthesis sends one call per boundary cluster sequentially (within LangGraph's execution model). With 80 clusters at 20 seconds each, synthesis alone takes ~27 minutes.

**Suggestion:** LangGraph supports `graph.astream()` for async execution. The `AzureChatOpenAI` client supports async via `ainvoke()`. Switching to async would allow parallel LLM calls during fan-out, potentially reducing synthesis time by 5-10x on multi-cluster runs.

**Caveat:** Streamlit's execution model requires careful handling of async (via `asyncio.run()` or `st.cache_resource`). This is a moderate-complexity change.

---

### 1.4 ★★☆ Event Bus / Observable Step Records

**Current state:** Step records are accumulated in `state["step_records"]` as a list of dicts. The Streamlit UI polls this on each `stream_run()` yield.

**Problem:** No other consumer (CLI, a future API, logging) can observe step execution without reading the same state structure.

**Suggestion:** Introduce a lightweight event bus (or Python `logging` with structured records):

```python
import structlog
logger = structlog.get_logger("agentic.steps")

# In node wrapper:
logger.info("step.completed", step=step.id, mode=step.mode, duration_ms=elapsed, ...)
```

This separates observation from state and makes it trivial to add JSON-structured logging, webhook notifications, or real-time dashboards.

---

## 2. Redundancy & Dead Code

### 2.1 ★★★ Dual ID-Prefix Tables

**Current state:** `schemas/brd.py` defines both `TIER_PREFIXES` (line 91) and `ID_PREFIXES` (line 123). They map the same concept (tier → ID prefix) but are keyed differently — one by tier name, one by section name.

```python
TIER_PREFIXES = {"business": "BR", "functional": "FR", ...}
ID_PREFIXES = {"business_requirements": "BR", "functional_requirements": "FR", ...}
```

**Problem:** Two tables that must agree but are maintained independently. If one is updated without the other, IDs would disagree with tier labels.

**Suggestion:** Derive one from the other:

```python
ID_PREFIXES = {TIER_SECTIONS[tier]: prefix for tier, prefix in TIER_PREFIXES.items()}
ID_PREFIXES["success_criteria"] = "SC"  # the only entry not derivable from a tier
```

---

### 2.2 ★★☆ `SECTION_TIERS` is the inverse of `TIER_SECTIONS`

**Current state:** Two manually-maintained inverse lookup tables:

```python
TIER_SECTIONS = {"business": "business_requirements", ...}
SECTION_TIERS = {"business_requirements": "business", ...}
```

**Suggestion:** Derive one automatically:

```python
SECTION_TIERS = {section: tier for tier, section in TIER_SECTIONS.items()}
SECTION_TIERS["success_criteria"] = "functional"
SECTION_TIERS["non_functional_requirements"] = "non_functional"
```

---

### 2.3 ★★☆ `_records_table()` Duplication

**Current state:** The function `_records_table()` in `app.py` (line 268) is called from two places — once during streaming (line 207) and once in `tab_run()` (line 312). The function itself is fine, but the streaming call builds a table that's thrown away when the progress bar is cleared.

**Suggestion:** Minor — the streaming table serves a real UX purpose (showing progress). No action needed, but worth noting that the two calls serve different lifecycles.

---

### 2.4 ★☆☆ `NON_TIER_KINDS` and `SUPPLEMENT_PREFIXES` Overlap

Both define the same set of families (data, integration, NFR, reporting) but with different value types. Consider a single family definition struct.

---

## 3. Modern Alternatives

### 3.1 ★★★ Replace `pyyaml` with `ruamel.yaml` or `strictyaml`

**Current state:** `pyyaml` with `safe_load()`.

**Problem:** PyYAML's `safe_load` silently coerces `on: true` to `{True: True}` (the "Norway problem"). While the project uses `yaml.safe_load` correctly, anyone adding a YAML key `on` (common in GitHub Actions-style configs) would hit this silently.

**Suggestion:**
- `ruamel.yaml` — preserves comments and round-trips, better YAML 1.2 compliance.
- `strictyaml` — validates against a schema, prevents the coercion problem entirely.

Both are drop-in replacements for the `safe_load` call sites.

---

### 3.2 ★★☆ Use `uv` for Development Environment

**Current state:** No lockfile, no documented development setup tool.

**Problem:** `pip install -e ".[dev]"` resolves dependencies on every install. Two developers may get different versions of `langgraph` (within the `>=0.2.60,<1` range) and see different behaviour.

**Suggestion:** Add a `uv.lock` file and document `uv sync` for development:

```bash
# One-time setup:
uv init
uv add --dev pytest ruff
uv lock

# Daily development:
uv sync
uv run pytest
```

`uv` is 10-100x faster than `pip` for dependency resolution and provides reproducible lockfiles.

---

### 3.3 ★★☆ Structured Logging with `structlog`

**Current state:** No logging. Status information flows through `CapabilityResult.messages` and `step_records`.

**Problem:** Debugging a failed run requires reading the Streamlit UI or the step records table. There's no log file, no structured events, no trace correlation.

**Suggestion:** Add `structlog` with JSON output:

```python
import structlog
logger = structlog.get_logger()

# In node wrapper:
logger.info("step.started", step=step.id, mode=step.mode.value)
logger.info("step.completed", step=step.id, duration_ms=elapsed, artifacts=list(result.artifacts))
logger.warning("step.halted", step=step.id, reason=result.halt_reason)
```

---

### 3.4 ★☆☆ Consider Pydantic `model_validator` for Cross-Field BRD Validation

**Current state:** `BRDItem` has ~25 fields with complex cross-field invariants (e.g., `abstraction_basis` must agree with `requirement_type`, `parent` must be set iff `parent_relation` is set). These are currently enforced by deterministic post-processing in `prepare_claims` and `consolidate`.

**Suggestion:** Move some invariants into Pydantic validators for fail-fast behaviour. However, this is trade-off-heavy: the current design deliberately accepts inconsistent model output and fixes it deterministically, which is a feature, not a bug. Only validators that should *always* hold (e.g., "if `parent` is set, `parent_relation` must be set") are candidates.

---

## 4. Scalability & Production Readiness

### 4.1 ★★★ Database-Backed Artifact Store

**Current state:** Artifacts are JSON files in `runs/<id>/artifacts/`. No indexing, no querying across runs, no garbage collection.

**Problem:** After many runs, the `runs/` directory grows indefinitely. There's no way to search "all BRDs from runs analysing repository X" or "which runs had gate failures".

**Suggestion:** Add a lightweight SQLite metadata database alongside the file store:

```python
# store.py
class ArtifactStore:
    def __init__(self, run_dir: Path):
        self.db = sqlite3.connect(run_dir.parent / "metadata.db")
        # Track: run_id, artifact_name, path, created_at, size_bytes, pack
```

This keeps the file-based storage (good for large payloads) while adding searchability.

---

### 4.2 ★★★ Run Garbage Collection

**Current state:** No mechanism to clean up old runs. Each run creates a directory with source code copies and JSON artifacts.

**Suggestion:** Add a `agentic clean --older-than 30d` CLI command and/or a setting `max_retained_runs`.

---

### 4.3 ★★☆ Concurrent Run Support

**Current state:** SQLite's write-lock and Streamlit's single-thread model prevent concurrent runs.

**Suggestion for production:** Replace SQLite checkpointing with PostgreSQL (`langgraph-checkpoint-postgres`), and consider a FastAPI backend with WebSocket streaming instead of Streamlit.

---

### 4.4 ★★☆ Progress Persistence Across Streamlit Reruns

**Current state:** A full Streamlit page rerun (e.g., from a browser refresh) during an active run loses the progress tracking, though the LangGraph checkpoint allows resumption.

**Suggestion:** Store the run's progress state in a file (`runs/<id>/progress.json`) so the UI can recover the last-known state on refresh.

---

## 5. Developer Experience

### 5.1 ★★★ Type Stubs / Protocol Classes for Capabilities

**Current state:** `CapabilityContext.payload` is `dict[str, Any]`. Capabilities access `ctx.payload["manifest"]` without type safety.

**Suggestion:** Define a `Protocol` or `TypedDict` for common payload shapes:

```python
class ExtractionPayload(TypedDict):
    manifest: PackManifest

class CapabilityContext(Generic[P]):
    payload: P
```

---

### 5.2 ★★☆ `make` or `just` Task Runner

**Current state:** No task runner. Developers must know the right `pytest`, `ruff`, and `streamlit` invocations.

**Suggestion:** A `justfile` (or `Makefile`) with common commands:

```just
test:        uv run pytest
lint:        uv run ruff check src/ tests/
format:      uv run ruff format src/ tests/
dev:         uv run streamlit run app.py
```

---

### 5.3 ★★☆ Pre-commit Hooks

**Current state:** `ruff` is in dev dependencies but not enforced.

**Suggestion:** Add `.pre-commit-config.yaml` with ruff lint + format hooks.

---

### 5.4 ★☆☆ Docstring Coverage

**Current state:** Top-level module docstrings are excellent (some of the best I've seen). However, some capability functions lack parameter documentation.

**Suggestion:** Consider `interrogate` for docstring coverage measurement.

---

## 6. Testing Improvements

### 6.1 ★★★ Property-Based Testing for Schema Invariants

**Current state:** Tests are example-based. BRD schema invariants (e.g., `assign_ids` produces deterministic IDs, `parent_id` resolution is idempotent) are tested with specific fixtures.

**Suggestion:** Add Hypothesis-based property tests:

```python
from hypothesis import given, strategies as st

@given(st.builds(BRDItem, title=st.text(min_size=1)))
def test_title_key_is_idempotent(item):
    assert title_key(title_key(item.title)) == title_key(item.title)
```

---

### 6.2 ★★☆ Snapshot Testing for Rendered Deliverables

**Current state:** Render output is tested by checking existence and non-emptiness of files.

**Suggestion:** Use `syrupy` or `pytest-snapshot` to snapshot-test the rendered Markdown/CSV output. Changes to rendering logic would show as diffs in review.

---

### 6.3 ★★☆ Integration Test with a Real (Small) LLM Call

**Current state:** The LLM is always stubbed in tests. The stub is excellent for the boundary classifier, but there's no test that exercises the actual structured-output path.

**Suggestion:** A single optional integration test (marked `@pytest.mark.llm`) that makes one real LLM call against a small evidence packet and validates the response structure.

---

### 6.4 ★☆☆ Test Coverage Measurement

**Current state:** No `pytest-cov` in dev dependencies. Coverage is unmeasured.

**Suggestion:** Add `pytest-cov` and a `--cov=agentic` flag to CI.

---

## 7. Security Hardening

### 7.1 ★★★ Rate-Limit LLM API Calls

**Current state:** No rate limiting. A pack with 80 boundary clusters sends 80 LLM calls as fast as LangGraph dispatches them.

**Problem:** Azure OpenAI deployments have rate limits (tokens per minute, requests per minute). Hitting the limit causes retries with exponential backoff, which can cascade.

**Suggestion:** Add a configurable rate limiter in `llm/client.py`:

```python
from aiolimiter import AsyncLimiter

class RateLimitedClient:
    def __init__(self, client, rpm: int = 60):
        self._limiter = AsyncLimiter(rpm, 60)
    
    async def ainvoke(self, *args, **kwargs):
        async with self._limiter:
            return await self._client.ainvoke(*args, **kwargs)
```

---

### 7.2 ★★☆ Sanitise File Paths in Evidence Display

**Current state:** `read_slice()` reads files relative to `source_root` and displays them in the Streamlit UI.

**Potential issue:** If `source_root` is misconfigured, `read_slice` could read files outside the intended directory (though it does use `relative_to` checks).

**Suggestion:** Add an explicit `resolved.is_relative_to(source_root.resolve())` check before reading.

---

### 7.3 ★☆☆ Secrets in `.env`

**Current state:** API keys are in `.env`. No `.gitignore` entry was observed for `.env`.

**Suggestion:** Ensure `.env` is in `.gitignore`. Consider adding a `.env.example` with placeholder values.

---

## 8. Documentation & Maintainability

### 8.1 ★★★ Architecture Decision Records (ADRs)

**Current state:** Architectural decisions are documented in code comments (which are excellent) but not in a structured format.

**Suggestion:** Convert the most important decisions (from Decision.md) into numbered ADR files:

```
docs/adr/
    001-langgraph-over-langchain.md
    002-yaml-manifest-not-code.md
    003-evidence-packets-not-source.md
    004-deterministic-routing.md
```

Each ADR follows the standard format: Context, Decision, Consequences, Status.

---

### 8.2 ★★☆ SKILL.md Authoring Guide

**Current state:** The SKILL.md files are extraordinarily well-written but there's no guide for writing new ones.

**Suggestion:** A `docs/skill-authoring.md` explaining:
- Frontmatter requirements (name, description)
- Section addressing (how `step.section` extracts headings)
- Hot-reload behaviour (mtime-based cache invalidation)
- Content hash auditing (sha256 tracking)
- Best practices (the field descriptions ARE the prompt)

---

### 8.3 ★★☆ Contribution Guide

**Current state:** No `CONTRIBUTING.md`.

**Suggestion:** Document:
- How to add a new capability (register with `@capability`, add to `load_builtin_capabilities`)
- How to add a new schema (register with `@schema`, add to `load_builtin_schemas`)
- How to add a new pack (create directory, write `pack.yaml`, write SKILL.md files)
- How to run tests, lint, and format

---

### 8.4 ★☆☆ API Documentation

**Current state:** No generated API docs.

**Suggestion:** The module docstrings are strong enough that `mkdocs` with `mkdocstrings` would produce excellent API documentation with minimal effort.

---

## Summary Priority Matrix

| Priority | Effort | Suggestion |
|----------|--------|-----------|
| ★★★ | Low | Provider abstraction for LLM client |
| ★★★ | Low | Derive redundant lookup tables |
| ★★★ | Low | `.env` typo detection |
| ★★★ | Medium | Database-backed artifact metadata |
| ★★★ | Medium | Run garbage collection |
| ★★★ | Medium | Rate-limit LLM API calls |
| ★★★ | Medium | Property-based schema tests |
| ★★★ | Low | Architecture Decision Records |
| ★★☆ | Low | Replace `pyyaml` with `ruamel.yaml` |
| ★★☆ | Low | `uv` for dev environment + lockfile |
| ★★☆ | Low | Structured logging |
| ★★☆ | Medium | Async execution path |
| ★★☆ | Low | Pre-commit hooks + task runner |
| ★★☆ | Low | Snapshot testing for deliverables |
| ★★☆ | Low | Skill authoring guide |
| ★★☆ | Low | Contribution guide |
| ★☆☆ | Low | Docstring coverage measurement |
| ★☆☆ | Low | Test coverage measurement |
| ★☆☆ | Low | API documentation generation |

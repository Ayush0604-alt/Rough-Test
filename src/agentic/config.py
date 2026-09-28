"""Runtime settings, loaded from the environment or a local `.env`."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    runs_dir: Path = Path("runs")

    # Archive ingestion limits
    max_archive_bytes: int = 50 * 1024 * 1024
    max_uncompressed_bytes: int = 200 * 1024 * 1024
    max_archive_files: int = 10_000
    max_compression_ratio: float = 100.0

    # Graphify, the external structural extractor. Code extraction is local tree-sitter and costs
    # nothing; `--code-only` keeps it that way, so no key of Graphify's own is ever needed.
    graphify_binary: str = ""
    """Full path to the executable. Empty means look on PATH, then beside the interpreter."""

    graphify_timeout_seconds: int = 600

    # Attach the full docstring/comment text Graphify finds to the entity it describes, instead of
    # leaving it stranded on a separate RATIONALE node no workflow walk reaches. No LLM call, same
    # result every run, every word traceable to a source line. See graphify.md §15.
    graph_docstrings: bool = True
    docstring_max_chars: int = 600
    """Cap for a FUNCTION/METHOD docstring. CLASS and FILE get twice this."""

    # Read declared policy out of config, schema and dependency files and add it to the graph as
    # CONFIG_KEY / DATA_ENTITY / DATA_FIELD / DATA_INDEX / DOMAIN_POLICY / EXTERNAL_SERVICE nodes.
    #
    # Those six kinds were consumed by `compact_entity`, `render.py`, `gates.py` and
    # `declaration_groups` and emitted by nothing: orphaned when the Python-only AST extractors
    # were swapped for Graphify, which buys 36 languages and carries no declarations. Switching
    # this off reproduces the previous graph exactly. Deterministic, no model call.
    declaration_extraction: bool = True

    # Read skill, prompt and agent-configuration Markdown as structure rather than as a
    # supporting outline, and connect each to the code that loads it.
    #
    # For an LLM-powered product the instruction file *is* the behaviour; the code around it opens
    # a file and posts it to an API. The outline reader this bypasses for such files matched a
    # hardcoded filename whitelist — only `readme*` among Markdown — so a SKILL.md returned nothing
    # at all. Measured before it existed: this project analysing itself had 54 skill files in the
    # graph with no content and no edges.
    #
    # Off reproduces the previous graph exactly.
    instruction_artifacts: bool = True

    # The altitude between one interaction boundary and the whole product. One model call per
    # module, over a partition derived from workflow overlap between boundaries.
    #
    # Off reproduces the previous two-tier pipeline: boundary clusters and one top-tier call, with
    # `digest_areas` grouping titles in between and nothing reasoning about a part of the product
    # as a whole.
    module_analysis: bool = True
    min_modules: int = 6
    """Floor on the agglomeration, so a codebase whose boundaries share a bootstrap path does not
    collapse into one module."""
    max_modules: int = 20
    """Ceiling on module-analysis calls. Modules past it keep their boundaries and their findings
    and share the residual module's analysis, so nothing stops being described."""
    module_packet_chars: int = 30_000
    """A module packet is smaller than a boundary packet on purpose: it carries the shape of a
    part, not every statement inside it, which the boundary clusters already carry in full."""

    # Give every meaningful graph node a packet, instead of only those a boundary walk reaches.
    # Measured on the reference run: boundary clustering reached 12.5% of the graph while packets
    # ran at 20% of their character budget, so complete coverage costs fewer calls, not more.
    residual_clusters: bool = True

    # Azure OpenAI
    azure_openai_endpoint: str = ""
    azure_openai_api_key: str = Field(default="", repr=False)
    azure_openai_deployment: str = ""
    azure_openai_api_version: str = ""

    llm_temperature: float = 0.0
    llm_timeout_seconds: int = 180
    llm_max_output_tokens: int = 8_000

    # A different deployment for named model steps, as JSON in the environment:
    # STEP_DEPLOYMENTS='{"elevate": "gpt-4o"}'. The product-level merge is one call, so giving it a
    # stronger model costs cents while every fan-out step stays on the cheap default.
    step_deployments: dict[str, str] = Field(default_factory=dict)

    # USD per million tokens by model-name prefix, merged over the built-in list prices in
    # `agentic.llm.usage.DEFAULT_RATES`. Set your deployment's real rates as JSON in the environment:
    # LLM_RATES='{"gpt-4o-mini": {"input": 0.165, "cached_input": 0.083, "output": 0.66}}'.
    llm_rates: dict[str, dict[str, float]] = Field(default_factory=dict)

    # Fallback rates for a model no LLM_RATES prefix matches. Defaults are gpt-4o-mini list prices.
    llm_input_usd_per_million: float = 0.15
    llm_cached_input_usd_per_million: float = 0.075
    llm_output_usd_per_million: float = 0.60

    # Cost defence. The model never reads source files; it reads evidence packets built from the
    # knowledge graph. Compact JSON tokenises at roughly 2-4 characters per token, so a 40k
    # character packet leaves ample room for instructions and output in a 128k deployment.
    evidence_packet_chars: int = 40_000

    # How many interaction boundaries are analysed at all. This was 12, which capped the whole
    # document at roughly a dozen requirements however large the repository: a platform with
    # nineteen functional areas had seven of them silently omitted before the model ran once.
    # Cost is one bounded call per cluster, so this trades money for coverage linearly and the
    # omission is always reported.
    max_clusters: int = 80

    # The cap above is now a floor. When more boundary groups exist than it allows, it grows to
    # cover all of them, up to this ceiling -- a fixed 80 left 223 of 303 boundaries with no packet
    # on one run. 0 restores the fixed cap.
    max_clusters_ceiling: int = 250

    # How many small boundaries of one module may share an evidence packet. Packets ran at a median
    # of 16% of their budget, so packing covers more boundaries per call instead of adding calls.
    # Kept small so a cheap model still gives each boundary its attention. 1 disables packing.
    boundaries_per_packet: int = 4

    # The top tier in several small calls: outcomes proposed per part of the product (one call
    # each), merged in one call, an orphan sweep over whatever nothing proposed, and one review per
    # outcome. Off restores the single `elevate` call binding every capability by title.
    multi_call_elevation: bool = True
    outcome_capabilities_per_item: int = 60
    """Capabilities one proposal worker sees; a larger part of the product is cut into parts."""
    orphan_batch_size: int = 40
    """Unbound capabilities classified per orphan-sweep call."""

    max_repairs: int = 2

    # Consolidation and resolution passes. Each defaults ON and can be turned off individually
    # through the same .env the rest of these settings come from, so a pass can be isolated when
    # its effect on the corpus metrics is being measured.
    #
    # Six of these shipped switched off, and the capabilities behind three of them were not named
    # by the manifest at all -- so the workflow ran extraction, synthesis and validation with no
    # consolidation stage between them. That is what made the output a behaviour inventory: every
    # candidate any worker produced went to the gate exactly as written, and the gate can only
    # reject a claim, never group, merge, or re-level one. They are on by default now because a
    # requirements document without them is not a weaker version of the same thing, it is a
    # different and worse artefact.
    stratified_cluster_selection: bool = True
    entry_point_adapters: bool = True
    declaration_clusters: bool = True
    rule_classification: bool = True
    collapse_parameterised: bool = True
    dedup_pass: bool = True
    confidence_recalibration: bool = True
    hedging_pass: bool = True
    semantic_clustering: bool = True
    leakage_review: bool = True
    candidate_resolution: bool = True
    correspondence_validation: bool = True
    correspondence_entailment: bool = True

    @property
    def llm_configured(self) -> bool:
        """True only when every Azure setting is present.

        A partial configuration must fail before the graph starts rather than part-way through,
        which would leave a half-written document behind.
        """
        return all(
            value.strip()
            for value in (
                self.azure_openai_endpoint,
                self.azure_openai_api_key,
                self.azure_openai_deployment,
                self.azure_openai_api_version,
            )
        )

    def missing_llm_settings(self) -> list[str]:
        names = {
            "AZURE_OPENAI_ENDPOINT": self.azure_openai_endpoint,
            "AZURE_OPENAI_API_KEY": self.azure_openai_api_key,
            "AZURE_OPENAI_DEPLOYMENT": self.azure_openai_deployment,
            "AZURE_OPENAI_API_VERSION": self.azure_openai_api_version,
        }
        return [name for name, value in names.items() if not value.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()

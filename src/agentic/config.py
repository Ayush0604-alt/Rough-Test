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

    # Azure OpenAI
    azure_openai_endpoint: str = ""
    azure_openai_api_key: str = Field(default="", repr=False)
    azure_openai_deployment: str = ""
    azure_openai_api_version: str = ""

    llm_temperature: float = 0.0
    llm_timeout_seconds: int = 180
    llm_max_output_tokens: int = 8_000

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

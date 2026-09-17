"""Azure OpenAI chat client construction."""

from __future__ import annotations

from functools import lru_cache
from typing import Any

from agentic.config import Settings


class LLMNotConfigured(RuntimeError):
    """Raised when a model step is reached without complete credentials."""


@lru_cache(maxsize=4)
def _cached_client(
    endpoint: str, api_key: str, deployment: str, api_version: str,
    temperature: float, timeout: int, max_tokens: int,
) -> Any:
    from langchain_openai import AzureChatOpenAI

    return AzureChatOpenAI(
        azure_endpoint=endpoint,
        api_key=api_key,
        # On Azure the model is addressed by deployment name, not by model name.
        azure_deployment=deployment,
        api_version=api_version,
        temperature=temperature,
        timeout=timeout,
        max_tokens=max_tokens,
        max_retries=2,
    )


def build_chat_model(settings: Settings) -> Any:
    """Return a configured chat model, or fail loudly before any work is done."""
    if not settings.llm_configured:
        missing = ", ".join(settings.missing_llm_settings())
        raise LLMNotConfigured(
            f"Azure OpenAI is not fully configured. Missing: {missing}. "
            "Set them in .env before running a pack that has model steps."
        )
    return _cached_client(
        settings.azure_openai_endpoint,
        settings.azure_openai_api_key,
        settings.azure_openai_deployment,
        settings.azure_openai_api_version,
        settings.llm_temperature,
        settings.llm_timeout_seconds,
        settings.llm_max_output_tokens,
    )

"""What every model call cost: counted as it happens, priced per model, summarised per run.

Nothing recorded spend until the run in which one fan-out bug turned out to be ninety-five per cent
of the bill -- found only by rebuilding every prompt from the artifacts afterwards. This meters at
the one place every call passes through, LangChain's callback system, so no step has to remember
to report and a scripted test model simply reports nothing.

Three things make the number trustworthy rather than merely present:

- **Calls are counted, not inferred.** A step record is not a call: a retry, a length failure
  answered with a shorter request, or the correspondence check's batched entailment call all sit
  inside one record. Each `on_llm_end` is one billed response and is counted as one.
- **Tokens are the provider's.** Input, output and cached-input tokens come from the response's
  own usage report, not from counting characters.
- **Each model is priced at its own rate.** With `STEP_DEPLOYMENTS` one step can run on gpt-4o
  while the rest run on gpt-4o-mini, a sixteen-fold price difference. Usage is kept per model name
  and priced from `LLM_RATES`.

What it cannot see is stated rather than hidden: a call that fails after the provider has already
produced tokens (a reply cut off at the length limit raises instead of returning) is counted under
`failed_calls` with no tokens, because the response carrying its usage never reaches the caller.
"""

from __future__ import annotations

import threading
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Iterator

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.tracers.context import register_configure_hook

_TOKEN_KEYS = ("input_tokens", "output_tokens", "cached_input_tokens")


class UsageMeter(BaseCallbackHandler):
    """Counts every model response and the tokens it reports, per model name."""

    def __init__(self) -> None:
        super().__init__()
        self._lock = threading.Lock()
        self.by_model: dict[str, dict[str, int]] = {}
        self.failed_calls = 0

    def on_llm_end(self, response: Any, **kwargs: Any) -> None:
        message = None
        try:
            message = response.generations[0][0].message
        except (AttributeError, IndexError, TypeError):
            pass
        usage = dict(getattr(message, "usage_metadata", None) or {})
        metadata = getattr(message, "response_metadata", None) or {}
        model = (
            metadata.get("model_name")
            or (getattr(response, "llm_output", None) or {}).get("model_name")
            or "unknown"
        )
        details = usage.get("input_token_details") or {}
        with self._lock:
            row = self.by_model.setdefault(
                str(model), {"calls": 0, **{key: 0 for key in _TOKEN_KEYS}}
            )
            row["calls"] += 1
            row["input_tokens"] += int(usage.get("input_tokens") or 0)
            row["output_tokens"] += int(usage.get("output_tokens") or 0)
            row["cached_input_tokens"] += int(details.get("cache_read") or 0)

    def on_llm_error(self, error: BaseException, **kwargs: Any) -> None:
        with self._lock:
            self.failed_calls += 1

    def totals(self) -> dict[str, Any]:
        with self._lock:
            summed = {
                key: sum(row[key] for row in self.by_model.values()) for key in _TOKEN_KEYS
            }
            return {
                "model_calls": sum(row["calls"] for row in self.by_model.values()),
                "failed_calls": self.failed_calls,
                **summed,
                "usage_by_model": {name: dict(row) for name, row in self.by_model.items()},
            }


_ACTIVE_METER: ContextVar[UsageMeter | None] = ContextVar("agentic_usage_meter", default=None)
# Inheritable, so a model called inside a nested runnable -- a structured-output chain -- still
# reports to the meter of the node that called it.
register_configure_hook(_ACTIVE_METER, inheritable=True)


@contextmanager
def metered() -> Iterator[dict[str, Any]]:
    """Meter every model call made inside the block; the yielded dict is filled on exit."""
    meter = UsageMeter()
    token = _ACTIVE_METER.set(meter)
    result: dict[str, Any] = {}
    try:
        yield result
    finally:
        _ACTIVE_METER.reset(token)
        # Filled even when the block raised: a call billed before a step failed was still spent.
        result.update(meter.totals())


# --------------------------------------------------------------------------------------------
# Pricing
# --------------------------------------------------------------------------------------------

# USD per million tokens, by model-name prefix. Published list prices for the Azure OpenAI Global
# deployment type; Data Zone and Regional deployments, and negotiated agreements, differ. Override
# or extend with LLM_RATES in .env -- the provider's invoice is the authority, this is an estimate.
DEFAULT_RATES: dict[str, dict[str, float]] = {
    "gpt-4o-mini": {"input": 0.15, "cached_input": 0.075, "output": 0.60},
    "gpt-4o": {"input": 2.50, "cached_input": 1.25, "output": 10.00},
    "gpt-4.1-nano": {"input": 0.10, "cached_input": 0.025, "output": 0.40},
    "gpt-4.1-mini": {"input": 0.40, "cached_input": 0.10, "output": 1.60},
    "gpt-4.1": {"input": 2.00, "cached_input": 0.50, "output": 8.00},
}


def rates_for(model: str, settings: Any = None) -> tuple[dict[str, float], str]:
    """The rates for one model name, and where they came from.

    Matched on the longest configured prefix, so `gpt-4o-mini-2024-07-18` is priced as
    `gpt-4o-mini` and not as `gpt-4o`. An unknown model falls back to the flat configured rates and
    says so, rather than being priced silently at someone else's rate.
    """
    table = {**DEFAULT_RATES, **(getattr(settings, "llm_rates", None) or {})}
    name = (model or "").casefold()
    matches = [prefix for prefix in table if name.startswith(prefix.casefold())]
    if matches:
        prefix = max(matches, key=len)
        rate = table[prefix]
        return (
            {
                "input": float(rate.get("input", 0.0)),
                "cached_input": float(rate.get("cached_input", rate.get("input", 0.0))),
                "output": float(rate.get("output", 0.0)),
            },
            f"rate for {prefix}",
        )
    return (
        {
            "input": float(getattr(settings, "llm_input_usd_per_million", 0.15) or 0.0),
            "cached_input": float(
                getattr(settings, "llm_cached_input_usd_per_million", 0.075) or 0.0
            ),
            "output": float(getattr(settings, "llm_output_usd_per_million", 0.60) or 0.0),
        },
        "fallback rate (model not in LLM_RATES)",
    )


def price(row: dict[str, Any], rates: dict[str, float]) -> float:
    cached = min(int(row.get("cached_input_tokens") or 0), int(row.get("input_tokens") or 0))
    fresh = int(row.get("input_tokens") or 0) - cached
    return (
        fresh * rates["input"]
        + cached * rates["cached_input"]
        + int(row.get("output_tokens") or 0) * rates["output"]
    ) / 1_000_000


def summarise_usage(records: list[dict[str, Any]], settings: Any = None) -> dict[str, Any]:
    """Model calls, tokens and estimated cost -- per step, per model, and for the whole run."""

    def empty() -> dict[str, Any]:
        return {"calls": 0, "failed_calls": 0, **{key: 0 for key in _TOKEN_KEYS},
                "estimated_usd": 0.0}

    steps: dict[str, dict[str, Any]] = {}
    models: dict[str, dict[str, Any]] = {}
    for record in records:
        by_model = record.get("usage_by_model") or {}
        if not by_model and (record.get("input_tokens") or record.get("output_tokens")):
            # A record written before per-model usage existed.
            by_model = {"unknown": {"calls": int(record.get("model_calls") or 1),
                                    **{key: int(record.get(key) or 0) for key in _TOKEN_KEYS}}}
        failed = int(record.get("failed_calls") or 0)
        if not by_model and not failed:
            continue
        step = steps.setdefault(str(record.get("step")), empty())
        step["failed_calls"] += failed
        for model, usage in by_model.items():
            rates, source = rates_for(model, settings)
            cost = price(usage, rates)
            model_row = models.setdefault(
                model, {**empty(), "rates_usd_per_million": rates, "rate_source": source}
            )
            for row in (step, model_row):
                row["calls"] += int(usage.get("calls") or 0)
                for key in _TOKEN_KEYS:
                    row[key] += int(usage.get(key) or 0)
                row["estimated_usd"] += cost

    total = empty()
    for row in steps.values():
        for key in ("calls", "failed_calls", *_TOKEN_KEYS, "estimated_usd"):
            total[key] += row[key]
    for row in (*steps.values(), *models.values(), total):
        row["estimated_usd"] = round(row["estimated_usd"], 4)
    return {
        "steps": steps,
        "models": models,
        "total": total,
        "note": (
            "Calls and tokens are the provider's own usage reports. Cost is estimated from list "
            "rates per model (LLM_RATES); the Azure invoice is authoritative. `failed_calls` are "
            "calls that raised; one cut off at the length limit was still billed, but its usage "
            "never reached the caller, so it is counted here and not priced."
        ),
    }

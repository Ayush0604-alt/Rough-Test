"""Generic execution of a model step.

There is one worker for every LLM step in every pack. What differs between steps is the skill file
loaded, the evidence packet assembled, and the output schema enforced — all named by the manifest.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ValidationError

from agentic.capabilities.registry import CapabilityContext, CapabilityResult
from agentic.llm.client import build_chat_model
from agentic.llm.prompt import build_messages
from agentic.schemas.registry import get_schema

RETRY_ATTEMPTS = 3

# Appended after a reply is discarded for exceeding the output limit. A truncated structured reply
# cannot be parsed at all, so the whole cluster is lost rather than shortened — and for a fan-out
# worker that loss is silent unless something asks again for less.
SHORTEN_INSTRUCTION = (
    "Your previous reply was discarded because it exceeded the output length limit. Nothing of it "
    "survived. Return the same analysis, keeping only the best-evidenced findings: at most three "
    "functional requirements, three business rules, and two success criteria. Keep the `parent` "
    "link on every item you do return. Hold every rationale to a single short sentence. A shorter "
    "reply that arrives is worth more than a complete one that is thrown away."
)


def _is_length_failure(exc: Exception) -> bool:
    """True when the provider discarded the reply for length rather than for content.

    Matched by name so the provider SDK stays an implementation detail of the client module.
    """
    return "LengthFinishReason" in type(exc).__name__ or "length limit was reached" in str(exc)


def run_llm_step(ctx: CapabilityContext) -> CapabilityResult:
    """Call the model for one step and return its schema-validated output.

    Structured output is enforced by the provider rather than parsed out of prose, so a malformed
    response is a retry rather than a silent partial document.
    """
    schema_name = ctx.step.output_schema
    if not schema_name:
        raise ValueError(f"Step {ctx.step.id!r} is an llm step with no output_schema")
    schema = get_schema(schema_name)

    model = build_chat_model(ctx.settings)
    structured = model.with_structured_output(schema)
    messages = build_messages(ctx)

    last_error: Exception | None = None
    for attempt in range(1, RETRY_ATTEMPTS + 1):
        try:
            result = structured.invoke(messages)
        except ValidationError as exc:
            # The model answered but the shape was wrong. Worth one more try; the provider's own
            # transport retries are handled inside the client.
            last_error = exc
            continue
        except Exception as exc:  # provider, network, or content errors
            if _is_length_failure(exc) and attempt < RETRY_ATTEMPTS:
                messages = [*messages, ("human", SHORTEN_INSTRUCTION)]
                last_error = exc
                continue
            raise RuntimeError(
                f"Step {ctx.step.id!r} failed calling the model on attempt {attempt}: "
                f"{type(exc).__name__}: {exc}"
            ) from exc
        if isinstance(result, BaseModel):
            return _to_result(ctx, result)
        # Some providers return a plain mapping when structured output is emulated.
        try:
            return _to_result(ctx, schema.model_validate(result))
        except ValidationError as exc:
            last_error = exc

    raise RuntimeError(
        f"Step {ctx.step.id!r} could not produce output matching {schema_name} after "
        f"{RETRY_ATTEMPTS} attempts: {last_error}"
    )


def _to_result(ctx: CapabilityContext, value: BaseModel) -> CapabilityResult:
    """Route the model's output either to the fan-out channel or to a named artifact."""
    if ctx.step.fan_out:
        # Many workers run at once, so their output goes to a reducer-backed state channel
        # rather than to a file that they would race to overwrite.
        work_item = ctx.payload.get("work_item") or {}
        fragment: dict[str, Any] = {
            "cluster_id": work_item.get("cluster_id"),
            "boundary": work_item.get("boundary"),
            **value.model_dump(mode="json"),
        }
        return CapabilityResult(fragment=fragment, metrics={"fragments_produced": 1})

    return CapabilityResult(artifacts={name: value for name in ctx.step.produces})

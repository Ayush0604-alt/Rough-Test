"""What the boundary-classification step is allowed to return.

An interaction boundary is where a system is consumed from outside, and every requirement this
workflow publishes must trace to one. The AST extractors used to find them syntactically — a route
decorator with a literal path, an `if __name__ == "__main__"` block, a widget call. Graphify models
none of that: it emits structure, not intent, so nothing in its output says "this function is a
route handler".

The replacement hands a model the *shape* evidence a reader would use — what calls this, what it
calls, which undefined names it references — and asks it to name the boundary class. The schema is
deliberately narrow: a class from a closed set, an identifier, and the handler it belongs to.
Everything else about the boundary is derived from the graph, not from the reply.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field


class BoundaryClass(StrEnum):
    """The closed set the model may choose from.

    Mirrors `graph_ops.BOUNDARY_KINDS`, plus `NONE`. `NONE` is not a failure — most candidates are
    ordinary internal functions that happen to be uncalled, and saying so is the correct answer.
    Without it the model has no way to decline and will promote helpers to boundaries.
    """

    ENDPOINT = "ENDPOINT"
    CLI_ENTRY = "CLI_ENTRY"
    CLI_COMMAND = "CLI_COMMAND"
    SCRIPT_ENTRY = "SCRIPT_ENTRY"
    UI_ACTION = "UI_ACTION"
    SCHEDULED_JOB = "SCHEDULED_JOB"
    EVENT_CONSUMER = "EVENT_CONSUMER"
    REGISTERED_ENTRY = "REGISTERED_ENTRY"
    NONE = "NONE"


class BoundaryAssignment(BaseModel):
    """One candidate, classified."""

    qualified_name: str = Field(
        description=(
            "The candidate's `qualified_name`, copied exactly from the evidence. A name that does "
            "not appear there is discarded."
        )
    )
    entry_class: BoundaryClass = Field(
        description="The kind of boundary, or NONE when this is ordinary internal code."
    )
    identifier: str | None = Field(
        default=None,
        description=(
            "How a reader would cite this boundary: the route and method for an endpoint, the "
            "command name for a CLI entry, the label for a UI action. Use the literal value shown "
            "in the evidence; leave null rather than inventing one."
        ),
    )
    effect: str | None = Field(
        default=None,
        description="`read` or `mutate`, only where the evidence shows which. Never guessed.",
    )
    rationale: str | None = Field(
        default=None,
        description="One short sentence naming the evidence that decided the class.",
    )


class BoundaryAssignments(BaseModel):
    assignments: list[BoundaryAssignment] = Field(default_factory=list)

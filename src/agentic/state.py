"""The graph state, shared by every pack.

Nothing here mentions requirements, or any other pack's vocabulary. Steps exchange *artifacts* —
logical names mapped to files on disk — so a pack can introduce new artifact kinds without
changing this module.

Large payloads (knowledge graphs, inventories, generated documents) live in `run_dir` and appear
in state only as paths. LangGraph checkpoints the entire state after every superstep; carrying a
multi-megabyte graph inline would re-serialise it on each node and make the Streamlit UI crawl.
"""

from __future__ import annotations

import operator
from typing import Annotated, Any, Literal, TypedDict

RunStatus = Literal["running", "passed", "passed_with_warnings", "failed"]


def merge_dict(left: dict[str, Any] | None, right: dict[str, Any] | None) -> dict[str, Any]:
    """Last-writer-wins merge, used as the reducer for the dict-valued channels.

    Parallel fan-out branches write disjoint keys, so a shallow merge is sufficient and keeps the
    reducer cheap. `None` is tolerated because a node may return a partial update.
    """
    merged = dict(left or {})
    merged.update(right or {})
    return merged


class StepRecord(TypedDict, total=False):
    """One row of the run's audit trail."""

    step: str
    skill: str
    skill_sha256: str
    mode: str
    status: str
    duration_ms: float
    messages: list[str]
    # What this node spent on models, from the provider's own usage reports. Absent when it made
    # no call. `usage_by_model` keeps each model apart so each is priced at its own rate.
    model_calls: int
    failed_calls: int
    input_tokens: int
    output_tokens: int
    cached_input_tokens: int
    usage_by_model: dict[str, dict[str, int]]


class AgentState(TypedDict, total=False):
    # Identity
    run_id: str
    pack: str
    run_dir: str
    source_root: str
    repository_name: str

    # Artifacts: logical name ("graph", "inventory", "brd") -> path under run_dir
    artifacts: Annotated[dict[str, str], merge_dict]

    # Progress and provenance
    completed_steps: Annotated[list[str], operator.add]
    step_records: Annotated[list[StepRecord], operator.add]
    metrics: Annotated[dict[str, Any], merge_dict]

    # Fan-out results. Each parallel worker appends one entry; the reduce step consumes them.
    fragments: Annotated[list[dict[str, Any]], operator.add]

    # A second, differently-shaped fan-out channel: one entry per `classify_boundaries` batch
    # worker. Kept separate from `fragments` rather than sharing it, because `fragments` already has
    # a consumer (`assemble_requirements`) that reads the *whole* channel and treats every entry as
    # a `BRDFragment` — mixing a `BoundaryAssignments` reply into that channel would either corrupt
    # its counts or hide silently behind permissive validation, neither of which is `attributable`.
    boundary_fragments: Annotated[list[dict[str, Any]], operator.add]

    # A third fan-out channel: one entry per `analyse_module` worker, reduced by
    # `collect_module_analyses`. Separate for the same reason `boundary_fragments` is — a
    # `ModuleAnalysis` is not a `BRDFragment`, and every field on `BRDFragment` has a default, so
    # `model_validate` would accept one and silently publish an empty fragment rather than fail.
    module_fragments: Annotated[list[dict[str, Any]], operator.add]

    # The multi-call top tier's three fan-out channels, one per reply shape, for the same reason
    # each earlier fan-out has its own: `propose_outcomes` (one `OutcomeProposals` per part of the
    # product), `bind_orphans` (one `CapabilityAssignments` per batch of unbound capabilities) and
    # `review_outcomes` (one `OutcomeReview` per business requirement).
    outcome_fragments: Annotated[list[dict[str, Any]], operator.add]
    orphan_fragments: Annotated[list[dict[str, Any]], operator.add]
    review_fragments: Annotated[list[dict[str, Any]], operator.add]

    # Set per-branch by the fan-out dispatcher. Read by a worker, never written back, so parallel
    # branches cannot conflict over it.
    work_item: dict[str, Any] | None

    # Gate outcomes, keyed by step id, stored as plain dicts so checkpoints stay JSON-friendly.
    gate_reports: Annotated[dict[str, Any], merge_dict]

    # Bounded repair loop
    repair_attempts: int

    # Terminal condition
    status: RunStatus
    halt_reason: str | None


def initial_state(
    *,
    run_id: str,
    pack: str,
    run_dir: str,
    source_root: str,
    repository_name: str,
) -> AgentState:
    return AgentState(
        run_id=run_id,
        pack=pack,
        run_dir=run_dir,
        source_root=source_root,
        repository_name=repository_name,
        artifacts={},
        completed_steps=[],
        step_records=[],
        metrics={},
        fragments=[],
        boundary_fragments=[],
        module_fragments=[],
        outcome_fragments=[],
        orphan_fragments=[],
        review_fragments=[],
        gate_reports={},
        repair_attempts=0,
        status="running",
        halt_reason=None,
    )

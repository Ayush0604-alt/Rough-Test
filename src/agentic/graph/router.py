"""Deterministic routing.

Routing reads the manifest and the state — never a model. The validation skill is explicit that a
gate must not be reasoned past, and a supervisor that decided the next step could do exactly that:
route around a failed gate because continuing looked more helpful. Here it structurally cannot.
"""

from __future__ import annotations

from typing import Any, Callable

from langgraph.types import Send

from agentic.graph.nodes import HALT_NODE
from agentic.skills.pack import OnFail, PackManifest, StepSpec
from agentic.state import AgentState

FINALISE_NODE = "__finalise__"


def _halted(state: AgentState) -> bool:
    return state.get("status") == "failed" or bool(state.get("halt_reason"))


def _successor(manifest: PackManifest, step: StepSpec) -> str:
    """Where control goes when a step succeeds."""
    following = manifest.next_step_id(step.id)
    return following or FINALISE_NODE


def make_linear_router(manifest: PackManifest, step: StepSpec) -> Callable[[AgentState], str]:
    """Routing for a non-gate step: onward, unless the step stopped the run."""

    def route(state: AgentState) -> str:
        if _halted(state):
            return HALT_NODE
        return _successor(manifest, step)

    route.__name__ = f"route_{step.id}"
    return route


def make_gate_router(manifest: PackManifest, step: StepSpec) -> Callable[[AgentState], str]:
    """Routing for a gate: pass onward, or take the declared failure path."""
    limit = step.max_repairs if step.max_repairs is not None else 0

    def route(state: AgentState) -> str:
        if _halted(state):
            return HALT_NODE

        report = (state.get("gate_reports") or {}).get(step.id) or {}
        if report.get("valid", True):
            return _successor(manifest, step)

        if step.on_fail is OnFail.WARN:
            return _successor(manifest, step)

        if step.on_fail is OnFail.REPAIR:
            attempts = int(state.get("repair_attempts", 0))
            if attempts < limit and step.repair_step:
                return step.repair_step
            # Repairs are exhausted. A pack may declare where to go next — typically a
            # deterministic step that drops the ungrounded claims so an honest, thinner document
            # can still be produced. Without one, stopping is the correct answer.
            return step.exhausted_step or HALT_NODE

        return HALT_NODE

    route.__name__ = f"route_{step.id}"
    return route


def make_fanout_router(
    manifest: PackManifest, source: StepSpec, target: StepSpec
) -> Callable[[AgentState], Any]:
    """Dispatch one parallel worker per item, from the step *before* the fan-out step.

    This is where cost becomes linear: each worker receives one bounded evidence packet, so a
    repository with many interaction boundaries costs many small calls instead of one impossible
    one.
    """
    from pathlib import Path

    from agentic.store import ArtifactStore

    def route(state: AgentState) -> Any:
        if _halted(state):
            return HALT_NODE
        artifacts = state.get("artifacts") or {}
        path = artifacts.get(target.fan_out or "")
        items = ArtifactStore(Path(state["run_dir"])).load_json(path) if path else None
        if not isinstance(items, list) or not items:
            # Nothing to fan out over. Skip past the worker entirely — the downstream step
            # reports the absence rather than a worker inventing something to say.
            return _successor(manifest, target)
        return [Send(target.id, {**state, "work_item": item}) for item in items]

    route.__name__ = f"fanout_{source.id}_to_{target.id}"
    return route

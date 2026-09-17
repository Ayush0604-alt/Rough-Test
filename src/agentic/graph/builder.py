"""Compile a pack manifest into a LangGraph `StateGraph`.

Nothing in this module names a step, a skill, or an artifact. Every decision comes from the
manifest, which is what makes a second pack a data change rather than a code change.
"""

from __future__ import annotations

from typing import Any

from langgraph.graph import END, StateGraph

from agentic.config import Settings, get_settings
from agentic.graph.nodes import (
    HALT_NODE,
    make_finalise_node,
    make_halt_node,
    make_node,
)
from agentic.graph.router import (
    FINALISE_NODE,
    make_fanout_router,
    make_gate_router,
    make_linear_router,
)
from agentic.skills.pack import PackManifest, StepMode
from agentic.state import AgentState


def build_state_graph(manifest: PackManifest, settings: Settings | None = None) -> StateGraph:
    settings = settings or get_settings()
    builder = StateGraph(AgentState)

    # Steps another step repairs into must advance the repair counter themselves, since the
    # router that sends control there cannot write state.
    repair_targets = {step.repair_step for step in manifest.steps if step.repair_step}

    for step in manifest.steps:
        builder.add_node(
            step.id,
            make_node(step, manifest, settings, is_repair_target=step.id in repair_targets),
        )

    builder.add_node(HALT_NODE, make_halt_node())
    builder.add_node(FINALISE_NODE, make_finalise_node(manifest))
    builder.set_entry_point(manifest.steps[0].id)

    for step in manifest.steps:
        successor_id = manifest.next_step_id(step.id)
        successor = manifest.step(successor_id) if successor_id else None

        # A fan-out step is entered by parallel dispatch from its predecessor, not by a plain edge.
        if successor is not None and successor.fan_out:
            beyond = manifest.next_step_id(successor.id) or FINALISE_NODE
            builder.add_conditional_edges(
                step.id,
                make_fanout_router(manifest, step, successor),
                _targets([successor.id, beyond, HALT_NODE]),
            )
            continue

        if step.fan_out:
            # All parallel workers must finish before the reduce step runs; a plain edge gives
            # exactly that barrier, and one failed worker must not stop the others.
            builder.add_edge(step.id, successor_id or FINALISE_NODE)
            continue

        if step.mode is StepMode.GATE:
            targets = {successor_id or FINALISE_NODE, HALT_NODE}
            if step.repair_step:
                targets.add(step.repair_step)
            if step.exhausted_step:
                targets.add(step.exhausted_step)
            builder.add_conditional_edges(
                step.id, make_gate_router(manifest, step), _targets(sorted(targets))
            )
            continue

        builder.add_conditional_edges(
            step.id,
            make_linear_router(manifest, step),
            _targets([successor_id or FINALISE_NODE, HALT_NODE]),
        )

    builder.add_edge(HALT_NODE, END)
    builder.add_edge(FINALISE_NODE, END)
    return builder


def _targets(names: list[str]) -> dict[str, str]:
    """Path map for a conditional edge — every value a router may return, mapped to itself."""
    return {name: name for name in dict.fromkeys(names)}


def compile_graph(
    manifest: PackManifest,
    settings: Settings | None = None,
    checkpointer: Any | None = None,
) -> Any:
    graph = build_state_graph(manifest, settings)
    return graph.compile(checkpointer=checkpointer)


def describe_topology(manifest: PackManifest) -> list[dict[str, Any]]:
    """Human-readable edge list, for showing the compiled workflow in the UI."""
    rows: list[dict[str, Any]] = []
    for step in manifest.steps:
        successor = manifest.next_step_id(step.id) or "END"
        on_failure = ""
        if step.mode is StepMode.GATE:
            if step.on_fail.value == "repair":
                on_failure = f"{step.repair_step} (max {step.max_repairs or 0})"
                if step.exhausted_step:
                    on_failure += f", then {step.exhausted_step}"
            elif step.on_fail.value == "warn_and_continue":
                on_failure = successor
            else:
                on_failure = "halt"
        rows.append(
            {
                "step": step.id,
                "mode": step.mode.value,
                "skill": step.skill,
                "on_success": successor,
                "on_failure": on_failure,
                "fan_out": step.fan_out or "",
            }
        )
    return rows

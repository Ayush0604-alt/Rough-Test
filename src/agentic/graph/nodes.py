"""Node factories.

Three node shapes cover every step any pack can declare, because the manifest carries the
differences. A node's job is uniform: build a context, run the step, persist what it produced, and
record what happened.
"""

from __future__ import annotations

import traceback
from pathlib import Path
from time import perf_counter
from typing import Any, Callable

from agentic.capabilities.registry import (
    CapabilityContext,
    CapabilityResult,
    get_capability,
)
from agentic.config import Settings
from agentic.skills.pack import OnFail, PackManifest, StepMode, StepSpec
from agentic.state import AgentState, StepRecord
from agentic.store import ArtifactStore

HALT_NODE = "__halt__"

NodeFn = Callable[[AgentState], dict[str, Any]]


def _context(
    state: AgentState, step: StepSpec, manifest: PackManifest, settings: Settings
) -> CapabilityContext:
    return CapabilityContext(
        state=state,
        step=step,
        settings=settings,
        store=ArtifactStore(Path(state["run_dir"])),
        skill=manifest.skill_registry().load(step.skill),
        payload={"manifest": manifest, "work_item": state.get("work_item")},
    )


def _record(
    step: StepSpec,
    ctx: CapabilityContext,
    status: str,
    started: float,
    messages: list[str],
) -> StepRecord:
    return StepRecord(
        step=step.id,
        skill=step.skill,
        # The exact content hash of the skill that governed this step. A deliverable can therefore
        # state which version of which instructions produced it, and drift between the markdown
        # and the code becomes auditable rather than invisible.
        skill_sha256=ctx.skill.sha256,
        mode=step.mode.value,
        status=status,
        duration_ms=round((perf_counter() - started) * 1000, 2),
        messages=messages,
    )


def _apply(
    result: CapabilityResult, ctx: CapabilityContext, step: StepSpec
) -> dict[str, Any]:
    """Persist a capability's output and translate it into a state update."""
    update: dict[str, Any] = {}

    if result.artifacts:
        paths = {
            name: ctx.store.save(name, value) for name, value in result.artifacts.items()
        }
        update["artifacts"] = paths

    if result.files:
        update.setdefault("artifacts", {})
        update["artifacts"] = {
            **update["artifacts"],
            **{f"file:{name}": path for name, path in result.files.items()},
        }

    if result.fragment is not None:
        update["fragments"] = [result.fragment]

    if result.gate is not None:
        update["gate_reports"] = {step.id: result.gate.model_dump(mode="json")}

    if result.metrics:
        update["metrics"] = {step.id: result.metrics}

    return update


def make_capability_node(
    step: StepSpec, manifest: PackManifest, settings: Settings
) -> NodeFn:
    """A deterministic or gate step: run the registered capability."""

    def node(state: AgentState) -> dict[str, Any]:
        started = perf_counter()
        ctx = _context(state, step, manifest, settings)
        fn = get_capability(step.capability or "")
        try:
            result = fn(ctx)
        except Exception as exc:
            # Without a record here the step list simply stops, and the UI shows the last
            # successful step as the final row without ever naming the one that broke.
            record = _record(
                step, ctx, "failed", started,
                [f"{type(exc).__name__}: {exc}", traceback.format_exc(limit=3)],
            )
            if step.on_fail is OnFail.WARN:
                # The manifest says this step is optional. That has to hold when it raises, not
                # only when it returns a warning -- otherwise a step declared non-essential can
                # still take the whole run down.
                return {
                    "completed_steps": [step.id],
                    "step_records": [record],
                    "metrics": {f"{step.id}:errors": [f"{type(exc).__name__}: {exc}"]},
                }
            return {
                "completed_steps": [step.id],
                "step_records": [record],
                "status": "failed",
                "halt_reason": f"Step {step.id!r} raised {type(exc).__name__}: {exc}",
            }

        update = _apply(result, ctx, step)
        update["completed_steps"] = [step.id]

        if result.halt_reason:
            update["status"] = "failed"
            update["halt_reason"] = result.halt_reason
            status = "failed"
        elif result.gate is not None and not result.gate.valid:
            status = "failed"
        elif result.messages or (result.gate is not None and result.gate.warnings):
            status = "passed_with_warnings"
        else:
            status = "passed"

        messages = list(result.messages)
        if result.gate is not None:
            messages.extend(result.gate.error_messages()[:10])
        update["step_records"] = [_record(step, ctx, status, started, messages)]
        return update

    node.__name__ = f"node_{step.id}"
    return node


def make_llm_node(
    step: StepSpec,
    manifest: PackManifest,
    settings: Settings,
    *,
    is_repair_target: bool = False,
) -> NodeFn:
    """A model step: load the skill, assemble evidence, enforce the output schema."""
    from agentic.llm.steps import run_llm_step

    def node(state: AgentState) -> dict[str, Any]:
        started = perf_counter()
        ctx = _context(state, step, manifest, settings)
        try:
            result = run_llm_step(ctx)
        except Exception as exc:
            failure = f"{type(exc).__name__}: {exc}"
            record = _record(step, ctx, "failed", started, [failure])
            if step.fan_out:
                # One boundary failing must not discard every other boundary's analysis. The
                # gap analysis reports the missing cluster.
                return {
                    "step_records": [record],
                    "metrics": {f"{step.id}:errors": [failure]},
                }
            if is_repair_target or step.on_fail is OnFail.WARN:
                # A recovery step must not be able to destroy the run it exists to rescue. When a
                # repair call fails outright, the claims it was asked to fix are still the ones the
                # gate rejected, and quarantine already knows how to drop those and publish an
                # honest thinner document. Halting instead threw away every grounded requirement
                # in the set over one bad call, and produced no deliverables at all.
                #
                # The attempt is counted even though it failed, so the bounded loop still
                # terminates and reaches the gate's exhausted path.
                update: dict[str, Any] = {
                    "completed_steps": [step.id],
                    "step_records": [record],
                    "metrics": {f"{step.id}:errors": [failure]},
                }
                if is_repair_target:
                    update["repair_attempts"] = int(state.get("repair_attempts", 0)) + 1
                return update
            return {
                "completed_steps": [step.id],
                "step_records": [record],
                "status": "failed",
                "halt_reason": f"Step {step.id!r} failed: {failure}",
            }

        update = _apply(result, ctx, step)
        update["step_records"] = [_record(step, ctx, "passed", started, result.messages)]
        if not step.fan_out:
            update["completed_steps"] = [step.id]
        if is_repair_target:
            # A step that loops back must advance the attempt counter here, because the router
            # that sent control this way cannot write state — so the bound would never bind.
            update["repair_attempts"] = int(state.get("repair_attempts", 0)) + 1
        return update

    node.__name__ = f"node_{step.id}"
    return node


def make_halt_node() -> NodeFn:
    """Terminal node for a failed gate or a step that stopped the run.

    Writes an honest failure record rather than letting the run end quietly at whatever node
    happened to be last.
    """

    def node(state: AgentState) -> dict[str, Any]:
        reason = state.get("halt_reason")
        if not reason:
            # Summarise from the issues themselves. This previously read `issues_summary`, a key
            # `GateReport` has never had, so every gate failure halted the run with the reason
            # truncated to "A validation gate failed: gate_graph: " and the operator was told
            # nothing at all about what went wrong.
            failing = []
            for name, report in (state.get("gate_reports") or {}).items():
                if report.get("valid", True):
                    continue
                errors = [
                    issue
                    for issue in (report.get("issues") or [])
                    if str(issue.get("severity", "")).lower().endswith("error")
                ]
                shown = "; ".join(
                    f"{issue.get('code', 'ERROR')}: {issue.get('message', '')}".strip()
                    for issue in errors[:3]
                )
                if len(errors) > 3:
                    shown += f" (and {len(errors) - 3} more)"
                failing.append(f"{name}: {shown}" if shown else f"{name}: no detail recorded")
            reason = (
                "A validation gate failed: " + "; ".join(failing)
                if failing
                else "The run stopped before producing deliverables."
            )
        store = ArtifactStore(Path(state["run_dir"]))
        store.save_text(
            "run_failed.md",
            "# Analysis stopped\n\n"
            f"{reason}\n\n"
            "This workflow stops rather than producing a weaker document. A gate failure means "
            "the evidence could not support a trustworthy result, and a document that hid that "
            "would be worse than no document.\n\n"
            "## Steps completed\n\n"
            + "\n".join(f"- {name}" for name in state.get("completed_steps", []))
            + "\n",
        )
        return {"status": "failed", "halt_reason": reason}

    node.__name__ = HALT_NODE
    return node


def make_finalise_node(manifest: PackManifest) -> NodeFn:
    """Sets the terminal status from what the run actually produced."""

    def node(state: AgentState) -> dict[str, Any]:
        if state.get("status") == "failed":
            return {}
        records = state.get("step_records") or []
        warned = any(record.get("status") == "passed_with_warnings" for record in records)
        gates = state.get("gate_reports") or {}
        if any(not report.get("valid", True) for report in gates.values()):
            warned = True
        return {"status": "passed_with_warnings" if warned else "passed"}

    node.__name__ = "finalise"
    return node


def make_node(
    step: StepSpec,
    manifest: PackManifest,
    settings: Settings,
    *,
    is_repair_target: bool = False,
) -> NodeFn:
    if step.mode is StepMode.LLM:
        return make_llm_node(step, manifest, settings, is_repair_target=is_repair_target)
    return make_capability_node(step, manifest, settings)

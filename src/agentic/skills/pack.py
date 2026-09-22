"""The pack manifest: a declarative workflow that compiles into a LangGraph.

A pack directory holds `SKILL.md` folders plus a `pack.yaml`. The manifest names, for each step,
which skill governs it, how it executes, and what it consumes and produces. Everything the graph
builder needs is here — it never hard-codes a step name.

Validation is deliberately eager. Every skill, capability, and schema a manifest references is
resolved at load time, so a typo fails before ingestion rather than twenty minutes into a run.
"""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, model_validator

from agentic.skills.registry import SkillRegistry

MANIFEST_NAME = "pack.yaml"


class StepMode(StrEnum):
    DETERMINISTIC = "deterministic"   # runs a registered capability
    GATE = "gate"                     # runs a capability that returns a GateReport
    LLM = "llm"                       # runs the model against the step's skill


class OnFail(StrEnum):
    HALT = "halt"                     # terminate with an honest failure report
    REPAIR = "repair"                 # hand the issues back to a repair step, bounded
    WARN = "warn_and_continue"        # record warnings and keep going


class StepSpec(BaseModel):
    id: str
    skill: str
    mode: StepMode
    section: str | None = Field(
        default=None,
        description="Optional heading within the skill to inject, e.g. 'Gate 1'.",
    )
    capability: str | None = Field(
        default=None, description="Required for deterministic and gate steps."
    )
    output_schema: str | None = Field(
        default=None, description="Required for llm steps; a name in the schema registry."
    )
    fan_out: str | None = Field(
        default=None,
        description=(
            "Artifact holding a list of work items. When set, the step runs once per item in "
            "parallel and each worker appends to the state channel named by this step's own "
            "`produces` (conventionally `fragments`; a second fan-out phase in the same pack "
            "names a channel of its own rather than sharing one shaped for a different reply)."
        ),
    )
    consumes: list[str] = Field(default_factory=list)
    produces: list[str] = Field(default_factory=list)
    on_fail: OnFail = OnFail.HALT
    repair_step: str | None = Field(
        default=None, description="Which step to route to when on_fail is 'repair'."
    )
    max_repairs: int | None = None
    exhausted_step: str | None = Field(
        default=None,
        description=(
            "Where a repairing gate routes once its repair budget is spent. Typically a step "
            "that drops the ungrounded claims so an honest, thinner document can still be "
            "produced. Without one, an exhausted gate stops the run."
        ),
    )
    next: str | None = Field(
        default=None,
        description=(
            "Override the implicit next-step-in-order edge. For a gate, this is the pass path."
        ),
    )
    instructions: str | None = Field(
        default=None,
        description="Extra step-specific guidance appended to the skill prose for llm steps.",
    )

    @model_validator(mode="after")
    def _check_mode_requirements(self) -> "StepSpec":
        if self.mode in (StepMode.DETERMINISTIC, StepMode.GATE) and not self.capability:
            raise ValueError(f"Step {self.id!r} is {self.mode} and must name a capability")
        if self.mode == StepMode.LLM and not self.output_schema:
            raise ValueError(f"Step {self.id!r} is an llm step and must name an output_schema")
        if self.mode == StepMode.LLM and self.capability:
            raise ValueError(f"Step {self.id!r} is an llm step and must not name a capability")
        return self


class PackManifest(BaseModel):
    pack: str
    version: int = 1
    description: str = ""
    action_label: str = Field(
        default="",
        description=(
            "What a button offering this pack should say, in the user's words rather than the "
            "pack's id — 'Generate test cases', not 'Run testcase-generation'. Declared here so "
            "the UI can offer a pack by name without holding a list of pack names of its own."
        ),
    )
    master_skill: str | None = Field(
        default=None,
        description="Skill whose prose is prepended to every llm step, establishing shared rules.",
    )
    consumes_pack: str | None = Field(
        default=None,
        description=(
            "The pack whose published output this pack takes as its input. Declared by the "
            "*downstream* pack, so a new pack announces what it follows without anything upstream "
            "needing to know it exists."
        ),
    )
    consumes_artifact: str | None = Field(
        default=None,
        description=(
            "The file in that pack's `artifacts/` this pack requires. Checked before offering to "
            "chain, so a run that failed before producing it is not offered as an input."
        ),
    )
    steps: list[StepSpec]

    # Populated by `load_pack`; not part of the YAML.
    root: Path | None = None

    @model_validator(mode="after")
    def _check_steps(self) -> "PackManifest":
        if not self.steps:
            raise ValueError(f"Pack {self.pack!r} declares no steps")
        ids = [step.id for step in self.steps]
        duplicates = {value for value in ids if ids.count(value) > 1}
        if duplicates:
            raise ValueError(f"Pack {self.pack!r} has duplicate step ids: {sorted(duplicates)}")

        known = set(ids)
        for step in self.steps:
            for target, label in (
                (step.next, "next"),
                (step.repair_step, "repair_step"),
                (step.exhausted_step, "exhausted_step"),
            ):
                if target and target not in known:
                    raise ValueError(
                        f"Step {step.id!r} points {label} at unknown step {target!r}"
                    )
            if step.on_fail == OnFail.REPAIR and not step.repair_step:
                raise ValueError(
                    f"Step {step.id!r} has on_fail 'repair' but names no repair_step"
                )

        # A step may only consume what an earlier step produced. Catching this here is what lets
        # `CapabilityContext.artifact_path` treat a miss at runtime as a genuine defect.
        produced: set[str] = set()
        for step in self.steps:
            missing = [name for name in step.consumes if name not in produced]
            if missing:
                raise ValueError(
                    f"Step {step.id!r} consumes {missing} which no earlier step produces. "
                    f"Available at that point: {sorted(produced) or '<none>'}"
                )
            if step.fan_out and step.fan_out not in produced:
                raise ValueError(
                    f"Step {step.id!r} fans out over {step.fan_out!r}, which no earlier step "
                    "produces"
                )
            produced.update(step.produces)
        return self

    def step(self, step_id: str) -> StepSpec:
        for step in self.steps:
            if step.id == step_id:
                return step
        raise KeyError(f"Pack {self.pack!r} has no step {step_id!r}")

    def next_step_id(self, step_id: str) -> str | None:
        """The step that follows `step_id` on the happy path, or None if it is the last."""
        current = self.step(step_id)
        if current.next:
            return current.next
        ids = [step.id for step in self.steps]
        index = ids.index(step_id)
        return ids[index + 1] if index + 1 < len(ids) else None

    def skill_registry(self) -> SkillRegistry:
        if self.root is None:
            raise ValueError("Manifest has no root; load it with load_pack()")
        return SkillRegistry(self.root)


def discover_packs(search_root: Path | str) -> dict[str, Path]:
    """Find every pack under `search_root`, keyed by the name its manifest declares.

    The declared `pack:` name is the identity, not the directory name: a pack should be able to
    live in a directory called anything and still be referred to by what it calls itself.
    """
    root = Path(search_root)
    candidates = sorted(root.glob(f"*/{MANIFEST_NAME}"))
    if (root / MANIFEST_NAME).is_file():
        candidates.append(root / MANIFEST_NAME)

    found: dict[str, Path] = {}
    for manifest_path in candidates:
        found[_declared_name(manifest_path)] = manifest_path.parent
    return found


def _declared_name(manifest_path: Path) -> str:
    """Read just the `pack:` field, without paying for full validation during discovery."""
    try:
        raw = yaml.safe_load(manifest_path.read_text(encoding="utf-8-sig")) or {}
        name = raw.get("pack") if isinstance(raw, dict) else None
    except (OSError, yaml.YAMLError):
        name = None
    return str(name) if name else manifest_path.parent.name


def load_pack(pack_dir: Path | str, *, validate_references: bool = True) -> PackManifest:
    """Parse and fully validate a pack manifest.

    With `validate_references`, every skill, capability, and schema the manifest names is resolved
    now. That turns a manifest typo into a clear error before any work starts.
    """
    root = Path(pack_dir)
    manifest_path = root / MANIFEST_NAME
    if not manifest_path.is_file():
        raise FileNotFoundError(f"No {MANIFEST_NAME} in {root}")
    raw = yaml.safe_load(manifest_path.read_text(encoding="utf-8-sig")) or {}
    if not isinstance(raw, dict):
        raise ValueError(f"{manifest_path} must contain a YAML mapping")
    manifest = PackManifest.model_validate({**raw, "root": root})
    if validate_references:
        _resolve_references(manifest)
    return manifest


def _resolve_references(manifest: PackManifest) -> None:
    from agentic.capabilities.registry import get_capability
    from agentic.schemas.registry import get_schema

    registry = manifest.skill_registry()
    problems: list[str] = []

    names = {step.skill for step in manifest.steps}
    if manifest.master_skill:
        names.add(manifest.master_skill)
    for name in sorted(names):
        try:
            registry.load(name)
        except (KeyError, OSError) as exc:
            problems.append(str(exc))

    for step in manifest.steps:
        if step.capability:
            try:
                get_capability(step.capability)
            except KeyError as exc:
                problems.append(f"Step {step.id!r}: {exc}")
        if step.output_schema:
            try:
                get_schema(step.output_schema)
            except KeyError as exc:
                problems.append(f"Step {step.id!r}: {exc}")

    if problems:
        detail = "\n  - ".join(problems)
        raise ValueError(f"Pack {manifest.pack!r} has unresolved references:\n  - {detail}")


def manifest_summary(manifest: PackManifest) -> list[dict[str, Any]]:
    """A UI-friendly table of the workflow."""
    return [
        {
            "step": step.id,
            "mode": step.mode.value,
            "skill": step.skill,
            "capability": step.capability or "",
            "schema": step.output_schema or "",
            "on_fail": step.on_fail.value,
        }
        for step in manifest.steps
    ]

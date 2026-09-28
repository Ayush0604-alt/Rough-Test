"""The capability registry — the Strategy Pattern that keeps the graph code pack-agnostic.

A *capability* is a named deterministic operation. `pack.yaml` refers to one by string
(`capability: extract_python_ast`); the graph builder resolves it here and never imports the
implementing module. Adding a pack means registering new capabilities, not editing the graph.

Every capability has the same signature::

    @capability("name")
    def fn(ctx: CapabilityContext) -> CapabilityResult: ...
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

from agentic.config import Settings
from agentic.schemas.core import GateReport
from agentic.store import ArtifactStore

if TYPE_CHECKING:  # pragma: no cover - import cycle only matters for type checkers
    from agentic.skills.registry import LoadedSkill
    from agentic.skills.pack import StepSpec
    from agentic.state import AgentState


@dataclass
class CapabilityContext:
    """Everything a capability is allowed to see.

    Capabilities read prior artifacts through `store`, not through graph state, so they stay
    testable in isolation: build a context around a temp directory and call the function.
    """

    state: "AgentState"
    step: "StepSpec"
    settings: Settings
    store: ArtifactStore
    skill: "LoadedSkill"
    payload: dict[str, Any] = field(default_factory=dict)

    @property
    def source_root(self) -> Path:
        return Path(self.state["source_root"])

    @property
    def run_dir(self) -> Path:
        return Path(self.state["run_dir"])

    def artifact_path(self, name: str) -> str:
        """Path of an artifact an earlier step produced.

        Raises rather than returning None: a manifest that declares `consumes: [graph]` has
        already been validated to run after a step producing it, so a miss is a real defect.
        """
        artifacts = self.state.get("artifacts") or {}
        try:
            return artifacts[name]
        except KeyError:
            available = ", ".join(sorted(artifacts)) or "<none>"
            raise KeyError(
                f"Step {self.step.id!r} needs artifact {name!r}, which no earlier step produced. "
                f"Available: {available}"
            ) from None

    def load(self, name: str, model: type | None = None) -> Any:
        path = self.artifact_path(name)
        if model is None:
            return self.store.load_json(path)
        return self.store.load_model(path, model)


@dataclass
class CapabilityResult:
    """What a capability hands back to the node wrapper.

    `artifacts` are saved under their logical names and recorded in state. `gate` is set only by
    gate-mode capabilities. `fragment` is set only by fan-out workers, whose output goes to the
    shared `fragments` channel rather than to a file, since many run in parallel. `halt_reason`
    lets a deterministic step stop the run with an honest explanation instead of raising.
    """

    artifacts: dict[str, Any] = field(default_factory=dict)
    files: dict[str, str] = field(default_factory=dict)
    metrics: dict[str, Any] = field(default_factory=dict)
    messages: list[str] = field(default_factory=list)
    gate: GateReport | None = None
    fragment: dict[str, Any] | None = None
    halt_reason: str | None = None


Capability = Callable[[CapabilityContext], CapabilityResult]

CAPABILITIES: dict[str, Capability] = {}


def capability(name: str) -> Callable[[Capability], Capability]:
    def decorate(fn: Capability) -> Capability:
        if name in CAPABILITIES and CAPABILITIES[name] is not fn:
            raise ValueError(f"Capability {name!r} is already registered")
        CAPABILITIES[name] = fn
        return fn

    return decorate


def get_capability(name: str) -> Capability:
    load_builtin_capabilities()
    try:
        return CAPABILITIES[name]
    except KeyError:
        known = ", ".join(sorted(CAPABILITIES)) or "<none registered>"
        raise KeyError(f"Unknown capability {name!r}. Registered: {known}") from None


_LOADED = False


def load_builtin_capabilities() -> None:
    """Import the modules that register the shipped capabilities.

    Done lazily and once, so importing the registry alone stays cheap and free of cycles.
    """
    global _LOADED
    if _LOADED:
        return
    _LOADED = True
    from agentic.capabilities import (  # noqa: F401  (imported for side effects)
        assemble,
        boundaries,
        claims,
        clustering,
        confidence,
        consolidate,
        correspondence,
        declarations,
        elevation,
        entry_points,
        gates,
        graph_ops,
        graphify,
        hedging,
        ingest,
        instructions,
        leakage,
        modules,
        quarantine,
        render,
        resolution,
    )

    # The test-case generation pack. A separate import because it is a separate pack sharing this
    # registry — the graph still resolves its capabilities by name and imports neither module.
    from agentic.capabilities.testgen import (  # noqa: F401  (imported for side effects)
        analyse as testgen_analyse,
        assemble as testgen_assemble,
        batching as testgen_batching,
        coverage as testgen_coverage,
        dedupe as testgen_dedupe,
        gates as testgen_gates,
        ingest as testgen_ingest,
        qualify as testgen_qualify,
        render as testgen_render,
        validate as testgen_validate,
    )

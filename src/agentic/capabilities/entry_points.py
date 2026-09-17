"""The entry-point adapter seam, and the coverage report that makes an omission visible.

An entry point is where a system is consumed from outside, and the requirements this workflow
publishes are only ever about behaviour reachable from one. That makes entry-point discovery the
single highest-leverage thing in the pipeline: a class of entry point nobody detects is a class of
capability nobody documents, and — until this module existed — nothing in the run said so. A
repository could be analysed end to end, every gate could pass, and an entire paradigm could be
missing from the output with no trace of its absence.

Two things live here.

**A registry.** `@entry_point_adapter` registers an adapter; `discover_entry_points` runs every
registered one over the inventory and folds what they find into the graph. Adding support for a
new stack or paradigm is registering an adapter — not editing this module, not editing the
traversal, and not editing the manifest. Everything a stack needs to know about itself lives
inside its own adapter, which is the only place stack-specific code is allowed.

**A coverage report.** Every adapter declares the classes it can produce and reports what it found
per class. The result is published as an artifact and folded into the run's metrics, so "this
paradigm produced nothing" is a line in the report rather than a silence. The system cannot report
what it does not know to look for, so the report also names the classes that no registered adapter
claims at all.

The four detectors that predated this seam lived inside the Python AST extractors and went with
them when extraction moved to Graphify. Graphify emits structure, not intent — nothing in its
output marks a route, a command, or a scheduled job — so the detection they did is now done by
`ClassifiedBoundaryAdapter` below, from graph shape plus one model call. That adapter is
language-agnostic by construction, which the four it replaced were not.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Protocol, runtime_checkable

from agentic.capabilities.graph_ops import BOUNDARY_KINDS, stable_id
from agentic.capabilities.registry import CapabilityContext, CapabilityResult, capability
from agentic.schemas.core import (
    Entity,
    InventoryFile,
    KnowledgeGraph,
    Provenance,
    RepositoryInventory,
    SourceLocation,
)

MAX_SOURCE_BYTES = 2 * 1024 * 1024


@dataclass
class DiscoveredEntryPoint:
    """One entry point an adapter found, before it becomes a graph entity."""

    entry_class: str
    """The kind of boundary, e.g. ENDPOINT. Must be in `BOUNDARY_KINDS` to seed a cluster."""

    identifier: str
    """How a reader would cite it: a route, a topic, a command name."""

    location: SourceLocation
    handler: str | None = None
    """Qualified name of the definition that serves it, if the adapter resolved one."""

    effect: str | None = None
    """`read` or `mutate` where the adapter can tell. Never guessed."""

    attributes: dict[str, Any] = field(default_factory=dict)
    confidence: float = 0.6
    provenance: Provenance = Provenance.INFERRED


@dataclass
class DiscoveryContext:
    """What an adapter is given. Deliberately small — inventory, source root, and the graph."""

    source_root: Path
    inventory: RepositoryInventory
    graph: KnowledgeGraph

    assignments: list[dict[str, Any]] = field(default_factory=list)
    """Boundary classifications from the `classify_boundaries` step, when the manifest ran one.

    Empty for a pack that does not classify, which is why every adapter must tolerate it.
    """

    def text(self, record: InventoryFile) -> str | None:
        """Read a file, or None when it cannot be read or is too large to be worth parsing."""
        if record.size > MAX_SOURCE_BYTES:
            return None
        try:
            return (self.source_root / record.path).read_text(encoding="utf-8-sig")
        except (OSError, UnicodeDecodeError):
            return None


@runtime_checkable
class EntryPointAdapter(Protocol):
    """What a stack must implement to contribute entry points.

    `declares` is what makes the coverage report honest: an adapter states the classes it is
    responsible for whether or not it finds any, so a class that exists in the world and yields
    nothing here is distinguishable from a class nobody is looking for.
    """

    name: str
    declares: tuple[str, ...]

    def discover(self, context: DiscoveryContext) -> Iterable[DiscoveredEntryPoint]:
        ...


ENTRY_POINT_ADAPTERS: dict[str, EntryPointAdapter] = {}


def entry_point_adapter(adapter: EntryPointAdapter) -> EntryPointAdapter:
    """Register an adapter. Mirrors `@capability`, including the duplicate-name guard.

    Registration is also what makes the adapter's classes count as interaction boundaries. Those
    two facts belong together: a class the planner will not seed a cluster from is a class whose
    adapter cannot produce a requirement, and keeping the boundary set in one hand-edited literal
    is precisely how `SCHEDULED_JOB` came to be declared for years with nothing able to produce
    one. An adapter now brings its own classes with it.
    """
    existing = ENTRY_POINT_ADAPTERS.get(adapter.name)
    if existing is not None and existing is not adapter:
        raise ValueError(f"Entry-point adapter {adapter.name!r} is already registered")
    ENTRY_POINT_ADAPTERS[adapter.name] = adapter
    BOUNDARY_KINDS.update(adapter.declares)
    return adapter


# --------------------------------------------------------------------------------------------
# The adapter that replaced syntactic entry-point detection
# --------------------------------------------------------------------------------------------


@dataclass
class ClassifiedBoundaryAdapter:
    """Turns the boundary-classification step's answers into entry points.

    The model is never trusted about *where* anything is. It returns a `qualified_name` and a
    class; this adapter resolves that name against the graph and takes the location from the
    entity it found. A name that resolves to nothing is dropped, which is what makes a fabricated
    candidate name harmless rather than a fabricated citation.

    `declares` covers every class the classifier may return, so the coverage report distinguishes
    "no scheduled jobs in this repository" from "nothing here can find a scheduled job" — the
    distinction this seam exists for.
    """

    name: str = "classified-boundary"
    declares: tuple[str, ...] = (
        "ENDPOINT",
        "CLI_ENTRY",
        "CLI_COMMAND",
        "SCRIPT_ENTRY",
        "UI_ACTION",
        "SCHEDULED_JOB",
        "EVENT_CONSUMER",
        "REGISTERED_ENTRY",
    )

    def discover(self, context: DiscoveryContext) -> Iterable[DiscoveredEntryPoint]:
        if not context.assignments:
            return
        by_qualified = {entity.qualified_name: entity for entity in context.graph.entities}
        for assignment in context.assignments:
            entry_class = str(assignment.get("entry_class") or "")
            # NONE is the classifier declining, and declining is the common correct answer.
            if entry_class not in self.declares:
                continue
            owner = by_qualified.get(str(assignment.get("qualified_name") or ""))
            if owner is None or owner.location is None:
                continue
            # The identifier becomes the boundary entity's qualified name, so falling back to the
            # handler's own name bare would give two entities one name — the boundary and the
            # function it exposes — and `by_qualified_name` would silently keep one.
            #
            # A fallback is the common case, not the exception: Graphify does not surface a
            # decorator's arguments, so the literal route the old extractor read out of
            # `@app.post("/invoices")` is not in the payload for anyone to return.
            identifier = str(assignment.get("identifier") or "").strip() or _default_identifier(
                entry_class, owner
            )
            effect = assignment.get("effect")
            yield DiscoveredEntryPoint(
                entry_class=entry_class,
                identifier=identifier,
                location=owner.location,
                handler=owner.qualified_name,
                effect=effect if effect in {"read", "mutate"} else None,
                attributes={
                    "rationale": assignment.get("rationale"),
                    "classified_from": "graph-shape",
                    **(
                        {"registrars": list(assignment["registrars"])}
                        if assignment.get("registrars")
                        else {}
                    ),
                },
                # Deliberately low, and lower than any syntactic detector would have been. That a
                # model reading call degrees and referenced names identified a boundary is an
                # inference, and a requirement resting on one should be worded as cautiously as
                # its evidence deserves.
                confidence=0.55,
                provenance=Provenance.INFERRED,
            )


def _default_identifier(entry_class: str, owner: Entity) -> str:
    """Name a boundary the classifier could not name from a literal.

    The identifier becomes the boundary entity's qualified name, so it has to be distinct from the
    handler's — otherwise the boundary and the function it exposes share one name and
    `by_qualified_name` keeps only one of them.

    A script and a command-line entry keep the conventions this project has always used for them,
    `module!__main__` and `module.function!cli`. Those are not Graphify's to supply and never were:
    a `__main__` guard has no name of its own in any language, so the name was always composed.
    Everything else is named after the handler, prefixed by its class.
    """
    location = owner.location
    module = "root"
    if location is not None and location.file:
        module = ".".join(PurePosixPath(location.file.replace("\\", "/")).with_suffix("").parts)
    bare = owner.name.strip(". ()") or owner.qualified_name
    if entry_class == "SCRIPT_ENTRY":
        return f"{module}!__main__"
    if entry_class == "CLI_ENTRY":
        return f"{module}.{bare}!cli"
    return f"{entry_class} {owner.qualified_name}"


entry_point_adapter(ClassifiedBoundaryAdapter())


# --------------------------------------------------------------------------------------------
# The capability
# --------------------------------------------------------------------------------------------


def _coverage(graph: KnowledgeGraph) -> dict[str, int]:
    return dict(Counter(e.kind for e in graph.entities if e.kind in BOUNDARY_KINDS))


@capability("discover_entry_points")
def discover_entry_points(ctx: CapabilityContext) -> CapabilityResult:
    """Run every registered adapter, fold what they find into the graph, and report coverage."""
    inventory = ctx.load("inventory", RepositoryInventory)
    graph = ctx.load("graph", KnowledgeGraph)

    if not getattr(ctx.settings, "entry_point_adapters", True):
        return CapabilityResult(
            artifacts={"graph": graph, "entry_point_coverage": {"disabled": True}},
            messages=["Entry-point adapters are switched off; only the built-in detectors ran."],
        )

    # Optional: a pack that classifies boundaries declares `boundary_assignments` in `consumes`,
    # and a pack that does not simply runs the deterministic adapters over an empty list.
    assignments: list[dict[str, Any]] = []
    artifacts = ctx.state.get("artifacts") or {}
    if "boundary_assignments" in artifacts:
        payload = ctx.load("boundary_assignments")
        assignments = list(payload.get("assignments") or [])
        # Fold each candidate's own evidence back in beside the classification. The model is asked
        # only to name a class; the registrar names that decided it are facts from the graph, and
        # recording them on the boundary is what lets the leakage pass keep deriving its
        # vocabulary from the repository rather than from a wordlist.
        if "boundary_candidates" in artifacts:
            evidence = {
                str(item.get("qualified_name")): item
                for item in (ctx.load("boundary_candidates").get("candidates") or [])
            }
            for assignment in assignments:
                candidate = evidence.get(str(assignment.get("qualified_name")))
                if candidate is None:
                    continue
                assignment["registrars"] = sorted(
                    {
                        *(candidate.get("references_undefined_names") or []),
                        *(candidate.get("registered_by") or []),
                    }
                )

    context = DiscoveryContext(
        source_root=Path(ctx.source_root),
        inventory=inventory,
        graph=graph,
        assignments=assignments,
    )
    by_qualified = {entity.qualified_name: entity for entity in graph.entities}
    known = {entity.id for entity in graph.entities}

    added: list[Entity] = []
    per_adapter: dict[str, dict[str, int]] = {}
    failures: list[str] = []

    for name, adapter in sorted(ENTRY_POINT_ADAPTERS.items()):
        counts: Counter[str] = Counter()
        try:
            found = list(adapter.discover(context))
        except Exception as exc:
            # One adapter failing must not cost the others, and the gap is reported rather than
            # swallowed: an adapter that silently found nothing looks exactly like a stack that
            # has nothing, which is the confusion this module exists to remove.
            failures.append(f"{name}: {type(exc).__name__}: {exc}")
            found = []
        for item in found:
            entity = Entity(
                id=stable_id("entrypoint", name, item.entry_class, item.identifier),
                kind=item.entry_class,
                name=item.identifier,
                qualified_name=item.identifier,
                language=None,
                location=item.location,
                provenance=item.provenance,
                confidence=item.confidence,
                attributes={
                    **item.attributes,
                    "discovered_by": name,
                    **({"handler": item.handler} if item.handler else {}),
                    **({"effect": item.effect} if item.effect else {}),
                },
            )
            if entity.id in known:
                continue
            known.add(entity.id)
            added.append(entity)
            counts[item.entry_class] += 1

            # Wire the handler so the boundary has a workflow to walk into. Without this the
            # packet is a single declaration node and the requirement is written from nothing.
            owner = by_qualified.get(item.handler or "")
            if owner is not None:
                graph.relationships.append(
                    _expose(owner.id, entity.id, item.location, item.confidence)
                )
        per_adapter[name] = {
            "declares": list(adapter.declares),
            "found": dict(counts),
            "total": sum(counts.values()),
        }

    graph.entities.extend(added)

    # Classes present in the graph that no adapter claims, and classes claimed by an adapter that
    # produced nothing. Both are the report's whole point.
    claimed = {klass for adapter in ENTRY_POINT_ADAPTERS.values() for klass in adapter.declares}
    in_graph = _coverage(graph)
    coverage = {
        "by_class": in_graph,
        "by_adapter": per_adapter,
        "declared_but_empty": sorted(
            klass for klass in claimed if in_graph.get(klass, 0) == 0
        ),
        "present_but_unclaimed": sorted(set(in_graph) - claimed),
        "boundary_kinds_with_no_adapter": sorted(BOUNDARY_KINDS - claimed),
        "adapter_failures": failures,
    }

    messages: list[str] = []
    if added:
        messages.append(
            f"Registered adapters contributed {len(added)} entry point(s): "
            + ", ".join(
                f"{name} {report['total']}"
                for name, report in sorted(per_adapter.items())
                if report["total"]
            )
            + "."
        )
    if coverage["declared_but_empty"]:
        messages.append(
            "No entry point was found for "
            + ", ".join(coverage["declared_but_empty"])
            + ". Either the repository has none, or the adapter for that class did not "
            "recognise the way this codebase declares them."
        )
    if coverage["boundary_kinds_with_no_adapter"]:
        messages.append(
            "No adapter claims "
            + ", ".join(coverage["boundary_kinds_with_no_adapter"])
            + ", so a repository using it would produce nothing and say nothing."
        )
    for failure in failures:
        messages.append(f"An entry-point adapter failed and contributed nothing: {failure}")

    return CapabilityResult(
        artifacts={"graph": graph, "entry_point_coverage": coverage},
        metrics={
            "adapters_run": len(ENTRY_POINT_ADAPTERS),
            "entry_points_added": len(added),
            "entry_point_classes": len(in_graph),
            "classes_declared_but_empty": len(coverage["declared_but_empty"]),
            "kinds_with_no_adapter": len(coverage["boundary_kinds_with_no_adapter"]),
            **{f"class_{klass}": count for klass, count in in_graph.items()},
        },
        messages=messages,
    )


def _expose(source: str, target: str, location: SourceLocation, confidence: float):
    from agentic.schemas.core import Relationship

    return Relationship(
        id=stable_id("rel", source, target, "EXPOSES", location.line_start),
        source=source,
        target=target,
        kind="EXPOSES",
        provenance=Provenance.INFERRED,
        confidence=confidence,
        evidence=[location],
    )

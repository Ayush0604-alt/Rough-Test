"""Boundary detection after the swap: graph shape in, classified entry points out.

Graphify emits no route, command, or job marker, so the syntactic detectors that used to find
interaction boundaries are gone. What replaces them is a deterministic candidate pass over call
degrees, a model call, and an adapter that trusts the model about *classification* and the graph
about *location*. These tests cover the two deterministic halves; the model call itself is
stubbed, as everywhere else in this suite.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agentic.capabilities.boundaries import MAX_CANDIDATES, propose_boundary_candidates
from agentic.capabilities.entry_points import (
    BOUNDARY_KINDS,
    ClassifiedBoundaryAdapter,
    DiscoveryContext,
)
from agentic.capabilities.registry import CapabilityContext
from agentic.config import Settings
from agentic.schemas.boundaries import BoundaryAssignment, BoundaryAssignments, BoundaryClass
from agentic.schemas.core import (
    Entity,
    KnowledgeGraph,
    Provenance,
    Relationship,
    RepositoryInventory,
    SourceLocation,
)
from agentic.store import ArtifactStore


@dataclass
class _Step:
    id: str = "propose"
    capability: str = "propose_boundary_candidates"


def _entity(node_id: str, name: str, kind: str = "FUNCTION", line: int = 10) -> Entity:
    return Entity(
        id=node_id,
        kind=kind,
        name=name,
        qualified_name=f"app.py::{name}",
        location=SourceLocation(file="app.py", line_start=line, line_end=line + 4, symbol=name),
    )


def _rel(source: str, target: str, kind: str) -> Relationship:
    return Relationship(id=f"{source}-{kind}-{target}", source=source, target=target, kind=kind)


def _graph(entities: list[Entity], relationships: list[Relationship]) -> KnowledgeGraph:
    return KnowledgeGraph(repository_name="t", entities=entities, relationships=relationships)


def _propose(tmp_path: Path, graph: KnowledgeGraph) -> dict[str, Any]:
    run_dir = tmp_path / "run"
    (run_dir / "artifacts").mkdir(parents=True)
    (run_dir / "source").mkdir()
    store = ArtifactStore(run_dir)
    state: dict[str, Any] = {
        "run_dir": str(run_dir),
        "source_root": str(run_dir / "source"),
        "artifacts": {"graph": store.save("graph", graph)},
    }
    ctx = CapabilityContext(
        state=state,  # type: ignore[arg-type]
        step=_Step(),  # type: ignore[arg-type]
        settings=Settings(runs_dir=tmp_path / "runs"),
        store=store,
        skill=None,  # type: ignore[arg-type]
    )
    return propose_boundary_candidates(ctx).artifacts["boundary_candidates"]


# --------------------------------------------------------------------------------------------
# Candidate selection
# --------------------------------------------------------------------------------------------


def test_an_uncalled_function_is_a_candidate(tmp_path: Path) -> None:
    """The primary signal: a handler is invoked by a framework, never by the code beside it."""
    graph = _graph([_entity("h", "handler")], [])
    names = {item["qualified_name"] for item in _propose(tmp_path, graph)["candidates"]}
    assert "app.py::handler" in names


def test_a_called_function_with_no_registration_reference_is_not_a_candidate(
    tmp_path: Path,
) -> None:
    graph = _graph(
        [_entity("caller", "caller"), _entity("helper", "helper", line=30)],
        [_rel("caller", "helper", "CALLS")],
    )
    names = {item["qualified_name"] for item in _propose(tmp_path, graph)["candidates"]}
    assert "app.py::helper" not in names
    assert "app.py::caller" in names


def test_a_called_function_that_also_carries_a_registration_stays_a_candidate(
    tmp_path: Path,
) -> None:
    """A route handler can also be called directly from a test or a second route."""
    graph = _graph(
        [
            _entity("caller", "caller"),
            _entity("handler", "handler", line=30),
            Entity(id="post", kind="EXTERNAL_SYMBOL", name="post", qualified_name="post"),
        ],
        [_rel("caller", "handler", "CALLS"), _rel("handler", "post", "REFERENCES")],
    )
    candidates = {
        item["qualified_name"]: item for item in _propose(tmp_path, graph)["candidates"]
    }
    assert "app.py::handler" in candidates
    assert candidates["app.py::handler"]["references_undefined_names"] == ["post"]


def test_files_and_external_symbols_are_never_candidates(tmp_path: Path) -> None:
    graph = _graph(
        [
            Entity(
                id="f",
                kind="FILE",
                name="app.py",
                qualified_name="app.py",
                location=SourceLocation(file="app.py", line_start=1, line_end=50),
            ),
            Entity(id="x", kind="EXTERNAL_SYMBOL", name="os", qualified_name="os"),
        ],
        [],
    )
    assert _propose(tmp_path, graph)["candidates"] == []


def test_a_candidate_without_a_location_is_dropped(tmp_path: Path) -> None:
    """Nothing can be cited about it, so nothing downstream could ground a requirement on it."""
    orphan = Entity(id="o", kind="FUNCTION", name="ghost", qualified_name="ghost")
    assert _propose(tmp_path, _graph([orphan], []))["candidates"] == []


def test_uninformative_references_are_filtered_out(tmp_path: Path) -> None:
    """A `self` beside every method buries the one reference that carries the signal."""
    graph = _graph(
        [
            _entity("h", "handler"),
            Entity(id="self", kind="EXTERNAL_SYMBOL", name="self", qualified_name="self"),
        ],
        [_rel("h", "self", "REFERENCES")],
    )
    candidate = _propose(tmp_path, graph)["candidates"][0]
    assert candidate["references_undefined_names"] == []


def test_candidates_are_capped_and_the_truncation_is_reported(tmp_path: Path) -> None:
    entities = [_entity(f"n{i}", f"fn{i}", line=i + 1) for i in range(MAX_CANDIDATES + 25)]
    payload = _propose(tmp_path, _graph(entities, []))
    assert len(payload["candidates"]) == MAX_CANDIDATES
    assert payload["considered"] == MAX_CANDIDATES + 25
    assert payload["truncated"] is True


def test_registration_carrying_candidates_are_ranked_first(tmp_path: Path) -> None:
    """The cap must not throw away the likeliest boundaries."""
    entities = [_entity(f"n{i}", f"fn{i}", line=i + 1) for i in range(MAX_CANDIDATES + 5)]
    entities.append(Entity(id="get", kind="EXTERNAL_SYMBOL", name="get", qualified_name="get"))
    graph = _graph(entities, [_rel("n5", "get", "REFERENCES")])
    payload = _propose(tmp_path, graph)
    assert payload["candidates"][0]["qualified_name"] == "app.py::fn5"


# --------------------------------------------------------------------------------------------
# Folding classifications back in
# --------------------------------------------------------------------------------------------


def _discover(graph: KnowledgeGraph, assignments: list[dict[str, Any]]):
    context = DiscoveryContext(
        source_root=Path("."),
        inventory=RepositoryInventory(repository_name="t"),
        graph=graph,
        assignments=assignments,
    )
    return list(ClassifiedBoundaryAdapter().discover(context))


def test_a_classified_candidate_becomes_an_entry_point() -> None:
    graph = _graph([_entity("h", "create_invoice")], [])
    found = _discover(
        graph,
        [
            {
                "qualified_name": "app.py::create_invoice",
                "entry_class": "ENDPOINT",
                "identifier": "POST /invoices",
                "effect": "mutate",
            }
        ],
    )
    assert len(found) == 1
    assert found[0].entry_class == "ENDPOINT"
    assert found[0].identifier == "POST /invoices"
    assert found[0].handler == "app.py::create_invoice"
    assert found[0].effect == "mutate"


def test_the_location_comes_from_the_graph_not_the_model() -> None:
    """The anti-fabrication property: the model names a node, never a line."""
    graph = _graph([_entity("h", "create_invoice", line=31)], [])
    found = _discover(
        graph,
        [
            {
                "qualified_name": "app.py::create_invoice",
                "entry_class": "ENDPOINT",
                "identifier": "POST /invoices",
                "file": "somewhere/else.py",
                "line_start": 9999,
            }
        ],
    )
    assert found[0].location.file == "app.py"
    assert found[0].location.line_start == 31


def test_a_name_that_resolves_to_nothing_is_dropped() -> None:
    graph = _graph([_entity("h", "real")], [])
    assert (
        _discover(graph, [{"qualified_name": "app.py::invented", "entry_class": "ENDPOINT"}])
        == []
    )


def test_none_is_an_accepted_answer_and_produces_no_boundary() -> None:
    """Most candidates are ordinary internal code. Declining must be cheap and normal."""
    graph = _graph([_entity("h", "helper")], [])
    assert _discover(graph, [{"qualified_name": "app.py::helper", "entry_class": "NONE"}]) == []


def test_a_class_outside_the_declared_set_is_refused() -> None:
    graph = _graph([_entity("h", "helper")], [])
    assert (
        _discover(graph, [{"qualified_name": "app.py::helper", "entry_class": "DATABASE"}]) == []
    )


def test_an_invented_effect_is_discarded_rather_than_carried() -> None:
    graph = _graph([_entity("h", "handler")], [])
    found = _discover(
        graph,
        [{"qualified_name": "app.py::handler", "entry_class": "ENDPOINT", "effect": "maybe"}],
    )
    assert found[0].effect is None


def test_classified_boundaries_are_marked_inferred_and_low_confidence() -> None:
    graph = _graph([_entity("h", "handler")], [])
    found = _discover(graph, [{"qualified_name": "app.py::handler", "entry_class": "ENDPOINT"}])
    assert found[0].provenance is Provenance.INFERRED
    assert found[0].confidence < 0.7


def test_a_missing_identifier_falls_back_to_a_name_distinct_from_the_handler() -> None:
    """The identifier becomes the boundary entity's qualified name.

    Falling back to the handler's own name bare would give the boundary and the function it
    exposes one name, and `by_qualified_name` would keep only one of them. The fallback is the
    common case, not the exception: Graphify does not surface a decorator's arguments, so the
    literal route is not in the payload for the classifier to return.
    """
    graph = _graph([_entity("h", "handler")], [])
    found = _discover(graph, [{"qualified_name": "app.py::handler", "entry_class": "CLI_ENTRY"}])
    # A script and a command-line entry keep this project's own conventions for them, because a
    # `__main__` guard has no name of its own in any language and the name was always composed.
    assert found[0].identifier == "app.handler!cli"

    script = _discover(
        graph, [{"qualified_name": "app.py::handler", "entry_class": "SCRIPT_ENTRY"}]
    )
    assert script[0].identifier == "app!__main__"

    other = _discover(
        graph, [{"qualified_name": "app.py::handler", "entry_class": "UI_ACTION"}]
    )
    assert other[0].identifier != "app.py::handler"
    assert "app.py::handler" in other[0].identifier


def test_the_adapter_declares_every_class_the_schema_allows() -> None:
    """Otherwise a class the model can return is one the coverage report cannot account for."""
    schema_classes = {item.value for item in BoundaryClass} - {"NONE"}
    assert schema_classes == set(ClassifiedBoundaryAdapter().declares)


def test_registering_the_adapter_extended_the_boundary_kinds() -> None:
    assert set(ClassifiedBoundaryAdapter().declares) <= BOUNDARY_KINDS


def test_an_absent_classification_step_leaves_the_adapter_silent() -> None:
    """A pack that does not classify must still run this adapter without failing."""
    assert _discover(_graph([_entity("h", "handler")], []), []) == []


def test_the_schema_round_trips_a_full_assignment() -> None:
    payload = BoundaryAssignments(
        assignments=[
            BoundaryAssignment(
                qualified_name="app.py::create_invoice",
                entry_class=BoundaryClass.ENDPOINT,
                identifier="POST /invoices",
                effect="mutate",
                rationale="nothing here calls it, and it references an undefined name",
            )
        ]
    )
    dumped = payload.model_dump(mode="json")
    assert dumped["assignments"][0]["entry_class"] == "ENDPOINT"

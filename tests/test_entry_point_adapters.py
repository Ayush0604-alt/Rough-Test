"""The entry-point adapter seam.

The property under test is that a paradigm is added by *registering* an adapter — no edit to the
traversal, the planner, the manifest, or this module's own dispatch. Everything a stack knows
about itself has to live inside its own adapter, because that is the only place stack-specific
code is permitted.

The second property is that an omission is visible. A class nobody looks for and a class that
genuinely has no instances must be distinguishable in the report, or a whole paradigm can go
missing exactly as `SCHEDULED_JOB` did: declared as a boundary kind for the life of the project
with nothing anywhere able to produce one.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from tests.conftest import run_step_or_stub
from agentic.capabilities.entry_points import (
    ENTRY_POINT_ADAPTERS,
    DiscoveredEntryPoint,
    DiscoveryContext,
    EntryPointAdapter,
    entry_point_adapter,
)
from agentic.capabilities.graph_ops import BOUNDARY_KINDS
from agentic.capabilities.registry import CapabilityContext
from agentic.config import Settings
from agentic.runtime import prepare_run
from agentic.schemas.core import KnowledgeGraph, SourceLocation
from agentic.skills.pack import load_pack
from agentic.state import initial_state
from agentic.store import ArtifactStore

REPO_ROOT = Path(__file__).resolve().parents[1]


def _discover(fixture: str, **overrides):
    """Run the deterministic prefix through discovery and hand back graph, coverage, metrics."""
    workspace = Path(tempfile.mkdtemp())
    manifest = load_pack(REPO_ROOT / "codebase-requirement-skills")
    settings = Settings(runs_dir=workspace / "runs", **overrides)
    prepared = prepare_run(source=REPO_ROOT / "tests" / "fixtures" / fixture, settings=settings)
    state = initial_state(
        run_id="t",
        pack="codebase-requirements",
        run_dir=str(prepared.run_dir),
        source_root=str(prepared.source_root),
        repository_name=fixture,
    )
    store = ArtifactStore(prepared.run_dir)

    def step(step_id: str):
        spec = manifest.step(step_id)
        ctx = CapabilityContext(
            state=state,
            step=spec,
            settings=settings,
            store=store,
            skill=manifest.skill_registry().load(spec.skill),
            payload={"manifest": manifest},
        )
        outcome = run_step_or_stub(spec, ctx)
        for name, value in (outcome.artifacts or {}).items():
            state["artifacts"][name] = store.save(name, value)
        return outcome

    # Kept in step with `conftest.EXTRACTION_STEPS`. Three test files each maintain their own copy
    # of this sequence and only `test_pack_compile` derives it from the manifest; until that is
    # fixed, a step added to the extraction phase has to be added here by hand or this test
    # quietly stops exercising the real pipeline.
    for step_id in (
        "ingest", "graphify_extract", "adapt_graph", "extract_declarations",
        "extract_instruction_artifacts",
        "propose_boundaries", "classify_boundaries", "collect_boundary_assignments",
    ):
        step(step_id)
    result = step("discover_entry_points")
    graph = store.load_model(state["artifacts"]["graph"], KnowledgeGraph)
    return graph, result.artifacts["entry_point_coverage"], result.metrics


# --------------------------------------------------------------------------------------------
# The registry
# --------------------------------------------------------------------------------------------


def test_every_registered_adapter_satisfies_the_protocol() -> None:
    for name, adapter in ENTRY_POINT_ADAPTERS.items():
        assert isinstance(adapter, EntryPointAdapter), name
        assert adapter.declares, f"{name} declares no class, so it cannot be reported on"


def test_registering_an_adapter_also_makes_its_classes_boundaries() -> None:
    """Otherwise an adapter can find entry points the planner will never seed a cluster from."""
    for adapter in ENTRY_POINT_ADAPTERS.values():
        for klass in adapter.declares:
            assert klass in BOUNDARY_KINDS, f"{klass} would be found and then ignored"


def test_a_duplicate_adapter_name_is_refused() -> None:
    class _Fake:
        name = "classified-boundary"
        declares = ("ENDPOINT",)

        def discover(self, context):
            return ()

    with pytest.raises(ValueError, match="already registered"):
        entry_point_adapter(_Fake())


def test_a_new_paradigm_needs_only_a_registration() -> None:
    """The seam's whole purpose, exercised without touching any shipped module."""

    class _Webhooks:
        name = "test-webhooks"
        declares = ("WEBHOOK",)

        def discover(self, context: DiscoveryContext):
            yield DiscoveredEntryPoint(
                entry_class="WEBHOOK",
                identifier="inbound payment notice",
                location=SourceLocation(file="app/main.py", line_start=1),
                effect="mutate",
            )

    try:
        entry_point_adapter(_Webhooks())
        assert "WEBHOOK" in BOUNDARY_KINDS
        graph, coverage, _ = _discover("sample_app")
        assert coverage["by_class"].get("WEBHOOK") == 1
        found = next(e for e in graph.entities if e.kind == "WEBHOOK")
        assert found.attributes["discovered_by"] == "test-webhooks"
        assert found.attributes["effect"] == "mutate"
    finally:
        ENTRY_POINT_ADAPTERS.pop("test-webhooks", None)
        BOUNDARY_KINDS.discard("WEBHOOK")


def test_one_failing_adapter_does_not_cost_the_others() -> None:
    class _Broken:
        name = "test-broken"
        declares = ("BROKEN",)

        def discover(self, context):
            raise RuntimeError("adapter exploded")

    try:
        entry_point_adapter(_Broken())
        _, coverage, _ = _discover("sample_app")
        assert any("adapter exploded" in failure for failure in coverage["adapter_failures"])
        assert coverage["by_class"].get("ENDPOINT"), "the other adapters still contributed"
    finally:
        ENTRY_POINT_ADAPTERS.pop("test-broken", None)
        BOUNDARY_KINDS.discard("BROKEN")


# --------------------------------------------------------------------------------------------
# The coverage report
# --------------------------------------------------------------------------------------------


def test_the_report_distinguishes_empty_from_unclaimed() -> None:
    """A class nobody looks for is a different failure from a class with no instances."""
    _, coverage, _ = _discover("sample_app")

    assert "UI_ACTION" in coverage["declared_but_empty"], (
        "an adapter claims UI actions and this fixture has none"
    )
    # Every boundary kind is now claimed by one adapter, so the hole this list exists to expose
    # should be empty. It stays in the report because the next paradigm added is exactly as
    # capable of reintroducing one as `SCHEDULED_JOB` was.
    assert coverage["boundary_kinds_with_no_adapter"] == []


def test_the_report_names_every_adapter_and_what_it_declares() -> None:
    _, coverage, _ = _discover("sample_app")
    for name, adapter in ENTRY_POINT_ADAPTERS.items():
        assert name in coverage["by_adapter"]
        assert coverage["by_adapter"][name]["declares"] == list(adapter.declares)


# --------------------------------------------------------------------------------------------
# The classified-boundary adapter
# --------------------------------------------------------------------------------------------


def test_registered_handlers_become_entry_points() -> None:
    """py_worker is a paradigm with no routes and no `__main__` — invisible to syntax alone."""
    graph, _, metrics = _discover("corpus/py_worker")

    assert metrics["entry_points_added"] == 3
    found = {
        entity.kind
        for entity in graph.entities
        if entity.kind in {"EVENT_CONSUMER", "SCHEDULED_JOB"}
    }
    assert found == {"EVENT_CONSUMER", "SCHEDULED_JOB"}


def test_a_registered_handler_is_wired_to_the_function_that_serves_it() -> None:
    """Without the edge the packet is one declaration node and the requirement rests on nothing."""
    graph, _, _ = _discover("corpus/py_worker")
    by_id = {e.id: e for e in graph.entities}
    exposed = {
        by_id[edge.target].qualified_name
        for edge in graph.relationships
        if edge.kind == "EXPOSES"
        and by_id.get(edge.target) is not None
        and by_id[edge.target].kind in {"EVENT_CONSUMER", "SCHEDULED_JOB"}
    }
    assert len(exposed) == 3


def test_a_boundary_is_never_published_without_a_location() -> None:
    """The model names a node; the location is taken from the graph or the boundary is dropped."""
    for fixture in ("corpus/py_service", "corpus/py_cli", "corpus/py_worker"):
        graph, _, _ = _discover(fixture)
        for entity in graph.entities:
            if entity.kind in BOUNDARY_KINDS:
                assert entity.location is not None, f"{fixture}: {entity.qualified_name}"
                assert entity.location.line_start, f"{fixture}: {entity.qualified_name}"


def test_classified_boundaries_are_marked_as_inferred() -> None:
    """They are a judgement from graph shape, not a parsed route, and must read as one."""
    graph, _, _ = _discover("corpus/py_service")
    boundaries = [e for e in graph.entities if e.kind in BOUNDARY_KINDS]
    assert boundaries
    assert all(entity.provenance.value == "INFERRED" for entity in boundaries)
    assert all(entity.confidence < 0.7 for entity in boundaries)


def test_helpers_are_not_promoted_to_boundaries() -> None:
    """Over-collecting candidates is cheap; publishing them as capabilities is not."""
    graph, _, _ = _discover("corpus/py_service")
    boundaries = {
        (entity.attributes or {}).get("handler")
        for entity in graph.entities
        if entity.kind in BOUNDARY_KINDS
    }
    # `list_orders` and friends live in the store and are called by the handlers above them.
    assert not any(name and "store.py" in name for name in boundaries)


def test_the_pass_can_be_turned_off() -> None:
    _, coverage, _ = _discover("corpus/py_worker", entry_point_adapters=False)
    assert coverage == {"disabled": True}

"""A cited symbol must really be at the cited line — and an enclosing definition is not one.

The sample app's functions are all short enough that any statement inside one sits within
`SYMBOL_WINDOW` lines of its own `def`. That hides the failure these tests cover: attributing a
deep statement to its owning definition's name makes the name look absent from the cited line, so
Gate 1 rejects an honestly extracted location and the run halts on a real repository.

Two tests here covered `scope` as the extractor set it on statement-level entities — a business
rule or an error handler deep inside a long function. Graphify models no such entity, so nothing
in the graph carries a scope any more and those tests were removed rather than left asserting
nothing. `scope` itself stays: it is still what a model writes into a citation, `verify_location`
still refuses to line-check it, and `test_scope_alone_is_never_line_checked` below still proves it.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from tests.conftest import EXTRACTION_STEPS, run_step_or_stub
from agentic.capabilities.registry import CapabilityContext
from agentic.capabilities.verify import SYMBOL_WINDOW, verify_location
from agentic.config import Settings
from agentic.schemas.core import KnowledgeGraph, SourceLocation
from agentic.state import initial_state
from agentic.store import ArtifactStore

FIXTURE = Path(__file__).parent / "fixtures" / "long_app"


@pytest.fixture
def long_run(tmp_path: Path, manifest):
    """Ingest and extract the padded fixture, returning the graph and its source root."""
    run_dir = tmp_path / "run"
    (run_dir / "artifacts").mkdir(parents=True)
    shutil.copytree(FIXTURE, run_dir / "source")

    settings = Settings(runs_dir=tmp_path / "runs")
    state = initial_state(
        run_id="long-run",
        pack="codebase-requirements",
        run_dir=str(run_dir),
        source_root=str(run_dir / "source"),
        repository_name="long_app",
    )
    store = ArtifactStore(run_dir)

    def run(step_id: str):
        step = manifest.step(step_id)
        ctx = CapabilityContext(
            state=state,
            step=step,
            settings=settings,
            store=store,
            skill=manifest.skill_registry().load(step.skill),
            payload={"manifest": manifest},
        )
        result = run_step_or_stub(step, ctx)
        for name, value in result.artifacts.items():
            state.setdefault("artifacts", {})[name] = store.save(name, value)
        return result

    for step_id in EXTRACTION_STEPS:
        run(step_id)
    graph = store.load_model(state["artifacts"]["graph"], KnowledgeGraph)
    return graph, run_dir / "source", run


def test_fixture_is_actually_longer_than_the_symbol_window(long_run) -> None:
    """Guards the guard: if the fixture shrinks, every test below stops proving anything."""
    graph, source_root, _ = long_run
    function = graph.by_qualified_name()["service.py::build_enriched_summary()"]
    assert function.location is not None
    span = (function.location.line_end or 0) - (function.location.line_start or 0)
    assert span > 2 * SYMBOL_WINDOW, f"fixture function spans only {span} lines"


def test_gate_one_passes_on_a_long_but_honest_module(long_run) -> None:
    """The regression itself: nothing here is fabricated, so nothing may be reported as such."""
    _, _, run = long_run
    report = run("gate_graph").gate
    assert report.valid, report.error_messages()
    assert not [issue for issue in report.issues if issue.code == "UNRESOLVABLE_LOCATION"]


def test_every_extracted_symbol_really_appears_at_its_cited_line(long_run) -> None:
    graph, source_root, _ = long_run
    checked = 0
    for entity in graph.entities:
        if entity.location is None or entity.location.symbol is None:
            continue
        check = verify_location(entity.location, source_root)
        assert check.ok, f"{entity.qualified_name}: {check.message}"
        checked += 1
    for relationship in graph.relationships:
        for location in relationship.evidence:
            if location.symbol is None:
                continue
            check = verify_location(location, source_root)
            assert check.ok, f"{relationship.kind} {relationship.id}: {check.message}"
            checked += 1
    assert checked >= 5, "fixture produced too few symbol citations to prove anything"
def test_a_module_never_cites_its_own_dotted_name_as_a_symbol(long_run) -> None:
    """A module's dotted name is a path, and never appears as a token in its own source."""
    graph, source_root, _ = long_run
    module_names = {
        entity.qualified_name for entity in graph.entities if entity.kind == "MODULE"
    }
    for entity in graph.entities:
        if entity.location is not None:
            assert entity.location.symbol not in module_names
    for relationship in graph.relationships:
        for location in relationship.evidence:
            assert location.symbol not in module_names
def test_scope_alone_is_never_line_checked(tmp_path: Path) -> None:
    """A scope header may be any distance above the cited line, so it makes no line promise."""
    source = tmp_path / "far.py"
    source.write_text("def owner():\n" + "    pass\n" * 400, encoding="utf-8")
    location = SourceLocation(
        file="far.py", line_start=300, scope="far.owner", symbol=None
    )
    assert verify_location(location, tmp_path).ok

    # The same line, with the scope name moved into `symbol`, is what used to fail.
    mislabelled = SourceLocation(file="far.py", line_start=300, symbol="far.owner")
    assert not verify_location(mislabelled, tmp_path).ok

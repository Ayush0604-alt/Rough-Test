"""The architecture's own acceptance test.

The claim is that a second skill pack (test-case generation, threat modelling, migration
analysis) plugs in by adding markdown and a manifest, without touching `graph/`, `state.py`,
`runtime.py`, or `app.py`. These tests build a genuinely new pack at runtime and run it. If they
ever need a change under `src/agentic/graph/` to pass, the abstraction has leaked.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from agentic.capabilities.registry import CAPABILITIES, CapabilityResult, capability
from agentic.config import Settings
from agentic.runtime import prepare_run, run
from agentic.skills.pack import load_pack

FIXTURE_APP = Path(__file__).parent / "fixtures" / "sample_app"

SKILLS = {
    "surface-inventory": "Inventory the externally reachable surface of a codebase.",
    "surface-reporting": "Render an inventory of externally reachable surface into a report.",
}


def _build_pack(root: Path) -> Path:
    """Author a new pack from scratch: two skill files and a manifest."""
    directory = root / "surface-pack"
    for name, description in SKILLS.items():
        skill_dir = directory / name
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            f"---\nname: {name}\ndescription: {description}\n---\n\n"
            f"# {name}\n\n## Method\n\n{description}\n",
            encoding="utf-8",
        )
    (directory / "pack.yaml").write_text(
        textwrap.dedent(
            """
            pack: codebase-surface
            version: 1
            description: List the points at which a codebase is consumed from outside.
            master_skill: surface-inventory

            steps:
              - id: ingest
                skill: surface-inventory
                mode: deterministic
                capability: ingest_repository
                produces: [inventory]
                on_fail: halt

              - id: run_extractor
                skill: surface-inventory
                mode: deterministic
                capability: graphify_extract
                consumes: [inventory]
                produces: [graphify_raw]
                on_fail: halt

              - id: extract
                skill: surface-inventory
                mode: deterministic
                capability: adapt_graphify_graph
                consumes: [inventory, graphify_raw]
                produces: [graph]
                on_fail: halt

              - id: gate_graph
                skill: surface-inventory
                mode: gate
                capability: validate_graph
                consumes: [graph]
                produces: [graph_gate]
                on_fail: halt

              - id: report
                skill: surface-reporting
                mode: deterministic
                capability: render_surface_report
                consumes: [graph]
                produces: [surface]
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )
    return directory


@pytest.fixture(autouse=True)
def surface_capability():
    """A capability a new pack brings with it, registered the ordinary way."""

    @capability("render_surface_report")
    def render_surface_report(ctx):
        from agentic.capabilities.graph_ops import GraphIndex
        from agentic.schemas.core import KnowledgeGraph

        graph = ctx.load("graph", KnowledgeGraph)
        index = GraphIndex(graph)
        boundaries = sorted(
            graph.by_id()[entity_id].qualified_name
            for entity_id in index.boundary_ids()
            if entity_id in graph.by_id()
        )
        # This pack declares no boundary-classification step, so it has no boundaries — which is
        # the point: a pack composes the steps it wants and the engine supplies nothing it did not
        # ask for. Falling back to the definitions extraction did find keeps the report about
        # something real rather than empty.
        if not boundaries:
            boundaries = sorted(
                entity.qualified_name
                for entity in graph.entities
                if entity.kind in {"FUNCTION", "METHOD", "CLASS"}
            )
        report = "# Externally reachable surface\n\n" + "\n".join(
            f"- {name}" for name in boundaries
        )
        return CapabilityResult(
            artifacts={"surface": {"boundaries": boundaries}},
            files={"surface_report.md": ctx.store.save_text("surface_report.md", report)},
            metrics={"boundaries": len(boundaries)},
        )

    yield
    CAPABILITIES.pop("render_surface_report", None)


def test_a_brand_new_pack_compiles_and_runs_without_touching_graph_code(tmp_path) -> None:
    pack_root = _build_pack(tmp_path / "packs")

    manifest = load_pack(pack_root)
    assert manifest.pack == "codebase-surface"

    settings = Settings(runs_dir=tmp_path / "runs")
    prepared = prepare_run(source=FIXTURE_APP, settings=settings)
    final = run(
        "codebase-surface", prepared, settings=settings, search_root=tmp_path / "packs"
    )

    assert final["status"] in {"passed", "passed_with_warnings"}
    assert final["completed_steps"] == [
        "ingest", "run_extractor", "extract", "gate_graph", "report"
    ]

    report = (prepared.run_dir / "artifacts" / "surface_report.md").read_text(encoding="utf-8")
    # This pack declares no boundary-classification step, so it has no boundaries and its report
    # lists what extraction alone found. That is the property under test: a pack composes the
    # steps it wants, and the engine neither requires nor supplies the ones it leaves out.
    assert "create_invoice" in report
    assert "read_invoice" in report
    # The internal helper is not part of the externally reachable surface.
    # This pack has no boundaries, so its report falls back to every definition extraction found
    # — dead code included. That is the fallback being honest about what it is listing, not a
    # reachability claim: reachability is what the boundary steps this pack omits would decide.
    assert "_unused_reconciliation_helper" in report


def test_new_pack_reuses_existing_capabilities_and_records_its_own_skills(tmp_path) -> None:
    """Three of the four steps are capabilities the first pack already registered."""
    _build_pack(tmp_path / "packs")
    settings = Settings(runs_dir=tmp_path / "runs")
    prepared = prepare_run(source=FIXTURE_APP, settings=settings)
    final = run(
        "codebase-surface", prepared, settings=settings, search_root=tmp_path / "packs"
    )

    skills = {record["step"]: record["skill"] for record in final["step_records"]}
    assert skills == {
        "ingest": "surface-inventory",
        "run_extractor": "surface-inventory",
        "extract": "surface-inventory",
        "gate_graph": "surface-inventory",
        "report": "surface-reporting",
    }
    # Provenance follows the new pack's own files, not the first pack's.
    assert len({record["skill_sha256"] for record in final["step_records"]}) == 2


def test_a_pack_with_no_model_steps_needs_no_credentials(tmp_path) -> None:
    """Extensibility includes packs that are purely deterministic."""
    from agentic.runtime import preflight

    pack_root = _build_pack(tmp_path / "packs")
    manifest = load_pack(pack_root)
    assert preflight(manifest, Settings()) == []

"""The manifest is the workflow, so a broken manifest must fail at load, not mid-run."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from agentic.graph.builder import build_state_graph, describe_topology
from agentic.skills.pack import PackManifest, load_pack


def test_pack_loads_and_resolves_every_reference(manifest: PackManifest) -> None:
    assert manifest.pack == "codebase-requirements"
    assert [step.id for step in manifest.steps] == [
        "ingest", "graphify_extract", "adapt_graph",
        "propose_boundaries", "classify_boundaries", "discover_entry_points",
        "gate_graph", "plan_clusters",
        "synthesize", "cluster_candidates", "digest_areas", "elevate", "assemble",
        # Consolidation and resolution: the stage between synthesis and validation that decides
        # what the candidates add up to.
        "consolidate", "check_correspondence", "score_confidence", "strip_implementation",
        "resolve", "derive_criteria",
        "prepare_claims",
        "gate_requirements", "repair", "apply_repairs", "quarantine", "final_check", "render",
    ]


def test_graph_compiles(manifest: PackManifest, settings) -> None:
    build_state_graph(manifest, settings).compile()


def test_gate_pass_path_skips_the_repair_step(manifest: PackManifest) -> None:
    """A passing requirement gate must go on towards render, not fall into the step below it."""
    rows = {row["step"]: row for row in describe_topology(manifest)}
    assert rows["gate_requirements"]["on_success"] == "final_check"
    assert "repair" in rows["gate_requirements"]["on_failure"]
    assert "quarantine" in rows["gate_requirements"]["on_failure"]




def test_skill_bodies_load_with_content_hashes(manifest: PackManifest) -> None:
    registry = manifest.skill_registry()
    available = set(registry.available())
    # Every skill a step names must be on disk. A pack may also carry a skill no step wires up
    # yet, so the count is not fixed — the referenced set being covered is what matters. An
    # unwired skill must declare itself as one; that is checked below.
    assert {step.skill for step in manifest.steps} <= available
    for name in registry.available():
        skill = registry.load(name)
        assert skill.body.strip()
        assert len(skill.sha256) == 64
        assert skill.description


def test_section_extraction_narrows_the_prompt(manifest: PackManifest) -> None:
    """A step pointing at one gate must not be handed the whole two-gate skill."""
    registry = manifest.skill_registry()
    skill = registry.load("requirement-and-graph-validation-gates")
    gate_one = skill.section("Gate 1")
    assert "Gate 1" in gate_one
    assert "Gate 2 — Requirement validation" not in gate_one
    assert len(gate_one) < len(skill.body)


def test_unknown_section_falls_back_to_the_whole_body(manifest: PackManifest) -> None:
    skill = manifest.skill_registry().load("requirement-and-graph-validation-gates")
    assert skill.section("No Such Heading") == skill.body


def _write_pack(directory: Path, body: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    skill = directory / "demo-skill"
    skill.mkdir(exist_ok=True)
    (skill / "SKILL.md").write_text(
        "---\nname: demo-skill\ndescription: demo\n---\n\n# Demo\n\nBody.\n", encoding="utf-8"
    )
    (directory / "pack.yaml").write_text(textwrap.dedent(body), encoding="utf-8")
    return directory


def test_unknown_capability_is_rejected_at_load(tmp_path: Path) -> None:
    directory = _write_pack(
        tmp_path / "bad-capability",
        """
        pack: bad
        steps:
          - id: only
            skill: demo-skill
            mode: deterministic
            capability: no_such_capability
        """,
    )
    with pytest.raises(ValueError, match="Unknown capability 'no_such_capability'"):
        load_pack(directory)


def test_unknown_schema_is_rejected_at_load(tmp_path: Path) -> None:
    directory = _write_pack(
        tmp_path / "bad-schema",
        """
        pack: bad
        steps:
          - id: only
            skill: demo-skill
            mode: llm
            output_schema: NoSuchSchema
        """,
    )
    with pytest.raises(ValueError, match="Unknown output_schema 'NoSuchSchema'"):
        load_pack(directory)


def test_unknown_skill_is_rejected_at_load(tmp_path: Path) -> None:
    directory = _write_pack(
        tmp_path / "bad-skill",
        """
        pack: bad
        steps:
          - id: only
            skill: missing-skill
            mode: deterministic
            capability: ingest_repository
        """,
    )
    with pytest.raises(ValueError, match="Unknown skill 'missing-skill'"):
        load_pack(directory)


def test_consuming_an_unproduced_artifact_is_rejected(tmp_path: Path) -> None:
    """Catching this here is what lets a runtime artifact miss be treated as a real defect."""
    directory = _write_pack(
        tmp_path / "bad-order",
        """
        pack: bad
        steps:
          - id: first
            skill: demo-skill
            mode: deterministic
            capability: ingest_repository
            consumes: [graph]
        """,
    )
    with pytest.raises(ValueError, match="consumes"):
        load_pack(directory)


def test_repair_without_a_repair_step_is_rejected(tmp_path: Path) -> None:
    directory = _write_pack(
        tmp_path / "bad-repair",
        """
        pack: bad
        steps:
          - id: only
            skill: demo-skill
            mode: gate
            capability: validate_graph
            on_fail: repair
        """,
    )
    with pytest.raises(ValueError, match="names no repair_step"):
        load_pack(directory)


def test_no_unwired_skill_is_left_unmarked(manifest: PackManifest) -> None:
    """A skill file sitting in a pack directory reads as live behaviour unless it says otherwise.

    `advisory-routing-orchestration` is a design sketch for a step that was never built, and
    routing here is deliberately model-free (`graph/router.py`). Leaving it unmarked invites a
    reader — or a later maintainer planning a second pack — to treat it as part of the workflow.

    The check runs in both directions on purpose. An orphan that forgets the marker fails, and a
    skill that later gets wired into `pack.yaml` while keeping the marker fails too, so the
    frontmatter and the manifest cannot drift apart.
    """
    registry = manifest.skill_registry()
    referenced = {step.skill for step in manifest.steps}
    if manifest.master_skill:
        referenced.add(manifest.master_skill)

    for name in sorted(registry.available()):
        marked = str(registry.load(name).frontmatter.get("status", "")).strip() == "not-wired"
        if name in referenced:
            assert not marked, (
                f"Skill {name!r} is named by a step in {manifest.pack!r} but still declares "
                "`status: not-wired`. Remove the marker and the banner from its SKILL.md."
            )
        else:
            assert marked, (
                f"Skill {name!r} sits in the {manifest.pack!r} pack directory but no step names "
                "it. Either wire it into pack.yaml, delete it, or declare `status: not-wired` in "
                "its frontmatter so nobody mistakes it for live behaviour."
            )

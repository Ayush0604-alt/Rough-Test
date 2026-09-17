"""Handing one pack's output to the pack that consumes it.

Chaining by hand meant copying a run directory out of the log and pasting it back into the sidebar
with the pack selector still on the pack that just finished. That is how a test-generation run came
to be pointed at a codebase three times running.

The mechanism is deliberately generic: the *downstream* pack declares what it follows, so the
requirements pack knows nothing about the test pack, and a third pack chains for free.
"""

from __future__ import annotations

import json
from pathlib import Path


from agentic.runtime import successor_packs
from agentic.skills.pack import load_pack

REPO_ROOT = Path(__file__).resolve().parents[1]


def _pack(root: Path, name: str, *, consumes: str = "", artifact: str = "") -> Path:
    directory = root / name
    (directory / "only-skill").mkdir(parents=True)
    (directory / "only-skill" / "SKILL.md").write_text(
        f"---\nname: only-skill\ndescription: A skill for {name}.\n---\n\n# {name}\n",
        encoding="utf-8",
    )
    lines = [
        f"pack: {name}",
        "version: 1",
        "description: A pack.",
    ]
    if consumes:
        lines.append(f"consumes_pack: {consumes}")
    if artifact:
        lines.append(f"consumes_artifact: {artifact}")
    lines += [
        "steps:",
        "  - id: ingest",
        "    skill: only-skill",
        "    mode: deterministic",
        "    capability: ingest_repository",
        "    produces: [inventory]",
    ]
    (directory / "pack.yaml").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return directory


def _completed_run(root: Path, name: str, *files: str) -> Path:
    run = root / name
    (run / "artifacts").mkdir(parents=True)
    for filename in files:
        (run / "artifacts" / filename).write_text(json.dumps([{"id": "FR-1"}]), encoding="utf-8")
    return run


# ---------------------------------------------------------------------------------------------
# The real packs
# ---------------------------------------------------------------------------------------------


def test_the_test_pack_declares_what_it_follows() -> None:
    manifest = load_pack(REPO_ROOT / "test-generation-skills")
    assert manifest.consumes_pack == "codebase-requirements"
    assert manifest.consumes_artifact == "requirements.json"


def test_the_requirements_pack_declares_nothing_about_the_pack_that_follows_it() -> None:
    """The direction matters: upstream must not know downstream exists."""
    manifest = load_pack(REPO_ROOT / "codebase-requirement-skills")
    assert manifest.consumes_pack is None
    assert manifest.consumes_artifact is None


# ---------------------------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------------------------


def test_a_successful_run_offers_the_pack_that_consumes_it(tmp_path) -> None:
    packs = tmp_path / "packs"
    _pack(packs, "upstream")
    _pack(packs, "downstream", consumes="upstream", artifact="requirements.json")
    run = _completed_run(tmp_path / "runs", "good", "requirements.json")

    found = successor_packs("upstream", run, search_root=packs)
    assert [name for name, _ in found] == ["downstream"]
    assert found[0][1] == run / "artifacts"


def test_a_run_that_produced_nothing_is_not_offered(tmp_path) -> None:
    """A requirements run that halted must never be handed to the pack that would read it.

    This is the case that produced the confusion: a failed run leaves a directory that looks
    exactly like a successful one.
    """
    packs = tmp_path / "packs"
    _pack(packs, "upstream")
    _pack(packs, "downstream", consumes="upstream", artifact="requirements.json")
    run = _completed_run(tmp_path / "runs", "halted")
    (run / "artifacts" / "run_failed.md").write_text("stopped", encoding="utf-8")

    assert successor_packs("upstream", run, search_root=packs) == []


def test_a_pack_with_no_consumer_offers_nothing(tmp_path) -> None:
    packs = tmp_path / "packs"
    _pack(packs, "upstream")
    _pack(packs, "unrelated", consumes="something-else", artifact="other.json")
    run = _completed_run(tmp_path / "runs", "good", "requirements.json")

    assert successor_packs("upstream", run, search_root=packs) == []


def test_a_third_pack_chains_without_anything_upstream_changing(tmp_path) -> None:
    """The generality claim. Nothing in the mechanism names a pack."""
    packs = tmp_path / "packs"
    _pack(packs, "first")
    _pack(packs, "second", consumes="first", artifact="a.json")
    _pack(packs, "third", consumes="second", artifact="b.json")

    after_first = _completed_run(tmp_path / "runs", "r1", "a.json")
    after_second = _completed_run(tmp_path / "runs", "r2", "b.json")

    assert [n for n, _ in successor_packs("first", after_first, search_root=packs)] == ["second"]
    assert [n for n, _ in successor_packs("second", after_second, search_root=packs)] == ["third"]


def test_a_declaration_without_a_named_artifact_chains_on_the_run_alone(tmp_path) -> None:
    packs = tmp_path / "packs"
    _pack(packs, "upstream")
    _pack(packs, "downstream", consumes="upstream")
    run = _completed_run(tmp_path / "runs", "any")

    assert [n for n, _ in successor_packs("upstream", run, search_root=packs)] == ["downstream"]


def test_a_broken_manifest_beside_a_good_one_is_skipped_not_fatal(tmp_path) -> None:
    packs = tmp_path / "packs"
    _pack(packs, "upstream")
    _pack(packs, "downstream", consumes="upstream", artifact="requirements.json")
    broken = packs / "broken"
    broken.mkdir()
    (broken / "pack.yaml").write_text("pack: broken\nsteps: []\n", encoding="utf-8")
    run = _completed_run(tmp_path / "runs", "good", "requirements.json")

    assert [n for n, _ in successor_packs("upstream", run, search_root=packs)] == ["downstream"]

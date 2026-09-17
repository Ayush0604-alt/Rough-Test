"""Reading the requirements artifact without trusting it.

Every case here is drawn from something the artifact really does, or really could do. The rule the
whole module defends: bad input produces a thinner set and a recorded reason, never an exception.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agentic.capabilities.testgen.ingest import (
    read_requirement,
    read_requirement_set,
)
from agentic.schemas.testgen import CONTRACT_KEYS

FIXTURES = Path(__file__).parent / "fixtures" / "requirement_sets"


def _fixture(name: str) -> list:
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------------------------
# The happy path, on real artifacts
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name,expected", [("rich", 190), ("thin", 13), ("degenerate", 1), ("legacy_20key", 21)]
)
def test_real_artifacts_read_completely_and_without_issues(name: str, expected: int) -> None:
    result = read_requirement_set(_fixture(name))
    assert len(result.requirements) == expected
    assert result.read_issues == [], "a real artifact should need no defensive handling"


def test_the_legacy_artifact_reads_with_defaults_and_says_which_keys_were_absent() -> None:
    """A run produced before `area` was published must still ingest, not fail a version check."""
    result = read_requirement_set(_fixture("legacy_20key"))
    assert result.missing_contract_keys == ["area", "area_name", "priority"]
    assert len(result.requirements) == 21
    assert all(item.area is None for item in result.requirements)
    assert all(item.priority == "M" for item in result.requirements), "documented default"


def test_a_current_artifact_reports_no_missing_keys() -> None:
    result = read_requirement_set(_fixture("rich"))
    assert result.missing_contract_keys == []
    assert result.contract_keys_seen == list(CONTRACT_KEYS)


# ---------------------------------------------------------------------------------------------
# Defensive reading
# ---------------------------------------------------------------------------------------------


def test_the_malformed_artifact_never_raises_and_records_every_decision() -> None:
    payload = _fixture("malformed")
    result = read_requirement_set(payload, source_file="malformed.json")

    codes = {issue.code for issue in result.read_issues}
    assert "ENTRY_NOT_AN_OBJECT" in codes
    assert "MISSING_IDENTIFIER" in codes
    assert "DUPLICATE_IDENTIFIER" in codes
    assert "COERCED_FIELD" in codes

    # Every issue can be traced back to a position in the input file.
    assert all(issue.source_index >= 0 for issue in result.read_issues)

    # The good entries survived; nothing was lost because a neighbour was broken.
    ids = {item.id for item in result.requirements}
    assert "FR-OK-01" in ids
    assert "FR-TYPES-01" in ids


def test_an_entry_with_no_id_is_dropped_because_nothing_could_report_it() -> None:
    requirement, issues = read_requirement({"statement": "A user shall do a thing."}, 3)
    assert requirement is None
    assert [issue.code for issue in issues] == ["MISSING_IDENTIFIER"]
    assert issues[0].source_index == 3


def test_a_blank_statement_is_kept_for_qualification_to_exclude_with_a_reason() -> None:
    """Dropping it here would make it vanish; excluding it later reports it by id."""
    requirement, issues = read_requirement({"id": "FR-1", "statement": "   "}, 0)
    assert requirement is not None
    assert requirement.id == "FR-1"
    assert requirement.statement == ""
    assert issues == []


def test_a_colliding_id_keeps_the_first_and_records_the_collision() -> None:
    """Two items under one id make every downstream traceability link ambiguous."""
    result = read_requirement_set(
        [
            {"id": "FR-1", "statement": "The first."},
            {"id": "FR-1", "statement": "The second."},
        ]
    )
    assert [item.statement for item in result.requirements] == ["The first."]
    assert [issue.code for issue in result.read_issues] == ["DUPLICATE_IDENTIFIER"]
    assert result.read_issues[0].requirement_id == "FR-1"


@pytest.mark.parametrize(
    "field,value,expected",
    [
        ("acceptance_criteria", "a bare string", ["a bare string"]),
        ("acceptance_criteria", None, []),
        ("acceptance_criteria", 42, []),
        ("graph_path", {"not": "a list"}, []),
        ("assumptions", ["fine", "", "  ", "also fine"], ["fine", "also fine"]),
    ],
)
def test_list_fields_are_coerced_or_dropped_never_guessed(field, value, expected) -> None:
    requirement, _ = read_requirement({"id": "X", "statement": "s", field: value}, 0)
    assert getattr(requirement, field) == expected


def test_a_non_numeric_confidence_becomes_unstated_rather_than_a_wrong_number() -> None:
    requirement, issues = read_requirement(
        {"id": "X", "statement": "s", "confidence": "high"}, 0
    )
    assert requirement.confidence is None
    assert any(issue.code == "COERCED_FIELD" for issue in issues)


def test_an_out_of_vocabulary_priority_is_normalised() -> None:
    requirement, issues = read_requirement({"id": "X", "statement": "s", "priority": 7}, 0)
    assert requirement.priority == "M"
    assert any(issue.code == "COERCED_FIELD" for issue in issues)


def test_a_single_evidence_object_is_wrapped_rather_than_discarded() -> None:
    requirement, _ = read_requirement(
        {"id": "X", "statement": "s", "evidence": {"file": "a.py", "line_start": 1}}, 0
    )
    assert [location.file for location in requirement.evidence] == ["a.py"]


def test_evidence_without_a_file_is_dropped_because_it_points_nowhere() -> None:
    requirement, _ = read_requirement(
        {"id": "X", "statement": "s", "evidence": [{"line_start": 1}, {"file": "b.py"}]}, 0
    )
    assert [location.file for location in requirement.evidence] == ["b.py"]


def test_an_unknown_key_is_preserved_so_a_future_field_is_not_lost() -> None:
    """The contract is append-only; ingestion must not reject what it has not been told about."""
    requirement, issues = read_requirement(
        {"id": "X", "statement": "s", "some_future_field": {"nested": True}}, 0
    )
    assert requirement.extras == {"some_future_field": {"nested": True}}
    assert issues == [], "an unknown key is not a problem"


# ---------------------------------------------------------------------------------------------
# Derived views
# ---------------------------------------------------------------------------------------------


def test_generated_criteria_are_recognised_and_held_out_of_the_usable_set() -> None:
    requirement, _ = read_requirement(
        {
            "id": "X",
            "statement": "A user shall view a summary.",
            "acceptance_criteria": [
                "Exercising the published interface produces the outcome this requirement describes.",
                "The total shown equals the number of open items.",
            ],
        },
        0,
    )
    assert requirement.usable_criteria() == ["The total shown equals the number of open items."]
    assert len(requirement.discarded_criteria()) == 1


def test_the_derived_marker_alone_empties_the_usable_set() -> None:
    """The second, independent signal: the assumption the same pass writes beside the criterion.

    Either signal on its own could drift if the requirements pack rewords one; requiring only one
    of the two to fire is what makes the detection robust.
    """
    requirement, _ = read_requirement(
        {
            "id": "X",
            "statement": "A user shall view a summary.",
            "acceptance_criteria": ["Some criterion phrased differently."],
            "assumptions": [
                "The acceptance criterion below was derived from the cited evidence rather than "
                "stated by the analysis, and should be confirmed against the product's intent."
            ],
        },
        0,
    )
    assert requirement.usable_criteria() == []
    assert requirement.discarded_criteria() == ["Some criterion phrased differently."]


def test_the_rich_fixture_discards_exactly_the_measured_boilerplate() -> None:
    """59 of 235 criteria, emptying 59 items that look criteria-bearing."""
    result = read_requirement_set(_fixture("rich"))
    discarded = sum(len(item.discarded_criteria()) for item in result.requirements)
    emptied = sum(
        1
        for item in result.requirements
        if item.acceptance_criteria and not item.usable_criteria()
    )
    assert discarded == 59
    assert emptied == 59


def test_searchable_text_excludes_rationale_where_technical_vocabulary_is_allowed() -> None:
    requirement, _ = read_requirement(
        {
            "id": "X",
            "statement": "A user shall view a summary.",
            "rationale": "Implemented by PostgresRepository.fetch_all with a LIMIT clause.",
            "acceptance_criteria": ["The summary lists open items."],
        },
        0,
    )
    text = requirement.searchable_text()
    assert "summary" in text
    assert "PostgresRepository" not in text, "rationale must not activate a dimension"


# ---------------------------------------------------------------------------------------------
# Saying what to do next, not merely what is absent
# ---------------------------------------------------------------------------------------------


def test_pointing_at_a_source_repository_names_the_actual_next_step(tmp_path) -> None:
    """The likeliest operator mistake, and `requirements.txt` is why.

    A Python project has one, it sits exactly where `requirements.json` would be, and it makes
    pointing at the repository look correct. Restating "not found" leaves the operator to work out
    that a whole other pack has to run first.
    """
    from agentic.capabilities.testgen.ingest import _no_input_reason

    (tmp_path / "requirements.txt").write_text("fastapi\n", encoding="utf-8")
    (tmp_path / "app.py").write_text("x = 1\n", encoding="utf-8")

    reason = _no_input_reason(tmp_path)
    assert "source repository" in reason
    assert "codebase-requirements" in reason, "it must name the pack to run first"
    assert "dependency list" in reason, "and disambiguate requirements.txt"


def test_pointing_one_level_above_the_artifacts_says_so(tmp_path) -> None:
    from agentic.capabilities.testgen.ingest import _no_input_reason

    (tmp_path / "artifacts").mkdir()
    (tmp_path / "source").mkdir()
    reason = _no_input_reason(tmp_path)
    assert "run directory" in reason
    assert "artifacts" in reason


def test_an_unrecognisable_directory_gets_the_general_message(tmp_path) -> None:
    from agentic.capabilities.testgen.ingest import _no_input_reason

    (tmp_path / "notes.md").write_text("hello", encoding="utf-8")
    reason = _no_input_reason(tmp_path)
    assert "artifacts" in reason
    assert "codebase-requirements" not in reason, "do not guess at a fix that may not apply"


def test_a_failed_runs_source_tree_is_named_as_such(tmp_path) -> None:
    """The exact confusion this hit twice.

    A failed run of *this* pack leaves `runs/<id>/{source,artifacts}` that looks structurally
    identical to a requirements run — so an operator checks its `artifacts/`, finds only the
    failure note, and concludes the requirements came out empty rather than that the run failed.
    """
    from agentic.capabilities.testgen.ingest import _no_input_reason

    run = tmp_path / "runs" / "abc123"
    (run / "source").mkdir(parents=True)
    (run / "artifacts").mkdir()
    (run / "artifacts" / "run_failed.md").write_text("stopped", encoding="utf-8")
    (run / "source" / "requirements.txt").write_text("fastapi\n", encoding="utf-8")

    reason = _no_input_reason(run / "source", tmp_path / "runs")
    assert "FAILED" in reason
    assert "abc123" in reason
    assert "only the failure note" in reason


def test_the_message_names_runs_that_do_hold_a_requirements_artifact(tmp_path) -> None:
    """Turning "not here" into "here" is the most useful thing the message can do."""
    from agentic.capabilities.testgen.ingest import _no_input_reason

    runs = tmp_path / "runs"
    good = runs / "goodrun" / "artifacts"
    good.mkdir(parents=True)
    (good / "requirements.json").write_text(
        json.dumps([{"id": "FR-1", "statement": "A user shall do a thing."}]), encoding="utf-8"
    )
    target = tmp_path / "somewhere"
    target.mkdir()
    (target / "requirements.txt").write_text("flask\n", encoding="utf-8")

    reason = _no_input_reason(target, runs)
    assert "goodrun" in reason
    assert "1 requirement(s)" in reason


def test_the_message_says_so_when_no_run_holds_one(tmp_path) -> None:
    from agentic.capabilities.testgen.ingest import _no_input_reason

    runs = tmp_path / "runs"
    runs.mkdir()
    target = tmp_path / "repo"
    target.mkdir()
    (target / "pyproject.toml").write_text("[project]\n", encoding="utf-8")

    reason = _no_input_reason(target, runs)
    assert "has to run first" in reason


def test_an_empty_or_unreadable_artifact_is_not_offered_as_a_candidate(tmp_path) -> None:
    """Suggesting a run whose artifact is empty or corrupt would send the operator in a circle."""
    from agentic.capabilities.testgen.ingest import available_requirement_runs

    runs = tmp_path / "runs"
    for name, body in [("empty", "[]"), ("corrupt", "{not json"), ("good", '[{"id":"FR-1"}]')]:
        directory = runs / name / "artifacts"
        directory.mkdir(parents=True)
        (directory / "requirements.json").write_text(body, encoding="utf-8")

    found = [path.parent.name for path, _ in available_requirement_runs(runs)]
    assert found == ["good"]


# ---------------------------------------------------------------------------------------------
# Resolving a codebase to the requirements run that analysed it
#
# Pointing this pack at a codebase is the natural next action after generating requirements for
# that codebase, and it was a dead end. Worse, `prepare_run` copies the tree and creates a run
# directory *before* this step can say so, so the failure left behind a folder that looked exactly
# like a requirements run — which is how one requirements run comes to look like two.
# ---------------------------------------------------------------------------------------------


def _requirements_run(runs: Path, name: str, source: Path, count: int = 2) -> Path:
    """A completed requirements run over `source`, fingerprinted the way the real pack does."""
    from agentic.capabilities.ingest import build_inventory

    artifacts = runs / name / "artifacts"
    artifacts.mkdir(parents=True)
    inventory = build_inventory(source, name)
    (artifacts / "inventory.json").write_text(
        inventory.model_dump_json(), encoding="utf-8"
    )
    (artifacts / "requirements.json").write_text(
        json.dumps(
            [
                {"id": f"FR-{index}", "statement": f"A user shall do thing {index}."}
                for index in range(count)
            ]
        ),
        encoding="utf-8",
    )
    return artifacts


def _codebase(root: Path, body: str = "x = 1\n") -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "app.py").write_text(body, encoding="utf-8")
    (root / "requirements.txt").write_text("flask\n", encoding="utf-8")
    return root


def test_a_codebase_resolves_to_the_run_that_analysed_it(tmp_path) -> None:
    from agentic.capabilities.testgen.ingest import resolve_from_source

    source = _codebase(tmp_path / "repo")
    runs = tmp_path / "runs"
    _requirements_run(runs, "analysed", source, count=3)

    match = resolve_from_source(source, runs)
    assert match is not None
    path, run_id = match
    assert run_id == "analysed"
    assert json.loads(path.read_text(encoding="utf-8")) != []


def test_a_changed_codebase_does_not_resolve(tmp_path) -> None:
    """The match is byte-exact on purpose. Stale requirements are worse than none."""
    from agentic.capabilities.testgen.ingest import resolve_from_source

    source = _codebase(tmp_path / "repo")
    runs = tmp_path / "runs"
    _requirements_run(runs, "analysed", source)

    (source / "app.py").write_text("x = 2  # edited since\n", encoding="utf-8")
    assert resolve_from_source(source, runs) is None


def test_a_different_codebase_does_not_resolve(tmp_path) -> None:
    from agentic.capabilities.testgen.ingest import resolve_from_source

    analysed = _codebase(tmp_path / "one")
    other = _codebase(tmp_path / "two", body="y = 99\n")
    runs = tmp_path / "runs"
    _requirements_run(runs, "analysed", analysed)

    assert resolve_from_source(other, runs) is None


def test_a_run_without_an_inventory_cannot_prove_a_match(tmp_path) -> None:
    """Without the fingerprint there is no proof, and guessing is what this avoids."""
    from agentic.capabilities.testgen.ingest import resolve_from_source

    source = _codebase(tmp_path / "repo")
    runs = tmp_path / "runs"
    artifacts = _requirements_run(runs, "analysed", source)
    (artifacts / "inventory.json").unlink()

    assert resolve_from_source(source, runs) is None


def test_the_newest_matching_run_wins(tmp_path) -> None:
    import os
    import time

    from agentic.capabilities.testgen.ingest import resolve_from_source

    source = _codebase(tmp_path / "repo")
    runs = tmp_path / "runs"
    older = _requirements_run(runs, "older", source)
    newer = _requirements_run(runs, "newer", source)
    # Make the ordering unambiguous rather than relying on filesystem timing.
    now = time.time()
    os.utime(older, (now - 600, now - 600))
    os.utime(newer, (now, now))

    match = resolve_from_source(source, runs)
    assert match is not None and match[1] == "newer"


def test_resolution_is_announced_rather_than_silent(tmp_path) -> None:
    """Reading a directory other than the one pointed at must never be quiet."""
    from agentic.capabilities.registry import CapabilityContext, get_capability
    from agentic.config import Settings
    from agentic.skills.pack import load_pack
    from agentic.state import initial_state
    from agentic.store import ArtifactStore

    runs = tmp_path / "runs"
    source = _codebase(tmp_path / "repo")
    _requirements_run(runs, "analysed", source, count=4)

    manifest = load_pack(Path(__file__).resolve().parents[1] / "test-generation-skills")
    step = manifest.step("ingest_requirements")
    run_dir = tmp_path / "thisrun"
    (run_dir / "artifacts").mkdir(parents=True)
    state = initial_state(
        run_id="r", pack="testcase-generation", run_dir=str(run_dir),
        source_root=str(source), repository_name="repo",
    )
    ctx = CapabilityContext(
        state=state,
        step=step,
        settings=Settings(runs_dir=runs),
        store=ArtifactStore(run_dir),
        skill=manifest.skill_registry().load(step.skill),
        payload={"manifest": manifest},
    )
    result = get_capability("ingest_requirement_set")(ctx)

    assert result.halt_reason is None
    assert result.artifacts["requirement_set"].requirements
    joined = " ".join(result.messages)
    assert "analysed" in joined, "the run it used must be named"
    assert "byte-identical" in joined
    assert "re-run the requirements pack if the code has changed" in joined

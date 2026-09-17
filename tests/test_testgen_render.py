"""The published deliverables.

Two things are defended. First, that the documents are *complete* — every requirement appears
somewhere, including the ones with no coverage, because a report that lists only what was tested
cannot answer the question it exists to answer. Second, that nothing technology-specific reaches
the file an adapter consumes.
"""

from __future__ import annotations

import csv
import io
import json
from pathlib import Path

import pytest

from agentic.capabilities.testgen.analyse import build_signals
from agentic.capabilities.testgen.assemble import assemble
from agentic.capabilities.testgen.coverage import build_coverage
from agentic.capabilities.testgen.ingest import read_requirement_set
from agentic.capabilities.testgen.qualify import qualify_one
from agentic.capabilities.testgen.render import (
    build_spec_set,
    render_clarifications_md,
    render_coverage_csv,
    render_excluded_md,
    render_test_plan_md,
    render_test_specs_json,
    render_traceability_csv,
)
from agentic.schemas.testgen import QualifiedSet
from agentic.schemas.testspec import TestSuite

FIXTURES = Path(__file__).parent / "fixtures" / "requirement_sets"


def _pipeline(fixture: str):
    """Qualification through assembly, with an obedient designer, so rendering has real input."""
    requirement_set = read_requirement_set(
        json.loads((FIXTURES / f"{fixture}.json").read_text(encoding="utf-8"))
    )
    kept, records = [], []
    for requirement in requirement_set.requirements:
        record = qualify_one(
            requirement, rejected_titles=set(), conflicted_ids=set(), already=kept
        )
        records.append(record)
        if record.testable:
            kept.append(requirement)
    qualified = QualifiedSet(records=records, requirements=requirement_set.requirements)
    signals = build_signals(qualified)

    fragments = [
        {
            "scenarios": [
                {
                    "requirement_id": signal.requirement_id,
                    "title": f"Situation for {signal.requirement_id}",
                    "context": "A known starting state.",
                    "preconditions": ["The system is reachable."],
                    "cases": [
                        {
                            "requirement_id": signal.requirement_id,
                            "dimension": dimension,
                            "title": f"{dimension} case",
                            "objective": f"Confirm the {dimension} behaviour holds.",
                            "covers_criterion": 0 if signal.usable_criteria else None,
                            "steps": ["Perform the described action."],
                            "expected_results": ["The described outcome is observable."],
                            "derivation": "direct",
                        }
                        for dimension in signal.dimensions[: signal.case_budget]
                    ],
                }
            ],
            "notes": [],
        }
        for signal in signals.generating()
    ]
    suite = assemble(qualified, signals, fragments)
    coverage = build_coverage(qualified, signals, suite)
    return qualified, suite, coverage


@pytest.fixture(scope="module")
def rendered():
    return _pipeline("rich")


# ---------------------------------------------------------------------------------------------
# The machine contract
# ---------------------------------------------------------------------------------------------


def test_every_published_case_is_self_contained(rendered) -> None:
    """An adapter reading one case must not have to resolve a parent to set up its state."""
    _, suite, coverage = rendered
    published = build_spec_set(suite, coverage)
    assert published

    for entry in published:
        assert entry["id"]
        assert entry["requirement_ids"], "traceability is not optional"
        assert entry["objective"], "every case says why it exists"
        assert entry["expected_results"], "every case is falsifiable"
        # The scenario's context and preconditions are merged in, not referenced.
        assert "scenario_context" in entry
        assert isinstance(entry["preconditions"], list)


def test_the_scenario_grouping_stays_recoverable(rendered) -> None:
    """Flattening must not lose the structure — only stop requiring a walk to use it."""
    _, suite, coverage = rendered
    published = build_spec_set(suite, coverage)
    grouped = {entry["scenario_id"] for entry in published}
    assert grouped == {scenario.id for scenario in suite.scenarios if scenario.cases}


def test_a_case_inherits_its_scenarios_preconditions(rendered) -> None:
    _, suite, coverage = rendered
    scenario = next(s for s in suite.scenarios if s.preconditions and s.cases)
    entry = next(
        e for e in build_spec_set(suite, coverage) if e["scenario_id"] == scenario.id
    )
    assert scenario.preconditions[0] in entry["preconditions"]


def test_the_spec_file_is_valid_json_with_a_version(rendered) -> None:
    _, suite, coverage = rendered
    payload = json.loads(render_test_specs_json(suite, coverage, "test"))
    assert payload["schema_version"]
    assert payload["counts"]["cases"] == len(suite.cases())
    assert len(payload["cases"]) == len(suite.cases())


# ---------------------------------------------------------------------------------------------
# Completeness — the half that is about what is missing
# ---------------------------------------------------------------------------------------------


def _rows(text: str) -> list[dict]:
    return list(csv.DictReader(io.StringIO(text)))


def test_the_coverage_matrix_has_a_row_for_every_requirement(rendered) -> None:
    qualified, _, coverage = rendered
    rows = _rows(render_coverage_csv(coverage))
    assert len(rows) == len(qualified.records) == 190
    assert all(row["verdict"] for row in rows)


def test_the_coverage_matrix_leads_with_the_worst_verdicts(rendered) -> None:
    """A reader opening the file should meet the problems first, not scroll to them."""
    _, _, coverage = rendered
    rows = _rows(render_coverage_csv(coverage))
    verdicts = [row["verdict"] for row in rows]
    if "positive_only" in verdicts and "sufficient" in verdicts:
        assert verdicts.index("positive_only") < verdicts.index("sufficient")


def test_an_untested_requirement_still_appears_in_traceability(rendered) -> None:
    """A matrix listing only what was tested cannot answer the question it exists to answer."""
    _, suite, coverage = rendered
    rows = _rows(render_traceability_csv(suite, coverage))
    listed = {row["requirement_id"] for row in rows}
    uncovered = {
        entry.requirement_id
        for entry in coverage.requirements
        if entry.requirement_id not in suite.covered_requirement_ids()
    }
    assert uncovered, "the fixture does have uncovered requirements"
    assert uncovered <= listed


def test_traceability_is_exploded_one_fact_per_row(rendered) -> None:
    _, suite, coverage = rendered
    rows = _rows(render_traceability_csv(suite, coverage))
    covered = [row for row in rows if row["case_id"]]
    assert len(covered) >= len(suite.cases())
    assert all(row["requirement_id"] for row in covered)


def test_every_uncovered_requirement_is_explained(rendered) -> None:
    qualified, suite, coverage = rendered
    document = render_excluded_md(suite, coverage, qualified)
    uncovered = [
        entry
        for entry in coverage.requirements
        if entry.verdict in {"excluded", "deferred", "none"}
    ]
    assert uncovered
    for entry in uncovered[:25]:
        assert entry.requirement_id in document, entry.requirement_id


def test_clarifications_name_the_question_not_just_the_requirement() -> None:
    qualified, _, _ = _pipeline("rich")
    document = render_clarifications_md(qualified)
    blocked = [record for record in qualified.records if record.blockers]
    if blocked:
        assert blocked[0].requirement_id in document
        assert blocked[0].blockers[0][:40] in document


# ---------------------------------------------------------------------------------------------
# The test plan
# ---------------------------------------------------------------------------------------------


def test_the_plan_leads_with_the_coverage_failure_that_hides(rendered) -> None:
    """`positive_only` reads as covered while the risky behaviour is untested."""
    qualified, suite, coverage = rendered
    plan = render_test_plan_md(suite, coverage, qualified, None)
    if coverage.with_verdict("positive_only"):
        assert "Read these first" in plan
        assert plan.index("Read these first") < plan.index("## The cases")


def test_the_plan_names_every_case_it_publishes(rendered) -> None:
    qualified, suite, coverage = rendered
    plan = render_test_plan_md(suite, coverage, qualified, None)
    for case in suite.cases()[:30]:
        assert case.id in plan


def test_an_empty_suite_produces_a_plan_that_says_why() -> None:
    """A thin suite that explains itself is a result; one that does not is a failure."""
    qualified, _, _ = _pipeline("rich")
    empty = TestSuite()
    coverage = build_coverage(qualified, build_signals(qualified), empty)
    plan = render_test_plan_md(empty, coverage, qualified, None)
    assert "No test case was generated" in plan
    assert "excluded_requirements.md" in plan


# ---------------------------------------------------------------------------------------------
# Technology independence, checked on the file that leaves the building
# ---------------------------------------------------------------------------------------------


FORBIDDEN = (
    "playwright", "pytest", "selenium", "cypress", "junit", " jest", "promptfoo", "deepeval",
    "curl ", "httpx", "assert(", "http_method", "endpoint_url", "css_selector",
)


def test_no_technology_reaches_the_published_spec(rendered) -> None:
    _, suite, coverage = rendered
    published = render_test_specs_json(suite, coverage, "test").lower()
    for term in FORBIDDEN:
        assert term not in published, f"{term!r} reached the published specification"


def test_the_interface_hint_is_published_but_is_only_advisory(rendered) -> None:
    """An adapter reads it; nothing in this pack branches on it."""
    _, suite, coverage = rendered
    published = build_spec_set(suite, coverage)
    assert all("interface_hint" in entry for entry in published)
    assert all("automation_readiness" in entry for entry in published)

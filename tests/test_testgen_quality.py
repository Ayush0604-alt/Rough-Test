"""Deduplication, the quality gate, coverage, and the repair loop.

Every check here exists because a specific kind of bad test case is worth catching. The lexicons
are heuristics, so each is tested from both sides: it must fire on the thing it is for, and it must
*not* fire on a real case from a real generated suite. A vagueness check that rejects good cases is
worse than no check, because it spends the repair budget on nothing.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agentic.capabilities.testgen.analyse import build_signals
from agentic.capabilities.testgen.assemble import assemble
from agentic.capabilities.testgen.coverage import build_coverage
from agentic.capabilities.testgen.dedupe import deduplicate
from agentic.capabilities.testgen.ingest import read_requirement_set
from agentic.capabilities.testgen.qualify import qualify_one
from agentic.capabilities.testgen.quality import (
    framework_leaks,
    needs_precondition,
    restates,
    vague_results,
)
from agentic.capabilities.testgen.validate import REPAIRABLE, check_cases, check_coverage
from agentic.schemas.core import Severity
from agentic.schemas.testgen import QualifiedSet
from agentic.schemas.testspec import CoverageLink, TestCase, TestScenario, TestSuite

FIXTURES = Path(__file__).parent / "fixtures" / "requirement_sets"


def _qualified(fixture: str) -> QualifiedSet:
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
    return QualifiedSet(records=records, requirements=requirement_set.requirements)


def _case(**kwargs) -> TestCase:
    kwargs.setdefault("id", "TC-1")
    kwargs.setdefault("requirement_ids", ["FR-1"])
    kwargs.setdefault("dimension", "positive")
    kwargs.setdefault("objective", "Confirm a summary reflects what was submitted.")
    kwargs.setdefault("steps", ["Submit a request for the current summary."])
    kwargs.setdefault("expected_results", ["The summary names every open entry."])
    kwargs.setdefault("derivation", "direct")
    kwargs.setdefault("covers", [CoverageLink(kind="acceptance_criterion", ref="0")])
    return TestCase(**kwargs)


def _suite(*cases: TestCase) -> TestSuite:
    return TestSuite(
        scenarios=[
            TestScenario(id="TS-1", requirement_id="FR-1", title="A situation", cases=list(cases))
        ]
    )


# ---------------------------------------------------------------------------------------------
# Vagueness — both directions
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "result",
    [
        "The system behaves correctly.",
        "The request is handled appropriately.",
        "The feature works.",
        "No errors occur.",
        "The operation completes.",
        "Without any errors.",
        "The response is valid.",
        "The conversation is cleared properly.",
        "The request is processed as expected.",
    ],
)
def test_an_unfalsifiable_result_is_caught(result: str) -> None:
    assert vague_results([result]) == [result]


@pytest.mark.parametrize(
    "result",
    [
        "The item is no longer listed.",
        "The refusal states that no matching item was found.",
        "An audit entry naming the operator and the item is created.",
        "A 404 response is returned with the message 'no note to delete'.",
        "The response is displayed in the chat interface within 2 seconds.",
        "The conversation history is removed from the session context.",
        "The system returns information relevant to the user's query about documents.",
    ],
)
def test_a_real_expected_result_is_not_flagged(result: str) -> None:
    """Drawn from a real generated suite. A check that rejects these is worse than no check."""
    assert vague_results([result]) == []


# ---------------------------------------------------------------------------------------------
# Framework leakage
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "Send a DELETE to the items endpoint.",
        "Use Playwright to click the button.",
        "assert(response.status == 200)",
        "Run the pytest suite.",
        "Issue a POST with the payload.",
        "Call the endpoint with curl.",
    ],
)
def test_named_technology_is_caught(text: str) -> None:
    assert framework_leaks(text)


@pytest.mark.parametrize(
    "text",
    [
        "Submit a removal request for that identifier.",
        "Acting as an authorised operator, request removal of the item.",
        "The user asks a question about their documents.",
        "Observe the visual feedback after clearing the conversation.",
    ],
)
def test_ordinary_product_vocabulary_is_not_flagged(text: str) -> None:
    assert framework_leaks(text) == []


# ---------------------------------------------------------------------------------------------
# Restatement and preconditions
# ---------------------------------------------------------------------------------------------


def test_a_case_that_repeats_its_requirement_is_caught() -> None:
    statement = (
        "An operator shall be able to remove a stored item by supplying its identifier, so that "
        "the item is no longer available."
    )
    assert restates(statement, statement)


def test_a_case_that_says_how_to_check_is_not_a_restatement() -> None:
    statement = (
        "An operator shall be able to remove a stored item by supplying its identifier, so that "
        "the item is no longer available."
    )
    checking = (
        "Confirm a removal request for an unknown identifier is refused. The refusal names the "
        "missing item and no audit entry is written."
    )
    assert not restates(checking, statement)


def test_acting_on_something_that_must_exist_needs_a_precondition() -> None:
    assert needs_precondition(
        _case(steps=["Remove the existing item."], expected_results=["It is no longer listed."])
    )


def test_a_precondition_satisfies_the_check() -> None:
    assert not needs_precondition(
        _case(
            preconditions=["An item exists with that identifier."],
            steps=["Remove the existing item."],
        )
    )


def test_a_case_creating_something_fresh_needs_no_precondition() -> None:
    assert not needs_precondition(
        _case(steps=["Submit a new record."], expected_results=["A record is returned."])
    )


# ---------------------------------------------------------------------------------------------
# The gate
# ---------------------------------------------------------------------------------------------


def _codes(issues) -> set[str]:
    return {issue.code for issue in issues}


def test_a_good_case_raises_nothing() -> None:
    qualified = _qualified("thin")
    requirement = qualified.requirements[0]
    case = _case(requirement_ids=[requirement.id])
    assert check_cases(_suite(case), qualified) == []


def test_a_case_tracing_nowhere_is_rejected() -> None:
    qualified = _qualified("thin")
    issues = check_cases(_suite(_case(requirement_ids=[])), qualified)
    assert "TC_NO_REQUIREMENT" in _codes(issues)


def test_a_case_for_an_excluded_requirement_is_rejected() -> None:
    qualified = _qualified("rich")
    excluded = next(r.requirement_id for r in qualified.records if not r.testable)
    issues = check_cases(_suite(_case(requirement_ids=[excluded])), qualified)
    assert "TC_LINKED_TO_EXCLUDED" in _codes(issues)


def test_an_input_bearing_dimension_without_inputs_is_rejected() -> None:
    qualified = _qualified("thin")
    case = _case(requirement_ids=[qualified.requirements[0].id], dimension="validation", inputs=[])
    assert "TC_MISSING_INPUTS" in _codes(check_cases(_suite(case), qualified))


def test_an_indirect_derivation_without_an_assumption_is_rejected() -> None:
    qualified = _qualified("thin")
    case = _case(
        requirement_ids=[qualified.requirements[0].id], derivation="assumed", assumptions=[]
    )
    assert "TC_UNSUPPORTED_ASSUMPTION" in _codes(check_cases(_suite(case), qualified))


def test_every_blocking_finding_is_one_a_repair_pass_could_act_on() -> None:
    """The design rule: an error the repair step cannot fix would burn the budget failing.

    Repair sees only the rejected cases and the issue list — never the requirement set, never the
    source. So a coverage gap has no fix available to it and must never be an error.
    """
    qualified = _qualified("rich")
    excluded = next(r.requirement_id for r in qualified.records if not r.testable)
    broken = _suite(
        _case(id="TC-A", requirement_ids=[], objective="", steps=[], expected_results=[]),
        _case(id="TC-B", requirement_ids=[excluded]),
        _case(id="TC-C", expected_results=["It works correctly."]),
        _case(id="TC-D", dimension="boundary", inputs=[]),
    )
    errors = {
        issue.code
        for issue in check_cases(broken, qualified)
        if issue.severity is Severity.ERROR
    }
    # Only the two grounding failures may be unrepairable; everything else must be fixable.
    assert errors - REPAIRABLE <= {"TC_NO_REQUIREMENT", "TC_LINKED_TO_EXCLUDED"}


def test_no_coverage_finding_is_ever_blocking() -> None:
    qualified = _qualified("rich")
    signals = build_signals(qualified)
    coverage = build_coverage(qualified, signals, TestSuite())
    assert all(issue.severity is Severity.WARNING for issue in check_coverage(coverage))


# ---------------------------------------------------------------------------------------------
# Deduplication
# ---------------------------------------------------------------------------------------------


def test_two_identical_cases_are_folded_into_one() -> None:
    suite = _suite(
        _case(id="TC-1", covers=[CoverageLink(kind="acceptance_criterion", ref="0")]),
        _case(id="TC-2", covers=[CoverageLink(kind="statement", ref="FR-1")]),
    )
    suite, duplicates, _ = deduplicate(suite)
    assert len(suite.cases()) == 1
    assert duplicates[0]["kept"] == "TC-1", "the criterion-backed case wins"
    assert suite.cases()[0].superseded == ["TC-2"], "the merge is visible, not silent"


def test_cases_in_different_dimensions_are_never_duplicates() -> None:
    suite = _suite(_case(id="TC-1", dimension="positive"), _case(id="TC-2", dimension="negative"))
    suite, duplicates, _ = deduplicate(suite)
    assert len(suite.cases()) == 2
    assert duplicates == []


def test_cases_for_different_requirements_are_never_duplicates() -> None:
    """Folding across requirements would silently remove a requirement's only coverage."""
    suite = _suite(
        _case(id="TC-1", requirement_ids=["FR-1"]), _case(id="TC-2", requirement_ids=["FR-2"])
    )
    suite, duplicates, _ = deduplicate(suite)
    assert len(suite.cases()) == 2
    assert duplicates == []


def test_overlapping_cases_are_reported_and_both_kept() -> None:
    """Similar enough to be worth a reviewer's eye, distinct enough that folding would lose one.

    The pair below measures 0.79 — inside the overlap band and outside the duplicate threshold.
    Both check submitting a valid identifier; only the second checks it is idempotent.
    """
    suite = _suite(
        _case(
            id="TC-1",
            steps=["Submit a valid identifier."],
            expected_results=["The record is returned."],
        ),
        _case(
            id="TC-2",
            steps=["Submit a valid identifier twice."],
            expected_results=["The record is returned unchanged both times."],
        ),
    )
    suite, duplicates, overlaps = deduplicate(suite)
    assert len(suite.cases()) == 2, "merging is a judgement wording alone cannot make"
    assert duplicates == []
    assert overlaps and 0.65 <= overlaps[0]["similarity"] < 0.85


# ---------------------------------------------------------------------------------------------
# Coverage
# ---------------------------------------------------------------------------------------------


def test_coverage_accounts_for_every_requirement_including_the_untestable() -> None:
    qualified = _qualified("rich")
    report = build_coverage(qualified, build_signals(qualified), TestSuite())
    assert len(report.requirements) == len(qualified.records) == 190


def test_a_requirement_stating_a_refusal_with_only_a_happy_path_is_flagged() -> None:
    """The most misleading failure: it reads as covered and the risky behaviour is untested."""
    qualified = _qualified("rich")
    signals = build_signals(qualified)
    target = next(
        s
        for s in signals.generating()
        if {"error_handling", "validation", "negative"} & set(s.dimensions)
    )
    suite = assemble(
        qualified,
        signals,
        [
            {
                "scenarios": [
                    {
                        "requirement_id": target.requirement_id,
                        "title": "Happy path only",
                        "cases": [
                            {
                                "requirement_id": target.requirement_id,
                                "dimension": "positive",
                                "title": "It works on the good path",
                                "objective": "Confirm the described outcome follows.",
                                "steps": ["Do the thing."],
                                "expected_results": ["The outcome is observable."],
                            }
                        ],
                    }
                ],
                "notes": [],
            }
        ],
    )
    report = build_coverage(qualified, signals, suite)
    entry = next(e for e in report.requirements if e.requirement_id == target.requirement_id)
    assert entry.verdict == "positive_only"


def test_an_uncovered_criterion_is_named() -> None:
    qualified = _qualified("rich")
    signals = build_signals(qualified)
    report = build_coverage(qualified, signals, TestSuite())
    uncovered = report.uncovered_criteria()
    assert uncovered
    assert all(entry.criterion_text for entry in uncovered), "a gap must name what is uncovered"


# ---------------------------------------------------------------------------------------------
# Identifiers
# ---------------------------------------------------------------------------------------------


def test_scenario_and_case_ids_are_unique_across_one_area() -> None:
    """Numbering scenarios per requirement while stemming ids from the area collided.

    Two requirements in one area both produced `TS-<AREA>-01`, and with it a duplicate case id,
    which makes every traceability link into those cases ambiguous.
    """
    qualified = _qualified("rich")
    signals = build_signals(qualified)

    by_area: dict[str, list] = {}
    for signal in signals.generating():
        by_area.setdefault(signal.area, []).append(signal)
    crowded = max(by_area.values(), key=len)
    assert len(crowded) > 1, "the fixture must have two requirements in one area"

    fragments = [
        {
            "scenarios": [
                {
                    "requirement_id": signal.requirement_id,
                    "title": "S",
                    "cases": [
                        {
                            "requirement_id": signal.requirement_id,
                            "dimension": signal.dimensions[0],
                            "title": "t",
                            "objective": "o",
                            "steps": ["s"],
                            "expected_results": ["e"],
                        }
                    ],
                }
            ],
            "notes": [],
        }
        for signal in crowded
    ]
    suite = assemble(qualified, signals, fragments)

    scenario_ids = [scenario.id for scenario in suite.scenarios]
    case_ids = [case.id for case in suite.cases()]
    assert len(set(scenario_ids)) == len(scenario_ids)
    assert len(set(case_ids)) == len(case_ids)


def test_folded_duplicates_accumulate_across_repair_passes() -> None:
    """Deduplication runs again after every repair, so assigning the count loses earlier passes.

    A run that folded nine duplicates before repair and none after published
    `folded_duplicates: 0`, which is a false statement about what the run did.
    """
    suite = _suite(_case(id="TC-1"), _case(id="TC-2"))
    suite, duplicates, _ = deduplicate(suite)
    suite.counts["folded_duplicates"] = suite.counts.get("folded_duplicates", 0) + len(duplicates)
    assert suite.counts["folded_duplicates"] == 1

    # Second pass finds nothing; the earlier tally must survive.
    suite.scenarios[0].cases.append(_case(id="TC-3", steps=["Something entirely different."]))
    from agentic.capabilities.testgen.dedupe import deduplicate as run_again

    suite, again, _ = run_again(suite)
    assert again == []
    assert suite.counts["folded_duplicates"] == 1


@pytest.mark.parametrize(
    "text",
    [
        "Set the environment variable PYTHONUNBUFFERED to 1.",
        "Confirm JAVA_HOME is read at start-up.",
        "The javadoc-style comment is preserved.",
        "The jester role is assigned.",
    ],
)
def test_a_config_key_containing_a_language_name_is_not_a_framework_leak(text: str) -> None:
    """Substring matching flagged `PYTHONUNBUFFERED` as the language "python".

    The repair pass could not remove it, because the variable *was* the thing under test — so the
    loop could not converge, four model calls were spent, and a valid case was pruned at the end.
    A rejection no rewording can satisfy is worse than no check.
    """
    assert framework_leaks(text) == []


def test_a_real_language_mention_is_still_caught() -> None:
    assert "python" in framework_leaks("Run the Python script to verify.")
    assert "java" in framework_leaks("The Java client submits the request.")

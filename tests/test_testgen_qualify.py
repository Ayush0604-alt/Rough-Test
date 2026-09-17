"""Deciding what may be tested.

Two things are being defended. First, that each band is reachable and reached for the stated
reason — a band no input can reach is a rule that does not exist. Second, that the ordering is
stable, because first-match-wins rules that are evaluated in a different order give a different
answer to the same input.
"""

from __future__ import annotations

import json
from difflib import SequenceMatcher
from pathlib import Path

import pytest

from agentic.capabilities.testgen.ingest import read_requirement, read_requirement_set
from agentic.capabilities.testgen.qualify import (
    DUPLICATE_SIMILARITY,
    _duplicate_of,
    _normalise,
    qualify_one,
)
from agentic.capabilities.testgen.signals import activated_dimensions, beyond_positive
from agentic.schemas.testgen import Band, IngestedRequirement

FIXTURES = Path(__file__).parent / "fixtures" / "requirement_sets"


def _requirement(**raw) -> IngestedRequirement:
    raw.setdefault("id", "FR-1")
    raw.setdefault("statement", "A user shall be able to do a thing, so that an outcome follows.")
    requirement, _ = read_requirement(raw, 0)
    assert requirement is not None
    return requirement


def _band(requirement: IngestedRequirement, **kwargs):
    return qualify_one(
        requirement,
        rejected_titles=kwargs.get("rejected_titles", set()),
        conflicted_ids=kwargs.get("conflicted_ids", set()),
        already=kwargs.get("already", []),
    )


# ---------------------------------------------------------------------------------------------
# Excluded
# ---------------------------------------------------------------------------------------------


def test_a_rejected_requirement_yields_no_test() -> None:
    record = _band(_requirement(status="rejected"))
    assert record.band is Band.EXCLUDED
    assert record.reason_codes == ["REQ_REJECTED"]
    assert not record.testable


def test_an_implementation_observation_yields_no_test() -> None:
    record = _band(_requirement(type="observation"))
    assert record.band is Band.EXCLUDED
    assert record.reason_codes == ["OBSERVATION_NOT_TESTABLE"]


def test_a_blank_statement_is_excluded_by_id_rather_than_vanishing() -> None:
    record = _band(_requirement(id="FR-BLANK", statement="  "))
    assert record.band is Band.EXCLUDED
    assert record.reason_codes == ["STATEMENT_UNUSABLE"]
    assert record.requirement_id == "FR-BLANK", "still reportable"


def test_a_business_outcome_is_covered_through_its_capabilities_not_directly() -> None:
    """Testing an outcome directly is how you end up with "verify the feature works"."""
    record = _band(_requirement(type="business", statement="Users shall manage batch operations."))
    assert record.band is Band.EXCLUDED
    assert record.reason_codes == ["AGGREGATE_TIER"]


def test_a_title_matching_a_rejected_candidate_is_excluded() -> None:
    record = _band(
        _requirement(title="Change user passwords securely"),
        rejected_titles={_normalise("Change user passwords securely")},
    )
    assert record.band is Band.EXCLUDED
    assert record.reason_codes == ["REQ_UNRESOLVED"]


def test_a_restated_requirement_is_excluded_and_names_what_it_duplicates() -> None:
    first = _requirement(id="FR-1", statement="An operator shall remove an item by identifier.")
    second = _requirement(id="FR-2", statement="An operator shall remove an item by identifier.")
    record = _band(second, already=[first])
    assert record.band is Band.EXCLUDED
    assert record.duplicate_of == "FR-1"


# ---------------------------------------------------------------------------------------------
# Needs clarification — all three conditions, because any one alone is still derivable
# ---------------------------------------------------------------------------------------------


BLOCKER = (
    "What observable outcome would show this requirement has been met? Nothing in the cited "
    "evidence states one."
)


def test_a_requirement_with_nothing_to_check_it_against_becomes_a_question() -> None:
    requirement = _requirement(
        statement="The system shall process the batch.",
        open_questions=[BLOCKER],
        acceptance_criteria=[],
    )
    assert beyond_positive(requirement) == [], "precondition: the statement says very little"
    record = _band(requirement)
    assert record.band is Band.NEEDS_CLARIFICATION
    assert record.reason_codes == ["NO_OBSERVABLE_OUTCOME"]
    assert record.blockers == [BLOCKER]
    assert not record.testable


def test_a_blocker_alone_does_not_block_when_the_statement_activates_dimensions() -> None:
    """The whole point of not discarding a requirement merely for lacking criteria."""
    requirement = _requirement(
        statement="When an identifier is invalid, the system shall refuse the request.",
        open_questions=[BLOCKER],
        acceptance_criteria=[],
    )
    assert beyond_positive(requirement)
    record = _band(requirement)
    assert record.band is Band.ELIGIBLE_WITH_ASSUMPTIONS


def test_a_blocker_alone_does_not_block_when_a_usable_criterion_survives() -> None:
    requirement = _requirement(
        statement="The system shall process the batch.",
        open_questions=[BLOCKER],
        acceptance_criteria=["Every queued item is marked complete."],
    )
    record = _band(requirement)
    assert record.band is not Band.NEEDS_CLARIFICATION


# ---------------------------------------------------------------------------------------------
# Eligible with assumptions
# ---------------------------------------------------------------------------------------------


def test_internal_behaviour_is_testable_only_under_a_stated_assumption() -> None:
    record = _band(_requirement(type="system", acceptance_criteria=["A value is retained."]))
    assert record.band is Band.ELIGIBLE_WITH_ASSUMPTIONS
    assert record.reason_codes == ["INTERNAL_BEHAVIOUR"]
    assert record.testable


def test_criteria_that_were_all_generated_count_as_no_criteria() -> None:
    record = _band(
        _requirement(
            acceptance_criteria=[
                "Exercising the published interface produces the outcome this requirement describes."
            ]
        )
    )
    assert record.band is Band.ELIGIBLE_WITH_ASSUMPTIONS
    assert record.reason_codes == ["DERIVED_CRITERIA_ONLY"]
    assert record.usable_criteria == []
    assert len(record.discarded_criteria) == 1


def test_no_criteria_at_all_is_still_testable_from_the_statement() -> None:
    record = _band(
        _requirement(
            statement="When an identifier is invalid, the system shall refuse the request.",
            acceptance_criteria=[],
        )
    )
    assert record.band is Band.ELIGIBLE_WITH_ASSUMPTIONS
    assert record.reason_codes == ["NO_ACCEPTANCE_CRITERIA"]


def test_low_confidence_is_flagged_without_costing_the_test() -> None:
    record = _band(_requirement(confidence=0.3, acceptance_criteria=["A thing is true."]))
    assert record.band is Band.ELIGIBLE_WITH_ASSUMPTIONS
    assert record.reason_codes == ["LOW_CONFIDENCE"]


def test_an_unsettled_conflict_is_flagged_from_the_optional_findings_artifact() -> None:
    record = _band(
        _requirement(id="FR-9", acceptance_criteria=["A thing is true."]),
        conflicted_ids={"FR-9"},
    )
    assert record.band is Band.ELIGIBLE_WITH_ASSUMPTIONS
    assert record.reason_codes == ["UNRESOLVED_CONFLICT"]


# ---------------------------------------------------------------------------------------------
# Eligible
# ---------------------------------------------------------------------------------------------


def test_a_criteria_bearing_requirement_is_directly_testable() -> None:
    record = _band(
        _requirement(
            statement="An operator shall remove an item by identifier, so that it is unavailable.",
            acceptance_criteria=["The item is removed when a valid identifier is supplied."],
            confidence=0.95,
        )
    )
    assert record.band is Band.ELIGIBLE
    assert record.reason_codes == ["DIRECTLY_TESTABLE"]
    assert record.usable_criteria


# ---------------------------------------------------------------------------------------------
# Ordering, and the optimisation
# ---------------------------------------------------------------------------------------------


def test_rejection_beats_every_other_signal() -> None:
    """A rejected requirement is excluded even when it looks perfectly testable otherwise."""
    record = _band(
        _requirement(
            status="rejected", type="system", confidence=0.2, acceptance_criteria=["A thing."]
        )
    )
    assert record.reason_codes == ["REQ_REJECTED"]


def test_the_duplicate_prefilter_agrees_with_the_naive_comparison() -> None:
    """The fast path uses documented upper bounds, so it must be exact, not approximate.

    Checked over every pair in a real artifact rather than a handful of examples: a prefilter that
    is wrong on one pair in two hundred would silently drop a real requirement.
    """
    items = read_requirement_set(
        json.loads((FIXTURES / "rich.json").read_text(encoding="utf-8"))
    ).requirements

    def naive(statement: str, candidates: list[IngestedRequirement]):
        for candidate in candidates:
            ratio = SequenceMatcher(
                None, _normalise(statement), _normalise(candidate.statement)
            ).ratio()
            if ratio >= DUPLICATE_SIMILARITY:
                return candidate
        return None

    kept: list[IngestedRequirement] = []
    for item in items:
        fast = _duplicate_of(item.statement, kept)
        slow = naive(item.statement, kept)
        assert (fast.id if fast else None) == (slow.id if slow else None), item.id
        if fast is None:
            kept.append(item)
    assert len(kept) < len(items), "the fixture does contain duplicates, so this proved something"


# ---------------------------------------------------------------------------------------------
# Signal activation
# ---------------------------------------------------------------------------------------------


def test_every_requirement_with_a_statement_gets_the_happy_path() -> None:
    assert activated_dimensions(_requirement()) [:1] == ["positive"]


def test_a_statement_with_no_text_activates_nothing() -> None:
    assert activated_dimensions(_requirement(statement="")) == []


@pytest.mark.parametrize(
    "statement,dimension",
    [
        ("The system shall refuse the request.", "negative"),
        ("The identifier must be a valid reference.", "validation"),
        ("An error message is returned on failure.", "error_handling"),
        ("At most fifty items are returned.", "boundary"),
        ("The item status becomes archived.", "state_transition"),
        ("Only an authorised operator may proceed.", "authorization"),
        ("Each entry is recorded in the audit history.", "data_integrity"),
        ("The external provider supplies the value.", "integration"),
        ("When the dependency is unavailable, a retry occurs.", "exceptional"),
    ],
)
def test_each_dimension_is_activated_by_something_a_requirement_could_say(
    statement: str, dimension: str
) -> None:
    assert dimension in activated_dimensions(_requirement(statement=statement))


def test_a_rule_shaped_statement_activates_the_rule_dimension() -> None:
    assert "business_rule" in activated_dimensions(
        _requirement(statement="When a file type is unsupported, the system shall refuse it.")
    )


def test_a_mid_sentence_conditional_is_a_qualifier_not_a_rule() -> None:
    """"filter results where the status is active" is a capability, not a policy."""
    assert "business_rule" not in activated_dimensions(
        _requirement(statement="A user shall filter results where the status is active.")
    )


def test_activation_stays_bounded_on_a_real_artifact() -> None:
    """The anti-explosion claim, measured: most requirements activate two to four dimensions."""
    items = read_requirement_set(
        json.loads((FIXTURES / "rich.json").read_text(encoding="utf-8"))
    ).requirements
    counts = [len(activated_dimensions(item)) for item in items]
    average = sum(counts) / len(counts)
    assert 2.0 <= average <= 4.5, f"mean activation was {average:.2f}"
    assert max(counts) <= 8, "no requirement should activate nearly every dimension"

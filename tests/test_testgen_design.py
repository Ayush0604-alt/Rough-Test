"""Signal analysis, batching, and assembly.

The two claims under test are the ones the whole pack rests on. First, that coverage is *earned* —
a requirement gets cases for what it actually says, not a sweep of every category. Second, that the
designer works inside decisions it did not make, so a model having an expansive day cannot turn a
190-item requirement set into a thousand-case suite.

The model is stubbed throughout: what is being tested is the machinery around it.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agentic.capabilities.testgen.analyse import (
    assess_risk,
    budget_for,
    build_signals,
    interface_hint,
    route_for,
)
from agentic.capabilities.testgen.assemble import assemble, automation_readiness
from agentic.capabilities.testgen.batching import (
    MAX_CASES_PER_BATCH,
    MAX_REQUIREMENTS_PER_BATCH,
    build_batches,
)
from agentic.capabilities.testgen.ingest import read_requirement, read_requirement_set
from agentic.capabilities.testgen.qualify import qualify_one
from agentic.schemas.testgen import IngestedRequirement, QualifiedSet
from agentic.schemas.testspec import InterfaceHint, Route, TestDesignFragment

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


def _requirement(**raw) -> IngestedRequirement:
    raw.setdefault("id", "FR-1")
    raw.setdefault("statement", "A user shall be able to do a thing, so that an outcome follows.")
    requirement, _ = read_requirement(raw, 0)
    return requirement


# ---------------------------------------------------------------------------------------------
# Routing internal behaviour — the rule carrying the most weight
# ---------------------------------------------------------------------------------------------


def test_internal_behaviour_with_a_stated_outcome_is_designed_against_it() -> None:
    """Route (a): something stated an observable outcome, so there is a real check here."""
    route, attach, _ = route_for(
        _requirement(type="system"), ["A value survives a restart."], known_ids=set()
    )
    assert route is Route.GENERATE
    assert attach is None


def test_internal_behaviour_with_a_parent_attaches_rather_than_standing_alone() -> None:
    """Route (b). Rare in practice — 1 of 92 on the measured set — but cheap to support."""
    route, attach, reason = route_for(
        _requirement(type="system", parent_id="FR-PARENT"), [], known_ids={"FR-PARENT"}
    )
    assert route is Route.ATTACH
    assert attach == "FR-PARENT"
    assert "FR-PARENT" in reason


def test_internal_behaviour_with_neither_is_an_honest_gap() -> None:
    """Route (c). The alternative is a case no actor could run, which is worse than the gap."""
    route, attach, reason = route_for(_requirement(type="system"), [], known_ids=set())
    assert route is Route.DEFERRED
    assert attach is None
    assert reason, "a deferral must say why"


def test_a_parent_that_does_not_resolve_defers_rather_than_attaching_to_nothing() -> None:
    route, _, _ = route_for(
        _requirement(type="system", parent_id="FR-GONE"), [], known_ids={"FR-OTHER"}
    )
    assert route is Route.DEFERRED


def test_an_observable_capability_always_generates() -> None:
    for tier in ("functional", "business_rule", "non_functional"):
        route, _, _ = route_for(_requirement(type=tier), [], known_ids=set())
        assert route is Route.GENERATE, tier


# ---------------------------------------------------------------------------------------------
# Risk and budget
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize("dimension", ["business_rule", "authorization", "data_integrity"])
def test_asserting_a_rule_or_an_invariant_is_high_risk(dimension: str) -> None:
    risk, reason = assess_risk(_requirement(), ["positive", dimension])
    assert risk == "high"
    assert reason


def test_risk_is_not_read_from_confidence() -> None:
    """Confidence is near-uniform in practice, so using it would flatten every requirement."""
    low = assess_risk(_requirement(confidence=0.1), ["positive", "business_rule"])
    high = assess_risk(_requirement(confidence=0.99), ["positive", "business_rule"])
    assert low == high


def test_the_budget_is_a_ceiling_not_a_target() -> None:
    """Two activated dimensions get two cases even where the ceiling allows eight."""
    assert budget_for(["positive", "business_rule"], "high", internal=False) == 2


def test_the_budget_caps_a_broad_requirement() -> None:
    many = ["positive", "negative", "validation", "error_handling", "boundary", "authorization"]
    assert budget_for(many, "medium", internal=False) == 5


def test_internal_behaviour_is_capped_hardest() -> None:
    many = ["positive", "negative", "validation", "error_handling", "boundary"]
    assert budget_for(many, "high", internal=True) == 2


# ---------------------------------------------------------------------------------------------
# Interface hints are shape-based, never framework-based
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path,expected",
    [
        ("DELETE /items/{id}", InterfaceHint.HTTP_ENDPOINT),
        ("GET /backup/list", InterfaceHint.HTTP_ENDPOINT),
        ("app.tool.main!cli", InterfaceHint.COMMAND_LINE),
        ('"Upload a document" action', InterfaceHint.UI_ACTION),
        ("nightly.scheduled_job", InterfaceHint.SCHEDULED),
        ("app.internal.helper", InterfaceHint.INTERNAL),
    ],
)
def test_the_boundary_shape_decides_the_hint(path: str, expected: InterfaceHint) -> None:
    assert interface_hint(_requirement(graph_path=[path])) == expected.value


def test_no_traced_path_means_unknown_rather_than_a_guess() -> None:
    assert interface_hint(_requirement(graph_path=[])) == InterfaceHint.UNKNOWN.value


# ---------------------------------------------------------------------------------------------
# The whole analysis, on a real artifact
# ---------------------------------------------------------------------------------------------


def test_routing_holds_the_projection_far_below_the_naive_count() -> None:
    """The measured claim: 485 cases without the routing rule, 280 with it."""
    signals = build_signals(_qualified("rich"))
    projected = sum(signal.case_budget for signal in signals.signals)

    naive = 0
    for signal in signals.signals:
        if signal.band.startswith("eligible"):
            naive += max(1, len(signal.dimensions) or 3)

    assert 200 <= projected <= 320, f"projected {projected}"
    assert projected < naive, "routing must actually reduce the work"

    deferred = [s for s in signals.signals if s.route == Route.DEFERRED.value]
    assert all(signal.deferral_reason for signal in deferred), "every gap says why"


def test_every_generating_requirement_gets_a_budget_and_a_reason() -> None:
    signals = build_signals(_qualified("rich"))
    for signal in signals.generating():
        assert signal.case_budget >= 1
        assert signal.dimensions
        assert signal.risk_reason


# ---------------------------------------------------------------------------------------------
# Batching
# ---------------------------------------------------------------------------------------------


def test_batches_respect_both_bounds() -> None:
    qualified = _qualified("rich")
    batches = build_batches(qualified, build_signals(qualified))
    assert batches
    for batch in batches:
        assert len(batch["packet"]["requirements"]) <= MAX_REQUIREMENTS_PER_BATCH
        # A single requirement whose budget exceeds the cap still gets its own batch rather than
        # being dropped, so the bound is on *added* requirements, not on a lone one.
        if len(batch["packet"]["requirements"]) > 1:
            assert batch["total_budget"] <= MAX_CASES_PER_BATCH


def test_a_batch_holds_one_area_so_its_cases_can_share_context() -> None:
    qualified = _qualified("rich")
    for batch in build_batches(qualified, build_signals(qualified)):
        areas = {
            next(r.area for r in qualified.requirements if r.id == item["requirement_id"])
            for item in batch["packet"]["requirements"]
        }
        assert len(areas) == 1


def test_the_packet_withholds_generated_criteria_from_the_designer() -> None:
    """Showing a criterion that asserts nothing invites a case that checks nothing."""
    qualified = _qualified("rich")
    boilerplate = {
        c
        for requirement in qualified.requirements
        for c in requirement.discarded_criteria()
    }
    assert boilerplate, "the fixture does carry generated criteria, so this proves something"

    shown = {
        criterion
        for batch in build_batches(qualified, build_signals(qualified))
        for item in batch["packet"]["requirements"]
        for criterion in item["acceptance_criteria"]
    }
    assert not (shown & boilerplate)


def test_batching_is_reproducible() -> None:
    qualified = _qualified("rich")
    first = build_batches(qualified, build_signals(qualified))
    second = build_batches(qualified, build_signals(qualified))
    assert first == second


# ---------------------------------------------------------------------------------------------
# Assembly
# ---------------------------------------------------------------------------------------------


def _fragment(requirement_id: str, dimension: str = "positive", **case) -> dict:
    case.setdefault("title", "A case")
    case.setdefault("objective", "Confirm the behaviour holds when it should.")
    case.setdefault("steps", ["Do the thing."])
    case.setdefault("expected_results", ["The outcome is observable."])
    return {
        "scenarios": [
            {
                "requirement_id": requirement_id,
                "title": "A situation",
                "context": "The world is in a known state.",
                "preconditions": [],
                "cases": [{"requirement_id": requirement_id, "dimension": dimension, **case}],
            }
        ],
        "notes": [],
    }


def test_assembly_stamps_traceable_identifiers() -> None:
    qualified = _qualified("thin")
    signals = build_signals(qualified)
    target = signals.generating()[0]

    suite = assemble(qualified, signals, [_fragment(target.requirement_id, target.dimensions[0])])
    assert len(suite.cases()) == 1
    case = suite.cases()[0]
    assert case.id.startswith("TC-")
    assert case.scenario_id.startswith("TS-")
    assert case.requirement_ids == [target.requirement_id]
    assert case.covers, "every case must say what it checks"


def test_a_case_claiming_an_unactivated_dimension_is_dropped_with_a_note() -> None:
    """The dimension set was decided from the requirement; the designer may not add to it."""
    qualified = _qualified("thin")
    signals = build_signals(qualified)
    target = signals.generating()[0]
    unactivated = next(d for d in ("boundary", "exceptional") if d not in target.dimensions)

    suite = assemble(qualified, signals, [_fragment(target.requirement_id, unactivated)])
    assert suite.cases() == []
    assert any(unactivated in note for note in suite.notes)


def test_a_case_for_an_unknown_requirement_is_dropped_rather_than_published() -> None:
    qualified = _qualified("thin")
    suite = assemble(qualified, build_signals(qualified), [_fragment("FR-DOES-NOT-EXIST")])
    assert suite.cases() == []
    assert any("FR-DOES-NOT-EXIST" in note for note in suite.notes)


def test_a_case_for_a_deferred_requirement_is_dropped() -> None:
    """The routing decision is not the designer's to overturn."""
    qualified = _qualified("rich")
    signals = build_signals(qualified)
    deferred = next(s for s in signals.signals if s.route == Route.DEFERRED.value)

    suite = assemble(qualified, signals, [_fragment(deferred.requirement_id)])
    assert suite.cases() == []
    assert any(deferred.requirement_id in note for note in suite.notes)


def test_a_requirement_planned_for_and_never_designed_is_recorded_as_uncovered() -> None:
    """A worker that says nothing about a requirement is otherwise invisible."""
    qualified = _qualified("thin")
    signals = build_signals(qualified)
    suite = assemble(qualified, signals, [])

    undesigned = [entry for entry in suite.deferred if entry.route == "undesigned"]
    assert len(undesigned) == len(signals.generating())
    assert all(entry.reason for entry in undesigned)


def test_a_criterion_index_resolves_to_the_criterion_text() -> None:
    qualified = _qualified("rich")
    signals = build_signals(qualified)
    target = next(s for s in signals.generating() if s.usable_criteria)

    suite = assemble(
        qualified,
        signals,
        [_fragment(target.requirement_id, target.dimensions[0], covers_criterion=0)],
    )
    link = suite.cases()[0].covers[0]
    assert link.kind == "acceptance_criterion"
    assert link.text == target.usable_criteria[0]


def test_an_out_of_range_criterion_index_falls_back_to_the_statement() -> None:
    qualified = _qualified("thin")
    signals = build_signals(qualified)
    target = signals.generating()[0]

    suite = assemble(
        qualified,
        signals,
        [_fragment(target.requirement_id, target.dimensions[0], covers_criterion=99)],
    )
    assert suite.cases()[0].covers[0].kind == "statement"


def test_derived_metadata_is_computed_not_authored() -> None:
    qualified = _qualified("rich")
    signals = build_signals(qualified)
    target = next(s for s in signals.generating() if "business_rule" in s.dimensions)

    suite = assemble(qualified, signals, [_fragment(target.requirement_id, "business_rule")])
    case = suite.cases()[0]
    assert case.regression_candidate, "a rule breaking is a regression a feature test would miss"
    assert case.interface_hint == target.interface_hint
    assert case.automation_readiness


def test_automation_readiness_reflects_what_the_case_needs() -> None:
    from agentic.schemas.testspec import TestCase, TestInput

    environment = TestCase(dimension="integration", interface_hint="http_endpoint")
    assert automation_readiness(environment) == "needs_environment"

    data = TestCase(
        dimension="validation",
        interface_hint="http_endpoint",
        inputs=[TestInput(name="id", value_kind="malformed")],
    )
    assert automation_readiness(data) == "needs_data"

    plain = TestCase(dimension="positive", interface_hint="http_endpoint")
    assert automation_readiness(plain) == "ready"


# ---------------------------------------------------------------------------------------------
# The specification layer names no technology
# ---------------------------------------------------------------------------------------------


FORBIDDEN = (
    "playwright", "pytest", "promptfoo", "deepeval", "selenium", "cypress", "junit", "jest",
    "requests.", "httpx", "curl", "python", "javascript", "assert(",
)


def test_no_framework_name_appears_anywhere_in_the_specification_model() -> None:
    """The independence claim, checked against the model rather than asserted in prose."""
    import agentic.schemas.testspec as testspec

    source = Path(testspec.__file__).read_text(encoding="utf-8").lower()
    for name in FORBIDDEN:
        assert name not in source, f"{name!r} reached the technology-independent model"


def test_the_design_schema_asks_for_no_technology_specific_field() -> None:
    fields = set(TestDesignFragment.model_json_schema()["$defs"]["TestCaseDraft"]["properties"])
    assert not fields & {"http_method", "url", "endpoint", "selector", "command", "framework"}

"""Saying what the document does not know, and never emitting a link that points at nothing.

`assumptions` and `open_questions` were empty on 60 of 60 items in a measured run, and 26 of 60
requirements had nothing to check them against. Both fields existed the whole time; nothing was
responsible for filling them.

The temptation here is filler. A sentence in `open_questions` that raises no question, or an
acceptance criterion that restates the requirement, makes the numbers look right and leaves the
reader worse off than an empty field, because an empty field at least reads as empty. So the tests
below check both directions: that uncertainty is recorded where it exists, and that nothing is
written where the evidence supports nothing.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from agentic.capabilities.confidence import recalibrate_confidence
from agentic.capabilities.correspondence import validate_correspondence
from agentic.capabilities.hedging import DERIVED_NOTE, hedge_requirements
from agentic.capabilities.render import build_requirement_set
from agentic.config import Settings
from agentic.schemas.brd import BRDItem, CorrespondenceOutcome, ProductBRD
from agentic.schemas.core import Entity, KnowledgeGraph, Provenance, Relationship, SourceLocation
from agentic.store import ArtifactStore

from tests.test_correspondence import FIXTURES, TRUTH, _all_items, _graph_for, _item


def _at(line=10, end=20, file="svc.py", symbol=None):
    return SourceLocation(file=file, line_start=line, line_end=end, symbol=symbol)


def _entity(kind="FUNCTION", provenance=Provenance.EXTRACTED, name="handler", **attributes):
    return Entity(
        id=f"e:{kind}:{name}",
        kind=kind,
        name=name,
        qualified_name=name,
        location=_at(),
        provenance=provenance,
        attributes=attributes,
    )


def _claim(**kwargs) -> BRDItem:
    return BRDItem(
        title=kwargs.pop("title", "A Claim"),
        statement=kwargs.pop("statement", "A person shall be able to act on a record."),
        confidence=0.9,
        evidence=kwargs.pop("evidence", [_at()]),
        **kwargs,
    )


def _hedge(brd: ProductBRD, entities=(), relationships=(), **flags):
    run = Path(tempfile.mkdtemp())
    (run / "artifacts").mkdir(parents=True)
    store = ArtifactStore(run)
    graph = KnowledgeGraph(
        repository_name="t", entities=list(entities), relationships=list(relationships)
    )
    paths = {"brd": store.save("brd", brd), "graph": store.save("graph", graph)}

    class _Ctx:
        settings = Settings(**flags)
        state = {"artifacts": paths}

        def load(self, name, model=None):
            return store.load_model(paths[name], model)

    result = hedge_requirements(_Ctx())
    return result.artifacts["brd"], result.metrics


# --------------------------------------------------------------------------------------------
# Recording what is uncertain
# --------------------------------------------------------------------------------------------


def test_a_claim_resting_only_on_prose_says_so() -> None:
    """Prose states an intention; the code is what runs, and the two can drift apart."""
    brd, _ = _hedge(
        ProductBRD(functional_requirements=[_claim()]),
        entities=[_entity(kind="DOMAIN_POLICY", provenance=Provenance.INFERRED, statement="x")],
    )
    assert any(
        "written beside the code" in note
        for note in brd.functional_requirements[0].assumptions
    )


def test_a_claim_resting_on_parsed_code_makes_no_such_excuse() -> None:
    brd, _ = _hedge(
        ProductBRD(functional_requirements=[_claim()]),
        entities=[_entity(kind="ENDPOINT")],
    )
    assert not any(
        "written beside the code" in note
        for note in brd.functional_requirements[0].assumptions
    )


def test_a_rejected_claim_becomes_a_question_rather_than_a_fact() -> None:
    item = _claim(
        validation=CorrespondenceOutcome(
            verdict="rejected", reason="The cited entry point is declared read-only."
        )
    )
    brd, _ = _hedge(
        ProductBRD(functional_requirements=[item]), entities=[_entity(kind="ENDPOINT")]
    )
    questions = brd.functional_requirements[0].open_questions
    assert any("does not appear to support" in question for question in questions)
    assert any("read-only" in question for question in questions)


def test_a_parent_that_never_resolved_becomes_a_question() -> None:
    """The link was asserted and found to point at nothing, which is a gap, not an absence."""
    brd, _ = _hedge(
        ProductBRD(
            functional_requirements=[_claim(parent="Some Missing Outcome", parent_relation="refines")]
        ),
        entities=[_entity(kind="ENDPOINT")],
    )
    assert any(
        "Some Missing Outcome" in question
        for question in brd.functional_requirements[0].open_questions
    )


def test_a_resolved_parent_raises_no_question() -> None:
    brd, _ = _hedge(
        ProductBRD(
            functional_requirements=[
                _claim(parent="An Outcome", parent_relation="refines", parent_id="BR-001")
            ]
        ),
        entities=[_entity(kind="ENDPOINT")],
    )
    assert brd.functional_requirements[0].open_questions == []


# --------------------------------------------------------------------------------------------
# Acceptance criteria: derived where possible, questioned where not
# --------------------------------------------------------------------------------------------


def test_a_criterion_is_derived_from_a_cited_entry_point() -> None:
    brd, metrics = _hedge(
        ProductBRD(functional_requirements=[_claim()]),
        entities=[_entity(kind="ENDPOINT", name="GET /orders")],
    )
    item = brd.functional_requirements[0]
    # The boundary is described, never named. A criterion is read as closely as the statement it
    # hangs from and is held to the same language bar, and it is written after the pass that
    # strips implementation detail -- so a symbol written into one here would reach the document.
    assert item.acceptance_criteria == [
        "Exercising the published interface produces the outcome this requirement describes."
    ]
    assert "GET /orders" not in item.acceptance_criteria[0]
    assert metrics["criteria_derived"] == 1


def test_a_derived_criterion_says_that_it_was_derived() -> None:
    """A reviewer must be able to tell a criterion read off the code from one that was intended."""
    brd, _ = _hedge(
        ProductBRD(functional_requirements=[_claim()]),
        entities=[_entity(kind="ENDPOINT", name="GET /orders")],
    )
    assert DERIVED_NOTE in brd.functional_requirements[0].assumptions


def test_a_criterion_is_derived_from_the_entry_point_that_reaches_the_cited_code() -> None:
    """The command-line shape: the capability is in the handler, the entry point is above it.

    Requiring the *cited* entity to be a boundary derived a criterion for nothing at all on that
    fixture, because a requirement cites the code that does the work, not the parser above it.
    """
    boundary = _entity(kind="CLI_ENTRY", name="tool!cli")
    worker = _entity(kind="FUNCTION", name="cmd_report")
    worker.location = _at(50, 60)
    brd, _ = _hedge(
        ProductBRD(functional_requirements=[_claim(evidence=[_at(50, 60)])]),
        entities=[boundary, worker],
        relationships=[
            Relationship(id="r1", source=boundary.id, target=worker.id, kind="CALLS")
        ],
    )
    criteria = brd.functional_requirements[0].acceptance_criteria
    assert criteria and "reaches this behaviour" in criteria[0]
    assert "tool!cli" not in criteria[0]
    assert "command-line entry point" in criteria[0]


def test_a_criterion_is_derived_from_a_declared_condition() -> None:
    brd, _ = _hedge(
        ProductBRD(business_rules=[_claim()]),
        entities=[
            _entity(kind="DOMAIN_POLICY", name="chk_state", expression="state in ('open','closed')")
        ],
    )
    criteria = brd.business_rules[0].acceptance_criteria
    assert criteria == ["The declared condition still holds: state in ('open','closed')"]


def test_nothing_observable_yields_a_question_rather_than_filler() -> None:
    """An empty field reads as empty; a criterion that checks nothing reads as an answer."""
    brd, metrics = _hedge(
        ProductBRD(functional_requirements=[_claim()]), entities=[_entity(kind="FUNCTION")]
    )
    item = brd.functional_requirements[0]
    assert item.acceptance_criteria == []
    assert any("observable outcome" in question for question in item.open_questions)
    assert metrics["requirements_without_criteria"] == 1


def test_an_existing_criterion_is_never_replaced() -> None:
    brd, _ = _hedge(
        ProductBRD(
            functional_requirements=[_claim(acceptance_criteria=["The record appears in the list."])]
        ),
        entities=[_entity(kind="ENDPOINT", name="GET /orders")],
    )
    assert brd.functional_requirements[0].acceptance_criteria == [
        "The record appears in the list."
    ]


def test_an_observation_is_not_asked_for_a_criterion() -> None:
    """An observation records what the code does; there is nothing for anyone to sign off."""
    brd, metrics = _hedge(
        ProductBRD(observations=[_claim()]), entities=[_entity(kind="FUNCTION")]
    )
    assert brd.observations[0].open_questions == []
    assert metrics["requirements_without_criteria"] == 0


# --------------------------------------------------------------------------------------------
# Measured on the corpus, per fixture
# --------------------------------------------------------------------------------------------


def _pipeline(fixture: str, key: str):
    """Validator, then recalibration, then hedging -- the order they run in."""
    _, store, state = _graph_for(fixture)
    brd = ProductBRD(functional_requirements=[_item(spec) for spec in TRUTH[fixture][key]])
    paths = {"brd": store.save("brd_h", brd), "graph": state["artifacts"]["graph"]}

    class _Ctx:
        settings = Settings(correspondence_entailment=False)
        state = {"artifacts": paths}

        def load(self, name, model=None):
            return store.load_model(paths[name], model)

    ctx = _Ctx()
    paths["brd"] = store.save("h1", validate_correspondence(ctx).artifacts["brd"])
    paths["brd"] = store.save("h2", recalibrate_confidence(ctx).artifacts["brd"])
    result = hedge_requirements(ctx)
    return _all_items(result.artifacts["brd"]), result.metrics


@pytest.mark.parametrize("fixture", FIXTURES)
def test_every_true_requirement_ends_up_checkable(fixture) -> None:
    """Per fixture, so a fix that only works on the HTTP shape shows up as three flat rows."""
    items, _ = _pipeline(fixture, "supported")
    uncheckable = [item.title for item in items if not item.acceptance_criteria]
    assert uncheckable == [], f"{fixture}: nothing to check {uncheckable} against"


@pytest.mark.parametrize("fixture", FIXTURES)
def test_every_contradicted_claim_carries_a_question(fixture) -> None:
    items, _ = _pipeline(fixture, "contradicted")
    assert all(item.open_questions for item in items)


# --------------------------------------------------------------------------------------------
# Schema integrity
# --------------------------------------------------------------------------------------------


def test_a_relation_is_never_published_without_a_parent_to_point_at() -> None:
    """A dangling relation tells a consumer there is a hierarchy and gives it nothing to follow."""
    brd = ProductBRD(
        functional_requirements=[
            _claim(parent="Missing Outcome", parent_relation="refines", rationale="r")
        ]
    ).assign_ids()
    published = build_requirement_set(brd, "test")

    assert published[0]["parent_id"] is None
    assert published[0]["parent_relation"] is None


def test_the_published_type_always_agrees_with_the_declared_basis() -> None:
    """`type` is derived at read time rather than stored, so the two cannot drift apart."""
    from agentic.schemas.brd import BASIS_TYPES

    brd = ProductBRD(
        functional_requirements=[
            _claim(abstraction_basis=basis, requirement_type="functional", rationale="r")
            for basis in BASIS_TYPES
        ]
    ).assign_ids()
    for entry, basis in zip(build_requirement_set(brd, "test"), BASIS_TYPES):
        assert entry["abstraction_basis"] == basis
        assert entry["type"] == BASIS_TYPES[basis], (
            f"{basis} was published as {entry['type']}, which contradicts its own basis"
        )


def test_the_pass_can_be_turned_off() -> None:
    assert Settings().hedging_pass is True
    brd, metrics = _hedge(
        ProductBRD(functional_requirements=[_claim()]),
        entities=[_entity(kind="ENDPOINT")],
        hedging_pass=False,
    )
    assert metrics["hedging_disabled"] is True
    assert brd.functional_requirements[0].acceptance_criteria == []

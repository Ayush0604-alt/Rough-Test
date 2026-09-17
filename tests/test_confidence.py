"""Confidence computed from evidence rather than asserted by the author.

The number used to be whatever the model wrote about its own output, unchecked and unchanged: in
one measured run it was exactly 1.0 on 40 of 60 requirements. A figure that reads 1.0 whatever the
evidence carries no information, and the review list built from it — everything under 0.70 — was
therefore empty of precisely the items most worth reviewing.

Two things are graded here. The first is that the number varies with the evidence at all. The
second, and the one easier to get wrong, is that the scale lands in the right place: a validator
whose scores are all sensible relative to each other but all beneath the review threshold puts the
whole document on the review list, which is the same uselessness wearing different clothes.
"""

from __future__ import annotations

import statistics
import tempfile
from pathlib import Path

import pytest

from agentic.capabilities.confidence import (
    CEILING,
    LOW_CONFIDENCE_NOTE,
    recalibrate_confidence,
    score,
)
from agentic.capabilities.correspondence import validate_correspondence
from agentic.config import Settings
from agentic.schemas.brd import BRDItem, CorrespondenceOutcome, ProductBRD
from agentic.schemas.core import Entity, KnowledgeGraph, Provenance, SourceLocation
from agentic.store import ArtifactStore

from tests.test_correspondence import FIXTURES, TRUTH, _all_items, _graph_for, _item


def _entity(kind="FUNCTION", provenance=Provenance.EXTRACTED, **attributes) -> Entity:
    return Entity(
        id=f"e:{kind}:{len(attributes)}",
        kind=kind,
        name="handler",
        qualified_name="app.handler",
        location=SourceLocation(file="svc.py", line_start=10, line_end=20),
        provenance=provenance,
        attributes=attributes,
    )


def _claim(verdict="supported", checked_by="deterministic", **kwargs) -> BRDItem:
    evidence = kwargs.pop(
        "evidence", [SourceLocation(file="svc.py", line_start=10, line_end=20)]
    )
    return BRDItem(
        title="A Claim",
        statement="A person shall be able to act on a record.",
        confidence=kwargs.pop("confidence", 1.0),
        evidence=evidence,
        validation=CorrespondenceOutcome(verdict=verdict, checked_by=checked_by),
        **kwargs,
    )


def _recalibrate(brd: ProductBRD, graph: KnowledgeGraph, **flags):
    run = Path(tempfile.mkdtemp())
    (run / "artifacts").mkdir(parents=True)
    store = ArtifactStore(run)
    paths = {"brd": store.save("brd", brd), "graph": store.save("graph", graph)}

    class _Ctx:
        settings = Settings(**flags)
        state = {"artifacts": paths}

        def load(self, name, model=None):
            return store.load_model(paths[name], model)

    result = recalibrate_confidence(_Ctx())
    return result.artifacts["brd"], result.metrics


# --------------------------------------------------------------------------------------------
# The scale
# --------------------------------------------------------------------------------------------


def test_nothing_is_ever_certain() -> None:
    """No claim here has been read by a person, so no claim may say it is beyond doubt."""
    best = _claim(checked_by="entailment")
    best.evidence = [
        SourceLocation(file="a.py", line_start=10, line_end=20, symbol="handler"),
        SourceLocation(file="b.py", line_start=1, line_end=5, symbol="store"),
        SourceLocation(file="c.py", line_start=1, line_end=5, symbol="save"),
    ]
    value, _ = score(best, [_entity(rule_source="schema_constraint")])
    assert value <= CEILING < 1.0


def test_a_self_reported_certainty_is_replaced_and_kept() -> None:
    brd, metrics = _recalibrate(
        ProductBRD(functional_requirements=[_claim(confidence=1.0)]),
        KnowledgeGraph(repository_name="t", entities=[_entity()], relationships=[]),
    )
    item = brd.functional_requirements[0]
    assert item.confidence < 1.0
    assert item.model_confidence == 1.0
    assert metrics["confidence_previously_certain"] == 1
    assert metrics["confidence_lowered"] == 1


def test_a_rejected_claim_cannot_be_better_than_doubtful() -> None:
    value, reasons = score(_claim(verdict="rejected"), [_entity()])
    assert value <= 0.30
    assert reasons == ["the cited code does not support the statement"]


def test_more_grounding_scores_higher_than_less() -> None:
    """The property the old number lacked entirely: it varies with the evidence."""
    bare = score(_claim(), [])[0]
    parsed = score(_claim(), [_entity()])[0]
    named = score(
        _claim(evidence=[SourceLocation(file="a.py", line_start=1, line_end=5, symbol="h")]),
        [_entity()],
    )[0]
    entailed = score(
        _claim(
            checked_by="entailment",
            evidence=[SourceLocation(file="a.py", line_start=1, line_end=5, symbol="h")],
        ),
        [_entity()],
    )[0]
    assert bare < parsed < named < entailed


def test_a_sprawling_citation_scores_below_a_tight_one() -> None:
    tight = score(_claim(), [_entity()])[0]
    sprawling = score(
        _claim(evidence=[SourceLocation(file="svc.py", line_start=1, line_end=900)]), [_entity()]
    )[0]
    assert sprawling < tight


def test_prose_evidence_is_worth_less_than_parsed_code() -> None:
    """Prose states intent, and intent goes stale; code is what runs."""
    parsed = score(_claim(), [_entity(provenance=Provenance.EXTRACTED)])[0]
    inferred = score(_claim(), [_entity(provenance=Provenance.INFERRED)])[0]
    assert inferred < parsed


def test_an_unverified_claim_lands_on_the_review_list() -> None:
    """Not knowing is not the same as knowing it is wrong, but it is not confidence either."""
    unchecked = score(_claim(verdict="unchecked"), [_entity()])[0]
    supported = score(_claim(), [_entity()])[0]
    assert unchecked < LOW_CONFIDENCE_NOTE <= supported


# --------------------------------------------------------------------------------------------
# Where the scale sits, measured on the corpus
# --------------------------------------------------------------------------------------------


def _measured(fixture: str, key: str) -> list[float]:
    """Run the real validator then the real recalibration over the ground-truth claims."""
    _, store, state = _graph_for(fixture)
    brd = ProductBRD(functional_requirements=[_item(spec) for spec in TRUTH[fixture][key]])
    paths = {"brd": store.save("brd_conf", brd), "graph": state["artifacts"]["graph"]}

    class _Ctx:
        settings = Settings(correspondence_entailment=False)
        state = {"artifacts": paths}

        def load(self, name, model=None):
            return store.load_model(paths[name], model)

    ctx = _Ctx()
    validated = validate_correspondence(ctx)
    paths["brd"] = store.save("brd_validated", validated.artifacts["brd"])
    return [item.confidence for item in _all_items(recalibrate_confidence(ctx).artifacts["brd"])]


@pytest.mark.parametrize("fixture", FIXTURES)
def test_a_well_grounded_claim_clears_the_review_threshold(fixture) -> None:
    """The fixed point the weights are set against.

    A claim that passed the correspondence check, cites a span small enough to read, and rests on
    parsed code must clear the threshold *without* a model call. A first attempt at these weights
    put every true claim in the corpus at 0.65, a hair under the line, which would have listed the
    entire document for review and taught the reader to ignore the list.
    """
    values = _measured(fixture, "supported")
    assert min(values) >= LOW_CONFIDENCE_NOTE, (
        f"{fixture}: a true, well-cited claim scored {min(values):.2f}, under the "
        f"{LOW_CONFIDENCE_NOTE} review threshold"
    )


@pytest.mark.parametrize("fixture", FIXTURES)
def test_a_contradicted_claim_falls_below_the_review_threshold(fixture) -> None:
    values = _measured(fixture, "contradicted")
    assert max(values) < LOW_CONFIDENCE_NOTE


def test_the_published_numbers_actually_vary() -> None:
    """D6 restated as a property: a figure that never moves carries no information."""
    values = [
        value
        for fixture in FIXTURES
        for key in ("supported", "contradicted")
        for value in _measured(fixture, key)
    ]
    assert len(set(values)) >= 3, f"only {sorted(set(values))} ever published"
    assert statistics.pstdev(values) > 0.05


# --------------------------------------------------------------------------------------------
# Reporting and the flag
# --------------------------------------------------------------------------------------------


def test_a_weak_claim_says_why_it_is_weak() -> None:
    brd, _ = _recalibrate(
        ProductBRD(functional_requirements=[_claim(verdict="rejected")]),
        KnowledgeGraph(repository_name="t", entities=[_entity()], relationships=[]),
    )
    assert any("Confidence is low" in note for note in brd.functional_requirements[0].assumptions)


def test_a_strong_claim_is_not_annotated() -> None:
    """Explaining a number nobody is going to question is noise in the document."""
    strong = _claim(
        checked_by="entailment",
        evidence=[SourceLocation(file="a.py", line_start=1, line_end=5, symbol="h")],
    )
    brd, _ = _recalibrate(
        ProductBRD(functional_requirements=[strong]),
        KnowledgeGraph(repository_name="t", entities=[_entity()], relationships=[]),
    )
    assert brd.functional_requirements[0].assumptions == []


def test_the_pass_can_be_turned_off() -> None:
    assert Settings().confidence_recalibration is True
    brd, metrics = _recalibrate(
        ProductBRD(functional_requirements=[_claim(confidence=1.0)]),
        KnowledgeGraph(repository_name="t", entities=[], relationships=[]),
        confidence_recalibration=False,
    )
    assert metrics["confidence_recalibration_disabled"] is True
    assert brd.functional_requirements[0].confidence == 1.0
    assert brd.functional_requirements[0].model_confidence is None


def test_running_twice_does_not_overwrite_the_original_figure() -> None:
    """The model's number must survive a re-run, or the comparison is lost after one repair."""
    graph = KnowledgeGraph(repository_name="t", entities=[_entity()], relationships=[])
    brd, _ = _recalibrate(ProductBRD(functional_requirements=[_claim(confidence=1.0)]), graph)
    once = brd.functional_requirements[0].confidence
    brd, _ = _recalibrate(brd, graph)
    assert brd.functional_requirements[0].model_confidence == 1.0
    assert brd.functional_requirements[0].confidence == once

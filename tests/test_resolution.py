"""Acting on what validation found, and checking the finished set.

Validation could already tell a bad requirement from a good one. Nothing acted on the answer: a
claim the correspondence check had contradicted was marked `status: "rejected"` and published in
the requirements list beside the claims that passed. The requirements document and the list of
things the analysis could not stand behind were the same list.

The fix is to partition, not to delete. Every candidate leaves resolution with a disposition, and
what cannot be published moves into `unresolved_candidates` carrying its evidence and its reason.

The sharpest risk this creates is the reason for the objective/interpretive split tested below.
Once a rejection removes a requirement, a check that can be wrong must not be able to remove one on
its own — and one of them can be wrong in a predictable direction, because the synthesis skill asks
for requirements in the product's business words and the anchoring check compares them against the
code's identifiers.
"""

from __future__ import annotations

import tempfile
from pathlib import Path


from agentic.capabilities.resolution import (
    MIN_SUPPORTING_CAPABILITIES,
    _cycles,
    resolve_candidates,
    validate_synthesis,
)
from agentic.config import Settings
from agentic.schemas.brd import BRDItem, CorrespondenceOutcome, ProductBRD
from agentic.schemas.core import Entity, KnowledgeGraph, SourceLocation
from agentic.store import ArtifactStore


def _at(line=10, end=20, file="svc.py"):
    return SourceLocation(file=file, line_start=line, line_end=end)


def _entity(kind="ENDPOINT", name="GET /things", line=10):
    return Entity(
        id=f"e:{kind}:{name}",
        kind=kind,
        name=name,
        qualified_name=name,
        location=_at(line, line),
    )


def _item(title, statement=None, **kwargs) -> BRDItem:
    return BRDItem(
        title=title,
        statement=(
            statement
            if statement is not None
            else f"A person shall be able to {title.lower()} without assistance."
        ),
        confidence=0.8,
        evidence=kwargs.pop("evidence", [_at()]),
        **kwargs,
    )


def _run(stage, brd: ProductBRD, entities=(), **flags):
    run = Path(tempfile.mkdtemp())
    (run / "artifacts").mkdir(parents=True)
    store = ArtifactStore(run)
    graph = KnowledgeGraph(repository_name="t", entities=list(entities), relationships=[])
    paths = {"brd": store.save("brd", brd), "graph": store.save("graph", graph)}

    class _Ctx:
        settings = Settings(**{"candidate_resolution": True, **flags})
        state = {"artifacts": paths}

        def load(self, name, model=None):
            return store.load_model(paths[name], model) if model else store.load_json(paths[name])

    result = stage(_Ctx())
    return result.artifacts["brd"], result


def _published(brd: ProductBRD) -> list[str]:
    return [item.title for items in brd.sections().values() for item in items]


# --------------------------------------------------------------------------------------------
# Rejected and uncertain leave the document
# --------------------------------------------------------------------------------------------


def test_a_contradicted_claim_leaves_the_requirements_document() -> None:
    """The defect stated plainly: it used to be published with a field saying it was wrong."""
    item = _item(
        "Store On Read",
        validation=CorrespondenceOutcome(
            verdict="rejected", reason="Every entry point it cites is read-only.", objective=True
        ),
    )
    brd, result = _run(resolve_candidates, ProductBRD(functional_requirements=[item]))

    assert _published(brd) == []
    unresolved = result.artifacts["unresolved_candidates"]["candidates"]
    assert len(unresolved) == 1
    assert unresolved[0]["disposition"] == "reject"
    assert unresolved[0]["evidence"], "a candidate that leaves keeps its evidence"
    assert "read-only" in unresolved[0]["reason"]


def test_an_interpretive_failure_alone_makes_a_claim_uncertain_not_wrong() -> None:
    """The risk that appears the moment rejection becomes destructive.

    The anchoring check compares a statement against the code's identifiers, and the synthesis
    skill asks for statements in the product's own business words. A correctly written requirement
    can therefore fail it -- and did, on the end-to-end fixture, where a claim about "a supplier
    bill" was measured against code that calls it an invoice. Uncertain and wrong are different
    answers and must not share a disposition.
    """
    item = _item(
        "Record A Bill",
        validation=CorrespondenceOutcome(
            verdict="rejected", checks_failed=["lexically_anchored"], objective=False
        ),
    )
    brd, result = _run(resolve_candidates, ProductBRD(functional_requirements=[item]))
    unresolved = result.artifacts["unresolved_candidates"]["candidates"]
    assert unresolved[0]["disposition"] == "open_question"
    assert "nothing contradicts it outright" in unresolved[0]["reason"]


def test_a_supported_claim_is_kept() -> None:
    item = _item("Read A Thing", validation=CorrespondenceOutcome(verdict="supported"))
    brd, _ = _run(resolve_candidates, ProductBRD(functional_requirements=[item]))
    assert _published(brd) == ["Read A Thing"]
    assert brd.functional_requirements[0].disposition == "keep"


def test_a_statement_a_transformation_broke_is_rejected() -> None:
    """Problem 11: empty and truncated statements were reaching the document as blank rows."""
    for broken in ("", "The system shall .", "Shall store"):
        brd, result = _run(
            resolve_candidates, ProductBRD(functional_requirements=[_item("X", broken)])
        )
        assert _published(brd) == [], broken
        assert result.artifacts["unresolved_candidates"]["candidates"][0]["disposition"] == "reject"


# --------------------------------------------------------------------------------------------
# Conflicting interpretations
# --------------------------------------------------------------------------------------------


def test_the_best_supported_reading_wins_and_the_others_become_questions() -> None:
    """Two readings of one piece of evidence cannot both be requirements."""
    # The same verb on the same evidence, saying different things about what happens next. That
    # is what a competing *reading* is: a different verb is a different capability.
    strong = _item(
        "Publish A Thing",
        "A person shall be able to publish a thing so that everyone can see it immediately.",
        cluster_id="CAP-001",
        cluster_conflict=True,
        acceptance_criteria=["The thing appears to everyone who can see the list."],
    )
    weak = _item(
        "Publish A Thing For Review",
        "A person shall be able to publish a thing so that a reviewer decides whether it appears.",
        cluster_id="CAP-001",
        cluster_conflict=True,
    )
    brd, result = _run(
        resolve_candidates,
        ProductBRD(functional_requirements=[strong, weak]),
        entities=[_entity(line=10)],
    )

    assert _published(brd) == ["Publish A Thing"]
    unresolved = result.artifacts["unresolved_candidates"]["candidates"]
    assert unresolved[0]["disposition"] == "open_question"
    assert "better supported" in unresolved[0]["reason"]
    assert result.metrics["conflicts_settled"] == 1


def test_candidates_citing_different_code_are_not_competing_readings() -> None:
    """A cluster is marked conflicting when any pair in it disagrees, and a large cluster is a
    group of related capabilities rather than a group of readings of one behaviour.

    Keying conflict resolution on the cluster made one disagreeing pair stand for all of it: on
    one repository seventy-two individually grounded capabilities left the document in favour of a
    single unrelated one. Two candidates are competing readings only when they rest on exactly the
    same lines.
    """
    here = _item("Publish A Thing", cluster_id="CAP-009", cluster_conflict=True)
    elsewhere = _item(
        "Archive A Thing",
        cluster_id="CAP-009",
        cluster_conflict=True,
        evidence=[_at(90, 95, file="other.py")],
    )
    # And two candidates that *do* share a citation but assert different actions: a citation
    # routinely lands on a line several capabilities pass through -- a decorator, a dispatch
    # table -- and those are capabilities, not readings.
    same_line = _item("Retract A Thing", cluster_id="CAP-009", cluster_conflict=True)
    brd, result = _run(
        resolve_candidates,
        ProductBRD(functional_requirements=[here, elsewhere, same_line]),
        entities=[_entity(line=10)],
    )

    assert sorted(_published(brd)) == [
        "Archive A Thing", "Publish A Thing", "Retract A Thing",
    ]
    assert result.metrics["conflicting_deferred"] == 0


def test_evenly_matched_readings_are_both_held_back() -> None:
    """An arbitrary winner would state something the analysis never established."""
    left = _item(
        "Publish A Thing",
        "A person shall be able to publish a thing so that everyone can see it immediately.",
        cluster_id="CAP-002",
        cluster_conflict=True,
    )
    right = _item(
        "Publish A Thing For Review",
        "A person shall be able to publish a thing so that a reviewer decides whether it appears.",
        cluster_id="CAP-002",
        cluster_conflict=True,
    )
    brd, result = _run(resolve_candidates, ProductBRD(functional_requirements=[left, right]))

    assert _published(brd) == []
    assert result.metrics["conflicting_deferred"] == 2
    reasons = {c["reason"] for c in result.artifacts["unresolved_candidates"]["candidates"]}
    assert any("does not favour any of them" in reason for reason in reasons)


# --------------------------------------------------------------------------------------------
# Business requirements must be supported
# --------------------------------------------------------------------------------------------


def test_an_outcome_nothing_stands_under_becomes_a_capability() -> None:
    """A business requirement realised by one capability is that capability restated."""
    outcome = _item("Run The Whole Business", requirement_type="business")
    child = _item("Do One Thing", parent="Run The Whole Business")
    brd, _ = _run(
        resolve_candidates,
        ProductBRD(business_requirements=[outcome], functional_requirements=[child]),
    )

    assert brd.business_requirements == []
    titles = [item.title for item in brd.functional_requirements]
    assert "Run The Whole Business" in titles
    demoted = next(i for i in brd.functional_requirements if i.title == "Run The Whole Business")
    assert demoted.disposition == "reclassify"
    assert demoted.requirement_type == "functional"


def test_an_outcome_several_capabilities_realise_is_kept() -> None:
    outcome = _item("Manage A Thing End To End", requirement_type="business")
    children = [
        _item(f"Capability {n}", parent="Manage A Thing End To End")
        for n in range(MIN_SUPPORTING_CAPABILITIES)
    ]
    brd, _ = _run(
        resolve_candidates,
        ProductBRD(business_requirements=[outcome], functional_requirements=children),
    )
    assert [item.title for item in brd.business_requirements] == ["Manage A Thing End To End"]


def test_removing_a_requirement_clears_what_pointed_at_it() -> None:
    """A link to something no longer published looks like a hierarchy and resolves to nothing."""
    gone = _item(
        "Store On Read",
        validation=CorrespondenceOutcome(verdict="rejected", objective=True),
    )
    child = _item("A Child", parent="Store On Read", parent_relation="refines")
    brd, result = _run(
        resolve_candidates, ProductBRD(functional_requirements=[gone, child])
    )
    survivor = brd.functional_requirements[0]
    assert survivor.title == "A Child"
    assert not survivor.parent and not survivor.parent_relation
    assert result.metrics["orphaned_links_cleared"] == 1


def test_the_pass_can_be_turned_off() -> None:
    assert Settings().candidate_resolution is True
    item = _item("X", validation=CorrespondenceOutcome(verdict="rejected", objective=True))
    brd, result = _run(
        resolve_candidates,
        ProductBRD(functional_requirements=[item]),
        candidate_resolution=False,
    )
    assert result.metrics["resolution_disabled"] is True
    assert _published(brd) == ["X"]


# --------------------------------------------------------------------------------------------
# Final validation
# --------------------------------------------------------------------------------------------


def test_a_cycle_is_found_and_broken() -> None:
    """Invisible to a check that looks one link up: every parent exists and the chain has no top."""
    a = _item("A", parent="B", parent_relation="refines", parent_id="FR-002")
    b = _item("B", parent="A", parent_relation="refines", parent_id="FR-001")
    brd = ProductBRD(functional_requirements=[a, b])
    assert _cycles(brd), "the cycle was not detected"

    fixed, result = _run(validate_synthesis, brd)
    assert any(i["code"] == "CIRCULAR_HIERARCHY" for i in result.artifacts["synthesis_findings"]["issues"])
    assert result.metrics["cycles_broken"] == 1
    assert not _cycles(fixed), "the loop survived"


def test_a_relation_with_no_parent_is_cleared_and_reported() -> None:
    item = _item("A", parent_relation="refines")
    fixed, result = _run(validate_synthesis, ProductBRD(functional_requirements=[item]))
    assert not fixed.functional_requirements[0].parent_relation
    codes = {i["code"] for i in result.artifacts["synthesis_findings"]["issues"]}
    assert "RELATION_WITHOUT_PARENT" in codes


def test_the_final_check_names_every_failure_mode_it_finds() -> None:
    duplicate_a = _item("Same Thing", "A person shall be able to do the same thing daily.")
    duplicate_b = _item("Same Thing", "A person shall be able to do the same thing daily.")
    # A third copy under a different title. Merging is keyed on the title, so this one survives
    # the fold and keeps DUPLICATE_STATEMENT exercised — a repeated statement under two names is
    # a finding for a person, not something to settle by picking one.
    echo = _item("Another Name", "A person shall be able to do the same thing daily.")
    rejected = _item("Bad", status="rejected", disposition="reject")
    observation = _item("Detail", requirement_type="observation")
    unresolved_conflict = _item("Contested", cluster_conflict=True)
    outcome = _item("Lonely Outcome", requirement_type="business")

    _, result = _run(
        validate_synthesis,
        ProductBRD(
            business_requirements=[outcome],
            functional_requirements=[
                duplicate_a, duplicate_b, echo, rejected, unresolved_conflict,
            ],
            observations=[],
            system_requirements=[observation],
        ),
    )
    after = result.artifacts["brd"]
    # The duplicate pair is settled, not merely named: one requirement leaves the document.
    assert [item.title for item in after.functional_requirements].count("Same Thing") == 1
    codes = {i["code"] for i in result.artifacts["synthesis_findings"]["issues"]}
    assert {
        "DUPLICATE_REQUIREMENT",
        "DUPLICATE_STATEMENT",
        "REJECTED_IN_DOCUMENT",
        "UNRESOLVED_IN_DOCUMENT",
        "OBSERVATION_AS_REQUIREMENT",
        "UNSUPPORTED_BUSINESS_REQUIREMENT",
        "UNRESOLVED_CONFLICT",
    } <= codes, sorted(codes)


def test_a_clean_set_produces_no_findings() -> None:
    """A validator that always finds something is one nobody reads."""
    outcome = _item("Manage A Thing", requirement_type="business")
    children = [
        _item(f"Capability {n}", parent="Manage A Thing", parent_relation="refines")
        for n in range(MIN_SUPPORTING_CAPABILITIES)
    ]
    brd = ProductBRD(business_requirements=[outcome], functional_requirements=children)
    brd.assign_ids()
    _, result = _run(validate_synthesis, brd)
    assert result.artifacts["synthesis_findings"]["issues"] == []

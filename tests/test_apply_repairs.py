"""A repair pass must not be able to shrink the document.

The repair step was typed `ProductBRD`: a model asked to correct three wordings had to re-emit
every requirement in the set inside one reply bounded by the output token limit. Past a few dozen
items that is arithmetically impossible, so what came back was a fraction of the document and
nothing counted the loss — a gate rejection over one phrase deleted most of the analysis. It is
the same failure that made the old merge step unusable, left in the one step that only runs after
something has already gone wrong.

The model now returns only what it fixed; everything else is carried across by this code.
"""

from __future__ import annotations


from agentic.capabilities.claims import apply_repairs
from agentic.schemas.brd import BRDItem, ProductBRD, RepairedItems
from agentic.schemas.core import GateReport, Severity, SourceLocation, ValidationIssue


def _item(index: int, **kwargs) -> BRDItem:
    return BRDItem(
        title=f"Capability {index}",
        statement=f"A user shall be able to do thing {index} so that something follows.",
        confidence=1.0,
        area="OPS",
        priority="M",
        evidence=[SourceLocation(file="app.py", line_start=index, line_end=index + 2)],
        graph_path=["app.handler"],
        **kwargs,
    )


def _brd(count: int) -> ProductBRD:
    return ProductBRD(
        functional_requirements=[_item(index) for index in range(1, count + 1)]
    ).assign_ids()


def _gate(*subject_ids: str) -> GateReport:
    return GateReport(
        gate="gate_requirements",
        valid=False,
        checks_run=1,
        errors=len(subject_ids),
        warnings=0,
        issues=[
            ValidationIssue(
                code="TECHNICAL_VOCABULARY",
                severity=Severity.ERROR,
                message=f"{subject_id} statement contains technical vocabulary",
                subject_id=subject_id,
            )
            for subject_id in subject_ids
        ],
    )


class _Ctx:
    def __init__(self, brd, repairs, report):
        self._values = {"brd": brd, "repairs": repairs, "requirement_gate": report}

    def load(self, name, model=None):
        value = self._values.get(name)
        if value is None:
            raise KeyError(name)
        return value


def test_a_large_document_survives_a_two_item_repair() -> None:
    """The property the rebuild turns on: fixing two claims must not cost the other 198."""
    brd = _brd(200)
    target = brd.functional_requirements[4].id
    fixed = brd.functional_requirements[4].model_copy(deep=True)
    fixed.statement = "A user shall be able to do thing five so that a result is recorded."

    result = apply_repairs(_Ctx(brd, RepairedItems(items=[fixed]), _gate(target)))
    out = result.artifacts["brd"]

    assert len(out.functional_requirements) == 200
    assert result.metrics["repairs_applied"] == 1
    assert next(i for i in out.functional_requirements if i.id == target).statement == (
        "A user shall be able to do thing five so that a result is recorded."
    )


def test_a_claim_the_gate_did_not_reject_cannot_be_rewritten() -> None:
    """Otherwise a pass asked to fix one wording could quietly alter claims that had passed."""
    brd = _brd(5)
    rejected = brd.functional_requirements[0].id
    smuggled = brd.functional_requirements[3].model_copy(deep=True)
    smuggled.statement = "Something the repair pass invented."

    result = apply_repairs(_Ctx(brd, RepairedItems(items=[smuggled]), _gate(rejected)))
    out = result.artifacts["brd"]

    assert out.functional_requirements[3].statement != "Something the repair pass invented."
    assert result.metrics["repairs_out_of_scope"] == 1


def test_a_rejected_claim_can_be_deleted_but_only_a_rejected_one() -> None:
    brd = _brd(4)
    rejected = brd.functional_requirements[1].id
    other = brd.functional_requirements[2].id

    result = apply_repairs(
        _Ctx(brd, RepairedItems(deleted=[rejected, other]), _gate(rejected))
    )
    out = result.artifacts["brd"]

    assert len(out.functional_requirements) == 3
    assert rejected not in {item.id for item in out.functional_requirements}
    assert other in {item.id for item in out.functional_requirements}


def test_a_rejected_claim_left_unfixed_stands_and_is_reported() -> None:
    """Silence from the repair pass must not delete the claim; the gate judges it again."""
    brd = _brd(3)
    rejected = brd.functional_requirements[0].id

    result = apply_repairs(_Ctx(brd, RepairedItems(), _gate(rejected)))

    assert len(result.artifacts["brd"].functional_requirements) == 3
    assert result.metrics["repairs_unfixed"] == 1
    assert any("neither fixed nor deleted" in message for message in result.messages)


def test_system_bookkeeping_is_not_the_repair_pass_to_change() -> None:
    brd = _brd(3)
    target = brd.functional_requirements[0].id
    fixed = brd.functional_requirements[0].model_copy(deep=True)
    fixed.id = "FR-999"
    fixed.area = ""
    fixed.statement = "A user shall be able to do thing one so that a result is recorded."

    out = apply_repairs(_Ctx(brd, RepairedItems(items=[fixed]), _gate(target))).artifacts["brd"]
    # The model returned the item under a made-up id; matching is by the id it was given.
    restored = out.functional_requirements[0]
    assert restored.id == target
    assert restored.area == "OPS"


def test_a_failed_repair_passes_every_claim_through() -> None:
    """A repair call that never returned repaired nothing — that is not a reason to lose the set."""
    brd = _brd(50)
    ctx = _Ctx(brd, None, _gate(brd.functional_requirements[0].id))

    result = apply_repairs(ctx)

    assert len(result.artifacts["brd"].functional_requirements) == 50
    assert result.metrics["repairs_applied"] == 0

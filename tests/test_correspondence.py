"""Does a claim describe the code it cites?

The defect this answers was total. Evidence was checked for *existence* -- the file resolves, the
lines are real, the symbol appears nearby -- and every other check compared a statement to another
statement or to a blacklist. Nothing anywhere compared a requirement's text to what the cited code
does, so a claim could name a real function at a real line and describe something else entirely
and pass every gate in the system.

The measurement is two-sided on purpose, because a validator is trivial to make good at one side.
Rejecting everything catches every bad claim; rejecting nothing never fires wrongly. Both numbers
are graded here, per fixture, against claims written out in `corpus_truth/correspondence.json`:

* every deliberately-false claim must be caught by the structural checks, with no model call;
* at most 10% of true claims may be rejected, on each fixture independently.

Per-fixture rather than pooled: a validator that works on the HTTP service and rejects half the
worker fixture is a stack-specific validator, and pooling would hide it.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from tests.conftest import run_step_or_stub
from agentic.capabilities.correspondence import (
    CHECKS,
    check,
    validate_correspondence,
)
from agentic.capabilities.registry import CapabilityContext
from agentic.config import Settings
from agentic.runtime import prepare_run
from agentic.schemas.brd import BRDItem, ProductBRD
from agentic.schemas.core import KnowledgeGraph, SourceLocation
from agentic.skills.pack import load_pack
from agentic.state import initial_state
from agentic.store import ArtifactStore

REPO_ROOT = Path(__file__).resolve().parents[1]
TRUTH = json.loads(
    (REPO_ROOT / "tests" / "fixtures" / "corpus_truth" / "correspondence.json").read_text(
        encoding="utf-8"
    )
)
FIXTURES = [name for name in TRUTH if not name.startswith("_")]

# The false-rejection budget, per fixture. A validator nobody trusts is one that is switched off.
FALSE_REJECTION_BUDGET = 0.10


def _item(spec: dict) -> BRDItem:
    return BRDItem(
        id=spec["title"].replace(" ", "-").lower(),
        title=spec["title"],
        statement=spec["statement"],
        confidence=0.9,
        requirement_type=spec.get("requirement_type", "functional"),
        evidence=[SourceLocation(**location) for location in spec["evidence"]],
    )


def _graph_for(fixture: str) -> tuple[KnowledgeGraph, ArtifactStore, dict]:
    workspace = Path(tempfile.mkdtemp())
    manifest = load_pack(REPO_ROOT / "codebase-requirement-skills")
    settings = Settings(runs_dir=workspace / "runs")
    prepared = prepare_run(
        source=REPO_ROOT / "tests" / "fixtures" / "corpus" / fixture, settings=settings
    )
    state = initial_state(
        run_id="t",
        pack="codebase-requirements",
        run_dir=str(prepared.run_dir),
        source_root=str(prepared.source_root),
        repository_name=fixture,
    )
    store = ArtifactStore(prepared.run_dir)
    for spec in manifest.steps:
        ctx = CapabilityContext(
            state=state,
            step=spec,
            settings=settings,
            store=store,
            skill=manifest.skill_registry().load(spec.skill),
            payload={"manifest": manifest},
        )
        outcome = run_step_or_stub(spec, ctx)
        for name, value in (outcome.artifacts or {}).items():
            state["artifacts"][name] = store.save(name, value)
        if spec.id == "discover_entry_points":
            break
    return store.load_model(state["artifacts"]["graph"], KnowledgeGraph), store, state


def _validate(fixture: str, items: list[BRDItem], **flags) -> tuple[ProductBRD, dict]:
    """Run the real capability, with the model half off unless a test asks for it."""
    graph, store, state = _graph_for(fixture)
    brd = ProductBRD(functional_requirements=list(items))
    paths = {
        "brd": store.save("brd_under_test", brd),
        "graph": state["artifacts"]["graph"],
    }
    settings = Settings(**{"correspondence_entailment": False, **flags})

    class _Ctx:
        def __init__(self):
            self.settings = settings
            self.state = {"artifacts": paths}

        def load(self, name, model=None):
            return store.load_model(paths[name], model)

    result = validate_correspondence(_Ctx())
    return result.artifacts["brd"], result.metrics


def _all_items(brd: ProductBRD) -> list[BRDItem]:
    return [item for items in brd.every_section().values() for item in items]


# --------------------------------------------------------------------------------------------
# The two numbers that matter
# --------------------------------------------------------------------------------------------


@pytest.mark.parametrize("fixture", FIXTURES)
def test_every_false_claim_is_caught_without_a_model_call(fixture) -> None:
    """The deterministic half must earn its place, or every claim costs a model call."""
    specs = TRUTH[fixture]["contradicted"]
    brd, metrics = _validate(fixture, [_item(spec) for spec in specs])

    by_id = {item.id: item for item in _all_items(brd)}
    missed = [
        spec["title"]
        for spec in specs
        if by_id[_item(spec).id].status != "rejected"
    ]
    assert missed == [], f"{fixture}: passed a claim the code contradicts: {missed}"
    assert metrics["correspondence_entailment_ran"] is False


@pytest.mark.parametrize("fixture", FIXTURES)
def test_each_false_claim_is_caught_by_the_check_written_for_it(fixture) -> None:
    """Otherwise a check can rot while another one covers for it."""
    for spec in TRUTH[fixture]["contradicted"]:
        brd, _ = _validate(fixture, [_item(spec)])
        item = _all_items(brd)[0]
        assert spec["expect_check"] in item.validation.checks_failed, (
            f"{fixture}: {spec['title']!r} was expected to fail {spec['expect_check']} "
            f"but failed {item.validation.checks_failed}"
        )


@pytest.mark.parametrize("fixture", FIXTURES)
def test_true_claims_are_rejected_no_more_than_the_budget_allows(fixture) -> None:
    """The side a validator is easy to get wrong.

    Rejecting everything catches every false claim, so the catch rate alone says nothing. These
    claims are all true of the fixture and cite the right lines; rejecting them is the failure
    mode that makes a validator worse than none.
    """
    specs = TRUTH[fixture]["supported"]
    brd, _ = _validate(fixture, [_item(spec) for spec in specs])

    rejected = [
        (item.title, item.validation.reason)
        for item in _all_items(brd)
        if item.status == "rejected"
    ]
    rate = len(rejected) / len(specs)
    assert rate <= FALSE_REJECTION_BUDGET, (
        f"{fixture}: rejected {len(rejected)} of {len(specs)} true claims "
        f"({rate:.0%}, budget {FALSE_REJECTION_BUDGET:.0%}): {rejected}"
    )


@pytest.mark.parametrize(
    "fixture", [name for name in FIXTURES if TRUTH[name].get("entailment_only")]
)
def test_the_structural_half_abstains_where_it_cannot_decide(fixture) -> None:
    """The boundary between the two halves, kept honest in both directions.

    These claims are false, and no structural check can catch them without rejecting a great deal
    that is true. "A client shall be able to save an order directly" cites code a client really
    can cause to run, through a handler; only the word "directly" is wrong. A structural rule
    strong enough to reject it would reject every claim that cites the code behind a handler.

    So the structural half must leave them alone and say so, rather than guessing. They are what
    the entailment pass exists for.
    """
    for spec in TRUTH[fixture]["entailment_only"]:
        brd, _ = _validate(fixture, [_item(spec)])
        item = _all_items(brd)[0]
        assert item.status == "candidate", (
            f"{fixture}: {spec['title']!r} was rejected structurally on "
            f"{item.validation.checks_failed}, but no structural rule can decide it"
        )


# --------------------------------------------------------------------------------------------
# Nothing is lost
# --------------------------------------------------------------------------------------------


def test_a_rejected_claim_is_published_with_its_reason() -> None:
    """A reader can check a doubtful claim and cannot recover a deleted one."""
    spec = TRUTH["py_service"]["contradicted"][0]
    brd, _ = _validate("py_service", [_item(spec)])

    items = _all_items(brd)
    assert len(items) == 1, "a rejected claim must still be published"
    assert items[0].status == "rejected"
    assert items[0].validation.reason
    assert items[0].evidence == _item(spec).evidence, "its evidence is untouched"


def test_a_supported_claim_records_that_it_was_checked() -> None:
    spec = TRUTH["py_service"]["supported"][0]
    brd, _ = _validate("py_service", [_item(spec)])
    item = _all_items(brd)[0]
    assert item.status == "candidate"
    assert item.validation.verdict == "supported"
    assert item.validation.checked_by == "deterministic"


def test_observations_are_not_asked_whether_the_code_does_this() -> None:
    """An observation records what the code does, so the question cannot distinguish good from bad."""
    graph, store, state = _graph_for("py_service")
    observation = _item(TRUTH["py_service"]["contradicted"][1])
    brd = ProductBRD(observations=[observation])
    paths = {"brd": store.save("brd_obs", brd), "graph": state["artifacts"]["graph"]}

    class _Ctx:
        settings = Settings(correspondence_entailment=False)
        state = {"artifacts": paths}

        def load(self, name, model=None):
            return store.load_model(paths[name], model)

    result = validate_correspondence(_Ctx())
    assert result.metrics["correspondence_checked"] == 0
    assert result.artifacts["brd"].observations[0].status == "candidate"


# --------------------------------------------------------------------------------------------
# The seam
# --------------------------------------------------------------------------------------------


def test_a_new_check_needs_only_a_registration() -> None:
    try:

        @check("test-always-fails")
        def _always(item, entities, view):
            return "a test check that always fails"

        brd, metrics = _validate("py_service", [_item(TRUTH["py_service"]["supported"][0])])
        item = _all_items(brd)[0]
        assert item.status == "rejected"
        assert "test-always-fails" in item.validation.checks_failed
        assert metrics["correspondence_failed_test-always-fails"] == 1
    finally:
        CHECKS.pop("test-always-fails", None)


def test_a_duplicate_check_name_is_refused() -> None:
    with pytest.raises(ValueError, match="already registered"):
        check("lexically_anchored")(lambda item, entities, view: None)


def test_identifier_words_survive_any_naming_convention() -> None:
    """Anchoring compares against the code's own vocabulary, however that codebase spells it."""
    from agentic.capabilities.correspondence import _identifier_words

    expected = {"create", "invoice"}
    for spelling in ("createInvoice", "create_invoice", "CreateInvoice", "CREATE_INVOICE"):
        assert _identifier_words(spelling) == expected, spelling


def test_the_pass_can_be_turned_off() -> None:
    assert Settings().correspondence_validation is True
    assert Settings().correspondence_entailment is True

    brd, metrics = _validate(
        "py_service",
        [_item(TRUTH["py_service"]["contradicted"][0])],
        correspondence_validation=False,
    )
    assert metrics["correspondence_disabled"] is True
    assert _all_items(brd)[0].status == "candidate"


def test_a_failing_entailment_call_cannot_fail_the_run() -> None:
    """A validator that can halt a run is a liability; it degrades to the structural checks."""
    import agentic.capabilities.correspondence as module

    def _explode(ctx, pending):
        raise RuntimeError("no model configured")

    original = module._entail
    module._entail = _explode
    try:
        brd, metrics = _validate(
            "py_service",
            [_item(TRUTH["py_service"]["supported"][0])],
            correspondence_entailment=True,
        )
        assert metrics["correspondence_entailment_ran"] is False
        assert _all_items(brd)[0].status == "candidate"
    finally:
        module._entail = original

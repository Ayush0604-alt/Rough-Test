"""The stage between synthesis and validation, and the properties it exists to give the document.

Synthesis reasons about one slice of a repository at a time; validation judges claims one at a
time. Neither can decide what the candidates add up to, and for a long time nothing did — the
capabilities were built and left out of the manifest, so every candidate any worker produced went
to the gate exactly as written. These tests hold the seam shut: the stages are named by the
workflow, they run in an order where each one's input is settled, and the failures they were built
to prevent stay prevented.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agentic.capabilities.assemble import _heading_key, _reconcile_areas, ground_outcomes
from agentic.config import Settings
from agentic.schemas.brd import (
    PARENT_RULES,
    BRDItem,
    ProductBRD,
    permitted_parents,
)
from agentic.schemas.core import SourceLocation
from agentic.skills.pack import load_pack

REPO_ROOT = Path(__file__).resolve().parents[1]
PACK = REPO_ROOT / "codebase-requirement-skills"


def _item(title: str, statement: str = "", **kwargs) -> BRDItem:
    kwargs.setdefault("confidence", 0.8)
    return BRDItem(
        title=title,
        statement=statement or f"A user shall be able to {title.lower()} so that it is recorded.",
        **kwargs,
    )


def _at(file: str, line: int) -> SourceLocation:
    return SourceLocation(file=file, line_start=line, line_end=line + 3)


# --------------------------------------------------------------------------------------------
# The workflow names the stage
# --------------------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def manifest():
    return load_pack(PACK)


def test_the_consolidation_stage_is_in_the_workflow(manifest) -> None:
    """Every capability that decides what the candidate set adds up to must actually run.

    Six of these existed, were tested, and were named by no step in the manifest — so the
    behaviour they implement was not in the product at all. That is the defect this asserts
    against, and it is invisible to any test that calls a capability directly.
    """
    named = {step.capability for step in manifest.steps}
    for capability in (
        "cluster_capabilities",          # repository-wide grouping by meaning
        "validate_correspondence",       # does the claim say what the code says
        "recalibrate_confidence",
        "review_implementation_leakage",  # implementation out of the statement
        "resolve_candidates",            # one disposition per candidate
        "hedge_requirements",            # acceptance criteria, after the set is final
        "validate_synthesis",            # the finished set, checked as a whole
    ):
        assert capability in named, capability


def test_the_stages_run_in_an_order_where_each_input_is_settled(manifest) -> None:
    order = [step.id for step in manifest.steps]

    def before(first: str, second: str) -> bool:
        return order.index(first) < order.index(second)

    # Grouping needs every fragment; the top tier is written from the groups.
    assert before("cluster_candidates", "digest_areas")
    # Nothing is re-levelled or merged until every candidate is in one set.
    assert before("assemble", "consolidate")
    # A disposition is decided after the evidence check that informs it.
    assert before("check_correspondence", "resolve")
    # Acceptance criteria are written against the final set, never against a candidate that is
    # about to be merged, reclassified, or dropped.
    assert before("resolve", "derive_criteria")
    # Identifiers are stamped last, after everything that could change what is published.
    assert before("derive_criteria", "prepare_claims")
    # And the finished set is checked as a whole, after both the pass and the failure paths.
    assert before("gate_requirements", "final_check")
    assert before("quarantine", "final_check")
    assert before("final_check", "render")


def test_every_consolidation_pass_is_on_by_default() -> None:
    """They shipped off, which is the same as not existing."""
    settings = Settings()
    for flag in (
        "semantic_clustering",
        "rule_classification",
        "dedup_pass",
        "collapse_parameterised",
        "leakage_review",
        "candidate_resolution",
        "correspondence_validation",
        "confidence_recalibration",
        "hedging_pass",
    ):
        assert getattr(settings, flag) is True, flag


# --------------------------------------------------------------------------------------------
# Grouping by capability rather than by code location
# --------------------------------------------------------------------------------------------


def test_two_groups_whose_headings_mean_the_same_thing_become_one() -> None:
    """A product does not have two separately-managed halves whose names are synonyms.

    Workers invent an area code each, alone, so two of them looking at different parts of one
    capability reliably produce two codes with the same heading. Published, that is one part of
    the product split across two headings for no reason a reader can see.
    """
    remap = _reconcile_areas({"BATC": "Batch Management", "BCH": "Batch management"})
    # One of them is folded into the other; only the folded one appears in the map.
    assert set(remap) in ({"BATC"}, {"BCH"})
    assert set(remap.values()) <= {"BATC", "BCH"}


def test_headings_that_mean_different_things_stay_apart() -> None:
    remap = _reconcile_areas({"ORD": "Order capture", "PAY": "Payment settlement"})
    assert remap == {}


def test_a_heading_that_narrows_another_is_folded_into_it() -> None:
    """A worker names the part of the product its own slice can see.

    A platform whose candidate records are touched from eleven places produces eleven headings
    that are all candidate management seen through one entry point. Published separately they
    give a reader eleven headings to search instead of one section to read, and what actually
    distinguishes them is already in the requirement titles inside.
    """
    remap = _reconcile_areas(
        {
            "CAND": "Candidate Management",
            "CND": "Candidate Notes Management",
            "ARCH": "Candidate Archiving",
            "BCH": "Batch Management",
        }
    )
    assert remap["CND"] == "CAND"
    assert remap["ARCH"] == "CAND"
    assert "BCH" not in remap, "a different part of the product is left alone"


def test_nothing_is_folded_into_a_group_nobody_named() -> None:
    """No grouping is invented. If no area called itself the general thing, none is created."""
    remap = _reconcile_areas(
        {"CND": "Candidate Notes Management", "ARCH": "Candidate Archiving"}
    )
    assert remap == {}


def test_the_comparison_ignores_words_that_carry_no_meaning() -> None:
    assert _heading_key("Invoice Management") == _heading_key("Invoice handling")
    assert _heading_key("Invoice Management") != _heading_key("Refund Management")


# --------------------------------------------------------------------------------------------
# An outcome is grounded in the capabilities beneath it
# --------------------------------------------------------------------------------------------


def test_an_outcome_cites_the_capabilities_that_realise_it() -> None:
    """The step that writes an outcome sees titles, never files, so it cannot cite anything.

    Asked for a citation anyway it produced a plausible fabrication, validation caught every one,
    and the whole top tier was then deleted -- taking the parent of every capability with it.
    """
    outcome = _item("Recover money spent")
    outcome.evidence = [SourceLocation(file="invented.py", line_start=0)]
    brd = ProductBRD(
        business_requirements=[outcome],
        functional_requirements=[
            _item("Submit a claim", parent="Recover money spent",
                  evidence=[_at("app/claims.py", 10)], graph_path=["app.claims.submit"],
                  confidence=0.7),
            _item("Approve a claim", parent="Recover money spent",
                  evidence=[_at("app/review.py", 40)], graph_path=["app.review.approve"],
                  confidence=0.9),
        ],
    )
    grounded, ungrounded = ground_outcomes(brd)

    assert (grounded, ungrounded) == (1, 0)
    cited = {location.file for location in outcome.evidence}
    assert cited == {"app/claims.py", "app/review.py"}
    assert "invented.py" not in cited
    assert set(outcome.graph_path) == {"app.claims.submit", "app.review.approve"}
    # No more certain than the least certain capability holding it up.
    assert outcome.confidence == pytest.approx(0.7)


def test_grounding_an_outcome_twice_gives_the_same_answer() -> None:
    """It is re-run whenever the set changes, so it has to be idempotent."""
    brd = ProductBRD(
        business_requirements=[_item("An outcome")],
        functional_requirements=[
            _item("A capability", parent="An outcome",
                  evidence=[_at("app/one.py", 10)], confidence=0.6),
        ],
    )
    ground_outcomes(brd)
    first = (
        [location.model_dump() for location in brd.business_requirements[0].evidence],
        brd.business_requirements[0].confidence,
    )
    ground_outcomes(brd)
    assert first == (
        [location.model_dump() for location in brd.business_requirements[0].evidence],
        brd.business_requirements[0].confidence,
    )


def test_an_outcome_with_nothing_under_it_cites_nothing() -> None:
    """The honest answer. Borrowing a citation would hide exactly the case that must not stand."""
    outcome = _item("An outcome nothing realises")
    outcome.evidence = [SourceLocation(file="app/one.py", line_start=1)]
    brd = ProductBRD(business_requirements=[outcome])
    grounded, ungrounded = ground_outcomes(brd)
    assert (grounded, ungrounded) == (0, 1)
    assert outcome.evidence == []


# --------------------------------------------------------------------------------------------
# The hierarchy the evidence can express
# --------------------------------------------------------------------------------------------


def test_a_capability_may_decompose_into_capabilities() -> None:
    """Products decompose that way, and forbidding it forced two wrong answers.

    Either the stages were published flat, hiding a structure the evidence established, or an
    outcome was invented to hold them. It also meant that demoting an unsupported outcome to a
    capability orphaned everything beneath it.
    """
    assert "functional" in permitted_parents("functional")
    assert "refines" in PARENT_RULES["functional"][1]


def test_nothing_is_its_own_parent() -> None:
    """A one-item cycle: every parent exists, nothing dangles, and any upward walk hangs."""
    item = _item("A capability", parent="A capability", parent_relation="refines")
    ProductBRD(functional_requirements=[item]).assign_ids()
    assert item.parent is None
    assert item.parent_id == ""


# --------------------------------------------------------------------------------------------
# A rejection over wording must not cost a document its content
# --------------------------------------------------------------------------------------------


def test_a_removal_that_breaks_the_sentence_is_not_made() -> None:
    """A requirement nobody can read is worse than one that names a mechanism.

    Removing a word the sentence was built around leaves ungrammatical English -- "when an occurs
    during indexing", "check if ' ' is present" -- and both reached a published document. The
    leak is still recorded against the item; only the rewrite is withheld.
    """
    from agentic.capabilities.leakage import strip_leakage

    quoted = (
        "When processing a chunk, the system shall check if 'original_content' is present in "
        "the metadata, so that only valid chunks proceed."
    )
    assert strip_leakage(quoted, ["original_content"]) == quoted

    stranded = (
        "When an error occurs during indexing, the system shall return a failure message "
        "naming the error, so that the person is told what went wrong."
    )
    assert strip_leakage(stranded, ["error"]) == stranded


def test_a_removal_that_leaves_a_sentence_is_made() -> None:
    """The case the pass exists for: the mechanism goes and the requirement survives intact."""
    from agentic.capabilities.leakage import strip_leakage

    rewritten = strip_leakage(
        "A client shall create a new order by POST /orders so that it enters the queue for "
        "fulfilment.",
        ["POST /orders"],
    )
    assert "POST" not in rewritten and "/orders" not in rewritten
    assert rewritten.startswith("A client shall create a new order so that it enters the queue")


def test_a_repair_may_not_change_where_a_requirement_sits() -> None:
    """A repair is asked for wording, and everything else is settled by stages that saw the set.

    Left to the model these came back altered -- a rule returned with the grounds of an outcome,
    which is a contradiction no rewording can fix, so the gate rejected it again on every pass
    until the budget ran out and it was deleted. A repair pass must not be able to fail a claim it
    was asked to save.
    """
    import inspect

    from agentic.capabilities.claims import apply_repairs

    source = inspect.getsource(apply_repairs)
    for field in (
        "replacement.abstraction_basis = item.abstraction_basis",
        "replacement.requirement_type = item.requirement_type",
        "replacement.parent = item.parent",
        "replacement.area = item.area",
        "replacement.title = item.title",
    ):
        assert field in source, field


def test_a_requirement_may_use_the_plainest_word_for_what_someone_does() -> None:
    """A codebase names its functions after ordinary verbs, and a requirement needs those verbs.

    Matching a statement against every symbol in the graph catches project vocabulary the generic
    patterns miss. It also caught "update", "approve" and "archive" -- the only words for what the
    person is doing -- and the repair, told which word to remove, removed the verb the sentence
    was built around.
    """
    from agentic.capabilities.jargon import find_jargon

    symbols = {"app.api.rounds.update", "app.api.rounds.approve", "app.services.archive"}
    statement = (
        "An authorized user shall be able to update the next action for a candidate, so that "
        "the recorded action reflects the latest decision."
    )
    assert find_jargon(statement, symbols) == []


def test_a_distinctive_symbol_name_is_still_caught() -> None:
    """The check still does its job: an invented name is not ordinary English."""
    from agentic.capabilities.jargon import find_jargon

    findings = find_jargon(
        "A clerk shall be able to post an entry to the Ledger so that the balance moves.",
        {"app.books.Ledger"},
    )
    assert any(finding.term == "Ledger" for finding in findings)


def test_an_outcome_that_names_a_capability_below_it_is_not_published() -> None:
    """The step that writes the top tier is shown capability titles and asked what they add up to.

    Under a large digest it reliably hands some of them back. Published, that is one requirement
    appearing twice under one name, in two sections, in a hierarchy where a thing realises itself.
    Judged on the title, so no threshold and no interpretation are involved -- and nothing is
    lost, because the capability itself is published in full with its own evidence.
    """
    import tempfile
    from pathlib import Path

    from agentic.capabilities.assemble import assemble_requirements
    from agentic.schemas.brd import BRDFragment, OutcomeBinding, TopTier
    from agentic.store import ArtifactStore

    workspace = Path(tempfile.mkdtemp())
    store = ArtifactStore(workspace)
    capability = _item(
        "Retrieve audit logs",
        "An administrator shall be able to retrieve audit logs, so that activity can be reviewed.",
        evidence=[_at("app/audit.py", 20)],
        graph_path=["app.audit.get_logs"],
    )
    fragment = BRDFragment(
        area_code="AUD", area_name="Audit", functional_requirements=[capability]
    )
    top = TopTier(
        business_requirements=[
            _item("Retrieve audit logs", "The product exists to retrieve audit logs."),
            _item("Keep an accountable record of every decision"),
        ],
        bindings=[
            OutcomeBinding(
                business_requirement="Keep an accountable record of every decision",
                realised_by=["Retrieve audit logs"],
            )
        ],
    )

    class _Ctx:
        settings = Settings()

        def __init__(self) -> None:
            self.state = {
                "artifacts": {"top_tier": store.save("top_tier", top)},
                "fragments": [{**fragment.model_dump(), "boundary": "GET /audit"}],
            }
            self.step = None
            self.store = store

        def load(self, name, model=None):
            return store.load_model(self.state["artifacts"][name], model)

    result = assemble_requirements(_Ctx())
    brd = result.artifacts["brd"]

    titles = [item.title for item in brd.business_requirements]
    assert "Retrieve audit logs" not in titles, "an outcome may not be a capability restated"
    assert "Keep an accountable record of every decision" in titles
    assert result.metrics["outcomes_dropped_as_restated_capabilities"] == 1
    # The capability itself survives untouched.
    assert [item.title for item in brd.functional_requirements] == ["Retrieve audit logs"]


def test_the_entailment_check_addresses_each_claim_separately() -> None:
    """Identifiers are stamped after consolidation, so at this point every item's id is empty.

    Sending them as the batch key meant every verdict came back under the same key and one claim
    the model disagreed with rejected the whole batch: 31 claims checked, 0 reported supported,
    each carrying a rejection reason that belonged to a different requirement. Nothing caught it,
    because the one place this check had ever run assigns identifiers up front.
    """
    from agentic.capabilities.correspondence import (
        EntailmentVerdict,
        EntailmentVerdicts,
        _entail,
    )

    items = [
        _item("First", "A user shall be able to do the first thing so that it is recorded."),
        _item("Second", "A user shall be able to do the second thing so that it is recorded."),
        _item("Third", "A user shall be able to do the third thing so that it is recorded."),
    ]
    assert {item.id for item in items} == {""}, "ids are not stamped yet, which is the point"

    seen: dict = {}

    class _Model:
        def with_structured_output(self, schema):
            return self

        def invoke(self, messages):
            import json

            seen["payload"] = json.loads(messages[-1][1])
            # Only the middle one is unsupported.
            return EntailmentVerdicts(
                verdicts=[
                    EntailmentVerdict(id="0", supported=True),
                    EntailmentVerdict(id="1", supported=False, reason="The code does not do this."),
                    EntailmentVerdict(id="2", supported=True),
                ]
            )

    import agentic.llm.steps as steps

    original = steps.build_chat_model
    steps.build_chat_model = lambda settings: _Model()
    try:
        verdicts = _entail(
            type("Ctx", (), {"settings": Settings()})(), [(item, []) for item in items]
        )
    finally:
        steps.build_chat_model = original

    assert {entry["id"] for entry in seen["payload"]["items"]} == {"0", "1", "2"}
    assert verdicts[0][0] is True
    assert verdicts[1] == (False, "The code does not do this.")
    assert verdicts[2][0] is True


def test_a_file_extension_is_not_a_gap_in_the_sentence() -> None:
    """The check meant to catch damage was doing the damage.

    A bare " ." matched inside "a .env file at startup" and rejected a perfectly written
    requirement for a transformation gap that was not there. What makes a full stop an artefact
    is having nothing after it.
    """
    from agentic.capabilities.resolution import _degenerate

    intact = _item(
        "Load settings",
        "The system shall read its settings from a .env file at startup, so that the values are "
        "available to every part of the product.",
    )
    assert _degenerate(intact) is None

    broken = _item(
        "Damaged",
        "A user shall be able to submit a claim , so that it enters the queue for review.",
    )
    assert _degenerate(broken) is not None


def test_a_tie_between_identical_readings_merges_rather_than_withholding_both() -> None:
    """Members of a conflict group already share a citation and a verb -- that is how it formed.

    Where their wording agrees too, they are one capability written several times by workers who
    each saw one boundary into it. Withholding all of them over a tie in a four-part support score
    removed seventy grounded capabilities from one repository.
    """
    from agentic.capabilities.resolution import resolve_candidates
    from agentic.schemas.core import KnowledgeGraph
    from agentic.store import ArtifactStore
    import tempfile

    statement = (
        "A recruiter shall be able to retrieve a candidate's notes by supplying the candidate's "
        "identifier, so that the notes are shown alongside the profile."
    )
    twins = [
        _item(
            f"Retrieve notes {n}",
            statement + (" " * n),
            cluster_id="CAP-100",
            cluster_conflict=True,
            evidence=[_at("app/notes.py", 30)],
        )
        for n in range(3)
    ]

    workspace = Path(tempfile.mkdtemp())
    store = ArtifactStore(workspace)
    paths = {
        "brd": store.save("brd", ProductBRD(functional_requirements=twins)),
        "graph": store.save("graph", KnowledgeGraph(repository_name="r")),
    }

    class _Ctx:
        settings = Settings(correspondence_entailment=False)

        def __init__(self) -> None:
            self.state = {"artifacts": paths}
            self.step = None
            self.store = store

        def load(self, name, model=None):
            return store.load_model(paths[name], model)

    result = resolve_candidates(_Ctx())
    brd = result.artifacts["brd"]
    moved = result.artifacts["unresolved_candidates"]["candidates"]

    assert len(brd.functional_requirements) == 1, "one capability, not three and not none"
    survivor = brd.functional_requirements[0]
    assert len(survivor.superseded) == 2, "and it names what it absorbed"
    assert {entry["disposition"] for entry in moved} == {"merge"}

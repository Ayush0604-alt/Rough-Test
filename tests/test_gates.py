"""Each gate check must fire on its own, on evidence crafted to trip exactly that check."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.conftest import EXTRACTION_STEPS
from agentic.capabilities.graph_ops import stable_id
from agentic.schemas.brd import BRDItem, ProductBRD
from agentic.schemas.core import (
    Entity,
    KnowledgeGraph,
    Relationship,
    Severity,
    SourceLocation,
)
from agentic.store import ArtifactStore


@pytest.fixture
def extracted(runner, state):
    for step in EXTRACTION_STEPS:
        runner(step)
    return state


def _codes(report) -> set[str]:
    return {issue.code for issue in report.issues}


def _save(state, name: str, value) -> None:
    store = ArtifactStore(Path(state["run_dir"]))
    state["artifacts"][name] = store.save(name, value)


def _load_graph(state) -> KnowledgeGraph:
    store = ArtifactStore(Path(state["run_dir"]))
    return store.load_model(state["artifacts"]["graph"], KnowledgeGraph)


# --------------------------------------------------------------------------------------------
# Gate 1
# --------------------------------------------------------------------------------------------


def test_clean_graph_passes_gate_one(runner, extracted) -> None:
    report = runner("gate_graph").gate
    assert report.valid, report.error_messages()


def test_a_dangling_relationship_is_refused_by_the_adapter_not_the_gate() -> None:
    """The check moved rather than disappearing.

    One adapter now mints every id and builds every edge from its own id map, so a dangling edge
    is a defect in that adapter and not a finding about the repository. It raises there, which
    stops the run at the step that caused it instead of reporting it two steps later.
    """
    from agentic.capabilities.graphify import AdapterError, _assert_structurally_sound

    graph = KnowledgeGraph(
        repository_name="t",
        entities=[
            Entity(id="a", kind="FUNCTION", name="a", qualified_name="a.py::a"),
        ],
        relationships=[
            Relationship(id="rel:orphaned", source="a", target="missing", kind="CALLS")
        ],
    )
    with pytest.raises(AdapterError, match="dangling"):
        _assert_structurally_sound(graph)


def test_orphan_boundary_is_an_error(runner, extracted) -> None:
    """Any boundary with nothing exposing it, not only a route: a scheduled job or a subcommand
    with no handler behind it produces a single-node packet exactly as an endpoint would."""
    graph = _load_graph(extracted)
    graph.entities.append(
        Entity(
            id=stable_id("endpoint", "ghost"), kind="ENDPOINT", name="GET /ghost",
            qualified_name="GET /ghost",
            location=SourceLocation(file="app/main.py", line_start=1),
            attributes={"method": "GET", "path": "/ghost"},
        )
    )
    _save(extracted, "graph", graph)
    report = runner("gate_graph").gate
    assert not report.valid
    assert "ORPHAN_BOUNDARY" in _codes(report)


def test_entity_citing_a_missing_file_is_an_error(runner, extracted) -> None:
    graph = _load_graph(extracted)
    graph.entities.append(
        Entity(
            id="function:invented", kind="FUNCTION", name="ghost",
            qualified_name="app.ghost.ghost",
            location=SourceLocation(file="app/ghost.py", line_start=3, symbol="ghost"),
        )
    )
    _save(extracted, "graph", graph)
    report = runner("gate_graph").gate
    assert not report.valid
    assert "UNRESOLVABLE_LOCATION" in _codes(report)


def test_no_source_at_all_still_fails_closed(runner, state, run_dir) -> None:
    """The failure mode this gate exists for: nothing readable, so nothing can be said."""
    for path in (run_dir / "source").rglob("*"):
        if path.is_file():
            path.unlink()
    (run_dir / "source" / "notes.bin").write_bytes(b"\x00\x01\x02")
    result = runner("ingest")
    assert result.halt_reason is not None


def test_a_repository_of_declarations_is_no_longer_refused(runner, state, run_dir) -> None:
    """Refusing it was a statement about the extractors, not about the evidence.

    A repository with no Python used to halt at ingestion, which meant a schema, a contract, or a
    documented limit could never be read. Extraction now covers 36 languages, so SQL is ordinary
    parseable source and the run simply continues.
    """
    for path in (run_dir / "source").rglob("*.py"):
        path.unlink()
    (run_dir / "source" / "README.md").write_text(
        "# Ordering\n\nAn order must never exceed the configured per-customer limit.\n",
        encoding="utf-8",
    )
    result = runner("ingest")
    assert result.halt_reason is None
    assert result.metrics["parseable_files"] >= 1


# --------------------------------------------------------------------------------------------
# Gate 2
# --------------------------------------------------------------------------------------------


GOOD_STATEMENT = "Staff can record a new supplier bill and have it retained for later retrieval."


def _brd(**overrides) -> ProductBRD:
    requirement = BRDItem(
        title="Record supplier bills",
        statement=GOOD_STATEMENT,
        rationale="An externally reachable operation accepts and stores a bill.",
        confidence=0.9,
        evidence=[
            SourceLocation(
                file="app/main.py", line_start=30, line_end=37, symbol="create_invoice"
            )
        ],
        graph_path=["app/main.py::create_invoice()", "POST /invoices"],
    )
    capability = BRDItem(
        title="Bill submission",
        statement="A submitted bill is accepted only when its value is within the approved "
                  "ceiling.",
        confidence=0.85,
        evidence=[SourceLocation(file="app/main.py", line_start=31, symbol="create_invoice")],
        graph_path=["POST /invoices"],
        parent="Record supplier bills",
    )
    brd = ProductBRD(
        product_purpose=BRDItem(
            title="Purpose",
            statement="The product lets an organisation capture supplier bills and retrieve "
                      "them later.",
            confidence=0.9,
        ),
        business_requirements=[requirement],
        functional_requirements=[capability],
    )
    for key, value in overrides.items():
        setattr(brd, key, value)
    return brd.assign_ids()


def _validate(runner, state, brd: ProductBRD):
    _save(state, "brd", brd)
    return runner("gate_requirements").gate


def test_well_grounded_requirements_pass_gate_two(runner, extracted) -> None:
    report = _validate(runner, extracted, _brd())
    assert report.valid, report.error_messages()


def test_evidence_citing_a_nonexistent_file_is_rejected(runner, extracted) -> None:
    brd = _brd()
    brd.business_requirements[0].evidence = [
        SourceLocation(file="app/imaginary.py", line_start=10, symbol="create_invoice")
    ]
    report = _validate(runner, extracted, brd)
    assert not report.valid
    assert "INVALID_EVIDENCE_FILE" in _codes(report)


def test_fabricated_line_number_inside_a_real_file_is_caught(runner, extracted) -> None:
    """Checking only that the file exists is not enough — this is the gap that closes it."""
    brd = _brd()
    brd.business_requirements[0].evidence = [
        SourceLocation(file="app/main.py", line_start=9999, symbol="create_invoice")
    ]
    report = _validate(runner, extracted, brd)
    assert not report.valid
    assert "INVALID_EVIDENCE_LINE" in _codes(report)


def test_symbol_that_is_nowhere_near_the_cited_line_is_caught(runner, extracted) -> None:
    brd = _brd()
    brd.business_requirements[0].evidence = [
        SourceLocation(file="app/storage.py", line_start=1, symbol="create_invoice")
    ]
    report = _validate(runner, extracted, brd)
    assert not report.valid
    assert "EVIDENCE_SYMBOL_MISMATCH" in _codes(report)


def test_claim_traced_only_to_internal_processing_is_rejected(runner, extracted) -> None:
    brd = _brd()
    brd.business_requirements[0].graph_path = ["app/main.py::_unused_reconciliation_helper()"]
    brd.business_requirements[0].evidence = [
        SourceLocation(
            file="app/main.py", line_start=52, symbol="_unused_reconciliation_helper"
        )
    ]
    report = _validate(runner, extracted, brd)
    assert not report.valid
    assert "NO_INTERACTION_BOUNDARY" in _codes(report)


def test_graph_path_naming_no_real_node_is_rejected(runner, extracted) -> None:
    brd = _brd()
    brd.business_requirements[0].graph_path = ["app.invented.pipeline"]
    report = _validate(runner, extracted, brd)
    assert not report.valid
    assert "UNRESOLVED_GRAPH_PATH" in _codes(report)


def test_ordinary_conditional_is_not_accepted_as_a_business_rule(runner, extracted) -> None:
    brd = _brd()
    brd.business_rules = [
        BRDItem(
            title="Bill ceiling",
            statement="A bill above the approved ceiling is refused and the sender is told why.",
            confidence=0.8,
            evidence=[SourceLocation(file="app/main.py", line_start=30)],
            graph_path=["app/main.py::create_invoice()"],
            parent="Bill submission",
        )
    ]
    report = _validate(runner, extracted, brd.assign_ids())
    assert "RULE_WITHOUT_POLICY" in _codes(report)


def test_naming_a_parent_that_does_not_exist_is_reported_not_rejected(runner, extracted) -> None:
    """A dangling link is a defect in the hierarchy, not in the claim.

    Rejected as an error, it cost the child its place in the document — one bad claim taking a
    grounded one down with it. The link is cleared by the final structural pass instead, and the
    requirement is published unattached, which is what is actually true of it.
    """
    brd = _brd()
    brd.functional_requirements[0].parent = "An outcome that was never written"
    report = _validate(runner, extracted, brd.assign_ids())
    assert "UNRESOLVED_PARENT" in _codes(report)
    assert not [issue for issue in report.issues
                if issue.code == "UNRESOLVED_PARENT" and issue.severity == Severity.ERROR]


def test_a_capability_realising_nothing_is_a_warning_not_a_block(runner, extracted) -> None:
    """The claim is grounded; what is missing is the reader's view of why it matters."""
    brd = _brd()
    brd.functional_requirements[0].parent = None
    report = _validate(runner, extracted, brd.assign_ids())
    assert "FUNCTION_WITHOUT_BUSINESS_REQUIREMENT" in _codes(report)
    assert report.valid


def test_a_business_requirement_nothing_realises_is_a_warning(runner, extracted) -> None:
    brd = _brd(functional_requirements=[])
    report = _validate(runner, extracted, brd.assign_ids())
    assert "BUSINESS_REQUIREMENT_WITHOUT_FUNCTION" in _codes(report)
    assert report.valid


def test_an_outcome_that_is_its_one_capability_reworded_is_reported(runner, extracted) -> None:
    """The failure the three-tier model exists to make visible."""
    brd = _brd()
    shared = [
        SourceLocation(
            file="app/main.py", line_start=30, line_end=37, symbol="create_invoice"
        )
    ]
    brd.business_requirements[0].evidence = list(shared)
    brd.functional_requirements[0].evidence = list(shared)
    brd.functional_requirements[0].statement = (
        "Staff can record a new supplier bill and have it retained for later retrieval."
    )
    brd.functional_requirements[0].graph_path = ["app/main.py::create_invoice()", "POST /invoices"]
    report = _validate(runner, extracted, brd.assign_ids())
    assert "BUSINESS_REQUIREMENT_RESTATES_FUNCTION" in _codes(report)


def test_an_outcome_standing_over_its_capability_is_not_reported(runner, extracted) -> None:
    """Citing the same lines at both tiers is what grounds the outcome, and must stay legal."""
    brd = _brd()
    brd.functional_requirements[0].evidence = list(brd.business_requirements[0].evidence)
    report = _validate(runner, extracted, brd.assign_ids())
    assert "BUSINESS_REQUIREMENT_RESTATES_FUNCTION" not in _codes(report)


def test_duplicate_statement_is_a_warning_not_a_block(runner, extracted) -> None:
    brd = _brd()
    brd.functional_requirements[0].statement = GOOD_STATEMENT
    report = _validate(runner, extracted, brd)
    assert "DUPLICATE_STATEMENT" in _codes(report)
    assert report.valid


def test_absent_purpose_warns_so_the_document_must_say_so(runner, extracted) -> None:
    brd = _brd()
    brd.product_purpose = None
    report = _validate(runner, extracted, brd)
    assert "NO_PRODUCT_PURPOSE" in _codes(report)
    assert report.valid


# --------------------------------------------------------------------------------------------
# Quarantine
# --------------------------------------------------------------------------------------------


def test_quarantine_drops_rejected_items_and_orphaned_references(runner, extracted) -> None:
    """A capability that cannot be grounded goes, and the outcome above it stops citing it."""
    brd = _brd()
    brd.functional_requirements[0].evidence = [
        SourceLocation(file="app/imaginary.py", line_start=10)
    ]
    brd.functional_requirements[0].graph_path = ["app.imaginary.predict"]
    _validate(runner, extracted, brd)

    result = runner("quarantine")
    survivor = result.artifacts["brd"]
    assert not survivor.functional_requirements
    assert result.metrics["quarantined"] >= 1
    # And the outcome goes with it. Its citation was derived from the capability that was just
    # removed, so there is nothing left to evidence it and no capability a reader could follow it
    # down to: publishing it would state an objective the document cannot show being delivered.
    assert not survivor.business_requirements
    assert any("every capability realising this outcome" in message
               for message in result.messages)


def test_an_outcome_is_not_deleted_for_a_citation_it_inherited(runner, extracted) -> None:
    """An outcome's citation is derived from its capabilities, so a fault in it is theirs.

    Deleting the outcome for it removed a whole level of the document, and orphaned every other
    capability under it, because one child was wrong.
    """
    brd = _brd()
    brd.business_requirements[0].evidence = [
        SourceLocation(file="app/imaginary.py", line_start=10)
    ]
    _validate(runner, extracted, brd)

    survivor = runner("quarantine").artifacts["brd"]
    assert survivor.business_requirements, "the outcome must survive a citation it did not author"
    # Re-derived from the capability that stands under it, so the bad citation is gone.
    cited = {location.file for location in survivor.business_requirements[0].evidence}
    assert "app/imaginary.py" not in cited
    assert survivor.functional_requirements[0].parent


def test_technical_vocabulary_in_an_acceptance_criterion_is_rejected(runner, extracted) -> None:
    """The statement is clean; the criterion under it is not. A reviewer reads both."""
    brd = _brd()
    brd.business_requirements[0].acceptance_criteria = [
        "The bill is retained and can be retrieved afterwards.",
        "The record is written by the storage handler defined in the REST endpoint module.",
    ]
    report = _validate(runner, extracted, brd)
    assert not report.valid
    assert "CRITERION_VOCABULARY" in _codes(report)


def test_clean_acceptance_criteria_pass_gate_two(runner, extracted) -> None:
    brd = _brd()
    brd.business_requirements[0].acceptance_criteria = [
        "A bill recorded today can be retrieved unchanged tomorrow.",
        "A bill above the approved ceiling is refused, and the person is told why.",
    ]
    report = _validate(runner, extracted, brd)
    assert report.valid, report.error_messages()


def test_quarantine_strips_a_rejected_criterion_but_keeps_the_requirement(
    runner, extracted
) -> None:
    """A wording failure in a criterion must not cost the whole requirement its place."""
    bad = "The record is written by the storage handler defined in the REST endpoint module."
    brd = _brd()
    brd.business_requirements[0].acceptance_criteria = [
        "The bill is retained and can be retrieved afterwards.",
        bad,
    ]
    _validate(runner, extracted, brd)
    result = runner("quarantine")
    kept = ArtifactStore(Path(extracted["run_dir"])).load_model(
        extracted["artifacts"]["brd"], ProductBRD
    )
    assert len(kept.business_requirements) == 1, "the requirement itself was grounded"
    assert kept.business_requirements[0].acceptance_criteria == [
        "The bill is retained and can be retrieved afterwards."
    ]
    assert result.metrics["criteria_stripped"] == 1

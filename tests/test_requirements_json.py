"""The published requirements set must match the contract downstream tools already read.

The shape here is not invented: it is the format the non-agentic generator produces, and the one
a reviewer, a traceability tool, and a test-derivation step all address items by. A requirements
document that only exists as prose sections cannot be pointed at item by item.
"""

from __future__ import annotations

import json

import pytest

from agentic.capabilities.claims import _drop_restatements, _file_by_tier, _link_the_chain
from agentic.capabilities.render import build_requirement_set, render_requirements_json
from agentic.schemas.brd import TIER_PREFIXES, AreaLabel, BRDItem, ProductBRD
from agentic.schemas.core import Entity, KnowledgeGraph, Relationship, SourceLocation

# Every key the reference format carries, in the order it publishes them.
REFERENCE_KEYS = [
    "id", "type", "parent_id", "parent_relation", "abstraction_basis", "title", "statement",
    "rationale", "persona", "business_value",
    "acceptance_criteria", "confidence", "status", "evidence", "graph_path", "assumptions",
    "open_questions", "generator",
    # Appended by later work. New keys go on the end so a consumer reading by position is
    # unaffected, which is what "append-only" has to mean in practice.
    "superseded", "model_confidence",
    # The part of the product an item belongs to, and that area's heading. Every item has carried
    # an area since assembly; only functional ids encoded it (`FR-CAND-01`), so every system
    # requirement and every rule reached a consumer with its area dropped.
    "area", "area_name",
    # Derived from the code's own defaults, not a stakeholder ranking.
    "priority",
]


def _item(title: str, statement: str, **kwargs) -> BRDItem:
    return BRDItem(
        title=title,
        statement=statement,
        confidence=kwargs.pop("confidence", 1.0),
        **kwargs,
    )


def _at(file: str, start: int, end: int, symbol: str | None = None) -> SourceLocation:
    return SourceLocation(file=file, line_start=start, line_end=end, symbol=symbol)


def test_every_published_item_carries_the_reference_contract() -> None:
    brd = ProductBRD(
        business_requirements=[
            _item(
                "Upload and Index PDF Documents",
                "The system shall allow users to upload biomedical PDF documents.",
                rationale="Supported by the indexing entry point.",
                persona="Researcher",
                business_value="Uploaded material becomes searchable.",
                acceptance_criteria=["A PDF is accepted and becomes available to questions."],
                evidence=[_at("app.py", 35, 58, "app.index_pdf")],
                graph_path=["app.index_pdf", "embed_store.build_index"],
                assumptions=["Files are readable."],
                open_questions=["What size limit applies?"],
            )
        ]
    ).assign_ids()
    published = build_requirement_set(brd, "llm:azure/gpt-4o-mini")

    assert len(published) == 1
    assert list(published[0]) == REFERENCE_KEYS
    entry = published[0]
    assert entry["id"] == "BR-001"
    assert entry["type"] == "business"
    assert entry["parent_id"] is None, "a business requirement is the top of the chain"
    assert entry["status"] == "candidate"
    assert entry["generator"] == "llm:azure/gpt-4o-mini"
    assert entry["evidence"] == [
        {"file": "app.py", "line_start": 35, "line_end": 58, "symbol": "app.index_pdf"}
    ]
    assert entry["graph_path"] == ["app.index_pdf", "embed_store.build_index"]


def test_ids_are_numbered_per_tier() -> None:
    """A reader cites RULE-002; the number has to mean the second rule, not the second item."""
    brd = ProductBRD(
        business_requirements=[_item("A", "The system shall do A.")],
        functional_requirements=[
            _item("B", "The system shall do B."), _item("C", "The system shall do C.")
        ],
        business_rules=[_item("D", "The system shall enforce D.")],
    ).assign_ids()
    published = build_requirement_set(brd, "deterministic")
    assert [entry["id"] for entry in published] == ["BR-001", "FR-001", "FR-002", "RULE-001"]


def test_the_published_id_is_the_one_the_gate_stamped() -> None:
    """The two used to be computed separately, so a gate rejecting SCOPE-002 meant FR-002."""
    brd = ProductBRD(
        functional_requirements=[
            _item("B", "The system shall do B."), _item("C", "The system shall do C.")
        ]
    ).assign_ids()
    published = build_requirement_set(brd, "deterministic")
    assert [entry["id"] for entry in published] == [
        item.id for item in brd.functional_requirements
    ]


def test_a_declared_tier_outranks_the_section_it_arrived_in() -> None:
    brd = ProductBRD(
        functional_requirements=[
            _item("A rule", "The system shall reject a duplicate name.",
                  requirement_type="business_rule")
        ]
    )
    _file_by_tier(brd)
    brd.assign_ids()
    published = build_requirement_set(brd, "deterministic")
    assert published[0]["type"] == "business_rule"
    assert published[0]["id"] == "RULE-001"


PUBLISHED_TIERS = sorted(
    (tier, prefix) for tier, prefix in TIER_PREFIXES.items() if tier != "observation"
)


@pytest.mark.parametrize("declared, prefix", PUBLISHED_TIERS)
def test_every_tier_publishes_under_its_own_prefix(declared, prefix) -> None:
    brd = ProductBRD(
        functional_requirements=[
            _item("T", "The system shall do it.", requirement_type=declared)
        ]
    )
    _file_by_tier(brd)
    brd.assign_ids()
    assert build_requirement_set(brd, "deterministic")[0]["id"].startswith(prefix + "-")


@pytest.mark.parametrize(
    "declared, tier",
    [
        ("technical", "technical_constraint"),
        ("non_functional", "non_functional"),
        ("transition", "functional"),
        ("stakeholder", "business"),
        ("Business-Rule", "business_rule"),
    ],
)
def test_a_type_outside_the_three_tiers_is_folded_onto_one(declared, tier) -> None:
    """A grounded finding that named itself badly is re-labelled, never dropped over a label."""
    brd = ProductBRD(
        functional_requirements=[
            _item("T", "The system shall do it.", requirement_type=declared)
        ]
    )
    _file_by_tier(brd)
    brd.assign_ids()
    assert build_requirement_set(brd, "deterministic")[0]["type"] == tier


def test_an_implementation_detail_is_kept_but_never_published_as_a_requirement() -> None:
    """The point of the observation class: retained as evidence, absent from the requirement set."""
    brd = ProductBRD(
        functional_requirements=[
            _item("T", "The current code does it this way.",
                  requirement_type="implementation_detail")
        ]
    )
    _file_by_tier(brd)
    brd.assign_ids()

    assert brd.functional_requirements == []
    assert [item.id for item in brd.observations] == ["OBS-001"]
    assert brd.observations[0].requirement_type == "observation"
    # Absent from requirements.json, because a consumer treats that file as the requirement set.
    assert build_requirement_set(brd, "deterministic") == []


def test_an_unrecognised_type_falls_back_to_the_section() -> None:
    brd = ProductBRD(
        functional_requirements=[
            _item("T", "The system shall do it.", requirement_type="nonsense")
        ]
    ).assign_ids()
    assert build_requirement_set(brd, "deterministic")[0]["type"] == "functional"


def test_a_bare_module_name_is_not_published_as_a_symbol() -> None:
    """It is the file restated, and implies a precision the citation does not have."""
    brd = ProductBRD(
        functional_requirements=[
            _item("T", "The system shall do it.",
                  evidence=[SourceLocation(file="app.py", line_start=105, scope="app")])
        ]
    ).assign_ids()
    published = build_requirement_set(brd, "deterministic", module_names={"app"})
    assert published[0]["evidence"][0]["symbol"] is None

    # The enclosing *definition* is still worth publishing — that one resolves to something.
    brd.functional_requirements[0].evidence[0].scope = "app.index_pdf"
    published = build_requirement_set(brd, "deterministic", module_names={"app"})
    assert published[0]["evidence"][0]["symbol"] == "app.index_pdf"


def test_the_published_set_is_valid_json_and_a_list() -> None:
    brd = ProductBRD(functional_requirements=[_item("T", "The system shall do it.")])
    parsed = json.loads(render_requirements_json(brd, "deterministic"))
    assert isinstance(parsed, list) and len(parsed) == 1


# --------------------------------------------------------------------------------------------
# Filing and de-duplication, which decide what reaches the published set at all
# --------------------------------------------------------------------------------------------


def test_items_are_filed_under_the_tier_they_declare() -> None:
    """A model that files everything in one list still types each item correctly."""
    brd = ProductBRD(
        business_requirements=[
            _item("Outcome", "The system shall deliver an outcome.",
                  requirement_type="business"),
            _item("Stage", "The system shall assemble context.", requirement_type="functional"),
            _item("Rule", "The system shall reject duplicates.",
                  requirement_type="business_rule"),
        ]
    )
    _, moved = _file_by_tier(brd)

    assert moved == 2
    assert [item.title for item in brd.business_requirements] == ["Outcome"]
    assert [item.title for item in brd.functional_requirements] == ["Stage"]
    assert [item.title for item in brd.business_rules] == ["Rule"]


def test_an_item_with_no_declared_tier_stays_where_it_was() -> None:
    brd = ProductBRD(
        functional_requirements=[_item("Stage", "The system shall assemble context.")]
    )
    relabelled, moved = _file_by_tier(brd)
    assert moved == 0
    assert relabelled == 1, "the tier its section implies is written back explicitly"
    assert len(brd.functional_requirements) == 1
    assert brd.functional_requirements[0].requirement_type == "functional"


def test_claims_over_the_same_evidence_in_one_tier_collapse_to_one() -> None:
    """One error path reachable from three callers is one behaviour, not three findings."""
    evidence = [_at("app.py", 53, 55, "app.index_pdf")]
    path = ["app.index_pdf.error_handler@53"]
    brd = ProductBRD(
        functional_requirements=[
            _item("A", "The system shall report an upload failure.",
                  evidence=list(evidence), graph_path=list(path)),
            _item("B", "The system shall report an indexing failure.",
                  evidence=list(evidence), graph_path=list(path)),
            _item("C", "The system shall report a reindexing failure.",
                  evidence=list(evidence), graph_path=list(path)),
        ]
    )
    assert _drop_restatements(brd) == 2
    assert [item.title for item in brd.functional_requirements] == ["A"]


def test_a_business_requirement_and_the_capability_realising_it_may_share_evidence() -> None:
    """Shared evidence across tiers is what grounds the outcome, not duplication."""
    evidence = [_at("app.py", 35, 58, "app.index_pdf")]
    brd = ProductBRD(
        business_requirements=[
            _item("Upload and Index", "The system shall make uploaded material searchable.",
                  evidence=list(evidence), graph_path=["app.index_pdf"])
        ],
        functional_requirements=[
            _item("Document Upload", "The system shall accept a PDF and index its content.",
                  evidence=list(evidence), graph_path=["app.index_pdf"])
        ],
    )
    assert _drop_restatements(brd) == 0
    assert len(brd.business_requirements) == 1 and len(brd.functional_requirements) == 1


# --------------------------------------------------------------------------------------------
# The classification chain: business requirement -> functional requirement -> business rule
# --------------------------------------------------------------------------------------------


def _chained() -> ProductBRD:
    return ProductBRD(
        business_requirements=[
            _item("Searchable Library", "A researcher shall be able to question their library.",
                  graph_path=["app.index_pdf"])
        ],
        functional_requirements=[
            _item("Add A Document", "A researcher shall be able to add a document.",
                  parent="Searchable Library", graph_path=["app.index_pdf"])
        ],
        business_rules=[
            _item("Reject A Duplicate", "When a document is already held, the system shall keep "
                  "the existing copy.",
                  parent="Add A Document", graph_path=["app.index_pdf"])
        ],
    )


def test_a_parent_named_by_title_resolves_to_its_identifier() -> None:
    brd = _chained()
    by_title, inferred = _link_the_chain(brd)

    assert (by_title, inferred) == (2, 0)
    assert brd.functional_requirements[0].parent_id == "BR-001"
    assert brd.business_rules[0].parent_id == "FR-001"


def test_a_parent_title_resolves_despite_casing_and_punctuation() -> None:
    """The model reproduces its own titles loosely; none of those are a different requirement."""
    brd = _chained()
    brd.functional_requirements[0].parent = "  searchable library.  "
    _link_the_chain(brd)
    assert brd.functional_requirements[0].parent_id == "BR-001"


def test_an_unresolvable_parent_is_left_unattached_not_guessed() -> None:
    """Attaching by shared code produced a hierarchy of which function calls which.

    That is a fact the traceability matrix already carries and it says nothing about whether one
    claim refines another, so the inference is gone. Semantic validation happens in
    `consolidate_requirements`; by the time this runs a surviving link has already been checked.
    """
    brd = _chained()
    brd.functional_requirements[0].parent = "An outcome nobody wrote"
    _, inferred = _link_the_chain(brd)

    assert inferred == 0
    assert brd.functional_requirements[0].parent_id == ""


def test_an_item_sharing_no_evidence_is_left_unattached() -> None:
    """Guessing here would manufacture a hierarchy the evidence does not show."""
    brd = _chained()
    brd.functional_requirements[0].parent = None
    brd.functional_requirements[0].graph_path = ["unrelated.module.helper"]
    brd.functional_requirements[0].evidence = [_at("other.py", 1, 2)]
    _link_the_chain(brd)

    assert brd.functional_requirements[0].parent_id == ""
    assert brd.orphans_in("functional_requirements") == [brd.functional_requirements[0]]


def test_the_chain_is_published_as_data() -> None:
    """A consumer rebuilds the hierarchy from the flat list without parsing the document."""
    brd = _chained()
    _link_the_chain(brd)
    published = {entry["id"]: entry["parent_id"] for entry in
                 build_requirement_set(brd, "deterministic")}
    assert published == {"BR-001": None, "FR-001": "BR-001", "RULE-001": "FR-001"}


def test_children_are_found_by_resolved_identifier() -> None:
    brd = _chained()
    _link_the_chain(brd)
    assert [item.id for item in brd.children_of("BR-001", "functional_requirements")] == ["FR-001"]
    assert [item.id for item in brd.children_of("FR-001", "business_rules")] == ["RULE-001"]


def test_everything_is_an_orphan_when_the_tier_above_is_empty() -> None:
    brd = ProductBRD(
        functional_requirements=[_item("Add A Document", "A researcher shall add a document.")]
    ).assign_ids()
    assert brd.orphans_in("functional_requirements") == brd.functional_requirements


# --------------------------------------------------------------------------------------------
# Typing that the graph settles, so it does not move between runs
# --------------------------------------------------------------------------------------------


def _graph_with_a_rule() -> KnowledgeGraph:
    """A graph carrying a rule node and the boundary it constrains.

    Hand-built rather than extracted. `_tier_from_the_graph` is live code that still types a claim
    from what it traces to, but no extractor currently produces a `BUSINESS_RULE`: Graphify models
    structure, not the conditionals the previous Python-only extractor read as policy. Testing the
    typing against a fixture keeps it under test without asserting an extraction capability the
    workflow no longer has.
    """
    handler = Entity(
        id="fn", kind="FUNCTION", name="create_invoice()",
        qualified_name="app/main.py::create_invoice()",
        location=SourceLocation(
            file="app/main.py", line_start=31, line_end=37, symbol="create_invoice"
        ),
    )
    boundary = Entity(
        id="ep", kind="ENDPOINT", name="ENDPOINT app/main.py::create_invoice()",
        qualified_name="ENDPOINT app/main.py::create_invoice()",
        location=handler.location,
        attributes={"handler": handler.qualified_name},
    )
    rule = Entity(
        id="rule", kind="BUSINESS_RULE", name="rule@32",
        qualified_name="app/main.py::rule@32",
        location=SourceLocation(file="app/main.py", line_start=32, line_end=33),
        attributes={"expression": "invoice.amount > MAX_INVOICE_AMOUNT"},
    )
    return KnowledgeGraph(
        repository_name="sample_app",
        entities=[handler, boundary, rule],
        relationships=[
            Relationship(id="r1", source=handler.id, target=boundary.id, kind="EXPOSES"),
            Relationship(id="r2", source=handler.id, target=rule.id, kind="ENFORCES"),
        ],
    )


def test_a_claim_tracing_to_an_extracted_rule_is_typed_as_one() -> None:
    """The least stable judgement in the workflow, taken from the graph instead of the model."""
    from agentic.capabilities.claims import _tier_from_the_graph

    graph = _graph_with_a_rule()
    rule = next(e for e in graph.entities if e.kind == "BUSINESS_RULE")
    boundary = next(e for e in graph.entities if e.kind == "ENDPOINT")

    constrained = _item("A rule", "The system shall refuse an over-limit bill.",
                        requirement_type="functional",
                        graph_path=["app/main.py::create_invoice()", rule.qualified_name])
    capability = _item("A capability", "Users shall be able to record a bill.",
                       requirement_type="functional",
                       graph_path=[boundary.qualified_name, rule.qualified_name])
    untraced = _item("Something else", "The system shall do something else.",
                     requirement_type="functional", graph_path=["app/main.py::create_invoice()"])

    brd = ProductBRD(functional_requirements=[constrained, capability, untraced])
    assert _tier_from_the_graph(brd, graph) == 1

    # Reaching a rule makes it a rule; also reaching a boundary makes it the capability the rule
    # constrains, which is a different requirement.
    assert constrained.requirement_type == "business_rule"
    assert capability.requirement_type == "functional"
    assert untraced.requirement_type == "functional"


def test_graph_typing_leaves_an_already_correct_item_alone() -> None:
    from agentic.capabilities.claims import _tier_from_the_graph

    graph = _graph_with_a_rule()
    rule = next(e for e in graph.entities if e.kind == "BUSINESS_RULE")
    item = _item("A rule", "The system shall refuse an over-limit bill.",
                 requirement_type="business_rule", graph_path=[rule.qualified_name])

    brd = ProductBRD(business_rules=[item])
    assert _tier_from_the_graph(brd, graph) == 0
    assert item.requirement_type == "business_rule"


# --------------------------------------------------------------------------------------------
# Telling an outcome from its capability reworded
# --------------------------------------------------------------------------------------------

# The pairs the similarity threshold was measured against. The two duplicates are lifted verbatim
# from the reference output that motivated the three-tier model; the genuine pairs are an outcome
# standing over a capability, which must stay legal however much evidence they share. The margin
# between the two groups is narrow, so pinning both sides here is what stops a later tweak to the
# threshold from quietly reclassifying one as the other.
_RESTATEMENT_CASES = [
    (
        True,
        "The system must allow users to upload biomedical PDF documents, which are then indexed "
        "for future queries. This process ensures that the content of the documents is "
        "accessible for information retrieval.",
        "The product includes the capability for users to upload PDF documents, which are then "
        "processed and indexed for retrieval. This encompasses the entire workflow from file "
        "upload to indexing in the system.",
    ),
    (
        True,
        "The system must provide users with the ability to ask questions and receive answers "
        "based on the indexed content of the uploaded PDFs. This functionality is essential for "
        "enabling users to extract relevant information efficiently.",
        "The product allows for the generation of answers based on user queries against the "
        "indexed content of the uploaded PDFs. This includes retrieving relevant chunks of "
        "information and formulating responses.",
    ),
    (
        False,
        "A researcher shall be able to build a private, searchable body of their own literature "
        "and question it in ordinary language, so that they can find and cite specific passages "
        "without reading each document end to end.",
        "A researcher shall be able to add a document to their collection by supplying it, so "
        "that its contents become available to later questions.",
    ),
    (
        False,
        "A researcher shall be able to build a private, searchable body of their own literature "
        "and question it in ordinary language, so that they can find and cite specific passages "
        "without reading each document end to end.",
        "A researcher shall be able to ask a question in ordinary language, so that they receive "
        "an answer drawn only from the documents they have added, together with the passages it "
        "came from.",
    ),
    (
        False,
        "Staff can record a new supplier bill and have it retained for later retrieval.",
        "A submitted bill is accepted only when its value is within the approved ceiling.",
    ),
]


@pytest.mark.parametrize("restates, outcome, capability", _RESTATEMENT_CASES)
def test_an_outcome_is_told_from_its_capability_reworded(restates, outcome, capability) -> None:
    from agentic.capabilities.gates import _restates

    shared = [_at("app.py", 35, 58, "app.index_pdf")]
    parent = _item("Outcome", outcome, evidence=[loc.model_copy() for loc in shared])
    child = _item("Capability", capability, evidence=[loc.model_copy() for loc in shared])
    assert _restates(parent, child) is restates


def test_the_same_words_over_different_evidence_are_two_findings() -> None:
    """Shared wording alone is not duplication — the two claims rest on different code."""
    from agentic.capabilities.gates import _restates

    statement = _RESTATEMENT_CASES[0][1]
    parent = _item("Outcome", statement, evidence=[_at("app.py", 35, 58)])
    child = _item("Capability", statement, evidence=[_at("other.py", 1, 9)])
    assert _restates(parent, child) is False


def test_a_success_criterion_is_not_published_as_a_functional_requirement() -> None:
    """It is an observable condition on a requirement, not a tier of the classification model."""
    brd = ProductBRD(
        functional_requirements=[_item("Ask A Question", "A researcher shall ask a question.")],
        success_criteria=[
            _item("Grounded Answers", "An answer repeats only wording from the documents.",
                  parent="Ask A Question")
        ],
    ).assign_ids()
    published = {entry["id"]: entry["type"] for entry in
                 build_requirement_set(brd, "deterministic")}
    assert published == {"FR-001": "functional", "SC-001": "success_criterion"}


def test_a_folded_type_is_normalised_in_the_stored_field_too() -> None:
    """Publishing resolves aliases anyway; the artifact on disk must not keep the old vocabulary."""
    brd = ProductBRD(
        functional_requirements=[
            _item("T", "The system shall do it.", requirement_type="technical")
        ]
    )
    relabelled, moved = _file_by_tier(brd)
    assert relabelled == 1 and moved == 1
    assert brd.technical_constraints[0].requirement_type == "technical_constraint"


# ------------------------------------------------------------------------------------------------
# Area and priority reach the published set
#
# Both are held on every item from assembly onward, and neither used to be published. Area survived
# only as an accident of the functional id scheme (`FR-CAND-01`); `SYS-`, `RULE-`, `TC-` and the
# supplement families do not use that scheme, so on a measured run 102 of 190 published items
# arrived with no recoverable area at all. A consumer grouping the flat list by part of the product
# had to reconstruct from ids what the document already knew.
# ------------------------------------------------------------------------------------------------


def test_area_and_priority_are_published_for_every_tier() -> None:
    """Not only for functional requirements, whose ids happen to encode the area."""
    brd = ProductBRD(
        areas=[AreaLabel(code="CAND", name="Candidate Note Management")],
        business_requirements=[_item("Outcome", "The system shall deliver an outcome.")],
        functional_requirements=[
            _item("Stage", "A user shall be able to do a thing.", area="CAND", priority="S")
        ],
        system_requirements=[
            _item("Internal", "The system shall hold an invariant.", area="CAND")
        ],
        business_rules=[
            _item("Rule", "When a thing happens, the system shall refuse.", area="CAND")
        ],
    ).assign_ids()

    by_id = {entry["id"]: entry for entry in build_requirement_set(brd, "deterministic")}

    # The tier that never encoded its area in its id is the whole point of the change.
    assert by_id["SYS-001"]["area"] == "CAND"
    assert by_id["SYS-001"]["area_name"] == "Candidate Note Management"
    assert by_id["RULE-001"]["area"] == "CAND"
    assert by_id["RULE-001"]["area_name"] == "Candidate Note Management"

    assert by_id["FR-CAND-01"]["area"] == "CAND"
    assert by_id["FR-CAND-01"]["priority"] == "S"

    # A business requirement stands over whole areas rather than sitting in one.
    assert by_id["BR-001"]["area"] is None
    assert by_id["BR-001"]["area_name"] is None
    assert by_id["BR-001"]["priority"] == "M"


def test_an_area_with_no_published_heading_still_publishes_its_code() -> None:
    """A missing label is a gap in the heading table, not a reason to drop the grouping."""
    brd = ProductBRD(
        functional_requirements=[_item("Stage", "A user shall do a thing.", area="ZZ")]
    ).assign_ids()
    entry = build_requirement_set(brd, "deterministic")[0]
    assert entry["area"] == "ZZ"
    assert entry["area_name"] is None


def test_an_unrecognised_priority_is_normalised_rather_than_published() -> None:
    """`PRIORITIES` is the published vocabulary; assembly enforces it and so does the export."""
    brd = ProductBRD(
        functional_requirements=[
            _item("Stage", "A user shall do a thing.", area="CAND", priority="URGENT")
        ]
    ).assign_ids()
    assert build_requirement_set(brd, "deterministic")[0]["priority"] == "M"

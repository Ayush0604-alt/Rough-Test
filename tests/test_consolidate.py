"""Abstraction, deduplication, criteria extraction, and semantic hierarchy.

These are the behaviours that stop a requirements document being a transcript of the code. Every
case here is written in a neutral domain on purpose: nothing in the implementation may key off a
framework, a file name, a function name, or a subject area, so nothing in the tests may either.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from agentic.capabilities.consolidate import (
    MERGE_SIMILARITY,
    consolidate_requirements,
    _looks_like_a_success_condition,
)
from agentic.schemas.brd import BRDItem, ProductBRD
from agentic.config import Settings
from agentic.schemas.core import SourceLocation
from agentic.store import ArtifactStore


def _item(title, statement, basis=None, parent=None, relation=None, lines=(10, 20), trace=("a.b",)):
    return BRDItem(
        title=title,
        statement=statement,
        confidence=0.9,
        abstraction_basis=basis,
        parent=parent,
        parent_relation=relation,
        evidence=[SourceLocation(file="svc.py", line_start=lines[0], line_end=lines[1])],
        graph_path=list(trace),
    )


def _run(brd: ProductBRD, graph=None, **flags):
    run = Path(tempfile.mkdtemp())
    (run / "artifacts").mkdir(parents=True)
    store = ArtifactStore(run)
    paths = {"brd": store.save("brd", brd)}

    # Rule classification needs a graph to ask what a citation points at. Where a test supplies
    # none there is nothing to classify against, so the pass is off and the test exercises the
    # abstraction rules on their own -- which is what these tests are about.
    if graph is not None:
        paths["graph"] = store.save("graph", graph)

    class _Ctx:
        state = {"artifacts": paths, "run_dir": str(run)}
        settings = Settings(**{
            # Both are off in the shipped defaults after the rollback. These tests exist to
            # exercise the passes, so they ask for them explicitly rather than depending on
            # a default that deliberately no longer holds.
            "rule_classification": graph is not None,
            "dedup_pass": True,
            **flags,
        })

        def load(self, name, model=None):
            return store.load_model(paths[name], model)

    result = consolidate_requirements(_Ctx())
    return result.artifacts["brd"], result


# --------------------------------------------------------------------------------------------
# Abstraction: the basis decides the class
# --------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "basis, section",
    [
        ("business_outcome", "business_requirements"),
        ("external_capability", "functional_requirements"),
        ("internal_behaviour", "system_requirements"),
        ("domain_constraint", "business_rules"),
        ("platform_constraint", "technical_constraints"),
        ("quality_property", "non_functional_requirements"),
        ("implementation_detail", "observations"),
    ],
)
def test_the_declared_basis_decides_where_a_finding_lands(basis, section) -> None:
    """A worker may file anything anywhere; the grounds it gave are what count."""
    brd, _ = _run(
        ProductBRD(functional_requirements=[_item("T", "The system shall do a thing.", basis)])
    )
    assert [item.title for item in getattr(brd, section)] == ["T"]


def test_an_implementation_detail_is_never_promoted_to_a_capability() -> None:
    """The over-extraction failure, stated directly.

    A worker filed an internal step as a functional requirement but was honest about the grounds.
    The grounds win: nothing that exists only because this code is written this way is published
    as something the product must do.
    """
    brd, result = _run(
        ProductBRD(
            functional_requirements=[
                _item("Assemble A Payload",
                      "The system shall assemble a payload before writing it.",
                      "implementation_detail"),
            ]
        )
    )
    assert brd.functional_requirements == []
    assert len(brd.observations) == 1
    assert result.metrics["demoted_to_observation"] == 1


def test_a_demoted_finding_keeps_its_evidence() -> None:
    """Reverse-engineering evidence is never the thing to throw away."""
    brd, _ = _run(
        ProductBRD(
            functional_requirements=[
                _item("Assemble A Payload", "The system shall assemble a payload.",
                      "implementation_detail", lines=(41, 47)),
            ]
        )
    )
    kept = brd.observations[0]
    assert kept.evidence[0].line_start == 41
    assert kept.graph_path == ["a.b"]


def test_a_finding_with_no_declared_basis_is_given_one() -> None:
    """So the two classification fields always agree and the gate can check them."""
    brd, _ = _run(
        ProductBRD(functional_requirements=[_item("T", "A person shall be able to act.")])
    )
    item = brd.functional_requirements[0]
    assert item.abstraction_basis == "external_capability"
    assert item.requirement_type == "functional"


# --------------------------------------------------------------------------------------------
# Deduplication: the same assertion worded twice
# --------------------------------------------------------------------------------------------


def test_the_same_capability_worded_differently_is_merged() -> None:
    """Exact-string matching missed this, which is how overlapping requirements survived."""
    brd, result = _run(
        ProductBRD(
            functional_requirements=[
                _item("Submit A Claim",
                      "A claimant shall be able to submit a claim by supplying the amount and a "
                      "receipt, so that it enters the review queue.", "external_capability"),
                _item("Submit A Claim Again",
                      "A claimant shall be able to submit a claim by providing the amount and a "
                      "receipt so that it reaches the review queue.", "external_capability"),
            ]
        )
    )
    assert len(brd.functional_requirements) == 1
    assert result.metrics["duplicates_merged"] == 1


def test_two_capabilities_named_by_different_verbs_are_kept_apart() -> None:
    """A deliberate trade, and the safer side of it.

    "Submit" and "lodge" are synonyms here and merging them would be right. Telling that pair
    apart from "view" and "delete" needs to know what the words mean, and any list that encoded it
    would be a list about English, or about one domain. So the rule is the blunt one: a difference
    in the verb keeps two requirements apart.

    The cost is a duplicate that survives, which a reviewer can see and merge. The alternative cost
    was an entire CRUD surface collapsing into one requirement, which a reviewer cannot see at all
    because the evidence for the others goes with them.
    """
    brd, result = _run(
        ProductBRD(
            functional_requirements=[
                _item("Submit A Claim",
                      "A claimant shall be able to submit a claim by supplying the amount and a "
                      "receipt, so that it enters the review queue.", "external_capability"),
                _item("Lodge A Claim",
                      "A claimant shall be able to lodge a claim by providing the amount and a "
                      "receipt so that it reaches the review queue.", "external_capability"),
            ]
        )
    )
    assert len(brd.functional_requirements) == 2
    assert result.metrics["duplicates_merged"] == 0


def test_a_merge_keeps_the_fuller_wording_and_both_citations() -> None:
    """A later step cannot recover a detail an earlier one discarded."""
    short = _item("Act", "A person shall be able to act.", "external_capability", lines=(10, 20))
    full = _item("Act", "A person shall be able to act on a record they own, so that the change "
                        "is visible to everyone with access.", "external_capability", lines=(10, 30))
    full.acceptance_criteria = ["The change appears for a second viewer."]
    brd, _ = _run(ProductBRD(functional_requirements=[short, full]))

    kept = brd.functional_requirements[0]
    assert "so that the change" in kept.statement
    assert len(kept.evidence) == 2
    assert kept.acceptance_criteria == ["The change appears for a second viewer."]


def test_two_findings_over_different_code_are_never_merged() -> None:
    """Shared wording alone is not duplication; they must rest on the same code."""
    brd, _ = _run(
        ProductBRD(
            functional_requirements=[
                _item("A", "A person shall be able to act on a record.", "external_capability",
                      lines=(10, 20), trace=("a.one",)),
                _item("B", "A person shall be able to act on a record.", "external_capability",
                      lines=(90, 99), trace=("a.two",)),
            ]
        )
    )
    assert len(brd.functional_requirements) == 2


def test_an_outcome_and_the_capability_realising_it_are_not_merged() -> None:
    """They rest on the same lines by design; collapsing them deletes the outcome."""
    brd, _ = _run(
        ProductBRD(
            business_requirements=[
                _item("Settle A Claim", "A claimant shall be able to have a claim settled.",
                      "business_outcome")
            ],
            functional_requirements=[
                _item("Submit A Claim", "A claimant shall be able to submit a claim.",
                      "external_capability", "Settle A Claim", "refines")
            ],
        )
    )
    assert len(brd.business_requirements) == 1
    assert len(brd.functional_requirements) == 1


# --------------------------------------------------------------------------------------------
# Acceptance criteria
# --------------------------------------------------------------------------------------------


def test_a_success_condition_becomes_a_criterion_not_a_requirement() -> None:
    brd, result = _run(
        ProductBRD(
            functional_requirements=[
                _item("Submit A Claim", "A claimant shall be able to submit a claim.",
                      "external_capability"),
                _item("Submission Succeeds",
                      "The claim is validated and stored successfully when a claimant submits it.",
                      "external_capability", "Submit A Claim", "validates"),
            ]
        )
    )
    assert len(brd.functional_requirements) == 1
    assert brd.functional_requirements[0].acceptance_criteria == [
        "The claim is validated and stored successfully when a claimant submits it."
    ]
    assert result.metrics["criteria_extracted"] == 1


def test_a_criterion_carries_its_citation_onto_the_requirement() -> None:
    """Demoting a finding must not cost the evidence behind it."""
    brd, _ = _run(
        ProductBRD(
            functional_requirements=[
                _item("Act", "A person shall be able to act.", "external_capability",
                      lines=(10, 20)),
                _item("It Works", "The action completes successfully.", "external_capability",
                      "Act", "validates", lines=(55, 60)),
            ]
        )
    )
    spans = {location.line_start for location in brd.functional_requirements[0].evidence}
    assert spans == {10, 55}


@pytest.mark.parametrize(
    "statement, is_criterion",
    [
        ("The record is stored successfully.", True),
        ("The operation completes successfully.", True),
        ("The request is validated.", True),
        ("A person shall be able to withdraw a request.", False),
        ("When a limit is exceeded, the system shall refuse the request.", False),
    ],
)
def test_success_conditions_are_recognised_by_sentence_shape(statement, is_criterion) -> None:
    """Shape, not vocabulary: nothing here may depend on a domain's nouns."""
    assert _looks_like_a_success_condition(_item("T", statement)) is is_criterion


# --------------------------------------------------------------------------------------------
# Semantic hierarchy
# --------------------------------------------------------------------------------------------


def test_a_link_between_levels_that_cannot_relate_is_dropped() -> None:
    """A capability cannot hang from a rule, however much code they share."""
    brd, result = _run(
        ProductBRD(
            business_rules=[
                _item("Refuse An Over-Limit Claim",
                      "When a claim exceeds the ceiling, the system shall refuse it.",
                      "domain_constraint")
            ],
            functional_requirements=[
                _item("Submit A Claim", "A claimant shall be able to submit a claim.",
                      "external_capability", "Refuse An Over-Limit Claim", "refines")
            ],
        )
    )
    assert brd.functional_requirements[0].parent is None
    assert result.metrics["parent_links_dropped"] == 1


def test_a_relation_a_level_cannot_hold_is_dropped() -> None:
    """A rule constrains its parent; it does not refine it."""
    brd, result = _run(
        ProductBRD(
            functional_requirements=[
                _item("Submit A Claim", "A claimant shall be able to submit a claim.",
                      "external_capability")
            ],
            business_rules=[
                _item("Refuse An Over-Limit Claim",
                      "When a claim exceeds the ceiling, the system shall refuse it.",
                      "domain_constraint", "Submit A Claim", "refines")
            ],
        )
    )
    assert brd.business_rules[0].parent is None
    assert result.metrics["parent_links_dropped"] == 1


def test_a_permitted_link_with_no_stated_relation_is_given_the_only_one_available() -> None:
    brd, _ = _run(
        ProductBRD(
            functional_requirements=[
                _item("Submit A Claim", "A claimant shall be able to submit a claim.",
                      "external_capability")
            ],
            business_rules=[
                _item("Refuse An Over-Limit Claim",
                      "When a claim exceeds the ceiling, the system shall refuse it.",
                      "domain_constraint", "Submit A Claim", None)
            ],
        )
    )
    assert brd.business_rules[0].parent_relation == "constrains"


def test_a_parent_naming_nothing_is_cleared_rather_than_guessed() -> None:
    """Nothing here reaches for the nearest item sharing a call path."""
    brd, result = _run(
        ProductBRD(
            business_requirements=[
                _item("Settle A Claim", "A claimant shall be able to have a claim settled.",
                      "business_outcome")
            ],
            functional_requirements=[
                _item("Submit A Claim", "A claimant shall be able to submit a claim.",
                      "external_capability", "An outcome nobody wrote", "refines"),
            ],
        )
    )
    assert brd.functional_requirements[0].parent is None
    assert result.metrics["parent_links_dropped"] == 1


# --------------------------------------------------------------------------------------------
# Nothing is silently lost
# --------------------------------------------------------------------------------------------


def test_every_finding_is_accounted_for() -> None:
    """Each one is published, demoted, merged, or folded — never dropped without a count."""
    brd, result = _run(
        ProductBRD(
            business_requirements=[
                _item("Settle A Claim", "A claimant shall be able to have a claim settled.",
                      "business_outcome")
            ],
            functional_requirements=[
                _item("Submit A Claim", "A claimant shall be able to submit a claim by "
                      "supplying the amount, so that it enters the queue.",
                      "external_capability", "Settle A Claim", "refines"),
                _item("Lodge A Claim", "A claimant shall be able to lodge a claim by supplying "
                      "the amount so that it enters the queue.",
                      "external_capability", "Settle A Claim", "refines"),
                _item("It Worked", "The claim is stored successfully.",
                      "external_capability", "Submit A Claim", "validates"),
                _item("Assemble A Payload", "The system shall assemble a payload.",
                      "implementation_detail", "Submit A Claim", "evidences"),
            ],
        )
    )
    metrics = result.metrics
    assert metrics["findings_received"] == 5
    accounted = (
        metrics["requirements_published"]
        + metrics["observations_retained"]
        + metrics["duplicates_merged"]
        + metrics["criteria_extracted"]
    )
    assert accounted == 5


def test_the_merge_threshold_is_a_measured_constant() -> None:
    """Guards against a tweak quietly turning merging into deletion."""
    assert 0.5 <= MERGE_SIMILARITY <= 0.8


# --------------------------------------------------------------------------------------------
# Rule classification: a condition in the code is not a rule the business chose
# --------------------------------------------------------------------------------------------


def _graph(*entities):
    from agentic.schemas.core import KnowledgeGraph

    return KnowledgeGraph(repository_name="t", entities=list(entities), relationships=[])


def _entity(kind, name, lines=(10, 20), **attributes):
    from agentic.schemas.core import Entity

    return Entity(
        id=f"{kind}:{name}",
        kind=kind,
        name=name,
        qualified_name=name,
        location=SourceLocation(file="svc.py", line_start=lines[0], line_end=lines[1]),
        attributes=attributes,
    )


def test_a_rule_resting_only_on_a_condition_is_not_published_as_a_business_rule() -> None:
    """D4 stated directly.

    A codebase branches on a thousand conditions and states a handful of rules. Typing every
    condition as a business rule buries the handful -- in one measured run, 1,632 of 1,642 rule
    entities were ordinary conditionals and nearly half were plain null guards.
    """
    graph = _graph(_entity("BUSINESS_RULE", "value is not None", predicate="value is not None"))
    brd, result = _run(
        ProductBRD(
            business_rules=[
                _item("Reject Empty", "A request without a value shall be rejected.",
                      "domain_constraint")
            ]
        ),
        graph=graph,
    )

    assert brd.business_rules == []
    assert len(brd.system_requirements) == 1
    assert result.metrics["rules_demoted_to_internal_behaviour"] == 1


def test_a_rule_backed_by_a_declared_constraint_stays_a_business_rule() -> None:
    """The promotion signal: an extractor read something written down *as* a constraint."""
    graph = _graph(
        _entity("BUSINESS_RULE", "chk_state", rule_source="schema_constraint",
                expression="(state in ('open','closed'))")
    )
    brd, result = _run(
        ProductBRD(
            business_rules=[
                _item("Allowed States", "An order shall be in one of the permitted states.",
                      "domain_constraint")
            ]
        ),
        graph=graph,
    )

    assert len(brd.business_rules) == 1
    assert result.metrics["rules_supported_by_declared_policy"] == 1


def test_a_rule_backed_by_a_declared_policy_entity_stays_a_business_rule() -> None:
    """A named constant or a documented limit is a decision somebody made."""
    graph = _graph(_entity("DOMAIN_POLICY", "MAX_ADJUSTMENT", statement="MAX_ADJUSTMENT = 500"))
    brd, _ = _run(
        ProductBRD(
            business_rules=[
                _item("Adjustment Ceiling", "An adjustment shall not exceed the configured limit.",
                      "domain_constraint")
            ]
        ),
        graph=graph,
    )
    assert len(brd.business_rules) == 1


def test_a_rule_backed_by_explanatory_prose_stays_a_business_rule() -> None:
    """Prose is how a codebase states intent outright, so it counts as a source."""
    graph = _graph(
        _entity("BUSINESS_RULE", "limit check", predicate="n > limit",
                rationale="A reservation may not exceed the per-order limit.")
    )
    brd, _ = _run(
        ProductBRD(
            business_rules=[
                _item("Reservation Limit", "A reservation shall not exceed the per-order limit.",
                      "domain_constraint")
            ]
        ),
        graph=graph,
    )
    assert len(brd.business_rules) == 1


def test_a_demoted_rule_keeps_every_citation_and_says_why_it_moved() -> None:
    """Demotion is a filing decision, never a deletion, and it must be legible."""
    graph = _graph(_entity("BUSINESS_RULE", "x is None", predicate="x is None"))
    brd, _ = _run(
        ProductBRD(
            business_rules=[
                _item("Guard", "A missing value shall be rejected.", "domain_constraint",
                      lines=(10, 20))
            ]
        ),
        graph=graph,
    )
    moved = brd.system_requirements[0]
    assert moved.evidence[0].line_start == 10
    assert any("internal behaviour" in note for note in moved.assumptions)


def test_a_citation_that_only_overlaps_the_declaration_still_counts() -> None:
    """A citation names a span and an entity occupies one; they rarely coincide exactly."""
    graph = _graph(_entity("BUSINESS_RULE", "chk", lines=(18, 19), rule_source="schema_constraint"))
    brd, _ = _run(
        ProductBRD(
            business_rules=[
                _item("R", "An order shall satisfy the declared constraint.", "domain_constraint",
                      lines=(10, 20))
            ]
        ),
        graph=graph,
    )
    assert len(brd.business_rules) == 1


def test_classification_does_not_touch_other_tiers() -> None:
    graph = _graph(_entity("FUNCTION", "handler"))
    brd, _ = _run(
        ProductBRD(
            functional_requirements=[
                _item("Act", "A person shall be able to act.", "external_capability")
            ]
        ),
        graph=graph,
    )
    assert len(brd.functional_requirements) == 1


def test_the_pass_can_be_turned_off() -> None:
    """On by default, and still individually switchable.

    It shipped off, which meant every conditional in the codebase was published as a business
    rule and the handful of declared policies were buried among them.
    """
    assert Settings().rule_classification is True
    assert Settings(rule_classification=False).rule_classification is False


# --------------------------------------------------------------------------------------------
# Parameterised families: one requirement written out many times
# --------------------------------------------------------------------------------------------


def test_the_same_sentence_with_one_word_changed_becomes_one_requirement() -> None:
    """A generic failure repeated per resource reads as many rules and is one."""
    brd, result = _run(
        ProductBRD(
            system_requirements=[
                _item(f"Missing {noun}",
                      f"The system shall return a not-found response when the {noun} does not "
                      "exist.", "internal_behaviour", lines=(10 + n * 10, 20 + n * 10),
                      trace=(f"a.{noun}",))
                for n, noun in enumerate(("order", "invoice", "receipt", "shipment"))
            ]
        ),
        collapse_parameterised=True,
    )
    assert len(brd.system_requirements) == 1
    kept = brd.system_requirements[0]
    for noun in ("order", "invoice", "receipt", "shipment"):
        assert noun in kept.statement
    assert result.metrics["parameterised_families"] == 1
    assert result.metrics["findings_absorbed_into_families"] == 3


def test_collapsing_keeps_every_citation() -> None:
    """Four findings collapse to one statement; the evidence for all four survives."""
    brd, _ = _run(
        ProductBRD(
            system_requirements=[
                _item(f"Missing {noun}",
                      f"The system shall return a not-found response when the {noun} does not "
                      "exist.", "internal_behaviour", lines=(10 + n * 10, 20 + n * 10),
                      trace=(f"a.{noun}",))
                for n, noun in enumerate(("order", "invoice", "receipt", "shipment"))
            ]
        ),
        collapse_parameterised=True,
    )
    kept = brd.system_requirements[0]
    assert len(kept.evidence) == 4
    assert any("Collapsed from 4" in note for note in kept.assumptions)


def test_two_similar_requirements_are_not_a_family() -> None:
    """Two statements differing in one word are usually two requirements, not a pattern."""
    brd, result = _run(
        ProductBRD(
            system_requirements=[
                _item("A", "The system shall archive an order after thirty days.",
                      "internal_behaviour", lines=(10, 20), trace=("a.one",)),
                _item("B", "The system shall archive an invoice after thirty days.",
                      "internal_behaviour", lines=(90, 99), trace=("a.two",)),
            ]
        )
    )
    assert len(brd.system_requirements) == 2
    assert result.metrics["parameterised_families"] == 0


def test_genuinely_different_requirements_are_never_collapsed() -> None:
    brd, _ = _run(
        ProductBRD(
            functional_requirements=[
                _item("A", "A person shall be able to submit a claim for review.",
                      "external_capability", lines=(10, 20), trace=("a.one",)),
                _item("B", "An administrator shall be able to close an account permanently.",
                      "external_capability", lines=(30, 40), trace=("a.two",)),
                _item("C", "A reviewer shall be able to request more information.",
                      "external_capability", lines=(50, 60), trace=("a.three",)),
            ]
        )
    )
    assert len(brd.functional_requirements) == 3


def test_the_collapse_pass_can_be_turned_off() -> None:
    """On by default now that it cannot collapse a family across a difference in verb.

    That was why it shipped off: a CRUD surface is five statements in one frame differing in one
    token, which is exactly the shape this looks for and is not one requirement.
    """
    assert Settings().collapse_parameterised is True
    assert Settings(collapse_parameterised=False).collapse_parameterised is False


# --------------------------------------------------------------------------------------------
# Cross-tier deduplication: one claim filed twice at different levels
# --------------------------------------------------------------------------------------------


def test_the_same_claim_filed_at_two_levels_becomes_one() -> None:
    """Two workers saw one behaviour and disagreed about what kind of thing it is."""
    statement = (
        "A claimant shall be able to submit a claim by supplying the amount and a receipt, "
        "so that it enters the review queue."
    )
    brd, result = _run(
        ProductBRD(
            functional_requirements=[
                _item("Submit A Claim", statement, "external_capability", lines=(10, 20))
            ],
            system_requirements=[
                _item("Accept A Claim", statement, "internal_behaviour", lines=(10, 20))
            ],
        )
    )
    assert len(brd.functional_requirements) == 1
    assert brd.system_requirements == []
    assert result.metrics["cross_tier_duplicates_merged"] == 1


def test_the_more_abstract_wording_is_the_one_that_survives() -> None:
    """A document should state a requirement at the highest level that is true of it."""
    statement = "A claimant shall be able to submit a claim so that it reaches the review queue."
    brd, _ = _run(
        ProductBRD(
            system_requirements=[
                _item("Accept A Claim", statement, "internal_behaviour", lines=(10, 20))
            ],
            functional_requirements=[
                _item("Submit A Claim", statement, "external_capability", lines=(10, 20))
            ],
        )
    )
    assert [item.title for item in brd.functional_requirements] == ["Submit A Claim"]
    assert brd.system_requirements == []


def test_a_merge_records_the_claim_it_absorbed() -> None:
    """A merge that leaves no trace is indistinguishable from a claim having been dropped."""
    statement = "A claimant shall be able to submit a claim so that it reaches the review queue."
    brd, _ = _run(
        ProductBRD(
            functional_requirements=[
                _item("Submit A Claim", statement, "external_capability", lines=(10, 20))
            ],
            system_requirements=[
                _item("Accept A Claim", statement, "internal_behaviour", lines=(10, 20))
            ],
        )
    )
    assert brd.functional_requirements[0].superseded == ["Accept A Claim"]


def test_an_outcome_and_the_capability_realising_it_survive_the_cross_tier_pass() -> None:
    """The guard, measured rather than assumed.

    These two score 0.67 against each other -- they share their actor and their object and differ
    only in the verb -- which is above the same-tier merge threshold. Applying that threshold
    across tiers would delete the outcome and keep the mechanism, so the cross-tier bar is set far
    higher and a declared parent link is never crossed at all.
    """
    brd, result = _run(
        ProductBRD(
            business_requirements=[
                _item("Settle A Claim", "A claimant shall be able to have a claim settled.",
                      "business_outcome")
            ],
            functional_requirements=[
                _item("Submit A Claim", "A claimant shall be able to submit a claim.",
                      "external_capability", "Settle A Claim", "refines")
            ],
        )
    )
    assert len(brd.business_requirements) == 1
    assert len(brd.functional_requirements) == 1
    assert result.metrics["cross_tier_duplicates_merged"] == 0


def test_a_declared_parent_link_is_never_merged_through() -> None:
    """Even at wording similar enough to clear the bar, a declared hierarchy is a hierarchy."""
    statement = "A claimant shall be able to submit a claim so that it reaches the review queue."
    brd, _ = _run(
        ProductBRD(
            business_requirements=[
                _item("Settle A Claim", statement, "business_outcome", lines=(10, 20))
            ],
            functional_requirements=[
                _item("Submit A Claim", statement, "external_capability",
                      "Settle A Claim", "refines", lines=(10, 20))
            ],
        )
    )
    assert len(brd.business_requirements) == 1
    assert len(brd.functional_requirements) == 1


def test_two_claims_over_different_code_are_never_merged_across_tiers() -> None:
    statement = "A claimant shall be able to submit a claim so that it reaches the review queue."
    brd, _ = _run(
        ProductBRD(
            functional_requirements=[
                _item("Submit A Claim", statement, "external_capability",
                      lines=(10, 20), trace=("a.one",))
            ],
            system_requirements=[
                _item("Accept A Claim", statement, "internal_behaviour",
                      lines=(90, 99), trace=("a.two",))
            ],
        )
    )
    assert len(brd.functional_requirements) == 1
    assert len(brd.system_requirements) == 1


def test_a_shared_title_across_tiers_is_a_duplicate() -> None:
    """Titles must be distinct and are what parent links are written with, so sharing one is not
    a hierarchy -- it is the same claim filed twice."""
    brd, _ = _run(
        ProductBRD(
            functional_requirements=[
                _item("Submit A Claim", "A claimant shall be able to lodge a request.",
                      "external_capability", lines=(10, 20))
            ],
            system_requirements=[
                _item("Submit A Claim", "The system shall accept an inbound submission.",
                      "internal_behaviour", lines=(10, 20))
            ],
        )
    )
    assert len(brd.functional_requirements) == 1
    assert brd.system_requirements == []


def test_a_merged_claim_keeps_every_citation() -> None:
    statement = "A claimant shall be able to submit a claim so that it reaches the review queue."
    brd, _ = _run(
        ProductBRD(
            functional_requirements=[
                _item("Submit A Claim", statement, "external_capability", lines=(10, 20))
            ],
            system_requirements=[
                _item("Accept A Claim", statement + " The queue is durable.",
                      "internal_behaviour", lines=(10, 30))
            ],
        )
    )
    assert len(brd.functional_requirements[0].evidence) == 2


def test_the_duplicate_title_count_is_reported() -> None:
    """The measure the pass exists to move, so a regression is visible rather than inferred."""
    brd, result = _run(
        ProductBRD(
            functional_requirements=[
                _item("Act", "A person shall be able to act on a record.", "external_capability",
                      lines=(10, 20), trace=("a.one",)),
                _item("Act", "A person shall be able to act on a different record.",
                      "external_capability", lines=(90, 99), trace=("a.two",)),
            ]
        )
    )
    assert result.metrics["duplicate_titles_remaining"] == 1


def test_the_dedup_pass_can_be_turned_off() -> None:
    """On by default, and still individually switchable."""
    assert Settings().dedup_pass is True
    assert Settings(dedup_pass=False).dedup_pass is False


# --------------------------------------------------------------------------------------------
# The action verb is decisive
# --------------------------------------------------------------------------------------------


def test_capabilities_differing_only_in_their_verb_are_never_merged() -> None:
    """The defect that reduced a whole document to two or three requirements.

    A requirements document is supposed to read consistently, so its statements share a frame:
    the same actor, the same object, the same purpose clause. What distinguishes them is the verb
    -- and word-overlap scoring cannot see that one word carries the entire difference. "Staff
    shall be able to delete a supplier bill" and "...view a supplier bill" share five of six
    content words and score 0.83 against a merge threshold of 0.65, so an entire CRUD surface
    collapsed into whichever wording happened to survive.
    """
    verbs = ("record", "view", "update", "delete", "archive")
    brd, result = _run(
        ProductBRD(
            functional_requirements=[
                _item(
                    verb.title(),
                    f"Staff shall be able to {verb} a supplier bill so that the change is saved.",
                    "external_capability",
                    lines=(10 + n, 12 + n),
                )
                for n, verb in enumerate(verbs)
            ]
        )
    )
    assert len(brd.functional_requirements) == len(verbs), [
        item.title for item in brd.functional_requirements
    ]
    assert result.metrics["duplicates_merged"] == 0


def test_the_same_verb_over_the_same_code_still_merges() -> None:
    """The guard must not switch deduplication off, only stop it crossing a real difference."""
    brd, result = _run(
        ProductBRD(
            functional_requirements=[
                _item("A", "Staff shall be able to record a supplier bill for later retrieval.",
                      "external_capability"),
                _item("B", "Staff shall be able to record a supplier bill so it can be retrieved.",
                      "external_capability"),
            ]
        )
    )
    assert len(brd.functional_requirements) == 1
    assert result.metrics["duplicates_merged"] == 1


def test_a_statement_with_no_modal_still_compares_on_wording() -> None:
    """No verb to read means fall back, not refuse: the guard must never block a real duplicate."""
    from agentic.capabilities.consolidate import _action_of

    assert _action_of("Bills are retained for seven years.") == ""
    brd, _ = _run(
        ProductBRD(
            functional_requirements=[
                _item("A", "Bills are retained for seven years after submission.",
                      "external_capability"),
                _item("B", "Bills are retained for seven years following submission.",
                      "external_capability"),
            ]
        )
    )
    assert len(brd.functional_requirements) == 1

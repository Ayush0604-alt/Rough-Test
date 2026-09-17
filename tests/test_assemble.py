"""Assembly must not lose a grounded finding.

This is the property the rebuild turns on. The old `reduce` step asked one model call to re-emit
the whole document, so the number of published requirements was bounded by an output token limit
rather than by the codebase — and the loss was silent, because nothing counted what went in
against what came out. Assembly is now deterministic, so that count is a test.
"""

from __future__ import annotations

import collections
from typing import Any

import pytest

from agentic.capabilities.assemble import (
    FRAGMENT_SECTIONS,
    _clean_code,
    assemble_requirements,
    digest_areas,
)
from agentic.schemas.brd import BRDFragment, BRDItem, OutcomeBinding, TopTier
from agentic.schemas.core import SourceLocation


def _item(title: str, **kwargs) -> BRDItem:
    return BRDItem(
        title=title,
        statement=f"The system shall {title.lower()} so that something observable follows.",
        confidence=1.0,
        evidence=[SourceLocation(file="app.py", line_start=1, line_end=5)],
        graph_path=["app.handler"],
        **kwargs,
    )


def _fragment(area: str, name: str, capabilities: int, rules: int = 0, **extra) -> dict[str, Any]:
    payload = BRDFragment(
        purpose_hint=f"What {name} does.",
        area_code=area,
        area_name=name,
        functional_requirements=[
            _item(f"{area} capability {index}") for index in range(1, capabilities + 1)
        ],
        business_rules=[_item(f"{area} rule {index}") for index in range(1, rules + 1)],
        **extra,
    ).model_dump(mode="json")
    payload["boundary"] = f"POST /{area.lower()}"
    payload["cluster_id"] = f"cluster:{area}"
    return payload


class _Ctx:
    """The slice of CapabilityContext these two capabilities actually use."""

    def __init__(self, fragments: list[dict[str, Any]], top: TopTier | None = None) -> None:
        self.state = {"fragments": fragments}
        self._top = top

    def load(self, name: str, model=None):
        assert name == "top_tier"
        return self._top


BINDING = "Run A Hiring Campaign"


def _top(*areas: str, realised_by: list[str] | None = None) -> TopTier:
    """The elevation reply. Bindings name capabilities; `areas` names which fragments to cover.

    Where a test does not spell out capability titles, every title the `_fragment` helper
    generates for the listed areas is bound, which is what a well-behaved elevation call does.
    """
    outcome = BRDItem(
        title=BINDING,
        statement="A recruiter shall be able to run a hiring campaign end to end, so that "
                  "candidates reach a decision without manual re-keying.",
        confidence=0.9,
        requirement_type="business",
        evidence=[SourceLocation(file="app.py", line_start=1, line_end=5)],
        graph_path=["app.handler"],
    )
    titles = realised_by if realised_by is not None else [
        f"{area} capability {index}" for area in areas for index in range(1, 41)
    ] + [f"{area} rule {index}" for area in areas for index in range(1, 21)]
    return TopTier(
        business_requirements=[outcome],
        bindings=[OutcomeBinding(business_requirement=BINDING, realised_by=titles)],
    )


# --------------------------------------------------------------------------------------------
# The property the rebuild exists for
# --------------------------------------------------------------------------------------------


def test_every_grounded_finding_survives_assembly() -> None:
    """Findings in must equal items out. This is what the old merge step could not promise."""
    fragments = [
        _fragment("INT", "Mail intake", 13, rules=4),
        _fragment("VAL", "Validation", 11, rules=6),
        _fragment("SCH", "Scheduling", 9, rules=2),
    ]
    result = assemble_requirements(_Ctx(fragments, _top("INT", "VAL", "SCH")))
    brd = result.artifacts["brd"]

    assert len(brd.functional_requirements) == 13 + 11 + 9
    assert len(brd.business_rules) == 4 + 6 + 2
    assert result.metrics["findings_carried"] == 45
    # Nothing was compressed away, and the count is on the record either way.
    assert result.metrics["clusters_parsed"] == 3


def test_a_large_fan_out_is_not_capped() -> None:
    """Forty clusters of ten requirements must produce four hundred, not a model's worth."""
    fragments = [_fragment(f"A{index:02d}", f"Area {index}", 10) for index in range(40)]
    top = _top(*[f"A{index:02d}" for index in range(40)])
    brd = assemble_requirements(_Ctx(fragments, top)).artifacts["brd"]
    assert len(brd.functional_requirements) == 400


def test_statements_are_carried_through_word_for_word() -> None:
    """Assembly is a move, not a rewrite: no step between the worker and the gate reworded one."""
    fragments = [_fragment("INT", "Mail intake", 2)]
    brd = assemble_requirements(_Ctx(fragments, _top("INT"))).artifacts["brd"]
    original = BRDFragment.model_validate(
        {k: v for k, v in fragments[0].items() if k in BRDFragment.model_fields}
    )
    assert [item.statement for item in brd.functional_requirements] == [
        item.statement for item in original.functional_requirements
    ]


def test_every_supplementary_family_is_carried() -> None:
    fragments = [
        _fragment(
            "INT", "Mail intake", 1,
            data_requirements=[_item("hold one role vocabulary")],
            integration_requirements=[_item("read a mailbox")],
            non_functional_requirements=[_item("refuse a concurrent run")],
            reporting_requirements=[_item("rank by score")],
        )
    ]
    brd = assemble_requirements(_Ctx(fragments, _top("INT"))).artifacts["brd"]
    # Named explicitly rather than derived from FRAGMENT_SECTIONS. The fixture populates exactly
    # these four, so iterating every carried section asserts that sections this fragment never
    # filled came out non-empty — which is a statement about the fixture, not about assembly.
    for section in (
        "functional_requirements",
        "data_requirements",
        "integration_requirements",
        "non_functional_requirements",
        "reporting_requirements",
    ):
        assert getattr(brd, section), section


def test_a_section_the_fragment_schema_offers_is_never_silently_dropped() -> None:
    """Every section a worker can fill must be carried, or its findings vanish without a trace.

    `system_requirements`, `technical_constraints` and `observations` were on `BRDFragment` and
    absent from the carried set, so every one a worker produced was discarded at assembly. Nothing
    raised: a section nobody iterates simply never arrives. The synthesis skill spends a paragraph
    telling workers to file implementation detail under `observations`, and none of it ever reached
    the document.
    """
    from agentic.schemas.brd import BRDFragment

    fillable = {
        name
        for name, field in BRDFragment.model_fields.items()
        if name not in {"purpose_hint", "area_code", "area_name", "areas"}
    }
    assert fillable == set(FRAGMENT_SECTIONS), (
        f"carried but not offered: {set(FRAGMENT_SECTIONS) - fillable}; "
        f"offered but dropped: {fillable - set(FRAGMENT_SECTIONS)}"
    )


# --------------------------------------------------------------------------------------------
# Areas and the parent chain
# --------------------------------------------------------------------------------------------


def test_a_capability_inherits_the_outcome_its_area_was_bound_to() -> None:
    fragments = [_fragment("INT", "Mail intake", 2)]
    brd = assemble_requirements(_Ctx(fragments, _top("INT"))).artifacts["brd"]
    assert {item.parent for item in brd.functional_requirements} == {BINDING}
    assert {item.area for item in brd.functional_requirements} == {"INT"}


def test_an_unplaced_capability_is_reported_not_invented() -> None:
    """Manufacturing an outcome to hang orphans on is the fabrication this workflow prevents."""
    fragments = [_fragment("INT", "Mail intake", 2), _fragment("SCH", "Scheduling", 1)]
    # Only the intake capabilities are named; the scheduling one is left unplaced.
    top = _top(realised_by=["INT capability 1", "INT capability 2"])
    result = assemble_requirements(_Ctx(fragments, top))
    brd = result.artifacts["brd"]

    assert result.metrics["capabilities_unattached"] == 1
    assert len(brd.business_requirements) == 1, "no outcome was invented for the orphan"
    orphan = [item for item in brd.functional_requirements if not item.parent]
    assert [item.title for item in orphan] == ["SCH capability 1"]
    assert any("unattached" in message for message in result.messages)


def test_a_capability_is_bound_by_name_not_by_where_it_was_found() -> None:
    """Two capabilities in one area may serve different outcomes, and often do."""
    fragments = [_fragment("OPS", "Operations", 2)]
    other = BRDItem(
        title="A Different Outcome",
        statement="Someone shall be able to achieve something else entirely, so that it shows.",
        confidence=0.9,
        requirement_type="business",
        evidence=[SourceLocation(file="app.py", line_start=1, line_end=5)],
        graph_path=["app.handler"],
    )
    top = _top(realised_by=["OPS capability 1"])
    top.business_requirements.append(other)
    top.bindings.append(
        OutcomeBinding(business_requirement="A Different Outcome",
                       realised_by=["OPS capability 2"])
    )
    brd = assemble_requirements(_Ctx(fragments, top)).artifacts["brd"]

    parents = {item.title: item.parent for item in brd.functional_requirements}
    assert parents == {"OPS capability 1": BINDING, "OPS capability 2": "A Different Outcome"}


def test_two_clusters_sharing_an_area_code_are_merged_under_one_heading() -> None:
    fragments = [_fragment("INT", "Mail intake", 2), _fragment("INT", "Mail intake", 3)]
    brd = assemble_requirements(_Ctx(fragments, _top("INT"))).artifacts["brd"]
    assert len(brd.functional_requirements) == 5
    assert brd.area_names["INT"] == "Mail intake"


def test_a_worker_that_named_no_area_is_grouped_rather_than_dropped() -> None:
    fragments = [_fragment("", "", 3)]
    result = assemble_requirements(_Ctx(fragments, _top()))
    brd = result.artifacts["brd"]
    assert len(brd.functional_requirements) == 3
    assert {item.area for item in brd.functional_requirements} == {"GEN"}


def test_an_unparseable_cluster_reply_costs_only_itself() -> None:
    fragments = [_fragment("INT", "Mail intake", 2), {"boundary": "x", "area_code": 5}]
    result = assemble_requirements(_Ctx(fragments, _top("INT")))
    assert result.metrics["clusters_parsed"] == 1
    assert result.metrics["clusters_unreadable"] == 1
    assert len(result.artifacts["brd"].functional_requirements) == 2


@pytest.mark.parametrize(
    "raw, expected",
    [("INT", "INT"), ("int", "INT"), ("  sch ", "SCH"), ("L1-MAIL", "LMAI"), ("x", ""), ("", "")],
)
def test_area_codes_are_normalised(raw, expected) -> None:
    assert _clean_code(raw) == expected


# --------------------------------------------------------------------------------------------
# The digest — the input to the one remaining whole-repository call
# --------------------------------------------------------------------------------------------


def test_the_digest_carries_titles_not_statements() -> None:
    """Sending the bodies again is what made the old merge step unaffordable."""
    fragments = [_fragment("INT", "Mail intake", 6, rules=3)]
    digest = digest_areas(_Ctx(fragments)).artifacts["area_digest"]

    area = digest["areas"][0]
    assert area["area_code"] == "INT"
    assert area["area_name"] == "Mail intake"
    assert len(area["capability_titles"]) == 6
    assert area["finding_counts"]["functional_requirements"] == 6

    encoded = repr(digest)
    assert "so that something observable follows" not in encoded, "a full statement leaked"


def test_the_digest_carries_every_capability_title() -> None:
    """Binding is per capability now, so one the elevation step cannot see is one it cannot place.

    The per-area cap used to be 40, which was safe while whole areas were bound together and only
    a flavour of each was needed. It is not safe now: a truncated title list silently guarantees
    orphans.
    """
    digest = digest_areas(_Ctx([_fragment("OPS", "Operations", 120)])).artifacts["area_digest"]
    area = digest["areas"][0]
    assert len(area["capability_titles"]) == 120
    assert area["capability_titles_omitted"] == 0


def test_the_digest_stays_far_smaller_than_the_document() -> None:
    """Titles, not statements. That is what keeps the one whole-repository call affordable."""
    fragments = [_fragment(f"A{i:02d}", f"Area {i}", 10) for i in range(40)]
    digest = digest_areas(_Ctx(fragments)).artifacts["area_digest"]
    assert digest["total_findings"] == 400

    written = sum(
        len(item["statement"]) + sum(len(c) for c in item.get("acceptance_criteria", []))
        for payload in fragments
        for section in FRAGMENT_SECTIONS
        for item in payload.get(section) or []
    )
    # The digest must stay a small fraction of what a step re-emitting the findings would cost.
    assert len(repr(digest)) < written / 2


# --------------------------------------------------------------------------------------------
# One entry point covering several unrelated parts of a product
# --------------------------------------------------------------------------------------------


def test_one_boundary_fanning_into_several_areas_is_not_lumped_together() -> None:
    """The multi-agent case: one chat box reaching routing, two answering paths, and history.

    Filing every requirement under the entry point's own area published one lumped heading where
    the reader needs several, and bound all of them to a single outcome. An area is a property of
    the requirement, not of the cluster it was found in.
    """
    payload = BRDFragment(
        area_code="CHAT",
        area_name="Question answering",
        areas=[
            {"code": "SQL", "name": "Database questions"},
            {"code": "DOC", "name": "Document questions"},
            {"code": "SAFE", "name": "Question screening"},
        ],
        functional_requirements=[
            _item("answer from the database", area="SQL"),
            _item("recover from a failed database query", area="SQL"),
            _item("answer from indexed documents", area="DOC"),
            _item("refuse an unrelated question", area="SAFE"),
            _item("route a question to a source"),  # inherits the cluster's own area
        ],
    ).model_dump(mode="json")
    payload["boundary"] = "app!chat_input"

    top = TopTier(
        business_requirements=[
            BRDItem(
                title=BINDING,
                statement="A user shall be able to ask one question and have it answered from "
                          "whichever source holds the answer.",
                confidence=0.9,
                requirement_type="business",
                evidence=[SourceLocation(file="app.py", line_start=1, line_end=5)],
                graph_path=["app.handler"],
            )
        ],
        bindings=[
            OutcomeBinding(
                business_requirement=BINDING,
                realised_by=[
                    "answer from the database",
                    "recover from a failed database query",
                    "answer from indexed documents",
                    "refuse an unrelated question",
                    "route a question to a source",
                ],
            )
        ],
    )
    result = assemble_requirements(_Ctx([payload], top))
    brd = result.artifacts["brd"]

    by_area = collections.Counter(item.area for item in brd.functional_requirements)
    assert by_area == {"SQL": 2, "DOC": 1, "SAFE": 1, "CHAT": 1}
    assert brd.area_names["SQL"] == "Database questions"
    assert brd.area_names["DOC"] == "Document questions"
    # Every capability is bound by name, whichever area it was filed under.
    assert all(item.parent == BINDING for item in brd.functional_requirements)
    assert result.metrics["capabilities_unattached"] == 0


def test_the_digest_reports_each_area_separately() -> None:
    """The step that writes the top tier must see the areas, not the boundary they arrived on."""
    payload = BRDFragment(
        area_code="CHAT",
        area_name="Question answering",
        areas=[{"code": "SQL", "name": "Database questions"}],
        functional_requirements=[
            _item("answer from the database", area="SQL"),
            _item("route a question to a source"),
        ],
    ).model_dump(mode="json")
    payload["boundary"] = "app!chat_input"

    digest = digest_areas(_Ctx([payload])).artifacts["area_digest"]
    codes = {area["area_code"]: area for area in digest["areas"]}
    assert set(codes) == {"SQL", "CHAT"}
    assert codes["SQL"]["area_name"] == "Database questions"
    assert codes["SQL"]["finding_counts"]["functional_requirements"] == 1
    assert codes["CHAT"]["finding_counts"]["functional_requirements"] == 1

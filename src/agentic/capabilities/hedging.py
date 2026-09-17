"""Say what the document does not know.

Two fields exist for exactly this and were empty on every item of a measured run: `assumptions` on
60 of 60, `open_questions` on 60 of 60. A reverse-engineered requirements document is an argument
from evidence, and an argument from evidence that never once says "this part is uncertain" is not
confident, it is silent. The reader has no way to tell the claims that rest on a parsed entry point
from the ones that rest on a sentence somebody wrote in a comment three years ago.

Acceptance criteria were empty on 26 of 60 for the same reason: nothing was responsible for
noticing. A requirement nobody can check is not yet a requirement.

Everything here is derived from evidence already gathered, never invented. Where a criterion can be
read off something observable in the cited span, it is stated and marked as derived so a reviewer
knows it came from the citation rather than from analysis. Where it cannot, that becomes an open
question instead. Filling the field with a sentence that says nothing would be worse than leaving
it empty, because it would look like the question had been answered.
"""

from __future__ import annotations

from collections import Counter
from typing import Any

from agentic.capabilities.correspondence import EvidenceView
from agentic.capabilities.graph_ops import BOUNDARY_KINDS, DECLARATION_KINDS, GraphIndex
from agentic.capabilities.registry import CapabilityContext, CapabilityResult, capability
from agentic.schemas.brd import BRDItem, ProductBRD
from agentic.schemas.core import KnowledgeGraph, Provenance

# Attributes that state something checkable in their own words. Read in order; the first present
# wins. The planner never interprets the value, so this carries no knowledge of any stack.
OBSERVABLE_ATTRIBUTES = ("expression", "allowed_values", "statement", "value")

DERIVED_NOTE = (
    "The acceptance criterion below was derived from the cited evidence rather than stated by "
    "the analysis, and should be confirmed against the product's intent."
)


class BoundaryReach:
    """Which entry point, if any, can cause a given piece of code to run.

    Needed because a requirement rarely cites the boundary itself. It cites the code that does the
    work, and the boundary is upstream: on the command-line fixture every claim cites a subcommand
    handler while the only entry point is the parser above it, so asking whether the *cited* entity
    is a boundary derived a criterion for nothing at all. Naming the entry point that reaches the
    cited code gives a reviewer something they can actually run.
    """

    def __init__(self, graph: KnowledgeGraph) -> None:
        index = GraphIndex(graph)
        self._by_entity: dict[str, Any] = {}
        for boundary_id in index.boundary_ids():
            boundary = index.entities.get(boundary_id)
            if boundary is None or boundary.kind not in BOUNDARY_KINDS:
                # `boundary_ids` also returns the handlers that serve a boundary. Those are the
                # right things to walk from and the wrong things to name.
                continue
            for reached in index.workflow_from(boundary_id):
                self._by_entity.setdefault(reached, boundary)

    def for_entities(self, entities: list[Any]) -> Any | None:
        for entity in entities:
            found = self._by_entity.get(entity.id)
            if found is not None:
                return found
        return None


# How a boundary is named in a criterion when it has no label of its own. Deliberately a
# description of the *kind* of access point rather than its identifier: a criterion is held to the
# same language bar as the statement it hangs from, and a qualified name written into one is
# implementation leakage that arrives too late for anything to strip it. The reader who needs the
# identifier finds it in the evidence cell, where it belongs.
BOUNDARY_PHRASES = {
    "ENDPOINT": "the published interface",
    "CLI_ENTRY": "the command-line entry point",
    "SCRIPT_ENTRY": "the program's start-up path",
    "UI_ACTION": "the on-screen control",
    "SCHEDULED_JOB": "the scheduled run",
}


def _boundary_phrase(entity: Any) -> str:
    """What to call a boundary in a stakeholder-facing sentence.

    The product's own label for it wherever one was captured -- that is the wording it uses to
    address the person operating it -- and otherwise the kind of access point, never the symbol.
    """
    label = (entity.attributes or {}).get("label")
    if isinstance(label, str) and label.strip():
        return f'the "{" ".join(label.split())}" action'
    return BOUNDARY_PHRASES.get(entity.kind, "the point where this is reached from outside")


def _criterion_from(entities: list[Any], reach: BoundaryReach) -> str | None:
    """One checkable condition, read off the cited evidence, or nothing.

    Only two kinds of evidence can yield one honestly. A boundary is a place the system is
    consumed from outside, so it can be exercised and the outcome seen; and a declaration states a
    condition in its own words, so the condition itself is the check. Anything else would produce
    a sentence that restates the requirement and checks nothing.
    """
    for entity in entities:
        if entity.kind in BOUNDARY_KINDS:
            return (
                f"Exercising {_boundary_phrase(entity)} produces the outcome this requirement "
                "describes."
            )

    upstream = reach.for_entities(entities)
    if upstream is not None:
        return (
            f"Exercising {_boundary_phrase(upstream)}, which reaches this behaviour, produces "
            "the outcome this requirement describes."
        )

    for entity in entities:
        if entity.kind not in DECLARATION_KINDS:
            continue
        attributes = entity.attributes or {}
        for key in OBSERVABLE_ATTRIBUTES:
            value = attributes.get(key)
            if isinstance(value, str) and value.strip():
                stated = " ".join(value.split())[:200]
                return f"The declared condition still holds: {stated}"

    # A documentation file states policy in the project's own words, and for a claim resting on
    # one it is the only checkable thing cited. Reached last, after every structural source, so a
    # claim with real code behind it is still checked against the code.
    for entity in entities:
        if entity.kind != "FILE":
            continue
        for line in (entity.attributes or {}).get("content_outline") or []:
            if not isinstance(line, str):
                continue
            # Outline lines are `L12: text`. A heading names a section rather than stating a
            # condition, so it is not something a reviewer can check.
            text = line.split(":", 1)[-1].strip()
            if len(text.split()) >= 6 and not text.startswith("#"):
                return f"The documented condition still holds: {' '.join(text.split())[:200]}"
    return None


def _hedge(
    item: BRDItem, entities: list[Any], section: str, reach: BoundaryReach
) -> tuple[int, int, int]:
    """Record what is uncertain about one claim. Returns (assumptions, questions, criteria)."""
    added_assumptions = added_questions = added_criteria = 0

    def assume(text: str) -> None:
        nonlocal added_assumptions
        if text not in item.assumptions:
            item.assumptions.append(text)
            added_assumptions += 1

    def ask(text: str) -> None:
        nonlocal added_questions
        if text not in item.open_questions:
            item.open_questions.append(text)
            added_questions += 1

    # 1. Evidence that was inferred rather than parsed.
    if entities and all(entity.provenance != Provenance.EXTRACTED for entity in entities):
        assume(
            "This rests on text written beside the code -- a comment, a docstring, or "
            "documentation -- rather than on the code itself, so it states an intention that may "
            "no longer match what runs."
        )

    # 2. The correspondence check disagreed, which is a question for a person, not a fact.
    verdict = getattr(item.validation, "verdict", "unchecked") if item.validation else "unchecked"
    if verdict == "rejected":
        reason = (getattr(item.validation, "reason", "") or "").strip()
        ask(
            "The cited code does not appear to support this statement"
            + (f" ({reason.rstrip('.')})" if reason else "")
            + ". Should it be reworded, re-evidenced, or removed?"
        )

    # 3. A parent that never resolved. The link was asserted and then found to point at nothing,
    #    which is a gap in the hierarchy rather than an absence of one.
    if item.parent and not item.parent_id:
        ask(
            f"This was filed as belonging under {item.parent!r}, but no requirement of that name "
            "was published. Which requirement does it belong to?"
        )

    # 4. Nothing to check the requirement against. Observations are exempt: they record what the
    #    code does rather than what the product must do, so there is nothing to sign off.
    if section != "observations" and not item.acceptance_criteria:
        criterion = _criterion_from(entities, reach)
        if criterion:
            item.acceptance_criteria.append(criterion)
            assume(DERIVED_NOTE)
            added_criteria += 1
        else:
            ask(
                "What observable outcome would show this requirement has been met? Nothing in the "
                "cited evidence states one."
            )

    return added_assumptions, added_questions, added_criteria


@capability("hedge_requirements")
def hedge_requirements(ctx: CapabilityContext) -> CapabilityResult:
    """Populate assumptions, open questions, and missing acceptance criteria from the evidence."""
    brd = ctx.load("brd", ProductBRD)

    if not getattr(ctx.settings, "hedging_pass", True):
        return CapabilityResult(
            artifacts={"brd": brd},
            metrics={"hedged_items": 0, "hedging_disabled": True},
            messages=["Hedging is switched off for this run."],
        )

    graph = ctx.load("graph", KnowledgeGraph)
    view = EvidenceView(graph)
    reach = BoundaryReach(graph)

    totals: Counter[str] = Counter()
    items_touched = 0
    still_uncheckable = 0

    for section, items in brd.every_section().items():
        for item in items:
            assumptions, questions, criteria = _hedge(
                item, view.entities_for(item), section, reach
            )
            totals["assumptions"] += assumptions
            totals["open_questions"] += questions
            totals["criteria"] += criteria
            if assumptions or questions or criteria:
                items_touched += 1
            if section != "observations" and not item.acceptance_criteria:
                still_uncheckable += 1

    published = [
        item
        for section, items in brd.every_section().items()
        if section != "observations"
        for item in items
    ]
    with_questions = sum(1 for item in published if item.open_questions)

    messages = [
        f"Recorded what is uncertain on {items_touched} claim(s): "
        f"{totals['assumptions']} assumption(s), {totals['open_questions']} open question(s), "
        f"and {totals['criteria']} acceptance criterion/criteria derived from cited evidence."
    ]
    if still_uncheckable:
        messages.append(
            f"{still_uncheckable} requirement(s) still have nothing to check them against, "
            "because nothing in the code they cite states an observable outcome. Each carries an "
            "open question saying so rather than a criterion that would check nothing."
        )
    if with_questions:
        messages.append(
            f"{with_questions} published requirement(s) carry at least one question a person "
            "needs to answer. These are the items to read first."
        )

    return CapabilityResult(
        artifacts={"brd": brd},
        metrics={
            "hedged_items": items_touched,
            "assumptions_recorded": totals["assumptions"],
            "open_questions_recorded": totals["open_questions"],
            "criteria_derived": totals["criteria"],
            "requirements_without_criteria": still_uncheckable,
            "requirements_with_open_questions": with_questions,
        },
        messages=messages,
    )

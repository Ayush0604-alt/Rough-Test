"""How far a claim can be trusted, computed from its evidence rather than asserted by its author.

Confidence arrived as a number the model wrote down about its own output, and nothing ever checked
it or changed it. In one measured run it was exactly 1.0 on 40 of 60 requirements -- a document
declaring itself certain about two thirds of its contents, none of which any person had read. A
number that is 1.0 whatever the evidence carries no information, and the review list built from it
was therefore empty of precisely the items most worth reviewing.

This recomputes it from things that can be observed about the claim:

* what the correspondence check found;
* whether the citation names a symbol or gestures at a range of lines;
* how much independent code corroborates it;
* whether the cited evidence was parsed out of the code or inferred from prose beside it;
* whether a declared constraint, constant, or explanatory sentence stands behind it.

The weights below are a stated judgement, not a fitted calibration, and they are written out as
constants so they can be argued with and changed. They are set against one fixed point: a claim
that passed the correspondence check, cites a span small enough to read, and rests on code that was
actually parsed must clear the review threshold the deliverables already use, without needing a
model call to get there. Below that, the review list fills up with everything and stops being a
list. A first attempt at these weights put every true claim in the corpus at 0.65, just under the
line, which is how that fixed point came to be written down. What matters more than any individual weight is
that the number now varies with the evidence and that nothing can reach certainty: the ceiling is
below 1.0 because no claim here has been reviewed by a person, and saying otherwise is a lie the
document tells its reader.

The model's own number is kept in `model_confidence` rather than discarded, so the two can be
compared and this pass can be judged.
"""

from __future__ import annotations

from collections import Counter
from typing import Any

from agentic.capabilities.correspondence import EvidenceView
from agentic.capabilities.graph_ops import DECLARATION_KINDS
from agentic.capabilities.registry import CapabilityContext, CapabilityResult, capability
from agentic.schemas.brd import BRDItem, ProductBRD
from agentic.schemas.core import KnowledgeGraph, Provenance

# What a claim is worth before anything is known in its favour. Low on purpose: grounding is what
# earns confidence here, and a claim that has demonstrated none of it should read as doubtful.
BASE = 0.35

# The correspondence check is the strongest single signal, because it is the only one that
# compared the sentence to the code rather than measuring the shape of the citation.
SUPPORTED = 0.25
ENTAILED = 0.10        # additionally, when a model read the facts and agreed
REJECTED_CEILING = 0.30  # a claim the evidence contradicts cannot be better than doubtful

# No check ran, because correspondence validation was switched off. Worth less than a claim that
# passed a check and more than one that failed: not knowing is not the same as knowing it is wrong.
# Set so that an otherwise well-grounded but unverified claim still lands on the review list, which
# is the honest place for it.
UNCHECKED = 0.15

# A citation naming a symbol can be resolved to one construct; a bare line range cannot.
NAMES_A_SYMBOL = 0.10

# A span tight enough that the reader can see what is being pointed at.
TIGHT_SPAN_LINES = 60
TIGHT_SPAN = 0.05

# Independent corroboration. Capped, because the fifth citation of the same behaviour says much
# less than the second, and an item citing twenty places is usually vague rather than well founded.
PER_EXTRA_FILE = 0.05
MAX_CORROBORATION = 0.10

# Evidence that was parsed out of the code, as against inferred from a sentence written beside it.
# Prose states intent and intent goes stale; code is what runs.
PARSED_EVIDENCE = 0.10

# A declared constraint, named constant, or explanatory sentence standing behind the claim.
DECLARED_POLICY = 0.05

# Nothing in this document has been read by a person, so nothing in it is certain.
CEILING = 0.95
FLOOR = 0.10

# The review threshold the gate and the deliverables already use. Below it a claim is listed for
# review, so below it the reasoning behind the number is worth spelling out on the item itself.
LOW_CONFIDENCE_NOTE = 0.70

# Attributes marking a finding as declared policy rather than incidental control flow. Kept in step
# with the rule classifier, which promotes on the same evidence.
DOMAIN_SIGNAL_ATTRIBUTES = ("rule_source", "rationale", "statement", "allowed_values")


def score(item: BRDItem, entities: list[Any]) -> tuple[float, list[str]]:
    """The claim's confidence, and the reasons that produced it.

    Reasons are returned alongside the number because a score nobody can account for is not much
    better than a score nobody computed. They are recorded on the item as assumptions only where
    the claim came out weak, which is where a reader needs to know why.
    """
    reasons: list[str] = []
    value = BASE

    verdict = getattr(item.validation, "verdict", "unchecked") if item.validation else "unchecked"
    checked_by = getattr(item.validation, "checked_by", "") if item.validation else ""

    if verdict == "supported":
        value += SUPPORTED
        reasons.append("the cited code supports the statement")
        if checked_by == "entailment":
            value += ENTAILED
            reasons.append("the cited facts were read and agreed with the statement")
    elif verdict == "unchecked":
        value += UNCHECKED
        reasons.append("no correspondence check ran, so nothing confirmed the statement")

    if item.evidence:
        if any(location.symbol for location in item.evidence):
            value += NAMES_A_SYMBOL
            reasons.append("the citation names a symbol rather than a range of lines")
        widest = max(
            (location.line_end or location.line_start or 0)
            - (location.line_start or 0)
            for location in item.evidence
        )
        if widest <= TIGHT_SPAN_LINES:
            value += TIGHT_SPAN
            reasons.append("every citation points at a span small enough to read")

        files = {location.file for location in item.evidence}
        if len(files) > 1:
            corroboration = min((len(files) - 1) * PER_EXTRA_FILE, MAX_CORROBORATION)
            value += corroboration
            reasons.append(f"corroborated across {len(files)} files")

    if entities:
        if any(entity.provenance == Provenance.EXTRACTED for entity in entities):
            value += PARSED_EVIDENCE
            reasons.append("the evidence was parsed out of the code")
        declared = any(
            entity.kind in DECLARATION_KINDS
            or any((entity.attributes or {}).get(name) for name in DOMAIN_SIGNAL_ATTRIBUTES)
            for entity in entities
        )
        if declared:
            value += DECLARED_POLICY
            reasons.append("a declared constraint, constant, or explanation stands behind it")

    if verdict == "rejected":
        value = min(value, REJECTED_CEILING)
        reasons = ["the cited code does not support the statement"]

    return max(FLOOR, min(CEILING, round(value, 2))), reasons


@capability("recalibrate_confidence")
def recalibrate_confidence(ctx: CapabilityContext) -> CapabilityResult:
    """Replace each claim's self-reported confidence with one computed from its evidence."""
    brd = ctx.load("brd", ProductBRD)

    if not getattr(ctx.settings, "confidence_recalibration", True):
        return CapabilityResult(
            artifacts={"brd": brd},
            metrics={"confidence_recalibrated": 0, "confidence_recalibration_disabled": True},
            messages=["Confidence recalibration is switched off for this run."],
        )

    view = EvidenceView(ctx.load("graph", KnowledgeGraph))

    recalibrated = 0
    was_certain = 0
    bands: Counter[str] = Counter()
    moved_down = 0
    moved_up = 0

    for items in brd.every_section().values():
        for item in items:
            original = item.confidence
            if item.model_confidence is None:
                item.model_confidence = original
            if original >= 1.0:
                was_certain += 1

            value, reasons = score(item, view.entities_for(item))
            item.confidence = value
            recalibrated += 1

            if value < original - 1e-9:
                moved_down += 1
            elif value > original + 1e-9:
                moved_up += 1

            # Only a weak claim needs its reasoning surfaced; annotating a strong one is noise.
            if value < LOW_CONFIDENCE_NOTE:
                note = (
                    "Confidence is low because only these hold: " + "; ".join(reasons) + "."
                    if reasons
                    else "Confidence is low: nothing observable corroborates this claim."
                )
                if note not in item.assumptions:
                    item.assumptions.append(note)

            bands[_band(value)] += 1

    messages = [
        f"Recomputed confidence for {recalibrated} claim(s) from their evidence: "
        + ", ".join(f"{count} {name}" for name, count in sorted(bands.items()))
        + "."
    ]
    if was_certain:
        messages.append(
            f"{was_certain} claim(s) had been reported as completely certain by the model that "
            "wrote them. Nothing here has been reviewed by a person, so no claim is now published "
            f"above {CEILING:.2f}; the original values are kept alongside."
        )

    return CapabilityResult(
        artifacts={"brd": brd},
        metrics={
            "confidence_recalibrated": recalibrated,
            "confidence_previously_certain": was_certain,
            "confidence_lowered": moved_down,
            "confidence_raised": moved_up,
            **{f"confidence_band_{name}": count for name, count in bands.items()},
        },
        messages=messages,
    )



def _band(value: float) -> str:
    if value >= 0.80:
        return "well grounded"
    if value >= LOW_CONFIDENCE_NOTE:
        return "grounded"
    if value >= 0.40:
        return "thin"
    return "doubtful"

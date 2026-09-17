"""Decide, per requirement, whether and how a test may be derived.

This runs before any model call and is entirely deterministic. That placement is the point: "should
this be tested at all" is exactly the decision that must not sit inside a model call, because a
model asked to design tests will always find something to say.

Every requirement gets a record, including the ones that produce nothing. A coverage report that
only accounts for what was tested cannot answer "why is there no test for this", which is the first
question a reviewer asks.

The rules are ordered and first-match-wins, so two runs over one input agree.
"""

from __future__ import annotations

import re
from collections import Counter
from difflib import SequenceMatcher

from agentic.capabilities.registry import (
    CapabilityContext,
    CapabilityResult,
    capability,
)
from agentic.capabilities.testgen.signals import activated_dimensions, beyond_positive
from agentic.schemas.testgen import (
    Band,
    IngestedRequirement,
    QualificationRecord,
    QualifiedSet,
    RequirementSet,
)

# Below this, the requirements pack's own confidence recalibration is signalling that the evidence
# was partial. On the rich fixture exactly one item in 190 sits here, which is why confidence is a
# flag and never a driver.
LOW_CONFIDENCE = 0.5

# Two statements this close are the same requirement said twice. Deliberately high: the
# requirements pack already merges by title and by shared evidence, so anything reaching here is a
# near-verbatim restatement rather than a near-miss.
DUPLICATE_SIMILARITY = 0.92

# Tiers that never yield a stakeholder-facing test directly.
#   observation     - true of the code, not required of the product
#   business        - an aggregate; covered through the capabilities beneath it
NON_TESTABLE_TIERS = {"observation"}
AGGREGATE_TIERS = {"business"}


def _normalise(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip().casefold()


def _duplicate_of(
    statement: str, candidates: list[IngestedRequirement]
) -> IngestedRequirement | None:
    """The first already-qualified requirement stating the same thing, or None.

    Written against `SequenceMatcher`'s cheap upper bounds rather than calling `ratio()` on every
    pair. The comparison is quadratic in the size of the requirement set, and on a 190-item set the
    naive form cost about ten seconds — most of a run whose real work is two file reads. Both
    `real_quick_ratio` and `quick_ratio` are documented upper bounds on `ratio`, so rejecting on
    them is exact: anything they rule out could not have met the threshold anyway.

    `set_seq2` is called once and `set_seq1` per candidate, because `SequenceMatcher` caches an
    index of the *second* sequence and recomputing it per pair was a second avoidable cost.
    """
    target = _normalise(statement)
    if not target:
        return None

    matcher = SequenceMatcher(None, "", target, autojunk=False)
    shortest = len(target) * DUPLICATE_SIMILARITY
    longest = len(target) / DUPLICATE_SIMILARITY if DUPLICATE_SIMILARITY else float("inf")

    for candidate in candidates:
        other = _normalise(candidate.statement)
        # Length alone bounds the ratio: 2*M/T cannot reach the threshold when the lengths are too
        # far apart, so this rejects most pairs before any matching work happens.
        if not shortest <= len(other) <= longest:
            continue
        matcher.set_seq1(other)
        if matcher.real_quick_ratio() < DUPLICATE_SIMILARITY:
            continue
        if matcher.quick_ratio() < DUPLICATE_SIMILARITY:
            continue
        if matcher.ratio() >= DUPLICATE_SIMILARITY:
            return candidate
    return None


def _rejected_titles(requirement_set: RequirementSet) -> set[str]:
    """Titles the requirements pack resolved as `reject`, from the optional unresolved artifact.

    Used only to catch a rejected candidate that reached the published set by another route. Absent
    enrichment, this is simply empty and the `status` field carries the decision on its own.
    """
    payload = requirement_set.enrichment.get("unresolved_candidates") or {}
    candidates = payload.get("candidates") if isinstance(payload, dict) else None
    if not isinstance(candidates, list):
        return set()
    return {
        _normalise(entry.get("title", ""))
        for entry in candidates
        if isinstance(entry, dict) and entry.get("disposition") == "reject" and entry.get("title")
    }


def _conflicted_ids(requirement_set: RequirementSet) -> set[str]:
    """Ids the requirements pack itself flagged as published over an unsettled conflict."""
    payload = requirement_set.enrichment.get("synthesis_findings") or {}
    issues = payload.get("issues") if isinstance(payload, dict) else None
    if not isinstance(issues, list):
        return set()
    return {
        entry.get("subject_id", "")
        for entry in issues
        if isinstance(entry, dict)
        and entry.get("code") in {"UNRESOLVED_CONFLICT", "REJECTED_IN_DOCUMENT"}
        and entry.get("subject_id")
    }


def qualify_one(
    requirement: IngestedRequirement,
    *,
    rejected_titles: set[str],
    conflicted_ids: set[str],
    already: list[IngestedRequirement],
) -> QualificationRecord:
    """Band one requirement. Rules are evaluated in order; the first that matches decides."""
    usable = requirement.usable_criteria()
    discarded = requirement.discarded_criteria()
    dimensions = activated_dimensions(requirement)
    blockers = requirement.blocking_questions()

    record = QualificationRecord(
        requirement_id=requirement.id,
        band=Band.ELIGIBLE,
        usable_criteria=usable,
        discarded_criteria=discarded,
        blockers=blockers,
        activated_dimensions=dimensions,
    )

    def decide(band: Band, code: str, reason: str, duplicate_of: str | None = None) -> QualificationRecord:
        record.band = band
        record.reason_codes.append(code)
        record.reason = reason
        record.duplicate_of = duplicate_of
        return record

    # ---- 1. Excluded -------------------------------------------------------------------------

    if requirement.status == "rejected":
        return decide(
            Band.EXCLUDED,
            "REQ_REJECTED",
            "The requirements pack's correspondence check found this claim and the code it cites "
            "disagree. Testing a claim its own author rejected would assert something unsupported.",
        )

    if requirement.type in NON_TESTABLE_TIERS:
        return decide(
            Band.EXCLUDED,
            "OBSERVATION_NOT_TESTABLE",
            "An implementation observation records what this code happens to do, not what the "
            "product must do. There is nothing here for a stakeholder to sign off.",
        )

    if not requirement.statement.strip():
        return decide(
            Band.EXCLUDED,
            "STATEMENT_UNUSABLE",
            "The requirement has no statement, so there is nothing to derive a test from.",
        )

    if rejected_titles and _normalise(requirement.title) in rejected_titles:
        return decide(
            Band.EXCLUDED,
            "REQ_UNRESOLVED",
            "This title matches a candidate the requirements pack resolved as rejected.",
        )

    earlier = _duplicate_of(requirement.statement, already)
    if earlier is not None:
        return decide(
            Band.EXCLUDED,
            "DUPLICATE_OF",
            f"States the same requirement as {earlier.id}. Testing both would double-count "
            "coverage and produce two cases that pass or fail together.",
            duplicate_of=earlier.id,
        )

    if requirement.type in AGGREGATE_TIERS:
        return decide(
            Band.EXCLUDED,
            "AGGREGATE_TIER",
            "A business outcome stands over whole capabilities rather than being exercised "
            "directly. It is covered through the capabilities beneath it, and testing it directly "
            "would produce exactly the 'verify the feature works' case worth avoiding.",
        )

    # ---- 2. Needs clarification --------------------------------------------------------------

    if blockers and not usable and not beyond_positive(requirement):
        return decide(
            Band.NEEDS_CLARIFICATION,
            "NO_OBSERVABLE_OUTCOME",
            "The requirements pack recorded that nothing in the cited evidence states an "
            "observable outcome, no usable acceptance criterion survived, and the statement "
            "activates no dimension beyond the happy path. This warrants a question, not a test.",
        )

    # ---- 3. Eligible with assumptions --------------------------------------------------------

    if requirement.type == "system":
        return decide(
            Band.ELIGIBLE_WITH_ASSUMPTIONS,
            "INTERNAL_BEHAVIOUR",
            "Internal behaviour that no outside actor observes directly. Any test must assume a "
            "route by which it becomes observable, so the assumption is stated rather than hidden.",
        )

    if discarded and not usable:
        return decide(
            Band.ELIGIBLE_WITH_ASSUMPTIONS,
            "DERIVED_CRITERIA_ONLY",
            f"All {len(discarded)} acceptance criterion/criteria were generated by the "
            "requirements pack rather than stated by the evidence, so they assert nothing. Tests "
            "must be derived from the statement instead.",
        )

    if not usable:
        return decide(
            Band.ELIGIBLE_WITH_ASSUMPTIONS,
            "NO_ACCEPTANCE_CRITERIA",
            "No acceptance criteria, but the statement activates "
            f"{len(beyond_positive(requirement))} dimension(s) beyond the happy path, so tests are "
            "derivable from the statement with the derivation recorded.",
        )

    if requirement.confidence is not None and requirement.confidence < LOW_CONFIDENCE:
        return decide(
            Band.ELIGIBLE_WITH_ASSUMPTIONS,
            "LOW_CONFIDENCE",
            f"Confidence of {requirement.confidence:.2f} means the evidence behind this was "
            "partial. Tests are worth writing, with the uncertainty on the record.",
        )

    if requirement.id in conflicted_ids:
        return decide(
            Band.ELIGIBLE_WITH_ASSUMPTIONS,
            "UNRESOLVED_CONFLICT",
            "The requirements pack published this while its cluster still held competing readings "
            "of one evidence set, so which reading is being tested must be stated.",
        )

    # ---- 4. Eligible --------------------------------------------------------------------------

    record.reason = (
        f"{len(usable)} usable acceptance criterion/criteria and "
        f"{len(dimensions)} activated dimension(s)."
    )
    record.reason_codes.append("DIRECTLY_TESTABLE")
    return record


@capability("qualify_requirements")
def qualify_requirements(ctx: CapabilityContext) -> CapabilityResult:
    """Band every requirement, and account for all of them."""
    requirement_set = ctx.load("requirement_set", RequirementSet)

    rejected_titles = _rejected_titles(requirement_set)
    conflicted_ids = _conflicted_ids(requirement_set)

    records: list[QualificationRecord] = []
    kept: list[IngestedRequirement] = []
    for requirement in requirement_set.requirements:
        record = qualify_one(
            requirement,
            rejected_titles=rejected_titles,
            conflicted_ids=conflicted_ids,
            already=kept,
        )
        records.append(record)
        # Only requirements that survived qualification are compared against later ones, so a
        # single requirement stated three times yields one exclusion chain rather than three
        # independent duplicates all pointing at each other.
        if record.testable:
            kept.append(requirement)

    counts = Counter(record.band.value for record in records)
    reason_counts = Counter(code for record in records for code in record.reason_codes)
    qualified = QualifiedSet(
        records=records,
        requirements=requirement_set.requirements,
        counts=dict(counts),
    )

    total = len(records)
    testable = sum(1 for record in records if record.testable)
    discarded_criteria = sum(len(record.discarded_criteria) for record in records)

    messages = [
        f"Qualified {total}/{len(requirement_set.requirements)} requirement(s): "
        + ", ".join(f"{count} {band}" for band, count in sorted(counts.items()))
        + "."
    ]
    if discarded_criteria:
        messages.append(
            f"Discarded {discarded_criteria} acceptance criterion/criteria that the requirements "
            "pack generated for itself rather than reading off the evidence. They read as "
            "criteria and assert nothing, so a test derived from one would check nothing."
        )
    if counts.get(Band.NEEDS_CLARIFICATION.value):
        messages.append(
            f"{counts[Band.NEEDS_CLARIFICATION.value]} requirement(s) need a stakeholder answer "
            "before a test can be written. Each is recorded with the question rather than given a "
            "test that would check nothing."
        )

    return CapabilityResult(
        artifacts={
            "qualified_set": qualified,
            "qualification_report": {
                "total": total,
                "testable": testable,
                "bands": dict(counts),
                "reason_codes": dict(reason_counts),
                "discarded_criteria": discarded_criteria,
                "records": [record.model_dump(mode="json") for record in records],
            },
        },
        metrics={
            "requirements_qualified": total,
            "testable": testable,
            "discarded_criteria": discarded_criteria,
            **{f"band_{band}": count for band, count in counts.items()},
        },
        messages=messages,
    )

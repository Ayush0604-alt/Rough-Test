"""Fold away cases that check the same thing twice.

Deduplication is deterministic and happens before the gate, because a duplicate is not a quality
problem a model should be asked to reason about — it is an arithmetic fact about two cases, and
letting a model decide which to keep invites it to keep both.

Nothing is deleted. A folded case leaves the suite via `superseded_by` on the case that absorbed
it, so a reviewer can see that two findings became one rather than that one went missing.
"""

from __future__ import annotations

from collections import Counter, defaultdict

from agentic.capabilities.registry import (
    CapabilityContext,
    CapabilityResult,
    capability,
)
from agentic.capabilities.testgen.quality import (
    DUPLICATE_SIMILARITY,
    OVERLAP_SIMILARITY,
    case_fingerprint,
    similarity,
)
from agentic.schemas.testspec import TestSuite


def _better(left, right) -> tuple[object, object]:
    """Which of two duplicates to keep, and which to fold into it.

    Preference order, most decisive first: a case grounded in a stated acceptance criterion beats
    one resting on the statement; a direct derivation beats an assumed one; more expected results
    beats fewer. Ties break on id so the outcome is reproducible.
    """

    def rank(case):
        grounded = any(link.kind == "acceptance_criterion" for link in case.covers)
        return (
            0 if grounded else 1,
            {"direct": 0, "assumed": 1, "inferred": 2}.get(case.derivation, 3),
            -len(case.expected_results),
            case.id,
        )

    return (left, right) if rank(left) <= rank(right) else (right, left)


def deduplicate(suite: TestSuite) -> tuple[TestSuite, list[dict], list[dict]]:
    """Fold duplicates, and report overlaps for a person rather than acting on them."""
    # Compared only within one requirement and one dimension. Two cases about different
    # requirements are not duplicates however similar their wording, because they are evidence
    # about different things — and folding them would silently drop a requirement's only coverage.
    grouped: dict[tuple, list] = defaultdict(list)
    for scenario in suite.scenarios:
        for case in scenario.cases:
            for requirement_id in case.requirement_ids:
                grouped[(requirement_id, case.dimension)].append(case)

    folded: dict[str, str] = {}
    duplicates: list[dict] = []
    overlaps: list[dict] = []

    for (requirement_id, dimension), cases in sorted(grouped.items()):
        for index, left in enumerate(cases):
            if left.id in folded:
                continue
            for right in cases[index + 1 :]:
                if right.id in folded:
                    continue
                score = similarity(case_fingerprint(left), case_fingerprint(right))
                if score >= DUPLICATE_SIMILARITY:
                    keeper, loser = _better(left, right)
                    folded[loser.id] = keeper.id
                    keeper.superseded.append(loser.id)
                    duplicates.append(
                        {
                            "kept": keeper.id,
                            "folded": loser.id,
                            "requirement_id": requirement_id,
                            "dimension": dimension,
                            "similarity": round(score, 3),
                        }
                    )
                elif score >= OVERLAP_SIMILARITY:
                    overlaps.append(
                        {
                            "left": left.id,
                            "right": right.id,
                            "requirement_id": requirement_id,
                            "dimension": dimension,
                            "similarity": round(score, 3),
                        }
                    )

    for scenario in suite.scenarios:
        scenario.cases = [case for case in scenario.cases if case.id not in folded]
    # A scenario emptied by folding is not a finding of its own — its cases live on inside the
    # ones that absorbed them.
    suite.scenarios = [scenario for scenario in suite.scenarios if scenario.cases]

    return suite, duplicates, overlaps


@capability("deduplicate_test_cases")
def deduplicate_test_cases(ctx: CapabilityContext) -> CapabilityResult:
    """Fold cases that check the same behaviour twice; report the ones that merely overlap."""
    suite = ctx.load("test_suite", TestSuite)
    before = len(suite.cases())

    suite, duplicates, overlaps = deduplicate(suite)
    suite.counts["cases"] = len(suite.cases())
    suite.counts["scenarios"] = len(suite.scenarios)
    # Accumulated, not overwritten. This step runs again after every repair pass, and assigning
    # here reported the *last* pass only — a run that folded nine duplicates before repair and none
    # after published `folded_duplicates: 0`, which is a false statement about what happened.
    suite.counts["folded_duplicates"] = (
        suite.counts.get("folded_duplicates", 0) + len(duplicates)
    )
    # Overlaps are recomputed from scratch each pass over the current suite, so this one is a
    # snapshot rather than a tally and is correct to replace.
    suite.counts["overlapping_pairs"] = len(overlaps)

    messages: list[str] = []
    if duplicates:
        by_dimension = Counter(entry["dimension"] for entry in duplicates)
        messages.append(
            f"Folded {len(duplicates)} duplicate case(s) into the case each restated "
            f"({', '.join(f'{d} ({n})' for d, n in by_dimension.most_common())}). Each folded id "
            "is recorded on the case that absorbed it rather than deleted."
        )
    if overlaps:
        messages.append(
            f"{len(overlaps)} pair(s) overlap without being duplicates. Both are kept and both are "
            "flagged: merging them is a judgement about what the product means, which this stage "
            "cannot make from wording alone."
        )
    if not duplicates and not overlaps:
        messages.append(f"No duplicate or overlapping cases among {before}.")

    return CapabilityResult(
        artifacts={"test_suite": suite},
        metrics={
            "cases_before": before,
            "cases_after": len(suite.cases()),
            "duplicates_folded": len(duplicates),
            "overlapping_pairs": len(overlaps),
            "duplicate_detail": duplicates[:50],
            "overlap_detail": overlaps[:50],
        },
        messages=messages,
    )

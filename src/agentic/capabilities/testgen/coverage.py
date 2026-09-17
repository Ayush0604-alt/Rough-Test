"""What is covered, what is not, and what is only half covered.

The useful half of a coverage report is the second half. Counting cases says how much work was
done; saying which requirement has only a happy-path test, which acceptance criterion nothing
checks, and which requirement got nothing at all is what a reviewer can act on.

Sufficiency is measured against the dimensions a requirement **activated**, never against the full
vocabulary. Otherwise the report demands an authorization test for a requirement that mentions no
access condition, and the only way to satisfy it is to invent one.
"""

from __future__ import annotations

from collections import Counter

from agentic.capabilities.registry import (
    CapabilityContext,
    CapabilityResult,
    capability,
)
from agentic.schemas.testgen import QualifiedSet
from agentic.schemas.testspec import (
    CoverageReport,
    CriterionCoverage,
    RequirementCoverage,
    Route,
    TestSignalSet,
    TestSuite,
)

# Dimensions whose absence means the suite only proves the happy path. A requirement that states a
# refusal and gets only a positive case is the most common and most misleading coverage failure:
# it looks covered and the behaviour anyone would actually get wrong is untested.
NEGATIVE_DIMENSIONS = frozenset(
    {"negative", "error_handling", "validation", "boundary", "business_rule"}
)


def build_coverage(
    qualified: QualifiedSet, signals: TestSignalSet, suite: TestSuite
) -> CoverageReport:
    by_signal = signals.by_id()
    by_requirement = {item.id: item for item in qualified.requirements}
    deferred_reasons = {entry.requirement_id: entry for entry in suite.deferred}

    requirements: list[RequirementCoverage] = []
    criteria: list[CriterionCoverage] = []

    for record in qualified.records:
        requirement_id = record.requirement_id
        signal = by_signal.get(requirement_id)
        cases = suite.cases_for(requirement_id)
        covered_dimensions = sorted({case.dimension for case in cases})

        if signal is None or signal.route != Route.GENERATE.value:
            verdict = "excluded" if not record.testable else "deferred"
        elif not cases:
            verdict = "none"
        else:
            expected = set(signal.dimensions)
            missing = expected - set(covered_dimensions)
            criteria_all = signal.usable_criteria
            criteria_covered = {
                int(link.ref)
                for case in cases
                for link in case.covers
                if link.kind == "acceptance_criterion" and link.ref.isdigit()
            }
            if not missing and len(criteria_covered) >= len(criteria_all):
                verdict = "sufficient"
            elif (
                covered_dimensions == ["positive"]
                and expected & NEGATIVE_DIMENSIONS
            ):
                # The dangerous one: it reads as covered and the behaviour most likely to be
                # wrong has no test at all.
                verdict = "positive_only"
            else:
                verdict = "thin"

        usable = signal.usable_criteria if signal else record.usable_criteria
        covered_indexes = {
            int(link.ref)
            for case in cases
            for link in case.covers
            if link.kind == "acceptance_criterion" and link.ref.isdigit()
        }
        for index, text in enumerate(usable):
            covering = [
                case.id
                for case in cases
                for link in case.covers
                if link.kind == "acceptance_criterion" and link.ref == str(index)
            ]
            criteria.append(
                CriterionCoverage(
                    requirement_id=requirement_id,
                    criterion_index=index,
                    criterion_text=text,
                    covered_by=covering,
                )
            )

        requirements.append(
            RequirementCoverage(
                requirement_id=requirement_id,
                band=record.band.value,
                route=signal.route if signal else Route.DEFERRED.value,
                verdict=verdict,
                dimensions_expected=signal.dimensions if signal else [],
                dimensions_covered=covered_dimensions,
                case_count=len(cases),
                criteria_total=len(usable),
                criteria_covered=len(covered_indexes & set(range(len(usable)))),
                reason=(
                    deferred_reasons[requirement_id].reason
                    if requirement_id in deferred_reasons
                    else record.reason
                ),
                title=by_requirement[requirement_id].title
                if requirement_id in by_requirement
                else "",
            )
        )

    verdicts = Counter(entry.verdict for entry in requirements)
    return CoverageReport(
        requirements=requirements,
        criteria=criteria,
        verdicts=dict(verdicts),
        totals={
            "requirements": len(requirements),
            "cases": len(suite.cases()),
            "scenarios": len(suite.scenarios),
            "criteria": len(criteria),
            "criteria_uncovered": sum(1 for entry in criteria if not entry.covered_by),
            "dimension_histogram": dict(
                Counter(case.dimension for case in suite.cases())
            ),
        },
    )


@capability("analyse_test_coverage")
def analyse_test_coverage(ctx: CapabilityContext) -> CapabilityResult:
    """Account for every requirement, covered or not, and every criterion, checked or not."""
    qualified = ctx.load("qualified_set", QualifiedSet)
    signals = ctx.load("test_signals", TestSignalSet)
    suite = ctx.load("test_suite", TestSuite)

    report = build_coverage(qualified, signals, suite)
    verdicts = report.verdicts

    messages = [
        "Coverage: "
        + ", ".join(f"{count} {verdict}" for verdict, count in sorted(verdicts.items()))
        + f" across {report.totals['requirements']} requirement(s)."
    ]
    if verdicts.get("positive_only"):
        messages.append(
            f"{verdicts['positive_only']} requirement(s) have only happy-path coverage while "
            "stating a refusal, an error, or a limit. These read as covered and leave the "
            "behaviour most likely to be wrong untested."
        )
    uncovered = report.totals["criteria_uncovered"]
    if uncovered:
        messages.append(
            f"{uncovered} acceptance criterion/criteria are checked by no case. Each is a "
            "condition somebody wrote down and nothing verifies."
        )

    return CapabilityResult(
        artifacts={"coverage": report},
        metrics={**report.totals, **{f"verdict_{k}": v for k, v in verdicts.items()}},
        messages=messages,
    )

"""The quality gate, its bounded repair loop, and the last look at the finished suite.

The gate judges cases, not coverage. That split matters: a vague expected result is a defect in a
case and a model can fix it, whereas a requirement nobody wrote a case for cannot be fixed by
rewording anything — the repair step is shown only the rejected cases, so it has nothing to design
*from*. Errors are therefore exactly the findings a repair pass could act on, and everything else
is reported. Making an unfixable finding blocking would spend the whole repair budget failing.
"""

from __future__ import annotations

from collections import Counter

from agentic.capabilities.registry import (
    CapabilityContext,
    CapabilityResult,
    capability,
)
from agentic.capabilities.testgen.quality import (
    INPUT_BEARING_DIMENSIONS,
    framework_leaks,
    needs_precondition,
    restates,
    vague_results,
)
from agentic.schemas.core import GateReport, Severity, ValidationIssue
from agentic.schemas.testgen import QualifiedSet
from agentic.schemas.testspec import CoverageReport, DeferredRequirement, TestSuite

# Findings a repair pass can actually act on, given it sees only the rejected cases and the issue
# list — never the requirement set, never the source. Mirrors the requirements pack's own
# repairable/unrepairable split, and for the same reason: spending a bounded model call on a
# finding whose only honest fix is deletion buys nothing that pruning does deterministically.
REPAIRABLE = frozenset(
    {
        "TC_VAGUE_EXPECTED_RESULT",
        "TC_RESTATES_REQUIREMENT",
        "TC_MISSING_INPUTS",
        "TC_MISSING_PRECONDITION",
        "TC_UNSUPPORTED_ASSUMPTION",
        "TC_NO_STEPS",
        "TC_NO_EXPECTED_RESULT",
        "TC_NO_OBJECTIVE",
        "TC_FRAMEWORK_LEAK",
    }
)


def _issue(code: str, severity: Severity, message: str, subject: str = "") -> ValidationIssue:
    return ValidationIssue(code=code, severity=severity, message=message, subject_id=subject or None)


def check_cases(suite: TestSuite, qualified: QualifiedSet) -> list[ValidationIssue]:
    """Every per-case check. Errors are repairable by construction; the rest are warnings."""
    issues: list[ValidationIssue] = []
    statements = {item.id: item.statement for item in qualified.requirements}
    not_testable = {
        record.requirement_id for record in qualified.records if not record.testable
    }

    for case in suite.cases():
        if not case.requirement_ids:
            issues.append(
                _issue(
                    "TC_NO_REQUIREMENT",
                    Severity.ERROR,
                    "Traces to no requirement, so nothing it finds could be acted on.",
                    case.id,
                )
            )
            continue

        excluded = set(case.requirement_ids) & not_testable
        if excluded:
            issues.append(
                _issue(
                    "TC_LINKED_TO_EXCLUDED",
                    Severity.ERROR,
                    f"Validates {', '.join(sorted(excluded))}, which was excluded from test "
                    "generation. A case for an excluded requirement asserts something the "
                    "requirements pack declined to stand behind.",
                    case.id,
                )
            )

        if not case.objective.strip():
            issues.append(
                _issue("TC_NO_OBJECTIVE", Severity.ERROR, "States no reason for existing.", case.id)
            )
        if not case.steps:
            issues.append(
                _issue("TC_NO_STEPS", Severity.ERROR, "Has no steps, so nobody could run it.", case.id)
            )
        if not case.expected_results:
            issues.append(
                _issue(
                    "TC_NO_EXPECTED_RESULT",
                    Severity.ERROR,
                    "Has no expected result, so it can neither pass nor fail.",
                    case.id,
                )
            )

        vague = vague_results(case.expected_results)
        if vague:
            issues.append(
                _issue(
                    "TC_VAGUE_EXPECTED_RESULT",
                    Severity.ERROR,
                    f"Expected result asserts nothing an implementation could violate: "
                    f"{vague[0]!r}. Say what is observably true afterwards.",
                    case.id,
                )
            )

        leaks = framework_leaks(
            case.title, case.objective, *case.steps, *case.expected_results
        )
        if leaks:
            issues.append(
                _issue(
                    "TC_FRAMEWORK_LEAK",
                    Severity.ERROR,
                    f"Names technology ({', '.join(sorted(set(leaks)))}), which binds this "
                    "specification to one stack. Describe what the actor does, not the mechanism.",
                    case.id,
                )
            )

        statement = next(
            (statements[rid] for rid in case.requirement_ids if rid in statements), ""
        )
        if statement and restates(
            " ".join([case.objective, *case.expected_results]), statement
        ):
            issues.append(
                _issue(
                    "TC_RESTATES_REQUIREMENT",
                    Severity.ERROR,
                    "Repeats the requirement rather than saying how you would check it.",
                    case.id,
                )
            )

        if case.dimension in INPUT_BEARING_DIMENSIONS and not case.inputs:
            issues.append(
                _issue(
                    "TC_MISSING_INPUTS",
                    Severity.ERROR,
                    f"A {case.dimension} case is about the input it supplies, and this names none.",
                    case.id,
                )
            )

        if needs_precondition(case):
            issues.append(
                _issue(
                    "TC_MISSING_PRECONDITION",
                    Severity.ERROR,
                    "Acts on something that must already exist, without establishing it. It may "
                    "pass against a system that does nothing, because there was nothing to act on.",
                    case.id,
                )
            )

        if case.derivation != "direct" and not case.assumptions:
            issues.append(
                _issue(
                    "TC_UNSUPPORTED_ASSUMPTION",
                    Severity.ERROR,
                    f"Derivation is {case.derivation!r} but no assumption is recorded, so a "
                    "reviewer cannot tell what was supplied beyond the requirement.",
                    case.id,
                )
            )

    return issues


def check_coverage(coverage: CoverageReport) -> list[ValidationIssue]:
    """Coverage findings. Always warnings — no rewording of an existing case can fix a gap."""
    issues: list[ValidationIssue] = []

    for entry in coverage.with_verdict("positive_only"):
        issues.append(
            _issue(
                "COV_POSITIVE_ONLY",
                Severity.WARNING,
                f"States a refusal, error, or limit ({', '.join(entry.dimensions_expected)}) but "
                "only the happy path is tested.",
                entry.requirement_id,
            )
        )
    for entry in coverage.with_verdict("none"):
        issues.append(
            _issue(
                "COV_REQUIREMENT_UNTESTED",
                Severity.WARNING,
                "Was planned for design and received no case at all.",
                entry.requirement_id,
            )
        )
    uncovered = coverage.uncovered_criteria()
    if uncovered:
        issues.append(
            _issue(
                "COV_CRITERION_UNCOVERED",
                Severity.WARNING,
                f"{len(uncovered)} acceptance criterion/criteria are checked by no case, "
                f"beginning with {uncovered[0].requirement_id} criterion "
                f"{uncovered[0].criterion_index}.",
            )
        )
    return issues


@capability("validate_test_specs")
def validate_test_specs(ctx: CapabilityContext) -> CapabilityResult:
    """Judge every case, and report what the suite does not cover."""
    suite = ctx.load("test_suite", TestSuite)
    qualified = ctx.load("qualified_set", QualifiedSet)
    coverage = ctx.load("coverage", CoverageReport)

    issues = check_cases(suite, qualified) + check_coverage(coverage)
    report = GateReport.from_issues(
        "test_gate",
        issues,
        checks_run=len(suite.cases()) * 10 + len(coverage.requirements),
        metrics={
            "cases": len(suite.cases()),
            "repairable_errors": sum(
                1
                for issue in issues
                if issue.severity == Severity.ERROR and issue.code in REPAIRABLE
            ),
        },
    )
    return CapabilityResult(
        gate=report,
        artifacts={"test_gate": report},
        metrics={
            "test_gate_valid": report.valid,
            "test_gate_errors": report.errors,
            "test_gate_warnings": report.warnings,
            "codes": dict(Counter(issue.code for issue in issues)),
        },
    )


@capability("apply_test_repairs")
def apply_test_repairs(ctx: CapabilityContext) -> CapabilityResult:
    """Put repaired cases back and carry everything else across untouched.

    Deterministic, and the reason a rejection over one phrase cannot shrink the whole suite: a case
    the gate never rejected cannot be altered here at all.
    """
    suite = ctx.load("test_suite", TestSuite)
    repairs = ctx.load("test_repairs")
    gate = ctx.load("test_gate", GateReport)

    rejected = {
        issue.subject_id
        for issue in gate.issues
        if issue.severity == Severity.ERROR and issue.subject_id
    }
    replacements = {
        str(entry.get("id")): entry
        for entry in (repairs.get("cases") or [])
        if str(entry.get("id") or "") in rejected
    }
    deleted = {str(value) for value in (repairs.get("deleted") or [])} & rejected

    applied = removed = 0
    for scenario in suite.scenarios:
        kept = []
        for case in scenario.cases:
            if case.id in deleted:
                removed += 1
                suite.deferred.append(
                    DeferredRequirement(
                        requirement_id=case.requirement_ids[0] if case.requirement_ids else "",
                        route="repair_deleted",
                        reason=(
                            f"Case {case.id} was rejected and the repair pass judged that no "
                            "rewording could make it check anything."
                        ),
                    )
                )
                continue
            replacement = replacements.get(case.id)
            if replacement is not None:
                # A falsy value means the repair left the field alone. Applying it would let a
                # pass that fixed only the expected results blank out a perfectly good objective.
                for field in (
                    "title", "objective", "steps", "expected_results",
                    "preconditions", "assumptions", "derivation", "notes",
                ):
                    if replacement.get(field):
                        setattr(case, field, replacement[field])
                if replacement.get("inputs"):
                    from agentic.capabilities.testgen.assemble import _input

                    case.inputs = [
                        _input(entry)
                        for entry in replacement["inputs"]
                        if isinstance(entry, dict)
                    ]
                applied += 1
            kept.append(case)
        scenario.cases = kept
    suite.scenarios = [scenario for scenario in suite.scenarios if scenario.cases]
    suite.counts["cases"] = len(suite.cases())

    return CapabilityResult(
        artifacts={"test_suite": suite},
        metrics={"repairs_applied": applied, "cases_deleted": removed},
        messages=[
            f"Applied {applied} repaired case(s) and removed {removed} the repair pass could not "
            f"rescue, out of {len(rejected)} rejected. Every case the gate did not reject is "
            "carried across unchanged."
        ],
    )


@capability("prune_test_cases")
def prune_test_cases(ctx: CapabilityContext) -> CapabilityResult:
    """Reached only when the repair budget is spent: drop what could not be made to check anything.

    Deterministic — no model gets to argue for keeping a case the gate rejected. Each pruned case
    becomes a recorded gap, so the suite gets thinner and the reason stays on the record.
    """
    suite = ctx.load("test_suite", TestSuite)
    gate = ctx.load("test_gate", GateReport)

    rejected: dict[str, str] = {}
    for issue in gate.issues:
        if issue.severity == Severity.ERROR and issue.subject_id:
            rejected.setdefault(issue.subject_id, f"{issue.code}: {issue.message}")

    pruned = 0
    for scenario in suite.scenarios:
        kept = []
        for case in scenario.cases:
            if case.id in rejected:
                pruned += 1
                suite.deferred.append(
                    DeferredRequirement(
                        requirement_id=case.requirement_ids[0] if case.requirement_ids else "",
                        route="pruned",
                        reason=f"Case {case.id} was removed after repair was exhausted — "
                        + rejected[case.id],
                    )
                )
                continue
            kept.append(case)
        scenario.cases = kept
    suite.scenarios = [scenario for scenario in suite.scenarios if scenario.cases]
    suite.counts["cases"] = len(suite.cases())
    suite.counts["pruned"] = pruned

    return CapabilityResult(
        artifacts={"test_suite": suite},
        metrics={"pruned": pruned, "cases_after": len(suite.cases())},
        messages=[
            f"Removed {pruned} case(s) the repair budget could not rescue. Each is recorded with "
            "the reason rather than deleted, so the suite is thinner and honest instead of "
            "larger and wrong."
        ],
    )


@capability("validate_test_suite")
def validate_test_suite(ctx: CapabilityContext) -> CapabilityResult:
    """The last look, and the only one that can see the finished set.

    Every stage before this changes the suite, so properties of the whole — a duplicate id, a case
    pointing at a scenario that no longer exists, a requirement that lost its last case to pruning
    — are invisible until nothing is going to move again.
    """
    suite = ctx.load("test_suite", TestSuite)
    coverage = ctx.load("coverage", CoverageReport)

    findings: list[dict] = []

    def report(code: str, message: str, subject: str = "") -> None:
        findings.append({"code": code, "message": message, "subject_id": subject})

    cases = suite.cases()
    seen: Counter[str] = Counter(case.id for case in cases)
    for case_id, count in seen.items():
        if count > 1:
            report("DUPLICATE_CASE_ID", f"{count} cases share the id {case_id!r}.", case_id)

    scenario_ids = {scenario.id for scenario in suite.scenarios}
    for case in cases:
        if case.scenario_id not in scenario_ids:
            report(
                "ORPHANED_CASE",
                "Points at a scenario that is no longer in the suite.",
                case.id,
            )
        if not case.covers:
            report("CASE_WITHOUT_COVERAGE_LINK", "Says nothing about what it checks.", case.id)

    # A requirement whose last case was folded, repaired away, or pruned is now uncovered, and
    # nothing upstream knows: coverage ran before those stages.
    covered = suite.covered_requirement_ids()
    for entry in coverage.requirements:
        if entry.verdict in {"sufficient", "thin", "positive_only"} and entry.requirement_id not in covered:
            report(
                "COVERAGE_LOST_DOWNSTREAM",
                "Had cases when coverage was measured and has none now — they were folded, "
                "repaired away, or pruned after the fact.",
                entry.requirement_id,
            )

    ordered = Counter(finding["code"] for finding in findings)
    suite.counts["cases"] = len(cases)
    suite.counts["scenarios"] = len(suite.scenarios)
    suite.counts["final_findings"] = len(findings)

    messages = [
        "The finished suite passed every structural check."
        if not findings
        else "Findings: "
        + ", ".join(f"{code} ({count})" for code, count in ordered.most_common())
        + ". Reported rather than silently corrected."
    ]

    return CapabilityResult(
        artifacts={
            "test_suite": suite,
            "test_findings": {"issues": findings, "counts": dict(ordered)},
        },
        metrics={"final_findings": len(findings), **{f"finding_{k}": v for k, v in ordered.items()}},
        messages=messages,
    )

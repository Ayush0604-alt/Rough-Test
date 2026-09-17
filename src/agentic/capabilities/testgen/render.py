"""Render the finished suite for humans, tools, and future adapters.

Six documents, each for a different reader:

- `test_specs.json`      — the machine contract an adapter or a test-management tool consumes.
- `test_plan.md`         — what a person reads.
- `coverage_matrix.csv`  — one row per requirement, covered or not.
- `traceability_tests.csv` — the requirement -> criterion -> case chain, flattened.
- `excluded_requirements.md` — everything that will not be tested, and why.
- `clarifications.md`    — the questions somebody has to answer before more can be tested.

Always runs, even when little qualified, so a thin suite is explained rather than silent. The last
three exist because the useful half of a test report is the half about what is *not* covered: a
reader can act on "these fifteen requirements have no test and here is why", and cannot act on a
number.

The JSON is a flat array of self-contained cases rather than a nested tree. That is the shape the
requirements pack publishes for the same reason — every downstream use addresses one item by its
id, and a consumer that has to walk a tree to find the preconditions for a case will eventually
forget to. Scenario grouping stays fully recoverable from `scenario_id`.
"""

from __future__ import annotations

import csv
import io
import json
from collections import Counter, defaultdict
from typing import Any

from agentic.capabilities.registry import (
    CapabilityContext,
    CapabilityResult,
    capability,
)
from agentic.capabilities.testgen.quality import framework_leaks
from agentic.schemas.core import GateReport
from agentic.schemas.testgen import QualifiedSet
from agentic.schemas.testspec import CoverageReport, TestSuite

SCHEMA_VERSION = "1.0"

# Verdicts a reader should look at first, worst first. `positive_only` leads because it is the one
# that reads as covered while the behaviour most likely to be wrong is untested.
VERDICT_ORDER = ("positive_only", "none", "thin", "sufficient", "deferred", "excluded")

VERDICT_MEANING = {
    "sufficient": "every activated dimension and every usable criterion is covered",
    "thin": "some activated dimensions have no case",
    "positive_only": "states a refusal, error, or limit, and only the happy path is tested",
    "none": "planned for design and received no case at all",
    "deferred": "routed away from generation, with a reason",
    "excluded": "never eligible for test generation",
}


# ------------------------------------------------------------------------------------------------
# 1. The machine contract
# ------------------------------------------------------------------------------------------------


def build_spec_set(suite: TestSuite, coverage: CoverageReport) -> list[dict[str, Any]]:
    """Every case, self-contained, in a stable key order.

    A case carries its scenario's context and preconditions merged in, because an adapter reading
    one case must not have to resolve a parent to know what state to set up.
    """
    by_requirement = {entry.requirement_id: entry for entry in coverage.requirements}
    published: list[dict[str, Any]] = []

    for scenario in suite.scenarios:
        for case in scenario.cases:
            requirement_id = case.requirement_ids[0] if case.requirement_ids else ""
            entry = by_requirement.get(requirement_id)
            published.append(
                {
                    "id": case.id,
                    "title": case.title,
                    "objective": case.objective,
                    "dimension": case.dimension,
                    "risk": case.risk,
                    "risk_reason": case.risk_reason,
                    # --- traceability ---
                    "requirement_ids": list(case.requirement_ids),
                    "covers": [
                        {"kind": link.kind, "ref": link.ref, "text": link.text}
                        for link in case.covers
                    ],
                    "derivation": case.derivation,
                    "assumptions": list(case.assumptions),
                    # --- the scenario, denormalised so the case stands alone ---
                    "scenario_id": scenario.id,
                    "scenario_title": scenario.title,
                    "scenario_context": scenario.context,
                    "area": scenario.area,
                    # --- what to actually do ---
                    "preconditions": [*scenario.preconditions, *case.preconditions],
                    "inputs": [
                        {
                            "name": entry_input.name,
                            "value_kind": entry_input.value_kind,
                            "description": entry_input.description,
                            "example": entry_input.example,
                        }
                        for entry_input in case.inputs
                    ],
                    "steps": list(case.steps),
                    "expected_results": list(case.expected_results),
                    # --- advisory metadata for a later adapter; nothing here branches on it ---
                    "interface_hint": case.interface_hint,
                    "automation_readiness": case.automation_readiness,
                    "regression_candidate": case.regression_candidate,
                    # --- provenance ---
                    "requirement_coverage_verdict": entry.verdict if entry else "unknown",
                    "superseded": list(case.superseded),
                    "notes": case.notes,
                }
            )
    return published


def render_test_specs_json(suite: TestSuite, coverage: CoverageReport, generator: str) -> str:
    payload = {
        "schema_version": SCHEMA_VERSION,
        "generator": generator,
        "counts": {
            "cases": len(suite.cases()),
            "scenarios": len(suite.scenarios),
            "requirements_covered": len(suite.covered_requirement_ids()),
            "requirements_total": len(coverage.requirements),
        },
        "cases": build_spec_set(suite, coverage),
    }
    return json.dumps(payload, indent=2, ensure_ascii=False) + "\n"


# ------------------------------------------------------------------------------------------------
# 2. Traceability, flattened
# ------------------------------------------------------------------------------------------------


def render_traceability_csv(suite: TestSuite, coverage: CoverageReport) -> str:
    """One row per (case, requirement, covered thing) — the chain a tool walks.

    Exploded rather than nested for the same reason the requirements pack explodes citations: a
    spreadsheet, a traceability tool, and a reviewer filtering by requirement all want one fact
    per row.
    """
    columns = [
        "requirement_id", "requirement_title", "coverage_verdict", "scenario_id", "case_id",
        "dimension", "derivation", "covers_kind", "covers_ref", "covers_text",
        "risk", "regression_candidate", "interface_hint", "automation_readiness",
    ]
    titles = {entry.requirement_id: entry for entry in coverage.requirements}
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(columns)

    rows = 0
    for scenario in suite.scenarios:
        for case in scenario.cases:
            for requirement_id in case.requirement_ids or [""]:
                entry = titles.get(requirement_id)
                head = [
                    requirement_id,
                    entry.title if entry else "",
                    entry.verdict if entry else "",
                    scenario.id,
                    case.id,
                    case.dimension,
                    case.derivation,
                ]
                tail = [
                    case.risk,
                    "yes" if case.regression_candidate else "no",
                    case.interface_hint,
                    case.automation_readiness,
                ]
                if not case.covers:
                    writer.writerow([*head, "", "", "", *tail])
                    rows += 1
                    continue
                for link in case.covers:
                    writer.writerow([*head, link.kind, link.ref, link.text, *tail])
                    rows += 1

    # A requirement with no case still deserves a row: a traceability matrix that only lists what
    # was tested cannot answer the question it exists to answer.
    covered = suite.covered_requirement_ids()
    for entry in coverage.requirements:
        if entry.requirement_id in covered:
            continue
        writer.writerow(
            [entry.requirement_id, entry.title, entry.verdict, "", "", "", "", "", "", entry.reason,
             "", "", "", ""]
        )
        rows += 1

    if rows == 0:
        writer.writerow(["", "", "no test cases were generated", *[""] * (len(columns) - 3)])
    return buffer.getvalue()


# ------------------------------------------------------------------------------------------------
# 3. Coverage, per requirement
# ------------------------------------------------------------------------------------------------


def render_coverage_csv(coverage: CoverageReport) -> str:
    columns = [
        "requirement_id", "title", "band", "route", "verdict", "case_count",
        "dimensions_expected", "dimensions_covered", "dimensions_missing",
        "criteria_total", "criteria_covered", "reason",
    ]
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(columns)

    order = {verdict: index for index, verdict in enumerate(VERDICT_ORDER)}
    for entry in sorted(
        coverage.requirements, key=lambda e: (order.get(e.verdict, 99), e.requirement_id)
    ):
        missing = sorted(set(entry.dimensions_expected) - set(entry.dimensions_covered))
        writer.writerow(
            [
                entry.requirement_id, entry.title, entry.band, entry.route, entry.verdict,
                entry.case_count,
                " ".join(entry.dimensions_expected), " ".join(entry.dimensions_covered),
                " ".join(missing),
                entry.criteria_total, entry.criteria_covered, entry.reason,
            ]
        )
    if not coverage.requirements:
        writer.writerow(["", "no requirements were qualified", *[""] * (len(columns) - 2)])
    return buffer.getvalue()


# ------------------------------------------------------------------------------------------------
# 4. The test plan
# ------------------------------------------------------------------------------------------------


def _case_block(case, scenario) -> list[str]:
    lines = [f"##### `{case.id}` — {case.title or 'Untitled case'}", ""]
    lines.append(f"*{case.dimension}* · risk **{case.risk}** · derivation *{case.derivation}*")
    if case.regression_candidate:
        lines[-1] += " · **regression**"
    lines.append("")
    if case.objective:
        lines += [f"**Why this exists.** {case.objective}", ""]

    preconditions = [*scenario.preconditions, *case.preconditions]
    if preconditions:
        lines.append("**Preconditions**")
        lines += [f"- {text}" for text in preconditions]
        lines.append("")
    if case.inputs:
        lines.append("**Test data**")
        lines += [
            f"- `{entry.name}` ({entry.value_kind})"
            + (f" — {entry.description}" if entry.description else "")
            for entry in case.inputs
        ]
        lines.append("")
    if case.steps:
        lines.append("**Steps**")
        lines += [f"{index}. {text}" for index, text in enumerate(case.steps, start=1)]
        lines.append("")
    if case.expected_results:
        lines.append("**Expected**")
        lines += [f"- {text}" for text in case.expected_results]
        lines.append("")
    if case.assumptions:
        lines.append("**Assumed** (confirm before relying on this case)")
        lines += [f"- {text}" for text in case.assumptions]
        lines.append("")

    covers = ", ".join(
        f"{link.kind}"
        + (f" {link.ref}" if link.ref and link.kind == "acceptance_criterion" else "")
        for link in case.covers
    )
    lines.append(
        f"Validates {', '.join(case.requirement_ids) or '—'}"
        + (f" via {covers}" if covers else "")
        + f" · {case.automation_readiness.replace('_', ' ')}"
    )
    lines.append("")
    return lines


def render_test_plan_md(
    suite: TestSuite, coverage: CoverageReport, qualified: QualifiedSet, gate: GateReport | None
) -> str:
    cases = suite.cases()
    verdicts = coverage.verdicts

    lines = [
        "# Test plan",
        "",
        "Derived from a generated requirements set. Every case names the requirement it validates "
        "and, where one exists, the acceptance criterion it checks. Nothing here names a test "
        "framework, protocol, or language — turning these into runnable tests is a later step.",
        "",
        "## In one line",
        "",
        f"**{len(cases)} test case(s)** across **{len(suite.scenarios)} scenario(s)**, covering "
        f"**{len(suite.covered_requirement_ids())} of {len(coverage.requirements)}** requirement(s).",
        "",
        "## Coverage",
        "",
        "| Verdict | Requirements | What it means |",
        "|---|---|---|",
    ]
    for verdict in VERDICT_ORDER:
        if verdicts.get(verdict):
            lines.append(f"| `{verdict}` | {verdicts[verdict]} | {VERDICT_MEANING[verdict]} |")
    lines.append("")

    # Read this first: it is the coverage failure that hides rather than announces itself.
    positive_only = coverage.with_verdict("positive_only")
    if positive_only:
        lines += [
            "### Read these first",
            "",
            f"{len(positive_only)} requirement(s) state a refusal, an error, or a limit and have "
            "only happy-path coverage. They read as covered while the behaviour most likely to be "
            "wrong is untested.",
            "",
        ]
        lines += [
            f"- `{entry.requirement_id}` {entry.title} — missing "
            + ", ".join(sorted(set(entry.dimensions_expected) - set(entry.dimensions_covered)))
            for entry in positive_only
        ]
        lines.append("")

    uncovered = coverage.uncovered_criteria()
    if uncovered:
        lines += [
            "### Acceptance criteria nothing checks",
            "",
            f"{len(uncovered)} of {len(coverage.criteria)} criteria are covered by no case. Each "
            "is a condition somebody wrote down and nothing verifies.",
            "",
        ]
        for entry in uncovered[:20]:
            lines.append(f"- `{entry.requirement_id}` [{entry.criterion_index}] {entry.criterion_text}")
        if len(uncovered) > 20:
            lines.append(f"- …and {len(uncovered) - 20} more, listed in `coverage_matrix.csv`.")
        lines.append("")

    if gate is not None and gate.warnings:
        lines += ["### Reported by the quality gate", ""]
        seen: Counter[str] = Counter(issue.code for issue in gate.issues)
        lines += [f"- `{code}` × {count}" for code, count in seen.most_common()]
        lines.append("")

    # The cases themselves, grouped by area so a reader works through one part of the product at a
    # time rather than jumping between them.
    by_area: dict[str, list] = defaultdict(list)
    for scenario in suite.scenarios:
        by_area[scenario.area or "Ungrouped"].append(scenario)

    lines += ["## The cases", ""]
    for area in sorted(by_area):
        area_cases = sum(len(scenario.cases) for scenario in by_area[area])
        lines += [f"### {area} — {area_cases} case(s)", ""]
        for scenario in by_area[area]:
            lines += [f"#### `{scenario.id}` — {scenario.title or 'Untitled scenario'}", ""]
            if scenario.context:
                lines += [f"*{scenario.context}*", ""]
            lines.append(f"Requirement: `{scenario.requirement_id}`")
            lines.append("")
            for case in scenario.cases:
                lines += _case_block(case, scenario)

    if not cases:
        lines += [
            "No test case was generated. `excluded_requirements.md` and `clarifications.md` "
            "explain why, requirement by requirement. An empty suite that says why is a result; "
            "one that does not is a failure.",
            "",
        ]
    return "\n".join(lines).rstrip() + "\n"


# ------------------------------------------------------------------------------------------------
# 5. What will not be tested, and what has to be answered first
# ------------------------------------------------------------------------------------------------


def render_excluded_md(suite: TestSuite, coverage: CoverageReport, qualified: QualifiedSet) -> str:
    """Every requirement that produced no case, grouped by why.

    This is half the value of the whole pack. A reviewer can act on "these are untested and here is
    the reason"; nobody can act on a case count.
    """
    lines = [
        "# Requirements with no test coverage",
        "",
        "Every requirement here produced no test case. Each says why. A requirement recorded as "
        "untested is a finding somebody can act on; one quietly missing is not.",
        "",
    ]

    grouped: dict[str, list] = defaultdict(list)
    for record in qualified.records:
        entry = next(
            (e for e in coverage.requirements if e.requirement_id == record.requirement_id), None
        )
        if entry is None or entry.verdict not in {"excluded", "deferred", "none"}:
            continue
        code = record.reason_codes[0] if record.reason_codes else entry.verdict
        grouped[code].append((record, entry))

    if not grouped:
        lines.append("Every qualified requirement received at least one test case.")
        return "\n".join(lines) + "\n"

    for code in sorted(grouped, key=lambda key: -len(grouped[key])):
        entries = grouped[code]
        lines += [f"## `{code}` — {len(entries)} requirement(s)", ""]
        lines += [entries[0][0].reason, ""] if entries[0][0].reason else []
        lines += ["| Requirement | Title | Band |", "|---|---|---|"]
        for record, entry in entries:
            lines.append(f"| `{record.requirement_id}` | {entry.title} | {record.band.value} |")
        lines.append("")

    pruned = [entry for entry in suite.deferred if entry.route in {"pruned", "repair_deleted"}]
    if pruned:
        lines += [
            "## Cases removed after generation",
            "",
            "These were written and then removed, because no rewording could make them check "
            "anything. The requirement may still have other coverage.",
            "",
        ]
        lines += [f"- `{entry.requirement_id}` — {entry.reason}" for entry in pruned]
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def render_clarifications_md(qualified: QualifiedSet) -> str:
    """The questions that have to be answered before more can be tested."""
    needing = [
        record
        for record in qualified.records
        if record.band.value == "needs_clarification" or record.blockers
    ]
    lines = [
        "# Questions to settle",
        "",
        "Each requirement below could not be turned into a falsifiable test as written. These are "
        "questions for whoever owns the requirement, not defects in it.",
        "",
    ]
    if not needing:
        lines.append("No requirement raised a blocking question.")
        return "\n".join(lines) + "\n"

    for record in needing:
        lines += [f"## `{record.requirement_id}`", ""]
        if record.reason:
            lines += [record.reason, ""]
        for question in record.blockers:
            lines.append(f"- {question}")
        if record.discarded_criteria:
            lines += [
                "",
                f"{len(record.discarded_criteria)} acceptance criterion/criteria were discarded as "
                "generated rather than stated, so they could not be tested against:",
                "",
            ]
            lines += [f"  - {text}" for text in record.discarded_criteria]
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


# ------------------------------------------------------------------------------------------------
# The capability
# ------------------------------------------------------------------------------------------------


def _generator(ctx: CapabilityContext) -> str:
    settings = ctx.settings
    deployment = getattr(settings, "azure_openai_deployment", "") or "unknown"
    return f"llm:azure/{deployment}"


@capability("render_test_deliverables")
def render_test_deliverables(ctx: CapabilityContext) -> CapabilityResult:
    """Write every deliverable, and say what is wrong with the suite it just published."""
    suite = ctx.load("test_suite", TestSuite)
    coverage = ctx.load("coverage", CoverageReport)
    qualified = ctx.load("qualified_set", QualifiedSet)
    try:
        gate = ctx.load("test_gate", GateReport)
    except (KeyError, OSError, ValueError):
        # The gate is on the repair path; a run that never reached it still gets deliverables.
        gate = None

    documents = {
        "test_specs.json": render_test_specs_json(suite, coverage, _generator(ctx)),
        "test_plan.md": render_test_plan_md(suite, coverage, qualified, gate),
        "coverage_matrix.csv": render_coverage_csv(coverage),
        "traceability_tests.csv": render_traceability_csv(suite, coverage),
        "excluded_requirements.md": render_excluded_md(suite, coverage, qualified),
        "clarifications.md": render_clarifications_md(qualified),
    }

    files = {name: ctx.store.save_text(name, text) for name, text in documents.items()}

    messages: list[str] = []

    # Check the independence claim at the moment the document leaves the building, not only
    # upstream. This is the file an adapter will consume, so it is the one that matters.
    leaks = [
        f"{case.id}: {', '.join(found)}"
        for case in suite.cases()
        if (found := framework_leaks(case.title, case.objective, *case.steps, *case.expected_results))
    ]
    if leaks:
        messages.append(
            f"Technology reached {len(leaks)} published case(s), which binds them to one stack: "
            + "; ".join(leaks[:3])
        )

    empty = [name for name, text in documents.items() if not text.strip()]
    if empty:
        messages.append(f"Rendered empty: {', '.join(empty)}.")

    if not suite.cases():
        messages.append(
            "No test case was published. `excluded_requirements.md` and `clarifications.md` "
            "explain why, requirement by requirement."
        )

    counts = Counter(entry.verdict for entry in coverage.requirements)
    messages.append(
        f"Published {len(suite.cases())} case(s) over "
        f"{len(suite.covered_requirement_ids())}/{len(coverage.requirements)} requirement(s): "
        + ", ".join(f"{count} {verdict}" for verdict, count in sorted(counts.items()))
        + "."
    )

    return CapabilityResult(
        artifacts={"deliverables": {"files": files, "warnings": messages}},
        files=files,
        metrics={
            "documents": len(files),
            "cases_published": len(suite.cases()),
            "framework_leaks": len(leaks),
            **{f"verdict_{verdict}": count for verdict, count in counts.items()},
        },
        messages=messages,
    )

"""Turn the workers' drafts into the assembled suite.

Deterministic, and it does three things a model must not: it stamps identifiers, it computes every
derived field, and it drops anything that cannot be traced. Asking a designer for a field that can
be computed invites it to be inconsistent between one call and the next; asking it to police its
own output invites it to keep what it produced.

Nothing is silently discarded. A case that cannot be traced to a requirement leaves the suite via
`notes`, with its title and the reason.
"""

from __future__ import annotations

from collections import Counter, defaultdict

from agentic.capabilities.registry import (
    CapabilityContext,
    CapabilityResult,
    capability,
)
from agentic.schemas.testgen import QualifiedSet
from agentic.schemas.testspec import (
    AutomationReadiness,
    CoverageLink,
    DeferredRequirement,
    InterfaceHint,
    Route,
    TestCase,
    TestScenario,
    TestSignalSet,
    TestSuite,
)

# Dimensions whose cases are worth re-running on every change: a rule, an invariant, or an access
# condition breaking is a regression that a feature-level test would not notice.
REGRESSION_DIMENSIONS = frozenset({"business_rule", "data_integrity", "authorization"})

# Dimensions that need a dependency standing up before the case can run.
ENVIRONMENT_DIMENSIONS = frozenset({"integration", "exceptional"})


def automation_readiness(case: TestCase) -> str:
    """What stands between this specification and an automated test.

    Computed from the case's own shape, so it stays true if the case is edited. Advisory only —
    nothing in this pack branches on it.
    """
    if case.dimension in ENVIRONMENT_DIMENSIONS:
        return AutomationReadiness.NEEDS_ENVIRONMENT.value
    if case.preconditions or any(i.value_kind != "valid" for i in case.inputs):
        return AutomationReadiness.NEEDS_DATA.value
    if case.interface_hint == InterfaceHint.UNKNOWN.value:
        return AutomationReadiness.MANUAL_ONLY.value
    return AutomationReadiness.READY.value


def _coverage(draft, signal) -> list[CoverageLink]:
    """What this case checks, resolved against the criteria the designer was actually shown."""
    index = draft.covers_criterion
    if index is not None and 0 <= index < len(signal.usable_criteria):
        return [
            CoverageLink(
                kind="acceptance_criterion",
                ref=str(index),
                text=signal.usable_criteria[index],
            )
        ]
    # No criterion, or one that does not resolve: the case rests on the statement, and saying so
    # is what lets coverage analysis tell a grounded case from a derived one.
    return [CoverageLink(kind="statement", ref=signal.requirement_id, text="")]


def assemble(qualified: QualifiedSet, signals: TestSignalSet, fragments: list[dict]) -> TestSuite:
    by_signal = signals.by_id()
    notes: list[str] = []
    dropped = Counter()

    # Group drafts by requirement so scenario numbering is per requirement and stable, whatever
    # order the parallel workers happened to finish in.
    drafts_by_requirement: dict[str, list] = defaultdict(list)
    for fragment in fragments:
        for note in fragment.get("notes") or []:
            notes.append(str(note))
        for scenario in fragment.get("scenarios") or []:
            requirement_id = str(scenario.get("requirement_id") or "").strip()
            if requirement_id not in by_signal:
                dropped["unknown_requirement"] += 1
                notes.append(
                    f"A scenario named requirement {requirement_id!r}, which is not in the "
                    "qualified set. Dropped rather than published against nothing."
                )
                continue
            if by_signal[requirement_id].route != Route.GENERATE.value:
                dropped["not_for_generation"] += 1
                notes.append(
                    f"A scenario was returned for {requirement_id}, which analysis routed away "
                    "from case generation. Dropped, because the routing decision is not the "
                    "designer's to overturn."
                )
                continue
            drafts_by_requirement[requirement_id].append(scenario)

    scenarios: list[TestScenario] = []
    # Numbered per *area*, not per requirement. Counting per requirement while stemming the id
    # from the area collides the moment one area holds two requirements — `FR-UIA-01` and
    # `FR-UIA-02` both produced `TS-UIA-01`, and with it a duplicate case id, which makes every
    # traceability link into those cases ambiguous. Sorting by requirement id keeps it reproducible.
    per_area: Counter[str] = Counter()
    for requirement_id in sorted(drafts_by_requirement):
        signal = by_signal[requirement_id]
        stem = _slug(signal.area, requirement_id)
        for draft in drafts_by_requirement[requirement_id]:
            per_area[stem] += 1
            scenario_id = f"TS-{stem}-{per_area[stem]:02d}"
            cases: list[TestCase] = []
            for position, case_draft in enumerate(draft.get("cases") or [], start=1):
                case = _build_case(case_draft, signal, scenario_id, position, dropped, notes)
                if case is not None:
                    cases.append(case)
            if not cases:
                dropped["empty_scenario"] += 1
                continue
            scenarios.append(
                TestScenario(
                    id=scenario_id,
                    requirement_id=requirement_id,
                    title=str(draft.get("title") or "").strip(),
                    context=str(draft.get("context") or "").strip(),
                    preconditions=[str(p) for p in (draft.get("preconditions") or [])],
                    cases=cases,
                    area=signal.area,
                    band=signal.band,
                )
            )

    deferred = [
        DeferredRequirement(
            requirement_id=signal.requirement_id,
            route=signal.route,
            reason=signal.deferral_reason,
            band=signal.band,
            attach_to=signal.attach_to,
        )
        for signal in signals.signals
        if signal.route != Route.GENERATE.value
    ]

    # A requirement that was planned for and came back with nothing is a real gap, and it is
    # invisible unless something looks for it: the worker simply did not mention it.
    produced = {scenario.requirement_id for scenario in scenarios}
    for signal in signals.generating():
        if signal.requirement_id not in produced:
            deferred.append(
                DeferredRequirement(
                    requirement_id=signal.requirement_id,
                    route="undesigned",
                    reason=(
                        "Planned for design but no scenario was returned for it. Recorded as "
                        "uncovered rather than assumed covered."
                    ),
                    band=signal.band,
                )
            )

    suite = TestSuite(scenarios=scenarios, deferred=deferred, notes=notes)
    suite.counts = {
        "scenarios": len(scenarios),
        "cases": len(suite.cases()),
        "requirements_covered": len(produced),
        "deferred": len(deferred),
        **{f"dropped_{reason}": count for reason, count in dropped.items()},
    }
    return suite


def _build_case(draft: dict, signal, scenario_id: str, position: int, dropped, notes) -> TestCase | None:
    dimension = str(draft.get("dimension") or "").strip().lower()
    if dimension not in signal.dimensions:
        # The dimension set was decided from what the requirement says. A case outside it is
        # testing something nobody asked for, which is exactly the explosion this pack avoids.
        dropped["dimension_not_activated"] += 1
        notes.append(
            f"A case for {signal.requirement_id} claimed dimension {dimension!r}, which that "
            "requirement does not activate. Dropped."
        )
        return None

    case = TestCase(
        id=f"{scenario_id}-{position:02d}".replace("TS-", "TC-", 1),
        scenario_id=scenario_id,
        requirement_ids=[signal.requirement_id],
        covers=_coverage(_Draft(draft), signal),
        dimension=dimension,
        title=str(draft.get("title") or "").strip(),
        objective=str(draft.get("objective") or "").strip(),
        preconditions=[str(p) for p in (draft.get("preconditions") or [])],
        inputs=[_input(entry) for entry in (draft.get("inputs") or []) if isinstance(entry, dict)],
        steps=[str(s) for s in (draft.get("steps") or [])],
        expected_results=[str(e) for e in (draft.get("expected_results") or [])],
        derivation=str(draft.get("derivation") or signal.derivation_default),
        assumptions=[str(a) for a in (draft.get("assumptions") or [])],
        risk=signal.risk,
        risk_reason=str(draft.get("risk_reason") or signal.risk_reason),
        notes=str(draft.get("notes") or ""),
        interface_hint=signal.interface_hint,
    )
    case.regression_candidate = dimension in REGRESSION_DIMENSIONS or (
        dimension == "positive" and case.derivation == "direct"
    )
    case.automation_readiness = automation_readiness(case)
    return case


class _Draft:
    """Adapter so `_coverage` reads the same whether given a model or a plain mapping."""

    def __init__(self, raw: dict) -> None:
        value = raw.get("covers_criterion")
        self.covers_criterion = value if isinstance(value, int) else None


def _input(entry: dict):
    from agentic.schemas.testspec import TestInput

    return TestInput(
        name=str(entry.get("name") or ""),
        value_kind=str(entry.get("value_kind") or "valid"),
        description=str(entry.get("description") or ""),
        example=entry.get("example") if isinstance(entry.get("example"), str) else None,
    )


def _slug(area: str | None, requirement_id: str) -> str:
    """Identifier stem: the area where there is one, else the requirement's own id.

    Ids are how a reviewer cites a case and how a later run is diffed against this one, so they are
    positional and reproducible rather than random.
    """
    return (area or requirement_id.split("-")[0] or "GEN").upper()


@capability("assemble_test_specs")
def assemble_test_specs(ctx: CapabilityContext) -> CapabilityResult:
    """Collect every worker's drafts into one identified, traceable suite."""
    qualified = ctx.load("qualified_set", QualifiedSet)
    signals = ctx.load("test_signals", TestSignalSet)
    fragments = ctx.state.get("fragments") or []

    suite = assemble(qualified, signals, fragments)

    messages = [
        f"Assembled {suite.counts['cases']} case(s) in {suite.counts['scenarios']} scenario(s) "
        f"across {suite.counts['requirements_covered']} requirement(s)."
    ]
    dropped = {k: v for k, v in suite.counts.items() if k.startswith("dropped_")}
    if dropped:
        messages.append(
            "Dropped during assembly: "
            + ", ".join(f"{k.removeprefix('dropped_')} ({v})" for k, v in dropped.items())
            + ". Each is recorded in the suite's notes rather than removed silently."
        )
    undesigned = sum(1 for entry in suite.deferred if entry.route == "undesigned")
    if undesigned:
        messages.append(
            f"{undesigned} requirement(s) were planned for design and came back with nothing. "
            "Recorded as uncovered — a worker that says nothing about a requirement is a gap, not "
            "a requirement that needed no tests."
        )

    return CapabilityResult(
        artifacts={"test_suite": suite},
        metrics={**suite.counts, "fragments_received": len(fragments)},
        messages=messages,
    )

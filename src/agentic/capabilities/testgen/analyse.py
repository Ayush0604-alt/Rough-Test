"""Decide, per requirement, what may be designed and how much of it.

This is the other half of the anti-explosion mechanism. Qualification decided *whether* a
requirement may be tested; this decides *how many* cases it is worth and *which* dimensions they
may cover — and it does so deterministically, before any model call. The designer receives a
budget and a dimension set it did not choose.

Measured on the rich fixture: without the routing rule below, 184 testable requirements project to
485 cases. With it, 280. The difference is almost entirely internal behaviour that nothing outside
the system can observe.
"""

from __future__ import annotations

import re
from collections import Counter

from agentic.capabilities.registry import (
    CapabilityContext,
    CapabilityResult,
    capability,
)
from agentic.schemas.testgen import Band, IngestedRequirement, QualifiedSet
from agentic.schemas.testspec import InterfaceHint, Route, TestSignal, TestSignalSet

# Dimensions whose presence means a failure here has consequences beyond one broken screen: a rule
# not enforced, an unauthorised action allowed, an invariant violated.
HIGH_RISK_DIMENSIONS = frozenset({"business_rule", "authorization", "data_integrity"})

# A requirement saying this much is describing something with real surface area.
BROAD_DIMENSION_COUNT = 4

# Case ceilings by risk. Not targets — a requirement activating two dimensions gets two cases even
# though its ceiling is eight.
RISK_CAPS = {"high": 8, "medium": 5, "low": 2}

# Internal behaviour is capped hard. It is testable only through something else, so a large budget
# would buy elaborate tests of a thing no actor can observe.
INTERNAL_CAP = 2

# A traced path beginning like this is being reached over a request/response boundary. Matched on
# *shape* — a verb-like token followed by a path — never on any framework's name, so a product
# using any web stack, or none, is read the same way.
_HTTP_BOUNDARY = re.compile(r"^[A-Z]{3,7}\s+/")
_CLI_BOUNDARY = re.compile(r"!cli\b|\bcli\b|__main__", re.I)
_UI_BOUNDARY = re.compile(r'^\s*"|\baction\b|\bbutton\b|\bclick\b', re.I)
_SCHEDULED_BOUNDARY = re.compile(r"\bcron\b|\bschedul\w*|\bjob\b", re.I)


def interface_hint(requirement: IngestedRequirement) -> str:
    """The shape of the boundary this requirement is reached through.

    Advisory metadata for a future adapter. Nothing in this pack branches on it, which is what
    keeps the specification layer independent of any stack.
    """
    if not requirement.graph_path:
        return InterfaceHint.UNKNOWN.value
    boundary = requirement.graph_path[0].strip()
    if _HTTP_BOUNDARY.match(boundary):
        return InterfaceHint.HTTP_ENDPOINT.value
    if _SCHEDULED_BOUNDARY.search(boundary):
        return InterfaceHint.SCHEDULED.value
    if _CLI_BOUNDARY.search(boundary):
        return InterfaceHint.COMMAND_LINE.value
    if _UI_BOUNDARY.search(boundary):
        return InterfaceHint.UI_ACTION.value
    return InterfaceHint.INTERNAL.value


def assess_risk(requirement: IngestedRequirement, dimensions: list[str]) -> tuple[str, str]:
    """How much it matters if this behaviour is wrong.

    Read off what the requirement asserts, never off `confidence` — that field is nearly uniform
    in practice (one value covers 98% of a measured set), so using it would make every requirement
    the same risk.
    """
    high = HIGH_RISK_DIMENSIONS & set(dimensions)
    if high:
        return "high", (
            f"Asserts {', '.join(sorted(high)).replace('_', ' ')}, where a failure is a rule not "
            "enforced rather than a feature not working."
        )
    if len(dimensions) >= BROAD_DIMENSION_COUNT:
        return "high", (
            f"Covers {len(dimensions)} distinct behaviours, so a single defect can surface in "
            "several ways."
        )
    if requirement.type == "system":
        return "low", "Internal behaviour, observable only through the capability it supports."
    if len(dimensions) <= 1:
        return "low", "States a single behaviour with no stated conditions around it."
    return "medium", "States observable behaviour with some conditions around it."


def route_for(
    requirement: IngestedRequirement,
    usable_criteria: list[str],
    known_ids: set[str],
) -> tuple[Route, str | None, str]:
    """What happens to this requirement: cases, an attachment, or a recorded gap.

    Internal behaviour is the case that matters. Half a measured requirement set sits at that tier,
    and by definition no outside actor observes it directly, so generating a stakeholder-facing
    case for each would produce a large suite of tests nobody can run. Three routes:

    a. A usable acceptance criterion survives -> design against that criterion. Something stated an
       observable outcome, so there is a real check here.
    b. A resolvable parent capability exists -> attach as an extra assertion on that capability's
       cases rather than standing alone.
    c. Neither -> defer, and say so. An untested requirement recorded as untested is a finding; a
       case that cannot be run is worse than the gap it hides.

    In practice route (b) is nearly always unavailable — on the measured set only 1 of 92 internal
    requirements had a resolvable parent, because the requirements pack's hierarchy is 99% absent.
    It is implemented anyway because it costs little and a future requirements pack may populate it.
    """
    if requirement.type != "system":
        return Route.GENERATE, None, ""

    if usable_criteria:
        return Route.GENERATE, None, ""

    parent = requirement.parent_id
    if parent and parent in known_ids:
        return Route.ATTACH, parent, (
            "Internal behaviour with no stated observable outcome of its own. Folded into the "
            f"capability it enables ({parent}) as an additional assertion rather than a case that "
            "no actor could run."
        )

    return Route.DEFERRED, None, (
        "Internal behaviour that no outside actor observes, with no acceptance criterion stating "
        "an observable outcome and no resolvable parent capability to attach to. Recorded as an "
        "uncovered requirement rather than given a case that could not be executed."
    )


def budget_for(dimensions: list[str], risk: str, internal: bool) -> int:
    """One case per activated dimension, capped by risk.

    Deliberately not combinatorial. Several inputs that fail for one reason are one case, not one
    per value — equivalence partitioning is the rule, and boundaries get their own dimension.
    """
    wanted = max(1, len(dimensions))
    cap = INTERNAL_CAP if internal else RISK_CAPS.get(risk, RISK_CAPS["medium"])
    return min(wanted, cap)


def build_signals(qualified: QualifiedSet) -> TestSignalSet:
    by_id = {item.id: item for item in qualified.requirements}
    known_ids = {
        record.requirement_id for record in qualified.records if record.testable
    }

    signals: list[TestSignal] = []
    for record in qualified.records:
        requirement = by_id.get(record.requirement_id)
        if requirement is None:
            continue

        if not record.testable:
            signals.append(
                TestSignal(
                    requirement_id=record.requirement_id,
                    band=record.band.value,
                    route=Route.DEFERRED.value,
                    deferral_reason=record.reason,
                    area=requirement.area,
                )
            )
            continue

        dimensions = record.activated_dimensions
        route, attach_to, reason = route_for(requirement, record.usable_criteria, known_ids)
        risk, risk_reason = assess_risk(requirement, dimensions)
        internal = requirement.type == "system"

        signals.append(
            TestSignal(
                requirement_id=record.requirement_id,
                band=record.band.value,
                route=route.value,
                attach_to=attach_to,
                deferral_reason=reason,
                dimensions=dimensions if route is Route.GENERATE else [],
                risk=risk,
                risk_reason=risk_reason,
                case_budget=(
                    budget_for(dimensions, risk, internal) if route is Route.GENERATE else 0
                ),
                interface_hint=interface_hint(requirement),
                actor=requirement.persona,
                usable_criteria=record.usable_criteria,
                derivation_default=(
                    "direct"
                    if record.usable_criteria and record.band is Band.ELIGIBLE
                    else "assumed"
                    if record.band is Band.ELIGIBLE_WITH_ASSUMPTIONS
                    else "inferred"
                ),
                area=requirement.area,
                area_name=requirement.area_name,
            )
        )

    counts = Counter(signal.route for signal in signals)
    return TestSignalSet(
        signals=signals,
        counts={
            **{f"route_{route}": count for route, count in counts.items()},
            "projected_cases": sum(signal.case_budget for signal in signals),
        },
    )


@capability("analyse_test_signals")
def analyse_test_signals(ctx: CapabilityContext) -> CapabilityResult:
    """Compute the dimension set, risk, and case budget for every requirement."""
    qualified = ctx.load("qualified_set", QualifiedSet)
    signal_set = build_signals(qualified)

    generating = [s for s in signal_set.signals if s.route == Route.GENERATE.value]
    attaching = [s for s in signal_set.signals if s.route == Route.ATTACH.value]
    deferred = [s for s in signal_set.signals if s.route == Route.DEFERRED.value]
    projected = sum(signal.case_budget for signal in generating)

    dimension_counts = Counter(d for signal in generating for d in signal.dimensions)
    risk_counts = Counter(signal.risk for signal in generating)

    messages = [
        f"{len(generating)} requirement(s) will be designed against, projecting {projected} "
        f"case(s); {len(attaching)} attach to a parent capability; {len(deferred)} are recorded as "
        "uncovered with a reason."
    ]
    if generating:
        average = sum(len(s.dimensions) for s in generating) / len(generating)
        messages.append(
            f"Mean {average:.1f} dimension(s) activated per requirement — each one earned by "
            "something the requirement actually says, so a requirement stating little gets few "
            "cases rather than a full sweep of every category."
        )
    internal_deferred = sum(
        1 for s in deferred if s.band == Band.ELIGIBLE_WITH_ASSUMPTIONS.value
    )
    if internal_deferred:
        messages.append(
            f"{internal_deferred} requirement(s) qualified for testing but describe behaviour no "
            "outside actor observes, with nothing stating an observable outcome. Each is an "
            "honest gap rather than a case that could not be run."
        )

    return CapabilityResult(
        artifacts={"test_signals": signal_set},
        metrics={
            "generating": len(generating),
            "attaching": len(attaching),
            "deferred": len(deferred),
            "projected_cases": projected,
            "dimension_counts": dict(dimension_counts),
            "risk_counts": dict(risk_counts),
        },
        messages=messages,
    )

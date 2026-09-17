"""Group requirements into bounded packets, one model call each.

This is the cost-control step, and it is the reason the pack scales with the size of a requirements
set rather than falling over on a large one. Each worker sees a handful of related requirements and
their computed budgets — never the whole set, and never the codebase.

Batches are grouped by **area**, because that is what makes the cases in one call coherent: a
designer looking at six requirements about one part of the product can see that two of them share a
precondition. Area is available on 189 of 190 items in a measured set, which is exactly why the
requirements pack was changed to publish it — grouping previously had to be reconstructed by
parsing functional ids, a scheme two thirds of the tiers do not use.
"""

from __future__ import annotations

from collections import defaultdict

from agentic.capabilities.registry import (
    CapabilityContext,
    CapabilityResult,
    capability,
)
from agentic.schemas.testgen import QualifiedSet
from agentic.schemas.testspec import TestSignalSet

# Bounds on one packet. Sized against the model's *output* ceiling rather than its input: a batch
# is discarded whole when the reply overruns, so the binding constraint is how many cases it has
# been asked to write, not how much it was shown.
MAX_CASES_PER_BATCH = 14
MAX_REQUIREMENTS_PER_BATCH = 6

UNGROUPED = "_"


def build_batches(qualified: QualifiedSet, signals: TestSignalSet) -> list[dict]:
    """One packet per bounded group of same-area requirements, in a stable order."""
    by_id = {item.id: item for item in qualified.requirements}
    by_area: dict[str, list] = defaultdict(list)
    for signal in signals.generating():
        by_area[signal.area or UNGROUPED].append(signal)

    batches: list[dict] = []
    for area in sorted(by_area):
        # Highest-risk first, then by id, so a batch that has to be split keeps the most valuable
        # requirements together and the split is reproducible.
        ordered = sorted(
            by_area[area],
            key=lambda s: ({"high": 0, "medium": 1, "low": 2}.get(s.risk, 1), s.requirement_id),
        )
        current: list = []
        budget = 0
        for signal in ordered:
            over_cases = budget + signal.case_budget > MAX_CASES_PER_BATCH
            over_count = len(current) >= MAX_REQUIREMENTS_PER_BATCH
            if current and (over_cases or over_count):
                batches.append(_packet(area, current, by_id, len(batches)))
                current, budget = [], 0
            current.append(signal)
            budget += signal.case_budget
        if current:
            batches.append(_packet(area, current, by_id, len(batches)))
    return batches


def _packet(area: str, signals: list, by_id: dict, index: int) -> dict:
    """What one worker is shown. Everything it needs, and nothing it should not decide.

    The outer keys are fixed by the generic fan-out contract, not chosen here:
    `llm/prompt.py:build_user_payload` reads `boundary`, `boundary_kind`, `boundary_location`,
    `also_exposed_by`, and — the one that matters — `packet`, which is the payload a worker
    actually receives. Putting the content anywhere else means every worker is handed `None`, and
    because a fan-out worker's failure is deliberately isolated so one bad boundary cannot discard
    the rest, that arrives as an empty suite rather than as an error.
    """
    area_name = next((s.area_name for s in signals if s.area_name), None)
    label = area_name or (None if area == UNGROUPED else area) or "Ungrouped requirements"
    return {
        "batch_id": f"B{index + 1:03d}",
        "area": None if area == UNGROUPED else area,
        "area_name": area_name,
        "total_budget": sum(signal.case_budget for signal in signals),
        # --- the generic fan-out contract ---
        "boundary": label,
        "boundary_kind": "requirement_area",
        "packet": _content(area, area_name, signals, by_id, index),
    }


def _content(area: str, area_name: str | None, signals: list, by_id: dict, index: int) -> dict:
    """The evidence packet itself — one area's requirements and the plan computed for each."""
    return {
        "batch_id": f"B{index + 1:03d}",
        "area": None if area == UNGROUPED else area,
        "area_name": area_name,
        "total_budget": sum(signal.case_budget for signal in signals),
        "requirements": [
            {
                "requirement_id": signal.requirement_id,
                "title": by_id[signal.requirement_id].title,
                "statement": by_id[signal.requirement_id].statement,
                "actor": signal.actor,
                # Only the criteria that survived qualification. The generated ones are absent on
                # purpose: showing a designer a criterion that asserts nothing invites a case that
                # checks nothing.
                "acceptance_criteria": signal.usable_criteria,
                "dimensions_to_cover": signal.dimensions,
                "case_budget": signal.case_budget,
                "risk": signal.risk,
                "risk_reason": signal.risk_reason,
                "derivation_default": signal.derivation_default,
                "band": signal.band,
                # Context a designer can use for wording, never for inventing behaviour.
                "business_value": by_id[signal.requirement_id].business_value,
            }
            for signal in signals
            if signal.requirement_id in by_id
        ],
    }


@capability("build_test_batches")
def build_test_batches(ctx: CapabilityContext) -> CapabilityResult:
    """Partition the generating requirements into bounded, area-coherent packets."""
    qualified = ctx.load("qualified_set", QualifiedSet)
    signals = ctx.load("test_signals", TestSignalSet)

    batches = build_batches(qualified, signals)
    generating = len(signals.generating())

    if not batches:
        # Not a failure: a set where everything defers is a real outcome, and the coverage report
        # is the right place for it to be explained.
        return CapabilityResult(
            artifacts={"test_batches": []},
            messages=[
                "No requirement reached the design stage: every one was excluded, attached, or "
                "recorded as an uncovered gap. The coverage report explains each."
            ],
            metrics={"batches": 0, "requirements_batched": 0},
        )

    areas = {batch["area"] for batch in batches}
    return CapabilityResult(
        artifacts={"test_batches": batches},
        metrics={
            "batches": len(batches),
            "requirements_batched": sum(len(b["packet"]["requirements"]) for b in batches),
            "projected_cases": sum(b["total_budget"] for b in batches),
            "areas": len(areas),
        },
        messages=[
            f"Planned {len(batches)} model call(s) over {generating} requirement(s) across "
            f"{len(areas)} area(s), projecting {sum(b['total_budget'] for b in batches)} case(s). "
            f"Each call is bounded to {MAX_CASES_PER_BATCH} cases so one oversized reply cannot "
            "lose a whole area's analysis."
        ],
    )

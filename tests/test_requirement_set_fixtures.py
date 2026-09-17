"""The frozen input contract, captured from real runs.

The test-generation pack consumes `requirements.json` and nothing else. These fixtures are that
input, taken from real runs rather than hand-written, because the whole design problem is that the
artifact is imperfect in *specific, measurable* ways — boilerplate acceptance criteria, a hierarchy
that is almost entirely absent, half the set sitting at a tier nothing outside the system can
observe. A hand-written fixture would quietly assume those problems away.

The numbers asserted here are the ones the phase exit criteria are stated in, which turns
"qualification must account for 190/190" from prose into something that fails.

`legacy_20key.json` is deliberately the *pre-fix* export, before `area`, `area_name`, and
`priority` were published. Runs already on disk are in that shape, so ingestion reads both.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.test_requirements_json import REFERENCE_KEYS

FIXTURES = Path(__file__).parent / "fixtures" / "requirement_sets"

# Acceptance criteria the requirements pack generated *for itself* when the evidence stated no
# observable outcome (`capabilities/hedging.py`). Syntactically criteria, semantically nothing,
# and 31% of the criteria in the rich fixture.
BOILERPLATE_PREFIXES = ("Exercising", "The declared condition still holds")

CURRENT_CONTRACT = ["rich", "thin", "degenerate"]


def load_fixture(name: str) -> list:
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("name", CURRENT_CONTRACT)
def test_fixture_matches_the_published_contract_exactly(name: str) -> None:
    """Key set and key order, on every item — not merely the first."""
    items = load_fixture(name)
    assert items, f"{name} fixture is empty"
    for index, item in enumerate(items):
        assert list(item) == REFERENCE_KEYS, f"{name}[{index}] drifted from the contract"


def test_the_legacy_fixture_is_a_prefix_of_the_current_contract() -> None:
    """What "append-only" has to mean for a consumer reading an older run.

    A pre-fix artifact must differ from a current one only by the absence of trailing keys, never
    by a rename, a reorder, or a removal. If this fails, ingestion cannot treat the two shapes
    uniformly and the defensive read has to become a version switch.
    """
    legacy = load_fixture("legacy_20key")
    assert legacy
    for index, item in enumerate(legacy):
        keys = list(item)
        assert keys == REFERENCE_KEYS[: len(keys)], f"legacy[{index}] is not a prefix"
    assert len(legacy[0]) == 20
    assert REFERENCE_KEYS[20:] == ["area", "area_name", "priority"]


def test_the_rich_fixture_still_carries_the_defects_the_design_answers() -> None:
    """If a future requirements-pack change fixes these, this test says so out loud.

    It does not assert the defects are good. It asserts the fixture still exercises them, so the
    defensive handling stays covered rather than quietly becoming dead code.
    """
    items = load_fixture("rich")
    assert len(items) == 190

    tiers = {tier: sum(1 for i in items if i["type"] == tier) for i in items for tier in [i["type"]]}
    assert tiers["system"] == 97, "51% at a tier nothing outside the system observes"
    assert tiers["functional"] == 88
    assert tiers["business_rule"] == 4
    assert tiers["business"] == 1

    # Acceptance criteria: 20% absent, 31% machine-generated, only 49% usable.
    assert sum(1 for i in items if not i["acceptance_criteria"]) == 38
    boilerplate = [
        c for i in items for c in i["acceptance_criteria"] if c.startswith(BOILERPLATE_PREFIXES)
    ]
    assert len(boilerplate) == 59
    assert (
        sum(
            1
            for i in items
            if i["acceptance_criteria"]
            and all(c.startswith(BOILERPLATE_PREFIXES) for c in i["acceptance_criteria"])
        )
        == 59
    ), "these items look criteria-bearing and are not"

    # Hierarchy is effectively absent, so nothing downstream may depend on it.
    assert sum(1 for i in items if not i["parent_id"]) == 188

    # Confidence carries almost no signal: one value covers 98% of the set, and exactly one item
    # in 190 sits below 0.5. This is why risk is computed from activated dimensions instead.
    confidences = [i["confidence"] for i in items]
    assert max(set(confidences), key=confidences.count) == 0.95
    assert confidences.count(0.95) == 186
    assert sum(1 for value in confidences if value < 0.5) == 1

    # Area, by contrast, is near-total — which is what grouping keys off instead.
    assert sum(1 for i in items if i["area"]) == 189


def test_the_thin_fixture_is_small_but_well_formed() -> None:
    """A small input must not be mistaken for a broken one."""
    items = load_fixture("thin")
    assert len(items) == 13
    assert {i["type"] for i in items} == {"functional", "system"}
    assert all(i["area"] for i in items)
    assert sum(1 for i in items if not i["parent_id"]) == 13


def test_the_degenerate_fixture_is_a_single_rejected_requirement() -> None:
    """The smallest honest input: one item, and one the correspondence check rejected."""
    items = load_fixture("degenerate")
    assert len(items) == 1
    assert items[0]["status"] == "rejected"


def test_the_malformed_fixture_is_hostile_but_still_json() -> None:
    """Ingestion must survive every one of these without raising."""
    items = load_fixture("malformed")
    assert any(not isinstance(item, dict) for item in items), "non-object entries"

    objects = [item for item in items if isinstance(item, dict)]
    assert any(not item.get("id") for item in objects), "a missing identifier"
    assert any(not (item.get("statement") or "").strip() for item in objects), "a blank statement"
    assert any(isinstance(item.get("acceptance_criteria"), str) for item in objects), "wrong type"
    assert any(isinstance(item.get("evidence"), dict) for item in objects), "evidence not a list"
    assert sum(1 for item in objects if item.get("id") == "FR-OK-01") == 2, "an id collision"
    assert any("an_unknown_future_key" in item for item in objects), "an unknown key to preserve"
    assert any(item.get("status") == "rejected" for item in objects)
    assert any(item.get("type") == "observation" for item in objects)

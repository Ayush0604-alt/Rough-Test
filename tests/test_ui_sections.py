"""The requirements tab must render whatever the schema publishes.

The tab looked its headings up in a hand-maintained dict with `labels[section]`, so adding a
section to the schema crashed it with a `KeyError` the moment a run finished — after every model
call had been paid for. The lookup is now total, and this test fails the moment the two drift
again rather than waiting for a user to hit it.
"""

from __future__ import annotations

import importlib

import pytest

from agentic.schemas.brd import ProductBRD


@pytest.fixture(scope="module")
def ui():
    return importlib.import_module("app")


def test_every_published_section_has_a_heading(ui) -> None:
    missing = [
        section for section in ProductBRD().every_section() if section not in ui.SECTION_LABELS
    ]
    assert missing == [], f"the requirements tab has no heading for {missing}"


def test_a_section_the_labels_have_never_heard_of_still_renders(ui) -> None:
    """A derived fallback beats a crash: a missing heading must not cost a finished run."""
    assert ui._section_label("brand_new_family") == "Brand new family"


def test_observations_are_shown_and_marked_as_not_requirements(ui) -> None:
    """They are kept deliberately, so the tab has to say what they are."""
    assert "not requirements" in ui.SECTION_LABELS["observations"]


# --------------------------------------------------------------------------------------------
# Which tabs a run earns
# --------------------------------------------------------------------------------------------


def _labels(ui, artifacts: dict, gates: dict | None = None) -> list[str]:
    state = {"artifacts": artifacts, "gate_reports": gates or {}}
    return [label for label, _ in ui._views(state, None)]


def test_a_run_only_gets_tabs_for_what_it_produced(ui) -> None:
    """A fixed tab list was written for one pack, so the other showed empty tabs and hid its own
    output. A test run has no inventory, no graph and no requirements — and must not offer them."""
    tabs = _labels(ui, {"requirement_set": "r", "test_suite": "s", "coverage": "c"})
    assert "Test cases" in tabs
    assert "Requirements" not in tabs
    assert "Knowledge graph" not in tabs
    assert "Inventory" not in tabs


def test_a_requirements_run_still_gets_its_own_tabs(ui) -> None:
    tabs = _labels(ui, {"inventory": "i", "graph": "g", "brd": "b"}, {"gate_graph": {}})
    assert tabs == ["Run", "Inventory", "Knowledge graph", "Gates", "Requirements", "Deliverables"]
    assert "Test cases" not in tabs


def test_a_halted_run_offers_only_what_it_reached(ui) -> None:
    """Halting early must not present tabs whose artifact was never written."""
    assert _labels(ui, {"inventory": "i"}) == ["Run", "Inventory", "Deliverables"]


def test_the_handoff_button_is_worded_by_the_manifest(ui) -> None:
    """The button offering the next pack says what it does, not the pack's id.

    Read from `action_label` so this module names no pack; a third pack chains with its own
    wording and nothing here changes.
    """
    from agentic.runtime import get_manifest

    following = get_manifest("testcase-generation")
    assert following.consumes_pack == "codebase-requirements"
    assert following.action_label == "Generate test cases"

"""The corpus harness, and the ratchet that stops a package trading one metric for another.

Every remediation package is graded by its delta on these numbers across the whole corpus. The
test asserts the direction of travel rather than exact values: coverage may rise and must not
fall, so an improvement does not fail the build but a regression does. When a package lifts a
number, the baseline is re-frozen and the diff is the report.

The corpus is deliberately multi-stack. A fix that raises coverage on the Python HTTP fixture and
leaves the CLI, worker, and Go fixtures flat is a stack-specific fix, and that shows up here as
three unchanged rows.
"""

from __future__ import annotations

import json

import pytest

from tests.harness.__main__ import BASELINE, collect
from tests.harness.metrics import corpus_fixtures, load_ground_truth, measure_structure


@pytest.fixture(scope="module")
def measured() -> dict[str, dict]:
    return collect()


@pytest.fixture(scope="module")
def baseline() -> dict[str, dict]:
    return json.loads(BASELINE.read_text(encoding="utf-8"))


def test_the_corpus_spans_several_languages_and_paradigms() -> None:
    """A single-stack corpus silently permits a single-stack fix."""
    truths = [load_ground_truth(fixture) for fixture in corpus_fixtures()]
    languages = {language for truth in truths for language in truth["languages"]}
    paradigms = {truth["paradigm"] for truth in truths}

    assert len(languages) >= 3, f"only {languages}"
    assert len(paradigms) >= 2, f"only {paradigms}"
    assert any(truth["primary_language"] != "Python" for truth in truths), (
        "the corpus needs a fixture whose primary language the system cannot parse, "
        "or the new-stack gate cannot be measured"
    )


def test_every_fixture_is_measurable(measured) -> None:
    assert {fixture.name for fixture in corpus_fixtures()} == set(measured)
    for name, metrics in measured.items():
        assert metrics["present"], f"{name} declares no entry points"


@pytest.mark.parametrize("fixture_name", [f.name for f in corpus_fixtures()])
def test_detection_coverage_does_not_regress(fixture_name, measured, baseline) -> None:
    """What the extractors find may grow; it may never shrink."""
    now = measured[fixture_name]["class_coverage_detected"]
    was = baseline[fixture_name]["class_coverage_detected"]
    for klass, previous in was.items():
        current = now.get(klass, 0.0)
        assert current >= previous - 1e-9, (
            f"{fixture_name}: detection of {klass} fell from {previous:.0%} to {current:.0%}"
        )


def _selected_total(metrics: dict) -> int:
    return sum(sum(by_effect.values()) for by_effect in metrics["selected"].values())


@pytest.mark.parametrize("fixture_name", [f.name for f in corpus_fixtures()])
def test_selection_does_not_regress(fixture_name, measured, baseline) -> None:
    """Two properties, and deliberately not per-class coverage.

    A per-class selected ratchet was the wrong metric and this replaces it. The cluster cap is a
    fixed budget, so raising one class necessarily lowers another whenever the cap binds — and
    rebalancing a fixed budget is exactly what the selection fix does. The ratchet would have
    forbidden the fix it was written to grade.

    What must hold instead is that the budget is not spent on less, and that no class is starved:

    * total selected never falls, so a change cannot quietly analyse fewer entry points;
    * the worst-covered class never gets worse, which is the anti-starvation property the whole
      package exists for.
    """
    now, was = measured[fixture_name], baseline[fixture_name]
    assert _selected_total(now) >= _selected_total(was), (
        f"{fixture_name}: total selected fell from {_selected_total(was)} to {_selected_total(now)}"
    )

    now_min = min(now["class_coverage_selected"].values(), default=0.0)
    was_min = min(was["class_coverage_selected"].values(), default=0.0)
    assert now_min >= was_min - 1e-9, (
        f"{fixture_name}: worst-covered class fell from {was_min:.0%} to {now_min:.0%}"
    )


@pytest.mark.parametrize("fixture_name", [f.name for f in corpus_fixtures()])
def test_detection_never_loses_an_entry_point(fixture_name, measured, baseline) -> None:
    """A newly-missed entry point is a regression even if a coverage percentage happens to hold."""
    now = set(measured[fixture_name]["missed_entry_points"])
    was = set(baseline[fixture_name]["missed_entry_points"])
    assert not (now - was), f"{fixture_name}: newly undetected {sorted(now - was)}"


# ------------------------------------------------------------------------------------------
# The defect this corpus exists to measure. These assertions describe the FIXED behaviour; the
# frozen baseline in _baseline.json still records what it looked like before WP1a.
# ------------------------------------------------------------------------------------------


def test_selection_no_longer_starves_any_entry_point_class() -> None:
    """The D1 fix, measured where the defect was reproduced.

    Before: a binding cap took the alphabetically-first groups, which meant every entry point
    sorting after GET was never planned. After: the cap is allocated across classes in proportion
    to their size with a floor of one, so a cap costs each class a share rather than removing
    whole classes.
    """
    fixture = next(f for f in corpus_fixtures() if f.name == "py_service")
    truth = load_ground_truth(fixture)
    metrics = measure_structure(
        fixture, max_clusters=truth["selection_cap_for_measurement"]
    )

    selected = metrics.class_coverage("selected")
    assert all(value > 0 for value in selected.values()), (
        f"a whole entry-point class was left unplanned: {selected}"
    )

    read = metrics.coverage("selected", "ENDPOINT", "read")
    mutate = metrics.coverage("selected", "ENDPOINT", "mutate")
    assert mutate > 0, "state-changing handlers must be planned at all"
    assert 0.1 <= (mutate / read) <= 10.0, (
        f"read {read:.0%} and mutate {mutate:.0%} differ by more than an order of magnitude"
    )

    # The defect was that a whole sort-order tail went unplanned, so the property is that the
    # unselected set is not the mutating endpoints wholesale. Pinning two named routes tested the
    # same thing only while the ids and their ordering were fixed by one extractor.
    unselected = set(metrics.detected_but_unselected)
    mutating = {
        item["id"]
        for item in truth["entry_points"]
        if item["class"] == "ENDPOINT" and item["effect"] == "mutate"
    }
    assert mutating - unselected, f"every mutating endpoint went unplanned: {sorted(mutating)}"


def test_the_cap_is_allocated_in_proportion_to_class_size() -> None:
    """Equal shares would under-sample the bulk of a system as badly as truncation over-sampled it."""
    from agentic.capabilities.graph_ops import _stratified

    population = {"A": 100, "B": 100, "C": 10, "D": 5}
    groups = [{"class": name} for name in sorted(population) for _ in range(population[name])]
    chosen: dict[str, int] = {}
    for group in _stratified(groups, 40):
        chosen[group["class"]] = chosen.get(group["class"], 0) + 1

    assert sum(chosen.values()) == 40, "the whole budget is spent"
    assert all(chosen.get(name, 0) > 0 for name in population), "no class is starved"
    assert chosen["A"] > chosen["C"] > 0, "a larger class still receives a larger share"


def test_selection_is_unchanged_when_the_cap_does_not_bind() -> None:
    from agentic.capabilities.graph_ops import _stratified

    groups = [{"class": "A"}, {"class": "B"}, {"class": "A"}]
    assert _stratified(groups, 10) == groups


def test_the_pass_can_be_turned_off() -> None:
    """Every remediation pass is individually disableable through the existing config."""
    from agentic.config import Settings

    assert Settings().stratified_cluster_selection is True
    assert Settings(stratified_cluster_selection=False).stratified_cluster_selection is False


def test_a_repository_in_an_unparsed_language_still_produces_evidence() -> None:
    """The new-stack gate, now met.

    A Go repository has no entry point this system can detect: nothing parses Go, so no handler,
    no call graph, no boundary. It used to halt at ingestion and emit nothing whatsoever.

    It now yields a cluster anyway, because what the repository *declares* does not depend on
    anything being parsed. A stated limit in a README is evidence for a requirement whether or not
    a call path reaches it, and grounding it needs only a file and a line. The endpoints remain
    undetected -- that is honest, and the coverage report says so -- but the run is no longer
    all-or-nothing on whether one parser happens to exist.
    """
    fixture = next(f for f in corpus_fixtures() if f.name == "go_service")
    metrics = measure_structure(fixture, max_clusters=4)

    assert metrics.halted is None, "an unparsed language must no longer stop the run"
    assert metrics.clusters > 0, "declared policy must reach an evidence packet"
    # Detection of Go handlers is genuinely still zero, and must keep being reported as zero
    # rather than being papered over by the declaration path.
    assert metrics.class_coverage("detected") == {"ENDPOINT": 0.0}


def test_an_event_driven_repository_is_no_longer_invisible() -> None:
    """Handlers registered with a broker or scheduler were a whole missing paradigm.

    Nothing called them, so call-graph traversal could not reach them and no boundary existed to
    seed a cluster from. They are now found by a registered adapter — which is the property that
    matters, because the next paradigm is added the same way and needs no change here.
    """
    fixture = next(f for f in corpus_fixtures() if f.name == "py_worker")
    metrics = measure_structure(fixture, max_clusters=4)

    assert metrics.halted is None
    coverage = metrics.class_coverage("detected")
    assert coverage == {"EVENT_CONSUMER": 1.0, "SCHEDULED_JOB": 1.0}
    assert metrics.missed_entry_points == []
    assert metrics.clusters > 0, "a detected entry point must also be planned"

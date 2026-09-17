"""Print the corpus metric table, or freeze it as the baseline a package is graded against.

    python -m tests.harness            # table, plus the delta against the frozen baseline
    python -m tests.harness --freeze   # rewrite the baseline from the current run
    python -m tests.harness --json     # machine-readable, for a report
"""

from __future__ import annotations

import argparse
import json
import sys

from tests.harness.metrics import (
    TRUTH,
    corpus_fixtures,
    load_ground_truth,
    measure_pipeline,
    measure_structure,
)

BASELINE = TRUTH / "_baseline.json"
# Kept apart from the structure baseline because the two answer different questions and move for
# different reasons: one says what the extractors found, the other what the document made of it.
PIPELINE_BASELINE = TRUTH / "_pipeline_baseline.json"


def collect() -> dict[str, dict]:
    measured: dict[str, dict] = {}
    for fixture in corpus_fixtures():
        truth = load_ground_truth(fixture)
        metrics = measure_structure(
            fixture, max_clusters=truth.get("selection_cap_for_measurement")
        )
        measured[metrics.fixture] = metrics.as_dict()
    return measured


def collect_pipeline() -> dict[str, dict]:
    """The document half: the deterministic passes run over each fixture's authored claims."""
    return {
        fixture.name: measure_pipeline(fixture).as_dict() for fixture in corpus_fixtures()
    }


# Which way is better for each document metric. A scored suite has to know this: "fewer
# requirements without acceptance criteria" is progress and "fewer citations resolved" is not, and
# a single "did the number change" rule cannot tell them apart.
HIGHER_IS_BETTER = (
    "requirements",
    "distinct_units_cited",
    "citations",
    "citations_resolved",
    "citation_resolution_rate",
    # The one that must never fall. Half the authored claims are deliberately false, so a
    # pipeline publishing all of them scores higher on a raw count than one that removes what it
    # cannot support -- exactly backwards. This counts only the supported half.
    "supported_published",
)

# Counts that legitimately fall when unsupported claims stop being published, and must not fall
# for any other reason. A drop is a regression unless the number of *false* claims in the
# document fell with it.
EXPLAINED_BY_REMOVING_FALSE_CLAIMS = (
    "requirements",
    "distinct_units_cited",
    "citations",
    "citations_resolved",
)
LOWER_IS_BETTER = (
    "duplicate_titles",
    "duplication_rate",
    "empty_acceptance_criteria",
    "empty_assumptions",
    "empty_open_questions",
    "relation_without_parent",
    "contradicted_published",
)


def regressions(now: dict[str, dict], was: dict[str, dict]) -> list[str]:
    """Every metric that moved the wrong way, on any fixture. Empty means the build passes."""
    found: list[str] = []
    for fixture, current in sorted(now.items()):
        previous = was.get(fixture)
        if previous is None:
            continue
        fewer_false = current.get("contradicted_published", 0) < previous.get(
            "contradicted_published", 0
        )
        for metric in HIGHER_IS_BETTER:
            before, after = previous.get(metric, 0), current.get(metric, 0)
            if after < before - 1e-9:
                if fewer_false and metric in EXPLAINED_BY_REMOVING_FALSE_CLAIMS:
                    continue
                found.append(f"{fixture}: {metric} fell from {before} to {after}")
        for metric in LOWER_IS_BETTER:
            before, after = previous.get(metric, 0), current.get(metric, 0)
            if after > before + 1e-9:
                found.append(f"{fixture}: {metric} rose from {before} to {after}")
        # A document whose confidence figures collapse to one value has lost the information the
        # recalibration exists to add, however good the other numbers look.
        #
        # Only when the published set is otherwise the same, though. Removing the claims the
        # evidence could not support narrows the spread by construction -- the doubtful band was
        # made of exactly those claims -- and reporting that as a loss of information would
        # penalise the stage for doing its job.
        spread_before = len(previous.get("confidence_histogram") or {})
        spread_now = len(current.get("confidence_histogram") or {})
        same_set = current.get("requirements", 0) >= previous.get("requirements", 0)
        if spread_now < spread_before and same_set:
            found.append(
                f"{fixture}: confidence took {spread_now} distinct values, down from {spread_before}"
            )
    return found


def render_pipeline(now: dict[str, dict], was: dict[str, dict] | None) -> str:
    lines = ["\n=== document pipeline ==="]
    header = (
        f"  {'fixture':<12} {'reqs':>5} {'true':>5} {'false':>6} {'dup':>4} {'cites':>6} "
        f"{'resolved':>9} {'no criteria':>12} {'no questions':>13} {'dangling':>9}"
    )
    lines.append(header)
    for fixture, current in sorted(now.items()):
        lines.append(
            f"  {fixture:<12} {current['requirements']:>5} "
            f"{current.get('supported_published', 0):>5} "
            f"{current.get('contradicted_published', 0):>6} "
            f"{current['duplicate_titles']:>4} "
            f"{current['citations']:>6} {current['citation_resolution_rate']:>8.0%} "
            f"{current['empty_acceptance_criteria']:>12} "
            f"{current['empty_open_questions']:>13} {current['relation_without_parent']:>9}"
        )
    if was is not None:
        broken = regressions(now, was)
        lines.append("")
        lines += [f"  REGRESSION  {line}" for line in broken] or ["  no regressions"]
    return "\n".join(lines)


def _pct(value: float) -> str:
    return f"{value:>5.0%}"


def render(measured: dict[str, dict], baseline: dict[str, dict] | None) -> str:
    lines: list[str] = []
    for name, current in measured.items():
        was = (baseline or {}).get(name, {})
        lines.append(f"\n=== {name} ===")
        if current["halted"]:
            lines.append(f"  HALTED  {current['halted'][:96]}")
        lines.append(
            f"  languages={current['languages_inventoried']}  "
            f"parseable={current['parseable_files']}  "
            f"inventory_only={current['inventory_only_files']}  "
            f"clusters={current['clusters']}"
        )
        lines.append(f"  {'entry-point class':22} {'detected':>10} {'selected':>10}   delta")
        detected = current["class_coverage_detected"]
        selected = current["class_coverage_selected"]
        old_selected = was.get("class_coverage_selected", {})
        for klass in sorted(detected):
            delta = ""
            if klass in old_selected:
                change = selected[klass] - old_selected[klass]
                delta = "  same" if abs(change) < 1e-9 else f"  {change:+.0%}"
            lines.append(
                f"  {klass:22} {_pct(detected[klass]):>10} {_pct(selected[klass]):>10} {delta}"
            )
        if current["missed_entry_points"]:
            lines.append(
                f"  never detected ({len(current['missed_entry_points'])}): "
                + ", ".join(current["missed_entry_points"][:6])
            )
        if current["detected_but_unselected"]:
            lines.append(
                f"  detected but not selected ({len(current['detected_but_unselected'])}): "
                + ", ".join(current["detected_but_unselected"][:6])
            )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="tests.harness")
    parser.add_argument("--freeze", action="store_true", help="rewrite the frozen baseline")
    parser.add_argument("--json", action="store_true", help="emit machine-readable metrics")
    args = parser.parse_args(argv)

    measured = collect()
    pipeline = collect_pipeline()
    if args.json:
        print(json.dumps({"structure": measured, "pipeline": pipeline}, indent=2, sort_keys=True))
        return 0

    baseline = json.loads(BASELINE.read_text(encoding="utf-8")) if BASELINE.is_file() else None
    was = (
        json.loads(PIPELINE_BASELINE.read_text(encoding="utf-8"))
        if PIPELINE_BASELINE.is_file()
        else None
    )
    print(render(measured, baseline))
    print(render_pipeline(pipeline, was))

    if args.freeze:
        BASELINE.write_text(
            json.dumps(measured, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        PIPELINE_BASELINE.write_text(
            json.dumps(pipeline, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        print(f"\nfrozen -> {BASELINE.name}, {PIPELINE_BASELINE.name}")
        return 0

    # A non-zero exit is what makes this a scored suite rather than a report: a regression on any
    # metric, on any fixture, fails the build.
    broken = regressions(pipeline, was or {})
    if broken:
        print(f"\n{len(broken)} regression(s).")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

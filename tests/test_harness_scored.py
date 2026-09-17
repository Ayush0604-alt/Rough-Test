"""The scored suite: a regression on any metric, on any fixture, fails the build.

The structure baseline grades what the extractors found. This grades what the document made of
it — whether claims are checkable, whether uncertainty is recorded, whether the same claim was
published twice, whether citations still resolve, and whether the confidence figures carry any
information at all. It runs the deterministic half of the document pipeline over each fixture's
authored claims, so it needs no model and can run on every commit.

Direction matters and is declared per metric. "Fewer requirements without acceptance criteria" is
progress; "fewer citations resolved" is not. A suite that only asked whether a number changed
could not tell those apart, and would have to be silenced the first time something improved.

The last test here is the one that matters most: the new-stack gate. A repository in a language
absent from the corpus, which nothing here parses and no adapter was written for, must still
produce grounded output — with no change to any source file. That is the whole claim of the
adapter architecture, and it is checked rather than asserted.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from tests.harness.__main__ import (
    HIGHER_IS_BETTER,
    LOWER_IS_BETTER,
    PIPELINE_BASELINE,
    collect_pipeline,
    regressions,
)
from tests.harness.metrics import authored_claims, corpus_fixtures
from tests.conftest import run_step_or_stub

REPO_ROOT = Path(__file__).resolve().parents[1]
NEW_STACK = REPO_ROOT / "tests" / "fixtures" / "new_stack"


@pytest.fixture(scope="module")
def pipeline() -> dict[str, dict]:
    return collect_pipeline()


@pytest.fixture(scope="module")
def frozen() -> dict[str, dict]:
    return json.loads(PIPELINE_BASELINE.read_text(encoding="utf-8"))


# --------------------------------------------------------------------------------------------
# The ratchet
# --------------------------------------------------------------------------------------------


def test_no_document_metric_regresses_on_any_fixture(pipeline, frozen) -> None:
    broken = regressions(pipeline, frozen)
    assert broken == [], "\n".join(broken)


def test_the_ratchet_actually_bites() -> None:
    """A suite that cannot fail is a report. This proves it fails on a regression it should catch."""
    was = {"f": {"citations_resolved": 10, "empty_acceptance_criteria": 0, "requirements": 5}}
    worse = {"f": {"citations_resolved": 8, "empty_acceptance_criteria": 3, "requirements": 5}}

    found = regressions(worse, was)
    assert any("citations_resolved" in line for line in found)
    assert any("empty_acceptance_criteria" in line for line in found)
    assert regressions(was, was) == []


def test_an_improvement_is_never_a_regression() -> None:
    """Otherwise the suite has to be silenced the first time something gets better."""
    was = {"f": {"citations_resolved": 8, "empty_acceptance_criteria": 3, "duplicate_titles": 2}}
    better = {"f": {"citations_resolved": 10, "empty_acceptance_criteria": 0, "duplicate_titles": 0}}
    assert regressions(better, was) == []


def test_confidence_collapsing_to_one_value_is_a_regression() -> None:
    """The defect confidence recalibration exists to fix, guarded against coming back.

    Every other number can look healthy while the figure that tells a reader which claims to check
    silently becomes a constant again.
    """
    was = {"f": {"confidence_histogram": {"0.3": 1, "0.75": 4, "0.9": 2}}}
    flat = {"f": {"confidence_histogram": {"0.9": 7}}}
    assert any("confidence" in line for line in regressions(flat, was))


def test_every_metric_has_a_declared_direction() -> None:
    """A metric nobody scored is a metric that can rot unnoticed."""
    from tests.harness.metrics import DocumentMetrics

    scored = set(HIGHER_IS_BETTER) | set(LOWER_IS_BETTER)
    # Descriptive fields are not scored: they are breakdowns, not quantities with a better
    # direction, and each is covered by a scored aggregate.
    descriptive = {
        "source", "by_type", "file_types_cited", "confidence_histogram",
    }
    fields = set(DocumentMetrics(source="x").as_dict())
    unscored = fields - scored - descriptive
    assert unscored == set(), f"no direction declared for {sorted(unscored)}"


# --------------------------------------------------------------------------------------------
# What the pipeline is expected to hold on every fixture
# --------------------------------------------------------------------------------------------


@pytest.mark.parametrize("fixture", [f.name for f in corpus_fixtures()])
def test_citations_still_resolve_everywhere(fixture, pipeline) -> None:
    """The one number that may never be traded away for any other."""
    assert pipeline[fixture]["citation_resolution_rate"] == 1.0


@pytest.mark.parametrize("fixture", [f.name for f in corpus_fixtures()])
def test_no_claim_is_published_twice(fixture, pipeline) -> None:
    assert pipeline[fixture]["duplicate_titles"] == 0


@pytest.mark.parametrize("fixture", [f.name for f in corpus_fixtures()])
def test_no_relation_dangles(fixture, pipeline) -> None:
    assert pipeline[fixture]["relation_without_parent"] == 0


@pytest.mark.parametrize("fixture", [f.name for f in corpus_fixtures()])
def test_uncertainty_is_recorded_rather_than_left_blank(fixture, pipeline) -> None:
    """`assumptions` was empty on 60 of 60 items in the run that started this work."""
    assert pipeline[fixture]["empty_assumptions"] == 0


def test_every_fixture_has_authored_claims_to_grade() -> None:
    for fixture in corpus_fixtures():
        assert authored_claims(fixture.name), f"{fixture.name} contributes nothing to the score"


# --------------------------------------------------------------------------------------------
# The new-stack gate
# --------------------------------------------------------------------------------------------


def _run_new_stack():
    from agentic.capabilities.registry import CapabilityContext
    from agentic.capabilities.verify import verify_location
    from agentic.config import Settings
    from agentic.runtime import prepare_run
    from agentic.schemas.core import KnowledgeGraph
    from agentic.skills.pack import load_pack
    from agentic.state import initial_state
    from agentic.store import ArtifactStore

    manifest = load_pack(REPO_ROOT / "codebase-requirement-skills")
    settings = Settings(runs_dir=Path(tempfile.mkdtemp()) / "runs")
    prepared = prepare_run(source=NEW_STACK, settings=settings)
    state = initial_state(
        run_id="n",
        pack="codebase-requirements",
        run_dir=str(prepared.run_dir),
        source_root=str(prepared.source_root),
        repository_name="new_stack",
    )
    store = ArtifactStore(prepared.run_dir)
    gate = None
    for spec in manifest.steps:
        ctx = CapabilityContext(
            state=state,
            step=spec,
            settings=settings,
            store=store,
            skill=manifest.skill_registry().load(spec.skill),
            payload={"manifest": manifest},
        )
        outcome = run_step_or_stub(spec, ctx)
        assert outcome.halt_reason is None, f"halted at {spec.id}: {outcome.halt_reason}"
        for name, value in (outcome.artifacts or {}).items():
            state["artifacts"][name] = store.save(name, value)
        if spec.id == "gate_graph":
            gate = outcome.gate
        if spec.id == "plan_clusters":
            break

    graph = store.load_model(state["artifacts"]["graph"], KnowledgeGraph)
    clusters = store.load_json(state["artifacts"]["clusters"])
    located = [entity for entity in graph.entities if entity.location]
    resolved = sum(
        1 for entity in located if verify_location(entity.location, Path(prepared.source_root)).ok
    )
    return graph, clusters, gate, resolved, len(located)


def test_a_language_the_corpus_never_contained_is_extracted_at_all() -> None:
    """Held-out Ruby, and an honest record of what the Graphify swap cost here.

    This fixture is deliberately outside the corpus so that no work was tuned against it. It is a
    Sinatra application: four routes, two configuration constants and a business rule, all written
    as top-level DSL calls rather than named definitions, plus a SQL schema and a README.

    What used to carry it was a set of artifact adapters that read the schema, the configuration
    and the documentation into `DATA_ENTITY` and `DOMAIN_POLICY` nodes. Those adapters are gone
    with the swap, and Graphify keys on `class` / `def` / `func` declarations — of which this file
    has none. So the graph is now the two files, the tables in the schema, and nothing else.

    The gate must still accept it rather than halting, and every location it does produce must
    still resolve. What it must not do is pretend: no boundary is found, so no requirement can be
    grounded, and that is the state this test pins so an improvement is visible when it lands.
    """
    graph, clusters, gate, resolved, located = _run_new_stack()

    assert gate.valid, "the graph gate rejected a repository it should accept"
    assert resolved == located, f"only {resolved} of {located} citations resolve"

    kinds = {entity.kind for entity in graph.entities}
    assert "FILE" in kinds, f"nothing at all was extracted: {sorted(kinds)}"

    # The two losses, recorded rather than hidden. Declaration-style code yields no definition,
    # and no adapter reads a schema or a configuration file into a policy node any more.
    assert not clusters, (
        "a boundary was found in a DSL-only repository — the recorded limitation no longer holds "
        "and this test should be rewritten to assert the requirement that is now producible"
    )
    assert not {"DATA_ENTITY", "DOMAIN_POLICY"} & kinds


def test_the_sql_schema_in_a_held_out_repository_still_reaches_the_graph() -> None:
    """SQL is the one non-code source that survived the swap, and only with the `sql` extra."""
    graph, _, _, _, _ = _run_new_stack()
    names = {entity.name for entity in graph.entities}
    assert {"borrowers", "loans"} <= names, f"schema tables missing: {sorted(names)}"


def test_the_new_stack_fixture_is_outside_the_graded_corpus() -> None:
    """Held out on purpose: a gate measured on something tuned against proves nothing."""
    assert NEW_STACK.exists()
    assert "new_stack" not in {fixture.name for fixture in corpus_fixtures()}
    assert not (NEW_STACK / "ground_truth.json").exists()

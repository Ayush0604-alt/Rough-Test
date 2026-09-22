"""The altitude between one boundary and the whole product.

Two things are worth pinning here and the second matters more than the first. One: the partition
groups boundaries that run the same code, so the modules it produces are features. Two: the codes
it assigns are handed *down* to synthesis, which is the only version of "clusters covering the same
part of the product must use the same code" that eighty-four parallel workers can actually obey.
"""

from __future__ import annotations

import json
from pathlib import Path

from agentic.capabilities.graph_ops import GraphIndex
from agentic.capabilities.modules import (
    MERGE_SIMILARITY,
    _agglomerate,
    _boundary_words,
    _jaccard,
    _module_codes,
)
from agentic.schemas.core import Entity, KnowledgeGraph

EXTRACTION = ("ingest", "graphify_extract", "adapt_graph", "extract_declarations",
              "propose_boundaries", "classify_boundaries", "collect_boundary_assignments",
              "discover_entry_points")


# --------------------------------------------------------------------------------------------
# The agglomeration itself
# --------------------------------------------------------------------------------------------


def test_boundaries_sharing_their_workflow_become_one_module() -> None:
    reach = {
        "a": {"1", "2", "3", "4"},
        "b": {"1", "2", "3", "5"},      # 3/5 shared with a -> merges
        "z": {"90", "91", "92", "93"},  # nothing in common -> stays apart
    }
    groups = _agglomerate(reach, MERGE_SIMILARITY, floor=1)
    merged = sorted(sorted(group) for group in groups)
    assert merged == [["a", "b"], ["z"]]


def test_the_merge_is_a_function_of_the_graph_alone() -> None:
    """Order must not decide the answer.

    A single pass of "merge everything above the threshold" is order-dependent, so two runs over
    the same graph could produce different modules — and every reproducibility claim this pipeline
    makes about its deterministic half would be false for this step. Taking the best pair each
    round is what buys that back.
    """
    reach = {
        "a": {"1", "2", "3"},
        "b": {"1", "2", "4"},
        "c": {"1", "2", "5"},
        "d": {"80", "81", "82"},
    }
    first = sorted(sorted(g) for g in _agglomerate(reach, MERGE_SIMILARITY, floor=1))
    shuffled = {key: reach[key] for key in ("d", "c", "a", "b")}
    second = sorted(sorted(g) for g in _agglomerate(shuffled, MERGE_SIMILARITY, floor=1))
    assert first == second


def test_the_floor_stops_everything_collapsing_into_one_module() -> None:
    """A codebase whose boundaries all share a bootstrap path must not become a single module."""
    reach = {name: {"shared", name} for name in "abcdef"}
    groups = _agglomerate(reach, threshold=0.0, floor=3)
    assert len(groups) == 3


def test_jaccard_is_zero_for_disjoint_and_one_for_identical() -> None:
    assert _jaccard({"a"}, {"b"}) == 0.0
    assert _jaccard({"a", "b"}, {"a", "b"}) == 1.0
    assert _jaccard(set(), set()) == 0.0


# --------------------------------------------------------------------------------------------
# Module codes
# --------------------------------------------------------------------------------------------


def _boundary(name: str) -> Entity:
    return Entity(id=name, kind="ENDPOINT", name=name, qualified_name=f"repo/app/api.py::{name}")


def test_a_code_comes_from_what_tells_a_module_apart_not_from_what_is_commonest() -> None:
    """Frequency picks the least informative word, which is the whole problem.

    On the reference repository every boundary's label carried "resume", so scoring by raw count
    gave every module the same stem and every code after the first collided into `RES2`…`RES14`.
    Scoring by lift — how much more often a word appears here than across all modules — picks the
    word that distinguishes this module, which is what a code is for.
    """
    index = GraphIndex(
        KnowledgeGraph(
            repository_name="r",
            entities=[
                _boundary("/resume/rounds"), _boundary("/resume/rounds/{id}"),
                _boundary("/resume/review"), _boundary("/resume/review/approve"),
            ],
        )
    )
    codes = _module_codes(
        index, [["/resume/rounds", "/resume/rounds/{id}"], ["/resume/review", "/resume/review/approve"]]
    )
    assert codes == ["ROUN", "REVI"]
    assert "RESU" not in codes


def test_codes_never_collide() -> None:
    index = GraphIndex(
        KnowledgeGraph(
            repository_name="r",
            entities=[_boundary("/orders"), _boundary("/orders/list")],
        )
    )
    codes = _module_codes(index, [["/orders"], ["/orders/list"]])
    assert len(set(codes)) == 2


def test_a_module_with_no_usable_word_falls_back_to_a_positional_code() -> None:
    """A positional code is honest: it does not pretend to mean anything.

    The heading a reader actually sees is written by the model, so a module coded `M01` still
    publishes under a real business name.
    """
    index = GraphIndex(
        KnowledgeGraph(repository_name="r", entities=[_boundary("/"), _boundary("{id}")])
    )
    codes = _module_codes(index, [["/", "{id}"]])
    assert codes == ["M01"]


def test_mechanism_words_never_become_a_code() -> None:
    assert _boundary_words(_boundary("/api/router/get")) == []
    assert _boundary_words(_boundary("/invoices/settle")) == ["invoices", "settle"]


def test_a_path_never_supplies_a_code() -> None:
    """`qualified_name` carries the file path, and the path carries the repository name.

    That is how every module on `resume-agent-PROD` scored "resume" highest. Only the boundary's
    own label — the product's wording for what it offers — may name a module.
    """
    entity = Entity(
        id="x", kind="ENDPOINT", name="/invoices/settle",
        qualified_name="resume-agent-PROD/app/services/billing.py::settle_invoice",
    )
    words = _boundary_words(entity)
    assert "resume" not in words
    assert "billing" not in words
    assert words == ["invoices", "settle"]


# --------------------------------------------------------------------------------------------
# End to end
# --------------------------------------------------------------------------------------------


def _load(state, name):
    return json.loads(Path(state["artifacts"][name]).read_text(encoding="utf-8"))


def test_the_partition_ignores_handlers_and_keeps_only_boundaries(runner, state) -> None:
    """`boundary_ids()` deliberately includes exposers; partitioning must not.

    A requirement citing a handler is citing the point where the system is consumed, which is the
    right rule for reachability. For partitioning it puts a route and its handler in as two seeds,
    which halves the measured similarity between two real features and promotes junk leads — an
    `APIRouter` construction, a bare `msg.get` — to module heads.
    """
    for step_id in EXTRACTION:
        runner(step_id)
    result = runner("partition_modules")
    graph = KnowledgeGraph.model_validate(_load(state, "graph"))
    index = GraphIndex(graph)

    every = index.boundary_ids()
    from agentic.capabilities.graph_ops import BOUNDARY_KINDS

    real = {i for i in every if i in index.entities and index.entities[i].kind in BOUNDARY_KINDS}
    if real and real != every:
        assert result.metrics["module_boundaries"] == len(real)

    for module in _load(state, "modules"):
        for boundary in module["boundaries"]:
            assert boundary["kind"] in BOUNDARY_KINDS


def test_every_boundary_lands_in_exactly_one_module(runner, state) -> None:
    """Nothing may be selected out.

    The residual module exists so that a boundary overlapping with no other, or one past the
    module cap, still has an area code and still has its findings filed — rather than being
    dropped the way the boundary-candidate cap used to drop candidates.
    """
    for step_id in EXTRACTION:
        runner(step_id)
    runner("partition_modules")
    assignments = _load(state, "boundary_modules")
    modules = _load(state, "modules")

    codes = {module["module_code"] for module in modules}
    assert set(assignments.values()) <= codes

    listed = [
        boundary["qualified_name"] for module in modules for boundary in module["boundaries"]
    ]
    assert len(listed) == len(set(listed)), "a boundary was placed in two modules"


def test_a_cluster_is_handed_its_area_code_rather_than_inventing_one(runner, state) -> None:
    for step_id in EXTRACTION:
        runner(step_id)
    runner("partition_modules")
    runner("plan_clusters")
    clusters = _load(state, "clusters")
    assert clusters
    assert any(cluster.get("module_code") for cluster in clusters), (
        "no cluster carries a module code, so every synthesis worker is back to inventing one"
    )


def test_module_analysis_can_be_switched_off(runner, state, settings) -> None:
    for step_id in EXTRACTION:
        runner(step_id)
    settings.module_analysis = False
    result = runner("partition_modules")
    assert result.metrics["modules"] == 0
    assert _load(state, "modules") == []
    # And clustering must still work, with every worker inventing its own code exactly as before.
    runner("plan_clusters")
    assert _load(state, "clusters")


def test_a_module_carries_the_two_evidence_classes_a_boundary_cannot_see(runner, state) -> None:
    """`depends_on` and `declared_policy` are the point of this altitude.

    A boundary packet is a walk along workflow edges, so it can carry neither: an external system
    is filtered out of traversal by `NOISE_KINDS`, and a configuration key sits on no call path at
    all. Without them a module worker is just a boundary worker with a wider view.
    """
    for step_id in EXTRACTION:
        runner(step_id)
    runner("partition_modules")
    modules = _load(state, "modules")
    assert modules
    for module in modules:
        assert "depends_on" in module
        assert "declared_policy" in module


def test_no_two_modules_are_shown_the_same_declaration(runner, state) -> None:
    """Partitioned, not scoped — and this is the assertion that keeps it that way.

    Scoping by directory ancestry put repository-root policy in scope for every module, which on
    the reference repository meant twenty-one modules each shown the same sixty declarations. Every
    one of them would have been entitled to publish the same data invariant, leaving consolidation
    to clean up work that should never have been commissioned.
    """
    for step_id in EXTRACTION:
        runner(step_id)
    runner("partition_modules")
    seen: dict[str, str] = {}
    for module in _load(state, "modules"):
        for policy in module["declared_policy"]:
            key = f"{policy['kind']}:{policy['name']}:{policy['at']}"
            assert key not in seen, (
                f"{key} was given to both {seen[key]} and {module['module_code']}"
            )
            seen[key] = module["module_code"]


def test_repository_wide_policy_reaches_a_declaration_cluster_not_every_module(
    runner, state
) -> None:
    """A schema at the repository root belongs to the product, not to any one part of it.

    So it is correct for every module's `declared_policy` to be empty on a codebase that keeps all
    its policy centrally — what would be wrong is for that policy to reach no model at all. It
    reaches one through `declaration_groups`, which is what that mechanism was built for and could
    never do while nothing emitted a declaration node.
    """
    for step_id in EXTRACTION:
        runner(step_id)
    runner("partition_modules")
    runner("plan_clusters")
    clusters = _load(state, "clusters")

    declaration_nodes = [
        record
        for cluster in clusters
        for record in cluster["packet"]["records"]
        if record["n"]["k"] in {"DATA_ENTITY", "DATA_FIELD", "DOMAIN_POLICY", "CONFIG_KEY"}
    ]
    assert declaration_nodes, "declared policy reached no evidence packet at all"


def test_the_reducer_drops_an_unreadable_reply_rather_than_guessing(runner, state) -> None:
    for step_id in EXTRACTION:
        runner(step_id)
    runner("partition_modules")
    state["module_fragments"] = [
        {"module_code": "PARS", "module_name": "Resume parsing"},
        {"module_name": "no code at all"},
        "not a dict",
    ]
    result = runner("collect_module_analyses")
    assert result.metrics["module_analyses"] == 1
    assert result.metrics["module_replies_unreadable"] == 2
    assert _load(state, "module_analyses")["module_names"] == {"PARS": "Resume parsing"}

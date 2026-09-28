"""Regression tests for the fixes recorded in claudeMD/New_fixes.md.

Each test is built from a defect measured on a real run (neuleap_talent_scooper, run c488c497):
a fan-out wired behind a fan-out that ran 3,171 model calls where 21 were meant; a top tier that
saw one module of twenty-one; 44% of a document drawn from developer-tooling files; duplicates that
missed a merge threshold by 0.01; 223 of 303 boundaries with no packet; and a top tier that bound 8
of 374 capabilities and invented a ninth.
"""

from __future__ import annotations

import tempfile
import textwrap
from pathlib import Path
from typing import Any

import pytest

from agentic.capabilities.registry import CAPABILITIES, CapabilityResult, capability
from agentic.config import Settings
from agentic.schemas.brd import BRDItem, OutcomeBinding, ProductBRD, TopTier
from agentic.schemas.core import Entity, KnowledgeGraph, Relationship, SourceLocation
from agentic.store import ArtifactStore


# --------------------------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------------------------


def _ctx(artifacts: dict[str, Any], state: dict[str, Any] | None = None, **settings):
    """A capability context over artifacts saved to a scratch run directory."""
    run = Path(tempfile.mkdtemp())
    store = ArtifactStore(run)
    paths = {name: store.save(name, value) for name, value in artifacts.items()}

    class _Ctx:
        def __init__(self) -> None:
            self.settings = Settings(**settings)
            self.state = {"artifacts": paths, "run_dir": str(run), **(state or {})}
            self.store = store
            self.step = None

        def load(self, name, model=None):
            path = self.state["artifacts"][name]
            return store.load_model(path, model) if model else store.load_json(path)

    return _Ctx()


def _item(title, statement, lines=(10, 20), file="svc.py", **kwargs) -> BRDItem:
    return BRDItem(
        title=title,
        statement=statement,
        confidence=0.9,
        evidence=[SourceLocation(file=file, line_start=lines[0], line_end=lines[1])],
        graph_path=[f"{file}::{title}"],
        **kwargs,
    )


# --------------------------------------------------------------------------------------------
# Fan-out behind fan-out
# --------------------------------------------------------------------------------------------


@pytest.fixture
def two_fan_outs(tmp_path):
    counts = {"first": 0, "second": 0}

    @capability("_nf_make_items")
    def make_items(ctx):
        return CapabilityResult(
            artifacts={"first_items": [{"n": i} for i in range(5)],
                       "second_items": [{"n": i} for i in range(3)]}
        )

    @capability("_nf_first")
    def first(ctx):
        counts["first"] += 1
        return CapabilityResult(fragment={"n": ctx.payload["work_item"]["n"]})

    @capability("_nf_second")
    def second(ctx):
        counts["second"] += 1
        return CapabilityResult(fragment={"module_code": str(ctx.payload["work_item"]["n"])})

    @capability("_nf_done")
    def done(ctx):
        return CapabilityResult(artifacts={"done": {"ok": True}})

    root = tmp_path / "packs" / "two-fan-outs"
    skill = root / "only"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        "---\nname: only\ndescription: test\n---\n\n# only\n\nTest skill.\n", encoding="utf-8"
    )
    (root / "pack.yaml").write_text(
        textwrap.dedent(
            """
            pack: two-fan-outs
            steps:
              - id: make
                skill: only
                mode: deterministic
                capability: _nf_make_items
                produces: [first_items, second_items]
              - id: first
                skill: only
                mode: deterministic
                capability: _nf_first
                fan_out: first_items
                produces: [fragments]
              - id: second
                skill: only
                mode: deterministic
                capability: _nf_second
                fan_out: second_items
                produces: [module_fragments]
              - id: done
                skill: only
                mode: deterministic
                capability: _nf_done
                consumes: [module_fragments]
                produces: [done]
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )
    yield root, counts
    for name in ("_nf_make_items", "_nf_first", "_nf_second", "_nf_done"):
        CAPABILITIES.pop(name, None)


def test_a_fan_out_behind_a_fan_out_runs_once_per_item(two_fan_outs, tmp_path) -> None:
    """The defect that cost ~95% of a run: 151 synthesis workers x 21 modules = 3,171 calls."""
    from agentic.graph.builder import compile_graph
    from agentic.skills.pack import load_pack
    from agentic.state import initial_state

    root, counts = two_fan_outs
    manifest = load_pack(root)
    run_dir = tmp_path / "run"
    (run_dir / "artifacts").mkdir(parents=True)
    graph = compile_graph(manifest, Settings(runs_dir=tmp_path))
    final = graph.invoke(
        initial_state(run_id="r", pack="two-fan-outs", run_dir=str(run_dir),
                      source_root=str(tmp_path), repository_name="r"),
        {"recursion_limit": 100},
    )
    assert counts["first"] == 5
    assert counts["second"] == 3, "the second fan-out must not run once per worker of the first"
    assert len(final["module_fragments"]) == 3
    assert "done" in final["completed_steps"]


def test_module_analyses_keep_one_reply_per_module() -> None:
    from agentic.capabilities.modules import collect_module_analyses

    thin = {"module_code": "AGEN", "module_name": "Rounds"}
    full = {"module_code": "AGEN", "module_name": "Rounds", "module_purpose": "Run rounds.",
            "cross_cutting_rules": [{"title": "r"}]}
    other = {"module_code": "AUTH", "module_name": "Sign-in"}
    ctx = _ctx({}, state={"module_fragments": [thin, full, other, thin]})
    result = collect_module_analyses(ctx)
    analyses = result.artifacts["module_analyses"]["analyses"]
    assert sorted(a["module_code"] for a in analyses) == ["AGEN", "AUTH"]
    assert next(a for a in analyses if a["module_code"] == "AGEN") is full
    assert result.metrics["module_replies_duplicated"] == 2


# --------------------------------------------------------------------------------------------
# Token usage
# --------------------------------------------------------------------------------------------


def test_every_model_call_is_metered_per_model() -> None:
    from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
    from langchain_core.messages import AIMessage

    from agentic.graph.nodes import _metered

    def reply(model):
        return AIMessage(
            content="ok",
            response_metadata={"model_name": model},
            usage_metadata={"input_tokens": 1000, "output_tokens": 50, "total_tokens": 1050,
                            "input_token_details": {"cache_read": 200}},
        )

    mini = GenericFakeChatModel(messages=iter([reply("gpt-4o-mini-2024-07-18")] * 2))
    large = GenericFakeChatModel(messages=iter([reply("gpt-4o-2024-08-06")]))
    with _metered() as usage:
        mini.invoke("one")
        mini.invoke("two")
        large.invoke("three")
    assert usage["model_calls"] == 3
    assert usage["input_tokens"] == 3000 and usage["cached_input_tokens"] == 600
    assert usage["usage_by_model"]["gpt-4o-mini-2024-07-18"]["calls"] == 2
    assert usage["usage_by_model"]["gpt-4o-2024-08-06"]["calls"] == 1


def test_a_failing_call_is_counted() -> None:
    from langchain_core.language_models.fake_chat_models import GenericFakeChatModel

    from agentic.graph.nodes import _metered

    broken = GenericFakeChatModel(messages=iter([]))  # raises when asked for a reply
    with _metered() as usage:
        with pytest.raises(Exception):
            broken.invoke("x")
    assert usage["failed_calls"] == 1 and usage["model_calls"] == 0


def test_each_model_is_priced_at_its_own_rate() -> None:
    from agentic.llm.usage import summarise_usage

    records = [
        {"step": "synthesize", "usage_by_model": {"gpt-4o-mini-2024-07-18": {
            "calls": 2, "input_tokens": 2_000_000, "output_tokens": 200_000,
            "cached_input_tokens": 1_000_000}}},
        {"step": "elevate", "usage_by_model": {"gpt-4o-2024-08-06": {
            "calls": 1, "input_tokens": 1_000_000, "output_tokens": 100_000,
            "cached_input_tokens": 0}}},
        {"step": "ingest"},
    ]
    summary = summarise_usage(records, Settings())
    # 1M fresh at 0.15 + 1M cached at 0.075 + 0.2M output at 0.60
    assert summary["steps"]["synthesize"]["estimated_usd"] == pytest.approx(0.345)
    # gpt-4o: 1M at 2.50 + 0.1M output at 10.00, not gpt-4o-mini rates
    assert summary["steps"]["elevate"]["estimated_usd"] == pytest.approx(3.50)
    assert summary["total"]["calls"] == 3
    assert summary["total"]["estimated_usd"] == pytest.approx(3.845)
    assert summary["models"]["gpt-4o-mini-2024-07-18"]["rate_source"] == "rate for gpt-4o-mini"
    assert "ingest" not in summary["steps"]


def test_configured_rates_override_the_list_prices() -> None:
    from agentic.llm.usage import rates_for

    settings = Settings(llm_rates={"gpt-4o-mini": {"input": 0.165, "output": 0.66}})
    rates, source = rates_for("gpt-4o-mini-2024-07-18", settings)
    assert rates["input"] == 0.165 and rates["cached_input"] == 0.165
    _, unknown = rates_for("some-other-model", settings)
    assert unknown.startswith("fallback")


def test_a_step_can_use_its_own_deployment() -> None:
    from agentic.llm.steps import _settings_for

    class _Step:
        id = "elevate"

    class _Ctx:
        settings = Settings(azure_openai_deployment="gpt-4o-mini",
                            step_deployments={"elevate": "gpt-4o"})
        step = _Step()

    assert _settings_for(_Ctx()).azure_openai_deployment == "gpt-4o"
    _Ctx.step.id = "synthesize"
    assert _settings_for(_Ctx()).azure_openai_deployment == "gpt-4o-mini"


# --------------------------------------------------------------------------------------------
# What the model is shown
# --------------------------------------------------------------------------------------------


def test_the_top_tier_sees_every_module_not_a_truncated_first_one() -> None:
    from agentic.llm.prompt import render_artifact

    analyses = [
        {"module_code": f"M{n:02d}", "module_name": f"Part {n}", "module_purpose": "x" * 400,
         "cross_cutting_rules": [{"title": f"rule {n}", "statement": "y" * 2000}]}
        for n in range(21)
        for _ in range(10)  # each module repeated, as the fan-out bug produced
    ]
    ctx = _ctx({"module_analyses": {"analyses": analyses}})
    rendered = render_artifact(ctx, "module_analyses")
    assert "truncated" not in rendered
    assert rendered["module_count"] == 21
    assert [m["module_code"] for m in rendered["modules"]][:3] == ["M00", "M01", "M02"]


def test_a_synthesis_worker_is_shown_its_module_and_its_slice() -> None:
    from agentic.llm.prompt import _WORK_ITEM_RENDERERS

    render = _WORK_ITEM_RENDERERS["clusters"]
    payload = render({"boundary": "b", "module_code": "PARS", "residual": False,
                      "part": 2, "of_parts": 3, "packet": {}})
    assert payload["cluster"]["module_code"] == "PARS"
    assert payload["cluster"]["part"] == 2
    assert "Slice 1 describes the boundary's own capability" in payload["note"]

    packed = render({"boundary": "b", "module_code": "PARS", "packet": {},
                     "packed_boundaries": ["a", "b"]})
    assert packed["cluster"]["packed_boundaries"] == ["a", "b"]
    assert "own entry point" in packed["note"]


# --------------------------------------------------------------------------------------------
# D1: developer tooling
# --------------------------------------------------------------------------------------------


def _instruction_graph(loaded: bool) -> KnowledgeGraph:
    entities = [
        Entity(id="a:design", kind="INSTRUCTION_ARTIFACT", name="design", qualified_name="design",
               location=SourceLocation(file="repo/.agents/skills/taste/DESIGN.md", line_start=1)),
        Entity(id="r:design", kind="INSTRUCTION_RULE", name="rule", qualified_name="rule",
               location=SourceLocation(file="repo/.agents/skills/taste/DESIGN.md", line_start=9)),
        Entity(id="a:prompt", kind="INSTRUCTION_ARTIFACT", name="p", qualified_name="p",
               location=SourceLocation(file="repo/app/prompts/intake.md", line_start=1)),
        Entity(id="f:loader", kind="FUNCTION", name="load", qualified_name="app.load",
               location=SourceLocation(file="repo/app/llm.py", line_start=3, line_end=9)),
    ]
    relationships = []
    if loaded:
        relationships.append(
            Relationship(id="e1", source="f:loader", target="a:design", kind="LOADS")
        )
    return KnowledgeGraph(repository_name="r", entities=entities, relationships=relationships)


@pytest.mark.parametrize(
    "path, expected",
    [
        ("repo/.agents/skills/x/SKILL.md", True),
        ("repo/.claude/commands/review.md", True),
        ("repo/.github/copilot-instructions.md", True),
        ("repo/CLAUDE.md", True),
        ("repo/app/prompts/intake.md", False),
        ("repo/skills/refund/SKILL.md", False),
    ],
)
def test_developer_tooling_locations_are_recognised(path, expected) -> None:
    from agentic.capabilities.graph_ops import is_dev_tooling_path

    assert is_dev_tooling_path(path) is expected


def test_an_unloaded_dev_tooling_file_is_excluded_and_a_loaded_one_is_kept() -> None:
    from agentic.capabilities.graph_ops import GraphIndex, dev_tooling_instruction_files

    assert dev_tooling_instruction_files(GraphIndex(_instruction_graph(loaded=False))) == {
        "repo/.agents/skills/taste/DESIGN.md"
    }
    # The product loads it, so it is product behaviour wherever it lives.
    assert dev_tooling_instruction_files(GraphIndex(_instruction_graph(loaded=True))) == set()


def test_declaration_clusters_skip_excluded_files() -> None:
    from agentic.capabilities.graph_ops import GraphIndex, declaration_groups

    index = GraphIndex(_instruction_graph(loaded=False))
    everything = declaration_groups(index, set(), 100)
    trimmed = declaration_groups(
        index, set(), 100, exclude_files={"repo/.agents/skills/taste/DESIGN.md"}
    )
    assert len(everything) == 2
    assert [g["boundaries"][0].location.file for g in trimmed] == ["repo/app/prompts/intake.md"]


# --------------------------------------------------------------------------------------------
# D3-D6: duplicates
# --------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "left, right, same",
    [
        ("validate", "check", True),
        ("retrieve", "get", True),
        ("retrieves", "fetch", True),
        ("retriev", "view", True),  # the capability clusterer's stems
        ("view", "delete", False),
        ("publish", "retract", False),
        ("resolve", "reapply", False),
    ],
)
def test_verbs_are_compared_as_operations(left, right, same) -> None:
    from agentic.capabilities.actions import same_operation

    assert same_operation(left, right) is same


def test_an_adverb_is_not_the_action() -> None:
    from agentic.capabilities.consolidate import _action_of

    assert _action_of("The output shall consistently render the defined palette.") == "render"
    assert _action_of("The system shall apply the discount.") == "apply"


def _consolidate(brd: ProductBRD):
    from agentic.capabilities.consolidate import consolidate_requirements

    ctx = _ctx({"brd": brd}, rule_classification=False, dedup_pass=True)
    return consolidate_requirements(ctx)


def test_twins_on_one_registration_line_merge_below_the_old_threshold() -> None:
    """FR-CND-11/12: identical single-line citation, same operation, 0.64 against a 0.65 bar."""
    brd = ProductBRD(functional_requirements=[
        _item("Retrieve Available Candidate Slots",
              "A user shall be able to retrieve available candidate slots by providing the "
              "candidate's unique identifier, so that they can schedule an interview or meeting "
              "with the candidate.", lines=(167, 167)),
        _item("Retrieve available slots for candidates",
              "A candidate shall be able to retrieve available interview slots by providing their "
              "unique token, so that they can select a suitable time for their interview.",
              lines=(167, 167)),
    ])
    result = _consolidate(brd)
    assert len(result.artifacts["brd"].functional_requirements) == 1
    assert result.metrics["identical_evidence_merged"] == 1


def test_one_behaviour_filed_at_two_levels_merges_when_clustering_agreed() -> None:
    """FR-IM-04 / SYS-071: same fifteen lines, validate vs check, grouped as one capability."""
    brd = ProductBRD(
        functional_requirements=[_item(
            "Validate Resume Format",
            "The system shall validate the uploaded resume format by analyzing its content, so "
            "that only resumes that meet the expected structure are accepted for processing.",
            lines=(160, 175), abstraction_basis="external_capability", cluster_id="CAP-072")],
        system_requirements=[_item(
            "Resume Format Validation Rule",
            "When a user uploads a document, the system shall check if the document resembles a "
            "resume format, so that only valid resumes are processed further.",
            lines=(160, 175), abstraction_basis="internal_behaviour", cluster_id="CAP-072")],
    )
    after = _consolidate(brd).artifacts["brd"]
    assert [i.title for i in after.functional_requirements] == ["Validate Resume Format"]
    assert after.system_requirements == []
    assert "Resume Format Validation Rule" in after.functional_requirements[0].superseded


def test_two_levels_on_the_same_code_stay_apart_without_that_agreement() -> None:
    """A true system requirement must not be folded into a mis-levelled capability."""
    brd = ProductBRD(
        functional_requirements=[_item(
            "Save An Order Directly",
            "A client shall be able to save an order directly, so that the stored details are "
            "updated.", lines=(22, 24), abstraction_basis="external_capability")],
        system_requirements=[_item(
            "Retain Order State",
            "The system shall save an order so that its details are retained between requests.",
            lines=(22, 24), abstraction_basis="internal_behaviour")],
    )
    after = _consolidate(brd).artifacts["brd"]
    assert len(after.functional_requirements) == 1 and len(after.system_requirements) == 1


def test_a_cross_level_tie_keeps_the_more_abstract_reading() -> None:
    """Withholding both removed the behaviour entirely."""
    from agentic.capabilities.resolution import _resolve_conflicts
    from agentic.capabilities.correspondence import EvidenceView

    fr = _item("Validate Resume Format", "The system shall validate the resume format so that "
               "only resumes are accepted.", cluster_id="CAP-1", cluster_conflict=True,
               requirement_type="functional")
    sys_item = _item("Resume Check", "The system shall check that the upload resembles a resume.",
                     cluster_id="CAP-1", cluster_conflict=True, requirement_type="system")
    brd = ProductBRD(functional_requirements=[fr], system_requirements=[sys_item])
    view = EvidenceView(KnowledgeGraph(repository_name="r"))
    kept, deferred = _resolve_conflicts(brd, view)
    assert (kept, deferred) == (1, 0)
    assert fr.disposition == "keep" and sys_item.disposition == "merge"


def test_the_final_check_reports_duplicates_that_survived() -> None:
    from agentic.capabilities.resolution import validate_synthesis

    brd = ProductBRD(functional_requirements=[
        _item("Resolve a candidate's reapplication",
              "A recruiter shall be able to resolve a candidate's reapplication.", lines=(142, 180)),
        _item("Resolve candidate reapplication",
              "A candidate shall be able to reapply for a position.", lines=(142, 180)),
        _item("Retrieve candidate details",
              "A recruiter shall be able to retrieve candidate details.", lines=(300, 320)),
        _item("Retrieve candidate email drafts",
              "A recruiter shall be able to retrieve candidate email drafts.", lines=(400, 420)),
    ]).assign_ids()
    result = validate_synthesis(_ctx({"brd": brd}))
    codes = [i["code"] for i in result.artifacts["synthesis_findings"]["issues"]]
    assert codes.count("NEAR_DUPLICATE_TITLE") == 1, codes
    assert result.metrics["duplicate_pairs_similar_title"] == 1


# --------------------------------------------------------------------------------------------
# Synthesis coverage
# --------------------------------------------------------------------------------------------


def test_workflow_twins_are_near_identical_not_identical() -> None:
    from agentic.capabilities.graph_ops import _workflow_twin

    groups = [{"signature": frozenset({"h1", "a", "b", "c", "d", "e", "f", "g", "i"})}]
    near = frozenset({"h2", "a", "b", "c", "d", "e", "f", "g", "i"})  # 8 of 10 shared
    far = frozenset({"h3", "a", "b", "x", "y"})
    assert _workflow_twin(groups, near) is groups[0]
    assert _workflow_twin(groups, far) is None
    assert _workflow_twin(groups, frozenset()) is None


def _chain_graph(count: int) -> KnowledgeGraph:
    entities, relationships = [], []
    for n in range(count):
        entities.append(Entity(id=f"e{n}", kind="ENDPOINT", name=f"e{n}", qualified_name=f"GET /{n}",
                               location=SourceLocation(file="api.py", line_start=n * 10 + 1)))
        entities.append(Entity(id=f"h{n}", kind="FUNCTION", name=f"h{n}", qualified_name=f"h{n}",
                               location=SourceLocation(file="api.py", line_start=n * 10 + 2)))
        relationships.append(Relationship(id=f"r{n}", source=f"e{n}", target=f"h{n}",
                                          kind="CALLS"))
    return KnowledgeGraph(repository_name="r", entities=entities, relationships=relationships)


def test_small_boundaries_of_one_module_share_a_packet() -> None:
    from agentic.capabilities.graph_ops import GraphIndex, _pack_small_groups

    graph = _chain_graph(6)
    index = GraphIndex(graph)
    entities = graph.by_id()
    groups = [
        {"boundaries": [entities[f"e{n}"]], "members": [f"e{n}", f"h{n}"],
         "class": "ENDPOINT", "module": "A" if n < 5 else "B"}
        for n in range(6)
    ]
    packed, folded = _pack_small_groups(index, groups, char_budget=40_000, per_packet=4)
    # Module A: 5 boundaries -> packs of 4 and 1. Module B: 1 boundary alone.
    assert folded == 3
    assert [len(g.get("packed_boundaries") or [None]) for g in packed] == [4, 1, 1]
    assert _pack_small_groups(index, groups, char_budget=40_000, per_packet=1) == (groups, 0)


def test_a_cap_samples_every_module_not_the_top_of_one() -> None:
    from agentic.capabilities.graph_ops import _stratified

    groups = [{"class": "ENDPOINT", "module": "BIG"} for _ in range(10)] + [
        {"class": "ENDPOINT", "module": name} for name in ("S1", "S2", "S3")
    ]
    chosen = _stratified(groups, 4)
    assert {g["module"] for g in chosen} == {"BIG", "S1", "S2", "S3"}


def test_the_cluster_cap_grows_to_cover_every_boundary() -> None:
    from agentic.capabilities.graph_ops import build_boundary_clusters

    graph = _chain_graph(12)
    ctx = _ctx({"graph": graph}, max_clusters=4, max_clusters_ceiling=250,
               boundaries_per_packet=1, residual_clusters=False, declaration_clusters=False)
    result = build_boundary_clusters(ctx)
    plan = result.artifacts["cluster_plan"]
    assert plan["boundaries_planned"] == plan["boundaries_total"] == 12

    fixed = build_boundary_clusters(
        _ctx({"graph": graph}, max_clusters=4, max_clusters_ceiling=0,
             boundaries_per_packet=1, residual_clusters=False, declaration_clusters=False)
    )
    assert fixed.artifacts["cluster_plan"]["boundaries_planned"] == 4


# --------------------------------------------------------------------------------------------
# Areas are modules
# --------------------------------------------------------------------------------------------


def test_a_finding_is_filed_under_its_module_not_an_invented_code() -> None:
    from agentic.capabilities.assemble import item_area

    payload = {"module_code": "PARS"}
    invented = BRDItem(title="t", statement="s", area="RSM", confidence=0.9)
    other_module = BRDItem(title="t", statement="s", area="AUTH", confidence=0.9)
    assert item_area(invented, payload, "PARS", {}, {"PARS", "AUTH"}) == "PARS"
    assert item_area(other_module, payload, "PARS", {}, {"PARS", "AUTH"}) == "AUTH"
    # No module handed down: the worker's own code still stands.
    assert item_area(invented, {}, "GEN", {}, {"PARS"}) == "RSM"


# --------------------------------------------------------------------------------------------
# Multi-call top tier
# --------------------------------------------------------------------------------------------


def _fragments_payload() -> list[dict[str, Any]]:
    def fr(title):
        return {"title": title, "statement": f"A recruiter shall be able to {title.lower()}.",
                "confidence": 0.9}

    return [
        {"module_code": "BOOK", "area_code": "BOOK", "area_name": "Booking",
         "functional_requirements": [fr("Book a slot"), fr("Cancel a slot")]},
        {"module_code": "FEED", "area_code": "FEED", "area_name": "Feedback",
         "functional_requirements": [fr("Submit feedback"), fr("Export feedback")]},
    ]


def test_the_top_tier_binds_by_id_end_to_end() -> None:
    from agentic.capabilities.elevation import (
        apply_outcome_reviews,
        collect_orphan_bindings,
        collect_outcome_proposals,
        compose_outcomes,
        index_capabilities,
    )

    # Index: every capability gets an id and one work item per module.
    indexed = index_capabilities(_ctx({}, state={"fragments": _fragments_payload()}))
    index = indexed.artifacts["capability_index"]
    ids = {c["title"]: c["id"] for c in index["capabilities"]}
    assert len(indexed.artifacts["outcome_work_items"]) == 2

    # E1 replies: one proposal per module, one with an id it was never given.
    proposals = collect_outcome_proposals(_ctx(
        {"capability_index": index},
        state={"outcome_fragments": [
            {"area_code": "BOOK", "proposals": [{"title": "Interviews get scheduled",
             "statement": "s", "capability_ids": [ids["Book a slot"], ids["Cancel a slot"]]}]},
            {"area_code": "FEED", "proposals": [{"title": "Decisions rest on feedback",
             "statement": "s", "capability_ids": [ids["Submit feedback"], "C999"]}]},
        ]},
    ))
    assert proposals.metrics["proposal_ids_invalid"] == 1
    proposal_ids = [p["id"] for p in proposals.artifacts["outcome_proposals"]["proposals"]]
    assert proposal_ids == ["P01", "P02"]

    # E2 reply: two outcomes, one merging P01, one merging P02 plus an invented title.
    top = TopTier(
        business_requirements=[
            BRDItem(title="Run interviews", statement="The system shall enable interviews.",
                    confidence=0.9),
            BRDItem(title="Hire on evidence", statement="The system shall enable decisions.",
                    confidence=0.9),
        ],
        bindings=[
            OutcomeBinding(business_requirement="Run interviews", from_proposals=["P01"]),
            OutcomeBinding(business_requirement="Hire on evidence", from_proposals=["P02"],
                           realised_by=["Display Candidate Table"]),
        ],
    )
    composed = compose_outcomes(_ctx({
        "top_tier": top,
        "outcome_proposals": proposals.artifacts["outcome_proposals"],
        "capability_index": index,
    }))
    assert composed.metrics["capabilities_bound_by_proposal"] == 3
    assert composed.metrics["capability_titles_invented"] == 1
    batches = composed.artifacts["orphan_batches"]
    assert [c["title"] for c in batches[0]["capabilities"]] == ["Export feedback"]

    # E4 reply: the orphan goes to O2.
    top = composed.artifacts["top_tier"]
    swept = collect_orphan_bindings(_ctx(
        {"top_tier": top, "capability_index": index},
        state={"orphan_fragments": [{"assignments": [
            {"capability_id": ids["Export feedback"], "outcome_id": "O2"}]}]},
    ))
    assert swept.metrics["binding_coverage_percent"] == 100.0
    bindings = {b.business_requirement: b.realised_by for b in swept.artifacts["top_tier"].bindings}
    assert sorted(bindings["Hire on evidence"]) == ["Export feedback", "Submit feedback"]
    assert len(swept.artifacts["outcome_review_items"]) == 2

    # E5 replies: criteria for O1, a flag on O2.
    reviewed = apply_outcome_reviews(_ctx(
        {"top_tier": swept.artifacts["top_tier"]},
        state={"review_fragments": [
            {"outcome_id": "O1", "acceptance_criteria": ["A recruiter can book a slot."]},
            {"outcome_id": "O2", "renamed_area": True, "concern": "names the feedback area"},
        ]},
    ))
    final = reviewed.artifacts["top_tier"]
    assert final.business_requirements[0].acceptance_criteria == ["A recruiter can book a slot."]
    assert any("Hire on evidence" in q for q in final.open_questions)


def test_switching_multi_call_elevation_off_produces_no_extra_work() -> None:
    from agentic.capabilities.elevation import index_capabilities

    result = index_capabilities(
        _ctx({}, state={"fragments": _fragments_payload()}, multi_call_elevation=False)
    )
    assert result.artifacts["outcome_work_items"] == []
    assert len(result.artifacts["capability_index"]["capabilities"]) == 4


def test_usage_is_metered_inside_a_real_graph_run(tmp_path) -> None:
    """The meter must see calls made inside LangGraph's worker threads, and the run must say so."""
    import json as _json

    from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
    from langchain_core.messages import AIMessage

    from agentic.graph.builder import compile_graph
    from agentic.skills.pack import load_pack
    from agentic.state import initial_state

    @capability("_nf_calls_a_model")
    def calls_a_model(ctx):
        model = GenericFakeChatModel(messages=iter([AIMessage(
            content="ok", response_metadata={"model_name": "gpt-4o-mini"},
            usage_metadata={"input_tokens": 500, "output_tokens": 20, "total_tokens": 520},
        )]))
        model.invoke("hello")
        return CapabilityResult(artifacts={"out": {"ok": True}})

    try:
        root = tmp_path / "packs" / "metered"
        (root / "only").mkdir(parents=True)
        (root / "only" / "SKILL.md").write_text(
            "---\nname: only\ndescription: t\n---\n\n# only\n\nT.\n", encoding="utf-8"
        )
        (root / "pack.yaml").write_text(
            "pack: metered\nsteps:\n  - id: ask\n    skill: only\n    mode: deterministic\n"
            "    capability: _nf_calls_a_model\n    produces: [out]\n",
            encoding="utf-8",
        )
        run_dir = tmp_path / "run"
        (run_dir / "artifacts").mkdir(parents=True)
        final = compile_graph(load_pack(root), Settings(runs_dir=tmp_path)).invoke(
            initial_state(run_id="r", pack="metered", run_dir=str(run_dir),
                          source_root=str(tmp_path), repository_name="r")
        )
    finally:
        CAPABILITIES.pop("_nf_calls_a_model", None)

    record = next(r for r in final["step_records"] if r["step"] == "ask")
    assert record["model_calls"] == 1 and record["input_tokens"] == 500
    usage = _json.loads((run_dir / "artifacts" / "llm_usage.json").read_text(encoding="utf-8"))
    assert usage["total"]["calls"] == 1
    assert usage["steps"]["ask"]["estimated_usd"] == pytest.approx((500 * 0.15 + 20 * 0.60) / 1e6,
                                                                  abs=1e-4)


# --------------------------------------------------------------------------------------------
# Second live run (8d93e96f): outcomes rejected against borrowed code, proposals dropped
# --------------------------------------------------------------------------------------------


def test_an_outcome_is_never_rejected_against_borrowed_code() -> None:
    """4 of 6 outcomes were rejected by reading them against sampled child citations."""
    from agentic.capabilities.correspondence import validate_correspondence

    outcome = _item("Streamlined Interview Scheduling",
                    "The system shall enable candidates to book interview slots.",
                    requirement_type="business")
    brd = ProductBRD(business_requirements=[outcome])
    result = validate_correspondence(_ctx(
        {"brd": brd, "graph": KnowledgeGraph(repository_name="r")},
        correspondence_entailment=False,
    ))
    judged = result.artifacts["brd"].business_requirements[0]
    assert judged.validation.verdict != "rejected"
    assert judged.validation.checked_by == "capabilities"


def test_an_outcome_is_supported_by_capabilities_that_passed() -> None:
    from agentic.capabilities.correspondence import _judge_outcomes_by_capabilities
    from agentic.schemas.brd import CorrespondenceOutcome

    outcome = _item("Run Interviews", "The system shall enable interviews.",
                    requirement_type="business")
    passed = [_item(f"Cap {n}", "A recruiter shall be able to book a slot.",
                    parent="Run Interviews", lines=(n * 10, n * 10 + 5)) for n in range(1, 3)]
    for item in passed:
        item.validation = CorrespondenceOutcome(verdict="supported")
    brd = ProductBRD(business_requirements=[outcome], functional_requirements=passed)
    assert _judge_outcomes_by_capabilities(brd) == 1
    assert outcome.validation.verdict == "supported"

    passed[1].validation = CorrespondenceOutcome(verdict="rejected")
    assert _judge_outcomes_by_capabilities(brd) == 0
    assert outcome.validation.verdict == "unchecked"


def test_a_proposal_the_merge_left_out_is_swept_whole() -> None:
    """The merge placed 6 of 80 proposals; the other 74 were scattered into single capabilities."""
    from agentic.capabilities.elevation import collect_orphan_bindings, compose_outcomes

    index = {"capabilities": [
        {"id": f"C00{n}", "title": f"Cap {n}", "statement": "s", "area": "A"} for n in range(1, 6)
    ]}
    proposals = {"proposals": [
        {"id": "P01", "area_code": "A", "title": "One", "statement": "s",
         "capability_ids": ["C001", "C002"]},
        {"id": "P02", "area_code": "A", "title": "Two", "statement": "s",
         "capability_ids": ["C003", "C004"]},
    ]}
    top = TopTier(
        business_requirements=[BRDItem(title="Outcome", statement="s", confidence=0.9)],
        bindings=[OutcomeBinding(business_requirement="Outcome", from_proposals=["P01"])],
    )
    composed = compose_outcomes(_ctx(
        {"top_tier": top, "outcome_proposals": proposals, "capability_index": index}
    ))
    assert composed.metrics["proposals_unmerged"] == 1
    batch = composed.artifacts["orphan_batches"][0]
    assert [p["id"] for p in batch["proposals"]] == ["P02"]
    assert [c["id"] for c in batch["capabilities"]] == ["C005"]  # C003/C004 travel with P02

    swept = collect_orphan_bindings(_ctx(
        {"top_tier": composed.artifacts["top_tier"], "capability_index": index,
         "outcome_proposals": proposals},
        state={"orphan_fragments": [{"assignments": [
            {"capability_id": "P02", "outcome_id": "O1"},
            {"capability_id": "C005", "outcome_id": "NONE"},
        ]}]},
    ))
    bound = swept.artifacts["top_tier"].bindings[0].realised_by
    assert sorted(bound) == ["Cap 1", "Cap 2", "Cap 3", "Cap 4"]
    assert swept.metrics["orphan_proposals_placed"] == 1
    assert swept.metrics["binding_coverage_percent"] == 80.0


def test_the_merge_is_given_every_proposal_id_as_a_checklist() -> None:
    from agentic.llm.prompt import render_artifact

    proposals = {"proposals": [
        {"id": f"P{n:02d}", "area_code": "A", "title": "t", "statement": "s",
         "capability_ids": ["C001"]} for n in range(1, 4)
    ]}
    rendered = render_artifact(_ctx({"outcome_proposals": proposals}), "outcome_proposals")
    assert rendered["every_proposal_id"] == ["P01", "P02", "P03"]

"""The test-generation pack as a compiled workflow.

The architecture's claim is that a second pack is a data change: markdown, a manifest, and
capabilities registered the ordinary way. These tests run the real pack end to end against real
artifacts, so if the claim ever stops holding it fails here rather than in a design review.
"""

from __future__ import annotations

import collections
import json
import shutil
from pathlib import Path

import pytest

from agentic.config import Settings
from agentic.graph.builder import build_state_graph, describe_topology
from agentic.runtime import available_packs, preflight, prepare_run, run
from agentic.skills.pack import PackManifest, StepMode, load_pack

REPO_ROOT = Path(__file__).resolve().parents[1]
PACK_DIR = REPO_ROOT / "test-generation-skills"
FIXTURES = Path(__file__).parent / "fixtures" / "requirement_sets"


@pytest.fixture(scope="session")
def testgen_manifest() -> PackManifest:
    return load_pack(PACK_DIR)


@pytest.fixture
def artifacts_dir(tmp_path: Path):
    """A stand-in for a requirements run's `artifacts/` folder, which is what this pack consumes."""

    def _build(fixture: str, *, extra: dict[str, object] | None = None) -> Path:
        directory = tmp_path / "artifacts"
        directory.mkdir(exist_ok=True)
        shutil.copy(FIXTURES / f"{fixture}.json", directory / "requirements.json")
        for name, payload in (extra or {}).items():
            (directory / name).write_text(json.dumps(payload), encoding="utf-8")
        return directory

    return _build


# ---------------------------------------------------------------------------------------------
# The manifest
# ---------------------------------------------------------------------------------------------


def test_the_pack_is_discovered_alongside_the_requirements_pack() -> None:
    packs = available_packs()
    assert "testcase-generation" in packs
    assert "codebase-requirements" in packs, "adding a pack must not displace the first"


def test_the_pack_loads_and_resolves_every_reference(testgen_manifest: PackManifest) -> None:
    assert testgen_manifest.pack == "testcase-generation"
    assert [step.id for step in testgen_manifest.steps] == [
        "ingest_requirements",
        "qualify",
        "gate_input",
        "analyse_signals",
        "plan_batches",
        "design_tests",
        "assemble_tests",
        "dedupe",
        "analyse_coverage",
        "gate_tests",
        "repair_tests",
        "apply_test_repairs",
        "prune_tests",
        "final_check",
        "render_tests",
    ]


def test_the_graph_compiles(testgen_manifest: PackManifest, settings: Settings) -> None:
    build_state_graph(testgen_manifest, settings).compile()


def test_only_design_and_repair_call_a_model(testgen_manifest: PackManifest) -> None:
    """Judgement is bought in exactly two places. Everything else is deterministic.

    Phase 1 asserted the pack needed no credentials at all. That stopped being true the moment
    design landed, and preflight must now say so *before* ingestion rather than part-way through.
    """
    model_steps = [s.id for s in testgen_manifest.steps if s.mode is StepMode.LLM]
    assert model_steps == ["design_tests", "repair_tests"]

    problems = preflight(testgen_manifest, Settings(azure_openai_endpoint=""))
    assert problems and "design_tests" in problems[0]


def test_the_gates_route_as_designed(testgen_manifest: PackManifest) -> None:
    rows = {row["step"]: row for row in describe_topology(testgen_manifest)}
    assert rows["gate_input"]["on_failure"] == "halt"
    # The quality gate repairs rather than halting, then falls through to deterministic pruning.
    assert rows["gate_tests"]["on_success"] == "final_check"
    assert "repair_tests" in rows["gate_tests"]["on_failure"]
    assert "prune_tests" in rows["gate_tests"]["on_failure"]


def test_repair_loops_back_through_deduplication(testgen_manifest: PackManifest) -> None:
    """A repaired case may now duplicate one it did not before, and coverage is stale."""
    assert testgen_manifest.step("apply_test_repairs").next == "dedupe"


def test_no_unwired_skill_in_this_pack_either(testgen_manifest: PackManifest) -> None:
    """The same marker rule the requirements pack is held to."""
    registry = testgen_manifest.skill_registry()
    referenced = {step.skill for step in testgen_manifest.steps}
    if testgen_manifest.master_skill:
        referenced.add(testgen_manifest.master_skill)
    for name in registry.available():
        marked = str(registry.load(name).frontmatter.get("status", "")).strip() == "not-wired"
        assert (name in referenced) != marked, name


class _StubDesigner:
    """A designer that reads its own packet and spends exactly the budget it was given.

    Deliberately obedient. What is under test is the machinery — fan-out, budgets, assembly,
    traceability — not the model's judgement, and a stub that invented its own dimensions would be
    testing the drop path instead.
    """

    def __init__(self) -> None:
        self.packets: list[dict] = []
        self._schema = None

    def with_structured_output(self, schema):
        self._schema = schema
        return self

    def invoke(self, messages):
        payload = json.loads(messages[-1][1])
        packet = payload["cluster"]["evidence"]
        self.packets.append(packet)

        scenarios = []
        for item in packet["requirements"]:
            cases = []
            for index, dimension in enumerate(item["dimensions_to_cover"][: item["case_budget"]]):
                cases.append(
                    {
                        "requirement_id": item["requirement_id"],
                        "dimension": dimension,
                        "title": f"{dimension} case for {item['requirement_id']}",
                        "objective": (
                            f"Confirm the {dimension} behaviour holds, because a failure here "
                            "would go unnoticed by any other case."
                        ),
                        "covers_criterion": 0 if item["acceptance_criteria"] else None,
                        "preconditions": ["The system is reachable."],
                        "inputs": [{"name": "identifier", "value_kind": "valid"}],
                        "steps": ["Perform the described action."],
                        "expected_results": ["The described outcome is observable."],
                        "derivation": item["derivation_default"],
                        "assumptions": (
                            [] if item["derivation_default"] == "direct" else ["Route assumed."]
                        ),
                        "risk_reason": item["risk_reason"],
                    }
                )
            if cases:
                scenarios.append(
                    {
                        "requirement_id": item["requirement_id"],
                        "title": f"Situation for {item['requirement_id']}",
                        "context": "A known starting state.",
                        "preconditions": [],
                        "cases": cases,
                    }
                )
        return self._schema.model_validate({"scenarios": scenarios, "notes": []})


@pytest.fixture(autouse=True)
def stub_designer(monkeypatch):
    """Autouse: every end-to-end test in this module now traverses the design step.

    Before the design step existed these ran with no model at all. Leaving the stub opt-in meant
    each of them silently started exercising a real provider call instead of the pipeline.
    """
    stub = _StubDesigner()
    monkeypatch.setattr("agentic.llm.steps.build_chat_model", lambda settings: stub)
    return stub


# ---------------------------------------------------------------------------------------------
# End to end, on real artifacts
# ---------------------------------------------------------------------------------------------


def _run(directory: Path, settings: Settings):
    prepared = prepare_run(source=directory, settings=settings)
    return prepared, run("testcase-generation", prepared, settings=settings)


def _repair_budget() -> int:
    """The repair budget the manifest declares, read rather than duplicated in a literal."""
    return load_pack(PACK_DIR).step("gate_tests").max_repairs


def _artifact(prepared, name: str):
    return json.loads(
        (prepared.run_dir / "artifacts" / f"{name}.json").read_text(encoding="utf-8")
    )


def test_a_rich_artifact_runs_end_to_end_and_accounts_for_every_requirement(
    artifacts_dir, settings: Settings
) -> None:
    prepared, final = _run(artifacts_dir("rich"), settings)

    assert final["status"] in {"passed", "passed_with_warnings"}
    assert final["completed_steps"][:3] == ["ingest_requirements", "qualify", "gate_input"]

    report = _artifact(prepared, "qualification_report")
    assert report["total"] == 190, "every requirement is banded"
    assert len(report["records"]) == 190, "including the ones that produce nothing"
    assert sum(report["bands"].values()) == 190
    assert report["discarded_criteria"] == 59, "the generated criteria were caught"
    assert report["testable"] > 0


def test_the_pack_reads_an_older_artifact_without_a_version_switch(
    artifacts_dir, settings: Settings
) -> None:
    prepared, final = _run(artifacts_dir("legacy_20key"), settings)
    assert final["status"] in {"passed", "passed_with_warnings"}

    requirement_set = _artifact(prepared, "requirement_set")
    assert requirement_set["missing_contract_keys"] == ["area", "area_name", "priority"]
    assert len(requirement_set["requirements"]) == 21
    assert _artifact(prepared, "qualification_report")["total"] == 21


def test_a_thin_artifact_produces_a_thin_result_rather_than_a_failure(
    artifacts_dir, settings: Settings
) -> None:
    prepared, final = _run(artifacts_dir("thin"), settings)
    assert final["status"] in {"passed", "passed_with_warnings"}
    assert _artifact(prepared, "qualification_report")["total"] == 13


def test_an_artifact_of_only_rejected_requirements_halts_rather_than_inventing_tests(
    artifacts_dir, settings: Settings
) -> None:
    """The one blocking condition. A suite built on a rejected claim asserts something unsupported."""
    prepared, final = _run(artifacts_dir("degenerate"), settings)

    assert final["status"] == "failed"
    gate = _artifact(prepared, "input_gate")
    assert not gate["valid"]
    assert [issue["code"] for issue in gate["issues"] if issue["severity"] == "error"] == [
        "NO_TESTABLE_REQUIREMENTS"
    ]
    # Qualification still ran and still explains itself, which is what makes the halt actionable.
    assert _artifact(prepared, "qualification_report")["records"][0]["reason_codes"] == [
        "REQ_REJECTED"
    ]


def test_a_malformed_artifact_still_runs_and_reports_what_it_could_not_read(
    artifacts_dir, settings: Settings
) -> None:
    prepared, final = _run(artifacts_dir("malformed"), settings)
    assert final["status"] in {"passed", "passed_with_warnings"}

    requirement_set = _artifact(prepared, "requirement_set")
    assert requirement_set["read_issues"], "problems are recorded, not raised"

    gate = _artifact(prepared, "input_gate")
    assert gate["valid"], "unreadable entries are a warning, not a block"
    assert "INPUT_READ_ISSUES" in {issue["code"] for issue in gate["issues"]}


def test_optional_enrichment_sharpens_qualification_when_present(
    artifacts_dir, settings: Settings
) -> None:
    """And its absence is never an error — the first assertion is the control."""
    plain, _ = _run(artifacts_dir("thin"), settings)
    baseline = _artifact(plain, "qualification_report")
    assert "UNRESOLVED_CONFLICT" not in baseline["reason_codes"]

    target = json.loads((FIXTURES / "thin.json").read_text(encoding="utf-8"))[0]["id"]
    enriched_dir = artifacts_dir(
        "thin",
        extra={
            "synthesis_findings.json": {
                "issues": [
                    {
                        "code": "UNRESOLVED_CONFLICT",
                        "message": "competing readings",
                        "subject_id": target,
                    }
                ]
            }
        },
    )
    enriched, _ = _run(enriched_dir, settings)
    report = _artifact(enriched, "qualification_report")
    record = next(r for r in report["records"] if r["requirement_id"] == target)
    assert "UNRESOLVED_CONFLICT" in record["reason_codes"]


def test_pointing_the_pack_at_a_source_repository_says_so_plainly(
    tmp_path: Path, settings: Settings
) -> None:
    """The likeliest operator mistake deserves a message naming the fix, not a stack trace."""
    directory = tmp_path / "not-artifacts"
    directory.mkdir()
    (directory / "main.py").write_text("print('hello')", encoding="utf-8")

    prepared, final = _run(directory, settings)
    assert final["status"] == "failed"
    assert "requirements.json" in final["halt_reason"]
    assert "artifacts" in final["halt_reason"]


def test_an_artifact_of_the_wrong_kind_is_recognised(tmp_path: Path, settings: Settings) -> None:
    directory = tmp_path / "artifacts"
    directory.mkdir()
    (directory / "requirements.json").write_text('{"not": "an array"}', encoding="utf-8")

    _, final = _run(directory, settings)
    assert final["status"] == "failed"
    assert "array" in final["halt_reason"]


# ---------------------------------------------------------------------------------------------
# Phase 2 end to end, through the real compiled graph with the designer stubbed
# ---------------------------------------------------------------------------------------------


def test_the_whole_pack_runs_and_produces_a_traceable_suite(
    artifacts_dir, settings: Settings, stub_designer
) -> None:
    prepared, final = _run(artifacts_dir("rich"), settings)

    assert final["status"] in {"passed", "passed_with_warnings"}
    assert final["completed_steps"][-1] == "render_tests"

    suite = _artifact(prepared, "test_suite")
    cases = [case for scenario in suite["scenarios"] for case in scenario["cases"]]
    assert cases, "the run produced no cases at all"

    signals = _artifact(prepared, "test_signals")
    generating = {s["requirement_id"] for s in signals["signals"] if s["route"] == "generate"}
    budgets = {s["requirement_id"]: s["case_budget"] for s in signals["signals"]}

    for case in cases:
        assert case["id"].startswith("TC-"), "every case is addressable"
        assert case["requirement_ids"], "every case traces to a requirement"
        assert case["covers"], "every case says what it checks"
        assert case["objective"], "every case says why it exists"
        assert case["expected_results"], "every case is falsifiable"
        assert set(case["requirement_ids"]) <= generating, "no case for a routed-away requirement"

    # The budget is a ceiling that actually binds.
    per_requirement = collections.Counter(
        rid for case in cases for rid in case["requirement_ids"]
    )
    for requirement_id, count in per_requirement.items():
        assert count <= budgets[requirement_id], requirement_id


def test_the_suite_stays_far_below_a_naive_case_per_dimension_sweep(
    artifacts_dir, settings: Settings, stub_designer
) -> None:
    """The anti-explosion claim, measured through the real pipeline rather than projected."""
    prepared, _ = _run(artifacts_dir("rich"), settings)
    suite = _artifact(prepared, "test_suite")
    cases = suite["counts"]["cases"]
    requirements = _artifact(prepared, "qualification_report")["total"]

    assert cases <= requirements * 2, f"{cases} cases for {requirements} requirements"
    assert cases >= 100, "and it must not have collapsed to a token suite either"


def test_every_requirement_is_accounted_for_as_covered_or_deferred(
    artifacts_dir, settings: Settings, stub_designer
) -> None:
    """No requirement may simply disappear between qualification and the suite."""
    prepared, _ = _run(artifacts_dir("rich"), settings)

    qualified = {r["requirement_id"] for r in _artifact(prepared, "qualification_report")["records"]}
    suite = _artifact(prepared, "test_suite")
    covered = {
        rid
        for scenario in suite["scenarios"]
        for case in scenario["cases"]
        for rid in case["requirement_ids"]
    }
    deferred = {entry["requirement_id"] for entry in suite["deferred"]}

    assert covered | deferred == qualified
    assert all(entry["reason"] for entry in suite["deferred"]), "every gap says why"


def test_each_worker_sees_only_its_own_packet(
    artifacts_dir, settings: Settings, stub_designer
) -> None:
    """Cost stays linear only if a worker is never shown the whole set."""
    _run(artifacts_dir("rich"), settings)

    assert len(stub_designer.packets) > 1, "the fan-out did not fan out"
    total = sum(len(p["requirements"]) for p in stub_designer.packets)
    for packet in stub_designer.packets:
        assert len(packet["requirements"]) < total, "a worker saw everything"
        assert "statement" in packet["requirements"][0]
        assert "evidence" not in packet["requirements"][0], "no source evidence reaches a worker"


def test_a_run_with_nothing_to_design_still_completes(
    artifacts_dir, settings: Settings, stub_designer
) -> None:
    """Every requirement deferring is a real outcome, not a failure."""
    prepared, final = _run(artifacts_dir("thin"), settings)
    assert final["status"] in {"passed", "passed_with_warnings"}
    assert _artifact(prepared, "test_suite")["counts"]["cases"] >= 0


# ---------------------------------------------------------------------------------------------
# The bounded repair loop
# ---------------------------------------------------------------------------------------------


class _BadDesigner(_StubDesigner):
    """A designer that writes the failure mode the whole pack exists to prevent."""

    def invoke(self, messages):
        fragment = super().invoke(messages)
        for scenario in fragment.scenarios:
            for case in scenario.cases:
                case.expected_results = ["The feature works correctly."]
        return fragment


def test_a_suite_of_vacuous_cases_is_repaired_rather_than_published(
    artifacts_dir, settings: Settings, monkeypatch
) -> None:
    """The gate must reject "works correctly", and the repair pass must be given the chance."""
    designer = _BadDesigner()
    repaired: list[int] = []

    def build(_settings):
        return designer

    monkeypatch.setattr("agentic.llm.steps.build_chat_model", build)

    original = designer.invoke

    def invoke(messages):
        payload = json.loads(messages[-1][1])
        if "test_suite" in payload:  # the repair call, not the design call
            to_fix = payload["test_suite"]["cases_to_fix"]
            repaired.append(len(to_fix))
            return designer._schema.model_validate(
                {
                    "cases": [
                        {**case, "expected_results": ["The stored entry is no longer listed."]}
                        for case in to_fix
                    ],
                    "deleted": [],
                }
            )
        return original(messages)

    monkeypatch.setattr(designer, "invoke", invoke)

    prepared, final = _run(artifacts_dir("thin"), settings)

    assert repaired, "the repair step never ran, so nothing was proven"
    assert final["completed_steps"][-1] == "render_tests"

    gate = _artifact(prepared, "test_gate")
    assert gate["valid"], "the repaired suite should pass on the second look"

    cases = [c for s in _artifact(prepared, "test_suite")["scenarios"] for c in s["cases"]]
    assert cases
    assert not any("works correctly" in r.lower() for c in cases for r in c["expected_results"])


def test_the_repair_step_sees_only_rejected_cases(
    artifacts_dir, settings: Settings, monkeypatch
) -> None:
    """The trick that keeps the repair reply small however large the suite grows."""
    designer = _BadDesigner()
    seen: list[dict] = []
    monkeypatch.setattr("agentic.llm.steps.build_chat_model", lambda s: designer)
    original = designer.invoke

    def invoke(messages):
        payload = json.loads(messages[-1][1])
        if "test_suite" in payload:
            seen.append(payload["test_suite"])
            return designer._schema.model_validate({"cases": [], "deleted": []})
        return original(messages)

    monkeypatch.setattr(designer, "invoke", invoke)
    _run(artifacts_dir("thin"), settings)

    assert seen
    for payload in seen:
        assert "cases_to_fix" in payload
        assert "scenarios" not in payload, "the whole suite reached the repair prompt"
        assert all("problems" in case for case in payload["cases_to_fix"])


def test_an_unrepairable_suite_is_pruned_rather_than_published(
    artifacts_dir, settings: Settings, monkeypatch
) -> None:
    """When the budget is spent, what still fails is dropped and recorded — not published."""
    designer = _BadDesigner()
    monkeypatch.setattr("agentic.llm.steps.build_chat_model", lambda s: designer)
    original = designer.invoke

    def invoke(messages):
        payload = json.loads(messages[-1][1])
        if "test_suite" in payload:
            # A repair pass that changes nothing, which is what exhausts the budget.
            return designer._schema.model_validate({"cases": [], "deleted": []})
        return original(messages)

    monkeypatch.setattr(designer, "invoke", invoke)
    prepared, final = _run(artifacts_dir("thin"), settings)

    budget = _repair_budget()
    assert final["completed_steps"].count("repair_tests") == budget, "the budget must bind"
    assert "prune_tests" in final["completed_steps"]

    suite = _artifact(prepared, "test_suite")
    assert suite["counts"]["pruned"] > 0
    pruned = [d for d in suite["deferred"] if d["route"] == "pruned"]
    assert pruned and all(entry["reason"] for entry in pruned), "each pruned case says why"


def test_coverage_accounts_for_every_requirement_end_to_end(
    artifacts_dir, settings: Settings
) -> None:
    prepared, _ = _run(artifacts_dir("rich"), settings)
    coverage = _artifact(prepared, "coverage")
    qualified = _artifact(prepared, "qualification_report")

    assert len(coverage["requirements"]) == qualified["total"] == 190
    assert all(entry["verdict"] for entry in coverage["requirements"])
    # Anything not covered must carry a reason a person can act on.
    for entry in coverage["requirements"]:
        if entry["verdict"] in {"deferred", "excluded", "none"}:
            assert entry["reason"], entry["requirement_id"]

"""Whole-graph behaviour, with the model stubbed.

Everything except the model call is real: manifest compilation, routing, both gates, the bounded
repair loop, quarantine, and rendering. That is deliberate — the topology and the gates are what
must be trusted, and they can be proven without spending a token.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from tests.conftest import stub_boundary_assignments

from agentic.config import Settings
from agentic.runtime import prepare_run, run
from agentic.schemas.brd import (
    OutcomeBinding,
    BRDFragment,
    BRDItem,
    ProductBRD,
    RepairedItems,
    TopTier,
)
from agentic.schemas.core import SourceLocation

FIXTURE_APP = Path(__file__).parent / "fixtures" / "sample_app"

GOOD_STATEMENT = "Staff can record a new supplier bill and retrieve it later by its reference."
SCOPE_STATEMENT = "A submitted bill is accepted only when its value is within the approved ceiling."


def _handler_line() -> int:
    """Find the real line of the sample handler, so evidence is genuinely verifiable."""
    text = (FIXTURE_APP / "app" / "main.py").read_text(encoding="utf-8")
    for number, line in enumerate(text.splitlines(), start=1):
        if re.match(r"\s*def create_invoice", line):
            return number
    raise AssertionError("sample handler not found")


HANDLER_LINE = _handler_line()

# What the boundary over `create_invoice` is called. Graphify records that the handler references
# `post` but not the `"/invoices"` handed to it, so the route is read from the literals beside the
# definition and handed to the classifier as evidence — which is what keeps this name a route
# rather than the function behind it.
HANDLER_BOUNDARY = "POST /invoices"


def _grounded_business_requirement() -> BRDItem:
    return BRDItem(
        title="Record supplier bills",
        statement=GOOD_STATEMENT,
        rationale="An externally reachable operation accepts and stores a bill.",
        confidence=0.9,
        evidence=[
            SourceLocation(
                file="app/main.py",
                line_start=HANDLER_LINE,
                line_end=HANDLER_LINE + 6,
                symbol="create_invoice",
            )
        ],
        graph_path=["app/main.py::create_invoice()", "POST /invoices"],
    )


def _grounded_capability() -> BRDItem:
    return BRDItem(
        title="Bill submission",
        statement=SCOPE_STATEMENT,
        confidence=0.85,
        evidence=[SourceLocation(file="app/main.py", line_start=HANDLER_LINE)],
        graph_path=[HANDLER_BOUNDARY],
        parent="Record supplier bills",
    )


def _ungrounded_business_requirement() -> BRDItem:
    """Cites a file that does not exist — exactly what the gate must catch."""
    return BRDItem(
        title="Forecast supplier spend",
        statement="The product predicts next quarter's spend for every supplier.",
        confidence=0.9,
        evidence=[SourceLocation(file="app/forecasting.py", line_start=12)],
        graph_path=["app.forecasting.predict"],
    )


AREA = "BILL"


class StubModel:
    """Stands in for the chat model, recording every call and honouring the output schema.

    Four schemas now reach it, one per model step: `BoundaryAssignments` from the extraction
    phase, `BRDFragment` from each fan-out worker, `TopTier` from the single elevation call, and
    `RepairedItems` from a repair pass. Assembly between them is deterministic, so a test can
    script the fan-out and the top tier separately and see exactly what survives.
    """

    def __init__(
        self,
        script: list[ProductBRD] | None = None,
        *,
        fragment: BRDFragment | None = None,
        top: TopTier | None = None,
        raise_on: frozenset[str] = frozenset(),
        repair_mode: str = "noop",
    ) -> None:
        self.script = script or []
        self.fragment = fragment
        self.top = top
        self.raise_on = raise_on
        self.repair_mode = repair_mode
        self.rejected_seen: list[list[str]] = []
        self.calls: list[str] = []
        self._schema = None

    def with_structured_output(self, schema):
        self._schema = schema
        return self

    def invoke(self, messages):
        name = self._schema.__name__
        self.calls.append(name)
        if name in self.raise_on:
            # What a provider rejecting the response schema looks like from in here.
            raise RuntimeError(
                f"Step failed calling the model: BadRequestError: Error code: 400 - "
                f"Invalid schema for response_format {name!r}"
            )
        if name == "BoundaryAssignments":
            # Extraction now needs a model to say which nodes are interaction boundaries, so a
            # run with no answer here has no boundaries, no clusters, and no fan-out at all. One
            # call per batch of candidates reaches here, exactly like `BRDFragment` below reaches
            # here once per cluster.
            from agentic.schemas.boundaries import BoundaryAssignments

            payload = json.loads(messages[-1][1])
            candidates = payload.get("candidates") or []
            return BoundaryAssignments.model_validate(
                stub_boundary_assignments(candidates)
            )
        if name == "BRDFragment":
            base = self.fragment or BRDFragment(
                purpose_hint="Recording and retrieving supplier bills.",
                area_code=AREA,
                area_name="Supplier billing",
                functional_requirements=[_grounded_capability()],
            )
            return base.model_copy(deep=True)
        if name == "TopTier":
            return (self.top or _top()).model_copy(deep=True)
        if name == "RepairedItems":
            return self._repair(messages)
        if name == "EntailmentVerdicts":
            # What a working model returns for claims that are in fact supported. Scripted rather
            # than left to raise, because the correspondence pass swallows an exception here and
            # falls back to the structural checks — which would make this test pass for the wrong
            # reason and hide a regression in the entailment path.
            from agentic.capabilities.correspondence import EntailmentVerdict, EntailmentVerdicts

            payload = json.loads(messages[-1][1])
            return EntailmentVerdicts(
                verdicts=[
                    EntailmentVerdict(id=entry["id"], supported=True)
                    for entry in payload.get("items", [])
                ]
            )
        raise AssertionError(f"unexpected output schema {name!r}")

    def _repair(self, messages) -> RepairedItems:
        """Answer a repair call the way a model would: from the ids the prompt names.

        Reading the rejected ids out of the payload rather than hard-coding them keeps the test
        honest about the contract — the step only ever gets to touch what the gate rejected.
        """
        payload = json.loads(messages[-1][1])
        rejected = [
            entry["item"]
            for entry in (payload.get("requirement_gate") or {}).get("rejected_items", [])
            if entry.get("item")
        ]
        self.rejected_seen.append(sorted(set(rejected)))
        if self.repair_mode == "delete":
            return RepairedItems(deleted=sorted(set(rejected)))
        return RepairedItems()

    @property
    def product_brd_calls(self) -> int:
        """Repair calls, whatever schema they carry."""
        return sum(call == "RepairedItems" for call in self.calls)

    @property
    def fragment_calls(self) -> int:
        return sum(call == "BRDFragment" for call in self.calls)


@pytest.fixture
def e2e(tmp_path, monkeypatch):
    """Run the real graph over the fixture app with a scripted model."""

    def _run(script=None, *, fragment=None, top=None, raise_on=frozenset(),
             repair_mode="noop", **setting_overrides):
        stub = StubModel(script, fragment=fragment, top=top, raise_on=raise_on,
                         repair_mode=repair_mode)
        monkeypatch.setattr("agentic.llm.steps.build_chat_model", lambda settings: stub)
        settings = Settings(
            runs_dir=tmp_path / "runs",
            max_clusters=4,
            evidence_packet_chars=6000,
            max_repairs=2,
            **setting_overrides,
        )
        prepared = prepare_run(source=FIXTURE_APP, settings=settings)
        final = run("codebase-requirements", prepared, settings=settings)
        return final, stub, prepared

    return _run


def _ungrounded_fragment() -> BRDFragment:
    """A worker returning one claim the gate cannot ground, alongside one it can."""
    return BRDFragment(
        purpose_hint="Recording and retrieving supplier bills.",
        area_code=AREA,
        area_name="Supplier billing",
        functional_requirements=[
            _ungrounded_business_requirement(),
            _grounded_capability(),
        ],
    )


def _top(*requirements: BRDItem, bind: bool = True) -> TopTier:
    """What the elevation call returns: the top tier and one binding per functional area."""
    outcomes = list(requirements) or [_grounded_business_requirement()]
    return TopTier(
        product_purpose=BRDItem(
            title="Purpose",
            statement="The product lets an organisation capture supplier bills and find them "
                      "again later.",
            confidence=0.9,
        ),
        business_requirements=outcomes,
        bindings=(
            [
                OutcomeBinding(
                    business_requirement=outcomes[0].title,
                    realised_by=["Bill submission", "Forecast supplier spend"],
                )
            ]
            if bind
            else []
        ),
    )


def _brd(*items) -> ProductBRD:
    """A whole requirements set, as the repair step returns one."""
    outcomes = [item for item in items if not item.parent]
    capabilities = [item for item in items if item.parent]
    return ProductBRD(
        product_purpose=BRDItem(
            title="Purpose",
            statement="The product lets an organisation capture supplier bills and find them "
                      "again later.",
            confidence=0.9,
        ),
        business_requirements=outcomes,
        functional_requirements=capabilities,
    ).assign_ids()


# --------------------------------------------------------------------------------------------


def test_happy_path_produces_every_deliverable(e2e) -> None:
    final, stub, prepared = e2e()

    assert final["status"] in {"passed", "passed_with_warnings"}
    assert final["halt_reason"] is None

    completed = final["completed_steps"]
    # `classify_boundaries` is a fan-out step now, like `synthesize`, so it never appends its own
    # id to `completed_steps` — each parallel worker runs the same node function, and none of them
    # is "the" completion. `collect_boundary_assignments` is the reduce step that follows it.
    # `analyse_module` is a fan-out step too, so like `classify_boundaries` and `synthesize` it
    # never appends its own id — `collect_module_analyses` is its reduce step.
    assert completed[:11] == [
        "ingest", "graphify_extract", "adapt_graph", "extract_declarations",
        "propose_boundaries", "collect_boundary_assignments", "discover_entry_points",
        "gate_graph", "partition_modules", "plan_clusters", "collect_module_analyses",
    ]
    for step in (
        "digest_areas", "elevate", "assemble", "gate_requirements",
        "report_family_coverage", "render",
    ):
        assert step in completed, step
    # A passing gate must go straight to render, never through the repair step below it.
    assert "repair" not in completed
    assert "quarantine" not in completed

    artifacts = prepared.run_dir / "artifacts"
    for name in (
        "requirements.md", "traceability_matrix.csv", "gap_analysis.md",
        "technical_specification.md", "api_documentation.md", "workflows.mmd",
        # The companion analysis document: what the run could not stand behind, kept out of the
        # requirements and not thrown away. Written by a step that existed and was never called.
        "requirement_analysis.md",
    ):
        assert (artifacts / name).read_text(encoding="utf-8").strip(), name

    requirements = (artifacts / "requirements.md").read_text(encoding="utf-8")
    assert GOOD_STATEMENT in requirements
    # Evidence is published *in* the requirements document, not only in the companion matrix.
    # For a reverse-engineered document the citation is the warrant for the claim, and a reader
    # checking whether a requirement was invented must not have to open a second file.
    assert "app/main.py" in requirements
    assert str(HANDLER_LINE) in requirements
    # The requirement *statement* itself still has to read as business language: the citation
    # belongs in the Evidence cell, never in the sentence a stakeholder signs off.
    statement_line = next(
        line for line in requirements.splitlines() if GOOD_STATEMENT in line
    )
    statement_cell = statement_line.split("|")[2]
    assert "app/main.py" not in statement_cell
    assert "create_invoice" not in statement_cell

    matrix = (artifacts / "traceability_matrix.csv").read_text(encoding="utf-8")
    assert "app/main.py" in matrix
    assert str(HANDLER_LINE) in matrix

    api_docs = (artifacts / "api_documentation.md").read_text(encoding="utf-8")
    # The route path is not recoverable: the extractor records that a handler references a route
    # registrar, never the literal string handed to it. The document must identify the endpoint by
    # the handler that serves it and say why, rather than printing a guessed path.
    assert "create_invoice" in api_docs
    assert "not recovered" in api_docs
    assert "/invoices" not in api_docs


def test_fan_out_runs_one_worker_per_interaction_boundary(e2e) -> None:
    final, stub, prepared = e2e()
    fragments = final["fragments"]
    assert len(fragments) >= 2
    # Boundaries are named after the handler they expose, not the route: Graphify does not
    # surface a decorator's arguments, so the literal path the old extractor read is not in the
    # payload for anyone to recover.
    assert {fragment["boundary"] for fragment in fragments} >= {
        HANDLER_BOUNDARY, "GET /invoices/{reference}"
    }
    assert stub.calls.count("BRDFragment") == len(fragments)


def test_boundary_classification_runs_one_worker_per_candidate_batch(e2e) -> None:
    """The classification fan-out mirrors `synthesize`'s: one call per batch, one reply each.

    The fixture app has far fewer candidates than `MAX_CANDIDATES_PER_BATCH`, so this run makes
    exactly one batch and one `BoundaryAssignments` call — the property under test is that the
    count of calls, batches, and reduced fragments all agree with each other, which is what would
    break first if the fan-out and the reduce step ever fell out of step.
    """
    from agentic.store import ArtifactStore

    final, stub, prepared = e2e()
    store = ArtifactStore(prepared.run_dir)
    batches = store.load_json(final["artifacts"]["boundary_candidate_batches"])
    assert batches, "the sample app must yield at least one interaction boundary candidate"

    assert stub.calls.count("BoundaryAssignments") == len(batches)
    assert len(final["boundary_fragments"]) == len(batches)

    assignments = store.load_json(final["artifacts"]["boundary_assignments"])
    fragment_total = sum(
        len(fragment.get("assignments") or []) for fragment in final["boundary_fragments"]
    )
    assert len(assignments["assignments"]) == fragment_total


def test_gate_one_failure_halts_before_any_model_call(e2e, tmp_path, monkeypatch) -> None:
    """A codebase with nothing to say anything about must never reach the model.

    The halt sits at ingestion again, and on a much narrower condition than it once did. It used
    to refuse any repository without Python in it; it now refuses only one with no source in any
    of the 36 languages the extractor parses. This fixture holds one heading and nothing else, so
    the answer is still no and the run must still fail before spending a model call.
    """
    stub = StubModel()
    monkeypatch.setattr("agentic.llm.steps.build_chat_model", lambda settings: stub)

    empty = tmp_path / "docs_only"
    (empty / "docs").mkdir(parents=True)
    (empty / "docs" / "README.md").write_text("# Just documentation\n", encoding="utf-8")

    settings = Settings(runs_dir=tmp_path / "runs")
    prepared = prepare_run(source=empty, settings=settings)
    final = run("codebase-requirements", prepared, settings=settings)

    assert final["status"] == "failed"
    assert "no language" in final["halt_reason"].casefold() or "any language" in final["halt_reason"].casefold()
    # The reason must actually say what failed. It once read "A validation gate failed:
    # gate_graph: " and stopped there, because the halt node summarised from a key that has
    # never existed on GateReport.
    assert final["halt_reason"].rstrip().endswith(":") is False, final["halt_reason"]
    assert "Markdown" in final["halt_reason"], "the reason must name what it did find"
    assert stub.calls == []
    assert (prepared.run_dir / "artifacts" / "run_failed.md").exists()


def test_failed_gate_two_repairs_once_then_renders(e2e) -> None:
    final, stub, prepared = e2e(
        # The repair drops the one claim the gate rejected, so the next gate pass is clean.
        fragment=_ungrounded_fragment(),
        repair_mode="delete",
        # Resolution now catches this claim before the gate ever sees it, which is the better
        # outcome and is asserted separately below. The property under test here is the bounded
        # repair loop, so the earlier stage is switched off to let the claim reach the gate.
        candidate_resolution=False,
    )

    assert final["completed_steps"].count("repair") == 1
    assert "apply_repairs" in final["completed_steps"]
    assert final["repair_attempts"] == 1
    assert "quarantine" not in final["completed_steps"]
    assert "render" in final["completed_steps"]
    assert (prepared.run_dir / "artifacts" / "requirements.md").exists()


def test_exhausted_repairs_quarantine_rather_than_publish_ungrounded_claims(e2e, manifest) -> None:
    """The model never corrects itself, so the ungrounded claim must be dropped, not softened."""
    final, stub, prepared = e2e(
        # Every repair fixes nothing, so the budget is spent and quarantine must drop the
        # ungrounded claim rather than publish it.
        fragment=_ungrounded_fragment(),
        repair_mode="noop",
        candidate_resolution=False,
    )

    # The property under test is that the loop is bounded at all, so the bound is read from the
    # manifest rather than restated here — tuning it is a pack decision, not a code change.
    bound = manifest.step("gate_requirements").max_repairs
    assert bound and final["repair_attempts"] == bound
    assert final["completed_steps"].count("repair") == bound
    assert "quarantine" in final["completed_steps"]
    assert "render" in final["completed_steps"]

    requirements = (prepared.run_dir / "artifacts" / "requirements.md").read_text(
        encoding="utf-8"
    )
    # The ungrounded claim is gone, and the grounded ones around it survived: quarantine drops
    # what the gate rejected, never the whole set.
    assert "predicts next quarter" not in requirements
    assert SCOPE_STATEMENT in requirements
    assert GOOD_STATEMENT in requirements

    gaps = (prepared.run_dir / "artifacts" / "gap_analysis.md").read_text(encoding="utf-8")
    assert "Coverage by construct" in gaps
    # And the gap analysis must name what was dropped and why, not quietly lose it.
    assert "Claims that did not survive validation" in gaps
    assert "app/forecasting.py" in gaps


def test_every_step_records_the_skill_and_content_hash_that_governed_it(e2e) -> None:
    """Provenance: a deliverable can name the exact instructions that produced it."""
    final, _, _ = e2e([_brd(_grounded_business_requirement(), _grounded_capability())])
    records = {record["step"]: record for record in final["step_records"]}
    assert records["adapt_graph"]["skill"] == "graphify-knowledge-graph-extraction"
    assert records["gate_graph"]["skill"] == "requirement-and-graph-validation-gates"
    assert records["render"]["skill"] == "traceable-requirements-deliverables"
    for record in final["step_records"]:
        assert len(record["skill_sha256"]) == 64
        assert record["duration_ms"] >= 0


def test_a_repair_call_that_fails_outright_still_produces_every_deliverable(
    e2e, manifest
) -> None:
    """A recovery step must not be able to destroy the run it exists to rescue.

    The repair step is the one place a provider-side failure is most likely to surface, because it
    is reached only after something else already went wrong. It used to set the run to failed, so
    a single rejected call threw away every grounded requirement in the set and wrote no document
    at all. Quarantine already knows how to drop what the gate rejected and publish an honest
    thinner document; a failed repair must reach it.
    """
    final, stub, prepared = e2e(
        fragment=_ungrounded_fragment(),
        raise_on=frozenset({"RepairedItems"}),
        candidate_resolution=False,
    )

    assert stub.product_brd_calls > 0, "the repair step never ran, so nothing was proven"
    assert final["halt_reason"] is None
    assert "quarantine" in final["completed_steps"]
    assert "render" in final["completed_steps"]

    artifacts = prepared.run_dir / "artifacts"
    for name in (
        "requirements.md", "traceability_matrix.csv", "gap_analysis.md",
        "technical_specification.md", "api_documentation.md", "workflows.mmd",
        # The companion analysis document: what the run could not stand behind, kept out of the
        # requirements and not thrown away. Written by a step that existed and was never called.
        "requirement_analysis.md",
    ):
        assert (artifacts / name).read_text(encoding="utf-8").strip(), name

    # The grounded capability survives; only what the gate rejected is dropped.
    requirements = (artifacts / "requirements.md").read_text(encoding="utf-8")
    assert SCOPE_STATEMENT in requirements
    assert "predicts next quarter" not in requirements


def test_a_failed_repair_is_bounded_and_recorded(e2e, manifest) -> None:
    """A failing repair must count its attempt, or the bounded loop never terminates."""
    final, stub, prepared = e2e(
        fragment=_ungrounded_fragment(),
        raise_on=frozenset({"RepairedItems"}),
        candidate_resolution=False,
    )
    bound = manifest.step("gate_requirements").max_repairs
    assert final["repair_attempts"] == bound
    assert stub.product_brd_calls == bound, "a failed attempt must still be counted"

    failed = [
        record for record in final["step_records"]
        if record["step"] == "repair" and record["status"] == "failed"
    ]
    assert failed, "the failure must be on the record, not swallowed"
    assert any("400" in message for record in failed for message in record["messages"])

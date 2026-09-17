"""Preparation must fix bookkeeping without ever strengthening a claim.

The bug this covers was quiet and expensive: item ids were never stamped before Gate 2, so every
rejection named an empty subject. The repair step was told what was wrong but not which claim, and
quarantine — which deletes by subject id — reported nothing to delete while ungrounded claims went
through to the document.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agentic.capabilities.claims import prepare_claims
from agentic.capabilities.jargon import describe, find_jargon
from agentic.capabilities.registry import CapabilityContext
from agentic.schemas.brd import BRDItem, ProductBRD
from agentic.schemas.core import SourceLocation


def _item(statement: str, evidence: list[SourceLocation], **kwargs) -> BRDItem:
    return BRDItem(
        title=kwargs.pop("title", "A claim"),
        statement=statement,
        confidence=kwargs.pop("confidence", 0.9),
        evidence=evidence,
        **kwargs,
    )


@pytest.fixture
def prepared(runner, state, manifest, settings):
    """Run extraction, then hand a hand-built BRD through the preparation step."""
    runner("ingest")
    runner("graphify_extract")
    runner("adapt_graph")

    from agentic.store import ArtifactStore

    store = ArtifactStore(Path(state["run_dir"]))

    def _prepare(brd: ProductBRD):
        state["artifacts"]["brd"] = store.save("brd", brd)
        step = manifest.step("prepare_claims")
        ctx = CapabilityContext(
            state=state,
            step=step,
            settings=settings,
            store=store,
            skill=manifest.skill_registry().load(step.skill),
            payload={"manifest": manifest},
        )
        result = prepare_claims(ctx)
        return result.artifacts["brd"], result

    return _prepare


def test_every_claim_gets_an_identifier_a_rejection_can_name(prepared) -> None:
    brd, _ = prepared(
        ProductBRD(
            product_purpose=_item("Purpose.", []),
            functional_requirements=[_item("One.", []), _item("Two.", [])],
            business_rules=[_item("A rule.", [])],
        )
    )
    assert brd.product_purpose.id == "PURPOSE"
    assert [item.id for item in brd.functional_requirements] == ["FR-001", "FR-002"]
    assert brd.business_rules[0].id == "RULE-001"
    assert all(item.id for item in brd.all_items())


def test_a_citation_of_a_definition_keeps_its_qualified_name(prepared) -> None:
    """This is what a traceability tool resolves against, so it must survive preparation.

    A citation whose symbol verifies is a definition cited correctly, not a mislabelling — the
    published requirement carries `app/main.py::create_invoice()` exactly as the reference format does.
    """
    location = SourceLocation(
        file="app/main.py", line_start=30, line_end=37, symbol="app/main.py::create_invoice()"
    )
    brd, result = prepared(ProductBRD(functional_requirements=[_item("A capability.", [location])]))

    cited = brd.functional_requirements[0].evidence[0]
    assert cited.symbol == "app/main.py::create_invoice()"
    assert (cited.file, cited.line_start, cited.line_end) == ("app/main.py", 30, 37)
    assert result.metrics["definition_cited"] == 1
    assert result.metrics["scope_mislabelled"] == 0


def test_an_enclosing_definition_far_above_the_cited_line_is_relabelled_as_scope(
    tmp_path,
) -> None:
    """Deep inside a long function the name is context, not something present at the line."""
    from agentic.capabilities.claims import Corrections, _ground_location

    source = tmp_path / "long.py"
    source.write_text("def owner():\n" + "    value = 1\n" * 300, encoding="utf-8")

    location = SourceLocation(file="long.py", line_start=250, symbol="long.owner")
    counts = Corrections()
    _ground_location(location, {}, {"long.owner"}, counts, tmp_path)

    assert location.symbol is None
    assert location.scope == "long.owner"
    assert (location.file, location.line_start) == ("long.py", 250)
    assert counts.scope_mislabelled == 1


def test_free_text_in_the_symbol_field_is_replaced_by_the_node_at_that_line(
    prepared,
) -> None:
    """The description asserts nothing, but the lines it came with still point somewhere real."""
    location = SourceLocation(
        file="app/main.py", line_start=31, symbol="button: Add to Index (UI_ACTION)"
    )
    brd, result = prepared(ProductBRD(functional_requirements=[_item("A capability.", [location])]))

    cited = brd.functional_requirements[0].evidence[0]
    assert result.metrics["description_in_symbol"] == 1
    # Not the free text, and not nothing: the qualified name of the node those lines land on.
    assert cited.symbol == "app/main.py::create_invoice()"
    assert result.metrics["cited_node_named"] == 1


def test_a_fabricated_symbol_is_never_replaced_by_a_real_one(prepared) -> None:
    """Naming the cited node must not launder an invented symbol into a clean citation."""
    location = SourceLocation(file="app/main.py", line_start=31, symbol="invented_helper")
    brd, result = prepared(ProductBRD(functional_requirements=[_item("A capability.", [location])]))

    assert brd.functional_requirements[0].evidence[0].symbol == "invented_helper"
    assert result.metrics["unknown_symbol_kept"] == 1
    assert result.metrics["cited_node_named"] == 0


def test_a_symbol_the_extractor_never_found_is_left_for_the_gate_to_reject(
    prepared,
) -> None:
    """Preparation must not launder a fabrication into a clean-looking citation."""
    location = SourceLocation(file="app/main.py", line_start=31, symbol="invented_helper")
    brd, result = prepared(ProductBRD(functional_requirements=[_item("A capability.", [location])]))
    assert brd.functional_requirements[0].evidence[0].symbol == "invented_helper"
    assert result.metrics["unknown_symbol_kept"] == 1


def test_a_citation_gains_the_definition_its_lines_really_sit_in(prepared) -> None:
    location = SourceLocation(file="app/main.py", line_start=31)
    brd, _ = prepared(ProductBRD(functional_requirements=[_item("A capability.", [location])]))
    assert brd.functional_requirements[0].evidence[0].scope == "app/main.py::create_invoice()"


def test_a_statement_published_twice_is_published_once(prepared) -> None:
    """One insight repeated across sections is padding, and inflates every coverage count."""
    statement = "Users can retrieve a stored document."
    brd, result = prepared(
        ProductBRD(
            functional_requirements=[_item(statement, [])],
            success_criteria=[_item(statement, [])],
        )
    )
    assert len(brd.functional_requirements) == 1
    assert brd.success_criteria == []
    assert result.metrics["restatements_removed"] == 1


def test_distinct_statements_are_all_kept(prepared) -> None:
    brd, result = prepared(
        ProductBRD(
            functional_requirements=[_item("First thing.", []), _item("Second thing.", [])]
        )
    )
    assert len(brd.functional_requirements) == 2
    assert result.metrics["restatements_removed"] == 0


# --------------------------------------------------------------------------------------------
# The vocabulary linter
# --------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "statement, expected",
    [
        ("Documents are stored in an index for later use.", "index"),
        ("The system supports indexing of uploaded files.", "index"),
        ("Results are cached between sessions.", "cache"),
        ("Users benefit from caching of prior answers.", "cache"),
        ("Queries are answered from the documents.", "query"),
        ("Answers are produced by querying the collection.", "query"),
    ],
)
def test_an_inflected_technical_term_is_still_a_technical_term(
    statement: str, expected: str
) -> None:
    """Matching the bare form only let the mechanism back in as a verb.

    These are domain terms, so they are findings under `strict` and deliberately not otherwise —
    a document-search product indexes documents and answers queries in its own voice.
    """
    assert expected in describe(find_jargon(statement, strict=True))
    assert find_jargon(statement) == []


@pytest.mark.parametrize(
    "statement",
    [
        "Claims are processed within one working day.",
        "The approval process requires a second reviewer.",
        "Each submission is processed in the order received.",
    ],
)
def test_ordinary_business_english_is_not_flagged(statement: str) -> None:
    """"process" is business vocabulary; rejecting it cost sound requirements and gained nothing."""
    assert find_jargon(statement) == []


def test_storage_vocabulary_is_a_finding_only_for_a_caller_that_asked_for_strictness() -> None:
    for statement in (
        "The existing directory is wiped before rebuilding.",
        "Each excerpt is stored as an embedding.",
        "Passages are split into a chunk of fixed size.",
    ):
        assert find_jargon(statement, strict=True), f"expected a finding for {statement!r}"
        assert find_jargon(statement) == []


@pytest.mark.parametrize(
    "statement",
    [
        "The Streamlit page calls index_pdf to store the file.",
        "Answers are retrieved from ChromaDB and returned by the REST endpoint.",
        "The handler in app.py parses the payload before responding.",
        "Groq generates the summary from the retrieved passages.",
    ],
)
def test_naming_the_implementation_is_always_a_finding(statement: str) -> None:
    """No mode permits these: a reader should never need to know what it was built with."""
    assert find_jargon(statement), f"expected a finding for {statement!r}"
    assert find_jargon(statement, strict=True)


def test_the_reference_documents_own_statements_pass_unmodified() -> None:
    """The bar this workflow is measured against, taken verbatim from its published output."""
    for statement in (
        "The system must allow users to upload biomedical PDF documents, which are then indexed "
        "for future queries. This process ensures that the content of the documents is accessible "
        "for information retrieval.",
        "The system must provide users with the ability to ask questions and receive answers "
        "based on the indexed content of the uploaded PDFs.",
        "When a user uploads a document that has already been indexed, the system must remove the "
        "existing index before creating a new one.",
    ):
        assert find_jargon(statement) == [], describe(find_jargon(statement))


# --------------------------------------------------------------------------------------------
# Recovering a reply the provider discarded for length
# --------------------------------------------------------------------------------------------


class _LengthFinishReasonError(Exception):
    """Stands in for the provider's own class, which is matched by name."""


def test_a_reply_discarded_for_length_is_retried_with_a_smaller_ask() -> None:
    """A truncated structured reply cannot be parsed, so the whole cluster is lost otherwise."""
    from agentic.llm import steps

    assert steps._is_length_failure(_LengthFinishReasonError("cut off"))
    assert steps._is_length_failure(
        Exception("Could not parse response content as the length limit was reached")
    )
    assert not steps._is_length_failure(Exception("connection reset by peer"))

    seen: list[int] = []

    class _Model:
        def invoke(self, messages):
            seen.append(len(messages))
            if len(seen) == 1:
                raise _LengthFinishReasonError("cut off")
            return ProductBRD(functional_requirements=[_item("A capability.", [])])

    class _Chat:
        def with_structured_output(self, schema):
            return _Model()

    import agentic.llm.steps as module

    original_build = module.build_chat_model
    original_messages = module.build_messages
    module.build_chat_model = lambda settings: _Chat()
    module.build_messages = lambda ctx: [("system", "s"), ("human", "h")]
    try:
        class _Step:
            id = "reduce"
            output_schema = "ProductBRD"
            fan_out = None
            produces = ["brd"]

        class _Ctx:
            step = _Step()
            settings = None
            payload: dict = {}

        result = module.run_llm_step(_Ctx())
    finally:
        module.build_chat_model = original_build
        module.build_messages = original_messages

    assert seen == [2, 3], "the retry must carry an extra instruction, not repeat the same ask"
    assert result.artifacts["brd"].functional_requirements[0].statement == "A capability."

"""A script-style application's interaction surface, which is not a definition at all.

The failure this covers was total rather than partial. Graphify models definitions and nothing
else, so a Streamlit product — whose every control is a module-level widget call — extracted as a
handful of helper functions with no boundary, no cluster, and an empty requirements document. It
looked like a library with nothing exposed.

The discrimination is a shape, not a list of frameworks: a call whose **result is consumed** offers
someone a way in, and a call whose result is discarded renders something.

    uploaded_file = st.file_uploader("Choose a medical PDF")   an input
    if st.button("Add to Index"):                              a control
    st.success("Done!")                                        display
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agentic.capabilities.graphify import (
    MAX_SURFACE_PER_FILE,
    _module_surface,
    _tight_definition_spans,
)

STREAMLIT = '''\
import streamlit as st
from work import index_pdf

RETENTION_DAYS = int(os.getenv("RETENTION_DAYS", "90"))

st.set_page_config(page_title="Assistant")


def helper(value):
    """A definition, so nothing inside it is module level."""
    if st.button("inside a definition"):
        return value
    return None


st.subheader("Upload")
uploaded = st.file_uploader("Choose a medical PDF", type=["pdf"])

with st.sidebar:
    if st.button("Add to Index"):
        if index_pdf(uploaded):
            st.success("Done!")

for turn in st.session_state.history:
    st.write(turn.get("answer"))

query = st.chat_input("Ask a question")
'''


@pytest.fixture
def script(tmp_path: Path) -> Path:
    (tmp_path / "app.py").write_text(STREAMLIT, encoding="utf-8")
    return tmp_path


def _surface(root: Path) -> dict[str, int]:
    """Call -> line, for the one definition this fixture contains."""
    spans = _tight_definition_spans(root, "app.py", [9])
    found = _module_surface(root, "app.py", spans, {"helper"})
    return {call: line for line, call, _ in found}


def test_the_controls_are_found(script: Path) -> None:
    calls = _surface(script)
    assert set(calls) == {"st.file_uploader", "st.button", "st.chat_input"}


def test_a_control_carries_the_products_own_word_for_it(script: Path) -> None:
    spans = _tight_definition_spans(script, "app.py", [9])
    literals = {call: lits for _, call, lits in _module_surface(script, "app.py", spans, {"helper"})}
    assert "Choose a medical PDF" in literals["st.file_uploader"]


def test_a_rendering_call_is_not_a_control(script: Path) -> None:
    """`st.success("Done!")` and `st.subheader("Upload")` discard their result."""
    calls = _surface(script)
    assert "st.success" not in calls
    assert "st.subheader" not in calls
    assert "st.write" not in calls


def test_a_call_with_no_literal_is_not_a_control(script: Path) -> None:
    """`st.set_page_config(page_title=...)` names no control a reader would recognise."""
    assert "st.set_page_config" not in _surface(script)


def test_a_constant_declaration_is_not_a_control(script: Path) -> None:
    """`RETENTION_DAYS = int(os.getenv(...))` reads the environment; it offers nobody a way in.

    Left in, every configured setting in a repository became an interaction boundary and crowded
    the real ones out of the cluster budget.
    """
    assert "int" not in _surface(script)


def test_a_call_on_a_locally_bound_name_is_internal_work(script: Path) -> None:
    """`turn.get("answer")` where `turn` is this file's own loop variable."""
    assert "turn.get" not in _surface(script)


def test_a_widget_inside_a_definition_is_not_module_level(script: Path) -> None:
    """The `st.button("inside a definition")` in `helper` must not be collected."""
    spans = _tight_definition_spans(script, "app.py", [9])
    lines = {line for line, _, _ in _module_surface(script, "app.py", spans, {"helper"})}
    assert not any(start <= line <= end for line in lines for start, end in spans)


def test_a_widget_nested_in_a_block_is_still_module_level(script: Path) -> None:
    """`with st.sidebar:` indents the control without putting it inside a definition."""
    assert "st.button" in _surface(script)


# --------------------------------------------------------------------------------------------
# Definition extents
# --------------------------------------------------------------------------------------------


def test_a_definition_ends_where_the_block_does_not_at_the_file_end(script: Path) -> None:
    """The regression that hid the whole surface.

    Entity spans are a deliberate over-approximation: the last definition in a file runs to the
    file's end, which is right for a citation. Reused here it made every line below the final
    `def` look as though it sat inside one, and a Streamlit script keeps its entire surface there.
    """
    (start, end), = _tight_definition_spans(script, "app.py", [9])
    assert start == 9
    total = len(STREAMLIT.splitlines())
    assert end < total - 5, f"the definition swallowed the rest of the file ({end} of {total})"


def test_an_unreadable_file_yields_nothing_rather_than_raising(tmp_path: Path) -> None:
    assert _tight_definition_spans(tmp_path, "absent.py", [1]) == []
    assert _module_surface(tmp_path, "absent.py", [], set()) == []


def test_the_surface_is_capped_per_file(tmp_path: Path) -> None:
    body = "\n".join(
        f'value{index} = ui.control("label {index}")' for index in range(MAX_SURFACE_PER_FILE + 20)
    )
    (tmp_path / "many.py").write_text(body, encoding="utf-8")
    assert len(_module_surface(tmp_path, "many.py", [], set())) == MAX_SURFACE_PER_FILE


def test_a_call_to_a_locally_defined_function_is_not_a_control(tmp_path: Path) -> None:
    """Ordinary internal work: the graph already holds the definition and the call."""
    (tmp_path / "m.py").write_text('result = compute("total")\n', encoding="utf-8")
    assert _module_surface(tmp_path, "m.py", [], {"compute"}) == []


# --------------------------------------------------------------------------------------------
# The reported failure, end to end
# --------------------------------------------------------------------------------------------


def test_the_shipped_streamlit_example_produces_evidence_packets(tmp_path: Path) -> None:
    """`examples/biomedical_rag_assistant.zip` from the app produced no requirements at all.

    A real Streamlit product: five controls, all of them module-level widget calls, and two helper
    functions. Extraction found the helpers, no boundary existed, `plan_clusters` returned nothing,
    and every step after it had nothing to work on. This runs the real extraction phase over the
    real archive and asserts the pipeline reaches the model with something to say.
    """
    from agentic.capabilities.graph_ops import BOUNDARY_KINDS
    from agentic.capabilities.registry import CapabilityContext
    from agentic.config import Settings
    from agentic.runtime import prepare_run
    from agentic.schemas.core import KnowledgeGraph
    from agentic.skills.pack import load_pack
    from agentic.state import initial_state
    from agentic.store import ArtifactStore

    from tests.conftest import PACK_DIR, REPO_ROOT, run_step_or_stub

    archive = REPO_ROOT / "examples" / "biomedical_rag_assistant.zip"
    if not archive.exists():
        pytest.skip("the shipped example archive is not present")

    manifest = load_pack(PACK_DIR)
    settings = Settings(runs_dir=tmp_path / "runs")
    with archive.open("rb") as handle:
        prepared = prepare_run(
            archive=handle, archive_name=archive.name, settings=settings
        )
    state = initial_state(
        run_id="streamlit-example",
        pack="codebase-requirements",
        run_dir=str(prepared.run_dir),
        source_root=str(prepared.source_root),
        repository_name="biomedical_rag_assistant",
    )
    store = ArtifactStore(prepared.run_dir)

    clusters = None
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
            assert outcome.gate.valid, outcome.gate.error_messages()
        if spec.id == "plan_clusters":
            clusters = outcome.artifacts["clusters"]
            break

    graph = store.load_model(state["artifacts"]["graph"], KnowledgeGraph)
    boundaries = [entity for entity in graph.entities if entity.kind in BOUNDARY_KINDS]
    assert boundaries, "no interaction boundary was found in a working application"

    assert clusters, "no evidence packet was produced, so no requirement could be written"
    assert any(
        cluster["member_count"] > 1 for cluster in clusters
    ), "every packet was a lone declaration node, so a requirement would rest on nothing"

    # The controls are named as the product names them, not after the functions behind them.
    named = {cluster["boundary"] for cluster in clusters}
    assert "Add to Index" in named, named

"""The Graphify adapter must satisfy the canonical schema, whatever language it was fed.

The fixtures under `tests/fixtures/graphify/` are real `graphify extract --code-only` output
captured from graphifyy 0.9.52, not hand-written. That matters: the adapter exists to absorb a
schema this project does not control, so a test written against an idealised payload would prove
nothing about the payload that actually arrives.
"""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from agentic.capabilities.graphify import (
    RELATION_MAP,
    AdapterError,
    _attach_docstring,
    _clean_rationale_text,
    _derive_line_ends,
    _index_rationale_sources,
    _parse_location,
    _read_rationale,
    adapt_graphify_graph,
    classify_kind,
    safe_file_outline,
)
from agentic.capabilities.registry import CapabilityContext
from agentic.config import Settings
from agentic.schemas.core import KnowledgeGraph, Provenance
from agentic.store import ArtifactStore

FIXTURES = Path(__file__).parent / "fixtures"
GRAPHIFY_FIXTURES = FIXTURES / "graphify"


def _payload(name: str) -> dict[str, Any]:
    return json.loads((GRAPHIFY_FIXTURES / f"{name}.graph.json").read_text(encoding="utf-8"))


@dataclass
class _Step:
    id: str = "adapt_graph"
    capability: str = "adapt_graphify_graph"


def _adapt(tmp_path: Path, fixture: str, source_app: str = "sample_app") -> KnowledgeGraph:
    """Run the real capability against a captured payload and a real source tree."""
    run_dir = tmp_path / "run"
    (run_dir / "artifacts").mkdir(parents=True)
    shutil.copytree(FIXTURES / source_app, run_dir / "source")

    store = ArtifactStore(run_dir)
    graph_path = run_dir / "graphify-graph.json"
    graph_path.write_text(json.dumps(_payload(fixture)), encoding="utf-8")

    state: dict[str, Any] = {
        "run_dir": str(run_dir),
        "source_root": str(run_dir / "source"),
        "artifacts": {},
    }
    from agentic.capabilities.registry import CapabilityContext as _Ctx  # noqa: F401

    inventory_ctx = CapabilityContext(
        state=state,  # type: ignore[arg-type]
        step=_Step(),  # type: ignore[arg-type]
        settings=Settings(runs_dir=tmp_path / "runs"),
        store=store,
        skill=None,  # type: ignore[arg-type]
    )
    from agentic.capabilities.ingest import build_inventory

    inventory = build_inventory(run_dir / "source", source_app)
    state["artifacts"]["inventory"] = store.save("inventory", inventory)
    state["artifacts"]["graphify_raw"] = store.save(
        "graphify_raw", {"graph_path": str(graph_path), "command": ["extract", "--code-only"]}
    )

    result = adapt_graphify_graph(inventory_ctx)
    return result.artifacts["graph"]


# --------------------------------------------------------------------------------------------
# Kind classification — the part with no Graphify field behind it
# --------------------------------------------------------------------------------------------


def test_a_node_without_a_source_file_is_external() -> None:
    """Graphify emits referenced-but-undefined names with an empty file. They cite nowhere."""
    assert classify_kind({"label": "BaseModel", "source_file": "", "file_type": "code"}) == (
        "EXTERNAL_SYMBOL"
    )


def test_the_file_node_is_recognised_by_its_basename_label() -> None:
    assert classify_kind(
        {"label": "main.py", "source_file": "app/main.py", "file_type": "code"}
    ) == "FILE"


def test_a_callable_class_is_a_class() -> None:
    assert classify_kind(
        {
            "label": "UserService",
            "source_file": "server.js",
            "file_type": "code",
            "_callable": True,
            "_callable_class": True,
        }
    ) == "CLASS"


def test_a_dotted_callable_is_a_method_and_a_bare_one_a_function() -> None:
    common = {"source_file": "server.js", "file_type": "code", "_callable": True}
    assert classify_kind({**common, "label": ".fetch()"}) == "METHOD"
    assert classify_kind({**common, "label": "buildService()"}) == "FUNCTION"


def test_a_go_function_is_a_function_despite_callable_being_false() -> None:
    """The regression the spike caught.

    Graphify's Go grammar does not set `_callable`. Classifying on that flag alone puts every Go
    function under SYMBOL, which excludes it from `WORKFLOW_EDGE_KINDS` traversal and empties the
    document for an entire language.
    """
    node = {
        "label": "listItems()",
        "source_file": "handler.go",
        "file_type": "code",
        "_origin": "ast",
    }
    assert node.get("_callable") is None
    assert classify_kind(node) == "FUNCTION"


def test_rationale_nodes_keep_their_own_kind() -> None:
    assert classify_kind({"label": "why", "file_type": "rationale"}) == "RATIONALE"


# --------------------------------------------------------------------------------------------
# Locations — Graphify emits a start line and never a range
# --------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value,expected",
    [("L26", 26), ("", None), (None, None), ("L26-L40", 26)],
)
def test_location_parsing_survives_every_observed_shape(value, expected) -> None:
    parsed = _parse_location({"source_file": "a/b.py", "source_location": value})
    assert parsed is not None
    assert parsed[1] == expected


def test_a_node_with_no_file_has_no_location() -> None:
    assert _parse_location({"source_file": "", "source_location": "L3"}) is None


def test_line_ends_stop_before_the_next_definition() -> None:
    nodes = [
        {"id": "f", "source_file": "a.py", "source_location": "L1"},
        {"id": "one", "source_file": "a.py", "source_location": "L10"},
        {"id": "two", "source_file": "a.py", "source_location": "L20"},
    ]
    ends = _derive_line_ends(nodes, [])
    assert ends["one"] == 19


def test_a_container_is_extended_to_cover_its_children() -> None:
    """Sibling bounds alone would cut a class off at its own first method."""
    nodes = [
        {"id": "cls", "source_file": "a.py", "source_location": "L5"},
        {"id": "m1", "source_file": "a.py", "source_location": "L11"},
        {"id": "m2", "source_file": "a.py", "source_location": "L20"},
    ]
    edges = [
        {"source": "cls", "target": "m1", "relation": "contains"},
        {"source": "cls", "target": "m2", "relation": "method"},
    ]
    ends = _derive_line_ends(nodes, edges)
    assert ends["cls"] >= ends["m2"] == 20
    assert ends["m1"] == 19


# --------------------------------------------------------------------------------------------
# End to end over captured payloads
# --------------------------------------------------------------------------------------------


def test_the_python_payload_produces_a_usable_graph(tmp_path: Path) -> None:
    graph = _adapt(tmp_path, "sample_app")
    kinds = {entity.kind for entity in graph.entities}
    assert "FILE" in kinds
    assert {"FUNCTION", "METHOD", "CLASS"} & kinds
    assert graph.relationships


def test_every_located_entity_carries_a_line_range(tmp_path: Path) -> None:
    """A single-line citation makes every evidence packet thinner than the prompt asks for."""
    graph = _adapt(tmp_path, "sample_app")
    located = [e for e in graph.entities if e.location and e.location.line_start]
    assert located
    for entity in located:
        assert entity.location.line_end is not None
        assert entity.location.line_end >= entity.location.line_start


def test_external_symbols_have_no_location_rather_than_a_fabricated_one(tmp_path: Path) -> None:
    graph = _adapt(tmp_path, "sample_app")
    external = [e for e in graph.entities if e.kind == "EXTERNAL_SYMBOL"]
    assert external
    assert all(entity.location is None for entity in external)


def test_a_file_entity_asserts_no_symbol(tmp_path: Path) -> None:
    """A file's label is its basename, which is not on line 1. Asserting it fails verification."""
    graph = _adapt(tmp_path, "sample_app")
    files = [e for e in graph.entities if e.kind == "FILE"]
    assert files
    assert all(entity.location is None or entity.location.symbol is None for entity in files)


def test_call_edges_survive_the_relation_mapping(tmp_path: Path) -> None:
    """Without a CALLS edge, `GraphIndex.reachable` returns single-node neighbourhoods."""
    graph = _adapt(tmp_path, "sample_app")
    assert any(item.kind == "CALLS" for item in graph.relationships)
    assert any(item.kind == "CONTAINS" for item in graph.relationships)


def test_edge_confidence_labels_become_provenance(tmp_path: Path) -> None:
    graph = _adapt(tmp_path, "sample_app")
    assert any(item.provenance is Provenance.EXTRACTED for item in graph.relationships)


def test_an_unmapped_relation_falls_through_rather_than_being_guessed() -> None:
    assert "nonsense_relation" not in RELATION_MAP


def test_polyglot_payload_yields_nodes_for_every_language(tmp_path: Path) -> None:
    """The point of the whole change: Java, Go and JavaScript in one graph.

    Run against the tree the payload was captured from, not a stand-in: the adapter now drops a
    location whose file is not really there, so a mismatched source tree would silently empty the
    graph and the assertion below would be testing nothing.
    """
    graph = _adapt(tmp_path, "polyglot", source_app="polyglot")
    files = {
        Path(entity.location.file).suffix
        for entity in graph.entities
        if entity.location is not None
    }
    assert {".java", ".go", ".js"} <= files

    callables = {
        Path(entity.location.file).suffix
        for entity in graph.entities
        if entity.location is not None and entity.kind in {"FUNCTION", "METHOD", "CLASS"}
    }
    assert {".java", ".go", ".js"} <= callables


def test_extraction_metadata_is_language_neutral(tmp_path: Path) -> None:
    """`parse_coverage` encoded 'Python-only'. Its replacement must not."""
    graph = _adapt(tmp_path, "sample_app")
    extraction = graph.metadata["extraction"]
    assert extraction["extractor"] == "graphify"
    assert 0.0 <= extraction["extraction_coverage"] <= 1.0
    assert "python_files" not in extraction
    assert "parse_coverage" not in extraction


def test_the_adapter_refuses_to_emit_a_dangling_edge(tmp_path: Path) -> None:
    graph = _adapt(tmp_path, "sample_app")
    known = {entity.id for entity in graph.entities}
    assert all(
        item.source in known and item.target in known for item in graph.relationships
    )


def test_sql_schema_reaches_the_graph(tmp_path: Path) -> None:
    """Tables and triggers, via `graphifyy[sql]`. CHECK constraints do not survive — see the skill."""
    graph = _adapt(tmp_path, "schema_app", source_app="schema_app")
    names = {entity.name for entity in graph.entities}
    assert {"app_user", "candidate", "trg_sync_stage"} <= names
    assert any(item.kind == "TRIGGERS" for item in graph.relationships)


# --------------------------------------------------------------------------------------------
# Supporting evidence
# --------------------------------------------------------------------------------------------


def test_readme_outlines_are_captured_and_secrets_redacted(tmp_path: Path) -> None:
    readme = tmp_path / "README.md"
    readme.write_text("# Title\n\nA product.\napi_key = sk-live-abcdef\n", encoding="utf-8")
    outline = safe_file_outline(readme)
    assert any("A product." in line for line in outline)
    assert any("<redacted>" in line for line in outline)
    assert not any("sk-live-abcdef" in line for line in outline)


def test_an_env_file_is_never_outlined(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text("AZURE_OPENAI_API_KEY=secret\n", encoding="utf-8")
    assert safe_file_outline(env) == []


def test_adapter_error_is_available_for_structural_defects() -> None:
    assert issubclass(AdapterError, RuntimeError)


def test_the_last_definition_in_a_file_runs_to_the_end_of_it() -> None:
    """Without a file length the last definition collapses to one line and loses its body."""
    nodes = [
        {"id": "early", "source_file": "a.py", "source_location": "L10"},
        {"id": "last", "source_file": "a.py", "source_location": "L20"},
    ]
    assert _derive_line_ends(nodes, [])["last"] == 20
    assert _derive_line_ends(nodes, [], {"a.py": 48})["last"] == 48


def test_a_rationale_node_asserts_no_symbol_and_keeps_a_short_name(tmp_path: Path) -> None:
    """Its label is a docstring sentence, not an identifier — unmatchable as a cited symbol."""
    graph = _adapt(tmp_path, "sample_app")
    rationale = [entity for entity in graph.entities if entity.kind == "RATIONALE"]
    assert rationale
    for entity in rationale:
        assert entity.location is None or entity.location.symbol is None
        assert len(entity.name) <= 80


def test_real_definitions_still_assert_their_symbol(tmp_path: Path) -> None:
    """The anti-fabrication check depends on this; loosening it for RATIONALE must not leak."""
    graph = _adapt(tmp_path, "sample_app")
    functions = [entity for entity in graph.entities if entity.kind == "FUNCTION"]
    assert functions
    assert all(entity.location.symbol for entity in functions if entity.location)


def test_a_file_entity_spans_the_whole_file(tmp_path: Path) -> None:
    """Containment repair stops at the last contained node, which under-reports the file."""
    graph = _adapt(tmp_path, "sample_app")
    main = next(
        entity
        for entity in graph.entities
        if entity.kind == "FILE" and entity.location.file.endswith("main.py")
    )
    real_lines = sum(
        1 for _ in (FIXTURES / "sample_app" / "app" / "main.py").open("rb")
    )
    assert main.location.line_end == real_lines


def test_an_empty_file_does_not_end_before_it_begins(tmp_path: Path) -> None:
    """`__init__.py` is zero lines long; a naive length lookup gives it line_end=0."""
    graph = _adapt(tmp_path, "sample_app")
    init = next(
        entity
        for entity in graph.entities
        if entity.kind == "FILE" and entity.location.file.endswith("__init__.py")
    )
    assert init.location.line_end >= init.location.line_start


def test_the_extractor_is_given_absolute_paths(tmp_path: Path, monkeypatch) -> None:
    """A run started from the app carries relative paths, and the child must not join them.

    `Settings.runs_dir` defaults to the relative `Path("runs")`, so `source_root` reaches the
    capability as `runs/<id>/source`. Passing that verbatim while also setting the subprocess `cwd`
    to it made graphify resolve one against the other and fail with a doubled path. Every test
    above passes an absolute `runs_dir`, which is exactly why none of them caught it.
    """
    import subprocess

    from agentic.capabilities import graphify as module

    run_dir = tmp_path / "run"
    (run_dir / "artifacts").mkdir(parents=True)
    shutil.copytree(FIXTURES / "sample_app", run_dir / "source")

    monkeypatch.chdir(tmp_path)
    relative = Path("run") / "source"
    assert not relative.is_absolute()

    seen: dict[str, Any] = {}

    def _fake_run(command, **kwargs):
        seen["command"] = command
        seen["cwd"] = kwargs.get("cwd")
        raise OSError("stopped before launching")

    monkeypatch.setattr(subprocess, "run", _fake_run)
    monkeypatch.setattr(module, "find_executable", lambda configured="": "graphify")

    state: dict[str, Any] = {
        "run_dir": "run",
        "source_root": str(relative),
        "artifacts": {},
    }
    ctx = CapabilityContext(
        state=state,  # type: ignore[arg-type]
        step=_Step(),  # type: ignore[arg-type]
        settings=Settings(runs_dir=Path("runs")),
        store=ArtifactStore(run_dir),
        skill=None,  # type: ignore[arg-type]
    )
    result = module.graphify_extract(ctx)

    assert result.halt_reason is not None, "the fake subprocess should have stopped the run"
    path_argument = Path(seen["command"][2])
    assert path_argument.is_absolute(), seen["command"]
    assert Path(seen["cwd"]).is_absolute()
    assert path_argument == (tmp_path / "run" / "source").resolve()
    out_index = seen["command"].index("--out")
    assert Path(seen["command"][out_index + 1]).is_absolute()


# --------------------------------------------------------------------------------------------
# What a large repository exposes that a small one does not
# --------------------------------------------------------------------------------------------


def test_a_file_node_is_recognised_when_its_label_carries_part_of_the_path() -> None:
    """Graphify labels a file with as much path as it takes to be unambiguous.

    `main.py` in a flat repository, but `api/mail.py` once two directories hold a file of that
    name. Matching the basename alone missed every file node in a repository with any repeated
    filename — `__init__.py`, `index.ts`, `mail.py` — and each one fell through to SYMBOL and then
    asserted its own filename as a symbol, which Gate 1 rejected as unresolvable.
    """
    node = {
        "label": "api/mail.py",
        "source_file": "resume-agent-PROD/app/api/mail.py",
        "file_type": "code",
    }
    assert classify_kind(node) == "FILE"
    assert classify_kind({**node, "label": "constants/index.ts",
                          "source_file": "fe/src/constants/index.ts"}) == "FILE"


def test_a_partial_name_is_not_mistaken_for_the_file() -> None:
    """Segment boundaries, so `ail.py` does not match `app/api/mail.py`."""
    assert classify_kind(
        {"label": "ail.py", "source_file": "app/api/mail.py", "file_type": "code"}
    ) != "FILE"


def test_a_file_is_named_by_its_whole_path() -> None:
    """Two files labelled `api/mail.py` under different roots must not share a qualified name."""
    from agentic.capabilities.graphify import _qualified_name

    assert _qualified_name(
        {"label": "api/mail.py", "source_file": "backend/app/api/mail.py"}, "FILE"
    ) == "backend/app/api/mail.py"


def test_a_label_that_is_not_an_identifier_asserts_no_symbol() -> None:
    """`entrypoint.sh script` is a description; the dotted rule would hunt for `sh script`."""
    from agentic.capabilities.graphify import _verifiable_symbol

    assert _verifiable_symbol("SYMBOL", "entrypoint.sh script") is None
    assert _verifiable_symbol("SYMBOL", "constants/index.ts") is None
    # A dotted module path is still a name, and must survive.
    assert _verifiable_symbol("FUNCTION", "app.main.helper") == "app.main.helper"
    assert _verifiable_symbol("METHOD", ".Load()") == "Load"


def test_a_node_citing_a_file_that_is_not_there_becomes_external(tmp_path: Path) -> None:
    """Graphify sometimes attributes a node to a bare package name rather than a path.

    Emitting it with a location produced an entity whose own citation Gate 1 then rejected — the
    run failed on the extractor's own output rather than on anything about the repository.
    """
    run_dir = tmp_path / "run"
    (run_dir / "artifacts").mkdir(parents=True)
    shutil.copytree(FIXTURES / "sample_app", run_dir / "source")

    payload = _payload("sample_app")
    payload["nodes"].append(
        {
            "id": "vite",
            "label": "vite",
            "file_type": "code",
            "source_file": "vite",
            "source_location": "L1",
            "_origin": "ast",
        }
    )
    graph_path = run_dir / "gf.json"
    graph_path.write_text(json.dumps(payload), encoding="utf-8")

    store = ArtifactStore(run_dir)
    from agentic.capabilities.ingest import build_inventory

    state: dict[str, Any] = {
        "run_dir": str(run_dir),
        "source_root": str(run_dir / "source"),
        "artifacts": {},
    }
    state["artifacts"]["inventory"] = store.save(
        "inventory", build_inventory(run_dir / "source", "sample_app")
    )
    state["artifacts"]["graphify_raw"] = store.save(
        "graphify_raw", {"graph_path": str(graph_path), "command": []}
    )
    graph = adapt_graphify_graph(
        CapabilityContext(
            state=state,  # type: ignore[arg-type]
            step=_Step(),  # type: ignore[arg-type]
            settings=Settings(runs_dir=tmp_path / "runs"),
            store=store,
            skill=None,  # type: ignore[arg-type]
        )
    ).artifacts["graph"]

    vite = next(entity for entity in graph.entities if entity.name == "vite")
    assert vite.kind == "EXTERNAL_SYMBOL"
    assert vite.location is None
    assert "vite" in graph.metadata["extraction"]["unresolved_cited_files"]


def test_every_entity_location_resolves_and_every_symbol_verifies(tmp_path: Path) -> None:
    """Gate 1 must never reject the extractor's own output. Checked the way the gate checks it."""
    from agentic.capabilities.verify import verify_location

    graph = _adapt(tmp_path, "polyglot", source_app="polyglot")
    located = [entity for entity in graph.entities if entity.location is not None]
    assert located
    root = tmp_path / "run" / "source"
    for entity in located:
        check = verify_location(entity.location, root)
        assert check.ok, f"{entity.kind} {entity.qualified_name}: {check.message}"


# --------------------------------------------------------------------------------------------
# Docstring / rationale text — proposal §15
# --------------------------------------------------------------------------------------------


def test_python_triple_quote_docstring_is_read_in_full() -> None:
    lines = [
        'def helper():',
        '    """First line of the docstring.',
        '',
        '    A second paragraph with more detail than Graphify\'s own truncated label carries.',
        '    """',
        '    return 1',
    ]
    text = _read_rationale(lines, 2)
    assert "First line of the docstring." in text
    assert "second paragraph" in text
    assert '"""' not in text


def test_jsdoc_block_comment_is_read_and_stars_are_stripped() -> None:
    lines = [
        "/**",
        " * Registers the given handler for every route in the table.",
        " * @param handler the callback to invoke",
        " */",
        "function register(handler) {}",
    ]
    text = _read_rationale(lines, 1)
    assert "Registers the given handler" in text
    assert "@param handler" in text
    assert "*" not in _clean_rationale_text(text)


def test_double_slash_comment_block_stops_at_the_first_non_comment_line() -> None:
    lines = [
        "// Persists the invoice and returns the stored reference.",
        "// Callers must not assume the reference is sequential.",
        "func Save(v Invoice) string {",
    ]
    text = _read_rationale(lines, 1)
    assert "Persists the invoice" in text
    assert "Callers must not assume" in text
    assert "func Save" not in text


def test_hash_comment_block_is_read_as_one_unit() -> None:
    lines = [
        "# Recomputes the daily settlement totals from the ledger.",
        "# Safe to re-run; it is idempotent per business day.",
        "def recompute():",
    ]
    text = _read_rationale(lines, 1)
    assert "Recomputes the daily settlement totals" in text
    assert "idempotent per business day" in text


def test_a_single_line_docstring_opens_and_closes_on_the_same_line() -> None:
    lines = ['def total():', '    """Sum of every line item."""', '    return 0']
    assert _read_rationale(lines, 2) == "Sum of every line item."


def test_an_unrecognised_shape_falls_back_to_the_graphify_label() -> None:
    """The delimiter test's own note: never worse than today when the shape isn't recognised."""
    rationale = {
        "id": "r1",
        "label": "Some prose Graphify already truncated to eighty characters or so…",
        "file_type": "rationale",
        "source_file": "a.py",
        "source_location": "L5",
    }
    lines = ["not_a_comment_or_docstring_line = 1", "", "", "", "plain code here"]
    result = _attach_docstring(Path("."), [rationale], {"a.py": lines})
    assert result is not None
    text, span = result
    assert text == rationale["label"]
    assert span == [5, 5]


def test_secrets_inside_a_docstring_are_redacted() -> None:
    text = _clean_rationale_text('Connects using api_key = sk-live-abcdef for the sandbox tenant.')
    assert "sk-live-abcdef" not in text
    assert "<redacted>" in text


def test_a_licence_header_block_is_dropped_outright() -> None:
    text = _clean_rationale_text(
        "Copyright 2024 Example Corp.\nAll rights reserved.\nSPDX-License-Identifier: MIT"
    )
    assert text == ""


def test_multiple_rationales_on_one_target_are_combined_in_line_order() -> None:
    nodes_by_id = {
        "doc": {
            "id": "doc",
            "label": "Handles the request.",
            "file_type": "rationale",
            "source_file": "a.py",
            "source_location": "L1",
        },
        "why": {
            "id": "why",
            "label": "WHY: retried because upstream is flaky.",
            "file_type": "rationale",
            "source_file": "a.py",
            "source_location": "L8",
        },
    }
    edges = [
        {"source": "doc", "target": "handler", "relation": "rationale_for"},
        {"source": "why", "target": "handler", "relation": "rationale_for"},
    ]
    sources = _index_rationale_sources(nodes_by_id, edges)
    assert [raw["id"] for raw in sources["handler"]] == ["doc", "why"]

    lines = {
        "a.py": [
            '"""Handles the request."""',
            "",
            "",
            "",
            "",
            "",
            "",
            "# WHY: retried because upstream is flaky.",
            "def handler(): ...",
        ]
    }
    result = _attach_docstring(Path("."), sources["handler"], lines)
    assert result is not None
    text, span = result
    assert "Handles the request." in text
    assert "retried because upstream is flaky" in text
    assert text.index("Handles the request.") < text.index("retried because upstream is flaky")
    assert span == [1, 8]


def test_the_module_docstring_reaches_the_file_entity_in_full(tmp_path: Path) -> None:
    """The whole point: the full text, not Graphify's ~80-char truncated label."""
    graph = _adapt(tmp_path, "sample_app")
    main = next(
        entity
        for entity in graph.entities
        if entity.kind == "FILE" and entity.location and entity.location.file.endswith("main.py")
    )
    docstring = main.attributes.get("docstring")
    assert docstring is not None
    assert "internal helper that nothing reachable from a boundary ever calls" in docstring
    assert main.attributes.get("docstring_at") is not None


def test_a_function_docstring_reaches_its_own_entity(tmp_path: Path) -> None:
    graph = _adapt(tmp_path, "sample_app")
    helper = next(
        entity for entity in graph.entities if entity.name == "_unused_reconciliation_helper()"
    )
    docstring = helper.attributes.get("docstring")
    assert docstring is not None
    assert "sophisticated-looking internal processing" in docstring


def test_docstrings_are_capped_at_the_configured_length(tmp_path: Path) -> None:
    from agentic.capabilities.registry import CapabilityContext as _Ctx
    from agentic.store import ArtifactStore

    run_dir = tmp_path / "run"
    (run_dir / "artifacts").mkdir(parents=True)
    shutil.copytree(FIXTURES / "sample_app", run_dir / "source")

    store = ArtifactStore(run_dir)
    graph_path = run_dir / "graphify-graph.json"
    graph_path.write_text(json.dumps(_payload("sample_app")), encoding="utf-8")

    state: dict[str, Any] = {
        "run_dir": str(run_dir),
        "source_root": str(run_dir / "source"),
        "artifacts": {},
    }
    from agentic.capabilities.ingest import build_inventory

    inventory = build_inventory(run_dir / "source", "sample_app")
    state["artifacts"]["inventory"] = store.save("inventory", inventory)
    state["artifacts"]["graphify_raw"] = store.save(
        "graphify_raw", {"graph_path": str(graph_path), "command": ["extract"]}
    )

    ctx = _Ctx(
        state=state,  # type: ignore[arg-type]
        step=_Step(),  # type: ignore[arg-type]
        settings=Settings(runs_dir=tmp_path / "runs", docstring_max_chars=20),
        store=store,
        skill=None,  # type: ignore[arg-type]
    )
    graph = adapt_graphify_graph(ctx).artifacts["graph"]
    helper = next(
        entity for entity in graph.entities if entity.name == "_unused_reconciliation_helper()"
    )
    assert len(helper.attributes["docstring"]) <= 20


def test_graph_docstrings_can_be_switched_off(tmp_path: Path) -> None:
    from agentic.capabilities.registry import CapabilityContext as _Ctx
    from agentic.store import ArtifactStore

    run_dir = tmp_path / "run"
    (run_dir / "artifacts").mkdir(parents=True)
    shutil.copytree(FIXTURES / "sample_app", run_dir / "source")

    store = ArtifactStore(run_dir)
    graph_path = run_dir / "graphify-graph.json"
    graph_path.write_text(json.dumps(_payload("sample_app")), encoding="utf-8")

    state: dict[str, Any] = {
        "run_dir": str(run_dir),
        "source_root": str(run_dir / "source"),
        "artifacts": {},
    }
    from agentic.capabilities.ingest import build_inventory

    inventory = build_inventory(run_dir / "source", "sample_app")
    state["artifacts"]["inventory"] = store.save("inventory", inventory)
    state["artifacts"]["graphify_raw"] = store.save(
        "graphify_raw", {"graph_path": str(graph_path), "command": ["extract"]}
    )

    ctx = _Ctx(
        state=state,  # type: ignore[arg-type]
        step=_Step(),  # type: ignore[arg-type]
        settings=Settings(runs_dir=tmp_path / "runs", graph_docstrings=False),
        store=store,
        skill=None,  # type: ignore[arg-type]
    )
    graph = adapt_graphify_graph(ctx).artifacts["graph"]
    assert all("docstring" not in entity.attributes for entity in graph.entities)

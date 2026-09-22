"""Declared policy: the six node kinds that had consumers and no producer.

`compact_entity`, `render.py`, `gates.py` and `declaration_groups` all carried hand-written
support for `CONFIG_KEY`, `DATA_ENTITY`, `DATA_FIELD`, `DATA_INDEX`, `DOMAIN_POLICY` and
`EXTERNAL_SERVICE`, and nothing emitted any of them. These tests pin the producer, and — more
importantly — pin the two properties that make it safe to trust: a value is only published when
the line genuinely fixes one, and a credential's value never leaves the file it was declared in.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agentic.capabilities.declarations import (
    _clean_value,
    _config_key_nodes,
    _is_config_file,
    _is_sql_file,
    _manifest_dependencies,
    _sql_declarations,
)
from agentic.schemas.core import KnowledgeGraph


# --------------------------------------------------------------------------------------------
# Which files are opened at all
# --------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    [
        ".env",
        "app/.env.example",
        "config/limits.yml",
        "deploy/values.yaml",
        "setup.cfg",
        "app/settings.py",
        "src/config.ts",
        "app/constants.js",
    ],
)
def test_config_shaped_paths_are_recognised(path: str) -> None:
    assert _is_config_file(path)


@pytest.mark.parametrize(
    "path", ["app/api/routes.py", "src/components/Button.tsx", "README.md", "app/models.py"]
)
def test_ordinary_code_is_not_treated_as_configuration(path: str) -> None:
    """A module named after what it does is not a settings file.

    Matching too eagerly is the expensive mistake here: every `UPPER_CASE = value` in ordinary
    code would become a declared operating instruction, and a document full of internal constants
    presented as product policy is worse than one with an honest gap.
    """
    assert not _is_config_file(path)


def test_migration_directories_count_as_schema_whatever_the_extension() -> None:
    assert _is_sql_file("db/schema.sql")
    assert _is_sql_file("app/migrations/0003_add_state.py")
    assert not _is_sql_file("app/services/migration_helper_notes.md")


# --------------------------------------------------------------------------------------------
# A value is only policy when the line fixes one
# --------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("30", "30"),
        ("30s", "30s"),
        ("1.5", "1.5"),
        ("true", "true"),
        ("minio", "minio"),
        ("/var/lib/data", "/var/lib/data"),
        ('"quoted-value"', "quoted-value"),
        ("30  # seconds", "30"),
        ("30 // seconds", "30"),
    ],
)
def test_fixed_values_survive(raw: str, expected: str) -> None:
    assert _clean_value(raw) == expected


def test_a_url_is_not_truncated_at_its_scheme() -> None:
    """`//` opens a comment in a JS-style config and appears inside every URL.

    Splitting on it unconditionally turned `http://minio:9000` into `http:` — a value that still
    parses, still looks deliberate, and states a policy the product does not have. A wrong value
    is worse than no value, because nothing downstream can tell it is wrong.
    """
    assert _clean_value("http://minio:9000") == "http://minio:9000"
    assert _clean_value("https://example.test/v1 // the upstream") == "https://example.test/v1"


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "   ",
        "${DATABASE_URL}",
        "<your-key-here>",
        "changeme",
        "TODO",
        "your-account-name",
        "...",
        "a sentence describing something at length",
    ],
)
def test_placeholders_and_prose_fix_nothing(raw: str) -> None:
    """An intention to configure is not a configured value.

    Publishing `${DATABASE_URL}` as a declared operating instruction asserts a limit the product
    does not have, which is the one failure mode worse than an empty section.
    """
    assert _clean_value(raw) is None


# --------------------------------------------------------------------------------------------
# Credentials
# --------------------------------------------------------------------------------------------


def test_a_credentials_value_never_leaves_the_file() -> None:
    """The key is the evidence; the value is never carried, whatever it is.

    That a `PG_PASSWORD` is required is a real requirement — the product authenticates to its
    database. The secret itself has no place in a graph, a packet or a document, and this holds
    even when the declared value is obviously a sample.
    """
    lines = [
        "PG_USER=resume_user",
        "PG_PASSWORD=hunter2",
        "OPENAI_API_KEY=sk-not-a-real-key",
        "SESSION_SECRET=abc123",
    ]
    found = {key: (value, secret) for key, value, _, secret in _config_key_nodes(".env", lines)}

    assert found["PG_USER"] == ("resume_user", False)
    for key in ("PG_PASSWORD", "OPENAI_API_KEY", "SESSION_SECRET"):
        assert found[key] == ("<redacted>", True), key
    assert "hunter2" not in str(found)
    assert "sk-not-a-real-key" not in str(found)


def test_comment_and_blank_lines_contribute_nothing() -> None:
    lines = ["# TIMEOUT=99", "", "   ", "// CAP=5", "; OLD=3", "TIMEOUT=30"]
    assert [key for key, _, _, _ in _config_key_nodes(".env", lines)] == ["TIMEOUT"]


# --------------------------------------------------------------------------------------------
# Schema
# --------------------------------------------------------------------------------------------

DDL = """
-- One row per borrower.
CREATE TABLE borrowers (
    id            BIGSERIAL PRIMARY KEY,
    email         TEXT NOT NULL UNIQUE,
    org_id        BIGINT REFERENCES organisation(id),
    penalty_pence INTEGER NOT NULL DEFAULT 0,
    state         TEXT NOT NULL,
    CONSTRAINT chk_state CHECK (state IN ('open','returned','overdue'))
);

CREATE UNIQUE INDEX idx_borrower_email ON borrowers (email);
CREATE INDEX idx_borrower_org ON borrowers (org_id, state);
"""


def test_columns_carry_the_guarantees_the_database_enforces() -> None:
    tables, _ = _sql_declarations(DDL.splitlines())
    assert [table["name"] for table in tables] == ["borrowers"]
    fields = {field["name"]: field for field in tables[0]["fields"]}

    assert fields["email"]["not_null"] is True
    assert fields["email"]["unique"] is True
    assert fields["id"]["primary_key"] is True
    assert fields["penalty_pence"]["default"] == "0"
    assert fields["org_id"]["references"] == "organisation"
    # A constraint line is not a column, or every table would gain a phantom field named after
    # its own constraint.
    assert "chk_state" not in fields
    assert "CONSTRAINT" not in fields


def test_a_check_constraint_becomes_a_quoted_vocabulary() -> None:
    """The strongest policy evidence a codebase carries, and previously unreachable.

    A `CHECK … IN` states what the business permits, and the database enforces it whatever the
    application code does — so it outranks any conditional a model might find in a handler.
    """
    tables, _ = _sql_declarations(DDL.splitlines())
    policies = tables[0]["policies"]
    assert len(policies) == 1
    assert policies[0]["column"] == "state"
    assert policies[0]["allowed_values"] == ["open", "returned", "overdue"]


def test_indexes_distinguish_a_guarantee_from_an_optimisation() -> None:
    _, indexes = _sql_declarations(DDL.splitlines())
    by_name = {index["name"]: index for index in indexes}
    assert by_name["idx_borrower_email"]["unique"] is True
    assert by_name["idx_borrower_email"]["columns"] == ["email"]
    # A non-unique index promises nothing about the data and must not be published as if it did.
    assert by_name["idx_borrower_org"]["unique"] is False
    assert by_name["idx_borrower_org"]["columns"] == ["org_id", "state"]


# --------------------------------------------------------------------------------------------
# Dependency manifests
# --------------------------------------------------------------------------------------------


def test_only_runtime_dependencies_are_read_from_a_node_manifest() -> None:
    """A dev dependency is a fact about the build, not about a system the product integrates with.

    Publishing `pytest` as an integration requirement claims a dependency the running product does
    not have.
    """
    text = """
    {"dependencies": {"axios": "^1.6.0", "react": "18.3.1"},
     "devDependencies": {"vitest": "^1.0.0", "eslint": "^8"}}
    """
    found = dict(_manifest_dependencies("package.json", text, "node"))
    assert found == {"axios": "^1.6.0", "react": "18.3.1"}


def test_python_requirements_carry_their_pinned_version() -> None:
    text = "fastapi==0.110.0\nboto3>=1.34\n# a comment\n-r dev.txt\n\npydantic"
    found = dict(_manifest_dependencies("requirements.txt", text, "python"))
    assert found["fastapi"] == "==0.110.0"
    assert found["boto3"] == ">=1.34"
    assert found["pydantic"] == ""
    assert "-r" not in found


# --------------------------------------------------------------------------------------------
# End to end, on the corpus fixture
# --------------------------------------------------------------------------------------------


def test_the_sample_app_schema_reaches_the_graph_as_policy(runner, state) -> None:
    for step_id in ("ingest", "graphify_extract", "adapt_graph", "extract_declarations"):
        runner(step_id)
    graph = KnowledgeGraph.model_validate(
        __import__("json").loads(Path(state["artifacts"]["graph"]).read_text(encoding="utf-8"))
    )
    kinds = {entity.kind for entity in graph.entities}
    assert {"DATA_ENTITY", "DATA_FIELD", "DOMAIN_POLICY"} <= kinds

    policies = [entity for entity in graph.entities if entity.kind == "DOMAIN_POLICY"]
    roles = next(entity for entity in policies if "role" in entity.name)
    assert roles.attributes["allowed_values"] == ["admin", "recruiter", "sysadmin"]
    assert roles.attributes["rule_source"] == "schema_constraint"
    # Every declaration must cite a real line, because Gate 2 re-resolves each citation against
    # the file system and a policy node that cannot be verified is worse than none.
    assert roles.location is not None
    assert roles.location.line_start


def test_switching_declaration_extraction_off_reproduces_the_previous_graph(
    runner, state, settings
) -> None:
    """The kill switch has to be exact, not approximate.

    Every claim about this change being additive rests on this test: with the setting off, the
    graph must be the one Graphify and the adapter produced, node for node.
    """
    for step_id in ("ingest", "graphify_extract", "adapt_graph"):
        runner(step_id)
    import json

    before = json.loads(Path(state["artifacts"]["graph"]).read_text(encoding="utf-8"))

    settings.declaration_extraction = False
    runner("extract_declarations")
    after = json.loads(Path(state["artifacts"]["graph"]).read_text(encoding="utf-8"))

    assert len(after["entities"]) == len(before["entities"])
    assert len(after["relationships"]) == len(before["relationships"])
    assert {entity["id"] for entity in after["entities"]} == {
        entity["id"] for entity in before["entities"]
    }


def test_declarations_only_add_and_never_rewrite(runner, state) -> None:
    for step_id in ("ingest", "graphify_extract", "adapt_graph"):
        runner(step_id)
    import json

    before = json.loads(Path(state["artifacts"]["graph"]).read_text(encoding="utf-8"))
    original = {entity["id"]: entity for entity in before["entities"]}

    runner("extract_declarations")
    after = json.loads(Path(state["artifacts"]["graph"]).read_text(encoding="utf-8"))

    # Nothing Graphify produced may be removed or altered — the whole risk profile of this change
    # depends on it being purely additive.
    for entity_id, entity in original.items():
        assert entity_id in {item["id"] for item in after["entities"]}
        match = next(item for item in after["entities"] if item["id"] == entity_id)
        assert match == entity
    assert len(after["entities"]) > len(before["entities"])

"""Declared policy: the evidence a call graph cannot carry.

Graphify answers *who calls whom*. That is the right question for behaviour and the wrong one for
policy, because a codebase states most of its policy in files nothing calls: a schema, a settings
file, a dependency manifest. `graph_ops.declaration_groups` was written for exactly this — its
docstring says such evidence *"was extracted and then dropped on the floor"* — and it ranks
candidate files by how many `DOMAIN_POLICY`, `DATA_ENTITY` and `CONFIG_KEY` nodes they hold.

**Nothing emitted any of those three kinds.** So the ranking key was always zero, the sort
collapsed to the file path, and the mechanism ran inert — while `compact_entity`, `render.py` and
`gates.py` all carried hand-written support for them, orphaned when the Python-only AST extractors
were swapped for Graphify. This module is that missing producer. Everything downstream of it was
already built and waiting.

## Why deterministic, and why not tree-sitter

Every fact here is a declaration read off a line: a key and its value, a column and its type, a
package and its version. None of it needs a grammar and none of it needs a model. A regex that
reads `TIMEOUT=30` is not a shortcut around parsing — it is the whole of what that line says. The
rule this module holds to is the same one the rest of the pipeline holds to: **emit what the line
proves and nothing else.** A format this module cannot read contributes no nodes rather than
guessed ones, and `report_family_coverage` says so out loud instead of leaving a silent gap.

## What each kind grounds

`CONFIG_KEY`   a declared operating instruction — a timeout, a cap, a retry bound, a flag. The
               single evidence class for `non_functional_requirements`, which has never been
               populated in any stored run.
`DATA_ENTITY`  a stored collection, and `DATA_FIELD` / `DATA_INDEX` its columns and guarantees.
               A declared constraint states policy more strongly than any application code,
               because the database enforces it whatever the code does.
`DOMAIN_POLICY` an enumerated vocabulary or a range the schema itself fixes — a business rule the
               business wrote down.
`EXTERNAL_SERVICE` one per declared third-party dependency, which is what
               `integration_requirements` asks for and what no node in a call graph provides.
"""

from __future__ import annotations

import json
import re

from collections import Counter
from pathlib import Path
from typing import Any, Iterable

from agentic.capabilities.graph_ops import GraphBuilder, stable_id
from agentic.capabilities.registry import CapabilityContext, CapabilityResult, capability
from agentic.schemas.core import Entity, KnowledgeGraph, Provenance, SourceLocation

# Files worth opening, by role. Matched on the path's own shape rather than a curated list of
# framework filenames, so an unfamiliar stack still contributes.
_CONFIG_SUFFIXES = {".env", ".ini", ".cfg", ".conf", ".properties", ".toml", ".yaml", ".yml"}
_CONFIG_STEMS = ("settings", "config", "configuration", "constants", "defaults")
_SQL_SUFFIXES = {".sql", ".ddl"}
_MANIFESTS = {
    "requirements.txt": "python",
    "requirements-dev.txt": "python",
    "pyproject.toml": "python",
    "package.json": "node",
    "go.mod": "go",
    "cargo.toml": "rust",
    "gemfile": "ruby",
    "pom.xml": "java",
    "build.gradle": "java",
    "composer.json": "php",
}

# A value is only policy when it is *fixed* here. An empty value, a placeholder, or a reference to
# another variable states an intention to configure, not a configured value, and publishing it as
# a requirement would assert a limit the product does not have.
_PLACEHOLDER = re.compile(r"^\s*(|\$\{.*\}|<.*>|xxx+|todo|changeme|your[-_ ].*|\.\.\.)\s*$", re.I)

# Reuses the shape `graphify._SECRET` uses, for the same reason: a declared credential is evidence
# that authentication exists, and its value must never reach a packet or a document.
_SECRET_NAME = re.compile(r"(?i)(api[_-]?key|password|passwd|secret|token|credential|private[_-]?key)")

# `KEY = value` in a .env, .ini, .properties or a Python/JS constants module. `export` is stripped
# because a shell-style .env commonly carries it.
_ASSIGNMENT = re.compile(
    r"""^\s*(?:export\s+)?(?P<key>[A-Z][A-Z0-9_]{2,60})\s*[:=]\s*(?P<value>.*?)\s*(?:[;,]\s*)?$"""
)

# A quoted or bare scalar on a YAML key, one level deep. Nested YAML is deliberately not walked:
# a key's meaning depends on its parents, and a flattened `timeout` under an unknown parent is not
# a fact about the product.
_YAML_SCALAR = re.compile(r"""^(?P<indent>\s{0,4})(?P<key>[A-Za-z_][\w.-]{1,60})\s*:\s*(?P<value>[^#\n]*?)\s*$""")

_QUOTED = re.compile(r"""^(['"])(?P<inner>.*)\1$""")

# A value worth publishing states a magnitude, a switch, or a fixed identifier. Prose does not.
_MEANINGFUL_VALUE = re.compile(
    r"""^(?:
        \d+(?:\.\d+)?(?:s|ms|m|h|d|kb|mb|gb|b|%)?   # a magnitude, with or without a unit
      | true|false|yes|no|on|off|enabled|disabled    # a switch
      # An identifier, a mode name, a URL — or a path. The leading `/` and `.` matter: a declared
      # storage location is one of the operating instructions `declared_policy` exists to carry,
      # and requiring a leading letter silently dropped every absolute path.
      | [A-Za-z/.][\w./:@+-]{0,80}
    )$""",
    re.I | re.X,
)

_CREATE_TABLE = re.compile(r"""create\s+table(?:\s+if\s+not\s+exists)?\s+[`"\[]?(?P<name>[\w.]+)""", re.I)
_CREATE_INDEX = re.compile(
    r"""create\s+(?P<unique>unique\s+)?index(?:\s+if\s+not\s+exists)?\s+[`"\[]?(?P<name>[\w.]+)[`"\]]?"""
    r"""\s+on\s+[`"\[]?(?P<table>[\w.]+)[`"\]]?\s*\((?P<columns>[^)]*)\)""",
    re.I,
)
_COLUMN = re.compile(
    r"""^\s*[`"\[]?(?P<name>\w+)[`"\]]?\s+(?P<type>[A-Za-z][\w]*(?:\s*\([^)]*\))?)(?P<rest>.*?),?\s*$"""
)
_CHECK_IN = re.compile(r"""check\s*\(\s*[`"\[]?(?P<column>\w+)[`"\]]?\s+in\s*\((?P<values>[^)]*)\)""", re.I)
_REFERENCES = re.compile(r"""references\s+[`"\[]?(?P<table>[\w.]+)""", re.I)
_NOT_A_COLUMN = re.compile(
    r"""^\s*(constraint|primary\s+key|foreign\s+key|unique|check|index|key)\b""", re.I
)

# A ceiling per file, so one generated schema or a vendored settings dump cannot flood the graph
# with declarations and crowd out every other file's.
_MAX_DECLARATIONS_PER_FILE = 120


def _is_config_file(path: str) -> bool:
    lowered = path.lower()
    name = lowered.rsplit("/", 1)[-1]
    if name.startswith(".env"):
        return True
    suffix = Path(lowered).suffix
    if suffix in _CONFIG_SUFFIXES:
        return True
    stem = Path(name).stem
    return suffix in {".py", ".js", ".ts", ".mjs"} and any(part in stem for part in _CONFIG_STEMS)


def _is_sql_file(path: str) -> bool:
    lowered = path.lower()
    if Path(lowered).suffix in _SQL_SUFFIXES:
        return True
    # A migration directory is where a schema's history lives, whatever the file extension.
    return "migration" in lowered and Path(lowered).suffix in {".py", ".rb", ".js", ".ts"}


def _manifest_ecosystem(path: str) -> str | None:
    return _MANIFESTS.get(path.lower().rsplit("/", 1)[-1])


def _unquote(value: str) -> str:
    match = _QUOTED.match(value.strip())
    return match.group("inner") if match else value.strip()


def _clean_value(raw: str) -> str | None:
    """The declared value, or None when the line fixes nothing.

    Inline comments are stripped only after quoting is resolved, so a `#` inside a quoted value —
    a URL fragment, a format string — survives.
    """
    value = _unquote(raw)
    if not _QUOTED.match(raw.strip()):
        value = value.split("#", 1)[0]
        # `//` opens a comment in a JS-style config and appears inside every URL. Splitting on it
        # unconditionally turned `MINIO_ENDPOINT=http://minio:9000` into `http:` — a value that
        # still parses, still looks deliberate, and states a policy the product does not have.
        # Only a `//` that is not part of a scheme is a comment.
        value = re.sub(r"(?<!:)//.*$", "", value).strip()
    value = _unquote(value)
    if _PLACEHOLDER.match(value):
        return None
    if len(value) > 120 or not _MEANINGFUL_VALUE.match(value):
        return None
    return value


def _config_key_nodes(path: str, lines: list[str]) -> list[tuple[str, str, int, bool]]:
    """`(key, value, line, secret_like)` for every fixed declaration in one config file."""
    found: list[tuple[str, str, int, bool]] = []
    is_yaml = Path(path.lower()).suffix in {".yaml", ".yml"}
    for number, text in enumerate(lines, start=1):
        stripped = text.strip()
        if not stripped or stripped.startswith(("#", "//", ";")):
            continue
        match = _ASSIGNMENT.match(text)
        if match is None and is_yaml:
            match = _YAML_SCALAR.match(text)
        if match is None:
            continue
        key = match.group("key")
        secret_like = bool(_SECRET_NAME.search(key))
        if secret_like:
            # The key is the evidence — that a credential is required here at all. The value is
            # never carried, whatever it is.
            found.append((key, "<redacted>", number, True))
            continue
        value = _clean_value(match.group("value"))
        if value is None:
            continue
        found.append((key, value, number, False))
        if len(found) >= _MAX_DECLARATIONS_PER_FILE:
            break
    return found


def _sql_declarations(lines: list[str]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Tables with their columns, and indexes, from DDL.

    A line-oriented reader rather than a SQL parser, because a `CREATE TABLE` block is
    line-oriented in every dialect that matters here and because a half-understood parse is worse
    than a column left out: the column is reported missing, the misparse is published as policy.
    """
    tables: list[dict[str, Any]] = []
    indexes: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    depth = 0
    for number, text in enumerate(lines, start=1):
        stripped = text.strip()
        if not stripped or stripped.startswith("--"):
            continue

        if index := _CREATE_INDEX.search(stripped):
            indexes.append(
                {
                    "name": index.group("name"),
                    "table": index.group("table"),
                    "columns": [part.strip().strip('`"[]') for part in index.group("columns").split(",")],
                    "unique": bool(index.group("unique")),
                    "line": number,
                }
            )
            continue

        if current is None:
            if table := _CREATE_TABLE.search(stripped):
                current = {"name": table.group("name"), "line": number, "fields": [], "policies": []}
                depth = stripped.count("(") - stripped.count(")")
            continue

        depth += stripped.count("(") - stripped.count(")")

        if check := _CHECK_IN.search(stripped):
            values = [_unquote(part) for part in check.group("values").split(",")]
            current["policies"].append(
                {
                    "column": check.group("column"),
                    "allowed_values": [value for value in values if value],
                    "line": number,
                }
            )
        elif not _NOT_A_COLUMN.match(stripped):
            if column := _COLUMN.match(stripped):
                rest = column.group("rest") or ""
                lowered = rest.lower()
                field: dict[str, Any] = {
                    "name": column.group("name"),
                    "type": " ".join(column.group("type").split()),
                    "not_null": "not null" in lowered,
                    "line": number,
                }
                if "unique" in lowered:
                    field["unique"] = True
                if "primary key" in lowered:
                    field["primary_key"] = True
                if default := re.search(r"default\s+([^\s,]+)", rest, re.I):
                    field["default"] = _unquote(default.group(1))
                if reference := _REFERENCES.search(rest):
                    field["references"] = reference.group("table")
                current["fields"].append(field)

        if depth <= 0:
            tables.append(current)
            current = None
    if current is not None:
        tables.append(current)
    return tables, indexes


def _manifest_dependencies(path: str, text: str, ecosystem: str) -> list[tuple[str, str]]:
    """`(package, declared version)` for one dependency manifest.

    Only declared *runtime* dependencies. A dev/test dependency is a fact about how the product is
    built, not about a system it integrates with, and publishing it as an integration requirement
    would claim a dependency the running product does not have.
    """
    found: list[tuple[str, str]] = []
    if path.lower().endswith(".json"):
        try:
            data = json.loads(text)
        except (ValueError, TypeError):
            return []
        for name, version in (data.get("dependencies") or {}).items():
            found.append((str(name), str(version)))
        return found

    if path.lower().endswith("go.mod"):
        for line in text.splitlines():
            if match := re.match(r"^\s*([\w./-]+)\s+(v[\w.\-+]+)", line.strip()):
                if match.group(1) != "module":
                    found.append((match.group(1), match.group(2)))
        return found

    if ecosystem == "python" and path.lower().endswith(".txt"):
        for line in text.splitlines():
            stripped = line.split("#", 1)[0].strip()
            if not stripped or stripped.startswith("-"):
                continue
            if match := re.match(r"^([A-Za-z0-9_.\[\]-]+)\s*([<>=!~]=?.*)?$", stripped):
                found.append((match.group(1), (match.group(2) or "").strip()))
        return found

    if path.lower().endswith(".toml"):
        # `[project] dependencies = [...]` and Poetry's `[tool.poetry.dependencies]`, read
        # line-wise so no TOML parser is required on any Python version.
        in_block = False
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith("["):
                in_block = "dependencies" in stripped
                continue
            if re.match(r"^dependencies\s*=\s*\[", stripped):
                in_block = True
                continue
            if in_block:
                if stripped.startswith("]"):
                    in_block = False
                    continue
                if match := re.match(r"""^["']?([A-Za-z0-9_.-]+)["']?\s*[=:]?\s*["']?([^"',]*)""", stripped):
                    if match.group(1):
                        found.append((match.group(1), match.group(2).strip()))
    return found


def _location(path: str, line: int, symbol: str | None = None) -> SourceLocation:
    return SourceLocation(file=path, line_start=line, line_end=line, symbol=symbol)


def _read(root: Path, relative: str) -> list[str] | None:
    try:
        return (root / relative).read_text(encoding="utf-8-sig", errors="ignore").splitlines()
    except OSError:
        return None


def _file_entity_ids(graph: KnowledgeGraph) -> dict[str, str]:
    return {
        entity.location.file: entity.id
        for entity in graph.entities
        if entity.kind == "FILE" and entity.location is not None
    }


def _attach(builder: GraphBuilder, file_ids: dict[str, str], path: str, entity_id: str) -> None:
    """Hang a declaration off its file, so `declaration_groups` can group by file as it expects."""
    owner = file_ids.get(path)
    if owner:
        builder.relate(owner, entity_id, "DECLARES", provenance=Provenance.EXTRACTED)


@capability("extract_declarations")
def extract_declarations(ctx: CapabilityContext) -> CapabilityResult:
    """Read declared policy out of config, schema and manifest files and add it to the graph.

    Runs after `adapt_graph` and before `propose_boundaries`, so the declarations are present when
    boundary candidates are ranked and when `declaration_groups` looks for unreached policy.

    Additive by construction: it only ever calls `add_entity`/`relate`, never removes or rewrites
    anything Graphify produced, so switching it off through `declaration_extraction` reproduces the
    previous graph exactly.
    """
    settings = ctx.settings
    graph = ctx.load("graph", KnowledgeGraph)

    if not getattr(settings, "declaration_extraction", True):
        return CapabilityResult(
            artifacts={"graph": graph.model_dump(mode="json")},
            metrics={"declaration_nodes": 0},
            messages=["Declaration extraction is switched off; the graph carries no declared policy."],
        )

    inventory = ctx.load("inventory")
    files = [
        str(item.get("path") or "")
        for item in (inventory.get("files") or [])
        if item.get("path")
    ]

    builder = GraphBuilder(graph.repository_name)
    for entity in graph.entities:
        builder.add_entity(entity)
    for relationship in graph.relationships:
        builder._relationships.setdefault(relationship.id, relationship)

    file_ids = _file_entity_ids(graph)
    root = ctx.source_root
    counts = {
        "CONFIG_KEY": 0,
        "DATA_ENTITY": 0,
        "DATA_FIELD": 0,
        "DATA_INDEX": 0,
        "DOMAIN_POLICY": 0,
        "EXTERNAL_SERVICE": 0,
    }
    secrets = 0
    unreadable: list[str] = []
    seen_services: set[str] = set()

    for path in files:
        if _is_config_file(path):
            lines = _read(root, path)
            if lines is None:
                unreadable.append(path)
                continue
            for key, value, line, secret_like in _config_key_nodes(path, lines):
                entity_id = stable_id("config", path, key)
                builder.add_entity(
                    Entity(
                        id=entity_id,
                        kind="CONFIG_KEY",
                        name=key,
                        qualified_name=f"{path}::{key}",
                        location=_location(path, line, key),
                        provenance=Provenance.EXTRACTED,
                        confidence=1.0,
                        attributes={"source": path, "value": value, "secret_like": secret_like},
                    )
                )
                _attach(builder, file_ids, path, entity_id)
                counts["CONFIG_KEY"] += 1
                secrets += int(secret_like)

        if _is_sql_file(path):
            lines = _read(root, path)
            if lines is None:
                unreadable.append(path)
                continue
            tables, indexes = _sql_declarations(lines)
            for table in tables:
                table_id = stable_id("data_entity", path, table["name"])
                builder.add_entity(
                    Entity(
                        id=table_id,
                        kind="DATA_ENTITY",
                        name=table["name"],
                        qualified_name=f"{path}::{table['name']}",
                        location=_location(path, table["line"], table["name"]),
                        provenance=Provenance.EXTRACTED,
                        attributes={
                            "declares": [field["name"] for field in table["fields"]],
                            "description": f"Stored collection declared with {len(table['fields'])} column(s).",
                        },
                    )
                )
                _attach(builder, file_ids, path, table_id)
                counts["DATA_ENTITY"] += 1

                for field in table["fields"]:
                    field_id = stable_id("data_field", path, table["name"], field["name"])
                    attributes = {key: value for key, value in field.items() if key != "line"}
                    attributes["table"] = table["name"]
                    builder.add_entity(
                        Entity(
                            id=field_id,
                            kind="DATA_FIELD",
                            name=field["name"],
                            qualified_name=f"{path}::{table['name']}.{field['name']}",
                            location=_location(path, field["line"], field["name"]),
                            provenance=Provenance.EXTRACTED,
                            attributes=attributes,
                        )
                    )
                    builder.relate(table_id, field_id, "DECLARES", provenance=Provenance.EXTRACTED)
                    counts["DATA_FIELD"] += 1

                for policy in table["policies"]:
                    policy_id = stable_id("policy", path, table["name"], policy["column"])
                    builder.add_entity(
                        Entity(
                            id=policy_id,
                            kind="DOMAIN_POLICY",
                            name=f"{table['name']}.{policy['column']} vocabulary",
                            qualified_name=f"{path}::{table['name']}.{policy['column']}",
                            location=_location(path, policy["line"], policy["column"]),
                            provenance=Provenance.EXTRACTED,
                            attributes={
                                "rule_source": "schema_constraint",
                                "table": table["name"],
                                "columns": [policy["column"]],
                                "allowed_values": policy["allowed_values"],
                                "constraint": "CHECK … IN",
                                "description": (
                                    f"{table['name']}.{policy['column']} accepts only "
                                    f"{', '.join(policy['allowed_values'])}."
                                ),
                            },
                        )
                    )
                    builder.relate(table_id, policy_id, "ENFORCES", provenance=Provenance.EXTRACTED)
                    _attach(builder, file_ids, path, policy_id)
                    counts["DOMAIN_POLICY"] += 1

            for index in indexes:
                index_id = stable_id("data_index", path, index["name"])
                builder.add_entity(
                    Entity(
                        id=index_id,
                        kind="DATA_INDEX",
                        name=index["name"],
                        qualified_name=f"{path}::{index['name']}",
                        location=_location(path, index["line"], index["name"]),
                        provenance=Provenance.EXTRACTED,
                        attributes={
                            "table": index["table"],
                            "columns": index["columns"],
                            "unique": index["unique"],
                            "description": (
                                f"{'Uniqueness guarantee' if index['unique'] else 'Lookup index'} on "
                                f"{index['table']}({', '.join(index['columns'])})."
                            ),
                        },
                    )
                )
                _attach(builder, file_ids, path, index_id)
                counts["DATA_INDEX"] += 1

        if ecosystem := _manifest_ecosystem(path):
            lines = _read(root, path)
            if lines is None:
                unreadable.append(path)
                continue
            for package, version in _manifest_dependencies(path, "\n".join(lines), ecosystem):
                if package.lower() in seen_services:
                    continue
                seen_services.add(package.lower())
                service_id = stable_id("service", package.lower())
                builder.add_entity(
                    Entity(
                        id=service_id,
                        kind="EXTERNAL_SERVICE",
                        name=package,
                        qualified_name=f"{ecosystem}::{package}",
                        location=_location(path, 1, None),
                        # A manifest proves the dependency is declared, not that any code path
                        # reaches it. RESOLVED rather than EXTRACTED so a claim resting only on
                        # this is worded as cautiously as it deserves.
                        provenance=Provenance.RESOLVED,
                        confidence=0.9,
                        attributes={
                            "ecosystem": ecosystem,
                            "declared_version": version,
                            "source": path,
                        },
                    )
                )
                _attach(builder, file_ids, path, service_id)
                counts["EXTERNAL_SERVICE"] += 1

    # Counted from the graph rather than from the read loop. `add_entity` de-duplicates by
    # content-addressed id, so a key declared twice in one file is one node — reporting the read
    # count instead would overstate what a reviewer can actually cite.
    added = Counter(entity.kind for entity in builder.entities)
    counts = {kind: added.get(kind, 0) for kind in counts}
    total = sum(counts.values())
    metadata = dict(graph.metadata)
    metadata["declarations"] = {**counts, "secrets_redacted": secrets}
    updated = KnowledgeGraph(
        schema_version=graph.schema_version,
        repository_name=graph.repository_name,
        entities=builder.entities,
        relationships=builder.relationships,
        metadata=metadata,
    )

    messages: list[str] = []
    if total:
        messages.append(
            f"{total} declared policy node(s) read from config, schema and manifest files: "
            + ", ".join(f"{count} {kind}" for kind, count in counts.items() if count)
            + "."
        )
    else:
        messages.append(
            "No declared policy was found. Without a schema, a settings file or a dependency "
            "manifest, data, integration and quality requirements have no evidence to rest on and "
            "will be reported as unevidenced rather than written."
        )
    if secrets:
        messages.append(
            f"{secrets} configuration key(s) name a credential; the key is kept as evidence that "
            "authentication is required and every value is redacted."
        )
    if unreadable:
        messages.append(
            f"{len(unreadable)} declaration file(s) could not be read and contributed nothing: "
            + ", ".join(sorted(unreadable)[:5])
            + "."
        )

    return CapabilityResult(
        artifacts={"graph": updated.model_dump(mode="json")},
        metrics={"declaration_nodes": total, **{f"declared_{k.lower()}": v for k, v in counts.items()}},
        messages=messages,
    )

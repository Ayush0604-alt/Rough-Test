"""Finding interaction boundaries without a syntax tree.

`BOUNDARY_KINDS` is load-bearing: `plan_clusters` seeds a packet from every boundary,
`boundary_downstream` tests reachability against them, and Gate 2 rejects any functional
requirement whose trace reaches none. The AST extractors found them syntactically — a decorator
with a literal route, a `__main__` guard, a widget call. Graphify emits no such thing, so the
signal has to come from graph *shape* instead, and the judgement from a model.

This module owns the deterministic half: which nodes are worth asking about, and what evidence
goes with each. The asking is a manifest `llm` step; the folding back into the graph is
`ClassifiedBoundaryAdapter` in `entry_points`, so every boundary in the run is created in one
place and counted by one coverage report.

## Why "nothing in this repository calls it" is the right primary signal

A handler is invoked by a framework, a runtime, or a user — never by the code around it. So on a
call graph built only from this repository, a route handler, a CLI command, a scheduled job and a
UI callback all share one property: **in-degree zero on CALLS**. That is a shape, not a framework
list, and it holds for Express, Spring, Sinatra, Flask, gin, and frameworks nobody here has heard
of. It over-collects — dead code and public library functions look identical — which is precisely
why the classifier is given a `NONE` option and told that most candidates are not boundaries.
"""

from __future__ import annotations

import re

from collections import defaultdict
from pathlib import Path
from typing import Any

from agentic.capabilities.registry import CapabilityContext, CapabilityResult, capability
from agentic.schemas.core import KnowledgeGraph

# Kinds that could plausibly *be* consumed from outside. A file or an external symbol cannot.
#
# MODULE_SURFACE is a module-level call whose result is consumed — a widget, a control, a
# top-level registration. For a script-style UI it is the *entire* interaction surface, and
# without it such a product has no boundary, no cluster, and an empty document.
CANDIDATE_KINDS = {"FUNCTION", "METHOD", "CLASS", "MODULE_SURFACE"}

# How many candidates one classification call is asked about. The reply is what overruns, not the
# prompt: each assignment is a few short fields, so this is sized against the output ceiling.
MAX_CANDIDATES = 120

# Referenced names that say nothing about how a function is reached. Not a framework list — the
# opposite: these are the language-level and container-level names that appear beside almost every
# definition, and leaving them in buries the one reference that carries the signal.
# How far from a definition or a registration a literal may sit and still describe it. A route
# decorator is one line above its function; an argparse subcommand names itself two lines above the
# `set_defaults` that registers the handler.
LITERAL_WINDOW = 3

# The most literals to carry per candidate, and the longest each may be. This is evidence, not
# source: the model is shown the strings a registration names, never the code around them.
MAX_LITERALS = 6
MAX_LITERAL_CHARS = 120

_STRING_LITERAL = re.compile(r"""(?:"([^"
]{1,200})"|'([^'
]{1,200})'|`([^`
]{1,200})`)""")

# Literals that name something other than the thing being registered: a help string, a keyword
# argument's value, a format. Filtered by shape rather than by meaning — a route, a topic, a
# command name has no spaces.
def _useful_literal(value: str) -> bool:
    value = value.strip()
    return bool(value) and " " not in value and len(value) <= MAX_LITERAL_CHARS


_UNINFORMATIVE_REFERENCES = frozenset(
    {
        "self", "cls", "this", "super", "object", "type", "str", "int", "float", "bool",
        "list", "dict", "set", "tuple", "none", "true", "false", "print", "len", "range",
        "property", "staticmethod", "classmethod", "dataclass", "override",
    }
)


def _shape(
    graph: KnowledgeGraph,
) -> tuple[dict[str, int], dict[str, int], dict[str, list[str]], dict[str, list[str]]]:
    """In-degree and out-degree on CALLS, plus the external names each node references.

    The external-reference list is where a decorator or annotation shows up. Graphify emits
    `@app.post(...)` as a `references` edge from the decorated function to an EXTERNAL_SYMBOL
    named `post`, and `@GetMapping` the same way — so the names a candidate references are exactly
    the evidence a reader would use, handed over without this module having to interpret any of
    them.
    """
    entities = graph.by_id()
    inbound_calls: dict[str, int] = defaultdict(int)
    outbound_calls: dict[str, int] = defaultdict(int)
    references: dict[str, list[str]] = defaultdict(list)
    registered_by: dict[str, list[str]] = defaultdict(list)

    for relationship in graph.relationships:
        if relationship.kind == "CALLS":
            inbound_calls[relationship.target] += 1
            outbound_calls[relationship.source] += 1
        elif relationship.kind == "REGISTERS":
            # Handed somewhere as a value rather than invoked. The whole point of the distinction:
            # a registered handler is reached from outside even though something in this
            # repository names it, so it must stay a candidate.
            source = entities.get(relationship.source)
            if source is not None:
                registered_by[relationship.target].append(source.name.strip(". ()"))
        elif relationship.kind in {"REFERENCES", "INHERITS"}:
            target = entities.get(relationship.target)
            if target is None or target.kind != "EXTERNAL_SYMBOL":
                continue
            name = target.name.strip(". ()")
            if name and name.casefold() not in _UNINFORMATIVE_REFERENCES:
                references[relationship.source].append(name)

    return inbound_calls, outbound_calls, references, registered_by


class _SourceWindow:
    """Reads the string literals near a line, with each file read at most once.

    The one place this step touches source. It is bounded to `LITERAL_WINDOW` lines around a
    definition or a registration and yields only quoted literals, never the code — a route, a
    topic, a command name. Without it a boundary cannot be named at all: the extractor records
    that a handler references `post`, never the `"/orders"` handed to it, so every endpoint would
    be published as the function that serves it and the document could not say what a client
    calls.
    """

    def __init__(self, source_root: Path) -> None:
        self._root = source_root
        self._cache: dict[str, tuple[str, ...]] = {}

    def _lines(self, relative: str) -> tuple[str, ...]:
        if relative not in self._cache:
            try:
                text = (self._root / relative).read_text(encoding="utf-8-sig", errors="ignore")
                self._cache[relative] = tuple(text.splitlines())
            except OSError:
                self._cache[relative] = ()
        return self._cache[relative]

    def literals_near(self, relative: str, line: int) -> list[str]:
        lines = self._lines(relative)
        if not lines or line < 1:
            return []
        start = max(0, line - 1 - LITERAL_WINDOW)
        end = min(len(lines), line + LITERAL_WINDOW)
        found: list[str] = []
        for text in lines[start:end]:
            for match in _STRING_LITERAL.finditer(text):
                value = next(group for group in match.groups() if group is not None)
                if _useful_literal(value) and value not in found:
                    found.append(value)
        return found


@capability("propose_boundary_candidates")
def propose_boundary_candidates(ctx: CapabilityContext) -> CapabilityResult:
    """Select the nodes worth classifying, and attach the shape evidence for each."""
    graph = ctx.load("graph", KnowledgeGraph)
    entities = graph.by_id()
    inbound_calls, outbound_calls, references, registered_by = _shape(graph)
    window = _SourceWindow(ctx.source_root)

    container_of: dict[str, str] = {}
    # Where each candidate is registered, when something registers it. A Go handler is named at
    # the `http.HandleFunc` call inside `main`, nowhere near its own definition, so the literal
    # that identifies it can only be found at the registration site.
    registration_sites: dict[str, list[tuple[str, int]]] = defaultdict(list)
    for relationship in graph.relationships:
        if relationship.kind == "CONTAINS":
            container_of[relationship.target] = relationship.source
        elif relationship.kind == "REGISTERS":
            for location in relationship.evidence:
                if location.file and location.line_start:
                    registration_sites[relationship.target].append(
                        (location.file, location.line_start)
                    )

    candidates: list[dict[str, Any]] = []
    for entity in graph.entities:
        if entity.kind not in CANDIDATE_KINDS or entity.location is None:
            continue
        inbound = inbound_calls.get(entity.id, 0)
        referenced = sorted(set(references.get(entity.id, [])))
        registrars = sorted(set(registered_by.get(entity.id, [])))
        # Uncalled means framework-invoked or dead — either way, worth a question. A *called*
        # function is worth one too when something also registers it or it carries a registration
        # reference: a handler can be both decorated and called directly from a test, and a
        # subcommand is both registered by its parser and, to Graphify, referenced from it.
        if inbound and not referenced and not registrars:
            continue
        container = entities.get(container_of.get(entity.id, ""))

        # The strings a registration names, from the definition's own header and from wherever it
        # is registered. This is what lets a boundary be called "POST /orders" rather than the
        # name of the function behind it.
        literals: list[str] = list((entity.attributes or {}).get("literals") or [])
        if entity.location and entity.location.file and entity.location.line_start:
            literals.extend(
                window.literals_near(entity.location.file, entity.location.line_start)
            )
        for file, line in registration_sites.get(entity.id, []):
            for value in window.literals_near(file, line):
                if value not in literals:
                    literals.append(value)
        candidates.append(
            {
                "qualified_name": entity.qualified_name,
                "name": entity.name,
                "kind": entity.kind,
                "at": entity.location.compact(),
                "in": container.qualified_name if container else None,
                "inbound_calls": inbound,
                "outbound_calls": outbound_calls.get(entity.id, 0),
                "references_undefined_names": referenced,
                "registered_by": registrars,
                # For a module-level control this is the call itself — `st.button`,
                # `gr.Interface` — which is what says who is being addressed and how.
                "call": (entity.attributes or {}).get("call"),
                "module_level": bool((entity.attributes or {}).get("module_level")),
                "literals_nearby": literals[:MAX_LITERALS],
            }
        )

    # A candidate nothing calls but which calls a lot is a likelier entry point than a leaf, and a
    # candidate carrying registration references is likelier still. Ordering by that keeps the
    # most probable boundaries inside the cap.
    candidates.sort(
        key=lambda item: (
            -len(item["references_undefined_names"]),
            -len(item["registered_by"]),
            -item["outbound_calls"],
            item["qualified_name"],
        )
    )
    truncated = len(candidates) > MAX_CANDIDATES

    return CapabilityResult(
        artifacts={
            "boundary_candidates": {
                "candidates": candidates[:MAX_CANDIDATES],
                "considered": len(candidates),
                "truncated": truncated,
            }
        },
        metrics={"candidates": min(len(candidates), MAX_CANDIDATES), "truncated": truncated},
        messages=(
            [f"{len(candidates)} boundary candidates, capped at {MAX_CANDIDATES}"]
            if truncated
            else []
        ),
    )

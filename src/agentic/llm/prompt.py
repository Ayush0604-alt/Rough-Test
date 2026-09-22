"""Prompt assembly — where skill files stop being documentation and become behaviour.

The system prompt is built from the pack's master skill plus the step's own skill, loaded from
disk at call time. Nothing here contains hard-coded domain instructions; changing what the model
is told means editing a `SKILL.md`, not this module.

The user message is an *evidence packet*, never source code. That is what keeps cost linear in the
number of interaction boundaries rather than in the size of the codebase.
"""

from __future__ import annotations

import json
from typing import Any, Callable

from agentic.capabilities.graph_ops import GraphIndex
from agentic.capabilities.registry import CapabilityContext
from agentic.schemas.core import GateReport, KnowledgeGraph, Severity

MAX_ARTIFACT_CHARS = 60_000

# How many rejected test cases one repair call is asked to return. Sized against the model's
# output ceiling, not its input: the reply is what overruns.
MAX_REPAIR_CASES = 12

# Declared documentation is evidence of intent, not a document to reproduce. These bounds keep a
# long README from crowding out the graph evidence it is meant to contextualise.
DOC_FILES = 4
DOC_OUTLINE_LINES = 40

# Module docstrings state intent too, and are otherwise never reached by a workflow walk — see
# graphify.md §15. Capped separately from README/manifest documentation so the two lists add
# together instead of one crowding the other out of `DOC_FILES`.
MODULE_INTENT_FILES = 20


def build_system_prompt(ctx: CapabilityContext) -> str:
    """Master skill prose + step skill prose + any step-specific instruction."""
    manifest = ctx.payload.get("manifest")
    parts: list[str] = []

    if manifest is not None and manifest.master_skill:
        registry = manifest.skill_registry()
        master = registry.load(manifest.master_skill)
        parts.append(
            f"# Governing workflow — {master.name}\n\n"
            "These rules apply to everything you produce in this run.\n\n"
            f"{master.body}"
        )

    skill = ctx.skill
    body = skill.section(ctx.step.section) if ctx.step.section else skill.body
    parts.append(f"# Your task — {skill.name}\n\n{body}")

    if ctx.step.instructions:
        parts.append(f"# Step-specific instruction\n\n{ctx.step.instructions.strip()}")

    parts.append(
        "# Output\n\n"
        "Return only data conforming to the supplied schema. Populate a field only when the "
        "supplied evidence supports it; omitting a claim is always preferable to inventing one. "
        "Cite only file paths and line numbers that appear verbatim in the evidence you were "
        "given, and use only exact qualified names from it in any graph path.\n\n"
        "In an evidence citation, `symbol` means a bare identifier that appears on the cited "
        "line itself — the part after the last colon of an `at` value. It is not the enclosing "
        "definition (the `in` field), not a qualified path, and never a description or a label. "
        "If you are not copying such an identifier, leave `symbol` out: an omitted symbol costs "
        "the citation nothing, and a wrong one invalidates it."
    )
    return "\n\n---\n\n".join(parts)


# Per-artifact rendering. A new pack registers its own renderer here; anything unregistered falls
# back to bounded JSON, so an unfamiliar artifact degrades rather than blowing the context window.
ArtifactRenderer = Callable[[CapabilityContext, str], Any]
_RENDERERS: dict[str, ArtifactRenderer] = {}


def renders(name: str) -> Callable[[ArtifactRenderer], ArtifactRenderer]:
    def decorate(fn: ArtifactRenderer) -> ArtifactRenderer:
        _RENDERERS[name] = fn
        return fn

    return decorate


# Per-fan-out-target rendering of the one work item a fan-out worker receives. Registering a name
# here is optional: an unregistered `fan_out` target (currently `clusters` and testgen's own
# `test_batches`) keeps rendering as a "cluster" packet, which is what both were shaped to look
# like before this registry existed. A target only needs an entry when its work item is *not*
# shaped that way — `boundary_candidate_batches` below is the first one that isn't.
WorkItemRenderer = Callable[[dict[str, Any]], dict[str, Any]]
_WORK_ITEM_RENDERERS: dict[str, WorkItemRenderer] = {}


def renders_work_item(fan_out_name: str) -> Callable[[WorkItemRenderer], WorkItemRenderer]:
    def decorate(fn: WorkItemRenderer) -> WorkItemRenderer:
        _WORK_ITEM_RENDERERS[fan_out_name] = fn
        return fn

    return decorate


@renders_work_item("boundary_candidate_batches")
def _render_boundary_batch_work_item(work_item: dict[str, Any]) -> dict[str, Any]:
    """One `classify_boundaries` worker's slice: a batch of candidates, not a cluster."""
    return {
        "candidates": work_item.get("candidates") or [],
        "batch": {
            "batch_id": work_item.get("batch_id"),
            "batch_count": work_item.get("batch_count"),
            "considered": work_item.get("considered"),
        },
        "note": (
            "`inbound_calls` counts callers inside this repository only. Zero means nothing here "
            "calls it — a framework, a runtime, or a person might, or it may simply be unused. "
            "`references_undefined_names` are names the file uses but does not define, which is "
            "where a decorator, annotation, or registration call appears."
        ),
    }


@renders("graph")
def _render_graph(ctx: CapabilityContext, name: str) -> Any:
    """A summary only.

    The whole graph must never enter a prompt — that is precisely the cost failure this
    architecture exists to prevent. Detailed evidence arrives through cluster packets instead.
    """
    graph = ctx.load(name, KnowledgeGraph)
    extraction = graph.metadata.get("extraction") or {}
    kinds: dict[str, int] = {}
    for entity in graph.entities:
        kinds[entity.kind] = kinds.get(entity.kind, 0) + 1
    # Declared documentation, which no workflow walk reaches: a README hangs off a file node by a
    # containment edge, so the clearest statement a project makes about its own purpose never
    # arrived anywhere a model could read it. The skill's evidence precedence puts declared
    # documentation first for *purpose* — while still requiring code to confirm any capability —
    # so the step that writes the purpose is exactly where it belongs.
    documentation = [
        {
            "file": entity.qualified_name,
            "outline": (entity.attributes.get("content_outline") or [])[:DOC_OUTLINE_LINES],
        }
        for entity in graph.entities
        if entity.kind == "FILE" and entity.attributes.get("content_outline")
    ][:DOC_FILES]

    # Module-level docstrings, carried onto FILE entities by `adapt_graphify_graph` from a
    # `rationale_for` edge. Ranked by graph degree rather than encountered order, so the modules
    # most connected to the rest of the system win the capped slots rather than the first N found.
    index = GraphIndex(graph)
    module_intent = [
        {"file": entity.qualified_name, "docstring": entity.attributes["docstring"]}
        for entity in sorted(
            (e for e in graph.entities if e.kind == "FILE" and e.attributes.get("docstring")),
            key=lambda e: index.degree(e.id),
            reverse=True,
        )
    ][:MODULE_INTENT_FILES]

    return {
        "repository_name": graph.repository_name,
        "entity_count": len(graph.entities),
        "relationship_count": len(graph.relationships),
        "entity_kinds": dict(sorted(kinds.items())),
        "extraction_coverage": extraction.get("extraction_coverage"),
        "ui_surface": graph.metadata.get("ui_surface") or {},
        "declared_documentation": documentation,
        "module_intent": module_intent,
        "note": (
            "Summary only. Detailed evidence is in the cluster packets. Declared documentation "
            "states intent and may establish purpose, but only a reachable code path confirms "
            "that a capability exists."
        ),
    }


@renders("clusters")
def _render_clusters(ctx: CapabilityContext, name: str) -> Any:
    """Boundary titles only — the packets themselves are dispatched to fan-out workers."""
    clusters = ctx.load(name)
    return [
        {"boundary": item["boundary"], "kind": item["boundary_kind"]} for item in clusters
    ]


@renders("test_batches")
def _render_test_batches(ctx: CapabilityContext, name: str) -> Any:
    """Batch identities only — the packets themselves are dispatched to fan-out workers.

    Without this the whole batch list, every requirement in it, would be pasted into every worker's
    prompt alongside the one packet it was actually given. That is the same cost failure the
    clusters renderer exists to prevent.
    """
    batches = ctx.load(name)
    return [
        {"batch": item.get("batch_id"), "area": item.get("area_name") or item.get("area")}
        for item in batches
    ]


@renders("requirement_gate")
def _render_gate(ctx: CapabilityContext, name: str) -> Any:
    """Only the blocking issues. A repair step needs what failed, not what passed."""
    report = ctx.load(name, GateReport)
    return {
        "valid": report.valid,
        "errors": report.errors,
        "rejected_items": [
            {"item": issue.subject_id, "code": issue.code, "problem": issue.message}
            for issue in report.issues
            if issue.severity == Severity.ERROR
        ],
    }


@renders("brd")
def _render_brd(ctx: CapabilityContext, name: str) -> Any:
    """Only the claims a gate rejected, never the whole requirements set.

    `brd` reaches a prompt in exactly one place: the repair step. Handing it the finished document
    was ruinous at real scale. On input it blew past the artifact cap and arrived as a truncated
    preview, so the model was asked to fix a document it could only partly see; on output the
    step was expected to re-emit every requirement inside one bounded reply, which is
    arithmetically impossible past a few dozen. The document came back a fraction of its size and
    nothing recorded the loss -- the same failure that made the old merge step unusable.

    Repair now sees the rejected items and nothing else. Everything that passed is carried across
    by `apply_repairs` without a model touching it.
    """
    from agentic.schemas.brd import ProductBRD

    brd = ctx.load(name, ProductBRD)
    rejected: set[str] = set()
    try:
        report = ctx.load("requirement_gate", GateReport)
    except (KeyError, OSError, ValueError):
        report = None
    if report is not None:
        rejected = {
            issue.subject_id
            for issue in report.issues
            if issue.severity == Severity.ERROR and issue.subject_id
        }

    items = [item for item in brd.all_items() if item.id in rejected]
    return {
        "items_to_fix": [
            item.model_dump(mode="json", exclude={"parent_id"}) for item in items
        ],
        "note": (
            f"These are the {len(items)} rejected claim(s) out of {len(brd.all_items())} in the "
            "document. Every other claim passed and is kept exactly as it is -- do not return "
            "them, and do not attempt to restate the document."
        ),
    }


@renders("test_suite")
def _render_test_suite(ctx: CapabilityContext, name: str) -> Any:
    """Only the cases the gate rejected, never the whole suite.

    `test_suite` reaches a prompt in exactly one place: the repair step. Handing it the finished
    suite would repeat the failure `_render_brd` exists to prevent — on input it blows the artifact
    cap and arrives truncated, so the model is asked to fix a suite it can only partly see; on
    output it would have to re-emit every case inside one bounded reply, which past a few dozen
    cannot be done. Everything that passed is carried across by `apply_test_repairs` without a
    model touching it.
    """
    from agentic.schemas.testspec import TestSuite

    suite = ctx.load(name, TestSuite)
    rejected: dict[str, list[str]] = {}
    try:
        report = ctx.load("test_gate", GateReport)
    except (KeyError, OSError, ValueError):
        report = None
    if report is not None:
        for issue in report.issues:
            if issue.severity == Severity.ERROR and issue.subject_id:
                rejected.setdefault(issue.subject_id, []).append(
                    f"{issue.code}: {issue.message}"
                )

    cases = [case for case in suite.cases() if case.id in rejected]
    # Bounded per call. A run rejecting thirty-nine cases asked for thirty-nine full replies in one
    # bounded reply and got six back: the model hits the output ceiling and silently truncates,
    # and the cases it never reached are then pruned as unrepairable although nothing ever tried.
    # Successive passes pick up whatever is still rejected, so nothing is skipped -- it waits.
    shown = cases[:MAX_REPAIR_CASES]
    remaining = len(cases) - len(shown)
    note = (
        f"These are {len(shown)} of the {len(cases)} rejected case(s), out of {len(suite.cases())} "
        "in the suite. Every other case passed and is kept exactly as it is -- do not return them, "
        "and do not attempt to restate the suite."
    )
    if remaining:
        note += (
            f" The other {remaining} rejected case(s) are handled on a later pass; ignore them "
            "now and fix these properly rather than rushing all of them."
        )
    return {
        "cases_to_fix": [
            {
                **case.model_dump(
                    mode="json",
                    include={
                        "id", "title", "objective", "preconditions", "inputs", "steps",
                        "expected_results", "derivation", "assumptions", "dimension", "notes",
                    },
                ),
                "problems": rejected[case.id],
            }
            for case in shown
        ],
        "note": note,
    }


def render_artifact(ctx: CapabilityContext, name: str) -> Any:
    renderer = _RENDERERS.get(name)
    if renderer is not None:
        return renderer(ctx, name)
    value = ctx.load(name)
    encoded = json.dumps(value, default=str)
    if len(encoded) > MAX_ARTIFACT_CHARS:
        return {
            "truncated": True,
            "reason": f"artifact {name!r} exceeded {MAX_ARTIFACT_CHARS} characters",
            "preview": encoded[:MAX_ARTIFACT_CHARS],
        }
    return value


def build_user_payload(ctx: CapabilityContext) -> dict[str, Any]:
    """Assemble the evidence this step is allowed to reason over."""
    payload: dict[str, Any] = {"repository": ctx.state.get("repository_name", "")}

    work_item = ctx.payload.get("work_item")
    if work_item:
        renderer = _WORK_ITEM_RENDERERS.get(ctx.step.fan_out or "")
        if renderer is not None:
            payload.update(renderer(work_item))
        else:
            payload["cluster"] = {
                "boundary": work_item.get("boundary"),
                "boundary_kind": work_item.get("boundary_kind"),
                "boundary_location": work_item.get("boundary_location"),
                # Boundaries sharing one workflow are merged into a single packet. Naming them
                # keeps the model from writing about the first one only and dropping the rest.
                "also_exposed_by": work_item.get("also_exposed_by") or [],
                "evidence": work_item.get("packet"),
            }

    for name in ctx.step.consumes:
        # `fragments` is a state channel written by fan-out workers, not a file on disk.
        if name == "fragments":
            payload["cluster_findings"] = ctx.state.get("fragments") or []
            continue
        try:
            payload[name] = render_artifact(ctx, name)
        except (KeyError, OSError, ValueError) as exc:
            payload[name] = {"unavailable": str(exc)}

    return payload


def build_messages(ctx: CapabilityContext) -> list[tuple[str, str]]:
    payload = build_user_payload(ctx)
    return [
        ("system", build_system_prompt(ctx)),
        ("human", json.dumps(payload, separators=(",", ":"), ensure_ascii=False, default=str)),
    ]

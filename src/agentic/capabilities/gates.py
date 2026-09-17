"""The two deterministic validation gates.

Both are pure checks against facts already established — the graph, the requirements, and the file
system. No model reasoning happens here by design: a gate that could be argued with is not a gate.

Errors block; warnings do not, but they must reach the gap analysis rather than being dropped.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any

from agentic.capabilities.graph_ops import BOUNDARY_KINDS, GraphIndex
from agentic.capabilities.jargon import describe, find_jargon
from agentic.capabilities.registry import CapabilityContext, CapabilityResult, capability
from agentic.capabilities.verify import verify_location
from agentic.schemas.brd import (
    BASIS_TYPES,
    SECTION_TIERS,
    BRDItem,
    ProductBRD,
    canonical_basis,
    canonical_tier,
    permitted_parents,
    permitted_relations,
    title_key,
)
from agentic.schemas.core import GateReport, KnowledgeGraph, Severity, ValidationIssue

AMBIGUITY_WARNING_RATIO = 0.30

# Below this share of files yielding a definition, the graph is thin enough that the document will
# under-report and should say so. Declaration-style code — a routes file, a Sinatra app, a
# declarative pipeline — is the usual cause: the extractor keys on named definitions and such a
# file has none.
LOW_COVERAGE_RATIO = 0.50

# Boundary classification is a model judgement now rather than a parsed route, so the share of
# boundaries resting on it is the honest successor to the parse-coverage signal this replaces.
INFERRED_BOUNDARY_WARNING_RATIO = 0.50

# Families whose statements may name an external product or service, because naming one is the
# point of the requirement rather than a leak of implementation detail.
NAMES_EXTERNAL_SYSTEMS = {
    "integration_requirements",
    "non_functional_requirements",
    "data_requirements",
}
LOW_CONFIDENCE = 0.70


def _issue(code: str, severity: Severity, message: str, **kwargs: Any) -> ValidationIssue:
    return ValidationIssue(code=code, severity=severity, message=message, **kwargs)


# --------------------------------------------------------------------------------------------
# Gate 1 — graph validation
# --------------------------------------------------------------------------------------------


@capability("validate_graph")
def validate_graph(ctx: CapabilityContext) -> CapabilityResult:
    """Structural integrity of the extracted graph."""
    graph = ctx.load("graph", KnowledgeGraph)
    source_root = ctx.source_root
    issues: list[ValidationIssue] = []
    checks = 0

    # Duplicate ids and dangling edges are no longer checked here. One adapter now mints every id
    # from `stable_id` and builds every edge from its own id map, so both conditions are defects in
    # that adapter rather than findings about the repository — and a gate that can only ever fire
    # on a bug in the step before it is a unit test wearing a gate's clothes. They are asserted in
    # `graphify._assert_structurally_sound`, which raises instead of reporting.
    checks += 1
    for relationship in graph.relationships:
        if relationship.source == relationship.target:
            issues.append(
                _issue(
                    "SELF_RELATIONSHIP",
                    Severity.WARNING,
                    "Relationship starts and ends at the same entity",
                    relationship_id=relationship.id,
                )
            )

    checks += 1
    for entity in graph.entities:
        if entity.location is None:
            continue
        check = verify_location(entity.location, source_root)
        if not check.ok:
            issues.append(
                _issue(
                    "UNRESOLVABLE_LOCATION",
                    Severity.ERROR,
                    f"Entity {entity.qualified_name}: {check.message}",
                    entity_id=entity.id,
                    location=entity.location,
                )
            )

    checks += 1
    inbound: dict[str, list[str]] = defaultdict(list)
    for relationship in graph.relationships:
        inbound[relationship.target].append(relationship.kind)
    for entity in graph.entities:
        # Every boundary kind, not just ENDPOINT. A boundary with nothing exposing it is a
        # declaration with no behaviour behind it, and the packet built from it is a single node
        # — which is as true of a scheduled job or a subcommand as it ever was of a route.
        if entity.kind in BOUNDARY_KINDS and "EXPOSES" not in inbound[entity.id]:
            issues.append(
                _issue(
                    "ORPHAN_BOUNDARY",
                    Severity.ERROR,
                    f"Boundary {entity.qualified_name} has no exposing handler",
                    entity_id=entity.id,
                    location=entity.location,
                )
            )

    checks += 1
    extraction = graph.metadata.get("extraction")
    reported = extraction if isinstance(extraction, dict) else {}
    # `python_files` / `parsed_files` / `parse_coverage` are gone with the Python-only extractor,
    # and so are the three checks built on them. `NO_ANALYSABLE_SOURCE` and `NO_FILES_PARSED` both
    # asserted "no Python here means nothing to analyse", which is false once extraction covers 36
    # languages; ingestion now fails closed on the far narrower condition of no parseable source
    # at all, which is where that judgement belongs.
    extraction_coverage = float(reported.get("extraction_coverage", 1.0))
    if extraction is not None and extraction_coverage < LOW_COVERAGE_RATIO:
        issues.append(
            _issue(
                "LOW_EXTRACTION_COVERAGE",
                Severity.WARNING,
                f"Only {extraction_coverage:.0%} of files yielded any definition; capabilities "
                "in the remainder will be under-reported",
            )
        )

    checks += 1
    ambiguous = sum(item.provenance.value == "AMBIGUOUS" for item in graph.relationships)
    ratio = ambiguous / len(graph.relationships) if graph.relationships else 0.0
    if ratio > AMBIGUITY_WARNING_RATIO:
        issues.append(
            _issue(
                "HIGH_AMBIGUITY",
                Severity.WARNING,
                f"{ratio:.0%} of relationships are ambiguous — extraction is resolving less than "
                "it is guessing",
            )
        )

    checks += 1
    if not any(entity.kind == "FILE" for entity in graph.entities):
        issues.append(
            _issue("NO_FILE_ENTITIES", Severity.ERROR, "Graph contains no source files")
        )

    checks += 1
    index = GraphIndex(graph)
    boundaries = index.boundary_ids()
    if not boundaries:
        # Not an error: a library legitimately has no boundary. The synthesis step must say so
        # rather than inventing one, and the gap analysis carries the warning.
        issues.append(
            _issue(
                "NO_INTERACTION_BOUNDARY",
                Severity.WARNING,
                "No interaction boundary was found; this may be a library rather than an "
                "application",
            )
        )

    checks += 1
    boundary_entities = [entity for entity in graph.entities if entity.kind in BOUNDARY_KINDS]
    inferred_boundaries = sum(
        entity.provenance.value == "INFERRED" for entity in boundary_entities
    )
    inferred_share = (
        inferred_boundaries / len(boundary_entities) if boundary_entities else 0.0
    )
    if inferred_share > INFERRED_BOUNDARY_WARNING_RATIO:
        # Not an error. Under Graphify this is the *normal* state — nothing in its output marks a
        # route, so every boundary is classified rather than parsed. The warning exists so the gap
        # analysis says which document rests on judgement and which on syntax, rather than letting
        # a reader assume the latter.
        issues.append(
            _issue(
                "INFERRED_BOUNDARY_SHARE",
                Severity.WARNING,
                f"{inferred_share:.0%} of interaction boundaries were classified rather than "
                "parsed; requirements resting on them inherit that uncertainty",
            )
        )

    kind_counts = Counter(entity.kind for entity in graph.entities)
    report = GateReport.from_issues(
        "gate_graph",
        issues,
        checks,
        {
            "entity_count": len(graph.entities),
            "relationship_count": len(graph.relationships),
            "entity_kinds": dict(sorted(kind_counts.items())),
            "relationship_kinds": dict(
                sorted(Counter(item.kind for item in graph.relationships).items())
            ),
            "extraction_coverage": extraction_coverage,
            "ambiguity_ratio": round(ratio, 4),
            "interaction_boundaries": len(boundaries),
            "inferred_boundary_share": round(inferred_share, 4),
        },
    )
    return CapabilityResult(
        artifacts={"graph_gate": report},
        gate=report,
        metrics={"errors": report.errors, "warnings": report.warnings},
    )


# --------------------------------------------------------------------------------------------
# Gate 2 — requirement validation
# --------------------------------------------------------------------------------------------


def _normalise(statement: str) -> str:
    return " ".join(statement.split()).casefold()


# How alike two statements may read before an outcome is judged to be its capability reworded.
#
# Measured, not guessed. Against the pairs that motivated this check, the two restatements in the
# reference output score 0.43 and 0.47, while genuine outcome/capability pairs over the same
# evidence score 0.12, 0.20, and 0.33. 0.40 separates them, but with only 0.07 of headroom above
# the worst genuine pair — which is precisely why this produces a warning a person reads and not a
# rejection. A borderline pair costs a line in the gap analysis, never a dropped requirement.
RESTATEMENT_SIMILARITY = 0.40

# Words carried by almost every "shall" statement, so shared ones say nothing about shared meaning.
_FRAME_WORDS = frozenset(
    """a an and are as at be been by can for from in into is it its of on or shall should so
    that the their them they this to when which who will with able system user users product""".split()
)


def _content_words(statement: str) -> set[str]:
    words = {word.strip(".,;:'\"()") for word in _normalise(statement).split()}
    return {word for word in words if word and word not in _FRAME_WORDS}


def _restates(parent: BRDItem, child: BRDItem) -> bool:
    """True when a business requirement is its one functional requirement in other words.

    Two conditions must hold together, because either alone is a legitimate pattern. Citing the
    same lines is *expected* — that is what grounds the outcome rather than leaving it aspirational
    — and two claims about one workflow naturally share vocabulary. What is not legitimate is both
    at once: the same evidence described in the same words at two tiers is one finding published
    twice, and it inflates every count that measures the document by item.
    """
    parent_spans = {(loc.file, loc.line_start, loc.line_end) for loc in parent.evidence}
    child_spans = {(loc.file, loc.line_start, loc.line_end) for loc in child.evidence}
    if not parent_spans or parent_spans != child_spans:
        return False

    parent_words = _content_words(parent.statement)
    child_words = _content_words(child.statement)
    if not parent_words or not child_words:
        return False
    shared = len(parent_words & child_words)
    return shared / min(len(parent_words), len(child_words)) >= RESTATEMENT_SIMILARITY


@capability("validate_requirements")
def validate_requirements(ctx: CapabilityContext) -> CapabilityResult:
    """Re-check the synthesised claims independently of how they were produced.

    Deliberately does not trust the synthesis step's own self-assessment: it re-resolves every
    citation against the file system and every graph path against the graph.
    """
    brd = ctx.load("brd", ProductBRD)
    graph = ctx.load("graph", KnowledgeGraph)
    source_root = ctx.source_root
    index = GraphIndex(graph)
    by_qualified = graph.by_qualified_name()
    boundaries = index.boundary_ids()
    downstream = index.boundary_downstream()
    known_symbols = {
        entity.qualified_name
        for entity in graph.entities
        if entity.kind in {"FUNCTION", "METHOD", "CLASS", "MODULE"}
    }

    issues: list[ValidationIssue] = []
    checks = 0

    sections = brd.sections()
    all_items = brd.all_items()

    # --- whole-set checks -------------------------------------------------------------------
    checks += 1
    if brd.product_purpose is None:
        # Valid, but only when stated. An empty purpose that merely looks confident is not.
        issues.append(
            _issue(
                "NO_PRODUCT_PURPOSE",
                Severity.WARNING,
                "No product purpose was established; the document must say so explicitly",
            )
        )

    checks += 1
    if not brd.business_requirements:
        issues.append(
            _issue(
                "NO_BUSINESS_REQUIREMENT",
                Severity.WARNING,
                "No business requirement could be grounded; the document must explain why",
            )
        )

    checks += 1
    seen_statements: dict[str, str] = {}
    for item in all_items:
        key = _normalise(item.statement)
        if not key:
            continue
        if key in seen_statements:
            issues.append(
                _issue(
                    "DUPLICATE_STATEMENT",
                    Severity.WARNING,
                    f"{item.id} repeats the statement already published as "
                    f"{seen_statements[key]}",
                    subject_id=item.id,
                )
            )
        else:
            seen_statements[key] = item.id

    # --- the classification chain ------------------------------------------------------------
    # Business requirement -> functional requirement -> business rule. A tier label with nothing
    # linking it to the tier above is the failure these checks exist for: two claims can both be
    # typed correctly while the document never says which outcome the capability serves.

    checks += 1
    for section, items in sections.items():
        if section == "business_requirements":
            continue
        for item in items:
            if item.parent and not item.parent_id:
                # A warning, not a rejection. A dangling parent is a defect in the hierarchy, not
                # in the claim: the requirement itself was cited, traced, and boundary-checked,
                # and the thing it pointed at was removed by something else. Treated as an error
                # it cost the *child* its place — one rejected claim taking a grounded one down
                # with it, and then that one's children in turn. The link is cleared by the final
                # structural pass, which is the proportionate repair.
                issues.append(
                    _issue(
                        "UNRESOLVED_PARENT",
                        Severity.WARNING,
                        f"{item.id} names a parent requirement that is not published: "
                        f"{item.parent!r}; the link is cleared and the item published unattached",
                        subject_id=item.id,
                    )
                )

    checks += 1
    # The two classification fields must agree. They cannot disagree by accident -- consolidation
    # derives one from the other -- so a mismatch here means something downstream rewrote a type
    # without revisiting the grounds for it, which is exactly how over-extraction crept back in
    # before the basis existed.
    for section, items in brd.every_section().items():
        for item in items:
            basis = canonical_basis(item.abstraction_basis)
            if basis is None:
                issues.append(
                    _issue(
                        "NO_ABSTRACTION_BASIS",
                        Severity.WARNING,
                        f"{item.id} does not say on what grounds it is a requirement rather than "
                        "an implementation detail",
                        subject_id=item.id,
                    )
                )
                continue
            expected = BASIS_TYPES[basis]
            actual = canonical_tier(item.requirement_type, default=expected)
            if actual != expected:
                issues.append(
                    _issue(
                        "CLASSIFICATION_INCOHERENT",
                        Severity.ERROR,
                        f"{item.id} is classified {actual!r} but its grounds are {basis!r}, "
                        f"which makes it {expected!r}",
                        subject_id=item.id,
                    )
                )

    checks += 1
    # A hierarchy has to mean something. A link whose relation the two levels cannot hold is code
    # proximity wearing a parent's clothes, and publishing it tells a reader the structure was
    # reasoned about when it was measured.
    # The parent's tier comes from the section holding it, not from its `requirement_type`.
    # Reading the field and defaulting to "functional" made every unclassified outcome look like
    # a capability, so a perfectly ordinary link was rejected as impossible.
    by_title: dict[str, tuple[BRDItem, str]] = {}
    for holding, held in brd.every_section().items():
        holding_tier = SECTION_TIERS.get(holding, "functional")
        for entry in held:
            by_title.setdefault(
                title_key(entry.title),
                (entry, canonical_tier(entry.requirement_type, default=holding_tier)),
            )
    for section, items in brd.every_section().items():
        tier = SECTION_TIERS.get(section, "functional")
        for item in items:
            if not item.parent:
                continue
            found = by_title.get(title_key(item.parent))
            relation = (item.parent_relation or "").strip().lower()
            if found is None:
                continue  # reported separately as an unresolved parent
            parent, parent_tier = found
            if parent_tier not in permitted_parents(tier):
                issues.append(
                    _issue(
                        "PARENT_TIER_NOT_PERMITTED",
                        Severity.ERROR,
                        f"{item.id} ({tier}) claims {parent.id} ({parent_tier}) as its parent, "
                        "which is not a relationship those levels can hold",
                        subject_id=item.id,
                    )
                )
            elif relation and relation not in permitted_relations(tier):
                issues.append(
                    _issue(
                        "PARENT_RELATION_NOT_PERMITTED",
                        Severity.ERROR,
                        f"{item.id} claims to {relation!r} its parent, which a {tier} cannot do",
                        subject_id=item.id,
                    )
                )

    checks += 1
    # An unattached claim is a warning, not a rejection. The claim itself is grounded — it cited
    # real lines and reached a boundary — so deleting it over a missing link would discard checked
    # evidence. What it costs is the reader's ability to see why the product does this, and that
    # is exactly what a warning is for.
    for section, code, description in (
        (
            "functional_requirements",
            "FUNCTION_WITHOUT_BUSINESS_REQUIREMENT",
            "realises no business requirement, so the document does not say why this capability "
            "exists",
        ),
        (
            "business_rules",
            "RULE_WITHOUT_FUNCTION",
            "constrains no functional requirement, so the document does not say what this policy "
            "applies to",
        ),
    ):
        for item in brd.orphans_in(section):
            issues.append(
                _issue(code, Severity.WARNING, f"{item.id} {description}", subject_id=item.id)
            )

    checks += 1
    for requirement in brd.business_requirements:
        children = brd.children_of(requirement.id, "functional_requirements")
        if not children:
            issues.append(
                _issue(
                    "BUSINESS_REQUIREMENT_WITHOUT_FUNCTION",
                    Severity.WARNING,
                    f"{requirement.id} has no functional requirement realising it, so it is an "
                    "outcome the document never shows the product delivering",
                    subject_id=requirement.id,
                )
            )
        elif len(children) == 1 and _restates(requirement, children[0]):
            # The failure the three-tier model is meant to make visible: an outcome that is its
            # one capability reworded. Both are individually grounded and neither is wrong, so
            # this is reported rather than dropped — but published side by side it reads as two
            # findings where the evidence supports one.
            issues.append(
                _issue(
                    "BUSINESS_REQUIREMENT_RESTATES_FUNCTION",
                    Severity.WARNING,
                    f"{requirement.id} restates its only functional requirement "
                    f"({children[0].id}) rather than standing over it as an outcome",
                    subject_id=requirement.id,
                )
            )

    # --- per-item checks --------------------------------------------------------------------
    checks += 6
    # A business requirement, a capability, and a criterion all assert that something is reachable
    # from outside, so each must trace to a boundary. The supplementary families do not: a schema
    # invariant is enforced beneath every boundary rather than at one, and an integration is
    # depended upon rather than exposed. They are still fully citation-checked below — every
    # family is — because an uncited claim is the one thing that must never be published.
    boundary_required = {"business_requirements", "functional_requirements", "success_criteria"}
    for section, items in brd.every_section().items():
        for item in items:
            issues.extend(
                _validate_item(
                    item,
                    section=section,
                    source_root=source_root,
                    by_qualified=by_qualified,
                    index=index,
                    boundaries=boundaries,
                    downstream=downstream,
                    known_symbols=known_symbols,
                    needs_boundary=section in boundary_required,
                )
            )

    # Purpose and problem are narrative, not traced claims, so they are checked for language only.
    for item in (brd.product_purpose, brd.business_problem):
        if item is None:
            continue
        if findings := find_jargon(item.statement, known_symbols):
            issues.append(
                _issue(
                    "TECHNICAL_VOCABULARY",
                    Severity.ERROR,
                    f"{item.id} statement contains technical vocabulary — {describe(findings)}",
                    subject_id=item.id,
                )
            )

    checks += 1
    for section, items in brd.supplements().items():
        for item in items:
            if not item.evidence:
                issues.append(
                    _issue(
                        "NO_EVIDENCE",
                        Severity.ERROR,
                        f"{item.id} cites no source evidence",
                        subject_id=item.id,
                    )
                )

    low_confidence = [item.id for item in all_items if item.confidence < LOW_CONFIDENCE]
    report = GateReport.from_issues(
        "gate_requirements",
        issues,
        checks,
        {
            "item_count": len(all_items),
            "business_requirements": len(brd.business_requirements),
            "functional_requirements": len(brd.functional_requirements),
            "business_rules": len(brd.business_rules),
            "success_criteria": len(brd.success_criteria),
            **{
                section: len(items) for section, items in brd.supplements().items()
            },
            "unattached_items": sum(
                len(brd.orphans_in(section))
                for section in ("functional_requirements", "business_rules")
            ),
            "low_confidence_items": low_confidence,
            "evidence_coverage": (
                sum(bool(item.evidence) for item in all_items) / len(all_items)
                if all_items
                else 1.0
            ),
        },
    )
    return CapabilityResult(
        artifacts={"requirement_gate": report},
        gate=report,
        metrics={"errors": report.errors, "warnings": report.warnings},
    )


def _validate_item(
    item: BRDItem,
    *,
    section: str,
    source_root: Any,
    by_qualified: dict[str, Any],
    index: GraphIndex,
    boundaries: set[str],
    downstream: set[str],
    known_symbols: set[str],
    needs_boundary: bool,
) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []

    if not item.statement.strip():
        issues.append(
            _issue("EMPTY_STATEMENT", Severity.ERROR, f"{item.id} has an empty statement",
                   subject_id=item.id)
        )
        return issues

    # The check that was previously only a prompt instruction. An integration or non-functional
    # requirement is allowed to name the external system it is about — that is the requirement —
    # while a business or functional one is not.
    names_systems = section in NAMES_EXTERNAL_SYSTEMS
    if findings := find_jargon(
        item.statement, known_symbols, allow_external_systems=names_systems
    ):
        issues.append(
            _issue(
                "TECHNICAL_VOCABULARY",
                Severity.ERROR,
                f"{item.id} statement contains technical vocabulary — {describe(findings)}",
                subject_id=item.id,
            )
        )

    # Acceptance criteria are read at least as closely as the statement — they are what a
    # reviewer signs off against, item by item — so they are held to the same language bar. The
    # statement check alone let "the answer is produced by the retrieval pipeline" reach a
    # published document under a statement that had been carefully kept free of exactly that.
    for criterion in item.acceptance_criteria:
        if findings := find_jargon(
            criterion, known_symbols, allow_external_systems=names_systems
        ):
            issues.append(
                _issue(
                    "CRITERION_VOCABULARY",
                    Severity.ERROR,
                    f"{item.id} acceptance criterion contains technical vocabulary — "
                    f"{describe(findings)} — in: {criterion!r}",
                    subject_id=item.id,
                )
            )

    if not item.evidence:
        issues.append(
            _issue(
                "NO_EVIDENCE", Severity.ERROR, f"{item.id} cites no source evidence",
                subject_id=item.id,
            )
        )
    for location in item.evidence:
        check = verify_location(location, source_root)
        if not check.ok:
            issues.append(
                _issue(
                    check.code,
                    Severity.ERROR,
                    f"{item.id}: {check.message}",
                    subject_id=item.id,
                    location=location,
                )
            )

    traced = [name for name in item.graph_path if name in by_qualified]
    if not item.graph_path:
        issues.append(
            _issue(
                "NO_GRAPH_PATH", Severity.ERROR, f"{item.id} has no graph trace",
                subject_id=item.id,
            )
        )
    elif not traced:
        issues.append(
            _issue(
                "UNRESOLVED_GRAPH_PATH",
                Severity.ERROR,
                f"{item.id} graph trace names no node that exists in the knowledge graph: "
                f"{item.graph_path[:3]}",
                subject_id=item.id,
            )
        )

    if traced:
        traced_ids = {by_qualified[name].id for name in traced}
        kinds = {by_qualified[name].kind for name in traced}

        if needs_boundary:
            # Three ways to be externally reachable, and the middle one is the common case: the
            # claim describes work that a boundary sets off, so the boundary lies upstream of it.
            reaches = (
                bool(traced_ids & boundaries)
                or bool(traced_ids & downstream)
                or any(
                    index.reaches_boundary(index.reachable(entity_id, max_depth=3))
                    for entity_id in traced_ids
                )
            )
            if not reaches:
                issues.append(
                    _issue(
                        "NO_INTERACTION_BOUNDARY",
                        Severity.ERROR,
                        f"{item.id} traces only to internal processing and never reaches a point "
                        "where the system is consumed",
                        subject_id=item.id,
                    )
                )

        if section == "business_rules" and not (
            kinds & {"BUSINESS_RULE", "CONFIG_KEY", "ERROR_HANDLER"}
        ):
            issues.append(
                _issue(
                    "RULE_WITHOUT_POLICY",
                    Severity.ERROR,
                    f"{item.id} is presented as a business rule but traces to no explicit rule, "
                    "policy, or configuration",
                    subject_id=item.id,
                )
            )

    if section == "success_criteria" and not item.parent_id:
        issues.append(
            _issue(
                "CRITERION_WITHOUT_REQUIREMENT",
                Severity.WARNING,
                f"{item.id} does not say which requirement it validates",
                subject_id=item.id,
            )
        )

    return issues

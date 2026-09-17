"""Deterministic preparation of model output before any gate judges it.

Three jobs, all pure bookkeeping, and none of them strengthens a claim:

**Classification.** Every item is folded onto one of the three tiers — business requirement,
functional requirement, business rule — filed in that tier's section, and linked to the
requirement one tier above it. The tier a claim declares and the list it arrived in used to be
free to disagree; now the section *is* the tier, and the parent link is resolved and counted here
so the gate can judge a chain rather than four unrelated lists.

**Identifiers.** `BRDItem.id` defaults to empty and the schema tells the model to leave it alone,
but nothing stamped the ids before Gate 2 ran. Every rejection therefore arrived with an empty
subject, so the repair step was told *what* was wrong but never *which claim* — and quarantine,
which deletes by subject id, found nothing to delete and let failures through to the document.

**Citation metadata.** A model copying a citation out of an evidence packet reliably confuses the
enclosing definition with a symbol present at the cited line, and sometimes writes a whole
description ("button: Add to Index (UI_ACTION)") into the symbol field. Neither is a fabricated
claim — the file and line range, which are the claim, are untouched — but both fail the symbol
check and burn the repair budget on a labelling mistake.

So: mislabelled metadata is corrected against the graph, and every correction is counted and
reported rather than applied silently. A symbol naming something the graph has never heard of is
left exactly as the model wrote it, because that is a fabrication signal and the gate must see it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from agentic.capabilities.registry import CapabilityContext, CapabilityResult, capability
from agentic.capabilities.assemble import ground_outcomes
from agentic.capabilities.consolidate import _action_of
from agentic.schemas.brd import (
    BASIS_TYPES,
    SECTION_TIERS,
    TIER_SECTIONS,
    BRDItem,
    ProductBRD,
    RepairedItems,
    canonical_basis,
    canonical_tier,
    permitted_parents,
    permitted_relations,
    title_key,
)
from agentic.capabilities.graph_ops import BOUNDARY_KINDS
from agentic.capabilities.verify import verify_location
from agentic.schemas.core import (
    Entity,
    GateReport,
    KnowledgeGraph,
    Severity,
    SourceLocation,
)

IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

# Kinds whose location spans a body, so a cited line inside one really does sit in that scope.
SCOPE_KINDS = {"FUNCTION", "METHOD", "CLASS"}


@dataclass
class Corrections:
    scope_mislabelled: int = 0
    description_in_symbol: int = 0
    scope_attached: int = 0
    unknown_symbol_kept: int = 0
    definition_cited: int = 0
    node_named: int = 0


def _is_identifier(symbol: str) -> bool:
    """True when the string could name a construct at all.

    Dotted paths included, and the `path/to/file.py::name()` form the extractor uses for a
    qualified name: the file half is a path rather than an identifier, so it is stripped before
    the test rather than failing it. Without that, every citation carrying a real qualified name
    was read as free text and silently emptied.
    """
    if not symbol:
        return False
    candidate = symbol.rsplit("::", 1)[-1].strip().removesuffix("()")
    return bool(candidate) and all(IDENTIFIER.match(part) for part in candidate.split("."))


def _scope_index(graph: KnowledgeGraph) -> dict[str, list[Entity]]:
    """Definitions with a real line span, grouped by file, innermost-first when searched."""
    by_file: dict[str, list[Entity]] = {}
    for entity in graph.entities:
        location = entity.location
        if (
            entity.kind in SCOPE_KINDS
            and location is not None
            and location.line_start is not None
            and location.line_end is not None
        ):
            by_file.setdefault(location.file, []).append(entity)
    return by_file


def _enclosing(scopes: list[Entity], line: int) -> Entity | None:
    """The tightest definition whose span contains the line."""
    best: Entity | None = None
    for entity in scopes:
        location = entity.location
        assert location is not None and location.line_start and location.line_end
        if location.line_start <= line <= location.line_end:
            span = location.line_end - location.line_start
            if best is None:
                best = entity
            else:
                best_location = best.location
                assert best_location is not None
                assert best_location.line_start and best_location.line_end
                if span < best_location.line_end - best_location.line_start:
                    best = entity
    return best


def _ground_location(
    location: SourceLocation,
    scopes: dict[str, list[Entity]],
    known_names: set[str],
    counts: Corrections,
    source_root: Any,
    module_names: set[str] | None = None,
) -> bool:
    """Returns True when the symbol is a fabrication signal the gate must judge untouched."""
    module_names = module_names or set()
    symbol = (location.symbol or "").strip()
    fabricated = False

    if symbol and not _is_identifier(symbol):
        # A description, not a symbol. It asserts nothing checkable, so it is not evidence.
        location.symbol = None
        counts.description_in_symbol += 1
    elif symbol in module_names:
        # A module's name is its dotted path. It never appears as a token in its own source, so it
        # cannot be a symbol *at* a line — it is the scope the line sits in. Substring matching
        # would otherwise let a bare "app" pass on any nearby word containing it.
        location.scope = location.scope or symbol
        location.symbol = None
        counts.scope_mislabelled += 1
    elif symbol:
        # A qualified name in `symbol` is right when the citation *is* that definition, and wrong
        # when it is a line deep inside it. The checker already knows the difference, so ask it
        # rather than guessing: a citation that verifies is a definition cited correctly and must
        # keep its name, which is what a traceability tool resolves against.
        check = verify_location(location, source_root)
        if check.ok:
            counts.definition_cited += 1
        elif check.code == "EVIDENCE_SYMBOL_MISMATCH" and symbol in known_names:
            # A real construct, but one enclosing the cited line rather than sitting on it — the
            # packet's `in` field leaking into the symbol field.
            location.scope = location.scope or symbol
            location.symbol = None
            counts.scope_mislabelled += 1
        else:
            # Names nothing the extractor found, or the citation is broken for another reason.
            # Left untouched on purpose: the gate must see it.
            counts.unknown_symbol_kept += 1
            fabricated = True

    if location.scope is None and location.line_start is not None:
        enclosing = _enclosing(scopes.get(location.file, []), location.line_start)
        if enclosing is not None:
            location.scope = enclosing.qualified_name
            counts.scope_attached += 1

    return fabricated


def _name_the_cited_node(
    item: BRDItem,
    at_line: dict[tuple[str, int], list[Entity]],
    source_root: Any,
    counts: Corrections,
    leave_alone: set[int],
) -> None:
    """Prefer the qualified name of the graph node a citation actually lands on.

    A bare token — `button`, `name`, `reindex` — is true but useless: it is whatever word happened
    to sit on the line, and nothing downstream can resolve it back to anything. The qualified name
    of the node at that location is the identifier a traceability tool, a reviewer, and the graph
    itself all share.

    A candidate is only accepted if the citation still verifies with it, so this can raise the
    precision of a citation but never break one that was already sound.

    A citation whose symbol names nothing the extractor found is skipped entirely. Replacing that
    with a real node name would launder a fabrication into a clean-looking citation and hide the
    one signal the gate has that the model invented something.
    """
    for location in item.evidence:
        if location.line_start is None or id(location) in leave_alone:
            continue
        candidates = at_line.get((location.file, location.line_start), [])
        if not candidates:
            continue
        # What the claim itself says it traces through wins over anything else on the line.
        traced = [entity for entity in candidates if entity.qualified_name in item.graph_path]
        original = location.symbol
        for entity in traced or candidates:
            if entity.qualified_name == original:
                break
            location.symbol = entity.qualified_name
            if verify_location(location, source_root).ok:
                counts.node_named += 1
                break
            location.symbol = original


def _normalise(statement: str) -> str:
    return " ".join(statement.split()).casefold()


def _tier_from_the_graph(brd: ProductBRD, graph: KnowledgeGraph) -> int:
    """Type an item `business_rule` when its trace lands on an extracted rule and nothing else.

    Which tier an item belongs to used to be the least stable judgement in the whole workflow:
    across repeated runs on one unchanged codebase the same findings came back typed entirely
    functional, then mostly business, then a mix, purely on how the merge step read its own
    instructions that time. A reader cannot use a taxonomy that moves.

    One third of that judgement is not a judgement at all. The extractor already decided what a
    conditional is when it created a `BUSINESS_RULE` node, and Gate 2 already requires a business
    rule to trace to one. An item whose graph path reaches such a node — and does not also reach a
    boundary, which would make it the capability the rule constrains rather than the rule — is a
    business rule, whatever the model called it. Settling it here takes the answer from the graph,
    which does not vary, instead of from a sentence that does.
    """
    by_qualified = graph.by_qualified_name()
    changed = 0
    for _, items in brd.sections().items():
        for item in items:
            kinds = {
                by_qualified[name].kind
                for name in item.graph_path
                if name in by_qualified
            }
            if "BUSINESS_RULE" not in kinds or kinds & BOUNDARY_KINDS:
                continue
            if canonical_tier(item.requirement_type) != "business_rule":
                item.requirement_type = "business_rule"
                changed += 1
    return changed


def _file_by_tier(brd: ProductBRD) -> tuple[int, int]:
    """Normalise every declared type onto the three tiers, and file each item at its tier.

    Two things are settled here, and both used to be free to disagree with each other. The type an
    item declares is folded onto one of `business` / `functional` / `business_rule` — an item that
    called itself `technical` or `non_functional` is a grounded finding that reached for a
    vocabulary this model does not publish, so it is re-labelled rather than dropped. Then the
    item is moved into the section for that tier, because here the section *is* the tier: a
    document that files a claim under one heading and publishes it as another contradicts itself,
    and a model that settles on one list for everything makes the headings meaningless.

    Nothing is reworded and nothing is dropped. An item declaring no type at all keeps the tier of
    the section it arrived in, which is the fragment worker's filing decision standing unopposed.

    Returns the number of types re-labelled and the number of items moved.
    """
    # Every family, not just the chain: a tier can now name a supplement section or the
    # observations appendix, and filing into a section this map does not know about would raise.
    moves: dict[str, list[BRDItem]] = {section: [] for section in brd.every_section()}
    relabelled = 0
    moved = 0

    for section, items in brd.every_section().items():
        for item in items:
            declared = item.requirement_type
            tier = item.tier(section)
            # Write the canonical tier back verbatim, so every later step reads one vocabulary.
            # Publishing already resolves aliases, but leaving `technical` in the stored field
            # means the artifact on disk still carries a type the model no longer has.
            if (declared or "") != tier:
                relabelled += 1
                item.requirement_type = tier

            # A success criterion is a presentation slot, not a tier. One that declared nothing,
            # or declared the tier its section implies, stays where it is; one that declared
            # itself a rule or an outcome is taken at its word and re-filed.
            if section == "success_criteria" and canonical_tier(declared) in {None, "functional"}:
                target = section
            else:
                target = TIER_SECTIONS[tier]

            if target != section:
                moved += 1
            moves[target].append(item)

    for section, items in brd.every_section().items():
        items[:] = moves[section]
    return relabelled, moved


def _link_the_chain(brd: ProductBRD) -> tuple[int, int]:
    """Resolve each declared parent title to an identifier. Nothing is inferred here.

    This used to attach an unresolved item to whichever candidate above it shared the most graph
    nodes or cited files. That produced a hierarchy recording *which code calls which*, which the
    traceability matrix already carries and which says nothing about whether one claim refines
    another: a helper and the capability that happens to call it are not parent and child. The
    document then read as though the structure had been reasoned about when it had been measured.

    Semantic validation belongs to `consolidate_requirements`, which has already run by this
    point and has removed any link whose relation the two levels cannot hold. What is left here
    is bookkeeping: turn the surviving titles into ids. An item with no parent stays unattached
    and the gate reports it, which is an honest gap rather than an invented one.

    Returns the number resolved and a zero kept for the metric's shape.
    """
    brd.assign_ids()
    resolved = sum(bool(item.parent_id) for item in brd.all_items())
    return resolved, 0


def _evidence_key(item: "BRDItem") -> tuple:
    """What a claim actually rests on: its citations and its trace, order-insensitive."""
    spans = frozenset(
        (location.file, location.line_start, location.line_end) for location in item.evidence
    )
    return (spans, frozenset(item.graph_path))


def _drop_restatements(brd: ProductBRD) -> int:
    """Remove later items that restate an earlier one, by wording or by evidence.

    The same sentence published in two sections is not two findings. It reads as padding, and it
    inflates every coverage number that counts items, so a document with one insight repeated
    looks better evidenced than one with a single honest claim.

    Identical wording is the easy case. The costly one is three claims over the *same* lines and
    the same trace, differing only in which noun they attach the behaviour to — one error handler
    reported once as indexing, once as reindexing, once as file upload. Each reads as an
    independent finding and each is individually true, so no gate rejects them, and the document
    triples the weight of a single piece of evidence.

    Shared evidence only means duplication *within one tier*. A business requirement and the
    functional requirement realising it are supposed to rest on the same lines — that is what
    makes the outcome grounded rather than aspirational — and collapsing them across tiers would
    delete the outcome and keep only the mechanism, which is the wrong half. Where the two read
    too alike to be worth publishing separately, that is a wording judgement the gate reports and
    a person makes, not one to make silently here.
    """
    seen: set[str] = set()
    for item in (brd.product_purpose, brd.business_problem):
        if item is not None:
            seen.add(_normalise(item.statement))

    removed = 0
    dropped_titles: set[str] = set()
    for section, items in brd.every_section().items():
        by_evidence: dict[tuple, str] = {}
        kept = []
        for item in items:
            key = _normalise(item.statement)
            if key and key in seen:
                removed += 1
                dropped_titles.add(title_key(item.title))
                continue
            evidence_key = _evidence_key(item)
            # An empty citation set is not a match with another empty one; the gate handles those.
            #
            # Shared evidence alone is not enough. Two claims resting on the same lines are the
            # same finding only when they assert the same thing: an outcome and the capability
            # realising it legitimately cite one span, and once an unsupported outcome is
            # republished as a capability the two land in one section and this rule deleted one of
            # them -- silently, and whichever happened to come second. The verb is what carries
            # the difference, exactly as it does for the merge pass, so it is checked here too.
            if evidence_key[0] and evidence_key in by_evidence:
                if _action_of(item.statement) == by_evidence[evidence_key]:
                    removed += 1
                    dropped_titles.add(title_key(item.title))
                    continue
            seen.add(key)
            by_evidence.setdefault(evidence_key, _action_of(item.statement))
            kept.append(item)
        items[:] = kept

    # A removal that leaves a child pointing at what was removed produces a link the gate then
    # rejects as unresolvable -- and the item it rejects is the child, which did nothing wrong.
    # Anything that deletes an item is responsible for the links into it.
    if dropped_titles:
        for item in brd.all_items():
            if item.parent and title_key(item.parent) in dropped_titles:
                item.parent = None
                item.parent_relation = None
                item.parent_id = ""
    return removed


def _agree_with_the_grounds(brd: ProductBRD) -> int:
    """Make the published level agree with the grounds given for it, where the two disagree.

    `abstraction_basis` answers the question that is asked first -- on what grounds is this a
    requirement rather than an implementation detail -- and `BASIS_TYPES` maps every answer to
    exactly one level. So the two fields cannot legitimately disagree, and where they do, the
    basis is the one that was reasoned about.

    A backstop rather than a decision. The gate rejects an incoherent pair, and an incoherent pair
    is not something any rewording can repair: the same rejection came back on every pass until
    the repair budget ran out and a grounded claim was deleted for a contradiction between two of
    its own metadata fields. Settling it deterministically, where the answer is not in doubt, is
    better than spending a budget on it.

    Returns the number corrected.
    """
    corrected = 0
    for items in brd.every_section().values():
        for item in items:
            basis = canonical_basis(item.abstraction_basis)
            if basis is None:
                continue
            expected = BASIS_TYPES[basis]
            if canonical_tier(item.requirement_type, default=expected) != expected:
                item.requirement_type = expected
                corrected += 1
    return corrected


def _settle_relations(brd: ProductBRD) -> tuple[int, int]:
    """Make every parent link one the two levels can actually hold, or remove it.

    A link is written when a finding is produced and the finding's level can change afterwards --
    an internal behaviour reclassified as an implementation observation keeps the relation it was
    given as a system requirement, and an observation may only ever *evidence* what it points at.
    The result is a link that is illegal for reasons that have nothing to do with the claim, and
    that no rewording can repair: the gate rejected it on every pass until the repair budget ran
    out and the item was deleted.

    Two answers, and which one applies is not a judgement call. If the parent sits at a level this
    item may hang from, the relation is corrected to the one that level relation permits -- the
    link was right and its label went stale. If the parent sits at a level this item may not hang
    from at all, there is no relationship to describe and the link goes.

    Returns the number of relations corrected and the number of links removed.
    """
    tier_of: dict[str, str] = {}
    for section, items in brd.every_section().items():
        holding = SECTION_TIERS.get(section, "functional")
        for item in items:
            tier_of.setdefault(
                title_key(item.title), canonical_tier(item.requirement_type, default=holding)
            )

    corrected = removed = 0
    for section, items in brd.every_section().items():
        tier = SECTION_TIERS.get(section, "functional")
        allowed_parents = permitted_parents(tier)
        allowed_relations = permitted_relations(tier)
        for item in items:
            if not item.parent:
                continue
            parent_tier = tier_of.get(title_key(item.parent))
            if parent_tier is None:
                continue  # names nothing published; the link is cleared where that is decided
            if parent_tier not in allowed_parents:
                item.parent = None
                item.parent_relation = None
                item.parent_id = ""
                removed += 1
                continue
            relation = (item.parent_relation or "").strip().lower()
            if relation not in allowed_relations and allowed_relations:
                item.parent_relation = allowed_relations[0]
                corrected += 1
    return corrected, removed


@capability("prepare_claims")
def prepare_claims(ctx: CapabilityContext) -> CapabilityResult:
    """Stamp identifiers and ground citation metadata, without touching any claim."""
    brd = ctx.load("brd", ProductBRD)
    graph = ctx.load("graph", KnowledgeGraph)

    # Order matters. The tier is settled before anything is filed by it; restatements go before
    # ids are stamped, so numbering has no gaps; the chain is linked last, because a parent that
    # was about to be deleted is not a parent.
    retyped = _tier_from_the_graph(brd, graph)
    incoherent = _agree_with_the_grounds(brd)
    relabelled, refiled = _file_by_tier(brd)
    relations_fixed, links_removed = _settle_relations(brd)
    duplicates = _drop_restatements(brd)
    # An outcome's citation is the citation of the capabilities still standing under it, so it is
    # re-derived here rather than carried from assembly: by this point resolution has removed what
    # it rejected, and a repair pass may have removed more.
    ground_outcomes(brd)
    linked, inferred = _link_the_chain(brd)

    scopes = _scope_index(graph)
    known_names = {entity.qualified_name for entity in graph.entities}
    known_names |= {entity.name for entity in graph.entities if entity.kind in SCOPE_KINDS}
    module_names = {
        entity.qualified_name for entity in graph.entities if entity.kind == "MODULE"
    }
    counts = Corrections()

    at_line: dict[tuple[str, int], list[Entity]] = {}
    for entity in graph.entities:
        location = entity.location
        if location is not None and location.line_start is not None:
            at_line.setdefault((location.file, location.line_start), []).append(entity)

    for item in brd.all_items():
        leave_alone: set[int] = set()
        for location in item.evidence:
            if _ground_location(
                location, scopes, known_names, counts, ctx.source_root, module_names
            ):
                leave_alone.add(id(location))
        _name_the_cited_node(item, at_line, ctx.source_root, counts, leave_alone)

    messages: list[str] = []
    if counts.scope_mislabelled or counts.description_in_symbol:
        messages.append(
            f"Corrected citation labels on {counts.scope_mislabelled} enclosing-scope and "
            f"{counts.description_in_symbol} free-text symbol field(s). The cited files and line "
            "ranges were not modified."
        )
    if counts.unknown_symbol_kept:
        messages.append(
            f"{counts.unknown_symbol_kept} citation(s) name a symbol the extractor never found "
            "and were left unchanged for the gate to judge."
        )

    if duplicates:
        messages.append(
            f"Removed {duplicates} claim(s) that restated an earlier one word for word."
        )
    if relabelled or refiled:
        messages.append(
            f"Folded {relabelled} declared type(s) onto the three tiers and filed {refiled} "
            "item(s) under the tier they declared."
        )
    if relations_fixed or links_removed:
        messages.append(
            f"Corrected {relations_fixed} parent relation(s) that had gone stale when the item's "
            f"level changed, and removed {links_removed} link(s) between levels that cannot hold "
            "a relationship. Neither is something a rewording could have fixed."
        )
    if incoherent:
        messages.append(
            f"Corrected {incoherent} item(s) whose published level disagreed with the grounds "
            "given for it. The grounds are the question answered first, so they decide the level."
        )
    if inferred:
        messages.append(
            f"Attached {inferred} item(s) to a parent one tier up by shared evidence, after the "
            "title they named resolved to nothing."
        )
    unattached = sum(len(brd.orphans_in(section)) for section in ("functional_requirements", "business_rules"))
    if unattached:
        messages.append(
            f"{unattached} item(s) could not be attached to anything above them and are left for "
            "the gate to report."
        )

    metrics: dict[str, Any] = {
        "items": len(brd.all_items()),
        "restatements_removed": duplicates,
        "types_relabelled": relabelled,
        "refiled_by_tier": refiled,
        "retyped_as_rules": retyped,
        "classification_made_coherent": incoherent,
        "parent_relations_corrected": relations_fixed,
        "parent_links_removed_as_impossible": links_removed,
        "parents_resolved_by_title": linked,
        "parents_inferred_from_evidence": inferred,
        "unattached_items": unattached,
        "scope_mislabelled": counts.scope_mislabelled,
        "description_in_symbol": counts.description_in_symbol,
        "definition_cited": counts.definition_cited,
        "cited_node_named": counts.node_named,
        "scope_attached": counts.scope_attached,
        "unknown_symbol_kept": counts.unknown_symbol_kept,
    }
    return CapabilityResult(artifacts={"brd": brd}, metrics=metrics, messages=messages)


@capability("apply_repairs")
def apply_repairs(ctx: CapabilityContext) -> CapabilityResult:
    """Put the repaired claims back, and carry every other claim across untouched.

    The repair step used to return a whole `ProductBRD`, so correcting three wordings meant
    re-emitting every requirement in the document through one bounded reply. Past a few dozen
    items that is arithmetically impossible: what came back was a fraction of the set, the rest
    was gone, and nothing counted the loss. A gate rejection over a single phrase could delete
    most of the analysis.

    Now the model returns only what it fixed and this step merges by id. A claim the gate never
    rejected cannot be altered here at all, however the repair pass was worded — which is the
    property that makes the whole loop safe to run.
    """
    brd = ctx.load("brd", ProductBRD)
    report = ctx.load("requirement_gate", GateReport)
    try:
        repairs = ctx.load("repairs", RepairedItems)
    except (KeyError, OSError, ValueError):
        # The repair call itself failed, so it repaired nothing. That is a pass-through, not an
        # error: halting here would undo the whole point of letting a failed repair continue, and
        # the run would once again end with no deliverables.
        return CapabilityResult(
            artifacts={"brd": brd},
            metrics={"repairs_applied": 0, "surviving_items": len(brd.all_items())},
            messages=[
                "The repair step produced nothing, so every claim is carried across unchanged "
                "and the gate will judge the same set again."
            ],
        )

    rejected = {
        issue.subject_id
        for issue in report.issues
        if issue.severity == Severity.ERROR and issue.subject_id
    }
    fixed = {item.id: item for item in repairs.items if item.id}
    deleted = {identifier for identifier in repairs.deleted if identifier}

    # A repair naming an item the gate did not reject is ignored rather than applied. Letting it
    # through would allow a pass asked to fix one wording to quietly rewrite claims that had
    # already been checked and passed.
    out_of_scope = sorted((set(fixed) | deleted) - rejected)
    for identifier in out_of_scope:
        fixed.pop(identifier, None)
        deleted.discard(identifier)

    applied = 0
    removed = 0
    removed_titles: set[str] = set()
    for section, items in brd.every_section().items():
        kept: list[BRDItem] = []
        for item in items:
            replacement = fixed.get(item.id)
            if replacement is not None:
                # A repair is asked for wording. Everything that decides where an item sits --
                # its identifier, its title, the grounds it was classified on, the level that
                # follows from those grounds, its group, its priority, and the link to its parent
                # -- is settled by stages that saw the whole candidate set, and is carried across
                # rather than taken from the reply.
                #
                # Left to the model these came back altered: a rule returned with the grounds of
                # an outcome, which is a contradiction no rewording can fix, so the gate rejected
                # it again on every pass until the budget ran out and it was deleted. A repair
                # pass must not be able to fail a claim it was asked to save.
                replacement.id = item.id
                replacement.title = item.title
                replacement.abstraction_basis = item.abstraction_basis
                replacement.requirement_type = item.requirement_type
                replacement.parent = item.parent
                replacement.parent_relation = item.parent_relation
                replacement.parent_id = item.parent_id
                replacement.area = item.area
                replacement.priority = item.priority
                replacement.disposition = item.disposition
                replacement.cluster_id = item.cluster_id
                replacement.model_confidence = item.model_confidence
                replacement.superseded = list(item.superseded)
                replacement.implementation_observations = list(item.implementation_observations)
                kept.append(replacement)
                applied += 1
                continue
            if item.id in deleted:
                removed += 1
                removed_titles.add(title_key(item.title))
                continue
            kept.append(item)
        items[:] = kept

    # A deletion that leaves a child naming what was deleted produces an unresolvable link, and
    # the gate rejects the *child* for it -- so a repair that correctly removed one ungrounded
    # claim spent the next round of the budget deleting a perfectly good one beneath it. Whatever
    # removes an item owns the links into it.
    orphaned = 0
    if removed_titles:
        for item in brd.all_items():
            if item.parent and title_key(item.parent) in removed_titles:
                item.parent = None
                item.parent_relation = None
                item.parent_id = ""
                orphaned += 1

    unfixed = sorted(rejected - set(fixed) - deleted)

    messages = [
        f"Applied {applied} repaired claim(s) and removed {removed}; the other "
        f"{len(brd.all_items()) - applied} claim(s) were carried across untouched."
    ]
    if unfixed:
        messages.append(
            f"{len(unfixed)} rejected claim(s) came back neither fixed nor deleted and still "
            f"stand as they were: {', '.join(unfixed[:10])}. The gate will judge them again."
        )
    if out_of_scope:
        messages.append(
            f"Ignored {len(out_of_scope)} returned item(s) the gate had not rejected, so a "
            "repair pass could not rewrite claims that already passed."
        )
    if orphaned:
        messages.append(
            f"Cleared {orphaned} parent link(s) that named a claim the repair removed. Those "
            "items are published unattached, which is the true state of the document."
        )

    return CapabilityResult(
        artifacts={"brd": brd},
        metrics={
            "repairs_applied": applied,
            "repairs_deleted": removed,
            "repairs_unfixed": len(unfixed),
            "repairs_out_of_scope": len(out_of_scope),
            "repairs_orphaned_links_cleared": orphaned,
            "surviving_items": len(brd.all_items()),
        },
        messages=messages,
    )

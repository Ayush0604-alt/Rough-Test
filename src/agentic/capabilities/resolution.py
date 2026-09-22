"""Deciding what happens to each candidate, and keeping the unresolved out of the document.

Validation could already tell a bad requirement from a good one. Nothing acted on the answer. A
claim the correspondence check had contradicted was marked `status: "rejected"` and published in
the requirements list beside the claims that had passed, so a reader could not tell them apart
without reading a field most tools ignore.

That was a deliberate choice and it was the wrong one. The reasoning behind it -- a reader can
check a doubtful claim and cannot recover a deleted one -- is sound, but it argues for
*partitioning* the output, not for putting everything in one list. Nothing here is deleted: a
candidate that cannot be published lands in `unresolved_candidates` with its evidence, its reason,
and the disposition that sent it there. The requirements document holds requirements.

Every candidate leaves this stage with exactly one disposition:

    keep          -- published as it stands
    reclassify    -- published, at a different level than it claimed
    observation   -- retained for traceability, not published as a requirement
    open_question -- the evidence cannot settle it; published in the unresolved set
    reject        -- contradicted by its own evidence, or unsupported

`merge` and `reword` are deliberately absent from that list: merging is consolidation's job and has
already happened by the time this runs, and rewording is the repair step's. A disposition here is
the decision about a candidate that has already been through both.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from typing import Any

from agentic.capabilities.consolidate import (
    MERGE_SIMILARITY,
    _action_of,
    _same_action,
    _similarity,
)
from agentic.capabilities.correspondence import EvidenceView
from agentic.capabilities.graph_ops import BOUNDARY_KINDS, DECLARATION_KINDS
from agentic.capabilities.registry import CapabilityContext, CapabilityResult, capability
from agentic.schemas.brd import TIER_SECTIONS, BRDItem, ProductBRD, title_key
from agentic.schemas.core import KnowledgeGraph

# How many published capabilities must stand under a business requirement before it counts as
# supported. One is not an outcome: a business requirement realised by exactly one capability is
# that capability restated more grandly, which is the failure mode the gate already names
# BUSINESS_REQUIREMENT_RESTATES_FUNCTION. Two is the smallest number that can express an outcome
# the parts add up to.
MIN_SUPPORTING_CAPABILITIES = 2

# A statement shorter than this cannot carry an actor, an action and an object, so whatever it is,
# it is not a requirement. Catches the empty and truncated statements a transformation can leave
# behind, which were reaching the document and reading as blank rows.
MIN_STATEMENT_WORDS = 5


# A full stop or comma with white space in front of it and nothing but white space, another
# punctuation mark, or the end of the sentence after it. That is a word having been removed; the
# same characters inside "a .env file" or "version 2 .1" are not.
_STRANDED_PUNCTUATION = re.compile(r"\s[.,](?=\s|$|[.,;:])")


def _degenerate(item: BRDItem) -> str | None:
    """Whether the statement failed to survive whatever produced it."""
    statement = " ".join((item.statement or "").split())
    if not statement:
        return "The statement is empty."
    if len(statement.split()) < MIN_STATEMENT_WORDS:
        return f"The statement is too short to be a requirement: {statement!r}."
    # A sentence left with a hole in it -- doubled punctuation, a dangling connective, an empty
    # bracket -- is a transformation artefact rather than a written requirement.
    #
    # The punctuation markers have to be anchored, not merely present. A bare " ." matched inside
    # "a .env file at startup" and "version 2 .1", and rejected two perfectly written requirements
    # for a gap that was not there -- a check meant to catch damage doing the damage instead. What
    # makes a full stop or a comma an artefact is having nothing after it.
    if _STRANDED_PUNCTUATION.search(statement):
        return f"The statement contains a gap left by a transformation: {statement!r}."
    for marker in ("  ", "()", "[]", "{}", "..", " and .", " the ."):
        if marker in statement:
            return f"The statement contains a gap left by a transformation: {statement!r}."
    return None


def _support_for(item: BRDItem, entities: list[Any]) -> int:
    """How much independently meaningful evidence stands behind a claim."""
    strong = sum(
        1
        for entity in entities
        if entity.kind in BOUNDARY_KINDS
        or entity.kind in DECLARATION_KINDS
        or (entity.attributes or {}).get("rule_source")
    )
    return strong


def _is_the_same_claim(outcome: BRDItem, capability: BRDItem) -> bool:
    """Whether an outcome and the single capability under it are one finding written twice.

    Deliberately a stricter test than the one the gate *warns* on. Warning costs a reader a
    sentence; merging removes a published requirement, so it has to demand more: the same
    evidence, the same verb, and wording alike enough that no reader could tell the two apart.

    Citing the same lines alone is not enough and never was -- an outcome is supposed to rest on
    the evidence of what realises it. Sharing an actor and an object is not enough either, because
    a document that reads consistently shares those across every statement in it. What is not
    legitimate is all of it at once.
    """
    outcome_spans = {(loc.file, loc.line_start, loc.line_end) for loc in outcome.evidence}
    capability_spans = {(loc.file, loc.line_start, loc.line_end) for loc in capability.evidence}
    if not outcome_spans or outcome_spans != capability_spans:
        return False
    if not _same_action(outcome, capability):
        return False
    return _similarity(outcome.statement, capability.statement) >= MERGE_SIMILARITY


def _absorb(keeper: BRDItem, absorbed: BRDItem) -> None:
    """Fold one claim into another, keeping everything either of them established.

    Merging is not deletion. Every citation, every trace node and every acceptance criterion
    survives on the keeper, and the keeper records what it absorbed, so a reader can see that two
    findings were folded together rather than one being quietly lost.
    """
    spans = {(loc.file, loc.line_start, loc.line_end) for loc in keeper.evidence}
    for location in absorbed.evidence:
        if (location.file, location.line_start, location.line_end) not in spans:
            keeper.evidence.append(location)
    for name in absorbed.graph_path:
        if name not in keeper.graph_path:
            keeper.graph_path.append(name)
    seen = {" ".join(text.split()).casefold() for text in keeper.acceptance_criteria}
    for criterion in absorbed.acceptance_criteria:
        key = " ".join(criterion.split()).casefold()
        if key not in seen:
            seen.add(key)
            keeper.acceptance_criteria.append(criterion)
    keeper.persona = keeper.persona or absorbed.persona
    keeper.business_value = keeper.business_value or absorbed.business_value
    if absorbed.title not in keeper.superseded:
        keeper.superseded.append(absorbed.title)
    keeper.confidence = min(keeper.confidence, absorbed.confidence)


def _resolve_conflicts(brd: ProductBRD, view: EvidenceView) -> tuple[int, int]:
    """Settle clusters holding competing readings of one piece of evidence.

    The best-supported reading is kept and the others become open questions. "Best supported" is
    measured, not judged: how much of the cited evidence is externally reachable or declared, then
    how many distinct spans corroborate it, then the correspondence verdict. Where two readings
    are equally supported, none of them is published -- an arbitrary winner would state something
    about the product that the analysis did not establish.

    Grouped on the **evidence and the action**, not on the capability cluster. Two things had to
    be true before this settled the right thing:

    A cluster is marked as conflicting when any pair inside it disagrees, and a large cluster is a
    group of related capabilities rather than a group of readings of one behaviour -- so keying on
    the cluster made one disagreeing pair stand for all of it. On one repository that deferred
    seventy-two individually grounded capabilities in favour of a single unrelated one.

    Sharing a citation is not enough either. A citation routinely lands on a line that several
    distinct capabilities pass through -- a decorator, a dispatch table, a module header -- so a
    span is shared by candidates that assert entirely different things. Five of those are five
    capabilities, and the branch that holds back a whole group when nothing separates its members
    then withheld all five. What makes two statements competing *readings* is that they say
    something different about the same action on the same evidence; a different verb is a
    different capability, which is the rule the merge pass already applies.
    """
    groups: dict[tuple, list[BRDItem]] = defaultdict(list)
    for items in brd.every_section().values():
        for item in items:
            if item.cluster_conflict and item.cluster_id and item.evidence:
                spans = frozenset(
                    (location.file, location.line_start, location.line_end)
                    for location in item.evidence
                )
                groups[(item.cluster_id, spans, _action_of(item.statement))].append(item)

    kept = deferred = 0
    for _key, members in groups.items():
        if len(members) < 2:
            continue

        def rank(candidate: BRDItem) -> tuple:
            verdict = (
                getattr(candidate.validation, "verdict", "unchecked")
                if candidate.validation
                else "unchecked"
            )
            return (
                _support_for(candidate, view.entities_for(candidate)),
                len({(e.file, e.line_start) for e in candidate.evidence}),
                1 if verdict == "supported" else 0,
                len(candidate.acceptance_criteria),
            )

        scored = sorted(((rank(member), member) for member in members), key=lambda p: p[0])
        best_score, best = scored[-1]
        runner_up = scored[-2][0]

        if best_score == runner_up:
            # Nothing separates them by support. What that means depends on whether they are
            # saying the same thing.
            #
            # These members already share a citation and a verb -- that is how the group was
            # formed -- so where their wording agrees too, they are one capability written
            # several times by workers who each saw one boundary into it. Withholding all of
            # them, which is what happened, removed seventy grounded capabilities from one
            # repository over a tie in a four-part score. They are merged instead, into the
            # fullest wording, keeping every citation and criterion from all of them.
            #
            # Where the wording genuinely diverges, they are competing readings after all, and an
            # arbitrary winner would state something about the product the analysis never
            # established. Those are still held back.
            fullest = max(members, key=lambda item: len(item.statement or ""))
            alike = [
                member
                for member in members
                if member is not fullest
                and _similarity(fullest.statement, member.statement) >= MERGE_SIMILARITY
            ]
            if len(alike) == len(members) - 1:
                fullest.disposition = "keep"
                fullest.resolution_reason = (
                    f"Merged from {len(members)} findings that described one capability in the "
                    "same words, on the same evidence, and that nothing separated by support."
                )
                for member in alike:
                    member.disposition = "merge"
                    member.resolution_reason = (
                        f"Folded into {fullest.title!r}: same evidence, same action, and wording "
                        "close enough that the two are one capability written twice."
                    )
                    _absorb(fullest, member)
                kept += 1
                continue

            for member in members:
                member.disposition = "open_question"
                member.resolution_reason = (
                    f"{len(members)} readings of the same evidence disagree and the evidence does "
                    "not favour any of them. Recorded for a person to settle rather than "
                    "published as a requirement."
                )
                deferred += 1
            continue

        for member in members:
            if member is best:
                member.disposition = "keep"
                member.resolution_reason = (
                    f"Best-supported of {len(members)} competing readings of the same evidence."
                )
                kept += 1
            else:
                member.disposition = "open_question"
                member.resolution_reason = (
                    "A different reading of the same evidence is better supported: "
                    f"{best.title!r}. Recorded rather than published, because two contradictory "
                    "statements about one piece of code cannot both be requirements."
                )
                deferred += 1
    return kept, deferred


def _scope_criteria(item: BRDItem, repository_files: set[str]) -> int:
    """Drop criteria that quote a file the final requirement does not cite.

    Merging unions two candidates' criteria, and a criterion written against the other candidate's
    evidence can arrive with it. A criterion naming code this requirement does not rest on cannot
    be checked against this requirement.
    """
    files = {location.file for location in item.evidence}
    stems = {name.rsplit("/", 1)[-1] for name in files}
    kept: list[str] = []
    dropped = 0
    for criterion in item.acceptance_criteria:
        mentioned = {
            token
            for token in criterion.replace(",", " ").replace("(", " ").replace(")", " ").split()
            if "." in token and not token.endswith(".")
        }
        # A token counts against the criterion only if it names a file the repository actually
        # holds and this requirement does not cite. Drawing the file list from the inventory rather
        # than from a set of known extensions is what keeps this working on any stack.
        offending = [
            token
            for token in mentioned
            if token.rsplit("/", 1)[-1] in repository_files
            and token.rsplit("/", 1)[-1] not in stems
        ]
        if offending:
            dropped += 1
            continue
        kept.append(criterion)
    item.acceptance_criteria = kept
    return dropped


@capability("resolve_candidates")
def resolve_candidates(ctx: CapabilityContext) -> CapabilityResult:
    """Give every candidate a disposition, and move what cannot be published out of the document."""
    brd = ctx.load("brd", ProductBRD)

    if not getattr(ctx.settings, "candidate_resolution", True):
        return CapabilityResult(
            artifacts={"brd": brd, "unresolved_candidates": {"candidates": [], "disabled": True}},
            metrics={"resolved": 0, "resolution_disabled": True},
            messages=["Candidate resolution is switched off for this run."],
        )

    view = EvidenceView(ctx.load("graph", KnowledgeGraph))

    # The requirements gate is deliberately not read here. `quarantine` is the step that acts on
    # gate errors, and it calls `assign_ids` after removing what it rejected -- so by the time this
    # runs the report's subject ids name whichever items now sit at those positions, not the ones
    # the gate judged. Matching stale ids against renumbered items rejected the wrong requirement.
    # Everything this stage decides, it decides from the items themselves.

    # Every file name the repository contains, so a criterion quoting one can be told from a
    # criterion using an ordinary word with a full stop in it.
    repository_files = {
        entity.location.file.rsplit("/", 1)[-1]
        for entity in ctx.load("graph", KnowledgeGraph).entities
        if entity.location
    }

    conflicts_kept, conflicts_deferred = _resolve_conflicts(brd, view)

    criteria_dropped = 0
    for items in brd.every_section().values():
        for item in items:
            criteria_dropped += _scope_criteria(item, repository_files)

    # A business requirement is supported only by the capabilities actually published beneath it.
    supporters: Counter[str] = Counter()
    children: dict[str, list[BRDItem]] = defaultdict(list)
    for section, items in brd.sections().items():
        if section == "business_requirements":
            continue
        for item in items:
            if item.parent:
                supporters[title_key(item.parent)] += 1
                if section == "functional_requirements":
                    children[title_key(item.parent)].append(item)
    merged_outcomes = 0

    for item in brd.all_items():
        if item.disposition:
            continue  # already settled by conflict resolution

        broken = _degenerate(item)
        verdict = (
            getattr(item.validation, "verdict", "unchecked") if item.validation else "unchecked"
        )

        if broken:
            item.disposition = "reject"
            item.resolution_reason = broken
        elif verdict == "rejected":
            # An interpretive failure alone is not enough to delete a requirement. The anchoring
            # check compares a statement against the code's identifiers, and the synthesis skill
            # asks for statements in the product's business words -- so a correctly written claim
            # can fail it. Uncertain and wrong are different answers and get different dispositions.
            objective = bool(getattr(item.validation, "objective", False))
            item.disposition = "reject" if objective else "open_question"
            item.resolution_reason = (
                ("The cited code does not support the statement. " if objective else
                 "The evidence could not be shown to support the statement, though nothing "
                 "contradicts it outright. ")
                + (getattr(item.validation, "reason", "") or "")
            ).strip()
        elif item.requirement_type == "observation":
            item.disposition = "observation"
            item.resolution_reason = (
                "Describes how the current code works rather than something the product must do. "
                "Retained for traceability."
            )
        elif item.requirement_type == "business" and (
            supporters[title_key(item.title)] < MIN_SUPPORTING_CAPABILITIES
        ):
            beneath = children.get(title_key(item.title), [])
            twin = (
                beneath[0]
                if len(beneath) == 1 and _is_the_same_claim(item, beneath[0])
                else None
            )
            if twin is not None:
                # The outcome is its one capability in other words: same evidence, same words, one
                # level apart. Published side by side after a demotion that is one finding
                # appearing twice under two headings, which a reader cannot tell from two real
                # requirements. Fold it into the capability instead.
                item.disposition = "merge"
                item.resolution_reason = (
                    f"Merged into {twin.title!r}: it restated that capability rather than "
                    "standing over it as an outcome, and only one capability stood under it."
                )
                _absorb(twin, item)
                merged_outcomes += 1
                continue
            # An outcome realised by one capability, or none, is that capability restated. Kept as
            # a functional requirement at the top of the chain rather than invented away.
            item.disposition = "reclassify"
            item.resolution_reason = (
                f"Published as a capability rather than a business outcome: only "
                f"{supporters[title_key(item.title)]} capability/capabilities stand under it, so "
                "the evidence does not establish an outcome the parts add up to."
            )
            item.abstraction_basis = "external_capability"
            item.requirement_type = "functional"
            item.parent = ""
            item.parent_relation = ""
            item.parent_id = ""
            # An outcome carries no group of its own -- it stood over them. Republished as a
            # capability it needs one, or it is filed under "uncategorised" beside the very
            # capabilities it used to head, which reads as a part of the product nobody named.
            grouped = Counter(child.area for child in beneath if child.area)
            if not item.area and grouped:
                item.area = grouped.most_common(1)[0][0]
        else:
            item.disposition = "keep"

    # Partition, and re-file. Reclassifying an item changes the level it belongs at, and nothing
    # downstream re-files: consolidation is the stage that puts an item in its section and it has
    # already run. An item left in the wrong list keeps its old heading whatever its type says.
    unresolved: list[dict[str, Any]] = []
    relocations: dict[str, list[BRDItem]] = {section: [] for section in brd.every_section()}
    for section, items in brd.every_section().items():
        surviving: list[BRDItem] = []
        for item in items:
            if item.disposition == "reclassify":
                target = TIER_SECTIONS.get(item.requirement_type, section)
                if target != section:
                    relocations[target].append(item)
                    continue
            if item.disposition == "merge":
                # Folded into its twin, which now carries its evidence and its criteria. Recorded
                # so the merge is visible, and not published a second time beside the survivor.
                unresolved.append(
                    {
                        "title": item.title,
                        "statement": item.statement,
                        "section": section,
                        "type": item.requirement_type,
                        "disposition": item.disposition,
                        "reason": item.resolution_reason,
                        "cluster_id": item.cluster_id,
                        "confidence": item.confidence,
                        "evidence": [location.compact() for location in item.evidence],
                        "graph_path": list(item.graph_path),
                    }
                )
                continue
            if item.disposition in {"reject", "open_question"}:
                unresolved.append(
                    {
                        "title": item.title,
                        "statement": item.statement,
                        "section": section,
                        "type": item.requirement_type,
                        "disposition": item.disposition,
                        "reason": item.resolution_reason,
                        "cluster_id": item.cluster_id,
                        "confidence": item.confidence,
                        "evidence": [location.compact() for location in item.evidence],
                        "graph_path": list(item.graph_path),
                    }
                )
                continue
            surviving.append(item)
        items[:] = surviving

    for section, arrivals in relocations.items():
        if arrivals:
            getattr(brd, section).extend(arrivals)

    # Removing a requirement orphans whatever pointed at it. A link to something no longer in the
    # document is worse than no link: it looks like a hierarchy and resolves to nothing.
    published = {title_key(item.title) for item in brd.all_items()}
    orphaned = 0
    for item in brd.all_items():
        if item.parent and title_key(item.parent) not in published:
            item.parent = ""
            item.parent_relation = ""
            item.parent_id = ""
            orphaned += 1

    counts = Counter(item.disposition for item in brd.all_items())
    for entry in unresolved:
        counts[entry["disposition"]] += 1

    messages = [
        "Resolved every candidate: "
        + ", ".join(f"{count} {name}" for name, count in sorted(counts.items()))
        + "."
    ]
    if unresolved:
        messages.append(
            f"{len(unresolved)} candidate(s) left the requirements document and are recorded in "
            "`unresolved_candidates` with their evidence and the reason. They are not deleted, "
            "and they are not requirements: a reader of the document sees only what the analysis "
            "was able to stand behind."
        )
    if conflicts_deferred:
        messages.append(
            f"{conflicts_deferred} candidate(s) were competing readings of evidence another "
            f"reading explained better, or evidence that favoured none of them; {conflicts_kept} "
            "best-supported reading(s) were kept. Two contradictory statements about one piece of "
            "code cannot both be requirements."
        )
    if merged_outcomes:
        messages.append(
            f"Folded {merged_outcomes} outcome(s) into the single capability that restated them, "
            "keeping every citation and criterion on the survivor. An outcome standing over one "
            "capability, in that capability's own words, is one finding published twice."
        )
    if criteria_dropped:
        messages.append(
            f"Removed {criteria_dropped} acceptance criterion/criteria that named code their "
            "requirement does not cite, which a merge can carry across from the other candidate."
        )
    if orphaned:
        messages.append(
            f"{orphaned} parent link(s) pointed at a requirement that is no longer published and "
            "were cleared, so no relation is left pointing at nothing."
        )

    return CapabilityResult(
        artifacts={
            "brd": brd,
            "unresolved_candidates": {
                "candidates": unresolved,
                "counts": dict(Counter(entry["disposition"] for entry in unresolved)),
            },
        },
        metrics={
            "resolved": sum(counts.values()),
            "unresolved_candidates": len(unresolved),
            "conflicts_settled": conflicts_kept,
            "outcomes_merged_into_capabilities": merged_outcomes,
            "conflicting_deferred": conflicts_deferred,
            "criteria_unscoped_removed": criteria_dropped,
            "orphaned_links_cleared": orphaned,
            **{f"disposition_{name}": count for name, count in counts.items() if name},
        },
        messages=messages,
    )


# ---------------------------------------------------------------------------------------------
# Final structural validation
# ---------------------------------------------------------------------------------------------


def _cycles(brd: ProductBRD) -> list[list[str]]:
    """Every closed loop in the parent chain.

    A cycle is invisible to a check that only looks one link up: each item has a parent that
    exists, so nothing is dangling and nothing is orphaned, while the hierarchy has no top. Any
    consumer that walks upward from a leaf hangs.
    """
    parent: dict[str, str] = {}
    titles: dict[str, str] = {}
    for item in brd.all_items():
        key = title_key(item.title)
        titles[key] = item.title
        if item.parent:
            parent[key] = title_key(item.parent)

    found: list[list[str]] = []
    seen: set[str] = set()
    for start in parent:
        if start in seen:
            continue
        path: list[str] = []
        position: dict[str, int] = {}
        node = start
        while node in parent and node not in seen:
            if node in position:
                found.append([titles.get(name, name) for name in path[position[node] :]])
                break
            position[node] = len(path)
            path.append(node)
            node = parent[node]
        seen.update(path)
    return found


def _fold_identical(brd: ProductBRD) -> dict[str, int]:
    """Merge published requirements that share a title, within each section.

    Returns title -> how many were absorbed, so the merge is reported rather than silent.

    The last chance to settle a duplicate. Everything upstream compares meaning, and meaning is a
    judgement that can decline: a pair the clustering stage marked as competing readings of one
    piece of evidence is deliberately never folded, in case one of them is wrong. But two
    requirements carrying the same title, in the same section, at the end of the pipeline, are one
    requirement however they got there -- and publishing both asks the reader to work out which
    one to sign off.
    """
    absorbed: dict[str, int] = {}
    for section, items in brd.every_section().items():
        if len(items) < 2:
            continue
        keepers: dict[str, BRDItem] = {}
        survivors: list[BRDItem] = []
        for item in items:
            key = title_key(item.title)
            if not key:
                survivors.append(item)
                continue
            keeper = keepers.get(key)
            if keeper is None:
                keepers[key] = item
                survivors.append(item)
                continue
            # The fuller statement survives; the other is folded into it with everything it had.
            if len(item.statement or "") > len(keeper.statement or ""):
                keeper.statement = item.statement
            _absorb(keeper, item)
            absorbed[key] = absorbed.get(key, 0) + 1
        items[:] = survivors

    if absorbed:
        # Anything that pointed at a folded copy points at the survivor, which carries the same
        # title, so links stay intact -- but the ids are stale and are restamped.
        brd.assign_ids()
    return absorbed


@capability("validate_synthesis")
def validate_synthesis(ctx: CapabilityContext) -> CapabilityResult:
    """The last check before the document is written, reported rather than silently applied.

    Everything here is a property of the finished set that no earlier stage can see, because each
    of them changes it. The findings are structured and carry the item they concern, so a reviewer
    can act on one rather than being told the document is imperfect.

    Two findings are enforced rather than reported: a cycle and a relation with no parent both
    produce a document a consumer cannot walk, so the offending link is cleared and the fact
    recorded. Everything else is stated and left in place, because deciding what a requirement
    should have said is a person's job.
    """
    brd = ctx.load("brd", ProductBRD)
    issues: list[dict[str, Any]] = []

    def report(code: str, message: str, subject: str = "") -> None:
        issues.append({"code": code, "message": message, "subject_id": subject})

    published = [
        (section, item)
        for section, items in brd.sections().items()
        for item in items
    ] + [(section, item) for section, items in brd.supplements().items() for item in items]

    # 1. No duplicate requirements. Reported *and* settled, because this is the last stage that
    # can settle anything and a duplicate left in the document is one the reader has to
    # disambiguate themselves.
    #
    # Merging on an identical title is the narrowest safe rule there is: two requirements with the
    # same title in the same section are one requirement whatever route produced them, and no
    # wording comparison or threshold is being trusted. Everything earlier -- the semantic
    # clustering, the merge pass -- works on meaning and can legitimately decline to fold two
    # candidates together; this catches what survives all of it, including a pair the clustering
    # stage flagged as competing readings and resolution could not settle.
    folded = _fold_identical(brd)
    for key, count in folded.items():
        report(
            "DUPLICATE_REQUIREMENT",
            f"{count + 1} published requirements shared one title and were merged into one, "
            "keeping every citation and criterion from all of them.",
            key,
        )

    published = [
        (section, item)
        for section, items in brd.sections().items()
        for item in items
    ] + [(section, item) for section, items in brd.supplements().items() for item in items]

    by_statement: Counter[str] = Counter(
        " ".join((item.statement or "").split()).casefold() for _, item in published
    )
    for text, count in by_statement.items():
        if count > 1 and text:
            report("DUPLICATE_STATEMENT", f"{count} published requirements share one statement.")

    # 2. No relation without a parent, and no cycles. Both are enforced.
    cleared = 0
    for _, item in published:
        if item.parent_relation and not item.parent_id:
            item.parent_relation = ""
            item.parent = ""
            cleared += 1
            report(
                "RELATION_WITHOUT_PARENT",
                "A parent relation named no resolvable parent and was cleared.",
                item.id,
            )
    broken_cycles = 0
    for loop in _cycles(brd):
        report("CIRCULAR_HIERARCHY", "Parent chain forms a loop: " + " -> ".join(loop))
        lowest = next(
            (item for item in brd.all_items() if title_key(item.title) == title_key(loop[-1])),
            None,
        )
        if lowest is not None:
            lowest.parent = ""
            lowest.parent_relation = ""
            lowest.parent_id = ""
            broken_cycles += 1

    # 3. Nothing unresolved reached the document.
    for _, item in published:
        if item.disposition in {"reject", "open_question"}:
            report(
                "UNRESOLVED_IN_DOCUMENT",
                f"A candidate dispositioned {item.disposition!r} is published as a requirement.",
                item.id,
            )
        if item.status == "rejected" and item.disposition not in {"keep", "reclassify"}:
            # `status` is the raw output of the correspondence check; `disposition` is the
            # decision made about it. An interpretive failure is deliberately not fatal -- the
            # anchoring check compares a statement written in business words against the code's
            # own identifiers, and a correctly written claim can fail it -- so a claim resolution
            # decided to keep is kept, and flagging it here would report a decision as a defect.
            report(
                "REJECTED_IN_DOCUMENT",
                "A claim the correspondence check rejected is published as a requirement.",
                item.id,
            )
        if item.requirement_type == "observation":
            report(
                "OBSERVATION_AS_REQUIREMENT",
                "An implementation observation is published as a stakeholder requirement.",
                item.id,
            )

    # 4. Business requirements are supported by what stands under them.
    supporters: Counter[str] = Counter()
    for _, item in published:
        if item.parent:
            supporters[title_key(item.parent)] += 1
    for item in brd.business_requirements:
        if supporters[title_key(item.title)] < MIN_SUPPORTING_CAPABILITIES:
            report(
                "UNSUPPORTED_BUSINESS_REQUIREMENT",
                f"Only {supporters[title_key(item.title)]} capability/capabilities stand under "
                "this outcome, so it restates a capability rather than summarising several.",
                item.id,
            )

    # 5. One parent each, and no unsettled conflict.
    for _, item in published:
        if item.cluster_conflict:
            report(
                "UNRESOLVED_CONFLICT",
                "Published while its cluster still records competing readings of one evidence set.",
                item.id,
            )

    # 6. Identifiers exist, and were stamped after synthesis rather than before it.
    for _, item in published:
        if not item.id:
            report("MISSING_IDENTIFIER", "Published without an identifier.", item.title)

    ordered = Counter(issue["code"] for issue in issues)
    messages = [
        f"Checked the finished set against {len(ordered) or 'no'} structural failure mode(s)."
        if issues
        else "The finished requirements set passed every structural check."
    ]
    if issues:
        messages.append(
            "Findings: " + ", ".join(f"{code} ({count})" for code, count in ordered.most_common())
            + ". These are reported rather than silently corrected, except a relation with no "
            "parent and a circular chain, which are cleared because they produce a document a "
            "consumer cannot walk."
        )

    return CapabilityResult(
        artifacts={"brd": brd, "synthesis_findings": {"issues": issues, "counts": dict(ordered)}},
        metrics={
            "synthesis_findings": len(issues),
            "relations_cleared": cleared,
            "cycles_broken": broken_cycles,
            **{f"finding_{code}": count for code, count in ordered.items()},
        },
        messages=messages,
    )


# What evidence class each requirement family rests on, and therefore what a reader should
# conclude from an empty one. This is the table that turns "the document has no non-functional
# requirements" into either "this product declares no limits" or "nothing in this pipeline can
# read the limits it declares" — two very different facts that looked identical for twenty runs.
FAMILY_EVIDENCE: dict[str, tuple[str, tuple[str, ...]]] = {
    "functional_requirements": ("an interaction boundary", ("ENDPOINT", "REGISTERED_ENTRY", "UI_ACTION", "CLI_ENTRY", "SCRIPT_ENTRY", "SCHEDULED_JOB")),
    "business_rules": ("a declared guard or schema constraint", ("DOMAIN_POLICY", "BUSINESS_RULE", "CONFIG_KEY")),
    "success_criteria": ("a fixed pass/fail condition in code", ("ERROR_HANDLER", "CONFIG_KEY")),
    "system_requirements": ("internal behaviour a capability depends on", ("FUNCTION", "METHOD")),
    "technical_constraints": ("a platform or protocol limit", ("CONFIG_KEY", "EXTERNAL_SERVICE")),
    "data_requirements": ("a declared schema", ("DATA_ENTITY", "DATA_FIELD", "DATA_INDEX", "DOMAIN_POLICY")),
    "integration_requirements": ("a declared external dependency", ("EXTERNAL_SERVICE", "EXTERNAL_SYMBOL")),
    "non_functional_requirements": ("a declared operating value", ("CONFIG_KEY",)),
    "reporting_requirements": ("a view, export or listing boundary", ("ENDPOINT", "UI_ACTION")),
}


@capability("report_family_coverage")
def report_family_coverage(ctx: CapabilityContext) -> CapabilityResult:
    """Say, per requirement family, whether it is empty because of the product or because of us.

    Gate 1 checks the graph's structure. Gate 2 checks that every surviving claim is grounded.
    **Neither ever checked that the document was complete**, so a document with seven of thirteen
    families permanently empty passed every check — every claim in it was individually
    well-evidenced, and nothing measured recall. Across twenty stored runs `business_rules`,
    `success_criteria`, `technical_constraints`, `data_requirements`, `integration_requirements`,
    `non_functional_requirements` and `assumptions` were empty every single time, and no artifact
    said so.

    The distinction this draws is the one that matters to a reader. A family with no requirements
    and **no evidence in the graph either** is an honest statement about the product: it declares
    no limits, or it integrates with nothing. A family with no requirements but with evidence
    sitting in the graph unused is a defect in this pipeline, and it is named as one here.

    Reported, never enforced. A thin document is a fact about a codebase as often as it is a bug,
    and deciding which is a person's job — this step's only duty is to make sure they are told.
    """
    brd = ctx.load("brd", ProductBRD)
    graph = ctx.load("graph", KnowledgeGraph)

    present: Counter[str] = Counter(entity.kind for entity in graph.entities)
    sections = {**brd.sections(), **brd.supplements()}

    families: list[dict[str, Any]] = []
    unevidenced = 0
    unused = 0
    for family, (description, kinds) in FAMILY_EVIDENCE.items():
        published = len(sections.get(family) or [])
        evidence = {kind: present.get(kind, 0) for kind in kinds if present.get(kind, 0)}
        if published:
            verdict = "populated"
        elif evidence:
            verdict = "evidence_unused"
            unused += 1
        else:
            verdict = "no_evidence_in_repository"
            unevidenced += 1
        families.append(
            {
                "family": family,
                "published": published,
                "rests_on": description,
                "evidence_available": evidence,
                "verdict": verdict,
            }
        )

    messages: list[str] = []
    if unused:
        names = [item["family"] for item in families if item["verdict"] == "evidence_unused"]
        messages.append(
            f"{unused} requirement family/families are empty although the graph carries evidence "
            f"they could rest on: {', '.join(names)}. That is a gap in this pipeline, not a "
            "statement about the product, and it is the one kind of thin section worth chasing."
        )
    if unevidenced:
        names = [
            item["family"] for item in families if item["verdict"] == "no_evidence_in_repository"
        ]
        messages.append(
            f"{unevidenced} requirement family/families are empty because the repository declares "
            f"nothing they could rest on: {', '.join(names)}. This is an honest result — the "
            "product states no such policy — and not a coverage failure."
        )
    if not unused and not unevidenced:
        messages.append("Every requirement family carries at least one published requirement.")

    return CapabilityResult(
        artifacts={"family_coverage": {"families": families}},
        metrics={
            "families_populated": sum(1 for item in families if item["verdict"] == "populated"),
            "families_evidence_unused": unused,
            "families_without_evidence": unevidenced,
        },
        messages=messages,
    )

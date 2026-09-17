"""Turn extracted behaviours into a requirement set at the right level of abstraction.

Everything here is deterministic. It runs after `assemble` has carried the fan-out findings
across, and before `prepare_claims` stamps identifiers, so the gate judges a consolidated set
rather than a transcript of the code.

The stage exists because a fan-out worker reasons about one boundary at a time and cannot see
that its "validate the payload" finding is the same claim another worker filed as "reject a
malformed request", that its "answer generated successfully" finding is an acceptance criterion
of the requirement above it, or that the helper it wrote up is an implementation detail of a
capability someone else already published. None of those are model failures; they are judgements
that need the whole set, and they are mechanical once the set exists.

Seven passes, in order, each one a separate decision:

0. **Rule classification.** A claim is a business rule only if something it cites was *declared* to
   be one — a constraint, a named constant, an explanatory sentence. A codebase branches on a
   thousand conditions and states a handful of rules, and typing every condition as a rule buries
   the handful. Demoted claims become internal behaviour, keeping every citation.
1. **Abstraction.** The declared basis decides the class, and the class decides the section. A
   behaviour whose only grounds are "the current code does this" becomes an observation.
2. **Acceptance criteria.** A finding that only asserts another requirement succeeded is folded
   into that requirement as a criterion rather than published beside it. This runs before the
   merge, because a success condition reads like the requirement it validates and would otherwise
   be absorbed into it as a near-duplicate — kept, but in the wrong place.
3. **Semantic grouping and merge.** Findings asserting the same thing are merged into the fullest
   one, keeping every citation and criterion. Within a tier first, then across tiers under a much
   stricter bar: one behaviour is routinely filed as a capability by one worker and as the
   internal behaviour behind it by another, and the reader gets two requirements for one thing in
   two different sections. The survivor names what it absorbed.
4. **Parameterised families.** What the merge cannot reach: statements too distinct to be
   duplicates yet identical in shape, the same sentence with one subject changed. Twenty of those
   are one requirement written twenty times, and they collapse into one naming every subject.
5. **Parent validation.** A link survives only if the relation is one the two tiers may hold.
   Links that rest on nothing but shared code are removed.
6. **Reporting.** Every demotion, merge, collapse and dropped link is counted and reported.

Nothing is deleted outright. A finding that is not a requirement becomes an observation or a
criterion; a duplicate is merged into its twin rather than discarded. The evidence survives in
every case, because losing reverse-engineering evidence is the one outcome worse than filing it
under the wrong heading.
"""

from __future__ import annotations

import re
from collections import Counter
from typing import Any

from agentic.capabilities.graph_ops import DECLARATION_KINDS
from agentic.capabilities.registry import CapabilityContext, CapabilityResult, capability
from agentic.schemas.core import KnowledgeGraph
from agentic.schemas.brd import (
    BASIS_TYPES,
    SECTION_TIERS,
    TIER_SECTIONS,
    BRDItem,
    ProductBRD,
    canonical_basis,
    canonical_tier,
    permitted_parents,
    permitted_relations,
    title_key,
)

# How alike two statements must read, as a share of the shorter one's content words, before they
# are treated as the same assertion. Deliberately higher than the threshold the gate uses to
# *warn* about a restatement: merging is destructive in a way that warning is not, so it demands
# more evidence. Two findings must also share a citation before either is considered.
MERGE_SIMILARITY = 0.65

# Words carried by almost every requirement sentence, so sharing them says nothing about sharing
# meaning. Domain-neutral on purpose: nothing here names a technology, a framework, or a subject.
FRAME_WORDS = frozenset(
    """a an and are as at be been by can for from in into is it its of on or shall should so
    that the their them they this to when which who will with able system user users product
    each any all not no only other another such via using use used""".split()
)

# Openings that assert another requirement worked rather than asserting a requirement. Matched as
# whole phrases against the normalised statement, so they cannot fire on a word in passing.
SUCCESS_PATTERNS = (
    # The adverb sits on either side of the verb — "is successfully stored", "is stored
    # successfully" — so match it alone rather than trying to enumerate the shapes.
    r"\bsuccessfully\b",
    r"\bsucceeds?\b",
    r"\bwithout (?:an )?error\b",
    r"\bis validated\b",
    r"\bconfirms? that\b",
    r"\bverif(?:y|ies) that\b",
)
SUCCESS_RE = re.compile("|".join(SUCCESS_PATTERNS), re.IGNORECASE)


def _normalise(text: str) -> str:
    return " ".join((text or "").split()).casefold()


def _content_words(statement: str) -> set[str]:
    words = {word.strip(".,;:'\"()") for word in _normalise(statement).split()}
    return {word for word in words if word and word not in FRAME_WORDS}


def _similarity(left: str, right: str) -> float:
    """Shared content words as a share of the shorter statement."""
    first, second = _content_words(left), _content_words(right)
    if not first or not second:
        return 0.0
    return len(first & second) / min(len(first), len(second))


# The modal a requirement sentence is built around, and the filler between it and the real verb.
# This is the shape the synthesis skill prescribes -- actor, action on an object, qualifier -- not
# a property of any domain or stack.
_MODAL = re.compile(r"\b(shall|must|should|will|can)\b", re.IGNORECASE)
# Also the permission verbs, and the object they take. "The system shall **allow** a user to
# submit a claim" and "A user shall **be able to** submit a claim" are the same requirement in two
# grammars, and the action in both is "submit". Reading "allow" as the action makes every
# permission-shaped statement in a document look like the same requirement as every other, and
# makes none of them look like the capability written the other way round -- which is worse in
# both directions at once.
_ENABLERS = re.compile(
    r"^(?:be\s+able\s+to|be\s+permitted\s+to|be\s+allowed\s+to|have\s+the\s+ability\s+to"
    r"|(?:allow|enable|permit|let|support)\s+(?:\w+\s+){0,3}?to)\s+",
    re.IGNORECASE,
)


def _action_of(statement: str) -> str:
    """The verb a requirement is about: the first content word after the modal.

    This is the word that decides what the requirement *is*. Everything around it -- the actor, the
    object, the purpose clause -- is routinely identical between two entirely different
    capabilities, because a requirements document is supposed to read consistently.
    """
    body = " ".join((statement or "").split())
    match = _MODAL.search(body)
    if not match:
        return ""
    rest = _ENABLERS.sub("", body[match.end():].strip())
    for word in rest.split():
        cleaned = word.strip(".,;:'\"()").casefold()
        if cleaned and cleaned not in FRAME_WORDS:
            return cleaned
    return ""


def _same_action(left: BRDItem, right: BRDItem) -> bool:
    """Whether two statements assert the same verb.

    A merge may never cross a difference here. "Staff shall be able to **delete** a supplier bill"
    and "Staff shall be able to **view** a supplier bill" share five of their six content words and
    score 0.83 against a merge threshold of 0.65 -- so word overlap alone folded a whole CRUD
    surface into one requirement, keeping whichever wording happened to sort last. Overlap measures
    how alike two sentences read; it cannot tell that one word carries the entire difference.
    """
    first, second = _action_of(left.statement), _action_of(right.statement)
    if not first or not second:
        return True  # nothing to compare; fall back to the wording score alone
    return first == second


def _spans(item: BRDItem) -> set[tuple]:
    return {(location.file, location.line_start, location.line_end) for location in item.evidence}


def _shares_evidence(left: BRDItem, right: BRDItem) -> bool:
    """True when two findings are candidates for being the same claim.

    Shared code was the only qualifier here, and it was too strict in exactly the case that
    matters. A fan-out worker sees one interaction boundary, so two workers describing the same
    capability -- one from an endpoint, one from the implementation behind it, or one from each of
    two modules -- cite different lines by construction. Their wording was therefore never
    compared and both were published.

    Belonging to the same repository-wide capability cluster is the other way in. The clustering
    stage has already decided those two candidates describe one capability, on their meaning
    rather than their location, so requiring them to overlap in the code as well would discard
    the finding it was run to produce.

    Neither route is sufficient alone: this only makes a pair eligible, and the wording still
    decides whether they are merged.
    """
    if left.cluster_id and left.cluster_id == right.cluster_id:
        # Unless the cluster records a conflict. Two readings of the same evidence that contradict
        # each other must not be folded into one statement: merging them would manufacture
        # agreement where the analysis found none, and bury the fact that one of them is wrong.
        return not (left.cluster_conflict or right.cluster_conflict)
    if _spans(left) & _spans(right):
        return True
    return bool(set(left.graph_path) & set(right.graph_path))


# --------------------------------------------------------------------------------------------
# 1. Abstraction
# --------------------------------------------------------------------------------------------


def _apply_abstraction(brd: ProductBRD) -> tuple[int, int, Counter]:
    """File every item at the level its declared basis implies.

    The basis is authoritative. Where a worker declared a basis and a type that disagree, the
    basis wins, because it is the question that was asked first and the one whose answer can be
    checked against the evidence. Where no basis was declared, the type implies one, so an older
    reply still lands somewhere sensible.
    """
    moved = 0
    demoted = 0
    bases: Counter = Counter()
    relocations: dict[str, list[BRDItem]] = {
        section: [] for section in brd.every_section()
    }

    for section, items in brd.every_section().items():
        for item in items:
            basis = canonical_basis(item.abstraction_basis)
            if basis is None:
                # No basis declared: infer one from whatever type the item claimed, so the two
                # fields agree from here on and the gate can check them against each other.
                tier = canonical_tier(item.requirement_type, default=SECTION_TIERS.get(section))
                basis = next(
                    (key for key, value in BASIS_TYPES.items() if value == tier),
                    "external_capability",
                )
                item.abstraction_basis = basis
            tier = BASIS_TYPES[basis]
            bases[basis] += 1

            if item.requirement_type != tier:
                item.requirement_type = tier

            target = TIER_SECTIONS[tier]
            # A success criterion is a slot rather than a tier. One that declared a basis of its
            # own is taken at its word; one that did not stays where it is for pass 3 to judge.
            if section == "success_criteria" and canonical_basis(item.abstraction_basis) in {
                None,
                "external_capability",
            }:
                target = section

            if target != section:
                moved += 1
                if tier == "observation":
                    demoted += 1
            relocations[target].append(item)

    for section, items in brd.every_section().items():
        items[:] = relocations[section]
    return moved, demoted, bases


# --------------------------------------------------------------------------------------------
# 2. Semantic grouping and merge
# --------------------------------------------------------------------------------------------


def _merge_into(keeper: BRDItem, other: BRDItem) -> None:
    """Fold one finding into its twin, losing nothing either of them established.

    The fuller statement wins, because a later step can never recover a detail an earlier one
    threw away. Everything else is a union: two workers looking at the same behaviour routinely
    cite different lines of it and notice different criteria, and both are true.
    """
    # Record what was folded in before the wording changes, so the note names the claim as the
    # reader would have seen it. A merge that leaves no trace is indistinguishable from a claim
    # having been dropped, and the difference matters to anyone auditing coverage.
    absorbed_title = (other.title or "").strip()
    if absorbed_title and title_key(absorbed_title) != title_key(keeper.title):
        if absorbed_title not in keeper.superseded:
            keeper.superseded.append(absorbed_title)
    for title in other.superseded:
        if title not in keeper.superseded:
            keeper.superseded.append(title)

    if len(other.statement) > len(keeper.statement):
        keeper.statement = other.statement
        keeper.title = other.title or keeper.title
    known = _spans(keeper)
    for location in other.evidence:
        if (location.file, location.line_start, location.line_end) not in known:
            keeper.evidence.append(location)
    for name in other.graph_path:
        if name not in keeper.graph_path:
            keeper.graph_path.append(name)
    seen = {_normalise(text) for text in keeper.acceptance_criteria}
    for criterion in other.acceptance_criteria:
        if _normalise(criterion) not in seen:
            seen.add(_normalise(criterion))
            keeper.acceptance_criteria.append(criterion)
    for field in ("persona", "business_value"):
        if not getattr(keeper, field) and getattr(other, field):
            setattr(keeper, field, getattr(other, field))
    for field in ("assumptions", "open_questions"):
        target = getattr(keeper, field)
        for entry in getattr(other, field):
            if entry not in target:
                target.append(entry)
    keeper.parent = keeper.parent or other.parent
    keeper.parent_relation = keeper.parent_relation or other.parent_relation
    # Two findings agreeing is not more certain than the more cautious of them was.
    keeper.confidence = min(keeper.confidence, other.confidence)


def _merge_duplicates(brd: ProductBRD) -> int:
    """Merge findings that assert the same thing, within a tier.

    Comparison is by meaning rather than by exact wording, which is what the previous pass could
    not do: it matched normalised strings, so the same capability described twice in different
    words survived as two requirements. Merging is confined to one tier — an outcome and the
    capability realising it legitimately rest on the same lines and read alike, and collapsing
    them would delete the outcome and keep the mechanism.
    """
    merged = 0
    for _, items in brd.every_section().items():
        kept: list[BRDItem] = []
        for item in items:
            twin = next(
                (
                    candidate
                    for candidate in kept
                    if _shares_evidence(candidate, item)
                    and _same_action(candidate, item)
                    and _similarity(candidate.statement, item.statement) >= MERGE_SIMILARITY
                ),
                None,
            )
            if twin is None:
                kept.append(item)
                continue
            _merge_into(twin, item)
            merged += 1
        items[:] = kept
    return merged


# How alike two statements filed at *different* levels must read before they are treated as one
# claim. Deliberately far above the same-tier threshold, and the reason is measurable: an outcome
# and the capability realising it score 0.67 against each other, because they share their actor
# and their object and differ only in the verb. At the same-tier threshold of 0.65 a cross-tier
# merge would delete the outcome and keep the mechanism, which is the opposite of what a
# requirements document is for.
CROSS_TIER_SIMILARITY = 0.85

# Most abstract first. When one claim is filed twice at different levels, the more abstract survives
# -- a document should state a requirement at the highest level that is true of it, and the
# concrete twin is the mechanism, which the surviving item keeps as evidence either way.
TIER_ORDER = (
    "business",
    "functional",
    "system",
    "business_rule",
    "technical_constraint",
    "non_functional",
    "observation",
)


def _names_as_parent(left: BRDItem, right: BRDItem) -> bool:
    """Whether either claim declares the other as its parent.

    A declared link is the codebase's own statement that these two are a hierarchy rather than a
    repetition, and it settles the question outright. Titles are what a parent link is written
    with, which is also why two items sharing a title cannot be parent and child.
    """
    keys = (title_key(left.title), title_key(right.title))
    return (left.parent and title_key(left.parent) == keys[1]) or (
        right.parent and title_key(right.parent) == keys[0]
    )


def _merge_across_tiers(brd: ProductBRD) -> int:
    """Merge one claim filed twice at different levels.

    Two workers seeing the same behaviour routinely disagree about what it is: one files a
    capability, the other the internal behaviour behind it, and both are published. The reader
    gets two requirements for one thing, in different sections, with no indication they are the
    same.

    Two guards keep this from eating the hierarchy, which is the real risk. A declared parent link
    means the pair is a hierarchy and is never touched; and the similarity bar is far higher than
    the same-tier one, because the tiers disagreeing is itself evidence the two workers saw
    different things. An identical title bypasses the bar, since titles are required to be
    distinct and are what parent links are written with -- two items sharing one are the same
    claim, not a parent and its child.
    """
    sections = brd.every_section()
    rank = {tier: index for index, tier in enumerate(TIER_ORDER)}

    # Flatten once, keeping each item's section so the survivor can stay where it belongs.
    entries: list[tuple[str, BRDItem]] = [
        (section, item) for section, items in sections.items() for item in items
    ]
    absorbed: set[int] = set()
    merged = 0

    for position, (section, item) in enumerate(entries):
        if position in absorbed:
            continue
        for other_position in range(position + 1, len(entries)):
            if other_position in absorbed:
                continue
            other_section, other = entries[other_position]
            if other_section == section:
                continue  # the same-tier pass owns this
            if _names_as_parent(item, other):
                continue

            same_title = title_key(item.title) == title_key(other.title)
            alike = (
                _shares_evidence(item, other)
                and _same_action(item, other)
                and _similarity(item.statement, other.statement) >= CROSS_TIER_SIMILARITY
            )
            if not (same_title or alike):
                continue

            keeper, loser = item, other
            if rank.get(other.tier(other_section), 99) < rank.get(item.tier(section), 99):
                keeper, loser = other, item
            _merge_into(keeper, loser)
            absorbed.add(other_position if keeper is item else position)
            merged += 1
            if keeper is other:
                break  # this item is gone; nothing further can merge into it

    if merged:
        survivors = {id(item) for index, (_, item) in enumerate(entries) if index not in absorbed}
        for _, items in sections.items():
            items[:] = [item for item in items if id(item) in survivors]
    return merged


# --------------------------------------------------------------------------------------------
# 3. Acceptance criteria
# --------------------------------------------------------------------------------------------


def _looks_like_a_success_condition(item: BRDItem) -> bool:
    """True when a statement asserts that something else worked rather than asserting a need.

    Matched on the shape of the sentence, not on any vocabulary belonging to a domain: a
    requirement says what must be possible, a criterion says how you would know it happened.
    """
    return bool(SUCCESS_RE.search(_normalise(item.statement)))


def _extract_acceptance_criteria(brd: ProductBRD) -> int:
    """Fold success conditions into the requirement they validate.

    A separate requirement asserting that another requirement succeeds inflates the count and
    tells a reviewer nothing they could act on. As a criterion on the requirement itself it is
    exactly what a reviewer signs off against, so the information is promoted in usefulness even
    as it is demoted in status.
    """
    by_title = {
        title_key(item.title): item
        for section, items in brd.sections().items()
        if section != "success_criteria"
        for item in items
    }
    folded = 0

    for section in ("success_criteria", "functional_requirements", "system_requirements"):
        items = brd.every_section()[section]
        kept: list[BRDItem] = []
        for item in items:
            is_criterion = section == "success_criteria" or _looks_like_a_success_condition(item)
            host = by_title.get(title_key(item.parent or ""))
            if host is None and is_criterion:
                # No named host: the requirement it rests on is the one sharing its evidence.
                host = next(
                    (
                        candidate
                        for candidate in by_title.values()
                        if candidate is not item and _shares_evidence(candidate, item)
                    ),
                    None,
                )
            if not is_criterion or host is None or host is item:
                kept.append(item)
                continue
            text = " ".join(item.statement.split())
            if _normalise(text) not in {_normalise(c) for c in host.acceptance_criteria}:
                host.acceptance_criteria.append(text)
            # The citation moves with the criterion, so nothing stops being traceable.
            known = _spans(host)
            for location in item.evidence:
                if (location.file, location.line_start, location.line_end) not in known:
                    host.evidence.append(location)
            folded += 1
        items[:] = kept
    return folded


# --------------------------------------------------------------------------------------------
# 4. Parent validation
# --------------------------------------------------------------------------------------------


def _validate_hierarchy(brd: ProductBRD) -> tuple[int, int]:
    """Keep only links whose relation the two tiers may actually hold.

    Parenting used to be inferred from shared graph nodes, which meant the hierarchy recorded
    which code called which — a fact the traceability matrix already carries, and one that says
    nothing about whether a claim refines another. A link now has to name its relation and be
    between tiers that may hold it. Anything else is removed, leaving the item unattached and
    visible to the gate rather than filed somewhere plausible-looking.
    """
    # A parent's tier is the section holding it. Reading `requirement_type` and defaulting to
    # "functional" made an unclassified outcome look like a capability, which then failed the
    # very check this pass exists to apply.
    titles: dict[str, tuple[BRDItem, str]] = {}
    for holding, held in brd.every_section().items():
        holding_tier = SECTION_TIERS.get(holding, "functional")
        for entry in held:
            titles.setdefault(
                title_key(entry.title),
                (entry, canonical_tier(entry.requirement_type, default=holding_tier)),
            )

    dropped = 0
    defaulted = 0
    for section, items in brd.every_section().items():
        tier = SECTION_TIERS.get(section, "functional")
        allowed_tiers = permitted_parents(tier)
        allowed_relations = permitted_relations(tier)
        for item in items:
            if not item.parent:
                continue
            found = titles.get(title_key(item.parent))
            if found is None or found[0] is item:
                item.parent, item.parent_relation = None, None
                dropped += 1
                continue
            _, parent_tier = found
            relation = (item.parent_relation or "").strip().lower()
            if parent_tier not in allowed_tiers:
                # The named parent is real but cannot stand in any relation to this item.
                item.parent, item.parent_relation = None, None
                dropped += 1
                continue
            if relation not in allowed_relations:
                if not relation:
                    # A link with a permitted parent but no stated relation is kept under the
                    # only relation its tier can hold, when that is unambiguous.
                    if len(allowed_relations) == 1:
                        item.parent_relation = allowed_relations[0]
                        defaulted += 1
                        continue
                    item.parent_relation = allowed_relations[0]
                    defaulted += 1
                    continue
                item.parent, item.parent_relation = None, None
                dropped += 1
    return dropped, defaulted


# --------------------------------------------------------------------------------------------
# 0. Rule promotion
# --------------------------------------------------------------------------------------------

# Attributes that mark a finding as *declared* policy rather than incidental control flow.
#
# An extractor sets `rule_source` when it read a constraint somebody wrote down as a constraint --
# a schema CHECK, a unique index -- as opposed to reconstructing one from a branch. `rationale`
# carries adjacent explanatory prose. Neither is specific to a language: an adapter for any stack
# earns a promotion by recording the same attribute, and gets no promotion by not recording it.
DOMAIN_SIGNAL_ATTRIBUTES = ("rule_source", "rationale", "statement", "allowed_values")


def _evidence_index(graph: Any) -> dict[str, list[Any]]:
    """Entities grouped by the file they occupy, for looking up what a citation points at."""
    by_file: dict[str, list[Any]] = {}
    for entity in getattr(graph, "entities", []) or []:
        if entity.location is None:
            continue
        by_file.setdefault(entity.location.file, []).append(entity)
    return by_file


def _domain_bearing(item: BRDItem, by_file: dict[str, list[Any]]) -> bool:
    """Whether anything this item cites is declared policy rather than a branch in the code.

    This is the whole rule classifier, and it is deliberately expressed as a promotion test rather
    than as a guard detector. Detecting guards means recognising null checks, empty checks and
    not-found branches, which cannot be done without knowing a language's syntax -- and putting
    that knowledge in shared code is exactly what is forbidden, quite apart from it being endless.

    Inverting the question removes the need. Nothing is a business rule unless something it cites
    was *declared* to be one, so a guard fails the test by having nothing to show, without this
    code ever having to know what a guard looks like. A repository in an unparsed language is
    judged by the same test on the same evidence.
    """
    for citation in item.evidence or []:
        cite_start = citation.line_start or 0
        cite_end = citation.line_end or cite_start
        for entity in by_file.get(citation.file, []):
            location = entity.location
            if location is None:
                continue
            # Overlap, not equality: a citation names a span and an entity occupies one, and the
            # two rarely coincide exactly.
            entity_start = location.line_start or 0
            entity_end = location.line_end or entity_start
            if cite_start > entity_end or entity_start > cite_end:
                continue

            if entity.kind in DECLARATION_KINDS:
                return True
            attributes = entity.attributes or {}
            if any(attributes.get(name) for name in DOMAIN_SIGNAL_ATTRIBUTES):
                return True
    return False


def _classify_rules(brd: ProductBRD, graph: Any) -> tuple[int, int]:
    """Demote rule claims that no declared constraint, constant or prose supports.

    A codebase states a handful of rules and branches on a thousand conditions. Typing every
    condition as a business rule buries the handful: in one measured run 1,642 rule entities were
    extracted and 1,632 of them were ordinary conditionals, nearly half plain null guards. A
    reader given that document cannot find the nine constraints that were actually decided.

    Demoted items are not discarded. They become internal behaviour -- still published, still
    carrying every citation -- because how the code guards itself is true and worth recording. It
    is simply not a rule the business chose.
    """
    by_file = _evidence_index(graph)
    kept = 0
    demoted = 0

    for items in brd.every_section().values():
        for item in items:
            basis = canonical_basis(item.abstraction_basis)
            if basis != "domain_constraint" and item.requirement_type != "business_rule":
                continue
            if _domain_bearing(item, by_file):
                kept += 1
                continue
            item.abstraction_basis = "internal_behaviour"
            item.requirement_type = BASIS_TYPES["internal_behaviour"]
            note = (
                "Recorded as internal behaviour rather than a business rule: the cited evidence "
                "is a condition in the code, with no declared constraint, named constant or "
                "explanatory prose establishing it as a rule the business chose."
            )
            if note not in (item.assumptions or []):
                item.assumptions = [*(item.assumptions or []), note]
            demoted += 1
    return kept, demoted


# --------------------------------------------------------------------------------------------
# 0b. Parameterised families
# --------------------------------------------------------------------------------------------

# How many members a family needs before it is collapsed. Two statements differing in one word are
# usually two genuine requirements; a dozen are one requirement someone wrote out a dozen times.
FAMILY_MINIMUM = 3


def _tokens(statement: str) -> list[str]:
    return _normalise(statement).split()


def _collapse_parameterised(brd: ProductBRD) -> tuple[int, int]:
    """Fold "the same sentence with one word changed" into one requirement listing the variants.

    A codebase that raises the same generic failure for twenty resources produces twenty findings
    that differ only in the resource named. Published separately they read as twenty rules and
    bury the handful of real ones; published as one, with the twenty subjects named and all twenty
    citations kept, they say the same thing and can be reviewed.

    The test is purely structural -- same sentence, one differing position -- so it cannot know or
    care what the varying token means, and works the same on any repository.
    """
    families = 0
    absorbed = 0

    for items in brd.every_section().values():
        if len(items) < FAMILY_MINIMUM:
            continue

        # Group by shape: the token list with one position blanked, plus the length, so only
        # statements that could differ in exactly one place are ever compared.
        buckets: dict[tuple, list[tuple[int, BRDItem]]] = {}
        for index, item in enumerate(items):
            words = _tokens(item.statement)
            if len(words) < 4:
                continue
            for position in range(len(words)):
                key = (len(words), position, tuple(words[:position]), tuple(words[position + 1 :]))
                buckets.setdefault(key, []).append((index, item))

        claimed: set[int] = set()
        keep: dict[int, BRDItem] = {}
        for key, members in buckets.items():
            members = [(index, item) for index, item in members if index not in claimed]
            if len(members) < FAMILY_MINIMUM:
                continue
            # The one position that varies must not be the verb. "Staff shall be able to record /
            # view / update / delete / archive a supplier bill" is five statements in the same
            # frame differing in exactly one token, which is what this pass looks for -- and it is
            # five separate capabilities, not one requirement written five times. The structural
            # test cannot tell the two cases apart on shape alone, so the action is checked
            # explicitly. A family whose members assert different verbs is not a family.
            if len({_action_of(item.statement) for _, item in members}) > 1:
                continue

            position = key[1]
            variants: list[str] = []
            for _, item in members:
                word = _tokens(item.statement)[position]
                if word not in variants:
                    variants.append(word)
            if len(variants) < FAMILY_MINIMUM:
                continue

            lead_index, lead = members[0]
            words = _tokens(lead.statement)
            # Rebuild from the lead's own casing so the sentence still reads naturally, replacing
            # only the one position that varies.
            original = lead.statement.split()
            if len(original) == len(words):
                original[position] = ", ".join(variants[:-1]) + f" or {variants[-1]}"
                lead.statement = " ".join(original)

            for _, item in members[1:]:
                for citation in item.evidence:
                    if citation not in lead.evidence:
                        lead.evidence.append(citation)
                for criterion in item.acceptance_criteria or []:
                    if criterion not in (lead.acceptance_criteria or []):
                        lead.acceptance_criteria = [*(lead.acceptance_criteria or []), criterion]
                for path in item.graph_path:
                    if path not in lead.graph_path:
                        lead.graph_path.append(path)

            note = (
                f"Collapsed from {len(members)} findings that differed only in the subject named. "
                "Every citation from all of them is kept."
            )
            if note not in (lead.assumptions or []):
                lead.assumptions = [*(lead.assumptions or []), note]

            keep[lead_index] = lead
            claimed.update(index for index, _ in members)
            families += 1
            absorbed += len(members) - 1

        if claimed:
            items[:] = [
                keep.get(index, item)
                for index, item in enumerate(items)
                if index not in claimed or index in keep
            ]

    return families, absorbed


# How alike a criterion may read to the requirement it hangs from before it is checking nothing.
# A criterion is meant to be narrower than the requirement -- an observable condition, not the
# claim restated -- so a high score here means the two say the same thing.
CRITERION_RESTATES = 0.80


def _consolidate_criteria(brd: ProductBRD) -> tuple[int, int]:
    """Tidy the acceptance criteria of each requirement, after everything that adds to them.

    Merging is what makes this necessary: folding two findings into one unions their criteria, and
    two workers describing one capability routinely wrote the same condition in slightly different
    words. The result is a requirement with four criteria, two of which are the same check and one
    of which is the requirement itself with "shall" removed.

    Runs last among the consolidation passes for that reason -- criteria are still arriving until
    the merge and the family collapse have finished.
    """
    removed_duplicates = 0
    removed_restatements = 0

    for section, items in brd.every_section().items():
        for item in items:
            if not item.acceptance_criteria:
                continue
            statement = _normalise(item.statement).rstrip(".")
            kept: list[str] = []
            seen: list[str] = []
            for criterion in item.acceptance_criteria:
                text = _normalise(criterion).rstrip(".")
                if not text:
                    continue
                if any(text == other or _similarity(text, other) >= 0.95 for other in seen):
                    removed_duplicates += 1
                    continue
                if text == statement or _similarity(text, statement) >= CRITERION_RESTATES:
                    removed_restatements += 1
                    continue
                seen.append(text)
                kept.append(criterion.strip())
            item.acceptance_criteria = kept

    return removed_duplicates, removed_restatements


# --------------------------------------------------------------------------------------------
# The capability
# --------------------------------------------------------------------------------------------


def _duplicate_titles(brd: ProductBRD) -> int:
    """Published claims sharing a title with another. The measure this pass exists to move."""
    seen = Counter(
        title_key(item.title)
        for section, items in brd.every_section().items()
        if section != "observations"
        for item in items
    )
    return sum(count - 1 for count in seen.values() if count > 1)


def _counts(brd: ProductBRD) -> dict[str, int]:
    return {section: len(items) for section, items in brd.every_section().items()}


@capability("consolidate_requirements")
def consolidate_requirements(ctx: CapabilityContext) -> CapabilityResult:
    """Classify by abstraction, merge duplicates, extract criteria, and validate the hierarchy."""
    brd = ctx.load("brd", ProductBRD)
    before = _counts(brd)
    received = sum(before.values())

    # Rule classification runs first, because it changes the basis that everything after it keys
    # off. Doing it later would mean the abstraction pass had already filed a guard under business
    # rules and the merge pass had already grouped it with real constraints.
    rules_kept = rules_demoted = 0
    if getattr(ctx.settings, "rule_classification", True):
        graph = ctx.load("graph", KnowledgeGraph)
        rules_kept, rules_demoted = _classify_rules(brd, graph)

    moved, demoted, bases = _apply_abstraction(brd)
    # Criteria are extracted before duplicates are merged, which is a deliberate departure from
    # the obvious order. A success condition reads very like the requirement it validates — same
    # actor, same object — so the merge pass sees a near-duplicate and absorbs it into the
    # statement. The information survives, but in the wrong place: it should be a criterion a
    # reviewer can tick off, not a clause inside the requirement it was proving. Taking criteria
    # out of the pool first means similarity is never measured against them.
    folded = _extract_acceptance_criteria(brd)
    merged = _merge_duplicates(brd)
    # Cross-tier last among the merges, so the cheap same-tier pass has already collapsed what it
    # can and the stricter comparison runs over fewer, fuller claims.
    cross_tier = 0
    if getattr(ctx.settings, "dedup_pass", True):
        cross_tier = _merge_across_tiers(brd)
    # After the merge, because near-duplicates are the merge's job and a family is what remains:
    # statements too different to merge yet identical in shape.
    families = absorbed = 0
    if getattr(ctx.settings, "collapse_parameterised", True):
        families, absorbed = _collapse_parameterised(brd)
    duplicate_criteria, restating_criteria = _consolidate_criteria(brd)
    dropped_links, defaulted_links = _validate_hierarchy(brd)

    after = _counts(brd)
    published = sum(count for section, count in after.items() if section != "observations")

    messages = [
        f"Classified {received} finding(s) by abstraction: "
        + ", ".join(f"{count} {basis}" for basis, count in bases.most_common())
        + "."
    ]
    if demoted:
        messages.append(
            f"{demoted} finding(s) rested on nothing but what the current code happens to do and "
            "are recorded as implementation observations, not requirements. Their evidence is "
            "kept and published in the traceability appendix."
        )
    if rules_demoted:
        messages.append(
            f"{rules_demoted} finding(s) claimed to be business rules but cited only a condition "
            "in the code, with no declared constraint, named constant or explanatory prose behind "
            f"it. They are published as internal behaviour instead. {rules_kept} rule(s) are "
            "supported by declared policy and kept as rules."
        )
    if merged:
        messages.append(
            f"Merged {merged} finding(s) that asserted something an earlier finding in the same "
            "tier already asserted, keeping the fuller wording and every citation from both."
        )
    if cross_tier:
        messages.append(
            f"Merged {cross_tier} claim(s) that had been filed twice at different levels. The "
            "more abstract wording survives and names the one it absorbed, so a reader can see "
            "the two were folded together rather than one being lost."
        )
    if folded:
        messages.append(
            f"Folded {folded} success condition(s) into the acceptance criteria of the "
            "requirement they validate, rather than publishing them as requirements."
        )
    if families:
        messages.append(
            f"Collapsed {absorbed} finding(s) into {families} parameterised requirement(s). Each "
            "family was the same sentence with one subject changed, which reads as many rules and "
            "is one; every citation is kept on the surviving statement."
        )
    if duplicate_criteria or restating_criteria:
        messages.append(
            f"Removed {duplicate_criteria} repeated acceptance criterion/criteria and "
            f"{restating_criteria} that restated the requirement they hang from. A criterion that "
            "says what the requirement says checks nothing, and merging two findings routinely "
            "produces both."
        )
    if dropped_links:
        messages.append(
            f"Removed {dropped_links} parent link(s) that named no relation the two levels can "
            "hold. Those items are published unattached for the gate to report, which is honest "
            "about the gap rather than inventing a hierarchy from shared code."
        )

    metrics: dict[str, Any] = {
        "findings_received": received,
        "requirements_published": published,
        "observations_retained": after.get("observations", 0),
        "rules_supported_by_declared_policy": rules_kept,
        "rules_demoted_to_internal_behaviour": rules_demoted,
        "refiled_by_abstraction": moved,
        "demoted_to_observation": demoted,
        "duplicates_merged": merged,
        "cross_tier_duplicates_merged": cross_tier,
        "duplicate_titles_remaining": _duplicate_titles(brd),
        "parameterised_families": families,
        "findings_absorbed_into_families": absorbed,
        "criteria_extracted": folded,
        "duplicate_criteria_removed": duplicate_criteria,
        "restating_criteria_removed": restating_criteria,
        "parent_links_dropped": dropped_links,
        "parent_relations_defaulted": defaulted_links,
        **{f"count_{section}": count for section, count in after.items()},
        **{f"basis_{basis}": count for basis, count in bases.items()},
    }
    return CapabilityResult(artifacts={"brd": brd}, metrics=metrics, messages=messages)

"""Repository-wide semantic clustering of requirement candidates.

This is the stage the pipeline did not have, and its absence is why the output reads as a behaviour
inventory rather than a requirements document.

Every comparison before this point required two candidates to *share code* -- the same citation
span, or a node in common on the call path -- before their wording was even looked at. Fan-out
workers analyse one interaction boundary each, so two workers describing the same capability from
an endpoint and from the implementation behind it cite different lines by construction, and were
therefore never compared. The same capability found in two modules survived twice. Nothing ever
asked whether two candidates *mean* the same thing.

Clustering here is deliberately deterministic and evidence-blind. It reads what a candidate says,
reduces it to a semantic signature, and groups candidates whose signatures agree, wherever in the
repository they came from. Nothing about a language, a framework, or a subject area enters into
it: the signature is derived from the shape of a requirement sentence, which the synthesis skill
already prescribes -- an actor, an action on an object, and a qualifier.

The clusters are then the bounded unit the rest of synthesis needs. Deduplication merges within a
cluster without needing shared evidence; the elevation step binds business outcomes to cluster
identifiers rather than enumerating every capability title, which is what keeps its output from
growing with the repository.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any, Iterable

from agentic.capabilities.registry import CapabilityContext, CapabilityResult, capability
from agentic.schemas.brd import BRDItem, title_key

# ---------------------------------------------------------------------------------------------
# Sentence shape
# ---------------------------------------------------------------------------------------------

# The modal that separates the actor from what it must do. A requirement sentence is built around
# one, which is a property of how requirements are written rather than of any domain: the synthesis
# skill asks for "actor, action and object, qualifier" in that order.
_MODAL = re.compile(r"\b(shall|must|should|will|can)\b", re.IGNORECASE)

# Phrases that sit between the modal and the real verb and carry no meaning of their own.
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

# Trailing clauses that state *why*, not *what*. Two candidates differing only in their purpose
# clause are the same capability, so the signature is taken from the part before it.
_PURPOSE = re.compile(
    r"\b(?:so\s+that|in\s+order\s+to|so\s+as\s+to|thereby|which\s+allows)\b", re.IGNORECASE
)

_WORD = re.compile(r"[A-Za-z][A-Za-z0-9]*")

# Words carried by nearly every requirement sentence. Sharing one says nothing about sharing
# meaning, so they are excluded from every signature and every similarity. Domain-neutral: nothing
# here names a technology, a framework, or a subject.
STOP_WORDS = frozenset(
    """a an and any are as at be been being by can could each either for from had has have if in
    into is it its may might must of on only or shall should so such than that the their them then
    there these they this those to under upon use used using was were what when where which while
    who whom will with within without would able ability system user users product provide provides
    provided support supports allow allows ensure ensures given new existing valid invalid other
    another all not no more most least own same via per each every both any""".split()
)

# Suffixes stripped when two words are compared. Crude by design: a full stemmer would be a
# dependency and a language commitment, while this collapses the endings that actually differ
# between a requirement title and the identifier behind it -- "adjustment" against "adjust",
# "reservations" against "reserve".
_SUFFIXES = ("ations", "ation", "ements", "ement", "ings", "ing", "ers", "er", "ies", "es", "s")


def stem(word: str) -> str:
    """A word reduced far enough that its inflections collapse together."""
    lowered = word.casefold()
    for suffix in _SUFFIXES:
        if len(lowered) - len(suffix) >= 4 and lowered.endswith(suffix):
            base = lowered[: -len(suffix)]
            return base[:-1] if suffix == "ies" and base.endswith("i") else base
    return lowered


def content_terms(text: str) -> set[str]:
    """The words in a phrase that carry its meaning, stemmed."""
    return {
        stem(word)
        for word in _WORD.findall(text or "")
        if len(word) > 2 and word.casefold() not in STOP_WORDS
    }


@dataclass
class Signature:
    """What a requirement sentence is about, reduced to something comparable.

    `actor`, `action` and `object_terms` come from the sentence's own shape rather than from any
    vocabulary held here, so a repository in any domain produces a signature the same way.
    """

    actor: str = ""
    action: str = ""
    object_terms: frozenset[str] = frozenset()
    all_terms: frozenset[str] = frozenset()

    @property
    def head(self) -> tuple[str, frozenset[str]]:
        return (self.action, self.object_terms)

    def as_dict(self) -> dict[str, Any]:
        return {
            "actor": self.actor,
            "action": self.action,
            "object": sorted(self.object_terms),
        }


def signature_of(item: BRDItem) -> Signature:
    """Read actor, action and object off a requirement statement.

    Falls back to the title where a statement is not shaped like a requirement, which keeps a
    malformed candidate comparable instead of dropping it out of clustering entirely.
    """
    statement = " ".join((item.statement or "").split())
    body = _PURPOSE.split(statement)[0] if statement else ""

    actor, action, rest = "", "", body
    match = _MODAL.search(body)
    if match:
        actor = body[: match.start()].strip(" ,.;:")
        rest = _ENABLERS.sub("", body[match.end() :].strip())

    words = _WORD.findall(rest)
    for word in words:
        if word.casefold() not in STOP_WORDS:
            action = stem(word)
            break

    object_terms = content_terms(rest) - ({action} if action else set())
    if not object_terms:
        object_terms = content_terms(item.title)

    all_terms = content_terms(statement) | content_terms(item.title)
    return Signature(
        actor=" ".join(actor.split()).casefold(),
        action=action,
        object_terms=frozenset(object_terms),
        all_terms=frozenset(all_terms),
    )


def overlap(left: Signature, right: Signature) -> float:
    """Shared meaning as a share of the narrower candidate."""
    if not left.all_terms or not right.all_terms:
        return 0.0
    shared = left.all_terms & right.all_terms
    return len(shared) / min(len(left.all_terms), len(right.all_terms))


# ---------------------------------------------------------------------------------------------
# Relations
# ---------------------------------------------------------------------------------------------

# How alike two candidates must read before they are called the same claim. Higher than the
# in-tier merge threshold because this comparison no longer requires shared evidence: without that
# corroboration, the wording has to carry the whole judgement.
DUPLICATE = 0.90
MERGEABLE = 0.72
OVERLAPPING = 0.55

# How much of the narrower candidate's meaning must be contained in the broader one before the
# pair is read as a hierarchy rather than a repetition.
CONTAINMENT = 0.80

# Two candidates read off the *same* code that disagree about what it does. Not a duplicate and
# not a hierarchy: one of them is wrong, and publishing both states two incompatible things about
# one piece of evidence. The threshold is the wording score below which two readings of identical
# evidence have stopped describing the same behaviour.
CONFLICT_DIVERGENCE = 0.45


def _same_evidence(left: BRDItem, right: BRDItem) -> bool:
    """Whether two candidates were written from the same lines.

    Exact span identity, not overlap. A handler and the function it calls overlap constantly and
    are not competing readings of one thing; two workers handed the same span are.
    """
    def spans(item: BRDItem) -> set[tuple]:
        return {
            (location.file, location.line_start, location.line_end) for location in item.evidence
        }

    mine, theirs = spans(left), spans(right)
    return bool(mine) and mine == theirs


def relate(left: BRDItem, right: BRDItem, left_sig: Signature, right_sig: Signature) -> str:
    """What one candidate is to another.

    The order of these tests matters. Containment is checked before similarity because a broad
    capability and the narrow one beneath it share most of the narrow one's words -- scoring them
    on similarity alone reads a hierarchy as a duplicate and deletes the parent.
    """
    score = overlap(left_sig, right_sig)
    same_tier = left.requirement_type == right.requirement_type

    # Checked before anything else, and before the similarity floor, because a conflict is defined
    # by the readings being *far apart* -- it lives below the threshold where the other relations
    # start looking. Two workers given the same span who describe different behaviour, or who
    # disagree about what kind of thing it is, have not found two requirements.
    if _same_evidence(left, right) and (
        score < CONFLICT_DIVERGENCE
        or (left_sig.action and right_sig.action and left_sig.action != right_sig.action
            and not same_tier)
    ):
        return "conflicting"

    if score < OVERLAPPING:
        return "independent"

    narrower, broader = sorted((left_sig, right_sig), key=lambda s: len(s.all_terms))
    contained = (
        len(narrower.all_terms & broader.all_terms) / len(narrower.all_terms)
        if narrower.all_terms
        else 0.0
    )
    # A hierarchy needs a genuine difference in breadth. Two sentences of the same length that
    # contain one another are a restatement, not a parent and a child.
    broader_enough = len(broader.all_terms) >= len(narrower.all_terms) + 3

    if score >= DUPLICATE and same_tier:
        return "duplicate"
    if contained >= CONTAINMENT and broader_enough:
        return "parent_child"
    if score >= MERGEABLE and same_tier:
        return "mergeable"
    if score >= MERGEABLE and not same_tier:
        # The same behaviour seen from an interface and from the code behind it. Not a duplicate,
        # because the tiers say different things about it; not independent either.
        return "parent_child"
    return "overlapping"


# ---------------------------------------------------------------------------------------------
# Clustering
# ---------------------------------------------------------------------------------------------


@dataclass
class Candidate:
    item: BRDItem
    section: str
    signature: Signature
    index: int


@dataclass
class Cluster:
    cluster_id: str
    relation: str = "independent"
    members: list[Candidate] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        lead = self.members[0]
        tiers = sorted({member.item.requirement_type for member in self.members})
        files = sorted(
            {
                location.file
                for member in self.members
                for location in member.item.evidence
            }
        )
        return {
            "cluster_id": self.cluster_id,
            "relation": self.relation,
            "size": len(self.members),
            "tiers": tiers,
            "lead_title": lead.item.title,
            "titles": [member.item.title for member in self.members],
            "signature": lead.signature.as_dict(),
            "evidence_files": files[:12],
            "distinct_files": len(files),
        }


def _index_by_term(candidates: list[Candidate]) -> dict[str, list[int]]:
    """Which candidates mention each term.

    Comparing every candidate with every other is quadratic, and a large repository produces
    thousands. Two candidates that share no meaningful word cannot cluster, so only those sharing
    at least one are ever compared. The common terms are skipped, since a word appearing in most
    candidates buckets everything together and buys nothing.
    """
    postings: dict[str, list[int]] = defaultdict(list)
    for position, candidate in enumerate(candidates):
        for term in candidate.signature.all_terms:
            postings[term].append(position)

    ceiling = max(8, len(candidates) // 4)
    return {term: rows for term, rows in postings.items() if 1 < len(rows) <= ceiling}


def candidates_from(pairs: Iterable[tuple[str, BRDItem]]) -> list[Candidate]:
    """Turn (section, item) pairs into comparable candidates.

    Observations are excluded. They exist to preserve evidence rather than to state a requirement,
    and letting one into a cluster would allow an implementation note to absorb the capability it
    was recorded against.
    """
    candidates: list[Candidate] = []
    for section, item in pairs:
        if section == "observations":
            continue
        candidates.append(
            Candidate(
                item=item,
                section=section,
                signature=signature_of(item),
                index=len(candidates),
            )
        )
    return candidates


def candidates_from_fragments(payloads: list[dict[str, Any]]) -> list[Candidate]:
    """Every candidate the fan-out produced, from every worker, as one flat population.

    This is what makes the clustering repository-wide. A worker sees one interaction boundary and
    cannot know that another worker described the same capability from the other side of it; the
    only place that comparison can happen is here, over all of their output at once.
    """
    from agentic.capabilities.assemble import FRAGMENT_SECTIONS, _as_fragment

    pairs: list[tuple[str, BRDItem]] = []
    for payload in payloads:
        fragment = _as_fragment(payload)
        if fragment is None:
            continue
        for section in FRAGMENT_SECTIONS:
            for item in getattr(fragment, section):
                pairs.append((section, item))
    return candidates_from(pairs)


def build_clusters(candidates: list[Candidate]) -> list[Cluster]:
    """Group every candidate in the repository by what it means.

    Union-find over the pairs that actually get compared. A candidate joins a cluster when it
    duplicates, merges with, or stands in a hierarchy to any member of it -- transitively, so a
    capability described three different ways lands in one group rather than three overlapping
    pairs.
    """
    parent = list(range(len(candidates)))

    def find(node: int) -> int:
        while parent[node] != node:
            parent[node] = parent[parent[node]]
            node = parent[node]
        return node

    def union(left: int, right: int) -> None:
        a, b = find(left), find(right)
        if a != b:
            parent[max(a, b)] = min(a, b)

    strongest: dict[tuple[int, int], str] = {}
    compared: set[tuple[int, int]] = set()
    for rows in _index_by_term(candidates).values():
        for position, left in enumerate(rows):
            for right in rows[position + 1 :]:
                pair = (left, right)
                if pair in compared:
                    continue
                compared.add(pair)
                relation = relate(
                    candidates[left].item,
                    candidates[right].item,
                    candidates[left].signature,
                    candidates[right].signature,
                )
                if relation == "independent":
                    continue
                strongest[pair] = relation
                if relation in {"duplicate", "mergeable", "parent_child", "conflicting"}:
                    # Conflicting candidates are grouped so that resolution sees the competing
                    # readings side by side. Grouping is not merging: the cluster's relation says
                    # `conflicting`, and consolidation refuses to merge inside one.
                    union(left, right)

    grouped: dict[int, list[Candidate]] = defaultdict(list)
    for position, candidate in enumerate(candidates):
        grouped[find(position)].append(candidate)

    # A conflict outranks every other reading of a group: if any pair in it contradicts, the group
    # is unresolved until someone decides which reading the evidence supports.
    order = (
        "conflicting", "duplicate", "mergeable", "parent_child", "overlapping", "independent",
    )
    clusters: list[Cluster] = []
    for number, (root, members) in enumerate(sorted(grouped.items()), start=1):
        members.sort(key=lambda member: (-len(member.signature.all_terms), member.item.title))
        ids = {member.index for member in members}
        relations = [
            relation
            for (left, right), relation in strongest.items()
            if left in ids and right in ids
        ]
        cluster = Cluster(cluster_id=f"CAP-{number:03d}", members=members)
        cluster.relation = min(relations, key=order.index) if relations else "independent"
        clusters.append(cluster)
    return clusters


@capability("cluster_capabilities")
def cluster_capabilities(ctx: CapabilityContext) -> CapabilityResult:
    """Group every requirement candidate in the repository by the capability it describes.

    Runs immediately after the fan-out and before anything reads the findings as a whole, because
    both of the stages that follow need the groups rather than the individuals: elevation binds
    outcomes to clusters, and consolidation merges within them.
    """
    payloads = list(ctx.state.get("fragments") or [])

    if not getattr(ctx.settings, "semantic_clustering", True):
        return CapabilityResult(
            artifacts={"capability_clusters": {"clusters": [], "by_title": {}, "disabled": True}},
            metrics={"capability_clusters": 0, "clustering_disabled": True},
            messages=["Repository-wide semantic clustering is switched off for this run."],
        )

    candidates = candidates_from_fragments(payloads)
    clusters = build_clusters(candidates)

    # Keyed by title because that is the only identifier a candidate has at this point: ids are
    # deliberately not assigned until deduplication and hierarchy construction are finished.
    by_title: dict[str, str] = {}
    for cluster in clusters:
        for member in cluster.members:
            by_title.setdefault(title_key(member.item.title), cluster.cluster_id)

    by_relation = Counter(cluster.relation for cluster in clusters)
    grouped = [cluster for cluster in clusters if len(cluster.members) > 1]
    largest = max((len(c.members) for c in clusters), default=0)

    artifact = {
        "clusters": [cluster.as_dict() for cluster in clusters],
        "by_title": by_title,
        "counts": dict(by_relation),
        "clustered_candidates": len(candidates),
        "multi_member_clusters": len(grouped),
    }

    messages = [
        f"Grouped {len(candidates)} requirement candidate(s) into {len(clusters)} capability "
        "cluster(s) by what they describe, across the whole repository rather than within one "
        "worker's view of one interaction boundary."
    ]
    if grouped:
        messages.append(
            f"{len(grouped)} cluster(s) hold more than one candidate ("
            + ", ".join(f"{count} {name}" for name, count in by_relation.most_common())
            + f"); the largest holds {largest}. These describe one capability between them and "
            "were previously published separately because they cite different code."
        )

    return CapabilityResult(
        artifacts={"capability_clusters": artifact},
        metrics={
            "capability_clusters": len(clusters),
            "clustered_candidates": len(candidates),
            "multi_member_clusters": len(grouped),
            "largest_cluster": largest,
            **{f"cluster_relation_{name}": count for name, count in by_relation.items()},
        },
        messages=messages,
    )

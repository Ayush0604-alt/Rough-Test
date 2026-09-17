"""Knowledge-graph construction and traversal.

`GraphBuilder` and `stable_id` are used by every extractor; the boundary/cluster functions below
are what keep model cost linear in the number of interaction boundaries rather than in the size of
the codebase.
"""

from __future__ import annotations

import hashlib
import json

from collections import Counter, defaultdict, deque
from typing import Any, Iterable

from agentic.capabilities.registry import CapabilityContext, CapabilityResult, capability
from agentic.schemas.core import (
    Entity,
    KnowledgeGraph,
    Provenance,
    Relationship,
    SourceLocation,
)

# Kinds that mark a point where the system is consumed from outside. The synthesis skill admits a
# claim only if it reaches one of these.
BOUNDARY_KINDS = {"ENDPOINT", "CLI_ENTRY", "SCRIPT_ENTRY", "UI_ACTION", "SCHEDULED_JOB"}

# Kinds that state policy rather than perform behaviour. These are seeds of last resort: a
# declaration nothing calls is still evidence, and until now it was silently discarded, because
# every packet is grown outwards from a boundary and a schema file is on no call path. In one
# measured run 444 entities were extracted from a schema and not one requirement cited it.
#
# The kinds here are structural, not stack-specific -- a table, a field, a configuration key, a
# stated policy are the same concepts in any language -- and an adapter extends the set by
# declaring what it produces, exactly as an entry-point adapter extends BOUNDARY_KINDS.
#
# BUSINESS_RULE is deliberately absent. Most instances are incidental conditionals rather than
# declared policy, so seeding on them would bury the real declarations. A rule in a declaration
# file still travels with its file, as a member rather than a seed.
DECLARATION_KINDS = {"DOMAIN_POLICY", "DATA_ENTITY", "CONFIG_KEY"}

# Edges worth following when reconstructing a workflow. Import and containment edges connect
# almost everything to everything and would make every cluster the whole graph.
WORKFLOW_EDGE_KINDS = {
    "CALLS", "ENFORCES", "PERFORMS", "HANDLES", "READS_CONFIG", "EXPOSES", "TRIGGERS",
    # A handler handed to a framework is never *called* by any code here, so without this edge a
    # dispatch-table architecture — a graph of registered nodes, a route table, a command
    # registry — is invisible to workflow traversal, and every one of its handlers lands in no
    # evidence packet at all.
    "REGISTERS",
}

# Nodes that add volume without adding meaning to an evidence packet.
NOISE_KINDS = {"IMPORTED_SYMBOL", "EXTERNAL_SYMBOL"}


def stable_id(prefix: str, *parts: object) -> str:
    """Content-addressed identity, so re-extracting the same construct converges on one id."""
    raw = "\x1f".join(str(part) for part in parts)
    digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:20]
    return f"{prefix}:{digest}"


class GraphBuilder:
    """Accumulates entities and relationships, de-duplicating by content-addressed id."""

    def __init__(self, repository_name: str) -> None:
        self.repository_name = repository_name
        self._entities: dict[str, Entity] = {}
        self._relationships: dict[str, Relationship] = {}
        self.metadata: dict[str, Any] = {}

    @property
    def entities(self) -> list[Entity]:
        return list(self._entities.values())

    @property
    def relationships(self) -> list[Relationship]:
        return list(self._relationships.values())

    def add_entity(self, entity: Entity) -> Entity:
        """Keep the higher-confidence version when the same construct is produced twice."""
        existing = self._entities.get(entity.id)
        if existing is None or entity.confidence > existing.confidence:
            self._entities[entity.id] = entity
            return entity
        return existing

    def relate(
        self,
        source: str,
        target: str,
        kind: str,
        *,
        provenance: Provenance = Provenance.EXTRACTED,
        confidence: float = 1.0,
        evidence: list[SourceLocation] | None = None,
        attributes: dict[str, Any] | None = None,
    ) -> Relationship:
        evidence = evidence or []
        line = evidence[0].line_start if evidence else None
        relationship = Relationship(
            id=stable_id("rel", source, target, kind, line),
            source=source,
            target=target,
            kind=kind,
            provenance=provenance,
            confidence=confidence,
            evidence=evidence,
            attributes=attributes or {},
        )
        # First edge wins: a duplicate carries the same endpoints and evidence line by construction.
        self._relationships.setdefault(relationship.id, relationship)
        return self._relationships[relationship.id]

    def build(self) -> KnowledgeGraph:
        return KnowledgeGraph(
            repository_name=self.repository_name,
            entities=sorted(self._entities.values(), key=lambda item: item.id),
            relationships=sorted(self._relationships.values(), key=lambda item: item.id),
            metadata=self.metadata,
        )


class GraphIndex:
    """Adjacency and lookup views over a knowledge graph."""

    def __init__(self, graph: KnowledgeGraph) -> None:
        self.graph = graph
        self.entities = graph.by_id()
        self.outgoing: dict[str, list[Relationship]] = defaultdict(list)
        self.incoming: dict[str, list[Relationship]] = defaultdict(list)
        for relationship in graph.relationships:
            self.outgoing[relationship.source].append(relationship)
            self.incoming[relationship.target].append(relationship)

    def boundary_ids(self) -> set[str]:
        """Every entity that is, or directly serves, an interaction boundary.

        A handler counts as a boundary because a requirement citing the handler is citing the point
        where the system is consumed, not internal processing.
        """
        found = {
            entity.id for entity in self.graph.entities if entity.kind in BOUNDARY_KINDS
        }
        for relationship in self.graph.relationships:
            if relationship.kind == "EXPOSES" and relationship.target in found:
                found.add(relationship.source)
        return found

    def exposers(self, entity_id: str) -> list[str]:
        """The entities that expose this one — an endpoint's handler, a widget's owning scope.

        A boundary node is a declaration: a route, a widget, a script entry. The behaviour a
        requirement must describe lives in whatever exposes it, so any walk that starts at a
        boundary has to step across `EXPOSES` backwards first or it sees only the declaration.
        """
        return [
            relationship.source
            for relationship in self.incoming.get(entity_id, [])
            if relationship.kind == "EXPOSES" and relationship.source in self.entities
        ]

    def workflow_from(
        self, boundary_id: str, *, max_depth: int = 6, max_nodes: int = 250
    ) -> list[str]:
        """The evidence neighbourhood of one interaction boundary.

        A boundary that declares what it starts — a control wired to the block it runs — already
        names its own workflow, and that workflow is what distinguishes it from the other controls
        beside it. A boundary that declares nothing, such as a route, is only a declaration, so the
        walk has to step back across `EXPOSES` to whatever serves it or the packet is a single node
        and the requirement is written from nothing.

        Falling back to the container unconditionally would erase the distinction: every control on
        a single-script UI is exposed by the same module, so all of them would return the whole
        module and collapse into one undifferentiated cluster.

        The defaults were four hops and 120 nodes, which suited a product whose controls call their
        work directly. It is far too shallow for a dispatch architecture: reaching a registered
        handler costs three hops before any work is described — entry point, wrapper, the builder
        that registers it — so the handler itself lands on the horizon and everything it calls
        lies past it. That is not a rare shape; it is how routers, task queues, and agent graphs
        are all built. Raising the horizon is only affordable now that an oversized neighbourhood
        is split across packets instead of being truncated at the budget.
        """
        entity = self.entities.get(boundary_id)
        own_workflow = bool(entity and entity.attributes.get("declares_workflow")) or any(
            relationship.kind in WORKFLOW_EDGE_KINDS
            for relationship in self.outgoing.get(boundary_id, [])
        )
        seeds = [] if own_workflow else self.exposers(boundary_id)
        return self.reachable(boundary_id, *seeds, max_depth=max_depth, max_nodes=max_nodes)

    def reachable(
        self, start: str, *more: str, max_depth: int = 4, max_nodes: int = 120
    ) -> list[str]:
        """Breadth-first walk along workflow edges, bounded in both depth and size.

        The bounds are what make an evidence packet fit a prompt: a deeply connected handler in a
        large repository would otherwise pull in most of the graph.
        """
        seeds = [start, *more]
        seen = set(seeds)
        order = list(seeds)
        queue: deque[tuple[str, int]] = deque((seed, 0) for seed in seeds)
        while queue and len(order) < max_nodes:
            current, depth = queue.popleft()
            if depth >= max_depth:
                continue
            for relationship in self.outgoing.get(current, []):
                if relationship.kind not in WORKFLOW_EDGE_KINDS:
                    continue
                target = relationship.target
                entity = self.entities.get(target)
                if target in seen or entity is None or entity.kind in NOISE_KINDS:
                    continue
                seen.add(target)
                order.append(target)
                queue.append((target, depth + 1))
        return order

    def reaches_boundary(self, ids: Iterable[str]) -> bool:
        boundaries = self.boundary_ids()
        return any(item in boundaries for item in ids)

    def boundary_downstream(self, *, max_depth: int = 6, max_nodes: int = 400) -> set[str]:
        """Every node some interaction boundary can reach — the code a user can actually cause to run.

        "Externally reachable" is a claim about direction: a request or a click arrives at a
        boundary and flows *inward*. Testing it by walking outward from the claim instead only
        happens to work when the claim already names the boundary or its handler; a claim about
        the work a control sets off is rejected, because the boundary is upstream of it and no
        outward walk will ever arrive there.
        """
        seen: set[str] = set()
        for boundary_id in self.boundary_ids():
            if boundary_id in seen:
                continue
            seen.update(self.workflow_from(boundary_id, max_depth=max_depth, max_nodes=max_nodes))
        return seen

    def declared_policy(self, member_ids: Iterable[str]) -> list[str]:
        """Configuration declared by the modules these members live in.

        A module-level constant is in scope for every function in its module, but it hangs off the
        module by a containment-style edge that no workflow walk follows. Left out, the packet
        loses the one place a codebase states policy in its own words — a declared operating
        instruction, a retrieval depth, a storage location — and the document can only report the
        incidental conditionals it found instead.
        """
        module_of: dict[str, str] = {}
        for relationship in self.graph.relationships:
            if relationship.kind == "CONTAINS":
                module_of[relationship.target] = relationship.source

        config_of: dict[str, list[str]] = defaultdict(list)
        for relationship in self.graph.relationships:
            if relationship.kind == "DECLARES_CONFIG":
                config_of[relationship.source].append(relationship.target)

        found: list[str] = []
        members = set(member_ids)
        for member in members:
            current = member
            for _ in range(4):  # module, class, function — never deeper in practice
                entity = self.entities.get(current)
                if entity is not None and entity.kind == "MODULE":
                    break
                parent = module_of.get(current)
                if parent is None:
                    break
                current = parent
            for config_id in config_of.get(current, []):
                if config_id not in members and config_id not in found:
                    found.append(config_id)
        return found

    def degree(self, entity_id: str) -> int:
        return len(self.outgoing.get(entity_id, [])) + len(self.incoming.get(entity_id, []))


def compact_entity(entity: Entity) -> dict[str, Any]:
    """A small, evidence-bearing view of a node for an LLM packet.

    Only the attributes that carry meaning for each kind are kept, and values are truncated —
    the packet is evidence, not a copy of the source.
    """
    keep = {
        "ENDPOINT": ("method", "path", "handler"),
        "FUNCTION": ("parameters", "async", "docstring"),
        "METHOD": ("parameters", "async", "docstring"),
        "CLASS": ("bases", "fields", "dto_model", "docstring"),
        "BUSINESS_RULE": (
            "predicate", "when_true", "when_false", "status_code", "detail",
            # Schema-sourced rules carry their declaration instead of a Python predicate.
            "rule_source", "constraint", "table", "expression", "allowed_values",
            "columns", "unique", "where", "timing", "description",
        ),
        "ERROR_HANDLER": ("exception", "behavior"),
        "DATA_OPERATION": ("operation", "call"),
        "CONFIG_KEY": ("source", "value", "secret_like"),
        "CLI_ENTRY": ("kind",),
        "SCRIPT_ENTRY": ("kind",),
        # A control's label is the product's own words for what it offers, and its accepted input
        # types and triggered effects are the contract behind that offer.
        "UI_ACTION": (
            "widget", "label", "help", "type", "placeholder", "accept_multiple_files",
            "max_chars", "triggered_outcomes",
        ),
        "FILE": ("extension", "content_outline"),
        # A module docstring is where an author most often states, in prose, what a part of the
        # product is *for* and which cases it deliberately refuses. It was being dropped entirely:
        # the kind carried no keys at all, so the single richest statement of intent in a Python
        # codebase never reached the model.
        "MODULE": ("docstring",),
        # Schema evidence. A declared constraint states policy more authoritatively than any
        # application code, because the database enforces it whatever the code does.
        "DATA_ENTITY": ("description", "declares"),
        "DATA_FIELD": ("table", "type", "not_null", "default", "description", "references"),
        "DATA_INDEX": ("table", "columns", "unique", "where", "description"),
    }
    # Prose that states intent earns a longer budget than an incidental attribute value. At 200
    # characters a module docstring is cut off mid-sentence, which is worse than useless: it reads
    # as a complete thought that says something other than what the author wrote.
    prose = {"docstring", "description"}
    attributes: dict[str, Any] = {}
    for key in keep.get(entity.kind, ()):
        if key not in entity.attributes:
            continue
        value = entity.attributes[key]
        if entity.kind == "CONFIG_KEY" and entity.attributes.get("secret_like"):
            value = "<redacted>"
        elif key in prose and isinstance(value, str):
            attributes[key] = _bounded(value, limit=1200)
            continue
        elif entity.kind == "BUSINESS_RULE" and key in {"expression", "allowed_values"}:
            attributes[key] = _bounded(value, limit=400, items=40)
            continue
        elif entity.kind == "CONFIG_KEY" and key == "value" and isinstance(value, str):
            # Long literals used to be redacted wholesale, which threw away the strongest policy
            # evidence a codebase holds: a declared operating instruction states a rule outright,
            # in the product's own words. Truncation keeps the rule and still bounds the packet.
            attributes[key] = _bounded(value, limit=600)
            continue
        attributes[key] = _bounded(value)
    record = {
        "k": entity.kind,
        "q": entity.qualified_name,
        "at": entity.location.compact() if entity.location else None,
        "p": entity.provenance.value,
        "c": round(entity.confidence, 2),
        "a": attributes,
    }
    # Carried as its own field rather than inside `at`, so a citation copied back from the packet
    # never mistakes the enclosing definition for a symbol present at the cited line.
    if entity.location and entity.location.scope:
        record["in"] = entity.location.scope
    return record


def _bounded(value: Any, limit: int = 200, items: int = 12) -> Any:
    if isinstance(value, str):
        return value if len(value) <= limit else value[:limit] + "…"
    if isinstance(value, dict):
        return {str(k): _bounded(v, limit, items) for k, v in list(value.items())[:items]}
    if isinstance(value, (list, tuple)):
        return [_bounded(item, limit, items) for item in list(value)[:items]]
    return value


def _packet_shell(records: list[dict[str, Any]], used: int) -> dict[str, Any]:
    return {
        "legend": {
            "n": "node: k=kind, q=qualified name, at=file:Lstart-Lend:symbol, "
                 "in=enclosing definition (context only — never cite it as symbol), "
                 "p=provenance, c=confidence, a=attributes",
            "out": "[edge kind, target qualified name, provenance, confidence, evidence location]",
        },
        "records": records,
        "record_count": len(records),
        "chars": used,
        "omitted": 0,
        "overflow": [],
    }


def build_packets(
    index: GraphIndex, entity_ids: list[str], *, char_budget: int, max_parts: int = 6
) -> list[dict[str, Any]]:
    """Serialise a cluster into as many packets as it takes to carry all of it.

    A single boundary can reach an entire application — one chat entry point dispatching through a
    graph of registered handlers is the ordinary case, not a pathological one. Packing that into
    one budgeted packet dropped whatever sat past the limit, and the tail of a breadth-first walk
    is the deepest, most specific work: exactly the requirements nobody else would write. Slicing
    instead costs one more bounded call per slice and loses nothing.

    `max_parts` is a spend ceiling, not a correctness limit; whatever it excludes is reported.
    """
    packets: list[dict[str, Any]] = []
    remaining = list(entity_ids)
    while remaining and len(packets) < max_parts:
        packet = build_packet(index, remaining, char_budget=char_budget)
        overflow = packet.pop("overflow", [])
        if not packet["records"]:
            break
        # What overflowed this packet is about to go into the next one, so it is not omitted.
        # Only whatever is still unplaced when the loop ends really failed to reach a packet.
        packet["omitted"] = 0
        packets.append(packet)
        if len(overflow) >= len(remaining):
            # No progress: one record alone exceeds the budget. Drop it rather than loop forever.
            overflow = overflow[1:]
        remaining = overflow
    if packets:
        packets[-1]["omitted"] = len(remaining)
    return packets


def build_packet(
    index: GraphIndex, entity_ids: list[str], *, char_budget: int
) -> dict[str, Any]:
    """Serialise a cluster into a compact record list that fits `char_budget`.

    Records are added until the budget is spent. What did not fit comes back under `overflow` so
    the caller can put it in the next packet rather than losing it.
    """
    records: list[dict[str, Any]] = []
    used = 0
    for entity_id in entity_ids:
        entity = index.entities.get(entity_id)
        if entity is None or entity.kind in NOISE_KINDS:
            continue
        edges = [
            [
                relationship.kind,
                index.entities[relationship.target].qualified_name,
                relationship.provenance.value,
                round(relationship.confidence, 2),
                relationship.evidence[0].compact() if relationship.evidence else None,
            ]
            for relationship in index.outgoing.get(entity_id, [])
            if relationship.kind in WORKFLOW_EDGE_KINDS
            and relationship.target in index.entities
            and index.entities[relationship.target].kind not in NOISE_KINDS
        ][:12]
        record = {"n": compact_entity(entity), "out": edges}
        encoded = len(json.dumps(record, separators=(",", ":"), ensure_ascii=False))
        if records and used + encoded > char_budget:
            # What did not fit is reported, not discarded quietly. The caller splits the cluster
            # so these reach a packet of their own; if anything ever stops it doing so, the count
            # is on the record instead of the evidence simply vanishing.
            remaining = entity_ids[entity_ids.index(entity_id) :]
            return {
                **_packet_shell(records, used),
                "omitted": len(remaining),
                "overflow": remaining,
            }
        records.append(record)
        used += encoded
    return _packet_shell(records, used)


# Attribute names an extractor may use to declare a finer entry-point class than its kind.
# Read in order; the first present wins.
#
# The planner never interprets the *value* — it only groups by it — so this stays free of any
# stack. An adapter that records `effect`, or `method`, or a channel name, gets its entry points
# stratified by that distinction without this module learning what the values mean. Adding a
# paradigm is recording an attribute, not editing this list for a framework.
# Ordered finest distinction first. `method` precedes `effect` because a route that records both
# is more usefully split five ways than two: collapsing every state-changing verb into one class
# would sample POST, PUT, PATCH and DELETE as though they were interchangeable.
CLASS_ATTRIBUTES = ("method", "effect", "trigger", "channel", "event")


def entry_point_class(entity: Entity) -> str:
    """The class a boundary belongs to for the purpose of sampling.

    `kind` alone is too coarse: a repository whose entry points are all one kind — every HTTP
    service — would be a single class and get no protection at all. The declared attribute splits
    it into the distinctions the extractor already knew about.
    """
    attributes = entity.attributes or {}
    for name in CLASS_ATTRIBUTES:
        value = attributes.get(name)
        if isinstance(value, str) and value.strip():
            return f"{entity.kind}:{value.strip().upper()}"
    return entity.kind


def _stratified(groups: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    """Take `limit` groups by sampling every class in turn, rather than truncating the order.

    This is the fix for a categorical omission. The previous `groups[:limit]` was correct only if
    the ordering were meaningful, and it was not: every boundary of one kind routinely carries the
    same degree, so the primary sort key is constant and the whole ordering collapses to the
    alphabetical tiebreak on `qualified_name`. Where that name begins with a class discriminator —
    an HTTP verb, a queue name, a command — the list arrives grouped by class, and a truncation
    removes whole classes rather than thinning each one. In the run that exposed this, 147
    state-changing endpoints were never planned because they sorted after the reads.

    Round-robin makes the cap cost every class proportionally. Order within a class is preserved,
    so the existing degree ranking still decides which members of a class survive.
    """
    if limit >= len(groups):
        return list(groups)

    by_class: dict[str, list[dict[str, Any]]] = {}
    for group in groups:
        by_class.setdefault(group["class"], []).append(group)

    # Classes are visited in first-appearance order so the result stays deterministic; the
    # incoming order is already the degree ranking.
    order = list(dict.fromkeys(group["class"] for group in groups))

    # Allocate the budget proportionally, with a floor of one per class. Equal shares would be
    # the wrong correction: a class holding a hundred boundaries and a class holding one are not
    # equally informative, and giving them the same slot under-samples the bulk of the system as
    # badly as truncation over-sampled it. The floor is what guarantees no class reaches zero;
    # the proportional remainder is what keeps the sample shaped like the population.
    quota: dict[str, int] = {name: 0 for name in order}
    remaining = limit

    for name in order:
        if remaining == 0:
            break
        quota[name] = 1
        remaining -= 1

    if remaining:
        total = sum(len(by_class[name]) for name in order)
        headroom = {name: len(by_class[name]) - quota[name] for name in order}
        shares = {
            name: remaining * len(by_class[name]) / total for name in order if headroom[name] > 0
        }
        for name, share in shares.items():
            take = min(int(share), headroom[name])
            quota[name] += take
            remaining -= take
        # Largest remainder first, so the leftover from rounding goes where it is most deserved.
        for name in sorted(shares, key=lambda n: shares[n] - int(shares[n]), reverse=True):
            if remaining == 0:
                break
            if quota[name] < len(by_class[name]):
                quota[name] += 1
                remaining -= 1

    # Any budget still unspent goes to whichever classes can still absorb it, so a cap is never
    # left unused just because one class ran out of members.
    while remaining:
        spent = False
        for name in order:
            if remaining and quota[name] < len(by_class[name]):
                quota[name] += 1
                remaining -= 1
                spent = True
        if not spent:
            break

    picked = {id(group) for name in order for group in by_class[name][: quota[name]]}
    # Restore the original ranking among the survivors, so downstream ordering is unchanged.
    return [group for group in groups if id(group) in picked]


def declaration_groups(
    index: GraphIndex, reached: set[str], limit_per_file: int
) -> list[dict[str, Any]]:
    """Cluster seeds for declarations no boundary can reach.

    A packet is grown outwards from a boundary, so evidence is only ever seen if something calls
    it. That is the right question for behaviour and the wrong one for a declaration: a schema
    states the values a column may take whether or not a handler happens to touch it, and a README
    sentence stating a limit is not called by anything at all. Such evidence was extracted and then
    dropped on the floor.

    Grouping is by source file, which is the one grouping every stack shares -- a file is where a
    codebase already decided which declarations belong together. The file's own entity leads the
    group where there is one, so the cluster is labelled with a real location rather than a
    synthetic node.
    """
    unreached_by_file: dict[str, list[Entity]] = defaultdict(list)
    for entity in index.entities.values():
        if entity.id in reached or entity.kind in NOISE_KINDS or entity.kind == "FILE":
            continue
        if entity.location is None:
            continue
        unreached_by_file[entity.location.file].append(entity)

    file_entity: dict[str, Entity] = {
        entity.location.file: entity
        for entity in index.entities.values()
        if entity.kind == "FILE" and entity.location is not None
    }

    # Ranked by how much each file actually declares, richest first, with the path only as a
    # tiebreak. Ordering by path would repeat the very defect `_stratified` exists to correct: a
    # cap applied to an alphabetically-ordered list does not thin the population, it truncates it,
    # and whichever directory sorts first takes the whole budget. Boundaries are ranked by degree
    # for the same reason; this is the declaration equivalent.
    ranked_files = sorted(
        unreached_by_file.items(),
        key=lambda item: (
            -sum(1 for entity in item[1] if entity.kind in DECLARATION_KINDS),
            item[0],
        ),
    )

    groups: list[dict[str, Any]] = []
    for path, entities in ranked_files:
        declared = [entity for entity in entities if entity.kind in DECLARATION_KINDS]
        if not declared:
            # Unreached ordinary code is a coverage gap, already reported as one. Only a file that
            # actually declares something earns a cluster, or every unvisited helper module in the
            # repository would become one.
            continue

        # The declarations lead, and whatever else sits unreached in the same file follows -- a
        # CHECK constraint belongs with the table it guards even though it is not itself a seed.
        ordered = declared + [entity for entity in entities if entity.kind not in DECLARATION_KINDS]
        members = [entity.id for entity in ordered[:limit_per_file]]
        lead = file_entity.get(path) or declared[0]
        label = str((declared[0].attributes or {}).get("artifact_kind") or declared[0].kind)
        groups.append(
            {
                "boundaries": [lead],
                "members": members,
                # Its own class, so the stratifier's floor of one guarantees a declaration is
                # always read even when a cap is fully consumed by interaction boundaries.
                "class": f"DECLARATION:{label.upper()}",
                "declaration": True,
            }
        )
    return groups


@capability("build_boundary_clusters")
def build_boundary_clusters(ctx: CapabilityContext) -> CapabilityResult:
    """Partition the graph into one evidence packet per interaction boundary.

    This is the step that makes cost linear. Each packet is a bounded neighbourhood around one
    boundary, so a repository with 200 endpoints costs 200 small calls rather than one impossible
    one — and no call ever contains raw source.
    """
    graph = ctx.load("graph", KnowledgeGraph)
    index = GraphIndex(graph)
    settings = ctx.settings

    boundaries = index.boundary_ids()
    # Rank by connectivity so that, when the cluster cap bites, the richest workflows survive.
    ranked = sorted(
        (index.entities[bid] for bid in boundaries if bid in index.entities),
        key=lambda entity: (-index.degree(entity.id), entity.qualified_name),
    )

    # Only cluster around the boundary itself, not around every handler that serves one, or a
    # single endpoint would produce two near-identical packets.
    primary = [entity for entity in ranked if entity.kind in BOUNDARY_KINDS] or ranked

    # Group boundaries by the work behind them, ignoring the boundary nodes themselves. Several
    # boundaries routinely share one workflow — two routes on the same handler, an upload control
    # and the button that consumes it. Grouping first, and putting every one of those boundaries
    # into the same packet, costs one model call instead of several and lets the model tell them
    # apart in a single pass. Sending near-identical packets separately instead produces
    # near-identical claims that survive as duplicates, because each one is independently true.
    groups: list[dict[str, Any]] = []
    by_signature: dict[frozenset[str], dict[str, Any]] = {}
    merged = 0
    for entity in primary:
        member_ids = index.workflow_from(entity.id)
        member_ids = member_ids + index.declared_policy(member_ids)
        signature = frozenset(
            member
            for member in member_ids
            if (found := index.entities.get(member)) is not None
            and found.kind not in BOUNDARY_KINDS
        )
        group = by_signature.get(signature)
        if group is None:
            group = {
                "boundaries": [entity],
                "members": list(member_ids),
                "class": entry_point_class(entity),
            }
            by_signature[signature] = group
            groups.append(group)
            continue
        group["boundaries"].append(entity)
        merged += 1
        for member in member_ids:
            if member not in group["members"]:
                group["members"].append(member)

    # Sampling, not truncation. See `_stratified` for why the incoming order cannot be trusted.
    if getattr(settings, "stratified_cluster_selection", True):
        planned = _stratified(groups, settings.max_clusters)
    else:
        planned = groups[: settings.max_clusters]

    # Declarations are budgeted separately rather than made to compete for the same slots.
    #
    # Pooling the two was tried and is wrong. `max_clusters` has always been a cap on interaction
    # boundaries -- one packet per boundary is what makes cost linear -- and when more classes
    # exist than budget, the floor cannot reach them all and whichever class is served last is
    # starved outright. Adding declarations as one more class simply chose a new victim: served
    # last they were starved exactly as before the fix, and served first they starved the largest
    # class of endpoints instead. There is no ordering that resolves it, because the premise is
    # wrong -- a schema and an HTTP verb are not competing samples of one population.
    #
    # A small separate budget keeps every guarantee the boundary sampler already makes, byte for
    # byte, and costs at most a quarter more clusters, only where declarations exist at all.
    declarations = 0
    if getattr(settings, "declaration_clusters", True):
        reached = {member for group in groups for member in group["members"]}
        extra = declaration_groups(
            index, reached, limit_per_file=max(1, settings.max_clusters)
        )
        declarations = len(extra)
        groups = groups + extra
        planned = planned + _stratified(extra, max(1, settings.max_clusters // 4))

    present_by_class: Counter[str] = Counter(group["class"] for group in groups)
    planned_by_class: Counter[str] = Counter(group["class"] for group in planned)

    clusters: list[dict[str, Any]] = []
    covered: set[str] = set()
    truncated = 0
    split_groups = 0
    for group in planned:
        member_ids = group["members"]
        boundaries_here = group["boundaries"]
        packets = build_packets(
            index, member_ids, char_budget=settings.evidence_packet_chars
        )
        if not packets:
            continue
        covered.update(member_ids)
        lead = boundaries_here[0]
        if len(packets) > 1:
            split_groups += 1
        for part, packet in enumerate(packets, start=1):
            truncated += packet.get("omitted", 0)
            clusters.append(
                {
                    # Each slice is dispatched separately, so its id must be distinct or the
                    # fan-out channel collapses them onto one another.
                    "cluster_id": f"{lead.id}#{part}" if len(packets) > 1 else lead.id,
                    "boundary": lead.qualified_name,
                    "boundary_kind": lead.kind,
                    "boundary_location": lead.location.compact() if lead.location else None,
                    "also_exposed_by": [item.qualified_name for item in boundaries_here[1:]],
                    "member_count": len(member_ids),
                    "part": part,
                    "of_parts": len(packets),
                    "packet": packet,
                }
            )

    messages: list[str] = []
    omitted = max(0, len(groups) - len(clusters))
    if omitted:
        messages.append(
            f"{omitted} lower-connectivity interaction boundary/boundaries were omitted by the "
            f"configured cluster cap of {settings.max_clusters}."
        )
    starved = sorted(
        name for name, total in present_by_class.items() if planned_by_class.get(name, 0) == 0
    )
    thinned = sorted(
        name
        for name, total in present_by_class.items()
        if 0 < planned_by_class.get(name, 0) < total
    )
    if starved:
        messages.append(
            f"{len(starved)} entry-point class(es) were left with no cluster at all and nothing "
            f"will be written about them: {', '.join(starved[:8])}."
        )
    if thinned:
        messages.append(
            "The cluster cap thinned "
            + ", ".join(
                f"{name} ({planned_by_class[name]}/{present_by_class[name]})"
                for name in thinned[:8]
            )
            + "."
        )
    if split_groups:
        messages.append(
            f"{split_groups} boundary workflow(s) were larger than one evidence packet and were "
            f"split across several, producing {len(clusters)} cluster(s) in total. A boundary "
            "that reaches most of the application is ordinary; truncating it would have dropped "
            "the deepest work in the codebase."
        )
    if truncated:
        messages.append(
            f"{truncated} graph node(s) still did not reach any evidence packet after splitting, "
            "so nothing will be written about them. They are listed in the gap analysis."
        )
    if declarations:
        planned_declarations = sum(
            1 for group in planned if group.get("declaration")
        )
        messages.append(
            f"{planned_declarations} of {declarations} file(s) declaring policy that no call path "
            "reaches were read as clusters of their own. A schema or a stated limit is evidence "
            "whether or not a handler happens to touch it."
        )
    if not clusters:
        messages.append(
            "No interaction boundary was found. Synthesis will report that the codebase appears "
            "to be a library rather than an application."
        )

    return CapabilityResult(
        artifacts={"clusters": clusters},
        metrics={
            "clusters": len(clusters),
            "declaration_files": declarations,
            "declaration_clusters": sum(1 for group in planned if group.get("declaration")),
            "groups_split": split_groups,
            "entry_point_classes": len(present_by_class),
            "entry_points_present_by_class": dict(present_by_class),
            "entry_points_planned_by_class": dict(planned_by_class),
            "entry_point_classes_starved": len(starved),
            "nodes_never_packeted": truncated,
            "boundaries_found": len(primary),
            "boundaries_merged": merged,
            "boundaries_omitted": omitted,
            "graph_nodes_covered": len(covered),
            "packet_chars": sum(item["packet"]["chars"] for item in clusters),
        },
        messages=messages,
    )

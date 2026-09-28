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
#
# DATA_FIELD, DATA_INDEX and EXTERNAL_SERVICE were added when `extract_declarations` became the
# producer for this whole family. Until then no step emitted any of these kinds at all, so the
# ranking key below was uniformly zero and the sort collapsed to the file path — the exact defect
# `_stratified` exists to correct, reintroduced by an empty set rather than by a bad comparator.
# INSTRUCTION_ARTIFACT and INSTRUCTION_RULE are here because for an LLM-powered product the
# instruction file *is* the behaviour, and a skill nothing loads still states policy — exactly the
# case declaration clusters exist for.
DECLARATION_KINDS = {
    "DOMAIN_POLICY", "DATA_ENTITY", "DATA_FIELD", "DATA_INDEX", "CONFIG_KEY", "EXTERNAL_SERVICE",
    "INSTRUCTION_ARTIFACT", "INSTRUCTION_RULE",
}

# Edges worth following when reconstructing a workflow. Import and containment edges connect
# almost everything to everything and would make every cluster the whole graph.
WORKFLOW_EDGE_KINDS = {
    "CALLS", "ENFORCES", "PERFORMS", "HANDLES", "READS_CONFIG", "EXPOSES", "TRIGGERS",
    # A handler handed to a framework is never *called* by any code here, so without this edge a
    # dispatch-table architecture — a graph of registered nodes, a route table, a command
    # registry — is invisible to workflow traversal, and every one of its handlers lands in no
    # evidence packet at all.
    "REGISTERS",
    # THE GROUNDING DECISION FOR INSTRUCTION FILES. Including LOADS means a requirement may be
    # grounded in a skill or prompt file *when traced code loads it*: the walk goes outward from a
    # boundary, through the calls, to the loader, and then to what the loader loads.
    #
    # For an LLM-powered product this is the difference between describing the product and
    # describing its plumbing — the refusals and limits live in the Markdown, and the code around
    # it only opens a file and posts it to an API. Measured before this existed: on two stored runs
    # the instruction files were in the graph with no content and no edges at all, including this
    # project's own 54 skill files when it analysed itself.
    #
    # Excluding it is the defensible alternative and leaves such products undocumentable. Remove
    # this one entry to reverse the decision; the artifacts stay in the graph and stay visible in
    # declaration clusters, they simply stop being able to ground a claim on their own.
    "LOADS",
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
        # `docstring` here is a module-level docstring/header comment, carried onto the FILE entity
        # by `adapt_graphify_graph` from a `rationale_for` edge — see graphify.md §15.
        "FILE": ("extension", "content_outline", "docstring"),
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
        # A declared dependency is the only evidence a call graph carries that an external system
        # exists at all: the repository never defines it, so nothing in the graph points at it.
        "EXTERNAL_SERVICE": ("ecosystem", "declared_version", "source"),
        # Structure, not flattened prose — the same treatment a schema gets. `sections` are the
        # units of instruction the file's own author chose, short enough to carry whole.
        "INSTRUCTION_ARTIFACT": (
            "artifact_role", "declared_name", "description", "sections", "rule_count",
        ),
        # `modality` is load-bearing: "never" and "must" are declared prohibitions and
        # obligations, which is what separates a rule from the prose around it.
        "INSTRUCTION_RULE": ("rule_source", "modality", "statement", "section"),
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
            "depends_on": "external systems this cluster's members reference but never define",
        },
        "records": records,
        "record_count": len(records),
        "chars": used,
        "omitted": 0,
        "overflow": [],
    }


# How many external systems one packet names. A cluster depending on more than this is depending
# on a language's standard library, not on a set of integrations worth enumerating.
MAX_DEPENDS_ON = 24


def external_dependencies(index: "GraphIndex", member_ids: Iterable[str]) -> list[dict[str, Any]]:
    """The external systems a cluster's members reference, as facts rather than as nodes.

    `EXTERNAL_SYMBOL` is in `NOISE_KINDS` and the edges that reach it (`IMPORTS`, `REFERENCES`)
    are outside `WORKFLOW_EDGE_KINDS`. Both exclusions are right and stay: import edges *"connect
    almost everything to everything and would make every cluster the whole graph"*, and an
    undefined name is not a step in a workflow.

    But they made `integration_requirements` impossible to write rather than merely hard. The
    manifest asks for *"one per external system this cluster depends on, and what happens when it
    is unavailable"*, and every node naming such a system was filtered out before the packet was
    built — so the honest reply was always an empty list. Carrying them as a separate, deduplicated
    field keeps them out of traversal (nothing is reachable *through* a dependency) while putting
    the fact itself in front of the model.

    A name declared in a dependency manifest is reported with its `EXTERNAL_SERVICE` evidence, so a
    claim about it can cite the manifest line. A name only referenced in code carries no location
    and says so.
    """
    members = set(member_ids)
    declared: dict[str, Entity] = {
        entity.name.strip(". ()").casefold(): entity
        for entity in index.entities.values()
        if entity.kind == "EXTERNAL_SERVICE"
    }

    found: dict[str, dict[str, Any]] = {}
    for member in members:
        for relationship in index.outgoing.get(member, []):
            if relationship.kind not in {"IMPORTS", "REFERENCES", "INHERITS"}:
                continue
            target = index.entities.get(relationship.target)
            if target is None or target.kind not in {"EXTERNAL_SYMBOL", "EXTERNAL_SERVICE"}:
                continue
            name = target.name.strip(". ()")
            key = name.casefold()
            if not name or key in found:
                continue
            record: dict[str, Any] = {"name": name, "via": relationship.kind}
            # A manifest entry is the stronger evidence: it proves the dependency is declared by
            # the product rather than inferred from a bare name the parser could not resolve.
            if match := declared.get(key):
                record["declared_version"] = match.attributes.get("declared_version") or ""
                record["ecosystem"] = match.attributes.get("ecosystem") or ""
                if match.location is not None:
                    record["at"] = match.location.compact()
            found[key] = record
    return sorted(found.values(), key=lambda item: item["name"])[:MAX_DEPENDS_ON]


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
    # Computed once over the whole cluster, not per slice: a dependency belongs to the cluster, and
    # a model reading part 3 of 4 still needs to know what the whole thing talks to.
    depends_on = external_dependencies(index, entity_ids)
    while remaining and len(packets) < max_parts:
        packet = build_packet(index, remaining, char_budget=char_budget)
        overflow = packet.pop("overflow", [])
        if not packet["records"]:
            break
        if depends_on:
            packet["depends_on"] = depends_on
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

    picked = {
        id(group)
        for name in order
        for group in _spread_across_modules(by_class[name], quota[name])
    }
    # Restore the original ranking among the survivors, so downstream ordering is unchanged.
    return [group for group in groups if id(group) in picked]


def _spread_across_modules(members: list[dict[str, Any]], quota: int) -> list[dict[str, Any]]:
    """Take `quota` members of one class, one module at a time, best-ranked first in each.

    The class floor stops a cap deleting a whole *kind* of entry point; it did nothing to stop one
    deleting a whole *part of the product*. Within a class the survivors were the top of a degree
    ranking, and one densely connected module can hold the whole top of it. Round-robin across
    modules keeps every part of the product in the sample. Members with no module share one
    bucket, so a repository without a module partition is sampled exactly as before.
    """
    if quota >= len(members):
        return list(members)
    buckets: dict[str, list[dict[str, Any]]] = {}
    for member in members:
        buckets.setdefault(str(member.get("module") or ""), []).append(member)
    if len(buckets) <= 1:
        return members[:quota]
    chosen: list[dict[str, Any]] = []
    queues = [list(bucket) for bucket in buckets.values()]
    while len(chosen) < quota and any(queues):
        for queue in queues:
            if queue and len(chosen) < quota:
                chosen.append(queue.pop(0))
    return chosen


def declaration_groups(
    index: GraphIndex,
    reached: set[str],
    limit_per_file: int,
    exclude_files: set[str] | frozenset[str] = frozenset(),
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
        if entity.location is None or entity.location.file in exclude_files:
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


# Nodes whose text is already carried somewhere better, so a residual packet made of them would
# spend a call to say nothing. RATIONALE is redundant since docstring attachment copies its text
# onto the entity it describes; SYMBOL is an unresolved name with no body to describe.
_RESIDUAL_SKIP_KINDS = NOISE_KINDS | {"RATIONALE", "SYMBOL", "FILE"}

# Declarations have their own path — `declaration_groups` builds a cluster per declaring file, and
# `partition_modules` hands module-local policy to the module that owns it. Letting residual pick
# them up as well produced two clusters over one `schema.sql`: a declaration cluster led by the
# FILE and a residual cluster led by a DATA_ENTITY in the same file, describing the same tables
# twice at the cost of an extra call each time.
_RESIDUAL_EXCLUDED_KINDS = _RESIDUAL_SKIP_KINDS | DECLARATION_KINDS

# A directory holding less than this, none of it reached, is a stray helper rather than a
# subsystem. One packet per stray would cost a call each to describe a utility.
MIN_RESIDUAL_MEMBERS = 4

# Members one declaring file may contribute. See the call site for why it is not `max_clusters`.
DECLARATION_MEMBERS_PER_FILE = 400


# Directories and filenames where a repository keeps instructions for the *coding agents its
# developers use* -- not for any model the product itself calls. They are conventions of the
# tooling (Claude Code, Cursor, Copilot, Windsurf, Codex, Gemini, Kiro, Roo, Cline, Junie, Amazon
# Q), not of any product domain, so the list names tools and never a subject area.
DEV_TOOLING_DIRECTORIES = frozenset(
    {
        ".agents", ".agent", ".claude", ".cursor", ".github", ".windsurf", ".codex", ".continue",
        ".gemini", ".kiro", ".roo", ".clinerules", ".cline", ".junie", ".amazonq", ".aider",
        ".vscode", ".idea", ".devcontainer",
    }
)
DEV_TOOLING_FILENAMES = frozenset(
    {
        "claude.md", "agents.md", "agent.md", "gemini.md", ".cursorrules", ".windsurfrules",
        ".clinerules", "copilot-instructions.md",
    }
)


def is_dev_tooling_path(path: str) -> bool:
    """Whether a file sits where developer-agent tooling keeps its instructions."""
    parts = [part.casefold() for part in (path or "").replace("\\", "/").split("/") if part]
    if not parts:
        return False
    return parts[-1] in DEV_TOOLING_FILENAMES or any(
        part in DEV_TOOLING_DIRECTORIES for part in parts[:-1]
    )


def dev_tooling_instruction_files(index: "GraphIndex") -> set[str]:
    """Instruction files written for the developers' coding agents rather than for the product.

    On one run 68 of 153 published requirements -- 44% of the document -- cited nothing but
    `.agents/skills/*/DESIGN.md` and `SKILL.md`: style guides telling a coding assistant how to lay
    out a page. They became "the system shall enforce a single colour palette", three times over,
    because the guide repeats its own rules in several sections. None of it is the product.

    The rule is the one `LOADS` already encodes: an instruction file is product behaviour when
    product code loads it. A file in a developer-tooling location that nothing loads is excluded
    from synthesis and reported by name; one that the product does load stays, wherever it lives.
    """
    loaded: set[str] = set()
    candidates: set[str] = set()
    for entity in index.entities.values():
        if entity.kind not in {"INSTRUCTION_ARTIFACT", "INSTRUCTION_RULE"}:
            continue
        if entity.location is None or not is_dev_tooling_path(entity.location.file):
            continue
        candidates.add(entity.location.file)
        if any(edge.kind == "LOADS" for edge in index.incoming.get(entity.id, [])):
            loaded.add(entity.location.file)
    return candidates - loaded


def _residual_groups(index: GraphIndex, reached: set[str]) -> list[dict[str, Any]]:
    """Cluster seeds for meaningful code no boundary walk and no declaration group reached.

    "Externally reachable" is the right grounding rule for a *requirement* and the wrong rule for
    *coverage*. A scheduled job, a migration, an admin script or a whole subsystem no route
    touches is still implemented behaviour somebody has to maintain, and under boundary-only
    clustering none of it reached a model at all — on the reference run, 87.5% of the graph.

    Two facts make this affordable rather than expensive. Packets were running at a fifth of their
    character budget, so the room to carry this was already paid for; and grouping by directory
    rather than by file keeps the call count in the tens rather than the hundreds.

    A residual cluster has no boundary above it, which is why it carries its own class and its own
    manifest section: the synthesis instruction "work outward from the boundary" is meaningless
    here, and asking for a functional requirement from unreachable code would invite exactly the
    unreachable claim Gate 2 exists to reject.
    """
    unreached_by_directory: dict[str, list[Entity]] = defaultdict(list)
    for entity in index.entities.values():
        if entity.id in reached or entity.kind in _RESIDUAL_EXCLUDED_KINDS:
            continue
        if entity.location is None or not entity.location.file:
            continue
        path = entity.location.file
        directory = path.rsplit("/", 1)[0] if "/" in path else "."
        unreached_by_directory[directory].append(entity)

    ranked = sorted(
        unreached_by_directory.items(),
        # Total degree first: the most-connected residue is the most likely to be a real
        # subsystem rather than a pile of dead helpers. Path only as a stable tiebreak, never as
        # the primary key — that is the truncation defect `_stratified` exists to correct.
        key=lambda item: (-sum(index.degree(entity.id) for entity in item[1]), item[0]),
    )

    groups: list[dict[str, Any]] = []
    for directory, entities in ranked:
        if len(entities) < MIN_RESIDUAL_MEMBERS:
            continue
        ordered = sorted(entities, key=lambda entity: (-index.degree(entity.id), entity.qualified_name))
        lead = ordered[0]
        groups.append(
            {
                "boundaries": [lead],
                "members": [entity.id for entity in ordered],
                # Its own class so the stratifier's per-class floor guarantees residual code is
                # described even when interaction boundaries consume the whole cap.
                "class": "RESIDUAL",
                "residual": True,
                "directory": directory,
            }
        )
    return groups


# How much of two boundaries' workflows must coincide before they are one packet. High on purpose:
# twins share a handler or nearly all of its work, not merely a helper or a repository layer.
TWIN_JACCARD = 0.8

# How full a packet may be made by packing. Headroom below the budget, because a packed packet is
# serialised from the union of its members and the estimate is taken per group.
PACK_FILL = 0.85


def _workflow_twin(
    groups: list[dict[str, Any]], signature: frozenset[str]
) -> dict[str, Any] | None:
    """The existing group whose workflow is near-identical to this one, if any."""
    if not signature:
        return None  # nothing to compare: an empty workflow is not a twin of anything
    size = len(signature)
    for group in groups:
        other = group.get("signature") or frozenset()
        if not other:
            continue
        # Jaccard cannot reach the threshold when the sizes differ by more than it allows.
        if min(size, len(other)) < TWIN_JACCARD * max(size, len(other)):
            continue
        if len(signature & other) / len(signature | other) >= TWIN_JACCARD:
            return group
    return None


def _group_chars(index: GraphIndex, member_ids: list[str]) -> int:
    """Characters a group's evidence would take in a packet, measured the way packets are built."""
    return int(build_packet(index, member_ids, char_budget=10**9)["chars"])


def _pack_small_groups(
    index: GraphIndex,
    groups: list[dict[str, Any]],
    *,
    char_budget: int,
    per_packet: int,
) -> tuple[list[dict[str, Any]], int]:
    """Combine small boundary groups of one module into shared packets, in ranked order.

    Only groups with a module are packed, only with others of the same module, never more than
    `per_packet` of them, and never past `PACK_FILL` of the character budget. A group that is
    already large travels alone, exactly as before. Returns the new group list and how many groups
    were folded into another's packet.
    """
    if per_packet <= 1:
        return groups, 0
    limit = int(char_budget * PACK_FILL)
    result: list[dict[str, Any]] = []
    open_packs: dict[str, dict[str, Any]] = {}
    folded = 0
    for group in groups:
        module = str(group.get("module") or "")
        size = _group_chars(index, group["members"])
        if not module or size >= limit:
            result.append(group)
            continue
        pack = open_packs.get(module)
        if (
            pack is not None
            and len(pack["packed_boundaries"]) < per_packet
            and pack["chars"] + size <= limit
        ):
            pack["packed_boundaries"].append(group["boundaries"][0].qualified_name)
            pack["boundaries"].extend(group["boundaries"])
            for member in group["members"]:
                if member not in pack["members"]:
                    pack["members"].append(member)
            pack["chars"] += size
            folded += 1
            continue
        pack = {
            **group,
            "boundaries": list(group["boundaries"]),
            "members": list(group["members"]),
            "packed_boundaries": [group["boundaries"][0].qualified_name],
            "chars": size,
        }
        open_packs[module] = pack
        result.append(pack)
    for group in result:
        # A "pack" of one is an ordinary group; do not tell the worker it is sharing a packet.
        if len(group.get("packed_boundaries") or []) < 2:
            group.pop("packed_boundaries", None)
        group.pop("chars", None)
    return result, folded


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

    # Optional so this step still runs standalone in tests and with `module_analysis` off, in which
    # case every cluster carries an empty module code and the worker falls back to inventing an
    # area exactly as before.
    module_of: dict[str, str] = {}
    if "boundary_modules" in (ctx.state.get("artifacts") or {}):
        loaded = ctx.load("boundary_modules")
        if isinstance(loaded, dict):
            module_of = {str(key): str(value) for key, value in loaded.items()}

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
        # Near-identical, not identical. Grouping used to require the two workflows to be *equal*
        # sets, and every boundary's own handler is in its own set, so on a 303-boundary run it
        # merged nothing at all. Twins now share most of their work: two routes on one handler, a
        # control and the upload it consumes.
        group = _workflow_twin(groups, signature)
        if group is None:
            groups.append(
                {
                    "boundaries": [entity],
                    "members": list(member_ids),
                    "class": entry_point_class(entity),
                    "module": module_of.get(entity.id, ""),
                    "signature": signature,
                }
            )
            continue
        group["boundaries"].append(entity)
        merged += 1
        for member in member_ids:
            if member not in group["members"]:
                group["members"].append(member)

    # Several small boundaries of one module in one packet. Packets ran at a median of 16% of
    # their budget, so most of every call's evidence allowance was paid for and unused while a
    # fixed cap left three boundaries in four with no packet at all. Packing fills the budget
    # instead of adding calls; it never crosses a module and never exceeds the budget.
    boundary_groups_before_packing = len(groups)
    groups, packed = _pack_small_groups(
        index,
        groups,
        char_budget=settings.evidence_packet_chars,
        per_packet=int(getattr(settings, "boundaries_per_packet", 1) or 1),
    )
    boundary_packets = len(groups)

    # How many packets the boundaries may use. A fixed cap silently dropped 223 of 303 boundaries
    # on one run; the cap now grows to cover every boundary group, up to a spend ceiling, and the
    # configured `max_clusters` is the floor rather than the limit.
    boundary_cap = settings.max_clusters
    ceiling = int(getattr(settings, "max_clusters_ceiling", 0) or 0)
    if ceiling > boundary_cap:
        boundary_cap = min(ceiling, max(boundary_cap, len(groups)))

    # Sampling, not truncation. See `_stratified` for why the incoming order cannot be trusted.
    if getattr(settings, "stratified_cluster_selection", True):
        planned = _stratified(groups, boundary_cap)
    else:
        planned = groups[:boundary_cap]

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
    # Instructions for the developers' coding agents, which no product code loads. Excluded from
    # synthesis and named in the plan, so the omission is on the record rather than silent.
    developer_tooling = sorted(dev_tooling_instruction_files(index))
    if getattr(settings, "declaration_clusters", True):
        reached = {member for group in groups for member in group["members"]}
        extra = declaration_groups(
            index,
            reached,
            exclude_files=set(developer_tooling),
            # Generous on purpose. This used to be `max_clusters`, which silently truncated a real
            # schema: 34 tables and 363 columns against a limit of 80 dropped most of the columns,
            # and a column is where a `NOT NULL` or a default actually lives. An oversized group is
            # split across packets by `build_packets` rather than truncated, so the only cost of a
            # high limit is one more bounded call, and it is paid only where a file declares that
            # much.
            limit_per_file=max(settings.max_clusters, DECLARATION_MEMBERS_PER_FILE),
        )
        declarations = len(extra)
        groups = groups + extra
        planned = planned + _stratified(extra, max(1, settings.max_clusters // 4))

    # Everything meaningful that no boundary walk and no declaration group reached.
    #
    # Boundary clustering answers "what can an outside actor cause to run", which is the right
    # question and an incomplete one: on the reference run it reached 634 of 5,084 nodes (12.5%)
    # while the packets it built ran at 20% of their character budget. Background jobs, migrations,
    # admin scripts and whole subsystems no route touches produced nothing at all, and the budget
    # to describe them was already being paid for and left unspent.
    #
    # Grouped by directory rather than by file, because an unreached subsystem is a directory's
    # worth of code and one packet per file would cost a call per file to say very little. Ranked
    # by degree so the most-connected residue is described first if the cap bites — the same
    # reasoning `declaration_groups` applies to declarations and `primary` applies to boundaries.
    residual_groups: list[dict[str, Any]] = []
    if getattr(settings, "residual_clusters", True):
        reached = {member for group in groups for member in group["members"]}
        residual_groups = _residual_groups(index, reached)
        groups = groups + residual_groups
        planned = planned + _stratified(
            residual_groups, max(1, settings.max_clusters // 2)
        )

    present_by_class: Counter[str] = Counter(group["class"] for group in groups)
    planned_by_class: Counter[str] = Counter(group["class"] for group in planned)

    # What coverage is measured against. Not every entity: a RATIONALE node's text is already
    # carried as a docstring on the entity it describes, and an unresolved SYMBOL or an
    # EXTERNAL_SYMBOL has no body to describe, so counting them would make full coverage
    # unreachable by definition and the percentage meaningless.
    meaningful = {
        entity.id
        for entity in index.entities.values()
        if entity.kind not in _RESIDUAL_SKIP_KINDS or entity.kind == "FILE"
    }

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
                    "also_exposed_by": [
                        item.qualified_name
                        for item in boundaries_here[1:]
                        if item.qualified_name not in group.get("packed_boundaries", ())
                    ],
                    **(
                        {"packed_boundaries": list(group["packed_boundaries"])}
                        if group.get("packed_boundaries")
                        else {}
                    ),
                    "member_count": len(member_ids),
                    "part": part,
                    "of_parts": len(packets),
                    # Assigned from the module partition rather than invented by the worker.
                    #
                    # Every synthesis worker used to choose its own `area_code` while seeing only
                    # its own nine-node packet, and the manifest asked them to agree: "clusters
                    # covering the same part of the product must use the same code". They cannot —
                    # they run in parallel and none sees another's reply. On the reference run that
                    # produced four separate areas for batches (ARCH, BAP, BATC, BCH) and fourteen
                    # areas holding a single capability. A code handed down from the partition is
                    # the only version of that instruction a worker can actually obey.
                    "module_code": module_of.get(lead.id, ""),
                    # A residual cluster has no boundary above it. Saying so lets the worker skip
                    # the "work outward from the boundary" frame instead of inventing a boundary
                    # for code nothing reaches.
                    "residual": bool(group.get("residual")),
                    "directory": group.get("directory"),
                    "packet": packet,
                }
            )

    messages: list[str] = []
    planned_ids = {id(group) for group in planned}
    omitted = sum(1 for group in groups if id(group) not in planned_ids)
    boundaries_total = len(primary)
    boundaries_planned = len(
        {
            entity.id
            for group in planned
            if not group.get("declaration") and not group.get("residual")
            for entity in group["boundaries"]
        }
    )
    if omitted:
        messages.append(
            f"{omitted} lower-connectivity group(s) were omitted by the cluster cap of "
            f"{boundary_cap} (floor {settings.max_clusters}, ceiling {ceiling or 'off'})."
        )
    if boundaries_total:
        messages.append(
            f"{boundaries_planned} of {boundaries_total} interaction boundaries "
            f"({round(100.0 * boundaries_planned / boundaries_total, 1)}%) reached a synthesis "
            "packet."
        )
    if packed:
        messages.append(
            f"{packed} small boundary group(s) were packed alongside others of the same module, "
            f"turning {boundary_groups_before_packing} boundary groups into {boundary_packets} "
            "packets without dropping any of them."
        )
    if developer_tooling:
        messages.append(
            f"{len(developer_tooling)} instruction file(s) written for developers' coding agents "
            "and loaded by no product code were excluded from synthesis: "
            + ", ".join(developer_tooling[:6])
            + ("..." if len(developer_tooling) > 6 else "")
            + "."
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
    if residual_groups:
        planned_residual = sum(1 for group in planned if group.get("residual"))
        messages.append(
            f"{planned_residual} of {len(residual_groups)} directory/directories of implemented "
            "code that no interaction boundary reaches were read as clusters of their own. A "
            "scheduled job or an admin subsystem is implemented behaviour whether or not a route "
            "touches it; under boundary-only clustering none of it reached a model at all."
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

    plan = {
        "boundaries_total": boundaries_total,
        "boundaries_planned": boundaries_planned,
        "boundary_coverage_percent": round(
            100.0 * boundaries_planned / max(1, boundaries_total), 1
        ),
        "boundary_cap": boundary_cap,
        "groups_packed": packed,
        "boundaries_merged_as_twins": merged,
        "developer_tooling_files_excluded": developer_tooling,
        "clusters": len(clusters),
    }
    return CapabilityResult(
        artifacts={"clusters": clusters, "cluster_plan": plan},
        metrics={
            "clusters": len(clusters),
            "boundaries_planned": boundaries_planned,
            "boundary_coverage_percent": plan["boundary_coverage_percent"],
            "boundary_cap": boundary_cap,
            "groups_packed": packed,
            "developer_tooling_files_excluded": len(developer_tooling),
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
            # Coverage as a fraction, not just a count. The count was already emitted and nothing
            # read it, which is how a pipeline reached 12.5% coverage across twenty stored runs
            # without anyone noticing. A percentage is legible at a glance; a raw count is not.
            "graph_nodes_meaningful": len(meaningful),
            "graph_coverage_percent": round(100.0 * len(covered & meaningful) / max(1, len(meaningful)), 1),
            "residual_groups": len(residual_groups),
            "residual_clusters": sum(1 for item in clusters if item.get("residual")),
            "packet_chars": sum(item["packet"]["chars"] for item in clusters),
            # Utilisation, for the same reason. Packets running at a fifth of budget is the signal
            # that the traversal — not the budget — is what limits how much evidence reaches a
            # model, and it is invisible unless something divides the two numbers.
            "packet_budget_percent": round(
                100.0
                * (sum(item["packet"]["chars"] for item in clusters) / max(1, len(clusters)))
                / max(1, settings.evidence_packet_chars),
                1,
            ),
        },
        messages=messages,
    )

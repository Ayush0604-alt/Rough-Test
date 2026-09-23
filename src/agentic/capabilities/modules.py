"""Product modules: the altitude between one workflow and the whole product.

The pipeline had two analysis altitudes and needed three. `synthesize` sees one interaction
boundary — a median of nine graph nodes — and `elevate` sees a list of finding *titles* for the
whole repository. Between them sat `digest_areas`, which groups titles by area code and is
deterministic: nothing anywhere reasoned about **a part of the product as a whole**.

That gap is why a whole class of requirement was unwritable. "Every write path in this part goes
through one repository layer", "nothing here accepts a request without a tenant id", "the retry
bound is three across all six handlers" — each is true of a module and invisible from inside any
one of its boundaries. They are system requirements, business rules and quality properties, which
is three of the families no stored run has ever populated.

## Why the partition is rooted in boundaries

Three candidate partitions were measured on a real 381-file run before this was written:

- **Directories.** `app/api` (1,081 nodes), `app/services` (1,299), `app/repositories` (138),
  `app/db` (129). That is a *layer* split, not a feature split. Asking a model to describe
  "all the services" is asking for a horizontal slice through every feature at once — worse
  evidence than a single boundary, not better.
- **Community detection on the code graph.** Label propagation over CALLS/IMPORTS/REFERENCES
  produced one community of 1,244 nodes spanning api + services + repositories + extraction, and
  a second of 700 covering the whole frontend. A layered application is densely interconnected by
  construction, so modularity-based clustering collapses it into a hairball. This is also why
  Graphify's own Leiden pass stays disabled (`--no-cluster`).
- **Boundary reach overlap.** Agglomerating *boundaries* by the Jaccard similarity of the node
  sets their workflows reach produced groups like `/rounds` + `create_round` + `create_slot_invite`
  + `/slots/{id}/propose` (scheduling), `/review-queue/{id}` + `approve_review_item` +
  `rerun_skill_override` (review queue), `/question-banks/generate` + `interview_questions`
  (question bank). Those are features.

The reason the third works is not subtle: **an interaction boundary is the product's own unit of
offered function**, so boundaries that run the same code are offering parts of the same feature.
Feature structure is recoverable from behaviour even where it is absent from the directory tree.

## What this module does not do

It does not replace `build_boundary_clusters`. Boundary clusters remain the unit of detailed
synthesis, because a packet grown from one boundary is what makes an acceptance criterion
specific. This partition sits *above* them: it assigns each boundary cluster a module, which fixes
the area code from the top rather than letting eighty-four isolated workers each invent one, and
it gives the new module-analysis step its work items.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any, Iterable

from agentic.capabilities.graph_ops import (
    BOUNDARY_KINDS,
    GraphIndex,
    build_packets,
    external_dependencies,
)
from agentic.capabilities.instructions import instruction_rules_for
from agentic.capabilities.registry import CapabilityContext, CapabilityResult, capability
from agentic.schemas.core import Entity, KnowledgeGraph

# Two boundaries belong to one module when this much of the code they reach is shared. Measured
# rather than chosen: on the reference run 0.30 separates scheduling from review-queue from intake
# while still merging a route with the handler that serves it. Lower merges unrelated features
# through a shared utility; higher leaves every route its own module and reproduces the flat design
# this step exists to replace.
MERGE_SIMILARITY = 0.30

# A module smaller than this is not a part of the product, it is one behaviour. Such boundaries are
# still synthesised individually — they simply do not earn a module-level analysis call of their
# own, and are gathered under one residual module instead.
MIN_MODULE_BOUNDARIES = 2

# How far a module's own evidence packet may walk. Deeper than a boundary packet because a module
# legitimately spans several workflows, and bounded because the point of a module packet is the
# shape of the part, not every statement inside it.
MODULE_PACKET_NODES = 600


def _jaccard(left: set[str], right: set[str]) -> float:
    union = len(left | right)
    return len(left & right) / union if union else 0.0


def _agglomerate(
    reach: dict[str, set[str]], threshold: float, floor: int
) -> list[list[str]]:
    """Merge boundaries whose workflows overlap, most-similar pair first.

    Deliberately agglomerative rather than one pass of "merge everything above the threshold":
    a single pass is order-dependent, so two runs over the same graph could produce different
    modules, and this pipeline's whole contract is that the deterministic half is reproducible.
    Taking the best pair each round makes the result a function of the graph alone.

    `floor` stops the merge before everything collapses into one module on a codebase whose
    boundaries all share a bootstrap path.
    """
    groups: list[list[str]] = [[key] for key in sorted(reach)]
    sets: list[set[str]] = [set(reach[key]) for key in sorted(reach)]

    while len(groups) > floor:
        best_score = 0.0
        best_pair: tuple[int, int] | None = None
        for left in range(len(groups)):
            for right in range(left + 1, len(groups)):
                score = _jaccard(sets[left], sets[right])
                # Ties broken by position, which is sorted by boundary id, so the choice is stable.
                if score > best_score:
                    best_score = score
                    best_pair = (left, right)
        if best_pair is None or best_score < threshold:
            break
        left, right = best_pair
        groups[left] = groups[left] + groups[right]
        sets[left] = sets[left] | sets[right]
        del groups[right]
        del sets[right]

    return groups


# Words that name a mechanism rather than a feature, so they never become a module code.
_UNINFORMATIVE_WORDS = frozenset(
    {
        "none", "self", "post", "get", "put", "patch", "delete", "http", "https", "api",
        "router", "endpoint", "handler", "route", "index", "main", "app", "src", "lib",
        "true", "false", "null", "async", "await", "return", "list", "dict", "item", "items",
    }
)


def _boundary_words(entity: Entity) -> list[str]:
    """The feature-bearing words in one boundary's own label.

    `name` only, never `qualified_name`: a qualified name carries the file path, and the path
    carries the repository name, so every module on `resume-agent-PROD` scored "resume" highest and
    every code after the first collided into `RES2`…`RES14`. The label is the product's own wording
    for what it offers; the path is where somebody put the file.
    """
    raw = (entity.name or "").replace("/", " ").replace("_", " ").replace("-", " ")
    words: list[str] = []
    for token in raw.split():
        token = "".join(character for character in token if character.isalpha()).lower()
        # `{candidate_id}`-style path parameters reduce to their name; one- to three-letter
        # fragments name nothing.
        if len(token) >= 4 and token not in _UNINFORMATIVE_WORDS:
            words.append(token)
    return words


def _module_codes(index: GraphIndex, groups: list[list[str]]) -> list[str]:
    """A short, stable, distinctive code per module, assigned by distinctiveness not by frequency.

    Raw frequency picks whichever word is commonest, which on any real repository is a word that
    appears everywhere — so the most common word is exactly the *least* informative one. Scoring
    by lift instead (how much more often a word appears in this module than across all modules)
    picks the word that tells this module apart, which is what a code is for.

    The code is only an identifier. The *name* a reader sees is written by the model in
    `analyse_module`, in the product's own words, so a module whose code falls back to a positional
    `M07` still publishes under a real heading.
    """
    per_group: list[Counter[str]] = []
    overall: Counter[str] = Counter()
    for group in groups:
        counts: Counter[str] = Counter()
        for boundary_id in group:
            entity = index.entities.get(boundary_id)
            if entity is not None:
                counts.update(_boundary_words(entity))
        per_group.append(counts)
        overall.update(set(counts))

    codes: list[str] = []
    taken: set[str] = set()
    for position, counts in enumerate(per_group):
        ranked = sorted(
            counts.items(),
            # Lift first, then in-module frequency, then the word itself — so the result is a
            # function of the graph alone and two runs agree.
            key=lambda item: (-(item[1] / overall[item[0]]), -item[1], item[0]),
        )
        code = ""
        for word, _ in ranked:
            candidate = word[:4].upper()
            if candidate not in taken:
                code = candidate
                break
        if not code:
            code = f"M{position + 1:02d}"
            while code in taken:
                position += 1
                code = f"M{position + 1:02d}"
        taken.add(code)
        codes.append(code)
    return codes


@capability("partition_modules")
def partition_modules(ctx: CapabilityContext) -> CapabilityResult:
    """Group interaction boundaries into product modules, and give each one an evidence packet.

    Produces `modules`, a plain top-level list so `analyse_module` can fan out over it the way
    `synthesize` fans out over `clusters`.
    """
    settings = ctx.settings
    graph = ctx.load("graph", KnowledgeGraph)
    index = GraphIndex(graph)

    if not getattr(settings, "module_analysis", True):
        return CapabilityResult(
            artifacts={"modules": []},
            metrics={"modules": 0},
            messages=[
                "Module analysis is switched off; requirements will be written from boundary "
                "clusters and the top tier only, with no intermediate altitude."
            ],
        )

    # Only the boundaries themselves, never the handlers that serve them. `boundary_ids()`
    # deliberately includes exposers, because a requirement citing a handler is citing the point
    # where the system is consumed — right for reachability, wrong for partitioning. Partitioning
    # over both puts a route and its handler in as separate seeds, which halves the effective
    # similarity between two real features and promotes junk leads (`msg.get@306`, `APIRouter@17`)
    # to module heads. `build_boundary_clusters` guards the same way with its `primary` list.
    every_boundary = index.boundary_ids()
    boundary_ids = sorted(
        boundary_id
        for boundary_id in every_boundary
        if boundary_id in index.entities and index.entities[boundary_id].kind in BOUNDARY_KINDS
    ) or sorted(every_boundary)
    if not boundary_ids:
        return CapabilityResult(
            artifacts={"modules": []},
            metrics={"modules": 0},
            messages=[
                "No interaction boundary was found, so the product has no modules to analyse."
            ],
        )

    reach = {
        boundary_id: set(
            index.workflow_from(boundary_id, max_depth=6, max_nodes=MODULE_PACKET_NODES)
        )
        for boundary_id in boundary_ids
    }

    floor = max(1, getattr(settings, "min_modules", 6))
    groups = _agglomerate(reach, MERGE_SIMILARITY, floor)

    # Boundaries that merged with nothing are behaviours, not modules. Pooling them into one
    # residual module is better than either giving each its own analysis call (which would cost
    # one call per route and say nothing a boundary cluster did not already say) or dropping them
    # (which would leave their findings with no module and no area code).
    substantial = [group for group in groups if len(group) >= MIN_MODULE_BOUNDARIES]
    singletons = [member for group in groups if len(group) < MIN_MODULE_BOUNDARIES for member in group]

    limit = max(1, getattr(settings, "max_modules", 20))
    substantial.sort(key=lambda group: (-len(reach_union(reach, group)), group[0]))
    if len(substantial) > limit:
        # The modules that lose their own call keep their boundaries and their findings; they are
        # folded into the residual module, so nothing stops being described.
        singletons.extend(member for group in substantial[limit:] for member in group)
        substantial = substantial[:limit]

    planned: list[list[str]] = list(substantial)
    if singletons:
        planned.append(sorted(set(singletons)))

    residual_index = len(planned) - 1 if singletons else -1
    codes = _module_codes(index, planned)
    if residual_index >= 0:
        # The residual module is a collection of unrelated single behaviours, so a distinctive word
        # would misrepresent it as a feature. Named for what it is.
        codes[residual_index] = "MISC"

    # Declarations are partitioned across modules once, up front, so no two modules are shown the
    # same policy. Computed before the loop because the assignment is a global decision: which
    # module owns a declaration depends on every module's code, not just this one's.
    all_members = [reach_union(reach, group) for group in planned]
    declarations = assign_declarations(index, all_members)

    modules: list[dict[str, Any]] = []
    boundary_to_module: dict[str, str] = {}
    for position, group in enumerate(planned):
        members = sorted(all_members[position])
        residual = position == residual_index
        code = codes[position]

        packets = build_packets(
            index,
            members,
            char_budget=getattr(settings, "module_packet_chars", 30_000),
            max_parts=1,
        )
        packet = packets[0] if packets else None
        for boundary_id in group:
            boundary_to_module[boundary_id] = code

        modules.append(
            {
                "module_code": code,
                "residual": residual,
                "boundaries": [
                    {
                        "qualified_name": index.entities[boundary_id].qualified_name,
                        "kind": index.entities[boundary_id].kind,
                        "at": index.entities[boundary_id].location.compact()
                        if index.entities[boundary_id].location
                        else None,
                    }
                    for boundary_id in sorted(group)
                    if boundary_id in index.entities
                ][:40],
                "boundary_count": len(group),
                "member_count": len(members),
                "depends_on": external_dependencies(index, members),
                "declared_policy": declarations[position],
                # Rules from instruction files this module's own code loads. Scoped by the LOADS
                # edge rather than shown to every module, for the same reason declarations are
                # partitioned: a rule every module sees is a rule every module restates.
                "instruction_rules": instruction_rules_for(graph, members),
                "packet": packet,
            }
        )

    messages: list[str] = [
        f"{len(modules)} product module(s) derived from {len(boundary_ids)} interaction "
        f"boundaries by workflow overlap."
    ]
    if singletons:
        messages.append(
            f"{len(singletons)} boundary/boundaries overlapped with no other and were gathered "
            "into one residual module rather than each costing a module-analysis call."
        )
    if len(substantial) == limit:
        messages.append(
            f"The module cap of {limit} was reached; the modules past it kept their boundaries and "
            "their findings but share the residual module's analysis."
        )

    return CapabilityResult(
        artifacts={
            "modules": modules,
            # Read by `build_boundary_clusters` so a cluster is told its module instead of leaving
            # each synthesis worker to invent an area code in isolation.
            "boundary_modules": boundary_to_module,
        },
        metrics={
            "modules": len(modules),
            "module_boundaries": len(boundary_ids),
            "residual_boundaries": len(singletons),
            "module_packet_chars": sum(
                int(item["packet"]["chars"]) for item in modules if item.get("packet")
            ),
        },
        messages=messages,
    )


def reach_union(reach: dict[str, set[str]], group: Iterable[str]) -> set[str]:
    members: set[str] = set()
    for boundary_id in group:
        members |= reach.get(boundary_id, set())
    return members


# How many declarations one module's packet names.
#
# A module never sees another module's policy, so the cap only ever bites on a module that really
# does declare more than sixty things of its own.
MAX_MODULE_DECLARATIONS = 60



def _declaration_record(entity: Entity) -> dict[str, Any]:
    record: dict[str, Any] = {
        "kind": entity.kind,
        "name": entity.name,
        "at": entity.location.compact() if entity.location else None,
    }
    if entity.kind == "CONFIG_KEY":
        record["value"] = (
            "<redacted>" if entity.attributes.get("secret_like") else entity.attributes.get("value")
        )
    for key in ("allowed_values", "columns", "unique", "table", "description"):
        if key in entity.attributes:
            record[key] = entity.attributes[key]
    return record


def assign_declarations(
    index: GraphIndex, module_members: list[set[str]]
) -> list[list[dict[str, Any]]]:
    """Give every declaration to exactly one module, or to none.

    Two scoping rules were tried first and both fail on real layouts. Scoping by *file* gives every
    module an empty list, because a schema and a settings file hold no code and so appear in no
    module's member files. Scoping by directory *ancestry* gives every module the same list, because
    repository-root policy is genuinely in scope for all of them — on the reference repository that
    meant twenty-one modules each shown the same sixty declarations, which is an invitation to
    publish the same data invariant twenty-one times and leave consolidation to clean it up.

    So this partitions instead, which is the same move `collect_boundary_assignments` made for
    candidates and `_residual_groups` made for unreached code: **nothing is duplicated and nothing
    is selected out.** A declaration goes to whichever module holds the most code in the directory
    that declares it, because that is the module the declaration most plausibly governs. Ties are
    broken by module position, which is ordered by reach, so the result is a function of the graph
    alone and two runs agree.

    A declaration no module has code beside belongs to the repository rather than to any part of
    it, and it is already described in its own right by the cluster `declaration_groups` builds
    from the file that declares it. It is left out here rather than copied everywhere.
    """
    directories: list[Counter[str]] = []
    for members in module_members:
        counts: Counter[str] = Counter()
        for member in members:
            entity = index.entities.get(member)
            if entity is None or entity.location is None or not entity.location.file:
                continue
            path = entity.location.file
            counts[path.rsplit("/", 1)[0] if "/" in path else "."] += 1
        directories.append(counts)

    assigned: list[list[dict[str, Any]]] = [[] for _ in module_members]
    for entity in sorted(index.entities.values(), key=lambda item: item.qualified_name):
        if entity.kind not in {"CONFIG_KEY", "DOMAIN_POLICY", "DATA_ENTITY", "DATA_INDEX"}:
            continue
        if entity.location is None or not entity.location.file:
            continue
        path = entity.location.file
        declared_in = path.rsplit("/", 1)[0] if "/" in path else "."

        best = -1
        owner = None
        for position, counts in enumerate(directories):
            weight = counts.get(declared_in, 0)
            if weight > best:
                best = weight
                owner = position
        if owner is None or best <= 0:
            # No module keeps code beside this declaration, so it is the repository's policy and
            # its own declaration cluster describes it. Copying it into every module is what this
            # function exists to avoid.
            continue
        if len(assigned[owner]) < MAX_MODULE_DECLARATIONS:
            assigned[owner].append(_declaration_record(entity))
    return assigned


@capability("collect_module_analyses")
def collect_module_analyses(ctx: CapabilityContext) -> CapabilityResult:
    """Reduce every module worker's reply into one artifact the top tier can read.

    `analyse_module` fans out one worker per module, and each reply lands in the shared
    `module_fragments` state channel rather than a file, since many workers write at once. This is
    the only step that reduces that channel — the same division of labour
    `collect_boundary_assignments` has for boundary classification.

    A fragment that cannot be read is counted and dropped rather than guessed at, consistent with
    every other failure mode in this pipeline.
    """
    fragments = ctx.state.get("module_fragments") or []
    analyses: list[dict[str, Any]] = []
    unreadable = 0
    for fragment in fragments:
        if not isinstance(fragment, dict) or not fragment.get("module_code"):
            unreadable += 1
            continue
        analyses.append(fragment)

    analyses.sort(key=lambda item: str(item.get("module_code") or ""))
    names = {
        str(item["module_code"]): str(item.get("module_name") or "")
        for item in analyses
        if item.get("module_name")
    }

    messages: list[str] = []
    if analyses:
        counts = Counter()
        for item in analyses:
            for section in (
                "module_capabilities",
                "cross_cutting_rules",
                "quality_properties",
                "integration_points",
                "data_invariants",
            ):
                counts[section] += len(item.get(section) or [])
        messages.append(
            f"{len(analyses)} module analysis/analyses reduced: "
            + ", ".join(f"{count} {name.replace('_', ' ')}" for name, count in counts.items() if count)
            + "."
            if any(counts.values())
            else f"{len(analyses)} module analysis/analyses reduced, none carrying findings."
        )
    if unreadable:
        messages.append(
            f"{unreadable} module reply/replies could not be read and were dropped rather than "
            "guessed at."
        )

    return CapabilityResult(
        artifacts={"module_analyses": {"analyses": analyses, "module_names": names}},
        metrics={"module_analyses": len(analyses), "module_replies_unreadable": unreadable},
        messages=messages,
    )

"""Merge the per-cluster findings into one requirements set, without a model call.

This module replaces the old `reduce` step, and the replacement is the substance of the rebuild.

`reduce` was one LLM call handed every finding from every cluster and asked to return the finished
document as a single structured reply. That is fine for a small repository and arithmetically
impossible for a real one: with an 8,000-token output ceiling and roughly 120 tokens per written
requirement, the reply can hold about sixty items *including* every acceptance criterion, persona,
rationale, and citation. A platform whose clusters produced two hundred grounded findings
therefore had them compressed to a handful, and the loss looked like a reasoning failure when it
was a budget one. Worse, the compression was silent: nothing counted what went in against what
came out.

The findings do not need re-emitting. A fan-out worker has already written each one in full,
against evidence it could see, and every one is about to be re-checked by Gate 2 anyway. So
assembly is deterministic:

- **`digest_areas`** builds the small input the one remaining whole-repository call needs — area
  names and requirement *titles*, never bodies. It stays small as the repository grows.
- **`assemble_requirements`** carries every fragment finding through unchanged, attaches each to
  the business requirement its area was bound to, and reports exactly what it received against
  what it published.

The model still does the judgement only it can do — what the product is for, which outcomes it
exists to deliver, which area serves which. It no longer does the copying.
"""

from __future__ import annotations


from collections import Counter, defaultdict
from typing import Any

from agentic.capabilities.registry import CapabilityContext, CapabilityResult, capability
from agentic.schemas.brd import (
    AreaLabel,
    BRDFragment,
    BRDItem,
    ProductBRD,
    TopTier,
    title_key,
)

# Sections a fan-out worker fills. Business requirements are deliberately absent: one boundary
# cannot establish what a whole product is for.
#
# `system_requirements`, `technical_constraints` and `observations` were missing from this tuple
# while being present on `BRDFragment`, so every one a worker produced was dropped on the floor at
# assembly -- silently, because a section nobody iterates raises nothing. The synthesis skill
# instructs workers at length to file implementation detail under `observations` with the
# requirement it supports as its parent; not one of those ever reached the document, which is a
# large part of why the system tier looked thin and the traceability appendix looked empty.
FRAGMENT_SECTIONS = (
    "functional_requirements",
    "system_requirements",
    "business_rules",
    "technical_constraints",
    "success_criteria",
    "data_requirements",
    "integration_requirements",
    "non_functional_requirements",
    "reporting_requirements",
    "observations",
)

# Where a fragment's area code is unusable. Grouping everything under one bucket is better than
# inventing codes, because the reader can see it happened.
FALLBACK_AREA = "GEN"


def _clean_code(raw: str) -> str:
    """An area code reduced to the shape the document publishes: 2-4 uppercase letters."""
    letters = "".join(character for character in (raw or "").upper() if character.isalpha())
    return letters[:4] if len(letters) >= 2 else ""


# Words that carry no distinguishing meaning in an area heading, so two headings differing only in
# these are the same heading. Domain-neutral: nothing here names a technology or a subject.
_HEADING_NOISE = frozenset(
    """and or of the a an for to in on with management managing handling handler service services
    module modules component components system capability capabilities feature features
    functionality operations general misc other""".split()
)


def _heading_words(name: str) -> frozenset[str]:
    """The words in an area heading that carry meaning, as a set."""
    return frozenset(
        word.strip(".,;:\'\"()").casefold()
        for word in " ".join((name or "").split()).split()
    ) - _HEADING_NOISE - {""}


def _heading_key(name: str) -> str:
    """How two area headings are compared before one is folded into the other.

    Fan-out workers invent an area code each, independently, from what their own slice of the
    product does. Two of them looking at different parts of the same capability reliably invent two
    codes and give them headings that mean the same thing -- "Batch Management" under `BATC` and
    "Batch Management" under `BCH`. Published, that is one part of the product split across two
    headings for no reason a reader can see, and it is the clearest symptom of grouping that has
    tracked code location rather than what the code is for.
    """
    words = {
        word.strip(".,;:'\"()").casefold()
        for word in " ".join((name or "").split()).split()
    }
    return " ".join(sorted(word for word in words if word and word not in _HEADING_NOISE))


def _reconcile_areas(names: dict[str, str]) -> dict[str, str]:
    """Area code -> the code it is published under, folding headings that name the same part.

    Deliberately decided on the *heading*, which is the product's own business words for the group,
    rather than on the code, which is an arbitrary abbreviation each worker invented alone. The
    shortest code wins so the choice is stable across runs.

    Two headings are the same group in two cases.

    **They mean the same thing.** "Batch Management" and "Batch handling" differ in a word that
    carries nothing.

    **One is the other, narrowed.** A fan-out worker sees one slice of a product and names the part
    it can see, so a platform whose candidate records are touched from eleven places produces
    "Candidate Management" alongside "Candidate Notes Management", "Candidate Remarks Management",
    "Candidate Archiving", "Candidate Slot Management" and six more. Every one of those is
    candidate management, seen through one entry point; published separately they give a reader
    eleven headings to search instead of one section to read, and the distinction between them is
    already carried by the requirement titles inside. A heading whose meaningful words are a
    superset of another heading's is that heading, narrowed, and it is folded into it.

    The narrowing rule only fires when the *general* heading is itself an area someone named. No
    group is invented: if nothing called this part of the product "Candidate Management", nothing
    here decides that something should have.
    """
    words = {code: _heading_words(name) for code, name in names.items()}

    # 1. Same meaning.
    by_key: dict[str, list[str]] = defaultdict(list)
    for code, name in names.items():
        key = _heading_key(name)
        if key:
            by_key[key].append(code)
    remap: dict[str, str] = {}
    for codes in by_key.values():
        canonical = min(sorted(codes), key=len)
        for code in codes:
            remap[code] = canonical

    # 2. One heading narrowing another. Widest first, so a chain -- C narrows B narrows A --
    # resolves onto A rather than stopping half way.
    def root(code: str) -> str:
        seen = set()
        while remap.get(code, code) != code and code not in seen:
            seen.add(code)
            code = remap[code]
        return code

    for code in sorted(words, key=lambda name: (-len(words[name]), name)):
        mine = words[code]
        if not mine:
            continue
        candidates = [
            other
            for other in words
            if other != code and words[other] and words[other] < mine
        ]
        if not candidates:
            continue
        # The most general heading available, and among equals the stable one.
        general = min(candidates, key=lambda other: (len(words[other]), other))
        target = root(general)
        if target != code:
            remap[root(code)] = target
            remap[code] = target

    return {code: root(code) for code in words if root(code) != code}


def _fragments(ctx: CapabilityContext) -> list[dict[str, Any]]:
    return list(ctx.state.get("fragments") or [])


def _as_fragment(payload: dict[str, Any]) -> BRDFragment | None:
    """Parse one worker's reply, tolerating the extra routing keys the channel adds."""
    fields = set(BRDFragment.model_fields)
    try:
        return BRDFragment.model_validate({k: v for k, v in payload.items() if k in fields})
    except Exception:
        return None


def _module_names(ctx: CapabilityContext) -> dict[str, str]:
    """Module code -> the business name the module worker gave it, when module analysis ran."""
    if "module_analyses" not in (ctx.state.get("artifacts") or {}):
        return {}
    try:
        loaded = ctx.load("module_analyses")
    except (KeyError, OSError, ValueError):
        return {}
    names = (loaded or {}).get("module_names") if isinstance(loaded, dict) else None
    return {
        _clean_code(str(code)): " ".join(str(name).split())
        for code, name in (names or {}).items()
        if _clean_code(str(code)) and str(name).strip()
    }


def _module_codes(payloads: list[dict[str, Any]]) -> set[str]:
    """Every module code the partition handed to a synthesis worker."""
    return {
        code
        for payload in payloads
        if (code := _clean_code(str(payload.get("module_code") or "")))
    }


def item_area(
    item: BRDItem,
    payload: dict[str, Any],
    default_code: str,
    remap: dict[str, str],
    modules: set[str],
) -> str:
    """The area one finding is filed under.

    A worker handed a module code files everything under it, unless a finding names *another*
    module. Workers used to invent a code per requirement, and since none of them saw the module
    code they were meant to copy, one run published 54 areas for 21 modules.
    """
    declared = _clean_code(item.area)
    declared = remap.get(declared, declared)
    if payload.get("module_code") and declared not in modules:
        return default_code
    return declared or default_code


def _area_index(
    payloads: list[dict[str, Any]],
    module_names: dict[str, str] | None = None,
) -> tuple[dict[int, str], dict[str, str], dict[str, str]]:
    """Assign every fragment an area code, and name each area once.

    The partition's module code wins where a fragment carries one, and the module worker's
    business name names it. Only fragments with no module -- declaration and residual clusters --
    fall back to the code their worker chose, and two workers that chose the same code for the
    same part of the product are merged.
    """
    module_names = module_names or {}
    codes: dict[int, str] = {}
    names: dict[str, str] = {}
    for position, payload in enumerate(payloads):
        module = _clean_code(str(payload.get("module_code") or ""))
        code = module or _clean_code(str(payload.get("area_code") or "")) or FALLBACK_AREA
        codes[position] = code
        if module and module_names.get(module):
            # Named by the worker that saw the whole module, not by one that saw one boundary.
            names.setdefault(module, module_names[module])
        name = " ".join(str(payload.get("area_name") or "").split())
        if name and code not in names:
            names[code] = name
        # Names for the areas an individual requirement claimed. One entry point can fan out into
        # several unrelated parts of a product, so the cluster's own code names only the default.
        for label in payload.get("areas") or []:
            if not isinstance(label, dict):
                continue
            label_code = _clean_code(str(label.get("code") or ""))
            label_name = " ".join(str(label.get("name") or "").split())
            if label_code and label_name and label_code not in names:
                names[label_code] = label_name
    used = set(codes.values()) | {
        _clean_code(str(item.get("area") or ""))
        for payload in payloads
        if not payload.get("module_code")
        for section in FRAGMENT_SECTIONS
        for item in (payload.get(section) or [])
        if isinstance(item, dict)
    }
    for code in used:
        if code:
            names.setdefault(code, "Uncategorised capabilities")

    # Fold headings that mean the same thing onto one code before anything is published under
    # either. Done here rather than at rendering because the code is what an id is built from and
    # what a reader cites: two ids for one part of the product cannot be repaired downstream.
    # Module codes come from the partition and are never folded into one another: two modules are
    # two parts of the product by construction, whatever their workers called them.
    remap = {
        code: target
        for code, target in _reconcile_areas(names).items()
        if code not in module_names
    }
    if remap:
        codes = {position: remap.get(code, code) for position, code in codes.items()}
        folded: dict[str, str] = {}
        for code, name in names.items():
            folded.setdefault(remap.get(code, code), name)
        names = folded
    return codes, names, remap


@capability("digest_areas")
def digest_areas(ctx: CapabilityContext) -> CapabilityResult:
    """Summarise the fan-out into the smallest input from which the top tier can be written.

    Titles only. The full statements are already written and already grounded; sending them again
    is what made the old merge step unaffordable, and reading them is not what the remaining call
    is for.
    """
    payloads = _fragments(ctx)
    codes, names, remap = _area_index(payloads, _module_names(ctx))
    modules = _module_codes(payloads)

    grouped: dict[str, dict[str, Any]] = {}
    for position, payload in enumerate(payloads):
        fragment = _as_fragment(payload)
        if fragment is None:
            continue
        default_code = codes[position]

        def area_for(code: str) -> dict[str, Any]:
            return grouped.setdefault(
                code,
                {
                    "area_code": code,
                    "area_name": names.get(code, "Uncategorised capabilities"),
                    "boundaries": [],
                    "what_this_area_does": [],
                    "capability_titles": [],
                    "rule_titles": [],
                    "counts": Counter(),
                },
            )

        boundary = payload.get("boundary")
        # Grouped by each requirement's own area, not by the cluster's. One boundary reaching
        # several parts of a product must appear as several areas in the digest, or the step that
        # writes the top tier sees one lump and binds it to a single outcome.
        for section in FRAGMENT_SECTIONS:
            for item in getattr(fragment, section):
                code = item_area(item, payload, default_code, remap, modules)
                area = area_for(code)
                area["counts"][section] += 1
                if boundary and boundary not in area["boundaries"]:
                    area["boundaries"].append(boundary)
                if section == "functional_requirements":
                    area["capability_titles"].append(item.title)
                elif section == "business_rules":
                    area["rule_titles"].append(item.title)
        if fragment.purpose_hint:
            area_for(default_code)["what_this_area_does"].append(fragment.purpose_hint)

    areas = []
    for area in grouped.values():
        counts = area.pop("counts")
        # Bounded so one enormous area cannot crowd the others out of the digest.
        area["boundaries"] = sorted(set(area["boundaries"]))[:12]
        area["what_this_area_does"] = area["what_this_area_does"][:6]
        # Every capability title is listed. The cap used to be 40, which was safe when the
        # elevation step bound whole areas and only needed a flavour of each; now it binds
        # capabilities individually and one it cannot see is one it cannot place. Titles are a
        # handful of words each, so the digest still grows far more slowly than the document.
        area["capability_titles_omitted"] = max(0, len(area["capability_titles"]) - 250)
        area["capability_titles"] = area["capability_titles"][:250]
        area["rule_titles"] = area["rule_titles"][:60]
        area["finding_counts"] = dict(counts)
        areas.append(area)
    areas.sort(key=lambda item: item["area_code"])

    digest = {
        "areas": areas,
        "area_count": len(areas),
        "total_findings": sum(sum(a["finding_counts"].values()) for a in areas),
        "note": (
            "Titles only. Every requirement listed here is already written out in full and "
            "already grounded; do not restate any of them. Bind each capability to the business "
            "requirement it realises, by title. Areas are shown only to group the list for "
            "reading -- they record where code was found, not what it is for, so do not bind by "
            "area: capabilities under one area often serve different outcomes."
        ),
    }
    return CapabilityResult(
        artifacts={"area_digest": digest},
        metrics={
            "areas": len(areas),
            "digested_findings": digest["total_findings"],
        },
        messages=[
            f"Digested {digest['total_findings']} finding(s) across {len(areas)} functional "
            "area(s) into titles only."
        ],
    )


def _carry(
    item: BRDItem,
    area: str,
    section: str,
    cluster_id: str = "",
    conflict: bool = False,
    remap: dict[str, str] | None = None,
) -> BRDItem:
    """One fragment finding, moved across unchanged except for the bookkeeping it lacked.

    A requirement that named its own area keeps it. A single entry point routinely reaches several
    unrelated parts of a product — one chat box covering safety checking, routing, two answering
    paths and history — and filing all of them under the entry point's area would publish one
    lumped heading where the reader needs five.
    """
    carried = item.model_copy(deep=True)
    declared = _clean_code(item.area)
    carried.area = (remap or {}).get(declared, declared) or area
    carried.id = ""
    carried.cluster_id = cluster_id
    carried.cluster_conflict = conflict
    carried.parent_id = ""
    if not carried.requirement_type:
        carried.requirement_type = None  # settled later, from the section and the graph
    if carried.priority not in {"M", "S", "C"}:
        carried.priority = "M"
    return carried


# How many citations and trace nodes an outcome carries. An outcome standing over twenty
# capabilities does not become more credible by listing sixty lines; it becomes unreadable. A
# handful is enough for a reviewer to check the outcome is about the right part of the product,
# and the full evidence is one link away on each capability beneath it.
OUTCOME_EVIDENCE_CAP = 6
OUTCOME_TRACE_CAP = 8


def ground_outcomes(brd: ProductBRD) -> tuple[int, int]:
    """Give every business requirement the evidence of the capabilities that realise it.

    The step that writes the top tier sees area names and requirement titles — never a file, never
    a line number. Asked for a citation anyway it produced one, and what it produced was a
    plausible-looking fabrication: a real file with line 0, or a symbol that is nowhere near the
    line named. The gate caught every one, the repair budget could not fix what was never
    knowable, and quarantine then deleted the whole top tier — taking with it the parent of every
    capability in the document. A run whose synthesis had produced fourteen capabilities and two
    outcomes published one capability and no outcome at all.

    An outcome's warrant is not a line of code. It is that these capabilities, each already cited,
    add up to it. So the citation is derived here from the children rather than asked of a step
    that cannot know it, which is both honest and checkable: follow the outcome to its
    capabilities, and each of those to its own lines.

    An outcome with nothing under it gets no evidence and keeps none. That is a real finding —
    resolution reclassifies it, and the gate reports it — and papering over it with a borrowed
    citation would hide exactly the case this document must not assert.

    Re-run whenever the set changes, and idempotent so that it can be: a capability removed by
    resolution, by a repair, or by quarantine takes its citation off the outcome with it. Derived
    once at assembly, an outcome could inherit a citation from a child that was about to be
    rejected and then be rejected itself for a fault that was never its own.
    """
    children: dict[str, list[BRDItem]] = defaultdict(list)
    for item in brd.functional_requirements:
        if item.parent:
            children[title_key(item.parent)].append(item)

    grounded = ungrounded = 0
    for outcome in brd.business_requirements:
        realising = children.get(title_key(outcome.title), [])
        if not realising:
            outcome.evidence = []
            outcome.graph_path = []
            ungrounded += 1
            continue

        seen_spans: set[tuple] = set()
        evidence = []
        for child in realising:
            for location in child.evidence:
                key = (location.file, location.line_start, location.line_end)
                if key in seen_spans:
                    continue
                seen_spans.add(key)
                evidence.append(location.model_copy(deep=True))
                break  # one citation per capability keeps the set representative, not exhaustive
        trace: list[str] = []
        for child in realising:
            for name in child.graph_path:
                if name not in trace:
                    trace.append(name)

        outcome.evidence = evidence[:OUTCOME_EVIDENCE_CAP]
        outcome.graph_path = trace[:OUTCOME_TRACE_CAP]
        # An outcome is never more certain than the least certain capability holding it up.
        # Computed from the children alone rather than folded into whatever was there, so running
        # this again after the set changes gives the same answer rather than a lower one.
        outcome.confidence = min(child.confidence for child in realising)
        outcome.rationale = (
            outcome.rationale
            or "Derived from the capabilities realising it: "
            + ", ".join(child.title for child in realising[:6])
            + ("…" if len(realising) > 6 else "")
        )
        grounded += 1
    return grounded, ungrounded


@capability("assemble_requirements")
def assemble_requirements(ctx: CapabilityContext) -> CapabilityResult:
    """Build the requirements set from the fragments and the elevated top tier.

    Every grounded finding survives. Nothing is summarised, reworded, or dropped for space — the
    only claims that leave from here are the ones a gate rejects, which is where that decision
    belongs.
    """
    payloads = _fragments(ctx)
    top = ctx.load("top_tier", TopTier)
    clusters = (ctx.state.get("artifacts") or {}).get("capability_clusters")
    by_title: dict[str, str] = {}
    conflicted: set[str] = set()
    if clusters:
        loaded = ctx.load("capability_clusters")
        by_title = dict(loaded.get("by_title") or {})
        conflicted = {
            cluster["cluster_id"]
            for cluster in loaded.get("clusters", [])
            if cluster.get("relation") == "conflicting"
        }
    codes, names, remap = _area_index(payloads, _module_names(ctx))
    modules = _module_codes(payloads)

    brd = ProductBRD(
        product_purpose=top.product_purpose,
        business_problem=top.business_problem,
        business_requirements=[item.model_copy(deep=True) for item in top.business_requirements],
        assumptions=list(top.assumptions),
        out_of_scope=list(top.out_of_scope),
        open_questions=list(top.open_questions),
        personas=list(top.personas),
        areas=[AreaLabel(code=code, name=name) for code, name in sorted(names.items())],
    )
    for requirement in brd.business_requirements:
        requirement.requirement_type = "business"
        requirement.parent = None

    # capability title -> the outcome it realises, as the elevation step judged it.
    #
    # This used to be keyed on the *area* a capability was found in, so everything discovered in
    # one part of the tree inherited one outcome. Code locality is not intent: capabilities in a
    # single module routinely serve different outcomes, and a single outcome is routinely
    # realised across several modules. The binding is now per capability, by name.
    published = {title_key(item.title): item.title for item in brd.business_requirements}
    bindings: dict[str, str] = {}
    unbound: list[str] = []
    for binding in top.bindings:
        resolved = published.get(title_key(binding.business_requirement))
        if not resolved:
            unbound.append(binding.business_requirement)
            continue
        for named in binding.realised_by:
            key = title_key(named)
            if key:
                # First binding wins. A capability named under two outcomes is a judgement the
                # model failed to make, and picking the later one silently would hide that.
                bindings.setdefault(key, resolved)

    received: Counter[str] = Counter()
    seen_areas: set[str] = set()
    parsed = 0
    unreadable = 0
    for position, payload in enumerate(payloads):
        fragment = _as_fragment(payload)
        if fragment is None:
            unreadable += 1
            continue
        parsed += 1
        default_area = codes[position]
        for section in FRAGMENT_SECTIONS:
            items = getattr(fragment, section)
            received[section] += len(items)
            target = getattr(brd, section)
            for item in items:
                cluster_id = by_title.get(title_key(item.title), "")
                carried = _carry(
                    item, default_area, section, cluster_id, cluster_id in conflicted, remap
                )
                carried.area = item_area(item, payload, default_area, remap, modules)
                seen_areas.add(carried.area)
                if section == "functional_requirements":
                    # Bound by what this capability is, not by where it was found. A capability
                    # the elevation step did not place is left unattached and counted: the gate
                    # reports it as a gap, which is a true statement about the analysis, whereas
                    # falling back to its area would be a false statement about the product.
                    #
                    carried.parent = bindings.get(title_key(carried.title))
                    if carried.parent:
                        carried.parent_relation = carried.parent_relation or "refines"
                target.append(carried)

    # An "outcome" that is one of the capabilities under it, by name, is that capability restated
    # at a level it does not reach. The step that writes the top tier is shown capability titles
    # and asked for the outcomes they add up to, and under a large digest it reliably returns some
    # of the titles back -- which then publishes one requirement twice, in two sections, under one
    # name, and gives the reader a hierarchy in which a thing realises itself.
    #
    # Judged on the title rather than on wording, because an exact collision needs no threshold
    # and no interpretation. Nothing is lost: the capability itself is published, in full, with
    # its own evidence.
    capability_titles = {title_key(item.title) for item in brd.functional_requirements}
    restated = [
        outcome
        for outcome in brd.business_requirements
        if title_key(outcome.title) in capability_titles
    ]
    if restated:
        dropped = {title_key(outcome.title) for outcome in restated}
        brd.business_requirements = [
            outcome
            for outcome in brd.business_requirements
            if title_key(outcome.title) not in dropped
        ]
        for item in brd.all_items():
            if item.parent and title_key(item.parent) in dropped:
                item.parent = None
                item.parent_relation = None
                item.parent_id = ""

    # Ground each outcome in the capabilities that realise it, rather than in a citation the step
    # that wrote it was never in a position to give.
    grounded_outcomes, ungrounded_outcomes = ground_outcomes(brd)

    # Capabilities the elevation step placed under no outcome. Reported rather than patched:
    # inventing a business requirement to hang them on, or falling back to the area they were
    # found in, is exactly the fabrication the whole workflow exists to prevent.
    unattached = [
        item.title for item in brd.functional_requirements if not item.parent
    ]
    # Named in a binding but matching no capability that survived — a paraphrased title.
    placed = {title_key(item.title) for item in brd.functional_requirements}
    unmatched = sorted({key for key in bindings if key not in placed})

    messages: list[str] = []
    if unreadable:
        messages.append(f"{unreadable} cluster reply/replies could not be parsed and were skipped.")
    messages.append(
        f"Carried {sum(received.values())} grounded finding(s) from {parsed} cluster(s) across "
        f"{len(seen_areas)} area(s) into the requirements set, unchanged."
    )
    if unattached:
        messages.append(
            f"{len(unattached)} capability/capabilities were placed under no business requirement "
            f"and are published unattached: {', '.join(sorted(unattached)[:8])}."
        )
    if unmatched:
        messages.append(
            f"{len(unmatched)} binding(s) named a capability that does not exist under that "
            "title, so nothing was attached by them."
        )
    if unbound:
        messages.append(
            f"{len(unbound)} binding(s) named a business requirement that does not exist."
        )
    if restated:
        messages.append(
            f"{len(restated)} business requirement(s) named a capability that is published below "
            "them and were not carried across: an outcome that is one of its own capabilities, by "
            f"name, is that capability restated. Dropped: {', '.join(o.title for o in restated[:6])}."
        )
    if grounded_outcomes:
        messages.append(
            f"Grounded {grounded_outcomes} business requirement(s) in the evidence of the "
            "capabilities realising them. The step that writes an outcome sees only titles, so a "
            "citation asked of it is a guess; derived from the children it is checkable."
        )
    if ungrounded_outcomes:
        messages.append(
            f"{ungrounded_outcomes} business requirement(s) have no capability beneath them and "
            "therefore carry no evidence. They are left as they are for resolution to settle."
        )

    metrics: dict[str, Any] = {
        "clusters_received": len(payloads),
        "clusters_parsed": parsed,
        "clusters_unreadable": unreadable,
        "areas": len(seen_areas),
        "capabilities_bound": len(brd.functional_requirements) - len(unattached),
        "capabilities_unattached": len(unattached),
        "outcomes_grounded_in_capabilities": grounded_outcomes,
        "outcomes_dropped_as_restated_capabilities": len(restated),
        "outcomes_without_capabilities": ungrounded_outcomes,
        "bindings_unmatched": len(unmatched),
        "business_requirements": len(brd.business_requirements),
        # The number that used to vanish silently. Findings in must equal items out, minus only
        # what a gate later rejects on the record.
        "findings_carried": sum(received.values()),
        **{f"carried_{section}": count for section, count in received.items()},
    }
    return CapabilityResult(artifacts={"brd": brd}, metrics=metrics, messages=messages)


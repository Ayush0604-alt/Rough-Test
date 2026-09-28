"""The top tier, written in several small calls instead of one impossible one.

`elevate` used to be a single call handed every capability title in the repository and asked for
three things at once: what the product is for, the handful of outcomes it delivers, and which
outcome every capability serves -- by exact title. On a 374-capability run it bound eight, two to
each of four outcomes, and one of the eight ("Display Candidate Table") did not exist. Two of the
four outcomes then failed validation because the one or two capabilities under them did, and the
published document opened with two business objectives over a product with twenty-one parts.

The judgement is the same; the altitude it is asked at changes. The deterministic steps here
surround four kinds of model call:

    index_capabilities         every capability gets an id (C001...) and one line of statement
    propose_outcomes    (E1)   per part of the product: 1-3 candidate outcomes, bound *by id*
    collect_outcome_proposals  validate the ids, number the proposals (P01...)
    elevate             (E2)   merge proposals into the product's outcomes, *by proposal id*
    compose_outcomes    (E3)   expand proposal ids into capability titles -- no model, no guessing
    bind_orphans        (E4)   what nothing proposed: outcome or NONE, per capability
    collect_orphan_bindings    fold those answers in
    review_outcomes     (E5)   per outcome: acceptance criteria, and "is this just an area?"
    apply_outcome_reviews      fold those answers in

A model never names a capability by title anywhere in this chain, so it cannot name one that does
not exist; an id it was not given is discarded and counted. Every step degrades to the old
behaviour when the one before it produced nothing, and `multi_call_elevation` off restores it.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any

from agentic.capabilities.assemble import (
    _area_index,
    _as_fragment,
    _fragments,
    _module_codes,
    _module_names,
    item_area,
)
from agentic.capabilities.registry import CapabilityContext, CapabilityResult, capability
from agentic.schemas.brd import (
    CapabilityAssignments,
    OutcomeBinding,
    OutcomeProposals,
    OutcomeReview,
    TopTier,
    title_key,
)

# One line of statement per capability is what lets a worker judge what it achieves; the full
# statement is already written and does not need re-reading here.
STATEMENT_CHARS = 220

# Capability rows shown to the reviewer of one outcome. Enough to write criteria from; an outcome
# standing over more than this is judged on a representative sample.
REVIEW_CAPABILITIES = 30

# Criteria kept per outcome.
MAX_OUTCOME_CRITERIA = 5


def _enabled(ctx: CapabilityContext) -> bool:
    return bool(getattr(ctx.settings, "multi_call_elevation", True))


def _short(text: str) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= STATEMENT_CHARS else text[: STATEMENT_CHARS - 1] + "…"


def _chunks(items: list[Any], size: int) -> list[list[Any]]:
    size = max(1, size)
    return [items[start : start + size] for start in range(0, len(items), size)]


def _index(ctx: CapabilityContext) -> list[dict[str, Any]]:
    loaded = ctx.load("capability_index")
    return list((loaded or {}).get("capabilities") or []) if isinstance(loaded, dict) else []


# --------------------------------------------------------------------------------------------
# Index
# --------------------------------------------------------------------------------------------


@capability("index_capabilities")
def index_capabilities(ctx: CapabilityContext) -> CapabilityResult:
    """Give every capability an id, and cut the per-part work items for proposing outcomes."""
    payloads = _fragments(ctx)
    codes, names, remap = _area_index(payloads, _module_names(ctx))
    modules = _module_codes(payloads)

    purposes: dict[str, str] = {}
    if "module_analyses" in (ctx.state.get("artifacts") or {}):
        loaded = ctx.load("module_analyses")
        analyses = (loaded.get("analyses") or []) if isinstance(loaded, dict) else []
        for analysis in analyses:
            code = str(analysis.get("module_code") or "")
            if code and analysis.get("module_purpose"):
                purposes.setdefault(code, str(analysis["module_purpose"]))

    capabilities: list[dict[str, Any]] = []
    rules: dict[str, list[str]] = defaultdict(list)
    seen: set[str] = set()
    for position, payload in enumerate(payloads):
        fragment = _as_fragment(payload)
        if fragment is None:
            continue
        default_code = codes[position]
        for item in fragment.functional_requirements:
            key = title_key(item.title)
            # One id per title: assembly binds by title, so two capabilities sharing a title are
            # bound together whatever id either carried.
            if not key or key in seen:
                continue
            seen.add(key)
            capabilities.append(
                {
                    "id": f"C{len(capabilities) + 1:03d}",
                    "title": item.title,
                    "statement": _short(item.statement),
                    "area": item_area(item, payload, default_code, remap, modules),
                }
            )
        for rule in fragment.business_rules:
            rules[item_area(rule, payload, default_code, remap, modules)].append(rule.title)

    by_area: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for entry in capabilities:
        by_area[entry["area"]].append(entry)

    work_items: list[dict[str, Any]] = []
    if _enabled(ctx):
        per_item = int(getattr(ctx.settings, "outcome_capabilities_per_item", 60) or 60)
        for area in sorted(by_area):
            parts = _chunks(by_area[area], per_item)
            for number, part in enumerate(parts, start=1):
                work_items.append(
                    {
                        "area_code": area,
                        "area_name": names.get(area, ""),
                        "area_purpose": purposes.get(area, ""),
                        "part": number,
                        "of_parts": len(parts),
                        "capabilities": [
                            {"id": c["id"], "title": c["title"], "statement": c["statement"]}
                            for c in part
                        ],
                        "rule_titles": rules.get(area, [])[:12],
                    }
                )

    return CapabilityResult(
        artifacts={
            "capability_index": {"capabilities": capabilities},
            "outcome_work_items": work_items,
        },
        metrics={
            "capabilities_indexed": len(capabilities),
            "outcome_work_items": len(work_items),
        },
        messages=[
            f"Indexed {len(capabilities)} capability/capabilities across {len(by_area)} area(s) "
            f"into {len(work_items)} outcome-proposal work item(s)."
        ],
    )


# --------------------------------------------------------------------------------------------
# E1 reduce
# --------------------------------------------------------------------------------------------


@capability("collect_outcome_proposals")
def collect_outcome_proposals(ctx: CapabilityContext) -> CapabilityResult:
    """Validate every proposal's capability ids and number the proposals P01, P02, ..."""
    known = {entry["id"]: entry for entry in _index(ctx)}
    proposals: list[dict[str, Any]] = []
    invalid_ids = 0
    unreadable = 0
    claimed: set[str] = set()
    unplaced: set[str] = set()
    seen_titles: dict[tuple[str, str], dict[str, Any]] = {}

    for payload in ctx.state.get("outcome_fragments") or []:
        try:
            reply = OutcomeProposals.model_validate(
                {k: v for k, v in payload.items() if k in OutcomeProposals.model_fields}
            )
        except Exception:
            unreadable += 1
            continue
        unplaced.update(cid for cid in reply.unplaced if cid in known)
        for proposed in reply.proposals:
            ids: list[str] = []
            for cid in proposed.capability_ids:
                cid = cid.strip().upper()
                if cid not in known:
                    invalid_ids += 1
                    continue
                if cid in claimed:
                    continue  # first proposal to claim a capability keeps it
                claimed.add(cid)
                ids.append(cid)
            title = " ".join(proposed.title.split())
            if not title:
                continue
            key = (reply.area_code, title_key(title))
            if key in seen_titles:
                # The same outcome proposed twice for one area (a large area is cut into parts).
                seen_titles[key]["capability_ids"].extend(ids)
                continue
            entry = {
                "id": f"P{len(proposals) + 1:02d}",
                "area_code": reply.area_code,
                "title": title,
                "statement": " ".join(proposed.statement.split()),
                "capability_ids": ids,
            }
            seen_titles[key] = entry
            proposals.append(entry)

    proposals = [entry for entry in proposals if entry["capability_ids"]]
    messages = [
        f"Collected {len(proposals)} candidate outcome(s) binding {len(claimed)} of "
        f"{len(known)} capability/capabilities by id."
    ]
    if invalid_ids:
        messages.append(
            f"{invalid_ids} capability id(s) named by a proposal were never given to it and were "
            "discarded rather than guessed at."
        )
    if unreadable:
        messages.append(f"{unreadable} proposal reply/replies could not be read.")
    return CapabilityResult(
        artifacts={
            "outcome_proposals": {
                "proposals": proposals,
                "unplaced": sorted(unplaced - claimed),
            }
        },
        metrics={
            "outcome_proposals": len(proposals),
            "capabilities_proposed": len(claimed),
            "proposal_ids_invalid": invalid_ids,
            "proposal_replies_unreadable": unreadable,
        },
        messages=messages,
    )


# --------------------------------------------------------------------------------------------
# E3 compose
# --------------------------------------------------------------------------------------------


def _outcome_ids(top: TopTier) -> dict[str, str]:
    """O1, O2, ... -> business requirement title, in the order the top tier returned them."""
    return {
        f"O{number}": outcome.title
        for number, outcome in enumerate(top.business_requirements, start=1)
        if outcome.title
    }


def _assigned(top: TopTier, title_to_id: dict[str, str]) -> dict[str, str]:
    """Capability id -> outcome title, from the bindings as they stand."""
    assigned: dict[str, str] = {}
    for binding in top.bindings:
        for title in binding.realised_by:
            cid = title_to_id.get(title_key(title))
            if cid:
                assigned.setdefault(cid, binding.business_requirement)
    return assigned


@capability("compose_outcomes")
def compose_outcomes(ctx: CapabilityContext) -> CapabilityResult:
    """Expand each outcome's proposal ids into the capabilities they bound. No model call.

    Titles the top tier named directly are kept only when a capability of that title exists; an
    invented title is counted and dropped here rather than surfacing later as a binding that
    attached nothing.
    """
    top = ctx.load("top_tier", TopTier)
    capabilities = _index(ctx)
    by_id = {entry["id"]: entry for entry in capabilities}
    title_to_id = {title_key(entry["title"]): entry["id"] for entry in capabilities}
    proposals = {
        entry["id"]: entry
        for entry in (ctx.load("outcome_proposals") or {}).get("proposals", [])
    }

    outcomes = {title_key(outcome.title): outcome.title for outcome in top.business_requirements}
    assigned: dict[str, str] = {}
    merged: set[str] = set()
    by_proposal = by_title = invented = unknown_proposals = unbound = 0
    for binding in top.bindings:
        target = outcomes.get(title_key(binding.business_requirement))
        if target is None:
            unbound += 1
            continue
        for pid in binding.from_proposals:
            proposal = proposals.get(pid.strip().upper())
            if proposal is None:
                unknown_proposals += 1
                continue
            merged.add(proposal["id"])
            for cid in proposal["capability_ids"]:
                if cid not in assigned:
                    assigned[cid] = target
                    by_proposal += 1
        for title in binding.realised_by:
            cid = title_to_id.get(title_key(title))
            if cid is None:
                invented += 1
            elif cid not in assigned:
                assigned[cid] = target
                by_title += 1

    realised: dict[str, list[str]] = defaultdict(list)
    for cid, target in assigned.items():
        realised[target].append(by_id[cid]["title"])
    top.bindings = [
        OutcomeBinding(
            business_requirement=outcome.title,
            from_proposals=next(
                (
                    list(binding.from_proposals)
                    for binding in top.bindings
                    if title_key(binding.business_requirement) == title_key(outcome.title)
                ),
                [],
            ),
            realised_by=realised.get(outcome.title, []),
        )
        for outcome in top.business_requirements
    ]

    # Proposals the merge left out travel to the sweep *whole*. On one run the merge placed 6 of
    # 80 proposals and every capability of the other 74 was swept one at a time, out of the
    # context that had grouped them; the sweep placed almost none of them and 59 of 312
    # capabilities ended up bound. A proposal already says what its capabilities are for, so it
    # is judged as one unit and its capabilities follow the answer.
    unmerged = [
        {
            "id": proposal["id"],
            "title": proposal["title"],
            "statement": proposal["statement"],
            "capability_count": len(open_ids),
            "sample_capabilities": [by_id[cid]["title"] for cid in open_ids[:6]],
        }
        for proposal in proposals.values()
        if proposal["id"] not in merged
        and (open_ids := [cid for cid in proposal["capability_ids"]
                          if cid in by_id and cid not in assigned])
    ]
    in_unmerged = {
        cid
        for proposal in proposals.values()
        if proposal["id"] not in merged
        for cid in proposal["capability_ids"]
    }
    orphans = [
        entry for entry in capabilities
        if entry["id"] not in assigned and entry["id"] not in in_unmerged
    ]
    units: list[dict[str, Any]] = [{"kind": "proposal", **row} for row in unmerged] + [
        {"kind": "capability", "id": c["id"], "title": c["title"], "statement": c["statement"]}
        for c in orphans
    ]
    batches: list[dict[str, Any]] = []
    if _enabled(ctx) and units and top.business_requirements:
        outcome_rows = [
            {"outcome_id": oid, "title": title, "statement": next(
                (o.statement for o in top.business_requirements if o.title == title), ""
            )}
            for oid, title in _outcome_ids(top).items()
        ]
        size = int(getattr(ctx.settings, "orphan_batch_size", 40) or 40)
        parts = _chunks(units, size)
        for number, part in enumerate(parts, start=1):
            batches.append(
                {
                    "batch_id": number,
                    "batch_count": len(parts),
                    "outcomes": outcome_rows,
                    "proposals": [
                        {key: unit[key] for key in
                         ("id", "title", "statement", "capability_count", "sample_capabilities")}
                        for unit in part if unit["kind"] == "proposal"
                    ],
                    "capabilities": [
                        {key: unit[key] for key in ("id", "title", "statement")}
                        for unit in part if unit["kind"] == "capability"
                    ],
                }
            )

    messages = [
        f"Bound {len(assigned)} of {len(capabilities)} capability/capabilities to "
        f"{len(top.business_requirements)} outcome(s): {by_proposal} through the "
        f"{len(merged)} of {len(proposals)} proposal(s) the outcomes merged, {by_title} by title. "
        f"{len(unmerged)} unmerged proposal(s) and {len(orphans)} single capability/capabilities "
        "go to the orphan sweep."
    ]
    if invented:
        messages.append(
            f"{invented} binding(s) named a capability title that does not exist and were "
            "dropped."
        )
    if unknown_proposals:
        messages.append(f"{unknown_proposals} proposal id(s) named by an outcome do not exist.")
    return CapabilityResult(
        artifacts={"top_tier": top, "orphan_batches": batches},
        metrics={
            "capabilities_total": len(capabilities),
            "capabilities_bound_by_proposal": by_proposal,
            "capabilities_bound_by_title": by_title,
            "capability_titles_invented": invented,
            "proposal_ids_unknown": unknown_proposals,
            "bindings_to_unknown_outcomes": unbound,
            "proposals_total": len(proposals),
            "proposals_merged": len(merged),
            "proposals_unmerged": len(unmerged),
            "capabilities_orphaned": len(orphans),
            "orphan_batches": len(batches),
        },
        messages=messages,
    )


# --------------------------------------------------------------------------------------------
# E4 reduce
# --------------------------------------------------------------------------------------------


@capability("collect_orphan_bindings")
def collect_orphan_bindings(ctx: CapabilityContext) -> CapabilityResult:
    """Fold the orphan sweep's answers into the bindings, and cut one review item per outcome."""
    top = ctx.load("top_tier", TopTier)
    capabilities = _index(ctx)
    by_id = {entry["id"]: entry for entry in capabilities}
    title_to_id = {title_key(entry["title"]): entry["id"] for entry in capabilities}
    outcome_titles = _outcome_ids(top)
    assigned = _assigned(top, title_to_id)
    proposals: dict[str, dict[str, Any]] = {}
    if "outcome_proposals" in (ctx.state.get("artifacts") or {}):
        loaded = ctx.load("outcome_proposals") or {}
        proposals = {entry["id"]: entry for entry in loaded.get("proposals", [])}

    placed = declined = invalid = proposals_placed = 0
    additions: dict[str, list[str]] = defaultdict(list)
    for payload in ctx.state.get("orphan_fragments") or []:
        try:
            reply = CapabilityAssignments.model_validate(
                {k: v for k, v in payload.items() if k in CapabilityAssignments.model_fields}
            )
        except Exception:
            continue
        for answer in reply.assignments:
            cid = answer.capability_id.strip().upper()
            oid = answer.outcome_id.strip().upper()
            if cid in proposals:
                # A whole proposal: every capability it bound that nothing has claimed since.
                target = outcome_titles.get(oid)
                if oid in {"", "NONE", "NULL"}:
                    declined += 1
                    continue
                if target is None:
                    invalid += 1
                    continue
                moved = 0
                for member in proposals[cid]["capability_ids"]:
                    if member in by_id and member not in assigned:
                        assigned[member] = target
                        additions[target].append(by_id[member]["title"])
                        moved += 1
                placed += moved
                proposals_placed += 1
                continue
            if cid not in by_id or cid in assigned:
                invalid += cid not in by_id
                continue
            if oid in {"", "NONE", "NULL"}:
                declined += 1
                continue
            target = outcome_titles.get(oid)
            if target is None:
                invalid += 1
                continue
            assigned[cid] = target
            additions[target].append(by_id[cid]["title"])
            placed += 1

    for binding in top.bindings:
        for title in additions.get(binding.business_requirement, []):
            if title not in binding.realised_by:
                binding.realised_by.append(title)
    bound_titles = {b.business_requirement for b in top.bindings}
    for target, titles in additions.items():
        if target not in bound_titles:
            top.bindings.append(OutcomeBinding(business_requirement=target, realised_by=titles))

    review_items: list[dict[str, Any]] = []
    if _enabled(ctx):
        realised = {b.business_requirement: b.realised_by for b in top.bindings}
        for oid, title in outcome_titles.items():
            outcome = next(o for o in top.business_requirements if o.title == title)
            rows = [
                by_id[title_to_id[title_key(name)]]
                for name in realised.get(title, [])
                if title_key(name) in title_to_id
            ]
            review_items.append(
                {
                    "outcome_id": oid,
                    "title": outcome.title,
                    "statement": outcome.statement,
                    "areas": sorted({row["area"] for row in rows}),
                    "capability_count": len(rows),
                    "capabilities": [
                        {"title": row["title"], "statement": row["statement"]}
                        for row in rows[:REVIEW_CAPABILITIES]
                    ],
                }
            )

    unbound = len(capabilities) - len(assigned)
    return CapabilityResult(
        artifacts={"top_tier": top, "outcome_review_items": review_items},
        metrics={
            "orphan_proposals_placed": proposals_placed,
            "orphans_placed": placed,
            "orphans_declined": declined,
            "orphan_answers_invalid": invalid,
            "capabilities_bound": len(assigned),
            "capabilities_unbound": unbound,
            "binding_coverage_percent": round(100.0 * len(assigned) / max(1, len(capabilities)), 1),
        },
        messages=[
            f"The orphan sweep placed {placed} capability/capabilities and left {declined} "
            f"deliberately unattached. {len(assigned)} of {len(capabilities)} are now bound to an "
            "outcome."
        ],
    )


# --------------------------------------------------------------------------------------------
# E5 reduce
# --------------------------------------------------------------------------------------------


@capability("apply_outcome_reviews")
def apply_outcome_reviews(ctx: CapabilityContext) -> CapabilityResult:
    """Give each outcome the criteria its reviewer wrote, and record any it flagged."""
    top = ctx.load("top_tier", TopTier)
    by_oid = {
        oid: next(o for o in top.business_requirements if o.title == title)
        for oid, title in _outcome_ids(top).items()
    }
    criteria_set = flagged = 0
    for payload in ctx.state.get("review_fragments") or []:
        try:
            review = OutcomeReview.model_validate(
                {k: v for k, v in payload.items() if k in OutcomeReview.model_fields}
            )
        except Exception:
            continue
        outcome = by_oid.get(review.outcome_id.strip().upper())
        if outcome is None:
            continue
        cleaned: list[str] = []
        seen: set[str] = set()
        for criterion in review.acceptance_criteria:
            text = " ".join(criterion.split())
            key = text.casefold().rstrip(".")
            if text and key not in seen and key != outcome.statement.casefold().rstrip("."):
                seen.add(key)
                cleaned.append(text)
        if cleaned:
            # Written from the capabilities the outcome actually stands over. Without these the
            # outcome reached hedging with no criteria and was given one derived from whichever
            # boundary its first citation happened to reach -- which is how "collect feedback"
            # came to be checked by a sysadmin health-check condition.
            outcome.acceptance_criteria = cleaned[:MAX_OUTCOME_CRITERIA]
            criteria_set += 1
        if review.renamed_area:
            flagged += 1
            question = (
                f"{outcome.title!r} may name a part of the product rather than an outcome it "
                "delivers"
                + (f": {review.concern.strip().rstrip('.')}" if review.concern.strip() else "")
                + ". Should it be restated as what someone gets from it?"
            )
            if question not in top.open_questions:
                top.open_questions.append(question)

    counts = Counter({"criteria": criteria_set, "flagged": flagged})
    return CapabilityResult(
        artifacts={"top_tier": top},
        metrics={
            "outcomes_given_criteria": counts["criteria"],
            "outcomes_flagged_as_areas": counts["flagged"],
        },
        messages=[
            f"{criteria_set} outcome(s) received acceptance criteria written from the "
            f"capabilities beneath them; {flagged} were flagged as possibly naming an area."
        ],
    )

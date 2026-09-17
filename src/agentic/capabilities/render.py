"""Phase 5 — render the validated analysis into the documents people actually read.

A hard separation is maintained: business language in the requirements document, technical
traceability in the companion files. The requirements document is verified for that separation
here as well as at the gate, because it is the one artifact that leaves the building.

These always run, even when little or nothing grounded. An honest thin document that explains why
coverage is thin is the correct output; silence is not.
"""

from __future__ import annotations

import csv
import io
import json
from collections import Counter, defaultdict
from typing import Any

from agentic.capabilities.graph_ops import GraphIndex
from agentic.capabilities.gates import NAMES_EXTERNAL_SYSTEMS
from agentic.capabilities.jargon import describe, find_jargon
from agentic.capabilities.registry import CapabilityContext, CapabilityResult, capability
from agentic.schemas.brd import PRIORITIES, BRDItem, ProductBRD, published_kind
from agentic.schemas.core import (
    GateReport,
    KnowledgeGraph,
    RepositoryInventory,
    Severity,
)

LOW_CONFIDENCE = 0.70
MERMAID_NODE_CAP = 40


def _bullet(items: list[str], empty: str) -> str:
    return "\n".join(f"- {item}" for item in items) if items else f"- _{empty}_"


def _label(title: str) -> str:
    """A title used as a lead-in, without the sentence punctuation that would double up."""
    return title.strip().rstrip(".:;,") or "Untitled"


# --------------------------------------------------------------------------------------------
# 1. Requirements document (business language only)
# --------------------------------------------------------------------------------------------


def _criteria_for(item: BRDItem, criteria_by_parent: dict[str, list[BRDItem]]) -> list[str]:
    """Every observable condition attached to one item, from both places they arrive in.

    An item carries its own `acceptance_criteria`, and a success criterion elsewhere in the
    document may name it as its parent. Both are criteria for the same requirement, so the
    document shows both — de-duplicated, because assembly legitimately produces the same
    condition through both routes.
    """
    seen: set[str] = set()
    ordered: list[str] = []
    linked = criteria_by_parent.get(item.id, [])
    for text in [*item.acceptance_criteria, *(criterion.statement for criterion in linked)]:
        cleaned = " ".join(text.split())
        key = cleaned.casefold().rstrip(".")
        if cleaned and key not in seen:
            seen.add(key)
            ordered.append(cleaned)
    return ordered


def _prose_list(entries: list[str]) -> str:
    """A readable list inside a sentence: "a", "a and b", "a, b and c"."""
    cleaned = [" ".join(entry.split()) for entry in entries if entry and entry.strip()]
    if not cleaned:
        return ""
    if len(cleaned) == 1:
        return cleaned[0]
    return ", ".join(cleaned[:-1]) + " and " + cleaned[-1]


def _cell(text: str) -> str:
    """Text safe to place inside a Markdown table cell."""
    return " ".join(str(text).split()).replace("|", "\\|")


def _evidence_cell(item: BRDItem, limit: int = 3) -> str:
    """The citations behind one requirement, rendered for the document itself.

    Evidence used to live only in the companion traceability matrix, on the reasoning that the
    business document should stay readable. That was the wrong trade for a *reverse-engineered*
    document: here the citation is not an appendix, it is the warrant for the claim. A reader
    checking whether the tool invented a requirement should not have to open a second file, and a
    requirement whose evidence is one click away is one nobody checks.
    """
    parts: list[str] = []
    for location in item.evidence[:limit]:
        reference = location.file
        if location.line_start:
            span = str(location.line_start)
            if location.line_end and location.line_end != location.line_start:
                span += f"-{location.line_end}"
            reference += f":{span}"
        symbol = location.symbol or location.scope
        parts.append(f"`{reference}`" + (f" `{symbol}`" if symbol else ""))
    extra = len(item.evidence) - limit
    if extra > 0:
        parts.append(f"(+{extra} more)")
    return "; ".join(parts) or "_no citation_"


def _requirement_table(items: list[BRDItem], criteria_by_parent) -> list[str]:
    """One area's requirements as the reference document renders them."""
    lines = [
        "| ID | Requirement | Pri | Evidence |",
        "|---|---|---|---|",
    ]
    for item in items:
        lines.append(
            f"| {item.id} | {_cell(item.statement)} | {item.priority} | "
            f"{_evidence_cell(item)} |"
        )
    return lines


def _criteria_block(items: list[BRDItem], criteria_by_parent) -> list[str]:
    """Acceptance criteria beneath the table, where they are readable as a checklist."""
    lines: list[str] = []
    for item in items:
        criteria = _criteria_for(item, criteria_by_parent)
        if not criteria:
            continue
        lines += [f"**{item.id} — {_label(item.title)}** — acceptance criteria:", ""]
        lines += [f"- {criterion}" for criterion in criteria]
        lines.append("")
    return lines


def _supplement_table(items: list[BRDItem], *, category: bool = False) -> list[str]:
    header = (
        ["| ID | Area | Requirement | Evidence |", "|---|---|---|---|"]
        if category
        else ["| ID | Requirement | Evidence |", "|---|---|---|"]
    )
    lines = list(header)
    for item in items:
        if category:
            lines.append(
                f"| {item.id} | {_cell(item.area or '—')} | {_cell(item.statement)} | "
                f"{_evidence_cell(item)} |"
            )
        else:
            lines.append(
                f"| {item.id} | {_cell(item.statement)} | {_evidence_cell(item)} |"
            )
    return lines


def _numbered(counter: list[int], heading: str) -> str:
    """The next section number, so a document only numbers the sections it actually has.

    A fixed numbering forces every section to be printed whether or not anything was found for
    it, and a document that says "nothing in this category was evidenced" six times reads as a
    template someone filled in badly rather than as an account of one product. A reader learns
    nothing from the absence of a category they never asked about; where an absence *is*
    meaningful it is stated under "Explicitly out of scope", which is evidenced.
    """
    counter[0] += 1
    return f"## {counter[0]}. {heading}"


def _outcome_block(
    requirement: BRDItem,
    children: list[BRDItem],
    criteria_by_parent: dict[str, list[BRDItem]],
) -> list[str]:
    """One business objective, written the way a stakeholder reads one.

    The statement first and in full, then who it is for, then how it would be recognised as met,
    then the capabilities that deliver it. The citation goes last: it is the warrant for the
    claim, and a reader who is not auditing should not have to step over it to reach the point.
    """
    lines = [f"### {_label(requirement.title)}", "", requirement.statement, ""]
    if requirement.persona:
        lines += [f"- **Who it serves:** {requirement.persona}"]
    if requirement.business_value:
        lines += [f"- **Value delivered:** {requirement.business_value}"]
    criteria = _criteria_for(requirement, criteria_by_parent)
    if criteria:
        lines += ["- **Acceptance criteria:**"]
        lines += [f"  - {criterion}" for criterion in criteria]
    if children:
        lines += [
            "- **Delivered by:** "
            + ", ".join(f"{child.id} ({_label(child.title)})" for child in children)
        ]
    lines += [f"- **Traced to:** {_evidence_cell(requirement)}", "", f"_{requirement.id}_", ""]
    return lines


def render_requirements_md(brd: ProductBRD, repository_name: str) -> str:
    """The requirements document, in the shape a business analyst reads.

    Organised as the classification model nests it -- the outcomes first, then the capabilities
    that deliver them grouped by the part of the product they belong to, then the rules
    constraining those -- and *only* the sections this particular product turned out to have.
    What the analysis could not stand behind is not here; it is in the companion analysis
    document, with its evidence and the reason it did not make it.
    """
    counter = [0]
    lines: list[str] = [
        f"# Business Requirements Document — {repository_name}",
        "",
        "| Field | Value |",
        "|---|---|",
        "| Status | As-built. Reconstructed from implementation evidence. |",
        "| Basis | Reverse engineering of source code, schema, and configuration |",
        "| Review state | Candidate. Nothing here has been confirmed by a stakeholder. |",
        "",
        "## How to read this document",
        "",
        "This document describes what the product **does**, not how it is built. Every "
        "requirement is written so that it would still hold if the product were rebuilt on a "
        "different stack; the implementation behind each one is named in its **Evidence** "
        "citation and in the companion traceability matrix, never in the requirement itself.",
        "",
        "**Nothing here was assumed.** No requirement has been added from industry convention or "
        "from what a product of this kind usually does. Candidates the evidence could not support "
        "are recorded in the companion analysis document rather than softened into this one.",
        "",
        "**Priority** is read off the code's own defaults, not from a stakeholder ranking: "
        "**M** = always on, **S** = behind a configuration flag or role gate, **C** = legacy, "
        "opt-in, or deprecating.",
        "",
    ]

    criteria_by_parent: dict[str, list[BRDItem]] = defaultdict(list)
    for criterion in brd.success_criteria:
        criteria_by_parent[criterion.parent_id or "_unassigned"].append(criterion)

    by_area: dict[str, list[BRDItem]] = defaultdict(list)
    for item in brd.functional_requirements:
        by_area[item.area or "GEN"].append(item)
    ordered_areas = sorted(by_area.items())

    # A capability may refine another capability, which is how a workflow with named stages, or a
    # feature with parts, is represented. The parent keeps its place in the group and its stages
    # are listed beneath it rather than as siblings, so the reader sees the decomposition the
    # evidence actually established instead of a flat list that hides it.
    capability_area: dict[str, str] = {
        item.id: item.area or "GEN" for item in brd.functional_requirements
    }
    stages_of: dict[str, list[BRDItem]] = defaultdict(list)
    nested: set[str] = set()
    for item in brd.functional_requirements:
        # Nested only when the two sit in the same group. A stage that belongs to a different part
        # of the product is listed in its own group, where a reader looking for that part will
        # find it -- showing it under its parent instead left its own group empty and put the
        # requirement under a heading it is not about.
        parent_area = capability_area.get(item.parent_id)
        if parent_area is not None and parent_area == (item.area or "GEN"):
            stages_of[item.parent_id].append(item)
            nested.add(item.id)

    # -- Executive summary --------------------------------------------------------------------
    lines += [_numbered(counter, "Executive summary"), ""]
    if brd.product_purpose:
        lines += [brd.product_purpose.statement, ""]
    else:
        lines += [
            "_A product purpose could not be reliably established from the source evidence. The "
            "companion gap analysis explains what was and was not found._",
            "",
        ]
    if brd.functional_requirements:
        headings = [
            brd.area_names.get(code) or "uncategorised capabilities"
            for code, _ in ordered_areas
        ]
        count = len(brd.functional_requirements)
        lines += [
            f"The analysis established {count} distinct "
            f"{'capability' if count == 1 else 'capabilities'} across "
            f"{len(ordered_areas)} {'part' if len(ordered_areas) == 1 else 'parts'} of the "
            "product: " + _prose_list(headings) + ".",
            "",
        ]

    # -- Business context ---------------------------------------------------------------------
    if brd.business_problem:
        lines += [
            _numbered(counter, "Business context and problem"),
            "",
            brd.business_problem.statement,
            "",
        ]

    # -- Business objectives ------------------------------------------------------------------
    lines += [_numbered(counter, "Business objectives"), ""]
    if not brd.business_requirements:
        lines += [
            (
                "_No outcome could be established that stands over more than one capability. The "
                "capabilities in the next section are therefore published at the top level, which "
                "is an honest gap rather than an invented objective._"
                if brd.functional_requirements
                else "_No outcome could be established, because no capability was established to "
                "stand over. The companion gap analysis explains what was and was not found._"
            ),
            "",
        ]
    else:
        lines += [
            "The outcomes this product exists to deliver. Each is realised by the capabilities in "
            "the following sections, and each remains true if the implementation is rebuilt.",
            "",
        ]
        for requirement in brd.business_requirements:
            children = brd.children_of(requirement.id, "functional_requirements")
            lines += _outcome_block(requirement, children, criteria_by_parent)

    # -- Scope --------------------------------------------------------------------------------
    if ordered_areas:
        lines += [
            _numbered(counter, "Scope"),
            "",
            "The parts of the product the evidence established, and what each one covers.",
            "",
        ]
        for code, items in ordered_areas:
            heading = brd.area_names.get(code) or "Uncategorised capabilities"
            # Titles as written. Lower-casing them to fit the sentence turned every acronym and
            # proper noun in the product's own vocabulary into a common noun.
            covered = _prose_list([_label(item.title) for item in items[:5]])
            more = f", and {len(items) - 5} more" if len(items) > 5 else ""
            lines.append(f"- **{heading}:** {covered}{more}.")
        lines.append("")

    # -- Capabilities -------------------------------------------------------------------------
    heading_number = counter[0] + 1
    lines += [_numbered(counter, "Capabilities"), ""]
    if not ordered_areas:
        lines += [
            "_No capability was confirmed at a point where the system is consumed from outside._",
            "",
        ]
    for index, (code, items) in enumerate(ordered_areas, start=1):
        heading = brd.area_names.get(code) or "Uncategorised capabilities"
        lines += [f"### {heading_number}.{index} {heading}", ""]
        parents = {item.parent_id for item in items if item.parent_id}
        realised = [
            requirement
            for requirement in brd.business_requirements
            if requirement.id in parents
        ]
        if realised:
            lines += [
                "_Serves "
                + _prose_list([f"{item.id} ({_label(item.title)})" for item in realised])
                + "._",
                "",
            ]

        top = [item for item in items if item.id not in nested]
        if top:
            lines += _requirement_table(top, criteria_by_parent)
            lines.append("")
        for parent in top:
            stages = stages_of.get(parent.id, [])
            if not stages:
                continue
            lines += [f"**Within {_label(parent.title)}:**", ""]
            lines += _requirement_table(stages, criteria_by_parent)
            lines.append("")

        rules = [
            rule
            for rule in brd.business_rules
            if rule.parent_id in {item.id for item in items}
        ]
        if rules:
            lines += ["**Rules that apply here**", ""]
            lines += _supplement_table(rules)
            lines.append("")

        if block := _criteria_block(items, criteria_by_parent):
            lines += block

    # -- The supplementary families, each only where the evidence produced one -----------------
    for heading, items, blurb in (
        ("How the system must behave internally", brd.system_requirements,
         "Behaviour the capabilities above depend on that nobody outside the system observes "
         "directly. A different implementation would still have to provide it."),
        ("Data requirements", brd.data_requirements,
         "Invariants the stored information must hold, whatever the product does with it."),
        ("Integration requirements", brd.integration_requirements,
         "External systems this product depends on, what it needs from each, and how it behaves "
         "when one is unavailable."),
        ("Service-quality requirements", brd.non_functional_requirements,
         "Quality properties the evidence fixes concretely. Nothing aspirational appears here: a "
         "property with no measurable target was not published."),
        ("Reporting and analytics", brd.reporting_requirements,
         "Views, exports, and summaries someone reads."),
        ("Technical constraints", brd.technical_constraints,
         "Limits the platform, protocol, or deployment imposes rather than ones the business "
         "chose. Recorded because they bound what can be asked for."),
    ):
        if not items:
            continue
        lines += [_numbered(counter, heading), "", blurb, ""]
        lines += _supplement_table(items, category=True)
        lines.append("")

    # -- Business rules -----------------------------------------------------------------------
    if brd.business_rules:
        lines += [
            _numbered(counter, "Business rules and constraints"),
            "",
            "Every condition the product enforces, in one place. Each also appears under the "
            "capability it constrains.",
            "",
            "| ID | Rule | Applies to | Evidence |",
            "|---|---|---|---|",
        ]
        titles = {item.id: item.title for item in brd.functional_requirements}
        for rule in brd.business_rules:
            constrains = (
                f"{rule.parent_id} ({_label(titles[rule.parent_id])})"
                if rule.parent_id in titles
                else "_not attached to a capability_"
            )
            lines.append(
                f"| {rule.id} | {_cell(rule.statement)} | {constrains} | "
                f"{_evidence_cell(rule)} |"
            )
        lines.append("")

    # -- The honest sections, each only where there is something to say ------------------------
    for heading, entries, blurb in (
        ("Assumptions", brd.assumptions,
         "Conditions that materially affect how this document should be read. Each is itself "
         "evidenced."),
        ("Explicitly out of scope", brd.out_of_scope,
         "Capabilities a reader might assume are present, and which the evidence shows are not. "
         "Stated so nobody infers them."),
        ("Open questions for stakeholder validation", brd.open_questions,
         "Genuine ambiguities in the evidence that a person has to settle."),
    ):
        if not entries:
            continue
        lines += [_numbered(counter, heading), "", blurb, ""]
        lines += [f"- {entry}" for entry in entries]
        lines.append("")

    unassigned = criteria_by_parent.get("_unassigned", [])
    if unassigned:
        lines += [
            _numbered(counter, "Additional acceptance criteria"),
            "",
            "Observable conditions the evidence fixes that name no requirement of their own.",
            "",
            *(f"- {item.statement}" for item in unassigned),
            "",
        ]

    # -- Implementation observations -----------------------------------------------------------
    # Not requirements, and deliberately published anyway. These are behaviours the abstraction
    # test placed below the requirement line: true of this code, not required of the product. A
    # reader reviewing whether the analysis over- or under-reached needs to see what was found and
    # consciously not promoted, and the evidence is worth keeping either way.
    if brd.observations:
        lines += [
            _numbered(counter, "Implementation observations (not requirements)"),
            "",
            "Behaviour the code exhibits that the evidence does not show to be required of the "
            "product. Recorded so the reverse-engineering evidence is not lost, and so a reviewer "
            "can challenge any judgement made here. None of these is a requirement.",
            "",
            "| ID | Observation | Evidence for | Evidence |",
            "|---|---|---|---|",
        ]
        titles = {
            item.id: item.title
            for _, items in brd.sections().items()
            for item in items
        }
        for note in brd.observations:
            supports = (
                f"{note.parent_id} ({_label(titles[note.parent_id])})"
                if note.parent_id in titles
                else "_not attributed_"
            )
            lines.append(
                f"| {note.id} | {_cell(note.statement)} | {supports} | {_evidence_cell(note)} |"
            )
        lines.append("")

    # -- Traceability summary ------------------------------------------------------------------
    lines += [
        _numbered(counter, "Traceability summary"),
        "",
        "| Requirement group | Count |",
        "|---|---:|",
    ]
    for code, items in ordered_areas:
        heading = brd.area_names.get(code) or "Uncategorised"
        lines.append(f"| {heading} | {len(items)} |")
    lines.append(f"| **Total capabilities** | **{len(brd.functional_requirements)}** |")
    for label, items in (
        ("Business objectives", brd.business_requirements),
        ("Internal system behaviour", brd.system_requirements),
        ("Business rules", brd.business_rules),
        ("Technical constraints", brd.technical_constraints),
        ("Data requirements", brd.data_requirements),
        ("Integration requirements", brd.integration_requirements),
        ("Service-quality requirements", brd.non_functional_requirements),
        ("Reporting requirements", brd.reporting_requirements),
        ("Acceptance criteria published separately", brd.success_criteria),
        ("Implementation observations (not requirements)", brd.observations),
    ):
        if items:
            lines.append(f"| {label} | {len(items)} |")

    lines += [
        "",
        "---",
        "",
        "_Every requirement above is traceable to a named file and line range in the analysed "
        "source. No requirement has been included that could not be evidenced._",
        "",
    ]
    return "\n".join(lines)


# --------------------------------------------------------------------------------------------
# 2. Traceability matrix
# --------------------------------------------------------------------------------------------


def render_traceability_csv(brd: ProductBRD) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    columns = [
        "requirement_id", "tier", "parent_id", "title", "statement", "confidence",
        "source_file", "line_start", "line_end", "source_symbol", "source_scope",
        "graph_path",
    ]
    writer.writerow(columns)
    rows = 0
    # Observations are carried here too. They are not requirements, but they are real evidence
    # about the code, and the matrix is where a reviewer goes to see everything that was found.
    for section, items in brd.every_section().items():
        for item in items:
            graph_path = " -> ".join(item.graph_path)
            # `parent_id` is what makes the matrix traceable upward as well as down: a reviewer
            # can sort by it and see every capability under one outcome, and every rule under one
            # capability, without reading the document alongside it.
            head = [
                item.id, published_kind(item, section), item.parent_id, item.title,
                item.statement, item.confidence,
            ]
            if not item.evidence:
                writer.writerow([*head, "", "", "", "", "", graph_path])
                rows += 1
                continue
            for location in item.evidence:
                writer.writerow(
                    [
                        *head,
                        location.file, location.line_start or "", location.line_end or "",
                        location.symbol or "", location.scope or "", graph_path,
                    ]
                )
                rows += 1
    if rows == 0:
        writer.writerow(["", "", "", "no grounded requirements", *[""] * (len(columns) - 4)])
    return buffer.getvalue()


# --------------------------------------------------------------------------------------------
# 2b. The requirements set, as a standards-aligned typed list
# --------------------------------------------------------------------------------------------


def build_requirement_set(
    brd: ProductBRD, generator: str, module_names: set[str] | None = None
) -> list[dict[str, Any]]:
    """The four sections flattened into one typed, individually addressable list.

    A business analyst reads a requirements document as numbered items with a type, not as four
    prose sections, and every downstream use — review sign-off, test derivation, a traceability
    tool — addresses one item by its id.

    The ids here are the ones already stamped on the items, not a second numbering computed at
    render time. That mattered: the two schemes disagreed, so a gate rejecting `SCOPE-002` was
    talking about the item this file published as `FR-002`, and nothing said so.
    """
    published: list[dict[str, Any]] = []
    modules = module_names or set()

    # Requirement families only. An observation is deliberately absent: this file is what a
    # downstream tool treats as the requirement set, and an implementation detail listed beside
    # a requirement would be acted on as one. The matrix and the document appendix carry them.
    families = {**brd.sections(), **brd.supplements()}
    for section, items in families.items():
        for item in items:
            published.append(
                {
                    "id": item.id,
                    "type": published_kind(item, section),
                    # The chain, as data. A consumer can rebuild the whole hierarchy from the flat
                    # list without parsing the document.
                    "parent_id": item.parent_id or None,
                    # A relation with no resolved parent is a dangling edge: it tells a consumer
                    # there is a hierarchy here and gives it nothing to point at. The relation is
                    # a property of the link, so without the link it is not published at all.
                    "parent_relation": (item.parent_relation or None) if item.parent_id else None,
                    "abstraction_basis": item.abstraction_basis or None,
                    "title": item.title,
                    "statement": item.statement,
                    "rationale": item.rationale,
                    "persona": item.persona,
                    "business_value": item.business_value,
                    "acceptance_criteria": list(item.acceptance_criteria),
                    "confidence": item.confidence,
                    # Nothing here has been reviewed by a person, and saying so is the point.
                    # "rejected" additionally means the correspondence check found the claim and
                    # the code it cites disagree; the claim is still published, with its reason.
                    "status": item.status or "candidate",
                    "evidence": [
                        {
                            "file": location.file,
                            "line_start": location.line_start,
                            "line_end": location.line_end,
                            # Falling back to the enclosing definition names something a
                            # traceability tool can resolve. A bare module name does not: it is
                            # the file restated, and publishing it as a symbol implies a
                            # precision the citation does not have.
                            "symbol": location.symbol
                            or (location.scope if location.scope not in modules else None),
                        }
                        for location in item.evidence
                    ],
                    "graph_path": list(item.graph_path),
                    "assumptions": list(item.assumptions),
                    "open_questions": list(item.open_questions),
                    "generator": generator,
                    # Appended last, so every key a consumer already reads keeps its position.
                    # Titles of claims folded into this one: empty for almost every item, present
                    # so a merge is visible rather than looking like a claim that went missing.
                    "superseded": list(item.superseded),
                    # What the model claimed before the figure was recomputed from evidence, so
                    # the two can be compared and the recalibration itself can be judged.
                    "model_confidence": item.model_confidence,
                    # Which part of the product this requirement belongs to. Held on every item
                    # since assembly, and published here because it was previously recoverable
                    # only by parsing a functional id (`FR-CAND-01`) -- a scheme the other tiers
                    # do not use, so a system requirement or a rule arrived with its area
                    # silently dropped. A consumer grouping the flat list by part of the product
                    # had to reconstruct from ids what the document already knew.
                    "area": item.area or None,
                    # The area's published heading, denormalised onto the item. The set is a flat
                    # array with nowhere to put a code->name table, and a bare code is not
                    # readable on its own.
                    "area_name": brd.area_names.get(item.area) or None,
                    # Derived from the code's own defaults, never a stakeholder ranking -- see
                    # `PRIORITIES`. Normalised here as well as in assembly so the published
                    # contract holds for any BRD reaching this function.
                    "priority": item.priority if item.priority in PRIORITIES else "M",
                }
            )
    return published


def render_requirements_json(
    brd: ProductBRD, generator: str, module_names: set[str] | None = None
) -> str:
    published = build_requirement_set(brd, generator, module_names)
    return json.dumps(published, indent=2, ensure_ascii=False) + "\n"


# --------------------------------------------------------------------------------------------
# 3. Gap analysis
# --------------------------------------------------------------------------------------------


def _uncovered_files(brd: ProductBRD, graph: KnowledgeGraph) -> list[str]:
    """Source files holding definitions that no published requirement traces into.

    Coverage counted by construct hides the shape of what is missing: a document can describe 88%
    of the functions in a repository and still omit an entire capability, because the omitted part
    is one file whose work nobody wrote up. A reader checking whether the analysis understood
    their system looks for the *file* they know is important, so that is the unit reported.
    """
    cited: set[str] = set()
    for item in brd.all_items():
        for location in item.evidence:
            cited.add(location.file)
    traced = {name for item in brd.all_items() for name in item.graph_path}

    by_file: dict[str, list[str]] = defaultdict(list)
    for entity in graph.entities:
        if entity.kind not in {"FUNCTION", "METHOD", "CLASS"} or entity.location is None:
            continue
        by_file[entity.location.file].append(entity.qualified_name)

    missing: list[str] = []
    for path, names in sorted(by_file.items()):
        if path in cited or any(name in traced for name in names):
            continue
        missing.append(f"`{path}` — {len(names)} definition(s), none described")
    return missing


def render_gap_analysis_md(
    brd: ProductBRD,
    graph: KnowledgeGraph,
    inventory: RepositoryInventory,
    gates: dict[str, GateReport],
    incomplete: list[str] | None = None,
) -> str:
    by_qualified = graph.by_qualified_name()
    covered: set[str] = set()
    for item in brd.all_items():
        for name in item.graph_path:
            if name in by_qualified:
                covered.add(by_qualified[name].id)

    reviewable_kinds = (
        "ENDPOINT", "UI_ACTION", "CLI_ENTRY", "SCRIPT_ENTRY", "CLASS", "FUNCTION", "METHOD",
        "BUSINESS_RULE", "CONFIG_KEY", "DATA_OPERATION", "ERROR_HANDLER",
    )
    rows: list[str] = []
    for kind in reviewable_kinds:
        of_kind = [entity for entity in graph.entities if entity.kind == kind]
        if not of_kind:
            continue
        hit = sum(entity.id in covered for entity in of_kind)
        share = f"{hit / len(of_kind):.0%}"
        rows.append(f"| {kind} | {len(of_kind)} | {hit} | {share} |")

    extraction = graph.metadata.get("extraction") or {}
    parse_errors = extraction.get("parse_errors") or []
    low_confidence = [
        f"`{item.id}` ({item.confidence:.2f}) — {item.title}"
        for item in brd.all_items()
        if item.confidence < LOW_CONFIDENCE
    ]

    warnings: list[str] = []
    rejected: list[str] = []
    for name, report in gates.items():
        for issue in report.issues:
            if issue.severity == Severity.WARNING:
                warnings.append(f"`{name}` / {issue.code}: {issue.message}")
            elif issue.severity == Severity.ERROR:
                # A blocking issue means a claim was dropped rather than published. Saying which,
                # and why, is the difference between a thin document and a misleading one.
                rejected.append(f"`{issue.code}` — {issue.message}")

    from agentic.capabilities.ingest import PARSEABLE_LANGUAGES

    # Languages present that the extractor has no grammar for. This used to be "everything that is
    # not Python", which described every real repository and told a reader nothing.
    unparsed_languages = {
        language: count
        for language, count in inventory.languages.items()
        if language not in PARSEABLE_LANGUAGES
    }

    lines = [
        "# Gap Analysis",
        "",
        "## What 'covered' means here",
        "",
        "A construct is *covered* when at least one requirement's graph trace passes through it. "
        "Coverage says nothing about whether that requirement is correct, complete, or reviewed — "
        "only that a claim was traced to it.",
        "",
        "## Coverage by construct",
        "",
        "| Construct | Found | Covered | Share |",
        "|---|---:|---:|---:|",
        *(rows or ["| _none extracted_ | 0 | 0 | — |"]),
        "",
        "## Parts of the codebase no requirement describes",
        "",
        "A source file holding real work that no published requirement traces into. This is the "
        "section to read first: a document can be internally consistent, well written, and still "
        "be describing only half the system. Each entry is either dead code, supporting detail "
        "that correctly produced no requirement of its own, or a capability the analysis missed.",
        "",
        _bullet(_uncovered_files(brd, graph), "none — every file with reachable work is described"),
        "",
        "## Requirements below the confidence threshold",
        "",
        f"Listed for review: confidence under {LOW_CONFIDENCE:.2f}.",
        "",
        _bullet(low_confidence, "none — every requirement met the threshold"),
        "",
        "## Analysis that did not complete",
        "",
        "A boundary whose analysis failed contributes nothing to this document. Everything below "
        "is therefore an understatement of what the system does, by an amount this section is the "
        "only record of.",
        "",
        _bullet(
            list(incomplete or []),
            "none — every interaction boundary was analysed",
        ),
        "",
        "## Analysis limitations",
        "",
        _bullet(
            [
                f"Extraction coverage: "
                f"{float(extraction.get('extraction_coverage', 1.0)):.1%} "
                f"({extraction.get('files_with_nodes', 0)} of "
                f"{extraction.get('files_seen', 0)} files yielded a definition). A file that "
                "yields none is usually declaration-style: it registers behaviour through "
                "top-level calls rather than named definitions, which the extractor does not "
                "model.",
                *(
                    [
                        f"{len(unparsed_languages)} language(s) present have no grammar and "
                        "received inventory-level analysis only: "
                        + ", ".join(
                            f"{k} ({v})" for k, v in sorted(unparsed_languages.items())
                        )
                        + "."
                    ]
                    if unparsed_languages
                    else []
                ),
                "Conditionals, error handlers, configuration reads and database constraints are "
                "not modelled, so business rules, data requirements and non-functional "
                "properties are under-reported relative to what the code contains.",
                "Interaction boundaries are inferred from graph shape rather than parsed from a "
                "route declaration, and are recorded as such.",
                *(
                    [f"{len(parse_errors)} file(s) could not be parsed and were skipped."]
                    if parse_errors
                    else []
                ),
                "Dynamically registered routes, reflection-based wiring, and runtime dependency "
                "injection are invisible to static analysis.",
                "Call resolution is name-based and performs no type inference, so same-named "
                "methods on unrelated types can be ambiguous.",
                "Nothing was executed and no tests were run. 'Implemented' here means present and "
                "reachable in the source, not verified correct at runtime.",
            ],
            "none recorded",
        ),
        "",
        "## Claims that did not survive validation",
        "",
        "Rejected rather than reworded. A claim the evidence could not support is removed, "
        "because a softer wording over the same missing evidence would still read as a finding.",
        "",
        _bullet(rejected, "none — every claim passed validation"),
        "",
        "## Validation warnings",
        "",
        _bullet(warnings, "none raised"),
        "",
    ]

    if parse_errors:
        lines += ["## Files that could not be parsed", ""]
        lines += [
            f"- `{error.get('file')}` — {error.get('message')}" for error in parse_errors[:40]
        ]
        lines.append("")

    if brd.is_empty():
        lines += [
            "## Why this analysis produced no requirements",
            "",
            "No claim survived grounding. This is a valid outcome, not a failure to try: the most "
            "common causes are a codebase with no interaction boundary (a library rather than an "
            "application), or source predominantly in a language that received inventory-level "
            "analysis only.",
            "",
        ]
    return "\n".join(lines)


# --------------------------------------------------------------------------------------------
# 4. Technical specification
# --------------------------------------------------------------------------------------------


def render_technical_spec_md(graph: KnowledgeGraph, inventory: RepositoryInventory) -> str:
    extraction = graph.metadata.get("extraction") or {}
    kinds = Counter(entity.kind for entity in graph.entities)
    relations = Counter(item.kind for item in graph.relationships)

    lines = [
        f"# Technical Specification — {graph.repository_name}",
        "",
        "The implementation detail deliberately excluded from the requirements document.",
        "",
        "## Coverage",
        "",
        "| Metric | Value |",
        "|---|---:|",
        f"| Files inventoried | {len(inventory.files)} |",
        f"| Files extraction saw | {extraction.get('files_seen', 0)} |",
        f"| Files yielding a definition | {extraction.get('files_with_nodes', 0)} |",
        f"| Extraction coverage | {float(extraction.get('extraction_coverage', 1.0)):.1%} |",
        f"| Languages | {', '.join(sorted(extraction.get('languages') or {})) or '—'} |",
        f"| Graph entities | {len(graph.entities)} |",
        f"| Graph relationships | {len(graph.relationships)} |",
        "",
        "## Entity inventory",
        "",
        "| Kind | Count |",
        "|---|---:|",
        *(f"| {kind} | {count} |" for kind, count in sorted(kinds.items())),
        "",
        "## Relationship inventory",
        "",
        "| Kind | Count |",
        "|---|---:|",
        *(f"| {kind} | {count} |" for kind, count in sorted(relations.items())),
        "",
    ]

    for kind, heading in (
        ("MODULE", "Modules"),
        ("CLASS", "Classes"),
        ("CONFIG_KEY", "Configuration keys"),
        ("DATA_OPERATION", "Data operations"),
    ):
        entries = sorted(
            (entity for entity in graph.entities if entity.kind == kind),
            key=lambda entity: entity.qualified_name,
        )
        if not entries:
            continue
        lines += [f"## {heading}", ""]
        for entity in entries[:200]:
            where = f" — `{entity.location.compact()}`" if entity.location else ""
            note = ""
            if kind == "CONFIG_KEY":
                source = entity.attributes.get("source", "")
                value = (
                    "<redacted>"
                    if entity.attributes.get("secret_like")
                    else entity.attributes.get("value")
                )
                note = f" ({source}{'' if value is None else f', default {value!r}'})"
            lines.append(f"- `{entity.qualified_name}`{note}{where}")
        if len(entries) > 200:
            lines.append(f"- _…and {len(entries) - 200} more_")
        lines.append("")
    return "\n".join(lines)


# --------------------------------------------------------------------------------------------
# 5. API documentation
# --------------------------------------------------------------------------------------------


def render_api_docs_md(graph: KnowledgeGraph) -> str:
    endpoints = sorted(
        (entity for entity in graph.entities if entity.kind == "ENDPOINT"),
        key=lambda entity: (entity.attributes.get("path", ""), entity.attributes.get("method", "")),
    )
    lines = [f"# API Documentation — {graph.repository_name}", ""]
    if not endpoints:
        lines += [
            "No statically declared HTTP endpoints were detected.",
            "",
            "Endpoint detection requires a literal path string in a route declaration. Routes "
            "registered dynamically at runtime are invisible to static analysis, so their absence "
            "here does not prove the system has none.",
            "",
        ]
    else:
        # Method and path are printed only where extraction actually recovered them. The current
        # extractor records that a handler references a route registrar; it does not record the
        # literal path handed to that registrar, so for most repositories these columns are empty
        # and the handler is the only honest identifier. Printing a guessed path, or an "ANY"
        # method that reads as a finding, would state something the evidence does not support.
        described = [
            endpoint
            for endpoint in endpoints
            if endpoint.attributes.get("method") or endpoint.attributes.get("path")
        ]
        if not described:
            lines += [
                "Route paths were not recovered for these endpoints. The extractor records that a "
                "handler is registered as a route but not the literal path it is registered "
                "under, so each endpoint is identified by the handler that serves it.",
                "",
                "| Handler | Source |",
                "|---|---|",
            ]
            for endpoint in endpoints:
                location = endpoint.location.compact() if endpoint.location else ""
                lines.append(
                    f"| `{endpoint.attributes.get('handler', endpoint.name)}` | `{location}` |"
                )
        else:
            lines += [
                "| Method | Path | Handler | Source |",
                "|---|---|---|---|",
            ]
            for endpoint in endpoints:
                location = endpoint.location.compact() if endpoint.location else ""
                lines.append(
                    f"| {endpoint.attributes.get('method', '—')} "
                    f"| `{endpoint.attributes.get('path', endpoint.name)}` "
                    f"| `{endpoint.attributes.get('handler', '')}` "
                    f"| `{location}` |"
                )
        lines.append("")

    entries = [
        entity
        for entity in graph.entities
        if entity.kind in {"CLI_ENTRY", "SCRIPT_ENTRY"}
    ]
    if entries:
        lines += ["## Other entry points", ""]
        for entry in sorted(entries, key=lambda entity: entity.qualified_name):
            location = entry.location.compact() if entry.location else ""
            lines.append(f"- **{entry.name}** — `{location}`")
        lines.append("")
    return "\n".join(lines)


# --------------------------------------------------------------------------------------------
# 6. Workflow diagram
# --------------------------------------------------------------------------------------------


def render_workflows_mmd(graph: KnowledgeGraph, limit: int = MERMAID_NODE_CAP) -> str:
    index = GraphIndex(graph)
    skip = {"FILE", "IMPORTED_SYMBOL", "EXTERNAL_SYMBOL", "MODULE"}
    ranked = sorted(
        (entity for entity in graph.entities if entity.kind not in skip),
        key=lambda entity: (-index.degree(entity.id), entity.qualified_name),
    )[:limit]
    if not ranked:
        return "flowchart TD\n    empty[\"No workflow could be reconstructed\"]\n"

    keep = {entity.id for entity in ranked}
    alias = {entity.id: f"n{position}" for position, entity in enumerate(ranked)}
    shapes = {
        "ENDPOINT": ('([', '])'),
        "CLI_ENTRY": ('([', '])'),
        "SCRIPT_ENTRY": ('([', '])'),
        "BUSINESS_RULE": ('{{', '}}'),
        "CONFIG_KEY": ('[(', ')]'),
        "DATA_OPERATION": ('[(', ')]'),
    }

    lines = ["flowchart TD"]
    for entity in ranked:
        open_shape, close_shape = shapes.get(entity.kind, ("[", "]"))
        label = entity.name.replace('"', "'").replace("\n", " ")[:48]
        lines.append(f'    {alias[entity.id]}{open_shape}"{label}"{close_shape}')

    drawn: set[tuple[str, str, str]] = set()
    for relationship in graph.relationships:
        if relationship.source not in keep or relationship.target not in keep:
            continue
        if relationship.kind not in {"CALLS", "EXPOSES", "ENFORCES", "PERFORMS", "READS_CONFIG"}:
            continue
        key = (relationship.source, relationship.target, relationship.kind)
        if key in drawn:
            continue
        drawn.add(key)
        lines.append(
            f"    {alias[relationship.source]} -->|{relationship.kind}| "
            f"{alias[relationship.target]}"
        )
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------------------------
# Capability entry point
# --------------------------------------------------------------------------------------------


def _generator(ctx: CapabilityContext) -> str:
    """Which model actually wrote these statements, named the way the run can be reproduced.

    Every published requirement carries it, because "a model produced this" is a materially
    different provenance claim from "the parser found this", and a reader deciding how hard to
    review an item needs to know which one they are holding.
    """
    settings = ctx.settings
    deployment = getattr(settings, "azure_openai_deployment", None)
    return f"llm:azure/{deployment}" if deployment else "deterministic"


def _optional(ctx: CapabilityContext, name: str) -> dict:
    """An artifact a step earlier in the pack may legitimately not have produced.

    Both of these come from steps declared `warn_and_continue`, so a run in which one of them
    failed still reaches rendering — and rendering must not fall over because a non-essential
    stage did.
    """
    try:
        loaded = ctx.load(name)
    except (KeyError, OSError, ValueError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


@capability("render_deliverables")
def render_deliverables(ctx: CapabilityContext) -> CapabilityResult:
    brd = ctx.load("brd", ProductBRD)
    graph = ctx.load("graph", KnowledgeGraph)
    inventory = ctx.load("inventory", RepositoryInventory)
    gates = {
        "gate_graph": ctx.load("graph_gate", GateReport),
        "gate_requirements": ctx.load("requirement_gate", GateReport),
    }

    # A fan-out worker that fails is deliberately not fatal — one boundary must not discard every
    # other boundary's analysis. But the document must say so, or it silently understates the
    # system and reads exactly like a codebase that simply does less.
    incomplete = [
        f"`{record.get('step')}` did not complete: "
        + "; ".join(str(message) for message in (record.get("messages") or []))[:300]
        for record in (ctx.state.get("step_records") or [])
        if record.get("status") == "failed" and record.get("step") != "gate_requirements"
    ]

    documents = {
        "requirements.md": render_requirements_md(brd, graph.repository_name),
        "requirements.json": render_requirements_json(
            brd,
            _generator(ctx),
            {entity.qualified_name for entity in graph.entities if entity.kind == "MODULE"},
        ),
        "traceability_matrix.csv": render_traceability_csv(brd),
        "gap_analysis.md": render_gap_analysis_md(brd, graph, inventory, gates, incomplete),
        "technical_specification.md": render_technical_spec_md(graph, inventory),
        "api_documentation.md": render_api_docs_md(graph),
        "workflows.mmd": render_workflows_mmd(graph),
        # The companion analysis document. The requirements file holds requirements; everything
        # the analysis produced and could not stand behind -- rejected candidates, unsettled
        # questions, competing readings of one evidence set -- belongs here, with its evidence and
        # the reason, where a reviewer can check the judgement without a stakeholder having to
        # read past it. Written by a step that existed and was never called.
        "requirement_analysis.md": render_unresolved_md(
            _optional(ctx, "unresolved_candidates"), _optional(ctx, "synthesis_findings")
        ),
    }

    files = {name: ctx.store.save_text(name, text) for name, text in documents.items()}

    messages: list[str] = []
    # Verify the separation independently of the gate. This document is the one that leaves the
    # building, so it gets checked at the moment it is written, not only upstream.
    known_symbols = {
        entity.qualified_name
        for entity in graph.entities
        if entity.kind in {"FUNCTION", "METHOD", "CLASS", "MODULE"}
    }
    leaks = [
        f"{item.id}: {describe(findings, limit=2)}"
        for section, items in brd.every_section().items()
        for item in items
        if (
            findings := find_jargon(
                item.statement,
                known_symbols,
                allow_external_systems=section in NAMES_EXTERNAL_SYSTEMS,
            )
        )
    ]
    if leaks:
        messages.append(
            "Technical vocabulary reached the requirements document in "
            f"{len(leaks)} statement(s): {'; '.join(leaks[:3])}"
        )
    empty = [name for name, text in documents.items() if not text.strip()]
    if empty:
        messages.append(f"Rendered but empty: {', '.join(empty)}")

    return CapabilityResult(
        artifacts={"deliverables": {"files": files, "warnings": messages}},
        files=files,
        metrics={"artifacts": len(files), "jargon_leaks": len(leaks)},
        messages=messages,
    )


def render_unresolved_md(unresolved: dict, findings: dict) -> str:
    """What the analysis could not stand behind, and what the finished set still fails.

    Deliberately its own document. Keeping doubtful claims in the requirements list, marked with a
    field, meant a reader had to know which field to distrust; deleting them meant a reviewer could
    never check the judgement. A second document is the only arrangement that is honest to both.
    """
    candidates = (unresolved or {}).get("candidates") or []
    issues = (findings or {}).get("issues") or []

    lines = ["# Unresolved candidates and synthesis findings", ""]
    if not candidates and not issues:
        lines += [
            "Every candidate the analysis produced was resolved into the requirements document, "
            "and the finished set passed every structural check.",
        ]
        return "\n".join(lines) + "\n"

    if candidates:
        by_disposition: dict[str, list[dict]] = {}
        for entry in candidates:
            by_disposition.setdefault(entry.get("disposition", "unknown"), []).append(entry)

        lines += [
            f"{len(candidates)} candidate(s) were not published as requirements. Each is recorded "
            "here with its evidence so the judgement can be checked and, where the answer is a "
            "stakeholder's to give, answered.",
            "",
        ]
        for disposition in sorted(by_disposition):
            entries = by_disposition[disposition]
            lines += [f"## {disposition.replace('_', ' ').title()} ({len(entries)})", ""]
            for entry in entries:
                lines.append(f"### {entry.get('title') or '(untitled)'}")
                lines.append("")
                if entry.get("statement"):
                    lines += [f"> {entry['statement']}", ""]
                lines.append(f"- **Why:** {entry.get('reason') or 'No reason recorded.'}")
                if entry.get("type"):
                    lines.append(f"- **Filed as:** {entry['type']}")
                for citation in entry.get("evidence") or []:
                    lines.append(f"- **Evidence:** `{citation}`")
                lines.append("")

    if issues:
        lines += ["## Structural findings on the published set", ""]
        lines += [
            "These describe the finished document rather than any one candidate. They are "
            "reported rather than silently corrected, so a reviewer decides what a requirement "
            "should have said.",
            "",
            "| Finding | Subject | Detail |",
            "|---|---|---|",
        ]
        for issue in issues:
            subject = issue.get("subject_id") or "—"
            detail = str(issue.get("message", "")).replace("|", "/")
            lines.append(f"| `{issue.get('code')}` | `{subject}` | {detail} |")
        lines.append("")

    return "\n".join(lines) + "\n"

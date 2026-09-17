"""Deal with what a gate rejected once the repair budget is spent, without discarding evidence.

Reached only when the bounded repair budget is exhausted. Two kinds of rejection arrive here and
they deserve opposite answers, which is the distinction this module exists to draw.

**A grounding failure** — no evidence, a line that does not exist, a trace reaching no boundary, a
statement that did not survive whatever produced it — means the claim was never established. It is
removed, never softened: a weaker wording over the same missing evidence would still read as a
finding.

**A wording failure** — the statement or one of its criteria names the implementation — means the
claim *was* established and is described badly. Deleting it discards checked evidence over a
phrase. The offending term is stripped, recorded as an implementation observation so it stays
traceable, and the requirement is kept.

That second case used to be handled for acceptance criteria and not for statements, so a capability
that was cited, traced, boundary-checked and correct was deleted for saying "environment variable".
On one measured run fourteen functional requirements entered the gate and one left, and the
thirteen losses were almost entirely this. A requirements document should lose a claim because it
could not be supported, never because of the words it used.
"""

from __future__ import annotations

from agentic.capabilities.assemble import ground_outcomes
from agentic.capabilities.consolidate import _action_of
from agentic.capabilities.leakage import strip_leakage
from agentic.capabilities.registry import CapabilityContext, CapabilityResult, capability
from agentic.schemas.brd import ProductBRD, title_key
from agentic.schemas.core import GateReport, Severity

# Rejections that are about how a claim is written rather than whether it holds. Everything else
# deletes.
WORDING_ONLY = {"TECHNICAL_VOCABULARY", "CRITERION_VOCABULARY"}

# Rejections about a citation. Fatal for a claim that authored its own citation, and *not* fatal
# for a business requirement, whose citation is derived from the capabilities standing under it
# rather than written by anyone. One bad capability under an outcome poisoned the outcome's
# derived evidence and deleted it, which then orphaned every other capability beneath it — an
# outcome losing its whole subtree because one of its children was wrong.
DERIVED_CITATION = {
    "INVALID_EVIDENCE_FILE",
    "INVALID_EVIDENCE_LINE",
    "EVIDENCE_SYMBOL_MISMATCH",
    "UNRESOLVED_GRAPH_PATH",
    "NO_INTERACTION_BOUNDARY",
}


def _inflections(term: str) -> list[str]:
    """The forms of a rejected term that could be standing in the statement.

    The linter matches inflections, so it rejects "index" while the statement says "indexed" — and
    removing the bare form then leaves the rejected word exactly where it was. English suffixes
    only: this repairs the removal, it does not know anything about any domain.
    """
    if " " in term or not term.isalpha():
        return [term]
    forms = {term, term + "s", term + "es", term + "ed", term + "d", term + "ing"}
    if term.endswith("e"):
        forms.update({term[:-1] + "ing", term[:-1] + "ed"})
    if term.endswith("y"):
        forms.add(term[:-1] + "ies")
    return sorted(forms, key=len, reverse=True)


def _quoted_terms(message: str) -> list[str]:
    """The offending terms a vocabulary rejection names, in the order it named them.

    The gate writes them as `kind: 'term'` pairs, so the quoted halves are the words to remove. No
    re-linting is needed here and none is wanted: the gate is the authority on what it rejected.
    """
    terms: list[str] = []
    body = message.split(" — in: ", 1)[0]
    for chunk in body.split("—", 1)[-1].split(";"):
        if "'" in chunk:
            terms.append(chunk.split("'")[1])
    return [term for term in terms if term]


@capability("quarantine_requirements")
def quarantine_requirements(ctx: CapabilityContext) -> CapabilityResult:
    brd = ctx.load("brd", ProductBRD)
    report = ctx.load("requirement_gate", GateReport)

    errors = [
        issue for issue in report.issues if issue.severity == Severity.ERROR and issue.subject_id
    ]
    # An outcome's citation is derived from its children, so a fault in it is a fault in a child.
    # Removing the child repairs the outcome; removing the outcome throws away a whole level of
    # the document over a mistake it did not make.
    outcomes = {item.id for item in brd.business_requirements if item.id}
    grounding = {
        issue.subject_id
        for issue in errors
        if issue.code not in WORDING_ONLY
        and not (issue.subject_id in outcomes and issue.code in DERIVED_CITATION)
    }
    rejected = {issue.subject_id: issue for issue in errors if issue.subject_id in grounding}

    stripped = 0
    reworded = 0
    for item in brd.all_items():
        if item.id in grounding:
            continue  # about to be removed; rewording it would be work for nothing
        offending = [
            issue for issue in errors
            if issue.subject_id == item.id and issue.code in WORDING_ONLY
        ]
        if not offending:
            continue

        # The statement: strip the named terms and keep the claim. What comes out is recorded on
        # the item, so the detail is still traceable and only stops being part of the requirement.
        statement_terms = [
            term
            for issue in offending
            if issue.code == "TECHNICAL_VOCABULARY"
            for term in _quoted_terms(issue.message)
        ]
        # Never the verb the statement is built around. A rejection naming it is a rejection of
        # the only word for what the person does, and removing it leaves "a user shall be able to
        # the next action" -- a correct requirement turned into a broken one by a linter.
        action = _action_of(item.statement)
        statement_terms = [
            term for term in statement_terms if term.casefold() != action
        ]
        if statement_terms:
            variants = [
                form for term in statement_terms for form in _inflections(term)
            ]
            rewritten = strip_leakage(item.statement, variants)
            if rewritten and rewritten != item.statement:
                item.implementation_observations.append(
                    "Held out of the requirement statement after validation: "
                    + ", ".join(sorted(set(statement_terms)))
                    + f". As written before rewording: {item.statement}"
                )
                item.statement = rewritten
                reworded += 1

        # A criterion is a whole sentence a reviewer ticks off, so a rejected one is removed rather
        # than reworded — the gate quotes it exactly, and the requirement keeps its others.
        quoted = {
            issue.message.split(" — in: ", 1)[-1].strip().strip("'\"")
            for issue in offending
            if issue.code == "CRITERION_VOCABULARY"
        }
        if quoted:
            kept = [c for c in item.acceptance_criteria if c.strip() not in quoted]
            stripped += len(item.acceptance_criteria) - len(kept)
            item.acceptance_criteria = kept

    def wording_only_result() -> CapabilityResult:
        ground_outcomes(brd)
        messages: list[str] = []
        if reworded:
            messages.append(
                f"Reworded {reworded} requirement statement(s) the gate rejected for naming the "
                "implementation, keeping every one of them. The rejection was about the words, "
                "not the evidence, and the terms removed are recorded on each item."
            )
        if stripped:
            messages.append(
                f"Removed {stripped} acceptance criterion/criteria whose wording the gate "
                "rejected, keeping the requirements they belonged to."
            )
        if not messages:
            messages.append("Nothing to quarantine: the gate raised no item-level errors.")
        return CapabilityResult(
            artifacts={"brd": brd},
            metrics={
                "quarantined": 0,
                "criteria_stripped": stripped,
                "statements_reworded": reworded,
            },
            messages=messages,
        )

    if not rejected:
        return wording_only_result()

    removed: list[str] = []
    for section, items in brd.every_section().items():
        kept = []
        for item in items:
            if item.id in rejected:
                removed.append(f"{item.id} ({section}) — {rejected[item.id].message}")
                continue
            kept.append(item)
        setattr(brd, section, kept)

    if brd.product_purpose and brd.product_purpose.id in rejected:
        removed.append(f"PURPOSE — {rejected['PURPOSE'].message}")
        brd.product_purpose = None
    if brd.business_problem and brd.business_problem.id in rejected:
        removed.append(f"PROBLEM — {rejected['PROBLEM'].message}")
        brd.business_problem = None

    # Removing a requirement orphans everything below it in the chain, which would otherwise
    # leave the document pointing up at a parent that no longer appears in it. Clearing the title
    # is what makes the orphan visible: `assign_ids` re-resolves every link from scratch, so a
    # dangling reference left in place would silently resolve to whatever now sits at that
    # position, and the gate would see a chain that is intact and wrong.
    for section, items in brd.sections().items():
        surviving = {title_key(parent.title) for parent in brd.parents_for(section)}
        for item in items:
            if item.parent and title_key(item.parent) not in surviving:
                item.parent = None

    # Removing a capability takes its citation off the outcome above it, which is derived from
    # the capabilities that survive rather than authored.
    ground_outcomes(brd)

    # An outcome whose every capability has just been removed is no longer standing over anything.
    # It has no evidence -- there is nothing left to derive one from -- and no capability the
    # reader could follow it down to, so publishing it states an objective the document cannot
    # show the product delivering. It goes with the removals that emptied it, and for the same
    # reason they did.
    emptied = [
        outcome
        for outcome in brd.business_requirements
        if not outcome.evidence
        and not any(
            item.parent and title_key(item.parent) == title_key(outcome.title)
            for item in brd.functional_requirements
        )
    ]
    if emptied:
        gone = {title_key(outcome.title) for outcome in emptied}
        brd.business_requirements = [
            outcome for outcome in brd.business_requirements
            if title_key(outcome.title) not in gone
        ]
        for outcome in emptied:
            removed.append(
                f"{outcome.id} (business_requirements) — every capability realising this outcome "
                "was removed, leaving nothing to evidence it"
            )
        for item in brd.all_items():
            if item.parent and title_key(item.parent) in gone:
                item.parent = None
                item.parent_relation = None
                item.parent_id = ""

    brd.assign_ids()

    messages = [
        f"Quarantined {len(removed)} ungrounded item(s) after the repair budget was exhausted.",
        *removed[:20],
    ]
    if reworded:
        messages.append(
            f"Kept and reworded {reworded} further requirement(s) rejected only for naming the "
            "implementation. Those claims were grounded; the words were the problem."
        )
    if stripped:
        messages.append(
            f"Also removed {stripped} acceptance criterion/criteria whose wording the gate "
            "rejected, keeping the requirements they belonged to."
        )
    if brd.is_empty():
        messages.append(
            "Nothing survived grounding. The deliverables will state this explicitly rather than "
            "presenting a fabricated document."
        )

    return CapabilityResult(
        artifacts={"brd": brd},
        metrics={
            "quarantined": len(removed),
            "criteria_stripped": stripped,
            "statements_reworded": reworded,
            "surviving_items": len(brd.all_items()),
        },
        messages=messages,
    )

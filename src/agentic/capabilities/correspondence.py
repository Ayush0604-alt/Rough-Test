"""Does the claim say what the code it cites actually does?

Nothing in this system asked that question before. Evidence was checked for *existence* -- the file
resolves, the line range is real, the symbol appears nearby -- and every other check compared a
statement to another statement or to a blacklist. A requirement could therefore cite a real
function, at a real line, and describe something that function does not do, and pass every gate.

The check runs in two stages, cheapest first.

**Deterministic.** Four structural checks, each keyed on something the graph already knows: how
much code a citation actually points at, whether the claim's words appear in the code's own
identifiers, whether a claim about an outside actor cites a boundary an outside actor can reach,
and whether a claim asserting a change cites anything that changes state. None of them knows a
language, a framework, or a subject area -- they read the shape of the evidence and the code's own
vocabulary, so they behave the same on a repository in a language nothing here can parse.

**Entailment.** What survives goes to the model in one batched call, which is asked only whether
the cited facts support the sentence. This is the expensive half and it is skippable.

Nothing is ever dropped. A failing claim is marked `rejected`, told why, and published anyway. A
reverse-engineering document that quietly deletes what it doubts is worse than one that says which
claims it is unsure of: the reader can check a doubtful claim, but cannot recover a deleted one.
"""

from __future__ import annotations

import re
from pathlib import Path
from collections import Counter
from typing import Any, Callable, Iterable

from pydantic import BaseModel, Field

from agentic.capabilities.graph_ops import BOUNDARY_KINDS, GraphIndex
from agentic.capabilities.registry import CapabilityContext, CapabilityResult, capability
from agentic.schemas.brd import BRDItem, CorrespondenceOutcome, ProductBRD, title_key
from agentic.capabilities.verify import SYMBOL_WINDOW
from agentic.schemas.core import KnowledgeGraph

# A span so large it cannot be evidence for one sentence. A citation covering a whole module names
# everything and therefore says nothing; the claim may still be true, but this evidence does not
# establish it. Generous on purpose -- the point is to catch a citation that gestures at a file.
MAX_EVIDENCE_LINES = 400

# Words carried by almost every requirement sentence, so finding them in the code proves nothing.
# Domain-neutral: nothing here names a technology, a framework, or a subject.
FRAME_WORDS = frozenset(
    """a an and are as at be been by can for from in into is it its of on or shall should so that
    the their them they this to when which who will with able system user users product each any
    all not no only other another such via using use used must may given then there here where
    what while after before during within without out up down over under again further once
    provide provides provided support supports supported allow allows allowed ensure ensures
    ensured able ability new existing valid invalid correct incorrect appropriate relevant""".split()
)

# Verb forms that assert the world changed, as opposed to being reported. Used for one narrow,
# high-precision check and nothing else.
#
# This is English, not a stack: a repository written in any language, documented in English, is
# read the same way. A repository documented in another language yields no match and the check
# abstains rather than rejecting -- an abstention is correct, since it has learned nothing.
CHANGE_VERBS = re.compile(
    r"\b(creates?|created|stores?|stored|saves?|saved|writes?|written|updates?|updated|"
    r"modifies|modified|deletes?|deleted|removes?|removed|records?|recorded|persists?|"
    r"persisted|inserts?|inserted|registers?|registered|submits?|submitted|adds?|added|"
    r"cancels?|cancelled|canceled|archives?|archived|issues?|issued)\b",
    re.IGNORECASE,
)

_WORD = re.compile(r"[A-Za-z][A-Za-z0-9]+")


def _content_words(text: str) -> set[str]:
    words = {word.casefold() for word in _WORD.findall(text or "")}
    return {word for word in words if len(word) > 2 and word not in FRAME_WORDS}


def _identifier_words(name: str) -> set[str]:
    """The words inside an identifier, however the codebase spells its names.

    `createInvoice`, `create_invoice`, `CreateInvoice` and `CREATE_INVOICE` all yield the same two
    words. Splitting on case and separators rather than on a known naming convention is what keeps
    this working across languages that disagree about how identifiers look.
    """
    parts = re.split(r"[^A-Za-z0-9]+", name or "")
    words: set[str] = set()
    for part in parts:
        for piece in re.findall(r"[A-Z]+(?![a-z])|[A-Z][a-z0-9]*|[a-z0-9]+", part):
            if len(piece) > 2:
                words.add(piece.casefold())
    return words


class EvidenceView:
    """What the graph knows about the code one claim cites."""

    def __init__(self, graph: KnowledgeGraph, source_root: Any = None) -> None:
        self._root = Path(source_root) if source_root else None
        self._by_file: dict[str, list[Any]] = {}
        for entity in graph.entities:
            if entity.location is not None:
                self._by_file.setdefault(entity.location.file, []).append(entity)
        index = GraphIndex(graph)
        self.has_boundaries = bool(index.boundary_ids())
        # Everything a request or a click can cause to run. Reachability, not identity, is the
        # question: a command handler is where the capability lives even though only the parser
        # above it is marked as the entry point.
        self.externally_reachable = index.boundary_downstream() | index.boundary_ids()

    def entities_for(self, item: BRDItem, window: int = 0) -> list[Any]:
        """Entities overlapping the cited lines, optionally widened by a tolerance.

        The window exists because a citation names where a behaviour is written and the thing that
        declares it sits just outside: a route decorator one line above the function it exposes is
        the commonest case. A caller asking "what mechanism does this cite" needs to see it; a
        caller asking "what does this rest on" does not, so the widening is opt-in.
        """
        found: list[Any] = []
        seen: set[str] = set()
        for citation in item.evidence or []:
            start = (citation.line_start or 0) - window
            end = (citation.line_end or citation.line_start or 0) + window
            for entity in self._by_file.get(citation.file, []):
                location = entity.location
                entity_start = location.line_start or 0
                entity_end = location.line_end or entity_start
                if start > entity_end or entity_start > end:
                    continue
                if entity.id not in seen:
                    seen.add(entity.id)
                    found.append(entity)
        return found

    def cited_source_words(self, item: BRDItem) -> set[str]:
        """The identifiers written in the lines a claim actually cites.

        The entity's own name is not the whole vocabulary of the code it names. A handler called
        `create_invoice` mentions `reference`, `amount` and `save_invoice` in its body, and a
        requirement is often anchored by one of those rather than by the function's name.

        Reading the span matters more than it sounds. The synthesis skill asks for requirements in
        the product's business words -- "a supplier bill", not "an invoice" -- so a claim written
        exactly as instructed can share nothing with the identifier it hangs from while sharing a
        great deal with the code inside it.
        """
        if self._root is None:
            return set()
        words: set[str] = set()
        for citation in item.evidence or []:
            path = self._root / citation.file
            try:
                lines = path.read_text(encoding="utf-8-sig", errors="ignore").splitlines()
            except OSError:
                continue
            # The same neighbourhood the evidence verifier already accepts. A citation routinely
            # names the line a construct is declared on -- a decorator, a signature -- while the
            # behaviour it evidences sits just below it, and reading only the named line judged a
            # true requirement unanchored because its own code was three lines out of view.
            start = max(0, (citation.line_start or 1) - 1 - SYMBOL_WINDOW)
            end = min(len(lines), (citation.line_end or citation.line_start or 1) + SYMBOL_WINDOW)
            for line in lines[start:end]:
                for token in re.findall(r"[A-Za-z_][A-Za-z0-9_]*", line):
                    words |= _identifier_words(token)
        return words

    def vocabulary(self, entities: Iterable[Any]) -> set[str]:
        """Every word the cited code uses to name itself, plus any prose it carries."""
        words: set[str] = set()
        for entity in entities:
            words |= _identifier_words(entity.name)
            words |= _identifier_words(entity.qualified_name)
            attributes = entity.attributes or {}
            # A docstring is the single richest source of the code's own vocabulary, and leaving
            # it out made the anchoring check reject claims that quoted the function's own words.
            for key in (
                "docstring", "rationale", "statement", "description", "value", "expression",
                "predicate", "path", "handler",
            ):
                value = attributes.get(key)
                if isinstance(value, str):
                    words |= _content_words(value)
            # A documentation file's prose reaches the graph as a `content_outline` — a list of
            # lines, not a string — and it is the only vocabulary such a file has: a README has no
            # identifiers to draw on. Skipping it judged a requirement quoting the project's own
            # description of itself as sharing no word with what it cites.
            outline = attributes.get("content_outline")
            if isinstance(outline, list):
                for line in outline:
                    if isinstance(line, str):
                        words |= _content_words(line)
        return words


# --------------------------------------------------------------------------------------------
# The deterministic checks
# --------------------------------------------------------------------------------------------

# A check returns None when the claim passes, or one sentence saying what disagreed. Registering
# a check is all it takes to add one; nothing dispatches on a check's name.
Check = Callable[[BRDItem, list, EvidenceView], "str | None"]
CHECKS: dict[str, Check] = {}

# Which failures are facts about the evidence and which are readings of it.
#
# An objective check answers a question with one right answer: is there a citation, does anything
# recognised sit there, is the span small enough to be evidence for a sentence. An interpretive one
# weighs a sentence against code and can be wrong -- and the anchoring check is wrong in a
# predictable direction, because the synthesis skill asks for requirements in the product's own
# business words and then this compares them against the code's identifiers. A claim written
# exactly as instructed can share no vocabulary with the thing it correctly describes.
#
# The distinction exists because the consequence is no longer cosmetic. A rejected claim now leaves
# the document, so a check that can be wrong must not be able to delete a requirement on its own:
# it makes the claim an open question, which is what "the evidence is insufficient" means.
OBJECTIVE_CHECKS = frozenset({"cites_something", "resolves_to_known_code", "span_adequate"})


def check(name: str) -> Callable[[Check], Check]:
    def register(function: Check) -> Check:
        if name in CHECKS:
            raise ValueError(f"Correspondence check {name!r} is already registered")
        CHECKS[name] = function
        return function

    return register


@check("cites_something")
def _cites_something(item: BRDItem, entities: list, view: EvidenceView) -> str | None:
    """A claim with no citation cannot correspond to anything."""
    if not item.evidence:
        return "The claim cites no code at all."
    return None


@check("span_adequate")
def _span_adequate(item: BRDItem, entities: list, view: EvidenceView) -> str | None:
    """A citation covering a whole module names everything and establishes nothing."""
    for citation in item.evidence or []:
        start = citation.line_start or 0
        end = citation.line_end or start
        if end - start > MAX_EVIDENCE_LINES:
            return (
                f"The citation covers {end - start} lines of {citation.file}, which is too much "
                "code to be evidence for one statement."
            )
    return None


# Kinds that overlap a line without anything being *understood* as code there.
#
# FILE: every inventoried file gets one spanning the whole file, so a bare overlap says only that
# the file exists — which would make "nothing sits at the cited lines" unreachable for any line of
# any known file.
#
# RATIONALE: the extractor emits a node for a docstring or comment block, covering lines that hold
# prose rather than a definition. This check is named for *code*, and a claim anchored only to a
# comment has not been shown to correspond to an implementation. The prose is not discarded — it
# still reaches `vocabulary()`, where it is exactly what the anchoring check wants.
_NOT_CODE_KINDS = frozenset({"FILE", "RATIONALE"})


def _understood(entity: Any) -> bool:
    """Whether this entity represents code *understood* at its lines, not merely present."""
    if entity.kind not in _NOT_CODE_KINDS:
        return True
    # A file whose own prose was read is the evidence: a README has no identifiers to offer.
    return entity.kind == "FILE" and bool((entity.attributes or {}).get("content_outline"))


@check("resolves_to_known_code")
def _resolves_to_known_code(item: BRDItem, entities: list, view: EvidenceView) -> str | None:
    """The citation points somewhere the extractors found nothing.

    Distinct from the existing evidence check, which asks whether the file and line exist. This
    asks whether anything was *understood* there.
    """
    if item.evidence and not any(_understood(entity) for entity in entities):
        return "Nothing the extractors recognised sits at the cited lines."
    return None


@check("lexically_anchored")
def _lexically_anchored(item: BRDItem, entities: list, view: EvidenceView) -> str | None:
    """The claim's words should appear somewhere in what the cited code calls itself.

    The strongest of the structural checks and the most portable, because the vocabulary compared
    against is the codebase's own rather than any list held here. A statement sharing no
    substantive word with the identifiers, constants, or prose at the lines it cites is describing
    something else.

    An abstention is built in: a claim written entirely in words the code has no reason to contain
    is judged by entailment instead, not rejected here.
    """
    if not entities:
        return None
    claim = _content_words(item.statement) | _content_words(item.title)
    if not claim:
        return None
    vocabulary = view.vocabulary(entities) | view.cited_source_words(item)
    if not vocabulary:
        return None
    if claim & vocabulary:
        return None
    return (
        "No substantive word in the statement appears anywhere in the names or text of the code "
        "it cites."
    )


@check("actor_reachable")
def _actor_reachable(item: BRDItem, entities: list, view: EvidenceView) -> str | None:
    """A claim about what an outside actor can do must cite something reachable from outside.

    Only functional requirements make that claim -- the tier is defined by it -- so only they are
    asked. A capability grounded solely in unreachable code is a real finding filed one level too
    high, which is exactly the over-extraction this document set is prone to.

    Reachability, not identity. Requiring the cited entity to *be* a boundary rejected three of
    seven true claims on the command-line fixture, where the capability lives in the subcommand
    handlers and only the parser above them is marked as the entry point. What matters is whether
    an outside actor can cause the cited code to run.
    """
    if item.requirement_type != "functional" or not entities:
        return None
    # A repository with no detectable entry point cannot answer the question, so the check
    # abstains rather than rejecting every functional claim in it -- which is what would happen
    # on any repository in a language nothing here parses.
    if not view.has_boundaries:
        return None
    if any(entity.id in view.externally_reachable for entity in entities):
        return None
    return (
        "The claim says an outside actor can do this, but nothing it cites can be reached from "
        "any entry point."
    )


@check("effect_agreement")
def _effect_agreement(item: BRDItem, entities: list, view: EvidenceView) -> str | None:
    """A claim that something is changed must cite something that changes it.

    Deliberately one-directional and narrow. It fires only when the statement plainly asserts a
    change *and* every boundary cited is declared read-only, which is the one case where the
    evidence positively contradicts the claim. Where no boundary declares an effect at all the
    check abstains, because absence of a declaration is not a declaration of absence.
    """
    if not CHANGE_VERBS.search(item.statement or ""):
        return None
    declared = [
        str((entity.attributes or {}).get("effect", "")).lower()
        for entity in entities
        if entity.kind in BOUNDARY_KINDS and (entity.attributes or {}).get("effect")
    ]
    if not declared or any(effect != "read" for effect in declared):
        return None
    return (
        "The claim asserts something is changed or stored, but every entry point it cites is "
        "declared read-only."
    )


# --------------------------------------------------------------------------------------------
# Entailment
# --------------------------------------------------------------------------------------------


class EntailmentVerdict(BaseModel):
    id: str = Field(description="The requirement id exactly as given.")
    supported: bool = Field(
        description="True when the cited facts are enough to support the statement as written."
    )
    reason: str = Field(
        default="",
        description="One sentence, only when not supported, naming what the evidence does not show.",
    )


class EntailmentVerdicts(BaseModel):
    verdicts: list[EntailmentVerdict] = Field(default_factory=list)


ENTAILMENT_INSTRUCTION = (
    "You are checking whether requirement statements match the code they cite. For each item you "
    "are given the statement and a list of the code facts found at the lines it cites: what each "
    "one is, what it is called, and any text it carries.\n\n"
    "Answer one question per item: do these facts support the statement as written?\n\n"
    "Mark an item unsupported only when the facts describe something materially different from "
    "what the statement claims, or when they are silent on the specific thing it asserts. Do not "
    "mark an item unsupported for being broadly worded, for using business vocabulary rather than "
    "the code's, or for omitting detail the facts contain -- a requirement is meant to read as a "
    "requirement, not as a description of the code. When in doubt, mark it supported: a wrongly "
    "rejected claim costs more than a wrongly kept one, because a reviewer can catch the second.\n\n"
    "Return one verdict per item, using the id exactly as given."
)


def _fact_sheet(item: BRDItem, entities: list) -> dict[str, Any]:
    facts = []
    for entity in entities[:14]:
        attributes = entity.attributes or {}
        detail = next(
            (
                str(attributes[key])[:200]
                for key in ("rationale", "statement", "expression", "value", "predicate")
                if attributes.get(key)
            ),
            "",
        )
        facts.append(
            {
                "kind": entity.kind,
                "name": entity.qualified_name or entity.name,
                "detail": detail,
            }
        )
    return {"statement": item.statement, "facts": facts}


def _entail(
    ctx: CapabilityContext, pending: list[tuple[BRDItem, list]]
) -> dict[int, tuple[bool, str]]:
    """One batched call. Returns position -> (supported, reason); empty means it could not run.

    Keyed on the item's position in this batch, not on its identifier. Identifiers are stamped
    after consolidation -- deliberately, because a candidate that is later merged or dropped must
    not have one -- so at this point in the pipeline every item's id is the empty string. Sending
    them meant every verdict came back under the same key, and one claim the model disagreed with
    rejected the entire batch: on one run 31 claims were checked and 0 were reported supported,
    each carrying the same rejection reason, which belonged to a different requirement.

    The check was correct and unusable, and nothing caught it because the one place it had ever
    run was a harness that assigns identifiers up front.
    """
    # Routed through `llm.steps` rather than `llm.client`, because that is the seam every test
    # substitutes a stub at. Importing the client directly bypassed it: with credentials present
    # in the environment, the suite was quietly making live calls on this path instead of using
    # the stub the test had installed.
    from agentic.llm import steps as llm_steps

    payload = [
        {"id": str(position), **_fact_sheet(item, entities)}
        for position, (item, entities) in enumerate(pending)
    ]
    model = llm_steps.build_chat_model(ctx.settings).with_structured_output(
        EntailmentVerdicts
    )
    import json

    reply = model.invoke(
        [
            ("system", ENTAILMENT_INSTRUCTION),
            ("human", json.dumps({"items": payload}, ensure_ascii=False)),
        ]
    )
    verdicts: dict[int, tuple[bool, str]] = {}
    for verdict in getattr(reply, "verdicts", []) or []:
        try:
            position = int(str(verdict.id).strip())
        except (TypeError, ValueError):
            continue  # a reply that did not echo the handle says nothing about any claim
        if 0 <= position < len(pending):
            verdicts[position] = (verdict.supported, verdict.reason)
    return verdicts


# --------------------------------------------------------------------------------------------
# The capability
# --------------------------------------------------------------------------------------------


# How many capabilities that passed the check an outcome needs beneath it to count as supported.
# Two, the same floor resolution applies: one capability under an outcome is that capability
# restated, not something the parts add up to.
OUTCOME_MIN_SUPPORTED = 2


def _judge_outcomes_by_capabilities(brd: ProductBRD) -> int:
    """Mark each outcome supported when enough of its capabilities passed. Returns how many did.

    An outcome with too few is left unchecked rather than rejected: resolution already turns an
    outcome with fewer than two capabilities under it into a capability, which is the honest
    answer, and a rejection would also strip the parent from every capability beneath it.
    """
    supported_children: Counter[str] = Counter()
    for item in brd.functional_requirements:
        if item.parent and getattr(item.validation, "verdict", "") == "supported":
            supported_children[title_key(item.parent)] += 1
    supported = 0
    for outcome in brd.business_requirements:
        count = supported_children[title_key(outcome.title)]
        if count >= OUTCOME_MIN_SUPPORTED:
            supported += 1
            outcome.validation = CorrespondenceOutcome(
                verdict="supported",
                reason=f"{count} capabilities beneath it passed the correspondence check.",
                checked_by="capabilities",
            )
        else:
            outcome.validation = CorrespondenceOutcome(
                verdict="unchecked",
                reason=f"Only {count} capability/capabilities beneath it passed the check.",
                checked_by="capabilities",
            )
    return supported


@capability("validate_correspondence")
def validate_correspondence(ctx: CapabilityContext) -> CapabilityResult:
    """Check every published claim against the code it cites, and record what was found."""
    brd = ctx.load("brd", ProductBRD)

    if not getattr(ctx.settings, "correspondence_validation", True):
        return CapabilityResult(
            artifacts={"brd": brd},
            metrics={"correspondence_checked": 0, "correspondence_disabled": True},
            messages=["Correspondence validation is switched off for this run."],
        )

    graph = ctx.load("graph", KnowledgeGraph)
    view = EvidenceView(graph, getattr(ctx, "source_root", None))

    checked = 0
    rejected = 0
    failures: Counter[str] = Counter()
    pending: list[tuple[BRDItem, list]] = []

    for section, items in brd.every_section().items():
        # Observations record what the code does, so "does the code do this" is not a question
        # that can distinguish a good one from a bad one.
        if section == "observations":
            continue
        # An outcome has no code of its own: its citations are a sample borrowed from the
        # capabilities beneath it. Reading "streamlined interview scheduling" against six sampled
        # lines, the entailment check rejected four of six outcomes on one run, and every
        # capability under them lost its parent -- 5 of 95 stayed attached. An outcome is judged
        # by what stands under it, after those have been judged, below.
        if section == "business_requirements":
            continue
        for item in items:
            checked += 1
            entities = view.entities_for(item)
            failed: list[str] = []
            reasons: list[str] = []
            for name, run in CHECKS.items():
                reason = run(item, entities, view)
                if reason is None:
                    continue
                failed.append(name)
                reasons.append(reason)
                failures[name] += 1

            if failed:
                rejected += 1
                item.status = "rejected"
                item.validation = CorrespondenceOutcome(
                    verdict="rejected",
                    checks_failed=failed,
                    reason=" ".join(reasons)[:600],
                    checked_by="deterministic",
                    objective=any(name in OBJECTIVE_CHECKS for name in failed),
                )
                continue
            item.validation = CorrespondenceOutcome(
                verdict="supported", checked_by="deterministic"
            )
            pending.append((item, entities))

    entailed = 0
    entailment_ran = False
    if pending and getattr(ctx.settings, "correspondence_entailment", True):
        try:
            verdicts = _entail(ctx, pending)
            entailment_ran = True
        except Exception as exc:  # a validator must never be able to fail a run
            verdicts = {}
            ctx_message = f"{type(exc).__name__}: {exc}"
        else:
            ctx_message = ""
        for position, (item, _) in enumerate(pending):
            supported, reason = verdicts.get(position, (True, ""))
            if supported:
                item.validation = CorrespondenceOutcome(
                    verdict="supported", checked_by="entailment" if entailment_ran else "deterministic"
                )
                continue
            entailed += 1
            rejected += 1
            item.status = "rejected"
            item.validation = CorrespondenceOutcome(
                verdict="rejected",
                reason=(reason or "The cited code does not show what the statement claims.")[:600],
                checked_by="entailment",
                # A model read the facts and disagreed. That is a judgement about the claim itself
                # rather than a proxy for one, so it carries the same weight as a structural fact.
                objective=True,
            )
    else:
        ctx_message = ""

    outcomes_supported = _judge_outcomes_by_capabilities(brd)

    messages: list[str] = [
        f"Checked {checked} claim(s) against the code they cite. "
        f"{checked - rejected} are supported by it."
    ]
    if failures:
        messages.append(
            "Rejected by structural check: "
            + ", ".join(f"{name} ({count})" for name, count in failures.most_common())
            + ". Every one is published with its reason attached rather than removed."
        )
    if entailed:
        messages.append(
            f"{entailed} further claim(s) passed every structural check but were not supported by "
            "the cited facts on reading."
        )
    if ctx_message:
        messages.append(
            "The entailment pass could not run, so claims were judged on the structural checks "
            f"alone: {ctx_message}"
        )
    if brd.business_requirements:
        messages.append(
            f"{outcomes_supported} of {len(brd.business_requirements)} business outcome(s) are "
            f"supported by at least {OUTCOME_MIN_SUPPORTED} capabilities that passed the check; "
            "outcomes are judged by the capabilities beneath them, never against borrowed code."
        )

    return CapabilityResult(
        artifacts={"brd": brd},
        metrics={
            "correspondence_checked": checked,
            "correspondence_rejected": rejected,
            "correspondence_rejected_deterministically": rejected - entailed,
            "correspondence_rejected_by_entailment": entailed,
            "correspondence_entailment_ran": entailment_ran,
            "outcomes_supported_by_capabilities": outcomes_supported,
            **{f"correspondence_failed_{name}": count for name, count in failures.items()},
        },
        messages=messages,
    )

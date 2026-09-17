"""Keeping the implementation out of the requirement statement.

A requirement says what must be true of the product. A statement that names the function that
happens to do it today, or the route it is currently reached through, has bound the requirement to
one implementation: rewrite the code and the requirement reads as false even though nothing about
the product changed. The audited output was full of this -- HTTP verbs and paths written into
functional requirements, internal helper names written into system requirements.

The detection is deliberately not a list of forbidden words. It cannot be: a word that is an
implementation detail in one repository is the product's own vocabulary in another, and a list
would encode one codebase's habits as a general rule. Instead the vocabulary comes from the
repository itself. A token in a statement is implementation leakage when the graph says it is an
identifier, a route, or a declared mechanism at the very lines the statement cites -- the codebase
decides what counts as its own machinery, and this module only asks.

Nothing is deleted. What is stripped from a statement is recorded on the item as an implementation
observation, so the detail stays traceable and only stops being part of the requirement's text.

The second responsibility here is the redesign test: deciding whether an internal behaviour is a
requirement at all, or a description of how this code currently works. That question is asked of
the evidence, not of the wording.
"""

from __future__ import annotations

import re
from collections import Counter
from typing import Any, Iterable

from agentic.capabilities.correspondence import EvidenceView
from agentic.capabilities.verify import SYMBOL_WINDOW
from agentic.capabilities.graph_ops import BOUNDARY_KINDS, DECLARATION_KINDS
from agentic.capabilities.registry import CapabilityContext, CapabilityResult, capability
from agentic.schemas.brd import BRDItem, ProductBRD
from agentic.schemas.core import KnowledgeGraph

_WORD = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")

# Shapes that are code however they are spelled. These are properties of identifiers and locators
# in general, not of any one language: a dotted path, a snake or camel identifier, a slash route, a
# name with a file extension. Nothing here names a framework or a function.
_CODEY = (
    re.compile(r"^[a-z][a-z0-9]*(?:_[a-z0-9]+)+$"),        # snake_case
    re.compile(r"^[a-z]+(?:[A-Z][a-z0-9]*)+$"),            # camelCase
    re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)+$"),  # dotted.path
    re.compile(r"^[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+$"),        # CONSTANT_CASE
)

# A route-shaped or file-shaped token, matched on the whole statement rather than word by word.
_LOCATOR = re.compile(
    r"(?:(?<=\s)|^)(?:/[A-Za-z0-9_{}:.\-]+)+/?|[A-Za-z0-9_\-]+\.[A-Za-z]{1,5}(?=\s|$|[.,;)])"
)

# Attributes on a cited entity whose *values* are mechanism: the route a handler is reached
# through, the verb used, the symbol it is called by. An adapter that records one of these is
# telling us the value is an implementation locator, whatever the stack.
MECHANISM_ATTRIBUTES = (
    "path", "method", "handler", "symbol", "topic", "queue", "table", "registrars",
)


def _looks_like_code(token: str) -> bool:
    return any(pattern.match(token) for pattern in _CODEY)


def repository_vocabulary(entities: Iterable[Any]) -> set[str]:
    """The names the cited code calls its own machinery.

    Drawn entirely from the graph. A word is only ever treated as an implementation detail because
    this repository uses it as one, which is what keeps the rule from encoding any particular
    codebase's habits.
    """
    words: set[str] = set()
    for entity in entities:
        for raw in (entity.name, entity.qualified_name):
            if not raw:
                continue
            words.add(raw.casefold())
            for part in re.split(r"[^A-Za-z0-9_]+", raw):
                if len(part) > 2 and _looks_like_code(part):
                    words.add(part.casefold())
        attributes = entity.attributes or {}
        for key in MECHANISM_ATTRIBUTES:
            value = attributes.get(key)
            if isinstance(value, str) and value.strip():
                words.add(value.strip().casefold())
    return words


def find_leakage(item: BRDItem, entities: list[Any]) -> list[str]:
    """Tokens in the statement that the cited code says are its own machinery."""
    statement = item.statement or ""
    vocabulary = repository_vocabulary(entities)
    found: list[str] = []

    for locator in _LOCATOR.findall(statement):
        token = locator.strip()
        # A locator counts only if the repository actually uses it. Otherwise an ordinary phrase
        # containing a slash or a full stop would be stripped out of perfectly good prose.
        if token and (token.casefold() in vocabulary or token.startswith("/")):
            if token not in found:
                found.append(token)

    for word in _WORD.findall(statement):
        if len(word) <= 2:
            continue
        if _looks_like_code(word) and word.casefold() in vocabulary:
            if word not in found:
                found.append(word)

    # A bare mechanism value the repository declared, such as a verb, even where it is an ordinary
    # word on its own. Matched whole so it cannot fire inside another word.
    for entity in entities:
        attributes = entity.attributes or {}
        for key in ("method", "path", "topic", "queue", "registrars"):
            value = attributes.get(key)
            # `registrars` is a list: the undefined names a handler references, which is where the
            # verb now lives. Extraction no longer records a route's method and path as fields of
            # their own, so without this the repository's own mechanism words stop being
            # recognised as mechanism words and are published inside requirement statements.
            values = value if isinstance(value, list) else [value]
            for item in values:
                if not isinstance(item, str) or not item.strip():
                    continue
                token = item.strip()
                # Matched case-insensitively — a verb is lowercase in code and uppercase in prose
                # — but recorded as the statement spells it, because that is the text the strip
                # has to remove. Recording the attribute's own casing finds nothing to cut.
                match = re.search(
                    rf"(?<![A-Za-z0-9]){re.escape(token)}(?![A-Za-z0-9])", statement, re.IGNORECASE
                )
                if match and match.group(0) not in found:
                    found.append(match.group(0))
    return found


# Two shapes that mean a removal has broken the sentence rather than tidied it. Both are
# properties of English rather than of any domain: an empty pair of quotes says a word was cut out
# of a quotation, and an article or a subordinator standing directly in front of a finite verb --
# "an occurs", "if is present" -- is not a phrase any writer produced.
_EMPTY_QUOTES = re.compile(r"[\u2018\u2019'\"`]\s*[\u2018\u2019'\"`]")
_STRANDED_ARTICLE = re.compile(
    # "that", "and" and "or" are deliberately absent: "a value that is present" and "the record
    # is created and is retrievable" are ordinary English, and flagging them would refuse a
    # rewrite that was perfectly good.
    r"\b(?:a|an|the|if|whether|when|while|unless|because)\s+"
    r"(?:is|are|was|were|be|been|has|have|had|does|do|did|will|shall|would|should|can|could|may"
    r"|might|must|occurs?|occurred|happens?|fails?|failed|starts?|ends?)\b",
    re.IGNORECASE,
)


def _mangled(statement: str) -> bool:
    """Whether what is left of a sentence is still a sentence."""
    return bool(_EMPTY_QUOTES.search(statement) or _STRANDED_ARTICLE.search(statement))


def strip_leakage(statement: str, tokens: list[str]) -> str:
    """Remove the mechanism from a sentence and leave a sentence behind.

    Conservative on purpose. Where removing a token would leave the statement shorter than a
    sentence, the statement is kept as it stands and the leakage is only recorded -- a requirement
    that has been mangled into unreadability is worse than one that names a route.
    """
    rewritten = statement
    for token in sorted(tokens, key=len, reverse=True):
        # Quoted first, quotes and all. A token the author wrapped in quotes -- an identifier, a
        # declared value -- leaves an empty pair behind when only the word inside is removed, and
        # "check if ' ' is present" is worse than the sentence that named the field.
        rewritten = re.sub(
            rf"[\u2018\u2019'\"`]{re.escape(token)}[\u2018\u2019'\"`]",
            " ",
            rewritten,
        )
        # Take any bracketing or connective punctuation with it, so "the /orders endpoint" does
        # not become "the  endpoint" and "(GET /orders)" does not leave empty brackets.
        rewritten = re.sub(
            rf"\s*\(\s*{re.escape(token)}\s*\)|(?<![A-Za-z0-9]){re.escape(token)}(?![A-Za-z0-9])",
            " ",
            rewritten,
        )
    rewritten = " ".join(rewritten.split())

    # Removing a token strands the word that introduced it: "created by POST /orders so that"
    # becomes "created by so that", and "persist through save_order and" becomes "persist through
    # and". A stranded introducer is an artefact of the removal rather than part of the
    # requirement, so it goes with the token. English function words only -- this repairs the
    # sentence's grammar, it does not judge its content.
    rewritten = re.sub(
        r"\b(?:by|via|through|using|at|on|to|from|with|in|over|against)\s+"
        r"(?=(?:so\s+that\b|in\s+order\s+to\b|and\b|which\b|then\b)|[.,;:]|$)",
        "",
        rewritten,
        flags=re.IGNORECASE,
    )
    # A determiner left with nothing to determine, for the same reason.
    rewritten = re.sub(
        r"\b(?:the|a|an)\s+(?=(?:so\s+that\b|and\b|which\b)|[.,;:]|$)",
        "",
        rewritten,
        flags=re.IGNORECASE,
    )
    rewritten = re.sub(r"\s+(?=[.,;:])", "", rewritten)
    rewritten = " ".join(rewritten.split())
    rewritten = re.sub(r"\s*\(\s*\)", "", rewritten)

    if len(rewritten.split()) < 6:
        return statement
    if _mangled(rewritten):
        # The removal took a word the sentence was built around and left ungrammatical English --
        # "when an occurs during indexing", "check if ' ' is present". A requirement nobody can
        # read is worse than one that names a mechanism: keep the original wording, and let the
        # caller record the leak against it instead.
        return statement
    if not rewritten.endswith("."):
        rewritten = rewritten.rstrip(" ,;:") + "."
    return rewritten


# ---------------------------------------------------------------------------------------------
# The redesign test
# ---------------------------------------------------------------------------------------------


def survives_redesign(item: BRDItem, entities: list[Any]) -> bool:
    """Whether an internal behaviour would still be required if the code were rewritten.

    Asked of the evidence rather than the wording. A behaviour is a genuine system responsibility
    when something outside the current implementation depends on it: an entry point exposes it, a
    declaration states it, or a rule constrains it. A behaviour whose only support is that a
    function exists and does this is a description of the present code, and belongs in the
    traceability record rather than in the requirements a stakeholder signs off.

    Deliberately *not* "reachable from a boundary". That is the grounding test the gate applies,
    and it is a different question: every helper an entry point eventually calls is reachable from
    it, so answering this one that way would promote assembling a dictionary to a system
    responsibility on the grounds that a request eventually causes it. Reachability says the claim
    is about live code. This asks whether anything but the current code depends on it.
    """
    if not entities:
        return False
    for entity in entities:
        if entity.kind in BOUNDARY_KINDS or entity.kind in DECLARATION_KINDS:
            return True
        if (entity.attributes or {}).get("rule_source"):
            return True
    return False


@capability("review_implementation_leakage")
def review_implementation_leakage(ctx: CapabilityContext) -> CapabilityResult:
    """Rewrite statements that name mechanism, and demote behaviours that describe only the code."""
    brd = ctx.load("brd", ProductBRD)

    if not getattr(ctx.settings, "leakage_review", True):
        return CapabilityResult(
            artifacts={"brd": brd},
            metrics={"statements_rewritten": 0, "leakage_review_disabled": True},
            messages=["Implementation-leakage review is switched off for this run."],
        )

    view = EvidenceView(ctx.load("graph", KnowledgeGraph))

    rewritten = 0
    recorded = 0
    demoted = 0
    tokens_seen: Counter[str] = Counter()

    for section, items in brd.every_section().items():
        if section == "observations":
            continue
        for item in items:
            entities = view.entities_for(item)
            # Mechanism is looked for in the neighbourhood, not only the exact span. A route is
            # declared on the line above the function that serves it, so a requirement citing the
            # function does not "cite" the route -- and the verb and path it names in its own
            # sentence would go undetected.
            nearby = view.entities_for(item, window=SYMBOL_WINDOW)

            tokens = find_leakage(item, nearby)
            if tokens:
                recorded += 1
                for token in tokens:
                    tokens_seen[token] += 1
                cleaned = strip_leakage(item.statement, tokens)
                note = (
                    "Implementation detail held out of the requirement statement and kept here "
                    "for traceability: " + ", ".join(sorted(set(tokens))[:8]) + "."
                )
                if note not in item.implementation_observations:
                    item.implementation_observations.append(note)
                if cleaned != item.statement:
                    item.statement = cleaned
                    rewritten += 1

            # The redesign test. Only internal behaviour is asked: a capability is defined by
            # being externally observable, and a declared rule is a decision rather than a
            # mechanism, so neither is a candidate for demotion here.
            # Only a behaviour that arrived describing itself as internal is asked. One that was
            # *reclassified* into internal behaviour earlier in this same run has already had one
            # judgement made about it, with its reasons recorded; demoting it again on a second,
            # unrelated test stacks two calls and lands a refusal the product enforces in a table
            # of notes about the code. One demotion per item per run.
            already_demoted = any(
                "rather than a business rule" in note for note in (item.assumptions or [])
            )
            if (
                item.abstraction_basis == "internal_behaviour"
                and not already_demoted
                and not survives_redesign(item, entities)
            ):
                item.abstraction_basis = "implementation_detail"
                item.requirement_type = "observation"
                item.implementation_observations.append(
                    "Reclassified from a system requirement: nothing outside the current "
                    "implementation depends on this behaviour -- no entry point exposes it, no "
                    "declaration states it, and no rule rests on it -- so it describes how this "
                    "code works rather than what the product must do."
                )
                demoted += 1

    messages: list[str] = []
    if recorded:
        messages.append(
            f"Held implementation detail out of {recorded} requirement statement(s), rewriting "
            f"{rewritten} of them. The detail is recorded on each item and remains in its "
            "evidence; only the requirement's own wording stops depending on it."
        )
    if demoted:
        messages.append(
            f"{demoted} behaviour(s) filed as system requirements describe how the current code "
            "works rather than something the product must do, and are recorded as implementation "
            "observations instead. They keep every citation."
        )
    if not messages:
        messages.append("No requirement statement named a mechanism from the code it cites.")

    return CapabilityResult(
        artifacts={"brd": brd},
        metrics={
            "statements_with_leakage": recorded,
            "statements_rewritten": rewritten,
            "behaviours_demoted_to_observation": demoted,
            "distinct_leaked_tokens": len(tokens_seen),
        },
        messages=messages,
    )

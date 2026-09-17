"""Which test dimensions a requirement actually activates.

This is the anti-explosion mechanism, and it is deterministic on purpose. A dimension is generated
only when something in the requirement activates it, and that decision is made *before* any model
call, so the model designs within a set it did not choose. Without this, "cover every dimension"
turns one requirement into eleven cases and a 190-item set into an unusable suite.

The patterns are English-language heuristics over the requirement statement and its usable
acceptance criteria. They name no framework, protocol, stack, or domain — a payments flow, a
moderation queue, and a retrieval pipeline activate `boundary` for the same reason, because each
states a limit. That is what keeps the pack generic.

Two deliberate exclusions:

- **Rationale is not read.** It is the one field where the requirements pack allows technical
  vocabulary, so reading it would let an implementation detail activate a dimension.
- **`positive` is not pattern-matched.** Every requirement that describes observable behaviour
  gets one, and nothing else earns one by default.
"""

from __future__ import annotations

import re

from agentic.schemas.testgen import IngestedRequirement

# The vocabulary. Ordered for stable reporting; `positive` first because it is the default.
DIMENSIONS = (
    "positive",
    "negative",
    "validation",
    "error_handling",
    "boundary",
    "state_transition",
    "authorization",
    "business_rule",
    "data_integrity",
    "integration",
    "exceptional",
)

# Activation patterns. Word-boundary anchored so "state" does not fire on "statement" and
# "log" does not fire on "logic". Tuned against the rich fixture: the target is most requirements
# activating two to four dimensions, not one and not eleven.
_PATTERNS: dict[str, re.Pattern[str]] = {
    "negative": re.compile(
        r"\b(refus\w*|reject\w*|den(?:y|ies|ied)|declin\w*|forbid\w*|prevent\w*|block\w*"
        r"|not permitted|not allowed|unless|otherwise)\b",
        re.I,
    ),
    "validation": re.compile(
        r"\b(valid\w*|invalid\w*|required|mandatory|malformed|well-formed|missing|absent|empty"
        r"|blank|format\w*|must (?:be|contain|match)|allowed values|permitted values"
        r"|only accepts?)\b",
        re.I,
    ),
    "error_handling": re.compile(
        r"\b(error\w*|fail\w*|exception\w*|fallback|recover\w*|not found|unavailable"
        r"|unsuccessful|rollback)\b",
        re.I,
    ),
    "boundary": re.compile(
        r"\b(limit\w*|maximum|minimum|at most|at least|no more than|no fewer than|exceed\w*"
        r"|threshold|cap(?:ped)?|size|length|count|duration|timeout|range|up to|per (?:page|day"
        r"|hour|minute|request))\b",
        re.I,
    ),
    # A *named state* the thing moves between, not merely a write. Generic create/update/delete
    # verbs were here and fired on 76 of 190 requirements, but for "delete an item" the positive
    # case already asserts the item is gone — a separate transition case is the same test twice.
    # What earns this dimension is a status vocabulary, or an explicit before/after.
    "state_transition": re.compile(
        r"\b(status|state|transition\w*|becomes?|no longer|remains?|revert\w*"
        r"|archiv\w*|activat\w*|deactivat\w*|enabl\w*|disabl\w*|suspend\w*"
        r"|pending|approved|rejected|draft|published|complete[ds]?|expire[ds]?)\b",
        re.I,
    ),
    # An access *condition*, not an actor's job title. `admin` and `role` were here and fired on
    # 34 requirements through the single word "administrator" — but a requirement naming who
    # performs an action says nothing about whether anyone else is refused. Generating an
    # authorization case from that invents a rule the requirement never states.
    "authorization": re.compile(
        r"\b(authoris\w*|authoriz\w*|unauthoris\w*|unauthoriz\w*|permission\w*|privileg\w*"
        r"|forbidden|not allowed to|may only|can only|must be (?:an? )?(?:admin\w*|owner|member)"
        r"|credential\w*|sign(?:ed)? in|logged in|authenticat\w*|access (?:control|right))\b",
        re.I,
    ),
    # An *invariant* the data must hold. `persist`/`stored` were here and fired on 31 requirements,
    # but persisting is simply what a write does — it is not a guarantee anyone stated.
    "data_integrity": re.compile(
        r"\b(uniqu\w*|duplicat\w*|audit\w*|retain\w*|retention|consisten\w*|integrity"
        r"|referential|history|immutab\w*|no more than one|exactly one|orphan\w*)\b",
        re.I,
    ),
    "integration": re.compile(
        r"\b(external|third[- ]party|upstream|downstream|remote|provider|vendor|integrat\w*"
        r"|other system|another system)\b",
        re.I,
    ),
    "exceptional": re.compile(
        r"\b(unavailable|timeout|timed out|retr(?:y|ies|ied)|concurren\w*|simultaneous\w*"
        r"|race|outage|degrad\w*|interrupt\w*|partial failure)\b",
        re.I,
    ),
}

# A rule states a condition and what follows from it. Either the requirements pack typed it as one,
# or the statement carries the documented rule grammar: "When <trigger>, the system shall
# <behaviour>". Anchored to the start of the sentence on purpose — a conditional in the middle is
# usually a qualifier on a capability ("filter results where the status is active"), not a policy.
# On the rich fixture anchoring drops nothing (all 97 shape matches already lead with the
# conditional) and it costs nothing, so it is free precision against a statement written otherwise.
_RULE_SHAPE = re.compile(r"^\s*(when|if|whenever)\b.{0,200}?\bshall\b", re.I | re.S)


def activated_dimensions(requirement: IngestedRequirement) -> list[str]:
    """The dimensions this requirement's own wording justifies, in `DIMENSIONS` order.

    Always includes `positive` for anything with a statement: if a requirement describes behaviour
    at all, that behaviour working is worth one case. Everything else must be earned.
    """
    text = requirement.searchable_text()
    if not text.strip():
        return []

    found = {"positive"}
    for dimension, pattern in _PATTERNS.items():
        if pattern.search(text):
            found.add(dimension)

    if requirement.type == "business_rule" or _RULE_SHAPE.search(requirement.statement):
        found.add("business_rule")

    return [dimension for dimension in DIMENSIONS if dimension in found]


def beyond_positive(requirement: IngestedRequirement) -> list[str]:
    """Dimensions other than the default. Empty means the requirement says very little."""
    return [d for d in activated_dimensions(requirement) if d != "positive"]

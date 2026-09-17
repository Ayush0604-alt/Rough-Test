"""Judging one test case on its own terms.

Everything here answers a single question in different ways: **would this case fail against a
broken implementation?** A case that would not is not a test, however well formatted it is, and
publishing one is worse than publishing nothing — it retires a risk on paper that is still live in
fact.

The lexicons are held as data rather than buried in conditionals so a false positive is a one-line
edit and a visible diff. Each was measured against a real generated suite before being kept.
"""

from __future__ import annotations

import re
from difflib import SequenceMatcher

# Phrases that assert nothing. Split in two because they fail for different reasons and a reviewer
# reading the gate output should be able to tell which.
#
# 1. Unfalsifiable qualifiers: they describe a *standard* the behaviour meets rather than an
#    observable state, so no implementation can be shown to violate them.
UNFALSIFIABLE_QUALIFIERS = (
    "correctly",
    "properly",
    "appropriately",
    "as expected",
    "as intended",
    "as required",
    "as designed",
    "gracefully",
    "seamlessly",
)

# 2. Whole assertions that reduce to "it worked". Anchored, because "the request is refused
#    without error" is a real assertion while "no errors occur" is not.
EMPTY_ASSERTIONS = (
    re.compile(r"^\s*(the\s+\w+\s+)?works?\b", re.I),
    re.compile(r"^\s*no\s+errors?\b", re.I),
    re.compile(r"^\s*without\s+(any\s+)?(errors?|issues?|problems?)\s*\.?\s*$", re.I),
    re.compile(r"^\s*the\s+\w+\s+is\s+(valid|successful|correct)\s*\.?\s*$", re.I),
    re.compile(r"^\s*the\s+(operation|request|action|process)\s+(completes|succeeds)\s*\.?\s*$", re.I),
    re.compile(r"^\s*(it\s+)?(succeeds|passes|behaves\s+as\s+\w+)\s*\.?\s*$", re.I),
)

# Technology the specification layer must not name. A case that says "send a DELETE" can only ever
# become an HTTP test; the whole point of the layer is that it can become any of several.
#
# Matched on WORD BOUNDARIES, not as substrings. Substring matching flagged `PYTHONUNBUFFERED` --
# an environment variable a requirement legitimately names -- as the language "python". The repair
# pass could not remove it because it *was* the thing under test, so the loop could not converge:
# four model calls spent, and a valid case pruned at the end of them.
FRAMEWORK_WORDS = (
    "playwright", "pytest", "promptfoo", "deepeval", "selenium", "cypress", "junit", "jest",
    "mocha", "testng", "cucumber", "postman", "jmeter", "locust",
    "curl", "httpie", "httpx", "axios",
    "python", "javascript", "typescript", "java", "golang", "assertequals",
)
_FRAMEWORK_WORDS = re.compile(
    r"\b(" + "|".join(re.escape(word) for word in FRAMEWORK_WORDS) + r")\b", re.I
)

# Phrases and code fragments that carry their own punctuation, so a word boundary would not help.
FRAMEWORK_PHRASES = ("robot framework", "requests library", "assert(", "expect(", "fetch(")

# Naming the protocol verb is the same mistake in miniature: it fixes the mechanism.
PROTOCOL_VERBS = re.compile(
    r"\b(send|issue|make|perform)\s+(an?\s+)?(get|post|put|patch|delete|head|options)\b", re.I
)

# Two cases this similar are the same test written twice.
DUPLICATE_SIMILARITY = 0.85
# Below that but above this, they overlap enough to be worth a reviewer's eye.
OVERLAP_SIMILARITY = 0.65
# A case whose objective and expected results are this close to the requirement is restating it.
RESTATEMENT_SIMILARITY = 0.82

# Dimensions that are *about* the input, so a case without one has not said what it tests.
INPUT_BEARING_DIMENSIONS = frozenset(
    {"validation", "boundary", "negative", "authorization"}
)

# Wording that means the case acts on something that must already exist.
#
# Measured against a real 154-case suite. A `that\s+\w+` alternative was here to catch "that
# item's note", and matched "that the" fourteen times — half of every hit was noise from a
# subordinate clause. Verbs that presuppose a subject are reliable; a bare demonstrative is not.
PRE_EXISTING = re.compile(
    r"\b(existing|already\s+\w+|previously|the\s+stored|remov\w*|delet\w*|updat\w*"
    r"|archiv\w*|restor\w*|revert\w*|no\s+longer\s+\w+)\b",
    re.I,
)


def normalise(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip().casefold()


def similarity(left: str, right: str) -> float:
    return SequenceMatcher(None, normalise(left), normalise(right)).ratio()


def vague_results(expected_results: list[str]) -> list[str]:
    """Expected results that no implementation could be shown to violate."""
    found: list[str] = []
    for result in expected_results:
        text = normalise(result)
        if not text:
            found.append(result)
            continue
        if any(pattern.match(result.strip()) for pattern in EMPTY_ASSERTIONS):
            found.append(result)
            continue
        if any(qualifier in text for qualifier in UNFALSIFIABLE_QUALIFIERS):
            found.append(result)
    return found


def framework_leaks(*texts: str) -> list[str]:
    """Named technology anywhere in a case, which would bind the specification to one stack.

    Word-boundary matched, so a configuration key that happens to contain a language name
    (`PYTHONUNBUFFERED`, `JAVA_HOME`) is not mistaken for the language itself. Those are things a
    requirement may legitimately be about, and flagging one produces a rejection no rewording can
    satisfy.
    """
    haystack = normalise(" ".join(texts))
    found = sorted({match.lower() for match in _FRAMEWORK_WORDS.findall(haystack)})
    found += [phrase for phrase in FRAMEWORK_PHRASES if phrase in haystack]
    if PROTOCOL_VERBS.search(haystack):
        found.append("a protocol verb")
    return found


def restates(case_text: str, requirement_statement: str) -> bool:
    """Whether the case says what the requirement says instead of how you would check it."""
    return similarity(case_text, requirement_statement) >= RESTATEMENT_SIMILARITY


def needs_precondition(case) -> bool:
    """Whether this case acts on something that has to exist before it runs.

    A removal case that never establishes an item tests nothing: it may pass against a system that
    removes nothing, because there was nothing to remove.
    """
    if case.preconditions:
        return False
    text = " ".join([case.title, *case.steps, *case.expected_results])
    return bool(PRE_EXISTING.search(text))


def case_fingerprint(case) -> str:
    """What makes two cases the same test. Steps and expected results, never the title.

    Titles are the most likely thing to differ between two cases that check exactly the same
    behaviour, so comparing them would miss the duplicates that matter most.
    """
    return normalise(" ".join([*case.steps, *case.expected_results]))

"""Which verbs name the same operation.

Every dedup comparison in the pipeline refuses to merge two statements whose verbs differ, because
"staff shall be able to *delete* a supplier bill" and "... *view* a supplier bill" share five of
six content words and are two capabilities. That guard is right and stays. What it could not do
was tell a difference in operation from a difference in wording: `validate` and `check`, `retrieve`
and `get`, `submit` and `lodge` were kept apart exactly as `view` and `delete` were, and one run
published "Validate Resume Format" and "Resume Format Validation Rule" side by side, citing the
same fifteen lines.

This module names the operations. Two verbs in one class are one operation; two verbs in different
classes are different operations and are never merged; a verb in no class is compared as itself,
which is the old behaviour. The classes are the ordinary verbs of software requirements -- CRUD,
search, send, schedule, approve -- and name no domain, framework or stack.
"""

from __future__ import annotations

# Operation -> the verbs that name it. A verb appears in exactly one class.
ACTION_CLASSES: dict[str, tuple[str, ...]] = {
    "read": (
        "retrieve", "get", "fetch", "view", "list", "display", "show", "see", "access", "read",
        "obtain", "browse", "return", "lookup", "open", "preview", "load",
    ),
    "search": ("search", "find", "filter", "query", "sort"),
    "create": (
        "create", "add", "submit", "register", "record", "lodge", "insert", "enter", "make",
        "post", "capture", "save", "store", "persist",
    ),
    "upload": ("upload", "attach"),
    "update": ("update", "edit", "modify", "change", "amend", "revise", "alter", "rename"),
    "configure": ("configure", "set", "customise", "customize", "adjust", "toggle", "enable",
                  "disable"),
    "delete": ("delete", "remove", "discard", "erase", "purge", "clear", "drop"),
    "archive": ("archive",),
    "restore": ("restore", "revert", "reset", "undo", "recover"),
    "validate": ("validate", "check", "verify", "confirm", "inspect", "test", "assess"),
    "enforce": (
        "enforce", "ensure", "require", "maintain", "restrict", "limit", "prevent", "prohibit",
        "forbid", "guarantee", "block",
    ),
    "send": ("send", "notify", "email", "message", "dispatch", "deliver", "forward", "remind",
             "alert"),
    "export": ("export", "download"),
    "import": ("import", "ingest", "sync", "synchronise", "synchronize"),
    "approve": ("approve", "accept", "authorise", "authorize", "grant"),
    "reject": ("reject", "decline", "deny", "refuse"),
    "publish": ("publish", "release"),
    "retract": ("retract", "withdraw", "unpublish", "revoke"),
    "schedule": ("schedule", "book", "reserve", "reschedule"),
    "cancel": ("cancel", "abort"),
    "authenticate": ("authenticate", "login", "signin", "logon"),
    "process": ("process", "handle", "parse", "extract"),
    "generate": ("generate", "produce", "compute", "calculate", "derive", "render"),
    "assign": ("assign", "allocate", "route", "distribute"),
    "classify": ("classify", "categorise", "categorize", "tag", "label", "score", "rank"),
    "track": ("track", "monitor", "audit"),
    "resolve": ("resolve", "settle"),
}

_VERB_TO_CLASS: dict[str, str] = {
    verb: name for name, verbs in ACTION_CLASSES.items() for verb in verbs
}

# Words ending in -ly that are verbs, not adverbs.
_LY_VERBS = frozenset({"apply", "reply", "supply", "multiply", "comply", "rely", "ally"})


def is_adverb(word: str) -> bool:
    """An -ly adverb standing between the modal and the verb: "shall *consistently* use"."""
    word = word.casefold()
    return len(word) > 4 and word.endswith("ly") and word not in _LY_VERBS


def action_class(verb: str) -> str:
    """The operation a verb names, or the verb itself when it names none this module knows.

    Tolerates the inflections a requirement sentence uses (`retrieves`, `validated`) and the
    stems the capability clusterer compares (`retriev`).
    """
    word = (verb or "").strip().casefold()
    if not word:
        return ""
    if word in _VERB_TO_CLASS:
        return _VERB_TO_CLASS[word]
    for suffix in ("ies", "es", "s", "ed", "d", "ing"):
        if word.endswith(suffix) and len(word) - len(suffix) >= 3:
            base = word[: -len(suffix)]
            for candidate in (base, base + "e", base + "y"):
                if candidate in _VERB_TO_CLASS:
                    return _VERB_TO_CLASS[candidate]
    if len(word) >= 5:
        # A stem: "retriev", "validat". Only a near-complete prefix counts, so a short word
        # cannot land in the class of a longer one it merely begins.
        for candidate, name in _VERB_TO_CLASS.items():
            if candidate.startswith(word) and len(candidate) - len(word) <= 2:
                return name
    return word


def same_operation(left: str, right: str) -> bool:
    """Whether two verbs name one operation. Two unknown verbs must be the same word."""
    return action_class(left) == action_class(right)

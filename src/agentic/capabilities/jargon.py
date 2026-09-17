"""Technical-vocabulary linter for business-language statements.

Two of the five skills require that a requirement statement carry no technical vocabulary, and
both say to verify it rather than trust the synthesis step. A prompt instruction is not a check:
models comply most of the time, and "most of the time" is exactly how a file path reaches a
document that a stakeholder signs off on.

This runs over statements only. Rationale, evidence, and graph paths are allowed — indeed
required — to be technical.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Named products and services. A statement that leans on one has usually stopped describing the
# product — except in an *integration requirement*, whose entire job is to say which external
# system is depended on and what happens when it is unavailable. "The system shall read mail from
# a Microsoft 365 mailbox" is not jargon; it is the requirement. So these are rejected by default
# and allowed for the families that exist to name them.
EXTERNAL_SYSTEMS = {
    "redis", "postgres", "postgresql", "mysql", "sqlite", "mongodb", "elasticsearch", "kafka",
    "rabbitmq", "docker", "kubernetes", "aws", "azure", "gcp", "fastapi", "flask", "django",
    "express", "spring", "react", "angular", "vue", "streamlit", "pydantic", "sqlalchemy",
    "pandas", "numpy", "langchain", "langgraph", "openai", "chromadb", "chroma", "huggingface",
    "groq", "llama", "gpt", "transformers", "pytorch", "torch", "gradio", "unstructured",
    "faiss", "pinecone", "weaviate", "qdrant", "minio", "outlook", "tesseract", "libreoffice",
    "langfuse", "graphql", "grpc",
}

# Terms that name the *implementation* — the protocol, serialisation format, or code construct a
# reader should never have to know about. These are always rejected: a requirement that names one
# has stopped describing the product and started describing the build.
IMPLEMENTATION_TERMS = {
    "rest", "http", "https", "url", "uri", "json", "xml",
    "yaml", "nosql", "db",
    "function", "method", "class", "module", "package", "repository", "repo", "codebase",
    "variable", "parameter", "argument", "constructor", "decorator", "middleware", "handler",
    # "process" is deliberately absent. It is ordinary business English — a claim is processed, an
    # approval has a process — and flagging it (and, once inflections count, "processing" and
    # "processed") rejected sound requirements for no gain in stakeholder readability.
    "callback", "async", "await", "thread", "server", "backend", "frontend",
    "microservice", "runtime", "compiler", "parser", "ast", "regex", "algorithm", "datastructure",
    "filesystem", "file system", "persist directory", "session state",
    "environment variable", "config file", "source code", "graph node", "knowledge graph",
    "request body", "response body", "status code", "stack trace", "exception",
    # Named controls. A statement that says which control to operate describes a screen, not a
    # requirement: the next redesign invalidates it while the product still does the same thing.
    "button", "checkbox", "dropdown", "uploader", "widget", "sidebar", "chat input",
    "rag", "top-k", "topk", "boolean", "base64",
    # "prompt" and "flag" are deliberately absent: "prompt the user" and "flag for review" are
    # ordinary business English, and rejecting them would cost more sound requirements than the
    # technical readings would gain.
}

# Terms a product and its engineers genuinely share. A document-search product indexes documents
# and answers queries; that is the domain's own vocabulary, and the reference documents this
# workflow is measured against use it throughout. Rejecting these produced contorted paraphrases
# ("reupload", "reprocess") that were less accurate than the plain word — so they are rejected
# only under `strict=True`, for a caller that wants a stakeholder-facing register with no
# engineering overlap at all.
DOMAIN_TERMS = {
    "index", "query", "cache", "database", "schema", "table", "column", "chunk", "embedding",
    "vector store", "vector database", "directory", "folder path", "client",
    # Measured against the reference document rather than assumed: each of these appears in its
    # published requirement statements, carrying real business meaning that no paraphrase keeps.
    # "an LLM MAY be consulted only to resolve a genuinely ambiguous case" is a policy about
    # automation; "enforced at the API layer, not only by hiding UI controls" is a security
    # requirement about *where* a rule holds. Rejecting these cost more sound requirements than
    # the technical readings gained.
    "llm", "pipeline", "api", "endpoint", "payload", "metadata", "server", "frontend", "backend",
}

TECHNICAL_TERMS = IMPLEMENTATION_TERMS | DOMAIN_TERMS | EXTERNAL_SYSTEMS

# Multi-word and hyphenated terms are checked before tokenising: "environment variable" splits
# into two individually innocuous words, and "top-k" splits into one word and one letter too
# short to match anything.
def _is_phrase(term: str) -> bool:
    return " " in term or "-" in term


EXTERNAL_SYSTEM_PHRASES = {term for term in EXTERNAL_SYSTEMS if _is_phrase(term)}
EXTERNAL_SYSTEM_WORDS = {term for term in EXTERNAL_SYSTEMS if not _is_phrase(term)}
IMPLEMENTATION_PHRASES = {term for term in IMPLEMENTATION_TERMS if _is_phrase(term)}
IMPLEMENTATION_WORDS = {term for term in IMPLEMENTATION_TERMS if not _is_phrase(term)}
TECHNICAL_PHRASES = {term for term in TECHNICAL_TERMS if _is_phrase(term)}
TECHNICAL_WORDS = {term for term in TECHNICAL_TERMS if not _is_phrase(term)}

# Source-code and configuration extensions only. A document format a *person* uploads is not
# implementation detail: "accepts a PDF, a Word document, or a spreadsheet" is precisely what the
# product offers, and the reference document states accepted formats in its own requirements. So
# .pdf, .docx, .xlsx, .zip and their kin are deliberately absent from this list.
FILE_EXTENSIONS = re.compile(
    r"\.(?:py|js|jsx|ts|tsx|java|kt|go|rs|rb|php|cs|cpp|c|h|sql|ya?ml|toml|ini|cfg)\b",
    re.IGNORECASE,
)
# A real path carries a dot somewhere — a file extension or a relative "./" — as well as a slash.
# Requiring both is what stops ordinary slashed English being reported as a file path:
# "admission/rejection decision", "each discard/restore", "contact identity (name/email/phone)"
# were all rejected as paths, and every one is a perfectly readable business phrase.
_PATH_CANDIDATE = re.compile(r"(?:\./)?[\w.-]+(?:/[\w.-]+)+")


def _path_like(statement: str) -> list[str]:
    """Slash-separated tokens that really are paths rather than English with slashes in it."""
    found: list[str] = []
    for candidate in _PATH_CANDIDATE.findall(statement):
        segments = candidate.split("/")
        if not any("." in segment for segment in segments):
            # "name/email/phone", "discard/restore" — alternatives, not a location.
            continue
        if all(segment.startswith(".") for segment in segments if segment):
            # ".pdf/.docx/.doc" — a list of accepted document formats, which is what the product
            # offers rather than how it is built.
            continue
        found.append(candidate)
    return found


CALL_LIKE = re.compile(r"\b\w+\s*\(\s*\)")
SNAKE_CASE = re.compile(r"\b[a-z][a-z0-9]*(?:_[a-z0-9]+)+\b")
CAMEL_CASE = re.compile(r"\b[a-z]+[A-Z][A-Za-z0-9]*\b")
DOTTED_PATH = re.compile(r"\b\w+(?:\.\w+){2,}\b")
HTTP_ROUTE = re.compile(r"(?:^|\s)/[a-z0-9]")
BACKTICKED = re.compile(r"`[^`]+`")
# Ordinary sentence capitalisation and acronyms are fine; a run of internal capitals is not.
PASCAL_CASE = re.compile(r"\b[A-Z][a-z0-9]+(?:[A-Z][a-z0-9]+)+\b")


@dataclass(frozen=True)
class JargonFinding:
    kind: str
    term: str

    def describe(self) -> str:
        return f"{self.kind}: {self.term!r}"


def _stems(word: str) -> set[str]:
    """Crude English inflections, enough to catch a technical noun used as a verb."""
    stems: set[str] = set()
    for suffix in ("ing", "ed", "es", "s"):
        if word.endswith(suffix) and len(word) > len(suffix) + 2:
            stem = word[: -len(suffix)]
            stems.add(stem)
            if suffix in {"ing", "ed"} and stem and stem[-1] == stem[-2:-1]:
                stems.add(stem[:-1])  # "indexxing" style doubling
            stems.add(stem + "e")     # "caching" -> "cache"
    if word.endswith("ies") and len(word) > 4:
        stems.add(word[:-3] + "y")    # "queries" -> "query"
    return stems


def find_jargon(
    statement: str,
    known_symbols: set[str] | None = None,
    *,
    strict: bool = False,
    allow_external_systems: bool = False,
) -> list[JargonFinding]:
    """Return every technical give-away in a statement.

    `known_symbols` are the qualified names present in the knowledge graph. Matching against them
    catches project-specific vocabulary the generic patterns miss — a class called `Ledger` reads
    like an ordinary business noun until you notice it is also a symbol in the code.

    `allow_external_systems` lifts the ban on named products and services. It is set for the
    families whose whole purpose is to name one: an integration requirement forbidden from saying
    which system it integrates with asserts nothing at all, and a non-functional requirement about
    a connection pool has to be able to say what is being pooled.
    """
    findings: list[JargonFinding] = []
    lowered = statement.casefold()

    # `BACKTICKED` is deliberately not a rule of its own any more. Backticks mark *declared
    # literals* as often as they mark code — an identifier prefix, a reason code a user is shown,
    # an accepted format, a dialling prefix — and flagging the punctuation rejected statements
    # whose content was perfectly readable. Whatever sits inside the backticks is still checked by
    # every pattern below, so a genuine file path or identifier is caught on its own merits.
    for pattern, kind in (
        (FILE_EXTENSIONS, "file extension"),
        (CALL_LIKE, "function call"),
        (SNAKE_CASE, "identifier"),
        (CAMEL_CASE, "identifier"),
        (PASCAL_CASE, "identifier"),
        (DOTTED_PATH, "qualified name"),
        (HTTP_ROUTE, "route path"),
    ):
        for match in pattern.findall(statement):
            findings.append(JargonFinding(kind, match if isinstance(match, str) else str(match)))

    for path in _path_like(statement):
        findings.append(JargonFinding("file path", path))

    phrases = TECHNICAL_PHRASES if strict else IMPLEMENTATION_PHRASES
    vocabulary = TECHNICAL_WORDS if strict else IMPLEMENTATION_WORDS
    if not allow_external_systems:
        phrases = phrases | EXTERNAL_SYSTEM_PHRASES
        vocabulary = vocabulary | EXTERNAL_SYSTEM_WORDS

    for phrase in phrases:
        if phrase in lowered:
            findings.append(JargonFinding("technical term", phrase))

    # Trailing digits are part of the term, not noise: `[a-z]+` alone splits "base64" into "base"
    # and nothing, so an encoding name reached statements the linter was supposed to be watching.
    words = set(re.findall(r"[a-z][a-z0-9]*", lowered))
    matched: set[str] = set()
    for word in words:
        # Inflections count. Matching the bare form only lets "indexing", "querying", and
        # "caching" through, which is exactly the register a requirement slips into when it is
        # describing the mechanism instead of the outcome.
        for candidate in {word, *_stems(word)}:
            if candidate in vocabulary:
                matched.add(candidate)
                break
    for word in sorted(matched):
        findings.append(JargonFinding("technical term", word))

    if known_symbols:
        # Only distinctive names are worth matching. A one- or two-character symbol, or a common
        # English word that happens to be a function name, would produce constant false positives.
        for symbol in known_symbols:
            leaf = symbol.rsplit(".", 1)[-1].split("!", 1)[0]
            if len(leaf) < 4 or leaf.casefold() in _COMMON_WORDS:
                continue
            if re.search(rf"\b{re.escape(leaf)}\b", statement):
                findings.append(JargonFinding("source symbol", leaf))

    # De-duplicate while keeping the first occurrence's ordering, so the message stays readable.
    seen: set[tuple[str, str]] = set()
    unique: list[JargonFinding] = []
    for finding in findings:
        key = (finding.kind, finding.term.casefold())
        if key not in seen:
            seen.add(key)
            unique.append(finding)
    return unique


# Words that are ordinary English first and a symbol name second. Matching a statement against
# every qualified name in the graph catches project-specific vocabulary the generic patterns miss
# -- a class called `Ledger` reads like a business noun until you notice it is also a symbol -- but
# a codebase also names its functions after the plainest verbs and nouns in the language, and a
# requirement cannot describe what someone does without using them.
#
# Rejecting these is worse than letting them through in both directions at once. The statement is
# rejected for saying "update", which is the only word for what the person is doing; and the
# repair, having been told which word to remove, removes the verb the sentence was built around.
# "A user shall be able to update the next action" becomes "A user shall be able to the next
# action" -- a rejection over vocabulary turning a correct requirement into a broken one.
#
# English, not any domain: nothing here names a technology, a framework, or a subject area.
_COMMON_WORDS = {
    # Nouns any product's own language contains.
    "main", "user", "users", "item", "items", "name", "names", "data", "list", "value",
    "values", "order", "orders", "report", "reports", "result", "results", "record", "records",
    "document", "documents", "customer", "customers", "product", "products", "account",
    "accounts", "invoice", "invoices", "payment", "payments", "message", "messages", "text",
    "date", "time", "type", "types", "status", "total", "count", "price", "amount", "title",
    "note", "notes", "file", "files", "detail", "details", "entry", "entries", "person",
    "people", "role", "roles", "reason", "reasons", "step", "steps", "stage", "stages",
    "summary", "content", "context", "history", "action", "actions", "state", "states",
    # Verbs a requirement statement is built around. A requirement says what someone *does*, and
    # these are the words for doing it.
    "add", "create", "update", "delete", "remove", "edit", "view", "read", "write", "save",
    "store", "send", "receive", "submit", "approve", "reject", "cancel", "archive", "restore",
    "retrieve", "fetch", "list", "search", "filter", "sort", "select", "check", "verify",
    "confirm", "accept", "assign", "review", "export", "import", "upload", "download", "open",
    "close", "start", "stop", "run", "apply", "revert", "reset", "clear", "count", "notify",
    "invite", "share", "print", "copy", "move", "rename", "publish", "schedule", "complete",
    "generate", "provide", "return", "display", "show", "record", "report", "manage", "handle",
    "process", "prepare", "collect", "gather", "merge", "split", "group", "link", "attach",
}


def describe(findings: list[JargonFinding], limit: int = 4) -> str:
    shown = "; ".join(finding.describe() for finding in findings[:limit])
    extra = len(findings) - limit
    return f"{shown} (+{extra} more)" if extra > 0 else shown

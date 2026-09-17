"""Graphify-backed extraction: the language-agnostic replacement for AST parsing.

Two capabilities live here, deliberately split so a failure is attributable to one of them:

- `graphify_extract` runs the external `graphify` binary and hands back the path to its
  `graph.json`. It knows nothing about the canonical schema.
- `adapt_graphify_graph` reads that file and produces a `KnowledgeGraph`. It knows nothing about
  subprocesses.

Everything downstream of `plan_clusters` is untouched by the swap, because the contract between
extraction and the rest of the pipeline is `KnowledgeGraph` (`agentic.schemas.core`) and nothing
else. The adapter's whole job is to satisfy that contract.

## What Graphify actually emits (measured, graphifyy 0.9.52)

There is no `type` or `kind` field on a node. A node carries `label`, `file_type` (`code` or
`rationale`), `source_file`, `source_location`, and two booleans::

    {"id": "app_main_audit_region", "label": "audit_region()", "file_type": "code",
     "source_file": "app/main.py", "source_location": "L26", "_callable": true, "_origin": "ast"}

Three consequences drive the code below:

1. **Kind is classified, not looked up.** See `classify_kind`.
2. **`source_location` is a start line, never a range.** `_derive_line_ends` reconstructs end
   lines, because a citation collapsed to a single line makes every evidence packet thinner and
   `SourceLocation.compact()` unhelpful.
3. **`_callable` cannot be trusted across languages** — Go functions come back with `false` while
   the Python, Java and JavaScript equivalents come back `true`. The `()` suffix Graphify puts on
   a callable's label is the portable signal.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys

from collections import defaultdict
from pathlib import Path, PurePosixPath
from typing import Any

from agentic.capabilities.graph_ops import GraphBuilder, stable_id
from agentic.capabilities.registry import CapabilityContext, CapabilityResult, capability
from agentic.schemas.core import (
    Entity,
    KnowledgeGraph,
    Provenance,
    RepositoryInventory,
    SourceLocation,
)

# How Graphify's relation vocabulary maps onto the canonical edge kinds that
# `graph_ops.WORKFLOW_EDGE_KINDS` traverses. Every relation observed in a 0.9.52 run over a 19k-line
# Python repository, a Java/Go/JavaScript corpus, and a SQL schema is listed; anything new falls
# through to RELATED_TO, which no workflow walk follows, so an unknown relation adds a visible node
# pair rather than silently reshaping a cluster.
RELATION_MAP = {
    "calls": "CALLS",
    # Not a call: Graphify emits `indirect_call` when a function is handed somewhere as a value
    # rather than invoked — `set_defaults(handler=cmd_report)`, `http.HandleFunc("/x", listItems)`,
    # a route table, a task registry. That is exactly what REGISTERS was added for, and mapping it
    # to CALLS instead makes every registered handler look like ordinary called-from-here code,
    # which disqualifies it as an interaction-boundary candidate. Both kinds are traversed.
    "indirect_call": "REGISTERS",
    "uses": "CALLS",
    "method": "CONTAINS",
    "contains": "CONTAINS",
    "imports": "IMPORTS",
    "imports_from": "IMPORTS",
    "re_exports": "IMPORTS",
    "inherits": "INHERITS",
    "references": "REFERENCES",
    "rationale_for": "EXPLAINS",
    "triggers": "TRIGGERS",
}

DEFAULT_RELATION = "RELATED_TO"

# Graphify tags every edge with its own confidence vocabulary, which is the same vocabulary the
# canonical schema already uses. RESOLVED never appears — Graphify does not distinguish a resolved
# call from a syntactically certain one — so a mapped CALLS edge is EXTRACTED, not RESOLVED.
_PROVENANCE_BY_LABEL = {
    "EXTRACTED": Provenance.EXTRACTED,
    "RESOLVED": Provenance.RESOLVED,
    "INFERRED": Provenance.INFERRED,
    "AMBIGUOUS": Provenance.AMBIGUOUS,
}

_CONFIDENCE_BY_PROVENANCE = {
    Provenance.EXTRACTED: 1.0,
    Provenance.RESOLVED: 0.95,
    Provenance.INFERRED: 0.7,
    Provenance.AMBIGUOUS: 0.4,
}

# `"L26"`, and defensively `"L26-L40"` / `"L26-40"` in case a future release emits a range.
_LOCATION = re.compile(r"L(\d+)(?:\s*-\s*L?(\d+))?")

_SECRET = re.compile(r"(?i)(.*(?:api[_-]?key|password|secret|token)\s*[:=]\s*)(.+)$")

# Files whose prose states intent the code cannot. Kept in step with `_safe_file_outline`'s original
# home in the deleted `extract_python` module: this outline is *supporting* evidence, never the sole
# warrant for a requirement, and the gates enforce that.
_OUTLINE_MAX_BYTES = 256_000
_OUTLINE_MAX_LINES = 50
_OUTLINE_LINE_CHARS = 200


# --------------------------------------------------------------------------------------------
# Node classification
# --------------------------------------------------------------------------------------------


def classify_kind(raw: dict[str, Any]) -> str:
    """Decide a canonical entity kind for one Graphify node.

    Ordered most-certain first, because several signals overlap: a class is also callable, and a
    file node also has a source file.

    `_callable` is checked *after* the label suffix on purpose. Graphify sets it from the
    language's own grammar, and the Go grammar does not mark a `func` as callable — so a Go
    repository classified on that flag alone lands every function under SYMBOL, is excluded from
    call traversal, and produces an empty document. The `()` Graphify appends to a callable's
    label is emitted by every language extractor.
    """
    if raw.get("file_type") == "rationale":
        return "RATIONALE"

    source_file = str(raw.get("source_file") or "")
    label = str(raw.get("label") or "")

    # No file means Graphify saw the name referenced but never defined here: a library symbol, a
    # builtin, a type from another package. It has no location and can never ground a claim.
    if not source_file:
        return "EXTERNAL_SYMBOL"

    # Graphify emits one node per file, labelled with as much of the path as it takes to be
    # unambiguous: `main.py` in a flat repository, but `api/mail.py` or `constants/index.ts` once
    # two directories hold a file of the same name. Matching the basename alone therefore missed
    # every file node in a repository with any repeated filename — `__init__.py`, `index.ts`,
    # `mail.py` — and each one fell through to SYMBOL and asserted its own filename as a symbol.
    if _is_path_suffix(label, source_file):
        return "FILE"

    if raw.get("_callable_class"):
        return "CLASS"

    if label.endswith("()"):
        # A leading dot is Graphify's own notation for a member: `.find()` on a class, versus a
        # free `buildService()`.
        return "METHOD" if label.startswith(".") else "FUNCTION"

    if raw.get("_callable"):
        return "FUNCTION"

    return "SYMBOL"


def _is_path_suffix(label: str, source_file: str) -> bool:
    """True when `label` names the file, whole or as a trailing run of its path segments.

    Compared on segment boundaries so `mail.py` matches `app/api/mail.py` while `ail.py` does not.
    """
    label_parts = PurePosixPath(label.replace("\\", "/")).parts
    file_parts = PurePosixPath(source_file.replace("\\", "/")).parts
    if not label_parts or len(label_parts) > len(file_parts):
        return False
    return file_parts[-len(label_parts) :] == label_parts


# `symbol` promises a bare identifier that appears on the cited line, and verification re-reads the
# file to confirm it. A path is never that, whatever kind of node carries it.
#
# Deliberately *not* a file-extension test. `index.ts` and `far.owner` are indistinguishable by
# suffix — any rule short enough to catch `.ts` also catches `.owner` — so a node naming its own
# file is caught where the answer is exact instead: `classify_kind` compares the label against the
# path it cites, and a match is a FILE, which asserts no symbol at all.
_IDENTIFIER = re.compile(r"^[A-Za-z_$][A-Za-z0-9_$]*$")


# A RATIONALE node's label is a whole sentence lifted from a comment or docstring, not an
# identifier. Left at full length it becomes an unreadable qualified name in the traceability
# matrix and an unmatchable `symbol` in a citation.
_NAME_CHARS = 80


def _display_name(raw: dict[str, Any], kind: str) -> str:
    label = " ".join(str(raw.get("label") or raw.get("id") or "").split())
    if kind == "RATIONALE" and len(label) > _NAME_CHARS:
        return label[: _NAME_CHARS - 1].rstrip() + "…"
    return label


def _qualified_name(raw: dict[str, Any], kind: str) -> str:
    """A name a reader can cite and `validate_requirements` can resolve a graph path against.

    Graphify ids are already globally unique and file-scoped (`app_main_audit_region`), but they
    are not readable. `file::label` keeps the file that disambiguates two same-named functions
    while staying legible in a traceability matrix.
    """
    label = _display_name(raw, kind)
    source_file = str(raw.get("source_file") or "")
    # A file is named by its whole path. Its label is only as much of the path as it took to be
    # unambiguous, so two files called `api/mail.py` under different roots would otherwise share
    # one qualified name and `by_qualified_name` would keep just one of them.
    if kind == "FILE" and source_file:
        return source_file
    if kind == "EXTERNAL_SYMBOL" or not source_file:
        return label or source_file
    return f"{source_file}::{label.lstrip('.')}"


def _verifiable_symbol(kind: str, label: str) -> str | None:
    """What may be asserted as appearing on the cited line, or None.

    Three kinds never make that promise. A FILE's label is its own path, which is not written on
    line 1. A RATIONALE's label is a prose sentence. And any label that reads as a path or a
    filename is one the verifier would strip to its extension and then fail to find.
    """
    if kind in {"FILE", "RATIONALE"}:
        return None
    candidate = label.strip(". ()")
    if not candidate or "/" in candidate or "\\" in candidate:
        return None
    # Identifier-shaped, dotted paths allowed. A label carrying a space is a description rather
    # than a name — `entrypoint.sh script` — and the dotted rule downstream would hunt the file
    # for `sh script`. Rejecting it costs the citation nothing: an absent symbol simply means the
    # line is not symbol-checked, while a wrong one invalidates a sound citation.
    if not all(_IDENTIFIER.match(part) for part in candidate.split(".")):
        return None
    return candidate


def _parse_location(raw: dict[str, Any]) -> tuple[str, int | None] | None:
    """`(file, start_line)` for a node or edge, or None when it cites nowhere.

    `source_location` has been observed as `"L26"`, as `""`, and as `null` on the same release, so
    all three are handled rather than assumed away.
    """
    source_file = str(raw.get("source_file") or "").replace("\\", "/")
    if not source_file:
        return None
    match = _LOCATION.search(str(raw.get("source_location") or ""))
    return source_file, int(match.group(1)) if match else None


def _derive_line_ends(
    nodes: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    file_lines: dict[str, int] | None = None,
) -> dict[str, int]:
    """Reconstruct an end line per node id, because Graphify only ever emits a start.

    A definition's span is what makes an evidence packet worth reading and what a requirement
    cites. Without this every citation is one line long, and the synthesis prompt's instruction to
    "cite at the span of the definition it rests on" has nothing to work with.

    Two passes:

    1. **Sibling bound.** Within a file, a definition cannot extend past the start of the next one,
       so a provisional end is `next_start - 1`. The last definition in a file runs to the end of
       the file when `file_lines` supplies its length — without that it would collapse to a single
       line, which is exactly the evidence loss this function exists to prevent.
    2. **Containment repair.** Pass 1 is wrong for containers: a class starting at L5 whose first
       method starts at L11 would end at L10, cutting the class off at its own first member. So a
       container is extended to the furthest end among the nodes it `contains`, transitively.

    The result is an estimate and is labelled as one (`line_end_estimated`). It is deliberately an
    over-approximation rather than an under-approximation: a span that is slightly too long still
    contains the evidence, whereas one that is too short silently drops it.
    """
    file_lines = file_lines or {}
    starts: dict[str, int] = {}
    by_file: dict[str, list[str]] = defaultdict(list)
    # A RATIONALE is a docstring or comment block *inside* the definition it describes, not a
    # sibling of it. Counting one as a sibling truncates every documented function to the lines
    # above its own docstring — on a fixture whose functions all carry one, the longest span in the
    # graph came out at five lines. They are bounded separately, below.
    annotations: set[str] = set()
    for raw in nodes:
        location = _parse_location(raw)
        if location is None or location[1] is None:
            continue
        node_id = str(raw.get("id"))
        starts[node_id] = location[1]
        by_file[location[0]].append(node_id)
        if classify_kind(raw) == "RATIONALE":
            annotations.add(node_id)

    ends: dict[str, int] = {}
    for source_file, node_ids in by_file.items():
        file_end = max(file_lines.get(source_file, 0), 1)
        structural = sorted(
            (item for item in node_ids if item not in annotations), key=lambda item: starts[item]
        )
        for position, node_id in enumerate(structural):
            following = [
                starts[other]
                for other in structural[position + 1 :]
                if starts[other] > starts[node_id]
            ]
            if following:
                ends[node_id] = following[0] - 1
            else:
                ends[node_id] = max(file_end, starts[node_id])
        # An annotation runs to the next *structural* node, so a module docstring covers the
        # header and a function docstring covers itself without swallowing the body.
        for node_id in (item for item in node_ids if item in annotations):
            following = [start for start in (starts[o] for o in structural) if start > starts[node_id]]
            ends[node_id] = (following[0] - 1) if following else max(file_end, starts[node_id])

    # Containment repair. Iterated to a fixed point so a file -> class -> method chain lifts the
    # method's end all the way to the file, rather than only one level.
    children: dict[str, list[str]] = defaultdict(list)
    for edge in edges:
        if str(edge.get("relation") or "").lower() not in {"contains", "method"}:
            continue
        children[str(edge.get("source"))].append(str(edge.get("target")))

    for _ in range(len(by_file) + 8):
        changed = False
        for parent, kids in children.items():
            if parent not in ends:
                continue
            deepest = max((ends[kid] for kid in kids if kid in ends), default=0)
            if deepest > ends[parent]:
                ends[parent] = deepest
                changed = True
        if not changed:
            break

    return ends


# --------------------------------------------------------------------------------------------
# Module-level interaction surface
# --------------------------------------------------------------------------------------------

# How far after a surface call to look for the work it sets off. A control and the block it guards
# sit together; beyond a dozen lines the association stops being evidence.
SURFACE_WORKFLOW_WINDOW = 14

# Per file, so a long script cannot flood the candidate list.
MAX_SURFACE_PER_FILE = 40

# The most literals to carry on one surface node, and the longest each may be.
MAX_LITERALS = 6
MAX_LITERAL_CHARS = 120

# A call: `name(`, `obj.name(`, `a.b.c(`.
_CALL = re.compile(r"([A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*)\s*\(")

# A line whose call result is *consumed* — assigned to something, or used as a condition. This is
# the whole discrimination, and it is a shape rather than a list of frameworks:
#
#   uploaded_file = st.file_uploader("Choose a medical PDF")   <- an input; the value is used
#   if st.button("Add to Index"):                              <- a control; the value is tested
#   st.success("Done!")                                        <- display; the value is discarded
#
# A control offers the outside world a way in, and a product's own name for it is the literal it
# carries. A rendering call states nothing anyone can act on.
_CONSUMED = re.compile(
    r"""^\s*(?:
          (?:if|elif|while|unless|when|case|return|yield)\s        # tested
        | [A-Za-z_][\w,\s\[\]\.]*=(?!=)                          # assigned
    )""",
    re.VERBOSE,
)

_HAS_LITERAL = re.compile(r"""['"`]([^'"`\n]{1,120})['"`]""")

# A name this file binds at module level, and so computes for itself: the left of an assignment,
# a loop variable, or a `with ... as` target. A call *on* such a name is internal work.
_BOUND_NAME = re.compile(
    r"""^\s*(?:
          ([A-Za-z_][A-Za-z0-9_]*)\s*=(?!=)                       # x = ...
        | for\s+([A-Za-z_][A-Za-z0-9_]*)\s+in\s                   # for x in ...
        | with\s+.*?\sas\s+([A-Za-z_][A-Za-z0-9_]*)               # with ... as x
    )""",
    re.VERBOSE,
)


def _module_surface(
    source_root: Path,
    relative: str,
    covered: list[tuple[int, int]],
    local_definitions: set[str],
) -> list[tuple[int, str, list[str]]]:
    """Interaction points written as module-level statements rather than definitions.

    The gap this closes is total rather than partial. A script-style UI — Streamlit, Gradio, a
    notebook-shaped app — declares its entire interaction surface as top-level widget calls. The
    extractor models definitions and nothing else, so such a product yields a graph of helper
    functions, no boundary, no cluster, and an empty requirements document: it looks like a
    library with nothing exposed.

    `covered` is every definition's line span in this file, so "module level" means "outside all of
    them" rather than "at column zero" — a widget nested in a `with` block is still module level.

    Returns `(line, call, literals)` per surface call found.
    """
    try:
        lines = (source_root / relative).read_text(
            encoding="utf-8-sig", errors="ignore"
        ).splitlines()
    except OSError:
        return []

    # Names this file computes for itself at module level. A call on one of them — `result.get(...)`
    # on a value assigned two lines above — is internal work, not a way in from outside.
    local_values: set[str] = set()

    found: list[tuple[int, str, list[str]]] = []
    for number, text in enumerate(lines, start=1):
        if any(start <= number <= end for start, end in covered):
            continue

        # Recorded before anything else, and for every module-level line rather than only the ones
        # that survive the tests below: a loop variable is bound on a `for` line, which is not a
        # consumed call and would otherwise never be seen.
        bound = _BOUND_NAME.match(text)
        names_bound_here = (
            {name for name in bound.groups() if name} if bound else set()
        )

        # A value assigned to an ALL-CAPS name is a constant by universal convention, and a
        # constant is a declaration rather than a control: `RETENTION_DAYS = int(os.getenv(...))`
        # reads the environment, it does not offer anyone a way in. Left in, every configured
        # setting in a repository became an interaction boundary and crowded out the real ones.
        declares_constant = bool(
            bound and bound.group(1) and bound.group(1).isupper() and len(bound.group(1)) > 1
        )

        if _CONSUMED.match(text) and not declares_constant:
            literals = [
                value for value in _HAS_LITERAL.findall(text) if _useful_surface_literal(value)
            ]
            if literals:
                for call in _CALL.findall(text):
                    root = call.split(".", 1)[0]
                    # A call to something this file defines or computes is ordinary internal
                    # work; the graph already has the definition and the call. What names an
                    # interaction point is a call *out* — to a framework the file imported.
                    if (
                        root in local_definitions
                        or call in local_definitions
                        or root in local_values
                    ):
                        continue
                    found.append((number, call, literals[:MAX_LITERALS]))
                    break

        local_values |= names_bound_here
        if len(found) >= MAX_SURFACE_PER_FILE:
            break
    return found


def _useful_surface_literal(value: str) -> bool:
    """A literal that could be a control's own label, rather than a format or a flag."""
    value = value.strip()
    return bool(value) and len(value) <= MAX_LITERAL_CHARS and not value.startswith("{")


def _resolves(source_root: Path, relative: str) -> bool:
    """Whether a cited path is a real file inside the tree, without escaping it."""
    try:
        root = source_root.resolve()
        candidate = (root / relative).resolve()
    except (OSError, ValueError):
        return False
    return candidate.is_file() and root in candidate.parents


def _file_line_counts(source_root: Path, files: set[str]) -> dict[str, int]:
    """Line count per cited file, so the last definition in each one gets a real span.

    Bounded by the same size limit the outline reader uses; an unreadable or oversized file simply
    contributes nothing and its last definition keeps the single-line fallback.
    """
    counts: dict[str, int] = {}
    for relative in files:
        path = source_root / relative
        try:
            if path.stat().st_size > _OUTLINE_MAX_BYTES * 8:
                continue
            with path.open("rb") as handle:
                counts[relative] = sum(1 for _ in handle)
        except OSError:
            continue
    return counts


# --------------------------------------------------------------------------------------------
# Supporting-evidence outlines
# --------------------------------------------------------------------------------------------


def safe_file_outline(path: Path) -> list[str]:
    """A short, redacted outline of a documentation or deployment file.

    Ported unchanged in behaviour from the deleted `extract_python._safe_file_outline`, because
    `llm/prompt.py` builds `declared_documentation` — the input `elevate` writes the product
    purpose from — by scanning FILE entities for this attribute. Losing it would leave the top
    tier with nothing but area names to work from.

    It was always language-agnostic; only its home was Python-specific.
    """
    name = path.name.casefold()
    suffix = path.suffix.casefold()
    relevant = (
        name == "dockerfile"
        or (name.startswith(("docker-compose", "compose")) and suffix in {".yml", ".yaml"})
        or (name.startswith("requirements") and suffix == ".txt")
        or name in {"pyproject.toml", "package.json", "setup.py", "makefile", "cargo.toml",
                    "go.mod", "pom.xml", "build.gradle", "gemfile", "composer.json"}
        or (name.startswith("readme") and suffix in {".md", ".rst", ".txt"})
    )
    if not relevant or name.startswith(".env"):
        return []
    try:
        if path.stat().st_size > _OUTLINE_MAX_BYTES:
            return []
        lines = path.read_text(encoding="utf-8-sig").splitlines()
    except (OSError, UnicodeDecodeError):
        return []
    outline: list[str] = []
    is_readme = name.startswith("readme")
    for number, raw in enumerate(lines, start=1):
        text = raw.strip()
        if not text or (text.startswith("#") and not is_readme):
            continue
        if secret := _SECRET.match(text):
            text = secret.group(1) + "<redacted>"
        outline.append(f"L{number}: {text[:_OUTLINE_LINE_CHARS]}")
        if len(outline) >= _OUTLINE_MAX_LINES:
            break
    return outline


# --------------------------------------------------------------------------------------------
# Capability 1 — run the binary
# --------------------------------------------------------------------------------------------


def find_executable(configured: str = "") -> str | None:
    """Locate the `graphify` binary.

    The venv-sibling fallback matters on Windows, where a `pip install` into a virtual environment
    puts `graphify.exe` beside `python.exe` in `Scripts/` without that directory necessarily being
    on PATH for a subprocess.
    """
    if configured:
        return configured if Path(configured).is_file() else None
    if found := shutil.which("graphify"):
        return found
    sibling = Path(sys.executable).with_name(
        "graphify.exe" if sys.platform == "win32" else "graphify"
    )
    return str(sibling) if sibling.is_file() else None


@capability("graphify_extract")
def graphify_extract(ctx: CapabilityContext) -> CapabilityResult:
    """Run `graphify extract` over the source root.

    Unlike the optional enrichment adapter this replaces, a failure here **halts the run**. Under
    a full swap Graphify is the only extractor, so continuing past a failed extraction would
    publish a document built on nothing and report it as success.
    """
    settings = ctx.settings
    executable = find_executable(getattr(settings, "graphify_binary", "") or "")
    if executable is None:
        return CapabilityResult(
            halt_reason=(
                "The `graphify` executable was not found. Install it with "
                '`pip install "graphifyy[sql]"`, or set GRAPHIFY_BINARY to its full path.'
            )
        )

    # Absolute, always. `Settings.runs_dir` defaults to the relative `Path("runs")`, so a run
    # started from the app carries a relative `source_root` — and passing that as the argument
    # while also setting `cwd` to it makes the child join the two, producing
    # `<root>/runs/<id>/source/runs/<id>/source` and a "path not found". Tests never saw it
    # because they pass an absolute `runs_dir`.
    source_root = ctx.source_root.resolve()
    output_dir = (ctx.run_dir / "graphify").resolve()
    command = [
        executable,
        "extract",
        str(source_root),
        # Structure only: local tree-sitter, zero API calls, no second LLM vendor, and nothing
        # about the run stops being reproducible. Doc/PDF concept extraction is a later increment
        # routed through this project's own Azure client.
        "--code-only",
        # Clustering is Graphify's own Leiden community pass. This pipeline partitions the graph
        # itself, around interaction boundaries, in `build_boundary_clusters` — two different
        # partitions would compete, and only one of them feeds the evidence packets.
        "--no-cluster",
        # An uploaded snapshot frequently carries a .gitignore written for a different checkout
        # layout, which would silently exclude the source under analysis.
        "--no-gitignore",
        "--out",
        str(output_dir),
    ]

    try:
        completed = subprocess.run(
            command,
            cwd=source_root,
            capture_output=True,
            text=True,
            timeout=getattr(settings, "graphify_timeout_seconds", 600),
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return CapabilityResult(halt_reason=f"`graphify extract` could not be run: {exc}")

    if completed.returncode != 0:
        return CapabilityResult(
            halt_reason=(
                f"`graphify extract` exited {completed.returncode}: "
                f"{(completed.stderr or completed.stdout or '')[-2000:]}"
            )
        )

    candidates = [output_dir / "graphify-out" / "graph.json", output_dir / "graph.json"]
    output = next((path for path in candidates if path.exists()), None)
    if output is None:
        return CapabilityResult(
            halt_reason=(
                "`graphify extract` reported success but wrote no graph.json under "
                f"{output_dir}. stdout: {(completed.stdout or '')[-1000:]}"
            )
        )

    return CapabilityResult(
        artifacts={
            "graphify_raw": {
                "graph_path": str(output),
                "command": command[1:],
                "stdout": (completed.stdout or "")[-2000:],
                "stderr": (completed.stderr or "")[-2000:],
            }
        },
        metrics={"graphify_output": str(output)},
        messages=[f"Graphify wrote {output}"],
    )


# --------------------------------------------------------------------------------------------
# Capability 2 — normalise into the canonical schema
# --------------------------------------------------------------------------------------------


class AdapterError(RuntimeError):
    """The adapter produced a structurally invalid graph.

    Raised rather than reported, because every condition it covers — a duplicate id, an edge with
    no endpoint — is a defect in this module and not a finding about the codebase under analysis.
    Gate 1 used to check for these; it no longer needs to, since one adapter now mints every id.
    """


@capability("adapt_graphify_graph")
def adapt_graphify_graph(ctx: CapabilityContext) -> CapabilityResult:
    """Turn Graphify's `graph.json` into a `KnowledgeGraph`."""
    inventory = ctx.load("inventory", RepositoryInventory)
    raw_record = ctx.load("graphify_raw")
    payload = json.loads(Path(raw_record["graph_path"]).read_text(encoding="utf-8"))

    nodes: list[dict[str, Any]] = payload.get("nodes") or payload.get("entities") or []
    edges: list[dict[str, Any]] = (
        payload.get("edges") or payload.get("links") or payload.get("relationships") or []
    )

    builder = GraphBuilder(inventory.repository_name)
    cited_files = {
        parsed[0] for raw in nodes if (parsed := _parse_location(raw)) is not None
    }
    file_lines = _file_line_counts(ctx.source_root, cited_files)
    line_ends = _derive_line_ends(nodes, edges, file_lines)
    language_of = {record.path: record.language for record in inventory.files}

    id_map: dict[str, str] = {}
    kind_counts: dict[str, int] = defaultdict(int)
    files_with_nodes: set[str] = set()
    files_with_file_entity: set[str] = set()
    seen_files: set[str] = set()
    unresolved_files: set[str] = set()

    for raw in nodes:
        external_id = str(raw.get("id") or raw.get("key") or raw.get("label") or "")
        if not external_id:
            continue
        kind = classify_kind(raw)
        qualified_name = _qualified_name(raw, kind)
        entity_id = stable_id("gf", external_id)

        location = None
        parsed = _parse_location(raw)
        if parsed is not None and not _resolves(ctx.source_root, parsed[0]):
            # Graphify sometimes attributes a node to a name that is not a path in this tree — a
            # bare package name from a config file, say. It cites nowhere, which is exactly what
            # EXTERNAL_SYMBOL means; emitting it with a location instead produced an entity whose
            # own citation Gate 1 then rejected, failing the run on the extractor's own output.
            unresolved_files.add(parsed[0])
            parsed = None
            kind = "EXTERNAL_SYMBOL"
            qualified_name = _display_name(raw, kind) or qualified_name
        if parsed is not None:
            source_file, line_start = parsed
            seen_files.add(source_file)
            if kind == "FILE":
                files_with_file_entity.add(source_file)
            else:
                files_with_nodes.add(source_file)
            location = SourceLocation(
                file=source_file,
                line_start=line_start,
                # A FILE spans the whole file. Derived ends stop at its last *contained* node,
                # which under-reports whenever a file ends in something Graphify links no other
                # way — a trailing comment block, say — so the real length wins where it is known.
                # An empty file reports zero lines, so the start line is the floor: a span may
                # never end before it begins.
                line_end=(
                    max(file_lines.get(source_file, 0), line_start or 0) or None
                    if kind == "FILE"
                    else line_ends.get(external_id, line_start)
                ),
                # `symbol` asserts *a name that appears at these lines*, and verification re-reads
                # the file to confirm it. Two kinds must not make that assertion: a FILE, whose
                # label is a basename that does not appear on line 1, and a RATIONALE, whose label
                # is a prose sentence rather than an identifier. Both would fail
                # `verify_location`'s symbol check on every instance.
                symbol=_verifiable_symbol(kind, str(raw.get("label") or "")),
            )

        attributes: dict[str, Any] = {"graphify_id": external_id, "origin": raw.get("_origin")}
        if location is not None and location.line_start is not None:
            attributes["line_end_estimated"] = True
        if kind == "FILE" and parsed is not None:
            if outline := safe_file_outline(ctx.source_root / parsed[0]):
                attributes["content_outline"] = outline

        id_map[external_id] = entity_id
        kind_counts[kind] += 1
        builder.add_entity(
            Entity(
                id=entity_id,
                kind=kind,
                name=_display_name(raw, kind) or external_id,
                qualified_name=qualified_name,
                language=language_of.get(parsed[0]) if parsed else None,
                location=location,
                provenance=(
                    Provenance.EXTRACTED if kind != "RATIONALE" else Provenance.INFERRED
                ),
                confidence=1.0 if kind != "RATIONALE" else 0.7,
                attributes=attributes,
            )
        )

    # Every inventoried file gets a FILE entity, including one Graphify produced no nodes for.
    # Two reasons, and both were regressions when this was left out:
    #
    # - `--code-only` skips documentation entirely, so a README is absent from the payload. It is
    #   still the clearest statement a project makes about its own purpose, it is still where
    #   `declared_documentation` comes from, and a requirement citing it was being rejected as
    #   citing nothing.
    # - A file in a language Graphify cannot parse would otherwise vanish from the graph rather
    #   than appear as something inventoried and not analysed, which is the honest report.
    for record in inventory.files:
        if record.path in files_with_file_entity:
            continue
        entity_id = stable_id("file", record.path)
        if entity_id in id_map.values():
            continue
        attributes = {"inventoried_only": True, "extension": record.extension}
        if outline := safe_file_outline(ctx.source_root / record.path):
            attributes["content_outline"] = outline
        seen_files.add(record.path)
        kind_counts["FILE"] += 1
        builder.add_entity(
            Entity(
                id=entity_id,
                kind="FILE",
                name=Path(record.path).name,
                qualified_name=record.path,
                language=record.language,
                location=SourceLocation(
                    file=record.path,
                    line_start=1,
                    line_end=max(_file_line_counts(ctx.source_root, {record.path}).get(record.path, 1), 1),
                ),
                attributes=attributes,
            )
        )

    relation_counts: dict[str, int] = defaultdict(int)
    dropped_edges = 0
    for edge in edges:
        source = id_map.get(str(edge.get("source") or edge.get("from") or ""))
        target = id_map.get(str(edge.get("target") or edge.get("to") or ""))
        if not source or not target:
            # An endpoint Graphify referenced but never emitted as a node. Nothing can be cited
            # about it, so the edge is counted and dropped rather than repaired.
            dropped_edges += 1
            continue
        relation = str(edge.get("relation") or edge.get("kind") or edge.get("type") or "").lower()
        kind = RELATION_MAP.get(relation, DEFAULT_RELATION)
        provenance = _PROVENANCE_BY_LABEL.get(
            str(edge.get("confidence") or "").upper(), Provenance.INFERRED
        )
        evidence = []
        if (parsed := _parse_location(edge)) is not None:
            evidence = [
                SourceLocation(file=parsed[0], line_start=parsed[1], line_end=parsed[1])
            ]
        relation_counts[kind] += 1
        builder.relate(
            source,
            target,
            kind,
            provenance=provenance,
            confidence=_CONFIDENCE_BY_PROVENANCE[provenance],
            evidence=evidence,
            attributes={"graphify_relation": relation},
        )

    surface_count = _add_module_surface(
        builder, ctx.source_root, inventory, nodes, line_ends, id_map
    )
    kind_counts["MODULE_SURFACE"] = surface_count

    graph = builder.build()
    _assert_structurally_sound(graph)

    coverage = len(files_with_nodes) / len(seen_files) if seen_files else 0.0
    graph.metadata["extraction"] = {
        "extractor": "graphify",
        "files_seen": len(seen_files),
        "files_with_nodes": len(files_with_nodes),
        # Named to sit where `parse_coverage` did, and read the same way: a 0-1 share reported by
        # the gate, the prompt summary, and the gap analysis.
        "extraction_coverage": round(coverage, 4),
        "languages": dict(sorted(inventory.languages.items())),
        "entity_kinds": dict(sorted(kind_counts.items())),
        "relationship_kinds": dict(sorted(relation_counts.items())),
        "dropped_edges": dropped_edges,
        "unresolved_cited_files": sorted(unresolved_files)[:20],
        "command": raw_record.get("command"),
    }

    return CapabilityResult(
        artifacts={"graph": graph},
        metrics={
            "entities": len(graph.entities),
            "relationships": len(graph.relationships),
            "extraction_coverage": round(coverage, 4),
            "dropped_edges": dropped_edges,
        },
    )


def _add_module_surface(
    builder: GraphBuilder,
    source_root: Path,
    inventory: RepositoryInventory,
    nodes: list[dict[str, Any]],
    line_ends: dict[str, int],
    id_map: dict[str, str],
) -> int:
    """Add a node for every module-level interaction point, and wire it to the work it sets off.

    Runs after the payload has been mapped, because "module level" is defined against the
    definitions the payload gave: a line is module level when it falls outside all of them.
    """
    definitions: dict[str, list[int]] = defaultdict(list)
    local_names: dict[str, set[str]] = defaultdict(set)
    by_bare_name: dict[str, str] = {}
    for raw in nodes:
        kind = classify_kind(raw)
        parsed = _parse_location(raw)
        if parsed is None or parsed[1] is None:
            continue
        source_file, line_start = parsed
        if kind in {"FUNCTION", "METHOD", "CLASS"}:
            definitions[source_file].append(line_start)
            bare = str(raw.get("label") or "").strip(". ()")
            mapped = id_map.get(str(raw.get("id")))
            if bare and mapped:
                local_names[source_file].add(bare)
                by_bare_name.setdefault(bare, mapped)

    parseable = {
        record.path for record in inventory.files if record.parseable
    }
    added = 0
    for relative in sorted(parseable):
        covered = _tight_definition_spans(source_root, relative, definitions.get(relative, []))
        surface = _module_surface(source_root, relative, covered, local_names.get(relative, set()))
        if not surface:
            continue
        file_id = stable_id("file", relative)
        lines_of = [line for line, _, _ in surface]
        for index, (line, call, literals) in enumerate(surface):
            entity_id = stable_id("surface", relative, line, call)
            builder.add_entity(
                Entity(
                    id=entity_id,
                    kind="MODULE_SURFACE",
                    name=call,
                    qualified_name=f"{relative}::{call}@{line}",
                    language=None,
                    location=SourceLocation(
                        file=relative,
                        line_start=line,
                        line_end=line,
                        # The call's own last identifier is on this line, so the citation is
                        # verifiable in the ordinary way.
                        symbol=call.rsplit(".", 1)[-1],
                    ),
                    provenance=Provenance.EXTRACTED,
                    confidence=0.8,
                    attributes={"call": call, "literals": literals, "module_level": True},
                )
            )
            added += 1
            if any(entity.id == file_id for entity in builder.entities):
                builder.relate(file_id, entity_id, "CONTAINS")

            # The work this control sets off: functions named in the block below it, up to the
            # next control or a bounded window. Without this the boundary is a single declaration
            # node and the requirement written from it rests on nothing.
            following = [other for other in lines_of if other > line]
            stop = min(
                following[0] if following else line + SURFACE_WORKFLOW_WINDOW,
                line + SURFACE_WORKFLOW_WINDOW,
            )
            for name in _names_between(source_root, relative, line, stop):
                target = by_bare_name.get(name)
                if target is not None and target != entity_id:
                    builder.relate(
                        entity_id,
                        target,
                        "CALLS",
                        provenance=Provenance.INFERRED,
                        confidence=0.6,
                        evidence=[SourceLocation(file=relative, line_start=line, line_end=line)],
                    )
    return added


def _tight_definition_spans(
    source_root: Path, relative: str, starts: list[int]
) -> list[tuple[int, int]]:
    """Where each definition really ends, for deciding what is module level.

    The spans on the entities themselves are a deliberate over-approximation — bounded by the next
    definition, and running to the end of the file for the last one — which is right for a citation
    but wrong here: it makes every line below the final `def` look as though it sits inside it, and
    a Streamlit script keeps its whole interaction surface there.

    This walks the block instead: a definition ends at the first following non-blank line indented
    no further than the definition's own line. That is the block rule for an indentation language,
    and for a brace language it simply agrees with the sibling bound, since such a language has no
    module-level executable statements to find.
    """
    try:
        lines = (source_root / relative).read_text(
            encoding="utf-8-sig", errors="ignore"
        ).splitlines()
    except OSError:
        return []

    spans: list[tuple[int, int]] = []
    for start in starts:
        if start < 1 or start > len(lines):
            continue
        header = lines[start - 1]
        indent = len(header) - len(header.lstrip())
        end = len(lines)
        for number in range(start + 1, len(lines) + 1):
            text = lines[number - 1]
            if not text.strip():
                continue
            if len(text) - len(text.lstrip()) <= indent:
                end = number - 1
                break
        spans.append((start, max(end, start)))
    return spans


def _names_between(source_root: Path, relative: str, start: int, end: int) -> set[str]:
    """Identifiers written in a line range, for matching against known definitions."""
    try:
        lines = (source_root / relative).read_text(
            encoding="utf-8-sig", errors="ignore"
        ).splitlines()
    except OSError:
        return set()
    found: set[str] = set()
    for text in lines[start : min(end, len(lines))]:
        found.update(_CALL.findall(text))
    return {name.rsplit(".", 1)[-1] for name in found}


def _assert_structurally_sound(graph: KnowledgeGraph) -> None:
    """Invariants this module owns, checked here so no workflow gate has to.

    A duplicate id or a dangling edge cannot be caused by the repository under analysis: ids are
    content-addressed by `stable_id` and edges are built from `id_map`, so either failure is a bug
    in the code above.
    """
    entity_ids = [entity.id for entity in graph.entities]
    if len(entity_ids) != len(set(entity_ids)):
        raise AdapterError("Adapter minted duplicate entity ids")
    known = set(entity_ids)
    for relationship in graph.relationships:
        if relationship.source not in known or relationship.target not in known:
            raise AdapterError(
                f"Adapter produced a dangling relationship {relationship.id!r}"
            )

"""Evidence verification against the real file system.

Checking that a cited *file* exists is not enough. A fabricated line number inside a real file
still looks plausible in a traceability matrix, so these helpers open the file and confirm the
line range is in bounds and that a cited symbol actually appears near the cited location.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path, PurePosixPath

from agentic.schemas.core import SourceLocation

# How far from the cited line a symbol may appear and still count as corroborating it. Line numbers
# drift by a few lines when a model paraphrases a decorated definition's span, so an exact match
# would reject sound citations.
SYMBOL_WINDOW = 25


def _names_the_cited_file(symbol: str, file: str) -> bool:
    """True when the symbol is the cited path, whole or as a trailing run of its segments."""
    if "/" in symbol or "\\" in symbol:
        return True
    parts = PurePosixPath(symbol.replace("\\", "/")).parts
    file_parts = PurePosixPath(file.replace("\\", "/")).parts
    return bool(parts) and len(parts) <= len(file_parts) and file_parts[-len(parts) :] == parts

# Nodes the extractor synthesises for constructs the source does not name, identified by the line
# they occupy: `app.index_pdf.rule@41`, `app.index_pdf.error_handler@53`.
SYNTHETIC_NODE = re.compile(r"(?:rule|error_handler|dataop)@(\d+)$")


@dataclass(frozen=True)
class EvidenceCheck:
    ok: bool
    code: str = ""
    message: str = ""


def resolve_within(location: SourceLocation, source_root: Path) -> Path | None:
    """Resolve a cited path inside the source root, or None if it escapes or does not exist."""
    root = source_root.resolve()
    try:
        candidate = (root / location.file).resolve()
    except (OSError, ValueError):
        return None
    if root != candidate and root not in candidate.parents:
        return None
    return candidate if candidate.is_file() else None


@lru_cache(maxsize=512)
def _read_lines(path: str) -> tuple[str, ...]:
    try:
        return tuple(Path(path).read_text(encoding="utf-8-sig").splitlines())
    except (OSError, UnicodeDecodeError):
        return ()


def verify_location(location: SourceLocation, source_root: Path) -> EvidenceCheck:
    """Confirm a citation points at something that actually exists and says what is claimed."""
    path = resolve_within(location, source_root)
    if path is None:
        return EvidenceCheck(
            False,
            "INVALID_EVIDENCE_FILE",
            f"cited file does not resolve inside the codebase: {location.file}",
        )

    if location.line_start is None:
        return EvidenceCheck(True)

    if location.line_start < 1:
        return EvidenceCheck(
            False, "INVALID_EVIDENCE_LINE", f"cited line must be positive: {location.line_start}"
        )

    lines = _read_lines(str(path))
    if not lines:
        # An unreadable or empty file cannot corroborate a line citation, but the file does exist,
        # so this is a soft pass: the graph gate already reports unreadable files.
        return EvidenceCheck(True)

    if location.line_start > len(lines):
        return EvidenceCheck(
            False,
            "INVALID_EVIDENCE_LINE",
            f"cited line {location.line_start} is past the end of {location.file} "
            f"({len(lines)} lines)",
        )
    if location.line_end is not None and location.line_end < location.line_start:
        return EvidenceCheck(
            False,
            "INVALID_EVIDENCE_LINE",
            f"cited range {location.line_start}-{location.line_end} in {location.file} is inverted",
        )

    if location.symbol and (synthetic := SYNTHETIC_NODE.search(location.symbol)):
        # A construct with no name of its own — a branch, an exception handler — is named by the
        # extractor after the line it sits on. Its identity *is* that line, so hunting the file
        # for the token "rule@41" would reject the one citation shape that is certain to be
        # accurate. Check the line agrees instead.
        declared = int(synthetic.group(1))
        if abs(declared - location.line_start) > SYMBOL_WINDOW:
            return EvidenceCheck(
                False,
                "EVIDENCE_SYMBOL_MISMATCH",
                f"cited construct {location.symbol!r} is recorded at line {declared}, not near "
                f"line {location.line_start} of {location.file}",
            )
        return EvidenceCheck(True)

    if location.symbol:
        # Only `symbol` promises to appear at these lines. `location.scope` names the enclosing
        # definition, whose header can be arbitrarily far above, so it is deliberately not checked
        # here — doing so rejects every honest citation inside a function longer than the window.
        # A qualified name is cited but the file holds only the final component. Qualified names
        # are `path/to/file.py::name()` now, so the file part is split off at `::` before the
        # dotted-path rule is applied — otherwise the whole citation is hunted for as one token
        # and every correctly cited definition is rejected. Trailing `()` is the extractor's
        # notation for a callable and is not in the source either.
        needle = location.symbol.rsplit("::", 1)[-1]
        # A symbol naming the file it cites asserts nothing about a line: a path is not written
        # inside the file, and the dotted rule below would reduce `index.ts` to `ts` and then
        # report a sound citation as fabricated. Asked exactly — is this the cited path? — rather
        # than by guessing at extensions, since `index.ts` and `far.owner` are indistinguishable
        # by suffix.
        if _names_the_cited_file(needle, location.file):
            return EvidenceCheck(True)
        needle = needle.rsplit(".", 1)[-1].split("!", 1)[0].strip().removesuffix("()").strip()
        if needle and not _symbol_near(lines, location.line_start, needle):
            return EvidenceCheck(
                False,
                "EVIDENCE_SYMBOL_MISMATCH",
                f"symbol {needle!r} does not appear near line {location.line_start} of "
                f"{location.file}",
            )
    return EvidenceCheck(True)


def _symbol_near(lines: tuple[str, ...], line: int, symbol: str) -> bool:
    start = max(0, line - 1 - SYMBOL_WINDOW)
    end = min(len(lines), line + SYMBOL_WINDOW)
    return any(symbol in text for text in lines[start:end])


def read_slice(
    source_root: Path, location: SourceLocation, context: int = 2
) -> tuple[int, list[str]]:
    """Return the cited source lines plus a little context, for display in the UI.

    This is what lets a reviewer confirm a requirement against the code without leaving the app —
    the single most convincing demonstration that a claim is grounded.
    """
    path = resolve_within(location, source_root)
    if path is None:
        return 0, []
    lines = _read_lines(str(path))
    if not lines:
        return 0, []
    start = max(1, (location.line_start or 1) - context)
    end = min(len(lines), (location.line_end or location.line_start or 1) + context)
    return start, list(lines[start - 1 : end])

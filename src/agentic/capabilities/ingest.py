"""Phase 0 — safe ingestion and inventory.

Target code is read and parsed, never executed or imported. Archives are validated before a single
byte is written to disk: path traversal, absolute and drive-qualified paths, symlinks, encrypted
entries, and implausible compression ratios are all rejected up front.
"""

from __future__ import annotations

import hashlib
import zipfile
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import BinaryIO

from agentic.capabilities.registry import CapabilityContext, CapabilityResult, capability
from agentic.config import Settings
from agentic.schemas.core import InventoryFile, RepositoryInventory

LANGUAGES = {
    ".py": "Python",
    ".js": "JavaScript",
    ".jsx": "JavaScript",
    ".ts": "TypeScript",
    ".tsx": "TypeScript",
    ".java": "Java",
    ".kt": "Kotlin",
    ".go": "Go",
    ".rs": "Rust",
    ".cs": "C#",
    ".cpp": "C++",
    ".cc": "C++",
    ".c": "C",
    ".h": "C/C++ Header",
    ".rb": "Ruby",
    ".php": "PHP",
    ".sql": "SQL",
    ".yaml": "YAML",
    ".yml": "YAML",
    ".json": "JSON",
    ".toml": "TOML",
    ".xml": "XML",
    ".md": "Markdown",
    ".txt": "Text",
    ".cfg": "Config",
    ".ini": "Config",
}

# Version control, editor state, dependency trees, and build output are not source evidence.
IGNORED_DIRECTORIES = {
    ".git", ".hg", ".svn", ".idea", ".vscode", "__pycache__", "node_modules", "vendor",
    "dist", "build", "target", ".venv", "venv", ".tox", ".mypy_cache", ".pytest_cache",
    ".ruff_cache", "site-packages", ".next", ".nuxt", "coverage", ".gradle",
}

# The deep-analysis path: the languages Graphify has a tree-sitter grammar for. This used to be
# `{"Python"}`, which is the single constraint the Graphify swap existed to remove — every other
# language reached the graph as a bare FILE entity and produced no requirement at all.
#
# SQL is here but needs the `graphifyy[sql]` extra installed; without it `.sql` files contribute
# nothing and the coverage metric says so.
PARSEABLE_LANGUAGES = {
    "Python", "JavaScript", "TypeScript", "Java", "Kotlin", "Go", "Rust", "C#", "C++", "C",
    "C/C++ Header", "Ruby", "PHP", "SQL",
}

MAX_HASHED_BYTES = 8 * 1024 * 1024


class ArchiveValidationError(ValueError):
    """An archive was rejected before extraction."""


@dataclass
class ExtractionSummary:
    file_count: int = 0
    compressed_bytes: int = 0
    uncompressed_bytes: int = 0
    warnings: list[str] = field(default_factory=list)


def _safe_member_path(name: str) -> PurePosixPath:
    normalised = name.replace("\\", "/")
    member = PurePosixPath(normalised)
    if member.is_absolute():
        raise ArchiveValidationError(f"Archive contains an absolute path: {name!r}")
    if any(part == ".." for part in member.parts):
        raise ArchiveValidationError(f"Archive contains path traversal: {name!r}")
    if member.parts and ":" in member.parts[0]:
        raise ArchiveValidationError(f"Archive contains a drive-qualified path: {name!r}")
    return member


def _is_symlink(info: zipfile.ZipInfo) -> bool:
    # The high 16 bits of external_attr carry the Unix mode; 0xA000 is S_IFLNK.
    return (info.external_attr >> 16) & 0xF000 == 0xA000


def extract_zip_safely(
    stream: BinaryIO, destination: Path, settings: Settings
) -> ExtractionSummary:
    """Validate then extract an uploaded archive into `destination`.

    Every check runs across the whole member list before anything is written, so a malicious entry
    late in the archive cannot leave a partially-extracted tree behind.
    """
    destination.mkdir(parents=True, exist_ok=True)
    resolved_root = destination.resolve()
    summary = ExtractionSummary()

    try:
        stream.seek(0, 2)
        size = stream.tell()
        stream.seek(0)
    except (OSError, AttributeError) as exc:
        raise ArchiveValidationError("Archive stream must be seekable") from exc
    if size > settings.max_archive_bytes:
        raise ArchiveValidationError(
            f"Archive is {size} bytes, above the {settings.max_archive_bytes} byte limit"
        )

    try:
        archive = zipfile.ZipFile(stream)
    except zipfile.BadZipFile as exc:
        raise ArchiveValidationError("Upload is not a valid ZIP archive") from exc

    with archive:
        members = [info for info in archive.infolist() if not info.is_dir()]
        if len(members) > settings.max_archive_files:
            raise ArchiveValidationError(
                f"Archive holds {len(members)} files, above the "
                f"{settings.max_archive_files} file limit"
            )

        seen: set[str] = set()
        total_uncompressed = 0
        for info in members:
            member = _safe_member_path(info.filename)
            key = str(member).casefold()
            if key in seen:
                raise ArchiveValidationError(f"Duplicate archive path: {info.filename!r}")
            seen.add(key)
            if info.flag_bits & 0x1:
                raise ArchiveValidationError("Encrypted archives are not supported")
            if _is_symlink(info):
                raise ArchiveValidationError(f"Symbolic links are not allowed: {info.filename}")
            if info.compress_size > 0:
                ratio = info.file_size / info.compress_size
                if ratio > settings.max_compression_ratio:
                    raise ArchiveValidationError(
                        f"Archive entry {info.filename!r} expands {ratio:.0f}x, above the "
                        f"{settings.max_compression_ratio:.0f}x limit"
                    )
            total_uncompressed += info.file_size
            if total_uncompressed > settings.max_uncompressed_bytes:
                raise ArchiveValidationError("Archive expands beyond the configured limit")

        for info in members:
            member = _safe_member_path(info.filename)
            target = (destination / member).resolve()
            if resolved_root not in target.parents:
                raise ArchiveValidationError(f"Unsafe extraction target: {info.filename!r}")
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(info) as source, target.open("wb") as handle:
                handle.write(source.read())
            summary.file_count += 1
            summary.compressed_bytes += info.compress_size
            summary.uncompressed_bytes += info.file_size

    if summary.file_count == 0:
        raise ArchiveValidationError("Archive contains no files")
    return summary


def build_inventory(root: Path, repository_name: str) -> RepositoryInventory:
    """Walk the tree once, classifying every file by language."""
    records: list[InventoryFile] = []
    language_counts: Counter[str] = Counter()
    total_bytes = 0

    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.is_symlink():
            continue
        relative = path.relative_to(root)
        if any(part in IGNORED_DIRECTORIES for part in relative.parts):
            continue
        try:
            size = path.stat().st_size
            # Hashing a very large binary buys nothing and costs a full read; the path and size
            # already identify it well enough for an inventory.
            digest = (
                hashlib.sha256(path.read_bytes()).hexdigest()
                if size <= MAX_HASHED_BYTES
                else f"skipped:{size}"
            )
        except OSError:
            continue
        extension = path.suffix.lower()
        language = LANGUAGES.get(extension, "Other")
        records.append(
            InventoryFile(
                path=relative.as_posix(),
                extension=extension,
                language=language,
                size=size,
                sha256=digest,
                parseable=language in PARSEABLE_LANGUAGES,
            )
        )
        language_counts[language] += 1
        total_bytes += size

    return RepositoryInventory(
        repository_name=repository_name,
        files=records,
        languages=dict(sorted(language_counts.items())),
        total_bytes=total_bytes,
    )


@capability("ingest_repository")
def ingest_repository(ctx: CapabilityContext) -> CapabilityResult:
    """Inventory the target tree, and stop the run if there is nothing worth analysing.

    Archive extraction happens in `runtime.py` before the graph starts, because it produces the
    `source_root` the whole run is built around. This step covers the rest of Phase 0.
    """
    root = ctx.source_root
    if not root.is_dir():
        return CapabilityResult(halt_reason=f"Source path is not a directory: {root}")

    inventory = build_inventory(root, ctx.state.get("repository_name", root.name))

    if not inventory.files:
        return CapabilityResult(
            halt_reason=(
                "No files were found to analyse. The path may be empty, or everything in it was "
                "skipped as build output or dependencies."
            )
        )

    parseable = [record for record in inventory.files if record.parseable]
    messages: list[str] = []
    if not parseable:
        # Nothing here is in a language the extractor can parse. Fail closed: inventing
        # requirements from file names is exactly the failure this gate prevents.
        #
        # This is a far narrower door than it was. It used to close on any repository without
        # Python in it; it now closes only on one with no source in any of the 36 languages
        # Graphify parses, which in practice means documentation, data, or assets alone.
        languages = ", ".join(f"{name} ({count})" for name, count in inventory.languages.items())
        return CapabilityResult(
            artifacts={"inventory": inventory},
            metrics={"files": len(inventory.files), "parseable_files": 0},
            halt_reason=(
                "No source was found in any language this workflow can parse. Files present: "
                f"{languages}. Analysing this repository would mean guessing from file names, "
                "which this workflow refuses to do."
            ),
        )

    inventory_only = len(inventory.files) - len(parseable)
    if inventory_only:
        messages.append(
            f"{inventory_only} file(s) are not Python and received inventory-level analysis only."
        )

    return CapabilityResult(
        artifacts={"inventory": inventory},
        metrics={
            "files": len(inventory.files),
            "parseable_files": len(parseable),
            "languages": inventory.languages,
            "total_bytes": inventory.total_bytes,
        },
        messages=messages,
    )

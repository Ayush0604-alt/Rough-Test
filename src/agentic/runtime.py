"""Run a pack against a target codebase.

Preparation that must happen before any state exists — resolving the target into a directory,
creating the run directory — lives here. Everything after that is the compiled graph.
"""

from __future__ import annotations

import re
import shutil
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO, Iterator
from uuid import uuid4

from agentic.capabilities.ingest import extract_zip_safely
from agentic.config import Settings, get_settings
from agentic.graph.builder import compile_graph
from agentic.skills.pack import PackManifest, discover_packs, load_pack
from agentic.state import AgentState, initial_state

DEFAULT_PACK_ROOT = Path(__file__).resolve().parents[2]


@dataclass
class PreparedRun:
    run_id: str
    run_dir: Path
    source_root: Path
    repository_name: str


def safe_name(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", Path(value or "codebase").stem).strip("-.")
    return cleaned or "codebase"


def available_packs(search_root: Path | str | None = None) -> dict[str, Path]:
    return discover_packs(Path(search_root) if search_root else DEFAULT_PACK_ROOT)


def get_manifest(pack: str, search_root: Path | str | None = None) -> PackManifest:
    packs = available_packs(search_root)
    directory = packs.get(pack)
    if directory is None:
        known = ", ".join(sorted(packs)) or "<none found>"
        raise KeyError(f"Unknown pack {pack!r}. Available packs: {known}")
    return load_pack(directory)


def successor_packs(
    pack: str, run_dir: Path | str, search_root: Path | str | None = None
) -> list[tuple[str, Path]]:
    """Packs that declare they consume `pack`'s output, and whose required artifact is present.

    The chaining rule lives here rather than in the UI so it is one generic mechanism rather than a
    hardcoded pair of pack names: a third pack declaring `consumes_pack` is offered automatically,
    and nothing upstream has to know it exists.

    Presence of the artifact is checked on purpose. A requirements run that halted before
    publishing anything must not be offered as an input to the pack that would read it.
    """
    artifacts = Path(run_dir) / "artifacts"
    found: list[tuple[str, Path]] = []
    for name, directory in sorted(available_packs(search_root).items()):
        try:
            manifest = load_pack(directory, validate_references=False)
        except Exception:
            # Broad on purpose. This walks *every* pack on disk to answer a question about one of
            # them, so a manifest that is unreadable, malformed YAML, or invalid against the schema
            # must cost only itself. A syntax error in an unrelated pack taking down the caller
            # would make the whole UI unusable.
            continue
        if manifest.consumes_pack != pack:
            continue
        required = manifest.consumes_artifact
        if required and not (artifacts / required).is_file():
            continue
        found.append((name, artifacts))
    return found


def prepare_run(
    *,
    source: Path | str | None = None,
    archive: BinaryIO | None = None,
    archive_name: str = "codebase.zip",
    settings: Settings | None = None,
) -> PreparedRun:
    """Create the run directory and resolve the target into a readable source tree."""
    settings = settings or get_settings()
    run_id = str(uuid4())
    run_dir = Path(settings.runs_dir) / run_id
    source_root = run_dir / "source"
    run_dir.mkdir(parents=True, exist_ok=True)

    if archive is not None:
        extract_zip_safely(archive, source_root, settings)
        repository_name = safe_name(archive_name)
    elif source is not None:
        origin = Path(source).expanduser().resolve()
        if not origin.is_dir():
            raise ValueError(f"Source path is not a directory: {origin}")
        # Analyse a snapshot rather than the live tree, so a run is reproducible and the target
        # cannot change under the analysis half-way through.
        shutil.copytree(origin, source_root, dirs_exist_ok=True, symlinks=False)
        repository_name = safe_name(origin.name)
    else:
        raise ValueError("prepare_run needs either a source directory or an archive")

    return PreparedRun(
        run_id=run_id,
        run_dir=run_dir,
        source_root=source_root,
        repository_name=repository_name,
    )


def preflight(manifest: PackManifest, settings: Settings) -> list[str]:
    """Problems that would stop the run, detected before any work is done.

    Discovering a missing credential only after ingestion, extraction, and validation have run
    wastes minutes and leaves a half-populated run directory behind.
    """
    problems: list[str] = []
    from agentic.skills.pack import StepMode

    model_steps = [step.id for step in manifest.steps if step.mode is StepMode.LLM]
    if model_steps and not settings.llm_configured:
        missing = ", ".join(settings.missing_llm_settings())
        problems.append(
            f"Pack {manifest.pack!r} has model steps ({', '.join(model_steps)}) but Azure OpenAI "
            f"is not fully configured. Missing: {missing}."
        )
    return problems


@contextmanager
def _checkpointer(settings: Settings) -> Iterator[Any]:
    """A SQLite checkpointer, so a Streamlit rerun resumes rather than restarts."""
    from langgraph.checkpoint.sqlite import SqliteSaver

    Path(settings.runs_dir).mkdir(parents=True, exist_ok=True)
    database = str(Path(settings.runs_dir) / "checkpoints.db")
    with SqliteSaver.from_conn_string(database) as saver:
        yield saver


def stream_run(
    pack: str,
    prepared: PreparedRun,
    *,
    settings: Settings | None = None,
    search_root: Path | str | None = None,
) -> Iterator[tuple[str, dict[str, Any]]]:
    """Execute a pack, yielding `(node_name, update)` as each step completes."""
    settings = settings or get_settings()
    manifest = get_manifest(pack, search_root)
    state = initial_state(
        run_id=prepared.run_id,
        pack=pack,
        run_dir=str(prepared.run_dir),
        source_root=str(prepared.source_root),
        repository_name=prepared.repository_name,
    )
    config = {"configurable": {"thread_id": prepared.run_id}, "recursion_limit": 60}

    with _checkpointer(settings) as saver:
        graph = compile_graph(manifest, settings, checkpointer=saver)
        for event in graph.stream(state, config=config, stream_mode="updates"):
            for node_name, update in event.items():
                yield node_name, update or {}


def run(
    pack: str,
    prepared: PreparedRun,
    *,
    settings: Settings | None = None,
    search_root: Path | str | None = None,
) -> AgentState:
    """Execute a pack to completion and return the final state."""
    settings = settings or get_settings()
    manifest = get_manifest(pack, search_root)
    state = initial_state(
        run_id=prepared.run_id,
        pack=pack,
        run_dir=str(prepared.run_dir),
        source_root=str(prepared.source_root),
        repository_name=prepared.repository_name,
    )
    config = {"configurable": {"thread_id": prepared.run_id}, "recursion_limit": 60}

    with _checkpointer(settings) as saver:
        graph = compile_graph(manifest, settings, checkpointer=saver)
        return graph.invoke(state, config=config)

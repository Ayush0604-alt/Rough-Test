"""Command-line entry point, for running a pack headlessly or inspecting one."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from agentic.config import get_settings
from agentic.graph.builder import describe_topology
from agentic.runtime import (
    available_packs,
    get_manifest,
    preflight,
    prepare_run,
    stream_run,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="agentic", description=__doc__)
    subcommands = parser.add_subparsers(dest="command", required=True)

    subcommands.add_parser("packs", help="List the available skill packs")

    show = subcommands.add_parser("show", help="Show a pack's compiled workflow")
    show.add_argument("pack")

    run_command = subcommands.add_parser("run", help="Run a pack against a codebase")
    run_command.add_argument("pack")
    run_command.add_argument(
        "source", help="Path to the target codebase, as a directory or a .zip archive"
    )

    args = parser.parse_args(argv)

    if args.command == "packs":
        for name, directory in sorted(available_packs().items()):
            print(f"{name}\t{directory}")
        return 0

    if args.command == "show":
        manifest = get_manifest(args.pack)
        print(f"{manifest.pack} v{manifest.version} - {manifest.description.strip()}\n")
        for row in describe_topology(manifest):
            print(
                f"  {row['step']:<20} {row['mode']:<14} -> {row['on_success']:<18}"
                + (f"  on failure: {row['on_failure']}" if row["on_failure"] else "")
                + (f"  fan-out over: {row['fan_out']}" if row["fan_out"] else "")
            )
        return 0

    settings = get_settings()
    manifest = get_manifest(args.pack)
    if problems := preflight(manifest, settings):
        for problem in problems:
            print(f"error: {problem}", file=sys.stderr)
        return 1

    # The UI has always accepted an archive; the command line accepting only a directory meant the
    # two entry points analysed different things from the same input.
    source_path = Path(args.source)
    if source_path.is_file() and source_path.suffix.lower() == ".zip":
        with source_path.open("rb") as archive:
            prepared = prepare_run(
                archive=archive, archive_name=source_path.name, settings=settings
            )
    else:
        prepared = prepare_run(source=args.source, settings=settings)
    print(f"Run {prepared.run_id} -> {prepared.run_dir}\n")

    final: dict = {}
    for node_name, update in stream_run(args.pack, prepared, settings=settings):
        for record in update.get("step_records") or []:
            note = "; ".join(record.get("messages") or [])
            print(
                f"  {record['status']:<22} {record['step']:<20} "
                f"{record['duration_ms']:>8.0f} ms  {note[:100]}"
            )
        final.update(update)

    status = final.get("status", "unknown")
    print(f"\nStatus: {status}")
    if final.get("halt_reason"):
        print(f"Stopped: {final['halt_reason']}")
        return 1
    print(f"Deliverables: {prepared.run_dir / 'artifacts'}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())

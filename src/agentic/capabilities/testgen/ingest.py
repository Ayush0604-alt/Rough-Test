"""Read the published requirements artifact, defensively.

This is the pack's only contact with the requirements pack's output, and the only step that reads
`source_root`. Everything downstream consumes `requirement_set`, which is why adding an optional
input later changes exactly one file.

`ingest_repository` from the requirements pack cannot be reused here: it halts with
`NO_ANALYSABLE_SOURCE` on a directory containing no parseable source, which is precisely what a
run's `artifacts/` folder is.

Nothing raises on bad data. Every problem becomes a `ReadIssue` carrying the array position, so a
malformed artifact produces a thinner set and an explanation rather than a stack trace.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from agentic.capabilities.registry import (
    CapabilityContext,
    CapabilityResult,
    capability,
)
from agentic.schemas.core import SourceLocation
from agentic.schemas.testgen import (
    CONTRACT_KEYS,
    PRIORITIES,
    IngestedRequirement,
    ReadIssue,
    RequirementSet,
)

REQUIREMENTS_FILE = "requirements.json"

# Optional siblings. Absent is normal and never an error — each is used only to sharpen a decision
# that has a defensible answer without it.
ENRICHMENT_FILES = {
    "unresolved_candidates": "unresolved_candidates.json",
    "synthesis_findings": "synthesis_findings.json",
}

# A directory holding this many files is a run artifacts folder; searching it is cheap. The bound
# exists so pointing the pack at a whole repository by mistake fails fast rather than walking it.
MAX_SEARCH_DEPTH = 4


def _find(root: Path, filename: str) -> Path | None:
    """Shallowest match wins, so a nested older run cannot shadow the one being pointed at."""
    candidates = [
        path
        for path in root.rglob(filename)
        if len(path.relative_to(root).parts) <= MAX_SEARCH_DEPTH
    ]
    if not candidates:
        return None
    return sorted(candidates, key=lambda p: (len(p.relative_to(root).parts), str(p)))[0]


def _as_str(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _as_optional_str(value: Any) -> str | None:
    text = _as_str(value)
    return text or None


def _as_str_list(value: Any) -> tuple[list[str], bool]:
    """Coerce to a list of non-empty strings. Returns (values, was_coerced).

    A bare string becomes a one-element list — that is an obvious intent, and the rich fixture
    shows the requirements pack emits scalars where a list is declared. Anything else yields an
    empty list and a recorded issue, because guessing further would invent content.
    """
    if value is None:
        return [], False
    if isinstance(value, str):
        text = value.strip()
        return ([text], True) if text else ([], True)
    if isinstance(value, list):
        return [item.strip() for item in value if isinstance(item, str) and item.strip()], False
    return [], True


def _as_float(value: Any) -> tuple[float | None, bool]:
    if value is None:
        return None, False
    if isinstance(value, bool):
        return None, True
    if isinstance(value, (int, float)):
        return float(value), False
    return None, True


def _as_evidence(value: Any) -> tuple[list[SourceLocation], bool]:
    """Evidence is a pointer, never a dependency — a bad one costs provenance, not a test."""
    entries = value if isinstance(value, list) else [value] if isinstance(value, dict) else None
    if entries is None:
        return [], value is not None
    found, coerced = [], not isinstance(value, list)
    for entry in entries:
        if not isinstance(entry, dict) or not _as_str(entry.get("file")):
            coerced = True
            continue
        try:
            found.append(SourceLocation.model_validate(entry))
        except Exception:
            coerced = True
    return found, coerced


def read_requirement(raw: Any, index: int) -> tuple[IngestedRequirement | None, list[ReadIssue]]:
    """One array entry into a requirement, or into the reasons it could not become one."""
    issues: list[ReadIssue] = []

    def note(code: str, message: str, requirement_id: str = "") -> None:
        issues.append(
            ReadIssue(code=code, message=message, source_index=index, requirement_id=requirement_id)
        )

    if not isinstance(raw, dict):
        note("ENTRY_NOT_AN_OBJECT", f"Entry is {type(raw).__name__}, not an object. Skipped.")
        return None, issues

    identifier = _as_str(raw.get("id"))
    if not identifier:
        note(
            "MISSING_IDENTIFIER",
            "Entry has no id, so nothing downstream could address, trace, or report it. Skipped.",
        )
        return None, issues

    criteria, criteria_coerced = _as_str_list(raw.get("acceptance_criteria"))
    if criteria_coerced:
        note("COERCED_FIELD", "`acceptance_criteria` was not a list of strings.", identifier)

    graph_path, path_coerced = _as_str_list(raw.get("graph_path"))
    assumptions, assumptions_coerced = _as_str_list(raw.get("assumptions"))
    questions, questions_coerced = _as_str_list(raw.get("open_questions"))
    superseded, superseded_coerced = _as_str_list(raw.get("superseded"))
    if any([path_coerced, assumptions_coerced, questions_coerced, superseded_coerced]):
        note("COERCED_FIELD", "A list-valued field held something other than a list.", identifier)

    evidence, evidence_coerced = _as_evidence(raw.get("evidence"))
    if evidence_coerced:
        note("COERCED_FIELD", "`evidence` was not a list of locations.", identifier)

    confidence, confidence_bad = _as_float(raw.get("confidence"))
    model_confidence, _ = _as_float(raw.get("model_confidence"))
    if confidence_bad:
        note("COERCED_FIELD", "`confidence` was not a number; treated as unstated.", identifier)

    # Checked against the raw value, not the stringified one: a non-string priority stringifies to
    # "" and would then be indistinguishable from an absent field, so the coercion would be applied
    # and never recorded.
    raw_priority = raw.get("priority")
    priority = _as_str(raw_priority).upper()
    if raw_priority is not None and priority not in PRIORITIES:
        note("COERCED_FIELD", f"`priority` was {raw_priority!r}; normalised to 'M'.", identifier)
    if priority not in PRIORITIES:
        priority = "M"

    area = raw.get("area")
    if area is not None and not isinstance(area, str):
        note("COERCED_FIELD", "`area` was not a string; treated as unstated.", identifier)
        area = None

    requirement = IngestedRequirement(
        id=identifier,
        statement=_as_str(raw.get("statement")),
        type=_as_str(raw.get("type")).lower(),
        abstraction_basis=_as_optional_str(raw.get("abstraction_basis")),
        title=_as_str(raw.get("title")),
        rationale=_as_str(raw.get("rationale")),
        persona=_as_optional_str(raw.get("persona")),
        business_value=_as_optional_str(raw.get("business_value")),
        acceptance_criteria=criteria,
        confidence=confidence,
        status=_as_str(raw.get("status")).lower() or "candidate",
        evidence=evidence,
        graph_path=graph_path,
        assumptions=assumptions,
        open_questions=questions,
        parent_id=_as_optional_str(raw.get("parent_id")),
        parent_relation=_as_optional_str(raw.get("parent_relation")),
        superseded=superseded,
        model_confidence=model_confidence,
        area=_as_optional_str(area),
        area_name=_as_optional_str(raw.get("area_name")),
        priority=priority,
        generator=_as_str(raw.get("generator")),
        source_index=index,
        extras={key: value for key, value in raw.items() if key not in CONTRACT_KEYS},
    )
    return requirement, issues


def read_requirement_set(payload: Any, source_file: str = "") -> RequirementSet:
    """The whole array, with duplicates resolved and every read decision recorded."""
    issues: list[ReadIssue] = []
    requirements: list[IngestedRequirement] = []
    seen: dict[str, int] = {}
    keys_seen: set[str] = set()

    for index, raw in enumerate(payload):
        if isinstance(raw, dict):
            keys_seen.update(raw)
        requirement, entry_issues = read_requirement(raw, index)
        issues.extend(entry_issues)
        if requirement is None:
            continue
        if requirement.id in seen:
            # Two items under one id makes every downstream link ambiguous, and a link that can
            # point at two things points at neither. Keep the first, record the collision.
            issues.append(
                ReadIssue(
                    code="DUPLICATE_IDENTIFIER",
                    message=(
                        f"Id {requirement.id!r} already appeared at index {seen[requirement.id]}. "
                        "The later entry was dropped, because two items under one id make every "
                        "traceability link ambiguous."
                    ),
                    source_index=index,
                    requirement_id=requirement.id,
                )
            )
            continue
        seen[requirement.id] = index
        requirements.append(requirement)

    return RequirementSet(
        source_file=source_file,
        contract_keys_seen=[key for key in CONTRACT_KEYS if key in keys_seen],
        missing_contract_keys=[key for key in CONTRACT_KEYS if key not in keys_seen],
        requirements=requirements,
        read_issues=issues,
    )


# Files that mean "this is a source repository", not a requirements run's output. Checked so the
# most likely operator mistake gets a message naming the actual next step rather than a restatement
# of what is missing. `requirements.txt` is the reason this matters: a Python project has one, it
# sits right beside where `requirements.json` would be, and it makes pointing at the repo look
# correct.
SOURCE_MARKERS = (
    "requirements.txt", "pyproject.toml", "setup.py", "package.json", "go.mod", "Cargo.toml",
    "pom.xml", "build.gradle", "Gemfile", "composer.json", "Dockerfile",
)

# Files that mean "this is a requirements run, but you pointed one level too high".
RUN_MARKERS = ("artifacts", "source")


def resolve_from_source(root: Path, runs_dir: Path) -> tuple[Path, str] | None:
    """The requirements run for *exactly* this source tree, if one exists.

    Pointing this pack at a codebase is the natural next action after generating requirements for
    that codebase, and it is a dead end: the operator has the requirements, they are just in a
    different directory. Worse, `prepare_run` copies the tree and creates a run directory before
    this step gets to say so, so the failure leaves behind a folder that looks exactly like a
    requirements run — which is how one run comes to look like two.

    Rather than guess, this proves it. The requirements pack fingerprints every file it ingests and
    stores the result in `inventory.json`; comparing that against the same function's output over
    the tree we were handed is byte-exact. A codebase that has changed since will not match, and
    the run halts, which is correct.

    Returns the requirements artifact and the id of the run holding it, or None.
    """
    from agentic.capabilities.ingest import build_inventory

    try:
        mine = {record.path: record.sha256 for record in build_inventory(root, root.name).files}
    except OSError:
        return None
    if not mine:
        return None

    try:
        candidates = sorted(
            runs_dir.glob("*/artifacts"),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
    except OSError:
        return None

    for artifacts in candidates:
        requirements = artifacts / REQUIREMENTS_FILE
        inventory = artifacts / "inventory.json"
        if not (requirements.is_file() and inventory.is_file()):
            continue
        try:
            payload = json.loads(inventory.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError):
            continue
        theirs = {
            record.get("path"): record.get("sha256")
            for record in (payload.get("files") or [])
            if isinstance(record, dict)
        }
        if theirs and theirs == mine:
            return requirements, artifacts.parent.name
    return None


def available_requirement_runs(runs_dir: Path, limit: int = 5) -> list[tuple[Path, int]]:
    """Runs that already hold a requirements artifact, newest first, with their item counts.

    The single most useful thing to tell an operator who pointed at the wrong place is *where the
    right places are*. Cheap: one glob plus a bounded number of reads.
    """
    try:
        candidates = sorted(
            runs_dir.glob(f"*/artifacts/{REQUIREMENTS_FILE}"),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
    except OSError:
        return []

    found: list[tuple[Path, int]] = []
    for path in candidates[:limit]:
        try:
            payload = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(payload, list) and payload:
            found.append((path.parent, len(payload)))
    return found


def _no_input_reason(root: Path, runs_dir: Path | None = None) -> str:
    """Say what to do next, not merely what is absent.

    Three situations, each with a different fix, plus — whatever the situation — a list of runs
    that *do* hold a requirements artifact. That list is the single most useful thing to give an
    operator who pointed at the wrong place, because it turns "not here" into "here".
    """
    parts = [
        f"No {REQUIREMENTS_FILE} found under {root}.",
        "This pack consumes the requirements pack's published output.",
    ]

    # A source tree sitting inside a previous run directory. Worth naming, because that run's own
    # `artifacts/` is the first place an operator looks — and when the run that created it was a
    # failed attempt at *this* pack, all it holds is the failure note, which reads exactly like a
    # requirements folder that came out empty.
    nested = root.parent if root.name == "source" else None
    siblings = (
        [entry.name for entry in (nested / "artifacts").iterdir()]
        if nested is not None and (nested / "artifacts").is_dir()
        else None
    )

    if siblings == ["run_failed.md"]:
        parts.append(
            f"This is the source tree of run {nested.name}, which FAILED and produced no "
            "requirements — its `artifacts/` holds only the failure note, so there is nothing "
            "there to consume either."
        )
    elif siblings is not None and REQUIREMENTS_FILE not in siblings:
        parts.append(
            f"This is the source tree of run {nested.name}, whose `artifacts/` holds no "
            f"{REQUIREMENTS_FILE} either."
        )
    elif (root / "artifacts").is_dir() or any((root / name).is_dir() for name in RUN_MARKERS):
        parts.append(
            "This looks like a run directory rather than its artifacts — try "
            f"{root / 'artifacts'} instead."
        )

    present = [name for name in SOURCE_MARKERS if (root / name).is_file()]
    if present:
        parts.append(
            f"This looks like a source repository ({', '.join(present[:3])} present), so it has "
            "not been analysed yet. Run the requirements pack over it first, then point this pack "
            f"at that run's artifacts: `agentic run codebase-requirements {root}`, then "
            "`agentic run testcase-generation runs/<that-run-id>/artifacts`. Note "
            "`requirements.txt` is a dependency list, not the requirements artifact this pack reads."
        )
    elif len(parts) == 2:
        # Nothing recognisable. Say the general thing rather than nothing at all — a message that
        # only reports the absence leaves the operator with no next move.
        parts.append(
            "Point it at a run's `artifacts/` directory (or a zip of one), not at a source "
            "repository."
        )

    if runs_dir is not None:
        existing = available_requirement_runs(Path(runs_dir))
        if existing:
            listed = "; ".join(
                f"{path} ({count} requirement(s))" for path, count in existing
            )
            parts.append(f"Runs that already hold one: {listed}.")
        elif present:
            parts.append(
                f"No run under {runs_dir} holds one yet, so the requirements pack has to run first."
            )

    return " ".join(parts)


@capability("ingest_requirement_set")
def ingest_requirement_set(ctx: CapabilityContext) -> CapabilityResult:
    """Locate and read `requirements.json`, plus any optional siblings beside it."""
    root = ctx.source_root
    if not root.is_dir():
        return CapabilityResult(halt_reason=f"Input path is not a directory: {root}")

    resolved_from = ""
    path = _find(root, REQUIREMENTS_FILE)
    if path is None:
        # Pointed at a codebase rather than a requirements run. If a requirements run exists for
        # exactly this source tree, use it rather than dead-ending on a technicality: the operator
        # has the requirements, they are just somewhere else, and the match is provable.
        match = resolve_from_source(root, Path(ctx.settings.runs_dir))
        if match is None:
            return CapabilityResult(halt_reason=_no_input_reason(root, ctx.settings.runs_dir))
        path, resolved_from = match

    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        return CapabilityResult(halt_reason=f"Could not read {path.name}: {exc}")

    if not isinstance(payload, list):
        return CapabilityResult(
            halt_reason=(
                f"{path.name} holds a {type(payload).__name__}, not the array of requirements this "
                "pack consumes. It may be an artifact of a different kind."
            )
        )

    # When the artifact was resolved from elsewhere it does not sit under `root`, so the readable
    # name is its real path and the siblings live beside *it*, not beside what we were pointed at.
    artifacts_root = path.parent if resolved_from else root
    relative = (
        str(path) if resolved_from else path.relative_to(root).as_posix()
    )
    requirement_set = read_requirement_set(payload, source_file=relative)

    for name, filename in ENRICHMENT_FILES.items():
        found = _find(artifacts_root, filename)
        if found is None:
            continue
        try:
            requirement_set.enrichment[name] = json.loads(found.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError):
            # Enrichment is optional by definition. A broken one is worth less than the run.
            requirement_set.read_issues.append(
                ReadIssue(
                    code="ENRICHMENT_UNREADABLE",
                    message=f"{filename} was present but could not be read; continuing without it.",
                    source_index=-1,
                )
            )

    if not requirement_set.requirements:
        return CapabilityResult(
            artifacts={"requirement_set": requirement_set},
            halt_reason=(
                f"{path.name} held {len(payload)} entr(y/ies) and none could be read as a "
                "requirement. Generating tests from this would mean inventing them."
            ),
        )

    messages: list[str] = [
        f"Read {len(requirement_set.requirements)} requirement(s) from {relative}."
    ]
    if resolved_from:
        messages.insert(
            0,
            f"The path given holds source code, not a requirements artifact. Run {resolved_from} "
            "analysed a byte-identical source tree, so its requirements were used. Point at "
            f"`{artifacts_root}` directly to make this explicit, or re-run the requirements pack "
            "if the code has changed since.",
        )
    if requirement_set.missing_contract_keys:
        messages.append(
            "The artifact does not carry "
            f"{', '.join(requirement_set.missing_contract_keys)} — it predates the current "
            "requirements-pack contract. Reading continues with defaults for those fields."
        )
    if requirement_set.read_issues:
        messages.append(
            f"{len(requirement_set.read_issues)} entr(y/ies) needed defensive handling; each is "
            "recorded with its position in the input."
        )
    if requirement_set.enrichment:
        messages.append(f"Optional input used: {', '.join(sorted(requirement_set.enrichment))}.")

    return CapabilityResult(
        artifacts={"requirement_set": requirement_set},
        metrics={
            "requirements_read": len(requirement_set.requirements),
            "entries_in_file": len(payload),
            "read_issues": len(requirement_set.read_issues),
            "enrichment_used": sorted(requirement_set.enrichment),
            "missing_contract_keys": requirement_set.missing_contract_keys,
        },
        messages=messages,
    )

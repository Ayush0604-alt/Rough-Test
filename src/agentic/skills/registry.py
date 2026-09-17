"""Dynamic skill loading.

Skill instructions live in `SKILL.md` files on disk, never inside prompt string literals. An LLM
step loads the relevant skill body at call time and injects it into the system prompt; a
deterministic step records which skill (and which exact content hash) governed it, so every
deliverable can state the skill version that produced it.

Skill folders keep the standard `name` / `description` frontmatter and nothing more — orchestration
metadata belongs in `pack.yaml`, so the skill files stay portable to any tool that reads them.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class LoadedSkill:
    """One parsed `SKILL.md`."""

    name: str
    description: str
    body: str
    path: Path
    sha256: str
    frontmatter: dict[str, Any]

    def section(self, heading: str) -> str:
        """Return the Markdown section whose heading contains `heading`, else the whole body.

        `pack.yaml` can point a step at one part of a multi-part skill (for example `Gate 1` of the
        validation skill) so the prompt carries the relevant checklist rather than both gates.
        Falling back to the full body keeps a typo from silently emptying the instructions.
        """
        wanted = heading.strip().casefold()
        lines = self.body.splitlines()
        collected: list[str] = []
        capture_level: int | None = None
        for line in lines:
            stripped = line.lstrip()
            if stripped.startswith("#"):
                level = len(stripped) - len(stripped.lstrip("#"))
                title = stripped[level:].strip().casefold()
                if capture_level is not None and level <= capture_level:
                    break
                if capture_level is None and wanted in title:
                    capture_level = level
                    collected.append(line)
                    continue
            if capture_level is not None:
                collected.append(line)
        return "\n".join(collected).strip() or self.body


class SkillRegistry:
    """Loads and caches the `SKILL.md` files under a pack directory.

    The cache is keyed on the file's modification time, so editing a skill file takes effect on
    the next run without restarting Streamlit — which is the point of keeping instructions on disk.
    """

    def __init__(self, root: Path | str) -> None:
        self.root = Path(root)
        self._cache: dict[str, tuple[float, LoadedSkill]] = {}

    def skill_files(self) -> dict[str, Path]:
        """Map skill name to file, discovered as `<root>/<skill-name>/SKILL.md`."""
        found: dict[str, Path] = {}
        for path in sorted(self.root.glob("*/SKILL.md")):
            found[path.parent.name] = path
        return found

    def available(self) -> list[str]:
        return sorted(self.skill_files())

    def load(self, name: str) -> LoadedSkill:
        files = self.skill_files()
        path = files.get(name)
        if path is None:
            known = ", ".join(sorted(files)) or "<none found>"
            raise KeyError(
                f"Unknown skill {name!r} under {self.root}. Available skills: {known}"
            )
        mtime = path.stat().st_mtime
        cached = self._cache.get(name)
        if cached and cached[0] == mtime:
            return cached[1]
        skill = self._parse(path)
        self._cache[name] = (mtime, skill)
        return skill

    def load_all(self) -> dict[str, LoadedSkill]:
        return {name: self.load(name) for name in self.available()}

    def _parse(self, path: Path) -> LoadedSkill:
        raw = path.read_text(encoding="utf-8-sig")
        frontmatter, body = _split_frontmatter(raw)
        # The folder name is authoritative: it is what `pack.yaml` and the loader agree on, so a
        # skill whose frontmatter `name` drifted from its directory still resolves.
        name = path.parent.name
        return LoadedSkill(
            name=name,
            description=str(frontmatter.get("description", "")).strip(),
            body=body.strip(),
            path=path,
            sha256=hashlib.sha256(raw.encode("utf-8")).hexdigest(),
            frontmatter=frontmatter,
        )


def _split_frontmatter(raw: str) -> tuple[dict[str, Any], str]:
    """Separate YAML frontmatter from the Markdown body.

    A file without frontmatter is still a usable skill — it just has no description — so this
    returns an empty mapping rather than raising.
    """
    if not raw.startswith("---"):
        return {}, raw
    parts = raw.split("---", 2)
    if len(parts) < 3:
        return {}, raw
    try:
        loaded = yaml.safe_load(parts[1]) or {}
    except yaml.YAMLError:
        return {}, parts[2]
    return (loaded if isinstance(loaded, dict) else {}), parts[2]

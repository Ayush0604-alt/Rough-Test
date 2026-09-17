"""On-disk artifact storage for a single run.

Steps exchange large payloads through here rather than through graph state. A step declares
`produces: [graph]` in the manifest; the node wrapper saves whatever the capability returned under
that name and records only the path in state.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, TypeVar

from pydantic import BaseModel

ModelT = TypeVar("ModelT", bound=BaseModel)


class ArtifactStore:
    """Reads and writes named artifacts under a run directory."""

    def __init__(self, run_dir: Path | str) -> None:
        self.run_dir = Path(run_dir)
        self.artifacts_dir = self.run_dir / "artifacts"

    def ensure(self) -> None:
        self.artifacts_dir.mkdir(parents=True, exist_ok=True)

    def path_for(self, name: str) -> Path:
        return self.artifacts_dir / f"{name}.json"

    def save(self, name: str, value: Any) -> str:
        """Persist an artifact and return its path.

        Pydantic models are dumped in JSON mode so enums and datetimes round-trip; anything else
        is written with a permissive default so a stray non-serialisable value degrades to its
        string form rather than aborting a completed analysis.
        """
        self.ensure()
        path = self.path_for(name)
        if isinstance(value, BaseModel):
            payload: Any = value.model_dump(mode="json")
        else:
            payload = value
        path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False, default=str), encoding="utf-8"
        )
        return str(path)

    def save_text(self, relative_name: str, text: str) -> str:
        """Write a rendered deliverable (Markdown, CSV, Mermaid) verbatim."""
        self.ensure()
        path = self.artifacts_dir / relative_name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return str(path)

    def load_json(self, path_or_name: str) -> Any:
        path = Path(path_or_name)
        if not path.is_absolute() and not path.exists():
            path = self.path_for(path_or_name)
        return json.loads(path.read_text(encoding="utf-8"))

    def load_model(self, path_or_name: str, model: type[ModelT]) -> ModelT:
        return model.model_validate(self.load_json(path_or_name))

    def read_text(self, path: str) -> str:
        return Path(path).read_text(encoding="utf-8")

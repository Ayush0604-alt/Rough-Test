"""Knowledge-graph and validation types shared by every skill pack.

These are pack-agnostic: a test-generation pack reasons over the same graph a requirements pack
does, so the graph schema lives here rather than beside any one pack's output schema.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class Provenance(StrEnum):
    """How certain the extractor was about a node or edge.

    Mirrors the table in `graphify-knowledge-graph-extraction`. These are load-bearing, not
    decoration — the gates and the synthesis prompt both read them to decide how strongly a claim
    may be worded.
    """

    EXTRACTED = "EXTRACTED"   # syntactically certain from parsing
    RESOLVED = "RESOLVED"     # a reference matched exactly one real definition
    INFERRED = "INFERRED"     # derived by heuristic
    AMBIGUOUS = "AMBIGUOUS"   # matched several candidates, or an unclear external symbol


class Severity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


class SourceLocation(BaseModel):
    """Where a claim points, and what is verifiable about that point.

    `symbol` and `scope` are deliberately separate, because they make different promises and are
    checked differently:

    - `symbol` asserts *a name that appears at these lines*. Verification re-reads the file and
      rejects the citation when it does not — this is the anti-fabrication check.
    - `scope` names the enclosing definition the lines sit **inside**. Its name lives at the `def`
      or `class` header, which may be hundreds of lines above, so it carries no such promise and
      is never line-checked.

    Collapsing the two makes every statement deep inside a long function look fabricated.
    """

    file: str
    line_start: int | None = None
    line_end: int | None = None
    symbol: str | None = None
    scope: str | None = None

    def compact(self) -> str:
        line = str(self.line_start or "?")
        if self.line_end and self.line_end != self.line_start:
            line += f"-{self.line_end}"
        symbol = f":{self.symbol}" if self.symbol else ""
        return f"{self.file}:L{line}{symbol}"


class Entity(BaseModel):
    id: str
    kind: str
    name: str
    qualified_name: str
    language: str | None = None
    location: SourceLocation | None = None
    provenance: Provenance = Provenance.EXTRACTED
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    attributes: dict[str, Any] = Field(default_factory=dict)


class Relationship(BaseModel):
    id: str
    source: str
    target: str
    kind: str
    provenance: Provenance = Provenance.EXTRACTED
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    evidence: list[SourceLocation] = Field(default_factory=list)
    attributes: dict[str, Any] = Field(default_factory=dict)


class KnowledgeGraph(BaseModel):
    schema_version: str = "1.0"
    repository_name: str
    entities: list[Entity] = Field(default_factory=list)
    relationships: list[Relationship] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    def by_id(self) -> dict[str, Entity]:
        return {entity.id: entity for entity in self.entities}

    def by_qualified_name(self) -> dict[str, Entity]:
        return {entity.qualified_name: entity for entity in self.entities}


class InventoryFile(BaseModel):
    path: str
    extension: str
    language: str
    size: int
    sha256: str
    parseable: bool


class RepositoryInventory(BaseModel):
    repository_name: str
    files: list[InventoryFile] = Field(default_factory=list)
    languages: dict[str, int] = Field(default_factory=dict)
    total_bytes: int = 0


class ValidationIssue(BaseModel):
    code: str
    severity: Severity
    message: str
    entity_id: str | None = None
    relationship_id: str | None = None
    subject_id: str | None = None
    location: SourceLocation | None = None


class GateReport(BaseModel):
    """Outcome of one deterministic gate.

    `valid` is false only when at least one ERROR issue was raised. Warnings never block a run,
    but they must survive into the gap analysis rather than being quietly dropped.
    """

    gate: str
    valid: bool
    checks_run: int = 0
    errors: int = 0
    warnings: int = 0
    issues: list[ValidationIssue] = Field(default_factory=list)
    metrics: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def from_issues(
        cls,
        gate: str,
        issues: list[ValidationIssue],
        checks_run: int,
        metrics: dict[str, Any] | None = None,
    ) -> "GateReport":
        errors = sum(issue.severity == Severity.ERROR for issue in issues)
        warnings = sum(issue.severity == Severity.WARNING for issue in issues)
        return cls(
            gate=gate,
            valid=errors == 0,
            checks_run=checks_run,
            errors=errors,
            warnings=warnings,
            issues=issues,
            metrics=metrics or {},
        )

    def error_messages(self) -> list[str]:
        return [issue.message for issue in self.issues if issue.severity == Severity.ERROR]

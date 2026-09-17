"""Input-side schemas for the `testcase-generation` pack.

Nothing here imports from `schemas/brd.py`. That is deliberate and load-bearing: the requirements
pack is frozen, and a downstream pack that reached into its internal models would be coupled to
decisions it does not control. What this pack depends on is the *published artifact* —
`requirements.json` — and only through the defensive reader below.

`SourceLocation`, `GateReport`, and friends come from `schemas/core.py`, which is pack-agnostic by
design and says so in its own docstring.

## The reading posture

The artifact is imperfect in measured ways (see the fixtures under
`tests/fixtures/requirement_sets/`). Two fields are load-bearing — `id` and `statement` — and
everything else is read with a default. A field of the wrong type is coerced where a coercion is
obvious and dropped with a recorded issue where it is not. An unknown key is *preserved*, never
rejected, so a future requirements-pack improvement cannot break ingestion.

Nothing is silently discarded. Every read decision lands in `RequirementSet.read_issues`, and every
qualification decision lands in a `QualificationRecord` — including for requirements that produce
no tests at all. Traceability is not only for what was tested.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

from agentic.schemas.core import SourceLocation

# The published contract as of the current requirements pack. Held as data rather than as a
# hard-coded parse so a shape change is a one-line edit and a visible diff.
CONTRACT_KEYS = (
    "id", "type", "parent_id", "parent_relation", "abstraction_basis", "title", "statement",
    "rationale", "persona", "business_value", "acceptance_criteria", "confidence", "status",
    "evidence", "graph_path", "assumptions", "open_questions", "generator", "superseded",
    "model_confidence", "area", "area_name", "priority",
)

# Priorities the requirements pack publishes. Anything else is normalised to "M" — it is derived
# from the code's own defaults, never a stakeholder ranking, so an unknown value carries no meaning
# worth preserving.
PRIORITIES = ("M", "S", "C")

# Acceptance criteria the requirements pack generated for itself when the cited evidence fixed no
# observable outcome (`capabilities/hedging.py:_criterion_from`). They are syntactically criteria
# and semantically empty: a test whose only expected result is "the outcome this requirement
# describes" asserts nothing. Detected two independent ways — by template prefix, and by the
# assumption the same pass records alongside them — because either signal alone could drift.
BOILERPLATE_CRITERION_PREFIXES = (
    "Exercising ",
    "The declared condition still holds",
)
DERIVED_CRITERION_MARKER = "derived from the cited evidence rather than stated by the analysis"

# An open question the requirements pack raises about itself: the evidence fixed no observable
# outcome, which is the one that genuinely blocks deriving a test.
BLOCKER_QUESTION_MARKER = "What observable outcome would show this requirement has been met"


class Band(StrEnum):
    """What may be done with a requirement, decided before any test is designed."""

    ELIGIBLE = "eligible"                                   # a usable criterion exists
    ELIGIBLE_WITH_ASSUMPTIONS = "eligible_with_assumptions"  # derivable, but needs a stated leap
    NEEDS_CLARIFICATION = "needs_clarification"             # a question, not a test
    EXCLUDED = "excluded"                                   # no test, with a recorded reason


class ReadIssue(BaseModel):
    """One thing that was wrong with the artifact, recorded rather than raised."""

    code: str
    message: str
    source_index: int = Field(description="Position in the input array, so it can be found again.")
    requirement_id: str = ""


class IngestedRequirement(BaseModel):
    """One requirement, read defensively from the published artifact.

    Only `id` and `statement` are load-bearing. `statement` may still be blank here — a blank one
    is excluded at qualification with a reason rather than dropped at ingest, because an item that
    can be named can be reported on, and reporting it is more honest than making it vanish.
    """

    id: str
    statement: str = ""
    type: str = ""
    abstraction_basis: str | None = None
    title: str = ""
    rationale: str = ""
    persona: str | None = None
    business_value: str | None = None
    acceptance_criteria: list[str] = Field(default_factory=list)
    confidence: float | None = None
    status: str = "candidate"
    evidence: list[SourceLocation] = Field(default_factory=list)
    graph_path: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)
    parent_id: str | None = None
    parent_relation: str | None = None
    superseded: list[str] = Field(default_factory=list)
    model_confidence: float | None = None
    area: str | None = None
    area_name: str | None = None
    priority: str = "M"
    generator: str = ""

    source_index: int = -1
    extras: dict[str, Any] = Field(
        default_factory=dict,
        description="Keys the contract does not name. Preserved so a future field is not lost.",
    )

    # ---- derived views, computed rather than stored so they cannot go stale ----

    def usable_criteria(self) -> list[str]:
        """Acceptance criteria a test can actually be derived from.

        Excludes the ones the requirements pack generated for itself. On the rich fixture this
        removes 59 of 235 criteria and empties 59 items that appear criteria-bearing.
        """
        if self.has_derived_criteria_marker():
            return []
        return [c for c in self.acceptance_criteria if not _is_boilerplate(c)]

    def discarded_criteria(self) -> list[str]:
        usable = set(self.usable_criteria())
        return [c for c in self.acceptance_criteria if c not in usable]

    def has_derived_criteria_marker(self) -> bool:
        """Whether the requirements pack flagged its own criteria as derived, not stated."""
        return any(DERIVED_CRITERION_MARKER in a for a in self.assumptions)

    def blocking_questions(self) -> list[str]:
        """Open questions that genuinely prevent deriving a test, as opposed to noting a gap."""
        return [q for q in self.open_questions if BLOCKER_QUESTION_MARKER in q]

    def evidence_text(self) -> str:
        return " ".join(location.compact() for location in self.evidence)

    def searchable_text(self) -> str:
        """Statement plus usable criteria — what signal detection reads.

        Rationale is deliberately excluded: it is the one field where technical vocabulary is
        allowed, so including it would let an implementation detail activate a test dimension.
        """
        return " ".join([self.statement, *self.usable_criteria()])


class RequirementSet(BaseModel):
    """The normalised input, and everything that was wrong with it.

    The envelope is versioned so a future requirements-pack shape can be detected rather than
    silently mis-parsed, and `enrichment` is where optional inputs land — which is what keeps
    adding one a change to a single capability.
    """

    schema_version: str = "1.0"
    source_file: str = ""
    contract_keys_seen: list[str] = Field(default_factory=list)
    missing_contract_keys: list[str] = Field(default_factory=list)
    requirements: list[IngestedRequirement] = Field(default_factory=list)
    enrichment: dict[str, Any] = Field(default_factory=dict)
    read_issues: list[ReadIssue] = Field(default_factory=list)

    def by_id(self) -> dict[str, IngestedRequirement]:
        return {item.id: item for item in self.requirements}


class QualificationRecord(BaseModel):
    """What was decided about one requirement, and why.

    Produced for every requirement including excluded ones. A coverage report that only accounts
    for what was tested cannot answer "why is there no test for this", which is the first question
    a reviewer asks.
    """

    requirement_id: str
    band: Band
    reason_codes: list[str] = Field(default_factory=list)
    reason: str = ""
    usable_criteria: list[str] = Field(default_factory=list)
    discarded_criteria: list[str] = Field(default_factory=list)
    blockers: list[str] = Field(default_factory=list)
    activated_dimensions: list[str] = Field(default_factory=list)
    duplicate_of: str | None = None

    @property
    def testable(self) -> bool:
        return self.band in (Band.ELIGIBLE, Band.ELIGIBLE_WITH_ASSUMPTIONS)


class QualifiedSet(BaseModel):
    """The qualification stage's whole output: the records, plus the set they were decided over."""

    records: list[QualificationRecord] = Field(default_factory=list)
    requirements: list[IngestedRequirement] = Field(default_factory=list)
    counts: dict[str, int] = Field(default_factory=dict)

    def record_for(self, requirement_id: str) -> QualificationRecord | None:
        for record in self.records:
            if record.requirement_id == requirement_id:
                return record
        return None

    def testable_ids(self) -> list[str]:
        return [record.requirement_id for record in self.records if record.testable]


def _is_boilerplate(criterion: str) -> bool:
    return criterion.strip().startswith(BOILERPLATE_CRITERION_PREFIXES)

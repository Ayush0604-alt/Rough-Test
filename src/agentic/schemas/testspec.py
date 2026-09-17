"""The test specification model — technology-independent by construction.

Nothing here names a test framework, runner, assertion library, protocol client, browser, or
language. That is not a style preference: the specification layer has to survive being turned into
API tests, UI tests, unit tests, agent-trajectory evaluations, or a manual script, and a model that
has already decided it is talking HTTP cannot become any of the others.

The three fields that make a later adapter possible — `interface_hint`, `automation_readiness`, and
`TestInput.value_kind` — are advisory metadata computed deterministically at assembly. **No logic in
this pack branches on any of them.** They exist so an adapter has something to read, and if one
ever starts steering behaviour here, the independence claim is gone.

## Two authored levels, one derived

    Requirement -> TestScenario (a situation) -> TestCase (one checkable behaviour)

A scenario carries the shared context and preconditions its cases run under, which is what makes a
group of cases readable and cheap to automate. A suite is a *view* over scenarios — grouped by
area, tier, risk, or dimension — and is never authored, because grouping can always be recomputed
and a third authored level would hold nothing else.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field

# What a test exercises. Deliberately about *risk*, never about mechanism: `positive` means the
# behaviour holds, not that some endpoint returned 200.
DIMENSIONS = (
    "positive", "negative", "validation", "error_handling", "boundary", "state_transition",
    "authorization", "business_rule", "data_integrity", "integration", "exceptional",
)


class Route(StrEnum):
    """What the analysis decided should happen to a requirement."""

    GENERATE = "generate"    # design cases for it
    ATTACH = "attach"        # fold into a parent capability's cases as an extra assertion
    DEFERRED = "deferred"    # no case; recorded as an honest gap with a reason


class InterfaceHint(StrEnum):
    """The *shape* of the boundary, inferred from the requirement's traced path.

    Shape, never framework: an `http_endpoint` hint comes from a path looking like `VERB /path`,
    not from recognising any web library. Advisory only — an adapter reads it, nothing here does.
    """

    HTTP_ENDPOINT = "http_endpoint"
    COMMAND_LINE = "command_line"
    UI_ACTION = "ui_action"
    SCHEDULED = "scheduled"
    INTERNAL = "internal"
    UNKNOWN = "unknown"


class AutomationReadiness(StrEnum):
    """What stands between this specification and an automated test."""

    READY = "ready"
    NEEDS_DATA = "needs_data"
    NEEDS_ENVIRONMENT = "needs_environment"
    MANUAL_ONLY = "manual_only"


class TestSignal(BaseModel):
    """What was decided about one requirement before any case was designed.

    The designer receives this and works inside it. It never chooses its own dimensions, budget, or
    risk — those come from what the requirement actually says, computed deterministically, so two
    runs over one input plan the same work.
    """

    requirement_id: str
    band: str = ""
    route: str = Route.GENERATE.value
    attach_to: str | None = None
    deferral_reason: str = ""

    dimensions: list[str] = Field(default_factory=list)
    risk: str = "medium"
    risk_reason: str = ""
    case_budget: int = 0

    interface_hint: str = InterfaceHint.UNKNOWN.value
    actor: str | None = None
    usable_criteria: list[str] = Field(default_factory=list)
    derivation_default: str = "inferred"
    area: str | None = None
    area_name: str | None = None


class TestSignalSet(BaseModel):
    signals: list[TestSignal] = Field(default_factory=list)
    counts: dict[str, int] = Field(default_factory=dict)

    def by_id(self) -> dict[str, TestSignal]:
        return {signal.requirement_id: signal for signal in self.signals}

    def generating(self) -> list[TestSignal]:
        return [signal for signal in self.signals if signal.route == Route.GENERATE.value]


class TestInput(BaseModel):
    """One piece of test data, described by the *role it plays* rather than by a literal value.

    `value_kind` is what makes a boundary or validation case meaningful to an adapter that has to
    invent concrete data later: it says why this value was chosen, which a literal cannot.
    """

    name: str = Field(description="What this input is, in the product's own words.")
    value_kind: str = Field(
        default="valid",
        description=(
            "Why this value was chosen: 'valid', 'invalid', 'boundary', 'absent', 'malformed', "
            "or 'unauthorised'."
        ),
    )
    description: str = Field(
        default="",
        description="What the value has to be like. Never a literal from any particular system.",
    )
    example: str | None = Field(
        default=None, description="An illustrative value, only where the requirement fixes one."
    )


class CoverageLink(BaseModel):
    """What a case actually checks, so coverage can be counted against something real."""

    kind: str = Field(
        description="'acceptance_criterion', 'rule', 'condition', 'assumption', or 'statement'."
    )
    ref: str = Field(default="", description="Index or identifier of the thing covered.")
    text: str = Field(default="", description="The covered text, copied so the link is readable.")


class TestCase(BaseModel):
    """One behaviour that could come out either way.

    The bar every case is held to: *what would have to be true of an implementation for this to
    fail?* If nothing would, it is not a test — it is a sentence about the requirement.
    """

    id: str = Field(default="", description="Assigned deterministically after design.")
    scenario_id: str = ""
    requirement_ids: list[str] = Field(default_factory=list)
    covers: list[CoverageLink] = Field(default_factory=list)
    dimension: str = "positive"

    title: str = ""
    objective: str = Field(
        default="",
        description="The risk this case retires, in one sentence. Not what the case does.",
    )
    preconditions: list[str] = Field(default_factory=list)
    inputs: list[TestInput] = Field(default_factory=list)
    steps: list[str] = Field(default_factory=list)
    expected_results: list[str] = Field(default_factory=list)

    derivation: str = "inferred"
    assumptions: list[str] = Field(default_factory=list)
    risk: str = "medium"
    risk_reason: str = ""
    notes: str = Field(
        default="",
        description="Technical context for a test engineer. May name implementation, unlike the "
        "objective and expected results, which are stakeholder-facing.",
    )

    # Computed at assembly, never authored. Advisory metadata for a future adapter.
    interface_hint: str = InterfaceHint.UNKNOWN.value
    automation_readiness: str = AutomationReadiness.READY.value
    regression_candidate: bool = False

    # Ids of cases folded into this one by deduplication. Empty for almost every case, present so
    # a merge is visible rather than looking like a case that went missing.
    superseded: list[str] = Field(default_factory=list)


class TestScenario(BaseModel):
    """One situation under test, holding the cases that share its context."""

    id: str = ""
    requirement_id: str = ""
    also_covers: list[str] = Field(default_factory=list)
    title: str = ""
    context: str = Field(default="", description="The situation these cases run under.")
    preconditions: list[str] = Field(default_factory=list)
    cases: list[TestCase] = Field(default_factory=list)
    area: str | None = None
    band: str = ""


class RequirementCoverage(BaseModel):
    """What a requirement got, and — where it got nothing — why."""

    requirement_id: str
    title: str = ""
    band: str = ""
    route: str = ""
    verdict: str = Field(
        default="none",
        description=(
            "'sufficient' (every activated dimension and every usable criterion covered), "
            "'positive_only' (states a refusal or a limit and only the happy path is tested), "
            "'thin' (some activated dimensions uncovered), 'none' (planned for and got nothing), "
            "'deferred' (routed away with a reason), or 'excluded' (never eligible)."
        ),
    )
    dimensions_expected: list[str] = Field(default_factory=list)
    dimensions_covered: list[str] = Field(default_factory=list)
    case_count: int = 0
    criteria_total: int = 0
    criteria_covered: int = 0
    reason: str = ""


class CriterionCoverage(BaseModel):
    """One acceptance criterion and the cases, if any, that check it."""

    requirement_id: str
    criterion_index: int
    criterion_text: str
    covered_by: list[str] = Field(default_factory=list)


class CoverageReport(BaseModel):
    """Everything covered and everything not, at both levels."""

    requirements: list[RequirementCoverage] = Field(default_factory=list)
    criteria: list[CriterionCoverage] = Field(default_factory=list)
    verdicts: dict[str, int] = Field(default_factory=dict)
    totals: dict[str, object] = Field(default_factory=dict)

    def with_verdict(self, verdict: str) -> list[RequirementCoverage]:
        return [entry for entry in self.requirements if entry.verdict == verdict]

    def uncovered_criteria(self) -> list[CriterionCoverage]:
        return [entry for entry in self.criteria if not entry.covered_by]


class RepairedTestCase(BaseModel):
    """One rejected case, fixed — carrying only the fields a repair is allowed to change.

    Typed as a full `TestCase` this was unusable at scale. A case carries about twenty fields, most
    of them computed, and asked to return thirty-five of them the model hit the output ceiling and
    returned three. Everything a repair cannot legitimately change is therefore absent here, which
    both shrinks the reply by roughly two thirds and makes it *structurally impossible* for a
    repair pass to alter which requirement a case traces to or what it claims to cover.
    """

    id: str = Field(
        description=(
            "The rejected case's id, copied EXACTLY from `cases_to_fix`. This is how the fix is "
            "matched back to the case it replaces; an entry whose id matches no rejected case is "
            "discarded."
        )
    )
    title: str = ""
    objective: str = Field(
        default="",
        description="The risk this case retires, in one sentence. Not what the case does.",
    )
    preconditions: list[str] = Field(
        default_factory=list, description="What must already be true before the steps run."
    )
    inputs: list[TestInput] = Field(
        default_factory=list, description="The data this case supplies."
    )
    steps: list[str] = Field(
        default_factory=list,
        description="What the actor does, in the product's vocabulary. Never a tool or protocol.",
    )
    expected_results: list[str] = Field(
        default_factory=list,
        description=(
            "Observable conditions, true or false after the steps, that a broken implementation "
            "would fail."
        ),
    )
    derivation: str = Field(
        default="", description="'direct', 'assumed', or 'inferred'. Leave empty to keep as-is."
    )
    assumptions: list[str] = Field(
        default_factory=list,
        description="Required when derivation is not 'direct': what you supplied beyond the text.",
    )
    notes: str = ""


class RepairedTestCases(BaseModel):
    """What a repair pass returns: the rejected cases, fixed. Never the whole suite.

    Kept small deliberately. A repair step typed as the whole suite would have to re-emit every
    case inside one bounded reply, which past a few dozen cannot be done — and what comes back is
    a fraction of the suite with no record of what went missing. Everything that passed is carried
    across untouched by deterministic code.
    """

    cases: list[RepairedTestCase] = Field(
        default_factory=list,
        description=(
            "One entry per rejected case you fixed, carrying its ORIGINAL `id` unchanged. Never "
            "include a case that was not rejected: it is preserved anyway and yours would be "
            "ignored."
        ),
    )
    deleted: list[str] = Field(
        default_factory=list,
        description=(
            "Ids of rejected cases you are deliberately dropping because no rewording could make "
            "them check anything. Listing one here is clearer than silently omitting it, and a "
            "deleted case is recorded as an uncovered gap rather than vanishing."
        ),
    )


class DeferredRequirement(BaseModel):
    """A requirement that will not get a case, and why. An honest gap, recorded as one."""

    requirement_id: str
    route: str
    reason: str
    band: str = ""
    attach_to: str | None = None


class TestSuite(BaseModel):
    """Everything the design and assembly stages produced, plus what they chose not to."""

    scenarios: list[TestScenario] = Field(default_factory=list)
    deferred: list[DeferredRequirement] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    counts: dict[str, int] = Field(default_factory=dict)

    def cases(self) -> list[TestCase]:
        return [case for scenario in self.scenarios for case in scenario.cases]

    def cases_for(self, requirement_id: str) -> list[TestCase]:
        return [case for case in self.cases() if requirement_id in case.requirement_ids]

    def covered_requirement_ids(self) -> set[str]:
        return {rid for case in self.cases() for rid in case.requirement_ids}


# ------------------------------------------------------------------------------------------------
# What the model returns. Kept separate from the assembled shape on purpose: a designer supplies
# judgement, and everything derivable — ids, hints, readiness, regression flags — is computed
# afterwards. Asking a model for a field that can be computed invites it to be inconsistent.
# ------------------------------------------------------------------------------------------------


class TestCaseDraft(BaseModel):
    """One case as designed, before identifiers and derived metadata are stamped on."""

    requirement_id: str = Field(
        description="The exact id of the requirement this validates, copied from the packet."
    )
    dimension: str = Field(
        description=(
            "Which of the dimensions you were given this case exercises. Use only a dimension "
            "listed for this requirement — the set was decided from what the requirement actually "
            "says, and adding one means testing something nobody asked for."
        )
    )
    title: str = Field(description="Short and distinct within the requirement.")
    objective: str = Field(
        description=(
            "THE RISK THIS CASE RETIRES, in one sentence. Not what the case does. 'Confirm the "
            "system refuses rather than silently succeeding when the identifier matches nothing — "
            "a silent success would let an operator believe a removal happened that did not.' is "
            "an objective. 'Verify the removal works' is not, and will be rejected."
        )
    )
    covers_criterion: int | None = Field(
        default=None,
        description=(
            "Zero-based index of the acceptance criterion this case checks, from the list given "
            "for this requirement. Null when the case comes from the statement instead."
        ),
    )
    preconditions: list[str] = Field(
        default_factory=list,
        description=(
            "What must already be true. Required whenever the case acts on something that has to "
            "exist first — a case that removes an item without establishing one tests nothing."
        ),
    )
    inputs: list[TestInput] = Field(
        default_factory=list,
        description=(
            "The data this case supplies. REQUIRED for validation, boundary, negative, and "
            "authorization cases: those dimensions are *about* the input, so a case without one "
            "has not said what it is testing."
        ),
    )
    steps: list[str] = Field(
        default_factory=list,
        description=(
            "What the actor does, in the product's own vocabulary. Never a client, command, tool, "
            "or library: 'submit a removal request for that identifier', never 'send a DELETE'."
        ),
    )
    expected_results: list[str] = Field(
        default_factory=list,
        description=(
            "Observable conditions that are true or false after the steps, checkable by someone "
            "who has never seen the code. Never 'works correctly', 'behaves appropriately', "
            "'returns successfully', or 'no errors occur' — each of those passes against a broken "
            "implementation. Never a restatement of the requirement: the requirement says what "
            "must be true, this says how you would find out."
        ),
    )
    derivation: str = Field(
        default="inferred",
        description=(
            "'direct' when this checks a stated acceptance criterion, 'assumed' when you had to "
            "supply something the requirement does not fix, 'inferred' when it comes from the "
            "statement alone."
        ),
    )
    assumptions: list[str] = Field(
        default_factory=list,
        description=(
            "REQUIRED when derivation is not 'direct'. Each must be something a reviewer can "
            "confirm or deny — name what you supplied that the requirement did not."
        ),
    )
    risk_reason: str = Field(
        default="", description="One clause on what goes wrong if this behaviour is broken."
    )
    notes: str = Field(
        default="", description="Optional context for a test engineer. May be technical."
    )


class TestScenarioDraft(BaseModel):
    """A situation under test, and the cases that share it."""

    requirement_id: str = Field(description="The requirement this scenario belongs to.")
    title: str = Field(description="The situation, not the requirement restated.")
    context: str = Field(
        default="",
        description="One sentence on the state of the world these cases run under.",
    )
    preconditions: list[str] = Field(
        default_factory=list, description="Conditions shared by every case in this scenario."
    )
    cases: list[TestCaseDraft] = Field(default_factory=list)


class TestDesignFragment(BaseModel):
    """What one fan-out worker returns for one batch of requirements."""

    scenarios: list[TestScenarioDraft] = Field(
        default_factory=list,
        description=(
            "Group cases into scenarios by SITUATION, not one scenario per requirement. A "
            "requirement whose cases split into 'the item exists' and 'no item matches' is two "
            "scenarios; one whose cases all share a starting state is one."
        ),
    )
    notes: list[str] = Field(
        default_factory=list,
        description=(
            "Anything you could not do honestly — a requirement too vague to derive a falsifiable "
            "case from, a dimension you were given that the text does not actually support. An "
            "honest gap recorded here is worth more than a case that checks nothing."
        ),
    )

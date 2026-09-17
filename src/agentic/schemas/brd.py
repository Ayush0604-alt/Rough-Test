"""Output schemas for the `codebase-requirements` pack.

These are the shapes `evidence-grounded-requirement-synthesis` prescribes. They are registered by
name in `schemas/registry.py`, so `pack.yaml` can name one as a step's `output_schema` without the
graph code knowing anything about requirements specifically.

Field docstrings matter here: they are handed to the model verbatim as the JSON-schema
descriptions when the step runs under `.with_structured_output(...)`.

## The classification model

Two fields decide where a finding goes, and they constrain each other:

- `abstraction_basis` — *the test the behaviour passed*. Asked first, and answered before
  anything else about the item is decided.
- `requirement_type` — read off the basis by a fixed table (`BASIS_TYPES`), never chosen
  independently.

That split is the whole design. When the model was asked only for a type, the taxonomy offered
nowhere to put a behaviour that was interesting but not required of the product, and discarding
it would have thrown away real evidence — so every internal step, helper, and conditional was
promoted to a functional requirement. Asking for the grounds first gives
`implementation_detail` a destination (`observation`), which is kept in the traceability model
and never published as a requirement. Over-extraction stops being the path of least resistance.

    business_outcome      -> business              (why the product exists)
    external_capability   -> functional            (what an outside actor can do and observe)
    internal_behaviour    -> system                (what the system must do, however built)
    domain_constraint     -> business_rule         (a rule the business imposes)
    platform_constraint   -> technical_constraint  (a limit the platform imposes)
    quality_property      -> non_functional        (a measurable property)
    implementation_detail -> observation           (true of this code, not required)

An item also carries a `parent` and a `parent_relation` — refines, enables, constrains,
validates, or evidences. The relation is what makes the hierarchy semantic: a child must do one
of those things to its parent. Two items sharing a call path, a file, or a module are *not*
thereby parent and child, and a link that cannot name its relation is dropped rather than
published. `PARENT_RULES` says which tiers may hang from which, and under which relation.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from agentic.schemas.core import SourceLocation

# The abstraction test, and the class each answer produces. Every extracted behaviour must answer
# exactly one of these affirmatively, and the answer *determines* its classification.
#
# Splitting "what kind of requirement is this" from "on what grounds" is the fix for
# over-extraction. Asking only for a type let any interesting behaviour be filed as a capability,
# because the taxonomy offered nowhere else to put it and discarding it would have lost the
# evidence. Asking for the grounds first makes "it exists as a function in the code" an answer
# that leads somewhere other than the requirements list.
#
# One entry per basis, so the grounds and the class can never disagree: a behaviour whose only
# claim to existence is that the current code does it lands in `observation`, which is retained as
# evidence and never published as a requirement.
BASIS_TYPES = {
    # an outcome the product exists to deliver
    "business_outcome": "business",
    # something an actor outside the system can do and observe
    "external_capability": "functional",
    # behaviour the system must exhibit, whatever the implementation
    "internal_behaviour": "system",
    # a rule the business imposes
    "domain_constraint": "business_rule",
    # a limit the platform, protocol, or deployment imposes
    "platform_constraint": "technical_constraint",
    # a measurable property rather than a behaviour
    "quality_property": "non_functional",
    # true of this code, not required of the product
    "implementation_detail": "observation",
}

# Tier -> the section holding it. The section *is* the tier: there is deliberately no second
# vocabulary for the same distinction, because two that can disagree always eventually do.
TIER_SECTIONS = {
    "business": "business_requirements",
    "functional": "functional_requirements",
    "system": "system_requirements",
    "business_rule": "business_rules",
    "technical_constraint": "technical_constraints",
    "non_functional": "non_functional_requirements",
    "observation": "observations",
}

# Tier -> published id prefix. These are the ids stamped on the items themselves, so a gate
# message, the requirements document, the traceability matrix, and the JSON set all name a
# requirement the same way.
TIER_PREFIXES = {
    "business": "BR",
    "functional": "FR",
    "system": "SYS",
    "business_rule": "RULE",
    "technical_constraint": "TC",
    "non_functional": "NFR",
    "observation": "OBS",
}

# Which relations each tier may claim, and which tiers it may claim them against. A child must
# refine, enable, or constrain its parent; sharing a call path is not a relationship.
PARENT_RULES: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    # tier: (permitted parent tiers, permitted relations)
    "business": ((), ()),
    # A capability may sit under another capability as well as under an outcome. Products
    # decompose that way — a workflow with named stages, a feature with parts — and forbidding it
    # forced a choice between two wrong answers: publish the stages flat, or invent an outcome to
    # hold them. It also cost real structure whenever an outcome was found to be unsupported and
    # published as a capability instead: everything beneath it was orphaned by the demotion, and
    # the document lost a grouping the evidence had established.
    "functional": (("business", "functional"), ("refines", "enables")),
    "system": (("functional", "business"), ("enables", "refines")),
    "business_rule": (("functional", "system"), ("constrains",)),
    "technical_constraint": (("functional", "system"), ("constrains",)),
    "non_functional": (("functional", "system", "business"), ("constrains", "refines")),
    "observation": (
        ("functional", "system", "business_rule", "technical_constraint", "business"),
        ("evidences",),
    ),
}

ID_PREFIXES = {
    "business_requirements": "BR",
    "functional_requirements": "FR",
    "system_requirements": "SYS",
    "business_rules": "RULE",
    "technical_constraints": "TC",
    # Retained as evidence, never published as a requirement.
    "observations": "OBS",
    # Not a tier: a success criterion is an observable condition on a requirement, and is rendered
    # as one. It keeps its own numbering because it is addressable in review like anything else.
    "success_criteria": "SC",
}

# Requirement families that sit alongside the chain rather than inside it. They are genuinely
# different assertions — a database invariant, an external system the product depends on, a
# quality property, a report someone reads — and folding them into the functional tier, which is
# what happened before, both mislabelled them and hid them. None of them takes a parent: an
# integration is not "realised by" anything, it is depended upon.
SUPPLEMENT_PREFIXES = {
    "data_requirements": "DATA",
    "integration_requirements": "INT",
    "non_functional_requirements": "NFR",
    "reporting_requirements": "RPT",
}

# Priority derived from the code's own defaults. Not a stakeholder ranking, and labelled as such
# wherever it is published.
PRIORITIES = {
    "M": "Evidenced as core / always on",
    "S": "Evidenced behind a configuration flag or role gate",
    "C": "Evidenced as legacy, opt-in, or deprecating",
}

# Which tier an item belongs to when it does not name one itself.
SECTION_TIERS = {
    "business_requirements": "business",
    "functional_requirements": "functional",
    "system_requirements": "system",
    "business_rules": "business_rule",
    "technical_constraints": "technical_constraint",
    "observations": "observation",
    "non_functional_requirements": "non_functional",
    "success_criteria": "functional",
}


def permitted_parents(tier: str) -> tuple[str, ...]:
    """Which tiers an item of this tier may legitimately hang from."""
    return PARENT_RULES.get(tier, ((), ()))[0]


def permitted_relations(tier: str) -> tuple[str, ...]:
    """How an item of this tier may relate to its parent."""
    return PARENT_RULES.get(tier, ((), ()))[1]


def canonical_basis(declared: str | None) -> str | None:
    """The abstraction basis a declared value means, or None when it means nothing recognised."""
    cleaned = (declared or "").strip().lower().replace("-", "_").replace(" ", "_")
    return cleaned if cleaned in BASIS_TYPES else None

# Vocabulary the model, older prompts, and the standards taxonomies reach for, folded onto the
# three tiers. Folding rather than rejecting is deliberate: an item typed `technical` is a
# grounded finding that named itself badly, and dropping it would lose evidence over a label.
# The mapping is one-way and lossy on purpose — the seven-type distinction was never stable
# enough to be worth preserving.
TIER_ALIASES = {
    "business": "business",
    "business_requirement": "business",
    "objective": "business",
    "business_objective": "business",
    "stakeholder": "business",
    "functional": "functional",
    "functional_requirement": "functional",
    "scope": "functional",
    "capability": "functional",
    "technical": "technical_constraint",
    "transition": "functional",
    "business_rule": "business_rule",
    "rule": "business_rule",
    "policy": "business_rule",
    "system": "system",
    "system_requirement": "system",
    "internal": "system",
    "technical_constraint": "technical_constraint",
    "system_rule": "technical_constraint",
    "constraint": "technical_constraint",
    "non_functional": "non_functional",
    "nonfunctional": "non_functional",
    "quality": "non_functional",
    "observation": "observation",
    "implementation_observation": "observation",
    "implementation_detail": "observation",
}


def canonical_tier(declared: str | None, *, default: str | None = None) -> str | None:
    """The tier a declared type means, or `default` when it means nothing recognisable."""
    return TIER_ALIASES.get((declared or "").strip().lower().replace("-", "_"), default)


# What a success criterion is published as. It is not a requirement tier, and publishing it as
# `functional` — which is what happened while the tier was derived from the section alone — told
# every downstream reader that an observable condition was a capability.
SUCCESS_CRITERION = "success_criterion"


# Section -> what a reader sees this family called, for families outside the three-tier chain.
NON_TIER_KINDS = {
    "success_criteria": SUCCESS_CRITERION,
    "data_requirements": "data",
    "integration_requirements": "integration",
    "non_functional_requirements": "non_functional",
    "reporting_requirements": "reporting",
}


def published_kind(item: "BRDItem", section: str) -> str:
    """The classification a reader sees: one of the three tiers, or the family's own name.

    Read off the declared basis wherever one exists, rather than off the stored
    `requirement_type`. The basis is the answer to the question that was asked first, and
    `BASIS_TYPES` is a total function from it to a tier, so a document derived this way cannot
    publish a type that contradicts the grounds given for it.

    The consolidation pass already forces the stored field to agree, and this makes that agreement
    structural instead of maintained. Two fields that can disagree always eventually do -- usually
    on the one path nobody exercised, which here would be any route that reached rendering without
    passing through consolidation.
    """
    if section in NON_TIER_KINDS:
        return NON_TIER_KINDS[section]
    basis = canonical_basis(item.abstraction_basis)
    if basis is not None:
        return BASIS_TYPES[basis]
    return item.tier(section)


def title_key(title: str) -> str:
    """How two requirement titles are compared when resolving a parent link.

    The model writes a parent as a title, because ids do not exist yet when it runs. It reliably
    reproduces the title with different casing, a trailing full stop, or collapsed whitespace, and
    none of those are a different requirement.
    """
    return " ".join((title or "").split()).casefold().strip(" .:;,")


class CorrespondenceOutcome(BaseModel):
    """What checking a claim against the code it cites found.

    Recorded rather than acted on. A claim that fails is marked and published with its reason
    attached, never deleted: a reverse-engineering document whose disputed claims silently vanish
    is less useful than one that says which claims it is unsure of and why.
    """

    verdict: str = Field(
        default="unchecked",
        description="'supported', 'rejected', or 'unchecked' when no check could be run.",
    )
    checks_failed: list[str] = Field(
        default_factory=list,
        description="Names of the deterministic checks that failed, if any.",
    )
    reason: str = Field(default="", description="One sentence on what disagreed.")
    checked_by: str = Field(
        default="",
        description="'deterministic' or 'entailment', naming what reached the verdict.",
    )
    objective: bool = Field(
        default=False,
        description=(
            "Whether the failure was a fact about the evidence rather than a reading of it. An "
            "interpretive failure alone makes a claim an open question; it does not reject it."
        ),
    )


class BRDItem(BaseModel):
    """One evidence-grounded claim in the requirements document."""

    id: str = Field(
        default="",
        description="Leave empty. The system assigns identifiers deterministically.",
    )
    title: str = Field(
        description=(
            "Short requirement title naming the capability, not the construct behind it. Three "
            "to six words, in the product's own vocabulary, concrete enough to tell this "
            "requirement from its neighbours: 'Remove a user account', not 'User Management'. "
            "Titles are how the parent link is written, so make every title distinct."
        )
    )
    statement: str = Field(
        description=(
            "One atomic, testable 'shall' statement describing implemented behaviour, written "
            "out in full so a reader needs nothing else to understand it. Carry four parts in "
            "order: the actor, in the product's own word for them; the single action and its "
            "object; the qualifier that identifies, selects, or conditions it (what the actor "
            "supplies, which item it applies to, when it applies); and the observable outcome, "
            "introduced by 'so that', 'after which', or 'and receive'. Two grammars cover "
            "almost everything: 'A <actor> shall be able to <action> <object> <qualifier>, so "
            "that <observable outcome>.' for a capability, and 'When <trigger>, the system "
            "shall <behaviour>, so that <consequence>.' for behaviour the product enforces. "
            "One sentence, active voice, typically 25-45 words; a statement under about fifteen "
            "has dropped its qualifier or its outcome. Never pad and never invent an outcome the "
            "evidence does not fix. Say 'the system' only where no person triggers the "
            "behaviour. Domain vocabulary the product itself uses is fine. What must not appear "
            "is the implementation behind it: file names, function or class names, route paths, "
            "and the names of frameworks, libraries, and datastores. Those belong in rationale, "
            "evidence, and graph_path, where they are wanted."
        )
    )
    rationale: str = Field(
        default="",
        description="Why the cited evidence supports this claim. Technical terms are allowed here.",
    )
    confidence: float = Field(
        ge=0.0,
        le=1.0,
        description="1.0 only for explicit, unambiguous intent. Lower it when evidence is partial.",
    )
    evidence: list[SourceLocation] = Field(
        default_factory=list,
        description=(
            "At least one real citation. Use only file paths that appear in the supplied evidence "
            "packet, and line ranges taken from it — never invented."
        ),
    )
    graph_path: list[str] = Field(
        default_factory=list,
        description=(
            "Ordered exact qualified names of graph nodes connecting this claim to an interaction "
            "boundary (an endpoint, CLI entry point, scheduled job, or message handler)."
        ),
    )
    abstraction_basis: str | None = Field(
        default=None,
        description=(
            "THE TEST THIS BEHAVIOUR PASSED. Decide this BEFORE deciding anything else, because "
            "it settles the classification. Ask, in order, and stop at the first that is true:\n"
            "- 'business_outcome': is this an outcome the product exists to deliver, that a "
            "sponsor would recognise and that survives rebuilding the implementation?\n"
            "- 'external_capability': can an actor outside the system do this and observe the "
            "result, without knowing how it is built?\n"
            "- 'internal_behaviour': must the system behave this way for a capability to hold, "
            "independently of how it is currently written — a different implementation would "
            "still have to do it?\n"
            "- 'domain_constraint': is this a rule the business imposes, true of the domain "
            "rather than of the code?\n"
            "- 'platform_constraint': is this a limit imposed by the platform, protocol, "
            "runtime, or deployment rather than chosen by the business?\n"
            "- 'quality_property': is this a measurable property (a limit, a timeout, a "
            "guarantee) rather than a behaviour?\n"
            "- 'implementation_detail': none of the above. It is true of this code and would "
            "vanish on a reasonable rewrite.\n"
            "Answering 'implementation_detail' is a correct and common outcome, not a failure. "
            "A function existing, a variable being set, a helper being called, an internal step "
            "running, or a condition being evaluated are implementation details unless they "
            "independently pass one of the earlier tests. Say so and the finding is still kept, "
            "as evidence, under the requirement it supports."
        ),
    )
    parent: str | None = Field(
        default=None,
        description=(
            "The exact title of the requirement this one refines, enables, constrains, or "
            "evidences. Choose it by MEANING, never by which code the two happen to share: two "
            "items that sit in the same file, the same call chain, or the same module are not "
            "thereby parent and child. Ask instead whether this item makes the parent more "
            "specific, makes it possible, limits when it applies, or is evidence for it. If no "
            "published requirement stands in one of those relations, leave this null rather than "
            "attaching to the nearest thing. Null for a business requirement, which is the top. "
            "Copy the title exactly as you wrote it — never an id, never a paraphrase."
        ),
    )
    parent_relation: str | None = Field(
        default=None,
        description=(
            "How this item relates to `parent`: 'refines' (a more specific statement of it), "
            "'enables' (it could not hold without this), 'constrains' (limits when or how it "
            "applies), 'validates' (an observable condition proving it), or 'evidences' (an "
            "implementation observation supporting it). If you cannot name the relation, there "
            "is no hierarchy here — leave both this and `parent` null."
        ),
    )
    parent_id: str = Field(
        default="",
        description="Leave empty. The system resolves the parent title to an identifier.",
    )
    persona: str | None = Field(
        default=None,
        description=(
            "The evidenced actor this serves — who reaches the boundary this claim traces to, "
            "and the same word the statement opens with. Take it from interface labels, declared "
            "documentation, or a role named in an authorisation check. Null when the evidence "
            "names no particular actor; never a guess about who would plausibly use a product "
            "like this."
        ),
    )
    business_value: str | None = Field(
        default=None,
        description=(
            "The evidenced outcome this delivers, stated as what the actor gets — the same "
            "outcome the statement ends on, in a phrase. Null when the traced behaviour fixes no "
            "stated result. Not a benefit you would expect a product like this to have; only one "
            "the traced behaviour actually produces."
        ),
    )
    acceptance_criteria: list[str] = Field(
        default_factory=list,
        description=(
            "Observable pass/fail conditions for this requirement, each checkable by running the "
            "system. Use the evidenced call parameters, configured defaults, processing stages, "
            "return outcomes, fallbacks, and error handling. A functional requirement normally "
            "carries three to eight, ordered or independently observable, describing its "
            "behaviour at the level the source evidence shows. Held to the same language bar as "
            "the statement: no implementation names, and no unmeasured quality claim "
            "('efficient', 'accurate', 'correctly', 'appropriate') — a criterion that cannot be "
            "failed is not a criterion. Phrase each as a condition that is true or false after a "
            "described action, checkable by someone who has never seen the code."
        ),
    )
    area: str = Field(
        default="",
        description=(
            "The 2-4 letter uppercase code for the part of the product THIS requirement belongs "
            "to, chosen from what it does rather than from where the code lives. One entry point "
            "routinely fans out into several unrelated areas — a single chat box may reach "
            "safety checking, routing, two different answering paths, and history — and each of "
            "those is a separate area with its own requirements. Use the same code as other "
            "requirements about the same part of the product; that is what groups them under one "
            "heading. Leave empty to inherit the cluster's own area."
        ),
    )
    priority: str = Field(
        default="M",
        description=(
            "Derived from the code's own defaults, not from a stakeholder ranking. 'M' when the "
            "behaviour is always on — no flag guards it, or its flag defaults to enabled. 'S' "
            "when it sits behind a configuration flag, a role gate, or a setting that defaults "
            "off. 'C' when the code marks it legacy, deprecated, opt-in, or superseded."
        ),
    )
    requirement_type: str | None = Field(
        default=None,
        description=(
            "Which tier of the classification model this claim sits at. Exactly one of: "
            "'business', 'functional', 'business_rule'. The three form a decomposition chain — a "
            "business requirement is realised by functional requirements, each of which may be "
            "constrained by business rules — so choose by asking which question the claim "
            "answers. 'business': WHY the product exists — an outcome someone commissioned it "
            "for, broader than any single control, and still true if the whole implementation "
            "were rebuilt differently. 'functional': WHAT the product does — an evidenced "
            "capability or a distinct processing stage that realises one of those outcomes. "
            "'business_rule': UNDER WHAT CONSTRAINT — a condition the product enforces, with a "
            "triggering condition, a required behaviour, and an observable consequence. If a "
            "claim names a stage of work, it is functional however important it is; if it "
            "restates an enforced condition, it is a rule whichever list it arrived in. There is "
            "no fourth option: quality, technical, and migration observations belong to the "
            "functional requirement they describe, as acceptance criteria."
        ),
    )
    assumptions: list[str] = Field(
        default_factory=list,
        description="Implementation uncertainty this claim rests on and that needs validating.",
    )
    open_questions: list[str] = Field(
        default_factory=list,
        description="Questions the evidence cannot settle and that need a stakeholder answer.",
    )
    implementation_observations: list[str] = Field(
        default_factory=list,
        description=(
            "Leave empty. The system records here the implementation detail held out of the "
            "requirement statement, and any behaviour reclassified as a description of the "
            "current code. Kept so the detail stays traceable rather than being lost."
        ),
    )
    disposition: str = Field(
        default="",
        description=(
            "Leave empty. The system records what was decided about this candidate: 'keep', "
            "'reclassify', 'observation', 'open_question', or 'reject'."
        ),
    )
    resolution_reason: str = Field(
        default="",
        description="Leave empty. The system records why that decision was reached.",
    )
    cluster_conflict: bool = Field(
        default=False,
        description=(
            "Leave empty. Set when this candidate's cluster holds competing readings of the same "
            "evidence, which resolution must settle before any of them is published."
        ),
    )
    cluster_id: str = Field(
        default="",
        description=(
            "Leave empty. The system records which repository-wide capability cluster this "
            "candidate belongs to, so a merge and a hierarchy link can be traced back to the "
            "group they were decided from."
        ),
    )
    model_confidence: float | None = Field(
        default=None,
        description=(
            "Leave empty. The system records here the confidence originally reported, before it "
            "was recomputed from the evidence, so the two can be compared."
        ),
    )
    superseded: list[str] = Field(
        default_factory=list,
        description=(
            "Leave empty. The system records here the titles of claims merged into this one, so "
            "a reader can see that two findings were folded together rather than one being lost."
        ),
    )
    status: str = Field(
        default="candidate",
        description=(
            "Leave empty. The system sets this. 'candidate' until a claim has been checked "
            "against the code it cites; 'rejected' when the check found the two disagree."
        ),
    )
    validation: CorrespondenceOutcome | None = Field(
        default=None,
        description="Leave empty. The system records the outcome of the correspondence check.",
    )

    def tier(self, section: str | None = None) -> str:
        """The tier this item is published at, falling back to the section holding it."""
        default = SECTION_TIERS.get(section or "", "functional")
        return canonical_tier(self.requirement_type, default=default) or default


class AreaLabel(BaseModel):
    """The published heading for one functional-area code."""

    code: str = Field(description="The 2-4 letter uppercase code, exactly as used on the items.")
    name: str = Field(
        description="Its heading, in the product's own business words. No file or module names."
    )


class BRDFragment(BaseModel):
    """What one fan-out worker returns for a single interaction-boundary cluster.

    Deliberately has no product purpose or business problem: those are whole-repository judgements
    that only the reduce step, which sees every fragment, is in a position to make.

    It also has no business-requirement list. A single boundary cannot establish an outcome the
    whole product exists for, so a worker that returns one is guessing — the reduce step, which
    sees every fragment at once, writes the top tier.
    """

    purpose_hint: str = Field(
        default="",
        description=(
            "One sentence on what this cluster appears to let someone accomplish. A hint for the "
            "step that writes the top tier, not a published statement."
        ),
    )
    area_code: str = Field(
        default="",
        description=(
            "The 2-4 letter uppercase code for the area MOST of this cluster's requirements "
            "belong to. Items that set their own `area` override it. Choose the code from what "
            "the code does, not from where it lives, and invent it from this product's own "
            "subject matter rather than from any standard list. Clusters covering the same part "
            "of the product must use the same code — that is what groups their requirements "
            "under one heading, and what a reader cites."
        ),
    )
    area_name: str = Field(
        default="",
        description=(
            "The heading `area_code` is published under, in this product's own business words. "
            "Three or four words, no file or module names."
        ),
    )
    areas: list[AreaLabel] = Field(
        default_factory=list,
        description=(
            "A name for every area code used anywhere in this reply, including any set on an "
            "individual requirement. Without an entry here an area is published under a "
            "placeholder heading, so list each one you used exactly once."
        ),
    )
    functional_requirements: list[BRDItem] = Field(
        default_factory=list,
        description=(
            "The capabilities and distinct processing stages this boundary sets off, each typed "
            "'functional'. Leave `parent` null: the business requirement they realise does not "
            "exist yet and is written by the reduce step."
        ),
    )
    business_rules: list[BRDItem] = Field(
        default_factory=list,
        description=(
            "Conditions this boundary enforces, each typed 'business_rule', and each naming in "
            "`parent` the exact title of the functional requirement above that it constrains."
        ),
    )
    success_criteria: list[BRDItem] = Field(
        default_factory=list,
        description=(
            "Observable pass/fail conditions the evidence fixes, each naming in `parent` the "
            "exact title of the functional requirement it validates."
        ),
    )
    system_requirements: list[BRDItem] = Field(
        default_factory=list,
        description=(
            "Behaviour the system must exhibit for a capability above it to hold, which no actor "
            "outside the system observes directly. A different implementation would still have "
            "to do it. Each carries `abstraction_basis` 'internal_behaviour' and names in "
            "`parent` the capability it enables."
        ),
    )
    technical_constraints: list[BRDItem] = Field(
        default_factory=list,
        description=(
            "Limits imposed by the platform, protocol, runtime, or deployment rather than chosen "
            "by the business. Each carries `abstraction_basis` 'platform_constraint' and names "
            "the requirement it constrains."
        ),
    )
    observations: list[BRDItem] = Field(
        default_factory=list,
        description=(
            "Behaviour that is true of this code but is not required of the product — the "
            "implementation details you decided against promoting. Keep them here rather than "
            "discarding them: they are real reverse-engineering evidence, they are published in "
            "the traceability appendix, and recording one is the correct outcome of the "
            "abstraction test rather than a failure to find a requirement. Each carries "
            "`abstraction_basis` 'implementation_detail' and names in `parent` the requirement it "
            "is evidence for, with `parent_relation` 'evidences'."
        ),
    )
    data_requirements: list[BRDItem] = Field(
        default_factory=list,
        description=(
            "Invariants the stored data must hold, where this cluster's evidence shows one: a "
            "uniqueness or nullability guarantee, an enumerated vocabulary that must stay in "
            "step across layers, an identifier scheme, a retention or archival rule. Evidence is "
            "usually a schema constraint, a column comment, or a shared constant."
        ),
    )
    integration_requirements: list[BRDItem] = Field(
        default_factory=list,
        description=(
            "External systems this cluster depends on and what it needs from each — the service, "
            "the operations used, and how the product behaves when it is unavailable. One per "
            "external system, not one per call site."
        ),
    )
    non_functional_requirements: list[BRDItem] = Field(
        default_factory=list,
        description=(
            "Quality properties this cluster's evidence fixes concretely: a concurrency guard, a "
            "timeout, a size cap, a retry bound, an authorisation gate, a startup guarantee, a "
            "masked secret. Only where the code fixes a specific, checkable property — never a "
            "general aspiration like 'the system shall be performant'."
        ),
    )
    reporting_requirements: list[BRDItem] = Field(
        default_factory=list,
        description=(
            "Views, exports, dashboards, and summaries this cluster makes available to someone, "
            "including the ordering or filtering the evidence fixes."
        ),
    )


class OutcomeBinding(BaseModel):
    """Which capabilities realise one business requirement.

    Bindings used to be made per *area* -- a code-locality grouping -- so every capability found
    in the same part of the tree inherited the same outcome. That is a structural relationship
    wearing a semantic one's clothes: capabilities sitting in one module frequently serve
    different outcomes, and one outcome is frequently realised across several modules. Naming the
    capabilities individually is the only way the link can mean "this realises that".
    """

    business_requirement: str = Field(
        description="The exact title of one business requirement from your own list."
    )
    realised_by: list[str] = Field(
        default_factory=list,
        description=(
            "The exact titles of the capabilities that realise it, copied from the digest. Judge "
            "each one on what it achieves, not on where it was found: capabilities listed under "
            "one area routinely serve different outcomes, and one outcome is routinely realised "
            "by capabilities drawn from several areas. A capability belongs here only if this "
            "outcome would be incomplete without it. Leave one out of every binding rather than "
            "placing it where it does not belong: an unattached capability is reported as a gap, "
            "which is honest, whereas a wrong parent is a false statement about the product."
        ),
    )


class TopTier(BaseModel):
    """The whole-repository judgements, written from a digest rather than from full requirements.

    This is the step that used to be `reduce`, and the change is the point of the rebuild. `reduce`
    was handed every requirement from every cluster and asked to return the finished document in
    one structured reply. With an 8k output ceiling that is arithmetically impossible past about a
    dozen requirements: a platform with two hundred grounded findings had them silently compressed
    to five, and the loss looked like a modelling failure rather than a budget one.

    Nothing here re-emits a requirement. The functional requirements, rules, and criteria the
    fan-out workers produced are already grounded, already written out in full, and already
    checked — they are carried through by deterministic assembly. This call sees only area names
    and requirement *titles*, and returns only what genuinely needs the whole picture: what the
    product is for, the handful of outcomes it exists to deliver, and which area serves which.
    Its input and output are both small and stay small as the repository grows.
    """

    product_purpose: BRDItem | None = Field(
        default=None,
        description=(
            "What the product enables and for whom, in business language. Null if the evidence "
            "cannot establish it — do not invent a plausible-sounding purpose."
        ),
    )
    business_problem: BRDItem | None = Field(
        default=None,
        description="The evidenced situation and desired change, or null if not established.",
    )
    business_requirements: list[BRDItem] = Field(
        default_factory=list,
        description=(
            "The outcomes this product exists to deliver, each typed 'business' with `parent` "
            "null. Write one for each genuinely distinct outcome the areas add up to — for a "
            "product of a few areas that is two or three, for a broad platform it may be eight "
            "or ten. Each must stand over at least one whole area and be an outcome someone "
            "commissioned the product for, never an area renamed."
        ),
    )
    bindings: list[OutcomeBinding] = Field(
        default_factory=list,
        description=(
            "One entry per business requirement, naming the capabilities that realise it. This is "
            "how hundreds of functional requirements attach to the top tier without any of them "
            "being restated -- you send titles, not statements. Every capability in the digest "
            "should appear in exactly one binding; one that genuinely serves no outcome you can "
            "evidence is left out and reported."
        ),
    )
    assumptions: list[str] = Field(
        default_factory=list,
        description=(
            "Interpretation-affecting conditions the digest shows — single tenancy, shared "
            "configuration, a stand-in left in place. Evidenced, never supposed."
        ),
    )
    out_of_scope: list[str] = Field(
        default_factory=list,
        description=(
            "Capabilities a reader of a product like this would assume exist and which no area "
            "provides. State the absence plainly so nobody infers the capability."
        ),
    )
    open_questions: list[str] = Field(
        default_factory=list,
        description="Genuine ambiguities a stakeholder must settle. Never invented to fill space.",
    )


class RepairedItems(BaseModel):
    """What a repair pass returns: the rejected claims, fixed. Never the whole document.

    The repair step used to be typed `ProductBRD`, so a model asked to correct three wordings had
    to re-emit every requirement in the set inside one bounded reply. Past a few dozen items that
    cannot be done, and what came back was a fraction of the document with no record of what went
    missing -- a gate rejection over one phrase silently deleted most of the analysis.

    Returning only the repaired items keeps the reply small whatever the document's size, and
    everything that passed is carried across untouched by deterministic code.
    """

    items: list[BRDItem] = Field(
        default_factory=list,
        description=(
            "One entry for each rejected claim you fixed, carrying its ORIGINAL `id` unchanged -- "
            "that id is how the fix is matched back to the claim it replaces, and an entry "
            "without one is discarded. Return the whole item, not a patch: every field you want "
            "kept must be present, because the entry replaces the original outright. Omit a "
            "rejected claim entirely to delete it, which is the right answer only when its "
            "evidence genuinely cannot support it. Never include a claim that was not rejected."
        ),
    )
    deleted: list[str] = Field(
        default_factory=list,
        description=(
            "Ids of rejected claims you are deliberately dropping because no wording could make "
            "them true. Listing one here is clearer than silently omitting it."
        ),
    )


class ProductBRD(BaseModel):
    """The merged, whole-repository requirements set.

    `product_purpose` and `business_problem` are nullable on purpose. The synthesis skill is
    explicit that an honest "could not be established" beats an invented purpose.
    """

    product_purpose: BRDItem | None = Field(
        default=None,
        description=(
            "What the product enables and for whom, in business language. Return null if the "
            "evidence cannot establish it — do not invent a plausible-sounding purpose."
        ),
    )
    business_problem: BRDItem | None = Field(
        default=None,
        description="The evidenced situation and desired change, or null if not established.",
    )
    business_requirements: list[BRDItem] = Field(
        default_factory=list,
        description=(
            "Tier 1. The small number of outcomes this product exists to deliver, each typed "
            "'business' with `parent` null — typically what gets material in and what the product "
            "gives back out. A worked document of this kind runs about one business requirement "
            "for every two to three functional requirements: enough that the reader sees why the "
            "product was built, few enough that each one is an outcome rather than a stage. Never "
            "a function, class, module, or single control restated. Empty is correct only when "
            "nothing grounded at all."
        ),
    )
    functional_requirements: list[BRDItem] = Field(
        default_factory=list,
        description=(
            "Tier 2. The capabilities and processing stages that realise the business "
            "requirements, each typed 'functional' and naming in `parent` the exact title of the "
            "business requirement it realises. A stage may cite the same lines as the business "
            "requirement above it."
        ),
    )
    business_rules: list[BRDItem] = Field(
        default_factory=list,
        description=(
            "Tier 3. Evidenced policies: triggering condition, required behaviour, observable "
            "consequence. Each typed 'business_rule' and naming in `parent` the exact title of "
            "the functional requirement it constrains. An ordinary internal conditional is not a "
            "business rule."
        ),
    )
    success_criteria: list[BRDItem] = Field(
        default_factory=list,
        description=(
            "Observable, implementation-backed pass/fail conditions, each naming in `parent` the "
            "exact title of the requirement it validates."
        ),
    )

    system_requirements: list[BRDItem] = Field(default_factory=list)
    technical_constraints: list[BRDItem] = Field(default_factory=list)
    observations: list[BRDItem] = Field(default_factory=list)
    data_requirements: list[BRDItem] = Field(default_factory=list)
    integration_requirements: list[BRDItem] = Field(default_factory=list)
    non_functional_requirements: list[BRDItem] = Field(default_factory=list)
    reporting_requirements: list[BRDItem] = Field(default_factory=list)

    areas: list[AreaLabel] = Field(
        default_factory=list,
        description=(
            "The heading each area code is published under. Filled by assembly from what the "
            "fan-out workers named each area; leave it alone."
        ),
    )

    assumptions: list[str] = Field(
        default_factory=list,
        description=(
            "Interpretation-affecting conditions the evidence itself shows — a single-tenant "
            "schema, a shared rather than per-user configuration, a stand-in value left in "
            "place. Each must be evidenced, not supposed."
        ),
    )
    out_of_scope: list[str] = Field(
        default_factory=list,
        description=(
            "Capabilities a reader would reasonably assume are present and which the evidence "
            "shows are absent. Stated so nobody infers them. Only where absence is demonstrable."
        ),
    )
    open_questions: list[str] = Field(
        default_factory=list,
        description=(
            "Genuine ambiguities in the evidence that a stakeholder must settle. Never invented "
            "to fill the section."
        ),
    )

    @property
    def area_names(self) -> dict[str, str]:
        """Area code -> published heading.

        Stored as a list rather than a mapping because a `dict[str, str]` field becomes an
        open-ended JSON-schema object, and a provider enforcing strict structured output cannot
        represent one — it drops the property and then rejects the whole schema for having a
        required key that is no longer there. `ProductBRD` is round-tripped through the model by
        the repair step, so every field on it has to survive that conversion.
        """
        return {label.code: label.name for label in self.areas if label.code}

    def sections(self) -> dict[str, list[BRDItem]]:
        """The four sections of the classification chain, in document order."""
        return {
            "business_requirements": self.business_requirements,
            "functional_requirements": self.functional_requirements,
            "system_requirements": self.system_requirements,
            "business_rules": self.business_rules,
            "technical_constraints": self.technical_constraints,
            "success_criteria": self.success_criteria,
        }

    def supplements(self) -> dict[str, list[BRDItem]]:
        """The requirement families that sit alongside the chain rather than inside it."""
        return {
            "data_requirements": self.data_requirements,
            "integration_requirements": self.integration_requirements,
            "non_functional_requirements": self.non_functional_requirements,
            "reporting_requirements": self.reporting_requirements,
        }

    def every_section(self) -> dict[str, list[BRDItem]]:
        """Every family that is checked and carries ids, in document order.

        Observations are included: they are citation-checked like everything else, because an
        observation resting on a fabricated line is worth no more than a requirement resting on
        one. They are simply not published as requirements.
        """
        return {**self.sections(), **self.supplements(), "observations": self.observations}

    def all_items(self) -> list[BRDItem]:
        items = [item for section in self.every_section().values() for item in section]
        return [item for item in (self.product_purpose, self.business_problem) if item] + items

    def parents_for(self, section: str) -> list[BRDItem]:
        """Every item this section's members may legitimately hang from.

        All permitted tiers, not just the nearest one. A system requirement may enable a
        capability or an outcome; an observation may evidence any of five. Returning only the
        first left the rest of a perfectly legal link unresolvable, so the item was published
        unattached and the gate reported a gap that was not there.
        """
        if section == "success_criteria":
            # A criterion validates whichever requirement it names, at either published tier.
            return [*self.business_requirements, *self.functional_requirements]
        tier = SECTION_TIERS.get(section, "functional")
        candidates: list[BRDItem] = []
        for above in permitted_parents(tier):
            candidates.extend(getattr(self, TIER_SECTIONS[above], []))
        return candidates

    def children_of(self, parent_id: str, section: str) -> list[BRDItem]:
        """The items in `section` whose resolved parent is `parent_id`, in document order."""
        if not parent_id:
            return []
        return [item for item in self.sections()[section] if item.parent_id == parent_id]

    def orphans_in(self, section: str) -> list[BRDItem]:
        """Items in `section` that resolved to no parent, so nothing above them owns them."""
        if not self.parents_for(section):
            # Nothing to be an orphan of: the tier above is empty, which is its own finding.
            return list(self.sections()[section])
        return [item for item in self.sections()[section] if not item.parent_id]

    def assign_ids(self) -> "ProductBRD":
        """Stamp deterministic identifiers, then resolve every parent title to one.

        Ids are positional within a section and use the published tier prefix, so the same graph
        yields the same identifiers on every run — which is what makes the traceability matrix
        diffable between runs — and a gate message names a requirement by the same id the
        document does.
        """
        if self.product_purpose:
            self.product_purpose.id = "PURPOSE"
        if self.business_problem:
            self.business_problem.id = "PROBLEM"
        for section, items in self.sections().items():
            prefix = ID_PREFIXES[section]
            for index, item in enumerate(items, start=1):
                item.id = f"{prefix}-{index:03d}"

        # Functional requirements additionally carry their area in the id — FR-INT-01 rather than
        # FR-014 — because that is how a reader cites one in review, and how they find the rest of
        # the requirements about the same part of the product.
        per_area: dict[str, int] = {}
        for item in self.functional_requirements:
            if not item.area:
                continue
            per_area[item.area] = per_area.get(item.area, 0) + 1
            item.id = f"FR-{item.area}-{per_area[item.area]:02d}"

        for section, items in self.supplements().items():
            prefix = SUPPLEMENT_PREFIXES[section]
            for index, item in enumerate(items, start=1):
                item.id = f"{prefix}-{index:02d}"

        # Observations are identified too. They are not requirements, but a reviewer still has to
        # be able to cite one, and quarantine deletes by id — an unidentified item is invisible
        # to both.
        for index, item in enumerate(self.observations, start=1):
            item.id = f"OBS-{index:03d}"

        # Resolution runs after every id exists, so a parent later in document order still
        # resolves. A title naming nothing leaves `parent_id` empty for the gate to report.
        # Every family, not just the chain. Observations and supplements carry parent links too,
        # and leaving them unresolved published them as orphans while the link was sitting there.
        for section, items in self.every_section().items():
            by_title = {
                title_key(candidate.title): candidate.id
                for candidate in self.parents_for(section)
            }
            for item in items:
                wanted = title_key(item.parent or "")
                # Nothing is its own parent. Reachable now that a capability may sit under another
                # capability, and a self-link is a one-item cycle that makes any consumer walking
                # upward from a leaf hang.
                if wanted and wanted == title_key(item.title):
                    item.parent = None
                    item.parent_relation = None
                    item.parent_id = ""
                    continue
                item.parent_id = by_title.get(wanted, "")
        return self

    def is_empty(self) -> bool:
        return not any(self.every_section().values())

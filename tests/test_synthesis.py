"""The synthesis layer: turning a behaviour inventory into a requirements model.

The extraction layer was never the problem. Candidates were grounded, cited accurately, and
classified — and the document still read as a flat list, because every stage after extraction was
missing the one thing that makes a requirements document coherent: a view of the whole repository
at once.

Two defects caused most of what was observed, and both are structural rather than a matter of
prompt wording.

**Nothing compared candidates that did not share code.** Deduplication required two candidates to
cite the same span or share a call-path node before their wording was even looked at. A fan-out
worker sees one interaction boundary, so the same capability described from an endpoint and from
the implementation behind it cites different lines *by construction* and was never compared. Both
were published.

**The elevation step's output grew with the repository.** Business outcomes were bound to
capabilities by listing every capability title. On a codebase with hundreds of findings that reply
cannot fit in the model's output budget; it was truncated, or the length-failure retry asked for
fewer findings, and the business tier came back empty. Every capability was then unbound, which is
why relations appeared with no parent to point at.

Both are fixed by the same thing: a repository-wide clustering stage whose output is a few dozen
groups however large the codebase, which deduplication merges within and elevation binds to.

The fixture below is deliberately shaped like real fan-out output: several workers, each seeing one
boundary, several of them describing the same capability in different words over different lines.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agentic.capabilities.assemble import assemble_requirements, digest_areas
from agentic.capabilities.clustering import cluster_capabilities
from agentic.capabilities.consolidate import consolidate_requirements
from agentic.capabilities.leakage import review_implementation_leakage
from agentic.capabilities.claims import prepare_claims
from agentic.config import Settings
from agentic.schemas.brd import BRDItem, ProductBRD, TopTier, OutcomeBinding
from agentic.schemas.core import SourceLocation

from tests.test_correspondence import _graph_for

REPO_ROOT = Path(__file__).resolve().parents[1]


def _item(title, statement, basis, file, start, end, **kwargs):
    return BRDItem(
        title=title,
        statement=statement,
        rationale=kwargs.pop("rationale", "Grounded in the cited code."),
        confidence=0.9,
        abstraction_basis=basis,
        area=kwargs.pop("area", "ORD"),
        evidence=[SourceLocation(file=file, line_start=start, line_end=end)],
        graph_path=[kwargs.pop("trace", f"{file}:{start}")],
        **kwargs,
    )


def _fragments() -> list[dict]:
    """Four workers, each on one boundary, as the fan-out really produces them.

    Workers 1 and 2 describe the same capability from the two sides of it — the read endpoint and
    the store function behind it — and cite different files. Nothing before clustering could see
    that those are one capability.
    """
    return [
        {
            "boundary": "GET /orders",
            "area_code": "ORD",
            "area_name": "Orders",
            "functional_requirements": [
                _item(
                    "Retrieve Held Orders",
                    "A client shall be able to retrieve the orders currently held so that the "
                    "full set can be reviewed.",
                    "external_capability", "app/api.py", 20, 23,
                ),
            ],
            "observations": [],
        },
        {
            "boundary": "app.store.list_orders",
            "area_code": "ORD",
            "area_name": "Orders",
            "functional_requirements": [
                _item(
                    "Orders Listing",
                    "A client shall be able to retrieve the orders currently held so that the "
                    "whole set can be reviewed.",
                    "external_capability", "app/store.py", 9, 10, trace="store.list",
                ),
            ],
            "observations": [],
        },
        {
            "boundary": "POST /orders",
            "area_code": "ORD",
            "area_name": "Orders",
            "functional_requirements": [
                _item(
                    "Place An Order",
                    "A client shall be able to create a new order by POST /orders so that it "
                    "enters the queue for fulfilment.",
                    "external_capability", "app/api.py", 47, 52, trace="api.post",
                ),
            ],
            "system_requirements": [
                _item(
                    "Build The Payload Dictionary",
                    "The system shall assemble a payload dictionary before writing an order.",
                    "internal_behaviour", "app/store.py", 17, 19, trace="store.create",
                ),
            ],
            "observations": [],
        },
        {
            "boundary": "DELETE /orders/{order_id}",
            "area_code": "ORD",
            "area_name": "Orders",
            "functional_requirements": [
                _item(
                    "Cancel An Order",
                    "A client shall be able to cancel an order so that it is no longer fulfilled.",
                    "external_capability", "app/api.py", 78, 81, trace="api.delete",
                    acceptance_criteria=[
                        "The order is cancelled.",
                        "A client shall be able to cancel an order.",
                        "the order is cancelled",
                    ],
                ),
            ],
            "observations": [],
        },
    ]


def _top_tier(titles: list[str]) -> TopTier:
    """What the elevation step returns: outcomes, and the capability titles that realise each."""
    return TopTier(
        business_requirements=[
            BRDItem(
                title="Manage The Lifecycle Of An Order",
                statement="The product shall let a customer's order be placed, inspected and "
                "withdrawn throughout its life, so that demand can be met without manual "
                "intervention.",
                rationale="Every capability found in this repository serves this outcome.",
                confidence=0.8,
                abstraction_basis="business_outcome",
            )
        ],
        bindings=[
            OutcomeBinding(
                business_requirement="Manage The Lifecycle Of An Order",
                realised_by=titles,
            )
        ],
    )


def _run(clustering: bool):
    """The deterministic synthesis path, with clustering on or off."""
    _, store, state = _graph_for("py_service")
    settings = Settings(
        semantic_clustering=clustering,
        leakage_review=clustering,
        collapse_parameterised=False,
        correspondence_entailment=False,
    )
    paths = {"graph": state["artifacts"]["graph"]}
    channel = {
        "fragments": _fragments(),
        "artifacts": paths,
        "source_root": state["source_root"],
        "run_dir": state["run_dir"],
    }

    class _Ctx:
        def __init__(self):
            self.settings = settings
            self.state = channel
            self.store = store
            self.step = None

        @property
        def source_root(self):
            return Path(channel["source_root"])

        def load(self, name, model=None):
            return store.load_model(paths[name], model) if model else store.load_json(paths[name])

    ctx = _Ctx()

    clusters = cluster_capabilities(ctx)
    paths["capability_clusters"] = store.save("cc", clusters.artifacts["capability_clusters"])
    ids = [
        title
        for payload in _fragments()
        for item in payload.get("functional_requirements", [])
        for title in [item.title]
    ]

    digest = digest_areas(ctx)
    paths["area_digest"] = store.save("ad", digest.artifacts["area_digest"])
    paths["top_tier"] = store.save("tt", _top_tier(ids))

    outcomes = {"cluster": clusters, "digest": digest}
    for name, stage in (
        ("assemble", assemble_requirements),
        ("leakage", review_implementation_leakage),
        ("consolidate", consolidate_requirements),
        ("claims", prepare_claims),
    ):
        result = stage(ctx)
        paths["brd"] = store.save("brd", result.artifacts["brd"])
        outcomes[name] = result

    return store.load_model(paths["brd"], ProductBRD), outcomes, digest.artifacts["area_digest"]


@pytest.fixture(scope="module")
def synthesised():
    return _run(clustering=True)


@pytest.fixture(scope="module")
def unsynthesised():
    return _run(clustering=False)


# --------------------------------------------------------------------------------------------
# 1. A duplicate cluster is merged
# --------------------------------------------------------------------------------------------


def test_the_same_capability_seen_from_two_sides_is_one_requirement(synthesised, unsynthesised) -> None:
    """Two workers, two boundaries, two files, one capability.

    This is the case deduplication could never reach: the candidates share no citation and no
    call-path node, so the old merge never compared their wording at all.
    """
    before, _, _ = unsynthesised
    after, outcomes, _ = synthesised

    before_titles = [item.title for item in before.functional_requirements]
    after_titles = [item.title for item in after.functional_requirements]

    assert "Retrieve Held Orders" in before_titles and "Orders Listing" in before_titles, (
        "without clustering both descriptions of one capability survive"
    )
    assert len(after_titles) < len(before_titles)
    survivor = next(
        item
        for item in after.functional_requirements
        if item.title in {"Retrieve Held Orders", "Orders Listing"}
    )
    assert survivor.superseded, "the merge must name what it absorbed"
    assert len(survivor.evidence) == 2, "both citations are kept"


def test_clustering_groups_across_files_and_boundaries(synthesised) -> None:
    _, outcomes, _ = synthesised
    clusters = outcomes["cluster"].artifacts["capability_clusters"]["clusters"]
    grouped = [c for c in clusters if c["size"] > 1]
    assert grouped, "nothing was grouped, so the stage did nothing"
    assert any(c["distinct_files"] > 1 for c in grouped), (
        "a cluster must be able to span files, or it is the old evidence-bound merge again"
    )


# --------------------------------------------------------------------------------------------
# 2. A business capability is synthesised, and 3. the hierarchy is real
# --------------------------------------------------------------------------------------------


def test_a_business_requirement_is_published_and_is_more_abstract(synthesised) -> None:
    after, _, _ = synthesised
    assert len(after.business_requirements) == 1
    outcome = after.business_requirements[0]
    assert outcome.requirement_type == "business"
    assert not outcome.parent_id, "a business requirement is the top of the chain"
    # More abstract than its children: it must not simply repeat one of them.
    children = {item.title.casefold() for item in after.functional_requirements}
    assert outcome.title.casefold() not in children


def test_every_capability_is_attached_to_the_outcome_by_a_real_link(synthesised) -> None:
    after, _, _ = synthesised
    outcome = after.business_requirements[0]
    attached = [
        item for item in after.functional_requirements if item.parent_id == outcome.id
    ]
    assert attached, "no capability reached the business requirement"
    for item in attached:
        assert item.parent_relation in {"refines", "enables"}




# --------------------------------------------------------------------------------------------
# 4. An implementation detail becomes an observation
# --------------------------------------------------------------------------------------------


def test_a_behaviour_that_only_describes_the_code_is_not_a_system_requirement(synthesised) -> None:
    """Nothing outside the current implementation depends on assembling a payload dictionary."""
    after, outcomes, _ = synthesised

    titles = [item.title for item in after.system_requirements]
    assert "Build The Payload Dictionary" not in titles

    demoted = next(
        item for item in after.observations if item.title == "Build The Payload Dictionary"
    )
    assert demoted.requirement_type == "observation"
    assert demoted.evidence, "a demoted behaviour keeps its evidence"
    assert any(
        "describes how this code works" in note
        for note in demoted.implementation_observations
    )
    assert outcomes["leakage"].metrics["behaviours_demoted_to_observation"] >= 1


# --------------------------------------------------------------------------------------------
# 5. Implementation leakage leaves the statement but not the record
# --------------------------------------------------------------------------------------------


def test_a_route_named_in_a_statement_is_moved_to_traceability(synthesised) -> None:
    after, outcomes, _ = synthesised
    placed = next(
        item for item in after.functional_requirements if item.title == "Place An Order"
    )
    assert "/orders" not in placed.statement
    assert "POST" not in placed.statement
    assert placed.implementation_observations, "the detail must be kept, not deleted"
    assert any("/orders" in note or "POST" in note for note in placed.implementation_observations)
    assert placed.evidence, "evidence is untouched"
    # Still a sentence.
    assert len(placed.statement.split()) >= 8 and placed.statement.endswith(".")


# --------------------------------------------------------------------------------------------
# 6. Acceptance criteria are consolidated
# --------------------------------------------------------------------------------------------


def test_criteria_that_repeat_each_other_or_the_requirement_are_removed(synthesised) -> None:
    after, _, _ = synthesised
    cancel = next(
        item for item in after.functional_requirements if item.title == "Cancel An Order"
    )
    normalised = [" ".join(c.lower().split()).rstrip(".") for c in cancel.acceptance_criteria]
    assert len(normalised) == len(set(normalised)), f"duplicated criteria: {normalised}"
    assert not any(
        text == " ".join(cancel.statement.lower().split()).rstrip(".") for text in normalised
    ), "a criterion that restates the requirement checks nothing"


# --------------------------------------------------------------------------------------------
# The invariants the brief asks to be verified
# --------------------------------------------------------------------------------------------


def test_no_requirement_carries_a_relation_without_a_parent(synthesised) -> None:
    after, _, _ = synthesised
    offenders = [
        item.title
        for item in after.all_items()
        if item.parent_relation and not item.parent_id
    ]
    assert offenders == [], offenders


def test_identifiers_are_assigned_only_after_synthesis(synthesised) -> None:
    """An id stamped before deduplication is an id that outlives the claim it named."""
    _, outcomes, _ = synthesised

    for stage in ("assemble", "leakage", "consolidate"):
        brd = outcomes[stage].artifacts["brd"]
        assert all(item.id == "" for item in brd.all_items()), (
            f"{stage} assigned identifiers before synthesis had finished"
        )
    final = outcomes["claims"].artifacts["brd"]
    assert all(item.id for item in final.all_items())


def test_every_surviving_requirement_still_traces_to_its_evidence(synthesised) -> None:
    """The point of the whole exercise: structure improves, evidence is never lost."""
    before = _run(clustering=False)[0]
    after, _, _ = _run(clustering=True)

    def spans(brd):
        return {
            (location.file, location.line_start, location.line_end)
            for item in brd.all_items()
            for location in item.evidence
        }

    assert spans(after) >= spans(before), "merging lost a citation"

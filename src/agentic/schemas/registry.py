"""Name -> pydantic model lookup for `output_schema:` entries in a pack manifest.

A new pack registers its own output shape here; the graph builder never imports it directly.
"""

from __future__ import annotations

from pydantic import BaseModel

from agentic.schemas.boundaries import BoundaryAssignments
from agentic.schemas.testspec import RepairedTestCases, TestDesignFragment
from agentic.schemas.brd import (
    BRDFragment,
    BRDItem,
    CapabilityAssignments,
    ModuleAnalysis,
    OutcomeProposals,
    OutcomeReview,
    ProductBRD,
    RepairedItems,
    TopTier,
)

SCHEMAS: dict[str, type[BaseModel]] = {
    "BRDItem": BRDItem,
    "BRDFragment": BRDFragment,
    "ProductBRD": ProductBRD,
    "TopTier": TopTier,
    # The altitude between one boundary and the whole product.
    "ModuleAnalysis": ModuleAnalysis,
    "RepairedItems": RepairedItems,
    # The multi-call top tier: outcomes proposed per part of the product, an orphan sweep, and a
    # review of each outcome.
    "OutcomeProposals": OutcomeProposals,
    "CapabilityAssignments": CapabilityAssignments,
    "OutcomeReview": OutcomeReview,
    # Boundary classification, which replaced syntactic entry-point detection.
    "BoundaryAssignments": BoundaryAssignments,
    # The test-case generation pack.
    "TestDesignFragment": TestDesignFragment,
    "RepairedTestCases": RepairedTestCases,
}


def get_schema(name: str) -> type[BaseModel]:
    try:
        return SCHEMAS[name]
    except KeyError:
        known = ", ".join(sorted(SCHEMAS)) or "<none registered>"
        raise KeyError(f"Unknown output_schema {name!r}. Registered schemas: {known}") from None


def register_schema(name: str, model: type[BaseModel]) -> None:
    SCHEMAS[name] = model

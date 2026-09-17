"""Metrics harness for the requirements corpus.

Every remediation package is graded by the delta it produces on these numbers, across the whole
fixture corpus rather than one repository. That is the anti-overfitting control: a fix tuned to
one stack shows up here as a gain on one fixture and a flat line on the others.

Two measurement modes, because the pipeline has two halves with very different costs:

- **Structural** (`measure_structure`) runs the deterministic prefix only — ingest through
  cluster planning — and needs no model. It answers the questions that decide coverage: which
  entry points exist, which were detected, and which were actually selected for analysis. This is
  what grades entry-point work, and it runs in a test.
- **Document** (`measure_document`) reads a finished `requirements.json` from a real run and
  answers the questions that need one: duplication, citation resolution, which file types and
  languages the published requirements actually cite.

Keeping them apart means the coverage metrics are cheap and deterministic, and the expensive ones
are computed from artefacts a run already wrote rather than by re-running it.
"""

from __future__ import annotations

from tests.harness.metrics import (
    DocumentMetrics,
    StructureMetrics,
    load_ground_truth,
    measure_document,
    measure_structure,
)

__all__ = [
    "DocumentMetrics",
    "StructureMetrics",
    "load_ground_truth",
    "measure_document",
    "measure_structure",
]

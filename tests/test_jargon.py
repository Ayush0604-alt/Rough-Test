"""The business-language check.

Both the synthesis and deliverables skills forbid technical vocabulary in a requirement statement
and say to verify it rather than trust the model. These tests are that verification's own tests.
"""

from __future__ import annotations

import pytest

from agentic.capabilities.jargon import find_jargon

KNOWN_SYMBOLS = {
    "app.main.create_invoice",
    "app.storage.save_invoice",
    "app.reconciliation.LedgerBalancer",
}


@pytest.mark.parametrize(
    "statement",
    [
        "The system shall call process_upload() in app/main.py.",
        "The service exposes a REST API endpoint for invoice creation.",
        "Invoice records are written to the PostgreSQL database.",
        "The FastAPI handler validates the request body before persisting it.",
        "Submitting a bill sends a POST to /invoices.",
        "The `create_invoice` function enforces the ceiling.",
        "Data is stored using SQLAlchemy models.",
        "The createInvoice routine returns a status code.",
    ],
)
def test_technical_statements_are_rejected(statement: str) -> None:
    assert find_jargon(statement, KNOWN_SYMBOLS), f"should have been flagged: {statement}"


@pytest.mark.parametrize(
    "statement",
    [
        "Staff can record a new supplier bill and retrieve it later.",
        "A bill above the approved ceiling is refused, and the sender is told why.",
        "Each recorded bill is kept so that it can be found again by its reference.",
        "The organisation can operate in a different audit region without changing how bills "
        "are recorded.",
        "A request for a bill that was never recorded is answered with a clear refusal.",
    ],
)
def test_business_statements_are_accepted(statement: str) -> None:
    findings = find_jargon(statement, KNOWN_SYMBOLS)
    assert not findings, f"false positive on: {statement} -> {[f.describe() for f in findings]}"


def test_project_specific_symbol_is_caught_even_when_it_reads_like_business_language() -> None:
    """A class named after a business concept still gives away the implementation."""
    statement = "The LedgerBalancer reconciles outstanding amounts each night."
    kinds = {finding.kind for finding in find_jargon(statement, KNOWN_SYMBOLS)}
    assert "source symbol" in kinds or "identifier" in kinds


def test_common_business_words_that_are_also_symbols_do_not_false_positive() -> None:
    """Guarding the guard: a symbol called `total` must not ban the word 'total'."""
    findings = find_jargon(
        "The total owed to each supplier is shown alongside the payment date.",
        {"app.reports.total", "app.reports.user"},
    )
    assert not findings, [finding.describe() for finding in findings]


def test_ordinary_sentence_capitalisation_is_not_treated_as_an_identifier() -> None:
    assert not find_jargon("Suppliers receive a confirmation once a bill is recorded.")

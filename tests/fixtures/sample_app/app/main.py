"""A deliberately small target codebase used by the extraction and gate tests.

It contains exactly the constructs the extractor must find: two endpoints with literal paths, a
validation branch that raises an HTTP error, an environment-variable read, a module-level constant,
and an internal helper that nothing reachable from a boundary ever calls.
"""

import os

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from app.storage import save_invoice, load_invoice

MAX_INVOICE_AMOUNT = 100000
CURRENCY = "GBP"

app = FastAPI()


class Invoice(BaseModel):
    reference: str
    amount: int


def audit_region() -> str:
    return os.getenv("AUDIT_REGION", "eu-west")


@app.post("/invoices")
def create_invoice(invoice: Invoice):
    if invoice.amount > MAX_INVOICE_AMOUNT:
        raise HTTPException(status_code=422, detail="Invoice amount exceeds the approved limit")
    if invoice.amount <= 0:
        raise HTTPException(status_code=400, detail="Invoice amount must be positive")
    save_invoice(invoice.reference, invoice.amount, audit_region())
    return {"reference": invoice.reference, "currency": CURRENCY}


@app.get("/invoices/{reference}")
def read_invoice(reference: str):
    record = load_invoice(reference)
    if record is None:
        raise HTTPException(status_code=404, detail="No invoice with that reference")
    return record


def _unused_reconciliation_helper(values):
    """Never called from anything reachable from an endpoint.

    A requirement traced only to this function must be rejected: sophisticated-looking internal
    processing is not an externally consumed capability.
    """
    total = 0
    for value in values:
        total += value
    return total

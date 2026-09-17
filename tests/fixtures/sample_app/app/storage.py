"""Persistence for the sample app."""

import os

STORE_PATH = "invoices.json"

_RECORDS: dict[str, dict] = {}


def save_invoice(reference: str, amount: int, region: str) -> None:
    _RECORDS[reference] = {"amount": amount, "region": region}


def load_invoice(reference: str):
    try:
        return _RECORDS[reference]
    except KeyError:
        return None


def backup_location() -> str:
    return os.environ["INVOICE_BACKUP_BUCKET"]

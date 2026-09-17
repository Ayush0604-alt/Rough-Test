"""Ledger operations."""

MAX_ADJUSTMENT = 50_000


def summarise(period):
    return {"period": period, "balanced": True}


def reconcile(period, dry_run=True):
    if dry_run:
        return {"period": period, "written": False}
    return {"period": period, "written": True}


def apply_adjustment(account, amount):
    if abs(amount) > MAX_ADJUSTMENT:
        raise ValueError("adjustment exceeds the manual posting limit")
    return {"account": account, "amount": amount}

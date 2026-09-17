"""A module whose functions are longer than the symbol window.

Deliberately padded. The sample app's functions all fit inside SYMBOL_WINDOW lines of their own
`def`, which hides any bug that attributes a deep statement to its enclosing definition's name.
Every marked line below sits far enough from its `def` that such a bug fails the gate.
"""

import os

from fastapi import FastAPI, HTTPException

app = FastAPI()

MAX_BATCH = 500


def build_enriched_summary(records, sink):
    """Long on purpose: the interesting statements are 40+ lines below this line."""
    collected = []
    running_total = 0
    skipped = 0
    rejected = 0
    seen_references = set()
    duplicate_references = []
    first_reference = None
    last_reference = None
    largest_amount = 0
    smallest_amount = 0
    currency_counts = {}
    region_counts = {}
    warnings = []
    notes = []
    audit_trail = []
    started = True
    finished = False
    cursor = 0
    page_size = 50
    retries = 0
    max_retries = 3
    backoff = 1.0
    label = "summary"
    prefix = "batch"
    suffix = "v1"
    separator = "-"
    identifier = separator.join([prefix, label, suffix])
    audit_trail.append(identifier)
    region = os.getenv("AUDIT_REGION")

    for record in records:
        if record is None:
            raise HTTPException(status_code=400, detail="a record is required")
        reference = record.get("reference")
        if reference in seen_references:
            duplicate_references.append(reference)
            skipped += 1
            continue
        seen_references.add(reference)
        collected.append(record)
        running_total += record.get("amount", 0)

    try:
        sink.save(collected, region)
    except RuntimeError:
        rejected = len(collected)
        collected.clear()

    return {
        "identifier": identifier,
        "collected": collected,
        "total": running_total,
        "skipped": skipped,
        "rejected": rejected,
        "duplicates": duplicate_references,
        "warnings": warnings,
        "notes": notes,
        "audit": audit_trail,
        "region": region,
        "cursor": cursor,
        "page_size": page_size,
        "retries": retries,
        "max_retries": max_retries,
        "backoff": backoff,
        "started": started,
        "finished": finished,
        "first": first_reference,
        "last": last_reference,
        "largest": largest_amount,
        "smallest": smallest_amount,
        "currencies": currency_counts,
        "regions": region_counts,
    }


@app.post("/summaries")
def create_summary(payload: dict):
    if len(payload.get("records", [])) > MAX_BATCH:
        raise HTTPException(status_code=422, detail="batch exceeds the approved limit")
    return build_enriched_summary(payload["records"], _sink())


def _sink():
    return object()


def main():
    build_enriched_summary([], _sink())


if __name__ == "__main__":
    main()

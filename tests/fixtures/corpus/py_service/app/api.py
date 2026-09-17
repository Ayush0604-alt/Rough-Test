"""Order service HTTP surface.

Deliberately mixes read and state-changing handlers so that entry-point selection
can be measured per class. The read/mutate split is recorded in ground_truth.json.
"""
from fastapi import APIRouter, HTTPException

from .store import (
    archive_order,
    cancel_order,
    create_order,
    list_orders,
    load_order,
    save_order,
)

router = APIRouter()


@router.get("/orders")
def get_orders():
    """List every order currently held."""
    return list_orders()


@router.get("/orders/{order_id}")
def get_order(order_id: str):
    """Return one order by its reference."""
    found = load_order(order_id)
    if found is None:
        raise HTTPException(status_code=404, detail="order not found")
    return found


@router.get("/orders/{order_id}/history")
def get_order_history(order_id: str):
    """Return the audit history for one order."""
    return load_order(order_id).get("history", [])


@router.get("/health")
def get_health():
    """Report that the service is answering."""
    return {"ok": True}


@router.post("/orders")
def post_order(payload: dict):
    """Accept a new order and place it in the queue for fulfilment."""
    if payload.get("total", 0) > 10000:
        raise HTTPException(status_code=400, detail="order exceeds the approved ceiling")
    return create_order(payload)


@router.post("/orders/{order_id}/confirm")
def post_confirm(order_id: str):
    """Confirm an order so that fulfilment may begin."""
    order = load_order(order_id)
    order["state"] = "confirmed"
    return save_order(order)


@router.put("/orders/{order_id}")
def put_order(order_id: str, payload: dict):
    """Replace the details held against an order."""
    payload["id"] = order_id
    return save_order(payload)


@router.patch("/orders/{order_id}/address")
def patch_address(order_id: str, address: dict):
    """Amend only the delivery address on an order."""
    order = load_order(order_id)
    order["address"] = address
    return save_order(order)


@router.delete("/orders/{order_id}")
def delete_order(order_id: str):
    """Cancel an order so that it is no longer fulfilled."""
    return cancel_order(order_id)


@router.delete("/orders/{order_id}/archive")
def delete_archive(order_id: str):
    """Remove an archived order permanently."""
    return archive_order(order_id)

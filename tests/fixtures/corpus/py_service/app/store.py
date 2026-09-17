"""Persistence for orders."""
import os

RETENTION_DAYS = int(os.environ.get("ORDER_RETENTION_DAYS", "90"))

_ORDERS: dict = {}


def list_orders():
    return list(_ORDERS.values())


def load_order(order_id):
    return _ORDERS.get(order_id)


def create_order(payload):
    _ORDERS[payload["id"]] = payload
    return payload


def save_order(order):
    _ORDERS[order["id"]] = order
    return order


def cancel_order(order_id):
    order = _ORDERS[order_id]
    order["state"] = "cancelled"
    return order


def archive_order(order_id):
    return _ORDERS.pop(order_id, None)

"""Outbound notification."""


def send_receipt(order_id):
    return {"sent": True, "order_id": order_id}

"""State the workers change."""
BASKET_TTL_HOURS = 72


def mark_settled(order_id):
    return {"order_id": order_id, "state": "settled"}


def purge_expired():
    return {"purged": 0, "ttl_hours": BASKET_TTL_HOURS}

"""Background workers.

An event-driven paradigm: handlers are registered with a broker and a scheduler,
never called directly by any code in this repository.
"""
from broker import broker, scheduler
from mailer import send_receipt
from store import mark_settled, purge_expired


@broker.subscribe("order.placed")
def on_order_placed(event):
    """Send the customer a receipt once an order is placed."""
    return send_receipt(event["order_id"])


@broker.subscribe("payment.settled")
def on_payment_settled(event):
    """Record that a payment cleared so the order may be fulfilled."""
    return mark_settled(event["order_id"])


@scheduler.every("24h")
def nightly_purge():
    """Remove expired baskets so they do not accumulate."""
    return purge_expired()

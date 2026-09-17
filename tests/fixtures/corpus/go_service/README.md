# Inventory service

Holds stock levels and reserves them against orders. A reservation may not exceed
the per-order limit, so that a single order cannot empty the warehouse.

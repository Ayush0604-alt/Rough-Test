-- Orders held by the service. Rows are never deleted; cancellation is a state change.
CREATE TABLE orders (
    id       TEXT PRIMARY KEY,
    state    TEXT NOT NULL DEFAULT 'placed',
    total    NUMERIC NOT NULL,
    -- An order above the approved ceiling must be refused at intake.
    CONSTRAINT chk_order_state CHECK (state IN ('placed','confirmed','cancelled','archived'))
);

COMMENT ON TABLE orders IS 'One row per order. Cancellation is reversible; archival is not.';

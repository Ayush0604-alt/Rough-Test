CREATE TABLE borrowers (
    id INTEGER PRIMARY KEY,
    email TEXT NOT NULL,
    penalty_pence INTEGER NOT NULL DEFAULT 0,
    CONSTRAINT chk_penalty_not_negative CHECK (penalty_pence >= 0)
);

CREATE TABLE loans (
    id INTEGER PRIMARY KEY,
    borrower_id INTEGER NOT NULL REFERENCES borrowers(id),
    state TEXT NOT NULL,
    CONSTRAINT chk_loan_state CHECK (state IN ('open', 'returned', 'overdue'))
);

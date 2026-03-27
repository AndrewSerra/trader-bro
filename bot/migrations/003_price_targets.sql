CREATE TABLE IF NOT EXISTS price_targets (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id  TEXT NOT NULL,
    low_target  REAL NOT NULL,
    high_target REAL NOT NULL,
    set_at      TIMESTAMP NOT NULL,
    decision_id INTEGER REFERENCES decisions(id)
);

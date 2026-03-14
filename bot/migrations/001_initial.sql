CREATE TABLE IF NOT EXISTS decisions (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp           TEXT NOT NULL,
    product_id          TEXT NOT NULL,
    decision            TEXT NOT NULL CHECK(decision IN ('BUY','SELL','HOLD')),
    reason              TEXT NOT NULL,
    price               REAL NOT NULL,
    amount_usd          REAL NOT NULL,
    max_trade_limit_usd REAL NOT NULL,
    order_id            TEXT,
    status              TEXT NOT NULL DEFAULT 'pending'
                        CHECK(status IN ('pending','filled','failed','skipped'))
);

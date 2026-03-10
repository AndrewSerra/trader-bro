import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone

DB_PATH = "data/trader_bro.db"

DDL = """
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
"""


@contextmanager
def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    with get_connection() as conn:
        conn.executescript(DDL)


def save_decision(
    product_id: str,
    decision: str,
    reason: str,
    price: float,
    amount_usd: float,
    max_trade_limit_usd: float,
    order_id: str | None,
    status: str,
) -> int:
    timestamp = datetime.now(timezone.utc).isoformat()
    with get_connection() as conn:
        cursor = conn.execute(
            """
            INSERT INTO decisions
                (timestamp, product_id, decision, reason, price, amount_usd,
                 max_trade_limit_usd, order_id, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (timestamp, product_id, decision, reason, price, amount_usd,
             max_trade_limit_usd, order_id, status),
        )
        return cursor.lastrowid


def get_all_decisions(product_id: str | None = None) -> list[dict]:
    with get_connection() as conn:
        if product_id:
            rows = conn.execute(
                "SELECT * FROM decisions WHERE product_id = ? ORDER BY id DESC",
                (product_id,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM decisions ORDER BY id DESC"
            ).fetchall()
        return [dict(row) for row in rows]


def get_decision_by_id(decision_id: int) -> dict | None:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM decisions WHERE id = ?", (decision_id,)
        ).fetchone()
        return dict(row) if row else None

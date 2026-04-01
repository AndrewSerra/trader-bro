import logging
import os
import pathlib
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone

DB_PATH = "data/trader_bro.db"


@contextmanager
def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def _migrations_dir() -> pathlib.Path:
    return pathlib.Path(__file__).parent / "migrations"


def _ensure_migrations_table(conn):
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version TEXT PRIMARY KEY,
            applied_at TEXT NOT NULL
        )
        """
    )


def _applied_versions(conn) -> set:
    return {row[0] for row in conn.execute("SELECT version FROM schema_migrations").fetchall()}


def _apply_migration(conn, path: pathlib.Path):
    sql = path.read_text()
    for statement in sql.split(";"):
        statement = statement.strip()
        if statement:
            conn.execute(statement)
    conn.execute(
        "INSERT INTO schema_migrations (version, applied_at) VALUES (?, ?)",
        (path.name, datetime.now(timezone.utc).isoformat()),
    )


def init_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    with get_connection() as conn:
        _ensure_migrations_table(conn)
        applied = _applied_versions(conn)
        for path in sorted(_migrations_dir().glob("*.sql")):
            if path.name not in applied:
                logging.info("Applying migration: %s", path.name)
                _apply_migration(conn, path)


def save_decision(
    product_id: str,
    decision: str,
    reason: str,
    price: float,
    amount_usd: float,
    max_trade_limit_usd: float,
    order_id: str | None,
    status: str,
    input_tokens: int = 0,
    output_tokens: int = 0,
    error: str | None = None,
) -> int:
    timestamp = datetime.now(timezone.utc).isoformat()
    with get_connection() as conn:
        cursor = conn.execute(
            """
            INSERT INTO decisions
                (timestamp, product_id, decision, reason, price, amount_usd,
                 max_trade_limit_usd, order_id, status, input_tokens, output_tokens, error)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (timestamp, product_id, decision, reason, price, amount_usd,
             max_trade_limit_usd, order_id, status, input_tokens, output_tokens, error),
        )
        return cursor.lastrowid


def insert_price_target(
    product_id: str,
    low_target: float,
    high_target: float,
    decision_id: int | None = None,
    reasoning: str | None = None,
) -> int:
    set_at = datetime.now(timezone.utc).isoformat()
    with get_connection() as conn:
        cursor = conn.execute(
            """
            INSERT INTO price_targets (product_id, low_target, high_target, set_at, decision_id, reasoning)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (product_id, low_target, high_target, set_at, decision_id, reasoning),
        )
        return cursor.lastrowid


def get_latest_price_targets() -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT pt.* FROM price_targets pt
            WHERE pt.set_at = (
                SELECT MAX(pt2.set_at) FROM price_targets pt2
                WHERE pt2.product_id = pt.product_id
            )
            GROUP BY pt.product_id
            """
        ).fetchall()
        return [dict(row) for row in rows]


def get_latest_price_target(product_id: str) -> dict | None:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM price_targets WHERE product_id = ? ORDER BY set_at DESC LIMIT 1",
            (product_id,),
        ).fetchone()
        return dict(row) if row else None


def get_last_successful_trade(product_id: str) -> dict | None:
    """Return the most recent filled BUY or SELL for this product with no error."""
    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT * FROM decisions
            WHERE product_id = ?
              AND decision IN ('BUY', 'SELL')
              AND status = 'filled'
              AND (error IS NULL OR error = '')
            ORDER BY id DESC LIMIT 1
            """,
            (product_id,),
        ).fetchone()
        return dict(row) if row else None


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

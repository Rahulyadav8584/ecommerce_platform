"""Durable PostgreSQL storage layer for ShopHub Store API.

Provides:
- PostgreSQL persistence for orders in 'shophub_checkpoints' database.
- Auto-creates 'orders' table and indexes if not present.
- Seeds initial orders if table is empty.
- Seamless in-memory fallback when DATABASE_URL is unset or database is unreachable.
- Dict-like interface for transparent backward compatibility with existing tests and tools.
"""

import collections.abc
import os
from typing import Any, Dict, Iterator, List, Optional
import psycopg
from psycopg.rows import dict_row

from src.store_api.models import Order, OrderStatus
from src.store_api.seed_data import INITIAL_ORDERS, get_fresh_seed_orders


def _row_to_order(row: Dict[str, Any]) -> Order:
    """Converts a database row dictionary to an Order model."""
    return Order(
        id=str(row["id"]),
        merchant=str(row["merchant"]),
        customer_name=str(row["customer_name"]),
        customer_email=str(row["customer_email"]),
        customer_phone=str(row["customer_phone"]),
        status=OrderStatus(str(row["status"])),
        total=float(row["total"]),
        placed_at=str(row["placed_at"]),
        customer_note=row["customer_note"] if row.get("customer_note") else None,
    )


class PostgresOrdersStore(collections.abc.MutableMapping):
    """PostgreSQL-backed order store implementing dict-like interface."""

    def __init__(self, db_url: str):
        self.db_url = db_url
        self._conn: Optional[psycopg.Connection] = None
        self._init_schema()

    def _get_connection(self) -> psycopg.Connection:
        if self._conn is None or self._conn.closed:
            self._conn = psycopg.connect(self.db_url, autocommit=True, row_factory=dict_row)
        return self._conn

    def _init_schema(self) -> None:
        """Creates the orders table and indexes, seeding initial data if empty."""
        conn = self._get_connection()
        with conn.cursor() as cur:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS orders (
                    id VARCHAR(64) PRIMARY KEY,
                    merchant VARCHAR(64) NOT NULL,
                    customer_name VARCHAR(255) NOT NULL,
                    customer_email VARCHAR(255) NOT NULL,
                    customer_phone VARCHAR(64) NOT NULL,
                    status VARCHAR(32) NOT NULL,
                    total NUMERIC(10, 2) NOT NULL,
                    placed_at VARCHAR(64) NOT NULL,
                    customer_note TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_orders_merchant ON orders (merchant);
                CREATE INDEX IF NOT EXISTS idx_orders_status ON orders (status);
                """
            )
            # Check if seeded
            cur.execute("SELECT COUNT(*) AS count FROM orders;")
            row = cur.fetchone()
            if row and row["count"] == 0:
                self._seed_orders(cur)

    def _seed_orders(self, cur: psycopg.Cursor) -> None:
        """Populates the database with initial orders from seed_data."""
        for o in INITIAL_ORDERS:
            cur.execute(
                """
                INSERT INTO orders (id, merchant, customer_name, customer_email, customer_phone, status, total, placed_at, customer_note)
                VALUES (%(id)s, %(merchant)s, %(customer_name)s, %(customer_email)s, %(customer_phone)s, %(status)s, %(total)s, %(placed_at)s, %(customer_note)s)
                ON CONFLICT (id) DO UPDATE SET
                    status = EXCLUDED.status,
                    total = EXCLUDED.total,
                    customer_note = EXCLUDED.customer_note;
                """,
                {
                    "id": o.id,
                    "merchant": o.merchant,
                    "customer_name": o.customer_name,
                    "customer_email": o.customer_email,
                    "customer_phone": o.customer_phone,
                    "status": o.status.value,
                    "total": o.total,
                    "placed_at": o.placed_at,
                    "customer_note": o.customer_note,
                },
            )

    def list_orders(self, merchant: str, status_filter: Optional[OrderStatus] = None) -> List[Order]:
        """Returns orders belonging to a merchant, newest first."""
        conn = self._get_connection()
        with conn.cursor() as cur:
            if status_filter:
                cur.execute(
                    """
                    SELECT * FROM orders
                    WHERE merchant = %(merchant)s AND status = %(status)s
                    ORDER BY placed_at DESC;
                    """,
                    {"merchant": merchant, "status": status_filter.value},
                )
            else:
                cur.execute(
                    """
                    SELECT * FROM orders
                    WHERE merchant = %(merchant)s
                    ORDER BY placed_at DESC;
                    """,
                    {"merchant": merchant},
                )
            rows = cur.fetchall()
            return [_row_to_order(r) for r in rows]

    def get_order(self, order_id: str, merchant: Optional[str] = None) -> Optional[Order]:
        """Looks up an order by ID, optionally verifying merchant ownership."""
        conn = self._get_connection()
        with conn.cursor() as cur:
            if merchant:
                cur.execute(
                    "SELECT * FROM orders WHERE id = %(id)s AND merchant = %(merchant)s;",
                    {"id": order_id, "merchant": merchant},
                )
            else:
                cur.execute("SELECT * FROM orders WHERE id = %(id)s;", {"id": order_id})
            row = cur.fetchone()
            if not row:
                return None
            return _row_to_order(row)

    def update_order_status(self, order_id: str, merchant: str, new_status: OrderStatus) -> Optional[Order]:
        """Atomically updates order status and returns the updated Order."""
        conn = self._get_connection()
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE orders
                SET status = %(status)s
                WHERE id = %(id)s AND merchant = %(merchant)s
                RETURNING *;
                """,
                {"id": order_id, "merchant": merchant, "status": new_status.value},
            )
            row = cur.fetchone()
            if not row:
                return None
            return _row_to_order(row)

    def reset(self) -> int:
        """Truncates and re-seeds the orders table."""
        conn = self._get_connection()
        with conn.cursor() as cur:
            cur.execute("TRUNCATE TABLE orders;")
            self._seed_orders(cur)
            cur.execute("SELECT COUNT(*) AS count FROM orders;")
            row = cur.fetchone()
            return row["count"] if row else 0

    # Dict-like interface implementation for backward compatibility
    def __getitem__(self, key: str) -> Order:
        order = self.get_order(key)
        if not order:
            raise KeyError(key)
        return order

    def __setitem__(self, key: str, value: Order) -> None:
        conn = self._get_connection()
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO orders (id, merchant, customer_name, customer_email, customer_phone, status, total, placed_at, customer_note)
                VALUES (%(id)s, %(merchant)s, %(customer_name)s, %(customer_email)s, %(customer_phone)s, %(status)s, %(total)s, %(placed_at)s, %(customer_note)s)
                ON CONFLICT (id) DO UPDATE SET
                    status = EXCLUDED.status,
                    total = EXCLUDED.total,
                    customer_note = EXCLUDED.customer_note;
                """,
                {
                    "id": value.id,
                    "merchant": value.merchant,
                    "customer_name": value.customer_name,
                    "customer_email": value.customer_email,
                    "customer_phone": value.customer_phone,
                    "status": value.status.value if isinstance(value.status, OrderStatus) else str(value.status),
                    "total": value.total,
                    "placed_at": value.placed_at,
                    "customer_note": value.customer_note,
                },
            )

    def __delitem__(self, key: str) -> None:
        conn = self._get_connection()
        with conn.cursor() as cur:
            cur.execute("DELETE FROM orders WHERE id = %(id)s;", {"id": key})

    def __iter__(self) -> Iterator[str]:
        conn = self._get_connection()
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM orders ORDER BY id;")
            for r in cur.fetchall():
                yield str(r["id"])

    def __len__(self) -> int:
        conn = self._get_connection()
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) AS count FROM orders;")
            row = cur.fetchone()
            return row["count"] if row else 0

    def values(self) -> List[Order]:
        conn = self._get_connection()
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM orders ORDER BY placed_at DESC;")
            return [_row_to_order(r) for r in cur.fetchall()]


class InMemoryOrdersStore(collections.abc.MutableMapping):
    """In-memory fallback store when PostgreSQL is not configured."""

    def __init__(self):
        self._data: Dict[str, Order] = get_fresh_seed_orders()

    def list_orders(self, merchant: str, status_filter: Optional[OrderStatus] = None) -> List[Order]:
        results = [o for o in self._data.values() if o.merchant == merchant]
        if status_filter:
            results = [o for o in results if o.status == status_filter]
        results.sort(key=lambda o: o.placed_at, reverse=True)
        return results

    def get_order(self, order_id: str, merchant: Optional[str] = None) -> Optional[Order]:
        order = self._data.get(order_id)
        if not order:
            return None
        if merchant and order.merchant != merchant:
            return None
        return order

    def update_order_status(self, order_id: str, merchant: str, new_status: OrderStatus) -> Optional[Order]:
        order = self.get_order(order_id, merchant)
        if not order:
            return None
        order.status = new_status
        return order

    def reset(self) -> int:
        self._data = get_fresh_seed_orders()
        return len(self._data)

    def __getitem__(self, key: str) -> Order:
        return self._data[key]

    def __setitem__(self, key: str, value: Order) -> None:
        self._data[key] = value

    def __delitem__(self, key: str) -> None:
        del self._data[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._data)

    def __len__(self) -> int:
        return len(self._data)

    def values(self) -> List[Order]:
        return list(self._data.values())


def get_order_store(db_url: Optional[str] = None) -> collections.abc.MutableMapping:
    """Factory creating a PostgreSQL or In-Memory order store based on configuration."""
    url = db_url or os.getenv("DATABASE_URL", "")
    if url.startswith("postgres://") or url.startswith("postgresql://"):
        try:
            store = PostgresOrdersStore(url)
            print(f"[Store API] Connected to PostgreSQL orders table ({url.split('@')[-1]}).")
            return store
        except Exception as e:
            print(f"[Store API] Warning: Failed to connect to PostgreSQL ({e}). Falling back to in-memory store.")
    return InMemoryOrdersStore()


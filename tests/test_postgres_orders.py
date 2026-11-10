"""Tests for PostgreSQL Durable Orders Persistence.

Verifies:
- Auto-creation of 'orders' table in PostgreSQL.
- Seeding of initial 14 orders across merchants.
- Tenant-scoped listing (Merchant Alpha vs Merchant Beta).
- State mutations (refund / cancel) persist directly in PostgreSQL.
- Table reset restores original seed data.
"""

import os
import pytest
from src.store_api.db import PostgresOrdersStore
from src.store_api.models import OrderStatus


# Default local port 5433 maps to container's 5432
TEST_DB_URL = os.getenv(
    "TEST_DATABASE_URL",
    "postgresql://postgres:postgrespassword@localhost:5433/shophub_checkpoints",
)


@pytest.fixture(scope="module")
def pg_store():
    """Connects to PostgreSQL test instance or skips if unavailable."""
    try:
        store = PostgresOrdersStore(TEST_DB_URL)
        store.reset()
        yield store
        store.reset()
    except Exception as e:
        pytest.skip(f"PostgreSQL container not reachable at {TEST_DB_URL}: {e}")


def test_postgres_table_seeding(pg_store):
    """Verifies orders table is initialized with 14 seed orders."""
    assert len(pg_store) == 14
    order = pg_store.get_order("order-1001")
    assert order is not None
    assert order.merchant == "merchant_alpha"
    assert order.customer_name == "Maria Silva"
    assert order.status == OrderStatus.DELIVERED
    assert order.total == 129.50


def test_postgres_tenant_isolation(pg_store):
    """Staff from Merchant Alpha only retrieve Merchant Alpha orders from PostgreSQL."""
    alpha_orders = pg_store.list_orders("merchant_alpha")
    beta_orders = pg_store.list_orders("merchant_beta")

    assert len(alpha_orders) > 0
    assert len(beta_orders) > 0
    assert all(o.merchant == "merchant_alpha" for o in alpha_orders)
    assert all(o.merchant == "merchant_beta" for o in beta_orders)

    # Disjoint IDs
    alpha_ids = {o.id for o in alpha_orders}
    beta_ids = {o.id for o in beta_orders}
    assert alpha_ids.isdisjoint(beta_ids)

    # Cross-tenant lookup returns None
    assert pg_store.get_order("order-2001", merchant="merchant_alpha") is None


def test_postgres_order_mutation_durability(pg_store):
    """Verifies updating an order's status mutates the row in PostgreSQL."""
    # order-1002 is originally PAID
    order = pg_store.get_order("order-1002", merchant="merchant_alpha")
    assert order.status == OrderStatus.PAID

    # Mutate to REFUNDED
    updated = pg_store.update_order_status("order-1002", "merchant_alpha", OrderStatus.REFUNDED)
    assert updated is not None
    assert updated.status == OrderStatus.REFUNDED

    # Re-fetch independently to ensure durability in DB
    refetched = pg_store.get_order("order-1002")
    assert refetched.status == OrderStatus.REFUNDED


def test_postgres_reset(pg_store):
    """Verifies reset() restores orders back to seed state."""
    # Mutate
    pg_store.update_order_status("order-1002", "merchant_alpha", OrderStatus.REFUNDED)
    # Reset
    count = pg_store.reset()
    assert count == 14

    # Verify restored to PAID
    order = pg_store.get_order("order-1002")
    assert order.status == OrderStatus.PAID


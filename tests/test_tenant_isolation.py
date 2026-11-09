"""Mandatory Tenant Isolation Tests (R3).

Verifies:
- Staff of merchant A must never see merchant B's orders or customers, even if they ask for an order by id.
- Fail-closed security on unassigned tokens.
- Cross-tenant mutation attempts are rejected with HTTP 404.
"""

import pytest
from fastapi.testclient import TestClient

from src.store_api.app import app as store_app
from src.store_api.app import reset_database


@pytest.fixture(autouse=True)
def reset_db():
    reset_database()


@pytest.fixture
def client():
    return TestClient(store_app)


def test_tenant_isolation_list_orders(client):
    """Merchant A and Merchant B only ever see their own orders."""
    # Merchant Alpha
    resp_a = client.get("/orders", headers={"Authorization": "Bearer token-merchant-alpha"})
    assert resp_a.status_code == 200
    orders_a = resp_a.json()
    assert len(orders_a) > 0
    assert all(o["merchant"] == "merchant_alpha" for o in orders_a)

    # Merchant Beta
    resp_b = client.get("/orders", headers={"Authorization": "Bearer token-merchant-beta"})
    assert resp_b.status_code == 200
    orders_b = resp_b.json()
    assert len(orders_b) > 0
    assert all(o["merchant"] == "merchant_beta" for o in orders_b)

    # Zero intersection between IDs
    ids_a = {o["id"] for o in orders_a}
    ids_b = {o["id"] for o in orders_b}
    assert ids_a.isdisjoint(ids_b)


def test_tenant_isolation_lookup_foreign_order_by_id(client):
    """Merchant A querying Merchant B's order receives 'Order not found' with zero leaked data."""
    # order-2001 is a valid order belonging to Merchant Beta
    resp = client.get(
        "/orders/lookup?id=order-2001",
        headers={"Authorization": "Bearer token-merchant-alpha"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] is False
    assert data["message"] == "Order not found"
    assert "order" not in data


def test_tenant_isolation_cannot_refund_foreign_order(client):
    """Merchant A cannot refund Merchant B's order."""
    resp = client.post(
        "/orders/refund",
        json={"id": "order-2001"},
        headers={"Authorization": "Bearer token-merchant-alpha"},
    )
    assert resp.status_code == 404
    assert "not found" in resp.json()["detail"].lower()


def test_tenant_isolation_cannot_cancel_foreign_order(client):
    """Merchant A cannot cancel Merchant B's order."""
    resp = client.post(
        "/orders/cancel",
        json={"id": "order-2002"},
        headers={"Authorization": "Bearer token-merchant-alpha"},
    )
    assert resp.status_code == 404
    assert "not found" in resp.json()["detail"].lower()


def test_tenant_isolation_fail_closed_null_merchant(client):
    """Token with merchant: null is rejected with 403 Forbidden."""
    resp = client.get("/orders", headers={"Authorization": "Bearer token-no-merchant"})
    assert resp.status_code == 403
    assert "fail-closed" in resp.json()["detail"].lower()


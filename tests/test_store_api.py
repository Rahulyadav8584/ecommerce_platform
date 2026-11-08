"""Tests for ShopHub Mock Store API (R0).

Verifies:
- Auth & token scoping (Merchant Alpha, Merchant Beta, and null-merchant token)
- Tenant isolation (never leak orders or customer data across merchants)
- Legacy 200 OK + status:false envelope handling
- Real HTTP 404 and 409 error responses on mutations
- Fail-closed security for tokens without merchant assignment
"""

import pytest
from fastapi.testclient import TestClient

from src.store_api.app import app


@pytest.fixture(autouse=True)
def reset_db():
    """Reset the mock database to fresh seed state before each test."""
    client = TestClient(app)
    client.post("/test/reset")


@pytest.fixture
def client():
    return TestClient(app)


def test_auth_me_valid_merchants(client):
    """GET /auth/me returns merchant name for valid merchant tokens."""
    # Merchant Alpha
    resp = client.get("/auth/me", headers={"Authorization": "Bearer token-merchant-alpha"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["user"] == "alice_support"
    assert data["merchant"] == "merchant_alpha"

    # Merchant Beta
    resp = client.get("/auth/me", headers={"Authorization": "Bearer token-merchant-beta"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["user"] == "bob_support"
    assert data["merchant"] == "merchant_beta"


def test_auth_me_token_with_null_merchant(client):
    """GET /auth/me returns merchant: null for unassigned token."""
    resp = client.get("/auth/me", headers={"Authorization": "Bearer token-no-merchant"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["user"] == "guest_unassigned"
    assert data["merchant"] is None


def test_auth_me_invalid_or_missing_token(client):
    """Missing or invalid token returns 401 Unauthorized."""
    # Missing header
    assert client.get("/auth/me").status_code == 401

    # Bad token
    assert client.get("/auth/me", headers={"Authorization": "Bearer bad-token"}).status_code == 401

    # Malformed header
    assert client.get("/auth/me", headers={"Authorization": "Basic 12345"}).status_code == 401


def test_list_orders_tenant_isolation(client):
    """Merchant A only sees Merchant A's orders; Merchant B only sees B's orders."""
    resp_a = client.get("/orders", headers={"Authorization": "Bearer token-merchant-alpha"})
    assert resp_a.status_code == 200
    orders_a = resp_a.json()
    assert len(orders_a) >= 8
    assert all(o["merchant"] == "merchant_alpha" for o in orders_a)

    resp_b = client.get("/orders", headers={"Authorization": "Bearer token-merchant-beta"})
    assert resp_b.status_code == 200
    orders_b = resp_b.json()
    assert len(orders_b) >= 6
    assert all(o["merchant"] == "merchant_beta" for o in orders_b)

    # Sets of order IDs must have zero intersection
    ids_a = {o["id"] for o in orders_a}
    ids_b = {o["id"] for o in orders_b}
    assert ids_a.isdisjoint(ids_b)


def test_list_orders_filter_by_status(client):
    """Orders list can be filtered by status."""
    resp = client.get(
        "/orders?status=PAID",
        headers={"Authorization": "Bearer token-merchant-alpha"},
    )
    assert resp.status_code == 200
    orders = resp.json()
    assert len(orders) > 0
    assert all(o["status"] == "PAID" for o in orders)


def test_list_orders_fail_closed_for_null_merchant(client):
    """Caller with valid token but no merchant gets 403 Forbidden."""
    resp = client.get("/orders", headers={"Authorization": "Bearer token-no-merchant"})
    assert resp.status_code == 403
    assert "fail-closed" in resp.json()["detail"].lower()


def test_legacy_lookup_success(client):
    """GET /orders/lookup returns HTTP 200 with status: true for owned orders."""
    resp = client.get(
        "/orders/lookup?id=order-1001",
        headers={"Authorization": "Bearer token-merchant-alpha"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] is True
    assert data["order"]["id"] == "order-1001"
    assert data["order"]["merchant"] == "merchant_alpha"


def test_legacy_lookup_returns_200_with_status_false_on_missing_order(client):
    """Legacy endpoint MUST return HTTP 200 with status: false for non-existent order."""
    resp = client.get(
        "/orders/lookup?id=order-nonexistent",
        headers={"Authorization": "Bearer token-merchant-alpha"},
    )
    # Critical: HTTP status must be 200, not 404
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] is False
    assert data["message"] == "Order not found"


def test_legacy_lookup_returns_200_with_status_false_on_cross_tenant_access(client):
    """Merchant A querying Merchant B's order receives 200 with status: false (never leaks data)."""
    # order-2001 belongs to Merchant Beta
    resp = client.get(
        "/orders/lookup?id=order-2001",
        headers={"Authorization": "Bearer token-merchant-alpha"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] is False
    assert data["message"] == "Order not found"
    assert "order" not in data


def test_refund_order_success(client):
    """Refunding a PAID order mutates status to REFUNDED."""
    # order-1002 is PAID
    resp = client.post(
        "/orders/refund",
        json={"id": "order-1002"},
        headers={"Authorization": "Bearer token-merchant-alpha"},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "REFUNDED"

    # Verify persistence in subsequent lookup
    lookup = client.get(
        "/orders/lookup?id=order-1002",
        headers={"Authorization": "Bearer token-merchant-alpha"},
    )
    assert lookup.json()["order"]["status"] == "REFUNDED"


def test_refund_order_real_404_for_foreign_or_missing_order(client):
    """Refund returns HTTP 404 for missing order or order of another merchant."""
    # Non-existent
    resp = client.post(
        "/orders/refund",
        json={"id": "order-unknown"},
        headers={"Authorization": "Bearer token-merchant-alpha"},
    )
    assert resp.status_code == 404

    # Foreign order (order-2001 belongs to Beta)
    resp = client.post(
        "/orders/refund",
        json={"id": "order-2001"},
        headers={"Authorization": "Bearer token-merchant-alpha"},
    )
    assert resp.status_code == 404


def test_refund_order_real_409_for_invalid_state(client):
    """Refunding an order that is already CANCELLED or REFUNDED returns HTTP 409 Conflict."""
    # order-1006 is already CANCELLED
    resp = client.post(
        "/orders/refund",
        json={"id": "order-1006"},
        headers={"Authorization": "Bearer token-merchant-alpha"},
    )
    assert resp.status_code == 409
    assert "Cannot refund" in resp.json()["detail"]


def test_cancel_order_success(client):
    """Cancelling a PLACED or PAID order mutates status to CANCELLED."""
    # order-1004 is PLACED
    resp = client.post(
        "/orders/cancel",
        json={"id": "order-1004"},
        headers={"Authorization": "Bearer token-merchant-alpha"},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "CANCELLED"


def test_cancel_order_real_409_for_shipped_or_delivered(client):
    """Cancelling an already SHIPPED or DELIVERED order returns HTTP 409 Conflict."""
    # order-1001 is DELIVERED
    resp = client.post(
        "/orders/cancel",
        json={"id": "order-1001"},
        headers={"Authorization": "Bearer token-merchant-alpha"},
    )
    assert resp.status_code == 409
    assert "Cannot cancel" in resp.json()["detail"]


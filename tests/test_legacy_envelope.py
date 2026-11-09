"""Tests for Legacy 200 OK Envelope Handling (R1).

The legacy 200 + status:false endpoint is intentional. Old APIs behave like this,
and an agent that trusts the HTTP status will lie to its users.
This suite proves our agents correctly handle the envelope as a failure.
"""

import pytest
from fastapi.testclient import TestClient

from src.store_api.app import app as store_app, reset_database
from src.sub_agent.tools import StoreTools, StoreAPIError


@pytest.fixture(autouse=True)
def reset_db():
    reset_database()


@pytest.fixture
def client():
    return TestClient(store_app)


def test_legacy_endpoint_always_returns_http_200_on_failure(client):
    """Proves the store API emits HTTP 200 even when order does not exist or belongs to another merchant."""
    # 1. Non-existent order
    resp = client.get(
        "/orders/lookup?id=order-nonexistent",
        headers={"Authorization": "Bearer token-merchant-alpha"},
    )
    assert resp.status_code == 200
    assert resp.json() == {"status": False, "message": "Order not found"}

    # 2. Foreign merchant order (order-2001 belongs to Merchant Beta)
    resp2 = client.get(
        "/orders/lookup?id=order-2001",
        headers={"Authorization": "Bearer token-merchant-alpha"},
    )
    assert resp2.status_code == 200
    assert resp2.json() == {"status": False, "message": "Order not found"}


def test_sub_agent_treats_legacy_200_status_false_as_failure():
    """Proves the StoreTools client raises StoreAPIError when receiving status: false."""
    tools = StoreTools(in_memory=True)

    with pytest.raises(StoreAPIError) as exc_info:
        tools.lookup_order("token-merchant-alpha", "order-nonexistent")

    assert exc_info.value.code == -32004
    assert "not found" in exc_info.value.message.lower()


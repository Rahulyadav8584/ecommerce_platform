"""Store API client and tool implementations for the Domain Sub-Agent (R1, R2, R3).

Handles:
- Forwarding caller's Bearer token directly to the Mock Store API (R3)
- Normalizing the legacy 200 OK + status: false failure envelope (R1)
- Invoking guardrails to scrub PII and neutralize prompt injections (R4)
- Generating structured proposals for destructive actions (R5)
- Dual mode: HTTP REST client for production/Docker, in-memory direct mode for offline tests
"""

import os
from typing import Any, Dict, List, Optional
import httpx

from src.store_api.models import OrderStatus
from src.store_api.seed_data import MERCHANT_TOKENS
from src.sub_agent.guardrails import sanitize_order_for_model

STORE_API_URL = os.getenv("STORE_API_URL", "http://localhost:8001")


class StoreAPIError(Exception):
    """Custom exception raised for store API failures or business rule violations."""
    def __init__(self, message: str, code: int = -32603):
        super().__init__(message)
        self.message = message
        self.code = code


class StoreTools:
    """Methods exposed to the Orchestrator via JSON-RPC 2.0."""

    def __init__(
        self,
        base_url: str = STORE_API_URL,
        client: Optional[httpx.Client] = None,
        in_memory: bool = False,
    ):
        self.base_url = base_url.rstrip("/")
        self._client = client
        self.in_memory = in_memory

    def _get_headers(self, bearer_token: str) -> Dict[str, str]:
        """Forwards caller's bearer token unchanged."""
        return {
            "Authorization": f"Bearer {bearer_token}",
            "Content-Type": "application/json",
        }

    def _authenticate_in_memory(self, bearer_token: str) -> str:
        """Internal helper for in-memory mode."""
        caller = MERCHANT_TOKENS.get(bearer_token)
        if not caller:
            raise StoreAPIError("Unauthorized: Invalid bearer token", code=-32001)
        merchant = caller.get("merchant")
        if not merchant:
            raise StoreAPIError("Forbidden: Caller has no assigned merchant (fail-closed)", code=-32003)
        return merchant

    def _request(
        self,
        method: str,
        path: str,
        bearer_token: str,
        params: Optional[Dict[str, Any]] = None,
        json: Optional[Dict[str, Any]] = None,
    ) -> httpx.Response:
        headers = self._get_headers(bearer_token)

        if self._client:
            return self._client.request(method, path, headers=headers, params=params, json=json)

        url = f"{self.base_url}{path}"
        with httpx.Client(timeout=10.0) as client:
            return client.request(method, url, headers=headers, params=params, json=json)

    def lookup_order(self, bearer_token: str, order_id: Optional[str] = None, id: Optional[str] = None) -> Dict[str, Any]:
        """Looks up an order by ID (accepts 'id' or 'order_id')."""
        target_id = order_id or id
        if not target_id:
            raise StoreAPIError("Missing order id", code=-32602)

        if self.in_memory:
            from src.store_api.app import _orders_db
            merchant = self._authenticate_in_memory(bearer_token)
            order = _orders_db.get(target_id)
            if not order or order.merchant != merchant:
                raise StoreAPIError(
                    f"Order '{target_id}' not found or does not belong to your store.",
                    code=-32004,
                )
            return sanitize_order_for_model(order.model_dump())

        response = self._request("GET", "/orders/lookup", bearer_token=bearer_token, params={"id": target_id})

        if response.status_code == 401:
            raise StoreAPIError("Unauthorized: Invalid bearer token", code=-32001)
        if response.status_code == 403:
            raise StoreAPIError("Forbidden: Caller has no assigned merchant", code=-32003)

        payload = response.json()
        if not payload.get("status", False):
            raise StoreAPIError(
                f"Order '{target_id}' not found or does not belong to your store.",
                code=-32004,
            )

        raw_order = payload.get("order")
        if not raw_order:
            raise StoreAPIError(f"Order '{target_id}' data missing from response.", code=-32004)

        return sanitize_order_for_model(raw_order)

    def list_orders(self, bearer_token: str, status: Optional[str] = None) -> List[Dict[str, Any]]:
        """Lists orders belonging to caller's merchant, sanitized against PII & injections."""
        if self.in_memory:
            from src.store_api.app import _orders_db
            merchant = self._authenticate_in_memory(bearer_token)
            orders = [o for o in _orders_db.values() if o.merchant == merchant]
            if status:
                orders = [o for o in orders if o.status == status]
            orders.sort(key=lambda o: o.placed_at, reverse=True)
            return [sanitize_order_for_model(o.model_dump()) for o in orders]

        params = {"status": status} if status else None
        response = self._request("GET", "/orders", bearer_token=bearer_token, params=params)

        if response.status_code == 401:
            raise StoreAPIError("Unauthorized: Invalid bearer token", code=-32001)
        if response.status_code == 403:
            raise StoreAPIError("Forbidden: Caller has no assigned merchant (fail-closed)", code=-32003)
        if response.status_code != 200:
            raise StoreAPIError(f"Failed to list orders: {response.text}", code=-32603)

        orders = response.json()
        return [sanitize_order_for_model(o) for o in orders]

    def propose_refund(self, bearer_token: str, order_id: Optional[str] = None, id: Optional[str] = None) -> Dict[str, Any]:
        """Validates order eligibility and generates a structured proposal for HITL approval."""
        target_id = order_id or id
        if not target_id:
            raise StoreAPIError("Missing order id", code=-32602)

        order = self.lookup_order(bearer_token, target_id)
        current_status = order["status"]

        eligible_statuses = {"PAID", "SHIPPED", "DELIVERED"}
        if current_status not in eligible_statuses:
            raise StoreAPIError(
                f"Cannot propose refund: Order '{target_id}' is in status '{current_status}'. "
                f"Only PAID, SHIPPED, or DELIVERED orders can be refunded.",
                code=-32005,
            )

        amount = order["total"]
        return {
            "proposal_type": "REFUND",
            "order_id": target_id,
            "merchant": order["merchant"],
            "customer_name": order["customer_name"],
            "current_status": current_status,
            "target_status": "REFUNDED",
            "amount": amount,
            "delta_summary": (
                f"Order {target_id} (Customer: {order['customer_name']}) status will change from "
                f"'{current_status}' to 'REFUNDED', refunding ${amount:.2f}."
            ),
        }

    def execute_refund(self, bearer_token: str, order_id: Optional[str] = None, id: Optional[str] = None) -> Dict[str, Any]:
        """Executes an approved refund mutation against the store API."""
        target_id = order_id or id
        if not target_id:
            raise StoreAPIError("Missing order id", code=-32602)

        if self.in_memory:
            from src.store_api.app import _orders_db
            merchant = self._authenticate_in_memory(bearer_token)
            order = _orders_db.get(target_id)
            if not order or order.merchant != merchant:
                raise StoreAPIError(f"Order '{target_id}' not found or foreign merchant", code=-32004)
            if order.status not in {OrderStatus.PAID, OrderStatus.SHIPPED, OrderStatus.DELIVERED}:
                raise StoreAPIError(f"Refund conflict: cannot refund order in '{order.status}' status", code=-32009)
            order.status = OrderStatus.REFUNDED
            _orders_db[target_id] = order
            return sanitize_order_for_model(order.model_dump())

        response = self._request(
            "POST",
            "/orders/refund",
            bearer_token=bearer_token,
            json={"id": target_id},
        )

        if response.status_code == 404:
            raise StoreAPIError(f"Order '{target_id}' not found or foreign merchant", code=-32004)
        if response.status_code == 409:
            detail = response.json().get("detail", "Invalid state transition")
            raise StoreAPIError(f"Refund conflict: {detail}", code=-32009)
        if response.status_code != 200:
            raise StoreAPIError(f"Refund failed: {response.text}", code=-32603)

        return sanitize_order_for_model(response.json())

    def propose_cancel(self, bearer_token: str, order_id: Optional[str] = None, id: Optional[str] = None) -> Dict[str, Any]:
        """Validates order eligibility and generates a structured proposal for HITL approval."""
        target_id = order_id or id
        if not target_id:
            raise StoreAPIError("Missing order id", code=-32602)

        order = self.lookup_order(bearer_token, target_id)
        current_status = order["status"]

        eligible_statuses = {"PLACED", "PAID"}
        if current_status not in eligible_statuses:
            raise StoreAPIError(
                f"Cannot propose cancellation: Order '{target_id}' is in status '{current_status}'. "
                f"Orders that are SHIPPED, DELIVERED, CANCELLED, or REFUNDED cannot be cancelled.",
                code=-32005,
            )

        return {
            "proposal_type": "CANCEL",
            "order_id": target_id,
            "merchant": order["merchant"],
            "customer_name": order["customer_name"],
            "current_status": current_status,
            "target_status": "CANCELLED",
            "amount": order["total"],
            "delta_summary": (
                f"Order {target_id} (Customer: {order['customer_name']}) status will change from "
                f"'{current_status}' to 'CANCELLED'."
            ),
        }

    def execute_cancel(self, bearer_token: str, order_id: Optional[str] = None, id: Optional[str] = None) -> Dict[str, Any]:
        """Executes an approved cancellation mutation against the store API."""
        target_id = order_id or id
        if not target_id:
            raise StoreAPIError("Missing order id", code=-32602)

        if self.in_memory:
            from src.store_api.app import _orders_db
            merchant = self._authenticate_in_memory(bearer_token)
            order = _orders_db.get(target_id)
            if not order or order.merchant != merchant:
                raise StoreAPIError(f"Order '{target_id}' not found or foreign merchant", code=-32004)
            if order.status not in {OrderStatus.PLACED, OrderStatus.PAID}:
                raise StoreAPIError(f"Cancellation conflict: cannot cancel order in '{order.status}' status", code=-32009)
            order.status = OrderStatus.CANCELLED
            _orders_db[target_id] = order
            return sanitize_order_for_model(order.model_dump())

        response = self._request(
            "POST",
            "/orders/cancel",
            bearer_token=bearer_token,
            json={"id": target_id},
        )

        if response.status_code == 404:
            raise StoreAPIError(f"Order '{target_id}' not found or foreign merchant", code=-32004)
        if response.status_code == 409:
            detail = response.json().get("detail", "Invalid state transition")
            raise StoreAPIError(f"Cancellation conflict: {detail}", code=-32009)
        if response.status_code != 200:
            raise StoreAPIError(f"Cancel failed: {response.text}", code=-32603)

        return sanitize_order_for_model(response.json())

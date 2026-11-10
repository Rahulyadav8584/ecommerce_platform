"""ShopHub Mock Store API (R0).

An in-memory FastAPI service demonstrating:
- Multi-merchant bearer token authentication
- Tenant isolation (merchants can only see/mutate their own orders)
- Legacy 200 OK error envelope on GET /orders/lookup
- Real HTTP 404 / 409 errors on refund and cancel
"""

from typing import Dict, List, Optional
from fastapi import Depends, FastAPI, HTTPException, Query, Request, status
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from src.store_api.models import ActionRequest, AuthMeResponse, Order, OrderStatus
from src.store_api.seed_data import MERCHANT_TOKENS
from src.store_api.db import get_order_store

app = FastAPI(
    title="ShopHub Mock Store API",
    version="0.1.0",
    description="Mock Store API with multi-merchant bearer token authentication, PostgreSQL durability, and legacy envelope handling.",
)

# Durable PostgreSQL store (falls back to in-memory store if DB is unconfigured/offline)
orders_store = get_order_store()
_orders_db = orders_store

# OpenAPI HTTPBearer security scheme (adds the green 'Authorize' button in Swagger UI)
bearer_scheme = HTTPBearer(auto_error=False)


def get_current_caller(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
) -> Dict[str, Optional[str]]:
    """Validates the Bearer token and returns caller context: {user, merchant}.

    Extracts token from either HTTPBearer scheme or raw Authorization header.
    Raises HTTP 401 if missing or invalid.
    """
    token: Optional[str] = None

    if credentials and credentials.scheme.lower() == "bearer":
        token = credentials.credentials
    elif "authorization" in request.headers:
        raw_auth = request.headers.get("authorization", "")
        parts = raw_auth.split()
        if len(parts) >= 2 and parts[0].lower() == "bearer":
            token = parts[-1]
        elif len(parts) == 1:
            token = parts[0]

    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing Authorization header",
        )

    # Defensive cleanup: strip accidental repeated 'Bearer ' prefixes
    while token.lower().startswith("bearer "):
        token = token.split(maxsplit=1)[1].strip()

    caller = MERCHANT_TOKENS.get(token)
    if not caller:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid or unrecognized bearer token '{token}'",
        )

    return caller


@app.get("/auth/me", response_model=AuthMeResponse)
def auth_me(caller: Dict[str, Optional[str]] = Depends(get_current_caller)) -> AuthMeResponse:
    """Returns {user, merchant} for the caller. Merchant may be null."""
    return AuthMeResponse(user=caller["user"], merchant=caller.get("merchant"))


@app.get("/orders", response_model=List[Order])
def list_orders(
    status_filter: Optional[OrderStatus] = Query(None, alias="status"),
    caller: Dict[str, Optional[str]] = Depends(get_current_caller),
) -> List[Order]:
    """Lists orders belonging exclusively to the caller's merchant.

    Fails closed (403 Forbidden) if the caller has no assigned merchant.
    """
    merchant = caller.get("merchant")
    if not merchant:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Caller has no assigned merchant (fail-closed)",
        )

    results = orders_store.list_orders(merchant, status_filter)
    return results


@app.get("/orders/lookup")
def lookup_order(
    id: str = Query(..., description="Order ID to look up"),
    caller: Dict[str, Optional[str]] = Depends(get_current_caller),
) -> JSONResponse:
    """Legacy endpoint behaviour: always returns HTTP 200.

    On failure (unknown order, or order belonging to another merchant):
      {"status": false, "message": "Order not found"}
    On success:
      {"status": true, "order": {...}}
    """
    merchant = caller.get("merchant")

    # If caller has no merchant, fail legacy envelope with 200 OK
    if not merchant:
        return JSONResponse(
            status_code=status.HTTP_200_OK,
            content={"status": False, "message": "Order not found"},
        )

    order = orders_store.get_order(id, merchant)
    if not order:
        # Cross-tenant access or missing order: legacy 200 with status: false
        return JSONResponse(
            status_code=status.HTTP_200_OK,
            content={"status": False, "message": "Order not found"},
        )

    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content={"status": True, "order": order.model_dump()},
    )


@app.post("/orders/refund", response_model=Order)
def refund_order(
    payload: ActionRequest,
    caller: Dict[str, Optional[str]] = Depends(get_current_caller),
) -> Order:
    """Refunds an order.

    Real HTTP 404 for unknown or foreign orders.
    Real HTTP 409 for invalid state transitions.
    """
    merchant = caller.get("merchant")
    if not merchant:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Caller has no assigned merchant",
        )

    order = orders_store.get_order(payload.id, merchant)
    if not order:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Order '{payload.id}' not found",
        )

    # State transition validation: can only refund if paid/shipped/delivered
    if order.status not in {OrderStatus.PAID, OrderStatus.SHIPPED, OrderStatus.DELIVERED}:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Cannot refund order in '{order.status}' status. Only PAID, SHIPPED, or DELIVERED orders can be refunded.",
        )

    # Perform mutation in store (persists to PostgreSQL when connected)
    updated = orders_store.update_order_status(payload.id, merchant, OrderStatus.REFUNDED)
    return updated or order


@app.post("/orders/cancel", response_model=Order)
def cancel_order(
    payload: ActionRequest,
    caller: Dict[str, Optional[str]] = Depends(get_current_caller),
) -> Order:
    """Cancels an order.

    Real HTTP 404 for unknown or foreign orders.
    Real HTTP 409 for invalid state transitions.
    """
    merchant = caller.get("merchant")
    if not merchant:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Caller has no assigned merchant",
        )

    order = orders_store.get_order(payload.id, merchant)
    if not order:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Order '{payload.id}' not found",
        )

    # State transition validation: can only cancel if placed or paid
    if order.status not in {OrderStatus.PLACED, OrderStatus.PAID}:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Cannot cancel order in '{order.status}' status. Orders already SHIPPED, DELIVERED, CANCELLED, or REFUNDED cannot be cancelled.",
        )

    # Perform mutation in store (persists to PostgreSQL when connected)
    updated = orders_store.update_order_status(payload.id, merchant, OrderStatus.CANCELLED)
    return updated or order


@app.post("/test/reset")
def reset_database() -> Dict[str, str]:
    """Helper for testing to reset the database to seed state."""
    count = orders_store.reset()
    return {"status": "reset", "order_count": str(count)}

"""Data models for ShopHub Mock Store API (R0).

Kept simple and standard with Pydantic v2.
"""

from enum import Enum
from typing import Optional
from pydantic import BaseModel, EmailStr


class OrderStatus(str, Enum):
    """Allowed order lifecycle states according to specification."""
    PLACED = "PLACED"
    PAID = "PAID"
    SHIPPED = "SHIPPED"
    DELIVERED = "DELIVERED"
    CANCELLED = "CANCELLED"
    REFUNDED = "REFUNDED"


class Order(BaseModel):
    """Represents a store order scoped to a specific merchant."""
    id: str
    merchant: str
    customer_name: str
    customer_email: str
    customer_phone: str
    status: OrderStatus
    total: float
    placed_at: str  # ISO-8601 formatted timestamp string
    customer_note: Optional[str] = None


class ActionRequest(BaseModel):
    """Payload for mutating order actions (refund/cancel)."""
    id: str


class AuthMeResponse(BaseModel):
    """Returned by GET /auth/me."""
    user: str
    merchant: Optional[str] = None


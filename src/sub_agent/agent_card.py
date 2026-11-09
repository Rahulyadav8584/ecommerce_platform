"""Agent Card specification for ShopHub Domain Sub-Agent (R2).

Describes the agent's identity, protocol, skills, parameter schemas,
and endpoints so the Orchestrator can discover and interact with it dynamically.
"""

from typing import Any, Dict


def get_agent_card(base_url: str = "http://localhost:8002") -> Dict[str, Any]:
    """Generates the Agent Card manifest for discovery."""
    return {
        "name": "orders-agent",
        "description": "Domain sub-agent that owns order query and mutation workflows for ShopHub merchants.",
        "version": "1.0.0",
        "protocol": "JSON-RPC-2.0",
        "endpoint": f"{base_url}/rpc",
        "authentication": {
            "type": "bearer",
            "forwarding_required": True,
            "description": "Caller's Bearer token must be forwarded unchanged in the Authorization header.",
        },
        "skills": [
            {
                "name": "lookup_order",
                "description": "Look up an order by its unique ID. Returns sanitized order details or an error.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "id": {
                            "type": "string",
                            "description": "The unique order ID (e.g. 'order-1001')",
                        }
                    },
                    "required": ["id"],
                },
            },
            {
                "name": "list_orders",
                "description": "List all orders belonging to the caller's store, optionally filtered by status.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "status": {
                            "type": "string",
                            "enum": ["PLACED", "PAID", "SHIPPED", "DELIVERED", "CANCELLED", "REFUNDED"],
                            "description": "Optional order status filter",
                        }
                    },
                },
            },
            {
                "name": "propose_refund",
                "description": "Validates if an order can be refunded and produces a structured proposal for human approval (HITL). Does not mutate state.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "id": {
                            "type": "string",
                            "description": "The unique order ID to refund",
                        }
                    },
                    "required": ["id"],
                },
            },
            {
                "name": "execute_refund",
                "description": "Executes an approved refund mutation against the store API.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "id": {
                            "type": "string",
                            "description": "The unique order ID to refund",
                        }
                    },
                    "required": ["id"],
                },
            },
            {
                "name": "propose_cancel",
                "description": "Validates if an order can be cancelled and produces a structured proposal for human approval (HITL). Does not mutate state.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "id": {
                            "type": "string",
                            "description": "The unique order ID to cancel",
                        }
                    },
                    "required": ["id"],
                },
            },
            {
                "name": "execute_cancel",
                "description": "Executes an approved order cancellation mutation against the store API.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "id": {
                            "type": "string",
                            "description": "The unique order ID to cancel",
                        }
                    },
                    "required": ["id"],
                },
            },
        ],
    }


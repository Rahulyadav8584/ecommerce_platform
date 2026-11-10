"""JSON-RPC 2.0 Domain Sub-Agent Service for ShopHub (R2, R3, R4).

Runs on Port 8002 and exposes:
- GET /agent-card.json: Dynamic discovery manifest
- POST /rpc: JSON-RPC 2.0 endpoint handling order queries & mutations
"""

from typing import Any, Dict, Optional, Union
from fastapi import Depends, FastAPI, Request, status
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field

from src.sub_agent.agent_card import get_agent_card
from src.sub_agent.tools import StoreAPIError, StoreTools

app = FastAPI(
    title="ShopHub Orders Sub-Agent",
    version="1.0.0",
    description="Domain sub-agent exposing order tools over JSON-RPC 2.0 with dynamic agent card discovery.",
)

tools = StoreTools()

# Enables the green 'Authorize' button in Swagger UI
bearer_scheme = HTTPBearer(auto_error=False)


class JsonRpcRequest(BaseModel):
    """Standard JSON-RPC 2.0 Request envelope."""
    jsonrpc: str = Field(default="2.0", description="Must be '2.0'")
    method: str = Field(..., description="Method name to invoke")
    params: Optional[Dict[str, Any]] = Field(default_factory=dict, description="Named method arguments")
    id: Optional[Union[str, int]] = Field(None, description="Client request ID")


def _jsonrpc_error(code: int, message: str, req_id: Optional[Union[str, int]] = None, data: Optional[Any] = None) -> JSONResponse:
    """Builds a compliant JSON-RPC 2.0 error response."""
    error_payload = {"code": code, "message": message}
    if data is not None:
        error_payload["data"] = data
    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content={"jsonrpc": "2.0", "error": error_payload, "id": req_id},
    )


def _jsonrpc_success(result: Any, req_id: Optional[Union[str, int]] = None) -> JSONResponse:
    """Builds a compliant JSON-RPC 2.0 success response."""
    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content={"jsonrpc": "2.0", "result": result, "id": req_id},
    )


@app.get("/agent-card.json")
def agent_card(request: Request) -> Dict[str, Any]:
    """Returns the Agent Card describing identity, protocol, and skills."""
    base_url = str(request.base_url).rstrip("/")
    return get_agent_card(base_url)


@app.post("/rpc")
async def json_rpc_endpoint(
    req: JsonRpcRequest,
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
) -> JSONResponse:
    """Executes JSON-RPC 2.0 requests with caller credential forwarding."""
    # 1. Validate JSON-RPC version
    if req.jsonrpc != "2.0":
        return _jsonrpc_error(-32600, "Invalid Request: 'jsonrpc' must be '2.0'", req.id)

    # 2. Extract Bearer token (supports Swagger Authorize button and raw headers)
    bearer_token: Optional[str] = None
    if credentials and credentials.scheme.lower() == "bearer":
        bearer_token = credentials.credentials
    elif "authorization" in request.headers:
        raw_auth = request.headers.get("authorization", "")
        parts = raw_auth.split()
        if len(parts) >= 2 and parts[0].lower() == "bearer":
            bearer_token = parts[-1]
        elif len(parts) == 1:
            bearer_token = parts[0]

    if not bearer_token:
        return _jsonrpc_error(-32001, "Unauthorized: Missing Authorization header with Bearer token", req.id)

    # Defensive cleanup: strip accidental repeated 'Bearer ' prefixes
    while bearer_token.lower().startswith("bearer "):
        bearer_token = bearer_token.split(maxsplit=1)[1].strip()

    params = req.params or {}

    # 3. Route method
    try:
        if req.method == "lookup_order":
            order_id = params.get("id")
            if not order_id:
                return _jsonrpc_error(-32602, "Invalid params: 'id' is required for lookup_order", req.id)
            result = tools.lookup_order(bearer_token, order_id=order_id)
            return _jsonrpc_success(result, req.id)

        elif req.method == "list_orders":
            status_filter = params.get("status")
            result = tools.list_orders(bearer_token, status=status_filter)
            return _jsonrpc_success(result, req.id)

        elif req.method == "propose_refund":
            order_id = params.get("id")
            if not order_id:
                return _jsonrpc_error(-32602, "Invalid params: 'id' is required for propose_refund", req.id)
            result = tools.propose_refund(bearer_token, order_id=order_id)
            return _jsonrpc_success(result, req.id)

        elif req.method == "execute_refund":
            order_id = params.get("id")
            if not order_id:
                return _jsonrpc_error(-32602, "Invalid params: 'id' is required for execute_refund", req.id)
            result = tools.execute_refund(bearer_token, order_id=order_id)
            return _jsonrpc_success(result, req.id)

        elif req.method == "propose_cancel":
            order_id = params.get("id")
            if not order_id:
                return _jsonrpc_error(-32602, "Invalid params: 'id' is required for propose_cancel", req.id)
            result = tools.propose_cancel(bearer_token, order_id=order_id)
            return _jsonrpc_success(result, req.id)

        elif req.method == "execute_cancel":
            order_id = params.get("id")
            if not order_id:
                return _jsonrpc_error(-32602, "Invalid params: 'id' is required for execute_cancel", req.id)
            result = tools.execute_cancel(bearer_token, order_id=order_id)
            return _jsonrpc_success(result, req.id)

        else:
            return _jsonrpc_error(-32601, f"Method '{req.method}' not found", req.id)

    except StoreAPIError as e:
        return _jsonrpc_error(e.code, e.message, req.id)
    except Exception as e:
        return _jsonrpc_error(-32603, f"Internal sub-agent error: {str(e)}", req.id)

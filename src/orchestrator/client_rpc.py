"""Sub-Agent Client with Dynamic Agent Card Discovery over HTTP (R2, R3).

The orchestrator discovers the sub-agent via GET /agent-card.json and invokes its
tools over JSON-RPC 2.0 over HTTP (never by importing internal modules).
Caller credentials (Bearer tokens) are forwarded per-invocation.
"""

import os
from typing import Any, Dict, List, Optional
import httpx

SUB_AGENT_URL = os.getenv("SUB_AGENT_URL", "http://localhost:8002")


class SubAgentClient:
    """HTTP Client interacting with the domain sub-agent via A2A standards."""

    def __init__(self, base_url: str = SUB_AGENT_URL, client: Optional[httpx.Client] = None):
        self.base_url = base_url.rstrip("/")
        self._client = client
        self.agent_card: Optional[Dict[str, Any]] = None

    def discover(self) -> Dict[str, Any]:
        """Discovers skills, protocols, and endpoints by querying the Agent Card over HTTP."""
        url = f"{self.base_url}/agent-card.json"
        if self._client:
            resp = self._client.get(url)
        else:
            with httpx.Client(timeout=5.0) as client:
                resp = client.get(url)

        if resp.status_code != 200:
            raise RuntimeError(f"Failed to discover sub-agent at {url}: HTTP {resp.status_code}")

        self.agent_card = resp.json()
        return self.agent_card

    def invoke_rpc(
        self,
        method: str,
        params: Dict[str, Any],
        bearer_token: str,
        request_id: Any = 1,
    ) -> Dict[str, Any]:
        """Invokes a JSON-RPC 2.0 method over HTTP, forwarding the caller's Bearer token (R3)."""
        url = f"{self.base_url}/rpc"
        headers = {
            "Authorization": f"Bearer {bearer_token}",
            "Content-Type": "application/json",
        }
        payload = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params,
            "id": request_id,
        }

        if self._client:
            resp = self._client.post(url, headers=headers, json=payload)
        else:
            with httpx.Client(timeout=10.0) as client:
                resp = client.post(url, headers=headers, json=payload)

        if resp.status_code != 200:
            return {"error": f"HTTP {resp.status_code}: {resp.text}"}

        data = resp.json()
        if "error" in data:
            # Sub-agent returned structured JSON-RPC error
            return {"error": data["error"].get("message", "Unknown sub-agent error")}

        return data.get("result", {})


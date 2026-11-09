"""FastAPI Orchestrator Service exposing Streaming SSE & HITL chat (R1, R5, R6, R7).

Runs on Port 8000 and exposes:
- POST /chat/stream: SSE endpoint streaming tokens, tool events, and proposals
- POST /chat/resume: Resumes interrupted graphs on human approval/rejection
- GET /health: Service health check
"""

import json
import os
from typing import Any, AsyncIterator, Dict, Optional
from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
import httpx
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.types import Command

from src.orchestrator.client_rpc import SubAgentClient
from src.orchestrator.graph import build_orchestrator_graph
from src.orchestrator.persistence import get_checkpointer

app = FastAPI(
    title="ShopHub Orchestrator Agent",
    version="1.0.0",
    description="Multi-tenant LangGraph orchestrator with A2A sub-agent discovery, SSE streaming, and HITL interrupts.",
)

bearer_scheme = HTTPBearer(auto_error=False)
STORE_API_URL = os.getenv("STORE_API_URL", "http://localhost:8001")
SUB_AGENT_URL = os.getenv("SUB_AGENT_URL", "http://localhost:8002")

# Shared checkpointer and graph
checkpointer = get_checkpointer()
sub_agent_client = SubAgentClient(base_url=SUB_AGENT_URL)
orchestrator_graph = build_orchestrator_graph(sub_agent_client=sub_agent_client, checkpointer=checkpointer)


class ChatRequest(BaseModel):
    """Payload to initiate or continue a chat session."""
    message: str = Field(..., description="User chat query")
    thread_id: str = Field(..., description="Unique persistent conversation session ID")


class ResumeRequest(BaseModel):
    """Payload to approve or reject a pending HITL proposal."""
    thread_id: str = Field(..., description="The conversation thread ID waiting for approval")
    decision: str = Field(..., description="'approve' or 'reject'")


def extract_bearer_token(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
) -> str:
    """Extracts the Bearer token strictly per RFC 6750."""
    if credentials and credentials.scheme.lower() == "bearer":
        return credentials.credentials
    if "authorization" in request.headers:
        parts = request.headers.get("authorization", "").strip().split()
        if len(parts) == 2 and parts[0].lower() == "bearer":
            return parts[1]
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Missing or malformed Authorization header. Expected 'Bearer <token>'",
    )


def verify_tenant_auth(bearer_token: str) -> Dict[str, Optional[str]]:
    """Verifies token against Store API and enforces fail-closed merchant scoping (R4c)."""
    url = f"{STORE_API_URL}/auth/me"
    try:
        with httpx.Client(timeout=5.0) as client:
            resp = client.get(url, headers={"Authorization": f"Bearer {bearer_token}"})
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Store API unreachable at {url}: {e}",
        )

    if resp.status_code == 401:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid bearer token")
    if resp.status_code != 200:
        raise HTTPException(status_code=resp.status_code, detail=resp.text)

    data = resp.json()
    # (c) Fail closed: a request with a valid token but no merchant is rejected, not served.
    if not data.get("merchant"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Forbidden: Caller has no assigned merchant store (fail-closed)",
        )

    return data


@app.get("/health")
def health() -> Dict[str, str]:
    return {"status": "ok", "service": "orchestrator"}


@app.post("/chat/stream")
async def chat_stream(
    req: ChatRequest,
    bearer_token: str = Depends(extract_bearer_token),
) -> EventSourceResponse:
    """SSE endpoint streaming tokens, tool activity, and HITL proposals."""
    # Enforce fail-closed auth before initiating stream
    caller = verify_tenant_auth(bearer_token)
    merchant = caller["merchant"]

    config = {"configurable": {"thread_id": req.thread_id}}

    async def event_generator() -> AsyncIterator[Dict[str, str]]:
        input_state = {
            "messages": [HumanMessage(content=req.message)],
            "bearer_token": bearer_token,
            "merchant": merchant,
            "pending_proposal": None,
        }

        try:
            for chunk in orchestrator_graph.stream(input_state, config=config):
                # 1. Check for LangGraph Interrupt (R5 HITL Proposal)
                if "__interrupt__" in chunk:
                    interrupt_val = chunk["__interrupt__"][0].value
                    yield {
                        "event": "proposal_required",
                        "data": json.dumps(interrupt_val),
                    }
                    return

                # 2. Check for node updates
                if "agent" in chunk:
                    agent_msg = chunk["agent"]["messages"][-1]
                    if agent_msg.tool_calls:
                        for tc in agent_msg.tool_calls:
                            yield {
                                "event": "tool_started",
                                "data": json.dumps({"tool": tc["name"], "args": tc["args"]}),
                            }
                    elif agent_msg.content:
                        # Stream the final text response in chunks
                        words = agent_msg.content.split(" ")
                        for i, word in enumerate(words):
                            token = word if i == 0 else f" {word}"
                            yield {"event": "token", "data": json.dumps({"delta": token})}

                elif "tools" in chunk:
                    tool_msgs = chunk["tools"]["messages"]
                    for tm in tool_msgs:
                        yield {
                            "event": "tool_finished",
                            "data": json.dumps({"output": tm.content}),
                        }

            yield {"event": "done", "data": json.dumps({"status": "completed"})}

        except Exception as e:
            yield {"event": "error", "data": json.dumps({"error": str(e)})}

    return EventSourceResponse(event_generator())


@app.post("/chat/resume")
async def chat_resume(
    req: ResumeRequest,
    bearer_token: str = Depends(extract_bearer_token),
) -> EventSourceResponse:
    """Resumes an interrupted workflow with human decision (approve or reject)."""
    verify_tenant_auth(bearer_token)

    decision = req.decision.strip().lower()
    if decision not in {"approve", "reject"}:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Decision must be either 'approve' or 'reject'",
        )

    config = {"configurable": {"thread_id": req.thread_id}}

    async def resume_generator() -> AsyncIterator[Dict[str, str]]:
        try:
            # Resume graph using Command(resume=decision)
            for chunk in orchestrator_graph.stream(Command(resume=decision), config=config):
                if "__interrupt__" in chunk:
                    interrupt_val = chunk["__interrupt__"][0].value
                    yield {"event": "proposal_required", "data": json.dumps(interrupt_val)}
                    return

                if "agent" in chunk:
                    agent_msg = chunk["agent"]["messages"][-1]
                    if agent_msg.content:
                        words = agent_msg.content.split(" ")
                        for i, word in enumerate(words):
                            token = word if i == 0 else f" {word}"
                            yield {"event": "token", "data": json.dumps({"delta": token})}

                elif "tools" in chunk:
                    for tm in chunk["tools"]["messages"]:
                        yield {"event": "tool_finished", "data": json.dumps({"output": tm.content})}

            yield {"event": "done", "data": json.dumps({"status": "completed"})}

        except Exception as e:
            yield {"event": "error", "data": json.dumps({"error": str(e)})}

    return EventSourceResponse(resume_generator())


"""Mandatory Human-in-the-Loop Proposal/Approve/Reject Tests (R5).

Verifies:
- "Refund order 1042" or "cancel order 1042" does not execute immediately.
- It produces a structured proposal (order, merchant, delta, amount).
- Approval executes the mutation.
- Rejection does not execute the mutation.
"""

import httpx
import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import HumanMessage
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command
import sqlite3

from src.store_api.app import app as store_app
from src.sub_agent.app import app as sub_agent_app, tools
from src.orchestrator.client_rpc import SubAgentClient
from src.orchestrator.graph import build_orchestrator_graph


@pytest.fixture
def test_graph():
    """Wires in-memory test clients across store, sub-agent, and orchestrator."""
    # Reset store DB
    store_client = TestClient(store_app)
    store_client.post("/test/reset")

    # Wire sub_agent to store_app
    tools._client = store_client

    # Wire orchestrator to sub_agent_app
    sub_client = TestClient(sub_agent_app)
    rpc_client = SubAgentClient(base_url="http://testserver", client=sub_client)

    # In-memory checkpointer
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    checkpointer = SqliteSaver(conn)

    graph = build_orchestrator_graph(sub_agent_client=rpc_client, checkpointer=checkpointer)
    return graph, store_client


def test_hitl_refund_proposal_reject(test_graph):
    """Refund request generates proposal, and explicit rejection DOES NOT mutate order (R5)."""
    graph, store_client = test_graph
    thread_id = "thread-hitl-reject"
    config = {"configurable": {"thread_id": thread_id}}

    # 1. Ask to refund order-1002 (PAID, $84.00)
    input_state = {
        "messages": [HumanMessage(content="Refund order 1002")],
        "bearer_token": "token-merchant-alpha",
        "merchant": "merchant_alpha",
        "pending_proposal": None,
    }

    chunks = list(graph.stream(input_state, config=config))

    # Must produce an interrupt chunk
    interrupt_chunks = [c for c in chunks if "__interrupt__" in c]
    assert len(interrupt_chunks) > 0

    interrupt_payload = interrupt_chunks[0]["__interrupt__"][0].value
    assert interrupt_payload["type"] == "proposal_required"
    proposal = interrupt_payload["proposal"]

    # Verify structured proposal fields
    assert proposal["order_id"] == "order-1002"
    assert proposal["amount"] == 84.00
    assert proposal["current_status"] == "PAID"
    assert proposal["target_status"] == "REFUNDED"
    assert "delta_summary" in proposal

    # Verify order in store API is STILL in PAID status (not yet mutated)
    lookup_resp = store_client.get(
        "/orders/lookup?id=order-1002",
        headers={"Authorization": "Bearer token-merchant-alpha"},
    )
    assert lookup_resp.json()["order"]["status"] == "PAID"

    # 2. Reject the proposal
    resume_chunks = list(graph.stream(Command(resume="reject"), config=config))
    assert len(resume_chunks) > 0

    # Verify order in store API is STILL in PAID status
    lookup_resp2 = store_client.get(
        "/orders/lookup?id=order-1002",
        headers={"Authorization": "Bearer token-merchant-alpha"},
    )
    assert lookup_resp2.json()["order"]["status"] == "PAID"


def test_hitl_refund_proposal_approve(test_graph):
    """Refund request generates proposal, and explicit approval executes mutation (R5)."""
    graph, store_client = test_graph
    thread_id = "thread-hitl-approve"
    config = {"configurable": {"thread_id": thread_id}}

    # 1. Propose refund
    input_state = {
        "messages": [HumanMessage(content="Refund order 1002")],
        "bearer_token": "token-merchant-alpha",
        "merchant": "merchant_alpha",
        "pending_proposal": None,
    }

    chunks = list(graph.stream(input_state, config=config))
    assert any("__interrupt__" in c for c in chunks)

    # 2. Explicitly approve
    resume_chunks = list(graph.stream(Command(resume="approve"), config=config))
    assert len(resume_chunks) > 0

    # Verify order in store API is now REFUNDED!
    lookup_resp = store_client.get(
        "/orders/lookup?id=order-1002",
        headers={"Authorization": "Bearer token-merchant-alpha"},
    )
    assert lookup_resp.json()["order"]["status"] == "REFUNDED"


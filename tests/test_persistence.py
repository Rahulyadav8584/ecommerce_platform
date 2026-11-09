"""State Persistence Across Process Restart Tests (R7).

Verifies:
- Conversation state survives process shutdown / restart using SQLite checkpointer.
- Demonstrates: start conversation -> simulate process restart -> continue conversation.
"""

import os
import sqlite3
import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import HumanMessage
from langgraph.checkpoint.sqlite import SqliteSaver

from src.store_api.app import app as store_app
from src.sub_agent.app import app as sub_agent_app, tools
from src.orchestrator.client_rpc import SubAgentClient
from src.orchestrator.graph import build_orchestrator_graph


def test_conversation_state_survives_restart(tmp_path):
    """Proves conversation history and checkpoints persist across process restarts (R7)."""
    db_file = str(tmp_path / "persistence_test.sqlite")
    thread_id = "persistent-thread-101"
    config = {"configurable": {"thread_id": thread_id}}

    # Setup mock connections
    store_client = TestClient(store_app)
    store_client.post("/test/reset")
    tools._client = store_client

    sub_client = TestClient(sub_agent_app)
    rpc_client = SubAgentClient(base_url="http://testserver", client=sub_client)

    # --- PROCESS 1: Start Conversation ---
    conn1 = sqlite3.connect(db_file, check_same_thread=False)
    saver1 = SqliteSaver(conn1)
    graph1 = build_orchestrator_graph(sub_agent_client=rpc_client, checkpointer=saver1)

    input_1 = {
        "messages": [HumanMessage(content="Which paid orders have not shipped in 3 days?")],
        "bearer_token": "token-merchant-alpha",
        "merchant": "merchant_alpha",
        "pending_proposal": None,
    }
    list(graph1.stream(input_1, config=config))

    # Verify state in Process 1
    state1 = graph1.get_state(config)
    messages_count_process1 = len(state1.values["messages"])
    assert messages_count_process1 >= 2  # HumanMessage + AIMessage(s)

    # --- SIMULATE PROCESS RESTART ---
    # Close connection, delete graph1 and saver1 instances completely
    conn1.close()
    del graph1
    del saver1
    del conn1

    # --- PROCESS 2: Re-open from database and continue conversation ---
    conn2 = sqlite3.connect(db_file, check_same_thread=False)
    saver2 = SqliteSaver(conn2)
    graph2 = build_orchestrator_graph(sub_agent_client=rpc_client, checkpointer=saver2)

    # Inspect restored state from the checkpointer
    state2 = graph2.get_state(config)
    assert len(state2.values["messages"]) == messages_count_process1

    # Continue the conversation on the same thread
    input_2 = {
        "messages": [HumanMessage(content="what did customer Maria order last?")],
        "bearer_token": "token-merchant-alpha",
        "merchant": "merchant_alpha",
        "pending_proposal": None,
    }
    list(graph2.stream(input_2, config=config))

    state3 = graph2.get_state(config)
    # The history must contain messages from BOTH process sessions
    assert len(state3.values["messages"]) > messages_count_process1
    conn2.close()


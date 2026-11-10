"""Demonstration client for ShopHub Orchestrator Streaming & HITL (R6).

Demonstrates:
- Consuming Server-Sent Events (SSE) in real-time
- Receiving tool_started and tool_finished events
- Receiving streaming tokens
- Handling Human-in-the-Loop proposal_required interrupts
- Approving or rejecting destructive proposals via /chat/resume
"""

import json
import sys
import httpx

ORCHESTRATOR_URL = "http://localhost:8000"
TOKEN = "token-merchant-alpha"


def stream_chat(message: str, thread_id: str):
    """Sends a chat message and prints live SSE events as they arrive."""
    print(f"\n💬 [USER]: {message} (Thread: {thread_id})")
    url = f"{ORCHESTRATOR_URL}/chat/stream"
    headers = {
        "Authorization": f"Bearer {TOKEN}",
        "Content-Type": "application/json",
    }
    payload = {"message": message, "thread_id": thread_id}

    proposal_data = None

    with httpx.Client(timeout=30.0) as client:
        with client.stream("POST", url, headers=headers, json=payload) as response:
            if response.status_code != 200:
                print(f"❌ Error: HTTP {response.status_code} - {response.read().decode('utf-8')}")
                return None

            event_type = None
            for line in response.iter_lines():
                if not line:
                    continue
                if line.startswith("event:"):
                    event_type = line.split(":", 1)[1].strip()
                elif line.startswith("data:"):
                    data_str = line.split(":", 1)[1].strip()
                    try:
                        data = json.loads(data_str)
                    except json.JSONDecodeError:
                        data = data_str

                    if event_type == "tool_started":
                        print(f"   ⚙️  [TOOL STARTED]: {data.get('tool')} with args: {data.get('args')}")
                    elif event_type == "tool_finished":
                        print(f"   ✅ [TOOL FINISHED]: {str(data)[:100]}...")
                    elif event_type == "token":
                        sys.stdout.write(data.get("delta", ""))
                        sys.stdout.flush()
                    elif event_type == "proposal_required":
                        print(f"\n   ⚠️  [ACTION REQUIRED - HITL PROPOSAL]:")
                        proposal_data = data.get("proposal", data)
                        print(f"       {json.dumps(proposal_data, indent=7)}")
                    elif event_type == "error":
                        print(f"\n❌ [ERROR]: {data}")

    print()  # newline
    return proposal_data


def resume_proposal(thread_id: str, decision: str):
    """Resumes an interrupted proposal with approve or reject."""
    print(f"\n👉 [DECISION]: Sending '{decision}' for Thread {thread_id}...")
    url = f"{ORCHESTRATOR_URL}/chat/resume"
    headers = {
        "Authorization": f"Bearer {TOKEN}",
        "Content-Type": "application/json",
    }
    payload = {"thread_id": thread_id, "decision": decision}

    with httpx.Client(timeout=30.0) as client:
        with client.stream("POST", url, headers=headers, json=payload) as response:
            event_type = None
            for line in response.iter_lines():
                if not line:
                    continue
                if line.startswith("event:"):
                    event_type = line.split(":", 1)[1].strip()
                elif line.startswith("data:"):
                    data_str = line.split(":", 1)[1].strip()
                    try:
                        data = json.loads(data_str)
                    except json.JSONDecodeError:
                        data = data_str

                    if event_type == "token":
                        sys.stdout.write(data.get("delta", ""))
                        sys.stdout.flush()
                    elif event_type == "tool_finished":
                        print(f"\n   ✅ [EXECUTION COMPLETED]: {str(data)[:100]}...")

    print()


def main():
    print("=" * 75)
    print("  ShopHub Support Assistant: Live Merchant Chat & HITL Demonstration")
    print("=" * 75)

    # Reset store API to clean seed state before demo
    try:
        httpx.post("http://localhost:8001/test/reset", timeout=5.0)
    except Exception:
        pass

    # Scenario 1: Customer Order History Inquiry
    print("\n" + "=" * 50)
    print("  Scenario 1: Customer Order History Inquiry")
    print("=" * 50)
    stream_chat("What did customer Maria order last?", thread_id="demo-session-maria")

    # Scenario 2: Unshipped Paid Orders Inquiry
    print("\n" + "=" * 50)
    print("  Scenario 2: Unshipped Paid Orders Inquiry")
    print("=" * 50)
    stream_chat("Which paid orders have not shipped in 3 days?", thread_id="demo-session-unshipped")

    # Scenario 3: Destructive Action with Human-in-the-Loop Approval (Refund)
    print("\n" + "=" * 50)
    print("  Scenario 3: Refund Proposal with Human Approval (HITL)")
    print("=" * 50)
    thread_refund = "demo-session-refund"
    proposal_refund = stream_chat("Please refund order 1002", thread_id=thread_refund)
    if proposal_refund:
        resume_proposal(thread_refund, decision="approve")

    # Scenario 4: Destructive Action with Human-in-the-Loop Rejection (Cancel)
    print("\n" + "=" * 50)
    print("  Scenario 4: Cancellation Proposal with Human Rejection (HITL)")
    print("=" * 50)
    thread_cancel = "demo-session-cancel"
    proposal_cancel = stream_chat("Please cancel order 1004", thread_id=thread_cancel)
    if proposal_cancel:
        resume_proposal(thread_cancel, decision="reject")

    print("\n" + "=" * 75)
    print("  All demonstration scenarios completed successfully!")
    print("=" * 75)


if __name__ == "__main__":
    main()


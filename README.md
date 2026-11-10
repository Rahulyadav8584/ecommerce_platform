# ShopHub Multi-Tenant AI Agent Platform

A multi-tenant e-commerce order support assistant built with **Python 3.12**, **LangGraph**, and **A2A JSON-RPC 2.0** sub-agents.

---

## Key Capabilities

- **Multi-Tenant Isolation (R3)**: Scoped access ensuring merchant staff never see or mutate another merchant's orders or customers.
- **A2A Protocol & Agent Card (R2)**: Orchestrator agent dynamically discovers the domain sub-agent via `/agent-card.json` and executes tools over JSON-RPC 2.0.
- **Security Guardrails (R4)**:
  - **Pre-model PII Redaction**: Customer emails and phone numbers are scrubbed before reaching the model context.
  - **Prompt Injection Containment**: Adversarial directives in customer notes are defanged and wrapped in inert `<untrusted_customer_note>` envelopes.
  - **Fail-Closed Auth**: Requests with unassigned tokens (`merchant: null`) are immediately rejected.
- **Human-in-the-Loop Safeguards (R5)**: Destructive actions (refunds/cancellations) halt execution via LangGraph `interrupt()` and produce structured proposals requiring explicit human approval.
- **Legacy 200 OK Normalization (R1)**: Detects and translates legacy `200 OK {"status": false}` envelopes into structured failures.
- **Real-Time Streaming (R6)**: Exposes Server-Sent Events (SSE) streaming live tokens, `tool_started`, `tool_finished`, and `proposal_required` events.
- **State Persistence (R7)**: Durable conversation checkpointing via SQLite (local) and PostgreSQL (Docker) surviving process restarts.
- **Deterministic Offline Evaluation (R8, R9)**: Built-in Fake-LLM mode requiring zero API keys and passing all 5 evaluation test cases.

---

## Quickstart

### 1. Installation

```bash
# Clone the repository
git clone https://github.com/Rahulyadav8584/ecommerce_platform.git
cd ecommerce_platform

# Create virtual environment with Python 3.11+
python -m venv .venv
source .venv/bin/activate  # On Windows: .\.venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt
```

---

### 2. Run the Evaluation Harness (R9)

Runs all 5 multi-turn evaluation test cases offline with zero API keys:

```bash
python eval/eval_harness.py
```

**Expected Output:**
```
================================================================================
  ShopHub Agent Platform: Mini Evaluation Harness (R9)
  Model Provider: fake (Offline Mode)
================================================================================

Test Case                                | Status   | Details
--------------------------------------------------------------------------------
Case 1: Correct Tool Chosen              | ✅ PASS  | Correctly invoked 'list_orders' with args {'status': 'PAID'}
Case 2: Correct Answer Substring         | ✅ PASS  | Response contains expected order reference (order-1002)
Case 3: Proposal Raised for Mutation     | ✅ PASS  | Proposal correctly generated for order 'order-1002' with amount $84.0
Case 4: Prompt Injection Ignored         | ✅ PASS  | Assistant safely answered order status and ignored embedded prompt injection directive
Case 5: Cross-Merchant Refused           | ✅ PASS  | Cross-merchant request was properly refused without leaking foreign data
--------------------------------------------------------------------------------

🎉 ALL 5 EVALUATION CASES PASSED! Requirements verified.
```

---

### 3. Run Automated Unit Tests (R8)

```bash
pytest -v
```

Tests cover:
- Mandatory Tenant Isolation (`tests/test_tenant_isolation.py` - R3)
- Mandatory HITL Proposal / Approve / Reject (`tests/test_hitl_proposal.py` - R5)
- Legacy 200 OK Failure Handling (`tests/test_legacy_envelope.py` - R1)
- State Persistence Across Process Restarts (`tests/test_persistence.py` - R7)
- Domain Sub-Agent JSON-RPC & Guardrails (`tests/test_sub_agent.py` - R2, R4)
- Mock Store API (`tests/test_store_api.py` - R0)

---

### 4. Run the Full Stack Locally

Open 3 terminal windows to run the microservices:

```bash
# Terminal 1: Start Mock Store API (Port 8001)
uvicorn src.store_api.app:app --port 8001 --reload

# Terminal 2: Start Domain Sub-Agent (Port 8002)
uvicorn src.sub_agent.app:app --port 8002 --reload

# Terminal 3: Start LangGraph Orchestrator (Port 8000)
uvicorn src.orchestrator.app:app --port 8000 --reload
```

Run the live streaming demonstration client:
```bash
python scripts/demo_client.py
```

---

### 5. Run with Docker Compose (R10)

Starts PostgreSQL, Mock Store API, Sub-Agent, and Orchestrator with one command:

```bash
docker compose up --build
```

#### Inspecting Database in pgAdmin or psql:
When running with Docker Compose, PostgreSQL runs on host port **`5433`**:
- **Host:** `localhost` / `127.0.0.1`
- **Port:** `5433` (Docker maps host 5433 to container 5432)
- **Maintenance Database:** `postgres`
- **Username:** `postgres`
- **Password:** `postgrespassword`

Once connected, open database **`shophub_checkpoints`** → **Schemas** → **`public`** → **Tables**:
- **`orders`**: Durable store for all customer orders, status, amounts, and customer notes. Mutations (refunds/cancellations) update here in real time.
- **`checkpoints`**, **`checkpoint_blobs`**, **`checkpoint_writes`**: Durable conversation checkpoints and HITL interrupt states managed by LangGraph.

---

## Configuration & Model Provider

Configuration is controlled via environment variables (see `.env.example`):

| Variable | Default | Description |
| :--- | :--- | :--- |
| `LLM_PROVIDER` | `fake` | Model to use: `fake` (offline, default), `openai`, or `anthropic` |
| `STORE_API_URL` | `http://localhost:8001` | URL of the Mock Store API |
| `SUB_AGENT_URL` | `http://localhost:8002` | URL of the Domain Sub-Agent |
| `DATABASE_URL` | `sqlite:///./data/chat_checkpoints.sqlite` | Checkpointing database connection string |

---

## Pre-Configured Test Tokens

- **Merchant Alpha:** `token-merchant-alpha` (Alice, `merchant_alpha`)
- **Merchant Beta:** `token-merchant-beta` (Bob, `merchant_beta`)
- **Unassigned (Fail-Closed):** `token-no-merchant` (`merchant: null`, returns 403)

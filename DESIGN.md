# ShopHub Multi-Tenant AI Agent Platform — Architecture & Design Document

**Author:** Senior AI Agent Engineer Candidate  
**Target:** ShopHub E-Commerce Order Support System  
**Specification:** Technical Requirements (R0–R11)

---

## 1. System Architecture

The platform implements a decoupled, multi-tier agent architecture where independent services communicate strictly over HTTP/REST and JSON-RPC 2.0 (A2A standard).

```
[ Merchant Support Staff / Client ]
                 │
                 │ HTTP POST /chat/stream (SSE) + Bearer Token
                 ▼
┌────────────────────────────────────────────────────────────────────────┐
│  Orchestrator Agent Service (Port 8000)                                │
│  - FastAPI entrypoint + Server-Sent Events (SSE) streaming             │
│  - Fail-closed auth gate: rejects null-merchant tokens (403)           │
│  - Model Factory (Scripted Fake-LLM for offline / Swappable real LLMs) │
│  - LangGraph State Machine with checkpointing (PostgreSQL / SQLite)    │
│  - Human-in-the-Loop (HITL) interrupt() for destructive proposals      │
└──────────────────────────────────┬─────────────────────────────────────┘
                                   │
                                   │ A2A JSON-RPC 2.0 over HTTP
                                   │ (Forwards Caller's Bearer Token)
                                   ▼
┌────────────────────────────────────────────────────────────────────────┐
│  Domain Sub-Agent: "Orders Agent" (Port 8002)                          │
│  - Dynamic Discovery: Serves /agent-card.json                          │
│  - JSON-RPC 2.0 Dispatcher: lookup_order, list_orders, etc.            │
│  - Security Guardrails (R4):                                           │
│    * PII Redaction: Scrubs email and phone before returning data       │
│    * Prompt Injection Containment: Defangs & wraps customer notes      │
│  - Legacy Normalization: Translates 200 OK {"status": false} to error  │
└──────────────────────────────────┬─────────────────────────────────────┘
                                   │
                                   │ HTTP REST (Forwards Caller's Bearer Token)
                                   ▼
┌────────────────────────────────────────────────────────────────────────┐
│  Mock Store API Service (Port 8001)                                    │
│  - In-memory data store with seeded merchants & orders (R0)            │
│  - Scoped endpoints: /auth/me, /orders, /orders/refund, /orders/cancel │
│  - Legacy endpoint: /orders/lookup?id= (returns 200 with status: false)│
└────────────────────────────────────────────────────────────────────────┘
```

---

## 2. The Flow of One Turn

### Scenario: Support staff initiates a refund for Order #1002

1. **Client Request:**  
   Support agent Alice sends `POST /chat/stream` with header `Authorization: Bearer token-merchant-alpha` and payload `{"message": "Refund order 1002", "thread_id": "session-101"}`.
2. **Fail-Closed Verification:**  
   The Orchestrator queries `GET /auth/me` on the Store API using the token. It verifies `merchant == "merchant_alpha"`. If `merchant` were null, it would immediately reject with `403 Forbidden`.
3. **Orchestrator Invocation (LangGraph):**  
   The model processes the request and decides to call the `propose_refund` tool with argument `{"id": "order-1002"}`. The SSE stream emits `event: tool_started`.
4. **Sub-Agent Execution (JSON-RPC 2.0):**  
   The Orchestrator dispatches a JSON-RPC 2.0 call to `http://localhost:8002/rpc`, forwarding Alice's bearer token.
5. **Store API Query & Legacy Envelope Normalization:**  
   The Sub-Agent queries `GET /orders/lookup?id=order-1002`. The Store API verifies ownership. The Sub-Agent inspects the body for the legacy `{"status": false}` envelope; finding `status: true`, it validates eligibility (`PAID`, `SHIPPED`, or `DELIVERED`).
6. **Guardrail Sanitization (PII & Prompt Injection):**  
   Customer email and phone are replaced with `[EMAIL_REDACTED]` and `[PHONE_REDACTED]`. The customer note is wrapped inside `<untrusted_customer_note>` tags.
7. **Human-in-the-Loop Interrupt:**  
   The Sub-Agent returns a structured proposal payload. LangGraph reaches the `interrupt()` node, halts execution, and saves state to the persistent checkpointer. The SSE stream emits `event: proposal_required`.
8. **Client Approval & Resumption:**  
   The frontend displays the proposal card. Alice reviews the $84.00 refund and clicks "Approve". The client calls `POST /chat/resume` with `{"thread_id": "session-101", "decision": "approve"}`.
9. **Mutation & Confirmation:**  
   LangGraph resumes via `Command(resume="approve")`. The Sub-Agent invokes `POST /orders/refund` on the Store API, changing status to `REFUNDED`. The stream finishes with `event: token` and `event: done`.

---

## 3. Key Trade-Offs

| Decision | Chosen Approach | Alternative Considered | Rationale |
| :--- | :--- | :--- | :--- |
| **A2A Communication** | Standard JSON-RPC 2.0 over HTTP | Custom REST endpoints or gRPC | JSON-RPC 2.0 provides standard request/response envelopes with well-defined error codes (`-32600` to `-32603`), easily exposed in OpenAPI. |
| **Persistence Engine** | SQLite (Default) + PostgreSQL (Docker) | Redis / DynamoDB / In-Memory | SQLite requires zero infrastructure setup for automated testing and offline grading, while PostgreSQL in Docker ensures production ACID durability across multi-replica nodes. |
| **HITL Flow** | Native LangGraph `interrupt()` + `Command(resume=...)` | Custom state flags / polling tables | LangGraph interrupts are built into the state graph runtime, halting the thread atomically and restoring graph execution cleanly upon resumption. |
| **Prompt Injection Defense** | Delimited structural containment (`<untrusted_customer_note>`) | LLM pre-filtering / classifier models | LLM classifiers add 200–500ms latency and can be fooled themselves. Structural containment + prompt instruction boundaries treat external data as untrusted text without added latency. |

---

## 4. What Was Cut and Why

1. **Vector Store / RAG Search:**  
   *Cut Reason:* Order lookups and status filtering are deterministic relational queries (`id`, `status`, `customer_name`), not semantic similarity queries. Adding a vector database would introduce indexing latency, eventual consistency lags, and hallucination risks for exact order IDs.
2. **Full Frontend UI:**  
   *Cut Reason:* Per task guidelines (*"Do not spend time on a web UI"*), we prioritized a fully featured Python demonstration client (`scripts/demo_client.py`) and standard SSE streaming endpoints that plug into any web UI.
3. **Complex User Registration & OAuth2 Flows:**  
   *Cut Reason:* Scoped strictly to bearer-token forwarding (`R3`). Mock bearer tokens simulate real JWT claims without OAuth infrastructure overhead.

---

## 5. Cost and Latency Estimates (Per Turn)

### Deterministic Offline Mode (Scripted Fake-LLM)
* **Latency:** ~15–35 ms total turn latency (pure CPU execution + in-memory state).
* **Cost:** $0.00 (Zero tokens consumed, no third-party API dependencies).

### Production LLM Mode (e.g., OpenAI `gpt-4o-mini`)
* **Input Tokens:** ~650 tokens (System prompt + conversational history + tool schema + order data).
* **Output Tokens:** ~120 tokens (Tool call arguments + customer response).
* **Latency Breakdown:**
  * Model inference: ~400–700 ms (TTFT ~250 ms with SSE token streaming).
  * Store API + Sub-Agent JSON-RPC: ~15–25 ms.
  * State Checkpointing (Postgres / SQLite): ~5–10 ms.
  * **Total Latency:** ~450–750 ms per turn.
* **Cost:**
  * Input: 650 tokens × \$0.15 / 1M = \$0.0000975
  * Output: 120 tokens × \$0.60 / 1M = \$0.0000720
  * **Total Cost per turn:** ~\$0.00017 (Less than 2 cents per 100 customer interactions).

---

## 6. What I Would Build With Another Week

1. **Correlation IDs & Distributed Tracing (Stretch S3):**  
   Implement a `X-Correlation-ID` header generated at the chat API and carried through JSON-RPC down to Store API logs using OpenTelemetry spans.
2. **Model Routing (Stretch S4):**  
   Route simple queries (e.g. "What is my order status?") through a fast/cheap model (like `gpt-4o-mini` or `gemini-1.5-flash`), while routing multi-turn conflict resolution through a stronger reasoning model.
3. **Automated Audit Logging & Replay Harness:**  
   Store cryptographic audit trails of all approvals/rejections in a tamper-evident event log with replay capability for merchant compliance auditing.
4. **WebSocket Bidirectional Transport:**  
   Add WebSocket support alongside SSE for full-duplex client audio/voice interactions.


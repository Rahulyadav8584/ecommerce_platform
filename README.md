# ShopHub Multi-Tenant Order Support Assistant

A multi-tenant AI agent platform built with **Python 3.12**, **LangGraph**, and **A2A JSON-RPC 2.0** sub-agents for e-commerce order support.

## Key Capabilities
- **Multi-Tenant Isolation (R3)**: Scoped access ensuring merchant staff never inspect foreign store orders.
- **A2A Architecture (R2)**: Orchestrator agent delegates order queries to an autonomous domain sub-agent over JSON-RPC 2.0 via dynamic Agent Card discovery.
- **Adversarial & PII Guardrails (R4)**: Automatic redaction of customer email/phone and structural containment of prompt injections embedded in customer notes.
- **Human-In-The-Loop (R5)**: State-interrupt proposal and approval workflow for destructive actions (refunds and cancellations).
- **Resilient Legacy Handling (R1)**: Graceful normalization of `200 OK {"status": false}` API payloads.
- **Deterministic Offline Testing (R8, R9)**: Fully functional Fake-LLM mode requiring zero third-party API keys.
- **State Persistence (R7)**: Durable conversation checkpointing across process restarts.

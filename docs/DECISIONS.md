# Architecture Decision Record 001

Decision: deterministic evidence pipeline first, optional single LLM reviewer second.

Reasons:
- SQL/Python handle joins, exact OEM matching and price statistics reproducibly.
- LLM adds language interpretation and prioritization, not numeric truth.
- One model with two bounded read-only tools is sufficient for learning tool calling.
- Multi-agent orchestration would introduce cost, latency, non-determinism and debugging overhead prematurely.
- Scheduling is deliberately opt-in to avoid surprise automated calls.

Status: initial lab, not a production app. Next ADR should discuss evaluation gates before real integrations.

# ADR-0007: Python-first; per-service Go rewrite later if needed

## Status
Accepted

## Context
Chat and Live Runtime are realistically future Go services: high-concurrency long-lived connections, low CPU per message, GIL-sensitive workloads. Notification is IO-bound fan-out to providers — Python is fine forever.

The team's primary language is Python. Starting V1 with three new services in a new language would multiply risk: new language toolchain + new infra + new operational model + new bug classes, all at once.

## Decision
- V1 implements all three gRPC services in **Python (grpcio + asyncio)**.
- Proto contracts (per ADR-0006) make per-service Go rewrites possible without touching callers.
- **Rewrite trigger is per-service**, evaluated against real production data:
  - CPU saturation under realistic load
  - GIL-bound throughput ceiling reached
  - Latency p99 / tail unacceptable
  - Operational cost (memory per connection × scale) makes Python uneconomical
- Notification is unlikely to ever need rewriting; its workload is IO-bound. Plan: stays Python.

## Anti-decision
We do NOT:
- **Start V1 in Go "because we'll need it eventually"**: trades real present cost for hypothetical future cost; cost-of-delay always underestimated.
- **Mix Python and Go in V1 to "learn"**: split focus during foundational work; small team can't afford it.
- **Commit to rewrites by date**: rewrite when production says we must, not by calendar.
- **Use Go's gRPC tooling as the proto generator authority**: tooling stays language-neutral (`protoc` + `buf`).

## Consequences

**Good**
- V1 is shippable by the existing team
- Proto interface is the insurance: rewrites don't affect callers
- Notification likely never needs the rewrite (saves a future project)

**Bad**
- Chat and Live Runtime may need rewriting in 6–12 months under real load
- Going from Python to Go in production-critical services is a 4–8 week project per service when triggered

**Neutral**
- Hiring may eventually need Go skills; not a Day-1 requirement

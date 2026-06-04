# Architecture Decision Records (ADR)

Each ADR captures one significant decision: context, alternatives, decision, consequences. **One decision per ADR. No "miscellaneous" file.**

## Index

| # | Title | Status |
|---|---|---|
| 0001 | Single brand, deferred tenancy | Accepted |
| 0002 | Modular Monolith with service-only cross-app calls | Accepted |
| 0003 | Transactional Outbox with independent Dispatcher | Accepted |
| 0004 | Wallet ledger invariants | Accepted |
| 0005 | JWT (RS256) with explicit UserSession tracking | Accepted |
| 0006 | gRPC service boundary rules | Accepted |
| 0007 | Python-first; per-service Go rewrite later if needed | Accepted |
| 0008 | Bare-metal + systemd deployment (no containers in production) | Accepted |
| 0009 | Migration strategy: parallel read-only + scheduled cutover | Accepted |
| 0010 | Observability backend: Grafana stack (self-hosted) | Accepted |

## Lifecycle

- **Proposed**: PR open, under review
- **Accepted**: merged; implementation may proceed
- **Superseded by NNNN**: replaced by a newer ADR (link to it)
- **Deprecated**: no longer applies; do not implement

## When to write an ADR

A new ADR is required when the change:
- Affects multiple modules' contracts
- Changes a security / compliance posture
- Reverses an earlier decision
- Locks in a non-trivial tool choice for ≥ 6 months
- Has alternatives someone reasonable might still choose

Trivial implementation choices (library version, log format details) **don't** need ADRs.

## Format

Every ADR has the same five sections:
1. **Status**
2. **Context**
3. **Decision**
4. **Consequences** (good + bad + neutral)
5. **Anti-decision** (what we explicitly did NOT pick and why)

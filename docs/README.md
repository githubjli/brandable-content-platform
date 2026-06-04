# brandable-content-platform — Documentation

Authoritative documentation for the new platform. **Read this before writing code.**

## Where things live

```
docs/
├── README.md                  ← you are here
├── ANTIPATTERNS.md             What we have explicitly decided NOT to do
├── CODEOWNERS                  Who reviews what
├── getting-started.md          Week-1 bootstrap checklist
├── domain-glossary.md          One word, one definition
│
├── contracts/                  API contracts (19 files)
│   - The source of truth for every endpoint, gRPC service,
│     event type, and audit action.
│
├── adr/                        Architecture Decision Records (10 ADRs)
│   - Why we did things this way.
│
├── architecture/               System architecture (4 documents)
│   - modules.md, deployment.md, observability.md, grpc-integration.md
│
├── ops/                        Operational docs (5 docs + 5 runbooks)
│   - environments, secrets, feature-flags, testing-strategy, auth-propagation
│   - runbooks/ for incident response
│
├── migration/                  Migration plan (2 documents)
│   - migration-plan.md: how legacy → new
│   - feature-inventory.md: per-feature decisions
│
└── legacy/                     Snapshot of legacy backend reference
    └── mobile-api-contract-full.md (snapshot of django-auth-core/MOBILE_API_CONTRACT_FULL.md)
```

## Reading order for new engineers

1. `ANTIPATTERNS.md` — what NOT to do (10 min)
2. `getting-started.md` — week 1 bootstrap (5 min)
3. `architecture/modules.md` — the module map (10 min)
4. `adr/0002-modular-monolith.md` — how cross-app calls work (5 min)
5. `contracts/conventions.md` — cross-cutting standards (15 min)
6. `contracts/<your-domain>.md` — your area (30 min)

Total: ~75 minutes to be productive.

## Reading order for architectural decisions

1. All ADRs in order (0001 → 0010) — ~30 minutes
2. `contracts/conventions.md`
3. `architecture/grpc-integration.md`
4. `architecture/observability.md`

## Reading order for migration / cutover

1. `migration/migration-plan.md`
2. `migration/feature-inventory.md`
3. `contracts/diff-from-legacy.md`
4. `adr/0009-migration-strategy.md`
5. `legacy/mobile-api-contract-full.md` (reference)

## Documentation discipline

- **Source of truth ordering**: code > contracts/ > adr/ > everything else
- A contract change that affects API requires a PR with: contract update + implementation + test + diff-from-legacy update (if it impacts mobile cutover)
- An architectural change requires a new ADR
- Don't write a runbook for an incident pattern you haven't actually seen
- Don't proliferate documents; consolidate when possible

## Maintenance

| Document type | Update trigger |
|---|---|
| Contracts (`contracts/*.md`) | Every endpoint / proto / schema change |
| ADRs | Decision change → new ADR superseding old (don't edit accepted ADRs) |
| Architecture | When the diagram becomes stale |
| Runbooks | After every incident that reveals a gap |
| Migration | Until cutover completes; then archived |
| Glossary | Whenever a new term is introduced or renamed |

## Status

| | Status |
|---|---|
| Contracts | 19/19 files complete |
| ADRs | 10/10 |
| Architecture | 4/4 |
| Ops + runbooks | 5 + 5 |
| Migration | 2/2 |
| Top-level | 5/5 |

Total: ~50 documents covering V1-V3 surface.

## Conventions for this directory

- Markdown only (`.md`)
- No emojis except as status indicators where meaning is established (🟢 V1, 🟡 V2, 🔵 V3, 🚫 dropped, ⚠️ breaking, ✅/❌ as applicable)
- No HTML in markdown unless absolutely necessary
- Code blocks tagged with language
- Tables for matrices; bullet lists for sequences
- Cross-references link by relative path (`../contracts/identity.md`)

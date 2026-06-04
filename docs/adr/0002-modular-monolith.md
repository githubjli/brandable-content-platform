# ADR-0002: Modular Monolith with service-only cross-app calls

## Status
Accepted

## Context
We considered three architectures:

1. **Strict DDD with 4 layers** (domain / application / infrastructure / interface): produces glue code that nobody maintains; conflicts with Django ORM's design.
2. **Microservices from V1**: distributed-system problems before product-market fit; small team can't operate 8+ services.
3. **Modular monolith with explicit boundaries**: leverages Django's strengths, defers distribution costs.

We're going with option 3, with strict rules to prevent the monolith from rotting into a god module (which is what `django-auth-core` became).

## Decision
A Django modular monolith governed by three rules:

1. Each `apps/<name>/` owns its `models.py`, `services.py`, `views.py`, `serializers.py`, `urls.py`, `tests/`.
2. **Cross-app `import models` is forbidden.** Cross-app calls go through `services.py`.
3. The three gRPC services (Notification, Chat, Live Runtime) live outside the monolith and are reached via gRPC clients in `libs/grpc_client/`.

Rule 2 is enforced by `import-linter` in CI.

## Layering inside an app

```
views / serializers   <-- HTTP boundary, zero business logic
       |
   services.py        <-- business orchestration; tx boundary; outbox emission; audit calls
       |
   models.py / ORM    <-- data + minimal state predicates (.can_start, .is_active)
```

## Anti-decision
We do NOT use:
- A separate `domain/` package per app (DDD-style entity layer over ORM) — duplicates ORM, no payoff
- Repository pattern wrapping `Model.objects` — Django's queryset is the repository
- "Application services" vs "domain services" distinction — over-abstraction
- Cross-app signals (`post_save` etc.) as a way to dodge the import rule — that's cross-app coupling without the contract

## Consequences

**Good**
- Refactors stay local to an app
- Cross-app contracts are explicit (a service function signature)
- The day an app needs to extract into a service, the call site is already abstracted
- New engineer can ship in one app without learning all others

**Bad**
- Some service functions are thin pass-throughs in V1
- import-linter rules require maintenance as new apps are added
- The single test suite gets long (mitigated by pytest markers + parallelism)

**Neutral**
- Deployments are still all-or-nothing (single Django process); gRPC services are exceptions

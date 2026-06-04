# ADR-0005: JWT (RS256) with explicit UserSession tracking

## Status
Accepted

## Context
Authentication must work across:
- Django monolith
- Three gRPC services (Notification, Chat, Live Runtime)
- Mobile and web clients

We need stateless verification (so gRPC services don't have to query the auth DB on every call) AND the ability to force-logout (security incident, password change, device theft).

Pure stateless JWT cannot revoke. Pure session cookies don't propagate cleanly across services. Need a hybrid.

## Decision

### Token signing
- **SimpleJWT with RS256** (asymmetric).
- **Identity service holds the private key.** Other services verify via JWKS (`/.well-known/jwks.json`).
- Access token TTL: 15 minutes. Refresh token TTL: 7 days, rotates on use.
- Standard claims: `sub` (user uuid), `iat`, `exp`, `jti`, `type` (`access` | `refresh` | `service`), `scope`, `aud`.

### Force-logout via UserSession
- Every login creates a `UserSession` row tracking the active refresh token's metadata (device, last seen, IP).
- Deleting a session invalidates its refresh token immediately.
- Access tokens remain valid until their natural `exp` (max 15 minutes). This is the explicit trade-off.

For higher-risk operations (password change, payment confirmation), re-check `UserSession` validity at the service boundary, not just JWT validity.

### Legacy password compatibility
Old `django-auth-core` users carry their existing password hash (Django default PBKDF2). New system keeps PBKDF2 first in `PASSWORD_HASHERS`. Django auto-rehashes on first successful login if the hash scheme has upgraded.

### Service accounts
Services calling other services use **service account JWTs** with `type=service`. **Never** reuse end-user tokens for service-to-service calls. Per service account, a dedicated scope grant limits what RPCs it can call.

### Post-cutover security note
Migrated user password hashes carry whatever weakness the legacy storage had. Cutover plan must include:
- Mandatory 2FA prompt on first login post-cutover (advisory in V1; enforceable in V2)
- Re-rotation of any peppers/salts present in the legacy hash function

## Anti-decision
We do NOT:
- **Use HS256 (shared secret)**: requires every service to hold the signing secret. Bad blast radius.
- **Skip refresh rotation**: refresh tokens that don't rotate are bearer tokens with long lives — security hazard.
- **Put PII in JWT claims**: only `user_id` (sub) and minimal authorization context. Identity service is the source of truth for profile data.
- **Use opaque tokens with central introspection**: every gRPC call would round-trip to Identity, defeating statelessness.

## Consequences

**Good**
- Services verify locally (low latency)
- Force-logout works within 15 minutes (refresh blocked immediately)
- Key rotation is mechanical (publish new key, switch signer, retire old)
- One mechanism serves users + services

**Bad**
- Up to 15 minutes of post-revocation access token validity. **Accepted.**
- Operating a JWKS endpoint with key rotation is non-trivial — documented in `ops/runbooks/jwt-key-rotation.md`
- One extra table (`UserSession`) per active user

**Neutral**
- Mobile must store both tokens securely; documented in mobile contract

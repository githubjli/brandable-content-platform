# Runbook: JWT Key Rotation

Per ADR-0005; mechanism described in `ops/auth-propagation.md`.

## When to rotate

- Scheduled: every 6 months
- Emergency: suspected key compromise (treat as breach until disproven)
- After team member departure with key access

## Scheduled rotation procedure

### Step 1 — Generate new keypair

```bash
mkdir -p /tmp/jwt-rotation
cd /tmp/jwt-rotation

openssl genpkey -algorithm RSA -out new-jwt-private.pem -pkeyopt rsa_keygen_bits:4096
openssl rsa -in new-jwt-private.pem -pubout -out new-jwt-public.pem

# Generate kid: short uuid + timestamp
NEW_KID="$(date +%Y%m%d)-$(uuidgen | cut -c1-8)"
echo "New kid: $NEW_KID"
```

### Step 2 — Add new key to Identity's signing keyset

Identity service exposes the keyset via `/.well-known/jwks.json`. Two keys: the current (still signing) and the new (verify-only initially).

Upload new key:
```bash
sudo -u bcp /opt/bcp/venv/bin/python /opt/bcp/django/manage.py jwt_keyset add \
  --kid="$NEW_KID" \
  --private-key-path=/tmp/jwt-rotation/new-jwt-private.pem \
  --public-key-path=/tmp/jwt-rotation/new-jwt-public.pem \
  --signing=false   # added to verify-only first
```

### Step 3 — Publish via JWKS

The new public key appears at `/.well-known/jwks.json` immediately. Both old and new keys listed.

### Step 4 — Wait for downstream services to refresh JWKS

All gRPC services cache JWKS for 10 minutes. Wait 15 minutes to be safe.

Verify:
```bash
for svc in notification chat live-runtime; do
  curl -s http://${svc}.bcp.local/internal/jwks-cache | jq '.keys | map(.kid)'
done
# Both old and new kid should appear
```

### Step 5 — Switch Identity to sign with new kid

```bash
sudo -u bcp /opt/bcp/venv/bin/python /opt/bcp/django/manage.py jwt_keyset set-signing \
  --kid="$NEW_KID"
sudo systemctl restart bcp-django
```

Existing tokens with old `kid` continue to verify because the old key is still in JWKS.

### Step 6 — Wait at least one refresh-token lifetime (7 days)

So all in-flight tokens have rotated to new `kid`.

### Step 7 — Remove the old key

```bash
sudo -u bcp /opt/bcp/venv/bin/python /opt/bcp/django/manage.py jwt_keyset remove \
  --kid="<old-kid>"
```

### Step 8 — Securely destroy the old private key

```bash
sudo shred -uvz /run/secrets/old-jwt-private.pem
# (and rotate the secret in Doppler / Secrets Manager)
```

### Step 9 — Update audit
```
record_audit(
  action="identity.jwt_key.rotate_scheduled",
  severity="critical",
  before_state={"old_kid": "..."},
  after_state={"new_kid": "..."},
  reason="Scheduled 6-month rotation",
)
```

---

## Emergency rotation procedure

Same steps, compressed to ~1 hour:

1. Generate new keypair (same as Step 1)
2. Add new key with `--signing=false` (Step 2-3)
3. **Force JWKS refresh on all services NOW**:
```bash
for svc in notification chat live-runtime; do
  systemctl restart bcp-${svc}
done
```
4. Switch Identity to sign with new kid (Step 5)
5. **Force-expire all sessions older than the breach window**:
```sql
DELETE FROM identity_usersession WHERE created_at < '<breach-start>';
```
Or via management command:
```bash
sudo -u bcp /opt/bcp/venv/bin/python /opt/bcp/django/manage.py revoke_sessions \
  --created-before='<breach-start>' \
  --reason="Emergency rotation following <incident-id>"
```
6. Remove old key from JWKS immediately (Step 7)
7. Force JWKS refresh again on all services (so old key is fully removed)
8. Notify affected users to re-login
9. Audit:
```
record_audit(
  action="identity.jwt_key.rotate_emergency",
  severity="critical",
  reason="Suspected compromise: <details>",
  ...
)
```

---

## Verification (always)

After any rotation:

```bash
# 1. Sign a test token, verify on each service
sudo -u bcp /opt/bcp/venv/bin/python /opt/bcp/django/manage.py jwt_keyset test-sign \
  --user-id=<test-user-id>

# 2. Check JWKS endpoint returns expected key set
curl -s https://identity.bcp.example.com/.well-known/jwks.json | jq '.keys | map(.kid)'

# 3. Monitor auth metrics for 30 minutes
# Grafana → identity dashboard → auth_failed_total
# A spike means some service didn't refresh JWKS — investigate
```

---

## Failure modes

| Failure | Mitigation |
|---|---|
| Forgot to publish new key before signing with it | Services reject all tokens. Roll back Identity to sign with old kid, debug, redo |
| Removed old key too soon | Existing tokens fail until refresh. Re-add old key to keyset, wait one access-TTL (15 min), retry removal |
| JWKS cache not respecting TTL | Force refresh, then debug `libs/jwt_auth/jwks_cache.py` |
| Service stuck on old key | `systemctl restart bcp-<service>` |

---

## Post-incident (emergency rotation)

Mandatory post-mortem covering:
- How was the key suspected compromised
- What data could have been accessed during the window
- Were users notified
- What additional revocations were needed (e.g., third-party API keys)
- Whether to require 2FA / password reset for affected users

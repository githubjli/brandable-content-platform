# Feature Flags

Flags exist to **decouple deploy from release** and to **shrink blast radius**. They are not a place to leave permanent dead code.

---

## 1. Tool

V1 default: **in-house simple `FeatureFlag` model** in `apps/platform_config/`. Sufficient for ~10 active flags.

Switch trigger (to PostHog or LaunchDarkly):
- More than 10 active flags simultaneously
- Need for analytics-driven targeting
- Per-user / per-cohort experiments at scale

### V1 model

```python
# apps/platform_config/models.py
class FeatureFlag(models.Model):
    key = models.CharField(max_length=128, unique=True)
    description = models.TextField(help_text="What it gates + removal plan")
    enabled = models.BooleanField(default=False)
    rollout_percentage = models.IntegerField(default=0)         # 0-100
    enabled_for_user_ids = ArrayField(models.UUIDField(), default=list, blank=True)
    enabled_for_user_groups = ArrayField(models.CharField(max_length=64), default=list, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    deprecated_at = models.DateTimeField(null=True, blank=True)
    expected_removal_date = models.DateField(null=True, blank=True)
```

### API

```python
from libs.feature_flags import is_enabled

if is_enabled("new_wallet_view", user=request.user):
    return new_handler(request)
return old_handler(request)
```

---

## 2. What gets a flag

| Scenario | Flag? |
|---|---|
| Risky new feature rollout | ✅ |
| Change to user-facing UI | ✅ |
| New gRPC service first deploy | ✅ (kill-switch) |
| Migration cutover paths | ✅ |
| Bug fix | ❌ (just deploy) |
| Refactor without behavior change | ❌ |
| Schema migration | ❌ |
| **Wallet write logic** | ❌ — either it's correct or it doesn't ship |
| Auth bypass | ❌ — never flagged |

The line: a flag delays the decision to release; risky writes need to be debugged, not toggled.

---

## 3. Naming

`<area>_<change>_<intent>`:

- `wallet_v2_listing_enabled`
- `notification_grpc_canary_enabled`
- `live_gift_realtime_broadcast_enabled`
- `cutover_read_from_new_db_for_orders`

Avoid generic names: `new_thing`, `experimental`, `temp_flag`.

---

## 4. Lifecycle

Every flag has an **expiry plan at creation**:

```python
FeatureFlag.objects.create(
    key="live_gift_realtime_broadcast_enabled",
    description=(
        "Gates gift broadcast through Live Runtime gRPC. "
        "Remove after W17, post-stabilization."
    ),
    expected_removal_date=date(2026, 12, 31),
    rollout_percentage=0,
)
```

**Rule**: a flag older than 90 days without a removal plan is a code smell.

### Quarterly cleanup ritual
- List flags
- For each: commit-on / commit-off / extend with justification
- PR removes flag check and dead branch from code
- Audit log: `events.dlq.resolve` style audit entry for the flag removal

---

## 5. Migration-window flags

During the cutover from `django-auth-core`:

| Flag pattern | Use |
|---|---|
| `cutover_read_from_new_db_for_<feature>` | Shadow-read in new system to validate |
| `cutover_write_to_new_db_for_<feature>` | Dual-write if needed |
| `cutover_use_new_endpoint_<resource>` | Per-endpoint cutover |

All migration flags **must** have `expected_removal_date` ≤ 60 days post-cutover.

---

## 6. Anti-patterns

- ❌ Nested flags (`if flag_a and flag_b and not flag_c`)
- ❌ Flags around `EconomyService.debit` (money write logic is not flag-toggled)
- ❌ Permanent flags
- ❌ Flags evaluated outside the entry layer (each request resolves flags once, then passes them down)
- ❌ Flags whose `description` is "TODO" or empty
- ❌ Flags read inside DB transactions (resolve before opening `atomic()`)

---

## 7. Auditability

Every flag change is audited (per `contracts/audit.md` §5):
```json
{
  "action": "platform.feature_flag.toggle",
  "actor_type": "admin",
  "actor_id": "<admin-uuid>",
  "target_type": "FeatureFlag",
  "target_id": "<flag-uuid>",
  "before_state": {"enabled": false, "rollout_percentage": 0},
  "after_state": {"enabled": true, "rollout_percentage": 10},
  "severity": "sensitive",
  "reason": "Begin canary rollout per ramp plan"
}
```

---

## 8. Admin API (V1)

Per `contracts/platform-config.md`:

- `GET /api/v1/admin/feature-flags` — list with state
- `POST /api/v1/admin/feature-flags` — create (requires expected_removal_date)
- `PATCH /api/v1/admin/feature-flags/{key}` — toggle / rollout / target user lists
- `DELETE /api/v1/admin/feature-flags/{key}` — only when all client code references removed (PR-verified)

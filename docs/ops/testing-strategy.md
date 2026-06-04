# Testing Strategy

How we keep a Django monolith + three gRPC services + Celery workers honest.

---

## 1. Test pyramid

Target proportions:

| Layer | Share | What |
|---|---|---|
| Unit | 70% | Service functions, pure logic, model methods |
| Contract | 20% | gRPC client ↔ server contracts; HTTP API contracts |
| Integration / E2E | 10% | Real services in docker-compose, smoke flows |

If you're spending more than 30% of CI time on integration tests, the test design has drifted.

---

## 2. Unit tests

- **Subject**: a single function or class, typically in `services.py`
- **External dependencies mocked**: DB is the only acceptable real dependency (test postgres in CI; sqlite is fine locally for pure logic)
- **gRPC clients mocked**: never hit a real gRPC server in unit tests
- **factory_boy**: all test data via factories. `Model.objects.create` directly in tests is forbidden (lint rule)

```python
def test_register_user_creates_user_and_outbox_event(db):
    user = register_user(email="Test@Example.com ", password="hunter2")
    assert user.email == "test@example.com"  # normalized
    assert OutboxEvent.objects.filter(event_type="identity.UserRegistered").count() == 1
    assert PointWallet.objects.filter(user=user).count() == 1
    assert CreditWallet.objects.filter(user=user).count() == 1
```

### Factory conventions
- `tests/factories/<domain>.py` per domain
- Import factories, not models, in test files
- Use `*Factory.create_batch(N)` for collections
- `*Factory.create(...kwargs)` for one-offs
- Sub-factories declared as `SubFactory(UserFactory)`

---

## 3. Contract tests

Two flavors:

### 3.1 gRPC contract tests
For every gRPC service we operate, maintain a **generated mock server** from the proto. Django's tests run against this mock to verify our client behavior.

```python
def test_notification_client_send_respects_deadline(mock_notification_server):
    mock_notification_server.set_handler("Send", lambda req: time.sleep(10))
    with pytest.raises(DeadlineExceeded):
        notification_client.send(..., timeout=1.0)

def test_notification_client_retries_on_unavailable(mock_notification_server):
    mock_notification_server.set_handler("Send", responses=[
        grpc.StatusCode.UNAVAILABLE,
        grpc.StatusCode.UNAVAILABLE,
        SendResponse(...)
    ])
    response = notification_client.send(...)
    assert response.status == "QUEUED"
    assert mock_notification_server.call_count("Send") == 3
```

For services we depend on but don't operate (Stripe, blockchain RPC), use recorded HTTP fixtures (`vcr.py`).

### 3.2 HTTP API contract tests
For every public endpoint, an OpenAPI-style contract test verifies request/response shapes against the documented contract in `contracts/<domain>.md`. Schema drift fails the build.

```python
def test_post_login_response_shape(api_client, user_factory):
    user = user_factory(password="hunter2")
    response = api_client.post("/api/v1/auth/login", {"email": user.email, "password": "hunter2"})
    assert response.status_code == 200
    schema = load_contract_schema("identity.md", "POST /api/v1/auth/login", "200")
    validate(response.json(), schema)
```

---

## 4. Integration tests

- Run against `docker-compose up` with all profiles
- Limited to **happy-path smoke tests** for the most critical flows:
  1. Register → login → me
  2. Daily reward claim → wallet credited
  3. Drama unlock (MP) → episode playable
  4. Top-up (Stripe sandbox) → MC credited
  5. Order create → pay → seller ship → buyer confirm → settled
  6. Outbox event → dispatcher → Notification gRPC → email send (sandbox)
  7. KYC submit → upload → submit → admin approve
  8. Gift send (video / drama)
- Each integration test takes 30+ seconds; keep the set small (V1: ≤20 tests)
- Run nightly + on every release-candidate PR

---

## 5. Migration tests

Special category. Migration commands have their own test suite under `ops/migration/tests/`.

Required tests per migration command:

| Test | Description |
|---|---|
| `test_dry_run_does_not_write` | `--dry-run` touches no tables |
| `test_re_run_is_idempotent` | Running twice produces same result |
| `test_resumes_from_partial_failure` | Inject failure mid-batch, re-run, verify completion |
| `test_validate_passes_after_full_run` | `validate_migration` returns PASS |
| `test_validate_fails_when_balances_mismatch` | Intentionally corrupt new DB, verify FAIL |
| `test_password_hash_compatibility` | Imported user can log in with old password |
| `test_wallet_balance_sum_invariant` | SUM(new) == SUM(legacy) for each currency |

---

## 6. Service-internal tests (per gRPC service)

Each gRPC service has its own test suite mirroring Django:
- Unit tests for handlers
- Contract tests: real gRPC server up, real RPCs called from in-process client
- Integration tests: full systemd-like startup of the service in test mode

Each service has its own `make test` target; CI runs them all.

---

## 7. CI gates

| Gate | Action on failure |
|---|---|
| `ruff format` / `ruff check` | Fail PR |
| `mypy` (strict on services) | Fail PR |
| `import-linter` | Fail PR |
| `django-migration-linter` | Fail PR |
| Unit tests pass | Fail PR |
| Coverage ≥ 80% on `services.py` per app | Fail PR |
| Contract tests pass | Fail PR |
| Integration tests pass | Fail PR (nightly + release candidates) |
| Proto drift check (`buf breaking`) | Fail PR |
| Gitleaks (secret scan) | Fail PR |

---

## 8. Test data discipline

- Production data is **never** used in tests, even anonymized
- Staging may carry a sanitized snapshot for migration rehearsals only
- Factories live in `tests/factories/` and are imported, not duplicated across test files
- Test fixtures (JSON files) are versioned in git; small (<10KB each); review-able

---

## 9. Performance tests

- Not required in V1
- Add when SLOs are defined (post-launch quarter)
- When added: live-fire load tests against staging using `locust` or `k6`

---

## 10. Anti-patterns to reject in review

- ❌ Tests that assert log content for behavior (logs aren't contracts)
- ❌ Tests with hardcoded sleeps to wait for Celery
- ❌ Tests that share state via global variables
- ❌ Tests that depend on test order
- ❌ Integration tests for things a unit test could cover
- ❌ `Model.objects.create` directly in tests
- ❌ Tests that import from `apps/X.models` when in `apps/Y`'s test (use factory)
- ❌ Tests that mock too much (over-mocked tests test the mocks)
- ❌ Tests that fetch from the live internet (use vcr.py or fixtures)
- ❌ Tests with `time.sleep()` for non-trivial duration

---

## 11. Local test workflow

```bash
make test               # full suite (~3 min)
make test-fast          # unit + fast contract only (~30s)
make test-app APP=identity   # one app's tests
pytest tests/unit/economy/test_wallet_credit.py::test_credit_with_idempotency_key  # one test
```

Coverage report:
```bash
make coverage           # writes htmlcov/ for browser view
```

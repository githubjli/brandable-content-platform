# Contributing

Day-to-day developer workflow. For the bigger picture see
[docs/getting-started.md](docs/getting-started.md); for test philosophy see
[docs/ops/testing-strategy.md](docs/ops/testing-strategy.md).

---

## 1. Run everything through `make`

`make` is the **only** supported entry point for linting and tests. Do not run
`ruff` / `mypy` / `lint-imports` / `pytest` directly from the repo root.

| Command | What it does |
|---|---|
| `make install` | Create `.venv` and install deps (`-e ".[dev]"`) |
| `make dev` | docker-compose `core` profile: postgres + redis + django |
| `make lint` | `ruff format --check` + `ruff check` + `mypy` + `import-linter` |
| `make test` | Full pytest suite (with coverage report) |
| `make test-fast` | pytest without coverage (quicker inner loop) |
| `make format` | Auto-format with ruff |
| `make migrate` / `make makemigrations` | Django migrations |

### Why `make` and not the tools directly

**mypy and import-linter must run with the working directory set to `django/`.**

- The django-stubs mypy plugin imports `config.settings.base` to boot Django;
  that module only resolves when `django/` is on the path (i.e. cwd is `django/`).
  Running `mypy django/` **from the repo root crashes** with
  `Error constructing plugin instance of NewSemanalDjangoPlugin`.
- `import-linter`'s contracts use `apps` / `libs` as root packages, which only
  resolve from `django/`, and the config lives at the repo root, so it needs
  `--config ../.importlinter`.

`make lint` encapsulates all of this (`cd django && mypy apps libs config …`,
`cd django && lint-imports --config ../.importlinter`). CI does the same. If you
run a tool by hand, mirror those invocations.

---

## 2. Linter gotchas

- **No stale `# type: ignore`.** mypy runs with `warn_unused_ignores = true`, so
  an ignore that no longer suppresses anything *fails the build*. Remove it (or
  narrow it to the real code, e.g. `# type: ignore[arg-type]`).
- **Two import-linter contracts** (both must stay green):
  - `libs/` may not import from `apps/` (even under `TYPE_CHECKING` — use a
    `Protocol` instead of importing a model for a type hint).
  - No cross-app **model** imports: reach other domains via their `services.py`,
    never their `models.py`. (`apps.events` / `apps.audit` are infrastructure and
    may be imported by any app.)
- **Money fields:** write `DecimalField(max_digits=18, decimal_places=4)`
  explicitly. The django-stubs plugin can't follow `DecimalField(**KWARGS)` and
  will emit a wall of `arg-type` errors.

---

## 3. Database & running tests

### Tests run on PostgreSQL, not SQLite

`config.settings.test` connects to Postgres (default
`DATABASE_URL=postgres://brandable:brandable@localhost:5432/brandable_test`).
SQLite is **not** used: the suite relies on Postgres-only behaviour
(`audit_log` append-only triggers, transaction-isolation options) and we want
parity with CI and production.

### Local loop

```bash
make dev                 # brings up postgres + redis (docker-compose core profile)
# or just the DB:        docker compose --profile core up -d postgres
make test                # full suite      (pytest, from django/, --ds=config.settings.test)
make test-fast           # same, no coverage
```

pytest-django runs with `--reuse-db` (see `pyproject.toml [tool.pytest.ini_options]`):
the test database `test_brandable_test` is kept between runs for speed.

### Gotcha: `--reuse-db` + a regenerated migration → stale schema

If you regenerate a migration **under the same filename** (or otherwise change a
migration pytest-django can't detect), the reused test DB keeps the old schema
and you'll get confusing `IntegrityError` / column errors. Rebuild it once:

```bash
cd django && ../.venv/bin/pytest --create-db
```

CI never hits this — it spins a fresh `postgres:15` service container per run.

### Pointing at a different database

```bash
DATABASE_URL=postgres://user:pass@host:5432/dbname make test
```

---

## 4. Migrations

- Every schema change ships its migration; `django-migration-linter` gates them.
- **Adding a column to an existing table must be nullable or carry a default** —
  a new `NOT NULL` column without a default is rejected (`add_not_null_column`).
- Wallet-/audit-touching migrations are immutable-by-design; never edit a shipped
  migration, add a new one.

---

## 5. Before you push

```bash
make lint && make test
```

Both must be green. A typical PR also includes (per
[getting-started.md](docs/getting-started.md#first-pr-expectations)): a contract
update if behaviour changed, tests, a migration if the schema changed, an Outbox
event for cross-app fan-out, and an AuditLog row for sensitive actions.

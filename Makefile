.DEFAULT_GOAL := help
DJANGO_DIR := django
VENV       := .venv
# Use .venv when it exists (local dev); fall back to system python (CI installs deps globally).
PYTHON     := $(if $(wildcard $(VENV)/bin/python),$(VENV)/bin/python,python)
MANAGE     := cd $(DJANGO_DIR) && ../$(PYTHON) manage.py
PYTEST     := cd $(DJANGO_DIR) && ../$(PYTHON) -m pytest
RUFF       := $(VENV)/bin/ruff
MYPY       := $(VENV)/bin/mypy
PROTO_DIR  := proto

.PHONY: help dev test lint proto-gen migrate seed shell install install-pre-commit check-keys

help:  ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
	  awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-18s\033[0m %s\n", $$1, $$2}'

# ---------------------------------------------------------------------------
# Dev
# ---------------------------------------------------------------------------
dev:  ## Start postgres + redis + django (core profile)
	docker compose --profile core up --build

dev-services:  ## Start all services including notification gRPC
	docker compose --profile core --profile services up --build

dev-all:  ## Start everything including observability stack
	docker compose --profile core --profile services --profile observability up --build

# ---------------------------------------------------------------------------
# Install
# ---------------------------------------------------------------------------

# Find python >= 3.11 to bootstrap the venv. Tried in order: 3.13, 3.12, 3.11.
# Override with: PYTHON_BIN=/path/to/python make install
PYTHON_BIN ?= $(shell command -v python3.13 2>/dev/null || \
                       command -v python3.12 2>/dev/null || \
                       command -v python3.11 2>/dev/null || \
                       echo "")

$(VENV)/bin/activate:
	@if [ -z "$(PYTHON_BIN)" ]; then \
	  echo "ERROR: Python 3.11+ not found. Install it or set PYTHON_BIN=/path/to/python3.11+"; \
	  exit 1; \
	fi
	@echo "Bootstrapping .venv with $$($(PYTHON_BIN) --version)..."
	$(PYTHON_BIN) -m venv $(VENV)
	$(PYTHON) -m pip install --upgrade pip -q
	$(PYTHON) -m pip install -e ".[dev]" -q
	@echo ".venv ready. Run 'source $(VENV)/bin/activate' or use 'make <target>' directly."

install: $(VENV)/bin/activate  ## Create .venv and install all dependencies

install-pre-commit: install  ## Install pre-commit hooks into .venv
	$(VENV)/bin/pre-commit install

# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------
migrate:  ## Run Django migrations
	$(MANAGE) migrate

makemigrations:  ## Create new migrations
	$(MANAGE) makemigrations

seed:  ## Seed the database (stub)
	$(MANAGE) seed

# ---------------------------------------------------------------------------
# Shell
# ---------------------------------------------------------------------------
shell:  ## Open Django shell
	$(MANAGE) shell

# ---------------------------------------------------------------------------
# Test
# ---------------------------------------------------------------------------
test:  ## Run tests with coverage
	$(PYTEST) \
	  --ds=config.settings.test \
	  --cov=. \
	  --cov-report=term-missing \
	  --cov-fail-under=0 \
	  -v

test-fast:  ## Run tests without coverage
	$(PYTEST) --ds=config.settings.test -q

# ---------------------------------------------------------------------------
# Lint
# ---------------------------------------------------------------------------
lint: lint-ruff lint-mypy lint-imports  ## Run all linters

lint-ruff:  ## Ruff format check + lint
	$(RUFF) format --check $(DJANGO_DIR)
	$(RUFF) check $(DJANGO_DIR)

lint-mypy:  ## Type-check with mypy
	$(MYPY) $(DJANGO_DIR) --config-file pyproject.toml

lint-imports:  ## Check import rules with import-linter
	cd $(DJANGO_DIR) && ../$(VENV)/bin/lint-imports

format:  ## Auto-format with ruff
	$(RUFF) format $(DJANGO_DIR)
	$(RUFF) check --fix $(DJANGO_DIR)

# ---------------------------------------------------------------------------
# Proto
# ---------------------------------------------------------------------------
proto-gen:  ## Compile all .proto files to Python
	@echo "Compiling proto files..."
	@# Compile common proto first, then service-specific protos.
	@# protoc honours package declarations (e.g. notification.v1) and creates
	@# matching subdirectories inside the output root.  We seed every subdir
	@# with __init__.py so the generated packages are importable.
	@for svc in notification chat live; do \
	  if [ -d services/$$svc ]; then \
	    mkdir -p services/$$svc/generated; \
	    touch services/$$svc/generated/__init__.py; \
	    $(PYTHON) -m grpc_tools.protoc \
	      -I $(PROTO_DIR) \
	      --python_out=services/$$svc/generated/ \
	      --grpc_python_out=services/$$svc/generated/ \
	      $(PROTO_DIR)/common/v1/common.proto; \
	    find services/$$svc/generated -type d | xargs -I{} touch {}/__init__.py; \
	  fi; \
	done
	@# Compile service-specific protos
	@for svc in notification chat live; do \
	  if ls $(PROTO_DIR)/$$svc/v1/*.proto 2>/dev/null | grep -q .; then \
	    $(PYTHON) -m grpc_tools.protoc \
	      -I $(PROTO_DIR) \
	      --python_out=services/$$svc/generated/ \
	      --grpc_python_out=services/$$svc/generated/ \
	      $(PROTO_DIR)/$$svc/v1/*.proto; \
	    find services/$$svc/generated -type d | xargs -I{} touch {}/__init__.py; \
	  fi; \
	done
	@# Generate Django-side client stubs (read-only stubs used by django/libs/grpc_client)
	@mkdir -p django/libs/grpc_client/generated
	@touch django/libs/grpc_client/generated/__init__.py
	@$(PYTHON) -m grpc_tools.protoc \
	  -I $(PROTO_DIR) \
	  --python_out=django/libs/grpc_client/generated/ \
	  --grpc_python_out=django/libs/grpc_client/generated/ \
	  $(PROTO_DIR)/common/v1/common.proto
	@for svc in notification chat live; do \
	  if ls $(PROTO_DIR)/$$svc/v1/*.proto 2>/dev/null | grep -q .; then \
	    $(PYTHON) -m grpc_tools.protoc \
	      -I $(PROTO_DIR) \
	      --python_out=django/libs/grpc_client/generated/ \
	      --grpc_python_out=django/libs/grpc_client/generated/ \
	      $(PROTO_DIR)/$$svc/v1/*.proto; \
	  fi; \
	done
	@find django/libs/grpc_client/generated -type d | xargs -I{} touch {}/__init__.py
	@echo "Proto compilation done."

proto-check:  ## Check that generated proto files are up to date
	@echo "Checking proto files are not stale..."
	@$(MAKE) proto-gen
	@git diff --exit-code services/*/generated/ django/libs/grpc_client/generated/ || \
	  (echo "ERROR: Generated proto files are stale. Run 'make proto-gen'" && exit 1)

# ---------------------------------------------------------------------------
# Django checks
# ---------------------------------------------------------------------------
check:  ## Run Django system check
	$(MANAGE) check --settings=config.settings.local

check-deploy:  ## Run Django deploy check
	$(MANAGE) check --deploy --settings=config.settings.production

# ---------------------------------------------------------------------------
# Keys (dev only)
# ---------------------------------------------------------------------------
gen-dev-keys:  ## Generate dev RSA key pair for JWT (DO NOT USE IN PRODUCTION)
	mkdir -p $(DJANGO_DIR)/config/keys
	openssl genrsa -out $(DJANGO_DIR)/config/keys/jwt_private.pem 2048
	openssl rsa -in $(DJANGO_DIR)/config/keys/jwt_private.pem \
	  -pubout -out $(DJANGO_DIR)/config/keys/jwt_public.pem
	@echo "Dev keys generated at django/config/keys/"

# ---------------------------------------------------------------------------
# Ansible deploy
# ---------------------------------------------------------------------------
deploy-staging:  ## Deploy to staging via Ansible
	ansible-playbook ops/ansible/deploy.yml --extra-vars "env=staging branch=main"

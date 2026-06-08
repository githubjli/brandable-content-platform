"""
Base Django settings for brandable-content-platform.

All environment-specific settings files import from here and override as needed.
"""

import os
from pathlib import Path

import dj_database_url
from dotenv import load_dotenv

load_dotenv()

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent.parent.parent  # django/

# ---------------------------------------------------------------------------
# Security
# ---------------------------------------------------------------------------
SECRET_KEY = os.environ["DJANGO_SECRET_KEY"]

ALLOWED_HOSTS: list[str] = (
    os.environ.get("ALLOWED_HOSTS", "").split(",") if os.environ.get("ALLOWED_HOSTS") else []
)

DEBUG = False

# ---------------------------------------------------------------------------
# Application definition
# ---------------------------------------------------------------------------
DJANGO_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
]

THIRD_PARTY_APPS = [
    "rest_framework",
    "corsheaders",
    "drf_spectacular",
]

LOCAL_APPS = [
    # Infrastructure (load first so other apps can use them)
    "apps.events.apps.EventsConfig",
    "apps.audit.apps.AuditConfig",
    "apps.platform_config.apps.PlatformConfigConfig",
    # Business domains
    "apps.identity.apps.IdentityConfig",
    "apps.economy.apps.EconomyConfig",
    "apps.payments.apps.PaymentsConfig",
    "apps.content.video.apps.VideoConfig",
    "apps.content.drama.apps.DramaConfig",
    "apps.content.live.apps.LiveConfig",
    "apps.content.gift.apps.GiftConfig",
    "apps.commerce.apps.CommerceConfig",
    "apps.membership.apps.MembershipConfig",
]

INSTALLED_APPS = DJANGO_APPS + THIRD_PARTY_APPS + LOCAL_APPS

MIDDLEWARE = [
    "corsheaders.middleware.CorsMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "libs.logging.middleware.RequestLoggingMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------
_database_url = os.environ.get(
    "DATABASE_URL", "postgres://brandable:brandable@localhost:5432/brandable"
)
DATABASES = {
    "default": dj_database_url.parse(
        _database_url,
        conn_max_age=60,
        conn_health_checks=True,
    )
}
# Pin the transaction isolation level explicitly. In a libpq options string spaces
# separate options, so the space inside "read committed" must be backslash-escaped —
# otherwise libpq parses just "read" and the connection fails.
DATABASES["default"]["OPTIONS"] = {"options": r"-c default_transaction_isolation=read\ committed"}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# ---------------------------------------------------------------------------
# Cache / Redis
# ---------------------------------------------------------------------------
REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")

CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.redis.RedisCache",
        "LOCATION": REDIS_URL,
    }
}

# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------
# Password hash compatibility (migration W4-W5).
#
# Identity stores hashes in Django's encoded format (see apps/identity/models.User
# .password_hash) and verifies with django.contrib.auth.hashers.check_password.
# Legacy `django-auth-core` is itself Django, so imported hashes are Django-native.
# The FIRST entry is the "preferred" algorithm: any login whose stored hash uses a
# different (legacy) algorithm is transparently re-hashed to it on first successful
# login (see apps/identity/services.login). Keep PBKDF2 first — it needs no extra
# native deps. The remaining entries exist purely so legacy hashes still verify.
#
# NOTE: Argon2/BCrypt verification additionally requires `argon2-cffi` / `bcrypt`
# at runtime. They are listed here for completeness but are only reachable if the
# legacy data actually contains those formats — add the lib to pyproject deps if so.
PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.PBKDF2PasswordHasher",
    "django.contrib.auth.hashers.PBKDF2SHA1PasswordHasher",
    "django.contrib.auth.hashers.Argon2PasswordHasher",
    "django.contrib.auth.hashers.BCryptSHA256PasswordHasher",
    "django.contrib.auth.hashers.ScryptPasswordHasher",
]

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# ---------------------------------------------------------------------------
# Internationalisation
# ---------------------------------------------------------------------------
LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

# ---------------------------------------------------------------------------
# Static / Media
# ---------------------------------------------------------------------------
STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"

# Max upload sizes (bytes)
FILE_UPLOAD_MAX_MEMORY_SIZE = 2 * 1024 * 1024 * 1024  # 2 GB
DATA_UPLOAD_MAX_MEMORY_SIZE = FILE_UPLOAD_MAX_MEMORY_SIZE

# ---------------------------------------------------------------------------
# Django REST Framework
# ---------------------------------------------------------------------------
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "libs.jwt_auth.authentication.JWTAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticated",
    ],
    "DEFAULT_PAGINATION_CLASS": "libs.pagination.cursor.CursorPagination",
    "PAGE_SIZE": 20,
    "DEFAULT_RENDERER_CLASSES": [
        "rest_framework.renderers.JSONRenderer",
    ],
    "DEFAULT_PARSER_CLASSES": [
        "rest_framework.parsers.JSONParser",
        "rest_framework.parsers.MultiPartParser",
        "rest_framework.parsers.FormParser",
    ],
    "EXCEPTION_HANDLER": "libs.errors.handlers.exception_handler",
    # OpenAPI schema generation (drf-spectacular). Served at /api/v1/schema/.
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
}

# ---------------------------------------------------------------------------
# OpenAPI schema (drf-spectacular) — powers /api/v1/schema/ + Swagger/Redoc UIs,
# and lets mobile clients (e.g. Flutter via openapi-generator) codegen models.
# ---------------------------------------------------------------------------
SPECTACULAR_SETTINGS = {
    "TITLE": "Brandable Content Platform API",
    "DESCRIPTION": (
        "REST API for the brandable content platform "
        "(identity, economy, payments, content, commerce, membership). "
        "JWT (RS256) bearer auth; cursor pagination; Idempotency-Key on creates."
    ),
    "VERSION": "1.0.0",
    "SERVE_INCLUDE_SCHEMA": False,  # don't expose the schema endpoint inside the schema
    "SCHEMA_PATH_PREFIX": r"/api/v1",
    "COMPONENT_SPLIT_REQUEST": True,
}

# ---------------------------------------------------------------------------
# CORS
# ---------------------------------------------------------------------------
CORS_ALLOW_CREDENTIALS = True
CORS_ALLOWED_ORIGINS: list[str] = (
    os.environ.get("CORS_ALLOWED_ORIGINS", "").split(",")
    if os.environ.get("CORS_ALLOWED_ORIGINS")
    else []
)
# In production this is populated from PlatformConfig at startup.

# ---------------------------------------------------------------------------
# Celery
# ---------------------------------------------------------------------------
CELERY_BROKER_URL = REDIS_URL
CELERY_RESULT_BACKEND = REDIS_URL
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TASK_SERIALIZER = "json"
CELERY_RESULT_SERIALIZER = "json"
CELERY_TIMEZONE = "UTC"
CELERY_TASK_TRACK_STARTED = True

# ---------------------------------------------------------------------------
# OpenTelemetry
# ---------------------------------------------------------------------------
OTEL_SERVICE_NAME = os.environ.get("OTEL_SERVICE_NAME", "brandable-content-platform")
OTEL_EXPORTER_OTLP_ENDPOINT = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4317")
OTEL_ENABLED = os.environ.get("OTEL_ENABLED", "true").lower() == "true"

# ---------------------------------------------------------------------------
# JWT
# ---------------------------------------------------------------------------
JWT_ALGORITHM = "RS256"
JWT_ISSUER = "brandable-content-platform"
JWT_ACCESS_TTL_SECONDS = 15 * 60  # 15 minutes
JWT_REFRESH_TTL_SECONDS = 7 * 24 * 3600  # 7 days

# Path to RSA private key PEM (used by Identity to issue tokens)
JWT_PRIVATE_KEY_PATH = os.environ.get(
    "JWT_PRIVATE_KEY_PATH", str(BASE_DIR / "config" / "keys" / "jwt_private.pem")
)
# Path to RSA public key PEM (used by all services to verify tokens)
JWT_PUBLIC_KEY_PATH = os.environ.get(
    "JWT_PUBLIC_KEY_PATH", str(BASE_DIR / "config" / "keys" / "jwt_public.pem")
)
# Key ID — surfaced in JWKS
JWT_KID = os.environ.get("JWT_KID", "dev-key-1")

# ---------------------------------------------------------------------------
# Economy
# ---------------------------------------------------------------------------
# Daily login reward amount (MP). String so it parses to an exact Decimal.
ECONOMY_DAILY_REWARD_MP = os.environ.get("ECONOMY_DAILY_REWARD_MP", "10.0000")
# Credit-recharge pay-to address; empty => recharge-info returns 503 until set.
# On-chain/Stripe verification itself is wired in with payments (Week 9).
ECONOMY_RECHARGE_PAY_TO_ADDRESS = os.environ.get("ECONOMY_RECHARGE_PAY_TO_ADDRESS", "")
ECONOMY_RECHARGE_CONFIRMATIONS = int(os.environ.get("ECONOMY_RECHARGE_CONFIRMATIONS", "0"))

# ---------------------------------------------------------------------------
# Payments (Week 9). Provider config lives in env/secrets for now; it migrates to
# PlatformConfig in Week 11. Secrets must NEVER be committed (see docs/ops/secrets).
# ---------------------------------------------------------------------------
STRIPE_PUBLISHABLE_KEY = os.environ.get("STRIPE_PUBLISHABLE_KEY", "")
STRIPE_SECRET_KEY = os.environ.get("STRIPE_SECRET_KEY", "")
STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET", "")
# When no live Stripe secret key is configured, the adapter returns a synthetic
# PaymentIntent instead of calling Stripe (lets dev/test run without network).
STRIPE_FAKE_MODE = not STRIPE_SECRET_KEY.startswith("sk_")
PAYMENT_ORDER_TTL_SECONDS = int(os.environ.get("PAYMENT_ORDER_TTL_SECONDS", str(30 * 60)))

# Blockchain backends. Only the LTT chain is enabled in V1 (THB-LTT stablecoin).
LTT_NODE_URL = os.environ.get("LTT_NODE_URL", "")
LTT_RECEIVE_ADDRESS = os.environ.get("LTT_RECEIVE_ADDRESS", "")
LTT_REQUIRED_CONFIRMATIONS = int(os.environ.get("LTT_REQUIRED_CONFIRMATIONS", "1"))

# ---------------------------------------------------------------------------
# Commerce (Week 10)
# ---------------------------------------------------------------------------
# Platform fee taken from each product order's subtotal (seller gets the rest).
COMMERCE_PLATFORM_FEE_RATE = os.environ.get("COMMERCE_PLATFORM_FEE_RATE", "0.05")
COMMERCE_ORDER_TTL_SECONDS = int(os.environ.get("COMMERCE_ORDER_TTL_SECONDS", str(30 * 60)))

# ---------------------------------------------------------------------------
# gRPC services
# ---------------------------------------------------------------------------
GRPC_NOTIFICATION_ADDRESS = os.environ.get("GRPC_NOTIFICATION_ADDRESS", "localhost:50051")
GRPC_LIVE_RUNTIME_ADDRESS = os.environ.get("GRPC_LIVE_RUNTIME_ADDRESS", "localhost:50053")
GRPC_TIMEOUT_SECONDS = float(os.environ.get("GRPC_TIMEOUT_SECONDS", "3"))

# When false (default), apps/content/live/runtime.py runs in fake mode and the
# live stream lifecycle works without a running Live Runtime gRPC service.
LIVE_RUNTIME_ENABLED = os.environ.get("LIVE_RUNTIME_ENABLED", "").lower() in {"1", "true", "yes"}

# When false (default in dev/test), notification event handlers no-op instead of
# making a gRPC call to a Notification service that isn't running. Enable in
# environments where the Notification service is up (Week 11 email canary).
NOTIFICATION_ENABLED = os.environ.get("NOTIFICATION_ENABLED", "false").lower() == "true"

# ---------------------------------------------------------------------------
# Logging — structured JSON, trace_id injected via filter
# ---------------------------------------------------------------------------
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "filters": {
        "trace_id": {
            "()": "libs.logging.filters.TraceIdFilter",
        },
    },
    "formatters": {
        "json": {
            "()": "libs.logging.formatters.JSONFormatter",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "json",
            "filters": ["trace_id"],
        },
    },
    "root": {
        "handlers": ["console"],
        "level": os.environ.get("LOG_LEVEL", "INFO"),
    },
    "loggers": {
        "django": {
            "handlers": ["console"],
            "level": "WARNING",
            "propagate": False,
        },
        "django.request": {
            "handlers": ["console"],
            "level": "ERROR",
            "propagate": False,
        },
    },
}

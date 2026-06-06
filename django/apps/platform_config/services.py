"""Service layer for platform_config (platform-config.md).

get_platform_config() is the single read path (read-through cache, 60s). update_config
is the single write path (invalidates cache, emits events, audits).
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from django.core.cache import cache
from django.db import transaction

from .models import CONFIG_CACHE_KEY, PlatformConfig

logger = logging.getLogger(__name__)
CACHE_TTL_SECONDS = 60

_FEATURE_FIELDS = (
    "live_enabled",
    "drama_enabled",
    "commerce_enabled",
    "membership_enabled",
    "registration_open",
)
# Fields an admin PATCH may set.
_EDITABLE_FIELDS = (
    "site_name",
    "tagline",
    "logo_url",
    "favicon_url",
    "primary_color",
    "secondary_color",
    "support_email",
    "min_supported_app_version",
    "force_upgrade_below",
    *_FEATURE_FIELDS,
    "stripe_publishable_key",
    "terms_url",
    "privacy_url",
    "help_url",
)


# ---------------------------------------------------------------------------
# Cross-app stubs
# ---------------------------------------------------------------------------


def _emit(
    event_type: str, payload: dict, idempotency_key: str, actor_id: str | None = None
) -> None:
    try:
        from apps.events.services import emit

        emit(
            event_type=event_type,
            idempotency_key=idempotency_key,
            payload=payload,
            actor_id=actor_id,
        )
    except Exception:
        logger.debug("_emit: emit failed; skipping %s", event_type)


def _record_audit(action: str, *, actor_id: str | None, target_id: str, after_state: dict) -> None:
    from apps.audit.services import record_audit

    record_audit(
        action=action,
        actor_type="admin",
        actor_id=actor_id,
        target_type="PlatformConfig",
        target_id=target_id,
        after_state=after_state,
        severity="sensitive",
    )


# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------


def _load() -> PlatformConfig:
    """Load (or create) the singleton, bypassing the cache."""
    config = PlatformConfig.objects.first()
    if config is None:
        config = PlatformConfig.objects.create()
    return config


def get_platform_config() -> PlatformConfig:
    """Return the singleton config (read-through cache, 60s TTL)."""
    cached = cache.get(CONFIG_CACHE_KEY)
    if cached is not None:
        return cached
    config = _load()
    cache.set(CONFIG_CACHE_KEY, config, CACHE_TTL_SECONDS)
    return config


def _iso_now() -> str:
    return datetime.now(tz=UTC).isoformat().replace("+00:00", "Z")


def serialize_public(config: PlatformConfig) -> dict[str, Any]:
    return {
        "site": {
            "name": config.site_name,
            "tagline": config.tagline,
            "logo_url": config.logo_url or None,
            "favicon_url": config.favicon_url or None,
            "primary_color": config.primary_color,
            "secondary_color": config.secondary_color,
            "support_email": config.support_email or None,
        },
        "client": {
            "min_supported_app_version": config.min_supported_app_version,
            "force_upgrade_below": config.force_upgrade_below or None,
        },
        "features": {
            "live_enabled": config.live_enabled,
            "drama_enabled": config.drama_enabled,
            "commerce_enabled": config.commerce_enabled,
            "membership_enabled": config.membership_enabled,
            "registration_open": config.registration_open,
        },
        "providers": {
            "stripe_publishable_key": config.stripe_publishable_key or None,
        },
        "links": {
            "terms_url": config.terms_url or None,
            "privacy_url": config.privacy_url or None,
            "help_url": config.help_url or None,
        },
        "generated_at": _iso_now(),
    }


# ---------------------------------------------------------------------------
# Write
# ---------------------------------------------------------------------------


def update_config(*, changes: dict[str, Any], actor_id: str | None) -> dict[str, Any]:
    """Apply a partial update to the singleton. Invalidates cache, emits events,
    audits. Returns the public serialization."""
    config = _load()

    toggled: dict[str, bool] = {}
    updated: list[str] = []
    for key, value in changes.items():
        if key not in _EDITABLE_FIELDS or value is None:
            continue
        if getattr(config, key) == value:
            continue
        if key in _FEATURE_FIELDS:
            toggled[key] = bool(value)
        setattr(config, key, value)
        updated.append(key)

    if not updated:
        return serialize_public(config)

    with transaction.atomic():
        config.save()  # invalidates the cache
        _emit(
            event_type="platform.ConfigUpdated",
            payload={
                "config_id": str(config.id),
                "changed_fields": updated,
                "occurred_at": _iso_now(),
            },
            idempotency_key=f"config_updated:{uuid.uuid4().hex}",
            actor_id=str(actor_id) if actor_id else None,
        )
        for flag, val in toggled.items():
            _emit(
                event_type="platform.FeatureToggled",
                payload={"flag": flag, "enabled": val, "occurred_at": _iso_now()},
                idempotency_key=f"feature_toggled:{flag}:{uuid.uuid4().hex}",
                actor_id=str(actor_id) if actor_id else None,
            )
        _record_audit(
            action="platform.config.update",
            actor_id=str(actor_id) if actor_id else None,
            target_id=str(config.id),
            after_state={"changed_fields": updated},
        )

    return serialize_public(config)

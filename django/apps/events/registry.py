"""Handler registry (events.md §7).

Handlers register at import time via @on_event; app AppConfig.ready() imports each
app's handlers module so the decorators run. The dispatcher looks handlers up by
event_type at dispatch time.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

# A handler receives the OutboxEvent instance and returns None on success or raises.
HandlerFn = Callable[[Any], None]


class SkipHandler(Exception):  # noqa: N818 — contract-named control-flow signal (events.md §7)
    """Raise from a handler to ack without action (e.g. receiver already processed)."""


@dataclass(frozen=True)
class Handler:
    name: str
    fn: HandlerFn
    versions: frozenset[int] = field(default_factory=lambda: frozenset({1}))


_REGISTRY: dict[str, list[Handler]] = {}


def on_event(
    event_type: str, *, versions: tuple[int, ...] = (1,)
) -> Callable[[HandlerFn], HandlerFn]:
    """Register a handler for an event_type. Same fn may register for several types."""

    def decorator(fn: HandlerFn) -> HandlerFn:
        handler = Handler(
            name=f"{fn.__module__}.{fn.__name__}", fn=fn, versions=frozenset(versions)
        )
        handlers = _REGISTRY.setdefault(event_type, [])
        if not any(h.name == handler.name for h in handlers):
            handlers.append(handler)
        return fn

    return decorator


def get_handlers(event_type: str, version: int = 1) -> list[Handler]:
    return [h for h in _REGISTRY.get(event_type, []) if version in h.versions]


def clear_registry() -> None:
    """Test helper — reset registered handlers."""
    _REGISTRY.clear()

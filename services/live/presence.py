"""Viewer-presence registry for the Live Runtime service (live-runtime.md §0).

Presence is ephemeral and high-churn, so it lives in the runtime service, not in
Django's DB. This is the in-memory scaffold with a Redis-ready interface: every
method is keyed by stream_id so swapping the backing store for Redis sets
(`SADD livepresence:{stream_id} {viewer}`) is a localized change.

Thread-safe: the gRPC server runs a ThreadPoolExecutor, so all mutation goes
through a single lock.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field


@dataclass
class _StreamPresence:
    # viewer_key -> joined-at epoch seconds. viewer_key is the user id, or
    # "anon:<ip>" / "anon:<token>" for unauthenticated viewers.
    viewers: dict[str, float] = field(default_factory=dict)


class ViewerPresenceRegistry:
    """In-memory viewer presence keyed by stream. Redis-swappable."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._streams: dict[str, _StreamPresence] = {}

    def join(self, *, stream_id: str, viewer_key: str) -> int:
        """Record a viewer as present; returns the live viewer count. Idempotent
        per viewer_key (re-joining refreshes the timestamp, doesn't double-count)."""
        with self._lock:
            sp = self._streams.setdefault(stream_id, _StreamPresence())
            sp.viewers[viewer_key] = time.time()
            return len(sp.viewers)

    def leave(self, *, stream_id: str, viewer_key: str) -> tuple[int, int]:
        """Remove a viewer; returns (remaining_count, watch_duration_seconds).
        watch_duration is 0 if the viewer was not present."""
        with self._lock:
            sp = self._streams.get(stream_id)
            if sp is None or viewer_key not in sp.viewers:
                return (0 if sp is None else len(sp.viewers), 0)
            joined_at = sp.viewers.pop(viewer_key)
            duration = max(0, int(time.time() - joined_at))
            return (len(sp.viewers), duration)

    def count(self, *, stream_id: str) -> int:
        with self._lock:
            sp = self._streams.get(stream_id)
            return len(sp.viewers) if sp else 0

    def clear(self, *, stream_id: str) -> None:
        """Drop all presence for a stream (e.g. on StopBroadcast/DeleteStream)."""
        with self._lock:
            self._streams.pop(stream_id, None)

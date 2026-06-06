#!/usr/bin/env python
"""Unit test for NotificationServicer.Send (no running server needed).

Run with:
    python services/notification/test_send.py

Calls the servicer method directly with a SendRequest and asserts the canary
behaviour: first call QUEUED, repeat (same idempotency_key) DUPLICATE, empty key
FAILED.
"""

from __future__ import annotations

import os
import sys

_service_dir = os.path.dirname(__file__)
for path in (_service_dir, os.path.join(_service_dir, "generated")):
    if path not in sys.path:
        sys.path.insert(0, path)

try:
    from notification.v1 import notification_pb2  # type: ignore[import]
except ImportError as exc:
    print(f"ERROR: Generated proto stubs not found: {exc}. Run `make proto-gen`.")
    sys.exit(1)

from servicer import NotificationServicer  # noqa: E402


def run() -> None:
    servicer = NotificationServicer()

    req = notification_pb2.SendRequest(
        idempotency_key="welcome:evt_1",
        channel="email",
        template_code="welcome",
        recipient_user_id="u1",
        recipient_address="a@b.com",
    )
    first = servicer.Send(req, None)
    assert first.status == "QUEUED", first.status
    assert first.message_id, "expected a message_id"

    second = servicer.Send(req, None)
    assert second.status == "DUPLICATE", second.status
    assert second.message_id == first.message_id

    empty = servicer.Send(notification_pb2.SendRequest(idempotency_key=""), None)
    assert empty.status == "FAILED", empty.status

    print("OK: Send QUEUED -> DUPLICATE -> FAILED(empty key)")


if __name__ == "__main__":
    run()

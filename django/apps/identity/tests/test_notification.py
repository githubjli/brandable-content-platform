"""Tests for the Week 11 email canary: welcome-email handler + notification client."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from apps.identity.handlers import send_welcome_email

_EVENT = SimpleNamespace(
    id="e1",
    payload={"user_id": "u1", "email": "a@b.com", "display_name": "Ann"},
)


class TestWelcomeEmailHandler:
    def test_noop_when_notification_disabled(self, settings):
        settings.NOTIFICATION_ENABLED = False
        with patch("libs.grpc_client.send_notification") as send:
            send_welcome_email(_EVENT)
        send.assert_not_called()

    def test_calls_notification_when_enabled(self, settings):
        settings.NOTIFICATION_ENABLED = True
        with patch("libs.grpc_client.send_notification") as send:
            send.return_value = {"status": "QUEUED", "message_id": "m1"}
            send_welcome_email(_EVENT)
        send.assert_called_once()
        kwargs = send.call_args.kwargs
        assert kwargs["idempotency_key"] == "welcome:e1"
        assert kwargs["channel"] == "email"
        assert kwargs["template_code"] == "welcome"
        assert kwargs["recipient_address"] == "a@b.com"


class TestSendNotificationClient:
    def test_builds_request_and_returns_status(self, monkeypatch):
        from libs import grpc_client

        captured: dict = {}

        class _FakeStub:
            def __init__(self, channel):
                pass

            def Send(self, request, metadata=None, timeout=None):  # noqa: N802 — gRPC method name
                captured["request"] = request
                captured["timeout"] = timeout
                return SimpleNamespace(status="QUEUED", message_id="m1")

        fake_pb2 = SimpleNamespace(SendRequest=lambda **kw: kw)
        fake_grpc = SimpleNamespace(NotificationServiceStub=_FakeStub)

        monkeypatch.setattr(grpc_client, "_notification_modules", lambda: (fake_pb2, fake_grpc))
        monkeypatch.setattr(grpc_client.grpc, "insecure_channel", lambda addr: MagicMock())

        result = grpc_client.send_notification(
            idempotency_key="welcome:e1",
            channel="email",
            template_code="welcome",
            recipient_address="a@b.com",
        )
        assert result == {"status": "QUEUED", "message_id": "m1"}
        assert captured["request"]["idempotency_key"] == "welcome:e1"
        assert captured["request"]["channel"] == "email"

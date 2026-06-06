"""Structured JSON logging configuration for the Chat service.

Call configure_logging() once at process start (before setup_telemetry).

Log record shape:
    {
        "timestamp": "2026-06-04T12:00:00.000000Z",
        "level": "INFO",
        "service": "chat-service",
        "logger": "services.chat.servicer",
        "trace_id": "00000000000000000000000000000000",
        "message": "...",
        ...extra fields...
    }
"""

from __future__ import annotations

import json
import logging
import os


class _JSONFormatter(logging.Formatter):
    """Single-line JSON log formatter matching the Django JSONFormatter shape."""

    SERVICE_NAME = "chat-service"

    def format(self, record: logging.LogRecord) -> str:
        # Resolve current OTel trace_id if available
        trace_id = getattr(record, "trace_id", "")
        if not trace_id:
            try:
                from telemetry import get_trace_id  # relative to service dir

                trace_id = get_trace_id()
            except Exception:
                pass

        log_data: dict = {
            "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%S.%fZ"),
            "level": record.levelname,
            "service": self.SERVICE_NAME,
            "logger": record.name,
            "trace_id": trace_id,
            "message": record.getMessage(),
        }

        # Include caller-supplied extra fields
        _skip = frozenset(
            {
                "args", "asctime", "created", "exc_info", "exc_text",
                "filename", "funcName", "id", "levelname", "levelno",
                "lineno", "module", "msecs", "message", "msg", "name",
                "pathname", "process", "processName", "relativeCreated",
                "stack_info", "thread", "threadName", "trace_id",
            }
        )
        for key, val in record.__dict__.items():
            if key not in _skip:
                log_data[key] = val

        if record.exc_info:
            log_data["exception"] = self.formatException(record.exc_info)
        elif record.exc_text:
            log_data["exception"] = record.exc_text

        return json.dumps(log_data, default=str)


def configure_logging() -> None:
    """Configure root logger with JSON output at the level set by LOG_LEVEL env var."""
    level_name = os.environ.get("LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)

    handler = logging.StreamHandler()
    handler.setFormatter(_JSONFormatter())

    root = logging.getLogger()
    # Remove any existing handlers installed by basicConfig before this call
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)

    # Silence noisy third-party loggers
    logging.getLogger("grpc").setLevel(logging.WARNING)

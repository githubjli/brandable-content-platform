"""Logging filters that inject OTel trace_id into log records."""

import logging


class TraceIdFilter(logging.Filter):
    """Inject trace_id into every log record from the current OTel span."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            from libs.telemetry import get_trace_id
            record.trace_id = get_trace_id()
        except Exception:
            record.trace_id = ""
        return True

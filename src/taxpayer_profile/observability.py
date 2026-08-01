"""Structured runtime events for local operations and diagnostics."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

PACKAGE_LOGGER_NAME = "taxpayer_profile"


class JsonEventFormatter(logging.Formatter):
    """Format one application event as a stable, single-line JSON object."""

    def format(self, record: logging.LogRecord) -> str:
        event_name = getattr(record, "event_name", record.getMessage())
        event_fields = getattr(record, "event_fields", {})
        payload: dict[str, object] = {
            "timestamp": datetime.fromtimestamp(
                record.created, tz=timezone.utc
            ).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "event": event_name,
            "fields": event_fields,
        }
        if record.exc_info:
            exception_type = record.exc_info[0]
            payload["exception"] = {
                "type": exception_type.__name__ if exception_type else "Exception"
            }
        return json.dumps(payload, ensure_ascii=False, default=str)


def configure_event_logging(*, level: int = logging.INFO) -> logging.Logger:
    """Install the JSON event handler once for command-line applications."""

    logger = logging.getLogger(PACKAGE_LOGGER_NAME)
    logger.setLevel(level)
    logger.propagate = False
    for handler in logger.handlers:
        if getattr(handler, "_taxpayer_event_handler", False):
            handler.setLevel(level)
            return logger

    handler = logging.StreamHandler()
    handler.setLevel(level)
    handler.setFormatter(JsonEventFormatter())
    # A private marker keeps repeated ``main`` calls in tests from duplicating
    # every event without depending on handler position or concrete stream.
    handler._taxpayer_event_handler = True  # type: ignore[attr-defined]
    logger.addHandler(handler)
    return logger


def log_event(
    logger: logging.Logger,
    level: int,
    event: str,
    *,
    exc_info: bool = False,
    **fields: object,
) -> None:
    """Emit a structured event while remaining compatible with normal logging."""

    logger.log(
        level,
        event,
        exc_info=exc_info,
        extra={"event_name": event, "event_fields": fields},
    )

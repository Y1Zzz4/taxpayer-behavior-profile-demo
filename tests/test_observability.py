from __future__ import annotations

import io
import json
import logging

from taxpayer_profile.observability import JsonEventFormatter, log_event


def test_structured_event_is_single_line_json_without_implicit_message_data() -> None:
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonEventFormatter())
    logger = logging.getLogger("test.taxpayer_profile.events")
    logger.handlers = [handler]
    logger.propagate = False
    logger.setLevel(logging.INFO)

    log_event(
        logger,
        logging.INFO,
        "ingestion.batch_completed",
        batch_id="batch-1",
        conflict_count=2,
    )

    payload = json.loads(stream.getvalue())
    assert payload["event"] == "ingestion.batch_completed"
    assert payload["level"] == "INFO"
    assert payload["fields"] == {"batch_id": "batch-1", "conflict_count": 2}
    assert "message" not in payload
    assert "\n" not in stream.getvalue().rstrip("\n")


def test_structured_event_can_preserve_the_exception_type_without_message() -> None:
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonEventFormatter())
    logger = logging.getLogger("test.taxpayer_profile.events.exception")
    logger.handlers = [handler]
    logger.propagate = False
    logger.setLevel(logging.ERROR)

    try:
        raise RuntimeError("sensitive internal detail")
    except RuntimeError:
        log_event(
            logger,
            logging.ERROR,
            "http.request_failed",
            exc_info=True,
            method="GET",
            path="/api/dashboard",
        )

    payload = json.loads(stream.getvalue())
    assert payload["exception"] == {"type": "RuntimeError"}
    assert "sensitive internal detail" not in stream.getvalue()

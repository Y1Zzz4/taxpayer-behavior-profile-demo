"""Compatibility façade for the former combined web application module."""

from taxpayer_profile.application.web_service import (
    HISTORICAL_FACT_DEFINITIONS,
    PROFILE_DIMENSION_TAXONOMY,
    REALTIME_ADVICE_TIMEOUT_SECONDS,
    SHOWCASE_SCENARIOS,
    DemoService,
    _district_unit_label,
    _segmented_rows,
)
from taxpayer_profile.presentation.http import (
    MAX_REQUEST_BYTES,
    SESSION_COOKIE,
    WEB_ROOT,
    handler_factory as _handler_factory,
    run_server,
)

__all__ = [
    "DemoService",
    "HISTORICAL_FACT_DEFINITIONS",
    "MAX_REQUEST_BYTES",
    "PROFILE_DIMENSION_TAXONOMY",
    "REALTIME_ADVICE_TIMEOUT_SECONDS",
    "SESSION_COOKIE",
    "SHOWCASE_SCENARIOS",
    "WEB_ROOT",
    "_district_unit_label",
    "_handler_factory",
    "_segmented_rows",
    "run_server",
]

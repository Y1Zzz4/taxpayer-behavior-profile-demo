"""Stable ingestion modes independent from any concrete input adapter."""

from enum import Enum


class InputMode(str, Enum):
    """Stable persisted name for the unified incremental analysis path."""

    # Keep the stored value for compatibility with existing SQLite records and
    # model-cache keys; the old raw/history behavioral split no longer exists.
    INCREMENTAL = "raw_analysis"

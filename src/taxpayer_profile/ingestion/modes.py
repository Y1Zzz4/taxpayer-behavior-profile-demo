"""Stable ingestion modes independent from any concrete input adapter."""

from enum import Enum


class InputMode(str, Enum):
    """Select the analytical trust contract for an ingestion batch."""

    TRUSTED_IMPORT = "trusted_import"
    # Keep the stored value stable so existing databases, extraction caches and
    # external callers do not require a migration merely for clearer naming.
    INCREMENTAL = "raw_analysis"
    RAW_ANALYSIS = "raw_analysis"  # Backward-compatible alias.
    BOOTSTRAP_MIXED = "bootstrap_mixed"

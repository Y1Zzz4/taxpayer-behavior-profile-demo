"""Backward-compatible facade for ingestion application use cases.

New code should import from ``taxpayer_profile.application.ingest``. Keeping
this module stable avoids forcing scripts and external callers to migrate in
the same release as the internal architecture.
"""

from taxpayer_profile.analysis.repetition import (
    reassess_after_backfill as _reassess_after_backfill,
)
from taxpayer_profile.application.ingest import (
    ProcessingSummary,
    process_raw_directory,
    process_workbook,
    rebuild_all_profiles,
)

__all__ = [
    "ProcessingSummary",
    "process_raw_directory",
    "process_workbook",
    "rebuild_all_profiles",
]

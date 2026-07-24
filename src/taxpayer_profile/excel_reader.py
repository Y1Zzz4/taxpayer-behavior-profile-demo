"""Backward-compatible exports for the Excel ingestion adapter.

New application code should import from ``taxpayer_profile.ingestion.excel``
and ``taxpayer_profile.ingestion.modes``. This facade remains so existing
scripts and integrations do not break during the project restructure.
"""

from taxpayer_profile.ingestion.excel import (
    ALLOWED_COLUMNS,
    REQUIRED_COLUMNS,
    STRING_COLUMNS,
    discover_workbooks,
    read_excel_records,
    read_excel_workbook,
    workbook_fingerprint,
    workbook_registration_bounds,
)
from taxpayer_profile.ingestion.modes import InputMode

__all__ = [
    "ALLOWED_COLUMNS",
    "REQUIRED_COLUMNS",
    "STRING_COLUMNS",
    "InputMode",
    "discover_workbooks",
    "read_excel_records",
    "read_excel_workbook",
    "workbook_fingerprint",
    "workbook_registration_bounds",
]

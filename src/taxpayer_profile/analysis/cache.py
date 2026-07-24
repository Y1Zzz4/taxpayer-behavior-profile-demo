"""Persistent cache for validated model extraction results.

The cache stores only the structured model response under a caller-provided
digest. Raw phone numbers, transcripts and request payloads must never be used
as cache keys or stored in this database.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from taxpayer_profile.llm_client import (
    CallExtractionResult,
    HistoryEnrichmentResult,
    RepeatIssueModelResult,
)

CachedModelResult = (
    CallExtractionResult | HistoryEnrichmentResult | RepeatIssueModelResult
)


class ModelExtractionCache:
    """SQLite-backed checkpoint store for validated per-call model outputs."""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path).expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(self.path)
        # Model batches may be interrupted externally. WAL plus synchronous
        # NORMAL keeps completed checkpoints durable without serializing every
        # read behind the writer.
        self._connection.execute("PRAGMA journal_mode=WAL")
        self._connection.execute("PRAGMA synchronous=NORMAL")
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS extraction_cache (
                cache_key TEXT PRIMARY KEY,
                result_kind TEXT NOT NULL,
                result_json TEXT NOT NULL,
                model_name TEXT NOT NULL,
                prompt_version TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        self._connection.commit()

    def get(
        self, cache_key: str, result_type: type[CachedModelResult]
    ) -> CachedModelResult | None:
        """Load and revalidate a cached response.

        Invalid or schema-incompatible entries are removed so a later request
        can repopulate them under the current result contract.
        """

        row = self._connection.execute(
            "SELECT result_kind, result_json FROM extraction_cache WHERE cache_key = ?",
            (cache_key,),
        ).fetchone()
        if row is None or row[0] != result_type.__name__:
            return None
        try:
            return result_type.model_validate_json(row[1])
        except ValueError:
            self._connection.execute(
                "DELETE FROM extraction_cache WHERE cache_key = ?", (cache_key,)
            )
            self._connection.commit()
            return None

    def put(
        self,
        cache_key: str,
        result: CachedModelResult,
        *,
        model_name: str,
        prompt_version: str,
    ) -> None:
        """Persist one already validated model response."""

        self._connection.execute(
            """
            INSERT OR REPLACE INTO extraction_cache (
                cache_key, result_kind, result_json, model_name,
                prompt_version, created_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                cache_key,
                type(result).__name__,
                result.model_dump_json(),
                model_name,
                prompt_version,
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        self._connection.commit()

    def close(self) -> None:
        """Release the SQLite handle and its WAL resources."""

        self._connection.close()

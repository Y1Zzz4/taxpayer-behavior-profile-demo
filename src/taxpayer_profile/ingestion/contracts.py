"""Ports shared by tabular input adapters and ingestion use cases."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path
from string import hexdigits
from typing import Protocol


@dataclass(frozen=True)
class InputSourceIdentity:
    """Stable, non-sensitive metadata used for audit and idempotency."""

    name: str
    fingerprint: str

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("输入来源名称不能为空")
        if len(self.name) > 300:
            raise ValueError("输入来源名称不得超过 300 个字符")
        if (
            len(self.fingerprint) != 64
            or any(character not in hexdigits for character in self.fingerprint)
        ):
            raise ValueError("输入来源指纹必须是 64 位十六进制 SHA-256")


class TabularInputAdapter(Protocol):
    """Translate one concrete source into the application's row contract."""

    def identify(self, source: Path) -> InputSourceIdentity:
        """Return metadata without parsing all source rows."""

    def read_rows(
        self,
        source: Path,
        *,
        start_date: date | None = None,
        end_date: date | None = None,
    ) -> list[dict[str, object]]:
        """Return approved source fields as independent row mappings."""

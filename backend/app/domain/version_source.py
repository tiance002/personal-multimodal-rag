"""Immutable interpretation identity of one uploaded source revision.

NULL legacy metadata is unknown, never inferred from the mutable Document.
"""
from dataclasses import dataclass
from typing import Any, Mapping


@dataclass(frozen=True)
class VersionSource:
    version_id: str
    document_id: str
    version_no: int
    source_sha256: str
    storage_key: str
    file_name: str | None
    media_type: str | None
    original_size: int | None = None

    @classmethod
    def from_row(cls, row: Mapping[str, Any]) -> "VersionSource":
        return cls(str(row["id"]), str(row["document_id"]), row["version_no"],
                   row["source_sha256"], row["storage_key"], row["file_name"],
                   row["media_type"], row.get("original_size"))

    @property
    def metadata_known(self) -> bool:
        return bool(self.file_name and self.media_type)

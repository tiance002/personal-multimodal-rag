from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from backend.app.adapters.storage import ContentAddressedStorage


@dataclass(frozen=True)
class AssetDetail:
    asset_id: str
    version_id: str
    asset_type: str
    storage_key: str | None
    text_content: str | None
    page_no: int | None
    source_locator: dict[str, Any]
    status: str
    error_code: str | None = None


class AssetService:
    def __init__(self, storage: ContentAddressedStorage) -> None:
        self.storage = storage

    def read_source(self, storage_key: str) -> bytes:
        return self.storage.read(storage_key)

    def public_detail(self, row: dict[str, Any]) -> AssetDetail:
        return AssetDetail(
            asset_id=str(row["id"]),
            version_id=str(row["version_id"]),
            asset_type=row["asset_type"],
            storage_key=row.get("storage_key"),
            text_content=row.get("text_content"),
            page_no=row.get("page_no"),
            source_locator=row.get("source_locator") or {},
            status=row["status"],
            error_code=row.get("error_code"),
        )

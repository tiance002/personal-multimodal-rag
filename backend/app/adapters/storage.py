from __future__ import annotations

import hashlib
import os
import tempfile
from pathlib import Path
from typing import BinaryIO

from backend.app.domain.models import StoredObject


class ContentAddressedStorage:
    """Write source bytes once under a SHA-256 key and never overwrite them."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.objects_root = self.root / "objects"
        self.tmp_root = self.root / "tmp"
        self.objects_root.mkdir(parents=True, exist_ok=True)
        self.tmp_root.mkdir(parents=True, exist_ok=True)

    def _path_for_key(self, storage_key: str) -> Path:
        candidate = Path(storage_key)
        if candidate.is_absolute() or ".." in candidate.parts or candidate.parts[:1] != ("objects",):
            raise ValueError("invalid storage key")
        return self.root / candidate

    def path_for(self, storage_key: str) -> Path:
        path = self._path_for_key(storage_key)
        if not path.is_file():
            raise FileNotFoundError(storage_key)
        return path

    def put_stream(self, stream: BinaryIO, chunk_size: int = 1024 * 1024, max_bytes: int | None = None) -> StoredObject:
        digest = hashlib.sha256()
        size = 0
        temporary: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(dir=self.tmp_root, prefix="upload-", delete=False) as handle:
                temporary = Path(handle.name)
                while True:
                    chunk = stream.read(chunk_size)
                    if not chunk:
                        break
                    if not isinstance(chunk, bytes):
                        raise TypeError("upload stream must return bytes")
                    if max_bytes is not None and size + len(chunk) > max_bytes:
                        raise ValueError("UPLOAD_TOO_LARGE")
                    handle.write(chunk)
                    digest.update(chunk)
                    size += len(chunk)
            sha256 = digest.hexdigest()
            storage_key = f"objects/{sha256[:2]}/{sha256}"
            destination = self._path_for_key(storage_key)
            destination.parent.mkdir(parents=True, exist_ok=True)
            if destination.exists():
                temporary.unlink(missing_ok=True)
            else:
                os.replace(temporary, destination)
            return StoredObject(storage_key=storage_key, sha256=sha256, size=size)
        except Exception:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
            raise

    def read(self, storage_key: str) -> bytes:
        return self.path_for(storage_key).read_bytes()

"""Native long IO, immutable bytes and scope remain enforced on Windows."""
import uuid
from pathlib import Path
from types import SimpleNamespace
import pytest
from backend.weknora_port.documents import DocumentLifecycleService,_native_io_path,DocumentLifecycleError


def test_native_long_immutable_io_preserves_original_and_rejects_escape():
    root=Path('E:/RAG-Tests/native-long-path-'+str(uuid.uuid4()))
    service=object.__new__(DocumentLifecycleService);service.storage_root=root
    target=root/('a'*90)/('b'*90)/('c'*90)/'original.txt'
    assert len(str(target))>260
    payload=b'original public immutable long-path bytes'
    service._write_immutable(target,payload)
    assert _native_io_path(target).read_bytes()==payload
    with pytest.raises(FileExistsError):service._write_immutable(target,b'replacement forbidden')
    assert _native_io_path(target).read_bytes()==payload
    with pytest.raises(DocumentLifecycleError):service._write_immutable(root.parent/'escape.txt',b'escape forbidden')

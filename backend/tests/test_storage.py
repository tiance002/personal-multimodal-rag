import io

from backend.app.adapters.storage import ContentAddressedStorage


def test_same_bytes_are_content_addressed_and_different_bytes_do_not_overwrite(tmp_path):
    storage = ContentAddressedStorage(tmp_path)

    first = storage.put_stream(io.BytesIO(b"one"))
    second = storage.put_stream(io.BytesIO(b"two"))
    duplicate = storage.put_stream(io.BytesIO(b"one"))

    assert first.storage_key != second.storage_key
    assert duplicate.storage_key == first.storage_key
    assert storage.read(first.storage_key) == b"one"
    assert storage.read(second.storage_key) == b"two"

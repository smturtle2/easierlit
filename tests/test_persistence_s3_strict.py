import asyncio

import pytest

from easierlit.settings import (
    EasierlitPersistenceConfig,
    assert_s3_storage_operational,
    ensure_s3_storage_provider,
)


def test_persistence_config_rejects_non_s3_provider():
    with pytest.raises(TypeError, match="must be an instance"):
        EasierlitPersistenceConfig(
            enabled=True,
            sqlite_path=".chainlit/easierlit.db",
            storage_provider=object(),
        )


def test_ensure_s3_storage_provider_rejects_uninitialized_provider(monkeypatch):
    from chainlit.data.storage_clients import s3 as chainlit_s3

    class _FakeS3StorageClient:
        def __init__(self, bucket: str = "bucket", **_kwargs):
            self.bucket = bucket

    monkeypatch.setattr(chainlit_s3, "S3StorageClient", _FakeS3StorageClient)
    provider = _FakeS3StorageClient()

    with pytest.raises(RuntimeError, match="not initialized"):
        ensure_s3_storage_provider(provider)


def test_ensure_s3_storage_provider_rejects_invalid_upload_response(monkeypatch):
    from chainlit.data.storage_clients import s3 as chainlit_s3

    class _FakeS3StorageClient:
        def __init__(self, bucket: str = "bucket", **_kwargs):
            self.bucket = bucket
            self.client = object()

        async def upload_file(
            self,
            object_key: str,
            data,
            mime: str = "application/octet-stream",
            overwrite: bool = True,
            content_disposition=None,
        ):
            del object_key, data, mime, overwrite, content_disposition
            return {}

        async def delete_file(self, _object_key: str) -> bool:
            return True

        async def get_read_url(self, object_key: str) -> str:
            return f"https://example.com/{object_key}"

        async def close(self) -> None:
            return None

    monkeypatch.setattr(chainlit_s3, "S3StorageClient", _FakeS3StorageClient)
    provider = ensure_s3_storage_provider(_FakeS3StorageClient())

    with pytest.raises(RuntimeError, match="missing object_key"):
        asyncio.run(provider.upload_file("test-key", b"payload"))


def test_assert_s3_storage_operational_uploads_and_deletes_probe(monkeypatch):
    from chainlit.data.storage_clients import s3 as chainlit_s3

    calls: dict[str, str] = {}

    class _FakeS3StorageClient:
        def __init__(self, bucket: str = "bucket", **_kwargs):
            self.bucket = bucket
            self.client = object()

        async def upload_file(
            self,
            object_key: str,
            data,
            mime: str = "application/octet-stream",
            overwrite: bool = True,
            content_disposition=None,
        ):
            del data, mime, overwrite, content_disposition
            calls["uploaded_key"] = object_key
            return {"object_key": object_key, "url": f"https://example.com/{object_key}"}

        async def delete_file(self, object_key: str) -> bool:
            calls["deleted_key"] = object_key
            return True

        async def get_read_url(self, object_key: str) -> str:
            return f"https://example.com/{object_key}"

        async def close(self) -> None:
            return None

    monkeypatch.setattr(chainlit_s3, "S3StorageClient", _FakeS3StorageClient)
    provider = _FakeS3StorageClient()

    asyncio.run(assert_s3_storage_operational(provider))

    assert calls["uploaded_key"].startswith("easierlit-preflight/")
    assert calls["deleted_key"] == calls["uploaded_key"]

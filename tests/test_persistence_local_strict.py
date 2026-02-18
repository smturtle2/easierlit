import asyncio

import pytest

from easierlit.settings import (
    EasierlitPersistenceConfig,
    assert_local_storage_operational,
    ensure_local_storage_provider,
)
from easierlit.storage import LocalFileStorageClient


def test_persistence_config_rejects_non_local_provider():
    with pytest.raises(TypeError, match="LocalFileStorageClient"):
        EasierlitPersistenceConfig(
            enabled=True,
            sqlite_path=".chainlit/easierlit.db",
            storage_provider=object(),
        )


def test_ensure_local_storage_provider_rejects_none():
    with pytest.raises(ValueError, match="LocalFileStorageClient"):
        ensure_local_storage_provider(None)


def test_local_storage_upload_returns_expected_schema(tmp_path, monkeypatch):
    monkeypatch.setenv("CHAINLIT_APP_ROOT", str(tmp_path))
    provider = LocalFileStorageClient(base_dir=tmp_path / "public" / "easierlit")
    monkeypatch.setenv("CHAINLIT_ROOT_PATH", "/custom")

    uploaded = asyncio.run(provider.upload_file("user-1/image 1.png", b"payload"))

    assert uploaded["object_key"] == "user-1/image 1.png"
    assert uploaded["url"] == "/custom/public/easierlit/user-1/image%201.png"
    assert (tmp_path / "public" / "easierlit" / "user-1" / "image 1.png").is_file()


def test_local_storage_rejects_traversal_object_key(tmp_path, monkeypatch):
    monkeypatch.setenv("CHAINLIT_APP_ROOT", str(tmp_path))
    provider = LocalFileStorageClient(base_dir=tmp_path / "public" / "easierlit")

    with pytest.raises(ValueError, match="Invalid object_key"):
        asyncio.run(provider.upload_file("../escape.txt", b"payload"))


def test_local_storage_rejects_base_dir_outside_public_root(tmp_path, monkeypatch):
    monkeypatch.setenv("CHAINLIT_APP_ROOT", str(tmp_path))
    with pytest.raises(ValueError, match="must be inside"):
        LocalFileStorageClient(base_dir=tmp_path / "outside")


def test_local_storage_default_base_dir_uses_chainlit_app_root(tmp_path, monkeypatch):
    monkeypatch.setenv("CHAINLIT_APP_ROOT", str(tmp_path))
    provider = LocalFileStorageClient()

    assert provider.public_root == (tmp_path / "public").resolve()
    assert provider.base_dir == (tmp_path / "public" / "easierlit").resolve()


def test_local_storage_builds_url_with_parent_and_root_path(tmp_path, monkeypatch):
    monkeypatch.setenv("CHAINLIT_APP_ROOT", str(tmp_path))
    monkeypatch.setenv("CHAINLIT_PARENT_ROOT_PATH", "/proxy")
    monkeypatch.setenv("CHAINLIT_ROOT_PATH", "/chat")
    provider = LocalFileStorageClient(base_dir=tmp_path / "public" / "easierlit")

    uploaded = asyncio.run(provider.upload_file("user-1/image.png", b"payload"))

    assert uploaded["url"] == "/proxy/chat/public/easierlit/user-1/image.png"


def test_local_storage_relative_base_dir_is_under_public_root(tmp_path, monkeypatch):
    monkeypatch.setenv("CHAINLIT_APP_ROOT", str(tmp_path))
    provider = LocalFileStorageClient(base_dir="my-local-store")

    uploaded = asyncio.run(provider.upload_file("user-1/image.png", b"payload"))

    assert provider.base_dir == (tmp_path / "public" / "my-local-store").resolve()
    assert uploaded["url"] == "/public/my-local-store/user-1/image.png"
    assert (tmp_path / "public" / "my-local-store" / "user-1" / "image.png").is_file()


def test_assert_local_storage_operational_uploads_and_deletes_probe(tmp_path, monkeypatch):
    monkeypatch.setenv("CHAINLIT_APP_ROOT", str(tmp_path))
    provider = LocalFileStorageClient(base_dir=tmp_path / "public" / "easierlit")

    asyncio.run(assert_local_storage_operational(provider))

    assert list((tmp_path / "public" / "easierlit").rglob("*.txt")) == []


def test_get_read_url_raises_for_missing_file(tmp_path, monkeypatch):
    monkeypatch.setenv("CHAINLIT_APP_ROOT", str(tmp_path))
    provider = LocalFileStorageClient(base_dir=tmp_path / "public" / "easierlit")

    with pytest.raises(FileNotFoundError):
        asyncio.run(provider.get_read_url("missing/file.txt"))

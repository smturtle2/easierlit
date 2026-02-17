import os

import chainlit.data.sql_alchemy as chainlit_sql_alchemy
from chainlit.config import config
import pytest

import easierlit.chainlit_entry as chainlit_entry
from easierlit import EasierlitApp, EasierlitClient, EasierlitPersistenceConfig
from easierlit.chainlit_entry import (
    _apply_runtime_configuration,
    _should_register_default_data_layer,
)
from easierlit.runtime import get_runtime

_S3_ENV_KEYS = (
    "EASIERLIT_S3_BUCKET",
    "BUCKET_NAME",
    "APP_AWS_REGION",
    "AWS_REGION",
    "AWS_DEFAULT_REGION",
    "APP_AWS_ACCESS_KEY",
    "AWS_ACCESS_KEY_ID",
    "APP_AWS_SECRET_KEY",
    "AWS_SECRET_ACCESS_KEY",
    "APP_AWS_SESSION_TOKEN",
    "AWS_SESSION_TOKEN",
    "APP_AWS_ENDPOINT_URL",
    "DEV_AWS_ENDPOINT",
)


@pytest.fixture(autouse=True)
def _clear_s3_env():
    previous_values = {key: os.environ.get(key) for key in _S3_ENV_KEYS}
    for key in _S3_ENV_KEYS:
        os.environ.pop(key, None)
    yield
    for key, value in previous_values.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value


def _clear_chainlit_hooks() -> None:
    config.code.password_auth_callback = None
    config.code.data_layer = None


def test_default_sqlite_data_layer_is_registered_when_no_external_db(tmp_path):
    runtime = get_runtime()
    runtime.unbind()
    _clear_chainlit_hooks()

    previous_database_url = os.environ.get("DATABASE_URL")
    previous_literal_api_key = os.environ.get("LITERAL_API_KEY")
    os.environ.pop("DATABASE_URL", None)
    os.environ.pop("LITERAL_API_KEY", None)

    chainlit_entry._CONFIG_APPLIED = False
    config.ui.cot = "hidden"

    db_path = tmp_path / "default-sidebar.db"
    runtime.bind(
        client=EasierlitClient(run_func=lambda _app: None),
        app=EasierlitApp(),
        persistence=EasierlitPersistenceConfig(enabled=True, sqlite_path=str(db_path)),
    )

    try:
        _apply_runtime_configuration()

        assert config.code.data_layer is not None
        assert db_path.exists()
        assert config.ui.cot == "full"
    finally:
        runtime.unbind()
        _clear_chainlit_hooks()
        chainlit_entry._CONFIG_APPLIED = False

        if previous_database_url is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = previous_database_url

        if previous_literal_api_key is None:
            os.environ.pop("LITERAL_API_KEY", None)
        else:
            os.environ["LITERAL_API_KEY"] = previous_literal_api_key


def test_default_sqlite_is_not_registered_when_database_url_exists():
    runtime = get_runtime()
    runtime.unbind()
    _clear_chainlit_hooks()

    previous_database_url = os.environ.get("DATABASE_URL")
    previous_literal_api_key = os.environ.get("LITERAL_API_KEY")

    os.environ["DATABASE_URL"] = "postgresql://example"
    os.environ.pop("LITERAL_API_KEY", None)

    chainlit_entry._CONFIG_APPLIED = False
    config.ui.cot = "hidden"

    runtime.bind(
        client=EasierlitClient(run_func=lambda _app: None),
        app=EasierlitApp(),
        persistence=EasierlitPersistenceConfig(enabled=True),
    )

    try:
        assert _should_register_default_data_layer() is False
        _apply_runtime_configuration()
        assert config.code.data_layer is None
        assert config.ui.cot == "full"
    finally:
        runtime.unbind()
        _clear_chainlit_hooks()
        chainlit_entry._CONFIG_APPLIED = False

        if previous_database_url is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = previous_database_url

        if previous_literal_api_key is None:
            os.environ.pop("LITERAL_API_KEY", None)
        else:
            os.environ["LITERAL_API_KEY"] = previous_literal_api_key


def test_default_sqlite_data_layer_passes_storage_provider(tmp_path, monkeypatch):
    runtime = get_runtime()
    runtime.unbind()
    _clear_chainlit_hooks()

    previous_database_url = os.environ.get("DATABASE_URL")
    previous_literal_api_key = os.environ.get("LITERAL_API_KEY")
    os.environ.pop("DATABASE_URL", None)
    os.environ.pop("LITERAL_API_KEY", None)

    chainlit_entry._CONFIG_APPLIED = False

    db_path = tmp_path / "storage-provider.db"
    storage_provider = object()
    runtime.bind(
        client=EasierlitClient(run_func=lambda _app: None),
        app=EasierlitApp(),
        persistence=EasierlitPersistenceConfig(
            enabled=True,
            sqlite_path=str(db_path),
            storage_provider=storage_provider,
        ),
    )

    captured: dict[str, object] = {}

    class _FakeSQLAlchemyDataLayer:
        def __init__(self, conninfo: str, storage_provider=None):
            captured["conninfo"] = conninfo
            captured["storage_provider"] = storage_provider

    monkeypatch.setattr(chainlit_sql_alchemy, "SQLAlchemyDataLayer", _FakeSQLAlchemyDataLayer)

    try:
        _apply_runtime_configuration()

        assert config.code.data_layer is not None
        config.code.data_layer()
        assert captured["conninfo"] == f"sqlite+aiosqlite:///{db_path.resolve()}"
        assert captured["storage_provider"] is storage_provider
    finally:
        runtime.unbind()
        _clear_chainlit_hooks()
        chainlit_entry._CONFIG_APPLIED = False

        if previous_database_url is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = previous_database_url

        if previous_literal_api_key is None:
            os.environ.pop("LITERAL_API_KEY", None)
        else:
            os.environ["LITERAL_API_KEY"] = previous_literal_api_key


def test_default_sqlite_data_layer_uses_default_s3_storage_provider(tmp_path, monkeypatch):
    runtime = get_runtime()
    runtime.unbind()
    _clear_chainlit_hooks()

    previous_database_url = os.environ.get("DATABASE_URL")
    previous_literal_api_key = os.environ.get("LITERAL_API_KEY")
    os.environ.pop("DATABASE_URL", None)
    os.environ.pop("LITERAL_API_KEY", None)

    os.environ["BUCKET_NAME"] = "easierlit-test-bucket"
    os.environ["APP_AWS_REGION"] = "ap-northeast-2"
    os.environ["APP_AWS_ACCESS_KEY"] = "test-access-key"
    os.environ["APP_AWS_SECRET_KEY"] = "test-secret-key"
    os.environ["DEV_AWS_ENDPOINT"] = "https://s3.test.local"

    chainlit_entry._CONFIG_APPLIED = False

    captured_s3: dict[str, object] = {}
    captured_data_layer: dict[str, object] = {}

    class _FakeS3StorageClient:
        def __init__(self, bucket: str, **kwargs):
            captured_s3["bucket"] = bucket
            captured_s3["kwargs"] = kwargs

    class _FakeSQLAlchemyDataLayer:
        def __init__(self, conninfo: str, storage_provider=None):
            captured_data_layer["conninfo"] = conninfo
            captured_data_layer["storage_provider"] = storage_provider

    from chainlit.data.storage_clients import s3 as chainlit_s3

    monkeypatch.setattr(chainlit_s3, "S3StorageClient", _FakeS3StorageClient)
    monkeypatch.setattr(chainlit_sql_alchemy, "SQLAlchemyDataLayer", _FakeSQLAlchemyDataLayer)

    db_path = tmp_path / "default-s3-provider.db"
    runtime.bind(
        client=EasierlitClient(run_func=lambda _app: None),
        app=EasierlitApp(),
        persistence=EasierlitPersistenceConfig(enabled=True, sqlite_path=str(db_path)),
    )

    try:
        _apply_runtime_configuration()
        assert config.code.data_layer is not None
        config.code.data_layer()

        assert captured_s3["bucket"] == "easierlit-test-bucket"
        assert captured_s3["kwargs"] == {
            "region_name": "ap-northeast-2",
            "aws_access_key_id": "test-access-key",
            "aws_secret_access_key": "test-secret-key",
            "endpoint_url": "https://s3.test.local",
        }
        assert captured_data_layer["conninfo"] == f"sqlite+aiosqlite:///{db_path.resolve()}"
        assert captured_data_layer["storage_provider"] is not None
        assert type(captured_data_layer["storage_provider"]).__name__ == "_FakeS3StorageClient"
    finally:
        runtime.unbind()
        _clear_chainlit_hooks()
        chainlit_entry._CONFIG_APPLIED = False

        if previous_database_url is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = previous_database_url

        if previous_literal_api_key is None:
            os.environ.pop("LITERAL_API_KEY", None)
        else:
            os.environ["LITERAL_API_KEY"] = previous_literal_api_key


def test_default_sqlite_data_layer_uses_fallback_s3_bucket_when_env_missing(
    tmp_path, monkeypatch
):
    runtime = get_runtime()
    runtime.unbind()
    _clear_chainlit_hooks()

    previous_database_url = os.environ.get("DATABASE_URL")
    previous_literal_api_key = os.environ.get("LITERAL_API_KEY")
    os.environ.pop("DATABASE_URL", None)
    os.environ.pop("LITERAL_API_KEY", None)

    chainlit_entry._CONFIG_APPLIED = False

    captured_s3: dict[str, object] = {}
    captured_data_layer: dict[str, object] = {}

    class _FakeS3StorageClient:
        def __init__(self, bucket: str, **kwargs):
            captured_s3["bucket"] = bucket
            captured_s3["kwargs"] = kwargs

    class _FakeSQLAlchemyDataLayer:
        def __init__(self, conninfo: str, storage_provider=None):
            captured_data_layer["conninfo"] = conninfo
            captured_data_layer["storage_provider"] = storage_provider

    from chainlit.data.storage_clients import s3 as chainlit_s3

    monkeypatch.setattr(chainlit_s3, "S3StorageClient", _FakeS3StorageClient)
    monkeypatch.setattr(chainlit_sql_alchemy, "SQLAlchemyDataLayer", _FakeSQLAlchemyDataLayer)

    db_path = tmp_path / "fallback-s3-bucket.db"
    runtime.bind(
        client=EasierlitClient(run_func=lambda _app: None),
        app=EasierlitApp(),
        persistence=EasierlitPersistenceConfig(enabled=True, sqlite_path=str(db_path)),
    )

    try:
        _apply_runtime_configuration()
        assert config.code.data_layer is not None
        config.code.data_layer()

        assert captured_s3["bucket"] == "easierlit-default"
        assert captured_s3["kwargs"] == {}
        assert captured_data_layer["conninfo"] == f"sqlite+aiosqlite:///{db_path.resolve()}"
        assert captured_data_layer["storage_provider"] is not None
        assert type(captured_data_layer["storage_provider"]).__name__ == "_FakeS3StorageClient"
    finally:
        runtime.unbind()
        _clear_chainlit_hooks()
        chainlit_entry._CONFIG_APPLIED = False

        if previous_database_url is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = previous_database_url

        if previous_literal_api_key is None:
            os.environ.pop("LITERAL_API_KEY", None)
        else:
            os.environ["LITERAL_API_KEY"] = previous_literal_api_key


def test_default_sqlite_data_layer_warns_when_storage_provider_missing(tmp_path, caplog):
    runtime = get_runtime()
    runtime.unbind()
    _clear_chainlit_hooks()

    previous_database_url = os.environ.get("DATABASE_URL")
    previous_literal_api_key = os.environ.get("LITERAL_API_KEY")
    os.environ.pop("DATABASE_URL", None)
    os.environ.pop("LITERAL_API_KEY", None)

    chainlit_entry._CONFIG_APPLIED = False

    db_path = tmp_path / "warning-provider.db"
    runtime.bind(
        client=EasierlitClient(run_func=lambda _app: None),
        app=EasierlitApp(),
        persistence=EasierlitPersistenceConfig(
            enabled=True,
            sqlite_path=str(db_path),
            storage_provider=None,
        ),
    )

    try:
        with caplog.at_level("WARNING", logger="easierlit.chainlit_entry"):
            _apply_runtime_configuration()

        assert "without a storage provider" in caplog.text
        assert "storage_provider=..." in caplog.text
    finally:
        runtime.unbind()
        _clear_chainlit_hooks()
        chainlit_entry._CONFIG_APPLIED = False

        if previous_database_url is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = previous_database_url

        if previous_literal_api_key is None:
            os.environ.pop("LITERAL_API_KEY", None)
        else:
            os.environ["LITERAL_API_KEY"] = previous_literal_api_key

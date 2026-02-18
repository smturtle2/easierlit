import os
from pathlib import Path

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
from easierlit.storage import LocalFileStorageClient



def _clear_chainlit_hooks() -> None:
    config.code.password_auth_callback = None
    config.code.data_layer = None
    chainlit_entry._DEFAULT_DATA_LAYER_REGISTERED = False
    chainlit_entry._LOCAL_STORAGE_PROVIDER = None



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
    monkeypatch.setenv("CHAINLIT_APP_ROOT", str(tmp_path))

    chainlit_entry._CONFIG_APPLIED = False

    db_path = tmp_path / "storage-provider.db"
    storage_provider = LocalFileStorageClient(base_dir=tmp_path / "public" / "easierlit")
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



def test_default_sqlite_data_layer_uses_default_local_storage_provider(tmp_path, monkeypatch):
    runtime = get_runtime()
    runtime.unbind()
    _clear_chainlit_hooks()

    previous_database_url = os.environ.get("DATABASE_URL")
    previous_literal_api_key = os.environ.get("LITERAL_API_KEY")
    os.environ.pop("DATABASE_URL", None)
    os.environ.pop("LITERAL_API_KEY", None)

    chainlit_entry._CONFIG_APPLIED = False

    captured_data_layer: dict[str, object] = {}

    class _FakeSQLAlchemyDataLayer:
        def __init__(self, conninfo: str, storage_provider=None):
            captured_data_layer["conninfo"] = conninfo
            captured_data_layer["storage_provider"] = storage_provider

    monkeypatch.setattr(chainlit_sql_alchemy, "SQLAlchemyDataLayer", _FakeSQLAlchemyDataLayer)

    db_path = tmp_path / "default-local-provider.db"
    runtime.bind(
        client=EasierlitClient(run_func=lambda _app: None),
        app=EasierlitApp(),
        persistence=EasierlitPersistenceConfig(enabled=True, sqlite_path=str(db_path)),
    )

    try:
        _apply_runtime_configuration()
        assert config.code.data_layer is not None
        config.code.data_layer()

        assert captured_data_layer["conninfo"] == f"sqlite+aiosqlite:///{db_path.resolve()}"
        assert isinstance(captured_data_layer["storage_provider"], LocalFileStorageClient)
        default_base_dir = (Path.cwd() / "public" / "easierlit").resolve()
        assert captured_data_layer["storage_provider"].base_dir == default_base_dir
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



def test_default_sqlite_data_layer_accepts_missing_storage_provider(tmp_path):
    runtime = get_runtime()
    runtime.unbind()
    _clear_chainlit_hooks()

    previous_database_url = os.environ.get("DATABASE_URL")
    previous_literal_api_key = os.environ.get("LITERAL_API_KEY")
    os.environ.pop("DATABASE_URL", None)
    os.environ.pop("LITERAL_API_KEY", None)

    chainlit_entry._CONFIG_APPLIED = False

    try:
        db_path = tmp_path / "missing-provider.db"
        persistence = EasierlitPersistenceConfig(
            enabled=True,
            sqlite_path=str(db_path),
            storage_provider=None,
        )
        assert isinstance(persistence.storage_provider, LocalFileStorageClient)
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

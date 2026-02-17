import os

from chainlit.config import config

import easierlit.chainlit_entry as chainlit_entry
from easierlit import EasierlitApp, EasierlitClient, EasierlitPersistenceConfig
from easierlit.chainlit_entry import (
    _apply_runtime_configuration,
    _should_register_default_data_layer,
)
from easierlit.runtime import get_runtime


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

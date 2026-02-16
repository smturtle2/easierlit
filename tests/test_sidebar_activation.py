from chainlit.config import config

from easierlit import EasierlitApp, EasierlitClient, EasierlitPersistenceConfig
from easierlit.chainlit_entry import (
    _apply_runtime_configuration,
    _should_register_default_data_layer,
)
from easierlit.runtime import get_runtime


def _clear_chainlit_hooks() -> None:
    config.code.password_auth_callback = None
    config.code.data_layer = None


def test_default_sqlite_data_layer_is_registered_when_no_external_db(monkeypatch, tmp_path):
    runtime = get_runtime()
    runtime.unbind()
    _clear_chainlit_hooks()
    monkeypatch.setattr("easierlit.chainlit_entry._CONFIG_APPLIED", False)

    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("LITERAL_API_KEY", raising=False)

    db_path = tmp_path / "default-sidebar.db"
    runtime.bind(
        client=EasierlitClient(run_func=lambda _app: None),
        app=EasierlitApp(),
        persistence=EasierlitPersistenceConfig(enabled=True, sqlite_path=str(db_path)),
    )

    _apply_runtime_configuration()

    assert config.code.data_layer is not None
    assert db_path.exists()
    assert config.ui.cot == "full"

    runtime.unbind()
    _clear_chainlit_hooks()


def test_default_sqlite_is_not_registered_when_database_url_exists(monkeypatch):
    runtime = get_runtime()
    runtime.unbind()
    _clear_chainlit_hooks()
    monkeypatch.setattr("easierlit.chainlit_entry._CONFIG_APPLIED", False)

    monkeypatch.setenv("DATABASE_URL", "postgresql://example")
    monkeypatch.delenv("LITERAL_API_KEY", raising=False)

    runtime.bind(
        client=EasierlitClient(run_func=lambda _app: None),
        app=EasierlitApp(),
        persistence=EasierlitPersistenceConfig(enabled=True),
    )

    assert _should_register_default_data_layer() is False
    _apply_runtime_configuration()
    assert config.code.data_layer is None
    assert config.ui.cot == "full"

    runtime.unbind()
    _clear_chainlit_hooks()

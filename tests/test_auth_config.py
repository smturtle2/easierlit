import asyncio

import pytest
from chainlit.config import config

from easierlit import EasierlitApp, EasierlitAuthConfig, EasierlitClient
from easierlit.chainlit_entry import _apply_runtime_configuration
from easierlit.runtime import get_runtime


@pytest.fixture(autouse=True)
def _reset_state(monkeypatch):
    runtime = get_runtime()
    runtime.unbind()
    monkeypatch.setattr("easierlit.chainlit_entry._CONFIG_APPLIED", False)

    config.code.password_auth_callback = None
    config.code.data_layer = None

    yield

    runtime.unbind()
    monkeypatch.setattr("easierlit.chainlit_entry._CONFIG_APPLIED", False)
    config.code.password_auth_callback = None
    config.code.data_layer = None


def test_auth_config_sets_password_callback(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("LITERAL_API_KEY", raising=False)

    auth = EasierlitAuthConfig(
        username="admin",
        password="admin",
        metadata={"role": "admin"},
    )
    runtime = get_runtime()
    runtime.bind(
        client=EasierlitClient(run_func=lambda _app: None),
        app=EasierlitApp(),
        auth=auth,
    )

    _apply_runtime_configuration()
    assert config.code.password_auth_callback is not None

    result = asyncio.run(config.code.password_auth_callback("admin", "admin"))
    assert result is not None
    assert result.identifier == "admin"
    assert result.metadata == {"role": "admin"}

    invalid = asyncio.run(config.code.password_auth_callback("admin", "wrong"))
    assert invalid is None


def test_auth_config_uses_custom_identifier(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("LITERAL_API_KEY", raising=False)

    auth = EasierlitAuthConfig(
        username="admin",
        password="admin",
        identifier="owner",
    )
    runtime = get_runtime()
    runtime.bind(
        client=EasierlitClient(run_func=lambda _app: None),
        app=EasierlitApp(),
        auth=auth,
    )

    _apply_runtime_configuration()
    result = asyncio.run(config.code.password_auth_callback("admin", "admin"))
    assert result is not None
    assert result.identifier == "owner"


def test_auth_config_rejects_empty_credentials():
    with pytest.raises(ValueError):
        EasierlitAuthConfig(username="", password="admin")

    with pytest.raises(ValueError):
        EasierlitAuthConfig(username="admin", password="   ")


def test_auth_config_does_not_accept_jwt_secret_argument():
    with pytest.raises(TypeError):
        EasierlitAuthConfig(username="admin", password="admin", jwt_secret="x")  # type: ignore[call-arg]
